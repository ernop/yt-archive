"""The transcript queue's durable work rows."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from ..paths import transcript_json_path, transcript_vtt_path

from .core import _now, connect
from .transcripts import _replace_transcript_conn


def _work_public(row: sqlite3.Row) -> dict:
    try:
        config = json.loads(row["config"] or "{}")
    except json.JSONDecodeError:
        config = {}
    try:
        log = json.loads(row["log"] or "[]")
    except json.JSONDecodeError:
        log = []
    return {
        "job_id": row["id"],
        "work_key": row["work_key"],
        "video_id": row["video_id"],
        "kind": row["kind"],
        "status": row["status"],
        "config": config,
        "progress": float(row["progress"] or 0),
        "message": row["message"] or "",
        "error": row["error"] or "",
        "log": log,
        "attempts": int(row["attempts"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def enqueue_work(
    data_dir: Path,
    *,
    work_key: str,
    video_id: str,
    kind: str,
    config: dict,
    force: bool = False,
) -> dict:
    """Insert once by durable work key, or requeue the same row for retry/replace."""
    now = _now()
    conn = connect(data_dir)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM work_jobs WHERE work_key=?", (work_key,)
        ).fetchone()
        if row:
            if row["status"] in ("queued", "running"):
                conn.commit()
            elif row["status"] == "error" or force:
                conn.execute(
                    """
                    UPDATE work_jobs SET
                      status='queued', progress=0, message='queued',
                      error='', log='[]', updated_at=?
                    WHERE id=?
                    """,
                    (now, row["id"]),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM work_jobs WHERE id=?", (row["id"],)
                ).fetchone()
            else:
                conn.commit()
            return _work_public(row)
        job_id = uuid.uuid4().hex[:12]
        conn.execute(
            """
            INSERT INTO work_jobs (
              id, work_key, video_id, kind, status, config, progress,
              message, error, log, attempts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'queued', ?, 0, 'queued', '', '[]', 0, ?, ?)
            """,
            (
                job_id,
                work_key,
                video_id,
                kind,
                json.dumps(config, sort_keys=True),
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM work_jobs WHERE id=?", (job_id,)).fetchone()
        return _work_public(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_work_job(data_dir: Path, job_id: str) -> dict | None:
    conn = connect(data_dir)
    try:
        row = conn.execute(
            "SELECT * FROM work_jobs WHERE id=?", (job_id,)
        ).fetchone()
        return _work_public(row) if row else None
    finally:
        conn.close()


def claim_next_work(data_dir: Path) -> dict | None:
    conn = connect(data_dir)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM work_jobs
            WHERE status='queued' ORDER BY created_at ASC LIMIT 1
            """
        ).fetchone()
        if not row:
            conn.commit()
            return None
        changed = conn.execute(
            """
            UPDATE work_jobs SET
              status='running', attempts=attempts+1, message='starting',
              updated_at=?
            WHERE id=? AND status='queued'
            """,
            (_now(), row["id"]),
        ).rowcount
        conn.commit()
        if not changed:
            return None
        row = conn.execute(
            "SELECT * FROM work_jobs WHERE id=?", (row["id"],)
        ).fetchone()
        return _work_public(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_work_job(data_dir: Path, job_id: str, **fields) -> dict | None:
    allowed = {"status", "progress", "message", "error"}
    sets = []
    params = []
    for key, value in fields.items():
        if key not in allowed:
            raise KeyError(key)
        if key == "progress":
            value = max(0.0, min(1.0, float(value)))
        sets.append(f"{key}=?")
        params.append(value)
    if not sets:
        return get_work_job(data_dir, job_id)
    sets.append("updated_at=?")
    params.extend([_now(), job_id])
    conn = connect(data_dir)
    try:
        conn.execute(f"UPDATE work_jobs SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    finally:
        conn.close()
    return get_work_job(data_dir, job_id)


def append_work_log(data_dir: Path, job_id: str, line: str) -> None:
    conn = connect(data_dir)
    try:
        row = conn.execute(
            "SELECT log FROM work_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not row:
            return
        try:
            log = json.loads(row["log"] or "[]")
        except json.JSONDecodeError:
            log = []
        log.append(str(line))
        conn.execute(
            "UPDATE work_jobs SET log=?, message=?, updated_at=? WHERE id=?",
            (json.dumps(log[-100:]), str(line), _now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def has_queued_work(data_dir: Path) -> bool:
    conn = connect(data_dir)
    try:
        return (
            conn.execute(
                "SELECT COUNT(*) FROM work_jobs WHERE status='queued'"
            ).fetchone()[0]
            > 0
        )
    finally:
        conn.close()


def recover_work_jobs(data_dir: Path) -> int:
    """Finish published work after a crash; otherwise safely retry the same row."""
    conn = connect(data_dir)
    try:
        rows = conn.execute(
            "SELECT * FROM work_jobs WHERE status='running'"
        ).fetchall()
        for row in rows:
            published = False
            path = transcript_json_path(data_dir, row["video_id"])
            vtt = transcript_vtt_path(data_dir, row["video_id"])
            if row["kind"] == "transcribe" and path.is_file() and vtt.is_file():
                try:
                    artifact = json.loads(path.read_text(encoding="utf-8"))
                    vtt_sha256 = hashlib.sha256(vtt.read_bytes()).hexdigest()
                    expected_generation = f"{row['id']}:{row['attempts']}"
                    if (
                        artifact.get("work_key") == row["work_key"]
                        and artifact.get("generation_id") == expected_generation
                        and artifact.get("vtt_sha256") == vtt_sha256
                    ):
                        _replace_transcript_conn(conn, artifact)
                        conn.execute(
                            "UPDATE videos SET has_transcript=1 WHERE video_id=?",
                            (row["video_id"],),
                        )
                        published = True
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    published = False
            conn.execute(
                """
                UPDATE work_jobs SET
                  status=?, progress=?, message=?, updated_at=?
                WHERE id=?
                """,
                (
                    "done" if published else "queued",
                    1 if published else 0,
                    "recovered completed transcript"
                    if published
                    else "recovered after service restart",
                    _now(),
                    row["id"],
                ),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()
