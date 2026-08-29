# yt-archive — Agent Entry Point

Start here.

## What this is

Local YouTube archive: download a video, build a squash contact sheet of its
distinct shots, browse and play the result. Public GitHub intro:
[README.md](README.md).

Sibling of [matthoom](https://github.com/ernop/matthoom). Matthoom owns watch
history and `to_archive` marks. This process owns the files and the ML venv
(torch / OpenCLIP / TransNetV2 stay out of Django).

## Docs

| Doc | What it is |
|-----|------------|
| [README.md](README.md) | Public GitHub intro — what this is, how to invoke it |
| [docs/setup.md](docs/setup.md) | New-machine install, `.venv`, live service |
| [docs/download.md](docs/download.md) | yt-dlp flags, cookies, JS runtime — settled, do not reinvent |
| [docs/framesheets.md](docs/framesheets.md) | Shot pipeline, thresholds, rejected approaches |

Every durable note in this repo is in that list or linked from one of those
files. Do not leave decisions only in chat.

## How you use it

The main path is the live service, not the CLI.

`yt-archive.service` (user unit) owns `127.0.0.1:8765`. Open
`http://127.0.0.1:8765/` — or `http://ytarchive.localhost` on boxes with
the Caddy `*.localhost` proxy. Paste one URL or a pile of them; each
becomes a queued job. Jobs run one at a time: download, then shots, then
soundtrack. The same page lists and searches the archive; each item has a
player, framesheet, and soundtrack.

```sh
systemctl --user status yt-archive.service
systemctl --user restart yt-archive.service
```

Do not start a second `./yt serve` while that unit is up. If the unit is
not installed, stop and ask. Install notes: [docs/setup.md](docs/setup.md).

Matthoom can also enqueue work:

```sh
uv run python manage.py archive_youtube <id>
uv run python manage.py archive_youtube --pending
```

## CLI

`./yt` is the only command-line entry point. It runs `-m yt_archive` with
this repo's `.venv` (see [docs/setup.md](docs/setup.md)). Do not invent a
second launcher. Use it for one-off get/shots/audio/reindex, or to run the
server when the unit is not in play.

```sh
./yt get <url-or-id>           # download + shots + soundtrack mp3
./yt get <url-or-id> --skip-shots
./yt shots <id>
./yt audio <id>                # dump soundtrack mp3 (video already on disk)
./yt list
./yt reindex                   # rebuild sqlite catalog from data/
./yt serve [--host 127.0.0.1] [--port 8765]
```

## Layout

```
data/<id>/
  <title>-<id>.mkv          # original, sacred — never overwrite or transcode
  *.info.json  *.webp  archive.json  index.html
  _condensed/
    framesheet.png
    soundtrack.mp3
    shots.json
    shots/0000.png …
```

Files are the source of truth. `data/ytarchive.sqlite` is a catalog so
list/search does not re-read every `archive.json`. `./yt reindex` rebuilds it.

`YT_ARCHIVE_DATA` overrides the data root. Default is `./data`.

## Session commands

- **pstatus** — report unpublished work; do not commit, push, or deploy unless asked.
- **ppush** — if uncommitted diffs are one coherent finished change, commit and push. Otherwise report and stop.
- **pdeploy** — ppush, then restart the written live target: user unit
  `yt-archive.service` (`127.0.0.1:8765`).
  `systemctl --user restart yt-archive.service`.
  If that unit is not installed, **stop and ask**. Do not guess another host
  or start a second copy on a new port.

## Rules

- Originals stay untouched. Derived work goes in `_condensed/`.
- Filenames: `[A-Za-z0-9._-]` only. yt-dlp uses `--restrict-filenames`.
  YouTube ids (`[A-Za-z0-9_-]`, case-sensitive) pass through unchanged.
- Shot and download recipes are settled. Read [docs/download.md](docs/download.md)
  and [docs/framesheets.md](docs/framesheets.md) before changing either.
  Do not reintroduce the rejected approaches.
- This repo is **public**. Do not name private repos, private paths, or
  other machines' internal layout in any committed file.
