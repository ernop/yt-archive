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

## Text contrast

All neutral UI text is pure white (`#fff`) on the black/dark interface. Gray
text, muted-gray labels, gray placeholders, and opacity that turns disabled
text gray are prohibited. Secondary labels use smaller type, spacing, and
weight—not reduced contrast. Semantic link, success, and error colors remain
allowed when they are strongly legible.

This applies to the live archive, generated static pages, dialogs, transcript
controls, and the custom player. The requirement exists because gray-on-black
text was difficult to read.

## Home and End keys

`Home`, `End`, `Ctrl+Home`, and `Ctrl+End` control video position only while
the video element itself has keyboard focus. When focus is elsewhere on the
page, these keys retain normal browser behavior; in particular, `End` scrolls
to the bottom of the page and `Ctrl+Home` goes to the top.

Clicking the video gives it focus. Keyboard focus is shown with a white outline
so it is clear when the player will consume these keys.
