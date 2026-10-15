# Shot images and framesheet

Parent: [agents.md](../agents.md). Settled 2026-08-18. Do not reinvent.

Code: `yt_archive/framesheet.py`. Everything local (HF cache
`~/.cache/huggingface`, ~600 MB CLIP). The pinned weights load from that cache
without contacting the Hugging Face Hub (`_load_clip` passes
`local_files_only`); only a missing cache downloads them. Before 2026-10-14
every shot job sent the Hub a revision check.

Sheets are written as `.framesheet*.png.part` and renamed into place only once
every sheet of the set is complete, so a stopped job leaves the previous
sheets, not a truncated one. A sheet that does not end in its PNG `IEND` chunk
counts as "framesheet to split" and is laid out again from the shot images.

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

## Balanced sheet sizes (settled 2026-08-30)

Long videos (a 3-hour game ≈ thousands of kept shots) must not make one
unbounded PNG, but modest videos should not be split unnecessarily:

- up to 250 kept shots stay in one `framesheet.png`;
- above 250, there are always at least two sheets;
- multi-sheet output uses `MAX_SHEET_TILES = 200`;
- frames are divided as evenly as possible, with sheet counts differing by at
  most one. For example, 251 becomes 126 + 125 instead of 200 + 51, and 401
  becomes 134 + 134 + 133.

Balanced chunks prevent a tiny, awkward final sheet. Each chunk is then laid
out independently using the existing near-square geometry search, so the
resulting sheets remain compact rectangles. Multi-sheet files are
`framesheet-1.png`, `framesheet-2.png`, … in shot order.

`paths.framesheet_paths()` lists whichever form exists (legacy single sheet
first), and every consumer in this repo — detail page, list thumbnail,
archive-needs check — goes through it. A re-run deletes all
`framesheet*.png` first so old and new parts never mix.

matthoom reads only `_condensed/framesheet.png`, so it shows no sheet for a
multi-sheet video (its shot images still work).

Splitting an existing oversized sheet does not re-run CLIP:
`framesheet.retile_sheets()` loads the kept shot PNGs in `shots.json` order
and lays them out again under this rule, then rewrites `shots.json`,
`archive.json`, and the list thumbnail.

## List thumbnail (settled 2026-10-14)

`_condensed/preview.jpg` is sheet 1 resized to at most 480 px wide (JPEG q85).
It is written right after sheet 1 and rebuilt when it is older than sheet 1.
The archive list uses it; it never uses a full sheet, which can be hundreds of
megapixels (Pillow's decompression-bomb guard is lifted only while making the
preview).

## Is a video current? (settled 2026-10-14)

`shots.json` records the method: `"sample": "change"` and
`"compare": "last-kept"`. A file without both came from an older method
(TransNet, adjacent dedupe, …) and counts as **old shot detection**.
`paths.archive_needs()` names everything a plain job would still do for a
video — no framesheet, old shot detection, framesheet to split, no list
thumbnail, no MP3 audio — and a plain job does exactly that: full pipeline
for the first two, `retile_sheets` for a split, a resize for the thumbnail.
A video with nothing missing is reported complete and no job is queued. See
[browsing.md](browsing.md#archive-status) for the UI.

## Progress

`make_shots` reports each stage for the queue panel: reading frames (from
ffmpeg's `-progress` clock against the probed duration), loading the CLIP
model, comparing frames n / N, saving shot images, laying out the sheet.

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
