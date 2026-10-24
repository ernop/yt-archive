from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from yt_archive import budget, db
from yt_archive.budget import BudgetExceeded
from yt_archive.creators import (
    AccountNeeded, CreatorBrowser, ListingParser, browse_url, fetch_listing, listing_id, listing_source,
    needs_account, playlist_browse_url,
)
from yt_archive.jobs import JobQueue
from yt_archive.paths import parse_video_id
from yt_archive.web import make_handler

CHANNEL = "UC" + "a" * 22
TAB = "https://www.youtube.com/@Example/"


def entry(video_id, title, tab="videos", **extra):
    return {"ie_key": "Youtube", "id": video_id, "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "playlist_webpage_url": TAB + tab, "playlist_channel": "Example",
            "playlist_channel_id": CHANNEL, **extra}


def channel_lines():
    """What yt-dlp --flat-playlist --dump-json prints for a channel, one dict per line."""
    return [
        entry("saved000001", "Already saved", duration=80, timestamp=1_700_000_000, view_count=10),
        entry("queued00001", "Already queued"),
        entry("choose00001", "Choose this <video>", duration=130, view_count=2_200_000),
        entry("choose00002", "Another video"),
        entry("private0001", "[Private video]"),
        entry("members0001", "Members only", availability="subscriber_only"),
        {"ie_key": "Youtube", "id": "../bad", "title": "Invalid ID"},
        entry("short000001", "A Short", "shorts",
              url="https://www.youtube.com/shorts/short000001"),
        entry("choose00001", "Duplicate", "shorts"),
        entry("pastlive001", "Past stream", "streams", live_status="was_live"),
        entry("livenow0001", "Live now", "streams", live_status="is_live"),
        entry("upcoming001", "Upcoming", "streams", live_status="is_upcoming"),
    ]


def parsed_channel():
    parser = ListingParser("channel")
    for line in channel_lines():
        parser.feed(json.dumps(line))
    return {**parser.result(), "warning": "", "requests": 3}


def parsed_playlist():
    parser = ListingParser("playlist")
    parser.feed(json.dumps({**entry("choose00001", "Saved for later", channel="Other"),
                            "playlist_title": "My list", "playlist_count": 1, "playlist_index": 1}))
    return {**parser.result(), "warning": "", "requests": 2}


def hidden_unless_signed_in(url, kind, data_dir, progress, account=False):
    """A private playlist: YouTube calls it missing unless the owner's account asks."""
    if not account:
        raise AccountNeeded("YouTube said: The playlist does not exist.", 1)
    return parsed_playlist()


class ListingSourceTests(unittest.TestCase):
    def test_creator_links_and_the_tab_they_point_at(self):
        for source, path, section in [
            (" @Example ", "@Example", ""), ("Example", "@Example", ""),
            (CHANNEL, "channel/" + CHANNEL, ""),
            ("https://youtube.com/@Example/videos?view=0#top", "@Example", "videos"),
            ("youtube.com/@Example/shorts", "@Example", "shorts"),
            ("https://m.youtube.com/user/OldName/streams/", "user/OldName", "live"),
            ("www.youtube.com/c/Custom/about", "c/Custom", ""),
            ("https://youtube.com/Custom", "Custom", ""),
            ("https://youtube.com/channel/" + CHANNEL, "channel/" + CHANNEL, ""),
            ("https://www.youtube.com/@%E6%97%A5%E6%9C%AC", "@%E6%97%A5%E6%9C%AC", ""),
        ]:
            with self.subTest(source=source):
                self.assertEqual(listing_source(source),
                                 {"kind": "channel", "url": "https://www.youtube.com/" + path, "section": section})

    def test_playlists_tab_and_playlist_links(self):
        self.assertEqual(listing_source("https://www.youtube.com/@Example/playlists")["kind"], "playlists")
        self.assertEqual(listing_source("https://www.youtube.com/channel/" + CHANNEL + "/playlists")["url"],
                         "https://www.youtube.com/channel/" + CHANNEL + "/playlists")
        playlist = "https://www.youtube.com/playlist?list=PLabc_DEF-123"
        for source in ["https://youtube.com/playlist?list=PLabc_DEF-123&si=x",
                       "https://www.youtube.com/watch?v=choose00001&list=PLabc_DEF-123&index=3",
                       "https://youtu.be/choose00001?list=PLabc_DEF-123",
                       "https://www.youtube.com/embed/videoseries?list=PLabc_DEF-123",
                       "https://music.youtube.com/playlist?list=PLabc_DEF-123"]:
            with self.subTest(source=source):
                self.assertEqual(listing_source(source), {"kind": "playlist", "url": playlist, "section": ""})
        self.assertFalse(needs_account(playlist))

    def test_account_lists_are_playlists_that_need_the_account(self):
        for source, list_id in [("https://www.youtube.com/playlist?list=WL", "WL"),
                                ("https://youtube.com/watch?v=choose00001&list=LL", "LL"),
                                ("https://music.youtube.com/playlist?list=LM", "LM")]:
            with self.subTest(source=source):
                target = listing_source(source)
                self.assertEqual(target, {"kind": "playlist", "section": "",
                                          "url": "https://www.youtube.com/playlist?list=" + list_id})
                self.assertTrue(needs_account(target["url"]))
        self.assertFalse(needs_account("https://www.youtube.com/@Example"))

    def test_rejects_videos_and_non_youtube_targets(self):
        for source in [None, 3, "", "two names", "https://example.com/@name",
                       "https://youtube.com.evil.test/@name", "file:///tmp/video",
                       "https://youtube.com/playlist?list=", "https://youtube.com/@name/../../watch",
                       "https://youtube.com/channel/invalid", "https://youtube.com/@bad%2Fname",
                       "https://youtube.com/@name/videos/extra", "https://music.youtube.com/channel/" + CHANNEL]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                listing_source(source)
        for source in ["https://youtube.com/watch?v=choose00001", "https://youtube.com/shorts/choose00001",
                       "https://youtu.be/choose00001"]:
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "single video"):
                listing_source(source)
        with self.assertRaisesRegex(ValueError, "no valid video"):
            listing_source("https://youtube.com/watch?v=short")

    def test_browse_links(self):
        self.assertEqual(browse_url("youtube.com/@Example/shorts"),
                         "/browse?source=https%3A%2F%2Fwww.youtube.com%2F%40Example&section=shorts")
        self.assertIn("playlist%3Flist%3DPLabc", playlist_browse_url(
            "https://www.youtube.com/watch?v=choose00001&list=PLabc"))
        self.assertIn("playlist%3Flist%3DWL", playlist_browse_url(
            "https://www.youtube.com/watch?v=choose00001&list=WL"))
        # Mixes are made up per viewer; a plain video has no playlist.
        self.assertEqual(playlist_browse_url("https://www.youtube.com/watch?v=choose00001&list=RDchoose00001"), "")
        self.assertEqual(playlist_browse_url("https://youtu.be/choose00001"), "")

    def test_videoseries_embed_is_not_a_video_id(self):
        with self.assertRaises(ValueError):
            parse_video_id("https://www.youtube.com/embed/videoseries?list=PLabc")
        self.assertEqual(parse_video_id("https://www.youtube.com/embed/choose00001"), "choose00001")


class ListingParserTests(unittest.TestCase):
    def test_channel_lines_become_sectioned_rows(self):
        listing = parsed_channel()
        self.assertEqual((listing["title"], listing["channel_id"]), ("Example", CHANNEL))
        rows = {row["video_id"]: row for row in listing["items"]}
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows["choose00001"]["title"], "Choose this <video>")
        self.assertEqual(rows["choose00001"]["section"], "videos")
        self.assertEqual(rows["short000001"]["section"], "shorts")
        self.assertEqual(rows["pastlive001"]["section"], "live")
        self.assertEqual((rows["saved000001"]["duration"], rows["saved000001"]["timestamp"]), (80, 1_700_000_000))
        self.assertEqual(rows["choose00001"]["views"], 2_200_000)
        self.assertEqual(rows["choose00002"]["duration"], None)
        self.assertFalse(rows["pastlive001"]["unavailable"])
        for video_id, reason in [("private0001", "Unavailable"), ("members0001", "Restricted"),
                                 ("livenow0001", "Live now"), ("upcoming001", "Not ready yet")]:
            self.assertEqual(rows[video_id]["unavailable"], reason)

    def test_playlist_and_playlists_lines(self):
        parser = ListingParser("playlist")
        for index, video_id in enumerate(["choose00001", "choose00002"], 1):
            parser.feed(json.dumps({**entry(video_id, "Guest " + video_id, channel="Other"),
                                    "playlist_title": "Guest spots", "playlist_count": 2, "playlist_index": index}))
        parser.feed("not json")
        listing = parser.result()
        self.assertEqual((listing["title"], parser.expected), ("Guest spots", 2))
        self.assertEqual([(row["position"], row["channel"], row["section"]) for row in listing["items"]],
                         [(1, "Other", ""), (2, "Other", "")])
        parser = ListingParser("playlists")
        parser.feed(json.dumps({"ie_key": "YoutubeTab", "id": "PLabc_DEF-123", "title": "Series",
                                "thumbnails": [{"url": "https://i.ytimg.com/vi/choose00001/hqdefault.jpg?x=1"}]}))
        parser.feed(json.dumps(entry("choose00001", "a video, not a playlist")))
        self.assertEqual(parser.result()["items"],
                         [{"playlist_id": "PLabc_DEF-123", "title": "Series", "thumb_id": "choose00001"}])


FAKE_YT_DLP = """#!{python}
import json, os, sys, time
plan = json.load(open(os.environ["FAKE_YTDLP_PLAN"]))
open(plan["args"], "w").write(json.dumps(sys.argv[1:]))
for number, page in enumerate(plan["pages"]):
    if number:
        print("[youtube:tab] Sleeping 0.75 seconds ...", file=sys.stderr, flush=True)
        time.sleep(plan.get("pause", 0.3))
    print("[youtube:tab] Downloading API JSON page %d" % number, file=sys.stderr, flush=True)
    for line in page:
        print(json.dumps(line), flush=True)
for error in plan.get("errors", []):
    print("ERROR: " + error, file=sys.stderr, flush=True)
time.sleep(plan.get("hang", 0))
sys.exit(plan.get("exit", 0))
"""


class FetchListingTests(unittest.TestCase):
    """fetch_listing against a stand-in yt-dlp that prints pages like the real one."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.binary = self.data / "yt-dlp"
        self.binary.write_text(FAKE_YT_DLP.format(python=sys.executable), encoding="utf-8")
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IEXEC)
        self.plan = self.data / "plan.json"
        self.args = self.data / "args.json"
        for target, value in [("yt_archive.creators.yt_dlp_bin", lambda: str(self.binary)),
                              ("os.environ", {**os.environ, "FAKE_YTDLP_PLAN": str(self.plan)})]:
            patcher = patch(target, value) if target != "os.environ" else patch.dict(os.environ, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_fake(self, pages, url="https://www.youtube.com/@Example", kind="channel", account=False, **plan):
        self.plan.write_text(json.dumps({"pages": pages, "args": str(self.args), **plan}), encoding="utf-8")
        budget.take(self.data, "listing")  # the caller pays for the first request
        return fetch_listing(url, kind, self.data, account=account)

    def test_streams_metadata_only_and_counts_every_request(self):
        lines = channel_lines()
        result = self.run_fake([lines[:4], lines[4:8], lines[8:]], pause=0)
        self.assertEqual((len(result["items"]), result["requests"], result["warning"]), (10, 3, ""))
        self.assertEqual(len(db.youtube_request_times(self.data, "listing", 0)), 3)
        command = json.loads(self.args.read_text())
        for flag in ["--ignore-config", "--flat-playlist", "--lazy-playlist", "--dump-json", "--verbose"]:
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--sleep-requests") + 1], "0.75")
        self.assertNotIn("--playlist-end", command)
        # Browsing must not reach YouTube as the user's account.
        self.assertFalse([arg for arg in command if "cookie" in arg])

    def test_budget_stops_before_the_next_request_and_keeps_what_arrived(self):
        lines = channel_lines()
        with patch.dict(budget.LIMITS, {"listing": 3}):
            result = self.run_fake([lines[:2], lines[2:4], lines[4:6], lines[6:]])
        self.assertEqual([row["video_id"] for row in result["items"]],
                         ["saved000001", "queued00001", "choose00001", "choose00002", "private0001", "members0001"])
        self.assertEqual(result["requests"], 3)
        self.assertIn("budget of 3 listing requests is used up", result["warning"])
        self.assertEqual(len(db.youtube_request_times(self.data, "listing", 0)), 3)

    def test_errors_warn_when_partial_and_fail_when_empty(self):
        result = self.run_fake([channel_lines()[:3]], errors=["one tab failed"], exit=1, pause=0)
        self.assertEqual(len(result["items"]), 3)
        self.assertIn("incomplete", result["warning"])
        self.assertIn("one tab failed", result["warning"])
        with self.assertRaisesRegex(RuntimeError, "creator not found"):
            self.run_fake([[]], errors=["creator not found"], exit=1)

    def test_the_account_is_sent_only_when_asked(self):
        playlist = "https://www.youtube.com/playlist?list=WL"
        self.run_fake([[entry("choose00001", "Saved for later")]], url=playlist, kind="playlist", account=True)
        command = json.loads(self.args.read_text())
        self.assertEqual(command[command.index("--cookies-from-browser") + 1], "firefox")
        self.assertEqual(command[-1], playlist)

    def test_a_playlist_youtube_calls_missing_may_be_private(self):
        missing = ["[youtube:tab] PLhidden: YouTube said: The playlist does not exist."]
        playlist = "https://www.youtube.com/playlist?list=PLhidden"
        with self.assertRaisesRegex(AccountNeeded, "does not exist") as raised:
            self.run_fake([[]], url=playlist, kind="playlist", errors=missing, exit=1)
        self.assertEqual(raised.exception.requests, 1)
        # Already asked with the account, or not a playlist: a plain failure.
        for kind, account in [("playlist", True), ("channel", False)]:
            with self.subTest(kind=kind, account=account), self.assertRaises(RuntimeError) as raised:
                self.run_fake([[]], url=playlist, kind=kind, account=account, errors=missing, exit=1)
            self.assertNotIsInstance(raised.exception, AccountNeeded)

    def test_a_hung_listing_is_stopped(self):
        with patch("yt_archive.creators.LISTING_TIMEOUT", 0.5):
            result = self.run_fake([channel_lines()[:2]], hang=30)
        self.assertEqual(len(result["items"]), 2)
        self.assertIn("longer than", result["warning"])


class CreatorBrowserTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        folder = self.data / "saved000001"
        folder.mkdir()
        (folder / "original.mkv").write_bytes(b"original")
        db.insert_job(self.data, "queued00001", "queued00001")
        # Use the real submission path without starting the media worker.
        self.queue = JobQueue.__new__(JobQueue)
        self.queue.data_dir = self.data
        self.queue._cv = threading.Condition()
        self.browser = CreatorBrowser(self.data, self.queue)

    def wait(self, browser, lookup_id, status="done"):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = browser.get(lookup_id)
            if result and result["status"] != "loading":
                self.assertEqual(result["status"], status, result.get("error"))
                return result
            time.sleep(.01)
        self.fail("Listing did not finish")

    def load(self, source="@Example", fetch=None, browser=None, refresh=False):
        browser = browser or self.browser
        fetch = fetch or Mock(return_value=parsed_channel())
        with patch("yt_archive.creators.fetch_listing", fetch):
            started = browser.start(source, refresh=refresh)
            return self.wait(browser, started["lookup_id"]), fetch

    def test_browsing_is_read_only_then_selected_and_all_queue_missing(self):
        listing, _ = self.load()
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
        listing, _ = self.load()
        for selection in [["choose00001", "notinlist01"], ["livenow0001"], [None], "choose00001", []]:
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                self.browser.enqueue(listing["lookup_id"], video_ids=selection)
        self.assertEqual(len(db.list_jobs(self.data)), 1)
        with self.assertRaisesRegex(ValueError, "gone"):
            self.browser.enqueue("0123456789abcdef", all_items=True)

    def test_kept_listing_survives_reloads_and_restarts_until_refreshed(self):
        listing, fetch = self.load()
        self.assertEqual(fetch.call_count, 1)
        restarted = CreatorBrowser(self.data, self.queue)
        for source in ["https://www.youtube.com/@Example/videos", "@Example", CHANNEL]:
            with self.subTest(source=source), patch("yt_archive.creators.fetch_listing") as again:
                kept = restarted.start(source)
                again.assert_not_called()
                self.assertEqual((kept["status"], kept["lookup_id"]), ("done", listing["lookup_id"]))
        refreshed, fetch = self.load(browser=restarted, refresh=True)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(refreshed["lookup_id"], listing["lookup_id"])
        self.assertEqual(len(db.youtube_request_times(self.data, "listing", 0)), 2)

    def test_one_fetch_per_list_and_two_lists_at_once(self):
        release = threading.Event()

        def slow(url, kind, data_dir, progress, account=False):
            release.wait(3)
            return parsed_channel()

        with patch("yt_archive.creators.fetch_listing", side_effect=slow) as fetch:
            first = self.browser.start("@Example")
            second = self.browser.start("https://youtube.com/@Example/shorts")
            self.assertEqual(second["lookup_id"], first["lookup_id"])
            other = self.browser.start("@Other")
            with self.assertRaisesRegex(ValueError, "already loading"):
                self.browser.start("@Third")
            with self.assertRaisesRegex(ValueError, "finish loading"):
                self.browser.enqueue(first["lookup_id"], all_items=True)
            release.set()
            self.wait(self.browser, first["lookup_id"])
            self.wait(self.browser, other["lookup_id"])
            self.assertEqual(fetch.call_count, 2)

    def test_a_failure_is_shown_again_not_fetched_again_until_retried(self):
        failing = Mock(side_effect=RuntimeError("creator not found"))
        with patch("yt_archive.creators.fetch_listing", failing):
            started = self.browser.start("@Missing")
            self.assertEqual(self.wait(self.browser, started["lookup_id"], "error")["error"], "creator not found")
            self.assertEqual(self.browser.start("@Missing")["status"], "error")
            self.assertEqual(failing.call_count, 1)
            self.browser.start("@Missing", refresh=True)
            self.wait(self.browser, started["lookup_id"], "error")
            self.assertEqual(failing.call_count, 2)

    def test_a_failed_refresh_keeps_the_list(self):
        listing, _ = self.load()
        with patch("yt_archive.creators.fetch_listing", side_effect=RuntimeError("network down")):
            self.browser.start("@Example", refresh=True)
            kept = self.wait(self.browser, listing["lookup_id"])
        self.assertEqual(len(kept["items"]), len(listing["items"]))
        self.assertEqual(kept["error"], "Refresh failed: network down")

    def test_creators_and_public_playlists_are_listed_without_the_account(self):
        listing, fetch = self.load()
        self.assertEqual((fetch.call_args.kwargs["account"], listing["account"]), (False, False))
        listing, fetch = self.load("https://www.youtube.com/playlist?list=PLabc", Mock(return_value=parsed_playlist()))
        self.assertEqual((fetch.call_args.kwargs["account"], listing["account"]), (False, False))

    def test_account_lists_are_read_with_the_account_from_the_start(self):
        listing, fetch = self.load("https://www.youtube.com/playlist?list=WL", Mock(return_value=parsed_playlist()))
        self.assertEqual([call.kwargs["account"] for call in fetch.call_args_list], [True])
        self.assertTrue(listing["account"])
        self.assertEqual(len(db.youtube_request_times(self.data, "listing", 0)), 1)

    def test_a_private_playlist_is_asked_again_with_the_account_and_kept_that_way(self):
        source = "https://www.youtube.com/playlist?list=PLhidden"
        listing, fetch = self.load(source, Mock(side_effect=hidden_unless_signed_in))
        self.assertEqual([call.kwargs["account"] for call in fetch.call_args_list], [False, True])
        self.assertEqual((listing["account"], listing["requests"], listing["title"]), (True, 3, "My list"))
        self.assertEqual(len(db.youtube_request_times(self.data, "listing", 0)), 2)
        restarted = CreatorBrowser(self.data, self.queue)
        self.assertTrue(restarted.start(source)["account"])
        refreshed, fetch = self.load(source, Mock(side_effect=hidden_unless_signed_in), restarted, refresh=True)
        self.assertEqual([call.kwargs["account"] for call in fetch.call_args_list], [True])
        self.assertTrue(refreshed["account"])

    def test_a_playlist_missing_with_the_account_too_fails_once(self):
        source = "https://www.youtube.com/playlist?list=PLgone"

        def missing(url, kind, data_dir, progress, account=False):
            if not account:
                raise AccountNeeded("YouTube said: The playlist does not exist.", 1)
            raise RuntimeError("YouTube said: The playlist does not exist.")

        fetch = Mock(side_effect=missing)
        with patch("yt_archive.creators.fetch_listing", fetch):
            started = self.browser.start(source)
            self.assertIn("does not exist", self.wait(self.browser, started["lookup_id"], "error")["error"])
            self.assertEqual(self.browser.start(source)["status"], "error")
            self.browser.start(source, refresh=True)
            self.wait(self.browser, started["lookup_id"], "error")
        self.assertEqual([call.kwargs["account"] for call in fetch.call_args_list], [False, True, True])

    def test_no_budget_left_for_the_second_try_fails_without_it(self):
        with patch.dict(budget.LIMITS, {"listing": 1}), \
                patch("yt_archive.creators.fetch_listing", Mock(side_effect=hidden_unless_signed_in)) as fetch:
            started = self.browser.start("https://www.youtube.com/playlist?list=PLhidden")
            failed = self.wait(self.browser, started["lookup_id"], "error")
        self.assertIn("used up", failed["error"])
        self.assertEqual([call.kwargs["account"] for call in fetch.call_args_list], [False])

    def test_used_up_budget_refuses_new_lists_but_keeps_old_ones(self):
        listing, _ = self.load()
        with patch.dict(budget.LIMITS, {"listing": 1}), patch("yt_archive.creators.fetch_listing") as fetch:
            with self.assertRaisesRegex(BudgetExceeded, "budget of 1 listing requests is used up"):
                self.browser.start("@Other")
            kept = self.browser.start("@Example", refresh=True)
            fetch.assert_not_called()
        self.assertEqual(kept["status"], "done")
        self.assertIn("used up", kept["error"])
        self.assertEqual(kept["lookup_id"], listing["lookup_id"])

    def test_playlists_list_has_nothing_to_queue(self):
        playlists = {"title": "Example", "channel": "Example", "channel_id": CHANNEL, "warning": "", "requests": 1,
                     "items": [{"playlist_id": "PLabc_DEF-123", "title": "Series", "thumb_id": ""}]}
        listing, _ = self.load("https://www.youtube.com/@Example/playlists", Mock(return_value=playlists))
        self.assertEqual(listing["kind"], "playlists")
        with self.assertRaisesRegex(ValueError, "Open one of the playlists"):
            self.browser.enqueue(listing["lookup_id"], all_items=True)

    def test_only_recent_listings_are_kept(self):
        with patch("yt_archive.db.LISTINGS_KEPT", 2):
            for name in ["One", "Two", "Three"]:
                url = "https://www.youtube.com/@" + name
                db.save_listing(self.data, {"id": listing_id(url), "url": url, "kind": "channel", "items": []})
                time.sleep(.01)
        self.assertIsNone(db.get_listing(self.data, listing_id("https://www.youtube.com/@One")))
        self.assertIsNotNone(db.get_listing(self.data, listing_id("https://www.youtube.com/@Three")))

    def test_http_routes(self):
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
            return handler.send_response.call_args.args[0], json.loads(handler.wfile.getvalue() or b"null") \
                if method == "POST" or path.startswith("/api/") else handler.wfile.getvalue()

        for source in ["@Example", "Example", "https://youtube.com/@Example/videos",
                       "https://www.youtube.com/playlist?list=PLabc", "https://www.youtube.com/playlist?list=WL"]:
            code, data = request("/api/get", {"url": source})
            self.assertEqual(code, 200)
            self.assertIn("/browse?source=", data["browse_url"])
        code, data = request("/api/get", {"url": "https://youtu.be/choose00001"})
        self.assertEqual((code, data["video_id"]), (202, "choose00001"))
        self.assertNotIn("playlist_browse_url", data)
        code, data = request("/api/get", {"url": "https://www.youtube.com/watch?v=choose00002&list=PLabc"})
        self.assertEqual((code, data["video_id"]), (202, "choose00002"))
        self.assertIn("list%3DPLabc", data["playlist_browse_url"])
        code, raw = request("/browse?source=%22%3E%3Cscript%3E", method="GET")
        self.assertEqual(code, 200)
        self.assertIn(b'&quot;&gt;&lt;script&gt;', raw)
        self.assertIn(b'creators.js', raw)
        for list_id in [b"WL", b"LL"]:
            self.assertIn(b"playlist%3Flist%3D" + list_id, raw)
        code, _ = request("/api/creators", {"source": "@Example"}, extra_headers={"Origin": "https://evil.test"})
        self.assertEqual(code, 403)
        for payload in [{"source": ["bad"]}, {"source": "@Example", "refresh": "yes"}]:
            code, _ = request("/api/creators", payload)
            self.assertEqual(code, 400)
        with patch.dict(budget.LIMITS, {"listing": 0}):
            code, data = request("/api/creators", {"source": "@Example"})
        self.assertEqual(code, 429)
        self.assertIn("used up", data["error"])
        self.assertGreater(data["retry_at"], 0)
        code, _ = request("/api/creators/0123456789abcdef/get", {"all": "false"})
        self.assertEqual(code, 400)
        code, _ = request("/api/creators/missing", method="GET")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
