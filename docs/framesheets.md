# Shot images and framesheet

Parent: [agents.md](../agents.md). Settled 2026-08-18. Do not reinvent.

Code: `yt_archive/framesheet.py`. Everything local (HF cache
`~/.cache/huggingface`, ~600 MB CLIP).

## Pipeline

1. **Sample at 2 fps**, long edge 800px, one ffmpeg pass. No TransNet.
   The video's looks decide tile count, not a 48×27 cut classifier.
2. **OpenCLIP ViT-B-32** (`laion2b_s34b_b79k`) embeds every sample. A new
   shot starts when cosine similarity to the **last kept** sample is
   `< 0.85`. That is last-kept, not previous-0.5s: a slow push-in that
   changes the picture still fires (0:06 archway → 0:08 face on
   `SFQpQTrtSEc`). No lookback into earlier history.
3. The sample that crossed the threshold **is** the tile — the frame
   where we alleged the look changed. Shot 0 is t=0.
4. Tiles are already ≤800px. Near-square squash grid (flush-left, no
   gaps; right edge may be ragged).

This service also keeps the individual shot PNGs under `_condensed/shots/`.
`./yt shots <id> --all-sheet` also writes `_condensed/shots_all_labeled.png`
(every kept shot, numbered + timestamp).

Clicking a shot seeks to that sample (`t0`).

Raise `--sim` toward 0.90 to split more (near-repeats stay); lower toward
0.80 to keep only harder look changes.

## At most 100 tiles per sheet (settled 2026-08-29)

Long videos (a 3-hour game ≈ thousands of kept shots) made one giant
PNG. Sheets now cap at `MAX_SHEET_TILES = 100` tiles: ≤100 kept shots
stay a single `framesheet.png`; more split into `framesheet-1.png`,
`framesheet-2.png`, … in shot order, each laid out independently.
`paths.framesheet_paths()` lists whichever form exists (legacy single
sheet first), and every consumer — detail page, card thumbnail (part 1),
static export, job skip-check — goes through it. A re-run deletes all
`framesheet*.png` first so old and new parts never mix.

## Sample dir is on disk, not /tmp (settled 2026-08-29)

`mkdtemp` for the sampled frames passes `dir=data_dir`. On Linux `/tmp`
is tmpfs (RAM): a 3-hour video at 2 fps is ~22k PNGs ≈ 11 GB, which
evicted the whole desktop to swap on a spinning disk and froze the
machine. Frames are written once and read once, so disk is fine. Do not
move this back to the default tempdir.

## Timing (CPU torch, 1080p)

One 2 fps decode + CLIP (~17 ms/sample after a 1.3 s load). A 5–6 min
clip is a few hundred samples, well under a minute. Peak RAM is the CLIP
model plus the sample batch.

## Why not TransNetV2

TransNetV2 at 48×27 with threshold 0.5 called the opening of
`SFQpQTrtSEc` two scenes (`0–11.2`, `11.2–23.7`). Inside those spans the
max cut score was 0.01–0.11. Human-obvious look changes at 0:06, 0:08,
and 0:17 never became tiles. Infrared B&W plus camera moves that reframe
the same palette are invisible at that resolution. CLIP at 224px on a
2 fps grid sees them.

## Rejected (do not reintroduce)

Tested on slideshows with slow crossfades, then on `SFQpQTrtSEc`:

1. 1 fps + thumbnail pixel diff — no principled threshold.
2. 1 fps + CLIP *adjacent-sample* dedupe — metric is good (same look
   ≥0.93, hard cuts ≤0.85) but a slow push-in stays adjacent-similar and
   never fires. Last-kept is the fix.
3. CLIP blend-detection (reconstruct a tile from neighbors) — flagged
   real photos. Embeddings are not linear over pixel blends.
4. 6 fps + per-second stability snap — worked, still an arbitrary time
   base used as a *snap*, not as last-kept change detection.
5. Midpoint-of-scene keyframe — samples far from the change we detected.
6. Compare each candidate to every already-kept frame — far lookback
   drops later angles of a face already seen (`SFQpQTrtSEc`). Slideshows
   that cycle the same photos (`XzWdZPFs2f8`) may now reappear; accepted.
7. TransNetV2 as the sole gate — misses look changes that are obvious at
   full(ish) resolution. See above.
