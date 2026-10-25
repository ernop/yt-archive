# Download (yt-dlp)

Parent: [agents.md](../agents.md). Settled 2026-08. Do not reinvent.

Code: `yt_archive/download.py`.

Tweets on X take the same recipe except where
[X tweets](#x-tweets-settled-2026-10-25) says otherwise.

## Flags (locked)

- **Highest resolution, any codec:**
  `-f bv*+ba/b -S res,fps,hdr,vcodec:av01:vp9.2:vp9:h264`
  (best video + best audio, merge without re-encode). Do not prefer mp4 —
  that can pick 1080p AVC over 4K VP9/AV1.
- **Firefox cookies required** (as of 2026-08-14). Anonymous downloads via
  the android_vr client get HTTP 403 after ~10 MB (URL invalidated
  server-side; `--http-chunk-size` does not help). web/ios/tv refuse
  without a PO token. Authenticated requests have no cutoff.
- **JS runtime required** or yt-dlp reports "This video is not available".
  Deno is yt-dlp's default (sandboxed). Node also works.
- `--restrict-filenames` — names stay in `[A-Za-z0-9._-]`. YouTube ids
  (`[A-Za-z0-9_-]`, case-sensitive) pass through unchanged.
- Output: `data/<id>/%(title)s-%(id)s.%(ext)s` so one video is one folder.

Use the official standalone `yt-dlp` on `PATH` (`~/.local/bin/yt-dlp`,
self-updates with `yt-dlp -U`). Distro packages lag.

## Progress and config (settled 2026-10-14)

- `--ignore-config` — a user-level yt-dlp config cannot change the locked
  recipe above.
- `--progress --newline --progress-delta 1 --progress-template
  "download:ytarchive-progress …"` — one machine-readable line per second
  (status, bytes done, total or estimate, speed, ETA, vcodec).
  `download.parse_progress()` turns it into the queue panel's fraction and
  message (`downloading video · 58.0 MB of 129.9 MB · 4.2 MB/s · 0:20 left`).
  Those lines are not written to the job log; everything else yt-dlp prints is.
- `archive.json` also keeps the downloaded format's `width`, `height`, `fps`,
  `vcodec`, `acodec` for the video page.
- yt-dlp's unmerged single-format files (`<title>-<id>.f399.mp4`) are never
  taken for the finished video, so a job that stopped mid-merge downloads again.

## Hourly budget (settled 2026-10-20)

Each download that runs yt-dlp first takes one unit of the 150-an-hour
download budget (half of YouTube's guest limit, see
[browsing.md](browsing.md#youtube-request-budget)); with none left it waits and
the queue row says until when. A video already on disk costs nothing. Listing
creators and playlists has its own budget and sends the cookies only for the
user's own Watch later, Liked videos, and private playlists
([browsing.md](browsing.md#listing-is-read-only-the-account-only-for-the-users-own-lists)).

Keep `yt-dlp` current (`yt-dlp -U`); an outdated copy prints a
"To suppress this warning, add --no-update" warning on every run, and YouTube
changes break old versions.

## X tweets (settled 2026-10-25)

A video in a tweet goes through the same pipeline as a YouTube video (download,
shots, MP3, transcript on request). Paste the tweet's link (`x.com` or
`twitter.com`, any `/status/<id>` form) into the home page, click the
bookmarklet on the tweet, or run `./yt get <tweet link>`.

- **Archive id `x-<tweet id>`**, folder `data/x-<tweet id>/`. Tweet ids of 10
  to 19 digits are accepted (every tweet that can carry a video has 18 or
  more), so an archive id is never 11 characters and never taken for a YouTube
  id. Some YouTube ids start with `x-` too; they stay YouTube ids.
- **No account.** X is read without the user's X login: no cookies, ever
  (`SITE_ARGS` in `download.py`). Firefox's cookies would make every download a
  request made as the user. Tweets X shows only to signed-in accounts (sensitive
  media, protected accounts) fail with "X shows this tweet only to signed-in
  accounts". Using an account needs the user's explicit approval first.
- **Only X's extractors** (`--use-extractors twitter.*`). For a tweet with no
  video of its own, yt-dlp follows the tweet's first link; restricted, the job
  fails with "This tweet has no video of its own; it links to <url>" instead of
  fetching from YouTube or another site outside that site's budget.
- **First video only** (`--playlist-items 1`, `--no-write-playlist-metafiles`),
  so one folder holds one video. A tweet with several videos keeps its first. A
  quoted tweet's video counts when the tweet has none of its own.
- **Same format recipe.** X offers HLS video-only streams with separate audio,
  and progressive MP4s labelled `http-256`, `http-832`, `http-2176`. The labels
  are tiers, not bitrates: on 2026-10-25 a 716×538 `http-2176` MP4 was
  6,359,453 bytes and the merged `hls-628` + 128k audio 6,356,661 bytes, the
  same encode. The locked `-f` / `-S` pick the HLS pair at the top resolution.
- **File name** `<uploader>-<media id>.mp4`: yt-dlp's `id` for a tweet's video
  is the media id; the tweet id is its `display_id`. Non-ASCII tweet text drops
  out under `--restrict-filenames`.
- **Metadata.** `archive.json` takes the title (`<display name> - <tweet
  text>`), `channel` (the display name), duration, upload date, size, and codec
  from the info JSON, and `url` is the tweet. `channel_id` stays empty: creator
  pages link a channel id to YouTube, and an X user id is not one, so the
  creator link groups tweets by display name. The video page links to the tweet
  on X.
- **Budget: 60 tweet downloads an hour** (`x-download` in `yt_archive/budget.py`),
  counted like the YouTube budgets
  ([browsing.md](browsing.md#youtube-request-budget)). X publishes no limit for
  reading without an account; each tweet costs yt-dlp two X API requests (a
  guest token, then the tweet), and the video itself comes from X's CDN. A tweet
  already on disk costs nothing.
