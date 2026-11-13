"""The video catalog: rows rebuilt from each archive.json, listing, search and sorting."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..paths import (
    card_thumb_path,
    folder_disk_bytes,
    list_archived_ids,
    load_archive_info,
    media_url,
    transcript_is_complete,
    transcript_json_path,
)

from .core import connect, db_path
from .transcripts import _replace_transcript_conn


VIDEO_SORT_ORDERS = {
    "recent": "downloaded_at DESC, video_id COLLATE NOCASE ASC",
    "oldest": "downloaded_at ASC, video_id COLLATE NOCASE ASC",
    "uploaded": "upload_date DESC, title COLLATE NOCASE ASC, video_id COLLATE NOCASE ASC",
    "title": "title COLLATE NOCASE ASC, video_id COLLATE NOCASE ASC",
    "channel": "channel COLLATE NOCASE ASC, title COLLATE NOCASE ASC, video_id COLLATE NOCASE ASC",
}


def _row_from_info(info: dict, disk_bytes: int | None = None) -> dict:
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
        "disk_bytes": disk_bytes,
        "downloaded_at": info.get("downloaded_at") or "",
        "shots_detected": info.get("shots_detected"),
        "shots_kept": shots or 0,
        "has_video": 1 if info.get("has_video") else 0,
        "has_framesheet": 1 if info.get("has_framesheet") else 0,
        "has_soundtrack": 1 if info.get("has_soundtrack") else 0,
        "has_transcript": 1 if info.get("has_transcript") else 0,
        "needs": ",".join(info.get("needs") or []),
        "added_via": info.get("added_via") or "",
    }


_VIDEO_COLUMNS = (
    "video_id", "title", "channel", "channel_id", "duration", "upload_date",
    "description", "video_file", "file_size", "disk_bytes", "downloaded_at", "shots_detected",
    "shots_kept", "has_video", "has_framesheet", "has_soundtrack", "has_transcript", "needs",
    "added_via",
)
_UPSERT_VIDEO = (
    f"INSERT INTO videos ({', '.join(_VIDEO_COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in _VIDEO_COLUMNS)}) "
    "ON CONFLICT(video_id) DO UPDATE SET "
    + ", ".join(f"{c}=excluded.{c}" for c in _VIDEO_COLUMNS[1:])
)


def upsert(data_dir: Path, video_id: str | None = None, info: dict | None = None) -> None:
    if info is None:
        if not video_id:
            raise ValueError("upsert needs video_id or info")
        info = load_archive_info(data_dir, video_id)
    row = _row_from_info(info, folder_disk_bytes(data_dir, info["video_id"]))
    conn = connect(data_dir)
    try:
        conn.execute(_UPSERT_VIDEO, row)
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
            conn.execute(
                _UPSERT_VIDEO,
                _row_from_info(
                    load_archive_info(data_dir, video_id), folder_disk_bytes(data_dir, video_id)
                ),
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
        thumb = card_thumb_path(data_dir, item["video_id"])
        if thumb:
            item["thumb_url"] = media_url(data_dir, thumb)
    return items


def count_videos(data_dir: Path) -> int:
    if not db_path(data_dir).is_file():
        rebuild(data_dir)
    conn = connect(data_dir)
    try:
        return conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    finally:
        conn.close()


def archive_disk_bytes(data_dir: Path) -> int:
    """Disk space every archived video's folder takes. A row from before disk_bytes is
    measured once here; after that each catalog refresh of the video keeps it current."""
    if not db_path(data_dir).is_file():
        rebuild(data_dir)
    conn = connect(data_dir)
    try:
        unmeasured = [
            row[0] for row in conn.execute("SELECT video_id FROM videos WHERE disk_bytes IS NULL")
        ]
        for video_id in unmeasured:
            conn.execute(
                "UPDATE videos SET disk_bytes=? WHERE video_id=?",
                (folder_disk_bytes(data_dir, video_id), video_id),
            )
        if unmeasured:
            conn.commit()
        return conn.execute("SELECT COALESCE(SUM(disk_bytes), 0) FROM videos").fetchone()[0]
    finally:
        conn.close()


def archive_needs_summary(data_dir: Path) -> dict:
    """How many archived videos a plain job would still change, by kind of work."""
    if not db_path(data_dir).is_file():
        rebuild(data_dir)
    conn = connect(data_dir)
    try:
        rows = conn.execute(
            "SELECT video_id, needs FROM videos WHERE has_video=1 AND needs != ''"
        ).fetchall()
    finally:
        conn.close()
    counts: dict[str, int] = {}
    for row in rows:
        for need in row["needs"].split(","):
            counts[need] = counts.get(need, 0) + 1
    return {"videos": [row["video_id"] for row in rows], "counts": counts}


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
        "needs": [need for need in (row["needs"] or "").split(",") if need],
        "added_via": row["added_via"] or "",
    }
