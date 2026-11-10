"""Transcripts mirrored from transcript.json for search."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .core import connect


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
