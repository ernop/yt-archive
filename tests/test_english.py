"""Unit tests for English translation, captions, dubbing metadata, DB indexing, and web routes."""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from yt_archive import db, english, llm
from yt_archive.paths import (
    english_ass_path,
    english_dub_path,
    english_has_dub,
    english_is_complete,
    english_mkv_path,
    english_srt_path,
    english_transcript_json_path,
    english_transcript_vtt_path,
    orig_srt_path,
    soundtrack_path,
    transcript_json_path,
    transcript_vtt_path,
)
from yt_archive.settings import load_settings
from yt_archive.web import detail_html, make_handler

from test_queue import fake_queue, make_video

VIDEO = "engtest0001"


def _fake_http_response(payload: dict):
    raw = json.dumps(payload).encode("utf-8")

    class _Resp:
        def read(self):
            return raw

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _Resp()


class SettingsTests(unittest.TestCase):
    def test_example_settings_loads_cleanly(self):
        example = Path(__file__).resolve().parents[1] / "settings.example.json"
        cfg = load_settings(example)
        self.assertEqual(cfg["english"]["provider"], "openai")
        self.assertEqual(cfg["english"]["model"], "gpt-6-luna")
        self.assertEqual(cfg["english"]["effort"], "low")
        self.assertEqual(cfg["english"]["chunk_size"], 120)

    def test_rejects_invalid_english_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "settings.json"
            bad_cases = [
                {"english": {"unknown_field": 1}},
                {"english": {"provider": "mystery"}},
                {"english": {"effort": "ultra"}},
                {"english": {"chunk_size": 0}},
                {"english": {"concurrency": -1}},
                {"english": {"ollama_url": "ftp://localhost:11434"}},
            ]
            for case in bad_cases:
                with self.subTest(case=case):
                    p.write_text(json.dumps(case), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_settings(p)


class LlmClientTests(unittest.TestCase):
    def test_check_configured_fails_without_key_for_cloud(self):
        cfg = load_settings(Path("/nonexistent/settings.json"))["english"]
        with self.assertRaises(llm.LLMError):
            llm.check_settings(cfg)
        with self.assertRaises(llm.LLMError):
            llm.check_configured(cfg)
        ollama_cfg = {**cfg, "provider": "ollama", "model": "qwen-test"}
        llm.check_settings(ollama_cfg)
        tags_resp = {"models": [{"name": "qwen-test:latest"}]}
        with patch("urllib.request.urlopen", return_value=_fake_http_response(tags_resp)):
            llm.check_configured(ollama_cfg)

    def test_openai_anthropic_gemini_structured_json(self):
        schema = {"type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]}
        base = load_settings(Path("/nonexistent/settings.json"))["english"]

        openai_resp = {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"items":[1]}'}]}],
            "usage": {"input_tokens": 12, "output_tokens": 5},
        }
        with patch("urllib.request.urlopen", return_value=_fake_http_response(openai_resp)):
            ans = llm.ask_json(
                {**base, "provider": "openai", "api_key": "sk-test-secret"},
                instructions="sys",
                prompt="usr",
                schema=schema,
            )
        self.assertEqual(ans.data, {"items": [1]})
        self.assertEqual((ans.input_tokens, ans.output_tokens), (12, 5))

        anth_resp = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": '{"items":[2]}'}],
            "usage": {"input_tokens": 20, "output_tokens": 7},
        }
        with patch("urllib.request.urlopen", return_value=_fake_http_response(anth_resp)):
            ans = llm.ask_json(
                {**base, "provider": "anthropic", "model": "claude-test", "api_key": "sk-ant-secret"},
                instructions="sys",
                prompt="usr",
                schema=schema,
            )
        self.assertEqual(ans.data, {"items": [2]})
        self.assertEqual(ans.input_tokens, 20)

        gem_resp = {
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"items":[3]}'}]}}],
            "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 9},
        }
        with patch("urllib.request.urlopen", return_value=_fake_http_response(gem_resp)):
            ans = llm.ask_json(
                {**base, "provider": "gemini", "model": "gemini-test", "api_key": "AIza-secret-token"},
                instructions="sys",
                prompt="usr",
                schema=schema,
            )
        self.assertEqual(ans.data, {"items": [3]})
        self.assertEqual(ans.output_tokens, 9)

    def test_http_error_scrubs_api_key(self):
        secret = "sk-very-secret-key-999"
        err = HTTPError(
            f"https://example.com/?key={secret}",
            401,
            "Unauthorized",
            {},
            io.BytesIO(f"invalid key {secret}".encode()),
        )
        cfg = {
            **load_settings(Path("/nonexistent/settings.json"))["english"],
            "provider": "gemini",
            "model": "gemini-test",
            "api_key": secret,
        }
        try:
            with patch("urllib.request.urlopen", side_effect=err):
                with self.assertRaises(llm.LLMError) as ctx:
                    llm.ask_json(cfg, instructions="s", prompt="u", schema={"type": "object"})
            self.assertNotIn(secret, str(ctx.exception))
        finally:
            err.close()


class EnglishHelpersTests(unittest.TestCase):
    def test_normalize_config_and_estimate(self):
        self.assertEqual(
            english.normalize_english_job_config({}),
            {"include_dub": True, "source_language": ""},
        )
        self.assertEqual(
            english.normalize_english_job_config({"include_dub": False, "source_language": "ZH"}),
            {"include_dub": False, "source_language": "zh"},
        )
        with self.assertRaises(ValueError):
            english.normalize_english_job_config({"source_language": "english"})
        full = english.estimate_minutes(3600, include_dub=True, has_transcript=False)
        reused = english.estimate_minutes(3600, include_dub=True, has_transcript=True)
        no_dub = english.estimate_minutes(3600, include_dub=False, has_transcript=True)
        self.assertGreater(full, reused)
        self.assertGreater(reused, no_dub)

    def test_split_segments_by_speaker(self):
        whisper = [
            {
                "start": 0.0,
                "end": 6.0,
                "text": "Hello world how are you doing today",
                "words": [
                    {"start": 0.0, "end": 1.0, "word": "Hello"},
                    {"start": 1.0, "end": 2.0, "word": "world"},
                    {"start": 2.2, "end": 3.0, "word": "how"},
                    {"start": 3.0, "end": 4.0, "word": "are"},
                    {"start": 4.0, "end": 5.0, "word": "you"},
                    {"start": 5.0, "end": 6.0, "word": "today"},
                ],
            }
        ]
        turns = [
            {"start": 0.0, "end": 2.1, "speaker_idx": 0},
            {"start": 2.1, "end": 6.5, "speaker_idx": 1},
        ]
        split = english.split_segments_by_speaker(whisper, turns)
        self.assertEqual(len(split), 2)
        self.assertEqual(split[0]["speaker_idx"], 0)
        self.assertEqual(split[1]["speaker_idx"], 1)
        self.assertIn("Hello", split[0]["text_orig"])
        self.assertIn("today", split[1]["text_orig"])

    def test_cast_voices_and_merge_utterances(self):
        segs = [
            {"index": 0, "start": 0.0, "end": 2.0, "speaker": "Host Woman", "text": "Welcome.", "dub_text": "Welcome."},
            {"index": 1, "start": 2.1, "end": 4.0, "speaker": "Host Woman", "text": "Let us begin.", "dub_text": "Let us begin."},
            {"index": 2, "start": 4.2, "end": 7.0, "speaker": "Mr. Guest", "text": "Thank you.", "dub_text": "Thank you."},
        ]
        casting = english.cast_voices(segs)
        self.assertTrue(casting["Host Woman"][0].startswith(("af_", "bf_")))
        self.assertTrue(casting["Mr. Guest"][0].startswith(("am_", "bm_")))
        utts = english.merge_utterances(segs, 10.0)
        self.assertEqual(len(utts), 2)
        self.assertEqual(utts[0]["speaker"], "Host Woman")
        self.assertIn("Welcome.", utts[0]["text_dub"])
        self.assertIn("Let us begin.", utts[0]["text_dub"])
        self.assertEqual(utts[1]["speaker"], "Mr. Guest")

    def test_build_srt_and_ass(self):
        segs = [
            {"start": 1.0, "end": 3.5, "speaker": "Interviewer", "text": "What happened {next}?", "text_orig": "发生什么了"},
            {"start": 3.5, "end": 6.0, "speaker": "Interviewer", "text": "Tell us.", "text_orig": "说说看"},
        ]
        srt = english.build_srt(segs, "text", include_speaker=True)
        self.assertIn("00:00:01,000 --> 00:00:03,500", srt)
        self.assertIn("[Interviewer] What happened {next}?", srt)
        self.assertNotIn("[Interviewer] Tell us.", srt)

        ass = english.build_ass(segs, title="Demo")
        self.assertIn("Style: Box,", ass)
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:03.50,Box", ass)
        self.assertIn("\\{next\\}", ass)


class EnglishStorageAndWebTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        make_video(
            self.data,
            VIDEO,
            shots=2,
            extra={"channel": "Doc Creator", "channel_id": "UCdoc", "duration": 600, "title": "Original Title"},
        )
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nbonjour monde\n"
        orig_art = {
            "video_id": VIDEO,
            "model": "large-v3",
            "language": "fr",
            "language_probability": 0.99,
            "duration": 600.0,
            "full_text": "bonjour monde",
            "segments": [{"index": 0, "start": 1.0, "end": 3.0, "text": "bonjour monde", "speaker": "", "words": []}],
            "vtt_sha256": hashlib.sha256(vtt.encode()).hexdigest(),
        }
        transcript_vtt_path(self.data, VIDEO).write_text(vtt, encoding="utf-8")
        transcript_json_path(self.data, VIDEO).write_text(json.dumps(orig_art), encoding="utf-8")
        english_dub_path(self.data, VIDEO).write_bytes(b"fake-m4a-audio")
        self.en_artifact = {
            "format_version": 1,
            "video_id": VIDEO,
            "title": "Original Title",
            "work_key": "wk-en",
            "generation_id": "gen-1",
            "source_sha256": "abc",
            "created_at": "2026-11-25T10:00:00+00:00",
            "engine": "whisper+openai",
            "provider": "openai",
            "model": "gpt-6-luna",
            "language": "en",
            "source_language": "fr",
            "duration": 600.0,
            "chapters": [{"start": 0, "end": 600, "title": "Opening"}],
            "segments": [
                {
                    "index": 0,
                    "start": 1.0,
                    "end": 3.0,
                    "speaker": "Host",
                    "text": "Hello sunshine world",
                    "dub_text": "Hello sunshine world",
                    "text_orig": "bonjour monde",
                },
                {
                    "index": 1,
                    "start": 3.5,
                    "end": 6.0,
                    "speaker": "Guest",
                    "text": "Unique telescope phrase",
                    "dub_text": "Unique telescope phrase",
                    "text_orig": "phrase unique",
                },
            ],
        }
        english._publish_english_files(self.data, VIDEO, self.en_artifact, has_dub=True)
        db.rebuild(self.data)

    def _request(self, handler_class, path, method="GET", payload=None):
        handler = handler_class.__new__(handler_class)
        raw = json.dumps(payload if payload is not None else {}).encode()
        handler.path, handler.command, handler.request_version = path, method, "HTTP/1.1"
        handler.requestline, handler.client_address = f"{method} {path} HTTP/1.1", ("127.0.0.1", 0)
        handler.headers = {
            "Host": "localhost:8765",
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
            "Origin": "http://localhost:8765",
        }
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        headers = {}

        def _capture_header(k, v):
            headers[k.lower()] = v

        handler.send_response = Mock()
        handler.send_header = _capture_header
        handler.end_headers = Mock()
        handler.log_message = lambda *args: None
        getattr(handler, "do_" + method)()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue(), headers

    def test_publish_search_rebuild_and_speaker_edit(self):
        self.assertTrue(english_is_complete(self.data, VIDEO))
        self.assertTrue(english_has_dub(self.data, VIDEO))
        self.assertTrue(english_ass_path(self.data, VIDEO).is_file())
        self.assertTrue(english_srt_path(self.data, VIDEO).is_file())
        self.assertTrue(orig_srt_path(self.data, VIDEO).is_file())

        # Search matches English words globally and on creator page
        found = db.list_videos(self.data, query="telescope")
        self.assertEqual([r["video_id"] for r in found], [VIDEO])
        self.assertTrue(found[0]["has_english"])
        self.assertTrue(found[0]["has_english_dub"])

        creator_found = db.list_creator_videos(self.data, channel_id="UCdoc", query="sunshine")
        self.assertEqual([r["video_id"] for r in creator_found], [VIDEO])

        # Rebuild restores English transcript from disk alone
        db.rebuild(self.data)
        restored = db.get_english_transcript(self.data, VIDEO)
        self.assertEqual(len(restored["segments"]), 2)
        self.assertEqual(restored["segments"][1]["text"], "Unique telescope phrase")
        self.assertEqual(restored["segments"][1]["text_orig"], "phrase unique")

        # Speaker edits update JSON, VTT, ASS, and DB
        english.update_english_segment_speaker(self.data, VIDEO, 0, "Alice")
        english.rename_english_speaker(self.data, VIDEO, "Guest", "Bob")
        updated = db.get_english_transcript(self.data, VIDEO)
        self.assertEqual([s["speaker"] for s in updated["segments"]], ["Alice", "Bob"])
        self.assertIn("Alice:", english_ass_path(self.data, VIDEO).read_text(encoding="utf-8"))
        self.assertIn("[Bob]", english_srt_path(self.data, VIDEO).read_text(encoding="utf-8"))

    def test_work_recovery_handles_english_kind(self):
        job = db.enqueue_work(
            self.data, work_key="en-recover", video_id=VIDEO, kind="english", config={"include_dub": True}
        )
        db.update_work_job(self.data, job["job_id"], status="running", message="translating")
        self.assertEqual(db.recover_work_jobs(self.data), 1)
        recovered = db.get_work_job(self.data, job["job_id"])
        self.assertEqual(recovered["status"], "queued")
        self.assertEqual(recovered["message"], "recovered after service restart")

    def test_export_english_mkv_invokes_ffmpeg(self):
        calls = []

        def fake_run(cmd, check=True):
            calls.append(cmd)
            Path(cmd[-1]).write_bytes(b"fake-mkv-bytes")
            return subprocess.CompletedProcess(cmd, 0)

        with patch("subprocess.run", side_effect=fake_run):
            out = english.export_english_mkv(self.data, VIDEO)
            self.assertEqual(out, english_mkv_path(self.data, VIDEO))
            self.assertEqual(len(calls), 1)
            cmd = calls[0]
            self.assertIn("title=English (AI Dub)", cmd)
            self.assertIn("title=English (Styled)", cmd)
            # Second call reuses cached MKV
            english.export_english_mkv(self.data, VIDEO)
            self.assertEqual(len(calls), 1)

    def test_detail_html_and_english_api_routes(self):
        secret = "sk-never-leak-to-html"
        fake_settings = {
            "live_clip_max_seconds": 1800.0,
            "english": {
                **load_settings(Path("/nonexistent/settings.json"))["english"],
                "api_key": secret,
            },
        }
        with patch("yt_archive.web.video.load_settings", return_value=fake_settings):
            html = detail_html(
                db.list_videos(self.data)[0],
                self.data,
                english_job={"job_id": "enjob123", "status": "running"},
            ).decode()
        self.assertNotIn(secret, html)
        self.assertIn("data-audio-tracks=", html)
        self.assertIn('data-original-audio-label="Original"', html)
        self.assertIn('srclang="en" label="English"', html)
        self.assertIn('srclang="fr" label="Original (fr)"', html)
        self.assertIn('id="cover-burned-cc"', html)
        self.assertIn('id="transcript-lang"', html)
        self.assertIn('id="english-dialog"', html)
        self.assertIn('data-english-job-id="enjob123"', html)

        # Missing api_key renders cleanly with an inline configuration error instead of crashing
        unconfigured = {
            "live_clip_max_seconds": 1800.0,
            "english": {**fake_settings["english"], "api_key": ""},
        }
        with patch("yt_archive.web.video.load_settings", return_value=unconfigured):
            err_html = detail_html(db.list_videos(self.data)[0], self.data).decode()
        self.assertIn("dialog-error", err_html)
        self.assertIn('id="start-english" disabled', err_html)

        queue = fake_queue(self.data)
        submit_english = Mock(return_value={"job_id": "ej1", "status": "queued"})
        work_queue = SimpleNamespace(
            get=lambda job_id: None,
            submit_transcription=Mock(),
            submit_english=submit_english,
        )
        handler_cls = make_handler(self.data, queue, work_queue)

        # GET /api/english/<id>
        code, raw, _ = self._request(handler_cls, f"/api/english/{VIDEO}")
        self.assertEqual(code, 200)
        payload = json.loads(raw)
        self.assertTrue(payload["has_dub"])
        self.assertIn("dub.en.m4a", payload["dub_url"])
        self.assertIn("subtitles.en.ass", payload["ass_url"])

        # ASS mime type is text/x-ssa, not audio/aac
        code, _, headers = self._request(handler_cls, f"/media/{VIDEO}/_condensed/subtitles.en.ass")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("content-type"), "text/x-ssa")

        # POST /api/english
        code, raw, _ = self._request(
            handler_cls,
            "/api/english",
            method="POST",
            payload={"video_id": VIDEO, "config": {"include_dub": True}},
        )
        self.assertEqual(code, 202)
        submit_english.assert_called_once()

        # POST /api/english/speaker & rename-speaker
        code, raw, _ = self._request(
            handler_cls,
            "/api/english/speaker",
            method="POST",
            payload={"video_id": VIDEO, "segment_index": 0, "speaker": "Narrator"},
        )
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["segments"][0]["speaker"], "Narrator")

        code, raw, _ = self._request(
            handler_cls,
            "/api/english/rename-speaker",
            method="POST",
            payload={"video_id": VIDEO, "old": "Narrator", "new": "Main Host"},
        )
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["segments"][0]["speaker"], "Main Host")

        # POST /api/english/mkv with open_vlc=True
        fake_mkv = english_mkv_path(self.data, VIDEO)
        fake_mkv.write_bytes(b"mkv")
        with patch("yt_archive.english.export_english_mkv", return_value=fake_mkv), \
                patch("subprocess.Popen") as popen:
            code, raw, _ = self._request(
                handler_cls,
                "/api/english/mkv",
                method="POST",
                payload={"video_id": VIDEO, "open_vlc": True},
            )
            self.assertEqual(code, 200)
            res = json.loads(raw)
            self.assertTrue(res["ok"])
            self.assertIn("english.mkv", res["url"])
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0][0], "vlc")


if __name__ == "__main__":
    unittest.main()
