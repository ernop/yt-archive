"""A page's HTML together with the text of the files in static/ it links, so a test can check
what a page runs without caring which file holds it."""
from __future__ import annotations

import re

from yt_archive.web import STATIC_DIR


def with_static(html: str) -> str:
    names = re.findall(r'"/static/([\w./-]+)\?v=', html)
    return html + "".join((STATIC_DIR / name).read_text(encoding="utf-8") for name in names)
