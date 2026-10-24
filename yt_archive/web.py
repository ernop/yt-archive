"""ytarchive web UI — paste a URL, get the video + squash framesheet."""
from __future__ import annotations

import hmac
import ipaddress
import json
import mimetypes
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from . import budget, db
from .budget import BudgetExceeded
from .creators import CreatorBrowser, browse_url, playlist_browse_url
from .db import list_creator_videos, normalize_video_sort
from .jobs import JobQueue
from .paths import (
    all_labeled_path,
    bookmarklet_key,
    condensed_dir,
    default_data_dir,
    file_cache_key,
    find_video_file,
    framesheet_paths,
    NEEDS,
    is_complete_png,
    write_grab_png,
    list_items,
    load_archive_info,
    media_url,
    parse_video_id,
    read_shots_json,
    shots_dir,
    soundtrack_path,
    transcript_is_complete,
    transcript_json_path,
    transcript_vtt_path,
    video_dir,
    watch_url,
)
from .work import DerivedWorkQueue

# Finished files never change at a given URL (multi-image-client rule).
# HTML embeds ?v=mtime-size; reget writes new bytes → new key.
IMMUTABLE = "public, max-age=31536000, immutable"
LIVE = "no-store"

STATIC_DIR = Path(__file__).resolve().parent / "static"

CSS = """
:root { color-scheme: dark; --bg:#000; --card:#000; --line:#555; --fg:#fff; --muted:#fff; --red:#c4302b;
  --ok:#8d8; --bad:#f88; --mono: ui-monospace, SFMono-Regular, Menlo, monospace; }
* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.45 system-ui, sans-serif; background: var(--bg); color: var(--fg); }
a { color: #f88; }
input::placeholder { color: #fff; opacity: 1; }
button:disabled { color: #fff !important; opacity: 1 !important; cursor: wait; }
header, main { width: 100%; margin: 0; padding: 1.25rem clamp(1rem, 3vw, 2.5rem) 2rem; }
header { padding-bottom: .5rem; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
h1 a { color: inherit; text-decoration: none; }
.sub { color: var(--muted); margin-bottom: 1.25rem; }
form.get { display: flex; gap: .5rem; margin-bottom: 1rem; }
form.get input { flex: 1; background: #0d0d0d; border: 1px solid var(--line); color: var(--fg);
  padding: .7rem .8rem; border-radius: 6px; font-size: 1rem; min-width: 0; }
form.get #browse-creator { background: #222; border: 1px solid var(--line); }
form.get input:focus { outline: none; border-color: var(--red); }
form.get button { background: var(--red); color: #fff; border: 0; padding: .7rem 1.2rem;
  border-radius: 6px; font-weight: 600; cursor: pointer; }
form.get button:disabled { opacity: 1; cursor: wait; }
.archive-tools { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .75rem;
  align-items: end; margin: 0 0 1rem; }
.archive-tools .find { display: flex; gap: .5rem; min-width: 0; }
.archive-tools input { flex: 1; min-width: 0; background: #0d0d0d; border: 1px solid var(--line); color: var(--fg);
  padding: .45rem .7rem; border-radius: 6px; font-size: .95rem; }
.archive-tools input:focus, .archive-tools select:focus { outline: none; border-color: var(--red); }
.archive-tools button { background: #2a2a2a; color: var(--fg); border: 1px solid var(--line);
  padding: .45rem .8rem; border-radius: 6px; cursor: pointer; }
.sort-field { display: grid; gap: .25rem; color: var(--muted); font-size: .78rem; font-weight: 700; }
.sort-field select { min-width: 12rem; background: #0d0d0d; border: 1px solid var(--line);
  color: var(--fg); padding: .45rem .7rem; border-radius: 6px; font: 500 .95rem/1.45 system-ui, sans-serif; }
#status { color: var(--muted); margin-bottom: .75rem; font-size: .95rem; }
#status:empty { display: none; }
#status .note { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#status .note.error { color: var(--bad); }
#status .note.done { color: var(--ok); }
.bookmarklet { margin: 0 0 1rem; }
.bookmarklet a { display: inline-block; background: #222; border: 1px solid var(--line); color: #fff;
  padding: .2rem .65rem; border-radius: 6px; font-weight: 700; text-decoration: none; cursor: grab; }
.bookmarklet a:hover { border-color: var(--red); }
.add h1 { font-size: 2rem; }
.add.done h1 { color: #8d8; }
.add.error h1 { color: #f88; }
.add-title { font-size: 1.15rem; font-weight: 700; margin: .3rem 0 .6rem; overflow-wrap: anywhere; }
.add .meta { overflow-wrap: anywhere; }
h2 { font-size: 1.1rem; color: var(--muted); font-weight: 650; margin: 2rem 0 .75rem; }
.num { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.btn { background: #161616; border: 1px solid var(--line); color: #fff; padding: .3rem .65rem;
  border-radius: 4px; cursor: pointer; font: 600 .85rem/1.3 system-ui, sans-serif; }
.btn:hover { border-color: #fff; }
.btn.primary { background: #2e7441; border-color: #4a9d61; }

.queue { margin: .25rem 0 1rem; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.queue-head { display: flex; align-items: center; flex-wrap: wrap; gap: .4rem 1.1rem;
  padding: .55rem .8rem; background: #0d0d0d; }
.queue-head h2 { margin: 0; font-size: 1.15rem; font-weight: 800; color: #fff; }
.queue-counts { display: flex; flex-wrap: wrap; gap: .3rem 1rem; font-size: .9rem; }
.queue-counts strong { font: 800 1.1rem/1 var(--mono); font-variant-numeric: tabular-nums; margin-right: .25rem; }
.queue-counts .working strong { color: #fff; }
.queue-counts .failed strong { color: var(--bad); }
.queue-head .btn { margin-left: auto; }
.qrow { display: grid; grid-template-columns: 6.5rem minmax(0, 3fr) minmax(0, 4fr) 7.5rem 9.5rem;
  gap: .9rem; align-items: center; padding: .4rem .8rem; border-top: 1px solid #262626; white-space: nowrap; }
.qrow > * { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.qstate { font-weight: 800; font-size: .9rem; }
.qrow.running .qstate { color: #fff; }
.qrow.running { background: #141414; }
.qrow.error .qstate, .qrow.error .qdetail { color: var(--bad); }
.qpos { font: 800 1rem var(--mono); margin-right: .3rem; }
.qtitle { color: #fff; font-weight: 700; text-decoration: none; }
a.qtitle:hover { text-decoration: underline; }
.qid { font: 600 .9rem var(--mono); }
.qdetail { font-size: .9rem; }
.qrow.error .qdetail { cursor: pointer; }
.qrow.expanded .qdetail { white-space: pre-wrap; overflow-wrap: anywhere; }
.step { display: inline-block; padding: .05rem .45rem; margin-right: .3rem; border: 1px solid #444;
  border-radius: 999px; font-size: .8rem; font-weight: 600; }
.step.done, .step.skipped { border-color: #3d6b46; color: var(--ok); }
.step.running { border-color: #fff; font-weight: 800; }
.step .pct { font: 800 .85rem var(--mono); font-variant-numeric: tabular-nums; }
.qtime { font: 700 .95rem var(--mono); font-variant-numeric: tabular-nums; text-align: right; }
.qact { display: flex; gap: .35rem; justify-content: flex-end; }
.qmore { padding: .35rem .8rem; border-top: 1px solid #262626; font-size: .9rem; }

.archive-head { display: flex; align-items: baseline; flex-wrap: wrap; gap: .4rem 1rem; margin: 1.5rem 0 .75rem; }
.archive-head h2 { margin: 0; font-size: 1.25rem; font-weight: 800; color: #fff; }
.archive-head .count strong { font: 800 1.15rem var(--mono); margin-right: .25rem; }
.health { display: flex; align-items: center; flex-wrap: wrap; gap: .4rem .9rem; margin: 0 0 1rem;
  padding: .5rem .8rem; border: 1px solid #7a5a1d; border-radius: 8px; background: #140f04; }
.health strong { font: 800 1.05rem var(--mono); }
.health .kinds { display: flex; flex-wrap: wrap; gap: .2rem .9rem; font-size: .9rem; }
.health .kinds b { font-family: var(--mono); margin-right: .2rem; }
#update-status:empty { display: none; }

.vlist { border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.vrow { display: grid; grid-template-columns: 8rem minmax(0, 6fr) minmax(0, 2.2fr) 5.5rem 4.5rem 7rem 11rem;
  gap: .9rem; align-items: center; padding: .3rem .8rem .3rem .3rem; border-top: 1px solid #262626;
  white-space: nowrap; }
.vlist.no-creator .vrow { grid-template-columns: 8rem minmax(0, 6fr) 5.5rem 4.5rem 7rem 11rem; }
.vrow:first-child { border-top: 0; }
.vrow:not(.vhead):hover { background: #111; }
.vrow > * { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.vhead { background: #0d0d0d; padding-top: .45rem; padding-bottom: .45rem; font-weight: 800; font-size: .95rem; }
.vrow .thumb { display: block; }
.vrow .thumb img, .vrow .thumb .ph { width: 8rem; aspect-ratio: 16 / 9; object-fit: cover; display: block;
  border-radius: 3px; background: #111; }
.vrow .thumb .ph { display: flex; align-items: center; justify-content: center; font-size: .75rem; }
.vrow .t { color: #fff; font-weight: 700; font-size: 1.02rem; text-decoration: none; }
.vrow .t:hover, .creator-link:hover { text-decoration: underline; }
.vrow .n, .vrow .d { text-align: right; }
.vrow .n { font: 700 1rem var(--mono); font-variant-numeric: tabular-nums; }
.vrow .d { font: 600 .92rem var(--mono); font-variant-numeric: tabular-nums; }
.vhead .n, .vhead .d { font: 800 .95rem system-ui, sans-serif; }
.vrow .busy { color: #fff; font-weight: 800; font-size: .8rem; margin-left: .5rem; padding: .05rem .4rem;
  border: 1px solid #fff; border-radius: 999px; }
.meta { color: var(--muted); font-size: .85rem; }
.creator-count { margin-top: .55rem; }
.creator-count strong { color: #fff; font: 750 1.4rem/1 var(--mono);
  font-variant-numeric: tabular-nums; }
.empty { color: var(--muted); padding: .6rem .8rem; }

.facts { display: flex; flex-wrap: wrap; align-items: baseline; gap: .3rem 1.2rem; margin: .4rem 0 .2rem; font-size: .9rem; }
.facts b { font: 800 1.05rem var(--mono); font-variant-numeric: tabular-nums; margin-right: .2rem; }
.facts .creator-link { font-weight: 700; font-size: 1rem; }
.banner { margin: .75rem 0; padding: .5rem .8rem; border: 1px solid #fff; border-radius: 8px; background: #141414; }
.banner strong { margin-right: .5rem; }
.description { margin: 0 0 1.25rem; }
.description summary { cursor: pointer; font-weight: 800; font-size: 1.05rem; }
.description .text { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: .5rem; max-width: none; }
.sheet { width: 100%; height: auto; border: 1px solid var(--line); margin-bottom: 1.25rem; }
.sheet-label { font-weight: 700; margin: .5rem 0 .3rem; }
.shots { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }
.shots figure { margin: 0; background: var(--card); border: 1px solid var(--line); }
.shots img { width: 100%; height: auto; display: block; }
.shots figcaption { padding: .3rem .45rem; font-size: .8rem; color: var(--muted); }
.backup { margin-top: 2.75rem; padding-top: 1rem; border-top: 1px solid var(--line);
  color: var(--muted); font-size: .85rem; }
.backup h2 { margin: 0 0 .5rem; font-size: 1rem; font-weight: 800; color: #fff; }
.backup p { margin: 0 0 .5rem; }
.backup button { margin-right: .4rem; }
#reget-status { margin-top: .6rem; font-family: var(--mono); white-space: pre-wrap; }
#reget-status.error { color: #f88; }
#reget-status.done { color: #8d8; }
.soundtrack { margin: 0 0 1.5rem; }
.soundtrack h2 { color: #fff; font-weight: 800; }
.soundtrack audio { width: 100%; margin: .4rem 0 .6rem; }
.soundtrack .row { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; }
#audio-status { margin-top: .4rem; font-family: var(--mono); font-size: .85rem; white-space: pre-wrap; color: var(--muted); }
#audio-status.error { color: #f88; }
#audio-status.done { color: #8d8; }
.transcript { margin: 1.25rem 0 2rem; padding: 1rem; border: 1px solid var(--line);
  border-radius: 8px; background: var(--card); }
.transcript h2 { color: var(--fg); font-size: 1.25rem; font-weight: 700; margin: 0 0 .65rem; }
.transcript .actions, .transcript-controls, .speaker-tools { display: flex; flex-wrap: wrap;
  align-items: center; gap: .55rem; }
.transcript button, .transcript select, .transcript input, dialog button, dialog select, dialog input {
  background: #111; border: 1px solid #444; color: var(--fg); padding: .45rem .65rem;
  border-radius: 5px; font: inherit; }
.transcript button, dialog button { cursor: pointer; }
.transcript button:hover, dialog button:hover { border-color: #888; }
.transcript button:disabled, dialog button:disabled { opacity: 1; cursor: wait; }
.transcript-search { flex: 1 1 18rem; min-width: 12rem; }
.transcript-progress { margin-top: .8rem; }
.transcript-progress-value { color: var(--fg); font: 700 1.65rem/1 var(--mono);
  font-variant-numeric: tabular-nums; }
.transcript-progress-label { color: var(--muted); margin-left: .45rem; }
.transcript-progress-track { height: 8px; background: #090909; border-radius: 4px; margin-top: .5rem; overflow: hidden; }
.transcript-progress-fill { height: 100%; width: 0; background: #68c77b; transition: width .2s; }
.transcript-progress.error .transcript-progress-value, .transcript-progress.error .transcript-progress-label { color: #f88; }
.transcript-meta { color: var(--muted); margin: .6rem 0; }
.caption-box { max-height: min(48vh, 34rem); overflow: auto; border: 1px solid #383838;
  background: #0d0d0d; margin-top: .75rem; scroll-behavior: smooth; }
.caption-box.rolling .segment:not(.now) { display: none; }
.caption-box.size-small .segment-text { font-size: .9rem; }
.caption-box.size-medium .segment-text { font-size: 1.08rem; }
.caption-box.size-large .segment-text { font-size: 1.4rem; line-height: 1.5; }
.segment { display: grid; grid-template-columns: 5.5rem 9rem minmax(0, 1fr); gap: .75rem;
  align-items: baseline; padding: .55rem .75rem; border-bottom: 1px solid #292929; cursor: pointer; }
.segment:last-child { border-bottom: 0; }
.segment:hover { background: #171717; }
.segment.now { background: #202820; box-shadow: inset 4px 0 #68c77b; }
.segment-time { color: #fff; font: 700 .95rem/1.45 var(--mono);
  font-variant-numeric: tabular-nums; }
.transcript .segment-speaker { color: #a9d6ff; background: none; border: 0; padding: 0; font-size: .85rem;
  font-weight: 700; text-align: left; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.segment-text { color: var(--fg); line-height: 1.45; }
.word { border-radius: 3px; }
.word:hover { background: #554b19; color: #fff; }
.segment.match { background: #28230f; }
.transcript-empty { color: var(--muted); padding: 1rem; }
.speaker-tools { border-top: 1px solid #333; margin-top: .8rem; padding-top: .8rem; }
.speaker-tools label, .transcript-controls label { color: var(--muted); font-size: .85rem; font-weight: 700; }
dialog { width: min(34rem, calc(100% - 2rem)); color: var(--fg); background: #181818;
  border: 1px solid #555; border-radius: 9px; padding: 1.1rem; }
dialog::backdrop { background: rgba(0,0,0,.72); }
dialog h2 { color: var(--fg); font-size: 1.25rem; font-weight: 750; margin: 0 0 .4rem; }
dialog .fields { display: grid; grid-template-columns: 1fr 1fr; gap: .8rem; margin: 1rem 0; }
dialog label { display: grid; gap: .3rem; color: #fff; font-size: .85rem; font-weight: 700; }
dialog .dialog-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: .5rem; }
dialog .primary { background: #2e7441; border-color: #4a9d61; font-weight: 700; }
@media (max-width: 760px) {
  form.get { flex-wrap: wrap; }
  form.get input { flex-basis: 100%; }
  .archive-tools { grid-template-columns: 1fr; align-items: stretch; }
  .sort-field select { width: 100%; }
  .vrow, .vlist.no-creator .vrow { grid-template-columns: 5.5rem minmax(0, 1fr) 4.5rem; }
  .vrow .thumb img, .vrow .thumb .ph { width: 5.5rem; }
  .vrow > .c, .vrow > .s, .vrow > .u, .vrow > .g { display: none; }
  .qrow { grid-template-columns: 5.5rem minmax(0, 1fr) auto; }
  .qrow > .qdetail, .qrow > .qtime { display: none; }
  .segment { grid-template-columns: 4.6rem 6rem minmax(0, 1fr); gap: .5rem; }
  dialog .fields { grid-template-columns: 1fr; }
}
"""


def _esc(text) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _is_local_host(value: str) -> bool:
    value = (value or "").strip().lower()
    if value.startswith("["):
        host = value[1:].split("]", 1)[0]
    else:
        host, separator, port = value.rpartition(":")
        if not separator or not port.isdigit():
            host = value
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _page(title: str, body: str, extra_js: str = "", extra_head: str = "", extra_tail: str = "") -> bytes:
    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{_esc(title)}</title>
<style>{CSS}</style>
{extra_head}
</head><body>
{body}
{f'<script>{extra_js}</script>' if extra_js else ''}
{extra_tail}
</body></html>
"""
    return html.encode("utf-8")


def _static_url(name: str) -> str:
    path = STATIC_DIR / name
    return f"/static/{name}?v={file_cache_key(path)}"


def _media_player_includes() -> tuple[str, str]:
    """The stylesheet and script tags of this repo's copy of the shared media player
    (docs/setup.md, "Media player")."""
    return (
        f'<link rel="stylesheet" href="{_esc(_static_url("media-player/media-player.css"))}">',
        f'<script src="{_esc(_static_url("media-player/media-player.js"))}"></script>',
    )


SORT_OPTIONS = (
    ("recent", "Recently gotten"),
    ("oldest", "First gotten"),
    ("uploaded", "Video date — newest"),
    ("title", "Title — A–Z"),
    ("channel", "Channel — A–Z"),
)


def _clock(seconds) -> str:
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _upload_date(value: str) -> str:
    value = str(value or "")
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _time_html(stamp: str) -> str:
    """A UTC timestamp; LOCAL_TIMES_JS rewrites it in the viewer's time zone."""
    stamp = str(stamp or "")
    display = stamp[:16].replace("T", " ") if stamp else "unknown"
    if stamp.endswith("+00:00") or stamp.endswith("Z"):
        display += " UTC"
    return f'<time datetime="{_esc(stamp)}" data-local>{_esc(display)}</time>'


def _size(num_bytes) -> str:
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") or size >= 100 else f"{size:.1f} {unit}"
        size /= 1000
    return ""


def _creator_url(item: dict) -> str:
    channel_id = str(item.get("channel_id") or "").strip()
    channel = str(item.get("channel") or "").strip()
    if channel_id:
        return "/creator?" + urlencode({"channel_id": channel_id})
    if channel:
        return "/creator?" + urlencode({"channel": channel})
    return ""


LIST_HEAD = (
    '<div class="vrow vhead"><span></span><span>Title</span>{creator}'
    '<span class="n">Length</span><span class="n s">Shots</span>'
    '<span class="d u">Uploaded</span><span class="d g">Got</span></div>'
)


def _video_row_html(item: dict, *, show_creator: bool = True, busy: frozenset = frozenset()) -> str:
    """One archived video as a single-line row (docs/browsing.md, "Archive list")."""
    video_id = item["video_id"]
    href = f"/v/{_esc(video_id)}"
    thumb = (
        f'<img src="{_esc(item["thumb_url"])}" alt="" loading="lazy" decoding="async">'
        if item.get("thumb_url")
        else '<span class="ph">no image</span>'
    )
    creator = ""
    if show_creator:
        name = _esc(item.get("channel") or "unknown uploader")
        url = _creator_url(item)
        creator = (
            f'<a class="c creator-link" href="{_esc(url)}">{name}</a>' if url else f'<span class="c">{name}</span>'
        )
    shots = item.get("shots_kept") or ""
    flag = '<span class="busy">updating</span>' if video_id in busy else ""
    return (
        f'<div class="vrow"><a class="thumb" href="{href}" tabindex="-1">{thumb}</a>'
        f'<span><a class="t" href="{href}" title="{_esc(item.get("title") or video_id)}">'
        f'{_esc(item.get("title") or video_id)}</a>{flag}</span>'
        f'{creator}'
        f'<span class="n">{_esc(_clock(item.get("duration")))}</span>'
        f'<span class="n s">{_esc(shots)}</span>'
        f'<span class="d u">{_esc(_upload_date(item.get("upload_date")))}</span>'
        f'<span class="d g">{_time_html(item.get("downloaded_at") or "")}</span></div>'
    )


def _video_list_html(items: list[dict], empty: str, *, show_creator: bool = True,
                     busy: frozenset = frozenset()) -> str:
    rows = "".join(_video_row_html(item, show_creator=show_creator, busy=busy) for item in items)
    head = LIST_HEAD.format(creator='<span class="c">Creator</span>' if show_creator else "")
    css = "vlist" if show_creator else "vlist no-creator"
    return f'<div class="{css}" id="archive-list">{head}{rows or f"<p class=empty>{empty}</p>"}</div>'


def _health_html(summary: dict) -> str:
    """The "out of date" bar: videos a plain job would still change (docs/browsing.md)."""
    videos = summary.get("videos") or []
    if not videos:
        return '<div id="archive-health"></div>'
    counts = summary.get("counts") or {}
    kinds = "".join(
        f"<span><b>{counts[need]}</b>{_esc(label)}</span>"
        for need, label in NEEDS.items()
        if counts.get(need)
    )
    return (
        '<div id="archive-health"><div class="health">'
        f'<span><strong>{len(videos)}</strong> videos are missing newer work:</span>'
        f'<span class="kinds">{kinds}</span>'
        f'<button type="button" class="btn primary" id="update-archive">Update {len(videos)} videos</button>'
        '<span id="update-status"></span></div></div>'
    )


LOCAL_TIMES_JS = r"""
function localizeTimes(root) {
  (root || document).querySelectorAll('time[data-local]').forEach((el) => {
    const date = new Date(el.dateTime);
    if (!el.dateTime || Number.isNaN(date.getTime())) return;
    const pad = (n) => String(n).padStart(2, '0');
    el.textContent = date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
      + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
  });
}
localizeTimes();
"""


def home_html(
    items: list[dict],
    query: str = "",
    data_dir: Path | None = None,
    sort: str = "recent",
    bookmarklet: str = "",
    health: dict | None = None,
    busy: frozenset = frozenset(),
    total: int | None = None,
) -> bytes:
    sort = normalize_video_sort(sort)
    bookmarklet_html = (
        f'<p class="bookmarklet">Or drag <a id="bookmarklet" href="#" data-key="{_esc(bookmarklet)}">'
        "+ ytarchive</a> to your bookmarks bar and click it on any YouTube video or creator page.</p>"
        if bookmarklet
        else ""
    )
    empty = f"No matches for “{_esc(query)}”." if query else "Nothing archived yet."
    total = len(items) if total is None else total
    count = (
        f'<strong>{len(items)}</strong>of <strong>{total}</strong>videos match'
        if query
        else f"<strong>{total}</strong>videos"
    )
    q_val = _esc(query)
    sort_options = "".join(
        f'<option value="{value}"{" selected" if value == sort else ""}>{label}</option>'
        for value, label in SORT_OPTIONS
    )
    body = f"""
<header>
  <h1><a href="/">ytarchive</a></h1>
  <div class="sub">Paste video links to get them, or a creator’s homepage, @handle, or playlist link to choose from all of its videos.</div>
  <form class="get" id="get-form">
    <input name="url" id="url" type="text" autofocus
      aria-label="Video links, YouTube creator, or playlist"
      placeholder="video links, creator homepage, @handle, or playlist link"
      autocomplete="off">
    <button type="submit" id="go">Get</button>
    <button type="button" id="browse-creator">Browse</button>
  </form>
  <div id="status" role="status" aria-live="polite"></div>
  <div id="creator-results"></div>
  <section class="queue" id="queue" aria-label="Download queue" hidden>
    <div class="queue-head"><h2>Download queue</h2><span class="queue-counts" id="queue-counts"></span></div>
    <div id="queue-rows"></div>
  </section>
  {bookmarklet_html}
</header>
<main id="archive">
  <div class="archive-head"><h2>Archive</h2><span class="count" id="archive-count">{count}</span></div>
  {_health_html(health or {})}
  <form class="archive-tools" method="get" action="/">
    <div class="find">
      <input name="q" value="{q_val}" placeholder="search title, channel, id, transcript…" autocomplete="off">
      <button type="submit">Search</button>
    </div>
    <label class="sort-field"><span>Sort archive</span>
      <select name="sort" onchange="this.form.submit()">{sort_options}</select>
    </label>
  </form>
  {_video_list_html(items, empty, busy=busy)}
</main>
"""
    js = LOCAL_TIMES_JS + r"""
const form = document.getElementById('get-form');
const status = document.getElementById('status');
const urlBox = document.getElementById('url');
const countsEl = document.getElementById('queue-counts');
const rowsEl = document.getElementById('queue-rows');
const expanded = new Set();
const NEEDS = """ + json.dumps(NEEDS) + r""";
let allWaiting = false;
let openSeen = null;
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function note(text, kind, href) {
  const row = document.createElement(href ? 'a' : 'div');
  row.className = 'note' + (kind ? ' ' + kind : '');
  row.textContent = text;
  if (href) { row.href = href; row.style.display = 'block'; }
  status.append(row);
}
document.getElementById('browse-creator').onclick = () => {
  const source = urlBox.value.trim().split(/\s+/)[0];
  window.location.assign(source ? '/browse?' + new URLSearchParams({source}) : '/browse');
};
const bookmarklet = document.getElementById('bookmarklet');
if (bookmarklet) {
  const code = '(()=>{const u=new URL(' + JSON.stringify(location.origin + '/add') + ');'
    + "u.searchParams.set('url',location.href);"
    + "u.searchParams.set('title',document.title);"
    + "u.searchParams.set('key'," + JSON.stringify(bookmarklet.dataset.key) + ');'
    + "if(!window.open(u.href,'_blank','popup,width=560,height=340'))"
    + "alert('ytarchive: allow pop-ups on this site to queue videos');})()";
  bookmarklet.href = 'javascript:' + encodeURIComponent(code);
  bookmarklet.onclick = (e) => {
    e.preventDefault();
    status.replaceChildren();
    note('Drag that button to your bookmarks bar, then click it on a YouTube page.');
  };
}
async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const urls = urlBox.value.split(/\s+/).map((s) => s.trim()).filter(Boolean);
  if (!urls.length) return;
  urlBox.value = '';
  urlBox.focus();
  status.replaceChildren();
  const creators = document.getElementById('creator-results');
  creators.replaceChildren();
  for (const url of urls) {
    try {
      const data = await post('/api/get', {url});
      if (data.browse_url) {
        if (urls.length === 1) { window.location.assign(data.browse_url); return; }
        const link = document.createElement('a');
        link.href = data.browse_url;
        link.textContent = 'Browse ' + url;
        const row = document.createElement('p');
        row.append(link);
        creators.append(row);
        continue;
      }
      const name = data.title || data.video_id;
      if (data.status === 'complete') note('Already archived and up to date: ' + name, 'done', '/v/' + data.video_id);
      else if (data.existing) note('Already in the queue: ' + name);
      else note('Added to the queue: ' + name);
      if (data.playlist_browse_url) note('That link is from a playlist. Choose more from the playlist →', '', data.playlist_browse_url);
    } catch (err) {
      note(url + ' — ' + err.message, 'error');
    }
  }
  paintQueue();
});
const STEPS = [['download', 'Download'], ['shots', 'Shots'], ['audio', 'MP3']];
function steps(j) {
  return STEPS.map(([key, label]) => {
    const state = j[key] || 'pending';
    let text = esc(label);
    if (state === 'running' && j.progress != null) text += ' <span class="pct">' + Math.floor(j.progress * 100) + '%</span>';
    if (state === 'done' || state === 'skipped') text = '✓ ' + text;
    return '<span class="step ' + state + '">' + text + '</span>';
  }).join('');
}
function clock(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  const h = Math.floor(seconds / 3600), m = Math.floor(seconds / 60) % 60, s = seconds % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
}
function when(iso) {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  const today = new Date().toDateString() === date.toDateString();
  return (today ? '' : (date.getMonth() + 1) + '/' + date.getDate() + ' ')
    + pad(date.getHours()) + ':' + pad(date.getMinutes());
}
function titleHtml(j) {
  const text = j.title ? esc(j.title) : '<span class="qid">' + esc(j.video_id) + '</span>';
  return j.archived
    ? '<a class="qtitle" href="/v/' + esc(j.video_id) + '" title="' + esc(j.title || j.video_id) + '">' + text + '</a>'
    : '<span class="qtitle" title="' + esc(j.url) + '">' + text + '</span>';
}
function row(kind, j, state, detail, time, actions) {
  const open = expanded.has(j.job_id) ? ' expanded' : '';
  return '<div class="qrow ' + kind + open + '" data-job="' + esc(j.job_id) + '" data-url="' + esc(j.url) + '">'
    + '<span class="qstate">' + state + '</span>' + titleHtml(j)
    + '<span class="qdetail" title="' + esc(j.error || j.message || '') + '">' + detail + '</span>'
    + '<span class="qtime"' + time + '</span>'
    + '<span class="qact">' + (actions || '') + '</span></div>';
}
function render(q) {
  const parts = [];
  if (q.running.length) parts.push('<span class="working"><strong>' + q.running.length + '</strong>working</span>');
  if (q.waiting_count) parts.push('<span><strong>' + q.waiting_count + '</strong>waiting</span>');
  if (q.failed.length) parts.push('<span class="failed"><strong>' + q.failed.length + '</strong>failed</span>');
  if (q.waiting_count > 1) parts.push('<button type="button" class="btn" data-act="clear">Remove all waiting</button>');
  countsEl.innerHTML = parts.join('');
  document.getElementById('queue').hidden = !(q.running.length || q.waiting_count || q.failed.length);
  const rows = [];
  q.running.forEach((j) => rows.push(row('running', j, 'Working', steps(j) + ' ' + esc(j.message || j.phase),
    ' data-since="' + esc(j.started_at) + '">', '')));
  q.waiting.forEach((j, i) => rows.push(row('waiting', j,
    '<span class="qpos">#' + (i + 1) + '</span>waiting',
    esc(j.message || (j.needs.length ? j.needs.map((n) => NEEDS[n] || n).join(' · ') : j.archived ? '' : 'download')), '>',
    '<button type="button" class="btn" data-act="remove">Remove</button>')));
  if (q.waiting_count > q.waiting.length) {
    rows.push('<div class="qmore">+ ' + (q.waiting_count - q.waiting.length) + ' more waiting · '
      + '<button type="button" class="btn" data-act="all-waiting">Show all</button></div>');
  } else if (allWaiting && q.waiting_count > 8) {
    rows.push('<div class="qmore"><button type="button" class="btn" data-act="few-waiting">Show fewer</button></div>');
  }
  q.failed.forEach((j) => rows.push(row('error', j, 'Failed', esc(j.error || 'failed'),
    '>' + esc(when(j.finished_at)),
    '<button type="button" class="btn" data-act="retry">Retry</button>'
    + '<button type="button" class="btn" data-act="dismiss">Dismiss</button>')));
  rowsEl.innerHTML = rows.join('');
  tick();
}
function tick() {
  rowsEl.querySelectorAll('.qtime[data-since]').forEach((el) => {
    const since = Date.parse(el.dataset.since);
    el.textContent = Number.isNaN(since) ? '' : clock((Date.now() - since) / 1000);
  });
}
setInterval(tick, 1000);
document.getElementById('queue').addEventListener('click', async (e) => {
  const button = e.target.closest('button[data-act]');
  const line = e.target.closest('.qrow');
  if (button && button.dataset.act.endsWith('-waiting') && button.dataset.act !== 'clear-waiting') {
    allWaiting = button.dataset.act === 'all-waiting';
    paintQueue();
    return;
  }
  if (!button) {
    if (line && line.classList.contains('error') && e.target.closest('.qdetail')) {
      const id = line.dataset.job;
      expanded.has(id) ? expanded.delete(id) : expanded.add(id);
      line.classList.toggle('expanded');
    }
    return;
  }
  button.disabled = true;
  const id = line ? encodeURIComponent(line.dataset.job) : '';
  try {
    if (button.dataset.act === 'clear') await post('/api/jobs/clear-waiting');
    if (button.dataset.act === 'remove') await post('/api/jobs/' + id + '/remove');
    if (button.dataset.act === 'dismiss') await post('/api/jobs/' + id + '/dismiss');
    if (button.dataset.act === 'retry') {
      const data = await post('/api/get', {url: line.dataset.url});
      if (data.status === 'complete') await post('/api/jobs/' + id + '/dismiss');
    }
  } catch (err) {
    status.replaceChildren();
    note(err.message, 'error');
  }
  paintQueue();
});
document.addEventListener('click', async (e) => {
  const button = e.target.closest('#update-archive');
  if (!button) return;
  button.disabled = true;
  const out = document.getElementById('update-status');
  out.textContent = 'queueing…';
  try {
    const data = await post('/api/update-archive');
    out.textContent = data.queued ? 'queued ' + data.queued : 'already queued';
  } catch (err) {
    out.textContent = err.message;
    button.disabled = false;
  }
  paintQueue();
});
async function refreshArchive() {
  const res = await fetch(window.location.pathname + window.location.search, {cache: 'no-store'});
  if (!res.ok) return;
  const page = new DOMParser().parseFromString(await res.text(), 'text/html');
  for (const id of ['archive-count', 'archive-health', 'archive-list']) {
    const fresh = page.getElementById(id), old = document.getElementById(id);
    if (fresh && old) old.replaceWith(fresh);
  }
  localizeTimes(document.getElementById('archive-list'));
}
async function paintQueue() {
  const res = await fetch('/api/jobs' + (allWaiting ? '?waiting=1000' : ''), {cache: 'no-store'});
  if (!res.ok) throw new Error(res.statusText);
  const q = await res.json();
  render(q);
  // A job starting, finishing, or being added or removed changes what the archive list shows.
  const open = q.running.map((j) => j.job_id).join(',') + '|' + q.waiting_count;
  if (openSeen !== null && open !== openSeen) refreshArchive();
  openSeen = open;
  return Boolean(q.running.length || q.waiting_count);
}
(async function loop() {
  const busy = await paintQueue().catch(() => false);
  setTimeout(loop, document.hidden ? 15000 : busy ? 1000 : 5000);
})();
"""
    return _page("ytarchive", body, js)


def creator_html(
    items: list[dict],
    *,
    creator_name: str,
    channel_id: str = "",
    channel: str = "",
    query: str = "",
    sort: str = "recent",
    total_count: int,
    data_dir: Path,
    busy: frozenset = frozenset(),
) -> bytes:
    sort = normalize_video_sort(sort)
    identity = (
        f'<input type="hidden" name="channel_id" value="{_esc(channel_id)}">'
        if channel_id
        else f'<input type="hidden" name="channel" value="{_esc(channel)}">'
    )
    sort_options = "".join(
        f'<option value="{value}"{" selected" if value == sort else ""}>{label}</option>'
        for value, label in SORT_OPTIONS
    )
    youtube_channel = (
        f' · <a href="https://www.youtube.com/channel/{_esc(channel_id)}">YouTube channel</a>'
        f' · <a href="/browse?{_esc(urlencode({"source": channel_id}))}">Browse more from this creator</a>'
        if channel_id
        else ""
    )
    shown = (
        f'<strong>{len(items)}</strong> of <strong>{total_count}</strong> videos'
        if query
        else f'<strong>{total_count}</strong> videos'
    )
    empty = f"No videos match “{_esc(query)}”."
    body = f"""
<header>
  <div><a href="/">← archive</a></div>
  <h1>{_esc(creator_name)}</h1>
  <div class="creator-count">{shown}</div>
  <div class="meta">{_esc(channel_id)}{youtube_channel}</div>
</header>
<main>
  <h2>Videos by this creator</h2>
  <form class="archive-tools" method="get" action="/creator">
    {identity}
    <div class="find">
      <input name="q" value="{_esc(query)}" placeholder="search this creator’s videos and transcripts…" autocomplete="off">
      <button type="submit">Search</button>
    </div>
    <label class="sort-field"><span>Sort videos</span>
      <select name="sort" onchange="this.form.submit()">{sort_options}</select>
    </label>
  </form>
  {_video_list_html(items, empty, show_creator=False, busy=busy)}
</main>
"""
    return _page(f"{creator_name} · ytarchive", body, LOCAL_TIMES_JS)


def browse_creator_html(source: str = "") -> bytes:
    """The picker (docs/browsing.md, "Browse YouTube"); static/creators.js fills it in."""
    body = f"""
<header>
  <p><a href="/">← archive and download queue</a></p>
  <h1>Browse YouTube</h1>
  <p class="sub">Paste a creator’s homepage or @handle to go through their videos, Shorts, live streams,
  and playlists, or paste a playlist link. Only the list is read; nothing downloads until you choose.
  Your <a href="{_esc(browse_url('https://www.youtube.com/playlist?list=WL'))}">Watch later</a>,
  <a href="{_esc(browse_url('https://www.youtube.com/playlist?list=LL'))}">Liked videos</a>,
  and private playlists are read with your YouTube account.</p>
  <form class="get" id="creator-form">
    <input id="creator-source" aria-label="YouTube creator or playlist" value="{_esc(source)}"
      placeholder="creator homepage, @handle, channel ID, or playlist link" required autocomplete="off">
    <button id="creator-load" type="submit">Browse</button>
  </form>
  <div id="creator-status" role="status" aria-live="polite"></div>
</header>
<main id="creator-picker" data-downloads-per-hour="{budget.LIMITS['download']}" hidden>
  <div class="pick-head">
    <h2 id="creator-title"></h2>
    <span id="creator-kind" class="pick-kind"></span>
    <a id="creator-owner" hidden></a>
    <a id="creator-youtube" target="_blank" rel="noopener noreferrer">open on YouTube ↗</a>
    <span id="creator-listed"></span>
    <button id="creator-refresh" type="button" class="btn">Refresh list</button>
  </div>
  <p id="creator-warning" role="status"></p>
  <nav id="creator-tabs" class="pick-tabs" aria-label="What to show"></nav>
  <section id="creator-videos" hidden>
    <div class="pick-tools">
      <input id="creator-filter" type="search" aria-label="Filter by title or video ID"
        placeholder="filter titles or video IDs…" autocomplete="off">
      <label class="pick-field"><span>Length</span><select id="creator-length">
        <option value="">any length</option>
        <option value="-60">under 1 min</option>
        <option value="60-300">1–5 min</option>
        <option value="300-1200">5–20 min</option>
        <option value="1200-3600">20–60 min</option>
        <option value="3600-">over 1 hour</option>
      </select></label>
      <label class="pick-field"><span>Uploaded</span><select id="creator-age">
        <option value="">any time</option>
        <option value="-7">past week</option>
        <option value="-31">past month</option>
        <option value="-92">past 3 months</option>
        <option value="-366">past year</option>
        <option value="366-">over a year ago</option>
      </select></label>
      <label class="pick-field"><span>Sort</span><select id="creator-sort">
        <option value="">YouTube’s order</option>
        <option value="new">newest first</option>
        <option value="old">oldest first</option>
        <option value="long">longest first</option>
        <option value="short">shortest first</option>
        <option value="views">most viewed</option>
        <option value="title">title A–Z</option>
      </select></label>
      <label class="pick-check"><input id="creator-hide" type="checkbox"> hide saved and queued</label>
    </div>
    <div class="pick-bar">
      <span class="pick-sel"><strong id="creator-selected-count">0</strong> selected<span id="creator-offview"></span></span>
      <button id="creator-get-selected" type="button" class="primary">Get selected</button>
      <button id="creator-get-shown" type="button">Get all shown</button>
      <button id="creator-one-start" type="button">Go one by one</button>
      <span class="pick-gap"></span>
      <button id="creator-select-shown" type="button">Select all shown</button>
      <span class="pick-first"><button id="creator-select-first" type="button">Select first</button>
        <input id="creator-first" type="number" min="1" max="99999" value="10" aria-label="How many to select"></span>
      <button id="creator-clear" type="button">Clear</button>
    </div>
    <p id="creator-count" class="pick-count"></p>
    <div id="creator-items" class="pick-list"></div>
    <nav class="pick-pager" aria-label="Pages">
      <button id="creator-prev" type="button">← previous</button>
      <span id="creator-page"></span>
      <button id="creator-next" type="button">next →</button>
    </nav>
    <p class="pick-keys"><kbd>j</kbd> <kbd>k</kbd> or arrows move · <kbd>x</kbd> or <kbd>space</kbd> selects ·
      shift-click or <kbd>shift</kbd>+<kbd>x</kbd> selects a range · <kbd>o</kbd> opens on YouTube</p>
  </section>
  <section id="creator-one" class="pick-one" hidden>
    <img id="creator-one-thumb" alt="" referrerpolicy="no-referrer">
    <div class="one-info">
      <div class="one-pos"><strong id="creator-one-index"></strong> of <strong id="creator-one-total"></strong>
        <span id="creator-one-chosen"></span></div>
      <h3 id="creator-one-title"></h3>
      <div id="creator-one-facts" class="one-facts"></div>
      <div class="one-actions">
        <button id="creator-one-choose" type="button" class="primary">Choose <kbd>y</kbd></button>
        <button id="creator-one-skip" type="button">Skip <kbd>n</kbd></button>
        <button id="creator-one-back" type="button">Back <kbd>b</kbd></button>
        <button id="creator-one-done" type="button">Back to the list <kbd>esc</kbd></button>
      </div>
      <a id="creator-one-youtube" target="_blank" rel="noopener noreferrer">watch on YouTube ↗</a>
    </div>
  </section>
  <section id="creator-playlists" hidden>
    <p id="creator-playlists-status" role="status"></p>
    <div id="creator-playlist-items" class="pick-list"></div>
  </section>
</main>
"""
    return _page(
        "Browse YouTube · ytarchive", body,
        extra_head=f'<link rel="stylesheet" href="{_static_url("creators.css")}">',
        extra_tail=f'<script src="{_static_url("creators.js")}"></script>',
    )


def add_html(url: str, title: str, authorized: bool) -> bytes:
    """The bookmarklet's pop-up (docs/bookmarklet.md). Only a matching key queues anything."""
    if not authorized:
        body = """
<main class="add error">
  <h1>Not queued: bookmarklet key doesn’t match</h1>
  <p>Drag a fresh <strong>+ ytarchive</strong> button from the
  <a href="/" target="_blank">archive page</a> to your bookmarks bar and delete the old one.</p>
</main>
"""
        return _page("ytarchive", body)
    title = re.sub(r"^\(\d+\)\s*", "", title).removesuffix(" - YouTube").strip()
    body = f"""
<main class="add" id="add" data-url="{_esc(url)}">
  <h1 id="add-status">Queuing…</h1>
  <p class="add-title">{_esc(title or url)}</p>
  <p class="meta" id="add-detail"></p>
  <p class="meta"><a href="/" target="_blank">archive and download queue</a></p>
</main>
"""
    js = r"""
const box = document.getElementById('add');
const heading = document.getElementById('add-status');
const detail = document.getElementById('add-detail');
(async () => {
  try {
    const res = await fetch('/api/get', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url: box.dataset.url}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    if (data.browse_url) {
      window.resizeTo(Math.min(screen.availWidth, 1280), screen.availHeight);
      location.replace(data.browse_url);
      return;
    }
    box.classList.add('done');
    heading.textContent = data.status === 'complete' ? 'Already archived'
      : data.existing ? 'Already in the queue' : 'Queued';
    detail.textContent = (data.title || data.video_id) + ' · this window closes itself';
    setTimeout(() => window.close(), 1500);
  } catch (err) {
    box.classList.add('error');
    heading.textContent = 'Not queued';
    detail.textContent = err.message;
  }
})();
"""
    return _page("ytarchive", body, js)


def _facts_html(info: dict, creator_link: str, shots: int) -> str:
    """The video's key facts on one line, values first in weight (docs/browsing.md)."""
    vid = info["video_id"]
    facts = [creator_link]
    if _clock(info.get("duration")):
        facts.append(f'<span><b>{_esc(_clock(info.get("duration")))}</b></span>')
    if info.get("width") and info.get("height"):
        extra = " · ".join(
            str(part) for part in (
                f'{round(float(info["fps"]))} fps' if info.get("fps") else "",
                str(info.get("vcodec") or "").split(".")[0],
            ) if part
        )
        facts.append(f'<span><b>{int(info["width"])}×{int(info["height"])}</b>{_esc(extra)}</span>')
    if _size(info.get("file_size")):
        facts.append(f'<span><b>{_esc(_size(info.get("file_size")))}</b></span>')
    if shots:
        facts.append(f"<span><b>{shots}</b>shots</span>")
    if info.get("upload_date"):
        facts.append(f'<span>uploaded <b>{_esc(_upload_date(info["upload_date"]))}</b></span>')
    if info.get("downloaded_at"):
        facts.append(f'<span>got <b>{_time_html(info["downloaded_at"])}</b></span>')
    facts.append(f"<span><code>{_esc(vid)}</code></span>")
    facts.append(f'<a href="{_esc(watch_url(vid))}" target="_blank" rel="noopener noreferrer">YouTube</a>')
    return f'<div class="facts">{"".join(facts)}</div>'


def detail_html(info: dict, data_dir: Path, job: dict | None = None) -> bytes:
    vid = info["video_id"]
    title = info.get("title") or vid
    duration = info.get("duration")
    video = find_video_file(data_dir, vid)
    transcript_file = transcript_json_path(data_dir, vid)
    captions_file = transcript_vtt_path(data_dir, vid)
    has_transcript = transcript_is_complete(data_dir, vid)
    transcript_language = "und"
    if has_transcript:
        try:
            transcript_language = (
                json.loads(transcript_file.read_text(encoding="utf-8")).get(
                    "language"
                )
                or "und"
            )
        except (OSError, json.JSONDecodeError):
            has_transcript = False
    sheets = framesheet_paths(data_dir, vid)
    shot_recs = (read_shots_json(data_dir, vid) or {}).get("shots", [])
    kept = [r for r in shot_recs if r.get("file") and r.get("kept") is not False]
    # Each kept shot's start is a marker on the player's scale; its id is the shot image's file,
    # so the page can light that image while the shot plays.
    shot_markers = [
        {"t": r["t0"], "label": f"Shot {Path(r['file']).stem}", "id": r["file"]}
        for r in kept
        if r.get("t0") is not None
    ]
    player_head, player_tail = _media_player_includes()
    creator_url = _creator_url(info)
    creator_name = _esc(info.get("channel") or "unknown uploader")
    creator_link = (
        f'<a class="creator-link" href="{_esc(creator_url)}">{creator_name}</a>'
        if creator_url
        else creator_name
    )
    parts = [
        '<header><div><a href="/">← archive</a></div>',
        f"<h1>{_esc(title)}</h1>",
        _facts_html(info, creator_link, len(kept)),
    ]
    if job:
        doing = job.get("message") or ("waiting in the queue" if job.get("status") == "queued" else job.get("phase"))
        parts.append(
            f'<div class="banner"><strong>Being updated now</strong>{_esc(doing)} · '
            'this page shows the files as they are; reload when the '
            '<a href="/#queue">download queue</a> says it is done.</div>'
        )
    if info.get("description"):
        parts.append(
            '<details class="description"><summary>Description</summary>'
            f'<div class="text">{_esc(info["description"])}</div></details>'
        )
    parts.append("</header><main>")
    if video:
        markers_attr = _esc(json.dumps(shot_markers, separators=(",", ":")))
        dur_attr = f' data-duration="{duration}"' if duration else ""
        captions_track = (
            f'<track kind="captions" srclang="{_esc(transcript_language)}" label="Local transcript" '
            f'src="{_esc(media_url(data_dir, captions_file))}">'
            if has_transcript
            else ""
        )
        parts.append(f"""
<div class="ytp" id="ytp" data-id="{_esc(vid)}">
  <video controls preload="metadata" src="{_esc(media_url(data_dir, video))}" data-media-player
    data-title="{_esc(title)}"{dur_attr} data-markers="{markers_attr}" data-markers-label="Shot"
    data-wide>{captions_track}</video>
</div>
<p class="meta">click a shot to play from it, then PageUp and PageDown step through shots · S saves this frame next to the MP3 · T widens the page ·
shift-click a shot to open its PNG · ? lists every key</p>
""")
    mp3 = soundtrack_path(data_dir, vid)
    soundtrack_bits = ['<div class="soundtrack">']
    if mp3.is_file():
        soundtrack_bits.append(
            f'<h2>MP3 audio</h2>'
            f'<p class="meta"><a href="{_esc(media_url(data_dir, mp3))}" download>download MP3 audio</a></p>'
            f'<audio controls preload="metadata" src="{_esc(media_url(data_dir, mp3))}" data-media-player '
            f'data-title="{_esc(f"MP3 audio: {title}")}"></audio>'
        )
    else:
        soundtrack_bits.append('<h2>MP3 audio</h2>')
    no_audio = info.get("audio_stream") is False
    can_dump = video and not mp3.is_file() and not no_audio
    if no_audio and not mp3.is_file():
        soundtrack_bits.append('<p class="meta">This video has no audio track, so there is no MP3.</p>')
    row = ['<div class="row">']
    if can_dump:
        row.append('<button type="button" class="btn" id="dump-audio">Create MP3 audio</button>')
    row.append(
        '<button type="button" class="btn" id="open-mp3-folder">Open MP3 folder</button>'
    )
    row.append("</div>")
    if can_dump:
        row.append('<div id="audio-status"></div>')
    soundtrack_bits.extend(row)
    soundtrack_bits.append("</div>")
    soundtrack_html = "".join(soundtrack_bits)
    transcript_action = "Re-transcribe…" if has_transcript else "Transcribe audio…"
    transcript_ready = (
        f"""
  <div class="transcript-meta" id="transcript-meta">Loading transcript…</div>
  <div class="transcript-controls">
    <input class="transcript-search" id="transcript-search" type="search"
      placeholder="search every spoken word…" autocomplete="off">
    <label>View
      <select id="transcript-view">
        <option value="full">full transcript</option>
        <option value="rolling">current caption only</option>
      </select>
    </label>
    <label>Text size
      <select id="transcript-size">
        <option value="small">small</option>
        <option value="medium" selected>medium</option>
        <option value="large">large</option>
      </select>
    </label>
    <button type="button" id="toggle-cc">Enable video CC</button>
    <a href="{_esc(media_url(data_dir, captions_file))}" download>download WebVTT</a>
  </div>
  <div class="caption-box size-medium" id="caption-box" aria-live="polite"></div>
  <div class="speaker-tools">
    <strong>Speaker groups</strong>
    <label>Rename
      <select id="speaker-old"><option value="">choose speaker</option></select>
    </label>
    <label>to
      <input id="speaker-new" maxlength="80" placeholder="name">
    </label>
    <button type="button" id="rename-speaker">Rename/group</button>
    <span class="meta">Click a segment’s speaker label to assign it.</span>
  </div>
"""
        if has_transcript
        else ""
    )
    parts.append(
        f"""
<section class="transcript" id="transcript-panel" data-ready="{str(has_transcript).lower()}">
  <h2>Spoken transcript</h2>
  <p class="meta">Local Whisper · speech only; sound-effect descriptions are not mixed into spoken words.</p>
  <div class="actions">
    <button type="button" id="open-transcribe">{transcript_action}</button>
  </div>
  <div class="transcript-progress" id="transcript-progress" hidden>
    <span class="transcript-progress-value" id="transcript-progress-value">0%</span>
    <span class="transcript-progress-label" id="transcript-progress-label">queued</span>
    <div class="transcript-progress-track"><div class="transcript-progress-fill" id="transcript-progress-fill"></div></div>
  </div>
  {transcript_ready}
</section>
<dialog id="transcribe-dialog">
  <h2>{'Replace transcript' if has_transcript else 'Generate transcript'}</h2>
  <p class="meta">The default uses the highest-quality Whisper model this machine can run. Nothing starts until you confirm.</p>
  <div class="fields">
    <label>Model
      <select id="whisper-model">
        <option value="large-v3" selected>large-v3 — highest quality</option>
        <option value="large-v3-turbo">large-v3-turbo — faster</option>
        <option value="distil-large-v3">distil-large-v3</option>
        <option value="medium">medium</option>
        <option value="small">small</option>
      </select>
    </label>
    <label>Language
      <input id="whisper-language" maxlength="3" placeholder="auto-detect, or en / fi / de">
    </label>
    <label>Beam size
      <select id="whisper-beam">
        <option value="1">1 — fastest</option>
        <option value="3">3</option>
        <option value="5" selected>5 — default</option>
        <option value="10">10 — thorough</option>
      </select>
    </label>
    <label><span>Speech filtering</span>
      <span><input type="checkbox" id="whisper-vad" checked> skip silence/non-speech</span>
    </label>
  </div>
  <div class="dialog-actions">
    <button type="button" id="cancel-transcribe">Cancel</button>
    <button type="button" id="configured-transcribe">Use these settings</button>
    <button type="button" class="primary" id="default-transcribe">{'Replace with defaults' if has_transcript else 'Yes — transcribe with defaults'}</button>
  </div>
</dialog>
"""
    )
    parts.append(soundtrack_html)
    if video and not sheets:
        parts.append('<p class="sheet-label">No framesheet yet — the next job for this video makes one.</p>')
    for n, sheet in enumerate(sheets, start=1):
        label = (
            f"Squished framesheet {n}/{len(sheets)}"
            if len(sheets) > 1
            else "Squished framesheet"
        )
        parts.append(
            f'<p class="sheet-label">{_esc(label)}</p>'
            f'<a href="{_esc(media_url(data_dir, sheet))}" target="_blank" rel="noopener">'
            f'<img class="sheet" src="{_esc(media_url(data_dir, sheet))}" alt="{_esc(label)}"></a>'
        )
    labeled = all_labeled_path(data_dir, vid)
    if labeled.is_file():
        parts.append(
            f'<p class="sheet-label">All detections (kept + dropped)</p>'
            f'<a href="{_esc(media_url(data_dir, labeled))}" target="_blank" rel="noopener">'
            f'<img class="sheet" src="{_esc(media_url(data_dir, labeled))}" alt="all shots labeled"></a>'
        )
    times = {r["file"]: r for r in kept}
    figs = []
    for shot in sorted(shots_dir(data_dir, vid).glob("*.png")):
        rec = times.get(shot.name) or {}
        t0 = rec.get("t0")
        sample = rec.get("sample", t0)
        cap = shot.stem + (f" @ {sample:.1f}s" if sample is not None else "")
        data_t = f' data-t="{sample}"' if sample is not None else ""
        figs.append(
            f'<figure data-file="{_esc(shot.name)}"{data_t}>'
            f'<a href="{_esc(media_url(data_dir, shot))}">'
            f'<img src="{_esc(media_url(data_dir, shot))}" alt="{shot.stem}"></a>'
            f"<figcaption>{cap}</figcaption></figure>"
        )
    if figs:
        parts.append(
            f'<p class="sheet-label">Shot images — click to play from that moment</p>'
            f'<div class="shots">{"".join(figs)}</div>'
        )
    parts.append(
        """
<div class="backup">
  <h2>Redo</h2>
  <p>If a piece came out wrong: rebuild the shots and framesheet from the local video, or download the video again.</p>
  <button type="button" class="btn" id="reget-png">Rebuild shots</button>
  <button type="button" class="btn" id="reget-video">Download again</button>
  <div id="reget-status"></div>
</div>
"""
    )
    parts.append("</main>")
    reget_js = LOCAL_TIMES_JS + r"""
const vid = """ + json.dumps(vid) + r""";
const STEP_NAMES = {download: 'downloading', shots: 'making shots', audio: 'making MP3 audio', queued: 'waiting in the queue'};
async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
async function watchJob(jobId, status) {
  for (;;) {
    const res = await fetch('/api/job/' + encodeURIComponent(jobId), {cache: 'no-store'});
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || res.statusText);
    if (j.status === 'done') {
      status.className = 'done';
      status.textContent = (j.message || 'done') + ' — reloading';
      location.reload();
      return true;
    }
    if (j.status === 'error') throw new Error(j.error || 'failed');
    const pct = j.progress != null ? Math.floor(j.progress * 100) + '% · ' : '';
    status.textContent = (STEP_NAMES[j.phase] || j.phase) + ' · ' + pct + (j.message || '');
    await new Promise((r) => setTimeout(r, 1000));
  }
}
async function reget(kind) {
  const ok = confirm(kind === 'video'
    ? 'Download this video from YouTube again and rebuild its shots and framesheet?'
    : 'Rebuild the shot images and framesheet from the local video?');
  if (!ok) return;
  const png = document.getElementById('reget-png');
  const video = document.getElementById('reget-video');
  const status = document.getElementById('reget-status');
  png.disabled = video.disabled = true;
  status.className = '';
  status.textContent = 'starting…';
  const body = kind === 'video'
    ? {url: vid, force_video: true, force_shots: true}
    : {url: vid, force_shots: true};
  try {
    const data = await post('/api/get', body);
    await watchJob(data.job_id, status);
  } catch (err) {
    status.className = 'error';
    status.textContent = err.message;
    png.disabled = video.disabled = false;
  }
}
document.getElementById('reget-png').onclick = () => reget('png');
document.getElementById('reget-video').onclick = () => reget('video');
const dumpBtn = document.getElementById('dump-audio');
if (dumpBtn) dumpBtn.onclick = async () => {
  dumpBtn.disabled = true;
  const status = document.getElementById('audio-status');
  status.className = '';
  status.textContent = 'starting…';
  try {
    const data = await post('/api/get', {url: vid});
    if (data.status === 'complete') { location.reload(); return; }
    await watchJob(data.job_id, status);
  } catch (err) {
    status.className = 'error';
    status.textContent = err.message;
    dumpBtn.disabled = false;
  }
};
const openBtn = document.getElementById('open-mp3-folder');
if (openBtn) openBtn.onclick = async () => {
  openBtn.disabled = true;
  try {
    const res = await fetch('/api/open-folder', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({video_id: vid}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
  } catch (err) {
    alert(err.message);
  }
  openBtn.disabled = false;
};

const transcribeDialog = document.getElementById('transcribe-dialog');
const transcribeProgress = document.getElementById('transcript-progress');
const transcribeValue = document.getElementById('transcript-progress-value');
const transcribeLabel = document.getElementById('transcript-progress-label');
const transcribeFill = document.getElementById('transcript-progress-fill');
const transcriptReady = document.getElementById('transcript-panel').dataset.ready === 'true';
document.getElementById('open-transcribe').onclick = () => transcribeDialog.showModal();
document.getElementById('cancel-transcribe').onclick = () => transcribeDialog.close();
document.getElementById('default-transcribe').onclick = () => startTranscription({
  model: 'large-v3', language: '', beam_size: 5, vad_filter: true,
});
document.getElementById('configured-transcribe').onclick = () => startTranscription({
  model: document.getElementById('whisper-model').value,
  language: document.getElementById('whisper-language').value.trim(),
  beam_size: Number(document.getElementById('whisper-beam').value),
  vad_filter: document.getElementById('whisper-vad').checked,
});

async function startTranscription(config) {
  transcribeDialog.close();
  document.getElementById('open-transcribe').disabled = true;
  transcribeProgress.hidden = false;
  transcribeProgress.className = 'transcript-progress';
  setTranscribeProgress(0, 'queueing local Whisper…');
  try {
    const res = await fetch('/api/transcribe', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({video_id: vid, config, force: transcriptReady}),
    });
    const job = await res.json();
    if (!res.ok) throw new Error(job.error || res.statusText);
    for (;;) {
      const jres = await fetch('/api/work/' + job.job_id);
      const current = await jres.json();
      if (!jres.ok) throw new Error(current.error || jres.statusText);
      setTranscribeProgress(
        current.progress || 0,
        current.message || current.status
      );
      if (current.status === 'done') {
        setTranscribeProgress(1, 'transcript ready — reloading');
        setTimeout(() => location.reload(), 500);
        return;
      }
      if (current.status === 'error') {
        throw new Error(current.error || 'transcription failed');
      }
      await new Promise(resolve => setTimeout(resolve, 900));
    }
  } catch (err) {
    transcribeProgress.classList.add('error');
    transcribeLabel.textContent = err.message;
    document.getElementById('open-transcribe').disabled = false;
  }
}

function setTranscribeProgress(value, label) {
  const pct = Math.max(0, Math.min(100, Math.round(Number(value || 0) * 100)));
  transcribeValue.textContent = pct + '%';
  transcribeLabel.textContent = label;
  transcribeFill.style.width = pct + '%';
}

function html(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function clock(t) {
  t = Math.max(0, Math.floor(Number(t) || 0));
  const h = Math.floor(t / 3600);
  const m = Math.floor(t / 60) % 60;
  const s = t % 60;
  return h ? h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0')
    : m + ':' + String(s).padStart(2, '0');
}

let transcript = null;
let activeSegment = -1;
async function loadTranscript() {
  const res = await fetch('/api/transcript/' + encodeURIComponent(vid));
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  transcript = data;
  paintTranscript();
  paintTranscriptSummary();
  paintSpeakerOptions();
}

function paintTranscriptSummary() {
  if (!transcript) return;
  const probability = Math.round((transcript.language_probability || 0) * 100);
  document.getElementById('transcript-meta').textContent =
    transcript.segments.length + ' spoken segments · '
    + (transcript.language || 'unknown language')
    + (probability ? ' ' + probability + '%' : '') + ' · ' + transcript.model;
}

function segmentHtml(segment, matched, hidden) {
  const words = (segment.words || []).length
    ? segment.words.map((word, index) => {
        const gap = index && !/^[,.;:!?)]/.test(word.word) ? ' ' : '';
        return gap + '<span class="word" data-t="'
          + Number(word.start || segment.start) + '">' + html(word.word) + '</span>';
      }).join('')
    : html(segment.text);
  return '<div class="segment' + (matched ? ' match' : '') + '"'
    + (hidden ? ' hidden' : '') + ' data-index="' + segment.index + '" data-start="'
    + Number(segment.start) + '" data-end="' + Number(segment.end) + '">'
    + '<span class="segment-time">' + clock(segment.start) + '</span>'
    + '<button type="button" class="segment-speaker" title="assign or group speaker: '
    + html(segment.speaker || 'Unassigned') + '">' + html(segment.speaker || 'Unassigned') + '</button>'
    + '<div class="segment-text">' + words + '</div></div>';
}

function paintTranscript() {
  const box = document.getElementById('caption-box');
  if (!box || !transcript) return;
  const query = document.getElementById('transcript-search').value.trim().toLowerCase();
  let shown = 0;
  box.innerHTML = transcript.segments.map(segment => {
    const match = !query || segment.text.toLowerCase().includes(query)
      || (segment.speaker || '').toLowerCase().includes(query);
    if (match) shown += 1;
    return segmentHtml(segment, Boolean(query && match), !match);
  }).join('') || '<div class="transcript-empty">No spoken words found.</div>';
  if (query) {
    document.getElementById('transcript-meta').textContent =
      shown + ' matching segment' + (shown === 1 ? '' : 's');
  } else paintTranscriptSummary();
  bindTranscriptRows();
  activeSegment = -1;
  syncTranscript();
}

function bindTranscriptRows() {
  const playerVideo = document.querySelector('#ytp video');
  document.querySelectorAll('.segment').forEach(row => {
    row.addEventListener('click', event => {
      if (event.target.closest('.segment-speaker')) return;
      const word = event.target.closest('.word');
      const time = word ? Number(word.dataset.t) : Number(row.dataset.start);
      if (playerVideo) {
        playerVideo.currentTime = time;
        playerVideo.play();
      }
    });
    row.querySelector('.segment-speaker').addEventListener('click', async event => {
      event.stopPropagation();
      const index = Number(row.dataset.index);
      const segment = transcript.segments.find(item => item.index === index);
      const current = segment ? segment.speaker || '' : '';
      const speaker = prompt(
        'Speaker name. Use the same name on other segments to group them.',
        current
      );
      if (speaker == null) return;
      await changeSegmentSpeaker(index, speaker);
    });
  });
}

async function changeSegmentSpeaker(index, speaker) {
  const res = await fetch('/api/transcript/speaker', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({video_id: vid, segment_index: index, speaker}),
  });
  const data = await res.json();
  if (!res.ok) return alert(data.error || res.statusText);
  transcript = data;
  paintTranscript();
  paintSpeakerOptions();
}

function paintSpeakerOptions() {
  const select = document.getElementById('speaker-old');
  if (!select || !transcript) return;
  const speakers = [...new Set(transcript.segments.map(s => s.speaker).filter(Boolean))].sort();
  select.innerHTML = '<option value="">choose speaker</option>'
    + speakers.map(s => '<option value="' + html(s) + '">' + html(s) + '</option>').join('');
}

const renameSpeaker = document.getElementById('rename-speaker');
if (renameSpeaker) renameSpeaker.onclick = async () => {
  const oldName = document.getElementById('speaker-old').value;
  const newName = document.getElementById('speaker-new').value.trim();
  if (!oldName || !newName) return;
  const res = await fetch('/api/transcript/rename-speaker', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({video_id: vid, old: oldName, new: newName}),
  });
  const data = await res.json();
  if (!res.ok) return alert(data.error || res.statusText);
  transcript = data;
  document.getElementById('speaker-new').value = '';
  paintTranscript();
  paintSpeakerOptions();
};

function syncTranscript() {
  const playerVideo = document.querySelector('#ytp video');
  if (!playerVideo || !transcript) return;
  const time = playerVideo.currentTime || 0;
  const index = transcript.segments.findIndex(
    segment => time >= segment.start && time < segment.end
  );
  if (index === activeSegment) return;
  activeSegment = index;
  document.querySelectorAll('.segment').forEach(row => {
    row.classList.toggle('now', Number(row.dataset.index) === index);
  });
  const current = document.querySelector('.segment.now');
  if (current && !document.getElementById('transcript-search').value) {
    current.scrollIntoView({block: 'nearest'});
  }
}

const transcriptSearch = document.getElementById('transcript-search');
if (transcriptSearch) transcriptSearch.addEventListener('input', paintTranscript);
const transcriptView = document.getElementById('transcript-view');
if (transcriptView) {
  transcriptView.value = localStorage.getItem('ytarchive.transcript.view') || 'full';
  transcriptView.onchange = () => {
    localStorage.setItem('ytarchive.transcript.view', transcriptView.value);
    document.getElementById('caption-box').classList.toggle('rolling', transcriptView.value === 'rolling');
    syncTranscript();
  };
  transcriptView.onchange();
}
const transcriptSize = document.getElementById('transcript-size');
if (transcriptSize) {
  transcriptSize.value = localStorage.getItem('ytarchive.transcript.size') || 'medium';
  transcriptSize.onchange = () => {
    localStorage.setItem('ytarchive.transcript.size', transcriptSize.value);
    const box = document.getElementById('caption-box');
    box.classList.remove('size-small', 'size-medium', 'size-large');
    box.classList.add('size-' + transcriptSize.value);
  };
  transcriptSize.onchange();
}
const playerVideo = document.querySelector('#ytp video');
if (playerVideo) playerVideo.addEventListener('timeupdate', syncTranscript);
const toggleCc = document.getElementById('toggle-cc');
if (toggleCc && playerVideo) {
  const track = playerVideo.textTracks[0];
  const paintCc = () => toggleCc.textContent =
    track && track.mode === 'showing' ? 'Disable video CC' : 'Enable video CC';
  toggleCc.onclick = () => {
    const player = window.MediaPlayer && MediaPlayer.get(playerVideo);
    if (player) player.toggleCaptions();
    else if (track) track.mode = track.mode === 'showing' ? 'hidden' : 'showing';
    paintCc();
  };
  document.getElementById('ytp').addEventListener('media-player:captions', paintCc);
  paintCc();
}
if (transcriptReady) loadTranscript().catch(err => {
  document.getElementById('transcript-meta').textContent = err.message;
});
"""
    return _page(
        title,
        "".join(parts),
        extra_js=reget_js,
        extra_head=f'{player_head}<link rel="stylesheet" href="{_esc(_static_url("video.css"))}">',
        extra_tail=f'{player_tail}<script src="{_esc(_static_url("video.js"))}"></script>',
    )


def _byte_range(header: str | None, size: int):
    """(start, end) for a single "bytes=" range, "unsatisfiable", or None to send it all."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not match or not any(match.groups()):
        return None
    first, last = match.groups()
    if not first:
        length = int(last)
        if length == 0 or size == 0:
            return "unsatisfiable"
        return max(0, size - length), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        return "unsatisfiable"
    return start, end


def make_handler(data_dir: Path, queue: JobQueue, work_queue: DerivedWorkQueue):
    creators = CreatorBrowser(data_dir, queue)
    key = bookmarklet_key(data_dir).encode("ascii")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f"{self.address_string()} {fmt % args}", flush=True)

        def do_GET(self):
            # A DNS-rebinding page reaches us under its own Host name; it gets nothing.
            if not _is_local_host(self.headers.get("Host") or ""):
                return self._json({"error": "untrusted Host header"}, 403)
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/browse":
                return self._bytes(browse_creator_html((parse_qs(parsed.query).get("source") or [""])[0]))
            if path.startswith("/api/creators/"):
                listing = creators.get(path.removeprefix("/api/creators/"))
                if not listing:
                    return self._json({"error": "This list is no longer kept. Browse it again."}, 404)
                return self._json(listing)
            if path == "/":
                params = parse_qs(parsed.query)
                q = (params.get("q") or [""])[0].strip()
                sort = normalize_video_sort((params.get("sort") or ["recent"])[0])
                return self._bytes(
                    home_html(
                        list_items(data_dir, q, sort), q, data_dir, sort,
                        bookmarklet=key.decode("ascii"),
                        health=db.archive_needs_summary(data_dir),
                        busy=frozenset(db.open_video_ids(data_dir)),
                        total=db.count_videos(data_dir),
                    )
                )
            if path == "/add":
                params = parse_qs(parsed.query)
                given = (params.get("key") or [""])[0].encode("utf-8")
                authorized = bool(key) and hmac.compare_digest(given, key)
                return self._bytes(
                    add_html(
                        (params.get("url") or [""])[0].strip()[:2048],
                        (params.get("title") or [""])[0].strip()[:300],
                        authorized,
                    ),
                    200 if authorized else 403,
                )
            if path == "/creator":
                params = parse_qs(parsed.query)
                channel_id = (params.get("channel_id") or [""])[0].strip()[:200]
                channel = (params.get("channel") or [""])[0].strip()[:300]
                q = (params.get("q") or [""])[0].strip()
                sort = normalize_video_sort((params.get("sort") or ["recent"])[0])
                all_items = list_creator_videos(
                    data_dir, channel_id=channel_id, channel=channel, sort=sort
                )
                if not all_items:
                    return self._bytes(
                        _page(
                            "creator not found",
                            '<main><p><a href="/">← archive</a></p>'
                            "<h1>Creator not found</h1></main>",
                        ),
                        404,
                    )
                items = (
                    list_creator_videos(
                        data_dir,
                        channel_id=channel_id,
                        channel=channel,
                        query=q,
                        sort=sort,
                    )
                    if q
                    else all_items
                )
                creator_name = all_items[0].get("channel") or channel or channel_id
                return self._bytes(
                    creator_html(
                        items,
                        creator_name=creator_name,
                        channel_id=channel_id,
                        channel=channel,
                        query=q,
                        sort=sort,
                        total_count=len(all_items),
                        data_dir=data_dir,
                        busy=frozenset(db.open_video_ids(data_dir)),
                    )
                )
            if path == "/api/list":
                params = parse_qs(parsed.query)
                q = (params.get("q") or [""])[0].strip()
                sort = normalize_video_sort((params.get("sort") or ["recent"])[0])
                return self._json(list_items(data_dir, q, sort))
            if path == "/api/jobs":
                params = parse_qs(parsed.query)
                try:
                    limit = min(1000, int((params.get("waiting") or [db.WAITING_SHOWN])[0]))
                except ValueError:
                    limit = db.WAITING_SHOWN
                return self._json(db.queue_overview(data_dir, limit))
            if path.startswith("/api/job/"):
                job = queue.get(path.split("/", 3)[-1])
                if not job:
                    return self._json({"error": "unknown job"}, 404)
                return self._json(queue.snapshot(job))
            if path.startswith("/api/work/"):
                job = work_queue.get(path.split("/", 3)[-1])
                if not job:
                    return self._json({"error": "unknown derived job"}, 404)
                return self._json(job)
            if path.startswith("/api/transcript/"):
                from .db import get_transcript

                try:
                    video_id = parse_video_id(path.split("/", 3)[-1])
                except ValueError:
                    return self._json({"error": "bad id"}, 404)
                transcript = get_transcript(data_dir, video_id)
                if not transcript:
                    return self._json({"error": "no transcript"}, 404)
                return self._json(transcript)
            if path.startswith("/v/"):
                video_id = path[3:].strip("/")
                try:
                    video_id = parse_video_id(video_id)
                except ValueError:
                    return self._json({"error": "bad id"}, 404)
                folder = video_dir(data_dir, video_id)
                if not folder.is_dir():
                    return self._bytes(_page("missing", f"<main><p>No archive for {_esc(video_id)}</p></main>"), 404)
                info = load_archive_info(data_dir, video_id)
                return self._bytes(detail_html(info, data_dir, db.find_open_job(data_dir, video_id)))
            if path.startswith("/media/"):
                return self._media(path[len("/media/"):])
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            self.send_error(404)

        def do_POST(self):
            if not self._trusted_mutation():
                return
            parsed = urlparse(self.path)
            if parsed.path == "/api/creators":
                payload = self._read_json()
                if payload is None:
                    return
                try:
                    if "refresh" in payload and not isinstance(payload["refresh"], bool):
                        raise ValueError("refresh must be a boolean")
                    listing = creators.start(payload.get("source"), refresh=payload.get("refresh", False))
                    return self._json(listing, 202 if listing["status"] == "loading" else 200)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                except BudgetExceeded as exc:
                    return self._json({"error": str(exc), "retry_at": exc.free_at}, 429)
            if parsed.path.startswith("/api/creators/") and parsed.path.endswith("/get"):
                payload = self._read_json(max_bytes=1024 * 1024)
                if payload is None:
                    return
                try:
                    if "all" in payload and not isinstance(payload["all"], bool):
                        raise ValueError("all must be a boolean")
                    result = creators.enqueue(
                        parsed.path.removeprefix("/api/creators/").removesuffix("/get"),
                        video_ids=payload.get("video_ids"), all_items=payload.get("all", False),
                    )
                    return self._json(result, 202)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
            if parsed.path == "/api/update-archive":
                return self._update_archive()
            if parsed.path == "/api/jobs/clear-waiting":
                if self._read_json() is None:
                    return
                return self._json({"removed": db.remove_all_waiting_jobs(data_dir)})
            match = re.fullmatch(r"/api/jobs/([0-9a-f]{1,64})/(remove|dismiss)", parsed.path)
            if match:
                if self._read_json() is None:
                    return
                job_id, action = match.groups()
                done = (
                    db.remove_waiting_job(data_dir, job_id)
                    if action == "remove"
                    else db.dismiss_failed_job(data_dir, job_id)
                )
                if not done:
                    return self._json({"error": f"that job is no longer {'waiting' if action == 'remove' else 'failed'}"}, 409)
                return self._json({"ok": True})
            if parsed.path == "/api/transcribe":
                return self._transcribe()
            if parsed.path == "/api/transcript/speaker":
                return self._set_segment_speaker()
            if parsed.path == "/api/transcript/rename-speaker":
                return self._rename_speaker()
            if parsed.path == "/api/open-folder":
                return self._open_folder()
            if parsed.path == "/api/grab":
                return self._grab(parsed)
            if parsed.path != "/api/get":
                return self.send_error(404)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                qs = parse_qs(raw.decode("utf-8", errors="replace"))
                payload = {"url": (qs.get("url") or [""])[0]}
            if not isinstance(payload, dict):
                return self._json({"error": "body must be an object"}, 400)
            url = payload.get("url") or payload.get("video_id") or ""
            if not isinstance(url, str):
                return self._json({"error": "url must be text"}, 400)
            url = url.strip()
            force_video = bool(payload.get("force_video"))
            force_shots = bool(payload.get("force_shots"))
            try:
                try:
                    parse_video_id(url)
                except ValueError:
                    if force_video or force_shots:
                        raise
                    return self._json({"browse_url": browse_url(url)})
                job = queue.submit(url, force_video=force_video, force_shots=force_shots)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            queued = job.get("status") != "complete" and not job.get("existing")
            result = queue.snapshot(job)
            if playlist_browse_url(url):
                result["playlist_browse_url"] = playlist_browse_url(url)
            return self._json(result, 202 if queued else 200)

        def _transcribe(self):
            payload = self._read_json()
            if payload is None:
                return
            try:
                video_id = parse_video_id(
                    (payload.get("video_id") or payload.get("url") or "").strip()
                )
                config = payload.get("config") or {}
                if not isinstance(config, dict):
                    raise ValueError("config must be an object")
                job = work_queue.submit_transcription(
                    video_id, config, force=bool(payload.get("force"))
                )
            except (ValueError, OSError) as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json(job, 202 if job["status"] != "done" else 200)

        def _set_segment_speaker(self):
            payload = self._read_json()
            if payload is None:
                return
            try:
                video_id = parse_video_id((payload.get("video_id") or "").strip())
                segment_index = int(payload.get("segment_index"))
                from .transcribe import update_segment_speaker

                update_segment_speaker(
                    data_dir, video_id, segment_index, payload.get("speaker") or ""
                )
                from .db import get_transcript

                return self._json(get_transcript(data_dir, video_id))
            except (ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, 400)

        def _rename_speaker(self):
            payload = self._read_json()
            if payload is None:
                return
            try:
                video_id = parse_video_id((payload.get("video_id") or "").strip())
                from .transcribe import rename_speaker

                rename_speaker(
                    data_dir,
                    video_id,
                    payload.get("old") or "",
                    payload.get("new") or "",
                )
                from .db import get_transcript

                return self._json(get_transcript(data_dir, video_id))
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, 400)

        def _update_archive(self):
            """Queue every archived video a plain job would still change (docs/browsing.md)."""
            if self._read_json() is None:
                return
            queued = []
            for video_id in db.archive_needs_summary(data_dir)["videos"]:
                job = queue.submit(video_id)
                if job.get("status") != "complete" and not job.get("existing"):
                    queued.append(video_id)
            return self._json({"queued": len(queued), "video_ids": queued}, 202 if queued else 200)

        def _open_folder(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, 400)
            try:
                video_id = parse_video_id((payload.get("video_id") or payload.get("url") or "").strip())
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            folder = condensed_dir(data_dir, video_id)
            if not folder.is_dir():
                folder = video_dir(data_dir, video_id)
            if not folder.is_dir():
                return self._json({"error": "no archive folder"}, 404)
            try:
                subprocess.Popen(
                    ["xdg-open", str(folder)],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                return self._json({"error": f"could not open folder: {exc}", "path": str(folder)}, 500)
            return self._json({"ok": True, "path": str(folder)})

        def _grab(self, parsed):
            qs = parse_qs(parsed.query)
            try:
                video_id = parse_video_id((qs.get("video_id") or [""])[0].strip())
                t = float((qs.get("t") or ["0"])[0])
            except (ValueError, TypeError) as exc:
                return self._json({"error": str(exc)}, 400)
            if not video_dir(data_dir, video_id).is_dir():
                return self._json({"error": "no archive"}, 404)
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 8 or length > 25 * 1024 * 1024:
                return self._json({"error": "bad frame size"}, 400)
            raw = self.rfile.read(length)
            if not is_complete_png(raw):
                return self._json({"error": "not a complete png"}, 400)
            folder = condensed_dir(data_dir, video_id)
            folder.mkdir(parents=True, exist_ok=True)
            title = load_archive_info(data_dir, video_id).get("title") or video_id
            dest = write_grab_png(folder, t, title, raw)
            return self._json({"ok": True, "name": dest.name})

        def _static(self, rel: str):
            root = STATIC_DIR.resolve()
            full = (root / rel).resolve()
            try:
                full.relative_to(root)
            except ValueError:
                return self.send_error(404)
            if not full.is_file():
                return self.send_error(404)
            ctype = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
            raw = full.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", IMMUTABLE)
            self.end_headers()
            self.wfile.write(raw)

        def _media(self, rel: str):
            root = data_dir.resolve()
            full = (root / rel).resolve()
            try:
                full.relative_to(root)
            except ValueError:
                return self.send_error(404)
            if not full.is_file():
                return self.send_error(404)
            ctype = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
            size = full.stat().st_size
            rng = _byte_range(self.headers.get("Range"), size)
            if rng == "unsatisfiable":
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if rng:
                start, end = rng
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", IMMUTABLE)
                self.end_headers()
                with full.open("rb") as fh:
                    fh.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = fh.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", IMMUTABLE)
            self.end_headers()
            with full.open("rb") as fh:
                while True:
                    chunk = fh.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def _json(self, obj, code=200):
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", LIVE)
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self, max_bytes=64 * 1024):
            content_type = (self.headers.get("Content-Type") or "").split(";", 1)[
                0
            ].strip().lower()
            if content_type != "application/json":
                self._json({"error": "Content-Type must be application/json"}, 415)
                return None
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length or 0)
            except ValueError:
                self._json({"error": "invalid Content-Length"}, 400)
                return None
            if length < 0:
                self._json({"error": "invalid Content-Length"}, 400)
                return None
            if length > max_bytes:
                self._json({"error": "request body too large"}, 413)
                return None
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, 400)
                return None
            if not isinstance(payload, dict):
                self._json({"error": "json body must be an object"}, 400)
                return None
            return payload

        def _trusted_mutation(self) -> bool:
            request_host = (self.headers.get("Host") or "").lower()
            if not _is_local_host(request_host):
                self._json({"error": "untrusted Host header"}, 403)
                return False
            if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
                self._json({"error": "cross-site request rejected"}, 403)
                return False
            origin = self.headers.get("Origin")
            if origin:
                origin_host = urlparse(origin).netloc.lower()
                if not origin_host or origin_host != request_host:
                    self._json({"error": "cross-origin request rejected"}, 403)
                    return False
            return True

        def _bytes(self, raw: bytes, code=200):
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", LIVE)
            self.end_headers()
            self.wfile.write(raw)

    return Handler


def serve(data_dir: Path | None = None, host: str = "127.0.0.1", port: int = 8765) -> None:
    data_dir = (data_dir or default_data_dir()).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    from .db import db_path, rebuild

    n = rebuild(data_dir)
    queue = JobQueue(data_dir)
    work_queue = DerivedWorkQueue(data_dir)
    httpd = ThreadingHTTPServer(
        (host, port), make_handler(data_dir, queue, work_queue)
    )
    print(f"ytarchive  http://{host}:{port}/  data={data_dir}  index={n} ({db_path(data_dir)})", flush=True)
    httpd.serve_forever()
