"""The bookmarklet's pop-up (docs/bookmarklet.md)."""
from __future__ import annotations

import re

from .html import _esc, _page


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
