from __future__ import annotations

import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from yt_archive import db
from yt_archive.creators import CreatorBrowser, creator_url, fetch_listing, parse_listing
from yt_archive.jobs import JobQueue
from yt_archive.web import make_handler


def raw_listing():
    return {"title": "Test creator", "entries": [
        {"title": "Videos", "entries": [
            {"id": "saved000001", "title": "Already saved", "duration": 80},
            {"id": "queued00001", "title": "Already queued"},
            {"id": "choose00001", "title": "Choose this <video>", "duration": 130},
            {"id": "choose00002", "title": "Another video"},
            {"id": "private0001", "title": "[Private video]"},
            None,
        ]},
        {"title": "Shorts", "entries": [
            {"id": "short000001", "title": "A Short"},
            {"id": "choose00001", "title": "Duplicate"},
        ]},
        {"title": "Live", "entries": [
            {"id": "pastlive001", "title": "Past stream", "live_status": "was_live"},
            {"id": "livenow0001", "title": "Live now", "live_status": "is_live"},
            {"id": "upcoming001", "title": "Upcoming", "live_status": "is_upcoming"},
            {"id": "../bad", "title": "Invalid ID"},
        ]},
    ]}


class CreatorParsingTests(unittest.TestCase):
    def test_supported_creator_inputs(self):
        channel = "UC" + "a" * 22
        for source, expected in [
            (" @Example ", "@Example"), ("Example", "@Example"),
            (channel, "channel/" + channel),
            ("https://youtube.com/@Example/videos?view=0#top", "@Example"),
            ("youtube.com/@Example/shorts", "@Example"),
            ("https://m.youtube.com/user/OldName/streams/", "user/OldName"),
            ("www.youtube.com/c/Custom/about", "c/Custom"),
            ("https://youtube.com/Custom", "Custom"),
            ("https://youtube.com/channel/" + channel, "channel/" + channel),
            ("https://www.youtube.com/@%E6%97%A5%E6%9C%AC", "@%E6%97%A5%E6%9C%AC"),
        ]:
            with self.subTest(source=source):
                self.assertEqual(creator_url(source), "https://www.youtube.com/" + expected)

    def test_rejects_non_creator_and_non_youtube_targets(self):
        for source in [None, 3, "", "two names", "https://example.com/@name",
                       "https://youtube.com.evil.test/@name", "file:///tmp/video",
                       "https://youtube.com/watch?v=choose00001", "https://youtube.com/playlist?list=123",
                       "https://youtube.com/shorts/choose00001", "https://youtube.com/@name/../../watch",
                       "https://youtube.com/channel/invalid", "https://youtube.com/@bad%2Fname"]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                creator_url(source)

    def test_nested_tabs_deduplicate_and_mark_unavailable(self):
        listing = parse_listing(raw_listing())
        self.assertEqual(listing["title"], "Test creator")
        rows = {row["video_id"]: row for row in listing["items"]}
        self.assertEqual(len(rows), 9)
        self.assertEqual(rows["choose00001"]["title"], "Choose this <video>")
        self.assertFalse(rows["pastlive001"]["unavailable"])
        for video_id in ["private0001", "livenow0001", "upcoming001"]:
            self.assertTrue(rows[video_id]["unavailable"])
        self.assertIn("short000001", rows)
        self.assertEqual(parse_listing({"entries": []})["items"], [])

    @patch("yt_archive.creators.yt_dlp_bin", return_value="yt-dlp")
    @patch("yt_archive.creators.subprocess.run")
    def test_metadata_only_command_and_partial_failure(self, run, binary):
        run.return_value = SimpleNamespace(stdout=json.dumps(raw_listing()),
                                          stderr="ERROR: one tab failed", returncode=1)
        result = fetch_listing("https://www.youtube.com/@Example")
        self.assertIn("incomplete", result["warning"])
        self.assertEqual(len(result["items"]), 9)
        command = run.call_args.args[0]
        self.assertIn("--skip-download", command)
        self.assertIn("--ignore-config", command)
        self.assertIn("--flat-playlist", command)
        self.assertNotIn("--playlist-end", command)
        run.return_value = SimpleNamespace(stdout="", stderr="ERROR: creator not found", returncode=1)
        with self.assertRaisesRegex(RuntimeError, "creator not found"):
            fetch_listing("https://www.youtube.com/@Example")
        run.side_effect = subprocess.TimeoutExpired(command, 600)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            fetch_listing("https://www.youtube.com/@Example")


class CreatorQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        folder = self.data / "saved000001"
        folder.mkdir()
        (folder / "original.mkv").write_bytes(b"original")
        db.insert_job(self.data, "queued00001", "queued00001")
        # Use the real submission path without starting the media worker.
        self.queue = JobQueue.__new__(JobQueue)
        self.queue.data_dir = self.data
        self.queue._cv = threading.Condition()
        self.browser = CreatorBrowser(self.data, self.queue)

    def load(self):
        with patch("yt_archive.creators.fetch_listing", return_value={**parse_listing(raw_listing()), "warning": ""}):
            started = self.browser.start("@Example")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                result = self.browser.get(started["lookup_id"])
                if result["status"] != "loading":
                    self.assertEqual(result["status"], "done")
                    return result
                time.sleep(.01)
        self.fail("Lookup did not complete")

    def test_browse_is_read_only_then_selected_and_all_queue_missing(self):
        listing = self.load()
        self.assertEqual(len(db.list_jobs(self.data)), 1)
        rows = {row["video_id"]: row for row in listing["items"]}
        self.assertTrue(rows["saved000001"]["archived"])
        self.assertTrue(rows["queued00001"]["queued"])
        result = self.browser.enqueue(listing["lookup_id"], video_ids=["choose00001", "choose00001"])
        self.assertEqual(result["video_ids"], ["choose00001"])
        result = self.browser.enqueue(listing["lookup_id"], all_items=True)
        self.assertEqual(set(result["video_ids"]), {"choose00002", "short000001", "pastlive001"})
        self.assertEqual(result["skipped"], 3)
        self.assertEqual(self.browser.enqueue(listing["lookup_id"], all_items=True)["queued"], 0)
        self.assertEqual((self.data / "saved000001" / "original.mkv").read_bytes(), b"original")

    def test_invalid_selection_cannot_partially_enqueue(self):
        listing = self.load()
        for selection in [["choose00001", "notinlist01"], ["livenow0001"], [None], "choose00001", []]:
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                self.browser.enqueue(listing["lookup_id"], video_ids=selection)
        self.assertEqual(len(db.list_jobs(self.data)), 1)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.browser.enqueue("unknown", all_items=True)

    def test_idle_preview_expires_without_discarding_queued_downloads(self):
        listing = self.load()
        self.browser.enqueue(listing["lookup_id"], video_ids=["choose00001"])
        with patch("yt_archive.creators.time.monotonic", return_value=time.monotonic() + 3601):
            self.assertIsNone(self.browser.get(listing["lookup_id"]))
        self.assertIsNotNone(db.find_open_job(self.data, "choose00001"))

    def test_concurrent_bulk_requests_queue_each_video_once_and_survive_restart(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: db.enqueue_missing_videos(
                self.data, ["choose00001", "choose00002"]), range(4)))
        self.assertEqual(sum(result["queued"] for result in results), 2)
        self.assertEqual(len(db.list_jobs(self.data)), 3)
        claimed = db.claim_next_job(self.data)
        self.assertEqual(claimed["status"], "running")
        db.recover_jobs(self.data)
        self.assertTrue(all(job["status"] == "queued" for job in db.list_jobs(self.data)))

    def test_async_lookup_deduplicates_loading_and_surfaces_failures(self):
        release = threading.Event()
        finished = threading.Event()

        def fail(url):
            release.wait(2)
            finished.set()
            raise RuntimeError("YouTube lookup failed")

        with patch("yt_archive.creators.fetch_listing", side_effect=fail):
            first = self.browser.start("@Example")
            second = self.browser.start("https://youtube.com/@Example/videos")
            self.assertEqual(first["lookup_id"], second["lookup_id"])
            with self.assertRaisesRegex(ValueError, "finish loading"):
                self.browser.enqueue(first["lookup_id"], all_items=True)
            release.set()
            finished.wait(2)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                result = self.browser.get(first["lookup_id"])
                if result["status"] == "error":
                    break
                time.sleep(.01)
            self.assertEqual(result["error"], "YouTube lookup failed")

    def test_http_routes_preserve_video_paste_and_route_creators_to_picker(self):
        handler_class = make_handler(self.data, self.queue, Mock())

        def request(path, payload=None, method="POST", extra_headers=None):
            handler = handler_class.__new__(handler_class)
            raw = json.dumps(payload or {}).encode()
            handler.path = path
            handler.headers = {"Host": "localhost:8765", "Content-Type": "application/json",
                               "Content-Length": str(len(raw)), **(extra_headers or {})}
            handler.rfile = io.BytesIO(raw)
            handler.wfile = io.BytesIO()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            getattr(handler, "do_" + method)()
            return handler.send_response.call_args.args[0], handler.wfile.getvalue()

        for source in ["@Example", "Example", "https://youtube.com/@Example/videos"]:
            code, raw = request("/api/get", {"url": source})
            self.assertEqual(code, 200)
            self.assertIn("/browse?source=", json.loads(raw)["browse_url"])
        code, raw = request("/api/get", {"url": "https://youtu.be/choose00001"})
        self.assertEqual(code, 202)
        self.assertEqual(json.loads(raw)["video_id"], "choose00001")
        code, raw = request("/browse?source=%22%3E%3Cscript%3E", method="GET")
        self.assertEqual(code, 200)
        self.assertIn(b'&quot;&gt;&lt;script&gt;', raw)
        self.assertIn(b'creators.js', raw)
        code, _ = request("/api/creators", {"source": "@Example"},
                          extra_headers={"Origin": "https://evil.test"})
        self.assertEqual(code, 403)
        code, _ = request("/api/creators", {"source": ["bad"]})
        self.assertEqual(code, 400)
        code, _ = request("/api/creators/missing/get", {"all": "false"})
        self.assertEqual(code, 400)
        code, _ = request("/api/creators/missing", method="GET")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
