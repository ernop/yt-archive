"""Live stream recording: off-lane recording, /api/played-stop, cap, upcoming streams, settings.

Uses a local fake yt-dlp script (no YouTube traffic, no real video downloads).
"""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from yt_archive import db
from yt_archive.download import parse_live_status, write_archive_json
from yt_archive.jobs import JobQueue
from yt_archive.paths import (
    archive_json_path,
    condensed_dir,
    framesheet_paths,
    load_archive_info,
    preview_path,
    shots_json_path,
    soundtrack_path,
    video_dir,
)
from yt_archive.settings import DEFAULT_LIVE_CLIP_MAX_SECONDS, load_settings
from yt_archive.web import make_handler


FAKE_YT_DLP_TEMPLATE = textwrap.dedent(
    """\
    #!{python}
    import json
    import re
    import signal
    import sys
    import time
    from pathlib import Path

    SPEC_PATH = Path({spec_path!r})
    TRACE_PATH = Path({trace_path!r})

    def record_trace(entry):
        with TRACE_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\\n")

    def main():
        args = sys.argv[1:]
        url = args[-1]
        out_tmpl = args[args.index("-o") + 1]
        dest = Path(out_tmpl).parent
        match = re.search(r"(?:v=|/)([A-Za-z0-9_-]{{11}})$", url)
        video_id = match.group(1) if match else url
        spec = json.loads(SPEC_PATH.read_text(encoding="utf-8")).get(video_id, {{"mode": "normal"}})
        mode = spec.get("mode", "normal")
        record_trace({{"event": "start", "video_id": video_id, "mode": mode}})

        def write_files(duration=None, is_live=False, live_status="not_live"):
            title = spec.get("title", f"Sample_{{video_id}}")
            video = dest / f"{{title}}-{{video_id}}.mp4"
            video.write_bytes(b"fake-mp4-bytes-" + video_id.encode())
            info = {{
                "id": video_id,
                "webpage_url": f"https://www.youtube.com/watch?v={{video_id}}",
                "title": title.replace("_", " "),
                "channel": "Sample Channel",
                "channel_id": "UCsample",
                "duration": duration,
                "upload_date": "20261120",
                "is_live": is_live,
                "live_status": live_status,
                "width": 1280,
                "height": 720,
                "fps": 30,
                "vcodec": "avc1",
                "acodec": "mp4a",
            }}
            (dest / f"{{title}}-{{video_id}}.info.json").write_text(json.dumps(info), encoding="utf-8")

        if mode == "upcoming_error":
            print(f"ERROR: [youtube] {{video_id}}: This live event will begin in 2 hours.", flush=True)
            sys.exit(1)

        if mode == "is_upcoming":
            print("ytarchive-live is_upcoming False", flush=True)
            time.sleep(5)
            sys.exit(1)

        if mode in ("normal", "was_live", "post_live"):
            live_status = "not_live" if mode == "normal" else mode
            print(f"ytarchive-live {{live_status}} False", flush=True)
            delay = float(spec.get("delay", 0))
            if delay > 0:
                time.sleep(delay)
            write_files(duration=spec.get("duration", 42), is_live=False, live_status=live_status)
            print("downloaded 1280x720 136 mp4", flush=True)
            record_trace({{"event": "done", "video_id": video_id, "mode": mode}})
            sys.exit(0)

        if mode == "is_live":
            got_sigint = []

            def on_sigint(signum, frame):
                got_sigint.append(signum)

            signal.signal(signal.SIGINT, on_sigint)
            print("ytarchive-live is_live True", flush=True)
            end_after = spec.get("end_after")
            deadline = time.monotonic() + (float(end_after) if end_after is not None else 15.0)
            while not got_sigint and time.monotonic() < deadline:
                time.sleep(0.02)
            if got_sigint:
                record_trace({{"event": "sigint", "video_id": video_id}})
            else:
                record_trace({{"event": "ended", "video_id": video_id}})
            if not spec.get("no_output"):
                write_files(duration=None, is_live=True, live_status="is_live")
                print("downloaded 1280x720 96 mp4", flush=True)
            sys.exit(0)

        print(f"ERROR: unknown fake mode {{mode}}", flush=True)
        sys.exit(2)

    if __name__ == "__main__":
        main()
    """
)


def _fake_make_shots(video, data_dir, video_id, **kwargs):
    cdir = condensed_dir(data_dir, video_id)
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "framesheet.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    preview_path(data_dir, video_id).write_bytes(b"\xff\xd8\xff\xd9")
    shots_json_path(data_dir, video_id).write_text(
        json.dumps({"shots_kept": 2, "layout": "squash", "shots": []}),
        encoding="utf-8",
    )
    return {"shots_kept": 2}


def _fake_dump_soundtrack(video, data_dir, video_id, **kwargs):
    cdir = condensed_dir(data_dir, video_id)
    cdir.mkdir(parents=True, exist_ok=True)
    soundtrack_path(data_dir, video_id).write_bytes(b"ID3fake")
    return True


def _wait_until(predicate, timeout: float = 10.0, interval: float = 0.03):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


class LiveStreamQueueTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.spec_path = self.root / "ytdlp-spec.json"
        self.trace_path = self.root / "ytdlp-trace.jsonl"
        self.spec: dict[str, dict] = {}
        self._write_spec()
        self.fake_bin = self.root / "fake-yt-dlp"
        self.fake_bin.write_text(
            FAKE_YT_DLP_TEMPLATE.format(
                python=sys.executable,
                spec_path=str(self.spec_path),
                trace_path=str(self.trace_path),
            ),
            encoding="utf-8",
        )
        self.fake_bin.chmod(self.fake_bin.stat().st_mode | stat.S_IXUSR)

        for patcher in (
            patch("yt_archive.download.yt_dlp_bin", return_value=str(self.fake_bin)),
            patch("yt_archive.download._probe_duration", return_value=123.5),
            patch("yt_archive.download._log_resolution"),
            patch("yt_archive.jobs.make_shots", side_effect=_fake_make_shots),
            patch("yt_archive.jobs.dump_soundtrack", side_effect=_fake_dump_soundtrack),
            patch("yt_archive.budget.wait_and_take"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write_spec(self):
        self.spec_path.write_text(json.dumps(self.spec), encoding="utf-8")

    def _trace(self) -> list[dict]:
        if not self.trace_path.is_file():
            return []
        return [
            json.loads(line)
            for line in self.trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _make_queue(self, *, live_clip_max_seconds: float = 30.0) -> JobQueue:
        queue = JobQueue(self.data, live_clip_max_seconds=live_clip_max_seconds)
        self.addCleanup(queue.stop_all_live)
        return queue

    def _request(self, handler_class, path, method="GET", payload=None, origin="http://localhost:8765"):
        handler = handler_class.__new__(handler_class)
        raw = json.dumps(payload if payload is not None else {}).encode()
        handler.path, handler.command, handler.request_version = path, method, "HTTP/1.1"
        handler.requestline, handler.client_address = f"{method} {path} HTTP/1.1", ("127.0.0.1", 0)
        handler.headers = {
            "Host": "localhost:8765",
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
        }
        if origin is not None:
            handler.headers["Origin"] = origin
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        handler.log_message = lambda *args: None
        getattr(handler, "do_" + method)()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue()

    def test_live_stream_stopped_by_played_stop_signal(self):
        vid = "live0000001"
        self.spec[vid] = {"mode": "is_live", "title": "City_Cam"}
        self._write_spec()
        queue = self._make_queue(live_clip_max_seconds=30.0)
        work_queue = SimpleNamespace(get=lambda job_id: None)
        handler_class = make_handler(self.data, queue, work_queue)

        job = queue.submit(vid, via="played")
        self.assertTrue(_wait_until(lambda: vid in queue.active_live_video_ids()))

        code, body = self._request(handler_class, "/api/played-stop", "POST", {"video_id": vid})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["status"], "stopping")

        done = _wait_until(
            lambda: (j := db.get_job(self.data, job["job_id"])) and j["status"] == "done" and j
        )
        self.assertTrue(done, f"job did not finish: {db.get_job(self.data, job['job_id'])}")
        self.assertEqual(done["download"], "done")
        self.assertEqual(done["shots"], "done")
        self.assertEqual(done["audio"], "done")

        self.assertIn({"event": "sigint", "video_id": vid}, self._trace())
        info = load_archive_info(self.data, vid)
        self.assertEqual(info["duration"], 123.5)
        self.assertEqual(info["live_clip"]["stopped_by"], "stop signal")
        self.assertTrue(info["live_clip"]["started_at"])
        self.assertTrue(info["live_clip"]["ended_at"])
        self.assertTrue(framesheet_paths(self.data, vid))
        self.assertTrue(soundtrack_path(self.data, vid).is_file())

        page_code, html = self._request(handler_class, f"/v/{vid}")
        self.assertEqual(page_code, 200)
        text = html.decode("utf-8")
        self.assertIn("live clip", text)
        self.assertIn("(stop signal)", text)
        self.assertIn(info["live_clip"]["started_at"], text)
        self.assertIn(info["live_clip"]["ended_at"], text)

    def test_live_stream_stopped_by_cap_when_no_stop_signal_arrives(self):
        vid = "livecap0001"
        self.spec[vid] = {"mode": "is_live", "title": "Harbor_Cam"}
        self._write_spec()
        queue = self._make_queue(live_clip_max_seconds=0.35)

        job = queue.submit(vid)
        done = _wait_until(
            lambda: (j := db.get_job(self.data, job["job_id"])) and j["status"] == "done" and j
        )
        self.assertTrue(done, f"job did not finish: {db.get_job(self.data, job['job_id'])}")
        self.assertIn({"event": "sigint", "video_id": vid}, self._trace())
        info = load_archive_info(self.data, vid)
        self.assertEqual(info["live_clip"]["stopped_by"], "cap")
        self.assertEqual(done["shots"], "done")
        self.assertEqual(done["audio"], "done")

    def test_live_stream_that_ends_naturally_records_stream_ended(self):
        vid = "liveend0001"
        self.spec[vid] = {"mode": "is_live", "end_after": 0.15}
        self._write_spec()
        queue = self._make_queue(live_clip_max_seconds=30.0)

        job = queue.submit(vid)
        done = _wait_until(
            lambda: (j := db.get_job(self.data, job["job_id"])) and j["status"] == "done" and j
        )
        self.assertTrue(done)
        info = load_archive_info(self.data, vid)
        self.assertEqual(info["live_clip"]["stopped_by"], "stream ended")

    def test_live_stream_does_not_block_queued_normal_video(self):
        live_id = "liveblk0001"
        normal_id = "normblk0001"
        self.spec[live_id] = {"mode": "is_live"}
        self.spec[normal_id] = {"mode": "normal", "duration": 95}
        self._write_spec()
        queue = self._make_queue(live_clip_max_seconds=30.0)

        live_job = queue.submit(live_id)
        self.assertTrue(_wait_until(lambda: live_id in queue.active_live_video_ids()))

        normal_job = queue.submit(normal_id)
        normal_done = _wait_until(
            lambda: (j := db.get_job(self.data, normal_job["job_id"])) and j["status"] == "done" and j
        )
        self.assertTrue(normal_done, "normal video stayed blocked behind active live stream")
        # Live stream is still recording while the normal video completed!
        self.assertIn(live_id, queue.active_live_video_ids())

        queue.stop_played(live_id)
        live_done = _wait_until(
            lambda: (j := db.get_job(self.data, live_job["job_id"])) and j["status"] == "done" and j
        )
        self.assertTrue(live_done)
        self.assertEqual(load_archive_info(self.data, live_id)["live_clip"]["stopped_by"], "stop signal")
        self.assertNotIn("live_clip", load_archive_info(self.data, normal_id))

    def test_upcoming_streams_fail_immediately_with_clear_message(self):
        self.spec["upcom000001"] = {"mode": "is_upcoming"}
        self.spec["upcom000002"] = {"mode": "upcoming_error"}
        self._write_spec()
        queue = self._make_queue()

        for vid in ("upcom000001", "upcom000002"):
            with self.subTest(video_id=vid):
                job = queue.submit(vid)
                failed = _wait_until(
                    lambda jid=job["job_id"]: (
                        (j := db.get_job(self.data, jid)) and j["status"] == "error" and j
                    )
                )
                self.assertTrue(failed)
                self.assertIn("stream has not started", failed["error"])
                self.assertFalse(video_dir(self.data, vid).exists())

    def test_finished_streams_use_normal_download_path(self):
        self.spec["waslive0001"] = {"mode": "was_live", "duration": 600}
        self.spec["postliv0001"] = {"mode": "post_live", "duration": 600}
        self._write_spec()
        queue = self._make_queue()

        for vid in ("waslive0001", "postliv0001"):
            with self.subTest(video_id=vid):
                job = queue.submit(vid)
                done = _wait_until(
                    lambda jid=job["job_id"]: (
                        (j := db.get_job(self.data, jid)) and j["status"] == "done" and j
                    )
                )
                self.assertTrue(done)
                info = load_archive_info(self.data, vid)
                self.assertNotIn("live_clip", info)
                self.assertEqual(info["duration"], 600)

    def test_early_stop_while_queued_cancels_live_but_ignores_normal_video(self):
        blocker = "block000001"
        live_id = "earlylv0001"
        normal_id = "earlynm0001"
        self.spec[blocker] = {"mode": "normal", "delay": 0.4}
        self.spec[live_id] = {"mode": "is_live"}
        self.spec[normal_id] = {"mode": "normal"}
        self._write_spec()
        queue = self._make_queue()

        queue.submit(blocker)
        live_job = queue.submit(live_id)
        normal_job = queue.submit(normal_id)

        # Send stop while both are still queued behind blocker.
        self.assertEqual(queue.stop_played(live_id)["status"], "noted")
        self.assertEqual(queue.stop_played(normal_id)["status"], "noted")

        live_done = _wait_until(
            lambda: (j := db.get_job(self.data, live_job["job_id"])) and j["status"] == "done" and j
        )
        normal_done = _wait_until(
            lambda: (j := db.get_job(self.data, normal_job["job_id"])) and j["status"] == "done" and j
        )
        self.assertTrue(live_done)
        self.assertIn("stopped before recording started", live_done["message"])
        self.assertFalse(video_dir(self.data, live_id).exists())
        self.assertTrue(normal_done)
        self.assertTrue(archive_json_path(self.data, normal_id).is_file())

    def test_played_stop_endpoint_validation_and_guards(self):
        queue = self._make_queue()
        work_queue = SimpleNamespace(get=lambda job_id: None)
        handler_class = make_handler(self.data, queue, work_queue)

        code, body = self._request(handler_class, "/api/played-stop", "POST", {"video_id": "bad"})
        self.assertEqual(code, 400)
        self.assertIn("error", json.loads(body))

        code, body = self._request(handler_class, "/api/played-stop", "POST", {"video_id": "idle0000001"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"video_id": "idle0000001", "status": "idle"})

        code, _ = self._request(
            handler_class, "/api/played-stop", "POST", {"video_id": "idle0000001"},
            origin="https://evil.example",
        )
        self.assertEqual(code, 403)


class LiveHelpersAndSettingsTests(unittest.TestCase):
    def test_parse_live_status(self):
        self.assertEqual(parse_live_status("ytarchive-live is_live True"), "is_live")
        self.assertEqual(parse_live_status("ytarchive-live NA True"), "is_live")
        self.assertEqual(parse_live_status("ytarchive-live is_upcoming False"), "is_upcoming")
        self.assertEqual(parse_live_status("ytarchive-live was_live False"), "was_live")
        self.assertEqual(parse_live_status("ytarchive-live not_live False"), "not_live")
        self.assertIsNone(parse_live_status("ytarchive-progress downloading 1 2 2 1 1 avc1"))

    def test_settings_defaults_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            self.assertEqual(
                load_settings(path),
                {"live_clip_max_seconds": float(DEFAULT_LIVE_CLIP_MAX_SECONDS)},
            )
            path.write_text(json.dumps({"live_clip_max_seconds": 1800}), encoding="utf-8")
            self.assertEqual(load_settings(path), {"live_clip_max_seconds": 1800.0})

            for bad in (
                "{not json",
                "[]",
                json.dumps({"unknown_key": 1}),
                json.dumps({"live_clip_max_seconds": 0}),
                json.dumps({"live_clip_max_seconds": -10}),
                json.dumps({"live_clip_max_seconds": True}),
                json.dumps({"live_clip_max_seconds": "3600"}),
            ):
                with self.subTest(bad=bad):
                    path.write_text(bad, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_settings(path)

    def test_write_archive_json_probes_duration_for_live_clip_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            vid = "probe000001"
            dest = video_dir(data_dir, vid)
            dest.mkdir(parents=True)
            video = dest / f"Cam-{vid}.mp4"
            video.write_bytes(b"mp4")
            (dest / f"Cam-{vid}.info.json").write_text(
                json.dumps({"id": vid, "title": "Cam", "duration": None}), encoding="utf-8"
            )
            with patch("yt_archive.download._probe_duration", return_value=75.25):
                write_archive_json(
                    data_dir,
                    vid,
                    video,
                    extra={
                        "live_clip": {
                            "started_at": "2026-11-20T12:00:00+00:00",
                            "ended_at": "2026-11-20T12:01:15+00:00",
                            "stopped_by": "stop signal",
                        }
                    },
                )
            info = load_archive_info(data_dir, vid)
            self.assertEqual(info["duration"], 75.25)
            self.assertEqual(info["live_clip"]["stopped_by"], "stop signal")


if __name__ == "__main__":
    unittest.main()
