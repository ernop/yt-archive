"""yt-dlp download. Flags: docs/download.md (settled 2026-08)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import budget
from .paths import (
    VIDEO_EXTS,
    archive_json_path,
    find_video_file,
    is_tweet,
    site_name,
    video_dir,
    watch_url,
)

PROGRESS_MARK = "ytarchive-progress"
PROGRESS_TEMPLATE = (
    "download:" + PROGRESS_MARK + " %(progress.status)s %(progress.downloaded_bytes)s"
    " %(progress.total_bytes)s %(progress.total_bytes_estimate)s %(progress.speed)s"
    " %(progress.eta)s %(info.vcodec)s"
)
# X is read without the user's account: never give it cookies. Only X's extractors run,
# so a tweet that just links elsewhere fails instead of fetching that site outside its
# budget. One video per folder: a tweet with several keeps its first.
SITE_ARGS = {
    "YouTube": ["--cookies-from-browser", "firefox"],
    "X": ["--use-extractors", "twitter.*", "--playlist-items", "1", "--no-write-playlist-metafiles"],
}
_LINKS_ELSEWHERE_RE = re.compile(r"No suitable extractor.* found for URL (\S+)")


def yt_dlp_bin() -> str:
    found = shutil.which("yt-dlp")
    if not found:
        raise FileNotFoundError("yt-dlp not on PATH (expected ~/.local/bin/yt-dlp)")
    return found


def download(
    video_id: str, data_dir: Path, log=print, force: bool = False, progress=None
) -> Path:
    """Download one video into data/<id>/. Returns the video file path.

    YouTube needs Firefox cookies: anonymous YouTube downloads 403 after ~10 MB.
    Deno (yt-dlp default) solves JS challenges. A tweet (x-<tweet id>) is read
    without any account. --restrict-filenames keeps names in [A-Za-z0-9._-].
    """
    dest = video_dir(data_dir, video_id)
    dest.mkdir(parents=True, exist_ok=True)
    existing = find_video_file(data_dir, video_id)
    if existing and not force:
        log(f"already have {existing.name}")
        return existing
    site = site_name(video_id)
    kind = "x-download" if site == "X" else "download"
    told = []

    def waiting(at: float) -> None:
        message = (
            f"waiting: this hour's {site} budget of {budget.LIMITS[kind]} downloads "
            f"is used up · next download at {budget.clock(at)}"
        )
        if progress:
            progress(None, message)
        if not told:
            told.append(at)
            log(message)

    budget.wait_and_take(data_dir, kind, waiting)
    if force:
        _remove_videos(dest, log)

    cmd = [
        yt_dlp_bin(),
        "--ignore-config",
        *SITE_ARGS[site],
        # Highest resolution available, any codec. Do not prefer mp4 — that
        # can pick 1080p AVC over 4K VP9/AV1. Merge without re-encode.
        "-f", "bv*+ba/b",
        "-S", "res,fps,hdr,vcodec:av01:vp9.2:vp9:h264",
        "--restrict-filenames",
        "--write-info-json",
        "--write-thumbnail",
        "--embed-metadata",
        "--no-playlist",
        "-o", str(dest / "%(title)s-%(id)s.%(ext)s"),
        "--print", "after_move:downloaded %(resolution)s %(format_id)s %(ext)s",
        "--progress", "--newline", "--progress-delta", "1",
        "--progress-template", PROGRESS_TEMPLATE,
        watch_url(video_id),
    ]
    log(f"yt-dlp starting (best video+audio, {'no account' if site == 'X' else 'firefox cookies'})")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip()
        if not line:
            continue
        report = parse_progress(line)
        if report is not None:
            if progress:
                progress(*report)
            continue
        lines.append(line)
        log(line)
    if proc.wait() != 0:
        _cleanup_empty(dest)
        raise RuntimeError(_yt_dlp_failure(lines, site))
    log("download finished")
    video = find_video_file(data_dir, video_id)
    if not video:
        raise FileNotFoundError(f"yt-dlp finished but no video in {dest}")
    write_archive_json(data_dir, video_id, video)
    _log_resolution(video, log)
    return video


def parse_progress(line: str) -> tuple[float | None, str] | None:
    """(fraction, message) from one PROGRESS_TEMPLATE line, or None for other output."""
    if not line.startswith(PROGRESS_MARK + " "):
        return None
    fields = line.split()[1:8]
    fields += ["NA"] * (7 - len(fields))
    status, done, total, estimate, speed, eta, vcodec = fields
    done_b, total_b = _number(done), _number(total) or _number(estimate)
    stream = "audio" if vcodec == "none" else "video" if vcodec != "NA" else ""
    if status == "finished":
        return 1.0, f"{stream} stream downloaded".strip().capitalize()
    fraction = done_b / total_b if done_b is not None and total_b else None
    parts = [f"downloading {stream}".strip()]
    if done_b is not None:
        parts.append(_mb(done_b) + (f" of {_mb(total_b)}" if total_b else ""))
    if _number(speed):
        parts.append(f"{_mb(_number(speed))}/s")
    if _number(eta) is not None and fraction is not None and fraction < 1:
        parts.append(f"{_eta(_number(eta))} left")
    return fraction, " · ".join(parts)


def _number(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _mb(size: float) -> str:
    return f"{size / 1e9:.2f} GB" if size >= 1e9 else f"{size / 1e6:.1f} MB"


def _eta(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _remove_videos(dest: Path, log) -> None:
    if not dest.is_dir():
        return
    for p in dest.iterdir():
        name = p.name.lower()
        if p.is_file() and (p.suffix.lower() in VIDEO_EXTS or name.endswith(".part")):
            log(f"removing {p.name} for reget")
            p.unlink()


def _cleanup_empty(dest: Path) -> None:
    try:
        if dest.is_dir() and not any(dest.iterdir()):
            dest.rmdir()
    except OSError:
        pass


def _yt_dlp_failure(lines: list[str], site: str = "YouTube") -> str:
    blob = "\n".join(lines)
    if "drm protected" in blob.lower():
        return (
            "YouTube marked this title DRM-protected (Movies / rental / purchase). "
            "No downloadable video stream — only storyboard images. Cannot archive it."
        )
    if site == "X" and "account credentials" in blob:
        return "X shows this tweet only to signed-in accounts, and the archive reads X without your account."
    elsewhere = _LINKS_ELSEWHERE_RE.search(blob) if site == "X" else None
    if elsewhere:
        return f"This tweet has no video of its own; it links to {elsewhere.group(1)}"
    for line in reversed(lines):
        if line.startswith("ERROR:"):
            return line.removeprefix("ERROR:").strip()
    tail = [ln for ln in lines if not ln.startswith("Extract")][-8:]
    return "yt-dlp failed" + (": " + " | ".join(tail) if tail else "")


def _log_resolution(video: Path, log) -> None:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,codec_name",
                "-of", "csv=p=0",
                str(video),
            ],
            text=True,
        ).strip()
        log(f"local file {video.name}: {out} ({video.stat().st_size / 1e6:.1f} MB)")
    except Exception as exc:
        log(f"ffprobe failed: {exc}")


def write_archive_json(data_dir: Path, video_id: str, video: Path, extra: dict | None = None) -> Path:
    info = {}
    for candidate in video_dir(data_dir, video_id).glob("*.info.json"):
        raw = json.loads(candidate.read_text(encoding="utf-8"))
        info = {
            "video_id": raw.get("id", video_id),
            "url": raw.get("webpage_url") or watch_url(video_id),
            "title": raw.get("title"),
            "channel": raw.get("channel") or raw.get("uploader"),
            # Creator pages link channel_id to YouTube; an X user id is not a channel there.
            "channel_id": "" if is_tweet(video_id) else raw.get("channel_id"),
            "duration": raw.get("duration"),
            "upload_date": raw.get("upload_date"),
            "description": (raw.get("description") or "")[:2000],
            "width": raw.get("width"),
            "height": raw.get("height"),
            "fps": raw.get("fps"),
            "vcodec": raw.get("vcodec"),
            "acodec": raw.get("acodec"),
        }
        break
    payload = {
        **info,
        "video_id": video_id,
        "video_file": video.name,
        "file_size": video.stat().st_size,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload.update(extra)
    out = archive_json_path(data_dir, video_id)
    if out.exists():
        prev = json.loads(out.read_text(encoding="utf-8"))
        kept = prev.get("downloaded_at")
        prev.update(payload)
        if kept:
            prev["downloaded_at"] = kept
        payload = prev
    _atomic_json(out, payload)
    from .db import upsert

    upsert(data_dir, video_id)
    return out


def clear_archive_extras(data_dir: Path, video_id: str, *keys: str) -> None:
    out = archive_json_path(data_dir, video_id)
    if not out.is_file():
        return
    payload = json.loads(out.read_text(encoding="utf-8"))
    changed = False
    for key in keys:
        if key in payload:
            payload.pop(key)
            changed = True
    if changed:
        _atomic_json(out, payload)


def _atomic_json(path: Path, payload: dict) -> None:
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".part", dir=path.parent
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
