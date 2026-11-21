"""One-at-a-time archive jobs, persisted in SQLite so restarts recover."""
from __future__ import annotations

import threading
import time
import traceback

from . import db
from .audio import dump_soundtrack
from .download import (
    LiveClipSession,
    UpcomingStreamError,
    clear_archive_extras,
    download,
    format_cap,
    start_download,
)
from .framesheet import ensure_preview, make_shots, retile_sheets
from .locks import try_video_lock, video_lock
from .lyrics import place_pending_lyrics
from .paths import (
    VIDEO_ID_RE,
    archive_needs,
    find_video_file,
    framesheet_paths,
    is_tweet,
    load_archive_info,
    parse_video_id,
    skips_default_shots,
    soundtrack_path,
    transcript_json_path,
    transcript_vtt_path,
    watch_url,
)
from .settings import DEFAULT_LIVE_CLIP_MAX_SECONDS, load_settings


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
    def __init__(self, data_dir, *, live_clip_max_seconds: float | None = None):
        self.data_dir = data_dir
        self._cv = threading.Condition()
        self.live_clip_max_seconds = (
            float(live_clip_max_seconds)
            if live_clip_max_seconds is not None
            else load_settings()["live_clip_max_seconds"]
        )
        self._ensure_live_state()
        n = db.recover_jobs(data_dir)
        if n:
            print(f"recovered {n} interrupted job(s)", flush=True)
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()
        if db.has_queued_jobs(data_dir):
            with self._cv:
                self._cv.notify()

    def _ensure_live_state(self) -> None:
        """Initialise live-clip tracking even for JobQueue.__new__ test instances."""
        if not hasattr(self, "_closed"):
            self._closed = False
        if not hasattr(self, "live_clip_max_seconds"):
            self.live_clip_max_seconds = float(DEFAULT_LIVE_CLIP_MAX_SECONDS)
        if not hasattr(self, "_live_lock"):
            self._live_lock = threading.Lock()
        if not hasattr(self, "_live_sessions"):
            self._live_sessions: dict[str, tuple[str, LiveClipSession]] = {}
        if not hasattr(self, "_early_stops"):
            self._early_stops: set[str] = set()
        if not hasattr(self, "_live_threads"):
            self._live_threads: set[threading.Thread] = set()

    def submit(
        self, url: str, *, force_video: bool = False, force_shots: bool = False, via: str | None = None
    ) -> dict:
        """Queue a video. Without force, an open job is reused and an up-to-date video
        is reported as complete instead of queueing a job that would do nothing.
        `via` names how a video was asked for (db.ADD_ROUTES); a redo of an archived video
        passes none."""
        self._ensure_live_state()
        video_id = parse_video_id(url)
        if via is not None:
            db.check_add_route(via)
        with self._cv:
            result = self._submit_locked(url, video_id, force_video, force_shots)
            if via is not None:
                db.note_first_add(self.data_dir, video_id, via)
        return result

    def _submit_locked(self, url: str, video_id: str, force_video: bool, force_shots: bool) -> dict:
        if not force_video and not force_shots:
            existing = db.find_open_job(self.data_dir, video_id)
            if existing:
                with self._live_lock:
                    self._early_stops.discard(existing["job_id"])
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

    def stop_played(self, video_id: str) -> dict:
        """A player watcher says the user stopped hearing video_id (docs/download.md, "Live streams").

        Ends an active live recording cleanly via SIGINT to yt-dlp, or notes an early stop if
        an open job for video_id has not entered live recording yet. Normal videos ignore it.
        """
        self._ensure_live_state()
        with self._live_lock:
            active = self._live_sessions.get(video_id)
            if active is None:
                open_job = db.find_open_job(self.data_dir, video_id)
                if open_job is not None:
                    self._early_stops.add(open_job["job_id"])
                    return {"video_id": video_id, "status": "noted", "job_id": open_job["job_id"]}
                return {"video_id": video_id, "status": "idle"}
        job_id, session = active
        stopped = session.stop("stop signal")
        return {
            "video_id": video_id,
            "status": "stopping" if stopped else "already_stopping",
            "job_id": job_id,
        }

    def active_live_video_ids(self) -> set[str]:
        self._ensure_live_state()
        with self._live_lock:
            return set(self._live_sessions)

    def stop_all_live(self, reason: str = "stop signal") -> None:
        self._ensure_live_state()
        with self._live_lock:
            sessions = [session for _, session in self._live_sessions.values()]
        for session in sessions:
            session.stop(reason)

    def close(self, timeout: float = 5.0) -> None:
        """Stop the worker loop and any active live recordings (used by tests)."""
        self._ensure_live_state()
        self.stop_all_live("shutdown")
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        worker = getattr(self, "_worker", None)
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)
        with self._live_lock:
            threads = list(self._live_threads)
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=timeout)

    def submit_many(
        self, video_ids: list[str], titles: dict[str, str] | None = None, *, via: str
    ) -> dict:
        if any(not isinstance(vid, str) or not (VIDEO_ID_RE.fullmatch(vid) or is_tweet(vid)) for vid in video_ids):
            raise ValueError("Invalid video ID in selection.")
        with self._cv:
            result = db.enqueue_missing_videos(self.data_dir, video_ids, titles, via=via)
            self._cv.notify()
        return result

    def get(self, job_id: str) -> dict | None:
        return db.get_job(self.data_dir, job_id)

    def snapshot(self, job: dict) -> dict:
        return job

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._closed and not db.has_queued_jobs(self.data_dir):
                    self._cv.wait()
                if self._closed:
                    return
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
        self._ensure_live_state()
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

        handed_to_live = False
        try:
            mp3 = soundtrack_path(self.data_dir, video_id)
            if job["force_video"] and job["download"] != "done":
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
                    self.data_dir,
                    video_id,
                    "transcript",
                    "captions",
                    "audio_stream",
                    "live_clip",
                    "duration",
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
                result = start_download(
                    video_id,
                    self.data_dir,
                    log=log,
                    force=job["force_video"],
                    progress=progress,
                    live_clip_max_seconds=self.live_clip_max_seconds,
                )
                if isinstance(result, LiveClipSession):
                    handed_to_live = True
                    self._handoff_live_clip(job, result, progress, log)
                    return
                video = result
                db.update_job(self.data_dir, job_id, download="done")
                did["download"] = "downloaded"
            else:
                skip("download")
                log(f"already have {video.name}")

            info = load_archive_info(self.data_dir, video_id)
            needs = info["needs"]
            has_sheets = bool(framesheet_paths(self.data_dir, video_id))
            long_video = skips_default_shots(info.get("duration"))
            need_shots = job["shots"] != "done" and (
                job["force_shots"]
                or bool({"shots", "reshoot"} & set(needs))
                or ((did_download or job["force_video"]) and (not long_video or has_sheets))
            )
            if need_shots:
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
                if long_video and not has_sheets:
                    log("skipping shots by default (video is over 30m)")
                else:
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

            if place_pending_lyrics(self.data_dir, video_id):
                did["lyrics"] = "lyrics"
                log("saved the lyrics that arrived before the video")

            db.upsert(self.data_dir, video_id)
            db.finish_job(self.data_dir, job_id, message=" · ".join(did.values()) or "already complete")
            log("done")
        except UpcomingStreamError as exc:
            db.finish_job(self.data_dir, job_id, error=str(exc) or "stream has not started")
            log(f"error: {exc}")
        except Exception as exc:
            db.finish_job(self.data_dir, job_id, error=str(exc) or type(exc).__name__)
            log(f"error: {exc}")
            traceback.print_exc()
        finally:
            if not handed_to_live:
                with self._live_lock:
                    self._early_stops.discard(job_id)

    def _handoff_live_clip(self, job: dict, session: LiveClipSession, progress, log) -> None:
        """Move an active live recording onto its own thread so the main queue lane keeps going."""
        job_id = job["job_id"]
        video_id = job["video_id"]
        with self._live_lock:
            early_stopped = job_id in self._early_stops
            self._early_stops.discard(job_id)
            if not early_stopped:
                self._live_sessions[video_id] = (job_id, session)
        if early_stopped:
            session.cancel_before_recording()
            db.finish_job(
                self.data_dir,
                job_id,
                message="live stream stopped before recording started",
            )
            log("live stream stopped before recording started")
            return
        cap_label = format_cap(session.cap_seconds)
        progress(None, f"recording live stream (cap {cap_label})")
        log(f"live stream detected; recording off the main queue (cap {cap_label})")
        session.start_cap_timer()
        thread = threading.Thread(
            target=self._finish_live_recording,
            args=(job, session, log),
            name=f"ytarchive-live-{video_id}",
            daemon=True,
        )
        with self._live_lock:
            self._live_threads.add(thread)
        thread.start()

    def _finish_live_recording(self, job: dict, session: LiveClipSession, log) -> None:
        job_id = job["job_id"]
        video_id = job["video_id"]
        current = threading.current_thread()
        try:
            with video_lock(video_id):
                try:
                    video, live_clip = session.finish()
                finally:
                    with self._live_lock:
                        if self._live_sessions.get(video_id) == (job_id, session):
                            self._live_sessions.pop(video_id, None)
                if video is None:
                    db.finish_job(
                        self.data_dir,
                        job_id,
                        message="live stream stopped before recording started",
                    )
                    log("live stream stopped before recording started")
                    return
                long_video = skips_default_shots(load_archive_info(self.data_dir, video_id).get("duration"))
                waiting_for = "MP3 audio" if long_video else "shots"
                db.update_job(
                    self.data_dir,
                    job_id,
                    status="queued",
                    download="done",
                    progress=None,
                    message=f"live clip recorded ({live_clip['stopped_by']}) · waiting for {waiting_for}",
                )
            with self._cv:
                self._cv.notify()
        except Exception as exc:
            db.finish_job(self.data_dir, job_id, error=str(exc) or type(exc).__name__)
            log(f"error: {exc}")
            traceback.print_exc()
        finally:
            with self._live_lock:
                if self._live_sessions.get(video_id) == (job_id, session):
                    self._live_sessions.pop(video_id, None)
                self._live_threads.discard(current)
