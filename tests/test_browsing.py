from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yt_archive import db
from yt_archive.web import CSS, creator_html, home_html


class BrowsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        records = [
            {
                "video_id": "newest00001",
                "title": "Zulu",
                "channel": "Beta",
                "channel_id": "UCbeta",
                "upload_date": "20200101",
                "downloaded_at": "2026-08-30T12:00:00+00:00",
            },
            {
                "video_id": "middle00001",
                "title": "Alpha",
                "channel": "Alpha",
                "channel_id": "UCalpha",
                "upload_date": "20250101",
                "downloaded_at": "2026-08-29T12:00:00+00:00",
            },
            {
                "video_id": "oldest00001",
                "title": "Bravo",
                "channel": "Beta",
                "channel_id": "UCbeta",
                "upload_date": "20240101",
                "downloaded_at": "2026-08-28T12:00:00+00:00",
            },
        ]
        for record in records:
            folder = self.data / record["video_id"]
            folder.mkdir()
            (folder / "archive.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
        db.rebuild(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def ids(self, sort: str = "recent", query: str = "") -> list[str]:
        return [
            row["video_id"]
            for row in db.list_videos(self.data, query=query, sort=sort)
        ]

    def test_recently_gotten_is_default_and_unknown_sort_falls_back(self):
        expected = ["newest00001", "middle00001", "oldest00001"]
        self.assertEqual(self.ids(), expected)
        self.assertEqual(self.ids("not-a-sort"), expected)

    def test_simple_sort_choices_work_with_search(self):
        self.assertEqual(
            self.ids("oldest"),
            ["oldest00001", "middle00001", "newest00001"],
        )
        self.assertEqual(
            self.ids("uploaded"),
            ["middle00001", "oldest00001", "newest00001"],
        )
        self.assertEqual(
            self.ids("title"),
            ["middle00001", "oldest00001", "newest00001"],
        )
        self.assertEqual(
            self.ids("channel"),
            ["middle00001", "oldest00001", "newest00001"],
        )
        self.assertEqual(
            self.ids("title", "Beta"),
            ["oldest00001", "newest00001"],
        )

    def test_home_page_exposes_selected_sort_and_visible_gotten_time(self):
        html = home_html(
            db.list_videos(self.data, sort="title"),
            data_dir=self.data,
            sort="title",
        ).decode()
        self.assertIn('<option value="title" selected>Title — A–Z</option>', html)
        self.assertIn(
            '<time datetime="2026-08-30T12:00:00+00:00" data-local>2026-08-30 12:00 UTC</time>', html
        )
        self.assertIn('<span class="d g">Got</span>', html)
        self.assertIn('href="/creator?channel_id=UCbeta"', html)
        self.assertEqual(html.count('<div class="vrow">'), 3)
        self.assertIn('<span class="d u">2020-01-01</span>', html)
        # The list refreshes by re-reading this page, not by a second renderer in JS.
        self.assertIn("refreshArchive", html)
        self.assertNotIn("/api/list", html)

    def test_list_rows_are_single_line_and_never_load_full_framesheets(self):
        folder = self.data / "newest00001"
        (folder / "newest00001.mp4").write_bytes(b"video")
        (folder / "_condensed").mkdir()
        (folder / "_condensed" / "framesheet.png").write_bytes(b"huge sheet")
        (folder / "newest00001.webp").write_bytes(b"youtube thumb")
        db.rebuild(self.data)
        html = home_html(db.list_videos(self.data), data_dir=self.data).decode()
        self.assertNotIn("framesheet.png", html)
        self.assertIn("/media/newest00001/newest00001.webp", html)
        self.assertIn('loading="lazy"', html)
        row = html[html.index('<div class="vrow"><a class="thumb" href="/v/newest00001"'):]
        row = row[: row.index("</div>") + 6]
        self.assertNotIn("<br", row)
        self.assertNotIn("<p", row)

    def test_creator_page_lists_only_that_uploader_and_supports_search(self):
        videos = db.list_creator_videos(self.data, channel_id="UCbeta")
        self.assertEqual(
            [video["video_id"] for video in videos],
            ["newest00001", "oldest00001"],
        )
        matches = db.list_creator_videos(
            self.data, channel_id="UCbeta", query="Zulu"
        )
        self.assertEqual([video["video_id"] for video in matches], ["newest00001"])
        html = creator_html(
            videos,
            creator_name="Beta",
            channel_id="UCbeta",
            total_count=2,
            data_dir=self.data,
        ).decode()
        self.assertIn("<h1>Beta</h1>", html)
        self.assertIn("<strong>2</strong> videos", html)
        self.assertIn("Zulu", html)
        self.assertIn("Bravo", html)
        self.assertNotIn("Alpha", html)

    def test_neutral_ui_text_is_pure_white(self):
        video_css = (
            Path(__file__).parents[1] / "yt_archive" / "static" / "video.css"
        ).read_text(encoding="utf-8")
        creators_css = (
            Path(__file__).parents[1] / "yt_archive" / "static" / "creators.css"
        ).read_text(encoding="utf-8")
        combined = (CSS + video_css + creators_css).lower()
        for forbidden in ("color: #999", "color: #aaa", "color: #bbb",
                          "color: #ccc", "color: #ddd", "color: #eee"):
            self.assertNotIn(forbidden, combined)
        self.assertIn("--fg:#fff", CSS)
        self.assertIn("--muted:#fff", CSS)
        self.assertIn("input::placeholder { color: #fff; opacity: 1; }", CSS)


if __name__ == "__main__":
    unittest.main()
