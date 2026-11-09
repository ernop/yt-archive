# Download (yt-dlp)

Parent: [AGENTS.md](../AGENTS.md). Settled 2026-08. Do not reinvent.

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
- `--restrict-filenames` — ASCII names without spaces or shell characters.
  It still keeps `%`, `+`, `=`, `@` and `~` from titles
  (`Someone_-_90%_-_...-<id>.mp4`), so every link to a file is built by
  `paths.media_url`, which percent-encodes the name (2026-10-31): a bare `%`
  in a link makes the Caddy `*.localhost` proxy answer 400 Bad Request, and the
  player then says it cannot play the file. YouTube ids (`[A-Za-z0-9_-]`,
  case-sensitive) pass through unchanged.
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
- **No account, unless X hides the tweet** (the login retry approved by the
  user 2026-11-06). Every tweet is first read without the user's X login: no
  cookies (`SITE_ARGS` in `download.py`), since cookies make a download a
  request made as the user. X hides some tweets from readers without an
  account (adult content, protected accounts), and yt-dlp then fails one of two
  ways: with a login hint ("NSFW tweet requires authentication … account
  credentials"), or, when X sends a bare `{"__typename":"TweetTombstone"}` with
  no reason in place of the tweet (both its API and its embed endpoint
  `cdn.syndication.twimg.com` did, 2026-11-06), with "No video could be found in
  this tweet", the same words as for a tweet without a video. Either failure
  asks X once more with the user's X login from Firefox
  (`--cookies-from-browser firefox`, the login the user browses x.com with), on
  its own budget (below); the job log says so. A tweet that really has no video
  therefore costs one request as the user too. If the signed-in try fails the
  same way, the job says X refused the tweet, or sent no video, even with the
  login, and to check that Firefox is signed in to x.com. A tweet found hidden
  this way, and one an X profile listing found marked sensitive
  ([browsing.md](browsing.md#x-profiles-settled-2026-11-06)), is noted in
  `x_hidden_tweets`; its downloads from then on go straight to the login,
  skipping the try X would refuse. Besides these downloads, only listing an X
  profile carries the login (approved by the user 2026-11-06); widening that
  needs the user's explicit approval.
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
- **File name** `<title>-<media id>.mp4`, the title being `<display name> -
  <tweet text>` as yt-dlp shortens it (ending in `...`): yt-dlp's `id` for a
  tweet's video is the media id; the tweet id is its `display_id`. Non-ASCII
  text drops out under `--restrict-filenames` and ASCII pieces such as `90%`
  stay, so a Chinese tweet can become `Someone_-_90%_-_...-<media id>.mp4`.
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
- **Budget: 30 signed-in tweet downloads an hour** (`x-account`), for every
  try with the user's X login: one X API request made as the user (no guest
  token), far below what the account sends while the user scrolls X.
  The queue row waits with "this hour's X account budget of 30 downloads is
  used up".
