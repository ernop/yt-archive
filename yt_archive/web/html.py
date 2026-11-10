"""Shared page pieces: the page frame, escaping, list rows and small formatters."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

from .. import db
from ..paths import file_cache_key, NEEDS


STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


def _esc(text) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


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
