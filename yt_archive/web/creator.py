"""A creator's archived videos, and the /browse picker for creators, playlists and X profiles."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from .. import budget
from ..creators import browse_url
from ..db import normalize_video_sort

from .html import SORT_OPTIONS, _esc, _page, _static_url, _video_list_html


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
