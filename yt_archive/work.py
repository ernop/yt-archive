"""Durable optional derived-media work, isolated from the download queue."""
from __future__ import annotations

import hashlib
import json
import threading
import traceback
from pathlib import Path

from . import db, gpu_gate, llm
from .english import (
    build_english_in_child,
    kokoro_model_paths,
    normalize_english_job_config,
)
from .locks import video_lock
from .paths import (
    english_has_dub,
    english_is_complete,
    find_video_file,
    parse_video_id,
    soundtrack_path,
    transcript_json_path,
)
from .settings import load_settings
from .transcribe import normalize_config, transcribe_in_child


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

    def submit_english(
        self,
        video_id: str,
        config: dict | None = None,
        *,
        force: bool = False,
        lane: str = "now",
    ) -> dict:
        video_id = parse_video_id(video_id)
        job_cfg = normalize_english_job_config(config)
        en_cfg = load_settings()["english"]
        llm.check_configured(en_cfg)
        if job_cfg["include_dub"]:
            kokoro_model_paths(en_cfg)
        with video_lock(video_id):
            if not find_video_file(self.data_dir, video_id):
                raise ValueError("archived video is missing")
            audio = soundtrack_path(self.data_dir, video_id)
            if not audio.is_file():
                raise ValueError("MP3 audio is missing; create it before building an English version")
            source_sha256 = _sha256(audio)
            identity = json.dumps(
                {
                    "kind": "english",
                    "video_id": video_id,
                    "source_sha256": source_sha256,
                    "config": job_cfg,
                    "provider": en_cfg["provider"],
                    "model": en_cfg["model"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            work_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            stored_cfg = {
                **job_cfg,
                "source_sha256": source_sha256,
                "lane": "backfill" if lane == "backfill" else "now",
            }
            missing_output = not english_is_complete(self.data_dir, video_id) or (
                job_cfg["include_dub"] and not english_has_dub(self.data_dir, video_id)
            )
            job = db.enqueue_work(
                self.data_dir,
                work_key=work_key,
                video_id=video_id,
                kind="english",
                config=stored_cfg,
                force=force or missing_output,
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
            if job["kind"] not in ("transcribe", "english"):
                raise ValueError(f"unknown derived work kind: {job['kind']}")
            audio = soundtrack_path(self.data_dir, job["video_id"])
            expected_sha = job["config"].get("source_sha256") or ""
            if not audio.is_file() or _sha256(audio) != expected_sha:
                raise RuntimeError(
                    "MP3 audio changed after this job was queued; start the job again"
                )
            if job["kind"] == "transcribe":
                transcribe_in_child(
                    self.data_dir,
                    job["video_id"],
                    job["config"],
                    log=log,
                    progress=progress,
                    work_key=job["work_key"],
                    source_sha256=expected_sha,
                    generation_id=f"{job_id}:{job['attempts']}",
                )
                done_msg = "transcript ready"
            else:
                gpu = (
                    gpu_gate.ENGLISH_BY_HAND
                    if job["config"].get("lane") == "backfill"
                    else gpu_gate.ENGLISH_CLICKED
                )
                build_english_in_child(
                    self.data_dir,
                    job["video_id"],
                    job["config"],
                    log=log,
                    progress=progress,
                    work_key=job["work_key"],
                    source_sha256=expected_sha,
                    generation_id=f"{job_id}:{job['attempts']}",
                    gpu=gpu,
                )
                done_msg = "English version ready"
            db.update_work_job(
                self.data_dir,
                job_id,
                status="done",
                progress=1,
                message=done_msg,
                error="",
            )
        except Exception as exc:
            fail_msg = "English build failed" if job["kind"] == "english" else "transcription failed"
            db.update_work_job(
                self.data_dir,
                job_id,
                status="error",
                message=fail_msg,
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
