"""Durable optional derived-media work, isolated from the download queue."""
from __future__ import annotations

import hashlib
import json
import threading
import traceback
from pathlib import Path

from . import db
from .locks import video_lock
from .paths import parse_video_id, soundtrack_path, transcript_json_path
from .transcribe import normalize_config, transcribe_soundtrack


class DerivedWorkQueue:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._cv = threading.Condition()
        recovered = db.recover_work_jobs(data_dir)
        if recovered:
            print(f"recovered {recovered} interrupted derived job(s)", flush=True)
        self._worker = threading.Thread(
            target=self._loop, name="ytarchive-derived-work", daemon=True
        )
        self._worker.start()
        if db.has_queued_work(data_dir):
            with self._cv:
                self._cv.notify()

    def submit_transcription(
        self, video_id: str, config: dict | None = None, *, force: bool = False
    ) -> dict:
        video_id = parse_video_id(video_id)
        config = normalize_config(config)
        with video_lock(video_id):
            audio = soundtrack_path(self.data_dir, video_id)
            if not audio.is_file():
                raise ValueError("MP3 audio is missing; create it before transcribing")
            source_sha256 = _sha256(audio)
            identity = json.dumps(
                {
                    "kind": "transcribe",
                    "video_id": video_id,
                    "source_sha256": source_sha256,
                    "config": config,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            work_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            job_config = {**config, "source_sha256": source_sha256}
            job = db.enqueue_work(
                self.data_dir,
                work_key=work_key,
                video_id=video_id,
                kind="transcribe",
                config=job_config,
                force=force
                or not transcript_json_path(self.data_dir, video_id).is_file(),
            )
        with self._cv:
            self._cv.notify()
        return job

    def get(self, job_id: str) -> dict | None:
        return db.get_work_job(self.data_dir, job_id)

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not db.has_queued_work(self.data_dir):
                    self._cv.wait()
            job = db.claim_next_work(self.data_dir)
            if job:
                self._run(job)

    def _run(self, job: dict) -> None:
        with video_lock(job["video_id"]):
            self._run_locked(job)

    def _run_locked(self, job: dict) -> None:
        job_id = job["job_id"]

        def log(message: str) -> None:
            print(f"[derived {job_id}] {message}", flush=True)
            db.append_work_log(self.data_dir, job_id, str(message))

        def progress(value: float, message: str) -> None:
            db.update_work_job(
                self.data_dir,
                job_id,
                progress=value,
                message=message,
            )

        try:
            if job["kind"] != "transcribe":
                raise ValueError(f"unknown derived work kind: {job['kind']}")
            audio = soundtrack_path(self.data_dir, job["video_id"])
            expected_sha = job["config"].get("source_sha256") or ""
            if not audio.is_file() or _sha256(audio) != expected_sha:
                raise RuntimeError(
                    "MP3 audio changed after this job was queued; start transcription again"
                )
            transcribe_soundtrack(
                self.data_dir,
                job["video_id"],
                job["config"],
                log=log,
                progress=progress,
                work_key=job["work_key"],
                source_sha256=expected_sha,
                generation_id=f"{job_id}:{job['attempts']}",
            )
            db.update_work_job(
                self.data_dir,
                job_id,
                status="done",
                progress=1,
                message="transcript ready",
                error="",
            )
        except Exception as exc:
            db.update_work_job(
                self.data_dir,
                job_id,
                status="error",
                message="transcription failed",
                error=str(exc),
            )
            log(f"error: {exc}")
            traceback.print_exc()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
