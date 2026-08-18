# yt-archive

A local YouTube archive: paste a URL, download the video, and get a contact sheet of its distinct shots. Browse and play everything already saved.

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
    shots.json
    shots/0000.png …
```

```sh
./yt get <url-or-id>           # download + shots
./yt get <url-or-id> --skip-shots
./yt shots <id>                # shots only (video already on disk)
./yt list
./yt reindex
./yt serve [--port 8765]
```
