"""The SQLite file: its schema, migrations and connections."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


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
  disk_bytes INTEGER,
  downloaded_at TEXT NOT NULL DEFAULT '',
  shots_detected INTEGER,
  shots_kept INTEGER,
  has_video INTEGER NOT NULL DEFAULT 0,
  has_framesheet INTEGER NOT NULL DEFAULT 0,
  has_soundtrack INTEGER NOT NULL DEFAULT 0,
  has_transcript INTEGER NOT NULL DEFAULT 0,
  needs TEXT NOT NULL DEFAULT '',
  added_via TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS videos_by_time ON videos(downloaded_at DESC);

CREATE TABLE IF NOT EXISTS video_adds (
  video_id TEXT PRIMARY KEY,
  via TEXT NOT NULL,
  added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_lyrics (
  video_id TEXT PRIMARY KEY,
  lrc TEXT NOT NULL,
  received_at TEXT NOT NULL
);

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
  title TEXT NOT NULL DEFAULT '',
  progress REAL,
  message TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT ''
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

CREATE TABLE IF NOT EXISTS youtube_listings (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT '',
  channel_id TEXT NOT NULL DEFAULT '',
  items TEXT NOT NULL DEFAULT '[]',
  warning TEXT NOT NULL DEFAULT '',
  requests INTEGER NOT NULL DEFAULT 0,
  account INTEGER NOT NULL DEFAULT 0,
  fetched_at TEXT NOT NULL,
  used_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS youtube_listings_by_channel ON youtube_listings(kind, channel_id);

CREATE TABLE IF NOT EXISTS youtube_requests (
  kind TEXT NOT NULL,
  at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS youtube_requests_by_time ON youtube_requests(kind, at);

CREATE TABLE IF NOT EXISTS x_hidden_tweets (
  video_id TEXT PRIMARY KEY,
  noted_at TEXT NOT NULL
);
"""


def db_path(data_dir: Path) -> Path:
    return Path(data_dir) / DB_NAME


_ready: set[Path] = set()
_ready_lock = threading.Lock()


def connect(data_dir: Path) -> sqlite3.Connection:
    path = db_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = path.resolve() not in _ready or not path.exists()
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    if fresh:
        with _ready_lock:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            _migrate(conn)
            _ready.add(path.resolve())
    return conn


_MIGRATIONS = {
    "videos": (
        ("has_soundtrack", "INTEGER NOT NULL DEFAULT 0"),
        ("has_transcript", "INTEGER NOT NULL DEFAULT 0"),
        ("needs", "TEXT NOT NULL DEFAULT ''"),
        ("added_via", "TEXT NOT NULL DEFAULT ''"),
        ("disk_bytes", "INTEGER"),
    ),
    "jobs": (
        ("audio", "TEXT NOT NULL DEFAULT 'pending'"),
        ("title", "TEXT NOT NULL DEFAULT ''"),
        ("progress", "REAL"),
        ("message", "TEXT NOT NULL DEFAULT ''"),
        ("started_at", "TEXT NOT NULL DEFAULT ''"),
        ("finished_at", "TEXT NOT NULL DEFAULT ''"),
    ),
    "youtube_listings": (
        ("account", "INTEGER NOT NULL DEFAULT 0"),
    ),
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _MIGRATIONS.items():
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
