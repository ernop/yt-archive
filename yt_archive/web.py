"""ytarchive web UI — paste a URL, get the video + squash framesheet."""
from __future__ import annotations

import ipaddress
import json
import mimetypes
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from .db import list_creator_videos, normalize_video_sort
from .jobs import JobQueue
from .locks import video_lock
from .paths import (
    all_labeled_path,
    condensed_dir,
    default_data_dir,
    file_cache_key,
    find_video_file,
    framesheet_paths,
    is_complete_png,
    list_archived_ids,
    write_grab_png,
    list_items,
    load_archive_info,
    media_url,
    parse_video_id,
    shots_dir,
    shots_json_path,
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
:root { color-scheme: dark; --bg:#000; --card:#000; --line:#555; --fg:#fff; --muted:#fff; --red:#c4302b; }
* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.45 system-ui, sans-serif; background: var(--bg); color: var(--fg); }
a { color: #f88; }
input::placeholder { color: #fff; opacity: 1; }
button:disabled { color: #fff !important; opacity: 1 !important; cursor: wait; }
header, main { width: 100%; margin: 0; padding: 1.25rem clamp(1rem, 3vw, 2.5rem) 2rem; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
h1 a { color: inherit; text-decoration: none; }
.sub { color: var(--muted); margin-bottom: 1.25rem; }
form.get { display: flex; gap: .5rem; margin-bottom: 1rem; }
form.get input { flex: 1; background: #0d0d0d; border: 1px solid var(--line); color: var(--fg);
  padding: .7rem .8rem; border-radius: 6px; font-size: 1rem; }
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
.sort-field { display: grid; gap: .25rem; color: var(--muted); font-size: .78rem; font-weight: 650; }
.sort-field select { min-width: 12rem; background: #0d0d0d; border: 1px solid var(--line);
  color: var(--fg); padding: .45rem .7rem; border-radius: 6px; font: 500 .95rem/1.45 system-ui, sans-serif; }
#status { min-height: 1.4em; color: var(--muted); margin-bottom: .5rem; font-family: ui-monospace, monospace; font-size: .9rem; white-space: pre-wrap; }
#status.error { color: #f88; }
#status.done { color: #8d8; }
#queue { margin: 0 0 1.25rem; font: 13px/1.45 ui-monospace, monospace; }
#queue:empty { display: none; }
#queue .job { color: var(--muted); padding: .15rem 0; }
#queue .job.running { color: var(--fg); }
#queue .job.done a { color: #8d8; }
#queue .job.error { color: #f88; }
h2 { font-size: 1.1rem; color: var(--muted); font-weight: 600; margin: 2rem 0 .75rem; }
.list { display: flex; flex-direction: column; gap: .75rem; }
.card { display: grid; grid-template-columns: 220px 1fr; gap: .9rem; background: var(--card);
  border: 1px solid var(--line); border-radius: 8px; overflow: hidden; text-decoration: none; color: inherit; }
.card:hover { border-color: var(--red); }
.card-thumb, .card-title { color: #fff; text-decoration: none; }
.card-title:hover, .creator-link:hover { text-decoration: underline; }
.card img, .card .ph { width: 100%; height: 124px; object-fit: cover; background: #000; display: block; }
.card .ph { color: var(--muted); display: flex; align-items: center; justify-content: center; font-size: .8rem; }
.card .info { min-width: 0; padding: .7rem .7rem .7rem 0; }
.card-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .75rem;
  align-items: start; margin-bottom: .3rem; }
.card strong { display: block; font-size: 1.03rem; font-weight: 700; }
.got { color: var(--muted); font-size: .72rem; white-space: nowrap; text-align: right; }
.got-label { margin-right: .3rem; }
.got time { color: #fff; font: 700 .9rem/1.25 ui-monospace, monospace;
  font-variant-numeric: tabular-nums; }
.meta { color: var(--muted); font-size: .85rem; }
.creator-count { margin-top: .55rem; }
.creator-count strong { color: #fff; font: 750 1.4rem/1 ui-monospace, monospace;
  font-variant-numeric: tabular-nums; }
.sheet { width: 100%; height: auto; border: 1px solid var(--line); margin-bottom: 1.25rem; }
.shots { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }
.shots figure { margin: 0; background: var(--card); border: 1px solid var(--line); }
.shots img { width: 100%; height: auto; display: block; }
.shots figcaption { padding: .3rem .45rem; font-size: .8rem; color: var(--muted); }
.empty { color: var(--muted); }
.backup { margin-top: 2.75rem; padding-top: 1rem; border-top: 1px solid var(--line);
  color: var(--muted); font-size: .82rem; }
.backup p { margin: 0 0 .5rem; }
.backup button { background: #161616; border: 1px solid var(--line); color: var(--muted);
  padding: .35rem .7rem; border-radius: 4px; cursor: pointer; margin-right: .4rem; font-size: .82rem; }
.backup button:hover { color: var(--fg); border-color: #666; }
.backup button:disabled { opacity: 1; cursor: wait; }
#reget-status { margin-top: .6rem; font-family: ui-monospace, monospace; white-space: pre-wrap; }
#reget-status.error { color: #f88; }
#reget-status.done { color: #8d8; }
.soundtrack { margin: 0 0 1.5rem; }
.soundtrack audio { width: 100%; margin: .4rem 0 .6rem; }
.soundtrack .row { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; }
.soundtrack button, .backfill button { background: #161616; border: 1px solid var(--line); color: var(--muted);
  padding: .35rem .7rem; border-radius: 4px; cursor: pointer; font-size: .82rem; }
.soundtrack button:hover, .backfill button:hover { color: var(--fg); border-color: #666; }
.soundtrack button:disabled, .backfill button:disabled { opacity: 1; cursor: wait; }
.backfill { margin: 0 0 1rem; }
#backfill-status, #audio-status { margin-top: .4rem; font-family: ui-monospace, monospace; font-size: .85rem; white-space: pre-wrap; color: var(--muted); }
#backfill-status.error, #audio-status.error { color: #f88; }
#backfill-status.done, #audio-status.done { color: #8d8; }
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
.transcript-progress-value { color: var(--fg); font: 700 1.65rem/1 ui-monospace, monospace;
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
.segment { display: grid; grid-template-columns: 6.5rem minmax(0, 1fr); gap: .75rem;
  padding: .65rem .75rem; border-bottom: 1px solid #292929; cursor: pointer; }
.segment:last-child { border-bottom: 0; }
.segment:hover { background: #171717; }
.segment.now { background: #202820; box-shadow: inset 4px 0 #68c77b; }
.segment-time { color: #fff; font: 700 .95rem/1.45 ui-monospace, monospace;
  font-variant-numeric: tabular-nums; }
.segment-speaker { color: #a9d6ff; display: block; font-size: .78rem; font-weight: 700;
  margin-top: .25rem; overflow-wrap: anywhere; }
.segment-text { color: var(--fg); line-height: 1.45; }
.word { border-radius: 3px; }
.word:hover { background: #554b19; color: #fff; }
.segment.match { background: #28230f; }
.transcript-empty { color: var(--muted); padding: 1rem; }
.speaker-tools { border-top: 1px solid #333; margin-top: .8rem; padding-top: .8rem; }
.speaker-tools label, .transcript-controls label { color: var(--muted); font-size: .85rem; }
dialog { width: min(34rem, calc(100% - 2rem)); color: var(--fg); background: #181818;
  border: 1px solid #555; border-radius: 9px; padding: 1.1rem; }
dialog::backdrop { background: rgba(0,0,0,.72); }
dialog h2 { color: var(--fg); font-size: 1.25rem; font-weight: 750; margin: 0 0 .4rem; }
dialog .fields { display: grid; grid-template-columns: 1fr 1fr; gap: .8rem; margin: 1rem 0; }
dialog label { display: grid; gap: .3rem; color: #fff; font-size: .85rem; }
dialog .dialog-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: .5rem; }
dialog .primary { background: #2e7441; border-color: #4a9d61; font-weight: 700; }
@media (max-width: 600px) {
  .archive-tools { grid-template-columns: 1fr; align-items: stretch; }
  .sort-field select { width: 100%; }
  .card { grid-template-columns: 1fr; }
  .card img, .card .ph { height: auto; min-height: 9rem; }
  .card .info { padding: .75rem; }
  .card-head { grid-template-columns: 1fr; gap: .25rem; }
  .got { text-align: left; }
  .segment { grid-template-columns: 5.4rem minmax(0, 1fr); }
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


SORT_OPTIONS = (
    ("recent", "Recently gotten"),
    ("oldest", "First gotten"),
    ("uploaded", "Video date — newest"),
    ("title", "Title — A–Z"),
    ("channel", "Channel — A–Z"),
)


def _got_html(downloaded_at: str) -> str:
    stamp = str(downloaded_at or "")
    display = stamp[:16].replace("T", " ") if stamp else "unknown"
    if stamp.endswith("+00:00") or stamp.endswith("Z"):
        display += " UTC"
    return (
        '<span class="got"><span class="got-label">got</span>'
        f'<time datetime="{_esc(stamp)}" data-local>{_esc(display)}</time></span>'
    )


def _creator_url(item: dict) -> str:
    channel_id = str(item.get("channel_id") or "").strip()
    channel = str(item.get("channel") or "").strip()
    if channel_id:
        return "/creator?" + urlencode({"channel_id": channel_id})
    if channel:
        return "/creator?" + urlencode({"channel": channel})
    return ""


def _video_card_html(item: dict, data_dir: Path | None = None) -> str:
    video_id = item["video_id"]
    title = item.get("title") or video_id
    shots = item.get("shots_kept") or len(item.get("shot_files") or [])
    sheets = framesheet_paths(data_dir, video_id) if data_dir else []
    thumb = (
        f'<img src="{_esc(media_url(data_dir, sheets[0]))}" alt="">'
        if data_dir and item.get("has_framesheet") and sheets
        else '<div class="ph">no sheet yet</div>'
    )
    creator_url = _creator_url(item)
    channel = _esc(item.get("channel") or "unknown uploader")
    creator = (
        f'<a class="creator-link" href="{_esc(creator_url)}">{channel}</a>'
        if creator_url
        else channel
    )
    return (
        f'<article class="card"><a class="card-thumb" href="/v/{video_id}">{thumb}</a>'
        f'<div class="info"><div class="card-head"><strong>'
        f'<a class="card-title" href="/v/{video_id}">{_esc(title)}</a></strong>'
        f'{_got_html(item.get("downloaded_at") or "")}</div>'
        f'<div class="meta">{creator}'
        f'{f" · {shots} shots" if shots else ""}</div></div></article>'
    )


def home_html(
    items: list[dict],
    query: str = "",
    data_dir: Path | None = None,
    sort: str = "recent",
) -> bytes:
    sort = normalize_video_sort(sort)
    cards = [_video_card_html(item, data_dir) for item in items]
    empty = (
        f'<p class="empty">No matches for “{_esc(query)}”.</p>'
        if query
        else '<p class="empty">Nothing archived yet.</p>'
    )
    list_html = "".join(cards) or empty
    q_val = _esc(query)
    sort_options = "".join(
        f'<option value="{value}"{" selected" if value == sort else ""}>{label}</option>'
        for value, label in SORT_OPTIONS
    )
    body = f"""
<header>
  <h1><a href="/">ytarchive</a></h1>
  <div class="sub">Paste one URL or a pile of them. Each becomes a queued job; they run one at a time.</div>
  <form class="get" id="get-form">
    <input name="url" id="url" type="text" autofocus
      placeholder="paste links — one or many, then Get"
      autocomplete="off">
    <button type="submit" id="go">Get</button>
  </form>
  <div id="status"></div>
  <div id="queue"></div>
</header>
<main>
  <h2>Archive</h2>
  <div class="backfill">
    <button type="button" id="backfill-audio">create missing MP3 audio</button>
    <div id="backfill-status"></div>
  </div>
  <form class="archive-tools" method="get" action="/">
    <div class="find">
      <input name="q" value="{q_val}" placeholder="search title, channel, id, transcript…" autocomplete="off">
      <button type="submit">Search</button>
    </div>
    <label class="sort-field"><span>Sort archive</span>
      <select name="sort" onchange="this.form.submit()">{sort_options}</select>
    </label>
  </form>
  <div class="list">{list_html}</div>
</main>
"""
    js = r"""
const form = document.getElementById('get-form');
const status = document.getElementById('status');
const urlBox = document.getElementById('url');
const queueEl = document.getElementById('queue');
const listEl = document.querySelector('.list');
let knownDone = new Set();
let primed = false;
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const urls = urlBox.value.split(/\s+/).map((s) => s.trim()).filter(Boolean);
  if (!urls.length) return;
  urlBox.value = '';
  urlBox.focus();
  status.className = '';
  const notes = [];
  for (const url of urls) {
    try {
      const res = await fetch('/api/get', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url}),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      notes.push((data.status === 'queued' ? 'queued ' : data.status + ' ') + data.video_id);
    } catch (err) {
      notes.push(url + ' — ' + err.message);
      status.className = 'error';
    }
  }
  status.textContent = notes.join('\n');
  paintQueue();
});
function jobLine(j) {
  const label = j.video_id + (j.phase && j.phase !== j.status ? ' · ' + j.phase : '');
  if (j.status === 'done') {
    return '<div class="job done"><a href="/v/' + j.video_id + '">' + label + ' done</a></div>';
  }
  const extra = j.status === 'error' ? ' — ' + (j.error || 'failed')
    : (j.log && j.log.length ? ' — ' + j.log[j.log.length - 1] : '');
  return '<div class="job ' + j.status + '">' + label + extra + '</div>';
}
async function paintQueue() {
  const res = await fetch('/api/jobs');
  const jobs = await res.json();
  queueEl.innerHTML = jobs.map(jobLine).join('');
  const open = jobs.filter((j) => j.status === 'queued' || j.status === 'running');
  if (!primed) {
    for (const j of jobs) {
      if (j.status === 'done') knownDone.add(j.job_id);
    }
    primed = true;
  }
  let added = false;
  for (const j of jobs) {
    if (j.status === 'done' && !knownDone.has(j.job_id)) {
      knownDone.add(j.job_id);
      added = true;
    }
  }
  if (added) refreshList();
  return open.length;
}
async function refreshList() {
  const params = new URLSearchParams(window.location.search);
  const res = await fetch('/api/list?' + params.toString());
  const items = await res.json();
  if (!items.length) {
    const query = params.get('q') || '';
    listEl.innerHTML = '<p class="empty">'
      + (query ? 'No matches for “' + escapeHtml(query) + '”.' : 'Nothing archived yet.')
      + '</p>';
    return;
  }
  listEl.innerHTML = items.map((it) => {
    const shots = it.shots_kept ? ' · ' + it.shots_kept + ' shots' : '';
    const thumb = it.thumb_url
      ? '<img src="' + it.thumb_url + '" alt="">'
      : '<div class="ph">no sheet yet</div>';
    const creator = creatorUrl(it);
    const channel = escapeHtml(it.channel || 'unknown uploader');
    const creatorHtml = creator
      ? '<a class="creator-link" href="' + creator + '">' + channel + '</a>'
      : channel;
    return '<article class="card"><a class="card-thumb" href="/v/' + it.video_id + '">' + thumb
      + '</a><div class="info"><div class="card-head"><strong>'
      + '<a class="card-title" href="/v/' + it.video_id + '">'
      + escapeHtml(it.title || it.video_id) + '</a></strong>' + gotHtml(it.downloaded_at)
      + '</div><div class="meta">' + creatorHtml + shots + '</div></div></article>';
  }).join('');
  localizeTimes();
}
function creatorUrl(it) {
  if (it.channel_id) return '/creator?channel_id=' + encodeURIComponent(it.channel_id);
  if (it.channel) return '/creator?channel=' + encodeURIComponent(it.channel);
  return '';
}
function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function gotHtml(stamp) {
  return '<span class="got"><span class="got-label">got</span><time datetime="'
    + escapeHtml(stamp || '') + '" data-local>' + escapeHtml(stamp || 'unknown') + '</time></span>';
}
function localizeTimes() {
  document.querySelectorAll('time[data-local]').forEach((el) => {
    if (!el.dateTime) return;
    const date = new Date(el.dateTime);
    if (Number.isNaN(date.getTime())) return;
    el.textContent = date.toLocaleString([], {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  });
}
document.getElementById('backfill-audio').onclick = async () => {
  const btn = document.getElementById('backfill-audio');
  const box = document.getElementById('backfill-status');
  btn.disabled = true;
  box.className = '';
  box.textContent = 'queueing…';
  try {
    const res = await fetch('/api/backfill-audio', {method: 'POST'});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    box.className = data.queued ? '' : 'done';
    box.textContent = data.queued
      ? 'queued ' + data.queued + ' MP3 audio job(s)'
      : 'every archived video already has MP3 audio';
    paintQueue();
  } catch (err) {
    box.className = 'error';
    box.textContent = err.message;
  }
  btn.disabled = false;
};
(async function loop() {
  const n = await paintQueue().catch(() => 0);
  setTimeout(loop, n ? 800 : 2500);
})();
localizeTimes();
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
) -> bytes:
    sort = normalize_video_sort(sort)
    cards = "".join(_video_card_html(item, data_dir) for item in items)
    if not cards:
        cards = f'<p class="empty">No videos match “{_esc(query)}”.</p>'
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
        if channel_id
        else ""
    )
    shown = (
        f'<strong>{len(items)}</strong> of <strong>{total_count}</strong> videos'
        if query
        else f'<strong>{total_count}</strong> videos'
    )
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
  <div class="list">{cards}</div>
</main>
"""
    js = r"""
document.querySelectorAll('time[data-local]').forEach((el) => {
  const date = new Date(el.dateTime);
  if (!Number.isNaN(date.getTime())) {
    el.textContent = new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium', timeStyle: 'short',
    }).format(date);
  }
});
"""
    return _page(f"{creator_name} · ytarchive", body, js)


def detail_html(info: dict, data_dir: Path) -> bytes:
    vid = info["video_id"]
    title = info.get("title") or vid
    duration = info.get("duration")
    dur = f"{int(duration) // 60}:{int(duration) % 60:02d}" if duration else ""
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
    shot_recs = []
    sj = shots_json_path(data_dir, vid)
    if sj.exists():
        shot_recs = json.loads(sj.read_text(encoding="utf-8")).get("shots", [])
    kept = [r for r in shot_recs if r.get("file") and r.get("kept") is not False]
    payload = [
        {"t": r.get("t0"), "mid": r.get("mid")}
        for r in kept
        if r.get("t0") is not None
    ]
    creator_url = _creator_url(info)
    creator_name = _esc(info.get("channel") or "unknown uploader")
    creator_link = (
        f'<a class="creator-link" href="{_esc(creator_url)}">{creator_name}</a>'
        if creator_url
        else creator_name
    )
    parts = [
        f'<header><div><a href="/">← archive</a></div>',
        f"<h1>{_esc(title)}</h1>",
        f'<div class="meta">{creator_link} · {dur} · {vid} · '
        f'<a href="{watch_url(vid)}">YouTube</a></div></header><main>',
    ]
    if video:
        shots_attr = _esc(json.dumps(payload, separators=(",", ":")))
        dur_attr = f'{duration or ""}'
        captions_track = (
            f'<track kind="captions" srclang="{_esc(transcript_language)}" label="Local transcript" '
            f'src="{_esc(media_url(data_dir, captions_file))}">'
            if has_transcript
            else ""
        )
        parts.append(f"""
<div class="ytp" id="ytp" data-id="{_esc(vid)}" data-duration="{dur_attr}" data-shots="{shots_attr}">
  <video preload="metadata" src="{_esc(media_url(data_dir, video))}">{captions_track}</video>
  <div class="ytp-overlay">
    <button type="button" class="ytp-bigplay" data-act="play" aria-label="Play">▶</button>
    <div class="ytp-flash"></div>
  </div>
  <button type="button" class="ytp-resume"></button>
  <div class="ytp-help">
    <b>keys</b><br>
    space / k  play-pause<br>
    j / l  −10s / +10s<br>
    ← →  −5s / +5s · shift = 1s<br>
    , .  frame step<br>
    s  save this frame<br>
    0–9  jump to %<br>
    home / end  video start / end (video focused)<br>
    &lt; &gt;  speed · m mute · f full<br>
    t theater · i / p pip<br>
    pgup / pgdn  prev / next shot<br>
    ? this list
  </div>
  <div class="ytp-bar">
    <div class="ytp-scrub">
      <div class="ytp-scrub-track"></div>
      <div class="ytp-scrub-buf"></div>
      <div class="ytp-scrub-fill"></div>
      <div class="ytp-ticks"></div>
      <div class="ytp-scrub-knob"></div>
      <div class="ytp-tip"></div>
    </div>
    <div class="ytp-row">
      <button type="button" data-act="play">▶</button>
      <button type="button" data-act="back">−10</button>
      <button type="button" data-act="fwd">+10</button>
      <span class="ytp-time">0:00 / 0:00</span>
      <button type="button" data-act="slower">−</button>
      <span class="ytp-speed">1×</span>
      <button type="button" data-act="faster">+</button>
      <span class="ytp-grow"></span>
      <button type="button" data-act="grab">grab</button>
      <button type="button" data-act="mute">mute</button>
      <button type="button" data-act="pip">pip</button>
      {('<button type="button" data-act="cc">CC</button>' if has_transcript else '')}
      <button type="button" data-act="theater">wide</button>
      <button type="button" data-act="fs">full</button>
      <button type="button" data-act="help">?</button>
    </div>
  </div>
</div>
<p class="meta">click a shot to seek · s saves this frame next to the mp3 · shift-click opens the PNG · resume and speed are remembered</p>
""")
    mp3 = soundtrack_path(data_dir, vid)
    soundtrack_bits = ['<div class="soundtrack">']
    if mp3.is_file():
        soundtrack_bits.append(
            f'<h2>MP3 audio</h2>'
            f'<p class="meta"><a href="{_esc(media_url(data_dir, mp3))}" download>download MP3 audio</a></p>'
            f'<audio controls preload="metadata" src="{_esc(media_url(data_dir, mp3))}"></audio>'
        )
    else:
        soundtrack_bits.append('<h2>MP3 audio</h2>')
    row = ['<div class="row">']
    if video and not mp3.is_file():
        row.append('<button type="button" id="dump-audio">create MP3 audio</button>')
    row.append(
        '<button type="button" id="open-mp3-folder">open MP3 folder</button>'
    )
    row.append("</div>")
    if video and not mp3.is_file():
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
    for n, sheet in enumerate(sheets, start=1):
        label = (
            f"Squished framesheet {n}/{len(sheets)}"
            if len(sheets) > 1
            else "Squished framesheet"
        )
        parts.append(
            f'<p class="meta">{_esc(label)}</p>'
            f'<a href="{_esc(media_url(data_dir, sheet))}" target="_blank" rel="noopener">'
            f'<img class="sheet" src="{_esc(media_url(data_dir, sheet))}" alt="{_esc(label)}"></a>'
        )
    labeled = all_labeled_path(data_dir, vid)
    if labeled.is_file():
        parts.append(
            f'<p class="meta">All detections (kept + dropped)</p>'
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
        data_t = f'data-t="{sample}"' if sample is not None else ""
        data_start = f' data-start="{t0}"' if t0 is not None else ""
        figs.append(
            f'<figure {data_t}{data_start}>'
            f'<a href="{_esc(media_url(data_dir, shot))}">'
            f'<img src="{_esc(media_url(data_dir, shot))}" alt="{shot.stem}"></a>'
            f"<figcaption>{cap}</figcaption></figure>"
        )
    if figs:
        parts.append(
            f'<p class="meta">Shot images — click to play from that moment</p>'
            f'<div class="shots">{"".join(figs)}</div>'
        )
    parts.append(
        f"""
<div class="backup">
  <p>backup — redo a piece if it came out wrong</p>
  <button type="button" id="reget-png">reget png</button>
  <button type="button" id="reget-video">reget video</button>
  <div id="reget-status"></div>
</div>
"""
    )
    parts.append("</main>")
    reget_js = r"""
const vid = """ + json.dumps(vid) + r""";
async function reget(kind) {
  const ok = confirm(kind === 'video'
    ? 'Re-download this video from YouTube and rebuild the framesheet?'
    : 'Rebuild shot PNGs and framesheet from the local video?');
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
    const res = await fetch('/api/get', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    for (;;) {
      const jres = await fetch('/api/job/' + data.job_id);
      const j = await jres.json();
      const tail = (j.log || []).slice(-4).join('\n');
      status.textContent = (j.phase || j.status) + (tail ? '\n' + tail : '');
      if (j.status === 'done') {
        status.className = 'done';
        status.textContent = 'done — reloading';
        location.reload();
        return;
      }
      if (j.status === 'error') {
        status.className = 'error';
        status.textContent = j.error || 'failed';
        png.disabled = video.disabled = false;
        return;
      }
      await new Promise(r => setTimeout(r, 800));
    }
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
    const res = await fetch('/api/get', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url: vid}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    for (;;) {
      const jres = await fetch('/api/job/' + data.job_id);
      const j = await jres.json();
      const tail = (j.log || []).slice(-4).join('\n');
      status.textContent = (j.phase || j.status) + (tail ? '\n' + tail : '');
      if (j.status === 'done') {
        status.className = 'done';
        status.textContent = 'done — reloading';
        location.reload();
        return;
      }
      if (j.status === 'error') {
        status.className = 'error';
        status.textContent = j.error || 'failed';
        dumpBtn.disabled = false;
        return;
      }
      await new Promise(r => setTimeout(r, 800));
    }
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
    + '<div><span class="segment-time">' + clock(segment.start) + '</span>'
    + '<button type="button" class="segment-speaker" title="assign or group speaker">'
    + html(segment.speaker || 'Unassigned') + '</button></div>'
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
    const playerCc = document.querySelector('#ytp [data-act=cc]');
    if (playerCc) playerCc.click();
    else if (track) track.mode = track.mode === 'showing' ? 'hidden' : 'showing';
    paintCc();
  };
  document.getElementById('ytp').addEventListener('ytarchive:cc', paintCc);
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
        extra_head=f'<link rel="stylesheet" href="{_esc(_static_url("player.css"))}">',
        extra_tail=f'<script src="{_esc(_static_url("player.js"))}"></script>',
    )


def make_handler(data_dir: Path, queue: JobQueue, work_queue: DerivedWorkQueue):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f"{self.address_string()} {fmt % args}", flush=True)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/":
                params = parse_qs(parsed.query)
                q = (params.get("q") or [""])[0].strip()
                sort = normalize_video_sort((params.get("sort") or ["recent"])[0])
                return self._bytes(
                    home_html(list_items(data_dir, q, sort), q, data_dir, sort)
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
                    )
                )
            if path == "/api/list":
                params = parse_qs(parsed.query)
                q = (params.get("q") or [""])[0].strip()
                sort = normalize_video_sort((params.get("sort") or ["recent"])[0])
                return self._json(list_items(data_dir, q, sort))
            if path == "/api/jobs":
                from .db import list_jobs

                return self._json(list_jobs(data_dir))
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
                with video_lock(video_id):
                    info = load_archive_info(data_dir, video_id)
                    return self._bytes(detail_html(info, data_dir))
            if path.startswith("/media/"):
                return self._media(path[len("/media/"):])
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            self.send_error(404)

        def do_POST(self):
            if not self._trusted_mutation():
                return
            parsed = urlparse(self.path)
            if parsed.path == "/api/backfill-audio":
                return self._backfill_audio()
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
            url = (payload.get("url") or payload.get("video_id") or "").strip()
            force_video = bool(payload.get("force_video"))
            force_shots = bool(payload.get("force_shots"))
            try:
                job = queue.submit(url, force_video=force_video, force_shots=force_shots)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json(queue.snapshot(job), 202)

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

        def _backfill_audio(self):
            queued = []
            for video_id in list_archived_ids(data_dir):
                if not find_video_file(data_dir, video_id):
                    continue
                if soundtrack_path(data_dir, video_id).is_file():
                    continue
                job = queue.submit(video_id)
                queued.append(job["video_id"])
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
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                start_s, _, end_s = rng[6:].partition("-")
                start = int(start_s or 0)
                end = int(end_s) if end_s else size - 1
                end = min(end, size - 1)
                if start > end or start < 0:
                    self.send_error(416)
                    return
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

        def _read_json(self):
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
            if length > 64 * 1024:
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
