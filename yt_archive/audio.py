"""Dump the video soundtrack to _condensed/soundtrack.mp3."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .download import write_archive_json
from .paths import soundtrack_path


def has_audio_stream(video: Path) -> bool:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                str(video),
            ],
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return False
    return bool(out)


def dump_soundtrack(
    video: Path,
    data_dir: Path,
    video_id: str,
    log=print,
    force: bool = False,
) -> Path | None:
    dest = soundtrack_path(data_dir, video_id)
    if dest.is_file() and not force:
        log(f"already have {dest.name}")
        return dest
    if not has_audio_stream(video):
        log("no audio stream — skipping soundtrack")
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    if tmp.exists():
        tmp.unlink()
    log(f"dumping soundtrack → {dest.name}")
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-y", "-i", str(video),
                "-vn", "-codec:a", "libmp3lame", "-q:a", "0",
                "-f", "mp3",
                str(tmp),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or not tmp.is_file():
            err = (proc.stderr or proc.stdout or "").strip() or "ffmpeg soundtrack failed"
            raise RuntimeError(err)
        tmp.replace(dest)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    log(f"soundtrack {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    write_archive_json(data_dir, video_id, video, extra={"soundtrack": dest.name})
    return dest
