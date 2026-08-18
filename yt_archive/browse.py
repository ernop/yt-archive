"""Static HTML so the archive is browsable without matthoom."""
from __future__ import annotations

import json
from pathlib import Path

from .paths import (
    archive_json_path,
    framesheet_path,
    list_archived_ids,
    shots_dir,
    shots_json_path,
    video_dir,
    watch_url,
)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; font: 16px/1.4 system-ui, sans-serif; background: #111; color: #eee; }}
  a {{ color: #f88; }}
  header, main {{ max-width: 1200px; margin: 0 auto; padding: 1.25rem; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 .4rem; }}
  .meta {{ color: #aaa; margin-bottom: 1rem; }}
  .sheet {{ width: 100%; height: auto; border: 1px solid #333; }}
  .shots {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }}
  .shots figure {{ margin: 0; background: #1a1a1a; border: 1px solid #333; }}
  .shots img {{ width: 100%; height: auto; display: block; }}
  .shots figcaption {{ padding: .35rem .5rem; font-size: .8rem; color: #aaa; }}
  .list a {{ display: block; padding: .6rem 0; border-bottom: 1px solid #2a2a2a; text-decoration: none; color: inherit; }}
  .list a:hover {{ color: #f88; }}
</style>
</head>
<body>
<header>
  <div><a href="{home}">yt-archive</a></div>
  <h1>{title}</h1>
  <div class="meta">{meta}</div>
</header>
<main>
{body}
</main>
</body>
</html>
"""


def write_indexes(data_dir: Path) -> Path:
    ids = list_archived_ids(data_dir)
    rows = []
    for video_id in ids:
        info = _info(data_dir, video_id)
        title = info.get("title") or video_id
        shots = info.get("shots_kept")
        shot_bit = f" · {shots} shots" if shots else ""
        rows.append(
            f'<a href="{video_id}/index.html"><strong>{_esc(title)}</strong>'
            f'<div class="meta">{video_id}{shot_bit}</div></a>'
        )
        write_video_index(data_dir, video_id)
    index = data_dir / "index.html"
    index.write_text(
        PAGE.format(
            title="YouTube archive",
            home="./index.html",
            meta=f"{len(ids)} video(s)",
            body=f'<div class="list">{"".join(rows) or "<p>Nothing archived yet.</p>"}</div>',
        ),
        encoding="utf-8",
    )
    return index


def write_video_index(data_dir: Path, video_id: str) -> Path:
    info = _info(data_dir, video_id)
    title = info.get("title") or video_id
    channel = info.get("channel") or ""
    duration = info.get("duration")
    dur = f"{int(duration) // 60}:{int(duration) % 60:02d}" if duration else ""
    meta_bits = [video_id, channel, dur, f'<a href="{watch_url(video_id)}">YouTube</a>']
    video = None
    folder = video_dir(data_dir, video_id)
    for p in folder.iterdir():
        if p.suffix.lower() in {".mkv", ".mp4", ".webm"}:
            video = p.name
            break
    parts = []
    if video:
        parts.append(
            f'<p><video controls preload="metadata" src="{_esc(video)}" '
            f'style="width:100%;max-height:70vh;background:#000"></video></p>'
        )
    sheet = framesheet_path(data_dir, video_id)
    if sheet.exists():
        parts.append(f'<p><img class="sheet" src="_condensed/framesheet.png" alt="framesheet"></p>')
    shot_files = sorted(shots_dir(data_dir, video_id).glob("*.png"))
    times = {}
    sj = shots_json_path(data_dir, video_id)
    if sj.exists():
        for rec in json.loads(sj.read_text(encoding="utf-8")).get("shots", []):
            if rec.get("file"):
                times[rec["file"]] = rec.get("mid")
    if shot_files:
        figs = []
        for shot in shot_files:
            t = times.get(shot.name)
            caption = f"{shot.stem}" + (f" @ {t:.1f}s" if t is not None else "")
            figs.append(
                f'<figure><a href="_condensed/shots/{shot.name}">'
                f'<img src="_condensed/shots/{shot.name}" alt="{shot.stem}"></a>'
                f'<figcaption>{caption}</figcaption></figure>'
            )
        parts.append(f'<div class="shots">{"".join(figs)}</div>')
    out = folder / "index.html"
    out.write_text(
        PAGE.format(
            title=_esc(title),
            home="../index.html",
            meta=" · ".join(b for b in meta_bits if b),
            body="".join(parts) or "<p>No files yet.</p>",
        ),
        encoding="utf-8",
    )
    return out


def _info(data_dir: Path, video_id: str) -> dict:
    path = archive_json_path(data_dir, video_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"video_id": video_id}


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
