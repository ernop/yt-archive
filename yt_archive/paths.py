"""Archive layout. One directory per YouTube id; originals stay untouched."""
from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
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


def framesheet_paths(data_dir: Path, video_id: str) -> list[Path]:
    """Existing sheets, in order. Single framesheet.png, or the
    framesheet-N.png parts written when >MAX_SHEET_TILES shots split."""
    cond = condensed_dir(data_dir, video_id)
    sheets = []
    single = cond / "framesheet.png"
    if single.is_file():
        sheets.append((0, single))
    for p in cond.glob("framesheet-*.png"):
        num = p.stem.removeprefix("framesheet-")
        if num.isdigit():
            sheets.append((int(num), p))
    return [p for _, p in sorted(sheets)]


def all_labeled_path(data_dir: Path, video_id: str) -> Path:
    return condensed_dir(data_dir, video_id) / "shots_all_labeled.png"


def soundtrack_path(data_dir: Path, video_id: str) -> Path:
    return condensed_dir(data_dir, video_id) / "soundtrack.mp3"


def transcript_json_path(data_dir: Path, video_id: str) -> Path:
    return condensed_dir(data_dir, video_id) / "transcript.json"


def transcript_vtt_path(data_dir: Path, video_id: str) -> Path:
    return condensed_dir(data_dir, video_id) / "transcript.vtt"


def restrict_filename(text: str, *, fallback: str = "untitled", max_len: int = 80) -> str:
    """Keep a title fragment in [A-Za-z0-9._-], same charset as --restrict-filenames."""
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    if not text:
        text = fallback
    if len(text) > max_len:
        text = text[:max_len].rstrip("._-") or fallback
    return text


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def is_complete_png(raw: bytes) -> bool:
    return len(raw) >= 20 and raw.startswith(PNG_MAGIC) and raw[-8:-4] == b"IEND"


def grab_filename(t: float, title: str = "") -> str:
    ms = max(0, int(round(float(t) * 1000)))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    # No extra dots: `39.953s.png` makes `.953s` look like the extension.
    stamp = f"{hours:02d}h{minutes:02d}m{seconds:02d}s{millis:03d}"
    label = restrict_filename(title).replace(".", "_")
    return f"grab-{label}-{stamp}.png"


def unique_grab_path(folder: Path, t: float, title: str = "") -> Path:
    dest = folder / grab_filename(t, title)
    if not dest.exists():
        return dest
    stem = dest.stem
    n = 2
    while True:
        cand = folder / f"{stem}-{n}.png"
        if not cand.exists():
            return cand
        n += 1


def write_grab_png(folder: Path, t: float, title: str, raw: bytes) -> Path:
    """Write a complete PNG under an exclusive name. Temp + link, never a half file."""
    if not is_complete_png(raw):
        raise ValueError("not a complete png")
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".grab-", suffix=".part", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        stem = grab_filename(t, title).removesuffix(".png")
        n = 1
        while True:
            dest = folder / f"{stem}.png" if n == 1 else folder / f"{stem}-{n}.png"
            try:
                os.link(tmp, dest)
            except FileExistsError:
                n += 1
                continue
            return dest
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


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
    info["has_framesheet"] = bool(framesheet_paths(data_dir, video_id))
    info["has_soundtrack"] = soundtrack_path(data_dir, video_id).is_file()
    info["has_transcript"] = (
        transcript_json_path(data_dir, video_id).is_file()
        and transcript_vtt_path(data_dir, video_id).is_file()
    )
    info["shot_files"] = sorted(p.name for p in shots_dir(data_dir, video_id).glob("*.png"))
    return info


def list_items(
    data_dir: Path, query: str = "", sort: str = "recent"
) -> list[dict]:
    from .db import list_videos

    return list_videos(data_dir, query, sort)
