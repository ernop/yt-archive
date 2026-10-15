"""One-at-a-time archive jobs, persisted in SQLite so restarts recover."""
from __future__ import annotations

import threading
import time
import traceback

from . import db
from .audio import dump_soundtrack
from .download import clear_archive_extras, download
from .framesheet import ensure_preview, make_shots, retile_sheets
from .locks import try_video_lock
from .paths import (
    VIDEO_ID_RE,
    archive_needs,
    find_video_file,
    load_archive_info,
    parse_video_id,
    soundtrack_path,
    transcript_json_path,
    transcript_vtt_path,
    watch_url,
)


class _Progress:
    """Job progress for the queue panel, which polls about once a second."""

    def __init__(self, data_dir, job_id, every: float = 0.5):
        self.data_dir = data_dir
        self.job_id = job_id
        self.every = every
        self._last = 0.0

    def __call__(self, fraction: float | None, message: str) -> None:
        now = time.monotonic()
        if fraction is not None and 0 < fraction < 1 and now - self._last < self.every:
            return
        self._last = now
        db.set_job_progress(self.data_dir, self.job_id, fraction, message)


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
        """Queue a video. Without force, an open job is reused and an up-to-date video
        is reported as complete instead of queueing a job that would do nothing."""
        video_id = parse_video_id(url)
        if force_video:
            force_shots = True
        with self._cv:
            if not force_video and not force_shots:
                existing = db.find_open_job(self.data_dir, video_id)
                if existing:
                    return {**existing, "existing": True}
                if find_video_file(self.data_dir, video_id) and not archive_needs(
                    self.data_dir, video_id
                ):
                    info = load_archive_info(self.data_dir, video_id)
                    return {"status": "complete", "video_id": video_id,
                            "title": info.get("title") or ""}
            job = db.insert_job(
                self.data_dir,
                url.strip() or watch_url(video_id),
                video_id,
                force_video=force_video,
                force_shots=force_shots,
            )
            self._cv.notify()
        return job

    def submit_many(self, video_ids: list[str], titles: dict[str, str] | None = None) -> dict:
        if any(not isinstance(vid, str) or not VIDEO_ID_RE.fullmatch(vid) for vid in video_ids):
            raise ValueError("Invalid YouTube video ID in selection.")
        with self._cv:
            result = db.enqueue_missing_videos(self.data_dir, video_ids, titles)
            self._cv.notify()
        return result

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
        with try_video_lock(job["video_id"]) as acquired:
            if not acquired:
                db.defer_job(self.data_dir, job["job_id"])
                with self._cv:
                    self._cv.wait(timeout=1)
                return
            self._run_locked(job)

    def _run_locked(self, job: dict) -> None:
        job_id = job["job_id"]
        video_id = job["video_id"]
        progress = _Progress(self.data_dir, job_id)
        # What each step did, for the queue panel; steps finished before a restart count too.
        did = {
            step: label
            for step, label in (("download", "downloaded"), ("shots", "shots"), ("audio", "MP3 audio"))
            if job.get(step) == "done"
        }

        def skip(step: str) -> None:
            if job.get(step) != "done":
                db.update_job(self.data_dir, job_id, **{step: "skipped"})

        def log(msg: str) -> None:
            print(f"[job {job_id}] {msg}", flush=True)
            db.append_job_log(self.data_dir, job_id, str(msg))

        try:
            mp3 = soundtrack_path(self.data_dir, video_id)
            if job["force_video"]:
                if mp3.is_file():
                    mp3.unlink()
                    log("removed old MP3 audio for reget")
                for path in (
                    transcript_json_path(self.data_dir, video_id),
                    transcript_vtt_path(self.data_dir, video_id),
                ):
                    if path.is_file():
                        path.unlink()
                clear_archive_extras(
                    self.data_dir, video_id, "transcript", "captions", "audio_stream"
                )
                db.delete_transcript(self.data_dir, video_id)
                log("cleared stale transcript state for reget")
            video = find_video_file(self.data_dir, video_id)
            # A reget that already downloaded before a restart does not download again.
            did_download = video is None or (job["force_video"] and job["download"] != "done")
            if did_download:
                db.update_job(self.data_dir, job_id, download="running")
                progress(None, "starting download")
                log(f"get {video_id}" + (" (reget video)" if job["force_video"] else ""))
                video = download(
                    video_id, self.data_dir, log=log, force=job["force_video"], progress=progress
                )
                db.update_job(self.data_dir, job_id, download="done")
                did["download"] = "downloaded"
            else:
                skip("download")
                log(f"already have {video.name}")

            needs = archive_needs(self.data_dir, video_id)
            if job["force_shots"] or did_download or {"shots", "reshoot"} & set(needs):
                db.update_job(self.data_dir, job_id, shots="running")
                log("making squash framesheet" + (" (reget png)" if job["force_shots"] else ""))
                summary = make_shots(
                    video, self.data_dir, video_id, layout="squash", log=log, progress=progress
                )
                db.update_job(self.data_dir, job_id, shots="done")
                kept = summary["shots_kept"]
                did["shots"] = f"{kept} shot" + ("" if kept == 1 else "s")
            elif "resplit" in needs:
                db.update_job(self.data_dir, job_id, shots="running")
                log("splitting the framesheet into balanced sheets")
                names = retile_sheets(self.data_dir, video_id, log=log, progress=progress)
                db.update_job(self.data_dir, job_id, shots="done")
                did["shots"] = f"framesheet in {len(names)} sheet" + ("" if len(names) == 1 else "s")
            elif "preview" in needs:
                db.update_job(self.data_dir, job_id, shots="running")
                progress(None, "making the list thumbnail")
                ensure_preview(self.data_dir, video_id, log=log)
                db.update_job(self.data_dir, job_id, shots="done")
                did["shots"] = "list thumbnail"
            else:
                skip("shots")
                log("framesheet already exists")

            if "audio" in needs:
                db.update_job(self.data_dir, job_id, audio="running")
                progress(None, "writing MP3 audio")
                log("dumping soundtrack")
                dumped = dump_soundtrack(video, self.data_dir, video_id, log=log)
                db.update_job(
                    self.data_dir, job_id, audio="done" if dumped else "skipped"
                )
                did["audio"] = "MP3 audio" if dumped else "no audio track"
            else:
                skip("audio")
                log("soundtrack already exists")

            db.upsert(self.data_dir, video_id)
            db.finish_job(self.data_dir, job_id, message=" · ".join(did.values()) or "already complete")
            log("done")
        except Exception as exc:
            db.finish_job(self.data_dir, job_id, error=str(exc) or type(exc).__name__)
            log(f"error: {exc}")
            traceback.print_exc()
