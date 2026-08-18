"""Archive layout. One directory per YouTube id; originals stay untouched."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Search anywhere in pasted text. Covers watch/shorts/embed/live/v, youtu.be,
# music./m./www., youtube-nocookie, extra query junk (&t= &list= &si=).
_ID = r"([A-Za-z0-9_-]{11})"
URL_ID_RES = [
    re.compile(rf"youtu\.be/{_ID}"),
    re.compile(rf"youtube(?:-nocookie)?\.com/watch\?(?:[^#]*&)?v={_ID}"),
    re.compile(rf"youtube(?:-nocookie)?\.com/(?:shorts|embed|live|v)/{_ID}"),
    VIDEO_ID_RE,
]

VIDEO_EXTS = (".mkv", ".mp4", ".webm", ".mov")


def default_data_dir() -> Path:
    env = os.environ.get("YT_ARCHIVE_DATA")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent / "data"


def parse_video_id(url_or_id: str) -> str:
    text = (url_or_id or "").strip()
    if not text:
        raise ValueError("empty url")
    for pat in URL_ID_RES:
        match = pat.search(text)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
    raise ValueError(f"Not a YouTube url or 11-char id: {url_or_id!r}")


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def video_dir(data_dir: Path, video_id: str) -> Path:
    return data_dir / video_id


def condensed_dir(data_dir: Path, video_id: str) -> Path:
    return video_dir(data_dir, video_id) / "_condensed"


def shots_dir(data_dir: Path, video_id: str) -> Path:
    return condensed_dir(data_dir, video_id) / "shots"


def framesheet_path(data_dir: Path, video_id: str) -> Path:
    return condensed_dir(data_dir, video_id) / "framesheet.png"


def file_cache_key(path: Path) -> str:
    """URL cache key: changes iff the file's bytes could have changed."""
    st = path.stat()
    return f"{st.st_mtime_ns:x}-{st.st_size:x}"


def media_url(data_dir: Path, path: Path) -> str:
    """Durable media URL. Same key ⇒ same bytes (see Cache-Control immutable)."""
    rel = path.resolve().relative_to(Path(data_dir).resolve()).as_posix()
    return f"/media/{rel}?v={file_cache_key(path)}"


def shots_json_path(data_dir: Path, video_id: str) -> Path:
    return condensed_dir(data_dir, video_id) / "shots.json"


def archive_json_path(data_dir: Path, video_id: str) -> Path:
    return video_dir(data_dir, video_id) / "archive.json"


def find_video_file(data_dir: Path, video_id: str) -> Path | None:
    folder = video_dir(data_dir, video_id)
    if not folder.is_dir():
        return None
    matches = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    ]
    if not matches:
        return None
    matches.sort(key=lambda p: p.stat().st_size, reverse=True)
    return matches[0]


def list_archived_ids(data_dir: Path) -> list[str]:
    if not data_dir.is_dir():
        return []
    return sorted(
        p.name for p in data_dir.iterdir()
        if p.is_dir() and VIDEO_ID_RE.fullmatch(p.name)
    )


def load_archive_info(data_dir: Path, video_id: str) -> dict:
    path = archive_json_path(data_dir, video_id)
    info = {"video_id": video_id}
    if path.exists():
        info.update(json.loads(path.read_text(encoding="utf-8")))
    folder = video_dir(data_dir, video_id)
    stamp = None
    if info.get("downloaded_at"):
        stamp = info["downloaded_at"]
    elif path.exists():
        stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    elif folder.exists():
        stamp = datetime.fromtimestamp(folder.stat().st_mtime, timezone.utc).isoformat()
    info["downloaded_at"] = stamp or ""
    info["has_video"] = find_video_file(data_dir, video_id) is not None
    info["has_framesheet"] = framesheet_path(data_dir, video_id).is_file()
    info["shot_files"] = sorted(p.name for p in shots_dir(data_dir, video_id).glob("*.png"))
    return info


def list_items(data_dir: Path, query: str = "") -> list[dict]:
    from .db import list_videos

    return list_videos(data_dir, query)
