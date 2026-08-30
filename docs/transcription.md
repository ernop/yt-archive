# Local transcription and captions

Parent: [agents.md](../agents.md).

## Product behavior

- Transcription is explicit, per video, and never part of download, framesheet,
  or MP3 generation. The video detail page opens a confirmation dialog with one
  prominent default action and optional model/language/beam configuration.
- The default is `faster-whisper` `large-v3`, word timestamps, beam size 5,
  automatic language detection, and voice-activity filtering. This favors
  transcription quality on the machine this service runs on.
- Transcript content is spoken language only. Bracketed non-speech labels such
  as music, applause, waves, and birds are removed rather than mixed into the
  spoken transcript. Non-speech event annotation, if added later, must be a
  separate data track and UI layer.
- Results are written to `_condensed/transcript.json` and
  `_condensed/transcript.vtt`, then mirrored into SQLite for search and fast
  browsing. Files remain the recoverable source of truth.
- The detail page provides a private timed transcript below the player:
  timestamp/word seeking, playback-following highlight, full-text filtering,
  rolling or full view, text-size choices, WebVTT download, and an optional
  actual caption track in the local player.
- Speaker names are editable per segment. Reusing a name groups segments;
  speaker groups can be renamed or merged. Automatic diarization is not enabled:
  the common high-quality diarization stack requires separately licensed/gated
  models and credentials. Its output can be added later without changing the
  transcript format's `speaker` field.

## Durable work semantics

Optional derived work has its own worker so Whisper never blocks the
one-at-a-time archive download queue. Each transcription identity is a hash of
the video id, MP3 file identity, and normalized configuration, enforced by a
unique SQLite key.

Submitting the same work repeatedly returns the existing row. Failed work is
retried in that row; explicit replacement requeues that row. A service restart
returns `running` work to `queued`. Whisper may recompute after a crash, but
temporary files plus atomic rename prevent duplicate or partial published
artifacts. Startup reindex restores transcript rows from JSON if a crash occurs
between file publication and the SQLite transaction.

Re-downloading a video invalidates its MP3 and transcript artifacts so stale
captions are not shown against new media.

## Files

```
data/<id>/_condensed/
  soundtrack.mp3
  transcript.json
  transcript.vtt
```

`transcript.json` contains metadata, timed spoken segments, word timestamps,
and optional speaker names. `transcript.vtt` is regenerated when speaker names
change.

## Setup

Install the current local runtime in this repository's venv:

```sh
.venv/bin/python -m pip install \
  faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12
```

The model is downloaded to the normal Hugging Face cache on first use. CUDA
with float16 is selected when CTranslate2 can use the local GPU; otherwise the
same model runs with CPU int8.

Explicit CLI parity is available for maintenance:

```sh
./yt transcribe <id>
./yt transcribe <id> --language en --model large-v3 --beam-size 5
```
