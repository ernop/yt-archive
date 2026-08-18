"""ytarchive web UI — paste a URL, get the video + squash framesheet."""
from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .jobs import JobQueue
from .paths import (
    default_data_dir,
    file_cache_key,
    find_video_file,
    framesheet_path,
    list_items,
    load_archive_info,
    media_url,
    parse_video_id,
    shots_dir,
    shots_json_path,
    video_dir,
    watch_url,
)

# Finished files never change at a given URL (multi-image-client rule).
# HTML embeds ?v=mtime-size; reget writes new bytes → new key.
IMMUTABLE = "public, max-age=31536000, immutable"
LIVE = "no-store"

STATIC_DIR = Path(__file__).resolve().parent / "static"

CSS = """
:root { color-scheme: dark; --bg:#111; --card:#1a1a1a; --line:#333; --fg:#eee; --muted:#999; --red:#c4302b; }
* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.45 system-ui, sans-serif; background: var(--bg); color: var(--fg); }
a { color: #f88; }
header, main { max-width: 1100px; margin: 0 auto; padding: 1.25rem 1.25rem 2rem; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
h1 a { color: inherit; text-decoration: none; }
.sub { color: var(--muted); margin-bottom: 1.25rem; }
form.get { display: flex; gap: .5rem; margin-bottom: 1rem; }
form.get input { flex: 1; background: #0d0d0d; border: 1px solid var(--line); color: var(--fg);
  padding: .7rem .8rem; border-radius: 6px; font-size: 1rem; }
form.get input:focus { outline: none; border-color: var(--red); }
form.get button { background: var(--red); color: #fff; border: 0; padding: .7rem 1.2rem;
  border-radius: 6px; font-weight: 600; cursor: pointer; }
form.get button:disabled { opacity: .5; cursor: wait; }
form.find { display: flex; gap: .5rem; margin: 0 0 1rem; }
form.find input { flex: 1; background: #0d0d0d; border: 1px solid var(--line); color: var(--fg);
  padding: .45rem .7rem; border-radius: 6px; font-size: .95rem; }
form.find input:focus { outline: none; border-color: var(--red); }
form.find button { background: #2a2a2a; color: var(--fg); border: 1px solid var(--line);
  padding: .45rem .8rem; border-radius: 6px; cursor: pointer; }
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
.card img, .card .ph { width: 100%; height: 124px; object-fit: cover; background: #000; display: block; }
.card .ph { color: var(--muted); display: flex; align-items: center; justify-content: center; font-size: .8rem; }
.card .info { padding: .7rem .7rem .7rem 0; }
.card strong { display: block; margin-bottom: .25rem; }
.meta { color: var(--muted); font-size: .85rem; }
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
.backup button:disabled { opacity: .5; cursor: wait; }
#reget-status { margin-top: .6rem; font-family: ui-monospace, monospace; white-space: pre-wrap; }
#reget-status.error { color: #f88; }
#reget-status.done { color: #8d8; }
"""


def _esc(text) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


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


def home_html(items: list[dict], query: str = "", data_dir: Path | None = None) -> bytes:
    cards = []
    for it in items:
        vid = it["video_id"]
        title = it.get("title") or vid
        when = (it.get("downloaded_at") or "")[:10]
        shots = it.get("shots_kept") or len(it.get("shot_files") or [])
        sheet = framesheet_path(data_dir, vid) if data_dir else None
        thumb = (
            f'<img src="{_esc(media_url(data_dir, sheet))}" alt="">'
            if data_dir and it.get("has_framesheet") and sheet.is_file()
            else '<div class="ph">no sheet yet</div>'
        )
        cards.append(
            f'<a class="card" href="/v/{vid}">{thumb}<div class="info">'
            f"<strong>{_esc(title)}</strong>"
            f'<div class="meta">{_esc(it.get("channel"))} · {when}'
            f'{f" · {shots} shots" if shots else ""}</div></div></a>'
        )
    empty = (
        f'<p class="empty">No matches for “{_esc(query)}”.</p>'
        if query
        else '<p class="empty">Nothing archived yet.</p>'
    )
    list_html = "".join(cards) or empty
    q_val = _esc(query)
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
  <form class="find" method="get" action="/">
    <input name="q" value="{q_val}" placeholder="search title, channel, id…" autocomplete="off">
    <button type="submit">Search</button>
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
  const res = await fetch('/api/list');
  const items = await res.json();
  if (!items.length) return;
  listEl.innerHTML = items.map((it) => {
    const when = (it.downloaded_at || '').slice(0, 10);
    const shots = it.shots_kept ? ' · ' + it.shots_kept + ' shots' : '';
    const thumb = it.thumb_url
      ? '<img src="' + it.thumb_url + '" alt="">'
      : '<div class="ph">no sheet yet</div>';
    return '<a class="card" href="/v/' + it.video_id + '">' + thumb
      + '<div class="info"><strong>' + escapeHtml(it.title || it.video_id) + '</strong>'
      + '<div class="meta">' + escapeHtml(it.channel || '') + ' · ' + when + shots + '</div></div></a>';
  }).join('');
}
function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
(async function loop() {
  const n = await paintQueue().catch(() => 0);
  setTimeout(loop, n ? 800 : 2500);
})();
"""
    return _page("ytarchive", body, js)


def detail_html(info: dict, data_dir: Path) -> bytes:
    vid = info["video_id"]
    title = info.get("title") or vid
    duration = info.get("duration")
    dur = f"{int(duration) // 60}:{int(duration) % 60:02d}" if duration else ""
    video = find_video_file(data_dir, vid)
    sheet = framesheet_path(data_dir, vid)
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
    parts = [
        f'<header><div><a href="/">← archive</a></div>',
        f"<h1>{_esc(title)}</h1>",
        f'<div class="meta">{_esc(info.get("channel"))} · {dur} · {vid} · '
        f'<a href="{watch_url(vid)}">YouTube</a></div></header><main>',
    ]
    if video:
        shots_attr = _esc(json.dumps(payload, separators=(",", ":")))
        dur_attr = f'{duration or ""}'
        parts.append(f"""
<div class="ytp" id="ytp" data-id="{_esc(vid)}" data-duration="{dur_attr}" data-shots="{shots_attr}">
  <video preload="metadata" src="{_esc(media_url(data_dir, video))}"></video>
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
    0–9  jump to %<br>
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
      <button type="button" data-act="mute">mute</button>
      <button type="button" data-act="pip">pip</button>
      <button type="button" data-act="theater">wide</button>
      <button type="button" data-act="fs">full</button>
      <button type="button" data-act="help">?</button>
    </div>
  </div>
</div>
<p class="meta">click a shot to seek · shift-click opens the PNG · resume and speed are remembered</p>
""")
    if sheet.is_file():
        parts.append(
            f'<p class="meta">Squished framesheet</p>'
            f'<a href="{_esc(media_url(data_dir, sheet))}">'
            f'<img class="sheet" src="{_esc(media_url(data_dir, sheet))}" alt="framesheet"></a>'
        )
    times = {r["file"]: r for r in kept}
    figs = []
    for shot in sorted(shots_dir(data_dir, vid).glob("*.png")):
        rec = times.get(shot.name) or {}
        mid = rec.get("mid")
        t0 = rec.get("t0")
        cap = shot.stem + (f" @ {mid:.1f}s" if mid is not None else "")
        data_t = f'data-t="{mid}"' if mid is not None else ""
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
"""
    return _page(
        title,
        "".join(parts),
        extra_js=reget_js,
        extra_head=f'<link rel="stylesheet" href="{_esc(_static_url("player.css"))}">',
        extra_tail=f'<script src="{_esc(_static_url("player.js"))}"></script>',
    )


def make_handler(data_dir: Path, queue: JobQueue):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f"{self.address_string()} {fmt % args}", flush=True)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/":
                q = (parse_qs(parsed.query).get("q") or [""])[0].strip()
                return self._bytes(home_html(list_items(data_dir, q), q, data_dir))
            if path == "/api/list":
                q = (parse_qs(parsed.query).get("q") or [""])[0].strip()
                return self._json(list_items(data_dir, q))
            if path == "/api/jobs":
                from .db import list_jobs

                return self._json(list_jobs(data_dir))
            if path.startswith("/api/job/"):
                job = queue.get(path.split("/", 3)[-1])
                if not job:
                    return self._json({"error": "unknown job"}, 404)
                return self._json(queue.snapshot(job))
            if path.startswith("/v/"):
                video_id = path[3:].strip("/")
                try:
                    video_id = parse_video_id(video_id)
                except ValueError:
                    return self._json({"error": "bad id"}, 404)
                folder = video_dir(data_dir, video_id)
                if not folder.is_dir():
                    return self._bytes(_page("missing", f"<main><p>No archive for {_esc(video_id)}</p></main>"), 404)
                return self._bytes(detail_html(load_archive_info(data_dir, video_id), data_dir))
            if path.startswith("/media/"):
                return self._media(path[len("/media/"):])
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            self.send_error(404)

        def do_POST(self):
            parsed = urlparse(self.path)
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
    httpd = ThreadingHTTPServer((host, port), make_handler(data_dir, queue))
    print(f"ytarchive  http://{host}:{port}/  data={data_dir}  index={n} ({db_path(data_dir)})", flush=True)
    httpd.serve_forever()
