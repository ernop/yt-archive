from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from yt_archive import db
from yt_archive.download import parse_progress
from yt_archive.framesheet import _sheet_chunk_sizes, retile_sheets
from yt_archive.jobs import JobQueue
from yt_archive.paths import archive_needs, card_thumb_path, framesheet_paths, preview_path
from yt_archive.web import _byte_range, detail_html, load_archive_info, make_handler

CURRENT = {"compare": "last-kept", "sample": "change"}


def _tiny_png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (4, 4)).save(out, format="PNG")
    return out.getvalue()


TINY_PNG = _tiny_png()


def make_video(data: Path, video_id: str, *, shots: int = 0, sheets: int = 1,
               current: bool = True, mp3: bool = True, preview: bool = True, extra: dict | None = None):
    folder = data / video_id
    cond = folder / "_condensed"
    (cond / "shots").mkdir(parents=True)
    (folder / f"{video_id}.mp4").write_bytes(b"video")
    archive = {"video_id": video_id, "title": f"Title {video_id}", **(extra or {})}
    (folder / "archive.json").write_text(json.dumps(archive), encoding="utf-8")
    if shots:
        names = ["framesheet.png"] if sheets == 1 else [f"framesheet-{n}.png" for n in range(1, sheets + 1)]
        for name in names:
            (cond / name).write_bytes(TINY_PNG)
        summary = {"shots_kept": shots, "framesheets": names, "shots": [], **(CURRENT if current else {})}
        (cond / "shots.json").write_text(json.dumps(summary), encoding="utf-8")
        if preview:
            (cond / "preview.jpg").write_bytes(b"preview")
    if mp3:
        (cond / "soundtrack.mp3").write_bytes(b"mp3")
    return folder


def fake_queue(data: Path) -> JobQueue:
    queue = JobQueue.__new__(JobQueue)
    queue.data_dir = data
    queue._cv = threading.Condition()
    return queue


class ProgressLineTests(unittest.TestCase):
    def test_download_progress_becomes_fraction_and_readable_message(self):
        fraction, message = parse_progress(
            "ytarchive-progress downloading 58000000 129900000 NA 4200000 20 av01.0.08M.08"
        )
        self.assertAlmostEqual(fraction, 58 / 129.9, places=3)
        self.assertEqual(message, "downloading video · 58.0 MB of 129.9 MB · 4.2 MB/s · 0:20 left")

    def test_estimate_is_used_when_total_is_unknown_and_finished_lines_complete(self):
        fraction, message = parse_progress("ytarchive-progress downloading 10 NA 40 NA NA none")
        self.assertEqual(fraction, 0.25)
        self.assertTrue(message.startswith("downloading audio"))
        self.assertEqual(parse_progress("ytarchive-progress finished 40 40 NA NA NA none"),
                         (1.0, "Audio stream downloaded"))

    def test_other_output_is_not_progress(self):
        self.assertIsNone(parse_progress("[youtube] abc: Downloading webpage"))
        self.assertIsNone(parse_progress("WARNING: To suppress this warning, add --no-update"))


class ArchiveNeedsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def test_up_to_date_video_needs_nothing(self):
        make_video(self.data, "complete001", shots=10)
        self.assertEqual(archive_needs(self.data, "complete001"), [])

    def test_each_kind_of_missing_work_is_named(self):
        make_video(self.data, "noshots0001", mp3=False)
        make_video(self.data, "oldmethod01", shots=10, current=False)
        make_video(self.data, "oversized01", shots=300, sheets=1)
        make_video(self.data, "nopreview01", shots=10, preview=False)
        make_video(self.data, "silent00001", shots=10, mp3=False, extra={"audio_stream": False})
        self.assertEqual(archive_needs(self.data, "noshots0001"), ["shots", "audio"])
        self.assertEqual(archive_needs(self.data, "oldmethod01"), ["reshoot"])
        self.assertEqual(archive_needs(self.data, "oversized01"), ["resplit"])
        self.assertEqual(archive_needs(self.data, "nopreview01"), ["preview"])
        make_video(self.data, "truncated01", shots=10)
        (self.data / "truncated01" / "_condensed" / "framesheet.png").write_bytes(TINY_PNG[:-6])
        self.assertEqual(archive_needs(self.data, "truncated01"), ["resplit"])
        self.assertEqual(archive_needs(self.data, "silent00001"), [])

    def test_folder_without_a_video_file_is_not_flagged(self):
        (self.data / "nofile00001").mkdir()
        self.assertEqual(archive_needs(self.data, "nofile00001"), [])

    def test_catalog_summary_counts_videos_by_need(self):
        make_video(self.data, "complete001", shots=10)
        make_video(self.data, "oldmethod01", shots=10, current=False, mp3=False)
        db.rebuild(self.data)
        summary = db.archive_needs_summary(self.data)
        self.assertEqual(summary["videos"], ["oldmethod01"])
        self.assertEqual(summary["counts"], {"reshoot": 1, "audio": 1})

    def test_card_thumbnail_is_preview_or_youtube_image_never_a_sheet(self):
        folder = make_video(self.data, "complete001", shots=10)
        self.assertEqual(card_thumb_path(self.data, "complete001"), preview_path(self.data, "complete001"))
        preview_path(self.data, "complete001").unlink()
        self.assertIsNone(card_thumb_path(self.data, "complete001"))
        (folder / "complete001.webp").write_bytes(b"thumb")
        self.assertEqual(card_thumb_path(self.data, "complete001").name, "complete001.webp")


class RetileTests(unittest.TestCase):
    def test_oversized_sheet_is_split_from_existing_shot_images(self):
        from PIL import Image

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data = Path(tmp.name)
        folder = make_video(data, "oversized01", shots=260, preview=False)
        shots = folder / "_condensed" / "shots"
        records = []
        for n in range(260):
            name = f"{n:04d}.png"
            Image.new("RGB", (32, 18), (n % 256, 0, 0)).save(shots / name)
            records.append({"file": name, "t0": n * 0.5, "kept": True})
        summary = json.loads((folder / "_condensed" / "shots.json").read_text())
        summary["shots"] = records
        (folder / "_condensed" / "shots.json").write_text(json.dumps(summary))
        self.assertEqual(archive_needs(data, "oversized01"), ["resplit"])

        names = retile_sheets(data, "oversized01", log=lambda _: None)

        self.assertEqual(len(names), len(_sheet_chunk_sizes(260)))
        self.assertEqual([p.name for p in framesheet_paths(data, "oversized01")], names)
        self.assertTrue(preview_path(data, "oversized01").is_file())
        self.assertEqual(archive_needs(data, "oversized01"), [])
        kept = json.loads((folder / "_condensed" / "shots.json").read_text())
        self.assertEqual(kept["framesheets"], names)
        self.assertEqual(kept["compare"], "last-kept")
        self.assertEqual(sorted(p.name for p in (folder / "_condensed").glob(".*.part")), [])


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.queue = fake_queue(self.data)

    def test_up_to_date_video_is_reported_complete_without_a_job(self):
        make_video(self.data, "complete001", shots=10)
        result = self.queue.submit("https://youtu.be/complete001")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["title"], "Title complete001")
        self.assertEqual(db.list_jobs(self.data), [])
        forced = self.queue.submit("complete001", force_shots=True)
        self.assertEqual(forced["status"], "queued")

    def test_an_open_job_is_reused(self):
        first = self.queue.submit("choose00001")
        again = self.queue.submit("https://www.youtube.com/watch?v=choose00001")
        self.assertTrue(again["existing"])
        self.assertEqual(again["job_id"], first["job_id"])
        self.assertEqual(len(db.list_jobs(self.data)), 1)

    def test_overview_shows_open_and_failed_jobs_but_not_finished_ones(self):
        done = db.insert_job(self.data, "a", "videoa00001")
        db.finish_job(self.data, done["job_id"], message="MP3 audio")
        failed = db.insert_job(self.data, "c", "videoc00001")
        db.finish_job(self.data, failed["job_id"], error="HTTP 403")
        retried_fail = db.insert_job(self.data, "d", "videod00001")
        db.finish_job(self.data, retried_fail["job_id"], error="timeout")
        db.insert_job(self.data, "d", "videod00001")
        db.insert_job(self.data, "e", "videoe00001")
        running = db.claim_next_job(self.data)

        q = db.queue_overview(self.data)

        self.assertEqual([j["job_id"] for j in q["running"]], [running["job_id"]])
        self.assertEqual(running["video_id"], "videod00001")
        self.assertEqual([j["video_id"] for j in q["waiting"]], ["videoe00001"])
        self.assertEqual(q["waiting_count"], 1)
        self.assertEqual([j["video_id"] for j in q["failed"]], ["videoc00001"])
        self.assertEqual(q["failed"][0]["error"], "HTTP 403")
        self.assertEqual(sorted(q), ["failed", "running", "waiting", "waiting_count"])
        self.assertEqual(db.get_job(self.data, done["job_id"])["message"], "MP3 audio")

    def test_restart_requeues_without_forcing_a_fresh_shot_pass(self):
        job = db.insert_job(self.data, "a", "videoa00001")
        db.claim_next_job(self.data)
        db.update_job(self.data, job["job_id"], download="done", shots="running")
        self.assertEqual(db.recover_jobs(self.data), 1)
        again = db.get_job(self.data, job["job_id"])
        self.assertEqual((again["status"], again["download"], again["shots"]), ("queued", "done", "pending"))
        self.assertFalse(again["force_shots"])
        self.assertEqual(again["message"], "restarted after the service stopped")

    def test_resumed_job_does_only_what_is_left_and_reports_all_of_it(self):
        make_video(self.data, "resumed0001", shots=10, mp3=False)
        job = db.insert_job(self.data, "resumed0001", "resumed0001")
        db.claim_next_job(self.data)
        db.update_job(self.data, job["job_id"], download="skipped", shots="done", audio="running")
        db.recover_jobs(self.data)
        claimed = db.claim_next_job(self.data)

        def fake_dump(video, data_dir, video_id, log=print):
            (data_dir / video_id / "_condensed" / "soundtrack.mp3").write_bytes(b"mp3")
            return True

        with patch("yt_archive.jobs.make_shots") as shots, patch("yt_archive.jobs.download") as download, \
                patch("yt_archive.jobs.dump_soundtrack", side_effect=fake_dump):
            self.queue._run_locked(claimed)
        shots.assert_not_called()
        download.assert_not_called()
        done = db.get_job(self.data, job["job_id"])
        self.assertEqual((done["status"], done["shots"], done["audio"]), ("done", "done", "done"))
        self.assertEqual(done["message"], "shots · MP3 audio")

    def test_waiting_list_is_short_unless_asked(self):
        for n in range(12):
            db.insert_job(self.data, "x", f"waiting{n:04d}")
        q = db.queue_overview(self.data)
        self.assertEqual((len(q["waiting"]), q["waiting_count"]), (db.WAITING_SHOWN, 12))
        self.assertEqual(len(db.queue_overview(self.data, 1000)["waiting"]), 12)

    def test_remove_and_dismiss_only_touch_their_own_state(self):
        waiting = db.insert_job(self.data, "a", "videoa00001")
        failed = db.insert_job(self.data, "b", "videob00001")
        db.finish_job(self.data, failed["job_id"], error="boom")
        self.assertFalse(db.dismiss_failed_job(self.data, waiting["job_id"]))
        self.assertFalse(db.remove_waiting_job(self.data, failed["job_id"]))
        self.assertTrue(db.dismiss_failed_job(self.data, failed["job_id"]))
        self.assertTrue(db.remove_waiting_job(self.data, waiting["job_id"]))
        q = db.queue_overview(self.data)
        self.assertEqual((q["waiting"], q["failed"]), ([], []))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.handler_class = make_handler(self.data, fake_queue(self.data), Mock())

    def request(self, path, payload=None, method="POST", host="localhost:8765"):
        handler = self.handler_class.__new__(self.handler_class)
        raw = json.dumps(payload or {}).encode()
        handler.path = path
        handler.headers = {"Host": host, "Content-Type": "application/json", "Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        getattr(handler, "do_" + method)()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue()

    def test_get_routes_refuse_a_rebound_host(self):
        make_video(self.data, "complete001", shots=10)
        for path in ("/", "/api/jobs", "/v/complete001", "/media/complete001/archive.json"):
            with self.subTest(path=path):
                code, raw = self.request(path, method="GET", host="rebound.evil.test:8765")
                self.assertEqual(code, 403)
                self.assertNotIn(b"complete001", raw)

    def test_queue_endpoints(self):
        code, raw = self.request("/api/get", {"url": "choose00001"})
        self.assertEqual(code, 202)
        job = json.loads(raw)
        code, raw = self.request("/api/get", {"url": "choose00001"})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(raw)["existing"])
        code, raw = self.request("/api/jobs", method="GET")
        self.assertEqual([j["video_id"] for j in json.loads(raw)["waiting"]], ["choose00001"])
        code, _ = self.request(f"/api/jobs/{job['job_id']}/dismiss")
        self.assertEqual(code, 409)
        code, _ = self.request(f"/api/jobs/{job['job_id']}/remove")
        self.assertEqual(code, 200)
        code, raw = self.request("/api/jobs", method="GET")
        self.assertEqual(json.loads(raw)["waiting_count"], 0)

    def test_update_archive_queues_only_videos_that_need_work(self):
        make_video(self.data, "complete001", shots=10)
        make_video(self.data, "oldmethod01", shots=10, current=False)
        db.rebuild(self.data)
        code, raw = self.request("/api/update-archive")
        self.assertEqual((code, json.loads(raw)["video_ids"]), (202, ["oldmethod01"]))
        code, raw = self.request("/api/update-archive")
        self.assertEqual((code, json.loads(raw)["queued"]), (200, 0))
        code, raw = self.request("/", method="GET")
        html = raw.decode()
        self.assertIn('id="update-archive">Update 1 videos</button>', html)
        self.assertIn("<b>1</b>old shot detection", html)
        self.assertIn('<span class="busy">updating</span>', html)

    def test_detail_page_shows_open_job_and_silent_videos(self):
        make_video(self.data, "silent00001", shots=10, mp3=False,
                   extra={"audio_stream": False, "width": 1920, "height": 1080, "duration": 125})
        html = detail_html(load_archive_info(self.data, "silent00001"), self.data).decode()
        self.assertIn("no audio track", html)
        self.assertNotIn('id="dump-audio"', html)
        self.assertIn("<b>1920×1080</b>", html)
        self.assertIn("<b>2:05</b>", html)
        self.assertNotIn("Being updated now", html)
        job = db.insert_job(self.data, "silent00001", "silent00001")
        code, raw = self.request("/v/silent00001", method="GET")
        self.assertEqual(code, 200)
        self.assertIn("Being updated now", raw.decode())
        self.assertTrue(job["job_id"])

    def test_byte_ranges(self):
        self.assertEqual(_byte_range("bytes=0-99", 1000), (0, 99))
        self.assertEqual(_byte_range("bytes=900-", 1000), (900, 999))
        self.assertEqual(_byte_range("bytes=-100", 1000), (900, 999))
        self.assertEqual(_byte_range("bytes=-5000", 1000), (0, 999))
        self.assertEqual(_byte_range("bytes=500-5000", 1000), (500, 999))
        self.assertEqual(_byte_range("bytes=1000-", 1000), "unsatisfiable")
        self.assertEqual(_byte_range("bytes=-0", 1000), "unsatisfiable")
        self.assertIsNone(_byte_range("bytes=0-1,5-9", 1000))
        self.assertIsNone(_byte_range(None, 1000))


if __name__ == "__main__":
    unittest.main()
