"""Shot images + contact sheet. Method: docs/framesheets.md (do not reinvent).

  1. TransNetV2 segments shots (hard cuts and dissolves). No fixed fps.
  2. One keyframe at each shot's temporal midpoint (never a crossfade blend).
  3. OpenCLIP ViT-B-32 drops a keyframe when cosine sim to any already-kept
     frame is >= sim_threshold (default 0.90).
  4. Survivors shrink to <=800px long axis and tile into a near-square grid.

Also writes the individual shot PNGs under _condensed/shots/.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from .paths import (
    condensed_dir,
    framesheet_path,
    shots_dir,
    shots_json_path,
)
from .download import write_archive_json

MAX_LONG = 800
GAP = 4
BATCH = 32
DEFAULT_SIM = 0.90


def make_shots(
    video: Path,
    data_dir: Path,
    video_id: str,
    sim_threshold: float = DEFAULT_SIM,
    layout: str = "squash",
    log=print,
) -> dict:
    import torch
    import open_clip
    from PIL import Image
    from transnetv2_pytorch import TransNetV2

    log(f"detecting shots in {video.name}…")
    shots = _detect_shots(video, TransNetV2)
    log(f"{len(shots)} shots detected")
    if not shots:
        raise RuntimeError(f"no shots detected in {video}")

    cond = condensed_dir(data_dir, video_id)
    dest_shots = shots_dir(data_dir, video_id)
    dest_shots.mkdir(parents=True, exist_ok=True)
    for old in dest_shots.glob("*.png"):
        old.unlink()

    tmp = tempfile.mkdtemp(prefix="yt_archive_keys_")
    try:
        files = _extract_keyframes(video, shots, Path(tmp))
        embs = _embed_all(files, torch, open_clip, Image)
        # Compare each candidate to every already-kept frame, not just the
        # predecessor. Consecutive-only misses slideshows that cycle the
        # same photos.
        keep_idx = [0]
        max_sim_to_kept: list[float | None] = [None]
        for i in range(1, len(files)):
            sims = (embs[i] * embs[keep_idx]).sum(dim=-1)
            best = float(sims.max())
            max_sim_to_kept.append(best)
            if best < sim_threshold:
                keep_idx.append(i)
        log(f"{len(keep_idx)} keyframes kept at sim<{sim_threshold}")

        kept_imgs = []
        records = []
        keep_set = set(keep_idx)
        for i, (t0, t1) in enumerate(shots):
            mid = (t0 + t1) / 2
            sim_kept = max_sim_to_kept[i]
            kept = i in keep_set
            rec = {
                "index": i,
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "mid": round(mid, 3),
                "kept": kept,
                "sim_to_kept": None if sim_kept is None else round(sim_kept, 4),
                "file": None,
            }
            if kept:
                im = Image.open(files[i]).convert("RGB")
                w, h = im.size
                if max(w, h) > MAX_LONG:
                    scale = MAX_LONG / max(w, h)
                    im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
                name = f"{len(kept_imgs):04d}.png"
                im.save(dest_shots / name)
                rec["file"] = name
                kept_imgs.append(im)
            records.append(rec)
    finally:
        shutil.rmtree(tmp)

    if layout == "grid":
        sheet, desc = _layout_grid(kept_imgs)
    else:
        sheet, desc = _layout_squash(kept_imgs)
    out_sheet = framesheet_path(data_dir, video_id)
    cond.mkdir(parents=True, exist_ok=True)
    sheet.save(out_sheet)
    log(f"framesheet {desc} {sheet.width}x{sheet.height} → {out_sheet}")

    summary = {
        "video_id": video_id,
        "video": str(video),
        "sim_threshold": sim_threshold,
        "shots_detected": len(shots),
        "shots_kept": len(kept_imgs),
        "framesheet": out_sheet.name,
        "layout": desc,
        "sheet_size": [sheet.width, sheet.height],
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
            "shots_detected": len(shots),
            "shots_kept": len(kept_imgs),
            "framesheet": str(out_sheet.relative_to(video.parent)),
        },
    )
    return summary


def _detect_shots(video: Path, TransNetV2) -> list[tuple[float, float]]:
    model = TransNetV2()
    model.eval()
    scenes = model.detect_scenes(str(video))
    return [(float(s["start_time"]), float(s["end_time"])) for s in scenes]


def _extract_keyframes(video: Path, shots: list[tuple[float, float]], out_dir: Path) -> list[Path]:
    files = []
    for i, (t0, t1) in enumerate(shots):
        mid = (t0 + t1) / 2
        dest = out_dir / f"{i:04d}.png"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-ss", f"{mid:.3f}", "-i", str(video), "-frames:v", "1", str(dest),
            ],
            check=True,
        )
        files.append(dest)
    return files


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
