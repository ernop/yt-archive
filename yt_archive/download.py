"""yt-dlp download. Flags: docs/download.md (settled 2026-08)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import budget, db
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
# X is read without the user's account. Only a tweet X hides from readers without one is
# asked again with the user's X login from Firefox, on its own budget (approved 2026-11-06).
# Only X's extractors run, so a tweet that just links elsewhere fails instead of fetching
# that site outside its budget. One video per folder: a tweet with several keeps its first.
SITE_ARGS = {
    "YouTube": ["--cookies-from-browser", "firefox"],
    "X": ["--use-extractors", "twitter.*", "--playlist-items", "1", "--no-write-playlist-metafiles"],
}
_LINKS_ELSEWHERE_RE = re.compile(r"No suitable extractor.* found for URL (\S+)")
# yt-dlp's words when X will not show a tweet to a reader without an account: a login hint
# (adult content, protected accounts), or "no video" for the bare tombstone X can send instead.
_HIDDEN_FROM_GUESTS_RE = re.compile(r"account credentials|No video could be found in this tweet")


YT_DLP_UPDATE_DAYS = 7
YT_DLP_UPDATE_FILE = "yt-dlp-update.txt"  # in the data root: when yt-dlp last asked, and its answer
_updating = threading.Lock()


def yt_dlp_bin() -> str:
    with _updating:  # a yt-dlp started while it replaces itself would read half of each file
        return _yt_dlp_path()


def _yt_dlp_path() -> str:
    found = shutil.which("yt-dlp")
    if not found:
        raise FileNotFoundError("yt-dlp not on PATH (expected ~/.local/bin/yt-dlp)")
    return found


def keep_yt_dlp_current(data_dir: Path, log=print) -> bool:
    """`yt-dlp -U` at most once a week (docs/download.md#keeping-yt-dlp-current): YouTube
    changes often and an old yt-dlp stops downloading. Returns whether it asked."""
    stamp = data_dir / YT_DLP_UPDATE_FILE
    try:
        if time.time() - stamp.stat().st_mtime < YT_DLP_UPDATE_DAYS * 86400:
            return False
    except FileNotFoundError:
        pass
    with _updating:
        binary = _yt_dlp_path()
        if _yt_dlp_running(binary):
            return False
        asked = datetime.now(timezone.utc).isoformat(timespec="seconds")
        stamp.write_text(f"{asked} asking\n", encoding="utf-8")
        try:
            done = subprocess.run(
                [binary, "-U"], capture_output=True, text=True, errors="replace", timeout=300
            )
            answer = (done.stdout + done.stderr).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            answer = f"failed: {exc}"
        stamp.write_text(f"{asked}\n{answer}\n", encoding="utf-8")
    log(f"yt-dlp -U: {answer.splitlines()[-1] if answer else 'no answer'}")
    return True


def keep_yt_dlp_current_hourly(data_dir: Path) -> None:
    """The service's thread for keep_yt_dlp_current: a week is due at most an hour late, and a
    week that falls while yt-dlp downloads waits for the next hour."""

    def loop() -> None:
        while True:
            try:
                keep_yt_dlp_current(data_dir, log=lambda message: print(message, flush=True))
            except Exception:
                traceback.print_exc()
            time.sleep(3600)

    threading.Thread(target=loop, name="ytarchive-yt-dlp-update", daemon=True).start()


def _yt_dlp_running(binary: str) -> bool:
    """Whether a yt-dlp process runs: replacing the file under one breaks it, as it reads
    its own modules from that file as it goes."""
    names = {binary, os.path.realpath(binary)}
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = cmdline.read_bytes().split(b"\0")[:2]
        except OSError:
            continue
        if any(os.fsdecode(arg) in names for arg in args):
            return True
    return False


def download(
    video_id: str, data_dir: Path, log=print, force: bool = False, progress=None
) -> Path:
    """Download one video into data/<id>/. Returns the video file path.

    YouTube needs Firefox cookies: anonymous YouTube downloads 403 after ~10 MB.
    Deno (yt-dlp default) solves JS challenges. A tweet (x-<tweet id>) is read
    without an account; one X hides from such readers is asked again with the
    user's X login from Firefox, and from then on asked with it straight away, as
    are the tweets an X profile listing found marked sensitive. --restrict-filenames
    keeps names ASCII, but % + = @ ~ stay in them.
    """
    dest = video_dir(data_dir, video_id)
    dest.mkdir(parents=True, exist_ok=True)
    existing = find_video_file(data_dir, video_id)
    if existing and not force:
        log(f"already have {existing.name}")
        return existing
    site = site_name(video_id)
    signed_in = site == "X" and db.x_hidden(data_dir, video_id)
    if signed_in:
        log("X hides this tweet from readers without an account; asking with your X login from Firefox")
    code, lines = _attempt(video_id, dest, data_dir, site, signed_in, force, log, progress)
    if code != 0 and site == "X" and not signed_in and _HIDDEN_FROM_GUESTS_RE.search("\n".join(lines)):
        db.mark_x_hidden(data_dir, [video_id])
        log("X hides this tweet from readers without an account; asking again with your X login from Firefox")
        code, lines = _attempt(video_id, dest, data_dir, site, True, False, log, progress)
    if code != 0:
        _cleanup_empty(dest)
        raise RuntimeError(_yt_dlp_failure(lines, site))
    log("download finished")
    video = find_video_file(data_dir, video_id)
    if not video:
        raise FileNotFoundError(f"yt-dlp finished but no video in {dest}")
    write_archive_json(data_dir, video_id, video)
    _log_resolution(video, log)
    return video


def _attempt(video_id, dest, data_dir, site, account, force, log, progress) -> tuple[int, list[str]]:
    """One yt-dlp run, paid for from its hourly budget before it starts."""
    kind = "x-account" if account else "x-download" if site == "X" else "download"
    budget.wait_and_take(data_dir, kind, _waiting(kind, log, progress))
    if force:
        _remove_videos(dest, log)
    how = "your X login from Firefox" if account else "no account" if site == "X" else "firefox cookies"
    log(f"yt-dlp starting (best video+audio, {how})")
    return _run_yt_dlp(_yt_dlp_cmd(video_id, dest, site, account=account), log, progress)


def _waiting(kind: str, log, progress):
    """wait_and_take's callback: the wait goes in the queue row, and in the log once."""
    told = []

    def waiting(at: float) -> None:
        message = (
            f"waiting: this hour's {budget.SITES[kind]} budget of {budget.LIMITS[kind]} downloads "
            f"is used up · next download at {budget.clock(at)}"
        )
        if progress:
            progress(None, message)
        if not told:
            told.append(at)
            log(message)

    return waiting


def _yt_dlp_cmd(video_id: str, dest: Path, site: str, account: bool = False) -> list[str]:
    return [
        yt_dlp_bin(),
        "--ignore-config",
        *SITE_ARGS[site],
        *(["--cookies-from-browser", "firefox"] if account else []),
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


def _run_yt_dlp(cmd: list[str], log, progress) -> tuple[int, list[str]]:
    """Run yt-dlp: progress lines go to progress(), the rest to log(). (exit code, logged lines)"""
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
    return proc.wait(), lines


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
    # download() has already asked X again with the user's X login when these appear.
    if site == "X" and "account credentials" in blob:
        return (
            "X refused this tweet even with your X login from Firefox. Check that Firefox is "
            "signed in to x.com and that your account can see the tweet there."
        )
    if site == "X" and "No video could be found in this tweet" in blob:
        return (
            "X sent no video for this tweet, even with your X login from Firefox: either it has "
            "none, or Firefox is not signed in to x.com."
        )
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
    if "added_via" not in payload:
        from .db import first_add

        payload.update(first_add(data_dir, video_id))
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
