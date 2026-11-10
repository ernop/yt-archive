"""How each video was first added (the Added by column), and lyrics waiting for their MP3."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..paths import archive_json_path

from .core import _now, connect


# How a video was first asked for, and its label in the archive list (docs/browsing.md, "Added by").
ADD_ROUTES = {
    "played": "Played",
    "bookmarklet": "Bookmarklet",
    "pasted": "Pasted",
    "picker": "Picker",
    "matthoom": "Matthoom",
    "cli": "Command line",
}


def check_add_route(via: str) -> str:
    if not isinstance(via, str) or via not in ADD_ROUTES:
        raise ValueError(f"via must be one of: {', '.join(ADD_ROUTES)}")
    return via


def note_first_add(data_dir: Path, video_id: str, via: str) -> None:
    """Keep the route of the first request for a video; later requests leave it alone."""
    check_add_route(via)
    # Archived before routes were kept: its first request is unknown, and this one is not it.
    if archive_json_path(data_dir, video_id).exists():
        return
    conn = connect(data_dir)
    try:
        _note_first_add_conn(conn, video_id, via)
        conn.commit()
    finally:
        conn.close()


def _note_first_add_conn(conn: sqlite3.Connection, video_id: str, via: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO video_adds (video_id, via, added_at) VALUES (?, ?, ?)",
        (video_id, via, _now()),
    )


def first_add(data_dir: Path, video_id: str) -> dict:
    """The archive.json fields for the first request, or {} when none was kept."""
    conn = connect(data_dir)
    try:
        row = conn.execute(
            "SELECT via, added_at FROM video_adds WHERE video_id=?", (video_id,)
        ).fetchone()
    finally:
        conn.close()
    return {"added_via": row["via"], "added_at": row["added_at"]} if row else {}


def save_pending_lyrics(data_dir: Path, video_id: str, lrc: str) -> None:
    conn = connect(data_dir)
    try:
        conn.execute(
            "INSERT INTO pending_lyrics (video_id, lrc, received_at) VALUES (?, ?, ?) "
            "ON CONFLICT(video_id) DO UPDATE SET lrc=excluded.lrc, received_at=excluded.received_at",
            (video_id, lrc, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def pending_lyrics(data_dir: Path, video_id: str) -> str | None:
    conn = connect(data_dir)
    try:
        row = conn.execute("SELECT lrc FROM pending_lyrics WHERE video_id=?", (video_id,)).fetchone()
    finally:
        conn.close()
    return row["lrc"] if row else None


def drop_pending_lyrics(data_dir: Path, video_id: str) -> None:
    conn = connect(data_dir)
    try:
        conn.execute("DELETE FROM pending_lyrics WHERE video_id=?", (video_id,))
        conn.commit()
    finally:
        conn.close()
