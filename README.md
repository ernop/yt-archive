# yt-archive

A local YouTube archive: paste a URL, download the video, and get a contact sheet of its distinct shots. Browse and play everything already saved.

Paste a creator's YouTube homepage, `@handle`, or username to browse their
videos, Shorts, and past streams. Search and select a batch, or **Get all
available**; saved and already queued videos are skipped. See
[creator browsing](docs/browsing.md#browse-a-creator-on-youtube).

Pairs with [matthoom](https://github.com/ernop/matthoom). Matthoom holds YouTube **watch history** (Takeout) and a gallery that can mark videos to archive. This service does the other half: **the file on disk** and **the shot images**.

```sh
./yt get <url-or-id>
./yt serve          # http://127.0.0.1:8765/
```

Each video is one folder. The original file is left untouched; derived shots live beside it:

```
data/<id>/
  <title>-<id>.mkv
  *.info.json  *.webp  archive.json
  _condensed/
    framesheet.png
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
