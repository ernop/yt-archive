"""Local gitignored settings (settings.json at the repo root, settings.example.json checked in)."""
from __future__ import annotations

import json
import math
from pathlib import Path

DEFAULT_LIVE_CLIP_MAX_SECONDS = 3600
DEFAULT_ENGLISH_SETTINGS = {
    "provider": "openai",
    "model": "gpt-6-luna",
    "effort": "low",
    "api_key": "",
    "ollama_url": "http://127.0.0.1:11434",
    "diarize_python": "",
    "kokoro_model_dir": "",
    "chunk_size": 120,
    "concurrency": 6,
}
ENGLISH_PROVIDERS = frozenset({"ollama", "anthropic", "openai", "gemini"})
ENGLISH_EFFORTS = frozenset({"", "low", "medium", "high"})

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"
_KNOWN_KEYS = frozenset({"live_clip_max_seconds", "english"})
_KNOWN_ENGLISH_KEYS = frozenset(DEFAULT_ENGLISH_SETTINGS)


def _validate_english(raw, target: Path) -> dict:
    if raw is None:
        return dict(DEFAULT_ENGLISH_SETTINGS)
    if not isinstance(raw, dict):
        raise ValueError(f"{target}: english must be a JSON object")
    unknown = sorted(set(raw) - _KNOWN_ENGLISH_KEYS)
    if unknown:
        raise ValueError(f"{target}: unknown english setting(s): {', '.join(unknown)}")
    cfg = {**DEFAULT_ENGLISH_SETTINGS, **raw}
    if cfg["provider"] not in ENGLISH_PROVIDERS:
        raise ValueError(
            f"{target}: english.provider must be one of {', '.join(sorted(ENGLISH_PROVIDERS))}"
        )
    for key in ("model", "api_key", "ollama_url", "diarize_python", "kokoro_model_dir", "effort"):
        if not isinstance(cfg[key], str):
            raise ValueError(f"{target}: english.{key} must be a string")
        cfg[key] = cfg[key].strip()
    if not cfg["model"]:
        raise ValueError(f"{target}: english.model must be non-empty")
    if cfg["ollama_url"] and not cfg["ollama_url"].startswith(("http://", "https://")):
        raise ValueError(f"{target}: english.ollama_url must start with http:// or https://")
    if cfg["effort"] not in ENGLISH_EFFORTS:
        raise ValueError(
            f"{target}: english.effort must be one of low, medium, high, or empty"
        )
    for key, lo, hi in (("chunk_size", 10, 300), ("concurrency", 1, 12)):
        val = cfg[key]
        if isinstance(val, bool) or not isinstance(val, int) or not (lo <= val <= hi):
            raise ValueError(f"{target}: english.{key} must be an integer between {lo} and {hi}")
    return cfg


def load_settings(path: Path | None = None) -> dict:
    """Read local settings. Missing file uses defaults; a malformed file fails at once."""
    target = Path(path) if path is not None else SETTINGS_PATH
    if not target.exists():
        return {
            "live_clip_max_seconds": float(DEFAULT_LIVE_CLIP_MAX_SECONDS),
            "english": dict(DEFAULT_ENGLISH_SETTINGS),
        }
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
    return {
        "live_clip_max_seconds": float(cap),
        "english": _validate_english(raw.get("english"), target),
    }
