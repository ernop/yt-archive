"""yt-dlp updates itself at most once a week, never under a running yt-dlp
(docs/download.md#keeping-yt-dlp-current)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from yt_archive import download

yt_dlp_running = download._yt_dlp_running


class YtDlpUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.stamp = self.data / download.YT_DLP_UPDATE_FILE
        for target, value in (("_yt_dlp_path", "/opt/bin/yt-dlp"), ("_yt_dlp_running", False)):
            patcher = patch.object(download, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def _update(self):
        answer = subprocess.CompletedProcess([], 0, stdout="yt-dlp is up to date (stable@2026.10.26)\n", stderr="")
        with patch.object(download.subprocess, "run", return_value=answer) as run:
            asked = download.keep_yt_dlp_current(self.data, log=lambda message: None)
        return asked, run

    def test_a_week_after_the_last_ask_it_asks_again_and_keeps_the_answer(self):
        self.stamp.write_text("old\n")
        week_ago = time.time() - download.YT_DLP_UPDATE_DAYS * 86400 - 60
        os.utime(self.stamp, (week_ago, week_ago))
        asked, run = self._update()
        self.assertTrue(asked)
        self.assertEqual(run.call_args.args[0], ["/opt/bin/yt-dlp", "-U"])
        self.assertIn("up to date", self.stamp.read_text())

    def test_within_the_week_nothing_is_sent(self):
        self.stamp.write_text("recent\n")
        asked, run = self._update()
        self.assertFalse(asked)
        run.assert_not_called()

    def test_the_first_run_asks(self):
        asked, run = self._update()
        self.assertTrue(asked)
        self.assertTrue(self.stamp.is_file())

    def test_a_running_yt_dlp_puts_it_off_to_the_next_hour(self):
        with patch.object(download, "_yt_dlp_running", return_value=True):
            asked, run = self._update()
        self.assertFalse(asked)
        run.assert_not_called()
        self.assertFalse(self.stamp.exists())

    def test_a_yt_dlp_run_by_python_is_seen_running(self):
        script = self.data / "yt-dlp"
        script.write_text("import time; time.sleep(30)\n")
        child = subprocess.Popen(["python3", str(script)])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        deadline = time.time() + 5
        while time.time() < deadline and not yt_dlp_running(str(script)):
            time.sleep(0.05)
        self.assertTrue(yt_dlp_running(str(script)))


if __name__ == "__main__":
    unittest.main()
