"""Read-only listings of an X profile's videos for the picker (docs/browsing.md, "X
profiles"). X shows a profile's Media tab, newest first, only to signed-in readers, so it is
read with the user's X login from Firefox, through gallery-dl, on its own hourly budget."""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from urllib.parse import unquote

from . import budget, db

KIND = "x-profile"
HOSTS = {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
_HANDLE_RE = re.compile(r"[A-Za-z0-9_]{1,15}\Z")
# Profile tabs a pasted link may point at; each is listed from the profile's Media tab.
_TABS = {"media", "with_replies", "highlights"}
# x.com pages that are not profiles.
_RESERVED = {
    "home", "explore", "notifications", "messages", "i", "settings", "search", "compose", "hashtag",
    "intent", "share", "login", "logout", "signup", "tos", "privacy", "about", "jobs", "download",
    "lists", "bookmarks", "communities", "premium", "account",
}
_TWEET_ID_RE = re.compile(r"[0-9]{10,19}\Z")
_POSTER_RE = re.compile(r"https://pbs\.twimg\.com/[\w./-]+(\?[\w=&.-]*)?\Z")
LISTING_TIMEOUT = 20 * 60
# gallery-dl pauses this long before every request after the first, logging "Sleeping …
# (request)" first; that line is where each request is paid for, before it is sent. urllib3
# logs every answer, so a request sent without that line is paid for right after it.
SLEEP_REQUEST = "2"
_SLEEP_RE = re.compile(r"\[twitter\]\[debug\] Sleeping [\d.]+ seconds \(request\)")
_ANSWER_RE = re.compile(r'\[urllib3\.connectionpool\]\[debug\] https://\S+ "[A-Z]+ ')
_ERROR_RE = re.compile(r"\[[\w.-]+\]\[error\] (.+)")
# Only the profile's own tweets and their videos, one request per page of up to 50 tweets,
# nothing fetched beyond the timeline. Video posters arrive as "preview" files. X's rate limit stops
# the listing instead of being waited out.
_OPTIONS = (
    "videos=true", "previews=true", "size=small", "retweets=false", "quoted=false", "pinned=false",
    "expand=false", "conversations=false", "cards=false", "text-tweets=false", "ratelimit=abort",
    "retries-api=2",
)
# One JSON line per file found; gallery-dl adds no newline after an expression. A video's
# poster comes right after it.
_PRINT = (
    "prepare:\fE std['json'].dumps({"
    "'tweet': locals().get('tweet_id'), 'type': locals().get('type'), "
    "'duration': locals().get('duration'), 'url': locals().get('_url'), "
    "'sensitive': locals().get('sensitive'), 'flags': locals().get('sensitive_flags'), "
    "'views': locals().get('view_count'), 'text': locals().get('content'), "
    "'date': str(locals().get('date')), 'nick': (locals().get('user') or {}).get('nick'), "
    "'name': (locals().get('user') or {}).get('name')}, ensure_ascii=False, default=str) + chr(10)"
)
# What gallery-dl's X errors mean for the user, by a phrase of the message.
_SIGN_IN = ("X wants a signed-in reader, and the X login from Firefox did not work. Sign in to x.com "
            "in Firefox, then refresh.")
_EXPLAINED = (
    ("temporarily locked", "X has temporarily locked your account. Open x.com in Firefox to unlock it."),
    ("Rate limit exceeded", "X says your account has read enough for now. Try again later."),
    ("could not be found", "X has no account by that name."),
    ("Tweets are protected", "That account's tweets are protected, and your X account does not follow it."),
    ("blocked your account", "That account has blocked your X account."),
    ("needed to access", _SIGN_IN),
    ("credentials required", _SIGN_IN),
    ("Could not authenticate you", _SIGN_IN),
)


def profile_source(path: str) -> dict:
    """The listing target for the path of a pasted x.com or twitter.com link."""
    parts = unquote(path).strip("/").split("/")
    if len(parts) >= 3 and parts[1] == "status":
        raise ValueError("That is a single tweet. Use Get on the archive page.")
    handle, tail = parts[0], parts[1:]
    if handle.lower() in _RESERVED or not _HANDLE_RE.fullmatch(handle) \
            or (tail and (len(tail) != 1 or tail[0] not in _TABS)):
        raise ValueError("Use an X profile link: x.com/name.")
    return {"kind": KIND, "url": f"https://x.com/{handle.lower()}/media", "section": ""}


def gallery_dl_bin() -> str:
    found = shutil.which("gallery-dl")
    if not found:
        raise FileNotFoundError("gallery-dl not on PATH (install it: uv tool install gallery-dl)")
    return found


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return value


def _tweet_time(tweet_id: int, date) -> int | None:
    # Tweet ids since late 2010 carry their posting time in milliseconds.
    if tweet_id >= 300_000_000_000_000:
        return ((tweet_id >> 22) + 1_288_834_974_657) // 1000
    try:
        return int(datetime.strptime(str(date), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


class ProfileParser:
    """Builds an X profile's listing from the lines _PRINT makes gallery-dl write: the first
    video of each tweet, with its poster. Tweets X marks sensitive are kept in hidden: X
    shows them only to signed-in readers."""

    def __init__(self, handle: str):
        self.handle = handle
        self.items: dict[str, dict] = {}
        self.hidden: set[str] = set()
        self.nick = self.screen_name = ""
        self._poster_for = ""

    def feed(self, line: str) -> None:
        try:
            raw = json.loads(line)
        except ValueError:
            return
        if not isinstance(raw, dict) or not _TWEET_ID_RE.fullmatch(str(raw.get("tweet"))):
            return
        self.nick = self.nick or str(raw.get("nick") or "")[:200]
        if not self.screen_name and _HANDLE_RE.fullmatch(str(raw.get("name") or "")):
            self.screen_name = raw["name"]
        video_id = "x-" + str(raw["tweet"])
        poster_for, self._poster_for = self._poster_for, ""
        if raw.get("type") == "preview":
            if video_id == poster_for and _POSTER_RE.match(str(raw.get("url") or "")):
                self.items[video_id]["thumb"] = raw["url"]
            return
        if raw.get("type") != "video" or video_id in self.items:
            return
        hidden = bool(raw.get("sensitive") or raw.get("flags"))
        if hidden:
            self.hidden.add(video_id)
        duration = _number(raw.get("duration"))
        self.items[video_id] = {
            "video_id": video_id,
            "title": " ".join(str(raw.get("text") or "").split())[:500] or "(no text)",
            "duration": round(duration) if duration is not None else None,
            "views": int(_number(raw.get("views")) or 0) or None,
            "timestamp": _tweet_time(int(raw["tweet"]), raw.get("date")),
            "section": "",
            "position": len(self.items) + 1,
            "channel": self.nick,
            "unavailable": "",
            "thumb": "",
            "hidden": hidden,
        }
        self._poster_for = video_id

    @property
    def name(self) -> str:
        handle = "@" + (self.screen_name or self.handle)
        return f"{self.nick} ({handle})" if self.nick else handle

    def result(self) -> dict:
        return {"title": self.name, "channel": self.nick, "channel_id": "", "items": list(self.items.values())}


def _explain(message: str) -> str:
    return next((plain for phrase, plain in _EXPLAINED if phrase in message), message)


def fetch_profile(url: str, data_dir, progress=None) -> dict:
    """List the videos on an X profile's Media tab with gallery-dl, metadata only, signed in
    with the user's X login from Firefox (only its x.com cookies are read). The caller has
    paid for the first request; each later one is paid for from the hourly X listing budget,
    and when the budget runs out gallery-dl is stopped and the videos already found are
    kept. Tweets X marks sensitive are noted, so their downloads go straight to the login."""
    command = [
        gallery_dl_bin(), "--config-ignore", "--no-colors", "--no-input", "--verbose",
        "--cookies-from-browser", "firefox/.x.com",
        "--sleep-request", SLEEP_REQUEST, "--retries", "2", "--http-timeout", "20",
        *[arg for option in _OPTIONS for arg in ("-o", "extractor.twitter." + option)],
        "--print", _PRINT, url,
    ]
    parser = ProfileParser(url.split("/")[3])
    state = {"requests": 1, "answers": 0, "errors": [], "stopped": ""}
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace") as proc:

        def report() -> None:
            if progress:
                progress({"found": len(parser.items), "requests": state["requests"],
                          "expected": None, "title": parser.name})

        def stop(reason: str) -> None:
            if not state["stopped"]:
                state["stopped"] = reason
                proc.kill()

        def pay() -> None:
            if budget.take(data_dir, "x-listing"):
                state["requests"] += 1
                report()
            else:
                stop(str(budget.exceeded(data_dir, "x-listing")))

        def read_stderr() -> None:
            for line in proc.stderr:
                if _SLEEP_RE.match(line):
                    pay()
                elif _ANSWER_RE.match(line):
                    state["answers"] += 1
                    if state["answers"] > state["requests"]:
                        pay()
                elif error := _ERROR_RE.match(line):
                    state["errors"].append(error.group(1).strip())

        reader = threading.Thread(target=read_stderr, daemon=True)
        reader.start()
        timer = threading.Timer(LISTING_TIMEOUT, stop, args=("X took longer than 20 minutes.",))
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
    db.mark_x_hidden(data_dir, parser.hidden)
    listing = parser.result()
    errors = [_explain(error) for error in state["errors"]]
    failed = bool(state["stopped"] or proc.returncode or errors)
    if failed and not listing["items"]:
        raise RuntimeError((state["stopped"] or (errors[-1] if errors else "X returned no list."))[:1000])
    if state["stopped"]:
        warning = f"Incomplete: {state['stopped']} The list shows what arrived before that."
    elif failed:
        warning = ("X returned an incomplete list. You can get the listed videos or refresh. "
                   + " · ".join(errors[-3:]))[:1500]
    else:
        warning = ""
    return {**listing, "warning": warning, "requests": state["requests"]}
