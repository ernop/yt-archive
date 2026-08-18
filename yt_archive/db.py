"""Tiny SQLite catalog + durable job queue. Files stay the source of truth."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .paths import framesheet_path, list_archived_ids, load_archive_info, media_url

DB_NAME = "ytarchive.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
  video_id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT '',
  channel_id TEXT NOT NULL DEFAULT '',
  duration INTEGER,
  upload_date TEXT,
  description TEXT NOT NULL DEFAULT '',
  video_file TEXT,
  file_size INTEGER,
  downloaded_at TEXT NOT NULL DEFAULT '',
  shots_detected INTEGER,
  shots_kept INTEGER,
  has_video INTEGER NOT NULL DEFAULT 0,
  has_framesheet INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS videos_by_time ON videos(downloaded_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  video_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  download TEXT NOT NULL DEFAULT 'pending',
  shots TEXT NOT NULL DEFAULT 'pending',
  force_video INTEGER NOT NULL DEFAULT 0,
  force_shots INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  log TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_by_status ON jobs(status, created_at);
"""


def db_path(data_dir: Path) -> Path:
    return Path(data_dir) / DB_NAME


def connect(data_dir: Path) -> sqlite3.Connection:
    path = db_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def _row_from_info(info: dict) -> dict:
    shots = info.get("shots_kept")
    if shots is None:
        shots = len(info.get("shot_files") or [])
    return {
        "video_id": info["video_id"],
        "title": info.get("title") or "",
        "channel": info.get("channel") or "",
        "channel_id": info.get("channel_id") or "",
        "duration": info.get("duration"),
        "upload_date": info.get("upload_date") or "",
        "description": info.get("description") or "",
        "video_file": info.get("video_file") or "",
        "file_size": info.get("file_size"),
        "downloaded_at": info.get("downloaded_at") or "",
        "shots_detected": info.get("shots_detected"),
        "shots_kept": shots or 0,
        "has_video": 1 if info.get("has_video") else 0,
        "has_framesheet": 1 if info.get("has_framesheet") else 0,
    }


def upsert(data_dir: Path, video_id: str | None = None, info: dict | None = None) -> None:
    if info is None:
        if not video_id:
            raise ValueError("upsert needs video_id or info")
        info = load_archive_info(data_dir, video_id)
    row = _row_from_info(info)
    conn = connect(data_dir)
    try:
        conn.execute(
            """
            INSERT INTO videos (
              video_id, title, channel, channel_id, duration, upload_date,
              description, video_file, file_size, downloaded_at,
              shots_detected, shots_kept, has_video, has_framesheet
            ) VALUES (
              :video_id, :title, :channel, :channel_id, :duration, :upload_date,
              :description, :video_file, :file_size, :downloaded_at,
              :shots_detected, :shots_kept, :has_video, :has_framesheet
            )
            ON CONFLICT(video_id) DO UPDATE SET
              title=excluded.title,
              channel=excluded.channel,
              channel_id=excluded.channel_id,
              duration=excluded.duration,
              upload_date=excluded.upload_date,
              description=excluded.description,
              video_file=excluded.video_file,
              file_size=excluded.file_size,
              downloaded_at=excluded.downloaded_at,
              shots_detected=excluded.shots_detected,
              shots_kept=excluded.shots_kept,
              has_video=excluded.has_video,
              has_framesheet=excluded.has_framesheet
            """,
            row,
        )
        conn.commit()
    finally:
        conn.close()


def rebuild(data_dir: Path) -> int:
    ids = list_archived_ids(data_dir)
    conn = connect(data_dir)
    try:
        conn.execute("DELETE FROM videos")
        for video_id in ids:
            row = _row_from_info(load_archive_info(data_dir, video_id))
            conn.execute(
                """
                INSERT INTO videos (
                  video_id, title, channel, channel_id, duration, upload_date,
                  description, video_file, file_size, downloaded_at,
                  shots_detected, shots_kept, has_video, has_framesheet
                ) VALUES (
                  :video_id, :title, :channel, :channel_id, :duration, :upload_date,
                  :description, :video_file, :file_size, :downloaded_at,
                  :shots_detected, :shots_kept, :has_video, :has_framesheet
                )
                """,
                row,
            )
        conn.commit()
        return len(ids)
    finally:
        conn.close()


def list_videos(data_dir: Path, query: str = "") -> list[dict]:
    if not db_path(data_dir).is_file():
        rebuild(data_dir)
    conn = connect(data_dir)
    try:
        sql = "SELECT * FROM videos"
        params: list[str] = []
        words = [w for w in (query or "").split() if w]
        if words:
            clauses = []
            for word in words:
                clauses.append(
                    "(title LIKE ? OR channel LIKE ? OR video_id LIKE ? OR description LIKE ?)"
                )
                needle = f"%{word}%"
                params.extend([needle, needle, needle, needle])
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY downloaded_at DESC"
        rows = conn.execute(sql, params).fetchall()
        items = [_public(r) for r in rows]
        for it in items:
            if it["has_framesheet"]:
                sheet = framesheet_path(data_dir, it["video_id"])
                if sheet.is_file():
                    it["thumb_url"] = media_url(data_dir, sheet)
        return items
    finally:
        conn.close()


def _public(row: sqlite3.Row) -> dict:
    return {
        "video_id": row["video_id"],
        "title": row["title"],
        "channel": row["channel"],
        "channel_id": row["channel_id"],
        "duration": row["duration"],
        "upload_date": row["upload_date"],
        "description": row["description"],
        "video_file": row["video_file"],
        "file_size": row["file_size"],
        "downloaded_at": row["downloaded_at"],
        "shots_detected": row["shots_detected"],
        "shots_kept": row["shots_kept"],
        "has_video": bool(row["has_video"]),
        "has_framesheet": bool(row["has_framesheet"]),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_public(row: sqlite3.Row) -> dict:
    try:
        log = json.loads(row["log"] or "[]")
    except json.JSONDecodeError:
        log = [row["log"]] if row["log"] else []
    download = row["download"]
    shots = row["shots"]
    status = row["status"]
    if status == "done":
        phase = "done"
    elif status == "queued":
        phase = "queued"
    elif download == "running":
        phase = "download"
    elif shots == "running":
        phase = "shots"
    elif status == "error":
        phase = "error"
    else:
        phase = status
    return {
        "job_id": row["id"],
        "url": row["url"],
        "video_id": row["video_id"],
        "status": status,
        "phase": phase,
        "download": download,
        "shots": shots,
        "force_video": bool(row["force_video"]),
        "force_shots": bool(row["force_shots"]),
        "error": row["error"] or "",
        "log": log,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def insert_job(
    data_dir: Path,
    url: str,
    video_id: str,
    *,
    force_video: bool = False,
    force_shots: bool = False,
) -> dict:
    job_id = uuid.uuid4().hex[:12]
    now = _now()
    conn = connect(data_dir)
    try:
        conn.execute(
            """
            INSERT INTO jobs (
              id, url, video_id, status, download, shots,
              force_video, force_shots, error, log, created_at, updated_at
            ) VALUES (?, ?, ?, 'queued', 'pending', 'pending', ?, ?, '', '[]', ?, ?)
            """,
            (job_id, url, video_id, int(force_video), int(force_shots), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get_job(data_dir, job_id)


def get_job(data_dir: Path, job_id: str) -> dict | None:
    conn = connect(data_dir)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _job_public(row) if row else None
    finally:
        conn.close()


def update_job(data_dir: Path, job_id: str, **fields) -> dict | None:
    allowed = {
        "status", "download", "shots", "force_video", "force_shots", "error",
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
            (json.dumps(log), _now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def claim_next_job(data_dir: Path) -> dict | None:
    conn = connect(data_dir)
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE jobs SET status='running', updated_at=? WHERE id=?",
            (_now(), row["id"]),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
        return _job_public(row)
    finally:
        conn.close()


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
    conn = connect(data_dir)
    try:
        open_rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('queued', 'running')
            ORDER BY created_at ASC
            """
        ).fetchall()
        recent = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('done', 'error')
            ORDER BY updated_at DESC
            LIMIT 30
            """
        ).fetchall()
        seen = {r["id"] for r in open_rows}
        out = [_job_public(r) for r in open_rows]
        out.extend(_job_public(r) for r in recent if r["id"] not in seen)
        return out
    finally:
        conn.close()


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
    """Interrupted running jobs become queued again; redo the section that died."""
    conn = connect(data_dir)
    try:
        rows = conn.execute("SELECT * FROM jobs WHERE status='running'").fetchall()
        now = _now()
        for row in rows:
            conn.execute(
                """
                UPDATE jobs SET
                  status='queued',
                  force_shots=CASE WHEN shots='running' THEN 1 ELSE force_shots END,
                  download=CASE WHEN download='running' THEN 'pending' ELSE download END,
                  shots=CASE WHEN shots='running' THEN 'pending' ELSE shots END,
                  updated_at=?
                WHERE id=?
                """,
                (now, row["id"]),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()
