"""Lyrics a local client sends for a video, kept beside its MP3 (docs/browsing.md, "Lyrics")."""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from . import db
from .paths import find_video_file, lyrics_path

MAX_LYRICS_BYTES = 256 * 1024

# The web server and the job worker both place lyrics; one at a time keeps the newest text.
_lock = threading.Lock()


def store_lyrics(data_dir: Path, video_id: str, lrc: str | None) -> str:
    """Saves (or, given None, removes) a video's lyrics. A video not on disk yet keeps them
    in the catalog until its job finishes. Returns "saved", "pending" or "removed"."""
    with _lock:
        if lrc is None:
            db.drop_pending_lyrics(data_dir, video_id)
            lyrics_path(data_dir, video_id).unlink(missing_ok=True)
            return "removed"
        if find_video_file(data_dir, video_id) is None:
            db.save_pending_lyrics(data_dir, video_id, lrc)
            return "pending"
        _write(lyrics_path(data_dir, video_id), lrc)
        db.drop_pending_lyrics(data_dir, video_id)
        return "saved"


def place_pending_lyrics(data_dir: Path, video_id: str) -> bool:
    with _lock:
        lrc = db.pending_lyrics(data_dir, video_id)
        if lrc is None or find_video_file(data_dir, video_id) is None:
            return False
        _write(lyrics_path(data_dir, video_id), lrc)
        db.drop_pending_lyrics(data_dir, video_id)
        return True


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
