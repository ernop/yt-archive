from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from yt_archive import db, gpu_gate, transcribe
from yt_archive.paths import transcript_json_path, transcript_vtt_path
from yt_archive.transcribe import _spoken_text, _timed_spoken_words, normalize_config
from yt_archive.web import detail_html


VIDEO_ID = "abcdefghijk"


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        folder = self.data / VIDEO_ID
        condensed = folder / "_condensed"
        condensed.mkdir(parents=True)
        (folder / "video.mp4").write_bytes(b"video")
        (condensed / "soundtrack.mp3").write_bytes(b"audio")
        (folder / "archive.json").write_text(
            json.dumps(
                {
                    "video_id": VIDEO_ID,
                    "title": "Test video",
                    "channel": "Test channel",
                    "duration": 80,
                }
            ),
            encoding="utf-8",
        )
        db.rebuild(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_work_identity_is_deduplicated_and_recovered(self):
        config = normalize_config()
        first = db.enqueue_work(
            self.data,
            work_key="same-work",
            video_id=VIDEO_ID,
            kind="transcribe",
            config=config,
        )
        second = db.enqueue_work(
            self.data,
            work_key="same-work",
            video_id=VIDEO_ID,
            kind="transcribe",
            config=config,
        )
        self.assertEqual(first["job_id"], second["job_id"])
        claimed = db.claim_next_work(self.data)
        self.assertEqual(claimed["status"], "running")
        forced_while_running = db.enqueue_work(
            self.data,
            work_key="same-work",
            video_id=VIDEO_ID,
            kind="transcribe",
            config=config,
            force=True,
        )
        self.assertEqual(forced_while_running["status"], "running")
        self.assertEqual(db.recover_work_jobs(self.data), 1)
        reclaimed = db.claim_next_work(self.data)
        self.assertEqual(reclaimed["job_id"], first["job_id"])
        self.assertEqual(reclaimed["attempts"], 2)

    def test_recovery_finishes_already_published_matching_work(self):
        job = db.enqueue_work(
            self.data,
            work_key="published-work",
            video_id=VIDEO_ID,
            kind="transcribe",
            config=normalize_config(),
        )
        db.claim_next_work(self.data)
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\npublished before restart\n"
        artifact = {
            "video_id": VIDEO_ID,
            "work_key": "published-work",
            "generation_id": f"{job['job_id']}:1",
            "vtt_sha256": hashlib.sha256(vtt.encode()).hexdigest(),
            "model": "large-v3",
            "full_text": "published before restart",
            "segments": [
                {
                    "index": 0,
                    "start": 1,
                    "end": 2,
                    "text": "published before restart",
                    "speaker": "",
                    "words": [],
                }
            ],
        }
        transcript_json_path(self.data, VIDEO_ID).write_text(
            json.dumps(artifact), encoding="utf-8"
        )
        transcript_vtt_path(self.data, VIDEO_ID).write_text(
            vtt,
            encoding="utf-8",
        )
        self.assertEqual(db.recover_work_jobs(self.data), 1)
        recovered = db.get_work_job(self.data, job["job_id"])
        self.assertEqual(recovered["status"], "done")
        self.assertEqual(recovered["progress"], 1)

    def test_recovery_retries_mixed_generation_files(self):
        job = db.enqueue_work(
            self.data,
            work_key="replacement-work",
            video_id=VIDEO_ID,
            kind="transcribe",
            config=normalize_config(),
        )
        db.claim_next_work(self.data)
        artifact = {
            "video_id": VIDEO_ID,
            "work_key": "replacement-work",
            "generation_id": f"{job['job_id']}:1",
            "vtt_sha256": hashlib.sha256(b"old captions").hexdigest(),
            "segments": [],
        }
        transcript_json_path(self.data, VIDEO_ID).write_text(
            json.dumps(artifact), encoding="utf-8"
        )
        transcript_vtt_path(self.data, VIDEO_ID).write_text(
            "new partial captions", encoding="utf-8"
        )
        db.recover_work_jobs(self.data)
        self.assertEqual(db.get_work_job(self.data, job["job_id"])["status"], "queued")

    def test_transcript_is_stored_and_global_search_finds_spoken_words(self):
        artifact = {
            "video_id": VIDEO_ID,
            "engine": "test",
            "model": "large-v3",
            "language": "en",
            "language_probability": 0.99,
            "duration": 80,
            "created_at": "2026-08-30T00:00:00+00:00",
            "full_text": "An uncommon telescope phrase",
            "segments": [
                {
                    "index": 0,
                    "start": 12.5,
                    "end": 14.5,
                    "text": "An uncommon telescope phrase",
                    "speaker": "Ada",
                    "words": [
                        {
                            "start": 12.5,
                            "end": 12.8,
                            "word": "An",
                            "probability": 0.99,
                        }
                    ],
                }
            ],
        }
        db.replace_transcript(self.data, artifact)
        loaded = db.get_transcript(self.data, VIDEO_ID)
        self.assertEqual(loaded["segments"][0]["speaker"], "Ada")
        self.assertEqual(
            [row["video_id"] for row in db.list_videos(self.data, "telescope")],
            [VIDEO_ID],
        )

    def test_rebuild_restores_database_from_transcript_file(self):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nrestorable words\n"
        artifact = {
            "video_id": VIDEO_ID,
            "model": "large-v3",
            "vtt_sha256": hashlib.sha256(vtt.encode()).hexdigest(),
            "full_text": "restorable words",
            "segments": [
                {
                    "index": 0,
                    "start": 1,
                    "end": 2,
                    "text": "restorable words",
                    "speaker": "",
                    "words": [],
                }
            ],
        }
        transcript_json_path(self.data, VIDEO_ID).write_text(
            json.dumps(artifact), encoding="utf-8"
        )
        transcript_vtt_path(self.data, VIDEO_ID).write_text(
            vtt,
            encoding="utf-8",
        )
        db.rebuild(self.data)
        self.assertEqual(
            db.get_transcript(self.data, VIDEO_ID)["full_text"], "restorable words"
        )

    def test_detail_page_opens_framesheets_in_new_tab(self):
        sheet = self.data / VIDEO_ID / "_condensed" / "framesheet.png"
        sheet.write_bytes(b"png")
        html = detail_html(
            {
                "video_id": VIDEO_ID,
                "title": "Test",
                "duration": 80,
                "channel": "Channel",
            },
            self.data,
        ).decode()
        self.assertIn('target="_blank" rel="noopener"', html)
        self.assertIn("MP3 audio", html)
        self.assertIn("Transcribe audio", html)

    def test_non_speech_labels_are_not_mixed_with_words(self):
        self.assertEqual(_spoken_text("[Music] Hello (birds chirping)"), "Hello")
        self.assertEqual(_spoken_text("[Applause]"), "")
        words = [
            SimpleNamespace(word=" [Music]", start=0, end=1, probability=1),
            SimpleNamespace(word=" Hello", start=1, end=2, probability=1),
            SimpleNamespace(word=" (birds", start=2, end=3, probability=1),
            SimpleNamespace(word=" chirping)", start=3, end=4, probability=1),
            SimpleNamespace(word=" again", start=4, end=5, probability=1),
        ]
        cleaned = _timed_spoken_words(
            words, "[Music] Hello (birds chirping) again"
        )
        self.assertEqual([word["word"] for word in cleaned], ["Hello", "again"])


class GpuGateTests(unittest.TestCase):
    """Whisper on the GPU waits for the gate before loading and lets go once done
    (docs/transcription.md#the-gpu-gate)."""

    setUp = TranscriptionTests.setUp
    tearDown = TranscriptionTests.tearDown

    def _fake_whisper(self, events, cuda_devices=1):
        class FakeModel:
            def __init__(self, name, **options):
                events.append(("load", options["device"]))

            def transcribe(self, path, **options):
                words = [SimpleNamespace(word=" Hello", start=0, end=1, probability=0.9)]
                segment = SimpleNamespace(text=" Hello", start=0.0, end=1.0, words=words)
                return iter([segment]), SimpleNamespace(
                    duration=80, language="en", language_probability=0.9
                )

        fake_modules = {
            "ctranslate2": SimpleNamespace(get_cuda_device_count=lambda: cuda_devices),
            "faster_whisper": SimpleNamespace(WhisperModel=FakeModel),
        }
        return [
            mock.patch.dict(sys.modules, fake_modules),
            mock.patch.object(transcribe, "_activate_cuda_libraries", return_value=(True, "")),
            mock.patch.object(
                transcribe, "_whisper_model",
                lambda WhisperModel, name, **options: WhisperModel(name, **options),
            ),
        ]

    def _run_with(self, patches):
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return transcribe.transcribe_soundtrack(self.data, VIDEO_ID, log=lambda message: None)

    def test_whisper_holds_the_gpu_before_loading_and_lets_go_after(self):
        events = []
        gate = SimpleNamespace(
            hold=lambda what, gb, **options: events.append(("hold", what, gb, options)),
            let_go=lambda what: events.append(("let go", what)),
        )
        patches = self._fake_whisper(events) + [mock.patch.object(gpu_gate, "load", return_value=gate)]
        artifact = self._run_with(patches)
        self.assertEqual(artifact["device"], "cuda")
        self.assertEqual([event[0] for event in events], ["hold", "load", "let go"])
        _, what, gb, options = events[0]
        self.assertEqual((what, gb), ("Whisper large-v3", gpu_gate.WHISPER_GB))
        self.assertEqual(options["lane"], "backfill")
        self.assertIsNone(options["wait"])
        self.assertEqual(options["minutes"], gpu_gate.whisper_minutes(80))
        self.assertEqual(events[2], ("let go", "Whisper large-v3"))

    def test_whisper_on_the_cpu_leaves_the_gate_alone(self):
        events = []
        loads = mock.patch.object(gpu_gate, "load", side_effect=AssertionError("no CPU hold"))
        artifact = self._run_with(self._fake_whisper(events, cuda_devices=0) + [loads])
        self.assertEqual(artifact["device"], "cpu")
        self.assertEqual(events, [("load", "cpu")])

    def test_the_gate_is_absent_without_its_file(self):
        with mock.patch.dict(os.environ, {"YT_ARCHIVE_GPU_GATE": str(self.data / "missing.py")}):
            self.assertIsNone(gpu_gate.load())

    def test_the_service_hears_the_childs_log_and_progress(self):
        lines = [
            '{"log": "loading Whisper large-v3 on cuda"}\n',
            '{"progress": 0.5, "message": "transcribing 0:40 / 1:20"}\n',
            "a library's own line\n",
        ]
        child = mock.MagicMock(stdout=iter(lines), returncode=0)
        child.__enter__.return_value = child
        logged, progressed = [], []
        with mock.patch.object(transcribe.subprocess, "Popen", return_value=child) as popen:
            transcribe.transcribe_in_child(
                self.data, VIDEO_ID, normalize_config(), log=logged.append,
                progress=lambda value, message: progressed.append((value, message)),
                work_key="k", source_sha256="s", generation_id="g",
            )
        spec = json.loads(popen.call_args.args[0][-1])
        self.assertEqual(spec["gpu"], gpu_gate.CLICKED)
        self.assertEqual(logged, ["loading Whisper large-v3 on cuda", "a library's own line"])
        self.assertEqual(progressed, [(0.5, "transcribing 0:40 / 1:20")])

    def test_a_failed_child_reports_why(self):
        (self.data / VIDEO_ID / "_condensed" / "soundtrack.mp3").unlink()
        with self.assertRaisesRegex(RuntimeError, "MP3 audio is missing"):
            transcribe.transcribe_in_child(
                self.data, VIDEO_ID, normalize_config(), log=lambda message: None,
                progress=lambda value, message: None,
                work_key="k", source_sha256="s", generation_id="g",
            )


if __name__ == "__main__":
    unittest.main()
