"""The archive page: paste box, download queue, list and search."""
from __future__ import annotations

from pathlib import Path

from ..db import normalize_video_sort
from ..paths import NEEDS

from .html import SORT_OPTIONS, _esc, _health_html, _page, _size, _video_list_html


def home_html(
    items: list[dict],
    query: str = "",
    data_dir: Path | None = None,
    sort: str = "recent",
    bookmarklet: str = "",
    health: dict | None = None,
    busy: frozenset = frozenset(),
    total: int | None = None,
    disk_used: int | None = None,
    disk_free: int | None = None,
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
    if _size(disk_used):
        count += f" · <strong>{_size(disk_used)}</strong>on disk"
    if _size(disk_free):
        count += f" · <strong>{_size(disk_free)}</strong>free"
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
