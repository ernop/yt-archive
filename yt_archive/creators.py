"""Read-only YouTube creator discovery, separate from the download queue."""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import uuid
from urllib.parse import quote, unquote, urlsplit

from . import db
from .download import yt_dlp_bin
from .paths import VIDEO_ID_RE, find_video_file

CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")
_NAME_RE = re.compile(r"[\w.·-]{1,100}\Z", re.UNICODE)
_TABS = {"featured", "videos", "shorts", "streams", "about", "playlists", "community"}
_RESERVED = _TABS | {"watch", "playlist", "feed", "results", "shorts", "live", "embed", "v", "user", "c", "channel"}


def creator_url(source: str) -> str:
    """Normalize handles and old /user, /c, /channel homepages to one channel."""
    if not isinstance(source, str) or not source.strip() or len(source) > 2048:
        raise ValueError("Paste a YouTube creator homepage, @handle, or username.")
    source = source.strip()
    if CHANNEL_ID_RE.fullmatch(source):
        return "https://www.youtube.com/channel/" + source
    if source.startswith("@") or _NAME_RE.fullmatch(source):
        name = source.removeprefix("@")
        if not _NAME_RE.fullmatch(name):
            raise ValueError("Invalid YouTube handle.")
        return "https://www.youtube.com/@" + quote(name)
    parsed = urlsplit(source if "://" in source else "https://" + source)
    if (parsed.scheme not in {"http", "https"}
            or parsed.netloc.lower() not in {"youtube.com", "www.youtube.com", "m.youtube.com"}):
        raise ValueError("Use a YouTube creator homepage or @handle.")
    parts = unquote(parsed.path).strip("/").split("/")
    if parts[0].startswith("@"):
        name = parts[0][1:]
        root = "@" + quote(name)
        tail = parts[1:]
    elif len(parts) >= 2 and parts[0] in {"user", "c", "channel"}:
        name = parts[1]
        if parts[0] == "channel" and not CHANNEL_ID_RE.fullmatch(name):
            raise ValueError("Invalid YouTube channel ID.")
        root = parts[0] + "/" + quote(name)
        tail = parts[2:]
    elif parts[0] not in _RESERVED and _NAME_RE.fullmatch(parts[0]):
        name = parts[0]
        root = quote(name)
        tail = parts[1:]
    else:
        raise ValueError("Use a YouTube creator homepage, /user/name, or @handle.")
    if not _NAME_RE.fullmatch(name) or (tail and (len(tail) != 1 or tail[0] not in _TABS)):
        raise ValueError("Use a YouTube creator homepage or @handle.")
    return "https://www.youtube.com/" + root


def parse_listing(raw: dict) -> dict:
    """Channels may contain nested playlists for videos, shorts, and streams."""
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        raise ValueError("YouTube did not return a creator's video list.")
    items = {}

    def visit(node):
        if not isinstance(node, dict):
            return
        if isinstance(node.get("entries"), list):
            for child in node["entries"]:
                visit(child)
            return
        video_id = node.get("id")
        if not isinstance(video_id, str) or not VIDEO_ID_RE.fullmatch(video_id):
            return
        title = str(node.get("title") or video_id)
        reason = ""
        if node.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
            reason = "Live now" if node["live_status"] == "is_live" else "Not ready yet"
        elif node.get("availability") in {"private", "premium_only", "subscriber_only", "needs_auth"}:
            reason = "Restricted"
        elif title.lower() in {"[private video]", "[deleted video]", "private video", "deleted video"}:
            reason = "Unavailable"
        items.setdefault(video_id, {
            "video_id": video_id,
            "title": title,
            "duration": node.get("duration"),
            "channel": node.get("channel") or node.get("uploader") or "",
            "unavailable": reason,
        })

    visit(raw)
    return {"title": raw.get("channel") or raw.get("uploader") or raw.get("title") or "Creator",
            "items": list(items.values())}


def fetch_listing(url: str) -> dict:
    try:
        result = subprocess.run(
            [yt_dlp_bin(), "--ignore-config", "--cookies-from-browser", "firefox",
             "--flat-playlist", "--dump-single-json", "--skip-download", "--ignore-errors",
             "--socket-timeout", "20", "--retries", "2", "--extractor-retries", "2", url],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Creator lookup timed out. Try again.") from exc
    try:
        listing = parse_listing(json.loads(result.stdout))
    except (ValueError, TypeError) as exc:
        detail = result.stderr.strip().splitlines()[-1:] or ["No video list returned."]
        raise RuntimeError(detail[0][:1000]) from exc
    # Never present a partial/error result as a complete channel.
    listing["warning"] = (
        "YouTube returned an incomplete list. You can get the listed items or retry. "
        + result.stderr.strip()[-1500:]
        if result.returncode or "ERROR:" in result.stderr else ""
    )
    return listing


class CreatorBrowser:
    """Bounded, expiring previews. Only explicitly queued downloads are durable."""
    def __init__(self, data_dir, queue):
        self.data_dir = data_dir
        self.queue = queue
        self._lock = threading.Lock()
        self._lookups = {}

    def start(self, source: str) -> dict:
        url = creator_url(source)
        with self._lock:
            self._lookups = {key: item for key, item in self._lookups.items()
                             if time.monotonic() - item["touched"] < 3600}
            for item in self._lookups.values():
                if item["url"] == url and item["status"] == "loading":
                    return self._summary(item)
            if sum(item["status"] == "loading" for item in self._lookups.values()) >= 2:
                raise ValueError("Two creators are already loading. Please wait for a lookup to finish.")
            if len(self._lookups) >= 16:
                oldest = min((key for key in self._lookups
                              if self._lookups[key]["status"] != "loading"),
                             key=lambda key: self._lookups[key]["touched"])
                del self._lookups[oldest]
            item = {"lookup_id": uuid.uuid4().hex, "url": url, "status": "loading",
                    "title": source, "items": [], "warning": "", "error": "",
                    "touched": time.monotonic()}
            self._lookups[item["lookup_id"]] = item
            threading.Thread(target=self._load, args=(item,), daemon=True).start()
            return self._summary(item)

    def _load(self, item):
        try:
            result = fetch_listing(item["url"])
            with self._lock:
                item.update(result, status="done")
        except Exception as exc:
            with self._lock:
                item.update(status="error", error=str(exc))

    @staticmethod
    def _summary(item):
        return {key: value for key, value in item.items() if key not in {"items", "touched"}}

    def get(self, lookup_id):
        with self._lock:
            item = self._lookups.get(lookup_id)
            if not item:
                return None
            if time.monotonic() - item["touched"] >= 3600:
                del self._lookups[lookup_id]
                return None
            item["touched"] = time.monotonic()
            result = {**self._summary(item), "items": [dict(row) for row in item["items"]]}
        if result["status"] == "done":
            open_ids = {job["video_id"] for job in db.list_jobs(self.data_dir)
                        if job["status"] in {"queued", "running"}}
            for row in result["items"]:
                row["archived"] = find_video_file(self.data_dir, row["video_id"]) is not None
                row["queued"] = row["video_id"] in open_ids
        return result

    def enqueue(self, lookup_id, *, video_ids=None, all_items=False):
        listing = self.get(lookup_id)
        if not listing:
            raise ValueError("This preview expired or the service restarted. Browse the creator again.")
        if listing["status"] != "done":
            raise ValueError("Wait for the creator's list to finish loading.")
        eligible = {row["video_id"] for row in listing["items"] if not row["unavailable"]}
        if all_items:
            selected = [row["video_id"] for row in listing["items"] if row["video_id"] in eligible]
        else:
            if not isinstance(video_ids, list) or not video_ids or any(
                    not isinstance(vid, str) or vid not in eligible for vid in video_ids):
                raise ValueError("Select available videos from this creator's list.")
            selected = list(dict.fromkeys(video_ids))
        return self.queue.submit_many(selected)
