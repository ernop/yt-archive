"""Local gitignored settings (settings.json at the repo root, settings.example.json checked in)."""
from __future__ import annotations

import json
import math
from pathlib import Path

DEFAULT_LIVE_CLIP_MAX_SECONDS = 3600
SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"
_KNOWN_KEYS = frozenset({"live_clip_max_seconds"})


def load_settings(path: Path | None = None) -> dict:
    """Read local settings. Missing file uses defaults; a malformed file fails at once."""
    target = Path(path) if path is not None else SETTINGS_PATH
    if not target.exists():
        return {"live_clip_max_seconds": DEFAULT_LIVE_CLIP_MAX_SECONDS}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{target}: could not read settings JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{target}: settings must be a JSON object")
    unknown = sorted(set(raw) - _KNOWN_KEYS)
    if unknown:
        raise ValueError(f"{target}: unknown setting(s): {', '.join(unknown)}")
    cap = raw.get("live_clip_max_seconds", DEFAULT_LIVE_CLIP_MAX_SECONDS)
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) or cap <= 0:
        raise ValueError(f"{target}: live_clip_max_seconds must be a positive number")
    return {"live_clip_max_seconds": float(cap)}
