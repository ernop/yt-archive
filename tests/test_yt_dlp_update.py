"""yt-dlp updates itself at most once a week, or on YouTube challenge/extractor
failures at most once every 6 hours, never under a running yt-dlp
(docs/download.md#keeping-yt-dlp-current-settled-2026-11-10)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from yt_archive import download

yt_dlp_running = download._yt_dlp_running

N_CHALLENGE_FAIL = [
    'WARNING: [youtube] [jsc] Error solving n challenge request using "deno" provider: TypeError',
    "WARNING: [youtube] chal0000001: n challenge solving failed: Some formats may be missing.",
    "WARNING: Only images are available for download. use --list-formats to see them",
    "ERROR: [youtube] chal0000001: Requested format is not available. Use --list-formats for a list of available formats",
]
SIG_CHALLENGE_FAIL = [
    "WARNING: [youtube] chal0000001: Signature solving failed: Some formats may be missing.",
    "ERROR: [youtube] chal0000001: Requested format is not available. Use --list-formats for a list of available formats",
]
NO_FORMATS_UPDATE_HINT = [
    "ERROR: [youtube] chal0000001: No video formats found!; please report this issue on "
    "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. "
    "Confirm you are on the latest version using  yt-dlp -U",
]
UPDATED_OUT = (
    "Current version: stable@2026.10.26 from yt-dlp/yt-dlp\n"
    "Latest version: stable@2026.11.23 from yt-dlp/yt-dlp\n"
    "Updated yt-dlp to stable@2026.11.23 from yt-dlp/yt-dlp\n"
)
UP_TO_DATE_OUT = "yt-dlp is up to date (stable@2026.11.23)\n"


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

    def test_failure_update_respects_six_hour_cooldown_and_reports_actual_upgrade(self):
        self.stamp.write_text("recent\n")
        two_hours_ago = time.time() - 2 * 3600
        os.utime(self.stamp, (two_hours_ago, two_hours_ago))
        with patch.object(download.subprocess, "run") as run:
            self.assertFalse(download.update_yt_dlp_after_failure(self.data, log=lambda *_: None))
        run.assert_not_called()

        seven_hours_ago = time.time() - (download.YT_DLP_FAILURE_UPDATE_HOURS + 1) * 3600
        os.utime(self.stamp, (seven_hours_ago, seven_hours_ago))
        up_to_date = subprocess.CompletedProcess([], 0, stdout=UP_TO_DATE_OUT, stderr="")
        with patch.object(download.subprocess, "run", return_value=up_to_date) as run:
            self.assertFalse(download.update_yt_dlp_after_failure(self.data, log=lambda *_: None))
        run.assert_called_once()
        self.assertIn("up to date", self.stamp.read_text())

        os.utime(self.stamp, (seven_hours_ago, seven_hours_ago))
        upgraded = subprocess.CompletedProcess([], 0, stdout=UPDATED_OUT, stderr="")
        with patch.object(download.subprocess, "run", return_value=upgraded) as run:
            self.assertTrue(download.update_yt_dlp_after_failure(self.data, log=lambda *_: None))
        run.assert_called_once()
        self.assertIn("Updated yt-dlp to stable@2026.11.23", self.stamp.read_text())

    def test_failure_update_returns_false_on_nonzero_exit(self):
        failed = subprocess.CompletedProcess([], 1, stdout=UPDATED_OUT, stderr="permission denied\n")
        with patch.object(download.subprocess, "run", return_value=failed):
            self.assertFalse(download.update_yt_dlp_after_failure(self.data, log=lambda *_: None))
        self.assertIn("permission denied", self.stamp.read_text())


class ReactiveUpdateDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.stamp = self.data / download.YT_DLP_UPDATE_FILE
        for target, value in (("_yt_dlp_path", "/opt/bin/yt-dlp"), ("_yt_dlp_running", False)):
            patcher = patch.object(download, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _proc(self, lines: list[str], code: int = 1, on_wait=None):
        proc = Mock(stdout=iter([ln + "\n" for ln in lines]))

        def wait():
            if on_wait:
                on_wait()
            return code

        proc.wait.side_effect = wait
        return proc

    def test_challenge_failure_updates_yt_dlp_and_retries_once_to_success(self):
        for case, first_lines in (
            ("n-challenge", N_CHALLENGE_FAIL),
            ("sig-challenge", SIG_CHALLENGE_FAIL),
            ("no-formats-hint", NO_FORMATS_UPDATE_HINT),
        ):
            with self.subTest(case=case):
                if self.stamp.exists():
                    self.stamp.unlink()
                vid = "chal0000001"
                dest = self.data / vid
                if dest.exists():
                    for p in dest.iterdir():
                        p.unlink()

                def create_video():
                    (dest / f"Sample-{vid}.mp4").write_bytes(b"video")

                procs = [
                    self._proc(first_lines, code=1),
                    self._proc(["downloaded 1280x720 136 mp4"], code=0, on_wait=create_video),
                ]
                upgraded = subprocess.CompletedProcess([], 0, stdout=UPDATED_OUT, stderr="")
                progress_events = []
                with (
                    patch("yt_archive.download.budget.wait_and_take") as wait,
                    patch("yt_archive.download.subprocess.Popen", side_effect=procs) as popen,
                    patch("yt_archive.download.subprocess.run", return_value=upgraded) as run,
                    patch("yt_archive.download._log_resolution"),
                    patch("yt_archive.download._probe_duration", return_value=12.0),
                ):
                    video = download.download(
                        vid, self.data, log=lambda *_: None, progress=lambda f, m: progress_events.append((f, m))
                    )
                self.assertEqual(video.name, f"Sample-{vid}.mp4")
                self.assertEqual(popen.call_count, 2)
                self.assertEqual([call.args[1] for call in wait.call_args_list], ["download", "download"])
                run.assert_called_once()
                self.assertEqual(run.call_args.args[0], ["/opt/bin/yt-dlp", "-U"])
                self.assertIn((None, "checking for yt-dlp update"), progress_events)

    def test_no_retry_when_yt_dlp_is_already_up_to_date_and_error_names_challenge(self):
        procs = [self._proc(N_CHALLENGE_FAIL, code=1)]
        up_to_date = subprocess.CompletedProcess([], 0, stdout=UP_TO_DATE_OUT, stderr="")
        with (
            patch("yt_archive.download.budget.wait_and_take") as wait,
            patch("yt_archive.download.subprocess.Popen", side_effect=procs) as popen,
            patch("yt_archive.download.subprocess.run", return_value=up_to_date) as run,
            self.assertRaises(RuntimeError) as failed,
        ):
            download.download("chal0000001", self.data, log=lambda *_: None)
        self.assertEqual(popen.call_count, 1)
        self.assertEqual([call.args[1] for call in wait.call_args_list], ["download"])
        run.assert_called_once()
        msg = str(failed.exception)
        self.assertTrue(msg.startswith("YouTube JS challenge solving failed"))
        self.assertIn("Requested format is not available", msg)

    def test_no_update_or_retry_inside_cooldown_or_while_yt_dlp_is_running(self):
        self.stamp.write_text("recent\n")
        with (
            patch("yt_archive.download.budget.wait_and_take"),
            patch("yt_archive.download.subprocess.Popen", side_effect=[self._proc(N_CHALLENGE_FAIL)]),
            patch("yt_archive.download.subprocess.run") as run,
            self.assertRaises(RuntimeError),
        ):
            download.download("chal0000001", self.data, log=lambda *_: None)
        run.assert_not_called()

        self.stamp.unlink()
        with (
            patch.object(download, "_yt_dlp_running", return_value=True),
            patch("yt_archive.download.budget.wait_and_take"),
            patch("yt_archive.download.subprocess.Popen", side_effect=[self._proc(N_CHALLENGE_FAIL)]),
            patch("yt_archive.download.subprocess.run") as run,
            self.assertRaises(RuntimeError),
        ):
            download.download("chal0000001", self.data, log=lambda *_: None)
        run.assert_not_called()
        self.assertFalse(self.stamp.exists())

    def test_non_challenge_warnings_upcoming_and_x_do_not_trigger_update(self):
        stray_warning = [
            "WARNING: [youtube] Failed to parse user PREF cookie; please report this issue. "
            "Confirm you are on the latest version using  yt-dlp -U",
            "ERROR: [youtube] chal0000001: Private video",
        ]
        upcoming = [
            "WARNING: [youtube] chal0000001: n challenge solving failed: Some formats may be missing.",
            "ERROR: [youtube] chal0000001: This live event will begin in 2 hours.",
        ]
        tweet_err = [
            "ERROR: [twitter] 1234567890123456789: Some failure. Confirm you are on the latest version using  yt-dlp -U",
        ]
        for vid, lines, exc_type in (
            ("chal0000001", stray_warning, RuntimeError),
            ("chal0000001", upcoming, download.UpcomingStreamError),
            ("x-1234567890123456789", tweet_err, RuntimeError),
        ):
            with self.subTest(vid=vid, exc=exc_type.__name__):
                with (
                    patch("yt_archive.download.budget.wait_and_take"),
                    patch("yt_archive.download.subprocess.Popen", side_effect=[self._proc(lines)]),
                    patch("yt_archive.download.subprocess.run") as run,
                    self.assertRaises(exc_type),
                ):
                    download.download(vid, self.data, log=lambda *_: None)
                run.assert_not_called()

    def test_reactive_update_retry_still_allows_drm_cookieless_retry(self):
        drm_lines = ["ERROR: [youtube] chal0000001: This video is DRM protected"]
        stopped_lines = ["ERROR: [youtube] chal0000001: stopped by test"]
        procs = [
            self._proc(N_CHALLENGE_FAIL, code=1),
            self._proc(drm_lines, code=1),
            self._proc(stopped_lines, code=1),
        ]
        upgraded = subprocess.CompletedProcess([], 0, stdout=UPDATED_OUT, stderr="")
        with (
            patch("yt_archive.download.budget.wait_and_take") as wait,
            patch("yt_archive.download.subprocess.Popen", side_effect=procs) as popen,
            patch("yt_archive.download.subprocess.run", return_value=upgraded) as run,
            self.assertRaises(RuntimeError) as failed,
        ):
            download.download("chal0000001", self.data, log=lambda *_: None)
        run.assert_called_once()
        self.assertEqual([call.args[1] for call in wait.call_args_list], ["download", "download", "download"])
        cmds = [call.args[0] for call in popen.call_args_list]
        self.assertIn("--cookies-from-browser", cmds[0])
        self.assertIn("--cookies-from-browser", cmds[1])
        self.assertNotIn("--cookies-from-browser", cmds[2])
        self.assertEqual(str(failed.exception), "[youtube] chal0000001: stopped by test")

    def test_failure_message_surfaces_challenge_without_masking_drm(self):
        msg = download._yt_dlp_failure(N_CHALLENGE_FAIL, "YouTube")
        self.assertTrue(msg.startswith("YouTube JS challenge solving failed"))
        self.assertIn("Requested format is not available", msg)

        no_err_line = [
            "WARNING: [youtube] chal0000001: n challenge solving failed: Some formats may be missing."
        ]
        self.assertEqual(
            download._yt_dlp_failure(no_err_line, "YouTube"),
            "YouTube JS challenge solving failed (yt-dlp may need a newer release for YouTube's player)",
        )

        drm_with_warn = [
            "WARNING: [youtube] chal0000001: n challenge solving failed: Some formats may be missing.",
            "ERROR: [youtube] chal0000001: This video is DRM protected",
        ]
        self.assertEqual(
            download._yt_dlp_failure(drm_with_warn, "YouTube"),
            "[youtube] chal0000001: This video is DRM protected",
        )

    def test_transient_dns_failure_waits_for_network_and_retries_once(self):
        dns_fail = [
            "WARNING: [youtube] HTTPSConnection(host='www.youtube.com', port=443): "
            "Failed to resolve 'www.youtube.com' ([Errno -3] Temporary failure in name resolution). Retrying (1/3)...",
            "ERROR: [youtube] net00000001: Unable to download API page: "
            "HTTPSConnection(host='www.youtube.com', port=443): Failed to resolve 'www.youtube.com' "
            "([Errno -2] Name or service not known)",
        ]
        vid = "net00000001"
        dest = self.data / vid

        def create_video():
            (dest / f"Sample-{vid}.mp4").write_bytes(b"video")

        procs = [
            self._proc(dns_fail, code=1),
            self._proc(["downloaded 1280x720 136 mp4"], code=0, on_wait=create_video),
        ]
        progress_events = []
        with (
            patch("yt_archive.download.budget.wait_and_take") as wait,
            patch("yt_archive.download.subprocess.Popen", side_effect=procs) as popen,
            patch("yt_archive.download._can_resolve_host", side_effect=[False, True]) as resolve,
            patch("yt_archive.download.time.sleep"),
            patch("yt_archive.download._log_resolution"),
            patch("yt_archive.download._probe_duration", return_value=12.0),
        ):
            video = download.download(
                vid, self.data, log=lambda *_: None, progress=lambda f, m: progress_events.append((f, m))
            )
        self.assertEqual(video.name, f"Sample-{vid}.mp4")
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(resolve.call_count, 2)
        self.assertEqual([call.args[1] for call in wait.call_args_list], ["download", "download"])
        self.assertIn((None, "waiting for network to reach YouTube"), progress_events)

    def test_persistent_dns_failure_raises_without_retry_after_timeout(self):
        dns_fail = [
            "ERROR: [youtube] net00000002: Unable to download API page: "
            "Failed to resolve 'www.youtube.com' ([Errno -2] Name or service not known)",
        ]
        with (
            patch("yt_archive.download.budget.wait_and_take") as wait,
            patch("yt_archive.download.subprocess.Popen", side_effect=[self._proc(dns_fail, code=1)]) as popen,
            patch("yt_archive.download._wait_for_network", return_value=False) as wait_net,
            self.assertRaises(RuntimeError) as failed,
        ):
            download.download("net00000002", self.data, log=lambda *_: None)
        wait_net.assert_called_once()
        self.assertEqual(popen.call_count, 1)
        self.assertEqual([call.args[1] for call in wait.call_args_list], ["download"])
        self.assertIn("Failed to resolve 'www.youtube.com'", str(failed.exception))


if __name__ == "__main__":
    unittest.main()
