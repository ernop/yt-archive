# Browsing the archive

Parent: [agents.md](../agents.md).

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
count, upload date, and when it was gotten (local time, `YYYY-MM-DD HH:MM`).
Numbers and dates are monospaced and right-aligned so they line up. A video
with an open job shows an **updating** marker. The creator column is dropped
on creator pages, and narrow screens keep thumbnail, title, and length.

The thumbnail is `_condensed/preview.jpg` (sheet 1 resized to 480 px wide,
tens of KB) or, until that exists, the YouTube thumbnail downloaded with the
video. The list never loads a full framesheet: those are up to hundreds of
megapixels each, and the list once pulled about 1.5 GB of PNGs per load.

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

## Browse a creator on YouTube

Paste a creator homepage, `@handle`, plain handle, or `UC…` channel ID into
the home page's input. **Get** opens a picker for creators; individual video
links still go straight into the download queue. **Browse creator** explicitly
treats the input as a creator (useful for handles that are exactly 11 characters,
which Get otherwise interprets as video IDs). Older `/user/name`, `/c/name`,
and vanity homepage URLs work too. A creator's archived page links to the picker
when its channel ID is known.

The picker loads all listed videos, Shorts, and streams from the creator's
homepage, including when the pasted URL ends in `/videos` or another channel
tab. Loading only reads metadata; it does not download media. Large channels
may take a few minutes. Known private/restricted items, live broadcasts, upcoming
streams, and streams still processing are shown but cannot be selected.
YouTube can still reject an apparently available item at download time; its
normal queue error remains visible.

Each listed item is one row: checkbox, YouTube thumbnail, title, length,
state (Available, Saved, Queued, or why it cannot be selected), and a link.
Thumbnails load from YouTube's image server, which the page asks for with no
referrer, as it does for every YouTube link on the site.

Filter by title or video ID, check individual items, or **Select all matching**
across every page. Selections survive filtering and pagination; **Clear selection**
clears them all. **Get selected** queues that selection. **Get all available**
queues every eligible listed item regardless of the filter or current page.
Saved and already queued videos are skipped, including on repeated requests.
Downloads use the usual one-at-a-time video → framesheet → MP3 pipeline and
survive leaving the page or restarting the service.

Lookups are temporary previews, retained for up to an hour of inactivity, with
at most 16 previews and two simultaneous lookups. A restart clears previews;
browse again to refresh. A partial lookup is explicitly labeled incomplete,
so "all" refers only to the returned list. Lookup failures can be retried with
Browse. No video limit is passed to yt-dlp; the UI shows 100 items per page.

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
list, creator picker, transcript segments (time, speaker, text side by side).
Extra detail goes inline, behind a click (a failed job's full error), or on
the video page; it is never stacked as a second line inside a row.

## Video page

Under the title, one line of facts: creator, length, resolution with fps and
codec (for videos downloaded or processed since October 2026), file size,
shot count, upload date, when it was gotten, the video ID, and a YouTube
link. The YouTube description follows, collapsed. If a job for the video is
waiting or running, a banner says so; the page does not wait for the job and
shows the files as they are. A video without an audio track says so instead
of offering **Create MP3 audio**. **Redo** at the bottom rebuilds the shots
from the local file or downloads the video again.

## Video and audio player

The video and the MP3 audio play in the shared media player, of which this
repo keeps a copy ([setup.md](setup.md#media-player)), so every project plays
media the same way; it replaced this site's own player in October 2026. `?` on
the page lists every key. What the video page adds:

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
