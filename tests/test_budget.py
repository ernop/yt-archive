from __future__ import annotations

import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from yt_archive import budget, db
from yt_archive.download import download


class BudgetTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)

    def test_refuses_past_the_limit_until_the_oldest_request_leaves_the_hour(self):
        now = 1_000_000.0
        with patch.dict(budget.LIMITS, {"listing": 3}):
            self.assertTrue(all(budget.take(self.data, "listing", now + i) for i in range(3)))
            self.assertFalse(budget.take(self.data, "listing", now + 10))
            self.assertEqual(budget.free_at(self.data, "listing", now + 10), now + budget.WINDOW)
            self.assertTrue(budget.take(self.data, "listing", now + budget.WINDOW + .5))
            self.assertFalse(budget.take(self.data, "listing", now + budget.WINDOW + .6))
            self.assertTrue(budget.take(self.data, "download", now + 10))

    def test_parallel_callers_never_overspend(self):
        with patch.dict(budget.LIMITS, {"download": 20}), ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda _: budget.take(self.data, "download"), range(50)))
        self.assertEqual(sum(results), 20)

    def test_wait_and_take_sleeps_until_a_unit_is_free(self):
        waits, sleeps = [], []

        def hour_passes(seconds):
            sleeps.append(seconds)
            conn = db.connect(self.data)
            conn.execute("DELETE FROM youtube_requests")
            conn.commit()
            conn.close()

        with patch.dict(budget.LIMITS, {"download": 1}):
            budget.take(self.data, "download", time.time() - budget.WINDOW + 30)
            budget.wait_and_take(self.data, "download", waits.append, sleep=hour_passes)
        self.assertEqual(len(waits), 1)
        self.assertTrue(1 <= sleeps[0] <= 60)
        self.assertEqual(len(db.youtube_request_times(self.data, "download", 0)), 1)

    def test_each_download_is_paid_for_before_yt_dlp_runs(self):
        class Paid(Exception):
            pass

        with patch("yt_archive.download.budget.wait_and_take", side_effect=Paid) as wait, \
                patch("yt_archive.download.subprocess.Popen") as popen:
            with self.assertRaises(Paid):
                download("choose00001", self.data, log=lambda *_: None)
            self.assertEqual(wait.call_args.args[:2], (self.data, "download"))
            popen.assert_not_called()
            folder = self.data / "saved000001"
            folder.mkdir()
            (folder / "saved000001.mp4").write_bytes(b"video")
            wait.reset_mock()
            download("saved000001", self.data, log=lambda *_: None)
            wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
