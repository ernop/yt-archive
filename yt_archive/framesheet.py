"""Shot images + contact sheet. Method: docs/framesheets.md (do not reinvent).

  1. Sample the video at SAMPLE_FPS (2), long edge MAX_LONG.
  2. OpenCLIP ViT-B-32 embeds every sample. A new shot starts when cosine
     sim to the last kept sample is < sim_threshold (default 0.85).
     Last-kept, not previous-0.5s: a slow push-in that changes the look
     still fires. No lookback into earlier history.
  3. The kept sample IS the tile (the frame where the look changed).
  4. Survivors tile into near-square squash grids. Up to 250 shots stay
     together. Above 250, balanced sheets cap at MAX_SHEET_TILES (200)
     so there is no tiny final sheet.

Also writes the individual shot PNGs under _condensed/shots/.
Optional --all-sheet writes shots_all_labeled.png (numbered + timestamp).
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from .paths import (
    all_labeled_path,
    condensed_dir,
    shots_dir,
    shots_json_path,
)
from .download import write_archive_json

MAX_LONG = 800
GAP = 4
BATCH = 32
SAMPLE_FPS = 2
DEFAULT_SIM = 0.85
LABEL_H = 36
ALL_TILE = 360
MAX_SHEET_TILES = 200
SINGLE_SHEET_TILES = 250


def make_shots(
    video: Path,
    data_dir: Path,
    video_id: str,
    sim_threshold: float = DEFAULT_SIM,
    layout: str = "squash",
    write_all_sheet: bool = False,
    log=print,
) -> dict:
    import torch
    import open_clip
    from PIL import Image

    cond = condensed_dir(data_dir, video_id)
    dest_shots = shots_dir(data_dir, video_id)
    dest_shots.mkdir(parents=True, exist_ok=True)
    for old in dest_shots.glob("*.png"):
        old.unlink()

    all_imgs = []
    kept_imgs = []
    records = []
    # Sample frames can total >10GB; /tmp is tmpfs (RAM-backed), and dumping
    # them there evicts the rest of the system to swap. Keep them on disk.
    tmp = tempfile.mkdtemp(prefix="yt_archive_keys_", dir=str(data_dir))
    try:
        log(f"sampling {video.name} at {SAMPLE_FPS} fps…")
        thumbs = _sample_thumbs(video, Path(tmp))
        if not thumbs:
            raise RuntimeError(f"no frames sampled from {video}")
        embs = _embed_all(thumbs, torch, open_clip, Image)
        keep_idx, sim_at = _keep_last(embs, sim_threshold)
        duration = len(thumbs) / SAMPLE_FPS
        log(f"{len(thumbs)} samples, {len(keep_idx)} shots at sim<{sim_threshold} vs last kept")

        for j, i in enumerate(keep_idx):
            t0 = i / SAMPLE_FPS
            t1 = keep_idx[j + 1] / SAMPLE_FPS if j + 1 < len(keep_idx) else duration
            rec = {
                "index": j,
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "sample": round(t0, 3),
                "mid": round((t0 + t1) / 2, 3),
                "kept": True,
                "sim_to_prev": None if sim_at[j] is None else round(sim_at[j], 4),
                "file": None,
            }
            im = Image.open(thumbs[i]).convert("RGB")
            name = f"{len(kept_imgs):04d}.png"
            im.save(dest_shots / name)
            rec["file"] = name
            kept_imgs.append(im)
            if write_all_sheet:
                all_imgs.append(im)
            records.append(rec)
    finally:
        shutil.rmtree(tmp)

    cond.mkdir(parents=True, exist_ok=True)
    sheet_names, sheet_sizes, descs = _write_sheets(kept_imgs, cond, layout, log)

    if write_all_sheet and all_imgs:
        labeled, ldesc = _layout_labeled(all_imgs, records)
        out_all = all_labeled_path(data_dir, video_id)
        labeled.save(out_all)
        log(f"all-shots {ldesc} {labeled.width}x{labeled.height} → {out_all}")

    summary = {
        "video_id": video_id,
        "video": str(video),
        "sim_threshold": sim_threshold,
        "compare": "last-kept",
        "sample": "change",
        "sample_fps": SAMPLE_FPS,
        "shots_detected": len(records),
        "shots_kept": len(kept_imgs),
        "framesheet": sheet_names[0],
        "framesheets": sheet_names,
        "layout": "; ".join(descs),
        "sheet_size": sheet_sizes[0],
        "sheet_sizes": sheet_sizes,
        "shots": records,
    }
    shots_json_path(data_dir, video_id).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    write_archive_json(
        data_dir,
        video_id,
        video,
        extra={
            "shots_detected": len(records),
            "shots_kept": len(kept_imgs),
            "framesheet": str((cond / sheet_names[0]).relative_to(video.parent)),
            "framesheets": [
                str((cond / name).relative_to(video.parent)) for name in sheet_names
            ],
        },
    )
    return summary


def _write_sheets(imgs, cond: Path, layout: str, log=print):
    """Tile imgs into one sheet through 250, then balanced sheets of <=200.

    A single chunk saves as framesheet.png; more become framesheet-1.png,
    framesheet-2.png, … Stale framesheet*.png from an earlier run are
    removed either way, so a re-run never mixes old and new parts.
    """
    tile = _layout_grid if layout == "grid" else _layout_squash
    for old in cond.glob("framesheet*.png"):
        old.unlink()
    chunks = []
    offset = 0
    for size in _sheet_chunk_sizes(len(imgs)):
        chunks.append(imgs[offset:offset + size])
        offset += size
    names, sizes, descs = [], [], []
    for n, chunk in enumerate(chunks, start=1):
        sheet, desc = tile(chunk)
        name = "framesheet.png" if len(chunks) == 1 else f"framesheet-{n}.png"
        sheet.save(cond / name)
        names.append(name)
        sizes.append([sheet.width, sheet.height])
        descs.append(desc)
        log(f"framesheet {desc} {sheet.width}x{sheet.height} → {cond / name}")
    return names, sizes, descs


def _sheet_chunk_sizes(total: int) -> list[int]:
    """Keep modest videos whole; otherwise avoid a small, awkward last sheet."""
    if total <= 0:
        return []
    if total <= SINGLE_SHEET_TILES:
        return [total]
    sheet_count = max(2, math.ceil(total / MAX_SHEET_TILES))
    base, extra = divmod(total, sheet_count)
    return [base + 1] * extra + [base] * (sheet_count - extra)


def _sample_thumbs(video: Path, out_dir: Path) -> list[Path]:
    dest = out_dir / "%05d.png"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video),
            "-vf", f"fps={SAMPLE_FPS},scale={MAX_LONG}:{MAX_LONG}:force_original_aspect_ratio=decrease",
            str(dest),
        ],
        check=True,
    )
    return sorted(out_dir.glob("*.png"))


def _keep_last(embs, threshold: float) -> tuple[list[int], list[float | None]]:
    keep = [0]
    sims: list[float | None] = [None]
    for i in range(1, len(embs)):
        sim = float((embs[i] * embs[keep[-1]]).sum())
        if sim < threshold:
            keep.append(i)
            sims.append(sim)
    return keep, sims


def _embed_all(files: list[Path], torch, open_clip, Image):
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k"
    )
    model.eval()
    embs = []
    with torch.no_grad():
        for i in range(0, len(files), BATCH):
            batch = torch.stack(
                [preprocess(Image.open(f).convert("RGB")) for f in files[i:i + BATCH]]
            )
            encoded = model.encode_image(batch)
            embs.append(encoded / encoded.norm(dim=-1, keepdim=True))
    return torch.cat(embs)


def _layout_grid(imgs):
    from PIL import Image

    tw, th = imgs[0].size
    n = len(imgs)
    cols = max(1, round(math.sqrt(n * th / tw)))
    best = None
    for c in range(max(1, cols - 2), cols + 3):
        rows = math.ceil(n / c)
        ww = c * tw + (c - 1) * GAP
        hh = rows * th + (rows - 1) * GAP
        ratio = max(ww, hh) / min(ww, hh)
        if best is None or ratio < best[0]:
            best = (ratio, c, rows, ww, hh)
    _, cols, rows, width, height = best
    sheet = Image.new("RGB", (width, height), (0, 0, 0))
    for i, im in enumerate(imgs):
        sheet.paste(im, ((i % cols) * (tw + GAP), (i // cols) * (th + GAP)))
    return sheet, f"grid {cols}x{rows}"


def _pack_rows(imgs, width):
    rows, row, x = [], [], 0
    for im in imgs:
        if row and x + im.width > width:
            rows.append(row)
            row, x = [], 0
        row.append(im)
        x += im.width
    rows.append(row)
    return rows


def _layout_squash(imgs):
    from PIL import Image

    max_w = max(im.width for im in imgs)
    total_w = sum(im.width for im in imgs)
    best = None
    for k in range(1, len(imgs) + 1):
        width = min(total_w, k * max_w)
        rows = _pack_rows(imgs, width)
        ww = max(sum(im.width for im in r) for r in rows)
        hh = sum(max(im.height for im in r) for r in rows)
        ratio = max(ww, hh) / min(ww, hh)
        if best is None or ratio < best[0]:
            best = (ratio, rows, ww, hh)
        if width == total_w:
            break
    _, rows, ww, hh = best
    sheet = Image.new("RGB", (ww, hh), (0, 0, 0))
    y = 0
    for row in rows:
        x = 0
        for im in row:
            sheet.paste(im, (x, y))
            x += im.width
        y += max(im.height for im in row)
    return sheet, f"squash {len(rows)} rows"


def _label_font(size: int):
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _layout_labeled(imgs, records):
    from PIL import Image, ImageDraw

    font = _label_font(18)
    tiles = []
    for im, rec in zip(imgs, records):
        w, h = im.size
        scale = ALL_TILE / max(w, h)
        tile = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        canvas = Image.new("RGB", (tile.width, tile.height + LABEL_H), (16, 16, 16))
        canvas.paste(tile, (0, 0))
        draw = ImageDraw.Draw(canvas)
        kept = rec.get("kept", True)
        bar = (120, 28, 28) if not kept else (28, 28, 28)
        draw.rectangle((0, tile.height, tile.width, tile.height + LABEL_H), fill=bar)
        t = rec.get("sample", rec.get("t0")) or 0
        tag = "" if kept else " DROP"
        text = f"{rec['index']}  {t:.1f}s{tag}"
        draw.text((8, tile.height + 8), text, fill=(255, 220, 220) if not kept else (235, 235, 235), font=font)
        tiles.append(canvas)
    tw = max(t.width for t in tiles)
    th = max(t.height for t in tiles)
    normed = []
    for tile in tiles:
        if tile.size != (tw, th):
            pad = Image.new("RGB", (tw, th), (16, 16, 16))
            pad.paste(tile, (0, 0))
            normed.append(pad)
        else:
            normed.append(tile)
    sheet, desc = _layout_grid(normed)
    return sheet, f"labeled {desc}"
