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
from .lyrics import MAX_LYRICS_BYTES, store_lyrics
from .paths import (
    VIDEO_ID_RE,
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
    lyrics_path,
    mp3_filename,
    parse_video_id,
    read_shots_json,
    shots_dir,
    site_name,
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


def _page(
    title: str,
    body: str,
    scripts: tuple[str, ...] = (),
    extra_head: str = "",
    extra_tail: str = "",
    data: dict | None = None,
) -> bytes:
    """A page: static/app.css, the body, then data as JSON (#page-data) for its scripts,
    then the scripts, each a file in static/."""
    data_tag = (
        '<script type="application/json" id="page-data">'
        + json.dumps(data).replace("<", "\\u003c")
        + "</script>"
        if data is not None
        else ""
    )
    script_tags = "".join(f'<script src="{_esc(_static_url(name))}"></script>' for name in scripts)
    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{_esc(title)}</title>
<link rel="stylesheet" href="{_esc(_static_url("app.css"))}">
{extra_head}
</head><body>
{body}
{data_tag}{script_tags}
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
    """A UTC timestamp; static/local-times.js rewrites it in the viewer's time zone."""
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
    '<span class="d u">Uploaded</span><span class="d g">Got</span><span class="a">Added by</span></div>'
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
        f'<span class="d g">{_time_html(item.get("downloaded_at") or "")}</span>'
        f'<span class="a">{_esc(db.ADD_ROUTES.get(item.get("added_via") or "", "Not recorded"))}</span></div>'
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
        "+ ytarchive</a> to your bookmarks bar and click it on any YouTube video or creator page, "
        "a tweet with a video, or an X profile.</p>"
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
  <div class="sub">Paste video links (YouTube, or tweets on X) to get them, or a YouTube creator’s homepage, @handle, or playlist link, or an X profile link, to choose from all of its videos.</div>
  <form class="get" id="get-form">
    <input name="url" id="url" type="text" autofocus
      aria-label="Video links, YouTube creator, playlist, or X profile"
      placeholder="video links, creator homepage, @handle, playlist, or X profile link"
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
    return _page("ytarchive", body, ("local-times.js", "home.js"), data={"needs": NEEDS})


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
    return _page(f"{creator_name} · ytarchive", body, ("local-times.js",))


def browse_creator_html(source: str = "") -> bytes:
    """The picker (docs/browsing.md, "Browse YouTube"); static/creators.js fills it in."""
    body = f"""
<header>
  <p><a href="/">← archive and download queue</a></p>
  <h1>Browse YouTube or X</h1>
  <p class="sub">Paste a creator’s homepage or @handle to go through their videos, Shorts, live streams,
  and playlists, or paste a playlist link. Only the list is read; nothing downloads until you choose.
  Your <a href="{_esc(browse_url('https://www.youtube.com/playlist?list=WL'))}">Watch later</a>,
  <a href="{_esc(browse_url('https://www.youtube.com/playlist?list=LL'))}">Liked videos</a>,
  and private playlists are read with your YouTube account. An X profile link (x.com/name) lists that
  profile’s videos, newest first, read with your X login from Firefox.</p>
  <form class="get" id="creator-form">
    <input id="creator-source" aria-label="YouTube creator, playlist, or X profile" value="{_esc(source)}"
      placeholder="creator homepage, @handle, channel ID, playlist, or X profile link" required autocomplete="off">
    <button id="creator-load" type="submit">Browse</button>
  </form>
  <div id="creator-status" role="status" aria-live="polite"></div>
</header>
<main id="creator-picker" data-downloads-per-hour="{budget.LIMITS['download']}"
  data-x-downloads-per-hour="{budget.LIMITS['x-download']}"
  data-x-login-downloads-per-hour="{budget.LIMITS['x-account']}" hidden>
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
        <option value="">as listed</option>
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
      shift-click or <kbd>shift</kbd>+<kbd>x</kbd> selects a range · <kbd>o</kbd> opens it on YouTube or X</p>
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
        "Browse YouTube or X · ytarchive", body,
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
    title = re.sub(r"^\(\d+\)\s*", "", title).removesuffix(" - YouTube").removesuffix(" / X").strip()
    body = f"""
<main class="add" id="add" data-url="{_esc(url)}">
  <h1 id="add-status">Queuing…</h1>
  <p class="add-title">{_esc(title or url)}</p>
  <p class="meta" id="add-detail"></p>
  <p class="meta"><a href="/" target="_blank">archive and download queue</a></p>
</main>
"""
    return _page("ytarchive", body, ("add.js",))


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
    facts.append(
        f'<a href="{_esc(watch_url(vid))}" target="_blank" rel="noopener noreferrer">{site_name(vid)}</a>'
    )
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
        mp3_name = mp3_filename(vid, info.get("title") or "", info.get("channel") or "")
        lrc = lyrics_path(data_dir, vid)
        lyrics_link = (
            f' · <a href="{_esc(media_url(data_dir, lrc))}" download="{_esc(mp3_name.removesuffix(".mp3"))}.lrc">'
            'download lyrics</a>'
            if lrc.is_file() else ""
        )
        soundtrack_bits.append(
            f'<h2>MP3 audio</h2>'
            f'<p class="meta"><a href="{_esc(media_url(data_dir, mp3))}" download="{_esc(mp3_name)}">'
            f'download MP3 audio</a>{lyrics_link}</p>'
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
    return _page(
        title,
        "".join(parts),
        scripts=("local-times.js", "video-page.js"),
        data={"vid": vid, "site": site_name(vid)},
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
            if parsed.path == "/api/lyrics":
                return self._lyrics()
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
            via = payload.get("via")
            try:
                if via is not None:
                    db.check_add_route(via)
                try:
                    parse_video_id(url)
                except ValueError:
                    if force_video or force_shots:
                        raise
                    return self._json({"browse_url": browse_url(url)})
                job = queue.submit(url, force_video=force_video, force_shots=force_shots, via=via)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            queued = job.get("status") != "complete" and not job.get("existing")
            result = queue.snapshot(job)
            if playlist_browse_url(url):
                result["playlist_browse_url"] = playlist_browse_url(url)
            return self._json(result, 202 if queued else 200)

        def _lyrics(self):
            """A local client's lyrics for a video; null removes them (docs/browsing.md, "Lyrics")."""
            payload = self._read_json(max_bytes=MAX_LYRICS_BYTES + 4096)
            if payload is None:
                return
            try:
                video_id = payload.get("video_id")
                if not isinstance(video_id, str) or not VIDEO_ID_RE.fullmatch(video_id):
                    raise ValueError("video_id must be a YouTube video id")
                if "lrc" not in payload:
                    raise ValueError("lrc is required; null removes the lyrics")
                lrc = payload["lrc"]
                if lrc is not None and (not isinstance(lrc, str) or not lrc.strip()):
                    raise ValueError("lrc must be text or null")
                if lrc is not None and len(lrc.encode("utf-8")) > MAX_LYRICS_BYTES:
                    raise ValueError("lrc is too long")
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"video_id": video_id, "lyrics": store_lyrics(data_dir, video_id, lrc)})

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
    from .download import keep_yt_dlp_current_hourly

    n = rebuild(data_dir)
    queue = JobQueue(data_dir)
    work_queue = DerivedWorkQueue(data_dir)
    keep_yt_dlp_current_hourly(data_dir)
    httpd = ThreadingHTTPServer(
        (host, port), make_handler(data_dir, queue, work_queue)
    )
    print(f"ytarchive  http://{host}:{port}/  data={data_dir}  index={n} ({db_path(data_dir)})", flush=True)
    httpd.serve_forever()
