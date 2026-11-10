"""Every page and API route answers, and no route needs a server error to say no: the net
under refactors of yt_archive/web/. Nothing here may start a process (yt-dlp, a file manager)."""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from yt_archive import db
from yt_archive.paths import bookmarklet_key, transcript_json_path, transcript_vtt_path
from yt_archive.web import STATIC_DIR, make_handler

from page_text import with_static
from test_queue import fake_queue, make_video

VIDEO = "smoke000001"
TWEET = "x-1234567890123456789"


class SmokeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        make_video(self.data, VIDEO, shots=3,
                   extra={"channel": "Smoke Channel", "channel_id": "UCsmoke", "duration": 80})
        make_video(self.data, TWEET, extra={"title": "a tweet", "duration": 12})
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nsmoke words\n"
        segment = {"index": 0, "start": 1, "end": 2, "text": "smoke words", "speaker": "", "words": []}
        artifact = {"video_id": VIDEO, "model": "large-v3", "full_text": "smoke words", "segments": [segment],
                    "vtt_sha256": hashlib.sha256(vtt.encode()).hexdigest()}
        transcript_vtt_path(self.data, VIDEO).write_text(vtt, encoding="utf-8")
        transcript_json_path(self.data, VIDEO).write_text(json.dumps(artifact), encoding="utf-8")
        db.rebuild(self.data)
        self.queue = fake_queue(self.data)
        self.job = self.queue.submit("queued00001")
        self.work = db.enqueue_work(self.data, work_key="smoke", video_id=VIDEO, kind="transcribe",
                                    config={"model": "large-v3"}, force=False)
        work_queue = SimpleNamespace(get=lambda job_id: db.get_work_job(self.data, job_id),
                                     submit_transcription=Mock(side_effect=ValueError("not in the smoke test")))
        self.handler_class = make_handler(self.data, self.queue, work_queue)
        self.key = bookmarklet_key(self.data)
        no_processes = patch("subprocess.Popen", side_effect=AssertionError("a route started a process"))
        no_processes.start()
        self.addCleanup(no_processes.stop)

    def request(self, path, method="GET", payload=None):
        handler = self.handler_class.__new__(self.handler_class)
        raw = json.dumps(payload if payload is not None else {}).encode()
        handler.path, handler.command, handler.request_version = path, method, "HTTP/1.1"
        handler.requestline, handler.client_address = f"{method} {path} HTTP/1.1", ("127.0.0.1", 0)
        handler.headers = {"Host": "localhost:8765", "Content-Type": "application/json",
                           "Content-Length": str(len(raw)), "Origin": "http://localhost:8765"}
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        handler.log_message = lambda *args: None
        getattr(handler, "do_" + method)()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue()

    def test_every_page_renders_with_the_files_it_links(self):
        pages = {
            "/": 200, "/?q=Smoke&sort=title": 200, "/browse": 200, "/browse?source=%40someone": 200,
            f"/add?url={VIDEO}&key={self.key}": 200, f"/add?url={VIDEO}": 403,
            "/creator?channel_id=UCsmoke": 200, "/creator?channel_id=UCsmoke&q=Title": 200,
            "/creator?channel=Nobody": 404,
            f"/v/{VIDEO}": 200, f"/v/{TWEET}": 200, "/v/missing0001": 404, "/v/not an id": 404,
            "/no-such-page": 404,
        }
        for path, expected in pages.items():
            with self.subTest(path=path):
                code, raw = self.request(path)
                self.assertEqual(code, expected)
                if code == 200:
                    with_static(raw.decode())

    def test_every_api_route_answers(self):
        routes = {
            "/api/list": 200, "/api/list?q=smoke": 200, "/api/jobs": 200, "/api/jobs?waiting=bad": 200,
            f"/api/job/{self.job['job_id']}": 200, "/api/job/nope": 404,
            f"/api/work/{self.work['job_id']}": 200, "/api/work/nope": 404,
            f"/api/transcript/{VIDEO}": 200, f"/api/transcript/{TWEET}": 404, "/api/transcript/bad": 404,
            "/api/creators/nope": 404,
        }
        for path, expected in routes.items():
            with self.subTest(path=path):
                code, raw = self.request(path)
                self.assertEqual(code, expected)
                json.loads(raw)

    def test_media_and_static_files_are_served(self):
        paths = [f"/media/{VIDEO}/archive.json", f"/media/{VIDEO}/_condensed/framesheet.png",
                 f"/media/{VIDEO}/_condensed/soundtrack.mp3"]
        paths += [f"/static/{p.relative_to(STATIC_DIR).as_posix()}" for p in STATIC_DIR.rglob("*") if p.is_file()]
        for path in paths:
            with self.subTest(path=path):
                code, raw = self.request(path)
                self.assertEqual(code, 200)
                self.assertTrue(raw)
        self.assertEqual(self.request(f"/media/{VIDEO}/../../etc/passwd")[0], 404)

    def test_every_post_route_refuses_an_empty_request_without_a_server_error(self):
        for path in ("/api/creators", "/api/creators/nope/get", "/api/update-archive", "/api/jobs/clear-waiting",
                     "/api/jobs/abc123/remove", "/api/jobs/abc123/dismiss", "/api/transcribe",
                     "/api/transcript/speaker", "/api/transcript/rename-speaker", "/api/open-folder", "/api/grab",
                     "/api/lyrics", "/api/get", "/api/no-such-route"):
            with self.subTest(path=path):
                code, _ = self.request(path, method="POST")
                self.assertLess(code, 500)


if __name__ == "__main__":
    unittest.main()
