# Download (yt-dlp)

Parent: [agents.md](../agents.md). Settled 2026-08. Do not reinvent.

Code: `yt_archive/download.py`.

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
