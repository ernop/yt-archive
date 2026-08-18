# Shot images and framesheet

Parent: [agents.md](../agents.md). Settled 2026-08-14. Do not reinvent.

Code: `yt_archive/framesheet.py`. Everything local (HF cache
`~/.cache/huggingface`, ~600 MB CLIP; TransNetV2 weights ship with the pip
package).

## Pipeline

1. **TransNetV2** (`transnetv2-pytorch`) classifies every frame at 48×27.
   Detects hard cuts *and* dissolves. Transition frames belong to no shot.
   **No fixed sampling rate** — the video's editing decides tile count.
2. One keyframe per shot at the **temporal midpoint** (maximally far from
   both transitions, so never a crossfade blend), extracted at full
   resolution with ffmpeg.
3. **OpenCLIP ViT-B-32** (`laion2b_s34b_b79k`) embeds keyframes. A shot is
   dropped when cosine similarity to **any already-kept** frame is ≥ `0.90`.
   Consecutive-only comparison misses slideshows that cycle the same photos
   (found 2026-08-17 on `XzWdZPFs2f8`). Lower toward 0.85 for only hard
   scene changes; raise toward 0.96 to keep near-repeats.
4. Tiles shrink only if the long axis exceeds 800px, then a near-square
   squash grid (flush-left, no gaps; right edge may be ragged).

This service also keeps the individual shot PNGs under `_condensed/shots/`.

## Timing (CPU torch, 1080p)

Shot detection dominates (~134 ms per video-second). Keyframe extract
~85 ms/shot. CLIP ~17 ms/shot after a 1.3 s load. A 5–6 min clip is about
a minute. Peak RAM ~1.7 GB.

## Rejected (do not reintroduce)

Tested on slideshows with slow crossfades:

1. 1 fps + thumbnail pixel diff — no principled threshold.
2. 1 fps + CLIP dedupe — metric is good (same scene ≥0.93, cuts ≤0.85) but
   1/s samples catch mid-dissolve ghosts.
3. CLIP blend-detection (reconstruct a tile from neighbors) — flagged real
   photos. Embeddings are not linear over pixel blends.
4. 6 fps + per-second stability snap — worked, still an arbitrary time
   base. Superseded by shot detection.
