# Setup

Parent: [agents.md](../agents.md).

## Tools on PATH

- `yt-dlp` — official standalone binary (`yt-dlp -U` to self-update). Distro
  copies are usually stale.
- `ffmpeg`
- A JS runtime yt-dlp can use (Deno is the default; Node also works)
- Firefox — cookie source for YouTube downloads

## Python venv (this repo)

`./yt` runs `.venv/bin/python` in this directory and nothing else. If that
file is missing, it exits and names this page.

```sh
cd <this-repo>
python3 -m venv .venv
.venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
.venv/bin/pip install open_clip_torch transnetv2-pytorch
```

CPU torch is the intended install: shot detection is the slow step and is
already fast enough for typical clips (a 5–6 min 1080p video is about a
minute; peak RAM ~1.7 GB). CUDA torch is the upgrade if hour-long videos
become routine.

`.venv/` is gitignored. A symlink at `.venv` to an already-built equivalent
venv (torch, OpenCLIP, TransNetV2) also satisfies `./yt`.

## Live service

`./yt serve` binds `127.0.0.1:8765`. On this fleet the user unit
`yt-archive.service` is the sole owner of that port; dashboard Start/Stop
go through `systemctl --user`. ExecStart is this repo's
`.venv/bin/python -m yt_archive serve --host 127.0.0.1 --port 8765`.
