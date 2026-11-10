"""The download queue's durable jobs: download, shots, MP3 audio."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from ..paths import archive_json_path, find_video_file, watch_url

from .adds import _note_first_add_conn, check_add_route
from .core import _now, connect


WAITING_SHOWN = 8


def _job_public(row: sqlite3.Row, *, with_log: bool = True) -> dict:
    keys = row.keys()
    download = row["download"]
    shots = row["shots"]
    audio = row["audio"]
    status = row["status"]
    if status in ("done", "queued", "error", "dismissed"):
        phase = status
    elif download == "running":
        phase = "download"
    elif shots == "running":
        phase = "shots"
    elif audio == "running":
        phase = "audio"
    else:
        phase = status
    title = (row["video_title"] if "video_title" in keys else "") or row["title"] or ""
    job = {
        "job_id": row["id"],
        "url": row["url"],
        "video_id": row["video_id"],
        "title": title,
        "archived": bool(row["video_archived"]) if "video_archived" in keys else False,
        "needs": [n for n in ((row["video_needs"] if "video_needs" in keys else "") or "").split(",") if n],
        "status": status,
        "phase": phase,
        "download": download,
        "shots": shots,
        "audio": audio,
        "force_video": bool(row["force_video"]),
        "force_shots": bool(row["force_shots"]),
        "progress": row["progress"],
        "message": row["message"] or "",
        "error": row["error"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"] or "",
        "finished_at": row["finished_at"] or (
            row["updated_at"] if status in ("done", "error") else ""
        ),
    }
    if status == "done" and not job["message"]:
        job["message"] = _steps_summary(download, shots, audio)
    if with_log:
        try:
            job["log"] = json.loads(row["log"] or "[]")
        except json.JSONDecodeError:
            job["log"] = [row["log"]] if row["log"] else []
    return job


def _steps_summary(download: str, shots: str, audio: str) -> str:
    did = [
        label
        for state, label in ((download, "downloaded"), (shots, "shots"), (audio, "MP3 audio"))
        if state == "done"
    ]
    return " · ".join(did) if did else "already complete"


def insert_job(
    data_dir: Path,
    url: str,
    video_id: str,
    *,
    force_video: bool = False,
    force_shots: bool = False,
    title: str = "",
) -> dict:
    job_id = uuid.uuid4().hex[:12]
    now = _now()
    conn = connect(data_dir)
    try:
        conn.execute(
            """
            INSERT INTO jobs (
              id, url, video_id, status, download, shots,
              force_video, force_shots, error, log, title, created_at, updated_at
            ) VALUES (?, ?, ?, 'queued', 'pending', 'pending', ?, ?, '', '[]', ?, ?, ?)
            """,
            (job_id, url, video_id, int(force_video), int(force_shots), title, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get_job(data_dir, job_id)


def get_job(data_dir: Path, job_id: str) -> dict | None:
    conn = connect(data_dir)
    try:
        row = conn.execute(
            "SELECT j.*, v.title AS video_title, v.has_video AS video_archived, v.needs AS video_needs FROM jobs j "
            "LEFT JOIN videos v ON v.video_id=j.video_id WHERE j.id=?",
            (job_id,),
        ).fetchone()
        return _job_public(row) if row else None
    finally:
        conn.close()


def enqueue_missing_videos(
    data_dir: Path, video_ids: list[str], titles: dict[str, str] | None = None, *, via: str
) -> dict:
    """Atomically queue a creator selection, skipping saved and open videos."""
    check_add_route(via)
    titles = titles or {}
    conn = connect(data_dir)
    queued, skipped = [], []
    try:
        conn.execute("BEGIN IMMEDIATE")
        open_ids = {row[0] for row in conn.execute(
            "SELECT video_id FROM jobs WHERE status IN ('queued', 'running')"
        )}
        now = _now()
        for video_id in dict.fromkeys(video_ids):
            if video_id in open_ids or find_video_file(data_dir, video_id):
                skipped.append(video_id)
                continue
            conn.execute(
                """INSERT INTO jobs (id, url, video_id, status, download, shots,
                   force_video, force_shots, error, log, title, created_at, updated_at)
                   VALUES (?, ?, ?, 'queued', 'pending', 'pending', 0, 0, '', '[]', ?, ?, ?)""",
                (uuid.uuid4().hex[:12], watch_url(video_id), video_id,
                 str(titles.get(video_id) or "")[:500], now, now),
            )
            if not archive_json_path(data_dir, video_id).exists():
                _note_first_add_conn(conn, video_id, via)
            queued.append(video_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"queued": len(queued), "skipped": len(skipped), "video_ids": queued}


def update_job(data_dir: Path, job_id: str, **fields) -> dict | None:
    allowed = {
        "status", "download", "shots", "audio", "force_video", "force_shots", "error",
        "progress", "message", "finished_at",
    }
    sets = []
    params: list = []
    for key, val in fields.items():
        if key not in allowed:
            raise KeyError(key)
        if key in ("force_video", "force_shots"):
            val = int(bool(val))
        sets.append(f"{key}=?")
        params.append(val)
    if not sets:
        return get_job(data_dir, job_id)
    sets.append("updated_at=?")
    params.append(_now())
    params.append(job_id)
    conn = connect(data_dir)
    try:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    finally:
        conn.close()
    return get_job(data_dir, job_id)


def set_job_progress(data_dir: Path, job_id: str, progress: float | None, message: str) -> None:
    if progress is not None:
        progress = max(0.0, min(1.0, float(progress)))
    conn = connect(data_dir)
    try:
        conn.execute(
            "UPDATE jobs SET progress=?, message=?, updated_at=? WHERE id=?",
            (progress, str(message)[:300], _now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def finish_job(data_dir: Path, job_id: str, *, error: str = "", message: str = "") -> None:
    now = _now()
    conn = connect(data_dir)
    try:
        conn.execute(
            """
            UPDATE jobs SET status=?, error=?, message=?, progress=NULL,
              finished_at=?, updated_at=?
            WHERE id=?
            """,
            ("error" if error else "done", error, message, now, now, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def defer_job(data_dir: Path, job_id: str) -> None:
    """Put a claimed job at the back while its video's source is busy."""
    now = _now()
    conn = connect(data_dir)
    try:
        conn.execute(
            """
            UPDATE jobs SET status='queued', started_at='', created_at=?, updated_at=?
            WHERE id=? AND status='running'
            """,
            (now, now, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def remove_waiting_job(data_dir: Path, job_id: str) -> bool:
    conn = connect(data_dir)
    try:
        removed = conn.execute(
            "DELETE FROM jobs WHERE id=? AND status='queued'", (job_id,)
        ).rowcount
        conn.commit()
        return bool(removed)
    finally:
        conn.close()


def remove_all_waiting_jobs(data_dir: Path) -> int:
    conn = connect(data_dir)
    try:
        removed = conn.execute("DELETE FROM jobs WHERE status='queued'").rowcount
        conn.commit()
        return removed
    finally:
        conn.close()


def dismiss_failed_job(data_dir: Path, job_id: str) -> bool:
    conn = connect(data_dir)
    try:
        changed = conn.execute(
            "UPDATE jobs SET status='dismissed', updated_at=? WHERE id=? AND status='error'",
            (_now(), job_id),
        ).rowcount
        conn.commit()
        return bool(changed)
    finally:
        conn.close()


def append_job_log(data_dir: Path, job_id: str, line: str) -> None:
    conn = connect(data_dir)
    try:
        row = conn.execute("SELECT log FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return
        try:
            log = json.loads(row["log"] or "[]")
        except json.JSONDecodeError:
            log = []
        log.append(str(line))
        conn.execute(
            "UPDATE jobs SET log=?, updated_at=? WHERE id=?",
            (json.dumps(log[-200:]), _now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def claim_next_job(data_dir: Path) -> dict | None:
    conn = connect(data_dir)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        if not row:
            conn.commit()
            return None
        now = _now()
        changed = conn.execute(
            """
            UPDATE jobs SET status='running', progress=NULL, message='starting',
              started_at=?, updated_at=?
            WHERE id=? AND status='queued'
            """,
            (now, now, row["id"]),
        ).rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_job(data_dir, row["id"]) if changed else None


def find_open_job(data_dir: Path, video_id: str) -> dict | None:
    conn = connect(data_dir)
    try:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE video_id=? AND status IN ('queued', 'running')
            ORDER BY created_at ASC LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        return _job_public(row) if row else None
    finally:
        conn.close()


def list_jobs(data_dir: Path) -> list[dict]:
    """Every open (running or waiting) job, running first, then in queue order."""
    conn = connect(data_dir)
    try:
        rows = conn.execute(
            """
            SELECT j.*, v.title AS video_title, v.has_video AS video_archived, v.needs AS video_needs FROM jobs j
            LEFT JOIN videos v ON v.video_id=j.video_id
            WHERE j.status IN ('queued', 'running')
            ORDER BY j.status='queued', j.created_at ASC
            """
        ).fetchall()
        return [_job_public(row, with_log=False) for row in rows]
    finally:
        conn.close()


def open_video_ids(data_dir: Path) -> set[str]:
    conn = connect(data_dir)
    try:
        return {row[0] for row in conn.execute(
            "SELECT video_id FROM jobs WHERE status IN ('queued', 'running')"
        )}
    finally:
        conn.close()


def queue_overview(data_dir: Path, waiting_limit: int = WAITING_SHOWN) -> dict:
    """The home page's download queue: what runs, what waits, and what failed.

    Finished jobs are left out; the archive list shows their result. A failure stays
    until dismissed or until a later job for the same video exists.
    """
    conn = connect(data_dir)
    try:
        select = "SELECT j.*, v.title AS video_title, v.has_video AS video_archived, v.needs AS video_needs FROM jobs j LEFT JOIN videos v ON v.video_id=j.video_id"
        running = conn.execute(
            f"{select} WHERE j.status='running' ORDER BY j.started_at ASC"
        ).fetchall()
        waiting_count = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='queued'"
        ).fetchone()[0]
        waiting = conn.execute(
            f"{select} WHERE j.status='queued' ORDER BY j.created_at ASC LIMIT ?",
            (max(0, waiting_limit),),
        ).fetchall()
        failed = conn.execute(
            f"""{select} WHERE j.status='error' AND NOT EXISTS (
                  SELECT 1 FROM jobs k WHERE k.video_id=j.video_id AND k.created_at > j.created_at
                    AND k.status IN ('queued', 'running', 'done', 'error', 'dismissed'))
                ORDER BY j.updated_at DESC"""
        ).fetchall()
    finally:
        conn.close()
    return {
        "running": [_job_public(row, with_log=False) for row in running],
        "waiting": [_job_public(row, with_log=False) for row in waiting],
        "waiting_count": waiting_count,
        "failed": [_job_public(row, with_log=False) for row in failed],
    }


def has_queued_jobs(data_dir: Path) -> bool:
    conn = connect(data_dir)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='queued'"
        ).fetchone()[0]
        return n > 0
    finally:
        conn.close()


def recover_jobs(data_dir: Path) -> int:
    """Interrupted running jobs become queued again. The job re-reads the files, so a
    half-made framesheet or sheet split is redone without forcing a fresh CLIP pass."""
    conn = connect(data_dir)
    try:
        rows = conn.execute("SELECT * FROM jobs WHERE status='running'").fetchall()
        now = _now()
        for row in rows:
            conn.execute(
                """
                UPDATE jobs SET
                  status='queued', started_at='', progress=NULL,
                  message='restarted after the service stopped',
                  download=CASE WHEN download='running' THEN 'pending' ELSE download END,
                  shots=CASE WHEN shots='running' THEN 'pending' ELSE shots END,
                  audio=CASE WHEN audio='running' THEN 'pending' ELSE audio END,
                  updated_at=?
                WHERE id=?
                """,
                (now, row["id"]),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()
