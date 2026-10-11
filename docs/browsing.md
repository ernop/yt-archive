# Browsing the archive

Parent: [agents.md](../agents.md).

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
card, with the browser converting them to local time.

This keeps the default focused on newly added archive material while retaining
predictable, low-friction ways to find older or alphabetically grouped items.

## Creator pages

Uploader names on archive cards and video detail pages link to a dedicated
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

This applies to the live archive, generated static pages, dialogs, transcript
controls, and the media player. The requirement exists because gray-on-black
text was difficult to read.

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
