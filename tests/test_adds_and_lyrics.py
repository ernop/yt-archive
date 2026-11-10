from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from yt_archive import db
from yt_archive.download import write_archive_json
from yt_archive.lyrics import MAX_LYRICS_BYTES, place_pending_lyrics
from yt_archive.paths import lyrics_path
from yt_archive.web import detail_html, load_archive_info, make_handler

from page_text import with_static
from test_queue import fake_queue, make_video

LRC = "[ti:Made-up song]\n[00:01.00]first line\n[00:03.50]second line\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.queue = fake_queue(self.data)
        self.handler_class = make_handler(self.data, self.queue, Mock())

    def request(self, path, payload=None, method="POST", content_type="application/json"):
        handler = self.handler_class.__new__(self.handler_class)
        raw = json.dumps(payload if payload is not None else {}).encode()
        handler.path = path
        handler.headers = {"Host": "localhost:8765", "Content-Type": content_type,
                           "Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        getattr(handler, "do_" + method)()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue()


class AddedByTests(Base):
    def test_the_first_route_is_kept_and_later_ones_leave_it(self):
        code, _ = self.request("/api/get", {"url": "played00001", "via": "played"})
        self.assertEqual(code, 202)
        code, _ = self.request("/api/get", {"url": "played00001", "via": "bookmarklet"})
        self.assertEqual(code, 200)
        self.assertEqual(db.first_add(self.data, "played00001")["added_via"], "played")

    def test_requests_without_a_route_record_nothing(self):
        self.request("/api/get", {"url": "retried0001"})
        self.assertEqual(db.first_add(self.data, "retried0001"), {})

    def test_a_video_archived_before_routes_were_kept_stays_unrecorded(self):
        make_video(self.data, "older000001", shots=10)
        self.request("/api/get", {"url": "older000001", "via": "pasted"})
        self.queue.submit("older000001", force_shots=True, via="played")
        self.assertEqual(db.first_add(self.data, "older000001"), {})

    def test_an_unknown_route_is_refused_before_anything_is_queued(self):
        for via in ("nope", ["played"], 3):
            with self.subTest(via=via):
                code, raw = self.request("/api/get", {"url": "refused0001", "via": via})
                self.assertEqual(code, 400)
                self.assertIn("via must be one of", json.loads(raw)["error"])
        self.assertEqual(db.list_jobs(self.data), [])

    def test_the_picker_records_only_the_videos_it_queues(self):
        make_video(self.data, "saved000001", shots=10)
        result = db.enqueue_missing_videos(self.data, ["saved000001", "picked00001"], via="picker")
        self.assertEqual(result["video_ids"], ["picked00001"])
        self.assertEqual(db.first_add(self.data, "picked00001")["added_via"], "picker")
        self.assertEqual(db.first_add(self.data, "saved000001"), {})

    def test_the_download_copies_the_route_into_the_files_and_the_list_shows_it(self):
        db.note_first_add(self.data, "fresh000001", "bookmarklet")
        folder = self.data / "fresh000001"
        folder.mkdir()
        video = folder / "fresh000001.mp4"
        video.write_bytes(b"video")
        write_archive_json(self.data, "fresh000001", video)
        archive = json.loads((folder / "archive.json").read_text(encoding="utf-8"))
        self.assertEqual(archive["added_via"], "bookmarklet")
        self.assertTrue(archive["added_at"])

        make_video(self.data, "older000001")
        db.rebuild(self.data)
        by_id = {item["video_id"]: item for item in db.list_videos(self.data)}
        self.assertEqual(by_id["fresh000001"]["added_via"], "bookmarklet")
        self.assertEqual(by_id["older000001"]["added_via"], "")
        code, raw = self.request("/", method="GET")
        html = raw.decode()
        self.assertEqual(code, 200)
        self.assertIn('<span class="a">Added by</span>', html)
        self.assertIn('<span class="a">Bookmarklet</span>', html)
        self.assertIn('<span class="a">Not recorded</span>', html)
        self.assertIn("via: 'pasted'", with_static(html))


class LyricsTests(Base):
    def test_lyrics_for_a_video_not_on_disk_wait_without_making_its_folder(self):
        code, raw = self.request("/api/lyrics", {"video_id": "waiting0001", "lrc": LRC})
        self.assertEqual((code, json.loads(raw)["lyrics"]), (200, "pending"))
        self.assertFalse((self.data / "waiting0001").exists())
        self.assertEqual(db.pending_lyrics(self.data, "waiting0001"), LRC)

        make_video(self.data, "waiting0001", shots=10)
        self.assertTrue(place_pending_lyrics(self.data, "waiting0001"))
        self.assertEqual(lyrics_path(self.data, "waiting0001").read_text(encoding="utf-8"), LRC)
        self.assertIsNone(db.pending_lyrics(self.data, "waiting0001"))
        self.assertFalse(place_pending_lyrics(self.data, "waiting0001"))

    def test_lyrics_for_an_archived_video_are_written_at_once_and_null_removes_them(self):
        make_video(self.data, "archived001", shots=10)
        code, raw = self.request("/api/lyrics", {"video_id": "archived001", "lrc": LRC})
        self.assertEqual((code, json.loads(raw)["lyrics"]), (200, "saved"))
        path = lyrics_path(self.data, "archived001")
        self.assertEqual(path.name, "soundtrack.lrc")
        self.assertEqual(path.read_text(encoding="utf-8"), LRC)
        self.assertEqual(sorted(p.name for p in path.parent.glob(".*.part")), [])

        html = detail_html(load_archive_info(self.data, "archived001"), self.data).decode()
        self.assertIn('download="Title_archived001-archived001.lrc">download lyrics</a>', html)

        code, raw = self.request("/api/lyrics", {"video_id": "archived001", "lrc": None})
        self.assertEqual((code, json.loads(raw)["lyrics"]), (200, "removed"))
        self.assertFalse(path.exists())
        html = detail_html(load_archive_info(self.data, "archived001"), self.data).decode()
        self.assertNotIn("download lyrics", html)

    def test_malformed_lyrics_requests_change_nothing(self):
        cases = [
            ({"video_id": "badlyrics01"}, 400),
            ({"video_id": "badlyrics01", "lrc": 7}, 400),
            ({"video_id": "badlyrics01", "lrc": "  \n"}, 400),
            ({"video_id": "https://youtu.be/badlyrics01", "lrc": LRC}, 400),
            ({"video_id": "badlyrics01", "lrc": "x" * (MAX_LYRICS_BYTES + 1)}, 400),
        ]
        for payload, status in cases:
            with self.subTest(payload=str(payload)[:60]):
                code, _ = self.request("/api/lyrics", payload)
                self.assertEqual(code, status)
        code, _ = self.request("/api/lyrics", {"video_id": "badlyrics01", "lrc": LRC},
                               content_type="text/plain")
        self.assertEqual(code, 415)
        self.assertIsNone(db.pending_lyrics(self.data, "badlyrics01"))

    def test_the_job_puts_waiting_lyrics_beside_the_new_mp3(self):
        db.save_pending_lyrics(self.data, "jobbed00001", LRC)
        make_video(self.data, "jobbed00001", shots=10, mp3=False)
        job = db.insert_job(self.data, "jobbed00001", "jobbed00001")
        claimed = db.claim_next_job(self.data)

        def fake_dump(video, data_dir, video_id, log=print):
            (data_dir / video_id / "_condensed" / "soundtrack.mp3").write_bytes(b"mp3")
            return True

        with patch("yt_archive.jobs.make_shots"), patch("yt_archive.jobs.download"), \
                patch("yt_archive.jobs.dump_soundtrack", side_effect=fake_dump):
            self.queue._run_locked(claimed)
        done = db.get_job(self.data, job["job_id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["message"], "MP3 audio · lyrics")
        self.assertEqual(lyrics_path(self.data, "jobbed00001").read_text(encoding="utf-8"), LRC)


if __name__ == "__main__":
    unittest.main()
