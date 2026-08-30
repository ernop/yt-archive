# Setup

Parent: [agents.md](../agents.md).

## Tools on PATH

- `yt-dlp` — official standalone binary (`yt-dlp -U` to self-update). Distro
  copies are usually stale.
- `ffmpeg` — shot keyframes and `_condensed/soundtrack.mp3` (libmp3lame, VBR `-q:a 0`)
- A JS runtime yt-dlp can use (Deno is the default; Node also works)
- Firefox — cookie source for YouTube downloads

## Python venv (this repo)

`./yt` runs `.venv/bin/python` in this directory and nothing else. If that
file is missing, it exits and names this page.

```sh
cd <this-repo>
python3 -m venv .venv
.venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
.venv/bin/pip install open_clip_torch faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12
```

CPU torch is the intended install: 2 fps CLIP sampling is the slow step
and is already fast enough for typical clips (a 5–6 min video is a few
hundred samples). CUDA torch is the upgrade if hour-long videos become
routine.

`faster-whisper` uses CTranslate2 independently of the CPU-only PyTorch used
by framesheets. The NVIDIA wheels make CUDA 12 math libraries available
without a machine-wide toolkit install. It selects CUDA float16 when the local
GPU is available and falls back to CPU int8. The highest-quality `large-v3`
model downloads on the first explicit transcription; transcription is never
automatic. See [transcription.md](transcription.md).

`.venv/` is gitignored. A symlink at `.venv` to an already-built equivalent
venv (torch, OpenCLIP, faster-whisper) also satisfies `./yt`.

## Live service

`./yt serve` binds `127.0.0.1:8765`. On this fleet the user unit
`yt-archive.service` is the sole owner of that port; dashboard Start/Stop
go through `systemctl --user`. ExecStart is this repo's
`.venv/bin/python -m yt_archive serve --host 127.0.0.1 --port 8765`.
