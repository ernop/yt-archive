from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from yt_archive import db
from yt_archive.paths import transcript_json_path, transcript_vtt_path
from yt_archive.transcribe import _spoken_text, normalize_config
from yt_archive.web import detail_html


VIDEO_ID = "abcdefghijk"


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        folder = self.data / VIDEO_ID
        condensed = folder / "_condensed"
        condensed.mkdir(parents=True)
        (folder / "video.mp4").write_bytes(b"video")
        (condensed / "soundtrack.mp3").write_bytes(b"audio")
        (folder / "archive.json").write_text(
            json.dumps(
                {
                    "video_id": VIDEO_ID,
                    "title": "Test video",
                    "channel": "Test channel",
                    "duration": 80,
                }
            ),
            encoding="utf-8",
        )
        db.rebuild(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_work_identity_is_deduplicated_and_recovered(self):
        config = normalize_config()
        first = db.enqueue_work(
            self.data,
            work_key="same-work",
            video_id=VIDEO_ID,
            kind="transcribe",
            config=config,
        )
        second = db.enqueue_work(
            self.data,
            work_key="same-work",
            video_id=VIDEO_ID,
            kind="transcribe",
            config=config,
        )
        self.assertEqual(first["job_id"], second["job_id"])
        claimed = db.claim_next_work(self.data)
        self.assertEqual(claimed["status"], "running")
        forced_while_running = db.enqueue_work(
            self.data,
            work_key="same-work",
            video_id=VIDEO_ID,
            kind="transcribe",
            config=config,
            force=True,
        )
        self.assertEqual(forced_while_running["status"], "running")
        self.assertEqual(db.recover_work_jobs(self.data), 1)
        reclaimed = db.claim_next_work(self.data)
        self.assertEqual(reclaimed["job_id"], first["job_id"])
        self.assertEqual(reclaimed["attempts"], 2)

    def test_recovery_finishes_already_published_matching_work(self):
        job = db.enqueue_work(
            self.data,
            work_key="published-work",
            video_id=VIDEO_ID,
            kind="transcribe",
            config=normalize_config(),
        )
        db.claim_next_work(self.data)
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\npublished before restart\n"
        artifact = {
            "video_id": VIDEO_ID,
            "work_key": "published-work",
            "generation_id": f"{job['job_id']}:1",
            "vtt_sha256": hashlib.sha256(vtt.encode()).hexdigest(),
            "model": "large-v3",
            "full_text": "published before restart",
            "segments": [
                {
                    "index": 0,
                    "start": 1,
                    "end": 2,
                    "text": "published before restart",
                    "speaker": "",
                    "words": [],
                }
            ],
        }
        transcript_json_path(self.data, VIDEO_ID).write_text(
            json.dumps(artifact), encoding="utf-8"
        )
        transcript_vtt_path(self.data, VIDEO_ID).write_text(
            vtt,
            encoding="utf-8",
        )
        self.assertEqual(db.recover_work_jobs(self.data), 1)
        recovered = db.get_work_job(self.data, job["job_id"])
        self.assertEqual(recovered["status"], "done")
        self.assertEqual(recovered["progress"], 1)

    def test_transcript_is_stored_and_global_search_finds_spoken_words(self):
        artifact = {
            "video_id": VIDEO_ID,
            "engine": "test",
            "model": "large-v3",
            "language": "en",
            "language_probability": 0.99,
            "duration": 80,
            "created_at": "2026-08-30T00:00:00+00:00",
            "full_text": "An uncommon telescope phrase",
            "segments": [
                {
                    "index": 0,
                    "start": 12.5,
                    "end": 14.5,
                    "text": "An uncommon telescope phrase",
                    "speaker": "Ada",
                    "words": [
                        {
                            "start": 12.5,
                            "end": 12.8,
                            "word": "An",
                            "probability": 0.99,
                        }
                    ],
                }
            ],
        }
        db.replace_transcript(self.data, artifact)
        loaded = db.get_transcript(self.data, VIDEO_ID)
        self.assertEqual(loaded["segments"][0]["speaker"], "Ada")
        self.assertEqual(
            [row["video_id"] for row in db.list_videos(self.data, "telescope")],
            [VIDEO_ID],
        )

    def test_rebuild_restores_database_from_transcript_file(self):
        artifact = {
            "video_id": VIDEO_ID,
            "model": "large-v3",
            "full_text": "restorable words",
            "segments": [
                {
                    "index": 0,
                    "start": 1,
                    "end": 2,
                    "text": "restorable words",
                    "speaker": "",
                    "words": [],
                }
            ],
        }
        transcript_json_path(self.data, VIDEO_ID).write_text(
            json.dumps(artifact), encoding="utf-8"
        )
        transcript_vtt_path(self.data, VIDEO_ID).write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nrestorable words\n",
            encoding="utf-8",
        )
        db.rebuild(self.data)
        self.assertEqual(
            db.get_transcript(self.data, VIDEO_ID)["full_text"], "restorable words"
        )

    def test_detail_page_opens_framesheets_in_new_tab(self):
        sheet = self.data / VIDEO_ID / "_condensed" / "framesheet.png"
        sheet.write_bytes(b"png")
        html = detail_html(
            {
                "video_id": VIDEO_ID,
                "title": "Test",
                "duration": 80,
                "channel": "Channel",
            },
            self.data,
        ).decode()
        self.assertIn('target="_blank" rel="noopener"', html)
        self.assertIn("MP3 audio", html)
        self.assertIn("Transcribe audio", html)

    def test_non_speech_labels_are_not_mixed_with_words(self):
        self.assertEqual(_spoken_text("[Music] Hello (birds chirping)"), "Hello")
        self.assertEqual(_spoken_text("[Applause]"), "")


if __name__ == "__main__":
    unittest.main()
