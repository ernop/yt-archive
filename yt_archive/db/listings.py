"""Listings kept for the picker, and the record of requests made to YouTube and X."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .core import _now, connect


LISTINGS_KEPT = 60
_LISTING_COLUMNS = (
    "id", "url", "kind", "title", "channel", "channel_id", "items", "warning", "requests",
    "account", "fetched_at", "used_at",
)


def save_listing(data_dir: Path, listing: dict) -> None:
    """Keep a YouTube listing until it is refreshed; only the LISTINGS_KEPT most recently
    used are kept (docs/browsing.md, "Browse YouTube")."""
    now = _now()
    row = {key: listing.get(key) for key in _LISTING_COLUMNS}
    row.update(
        items=json.dumps(listing.get("items") or [], separators=(",", ":"), ensure_ascii=False),
        title=row["title"] or "", channel=row["channel"] or "", channel_id=row["channel_id"] or "",
        warning=row["warning"] or "", requests=int(row["requests"] or 0),
        account=1 if row["account"] else 0, fetched_at=row["fetched_at"] or now, used_at=now,
    )
    conn = connect(data_dir)
    try:
        conn.execute(
            f"INSERT INTO youtube_listings ({', '.join(_LISTING_COLUMNS)}) "
            f"VALUES ({', '.join(':' + c for c in _LISTING_COLUMNS)}) "
            "ON CONFLICT(id) DO UPDATE SET "
            + ", ".join(f"{c}=excluded.{c}" for c in _LISTING_COLUMNS[1:]),
            row,
        )
        conn.execute(
            "DELETE FROM youtube_listings WHERE id NOT IN "
            "(SELECT id FROM youtube_listings ORDER BY used_at DESC LIMIT ?)",
            (LISTINGS_KEPT,),
        )
        conn.commit()
    finally:
        conn.close()


def _listing_public(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    listing = dict(row)
    listing["account"] = bool(listing.get("account"))
    try:
        listing["items"] = json.loads(listing["items"] or "[]")
    except json.JSONDecodeError:
        listing["items"] = []
    return listing


def get_listing(data_dir: Path, listing_id: str) -> dict | None:
    conn = connect(data_dir)
    try:
        return _listing_public(
            conn.execute("SELECT * FROM youtube_listings WHERE id=?", (listing_id,)).fetchone()
        )
    finally:
        conn.close()


def find_channel_listing(data_dir: Path, kind: str, channel_id: str) -> dict | None:
    """A kept listing of this kind for the channel, however its link was written."""
    conn = connect(data_dir)
    try:
        return _listing_public(conn.execute(
            "SELECT * FROM youtube_listings WHERE kind=? AND channel_id=? ORDER BY used_at DESC LIMIT 1",
            (kind, channel_id),
        ).fetchone())
    finally:
        conn.close()


def touch_listing(data_dir: Path, listing_id: str) -> None:
    conn = connect(data_dir)
    try:
        conn.execute("UPDATE youtube_listings SET used_at=? WHERE id=?", (_now(), listing_id))
        conn.commit()
    finally:
        conn.close()


def take_youtube_request(data_dir: Path, kind: str, limit: int, now: float, window: float) -> bool:
    """Record one request of this kind unless `limit` were already made in the window."""
    conn = connect(data_dir)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM youtube_requests WHERE at < ?", (now - window,))
        used = conn.execute(
            "SELECT COUNT(*) FROM youtube_requests WHERE kind=? AND at >= ?", (kind, now - window)
        ).fetchone()[0]
        if used < limit:
            conn.execute("INSERT INTO youtube_requests (kind, at) VALUES (?, ?)", (kind, now))
        conn.commit()
        return used < limit
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_x_hidden(data_dir: Path, video_ids) -> None:
    """Note tweets (x-<tweet id>) X hides from readers without an account, so their downloads
    go straight to the user's X login (docs/download.md, "X tweets")."""
    now = _now()
    conn = connect(data_dir)
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO x_hidden_tweets (video_id, noted_at) VALUES (?, ?)",
            [(video_id, now) for video_id in video_ids],
        )
        conn.commit()
    finally:
        conn.close()


def x_hidden(data_dir: Path, video_id: str) -> bool:
    conn = connect(data_dir)
    try:
        return conn.execute("SELECT 1 FROM x_hidden_tweets WHERE video_id=?", (video_id,)).fetchone() is not None
    finally:
        conn.close()


def youtube_request_times(data_dir: Path, kind: str, since: float) -> list[float]:
    conn = connect(data_dir)
    try:
        return [row[0] for row in conn.execute(
            "SELECT at FROM youtube_requests WHERE kind=? AND at >= ? ORDER BY at", (kind, since)
        )]
    finally:
        conn.close()
