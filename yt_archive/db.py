"""Tiny SQLite catalog + durable job queue. Files stay the source of truth."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .paths import (
    find_video_file,
    framesheet_paths,
    list_archived_ids,
    load_archive_info,
    media_url,
    transcript_is_complete,
    transcript_json_path,
    transcript_vtt_path,
    watch_url,
)

DB_NAME = "ytarchive.sqlite"

VIDEO_SORT_ORDERS = {
    "recent": "downloaded_at DESC, video_id COLLATE NOCASE ASC",
    "oldest": "downloaded_at ASC, video_id COLLATE NOCASE ASC",
    "uploaded": "upload_date DESC, title COLLATE NOCASE ASC, video_id COLLATE NOCASE ASC",
    "title": "title COLLATE NOCASE ASC, video_id COLLATE NOCASE ASC",
    "channel": "channel COLLATE NOCASE ASC, title COLLATE NOCASE ASC, video_id COLLATE NOCASE ASC",
}

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
  has_framesheet INTEGER NOT NULL DEFAULT 0,
  has_soundtrack INTEGER NOT NULL DEFAULT 0,
  has_transcript INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS videos_by_time ON videos(downloaded_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  video_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  download TEXT NOT NULL DEFAULT 'pending',
  shots TEXT NOT NULL DEFAULT 'pending',
  audio TEXT NOT NULL DEFAULT 'pending',
  force_video INTEGER NOT NULL DEFAULT 0,
  force_shots INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  log TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_by_status ON jobs(status, created_at);

CREATE TABLE IF NOT EXISTS work_jobs (
  id TEXT PRIMARY KEY,
  work_key TEXT NOT NULL UNIQUE,
  video_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  config TEXT NOT NULL DEFAULT '{}',
  progress REAL NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  log TEXT NOT NULL DEFAULT '[]',
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS work_jobs_by_status
  ON work_jobs(status, created_at);

CREATE TABLE IF NOT EXISTS transcripts (
  video_id TEXT PRIMARY KEY,
  engine TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  language TEXT NOT NULL DEFAULT '',
  language_probability REAL NOT NULL DEFAULT 0,
  duration REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT '',
  full_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS transcript_segments (
  video_id TEXT NOT NULL,
  segment_index INTEGER NOT NULL,
  start REAL NOT NULL,
  end REAL NOT NULL,
  text TEXT NOT NULL,
  speaker TEXT NOT NULL DEFAULT '',
  words_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY(video_id, segment_index)
);
CREATE INDEX IF NOT EXISTS transcript_segments_by_video_time
  ON transcript_segments(video_id, start);
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
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    vcols = {row[1] for row in conn.execute("PRAGMA table_info(videos)")}
    if "has_soundtrack" not in vcols:
        conn.execute(
            "ALTER TABLE videos ADD COLUMN has_soundtrack INTEGER NOT NULL DEFAULT 0"
        )
    if "has_transcript" not in vcols:
        conn.execute(
            "ALTER TABLE videos ADD COLUMN has_transcript INTEGER NOT NULL DEFAULT 0"
        )
    jcols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "audio" not in jcols:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN audio TEXT NOT NULL DEFAULT 'pending'"
        )
    conn.commit()


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
        "has_soundtrack": 1 if info.get("has_soundtrack") else 0,
        "has_transcript": 1 if info.get("has_transcript") else 0,
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
              shots_detected, shots_kept, has_video, has_framesheet, has_soundtrack,
              has_transcript
            ) VALUES (
              :video_id, :title, :channel, :channel_id, :duration, :upload_date,
              :description, :video_file, :file_size, :downloaded_at,
              :shots_detected, :shots_kept, :has_video, :has_framesheet,
              :has_soundtrack, :has_transcript
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
              has_framesheet=excluded.has_framesheet,
              has_soundtrack=excluded.has_soundtrack,
              has_transcript=excluded.has_transcript
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
        conn.execute("DELETE FROM transcripts")
        conn.execute("DELETE FROM transcript_segments")
        for video_id in ids:
            row = _row_from_info(load_archive_info(data_dir, video_id))
            conn.execute(
                """
                INSERT INTO videos (
                  video_id, title, channel, channel_id, duration, upload_date,
                  description, video_file, file_size, downloaded_at,
                  shots_detected, shots_kept, has_video, has_framesheet, has_soundtrack,
                  has_transcript
                ) VALUES (
                  :video_id, :title, :channel, :channel_id, :duration, :upload_date,
                  :description, :video_file, :file_size, :downloaded_at,
                  :shots_detected, :shots_kept, :has_video, :has_framesheet,
                  :has_soundtrack, :has_transcript
                )
                """,
                row,
            )
            transcript_path = transcript_json_path(data_dir, video_id)
            if transcript_is_complete(data_dir, video_id):
                try:
                    artifact = json.loads(transcript_path.read_text(encoding="utf-8"))
                    _replace_transcript_conn(conn, artifact)
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    pass
        conn.commit()
        return len(ids)
    finally:
        conn.close()


def normalize_video_sort(sort: str = "") -> str:
    return sort if sort in VIDEO_SORT_ORDERS else "recent"


def list_videos(
    data_dir: Path, query: str = "", sort: str = "recent"
) -> list[dict]:
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
                    """(
                      title LIKE ? OR channel LIKE ? OR video_id LIKE ? OR description LIKE ?
                      OR EXISTS (
                        SELECT 1 FROM transcript_segments ts
                        WHERE ts.video_id=videos.video_id
                          AND (ts.text LIKE ? OR ts.speaker LIKE ?)
                      )
                    )"""
                )
                needle = f"%{word}%"
                params.extend([needle, needle, needle, needle, needle, needle])
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {VIDEO_SORT_ORDERS[normalize_video_sort(sort)]}"
        rows = conn.execute(sql, params).fetchall()
        return _decorate_video_rows(data_dir, rows)
    finally:
        conn.close()


def list_creator_videos(
    data_dir: Path,
    *,
    channel_id: str = "",
    channel: str = "",
    query: str = "",
    sort: str = "recent",
) -> list[dict]:
    """List one uploader's videos, preferring stable channel id identity."""
    if not channel_id and not channel:
        return []
    if not db_path(data_dir).is_file():
        rebuild(data_dir)
    conn = connect(data_dir)
    try:
        clauses = ["channel_id=?" if channel_id else "channel=?"]
        params: list[str] = [channel_id or channel]
        for word in [word for word in (query or "").split() if word]:
            clauses.append(
                """(
                  title LIKE ? OR video_id LIKE ? OR description LIKE ?
                  OR EXISTS (
                    SELECT 1 FROM transcript_segments ts
                    WHERE ts.video_id=videos.video_id
                      AND (ts.text LIKE ? OR ts.speaker LIKE ?)
                  )
                )"""
            )
            needle = f"%{word}%"
            params.extend([needle, needle, needle, needle, needle])
        sql = "SELECT * FROM videos WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {VIDEO_SORT_ORDERS[normalize_video_sort(sort)]}"
        rows = conn.execute(sql, params).fetchall()
        return _decorate_video_rows(data_dir, rows)
    finally:
        conn.close()


def _decorate_video_rows(data_dir: Path, rows) -> list[dict]:
    items = [_public(row) for row in rows]
    for item in items:
        if item["has_framesheet"]:
            sheets = framesheet_paths(data_dir, item["video_id"])
            if sheets:
                item["thumb_url"] = media_url(data_dir, sheets[0])
    return items


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
        "has_soundtrack": bool(row["has_soundtrack"]),
        "has_transcript": bool(row["has_transcript"]),
    }


def replace_transcript(data_dir: Path, artifact: dict) -> None:
    conn = connect(data_dir)
    try:
        _replace_transcript_conn(conn, artifact)
        conn.execute(
            "UPDATE videos SET has_transcript=1 WHERE video_id=?",
            (artifact["video_id"],),
        )
        conn.commit()
    finally:
        conn.close()


def delete_transcript(data_dir: Path, video_id: str) -> None:
    conn = connect(data_dir)
    try:
        conn.execute("DELETE FROM transcript_segments WHERE video_id=?", (video_id,))
        conn.execute("DELETE FROM transcripts WHERE video_id=?", (video_id,))
        conn.execute(
            "UPDATE videos SET has_transcript=0 WHERE video_id=?", (video_id,)
        )
        conn.commit()
    finally:
        conn.close()


def _replace_transcript_conn(conn: sqlite3.Connection, artifact: dict) -> None:
    video_id = artifact["video_id"]
    conn.execute(
        """
        INSERT INTO transcripts (
          video_id, engine, model, language, language_probability,
          duration, created_at, full_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
          engine=excluded.engine,
          model=excluded.model,
          language=excluded.language,
          language_probability=excluded.language_probability,
          duration=excluded.duration,
          created_at=excluded.created_at,
          full_text=excluded.full_text
        """,
        (
            video_id,
            artifact.get("engine") or "",
            artifact.get("model") or "",
            artifact.get("language") or "",
            float(artifact.get("language_probability") or 0),
            float(artifact.get("duration") or 0),
            artifact.get("created_at") or "",
            artifact.get("full_text") or "",
        ),
    )
    conn.execute("DELETE FROM transcript_segments WHERE video_id=?", (video_id,))
    for index, segment in enumerate(artifact.get("segments") or []):
        conn.execute(
            """
            INSERT INTO transcript_segments (
              video_id, segment_index, start, end, text, speaker, words_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                int(segment.get("index", index)),
                float(segment.get("start") or 0),
                float(segment.get("end") or 0),
                segment.get("text") or "",
                segment.get("speaker") or "",
                json.dumps(segment.get("words") or [], ensure_ascii=False),
            ),
        )


def get_transcript(data_dir: Path, video_id: str) -> dict | None:
    conn = connect(data_dir)
    try:
        row = conn.execute(
            "SELECT * FROM transcripts WHERE video_id=?", (video_id,)
        ).fetchone()
        if not row:
            return None
        segments = conn.execute(
            """
            SELECT * FROM transcript_segments
            WHERE video_id=? ORDER BY segment_index
            """,
            (video_id,),
        ).fetchall()
        return {
            "video_id": row["video_id"],
            "engine": row["engine"],
            "model": row["model"],
            "language": row["language"],
            "language_probability": row["language_probability"],
            "duration": row["duration"],
            "created_at": row["created_at"],
            "full_text": row["full_text"],
            "segments": [
                {
                    "index": segment["segment_index"],
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": segment["text"],
                    "speaker": segment["speaker"],
                    "words": json.loads(segment["words_json"] or "[]"),
                }
                for segment in segments
            ],
        }
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_public(row: sqlite3.Row) -> dict:
    try:
        log = json.loads(row["log"] or "[]")
    except json.JSONDecodeError:
        log = [row["log"]] if row["log"] else []
    download = row["download"]
    shots = row["shots"]
    audio = row["audio"]
    status = row["status"]
    if status == "done":
        phase = "done"
    elif status == "queued":
        phase = "queued"
    elif download == "running":
        phase = "download"
    elif shots == "running":
        phase = "shots"
    elif audio == "running":
        phase = "audio"
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
        "audio": audio,
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


def enqueue_missing_videos(data_dir: Path, video_ids: list[str]) -> dict:
    """Atomically queue a creator selection, skipping saved and open videos."""
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
                   force_video, force_shots, error, log, created_at, updated_at)
                   VALUES (?, ?, ?, 'queued', 'pending', 'pending', 0, 0, '', '[]', ?, ?)""",
                (uuid.uuid4().hex[:12], watch_url(video_id), video_id, now, now),
            )
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


def defer_job(data_dir: Path, job_id: str) -> None:
    """Put a claimed job at the back while its video's source is busy."""
    now = _now()
    conn = connect(data_dir)
    try:
        conn.execute(
            """
            UPDATE jobs SET status='queued', created_at=?, updated_at=?
            WHERE id=? AND status='running'
            """,
            (now, now, job_id),
        )
        conn.commit()
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
