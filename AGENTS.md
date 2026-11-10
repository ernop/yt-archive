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

## Direction (2026-11-10)

The archive is broadening: everything the user watches or listens to is to be
archived automatically, with exclusions added later. Other sessions are
building that intake; do not start it here until the user settles it with
them.

Considered on 2026-11-10 and not being built now:

- A music shelf (ID3 tags and cover art on the MP3s, a Listen page that plays
  one after another): deferred until the user decides it is worth having.
- Following creators so their new videos download by themselves: no automatic
  downloads yet.
- Finding a moment by describing it (CLIP vectors of every shot, transcripts
  of every spoken video): not now.
- Lighter video pages (shot images loaded as you scroll): not now.

## Docs

| Doc | What it is |
|-----|------------|
| [README.md](README.md) | Public GitHub intro — what this is, how to invoke it |
| [docs/setup.md](docs/setup.md) | New-machine install, `.venv`, the media player copy, live service |
| [docs/download.md](docs/download.md) | yt-dlp flags, cookies, JS runtime, its weekly self-update; X tweets (`x-<tweet id>`, no account unless X hides the tweet, own budgets) — settled, do not reinvent |
| [docs/framesheets.md](docs/framesheets.md) | Shot pipeline, thresholds, rejected approaches |
| [docs/browsing.md](docs/browsing.md) | Download queue panel, archive status and update, single-line list rows, order and sort, creator pages, the picker for YouTube creators and playlists (and which lists use your account) and X profiles (with your X login, via gallery-dl), the hourly YouTube and X request budgets, text contrast, the video page and player |
| [docs/bookmarklet.md](docs/bookmarklet.md) | The **+ ytarchive** bookmarklet: queue the YouTube page or tweet you're on, its key, why a pop-up |
| [docs/transcription.md](docs/transcription.md) | Explicit local Whisper jobs, the GPU gate they hold, timed transcript UI, captions, speakers |

Every durable note in this repo is in that list or linked from one of those
files. Do not leave decisions only in chat.

## How you use it

The main path is the live service, not the CLI.

`yt-archive.service` (user unit) owns `127.0.0.1:8765`. Open
`http://127.0.0.1:8765/` — or `http://ytarchive.localhost` on boxes with
the Caddy `*.localhost` proxy. Paste one video URL or a pile of them; each
becomes a queued job. Creator homepages, handles, playlist links, and X
profile links open the `/browse` picker: sections, playlists, filters,
one-by-one review, and bulk get (see
[docs/browsing.md](docs/browsing.md#browse-youtube)). Everything this repo
asks of YouTube or X stays inside the hourly budgets in
`yt_archive/budget.py`; YouTube listings send cookies only for the user's own
Watch later, Liked videos, and private playlists, and X profiles are always
listed with the user's X login.
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
./yt shots <id> --detail       # also split on small changes (docs/framesheets.md)
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
    soundtrack.lrc          # lyrics a local client sent (docs/browsing.md, "Lyrics")
    transcript.json
    transcript.vtt
    shots.json
    shots/0000.png …
```

Code, in `yt_archive/`:

```
cli.py                 ./yt commands
web/                   the service: server.py (routes, JSON API, media), home.py, video.py,
                       creator.py (creator pages, /browse picker), add.py (bookmarklet pop-up),
                       html.py (page frame, list rows, formatters)
static/                stylesheets and page scripts (app.css, home.js, video-page.js, …),
                       the media player copy; a page's values reach its scripts as #page-data JSON
jobs.py                download queue: download, shots, MP3 audio, lyrics
work.py                transcript queue; transcribe.py runs Whisper (gpu_gate.py: the GPU gate)
download.py            yt-dlp, and its weekly self-update
framesheet.py          shots and framesheets; audio.py the MP3; lyrics.py
creators.py            YouTube listings for the picker; x_profiles.py X profiles
db/                    SQLite catalog, used as one module (db.list_videos): core.py (schema,
                       connections), videos.py, transcripts.py, adds.py, jobs.py, work.py, listings.py
paths.py               layout, ids, file names
budget.py              hourly request budgets; locks.py per-video locks
```

Files are the source of truth. `data/ytarchive.sqlite` is a catalog so
list/search does not re-read every `archive.json`. `./yt reindex` rebuilds it.

`data/bookmarklet.key` is the bookmarklet's secret (see
[docs/bookmarklet.md](docs/bookmarklet.md)); it is not rebuildable from the
archive, only replaceable.

`data/yt-dlp-update.txt` records when the service last ran `yt-dlp -U` and
its answer, so it asks at most once a week
([docs/download.md](docs/download.md#keeping-yt-dlp-current-settled-2026-11-10)).

`YT_ARCHIVE_DATA` overrides the data root. Default is `./data`.

## Finishing a change: commit, push, restart (settled 2026-11-09)

This archive is local: one user, one machine, a service on `127.0.0.1`. A
finished change is therefore shipped without asking; the user should not have
to say "commit and push". When the work is done and verified:

1. Run the tests: `.venv/bin/python -m unittest discover -s tests`.
2. Commit only this task's changes, one commit per coherent change, and push.
   Uncommitted work from another chat that is finished (tested, documented,
   in use) gets its own commits too; work that looks half-done is left alone
   and mentioned in the report.
3. If the change touches what the service runs (`yt_archive/`), restart
   `yt-archive.service`. A restart re-queues the running job, which then
   redoes its current step, and starts a running transcript over; queued jobs
   survive. So first wait until nothing is running:
   `sqlite3 -readonly data/ytarchive.sqlite "SELECT count(*) FROM jobs WHERE status='running' UNION ALL SELECT count(*) FROM work_jobs WHERE status='running'"`
   prints `0` twice. If the running job will take long, report the restart
   as pending instead of waiting. Then
   `systemctl --user restart yt-archive.service`, and check that a page showing
   the change answers (the service takes up to half a minute to come up).
   Docs-only and test-only changes need no restart. If the unit is not
   installed, stop and ask; do not guess another host or start a second copy
   on a new port.
4. Report in a line or two, for example: "Done: committed, pushed, and
   restarted. The MP3 download is now named `Artist_-_Title-<id>.mp3`. Try it
   on any video page, e.g. http://127.0.0.1:8765/v/<id>." Link the page where
   the change shows.

Before pushing, check that nothing committed names private repos, private
paths, other machines' layout, or what the user archives (test fixtures use
made-up names): this repo is public.

Stop and ask instead of shipping when:

- tests fail, or the change could not be verified;
- the work is exploratory or half-done, or the user said to hold it;
- it deletes or rewrites archive data, changes the `data/` layout, or changes
  the settled download or shot recipes;
- it sends a new kind of request to YouTube or X, or sends the user's login or
  anything about the user anywhere new.

Session commands still work on request: **pstatus** reports unpublished work
and changes nothing; **ppush** commits and pushes finished work without a
restart; **pdeploy** is ppush plus step 3.

## Rules

- Originals stay untouched. Derived work goes in `_condensed/`.
- Filenames this repo writes: `[A-Za-z0-9._-]` only (`paths.restrict_filename`).
  yt-dlp uses `--restrict-filenames`, which also keeps `%`, `+`, `=`, `@`, `~`,
  so every `/media/` link comes from `paths.media_url`, which percent-encodes
  it ([docs/download.md](docs/download.md#flags-locked)). YouTube ids
  (`[A-Za-z0-9_-]`, case-sensitive) pass through unchanged; a tweet's video is
  `x-<tweet id>` (never 11 characters, so never a YouTube id).
- X is read without the user's X account, with two exceptions the user
  approved: a tweet X hides from readers without an account is downloaded with
  the user's X login from Firefox, on the `x-account` budget
  ([docs/download.md](docs/download.md#x-tweets-settled-2026-10-25)), and an X
  profile the user browses is listed with it through gallery-dl, on the
  `x-listing` budget ([docs/browsing.md](docs/browsing.md#x-profiles-settled-2026-11-06)).
  Never send that login with any other X request without the user's explicit
  approval.
- Anything on the GPU holds matthoom's GPU gate first, with a measured claim
  ([docs/transcription.md](docs/transcription.md#the-gpu-gate)). Whisper is
  the only GPU work today; shots run CLIP on the processor.
- Shot and download recipes are settled. Read [docs/download.md](docs/download.md)
  and [docs/framesheets.md](docs/framesheets.md) before changing either.
  Do not reintroduce the rejected approaches.
- This repo is **public**. Do not name private repos, private paths, or
  other machines' internal layout in any committed file.
