# Browsing the archive

Parent: [AGENTS.md](../AGENTS.md).

## Download queue (home page)

The panel under the input is the **download queue**: what the one worker is
doing, what waits for it, and what failed. It is not the archive; the archive
list is below it. Finished jobs are not listed: their result is the video in
the archive list, which refreshes when a job finishes, so a "done" row would
only repeat it. With nothing running, waiting, or failed the panel is hidden.
Each job is one single-line row:
state, video title (the video ID until the download has fetched the title),
detail, time, actions.

- **Working** — the running job. Step pills *Download*, *Shots*, *MP3* show
  which steps are done (✓), running (with a percent), or still to come. The
  detail is the step's own progress, e.g. `downloading video · 58.0 MB of
  129.9 MB · 4.2 MB/s · 0:20 left`, `reading frames 1:20 / 4:03`,
  `comparing frames 120 / 486`. Time is elapsed, counting live.
- **#n waiting** — queue order, with what the job will do for that video
  (e.g. `old shot detection · no MP3 audio`, or `download` for a new one).
  **Remove** drops one waiting job; **Remove all waiting** appears when more
  than one waits. The first 8 are listed; **Show all** lists the rest, so a
  long update never pushes the archive off the page.
- **Failed** — the error text; click it to see all of it. **Retry** queues the
  video again (a plain job, which works out what is missing); **Dismiss**
  hides it. A failure also disappears once a later job for the same video
  exists.

A finished job keeps a summary of what it did (`downloaded · 65 shots · MP3
audio`, `framesheet in 2 sheets`, `list thumbnail`, `no audio track`) in the
database and in `/api/job/<id>`; the video page's Redo and MP3 buttons show it.

Raw yt-dlp output never reaches the panel; it stays in the job's log
(`/api/job/<id>`). The panel polls `/api/jobs` once a second while something
runs or waits, every 5 s when idle, and every 15 s in a background tab.
Starting, finishing, adding, or removing a job re-reads the archive list from
the server page.

Pasting a link to a video that is already archived and up to date queues
nothing; the status line says "Already archived and up to date" with a link.
Pasting one that is already in the queue says so and reuses that job.
A link to a tweet with a video queues it the same way
([download.md](download.md#x-tweets-settled-2026-10-25)).

## Archive status

When some archived videos lack work the current pipeline does, a bar above
the list says how many and why, with one **Update N videos** button:

| Need | Meaning | What the job does |
|------|---------|-------------------|
| no framesheet | video but no shots or sheet | full shot pipeline |
| old shot detection | `shots.json` not from the settled last-kept method | full shot pipeline |
| framesheet to split | sheet count breaks the balanced-sheet rule | re-lay the existing shot PNGs, no CLIP |
| no list thumbnail | no `preview.jpg`, or older than sheet 1 | resize sheet 1 |
| no MP3 audio | no `soundtrack.mp3` and the video has an audio track | dump MP3 |

The button queues plain jobs; each job reads the files again and does only
what is still missing, so pressing it twice or racing a running job is
harmless. The rules live in `paths.archive_needs()`; the catalog stores the
result per video (`videos.needs`) so the bar costs one query.

## Archive list

One single-line row per video: list thumbnail, title, creator, length, shot
count, upload date, when it was gotten (local time, `YYYY-MM-DD HH:MM`), and
how it was first asked for ([Added by](#added-by-settled-2026-11-10)).
Numbers and dates are monospaced and right-aligned so they line up. A video
with an open job shows an **updating** marker. The creator column is dropped
on creator pages, and narrow screens keep thumbnail, title, and length.

The thumbnail is `_condensed/preview.jpg` (sheet 1 resized to 480 px wide,
tens of KB) or, until that exists, the YouTube thumbnail downloaded with the
video. The list never loads a full framesheet: those are up to hundreds of
megapixels each, and the list once pulled about 1.5 GB of PNGs per load.

## Added by (settled 2026-11-10)

The archive list's last column names the route of the **first** request for
each video. Later requests leave it alone.

| Column | First request came from |
|--------|-------------------------|
| Played | a local player watcher, when the video started playing |
| Bookmarklet | the **+ ytarchive** pop-up ([bookmarklet.md](bookmarklet.md)) |
| Pasted | the home page's paste box |
| Picker | the YouTube or X picker |
| Matthoom | Matthoom |
| Command line | `./yt get` |
| Not recorded | before routes were kept (videos archived before November 10, 2026), or a request that named no route |

A request names its route with `"via"` on `POST /api/get`:

```bash
curl -s -X POST localhost:8765/api/get -H 'Content-Type: application/json' \
  -d '{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "via": "played"}'
```

Values are `played`, `bookmarklet`, `pasted`, `picker`, `matthoom`, `cli`;
anything else is refused with 400 before anything is queued. Retry, Redo,
**Create MP3 audio** and **Update N videos** send none: they redo a video,
they do not add one.

The route is kept in the catalog's `video_adds` table from the first request
on, and the download copies it into the video's `archive.json` as `added_via`
and `added_at`, so a rebuilt catalog shows it again. A video that already has
an `archive.json` without a route was archived before routes were kept; a new
request for it is not its first, so nothing is recorded.

## List order

The archive home page defaults to **Recently gotten**, ordered by the time
yt-archive first acquired each video, newest first. Reprocessing an existing
video does not change that acquisition time.

The sort control is URL-backed and works together with search. It offers:

- recently gotten;
- first gotten;
- original YouTube upload date, newest first;
- title, A–Z;
- channel, A–Z.

Unknown sort values fall back to Recently gotten. The server-rendered page and
the live refresh after a queued download use the same query and sort, so the
list cannot silently change order. Acquisition timestamps are shown on every
row, with the browser converting them to local time.

This keeps the default focused on newly added archive material while retaining
predictable, low-friction ways to find older or alphabetically grouped items.

## Creator pages

Uploader names in archive rows and on video detail pages link to a dedicated
creator page. That page shows every archived video from the uploader and
supports the same sort choices plus search across titles, descriptions, video
ids, transcripts, and speaker names.

Creator identity uses YouTube's stable `channel_id` whenever it is available.
Older archive records without one fall back to their exact channel name. The
page keeps the creator identity in its URL so it can be bookmarked and shared
locally.

## Browse YouTube

The `/browse` picker goes through a creator's or a playlist's videos, or an X
profile's ([below](#x-profiles-settled-2026-11-06)), so they can be queued all
at once, by section, by filter, or one by one, instead of pasting links one at
a time. Code: `yt_archive/creators.py`, `yt_archive/x_profiles.py`,
`static/creators.js`.

### What to paste

- A creator: homepage (`youtube.com/@name`, `/channel/UC…`, `/user/name`,
  `/c/name`, vanity URLs), `@handle`, plain handle, or `UC…` channel ID.
- A playlist: `/playlist?list=…`, a watch link with `list=`,
  `youtu.be/…?list=`, `embed/videoseries?list=`, or
  `music.youtube.com/playlist?list=`. The user's own Watch later (`list=WL`),
  Liked videos (`LL`), Liked music (`LM`) and private playlists work too (see
  below); the picker page links Watch later and Liked videos directly.
- A link ending in a channel tab opens that part: `/videos` → Videos,
  `/shorts` → Shorts, `/streams` → Live, `/playlists` → the creator's
  playlists.
- An X profile: `x.com/name` or `twitter.com/name` (also `www.`, `mobile.`,
  and the `/media`, `/with_replies`, `/highlights` tabs). A bare `@handle`
  stays a YouTube handle; a tweet link is a single video for **Get**.

On the home page, **Get** with one creator or playlist link opens the picker;
with several links, each creator or playlist becomes a Browse link and videos
are queued. A video link still goes straight into the queue; if it carried a
`list=`, the status line adds a link to choose more from that playlist (not
for Mixes, `list=RD…`, which YouTube makes up per viewer). **Browse** treats
the input as a creator or playlist (for handles of exactly 11 characters,
which Get reads as video IDs) and opens the empty picker when the input is
empty. The bookmarklet on a creator or playlist page opens the picker too, and
a creator's archived page links to it when its channel ID is known.

### Listing is read-only; the account only for the user's own lists

yt-dlp lists with `--flat-playlist --lazy-playlist --dump-json`: metadata
only, one line per item as pages arrive, so the page shows the count found
and requests made while it loads. Listing sends **no cookies**, so YouTube
does not see the user's account while browsing creators, their playlists,
and public or unlisted playlists. Downloads use the Firefox cookies
([download.md](download.md)). yt-dlp does not list YouTube "course"
playlists.

Lists YouTube shows only to the signed-in user are read **with the same
Firefox cookies**, automatically (settled 2026-10-23):

- Watch later, Liked videos, and Liked music (`list=WL`, `LL`, `LM`) use the
  account from the first request; asking for them without it would be wasted.
- A private playlist: YouTube answers "The playlist does not exist" to anyone
  but its owner, the same as for a playlist that really does not exist. That
  answer to a playlist listed without the account is asked once more with it.
  A playlist missing either way therefore costs one request as the user, and
  its failure is then remembered like any other.
- A kept list that was read with the account is refreshed with it.

The loading line and the picker header say "with your YouTube account" for
these lists. Why: nothing is sent as the user without their consent, and they
approved the account for their own lists only. Those requests
tell YouTube nothing it does not already know (the lists are the user's own,
and every download already goes out with the cookies), while which other
creators the user browses stays unseen. Both tries are paid from the listing
budget below.

A creator's list holds every tab yt-dlp reads from the homepage: Videos,
Shorts, and Live (past streams). Upload dates come from YouTube's "3 weeks
ago" text (`youtubetab:approximate_date`), so they are shown that coarsely
(`3 wk`, `2 yr`) and only drive the Uploaded filter and date sorts. Shorts
have no length or date in a listing. Live, upcoming, still-processing, and
known private or members-only items are listed but cannot be chosen. YouTube
can still refuse an item at download time; the queue shows that error.

### X profiles (settled 2026-11-06)

An X profile's list holds the videos of its **Media tab, newest first**: the
first video of each of the profile's own tweets and replies, with its poster.
Photos, GIFs, retweets, and quoted tweets are left out. Each row's title is
the tweet's text; length, views, and the posting time (from the tweet ID, so
exact) fill the other columns, and the row links to the tweet. X lists have no
tabs; kept lists, Refresh, the filters, one by one, and Get work as for
YouTube, and the videos download as tweets do
([download.md](download.md#x-tweets-settled-2026-10-25)).

- **Read with the user's X login, approved by the user (2026-11-06).** X shows
  signed-out readers a profile sorted by likes and no Media tab, and its guest
  API returns empty timelines for many accounts, so only a signed-in reader
  gets a complete list in order. The listing therefore always uses the X
  login from Firefox, and the loading line and header say "with your X
  login". The approved cost: X sees the account open each listed profile's
  Media tab, as when the user opens it on x.com, and there is a small risk
  that X temporarily locks the account for automated reading. The budget
  below keeps the reading slower than the user's own scrolling.
- **How.** gallery-dl, installed as its own `uv` tool
  ([setup.md](setup.md)), reads the tab and downloads nothing:
  `--cookies-from-browser firefox/.x.com` loads only the x.com cookies,
  `--config-ignore` skips any gallery-dl configuration, and `--print` writes
  one JSON line per file found. yt-dlp cannot list X profiles.
- **Requests.** About three to start (X's client keys, which gallery-dl
  caches for three hours, and the profile), then one per page of up to 50
  tweets, two seconds apart (`--sleep-request 2`). Each is paid from the `x-listing`
  budget at gallery-dl's `Sleeping … (request)` log line, before it goes out;
  a request sent without that line (a retry) is paid as soon as its answer is
  logged. X's rate limit stops the listing instead of being waited out, and a
  stopped list keeps what had arrived, marked Incomplete.
- **Errors** say what happened in plain words: X locked the account, X's rate
  limit, no such account, protected tweets, the account blocked the user, or
  the Firefox login not working.
- **Hidden tweets.** Tweets X marks sensitive are noted (`x_hidden_tweets`),
  and their downloads go straight to the X login instead of first trying
  without an account, which X would refuse. The confirmation for more than
  50 counts them at the signed-in download budget.
- Posters load lazily from X's image server (`pbs.twimg.com`, small size in
  rows, medium one by one) with no referrer.

### Kept lists

A finished list is kept in SQLite (`youtube_listings`) until **Refresh list**.
Reloads, other tabs, other links to the same creator (a handle or its channel
ID), and service restarts reuse it and ask YouTube or X nothing; `listed 3 h
ago` shows its age. A creator's list only changes when they upload, so asking
again is a deliberate act. The 60 most recently used lists are kept.

- One fetch per list at a time; a second tab waits for the same fetch. At most
  two lists load at once.
- A failed list is shown again, not fetched again, for 10 minutes; **Try
  again** fetches it. A failed refresh keeps the old list and says why.
- A list cut short (budget, 20-minute limit, YouTube or X errors) is kept
  with an "Incomplete" warning, and "all" means all that was listed.

### The picker

- **Header**: title, kind (creator, playlist, playlists, X profile), the
  owner for a playlist (links to the owner's picker), YouTube or X link, list
  age, **Refresh list**.
- **Tabs** for a creator: Videos, Shorts, Live, All, each with its count, and
  Playlists. Playlists lists the creator's playlists on first click (its own
  kept list, with its own Refresh); each opens in the picker, and Back returns
  to the creator from the kept list.
- **Filters**: title or video ID; Length (under 1 min, 1–5, 5–20, 20–60 min,
  over 1 hour); Uploaded (past week, month, 3 months, year, over a year ago);
  Sort (as listed, newest, oldest, longest, shortest, most viewed, title);
  hide saved and queued. Items without a length or date drop out
  while that filter is on. The tab and filters are kept in the URL.
- **Rows**, 100 per page, one line each: checkbox, position in the current
  view, thumbnail, title, channel (playlists only, where videos come from
  many channels), length, views, uploaded, status, YouTube or X link. Status is
  empty for items that can be chosen; otherwise **Saved** (links to the
  archive page), **Queued**, or why it cannot be chosen (Live now, Not ready
  yet, Restricted, Unavailable).
- **Selecting**: click a row or its checkbox; shift-click selects the range
  from the last click; **Select all shown** (every page of the current view);
  **Select first N** (in the current order); **Clear**. Keys: `j`/`k` or
  arrows move, `x` or space selects, `shift`+`x` selects a range, `o` opens it
  on YouTube or X. The selection holds across tabs, filters, and pages and survives
  reloading the tab; the bar says how many selected items the current view
  does not show.
- **Go one by one** shows each choosable video of the current view, in its
  order, large: thumbnail, title, length, views, age. **Choose** (`y`),
  **Skip** (`n` or →, also unselects), **Back** (`b` or ←), **Back to the
  list** (Esc). It starts at the first one not selected yet and ends with
  **Get N selected**.
- **Get selected** queues the selection; **Get all shown** queues every
  choosable item of the current view (tab and filters, all pages). More than
  50 asks first, saying when the download budget spreads them over hours.
  Saved and queued videos are skipped, also on repeats. Downloads are the
  usual one-at-a-time video → framesheet → MP3 jobs and survive leaving the
  page and restarts.
- Thumbnails load lazily from YouTube's image server with no referrer, as for
  every YouTube link on the site.

### YouTube request budget

yt-dlp's wiki puts YouTube's limits for a guest session at about 1000
webpage/API requests and 300 videos an hour (accounts get about four times
that). Two hard budgets sit at half the guest figures, counted in SQLite
(`youtube_requests`) over a rolling hour and shared by the service and the
CLI (`yt_archive/budget.py`):

| Budget | Per hour | Counted |
|--------|----------|---------|
| listing | 500 requests | every request yt-dlp makes while listing |
| download | 150 videos | every download that runs yt-dlp |
| x-download | 60 tweets | every tweet download, X's own budget ([download.md](download.md#x-tweets-settled-2026-10-25)) |
| x-account | 30 tweets | every tweet download with the user's X login, for a tweet X hides from readers without an account ([download.md](download.md#x-tweets-settled-2026-10-25)) |
| x-listing | 60 requests | every request gallery-dl makes while listing an X profile with the user's X login ([X profiles](#x-profiles-settled-2026-11-06)) |

- **Listing** runs yt-dlp with `--sleep-requests 0.75`; it prints
  `[youtube:tab] Sleeping …` before every request after the first, and each
  is paid for at that line, before the request goes out. When the budget is
  spent, yt-dlp is stopped and the list keeps what had arrived. Starting a
  list pays for its first request; with nothing left the page says when the
  budget frees (HTTP 429), and a refresh keeps the old list. At about 25 items
  per request, one hour lists creators up to about 12,000 items.
- **Downloads** wait for a free unit; the queue row then reads `waiting:
  this hour's YouTube budget of 150 downloads is used up · next download at
  14:05`. Videos already on disk cost nothing. This also stops a failing queue
  (expired cookies, say) from hammering YouTube.
- **X listing** pays the same way, per request and before it is sent
  ([X profiles](#x-profiles-settled-2026-11-06)). At up to 50 tweets per
  request, one hour lists up to about 2,800 of a profile's newest media tweets.
- Tabs, reloads, polling, and selections never reach YouTube or X; only
  listing a link that is not kept, Refresh, Try again, and downloads do.

### API

```bash
# Open a list (kept, or fetched once). 202 while loading; 429 + retry_at when the
# budget is spent; "refresh": true asks YouTube again.
curl -s -X POST localhost:8765/api/creators -H 'Content-Type: application/json' \
  -d '{"source": "https://www.youtube.com/@name"}'
# Status and items: video_id, title, duration, views, timestamp, section,
# position, channel, unavailable, archived, queued; X profile items
# (source "https://x.com/name") add thumb and hidden.
curl -s localhost:8765/api/creators/<lookup_id>
# Queue some, or {"all": true} for every choosable item.
curl -s -X POST localhost:8765/api/creators/<lookup_id>/get -H 'Content-Type: application/json' \
  -d '{"video_ids": ["dQw4w9WgXcQ"]}'
```

## Text contrast

All neutral UI text is pure white (`#fff`) on the black/dark interface. Gray
text, muted-gray labels, gray placeholders, and opacity that turns disabled
text gray are prohibited. Secondary labels use smaller type, spacing, and
weight—not reduced contrast. Semantic link, success, and error colors remain
allowed when they are strongly legible.

This applies to the live archive, dialogs, transcript controls, and the media
player. The requirement exists because gray-on-black
text was difficult to read.

## Rows are single lines

Every list or table row on the site is one line: download queue, archive
list, the YouTube and X picker, transcript segments (time, speaker, text side by side).
Extra detail goes inline, behind a click (a failed job's full error), or on
the video page; it is never stacked as a second line inside a row.

## Video page

Under the title, one line of facts: creator, length, resolution with fps and
codec (for videos downloaded or processed since October 2026), file size,
shot count, upload date, when it was gotten, the video ID, and a YouTube
link (an X link for a tweet's video). The description (a tweet's text)
follows, collapsed. If a job for the video is
waiting or running, a banner says so; the page does not wait for the job and
shows the files as they are. A video without an audio track says so instead
of offering **Create MP3 audio**. **Redo** at the bottom rebuilds the shots
from the local file or downloads the video again.

**download MP3 audio** saves the file as `<artist>_-_<title>-<id>.mp3`
(November 9, 2026), not as the `soundtrack.mp3` it is on disk, so a pile of
downloads stays readable in a music player or a Downloads folder. The artist
is the channel, without the ` - Topic` YouTube adds to its auto-made artist
channels; it is left out when the title already names one (`Artist - Song`,
a tweet's `Name - text`) or contains the channel name. The name keeps to
`[A-Za-z0-9._-]` like every filename here, so a title with no Latin letters
leaves just `<id>.mp3`.

## Lyrics (settled 2026-11-10)

A local client that finds a song's lyrics (a lyrics overlay, say) can hand
them to the archive:

```bash
curl -s -X POST localhost:8765/api/lyrics -H 'Content-Type: application/json' \
  -d '{"video_id": "dQw4w9WgXcQ", "lrc": "[00:01.00]first line\n"}'
```

`lrc` is the text of an LRC file (timed `[mm:ss.xx]` lines, or plain lines),
at most 256 KB; `null` removes the video's lyrics. The answer's `lyrics` is
`saved`, `pending`, or `removed`.

- A video on disk gets `_condensed/soundtrack.lrc` at once. It sits beside
  `soundtrack.mp3` under the same name, which is where music players look
  for an LRC, and the video page offers **download lyrics** next to
  **download MP3 audio**, named like the MP3 (`<artist>_-_<title>-<id>.lrc`).
- Lyrics for a video not downloaded yet wait in the catalog's
  `pending_lyrics` table (`pending`). The video's job writes the file when it
  finishes, and its summary ends with `lyrics`. Waiting lyrics never create
  the video's folder, since any folder named like a video ID is a list row.
- Newer lyrics replace older ones; the file is written to a temporary name
  and renamed, so a reader never sees half of it.

## Video and audio player

The video and the MP3 audio play in the shared media player, of which this
repo keeps a copy ([setup.md](setup.md#media-player)), so every project plays
media the same way; it replaced this site's own player in October 2026. `?` on
the page lists every key. `R` or the repeat button (October 27, 2026) plays the
video or the MP3 again from the start each time it ends; each player has its
own, and a new page opens with it off. What the video page adds:

- Every kept shot is a marker on the seek scale, named by its image
  (`Shot 0041`). The image of the shot that is playing is outlined in red.
- Clicking a shot image plays from it and gives the player the keyboard, so
  PageUp and PageDown then step through the shots. Shift-click opens the PNG.
- `S` or the camera button saves the frame showing as a full-size PNG into
  `_condensed/`, next to the MP3 (`grab-<title>-<time>.png`).
- `T` or the wide button gives the video 86% of the window height instead of
  72%. The player remembers the choice, as it does captions.
- With a transcript, `C`, the player's CC button and the transcript's caption
  button all switch the same captions.

Keys that scroll the page (Up, Down, Home, End, PageUp, PageDown) act on the
player only while it has keyboard focus: after clicking it, tabbing into it or
clicking a shot image. The player's edge is red while it has focus. Elsewhere
these keys keep their normal browser behavior; in particular `End` scrolls to
the bottom of the page and `Ctrl+Home` goes to the top.
