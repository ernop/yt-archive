#!/usr/bin/env bash
# Build or repair yt-archive's standalone Python .venv (docs/setup.md).
# Usage: scripts/setup-venv.sh [--target PATH] [--cpu-only]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT/.venv"
CPU_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      TARGET="$2"
      shift 2
      ;;
    --cpu-only)
      CPU_ONLY=1
      shift
      ;;
    *)
      echo "usage: $0 [--target PATH] [--cpu-only]" >&2
      exit 2
      ;;
  esac
done

if [[ -L "$TARGET" ]]; then
  echo "error: $TARGET is a symlink ($(readlink "$TARGET")); remove it before building a standalone venv" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required on PATH (https://docs.astral.sh/uv/)" >&2
  exit 1
fi

if [[ ! -x "$TARGET/bin/python" ]]; then
  uv venv --python python3 "$TARGET"
fi
PY="$TARGET/bin/python"

# 1. CPU-only PyTorch for OpenCLIP framesheets (keeps venv small; CUDA stays in ORT/CTranslate2).
uv pip install --python "$PY" \
  --index-url https://download.pytorch.org/whl/cpu \
  torch==2.13.0+cpu torchvision==0.28.0+cpu

# 2. Project dependencies from requirements.txt.
uv pip install --python "$PY" -r "$ROOT/requirements.txt"

# 3. faster-whisper and kokoro-onnx declare CPU `onnxruntime`, which overwrites
#    onnxruntime-gpu's shared libraries if left installed. Remove CPU onnxruntime
#    and reinstall pinned CUDA 12 onnxruntime-gpu cleanly.
if [[ "$CPU_ONLY" -eq 0 ]]; then
  uv pip uninstall --python "$PY" onnxruntime >/dev/null 2>&1 || true
  uv pip install --python "$PY" --reinstall --no-deps onnxruntime-gpu==1.24.4
fi

# 4. Verify the finished environment loudly.
"$PY" - "$CPU_ONLY" <<'PY'
import sys
import torch
import torchvision
import open_clip
import faster_whisper
import kokoro_onnx
import soundfile
import onnxruntime as ort

cpu_only = sys.argv[1] == "1"
if "+cpu" not in torch.__version__:
    raise SystemExit(f"expected CPU torch build, got {torch.__version__}")
if not cpu_only:
    if ort.__version__ != "1.24.4":
        raise SystemExit(f"expected onnxruntime-gpu 1.24.4, got {ort.__version__}")
    providers = ort.get_available_providers()
    if "CUDAExecutionProvider" not in providers:
        raise SystemExit(f"CUDAExecutionProvider missing (got {providers})")
    if hasattr(ort, "preload_dlls"):
        ort.preload_dlls()
print(
    f"yt-archive venv ready: python={sys.version.split()[0]} "
    f"torch={torch.__version__} ort={ort.__version__} "
    f"providers={ort.get_available_providers()}"
)
PY
