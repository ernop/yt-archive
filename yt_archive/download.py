"""yt-dlp download. Flags: docs/download.md (settled 2026-08)."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .paths import VIDEO_EXTS, archive_json_path, find_video_file, video_dir, watch_url


def yt_dlp_bin() -> str:
    found = shutil.which("yt-dlp")
    if not found:
        raise FileNotFoundError("yt-dlp not on PATH (expected ~/.local/bin/yt-dlp)")
    return found


def download(video_id: str, data_dir: Path, log=print, force: bool = False) -> Path:
    """Download one video into data/<id>/. Returns the video file path.

    Firefox cookies are required: anonymous YouTube downloads 403 after ~10 MB.
    Deno (yt-dlp default) solves JS challenges. --restrict-filenames keeps
    names in [A-Za-z0-9._-].
    """
    dest = video_dir(data_dir, video_id)
    dest.mkdir(parents=True, exist_ok=True)
    existing = find_video_file(data_dir, video_id)
    if existing and not force:
        log(f"already have {existing.name}")
        return existing
    if force:
        _remove_videos(dest, log)

    cmd = [
        yt_dlp_bin(),
        "--cookies-from-browser", "firefox",
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
        watch_url(video_id),
    ]
    log("yt-dlp starting (best video+audio, firefox cookies)")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip()
        if not line:
            continue
        lines.append(line)
        log(line)
    if proc.wait() != 0:
        _cleanup_empty(dest)
        raise RuntimeError(_yt_dlp_failure(lines))
    log("download finished")
    video = find_video_file(data_dir, video_id)
    if not video:
        raise FileNotFoundError(f"yt-dlp finished but no video in {dest}")
    write_archive_json(data_dir, video_id, video)
    _log_resolution(video, log)
    return video


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


def _yt_dlp_failure(lines: list[str]) -> str:
    blob = "\n".join(lines)
    if "drm protected" in blob.lower():
        return (
            "YouTube marked this title DRM-protected (Movies / rental / purchase). "
            "No downloadable video stream — only storyboard images. Cannot archive it."
        )
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
            "channel_id": raw.get("channel_id"),
            "duration": raw.get("duration"),
            "upload_date": raw.get("upload_date"),
            "description": (raw.get("description") or "")[:2000],
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
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    from .db import upsert

    upsert(data_dir, video_id)
    return out
