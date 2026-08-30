"""In-process per-video locks shared by queues and editing requests."""
from __future__ import annotations

import threading
from contextlib import contextmanager

_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


@contextmanager
def video_lock(video_id: str):
    with _guard:
        lock = _locks.setdefault(video_id, threading.RLock())
    with lock:
        yield


@contextmanager
def try_video_lock(video_id: str):
    with _guard:
        lock = _locks.setdefault(video_id, threading.RLock())
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()
