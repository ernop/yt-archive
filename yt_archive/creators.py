"""Read-only YouTube listings, separate from the download queue: a creator's uploads, a
creator's playlists, or one playlist (docs/browsing.md, "Browse YouTube")."""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

from . import budget, db
from .download import yt_dlp_bin
from .paths import VIDEO_ID_RE, find_video_file, parse_video_id

CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_-]{22}\Z")
PLAYLIST_ID_RE = re.compile(r"[A-Za-z0-9_-]{2,64}\Z")
LISTING_ID_RE = re.compile(r"[0-9a-f]{16}\Z")
_NAME_RE = re.compile(r"[\w.·-]{1,100}\Z", re.UNICODE)
_TABS = {"featured", "videos", "shorts", "streams", "about", "playlists", "community"}
_RESERVED = _TABS | {"watch", "playlist", "feed", "results", "shorts", "live", "embed", "v", "user", "c", "channel"}
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
# Watch later, Liked videos, and Liked music exist only for a signed-in account.
_ACCOUNT_LISTS = {"WL", "LL", "LM"}
# What YouTube tells anyone but its owner about a private playlist.
_HIDDEN_RE = re.compile(r"YouTube said: The playlist does not exist")
# A channel tab, as the section of the creator's listing it fills.
SECTIONS = {"videos": "videos", "shorts": "shorts", "streams": "live"}
_UNAVAILABLE_TITLES = {"[private video]", "[deleted video]", "private video", "deleted video"}
_THUMB_RE = re.compile(r"https://i\d?\.ytimg\.com/vi/([A-Za-z0-9_-]{11})/")

LOADING_AT_ONCE = 2
LISTING_TIMEOUT = 20 * 60
# A failed listing is shown again, not fetched again, until this old or explicitly retried.
FAILURE_KEPT = 10 * 60
# yt-dlp pauses this long before every request after the first, printing "Sleeping …" first;
# that line is where each request is paid for from the budget, before it is sent.
SLEEP_REQUESTS = "0.75"
_SLEEP_RE = re.compile(r"\[youtube[^\]]*\] Sleeping [\d.]+ seconds")


def listing_source(source: str) -> dict:
    """What to list for a pasted link: {"kind", "url", "section"}.

    kind is "channel" (a creator's uploads), "playlists" (a creator's playlists) or
    "playlist"; section is the channel tab the link pointed at ("videos", "shorts", "live").
    """
    if not isinstance(source, str) or not source.strip() or len(source) > 2048:
        raise ValueError("Paste a YouTube creator homepage, @handle, or playlist link.")
    source = source.strip()
    if CHANNEL_ID_RE.fullmatch(source):
        return _target("channel", "channel/" + source)
    if source.startswith("@") or _NAME_RE.fullmatch(source):
        name = source.removeprefix("@")
        if not _NAME_RE.fullmatch(name):
            raise ValueError("Invalid YouTube handle.")
        return _target("channel", "@" + quote(name))
    parsed = urlsplit(source if "://" in source else "https://" + source)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"} or host not in _HOSTS:
        raise ValueError("Use a YouTube creator homepage, @handle, or playlist link.")
    parts = unquote(parsed.path).strip("/").split("/")
    playlist_id = (parse_qs(parsed.query).get("list") or [""])[0]
    if host in {"youtu.be", "music.youtube.com"} or parts[0] in {"playlist", "watch"} or parts[:2] == ["embed", "videoseries"]:
        if PLAYLIST_ID_RE.fullmatch(playlist_id):
            return {"kind": "playlist", "url": "https://www.youtube.com/playlist?list=" + playlist_id, "section": ""}
        if parts[0] == "playlist" or parts[:2] == ["embed", "videoseries"]:
            raise ValueError("That playlist link has no list= id.")
        if host == "music.youtube.com":
            raise ValueError("Use a youtube.com creator homepage or a playlist link.")
        _single_video(source)
    if parts[0] in {"shorts", "live", "embed", "v"}:
        _single_video(source)
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
        raise ValueError("Use a YouTube creator homepage, /user/name, @handle, or playlist link.")
    if not _NAME_RE.fullmatch(name) or (tail and (len(tail) != 1 or tail[0] not in _TABS)):
        raise ValueError("Use a YouTube creator homepage, @handle, or playlist link.")
    tab = tail[0] if tail else ""
    if tab == "playlists":
        return _target("playlists", root + "/playlists")
    return _target("channel", root, SECTIONS.get(tab, ""))


def _single_video(source: str):
    try:
        parse_video_id(source)
    except ValueError:
        raise ValueError("That link has no valid video or playlist ID.") from None
    raise ValueError("That is a single video. Use Get on the archive page.")


def _target(kind: str, path: str, section: str = "") -> dict:
    return {"kind": kind, "url": "https://www.youtube.com/" + path, "section": section}


def browse_url(source: str) -> str:
    """The picker page for a pasted creator or playlist link."""
    target = listing_source(source)
    query = {"source": target["url"]}
    if target["section"]:
        query["section"] = target["section"]
    return "/browse?" + urlencode(query)


def playlist_browse_url(video_link: str) -> str:
    """The picker for the playlist a pasted video link was playing in, if any. Mixes
    (list=RD…) are made up per viewer and are not offered."""
    try:
        target = listing_source(video_link)
    except ValueError:
        return ""
    if target["kind"] != "playlist" or target["url"].rpartition("=")[2].startswith("RD"):
        return ""
    return browse_url(target["url"])


def needs_account(url: str) -> bool:
    """A list YouTube shows only to the signed-in user, so asking without the account is wasted."""
    return (parse_qs(urlsplit(url).query).get("list") or [""])[0] in _ACCOUNT_LISTS


def listing_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _count(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return int(value)


def _thumb_video_id(raw: dict) -> str:
    for thumb in raw.get("thumbnails") or []:
        match = _THUMB_RE.match(str((thumb or {}).get("url") or "")) if isinstance(thumb, dict) else None
        if match:
            return match.group(1)
    return ""


def parse_entry(raw: dict, kind: str) -> dict | None:
    """One line of yt-dlp's flat listing as a row of the picker, or None to skip it."""
    if not isinstance(raw, dict):
        return None
    if kind == "playlists":
        playlist_id = raw.get("id")
        if raw.get("ie_key") != "YoutubeTab" or not isinstance(playlist_id, str) \
                or not PLAYLIST_ID_RE.fullmatch(playlist_id):
            return None
        return {"playlist_id": playlist_id, "title": str(raw.get("title") or playlist_id)[:500],
                "thumb_id": _thumb_video_id(raw)}
    video_id = raw.get("id")
    if raw.get("ie_key") not in (None, "Youtube") or not isinstance(video_id, str) \
            or not VIDEO_ID_RE.fullmatch(video_id):
        return None
    title = str(raw.get("title") or video_id)[:500]
    live = raw.get("live_status")
    reason = ""
    if live in {"is_live", "is_upcoming", "post_live"}:
        reason = "Live now" if live == "is_live" else "Not ready yet"
    elif raw.get("availability") in {"private", "premium_only", "subscriber_only", "needs_auth"}:
        reason = "Restricted"
    elif title.lower() in _UNAVAILABLE_TITLES:
        reason = "Unavailable"
    section = ""
    if kind == "channel":
        tab = urlsplit(str(raw.get("playlist_webpage_url") or "")).path.rstrip("/").rpartition("/")[2]
        section = SECTIONS.get(tab) or (
            "shorts" if "/shorts/" in str(raw.get("url") or "")
            else "live" if live == "was_live" else "videos"
        )
    return {
        "video_id": video_id,
        "title": title,
        "duration": _count(raw.get("duration")),
        "views": _count(raw.get("view_count")),
        # From YouTube's "3 weeks ago" text, so only as exact as that text.
        "timestamp": _count(raw.get("timestamp")),
        "section": section,
        "position": _count(raw.get("playlist_index")),
        "channel": str(raw.get("channel") or raw.get("uploader") or raw.get("playlist_channel") or "")[:200],
        "unavailable": reason,
    }


class ListingParser:
    """Builds a listing from yt-dlp's --flat-playlist --dump-json lines as they arrive."""

    def __init__(self, kind: str):
        self.kind = kind
        self.items: dict[str, dict] = {}
        self.title = self.channel = self.channel_id = ""
        self.expected: int | None = None

    def feed(self, line: str) -> None:
        try:
            raw = json.loads(line)
        except ValueError:
            return
        if not isinstance(raw, dict):
            return
        self.channel = self.channel or str(raw.get("playlist_channel") or raw.get("playlist_uploader") or "")[:200]
        if not self.channel_id and CHANNEL_ID_RE.fullmatch(str(raw.get("playlist_channel_id") or "")):
            self.channel_id = raw["playlist_channel_id"]
        if self.kind == "playlist":
            self.title = self.title or str(raw.get("playlist_title") or "")[:300]
            if self.expected is None:
                self.expected = _count(raw.get("playlist_count"))
        item = parse_entry(raw, self.kind)
        if item:
            self.items.setdefault(item.get("video_id") or item["playlist_id"], item)

    @property
    def name(self) -> str:
        return self.title if self.kind == "playlist" else self.channel

    def result(self) -> dict:
        return {"title": self.name, "channel": self.channel, "channel_id": self.channel_id,
                "items": list(self.items.values())}


class AccountNeeded(RuntimeError):
    """An anonymous playlist listing YouTube answered as private playlists are answered."""

    def __init__(self, message: str, requests: int):
        super().__init__(message)
        self.requests = requests


def fetch_listing(url: str, kind: str, data_dir, progress=None, account: bool = False) -> dict:
    """List url with yt-dlp, metadata only. Without account no cookies are sent, so YouTube
    does not see the user's account; with it, the Firefox cookies downloads use are sent.
    The caller has paid for yt-dlp's first request; each later one is paid for from the
    hourly budget before it is sent, and when the budget runs out yt-dlp is stopped and the
    items it already printed are kept."""
    command = [
        yt_dlp_bin(), "--ignore-config",
        *(["--cookies-from-browser", "firefox"] if account else []),
        "--flat-playlist", "--lazy-playlist", "--dump-json", "--ignore-errors",
        "--verbose", "--sleep-requests", SLEEP_REQUESTS,
        "--extractor-args", "youtubetab:approximate_date",
        "--socket-timeout", "20", "--retries", "2", "--extractor-retries", "2", url,
    ]
    parser = ListingParser(kind)
    state = {"requests": 1, "errors": [], "stopped": ""}
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace") as proc:

        def report() -> None:
            if progress:
                progress({"found": len(parser.items), "requests": state["requests"],
                          "expected": parser.expected, "title": parser.name})

        def stop(reason: str) -> None:
            if not state["stopped"]:
                state["stopped"] = reason
                proc.kill()

        def read_stderr() -> None:
            for line in proc.stderr:
                if _SLEEP_RE.match(line):
                    if budget.take(data_dir, "listing"):
                        state["requests"] += 1
                        report()
                    else:
                        stop(str(budget.exceeded(data_dir, "listing")))
                elif line.startswith("ERROR:"):
                    state["errors"].append(line.removeprefix("ERROR:").strip())

        reader = threading.Thread(target=read_stderr, daemon=True)
        reader.start()
        timer = threading.Timer(LISTING_TIMEOUT, stop, args=("YouTube took longer than 20 minutes.",))
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout:
                parser.feed(line)
                report()
        except BaseException:
            stop("ytarchive stopped reading the list.")
            raise
        finally:
            proc.wait()
            timer.cancel()
            reader.join(timeout=10)
    listing = parser.result()
    errors = state["errors"]
    failed = bool(state["stopped"] or proc.returncode or errors)
    if failed and not listing["items"]:
        message = (state["stopped"] or (errors[-1] if errors else "YouTube returned no list."))[:1000]
        if kind == "playlist" and not account and not state["stopped"] and any(map(_HIDDEN_RE.search, errors)):
            raise AccountNeeded(message, state["requests"])
        raise RuntimeError(message)
    if state["stopped"]:
        warning = f"Incomplete: {state['stopped']} The list shows what arrived before that."
    elif failed:
        warning = ("YouTube returned an incomplete list. You can get the listed items or refresh. "
                   + " · ".join(errors[-3:]))[:1500]
    else:
        warning = ""
    return {**listing, "warning": warning, "requests": state["requests"]}


class CreatorBrowser:
    """Listings are kept in SQLite until refreshed, so reloads, other tabs and restarts never
    ask YouTube again. One fetch per link at a time, LOADING_AT_ONCE at most, each request
    paid for from the hourly listing budget. Only lists that exist for the user's account
    alone are read with it: account lists from the start, a playlist after YouTube called it
    missing without the account, and a kept list read with the account on every refresh."""

    def __init__(self, data_dir, queue):
        self.data_dir = data_dir
        self.queue = queue
        self._lock = threading.Lock()
        self._fetches: dict[str, dict] = {}

    def start(self, source: str, refresh: bool = False) -> dict:
        target = listing_source(source)
        with self._lock:
            self._fetches = {key: fetch for key, fetch in self._fetches.items()
                             if fetch["status"] == "loading" or time.monotonic() - fetch["at"] < 3600}
            kept = db.get_listing(self.data_dir, listing_id(target["url"])) or self._alias(target)
            url = kept["url"] if kept else target["url"]
            key = listing_id(url)
            fetch = self._fetches.get(key)
            if fetch and fetch["status"] == "loading":
                return self._fetch_summary(fetch)
            if kept and not refresh:
                db.touch_listing(self.data_dir, key)
                return self._summary(kept)
            if fetch and not refresh and time.monotonic() - fetch["at"] < FAILURE_KEPT:
                return self._fetch_summary(fetch)
            if sum(item["status"] == "loading" for item in self._fetches.values()) >= LOADING_AT_ONCE:
                raise ValueError("Two YouTube lists are already loading. Wait for one to finish.")
            if not budget.take(self.data_dir, "listing"):
                exceeded = budget.exceeded(self.data_dir, "listing")
                if kept:
                    return {**self._summary(kept), "error": str(exceeded)}
                raise exceeded
            earlier = kept or fetch or {}
            fetch = {"lookup_id": key, "kind": target["kind"], "url": url, "status": "loading",
                     "title": kept["title"] if kept else "", "found": 0, "requests": 1,
                     "expected": None, "error": "", "at": time.monotonic(),
                     "account": needs_account(url) or bool(earlier.get("account"))}
            self._fetches[key] = fetch
            summary = self._fetch_summary(fetch)
        threading.Thread(target=self._load, args=(fetch,), daemon=True).start()
        return summary

    def _alias(self, target: dict) -> dict | None:
        match = re.fullmatch(r"https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})(?:/playlists)?",
                             target["url"])
        return db.find_channel_listing(self.data_dir, target["kind"], match.group(1)) if match else None

    def _fetch(self, fetch: dict, progress) -> dict:
        try:
            return fetch_listing(fetch["url"], fetch["kind"], self.data_dir, progress, account=fetch["account"])
        except AccountNeeded as hidden:
            spent = hidden.requests
        if not budget.take(self.data_dir, "listing"):
            raise budget.exceeded(self.data_dir, "listing")
        with self._lock:
            fetch.update(account=True, requests=spent + 1)
        result = fetch_listing(fetch["url"], fetch["kind"], self.data_dir,
                               lambda update: progress({**update, "requests": update["requests"] + spent}),
                               account=True)
        return {**result, "requests": result["requests"] + spent}

    def _load(self, fetch: dict) -> None:
        def progress(update: dict) -> None:
            with self._lock:
                fetch.update({key: value for key, value in update.items() if value or key == "found"})

        try:
            result = self._fetch(fetch, progress)
            db.save_listing(self.data_dir, {
                **result, "id": fetch["lookup_id"], "url": fetch["url"], "kind": fetch["kind"],
                "account": fetch["account"], "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
            with self._lock:
                self._fetches.pop(fetch["lookup_id"], None)
        except Exception as exc:
            with self._lock:
                fetch.update(status="error", error=str(exc) or type(exc).__name__, at=time.monotonic())

    @staticmethod
    def _summary(listing: dict) -> dict:
        return {
            "lookup_id": listing["id"], "kind": listing["kind"], "url": listing["url"],
            "title": listing["title"], "channel": listing["channel"], "channel_id": listing["channel_id"],
            "status": "done", "found": len(listing["items"]), "requests": listing["requests"],
            "fetched_at": listing["fetched_at"], "warning": listing["warning"], "error": "",
            "account": bool(listing.get("account")),
        }

    @staticmethod
    def _fetch_summary(fetch: dict) -> dict:
        return {key: value for key, value in fetch.items() if key != "at"}

    def get(self, lookup_id: str) -> dict | None:
        with self._lock:
            fetch = dict(self._fetches.get(lookup_id) or {})
        if fetch.get("status") == "loading":
            return self._fetch_summary(fetch)
        kept = db.get_listing(self.data_dir, lookup_id) if LISTING_ID_RE.fullmatch(lookup_id or "") else None
        if not kept:
            return self._fetch_summary(fetch) if fetch else None
        result = {**self._summary(kept), "items": kept["items"]}
        if fetch.get("status") == "error" and time.monotonic() - fetch["at"] < FAILURE_KEPT:
            result["error"] = "Refresh failed: " + fetch["error"]
        if kept["kind"] != "playlists":
            open_ids = db.open_video_ids(self.data_dir)
            for row in result["items"]:
                row["archived"] = find_video_file(self.data_dir, row["video_id"]) is not None
                row["queued"] = row["video_id"] in open_ids
        return result

    def enqueue(self, lookup_id, *, video_ids=None, all_items=False):
        listing = self.get(lookup_id)
        if not listing:
            raise ValueError("This list is gone. Browse it again.")
        if listing["status"] != "done":
            raise ValueError("Wait for the list to finish loading.")
        if listing["kind"] == "playlists":
            raise ValueError("Open one of the playlists to choose its videos.")
        eligible = {row["video_id"] for row in listing["items"] if not row["unavailable"]}
        if all_items:
            selected = [row["video_id"] for row in listing["items"] if row["video_id"] in eligible]
        else:
            if not isinstance(video_ids, list) or not video_ids or any(
                    not isinstance(vid, str) or vid not in eligible for vid in video_ids):
                raise ValueError("Select available videos from this list.")
            selected = list(dict.fromkeys(video_ids))
        titles = {row["video_id"]: row["title"] for row in listing["items"]}
        return self.queue.submit_many(selected, titles)
