"""One-at-a-time archive jobs, persisted in SQLite so restarts recover."""
from __future__ import annotations

import threading
import traceback

from . import db
from .download import download
from .framesheet import make_shots
from .paths import find_video_file, framesheet_path, parse_video_id, watch_url


class JobQueue:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self._cv = threading.Condition()
        n = db.recover_jobs(data_dir)
        if n:
            print(f"recovered {n} interrupted job(s)", flush=True)
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()
        if db.has_queued_jobs(data_dir):
            with self._cv:
                self._cv.notify()

    def submit(self, url: str, *, force_video: bool = False, force_shots: bool = False) -> dict:
        video_id = parse_video_id(url)
        if force_video:
            force_shots = True
        if not force_video and not force_shots:
            existing = db.find_open_job(self.data_dir, video_id)
            if existing:
                return existing
        job = db.insert_job(
            self.data_dir,
            url.strip() or watch_url(video_id),
            video_id,
            force_video=force_video,
            force_shots=force_shots,
        )
        with self._cv:
            self._cv.notify()
        return job

    def get(self, job_id: str) -> dict | None:
        return db.get_job(self.data_dir, job_id)

    def snapshot(self, job: dict) -> dict:
        return job

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not db.has_queued_jobs(self.data_dir):
                    self._cv.wait()
            job = db.claim_next_job(self.data_dir)
            if job:
                self._run(job)

    def _run(self, job: dict) -> None:
        job_id = job["job_id"]
        video_id = job["video_id"]

        def log(msg: str) -> None:
            print(f"[job {job_id}] {msg}", flush=True)
            db.append_job_log(self.data_dir, job_id, str(msg))

        try:
            video = find_video_file(self.data_dir, video_id)
            need_video = job["force_video"] or video is None
            if need_video:
                db.update_job(self.data_dir, job_id, download="running")
                log(f"get {video_id}" + (" (reget video)" if job["force_video"] else ""))
                video = download(
                    video_id, self.data_dir, log=log, force=job["force_video"]
                )
                db.update_job(self.data_dir, job_id, download="done")
                did_download = True
            else:
                db.update_job(self.data_dir, job_id, download="skipped")
                log(f"already have {video.name}")
                did_download = False

            sheet = framesheet_path(self.data_dir, video_id)
            need_shots = job["force_shots"] or did_download or not sheet.is_file()
            if need_shots:
                db.update_job(self.data_dir, job_id, shots="running")
                log("making squash framesheet" + (" (reget png)" if job["force_shots"] else ""))
                make_shots(video, self.data_dir, video_id, layout="squash", log=log)
                db.update_job(self.data_dir, job_id, shots="done")
            else:
                db.update_job(self.data_dir, job_id, shots="skipped")
                log("framesheet already exists")

            db.update_job(self.data_dir, job_id, status="done")
            log("done")
        except Exception as exc:
            db.update_job(self.data_dir, job_id, status="error", error=str(exc))
            log(f"error: {exc}")
            traceback.print_exc()
