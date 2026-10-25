# Bookmarklet: queue from YouTube or X

Parent: [agents.md](../agents.md).

## What it does

The archive home page shows a **+ ytarchive** button. Drag it to the browser's
bookmarks bar once. On any YouTube video page, or a tweet with a video on X,
clicking that bookmark queues the video — the same as pasting its link into the
home page — without leaving the page:

- A small pop-up window opens on this archive (`/add`), says **Queued** with
  the video's title and id, and closes itself after 1.5 s. Playback on
  YouTube is not interrupted.
- On a creator homepage, `@handle`, or playlist page, the pop-up instead
  becomes the picker ([browsing.md](browsing.md#browse-youtube)) and stays
  open. On a video playing inside a playlist it queues just that video.
- Anything the archive cannot use (YouTube's home feed, any other site)
  leaves the pop-up open with the reason. Nothing is queued. A tweet without a
  video of its own is queued and then fails with the reason
  ([download.md](download.md#x-tweets-settled-2026-10-25)).
- Clicking it again on the same video does not queue a second job; the
  existing queued or running job is reused.

The bookmarklet sends the page's address and title only to this archive.
It opens the pop-up at whatever address the home page was loaded from
(`127.0.0.1:8765`, or the `*.localhost` name through a proxy), so drag it from
the address you normally use.

If a browser blocks the pop-up, the bookmarklet shows an alert asking to allow
pop-ups for that site.

## Key

Every bookmarklet carries a random key, kept in `data/bookmarklet.key`
(mode 0600, created on first start, never committed). `GET /add` only
queues automatically when the key matches; otherwise it shows "bookmarklet key
doesn't match" and queues nothing. The home page includes the key only when the
request's Host is local (`localhost`, `*.localhost`, loopback IPs), so a
DNS-rebinding page under another host name never sees it.

To issue a new key: delete `data/bookmarklet.key`, restart the service, and
drag a fresh button from the home page (old bookmarklets stop working).

The bookmarklet runs inside the page it is clicked on, so that page's own
scripts could in principle read the key. The key only lets a page queue
YouTube or X downloads on this machine.

## Why a pop-up, not a background request

Decided 2026-10-14. A bookmarklet's code runs as the YouTube page, so a
background `fetch` to this archive is a cross-site request. That would need:

- the cross-site POST check in `_trusted_mutation` loosened;
- CORS headers;
- the browser's local-network-access permission granted to youtube.com.

A pop-up is a normal top-level page on this archive's own origin. Its
`POST /api/get` is same-origin and goes through the existing checks unchanged,
and no browser permission is needed. `GET /add` itself changes nothing; the
page's script does the queueing only when the key matched.
