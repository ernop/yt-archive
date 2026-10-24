"""Hard hourly budgets for what this machine asks of YouTube (docs/browsing.md,
"YouTube request budget"). Counted in SQLite, so restarts and the CLI share them."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from . import db

WINDOW = 3600
# yt-dlp's wiki puts YouTube's limits for a guest session at ~1000 webpage/API requests and
# ~300 videos an hour (accounts get about four times that). Each budget is half a guest limit.
LIMITS = {"listing": 500, "download": 150}
NOUNS = {"listing": "listing requests", "download": "video downloads"}


class BudgetExceeded(RuntimeError):
    def __init__(self, kind: str, free_at: float):
        self.kind = kind
        self.free_at = free_at
        super().__init__(
            f"This hour's YouTube budget of {LIMITS[kind]} {NOUNS[kind]} is used up. "
            f"Try again after {clock(free_at)}."
        )


def take(data_dir: Path, kind: str, now: float | None = None) -> bool:
    """Spend one unit of this hour's budget, or return False and spend nothing."""
    return db.take_youtube_request(
        data_dir, kind, LIMITS[kind], time.time() if now is None else now, WINDOW
    )


def free_at(data_dir: Path, kind: str, now: float | None = None) -> float:
    """When take() can next succeed: the moment enough spent units leave the window."""
    now = time.time() if now is None else now
    limit = LIMITS[kind]
    times = db.youtube_request_times(data_dir, kind, now - WINDOW)
    if len(times) < limit:
        return now
    return (times[len(times) - limit] if limit > 0 else now) + WINDOW


def exceeded(data_dir: Path, kind: str) -> BudgetExceeded:
    return BudgetExceeded(kind, free_at(data_dir, kind))


def wait_and_take(data_dir: Path, kind: str, on_wait=None, sleep=time.sleep) -> None:
    """Block until a unit is free, then spend it. on_wait(free_at) is told about each wait."""
    while not take(data_dir, kind):
        at = free_at(data_dir, kind)
        if on_wait:
            on_wait(at)
        sleep(min(60.0, max(1.0, at - time.time())))


def clock(stamp: float) -> str:
    return datetime.fromtimestamp(stamp).strftime("%H:%M")
