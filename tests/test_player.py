"""The detail page plays its video and MP3 audio in this repo's copy of the shared media player
(docs/setup.md)."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from html import unescape
from pathlib import Path

from yt_archive.paths import mp3_filename
from yt_archive.web import STATIC_DIR, detail_html

VIDEO_ID = "abcdefghijk"
COPY = STATIC_DIR / "media-player"


class PlayerPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        folder = self.data / VIDEO_ID
        condensed = folder / "_condensed"
        (condensed / "shots").mkdir(parents=True)
        (folder / "video.mp4").write_bytes(b"video")
        (condensed / "soundtrack.mp3").write_bytes(b"audio")
        for name in ("0000.png", "0001.png", "0002.png"):
            (condensed / "shots" / name).write_bytes(b"png")
        shots = [
            {"file": "0000.png", "t0": 0.0, "sample": 1.5, "kept": True},
            {"file": "0001.png", "t0": 12.5, "sample": 14.0, "kept": False},
            {"file": "0002.png", "t0": 30.25, "sample": 31.0},
        ]
        (condensed / "shots.json").write_text(json.dumps({"shots": shots}), encoding="utf-8")

    def page(self):
        return detail_html({"video_id": VIDEO_ID, "title": "Test", "duration": 80}, self.data).decode()

    def test_every_video_and_audio_uses_the_shared_player(self):
        tags = re.findall(r"<(?:video|audio)\b[^>]*>", self.page())
        self.assertEqual(len(tags), 2)
        for tag in tags:
            self.assertIn("data-media-player", tag)
            self.assertIn("controls", tag)

    def test_kept_shots_are_markers_named_by_their_image(self):
        html = self.page()
        markers = json.loads(unescape(re.search(r'data-markers="([^"]*)"', html).group(1)))
        self.assertEqual(markers, [
            {"t": 0.0, "label": "Shot 0000", "id": "0000.png"},
            {"t": 30.25, "label": "Shot 0002", "id": "0002.png"},
        ])
        self.assertIn('<figure data-file="0002.png" data-t="31.0">', html)
        self.assertIn("data-wide", html)

    def test_the_page_loads_this_repos_copy_of_the_player(self):
        html = self.page()
        self.assertIn('src="/static/media-player/media-player.js?v=', html)
        self.assertIn('href="/static/media-player/media-player.css?v=', html)

    def test_the_mp3_downloads_under_artist_title_and_id(self):
        html = detail_html(
            {"video_id": VIDEO_ID, "title": "Song Title, Part 2", "channel": "Example Artist - Topic"},
            self.data,
        ).decode()
        self.assertIn(f'download="Example_Artist_-_Song_Title_Part_2-{VIDEO_ID}.mp3"', html)


class Mp3FilenameTests(unittest.TestCase):
    def test_names(self):
        cases = [
            ("Example Artist - Topic", "Song Title, Part 2", "Example_Artist_-_Song_Title_Part_2-abcdefghijk.mp3"),
            ("Some Band", "Night Drive", "Some_Band_-_Night_Drive-abcdefghijk.mp3"),
            ("Label Records", "Singer - Song Name", "Singer_-_Song_Name-abcdefghijk.mp3"),
            ("Uploader9", "Uploader9 plays a song", "Uploader9_plays_a_song-abcdefghijk.mp3"),
            ("Some One", "", "Some_One-abcdefghijk.mp3"),
            ("", "Night Drive", "Night_Drive-abcdefghijk.mp3"),
            ("日本", "日本語", "abcdefghijk.mp3"),
            ("音樂 Example Music-精選", "Band Name【告別（Farewell）】",
             "Example_Music_-_Band_Name_Farewell-abcdefghijk.mp3"),
        ]
        for channel, title, want in cases:
            with self.subTest(title=title, channel=channel):
                self.assertEqual(mp3_filename(VIDEO_ID, title, channel), want)

    def test_tweet_titles_already_name_the_poster(self):
        self.assertEqual(
            mp3_filename("x-1111111111111111111", "Someone - 90% of this", "Someone"),
            "Someone_-_90_of_this-x-1111111111111111111.mp3",
        )


class PlayerCopyTests(unittest.TestCase):
    def test_the_copy_is_the_librarys_files_unedited(self):
        checksums = {}
        for line in (COPY / "media-player.sha256").read_text(encoding="utf-8").splitlines():
            digest, name = line.split(maxsplit=1)
            checksums[name.lstrip("*")] = digest
        self.assertEqual(sorted(checksums), ["media-player.css", "media-player.js"])
        for name, digest in checksums.items():
            with self.subTest(name):
                # Git may check text files out with CRLF line endings on Windows.
                data = (COPY / name).read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(
                    hashlib.sha256(data).hexdigest(), digest,
                    f"yt_archive/static/media-player/{name} was edited: change the media-player "
                    "library, then run its copy-into.sh yt_archive/static/media-player from here",
                )


if __name__ == "__main__":
    unittest.main()
