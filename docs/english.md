# English versions (captions, transcript, and voiceover dub)

Parent: [AGENTS.md](../AGENTS.md).

An archived video can be translated into English on demand from its video page
(**Make English version…**) or from the command line (`./yt english <id>`).
Nothing runs automatically during downloads.

## What it produces

All outputs live in `data/<id>/_condensed/`. The original video file is never
modified.

- `transcript.en.json` — timed English segments (`text`, `dub_text`,
  `text_orig`, `speaker`, translated `chapters`, token usage, provider/model).
  This file is written last as the publication marker.
- `transcript.en.vtt` — English WebVTT captions shown in the browser player.
- `subtitles.en.ass` — styled ASS subtitles with an opaque dark lower-third box
  and speaker-colored labels to cover burned-in source subtitles in external
  players such as VLC.
- `subtitles.en.srt` and `subtitles.orig.srt` — plain English and original SRT
  files.
- `dub.en.m4a` — optional multi-speaker English voiceover mixed over smoothly
  ducked original stereo audio (original audio ducks to -17.7 dB during speech
  and returns to 0 dB during pauses and music, normalized to EBU R128 -16 LUFS).
- `english.mkv` — built on demand by **Download bilingual MKV**, **Open in
  VLC**, or `./yt english <id> --mkv`. Losslessly copies the original video and
  original audio streams, adds `dub.en.m4a` as the default audio track when
  present, attaches the ASS and SRT subtitle tracks, and writes translated
  chapters.

The archive's global search and creator search index both the original
transcript (`transcript_segments`) and the English translation
(`english_segments`, including English text, original text, and speaker names).
`./yt reindex` rebuilds the English index from `transcript.en.json`.

## Pipeline stages

1. **Source transcript.** Reuses `_condensed/transcript.json` if it is already
   complete; otherwise runs local Whisper `large-v3` (`beam_size=5`,
   `vad_filter=True`) under the GPU gate (`7.0 GB`).
2. **Speaker diarization (optional).** When `english.diarize_python` is set in
   `settings.json`, runs `pyannote/speaker-diarization-community-1` in that
   Python environment under the GPU gate (`13.0 GB`) and splits Whisper segments
   at word-level speaker transitions. If `diarize_python` is empty, diarization
   is skipped. If it is set and fails, the job fails loudly.
3. **LLM translation.** Sends chunked segments (default 120 segments per chunk,
   with rolling context from preceding segments) via plain HTTPS JSON Schema
   structured output (`yt_archive/llm.py`, no SDK dependencies). Supports
   `openai`, `anthropic`, `gemini`, and local `ollama`. Automatically runs a
   single repair pass for any missing segment IDs and translates video chapters
   when present.
4. **Voiceover dub (optional, on by default).** Synthesizes each speaker turn
   with a distinct Kokoro v1.0 voice (`kokoro-onnx` on CUDA via
   `onnxruntime-gpu`, holding the GPU gate at `2.5 GB`), adjusts speaking speed
   to fit each segment's window, and mixes over the ducked original stereo
   audio into `dub.en.m4a`.

Work runs in a child process via `DerivedWorkQueue` (`kind="english"`) so CUDA
VRAM is released when the job finishes. Interrupted jobs are re-queued on
service restart.

## Configuration (`settings.json`)

Copy `settings.example.json` to gitignored `settings.json` and configure the
`english` object:

- `provider` — `"openai"` (default), `"anthropic"`, `"gemini"`, or `"ollama"`.
- `model` — model name (default `"gpt-6-luna"`).
- `effort` — reasoning effort for OpenAI/Anthropic: `""`, `"low"` (default),
  `"medium"`, or `"high"`.
- `api_key` — API key for cloud providers (never rendered in HTML or logged).
- `ollama_url` — Ollama base URL (default `"http://127.0.0.1:11434"`).
- `diarize_python` — optional path to a Python executable that has
  `pyannote.audio` installed.
- `kokoro_model_dir` — folder holding `kokoro-v1.0.onnx` and `voices-v1.0.bin`
  (defaults to `~/.cache/kokoro-onnx`).
- `chunk_size` — segments per LLM request (10–300, default 120).
- `concurrency` — parallel LLM requests for cloud providers (1–12, default 6;
  Ollama always runs 1 at a time).

Missing or invalid keys fail before GPU work starts.

## Measurements (2026-11-25)

Measured on an RTX 3090 (24 GB VRAM) using a 1-hour Mandarin documentary
(1,765 segments, ~45 minutes of speech) and a 120-segment + 23-segment
homophone repair benchmark:

| Backend | 120-seg chunk | Scaled 1h wall | Cost / 1h | Hard ASR homophones |
|---------|---------------|----------------|-----------|---------------------|
| `openai / gpt-6-luna` (low) | 25.4 s | ~0.9 min | \$0.04 | 4/4 repaired |
| `anthropic / claude-haiku-5-5` (low) | 29.4 s | ~1.0 min | \$0.07 | 3/4 |
| `anthropic / claude-sonnet-5-5` (low) | 34.3 s | ~1.2 min | \$1.13 | 2/4 |
| `openai / gpt-6.1-sol` (low) | 62.4 s | ~2.1 min | \$0.69 | 4/4 repaired |
| Local Ollama 27B–31B | >300 s timeout | >75 min | \$0 | timed out |

GPU Kokoro v1.0 ONNX (`onnxruntime-gpu`, `CUDAExecutionProvider` after
`onnxruntime.preload_dlls()`): 1.43 s load, 11.8x realtime (~3.8 min for 45 min
of speech), peak VRAM 1.3 GB (`KOKORO_GB = 2.5` in `yt_archive/gpu_gate.py`).

End-to-end wall time for a 1-hour video with `openai / gpt-6-luna` and Kokoro
GPU dubbing is ~13 minutes from scratch, or ~8 minutes when a local Whisper
transcript already exists.

## Playback in the browser

- When `dub.en.m4a` exists, the video player adds an **Audio** control (`B` key)
  switching between **Original** and **English dub** in sync with the video.
- When `transcript.en.vtt` exists, `C` (and the CC button) cycles **English**,
  **Original**, and **Off**.
- **Cover burned-in captions** styles WebVTT cues with an opaque black band over
  the lower area of the video.
- The transcript panel switches between **English**, **Original**, and
  **Side-by-side** views; speaker edits on the English views update
  `transcript.en.json`, `transcript.en.vtt`, `subtitles.en.ass`, and
  `subtitles.en.srt`.
