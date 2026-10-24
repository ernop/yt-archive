# yt-archive

A local YouTube archive: paste a URL, download the video, and get a contact sheet of its distinct shots. Browse and play everything already saved.

Paste a creator's YouTube homepage, `@handle`, or a playlist link to go
through all of its videos: Videos, Shorts, Live, and the creator's playlists,
with filters for length and upload age. Get them all, a filtered part, a
hand-picked selection, or go one by one; saved and already queued videos are
skipped. Your own Watch later, Liked videos, and private playlists work too.
Lists are read without your YouTube account, except those, which only your
account can see; lists are kept until you refresh them and held to an hourly
request budget. See
[browsing YouTube](docs/browsing.md#browse-youtube).

Drag the home page's **+ ytarchive** button to your bookmarks bar, then click
it on any YouTube video to queue it without leaving the page. See
[the bookmarklet](docs/bookmarklet.md).

Pairs with [matthoom](https://github.com/ernop/matthoom). Matthoom holds YouTube **watch history** (Takeout) and a gallery that can mark videos to archive. This service does the other half: **the file on disk** and **the shot images**.

```sh
./yt get <url-or-id>
./yt serve          # http://127.0.0.1:8765/
```

The home page shows the download queue live (what is downloading, waiting, or
failed, with progress) above the archive list, and offers
one button to bring older videos up to the current pipeline. See
[browsing](docs/browsing.md#download-queue-home-page).

Each video is one folder. The original file is left untouched; derived shots live beside it:

```
data/<id>/
  <title>-<id>.mkv
  *.info.json  *.webp  archive.json
  _condensed/
    framesheet.png                      # framesheet-1.png, -2.png … past 250 shots
    preview.jpg                         # small copy of sheet 1 for the archive list
    soundtrack.mp3
    transcript.json                     # optional, explicit local Whisper run
    transcript.vtt                      # selectable captions
    grab-Some_Title-00h01m23s450.png   # optional, player `s` / grab
    shots.json
    shots/0000.png …
```

```sh
./yt get <url-or-id>           # download + shots + MP3 audio
./yt get <url-or-id> --skip-shots
./yt shots <id>                # shots only (video already on disk)
./yt audio <id>                # MP3 audio only (video already on disk)
./yt transcribe <id>           # explicit local Whisper transcription
./yt list
./yt reindex
./yt serve [--port 8765]
```

The video detail page can run transcription on demand, search and seek by
spoken word, show a playback-following transcript, assign speaker groups, and
enable the generated WebVTT as actual player captions. See
[docs/transcription.md](docs/transcription.md).
