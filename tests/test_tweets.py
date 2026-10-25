from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from yt_archive import db
from yt_archive.download import _yt_dlp_failure, download, write_archive_json
from yt_archive.jobs import JobQueue
from yt_archive.paths import (
    is_tweet,
    list_archived_ids,
    parse_video_id,
    site_name,
    watch_url,
)
from yt_archive.web import load_archive_info, make_handler

TWEET = "x-1234567890123456789"


class TweetIdTests(unittest.TestCase):
    def test_tweet_links_become_x_archive_ids(self):
        for link in (
            "https://x.com/someone/status/1234567890123456789",
            "https://twitter.com/someone/status/1234567890123456789?s=20&t=abc",
            "https://mobile.twitter.com/someone/status/1234567890123456789/video/1",
            "x.com/i/status/1234567890123456789",
            "https://x.com/i/web/status/1234567890123456789",
            TWEET,
        ):
            with self.subTest(link=link):
                self.assertEqual(parse_video_id(link), TWEET)

    def test_youtube_ids_are_unchanged_and_other_links_refused(self):
        self.assertEqual(parse_video_id("https://youtu.be/choose00001"), "choose00001")
        for youtube_id in ("x-123456789", "x-Ab3_dEf9Q"):
            with self.subTest(youtube_id=youtube_id):
                self.assertEqual(parse_video_id(youtube_id), youtube_id)
                self.assertFalse(is_tweet(youtube_id))
                self.assertEqual(site_name(youtube_id), "YouTube")
        for bad in (
            "https://x.com/someone",
            "https://fixupx.com/a/status/1234567890123456789",
            "https://x.com/a/status/12345678901234567890",
            "x-12",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_video_id(bad)

    def test_site_and_link(self):
        self.assertEqual(site_name(TWEET), "X")
        self.assertEqual(watch_url(TWEET), "https://x.com/i/status/1234567890123456789")
        self.assertEqual(site_name("choose00001"), "YouTube")
        self.assertEqual(watch_url("choose00001"), "https://www.youtube.com/watch?v=choose00001")

    def test_tweet_folders_are_listed_with_youtube_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            for name in (TWEET, "choose00001", "x-12", "_condensed"):
                (data / name).mkdir()
            self.assertEqual(list_archived_ids(data), ["choose00001", TWEET])


class TweetDownloadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)

    def run_yt_dlp(self, video_id):
        """The command download() hands yt-dlp, and the budget it paid from."""
        proc = Mock(stdout=iter(["ERROR: [twitter] stopped by the test"]))
        proc.wait.return_value = 1
        with (
            patch("yt_archive.download.budget.wait_and_take") as wait,
            patch("yt_archive.download.yt_dlp_bin", return_value="yt-dlp"),
            patch("yt_archive.download.subprocess.Popen", return_value=proc) as popen,
            self.assertRaises(RuntimeError),
        ):
            download(video_id, self.data, log=lambda *_: None)
        return popen.call_args.args[0], wait.call_args.args[1]

    def test_tweets_are_read_without_an_account_and_only_by_x_extractors(self):
        cmd, kind = self.run_yt_dlp(TWEET)
        self.assertEqual(kind, "x-download")
        self.assertFalse([arg for arg in cmd if arg.startswith("--cookies")])
        self.assertEqual(cmd[cmd.index("--use-extractors") + 1], "twitter.*")
        self.assertEqual(cmd[cmd.index("--playlist-items") + 1], "1")
        self.assertEqual(cmd[-1], "https://x.com/i/status/1234567890123456789")

    def test_youtube_downloads_keep_their_recipe(self):
        cmd, kind = self.run_yt_dlp("choose00001")
        self.assertEqual(kind, "download")
        self.assertEqual(cmd[cmd.index("--cookies-from-browser") + 1], "firefox")
        self.assertNotIn("--use-extractors", cmd)
        self.assertEqual(cmd[-1], "https://www.youtube.com/watch?v=choose00001")

    def test_failures_say_what_the_archive_will_not_do(self):
        login = (
            "ERROR: [twitter] 1234567890123456789: NSFW tweet requires authentication. Use --cookies, "
            "--cookies-from-browser, --username and --password, --netrc-cmd, or --netrc (twitter) "
            "to provide account credentials."
        )
        self.assertIn("without your account", _yt_dlp_failure([login], "X"))
        elsewhere = "ERROR: No suitable extractor found for URL https://youtu.be/choose00001"
        self.assertEqual(
            _yt_dlp_failure([elsewhere], "X"),
            "This tweet has no video of its own; it links to https://youtu.be/choose00001",
        )

    def test_tweet_metadata_has_no_youtube_channel(self):
        folder = self.data / TWEET
        folder.mkdir()
        video = folder / "Someone-1111111111111111111.mp4"
        video.write_bytes(b"video")
        (folder / "Someone-1111111111111111111.info.json").write_text(json.dumps({
            "id": "1111111111111111111",
            "display_id": "1234567890123456789",
            "title": "Someone - a tweet",
            "uploader": "Someone",
            "uploader_id": "someone",
            "channel_id": "2222222222222222222",
            "duration": 79.566,
            "upload_date": "20261025",
            "webpage_url": "https://x.com/someone/status/1234567890123456789",
        }), encoding="utf-8")
        write_archive_json(self.data, TWEET, video)
        info = load_archive_info(self.data, TWEET)
        self.assertEqual((info["video_id"], info["channel"], info["channel_id"]), (TWEET, "Someone", ""))
        self.assertEqual(info["url"], "https://x.com/someone/status/1234567890123456789")
        self.assertEqual([row["video_id"] for row in db.list_videos(self.data)], [TWEET])


class TweetPageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        queue = JobQueue.__new__(JobQueue)
        queue.data_dir = self.data
        queue._cv = threading.Condition()
        self.handler_class = make_handler(self.data, queue, Mock())

    def request(self, path, payload=None, method="POST"):
        handler = self.handler_class.__new__(self.handler_class)
        raw = json.dumps(payload or {}).encode()
        handler.path = path
        handler.headers = {"Host": "localhost:8765", "Content-Type": "application/json", "Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        getattr(handler, "do_" + method)()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue()

    def test_a_pasted_tweet_is_queued_and_its_page_links_to_x(self):
        code, raw = self.request("/api/get", {"url": "https://x.com/someone/status/1234567890123456789"})
        self.assertEqual((code, json.loads(raw)["video_id"]), (202, TWEET))
        folder = self.data / TWEET
        folder.mkdir()
        (folder / "Someone-1111111111111111111.mp4").write_bytes(b"video")
        (folder / "archive.json").write_text(json.dumps({"video_id": TWEET, "title": "a tweet"}), encoding="utf-8")
        code, raw = self.request(f"/v/{TWEET}", method="GET")
        self.assertEqual(code, 200)
        html = raw.decode()
        self.assertIn('href="https://x.com/i/status/1234567890123456789" target="_blank" rel="noopener noreferrer">X</a>', html)
        self.assertIn('const site = "X";', html)


if __name__ == "__main__":
    unittest.main()
