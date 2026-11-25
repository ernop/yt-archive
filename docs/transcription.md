# Local transcription and captions

Parent: [AGENTS.md](../AGENTS.md).

## Product behavior

- Transcription is explicit, per video, and never part of download, framesheet,
  or MP3 generation. The video detail page opens a confirmation dialog with two
  modes:
  - **Single stream (Whisper)** — `faster-whisper` `large-v3` by default, word
    timestamps, beam size 5, automatic language detection across 99 languages,
    voice-activity filtering, and `condition_on_previous_text=False` (prevents
    long-recording lowercase/unpunctuated context collapse).
  - **Multi-speaker with overlaps** — NVIDIA `Nemotron-3-Diarization`
    (Sortformer v3, up to 8 speakers, 10 ms frame resolution) followed
    sequentially by `Multitalker Parakeet Ultra` (speaker-conditioned Parakeet
    TDT ONNX) across 25 European languages. Both checkpoints are ungated ONNX
    files cached locally after first download. Speakers are numbered in arrival
    order (`Speaker 1`, `Speaker 2`, …) and simultaneous speech is preserved
    with per-word timestamps instead of collapsing to a single stream.
- Transcript content is spoken language only. Bracketed non-speech labels such
  as music, applause, waves, and birds are removed rather than mixed into the
  spoken transcript. Non-speech event annotation, if added later, must be a
  separate data track and UI layer.
- Results are written to `_condensed/transcript.json` and
  `_condensed/transcript.vtt`, then mirrored into SQLite for search and fast
  browsing. Files remain the recoverable source of truth.
- The detail page provides a private timed transcript below the player:
  timestamp/word seeking, simultaneous multi-row and word-level playback
  highlighting, full-text search, speaker and overlap filtering, rolling or
  full view, text-size choices, WebVTT download, and an optional actual caption
  track in the local player.
- Speaker names are editable per segment. Reusing a name groups segments;
  speaker groups can be renamed or merged, updating the transcript, timeline,
  and WebVTT captions together.
- When a transcript has two or more speakers, the video page switches to a
  **Split view** workbench on wide screens (toggleable back to **Stacked view**):
  - **Top row (60% / 40%):** video player on the left, full speaker-labeled
    transcript panel on the right matched to the video's height via
    `ResizeObserver`. Transcript auto-scroll adjusts only `#caption-box.scrollTop`
    (never scrolling the outer page) and toggles on/off with **Auto-scroll**.
  - **Bottom row (100% full width):** **Live dual-color speaker subtitles**
    (toggleable with **Live subs** or **Overlay on video**) and the
    time-proportional **Speaker overlap timeline** (`10s / 20s / 30s / 60s`
    window, ruler overlap duration badges, click-to-seek, drag-to-pan,
    previous/next overlap jumps, and full-video overview minimap).
  - **Inline overlap markers in the transcript:** overlapped words are
    underlined in the interrupting speaker's color; mid-sentence overtalk also
    shows a compact clickable inline note (`∥ Speaker 2: "…"`) right at the
    words it overlapped, while the interrupting speaker's own row remains
    present beneath with a `∥` badge and colored rail. Hovering a row, chip,
    or swimlane bar cross-highlights every paired overlapping turn.

## Settled multi-speaker architecture (2026-11-25)

Multi-speaker transcription uses pure ONNX Runtime (`yt_archive/multitalker.py`):

1. **Pre-GPU preparation:** `ffmpeg` decodes `soundtrack.mp3` to 16 kHz mono
   and CPU `torch.stft` + Slaney log-mel filterbank computes Sortformer features
   before acquiring the GPU gate, so the GPU is never held idle during decoding.
2. **Diarization:** `Nemotron-3-Diarization` runs streaming Sortformer v3 on
   CUDA (~0.8 GB VRAM), binarized with hysteresis (`onset=0.55`, `offset=0.45`,
   `min_on=0.15 s`, `min_off=0.10 s`), then unloaded before ASR starts.
3. **Target-speaker ASR:** `Multitalker Parakeet Ultra` transcribes each active
   speaker independently in ~20 s pause-aligned chunks using target and
   background speaker masks (`speaker_gate=0` on single-speaker chunks), with
   the encoder on CUDA and the per-token joint decoder on CPU.
4. **Utterance grouping:** per-speaker word streams split on pauses, other-speaker
   interjections, and sentence boundaries while keeping exact overlapping times.

Models come from `altunenes/parakeet-rs` and load with `local_files_only=True`
once cached. Because ONNX Runtime rejects external data (`.onnx.data`) across
Hugging Face's `blobs/` symlinks, the four ASR files are hardlinked into a flat
directory inside the Hugging Face cache (`yt_archive_staged/<commit>/`).

Rejected approaches (do not reintroduce):

- `pyannote/speaker-diarization-community-1` (raw or vocal-separated): merged
  distinct voices and flipped speaker labels mid-sentence during rapid
  overlapping dialogue.
- Single-stream Whisper + forced alignment + diarization: drops simultaneous
  speech because Whisper emits only one text stream per time span.
- Physical speech separation (`MossFormer2_SS_16K`) + Whisper: split solo
  voices inconsistently across stems and lost global speaker identity.

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

Settled 2026-11-10 (updated 2026-11-25 for multi-speaker ONNX). Every program
on this computer that puts anything on the GPU holds it through matthoom's gate
first (`core/utils/gpu.py`, explained in matthoom's `docs/GPU.md`), so none
runs out of GPU memory beside another and the most urgent work goes first.
Transcription (Whisper and multi-speaker ONNX) is ytarchive's only GPU work:
shots run CLIP on the processor.

- **A process of its own.** The service runs each transcription as
  `python -m yt_archive.transcribe <job>` (`transcribe_in_child`). The CUDA
  libraries and their GPU memory go when it ends, so the service itself never
  holds the GPU, and a crash in them cannot take the service down. The child
  sends its log and progress back as JSON lines on its standard output; what
  the libraries print goes to the service's journal.
- **Whisper hold.** Before loading Whisper on the GPU, the process holds the
  gate for `Whisper <model>`, 7 GB (`gpu_gate.WHISPER_GB`). It lets go once the
  model is freed, and the hold ends with the process however it ends. The hold
  says it lasts 5 minutes plus a tenth of the audio's length.
- **Measured (Whisper)** with `gpu run` (2026-11-10): large-v3 in float16 had
  at most 4.4 GB on the GPU on 6 minutes of speech and 5.3 GB on 78 minutes, so
  longer audio reaches a little higher, and the claim keeps 1.7 GB above the
  longer run. It ran 13.7 and 11.4 times as fast as the audio plays (the second
  beside a busy processor) after a 4 to 5 s load. The other models are smaller.
- **Multi-speaker hold.** After CPU audio decoding and feature extraction, the
  process holds the gate for `Multitalker Parakeet + Nemotron-3`, 5.5 GB
  (`gpu_gate.MULTITALKER_GB`), for 3 minutes plus a twentieth of the audio's
  length. Diarization unloads before the ASR encoder loads.
- **Measured (multi-speaker)** on CUDA (2026-11-25): peaked at 3.3 GB on
  2-minute clips and 4.28 GB (4380 MiB) on a 115-minute two-speaker recording
  with dense overlap, completing end-to-end in 259.5 s (26.6× real-time). The
  5.5 GB claim keeps 1.2 GB above the 115-minute peak.
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
  ranks above `now`, and a three-hour recording takes about 7 minutes in
  multi-speaker mode or 16 minutes in Whisper.
- On the processor there is no hold. Where the gate is not installed
  (matthoom not checked out beside this repo), transcription runs without it;
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

Install the local runtimes in this repository's venv:

```sh
.venv/bin/python -m pip install \
  faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12 \
  onnxruntime-gpu==1.24.4 \
  nvidia-cuda-runtime-cu12 nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-nvjitlink-cu12
```

`onnxruntime-gpu==1.24.4` is pinned because it targets CUDA 12 (matching the
CUDA 12 wheels shared with `faster-whisper`); newer `onnxruntime-gpu` releases
expect CUDA 13. Models download to the normal Hugging Face cache on first use.
After that they load from the cache with `local_files_only`, so a transcription
sends no request to the Hugging Face Hub (released weights never change).
Whisper selects CUDA float16 when available and falls back to CPU int8;
multi-speaker mode uses ONNX Runtime `CUDAExecutionProvider` when a GPU is
present and fails loudly if CUDA libraries cannot initialize while claiming the
GPU.

Explicit CLI parity is available for maintenance:

```sh
./yt transcribe <id>
./yt transcribe <id> --speakers
./yt transcribe <id> --language en --model large-v3 --beam-size 5
```
