from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yt_archive import db
from yt_archive.web import home_html


class BrowsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        records = [
            {
                "video_id": "newest00001",
                "title": "Zulu",
                "channel": "Beta",
                "upload_date": "20200101",
                "downloaded_at": "2026-08-30T12:00:00+00:00",
            },
            {
                "video_id": "middle00001",
                "title": "Alpha",
                "channel": "Alpha",
                "upload_date": "20250101",
                "downloaded_at": "2026-08-29T12:00:00+00:00",
            },
            {
                "video_id": "oldest00001",
                "title": "Bravo",
                "channel": "Beta",
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
        self.assertIn('class="got-label">got</span><time', html)
        self.assertIn("2026-08-30 12:00 UTC", html)
        self.assertIn("new URLSearchParams(window.location.search)", html)


if __name__ == "__main__":
    unittest.main()
