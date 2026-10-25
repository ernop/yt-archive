# yt-archive — Agent Entry Point

Start here.

## What this is

Local YouTube archive: download a video, build a squash contact sheet of its
distinct shots, browse and play the result. Videos in tweets on X go through
the same pipeline ([docs/download.md](docs/download.md#x-tweets-settled-2026-10-25)).
Public GitHub intro: [README.md](README.md).

Sibling of [matthoom](https://github.com/ernop/matthoom). Matthoom owns watch
history and `to_archive` marks. This process owns the files and the ML venv
(torch / OpenCLIP stay out of Django).

## Docs

| Doc | What it is |
|-----|------------|
| [README.md](README.md) | Public GitHub intro — what this is, how to invoke it |
| [docs/setup.md](docs/setup.md) | New-machine install, `.venv`, the media player copy, live service |
| [docs/download.md](docs/download.md) | yt-dlp flags, cookies, JS runtime; X tweets (`x-<tweet id>`, no account, own budget) — settled, do not reinvent |
| [docs/framesheets.md](docs/framesheets.md) | Shot pipeline, thresholds, rejected approaches |
| [docs/browsing.md](docs/browsing.md) | Download queue panel, archive status and update, single-line list rows, order and sort, creator pages, the YouTube picker for creators and playlists (and which lists use your account), the hourly YouTube request budget, text contrast, the video page and player |
| [docs/bookmarklet.md](docs/bookmarklet.md) | The **+ ytarchive** bookmarklet: queue the YouTube page or tweet you're on, its key, why a pop-up |
| [docs/transcription.md](docs/transcription.md) | Explicit local Whisper jobs, timed transcript UI, captions, speakers |

Every durable note in this repo is in that list or linked from one of those
files. Do not leave decisions only in chat.

## How you use it

The main path is the live service, not the CLI.

`yt-archive.service` (user unit) owns `127.0.0.1:8765`. Open
`http://127.0.0.1:8765/` — or `http://ytarchive.localhost` on boxes with
the Caddy `*.localhost` proxy. Paste one video URL or a pile of them; each
becomes a queued job. Creator homepages, handles, and playlist links open the
`/browse` picker: sections, playlists, filters, one-by-one review, and bulk
get (see [docs/browsing.md](docs/browsing.md#browse-youtube)). Everything
this repo asks of YouTube stays inside the hourly budgets in
`yt_archive/budget.py`; listings send cookies only for the user's own Watch
later, Liked videos, and private playlists.
The **+ ytarchive** bookmarklet on that page queues the YouTube page you are
on in one click ([docs/bookmarklet.md](docs/bookmarklet.md)).
Jobs run one at a time: download, then shots, then
MP3 audio, shown live in the page's **download queue** panel. The same page
lists and searches the archive, and offers **Update N videos** when older
videos lack current work (see [docs/browsing.md](docs/browsing.md)); each item
has a player, framesheet, MP3 audio, and optional user-triggered transcript.

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
./yt get <url-or-id>           # download + shots + MP3 audio
./yt get <url-or-id> --skip-shots
./yt shots <id>
./yt audio <id>                # create MP3 audio (video already on disk)
./yt transcribe <id>           # explicit local Whisper run
./yt list
./yt reindex                   # rebuild sqlite catalog from data/
./yt serve [--host 127.0.0.1] [--port 8765]
```

## Layout

```
data/<id>/
  <title>-<id>.mkv          # original, sacred — never overwrite or transcode
  *.info.json  *.webp  archive.json
  _condensed/
    framesheet.png          # or framesheet-1.png, framesheet-2.png … (docs/framesheets.md)
    preview.jpg             # sheet 1 at 480 px wide, for the archive list
    soundtrack.mp3
    transcript.json
    transcript.vtt
    shots.json
    shots/0000.png …
```

Files are the source of truth. `data/ytarchive.sqlite` is a catalog so
list/search does not re-read every `archive.json`. `./yt reindex` rebuilds it.

`data/bookmarklet.key` is the bookmarklet's secret (see
[docs/bookmarklet.md](docs/bookmarklet.md)); it is not rebuildable from the
archive, only replaceable.

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
  YouTube ids (`[A-Za-z0-9_-]`, case-sensitive) pass through unchanged; a
  tweet's video is `x-<tweet id>` (never 11 characters, so never a YouTube id).
- X is read without the user's X account. Never add cookies or a login to X
  downloads without the user's explicit approval.
- Shot and download recipes are settled. Read [docs/download.md](docs/download.md)
  and [docs/framesheets.md](docs/framesheets.md) before changing either.
  Do not reintroduce the rejected approaches.
- This repo is **public**. Do not name private repos, private paths, or
  other machines' internal layout in any committed file.
