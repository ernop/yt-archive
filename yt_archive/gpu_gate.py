"""The GPU gate: every program on this computer holds the GPU through matthoom's
core/utils/gpu.py before it loads anything there, so none runs out of GPU memory beside
another (docs/transcription.md#the-gpu-gate)."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

# Whisper large-v3 in float16 had at most 4.4 GB on the GPU on 6 minutes of speech and 5.3 GB
# on 78, read every 5 s by `gpu run` (2026-11-10): longer audio reaches a little higher. The
# other models are smaller.
WHISPER_GB = 7.0
# The same runs: 13.7 and 11.4 times as fast as the audio plays (the second beside a busy
# processor), after a 4 to 5 s load.
WHISPER_SPEED = 10

# A transcript clicked for on the video page is waited on; one run by hand is backfill.
CLICKED = {"who": "ytarchive transcripts", "lane": "now"}
BY_HAND = {"who": "ytarchive transcribe, run by hand", "lane": "backfill"}


def gate_path() -> Path:
    configured = os.environ.get("YT_ARCHIVE_GPU_GATE")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "matthoom" / "core" / "utils" / "gpu.py"


def load():
    """The gate's module, or None on a computer without it."""
    path = gate_path()
    if not path.is_file():
        return None
    loaded = sys.modules.get("gpu")
    if loaded is not None and getattr(loaded, "__file__", "") == str(path):
        return loaded
    spec = importlib.util.spec_from_file_location("gpu", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gpu"] = module  # its dataclasses look the module up while it loads
    spec.loader.exec_module(module)
    return module


def whisper_minutes(seconds: float) -> float:
    """How long a Whisper hold says it lasts, for audio of this many seconds."""
    return round(5 + float(seconds or 0) / 60 / WHISPER_SPEED, 1)
