# Local transcription and captions

Parent: [AGENTS.md](../AGENTS.md).

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

## The GPU gate

Settled 2026-11-10. Every program on this computer that puts anything on the
GPU holds it through matthoom's gate first (`core/utils/gpu.py`, explained in
matthoom's `docs/GPU.md`), so none runs out of GPU memory beside another and
the most urgent work goes first. Whisper is ytarchive's only GPU work: shots
run CLIP on the processor.

- **A process of its own.** The service runs each transcription as
  `python -m yt_archive.transcribe <job>` (`transcribe_in_child`). The CUDA
  libraries and their GPU memory go when it ends, so the service itself never
  holds the GPU, and a crash in them cannot take the service down. The child
  sends its log and progress back as JSON lines on its standard output; what
  the libraries print goes to the service's journal.
- **The hold.** Before loading Whisper on the GPU, the process holds the gate
  for `Whisper <model>`, 7 GB (`gpu_gate.WHISPER_GB`). It lets go once the
  model is freed, and the hold ends with the process however it ends. The hold
  says it lasts 5 minutes plus a tenth of the audio's length.
- **Measured** with `gpu run` (2026-11-10): large-v3 in float16 had at most
  4.4 GB on the GPU on 6 minutes of speech and 5.3 GB on 78 minutes, so longer
  audio reaches a little higher, and the claim keeps 1.7 GB above the longer
  run. It ran 13.7 and 11.4 times as fast as the audio plays (the second beside
  a busy processor) after a 4 to 5 s load. The other models are smaller.
- **Lanes.** A transcript clicked for on the video page is
  `ytarchive transcripts` in the lane `now`: the user waits on it.
  `./yt transcribe` is `ytarchive transcribe, run by hand` in `backfill`, as
  the gate's guide puts every command run by hand.
- **It waits as long as it takes**, and the transcript panel shows why
  ("waiting for the GPU: 4.4 GB is free; it needs 7.0"). The gate's rule for
  a job on a queue is to wait at most 2 minutes so the jobs behind it are not
  held up; this queue holds only transcripts, which need the GPU too, so
  nothing behind one could go ahead. The gate alerts the user's desktop when
  a `now` request waits 15 minutes.
- A transcript is one item, so it does not step aside partway through: nothing
  ranks above `now`, and a three-hour recording takes about 16 minutes.
- On the processor there is no hold. Where the gate is not installed
  (matthoom not checked out beside this repo), Whisper runs without it;
  `YT_ARCHIVE_GPU_GATE` points at the gate's file anywhere else.

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

The model is downloaded to the normal Hugging Face cache on first use. After
that it loads from the cache with `local_files_only`, so a transcription sends
no request to the Hugging Face Hub (released weights never change). CUDA
with float16 is selected when CTranslate2 can use the local GPU; otherwise the
same model runs with CPU int8.

Explicit CLI parity is available for maintenance:

```sh
./yt transcribe <id>
./yt transcribe <id> --language en --model large-v3 --beam-size 5
```
