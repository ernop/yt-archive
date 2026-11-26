#!/usr/bin/env python3
"""Export a multi-speaker archived video as a standalone static YouTube-backed overlap viewer.

Zero video re-hosting: embeds the original YouTube video via youtube-nocookie.com
using postMessage (no external script tags; compatible with strict `script-src 'self'` CSPs).

Usage:
    .venv/bin/python scripts/export_overlap_page.py <video_id> <out_dir> [--back-url https://example.com/] [--back-label "site"]
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yt_archive.paths import (
    default_data_dir,
    load_archive_info,
    parse_video_id,
    transcript_json_path,
    video_dir,
)


def _read_chapters(data_dir: Path, video_id: str) -> list[dict]:
    folder = video_dir(data_dir, video_id)
    for info_file in sorted(folder.glob("*.info.json")):
        try:
            raw = json.loads(info_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        chapters = raw.get("chapters") or []
        if isinstance(chapters, list) and chapters:
            return [
                {
                    "s": round(float(c.get("start_time") or 0), 2),
                    "e": round(float(c.get("end_time") or 0), 2),
                    "t": str(c.get("title") or "").strip(),
                }
                for c in chapters
                if c.get("title")
            ]
    return []


def _compact_transcript(data_dir: Path, video_id: str) -> dict:
    tpath = transcript_json_path(data_dir, video_id)
    if not tpath.is_file():
        raise FileNotFoundError(f"missing transcript: {tpath}")
    doc = json.loads(tpath.read_text(encoding="utf-8"))
    info = load_archive_info(data_dir, video_id)
    speakers: list[str] = []
    spk_idx: dict[str, int] = {}
    segments = []
    for i, seg in enumerate(doc.get("segments") or []):
        spk = str(seg.get("speaker") or "").strip()
        if spk not in spk_idx:
            spk_idx[spk] = len(speakers)
            speakers.append(spk)
        words = []
        for w in seg.get("words") or []:
            txt = str(w.get("word") or "").strip()
            if not txt:
                continue
            ws = round(float(w.get("start") if w.get("start") is not None else seg.get("start") or 0), 2)
            we = round(float(w.get("end") if w.get("end") is not None else ws), 2)
            words.append([ws, we, txt])
        segments.append(
            {
                "i": int(seg.get("index", i)),
                "s": round(float(seg.get("start") or 0), 2),
                "e": round(float(seg.get("end") or 0), 2),
                "k": spk_idx[spk],
                "t": str(seg.get("text") or "").strip(),
                "w": words,
            }
        )
    return {
        "v": 1,
        "video_id": video_id,
        "title": info.get("title") or video_id,
        "channel": info.get("channel") or "",
        "upload_date": info.get("upload_date") or "",
        "duration": round(float(doc.get("duration") or info.get("duration") or 0), 2),
        "engine": str(doc.get("engine") or ""),
        "chapters": _read_chapters(data_dir, video_id),
        "speakers": speakers,
        "segments": segments,
    }


APP_CSS = r"""* { box-sizing: border-box; }
:root {
  --bg: #07080a;
  --panel: #0e1014;
  --border: #262b33;
  --fg: #ffffff;
  --accent: #7cc4ff;
  --gold: #ffd240;
  --red: #ff4747;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
html, body {
  margin: 0; padding: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
a { color: #7cc4ff; text-decoration: none; }
a:hover { text-decoration: underline; }
button, select, input {
  font: inherit; color: #fff; background: #14171d; border: 1px solid #383f4b;
  border-radius: 5px; padding: .26rem .52rem; cursor: pointer;
}
button:hover, select:hover { background: #1e232c; border-color: #637083; }
button:disabled { opacity: 1; border-color: #252a32; background: #101216; cursor: not-allowed; }
button[aria-pressed="true"] { border-color: #68c77b; background: #142618; }
input[type="search"], input[type="text"] { cursor: text; }
input::placeholder { color: #fff; opacity: 1; }
[hidden] { display: none !important; }
[lang="ja"], [lang="ja"] * {
  font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Hiragino Kaku Gothic ProN",
    "Yu Gothic UI", "Yu Gothic", Meiryo, "Noto Sans CJK JP", "Noto Sans JP", sans-serif;
}
ruby { ruby-position: over; }
rt { font-size: .56em; font-weight: 600; color: #dbeafe; user-select: none; }
html.no-furigana rt { display: none; }

header.top {
  padding: .55rem 1rem .45rem; border-bottom: 1px solid #1d2129; background: #0a0b0e;
}
.top-row {
  display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
  gap: .35rem 1rem;
}
header.top h1 { margin: 0; font-size: 1.12rem; line-height: 1.25; font-weight: 700; }
.top-nav { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem .85rem; font-size: .84rem; }
.top-nav .sep { opacity: .45; }
.top-nav button { font-size: .8rem; padding: .16rem .46rem; }
.chapters { display: flex; flex-wrap: wrap; align-items: center; gap: .3rem; margin-top: .38rem; }
.chapters button { font-size: .78rem; padding: .14rem .45rem; background: #111419; border-color: #2d333e; }

main { padding: .6rem 1rem 2rem; width: 100%; max-width: none; margin: 0 auto; }
main.split-view { display: block; }

.player-wrap {
  position: relative; background: #000; border: 1px solid var(--border);
  border-radius: 7px; overflow: hidden;
}
.player-wrap:fullscreen,
.player-wrap:-webkit-full-screen {
  width: 100vw; height: 100vh; max-width: none; max-height: none;
  border: 0; border-radius: 0; background: #000;
}
.video-box { position: relative; width: 100%; aspect-ratio: 16 / 9; background: #000; }
.player-wrap:fullscreen .video-box,
.player-wrap:-webkit-full-screen .video-box {
  width: 100%; height: 100%; aspect-ratio: auto;
}
.video-box iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }

/* Overlay timeline over the bottom of the YouTube player (opt-in via Overlay button) */
.ov-overlay {
  --ov-scale: 1;
  --ov-bottom: 62px;
  --ov-alpha: 0.88;
  position: absolute; left: .7rem; right: .7rem; bottom: var(--ov-bottom); z-index: 20;
  overflow: visible;
  background: rgba(7, 8, 10, var(--ov-alpha)); border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: 7px; pointer-events: auto; backdrop-filter: blur(5px);
  user-select: none;
}
.ov-ruler-row, .ov-row { display: grid; grid-template-columns: 9.8rem minmax(0, 1fr); }
.ov-ruler-row {
  height: calc(1.35rem * var(--ov-scale)); background: rgba(14, 16, 20, 0.96);
  border-bottom: 1px solid rgba(255, 255, 255, 0.16); border-radius: 6px 6px 0 0;
}
.ov-gutter {
  display: flex; align-items: center; justify-content: space-between; gap: .25rem;
  padding: 0 .32rem; border-right: 1px solid rgba(255, 255, 255, 0.14);
  cursor: ns-resize; color: #94a3b8; font-size: calc(.72rem * var(--ov-scale));
  line-height: 1; touch-action: none;
}
.ov-gutter:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
.ov-grip { display: inline-flex; align-items: center; gap: .22rem; font-weight: 700; white-space: nowrap; }
.ov-fs-btn {
  padding: 0 .28rem; height: calc(1.08rem * var(--ov-scale)); min-height: 14px;
  font-size: calc(.68rem * var(--ov-scale)); line-height: 1;
  border-radius: 3px; border: 1px solid rgba(255, 255, 255, 0.22);
  background: rgba(20, 24, 32, 0.9); color: #e2e8f0; cursor: pointer;
}
.ov-fs-btn:hover { background: #263143; border-color: #7cc4ff; color: #fff; }
.ov-ruler { position: relative; overflow: hidden; cursor: pointer; }
.ov-overlay .ruler-tick {
  font-size: calc(.72rem * var(--ov-scale)); line-height: calc(1.35rem * var(--ov-scale));
}
.ov-overlay .ruler-ov-pill,
.ov-overlay .ruler-note-pin {
  font-size: calc(.65rem * var(--ov-scale)); line-height: calc(1.35rem * var(--ov-scale) - 4px);
}
.ov-row { height: calc(2.55rem * var(--ov-scale)); border-bottom: 1px solid rgba(255, 255, 255, 0.1); }
.ov-row.row-ja { height: calc(2.5rem * var(--ov-scale)); }
.ov-row.row-ja-furi { height: calc(3.15rem * var(--ov-scale)); }
.ov-row.row-literal { height: calc(1.95rem * var(--ov-scale)); }
.ov-row.row-en { height: calc(2.0rem * var(--ov-scale)); }
.ov-row.row-grp-end { border-bottom: 2px solid rgba(255, 255, 255, 0.28); }
.ov-row:last-child { border-bottom: 0; }
.ov-label {
  display: flex; align-items: center; gap: .32rem; padding: 0 .4rem 0 .26rem;
  border-right: 1px solid rgba(255, 255, 255, 0.14); font-size: calc(.8rem * var(--ov-scale)); font-weight: 800;
  color: var(--spk-color, #fff); background: rgba(10, 12, 16, 0.88);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; cursor: grab; touch-action: none;
}
.ov-label:hover { background: rgba(24, 30, 40, 0.94); }
.ov-label.sub-lbl {
  font-size: calc(.73rem * var(--ov-scale)); font-weight: 700; box-shadow: inset 3px 0 var(--spk-color, #fff);
  background: rgba(8, 10, 14, 0.86);
}
.ov-overlay .spk-avatar {
  width: calc(20px * var(--ov-scale)); height: calc(20px * var(--ov-scale));
  min-width: 12px; min-height: 12px; border-width: 1.5px;
}
.ov-overlay .lbl-kind { font-size: calc(.64rem * var(--ov-scale)); }
.ov-track { position: relative; overflow: hidden; cursor: pointer; }
.ov-overlay .lane-block {
  top: 2px; bottom: 2px;
  line-height: calc(2.55rem * var(--ov-scale) - 6px);
  font-size: calc(.84rem * var(--ov-scale));
}
.ov-overlay .lane-block.ja-plain-blk {
  line-height: calc(2.5rem * var(--ov-scale) - 6px);
  font-size: calc(.84rem * var(--ov-scale));
}
.ov-overlay .lane-block.ja-furi-blk { line-height: 1.18; font-size: calc(.86rem * var(--ov-scale)); }
.ov-overlay .lane-block.ja-furi-blk .lane-word { top: auto; bottom: 2px; line-height: 1.18; }
.ov-overlay .lane-block.ja-plain-blk .lane-word { top: 50%; bottom: auto; transform: translateY(-50%); }
.ov-overlay .lane-block.lb-ww {
  line-height: calc(1.95rem * var(--ov-scale) - 6px);
  font-size: calc(.74rem * var(--ov-scale));
}
.ov-overlay .lane-block.lb-en {
  line-height: calc(2.0rem * var(--ov-scale) - 6px);
  font-size: calc(.78rem * var(--ov-scale));
}
.lbl-grip {
  color: rgba(255, 255, 255, 0.34); font-size: .78em; line-height: 1; flex: 0 0 auto;
  letter-spacing: -1px;
}
.ov-label:hover .lbl-grip, .lane-label:hover .lbl-grip { color: rgba(255, 255, 255, 0.85); }
.is-dragging { opacity: .42; }
.is-dragging .ov-label, .is-dragging .lane-label { cursor: grabbing; }
.drag-over-top { box-shadow: inset 0 2.5px 0 var(--gold); }
.drag-over-bot { box-shadow: inset 0 -2.5px 0 var(--gold); }

/* Primary speech-overlap swimlane visualization */
.viz-stage { margin: .65rem 0 .8rem; }
.speaker-lanes {
  border: 1px solid var(--border); border-radius: 7px;
  background: #08090c; overflow: hidden;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.4);
}
.lanes-stage { position: relative; user-select: none; }
.lanes-ruler-row, .lane-row { display: grid; grid-template-columns: 10.2rem minmax(0, 1fr); }
.lanes-ruler-row {
  border-bottom: 1px solid #222730; background: #0d1015; height: 1.55rem;
}
.lanes-gutter { border-right: 1px solid #222730; }
.lanes-ruler { position: relative; overflow: hidden; cursor: pointer; }
.ruler-tick {
  position: absolute; top: 0; bottom: 0; border-left: 1px solid #2a303b;
  padding-left: .28rem; font: 700 .72rem/1.55rem var(--mono); color: #fff;
  font-variant-numeric: tabular-nums; pointer-events: none; white-space: nowrap;
}
.lane-gridline {
  position: absolute; top: 0; bottom: 0; border-left: 1px solid rgba(255, 255, 255, 0.05);
  pointer-events: none; z-index: 0;
}
.ruler-ov-pill {
  position: absolute; top: 2px; bottom: 2px; background: #261d04;
  border: 1px solid var(--gold); border-radius: 3px; padding: 0 .22rem;
  font: 800 .68rem/1.15rem var(--mono); color: var(--gold); white-space: nowrap;
  overflow: hidden; text-overflow: clip; pointer-events: none; z-index: 3;
}
.ruler-note-pin {
  position: absolute; top: 2px; bottom: 2px; min-width: 6px; padding: 0 .28rem;
  border-radius: 3px; background: #141a24; border: 1px solid var(--cat-color, var(--accent));
  font: 700 .66rem/1.15rem var(--mono); color: #fff; white-space: nowrap;
  z-index: 4; cursor: pointer; transform: translateX(-2px);
}
.ruler-note-pin:hover { background: #1f2a3a; }
.lane-ann-span {
  position: absolute; top: 0; height: 3px; background: var(--cat-color, var(--accent));
  opacity: .8; pointer-events: none; z-index: 4;
}
.ann-live {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: .35rem .6rem;
  padding: .42rem .65rem; background: #11151d; border-top: 1px solid #252c38;
  border-left: 3px solid var(--cat-color, var(--accent)); font-size: .88rem;
}
.ann-live-cat {
  display: inline-block; padding: .04rem .38rem; border-radius: 4px;
  border: 1px solid var(--cat-color, var(--accent)); font-size: .76rem; font-weight: 800;
  color: var(--cat-color, var(--accent)); white-space: nowrap;
}
.ann-live-title { font-weight: 800; color: #fff; }
.ann-live-note { color: #fff; flex: 1 1 18rem; }
.ann-live button { font-size: .76rem; padding: .12rem .4rem; margin-left: auto; }
.lane-row { border-bottom: 1px solid #1b1f27; height: 3.05rem; }
.lane-row.row-ja { height: 3.05rem; }
.lane-row.row-ja-furi { height: 3.6rem; }
.lane-row.row-literal { height: 2.1rem; }
.lane-row.row-en { height: 2.2rem; }
.lane-row.row-grp-end { border-bottom: 2px solid #2e3748; }
.lane-row:last-child { border-bottom: 0; }
.lane-label.sub-lbl {
  font-size: .76rem; font-weight: 700; color: var(--spk-color, #fff); background: #090b0f;
  padding-left: .55rem; box-shadow: inset 3px 0 var(--spk-color, #fff); cursor: pointer;
}
.lane-label.sub-lbl::before { display: none; }
.lane-label.has-avatar::before { display: none; }
.lbl-txt { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.lbl-kind {
  margin-left: auto; font: 700 .66rem/1.2 var(--mono); padding: .04rem .26rem;
  border-radius: 3px; border: 1px solid var(--spk-color, #7cc4ff); color: var(--spk-color, #fff);
  opacity: .9; flex: 0 0 auto;
}
.spk-avatar {
  width: 26px; height: 26px; border-radius: 999px; object-fit: cover; flex: 0 0 auto;
  border: 2px solid var(--spk-color, #7cc4ff); background: #141820;
}
.spk-avatar.av-sm { width: 20px; height: 20px; border-width: 1.5px; }
.spk-avatar.av-xs { width: 18px; height: 18px; border-width: 1.5px; }
.lane-block.ja-furi-blk {
  top: 4px; bottom: 4px; line-height: 1.25; font-size: .92rem;
}
.lane-block.ja-furi-blk .lane-word { top: auto; bottom: 4px; line-height: 1.25; }
.lane-block.ja-furi-blk rt { display: ruby-text !important; }
.lane-block.ja-plain-blk {
  top: 4px; bottom: 4px; line-height: calc(3.05rem - 10px); font-size: .92rem;
}
.lane-block.ja-plain-blk rt { display: none !important; }
.lane-block.lb-ww {
  top: 3px; bottom: 3px; border: 1px dashed var(--spk-color, #7cc4ff);
  background: var(--spk-bg, rgba(124, 196, 255, 0.16));
  font-size: .78rem; font-weight: 600; line-height: calc(2.1rem - 8px);
}
.lane-block.lb-ww .lane-word { color: #fff; font-weight: 600; }
.lane-block.lb-ww.now { border-style: solid; border-color: #fff; background: var(--spk-bg-hi, rgba(124, 196, 255, 0.36)); }
.lane-block.lb-ww .lane-word.lw-now { color: #fff; background: rgba(255, 255, 255, 0.32); }
.lane-block.lb-en {
  top: 3px; bottom: 3px; border: 1px solid var(--spk-color, #7cc4ff);
  border-left: 3px solid var(--spk-color, #7cc4ff);
  background: var(--spk-bg, rgba(124, 196, 255, 0.14)); color: #fff;
  font-size: .82rem; font-weight: 500; line-height: calc(2.2rem - 8px); padding: 0 .45rem;
}
.lane-block.lb-en.now { border-color: #fff; background: var(--spk-bg-hi, rgba(124, 196, 255, 0.38)); }
.lanes-empty {
  padding: .65rem .9rem; font-size: .84rem; color: #cbd5e1; text-align: center;
  background: rgba(12, 15, 21, 0.85); cursor: context-menu;
}
.stream-ctx-menu {
  position: fixed; z-index: 250; min-width: 16rem; max-width: 23rem;
  max-height: calc(100vh - 16px); overflow-y: auto;
  background: #10141c; border: 1px solid #49566b; border-radius: 8px;
  box-shadow: 0 12px 34px rgba(0, 0, 0, 0.68); padding: .35rem 0;
  color: #fff; font-size: .84rem; user-select: none;
}
.ctx-head {
  display: flex; align-items: center; gap: .4rem; padding: .3rem .75rem .35rem;
  font-size: .78rem; font-weight: 800; color: #a9d6ff; border-bottom: 1px solid #232a36;
}
.ctx-sec {
  padding: .3rem .75rem .15rem; font-size: .7rem; font-weight: 800;
  text-transform: uppercase; letter-spacing: .04em; color: #94a3b8;
}
.ctx-sep { height: 1px; background: #232a36; margin: .25rem 0; }
.ctx-item {
  display: flex; align-items: center; gap: .45rem; width: 100%; border: 0;
  border-radius: 0; background: transparent; color: #fff; text-align: left;
  padding: .32rem .75rem; font-size: .83rem; cursor: pointer;
}
.ctx-item:hover { background: #1c2433; }
.ctx-item.ctx-danger { color: #fca5a5; }
.ctx-item .ctx-act {
  margin-left: auto; font: 700 .72rem/1.2 var(--mono); padding: .04rem .3rem;
  border-radius: 3px; border: 1px solid #3c4657; color: #cbd5e1;
}
.ctx-item.is-on .ctx-act { border-color: #68c77b; color: #7ee787; background: #112416; }
.ctx-item.is-off .ctx-act { border-color: #7cc4ff; color: #7cc4ff; background: #0f1c2e; }
.ctx-inline-bar {
  display: flex; flex-wrap: wrap; align-items: center; gap: .26rem;
  padding: .2rem .75rem .28rem;
}
.ctx-inline-lbl {
  font-size: .74rem; font-weight: 700; color: #cbd5e1; margin-right: .15rem;
  min-width: 2.8rem;
}
.ctx-meta {
  margin-left: auto; font: 700 .72rem/1.2 var(--mono); color: #94a3b8;
}
.ctx-chip-btn {
  padding: .16rem .42rem; font-size: .76rem; font-weight: 700;
  border-radius: 4px; border: 1px solid #3c4657; background: #161c27; color: #fff;
  cursor: pointer;
}
.ctx-chip-btn:hover:not(:disabled) { background: #222c3e; border-color: #7cc4ff; }
.ctx-chip-btn:disabled { opacity: .38; cursor: not-allowed; }
.ctx-chip-btn.is-on { border-color: #68c77b; background: #132818; color: #7ee787; }
.ctx-chip-btn.is-off { border-color: #7cc4ff; background: #102036; color: #a9d6ff; }
.lane-pulse {
  position: absolute; top: 8px; bottom: 8px; min-width: 6px; border-radius: 4px;
  border: 1px dashed var(--spk-color, #7cc4ff); background: var(--spk-bg, rgba(124, 196, 255, 0.14));
  opacity: .72; pointer-events: auto; z-index: 2; cursor: pointer;
}
.lane-word.has-vocab { cursor: pointer; }
.word-inspector {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: .35rem .65rem;
  padding: .46rem .7rem; background: #121722; border-top: 1px solid #2a3444;
  border-left: 3px solid var(--accent); font-size: .88rem;
}
.wi-ja { font-size: 1.25rem; font-weight: 700; color: #fff; line-height: 1.6; }
.wi-read { font-family: var(--mono); font-size: .82rem; color: #a9d6ff; }
.wi-gloss { font-weight: 800; color: #fff; }
.wi-role { font-size: .8rem; color: #cbd5e1; }
.wi-note { font-size: .82rem; color: #fde68a; flex: 1 1 16rem; }
.wi-btns { display: inline-flex; gap: .3rem; margin-left: auto; }
.wi-btns button { font-size: .76rem; padding: .12rem .45rem; }
.lane-label {
  display: flex; align-items: center; gap: .38rem; padding: 0 .5rem 0 .32rem;
  border-right: 1px solid #222730; font-size: .88rem; font-weight: 800;
  color: var(--spk-color, #fff); background: #0c0e12;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; cursor: grab; touch-action: none;
}
.lane-label::before {
  content: ""; width: .5rem; height: .5rem; border-radius: 999px; flex: 0 0 auto;
  background: var(--spk-color, #fff);
}
.lane-label:hover { background: #151922; }
.lane-label.filtered { background: #182130; box-shadow: inset 3px 0 var(--spk-color, #fff); }
.lane-track { position: relative; overflow: hidden; cursor: pointer; background: #08090c; }
.lane-ov-band {
  position: absolute; top: 0; bottom: 0;
  background: linear-gradient(180deg, rgba(255, 210, 64, 0.14), rgba(255, 210, 64, 0.08));
  border-top: 1px solid rgba(255, 210, 64, 0.55);
  border-bottom: 1px solid rgba(255, 210, 64, 0.55);
  pointer-events: none; z-index: 1;
}
.lane-block {
  position: absolute; top: 5px; bottom: 5px; min-width: 4px; border-radius: 5px;
  border: 1.5px solid var(--spk-color, #7cc4ff);
  background: var(--spk-bg, rgba(124, 196, 255, 0.2));
  color: #fff; padding: 0 .4rem; font-size: .9rem; font-weight: 600;
  line-height: calc(3.05rem - 13px); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; z-index: 2; cursor: pointer;
}
.lane-block.has-words { padding: 0; text-overflow: clip; }
.lane-words { position: relative; width: 100%; height: 100%; overflow: hidden; }
.lane-block.is-clipped .lane-words {
  -webkit-mask-image: linear-gradient(to right, #000 calc(100% - 18px), transparent 100%);
  mask-image: linear-gradient(to right, #000 calc(100% - 18px), transparent 100%);
}
.lane-word { position: absolute; top: 0; bottom: 0; white-space: nowrap; color: #fff; border-radius: 3px; }
.lane-word.lw-ov {
  text-decoration: underline; text-decoration-color: var(--ov-color, var(--gold));
  text-decoration-thickness: 2px; text-underline-offset: 3px;
}
.lane-word.lw-now {
  background: rgba(255, 255, 255, 0.3);
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.28);
  color: #fff; font-weight: 800;
}
.lane-block.has-ov { box-shadow: inset 0 -2px 0 rgba(255, 210, 64, 0.85); }
.lane-block.now {
  border-color: #fff; background: var(--spk-bg-hi, rgba(124, 196, 255, 0.4));
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.85); z-index: 3;
}
.lane-block.ov-linked { border-color: var(--gold); background: rgba(255, 210, 64, 0.3); z-index: 4; }
.lane-playhead {
  position: absolute; top: 0; bottom: 0; width: 2px; background: var(--red);
  box-shadow: 0 0 8px rgba(255, 71, 71, 0.7); pointer-events: none; z-index: 6;
}

.lanes-bar {
  display: flex; flex-wrap: wrap; align-items: center; gap: .35rem .65rem;
  padding: .36rem .6rem; background: #0d1015; border-top: 1px solid #20252e;
}
.lanes-status {
  flex: 1 1 10rem; min-width: 0; font-size: .82rem;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.lanes-status .ov-badge { color: var(--gold); font-weight: 800; margin-right: .35rem; }
.lanes-tools { display: flex; flex-wrap: wrap; align-items: center; gap: .32rem; margin-left: auto; }
.lanes-tools button, .lanes-tools select { padding: .2rem .46rem; font-size: .8rem; }
.lanes-tools label { display: inline-flex; align-items: center; gap: .28rem; font-size: .8rem; font-weight: 700; }

.lanes-minimap-wrap {
  position: relative; height: 22px; background: #050608;
  border-top: 1px solid #20252e; cursor: pointer;
}
.lanes-minimap { display: block; width: 100%; height: 22px; }
.minimap-viewport {
  position: absolute; top: 0; bottom: 0; border: 1px solid #fff;
  background: rgba(255, 255, 255, 0.14); pointer-events: none;
}
.minimap-playhead {
  position: absolute; top: 0; bottom: 0; width: 2px; background: var(--red);
  pointer-events: none;
}

.transcript {
  margin-top: .7rem; padding: .6rem .75rem; border: 1px solid var(--border);
  border-radius: 7px; background: var(--panel);
}
.transcript-controls { display: flex; flex-wrap: wrap; align-items: center; gap: .38rem; }
.transcript-controls label { display: inline-flex; align-items: center; gap: .28rem; font-size: .8rem; font-weight: 700; }
.transcript-controls button, .transcript-controls select, .transcript-controls input {
  font-size: .82rem; padding: .22rem .46rem;
}
.transcript-search { flex: 1 1 10rem; min-width: 7.5rem; }
.caption-box {
  margin-top: .45rem; border: 1px solid #222730; border-radius: 6px;
  background: #08090c; max-height: 28rem; overflow-y: auto;
}
.segment {
  display: grid; grid-template-columns: 4.5rem 7rem minmax(0, 1fr); gap: .48rem;
  align-items: baseline; padding: .4rem .6rem; border-bottom: 1px solid #191d24; cursor: pointer;
}
.segment:last-child { border-bottom: 0; }
.segment:hover { background: #141820; }
.segment.ov-child {
  background: #0d1017; box-shadow: inset 3px 0 var(--spk-color, #a9d6ff);
  padding-left: .8rem;
}
.segment.ov-linked { background: #242110 !important; box-shadow: inset 4px 0 var(--gold); }
.segment.now { background: #18271c; box-shadow: inset 4px 0 #68c77b; }
.segment.match { background: #28230f; }
.segment-time { font: 700 .84rem/1.4 var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
.ov-mark {
  display: inline-block; margin-left: .22rem; padding: 0 .2rem; border-radius: 3px;
  font: 800 .72rem/1.25 var(--mono); color: var(--gold); border: 1px solid #8a6e14; background: #1d1604;
}
.segment-speaker {
  display: inline-flex; align-items: center; gap: .32rem;
  color: var(--spk-color, #a9d6ff); font-size: .84rem; font-weight: 800;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.segment-text { line-height: 1.46; }
.segment-ja { font-size: 1.02rem; line-height: 2.05; }
html.no-furigana .segment-ja { line-height: 1.5; }
.segment-ww {
  margin-top: .16rem; font-size: .78rem; line-height: 1.45; color: #bcd0ea;
  display: flex; flex-wrap: wrap; gap: .15rem .45rem;
}
.segment-ww .ww-pair { white-space: nowrap; cursor: pointer; }
.segment-ww .ww-pair:hover { color: #fff; text-decoration: underline; }
.segment-ww .ww-ja { color: #90b4e8; margin-right: .15rem; }
.segment-en { margin-top: .2rem; font-size: .88rem; line-height: 1.42; color: #e2e8f0; }

.vocab-panel {
  margin-top: .7rem; padding: .6rem .75rem; border: 1px solid var(--border);
  border-radius: 7px; background: var(--panel);
}
.vocab-bar { display: flex; flex-wrap: wrap; align-items: center; gap: .38rem .5rem; }
.vocab-bar h2 { margin: 0; font-size: .95rem; }
.vocab-count { font: 700 .8rem/1.3 var(--mono); color: #9ec5fe; margin-right: .25rem; }
.vocab-bar select, .vocab-bar input, .vocab-bar button { font-size: .82rem; padding: .2rem .46rem; }
.vocab-bar input[type="search"] { flex: 1 1 11rem; min-width: 8rem; }
.vocab-list { margin-top: .5rem; display: grid; gap: .32rem; max-height: 30rem; overflow-y: auto; }
.vocab-row {
  display: grid; grid-template-columns: minmax(9rem, 13rem) 4.6rem minmax(0, 1fr) auto auto;
  gap: .45rem .65rem; align-items: baseline; padding: .38rem .55rem;
  border: 1px solid #222730; border-radius: 5px; background: #10131a; font-size: .86rem;
}
.vocab-row.is-known { opacity: .55; }
.v-word { display: flex; flex-wrap: wrap; align-items: baseline; gap: .15rem .4rem; }
.v-ja { font-size: 1.12rem; font-weight: 700; color: #fff; line-height: 1.7; }
html.no-furigana .v-ja { line-height: 1.35; }
.v-read { font-family: var(--mono); font-size: .76rem; color: #9ec5fe; }
.v-diff {
  display: inline-block; text-align: center; padding: .04rem .34rem; border-radius: 4px;
  font: 800 .72rem/1.3 var(--mono); text-transform: uppercase; border: 1px solid #475569;
  color: #fff; background: #181e29; white-space: nowrap;
}
.vd-mtg { border-color: #ffd240; color: #ffd240; background: #241c06; }
.vd-slang { border-color: #ff85a2; color: #ff85a2; background: #261018; }
.vd-n1 { border-color: #d2a8ff; color: #d2a8ff; background: #1e132b; }
.vd-n2 { border-color: #7cc4ff; color: #7cc4ff; background: #102033; }
.vd-n3 { border-color: #7ee787; color: #7ee787; background: #102414; }
.vd-basic { border-color: #64748b; color: #cbd5e1; background: #141820; }
.v-meaning { line-height: 1.42; }
.v-gloss { font-weight: 700; color: #fff; }
.v-role { font-size: .78rem; color: #b6c4d8; margin-left: .35rem; }
.v-note { display: block; margin-top: .12rem; font-size: .8rem; color: #fde68a; }
.v-times { display: flex; flex-wrap: wrap; gap: .22rem; justify-content: flex-end; max-width: 14rem; }
.v-time {
  font: 700 .76rem/1.25 var(--mono); padding: .08rem .34rem; border-radius: 4px;
  color: #7cc4ff; border: 1px solid #2b384d; background: #0c111a;
}
.v-time:hover { background: #182336; border-color: #7cc4ff; }
.v-known { font-size: .76rem; padding: .1rem .4rem; white-space: nowrap; }
.v-known[aria-pressed="true"] { border-color: #68c77b; background: #142618; color: #7ee787; }
.word { border-radius: 3px; }
.word:hover { background: #554b19; }
.word.word-ov { border-bottom: 2px solid var(--ov-color, var(--gold)); padding-bottom: 1px; }
.word.word-ov-hi { background: rgba(255, 210, 64, 0.28); }
.word.word-now { background: #2e5937; }
.ov-chip {
  display: inline-block; margin: 0 .22rem; padding: .02rem .38rem; border-radius: 4px;
  border: 1px solid var(--ov-color, var(--gold)); background: #12151b; color: #fff;
  font-size: .8em; line-height: 1.35; cursor: pointer; white-space: nowrap; max-width: 22rem;
  overflow: hidden; text-overflow: ellipsis; vertical-align: -0.12em;
}
.ov-chip strong { color: var(--ov-color, var(--gold)); }

.notes-panel {
  margin-top: .7rem; padding: .55rem .75rem; border: 1px solid var(--border);
  border-radius: 7px; background: var(--panel);
}
.notes-bar { display: flex; flex-wrap: wrap; align-items: center; gap: .38rem; }
.notes-bar h2 { margin: 0; font-size: .92rem; margin-right: .25rem; }
.notes-bar input.note-title-in { flex: 0 1 12rem; min-width: 8rem; font-size: .84rem; }
.notes-bar input.note-text-in { flex: 1 1 14rem; min-width: 10rem; font-size: .84rem; }
.notes-bar select, .notes-bar button { font-size: .82rem; }
.ann-filter-bar {
  display: flex; flex-wrap: wrap; align-items: center; gap: .38rem;
  margin-top: .45rem; padding-top: .45rem; border-top: 1px solid #1e232c;
}
.ann-filter-bar label { display: inline-flex; align-items: center; gap: .28rem; font-size: .8rem; font-weight: 700; }
.ann-filter-bar select, .ann-filter-bar button { font-size: .8rem; padding: .18rem .46rem; }
.ann-filter-bar .ann-actions { display: flex; flex-wrap: wrap; gap: .32rem; margin-left: auto; }
.notes-list { margin-top: .45rem; display: grid; gap: .3rem; }
.notes-list:empty { display: none; }
.note-row {
  display: grid; grid-template-columns: 4.5rem 10.5rem minmax(0, 1fr) auto; gap: .5rem;
  align-items: baseline; padding: .34rem .5rem; border: 1px solid #222730;
  border-left: 3px solid var(--cat-color, var(--accent)); border-radius: 4px;
  background: #101319; font-size: .85rem;
}
.note-row.ann-active { background: #182332; border-color: var(--cat-color, var(--accent)); }
.note-row .nt-time { font: 700 .84rem/1.3 var(--mono); color: #7cc4ff; cursor: pointer; white-space: nowrap; }
.note-row .nt-tag {
  font-weight: 800; font-size: .78rem; color: var(--cat-color, var(--gold));
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.note-row .nt-body { line-height: 1.42; }
.note-row .nt-title { font-weight: 800; color: #fff; margin-right: .35rem; }
.note-row .nt-src {
  display: inline-block; margin-left: .3rem; padding: 0 .28rem; border-radius: 3px;
  border: 1px solid #3c4657; font: 700 .68rem/1.25 var(--mono); color: #fff;
  vertical-align: middle;
}
.note-row .nt-btns { display: inline-flex; gap: .25rem; }
.note-row .nt-edit, .note-row .nt-del { padding: .08rem .38rem; font-size: .76rem; }
.note-edit-form {
  grid-column: 1 / -1; display: grid; gap: .35rem; margin-top: .3rem;
  padding-top: .35rem; border-top: 1px solid #242a35;
}
.note-edit-row { display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; }
.note-edit-form input, .note-edit-form select, .note-edit-form textarea {
  font: inherit; font-size: .84rem; color: #fff; background: #0a0c10;
  border: 1px solid #3f4857; border-radius: 4px; padding: .24rem .45rem;
}
.note-edit-form input.ed-time { width: 5.5rem; font-family: var(--mono); }
.note-edit-form input.ed-title { flex: 1 1 12rem; }
.note-edit-form textarea.ed-note { width: 100%; min-height: 3.2rem; resize: vertical; }

@media (min-width: 1040px) {
  main.split-view {
    display: grid;
    grid-template-columns: minmax(0, min(62vw, calc(66vh * 16 / 9))) minmax(22rem, 1fr);
    column-gap: .85rem; row-gap: .65rem; align-items: start;
  }
  main.split-view > .player-wrap { grid-column: 1; grid-row: 1; }
  main.split-view > .transcript {
    grid-column: 2; grid-row: 1; margin: 0;
    display: flex; flex-direction: column; overflow: hidden;
  }
  main.split-view > .transcript .caption-box { flex: 1 1 0; max-height: none; min-height: 0; }
  main.split-view > .viz-stage,
  main.split-view > .vocab-panel,
  main.split-view > .notes-panel { grid-column: 1 / -1; margin-top: 0; }
}
@media (max-width: 760px) {
  .lanes-ruler-row, .lane-row { grid-template-columns: 6.4rem minmax(0, 1fr); }
  .ov-ruler-row, .ov-row { grid-template-columns: 5.8rem minmax(0, 1fr); }
  .ov-grip-txt { display: none; }
  .segment { grid-template-columns: 4.2rem 5.8rem minmax(0, 1fr); gap: .38rem; }
  .note-row { grid-template-columns: 4rem 6rem minmax(0, 1fr) auto; }
  .vocab-row { grid-template-columns: minmax(0, 1fr) auto; }
  .v-times { grid-column: 1 / -1; justify-content: flex-start; max-width: none; }
}
"""

APP_JS = r"""(() => {
  'use strict';

  const SPEAKER_PALETTE = [
    {fg: '#7cc4ff', bg: 'rgba(124,196,255,0.20)', bgHi: 'rgba(124,196,255,0.40)'},
    {fg: '#ffb84d', bg: 'rgba(255,184,77,0.20)',  bgHi: 'rgba(255,184,77,0.40)'},
    {fg: '#7ee787', bg: 'rgba(126,231,135,0.20)', bgHi: 'rgba(126,231,135,0.40)'},
    {fg: '#d2a8ff', bg: 'rgba(210,168,255,0.20)', bgHi: 'rgba(210,168,255,0.40)'},
    {fg: '#ff85a2', bg: 'rgba(255,133,162,0.20)', bgHi: 'rgba(255,133,162,0.40)'},
    {fg: '#66e3d8', bg: 'rgba(102,227,216,0.20)', bgHi: 'rgba(102,227,216,0.40)'},
  ];
  const UNASSIGNED = {fg: '#a9d6ff', bg: 'rgba(169,214,255,0.16)', bgHi: 'rgba(169,214,255,0.36)'};
  const YT_ORIGIN = 'https://www.youtube-nocookie.com';
  const OV_WINDOW_SEC = 10;

  let doc = null;
  let model = null;
  let iframe = null;
  let ytReady = false;
  let ytPlaying = false;
  let ytTime = 0;
  let ytStamp = 0;
  let ytRate = 1;
  let pendingHashSeek = parseHashTime();
  let pendingUserSeek = null;

  let rowByIndex = new Map();
  let activeKey = '';
  let activeWordSpans = [];
  let hoveredIndices = null;
  let hoveredKey = '';

  let lanesWindow = Number(localStorage.getItem('ovpub.window')) || 20;
  let lanesFollow = true;
  let showOverlay = localStorage.getItem('ovpub.overlay') === 'true';
  let showMinimap = localStorage.getItem('ovpub.minimap') === 'true';
  let showBadges = localStorage.getItem('ovpub.badges') === 'true';
  let showAnnotations = false;
  let splitView = localStorage.getItem('ovpub.split') !== 'false';
  let autoScroll = localStorage.getItem('ovpub.autoScroll') !== 'false';

  let hasStudy = false;
  let showFurigana = localStorage.getItem('ovpub.furigana') !== 'false';
  let showGloss = localStorage.getItem('ovpub.gloss') !== 'false';
  let showEnglish = localStorage.getItem('ovpub.english') !== 'false';
  let vocabById = new Map();
  let knownVocab = new Set();
  let vocabDiff = localStorage.getItem('ovpub.vocabDiff') || 'n3+';
  let vocabSort = 'first';
  let hideKnown = localStorage.getItem('ovpub.hideKnown') !== 'false';
  let inspectedVocabId = null;

  const DIFF_LABELS = {
    mtg: 'MTG', slang: 'Slang', n1: 'N1', n2: 'N2', n3: 'N3', basic: 'Basic',
  };
  const DIFF_ORDER = {mtg: 5, slang: 4, n1: 3, n2: 2, n3: 1, basic: 0};

  const STUDY_STREAMS = [
    {id: 'ja', label: 'Native Japanese', short: 'JP'},
    {id: 'ja-furi', label: 'Japanese + Furigana', short: 'JP+furi'},
    {id: 'literal', label: 'English literal (word-by-word)', short: 'literal'},
    {id: 'en', label: 'English translation', short: 'English'},
  ];
  const STREAM_MAP = new Map(STUDY_STREAMS.map(s => [s.id, s]));
  const STREAM_ORDER = new Map(STUDY_STREAMS.map((s, i) => [s.id, i]));
  let mainRows = [];
  let overlayRows = [];
  let ctxMenuState = null;

  function readNumStorage(key, fallback) {
    const raw = localStorage.getItem(key);
    if (raw == null || raw === '') return fallback;
    const n = Number(raw);
    return Number.isFinite(n) ? n : fallback;
  }
  const DEFAULT_OV_BOTTOM_WIN = 62;
  const DEFAULT_OV_BOTTOM_FS = 84;
  let ovBottomWin = readNumStorage('ovpub.ovBottomWin', DEFAULT_OV_BOTTOM_WIN);
  let ovBottomFs = readNumStorage('ovpub.ovBottomFs', DEFAULT_OV_BOTTOM_FS);
  let ovFontScale = readNumStorage('ovpub.ovFont', 1);
  let ovAlpha = readNumStorage('ovpub.ovAlpha', 0.88);
  let ovCurrentScale = 1;
  let autoOverlayForFs = false;

  function isPlayerFullscreen() {
    const pw = document.getElementById('player-wrap');
    return Boolean(pw && (document.fullscreenElement === pw || document.webkitFullscreenElement === pw));
  }
  function getOvBottom() {
    return isPlayerFullscreen() ? ovBottomFs : ovBottomWin;
  }
  function ovRowRem(kind) {
    if (kind === 'ja-furi') return 3.15;
    if (kind === 'ja') return 2.5;
    if (kind === 'literal') return 1.95;
    if (kind === 'en') return 2.0;
    return 2.55;
  }
  function fitOverlayLayout() {
    const ovEl = document.getElementById('ov-overlay');
    const pw = document.getElementById('player-wrap');
    if (!ovEl || !pw) return getOvBottom();
    const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 15;
    const pwH = pw.clientHeight || pw.getBoundingClientRect().height
      || (isPlayerFullscreen() ? window.innerHeight : 500);
    const rows = overlayRows || [];
    let natRem = 1.35;
    if (rows.length) {
      for (const r of rows) natRem += ovRowRem(r.kind);
    } else {
      natRem += 2.6;
    }
    const natPx = natRem * rootPx + Math.max(2, rows.length + 2);
    const baseBottom = Math.max(12, getOvBottom());
    const minFloorBottom = isPlayerFullscreen() ? 76 : 56;
    const desired = Math.max(0.7, Math.min(1.4, ovFontScale || 1));
    let effBaseBottom = baseBottom;
    // If the user previously dragged the overlay high up and then added many rows,
    // slide down toward YouTube's control-bar clearance before shrinking text.
    if (pwH - effBaseBottom - 14 < natPx * desired && effBaseBottom > minFloorBottom) {
      effBaseBottom = Math.max(minFloorBottom, Math.round(pwH - natPx * desired - 14));
    }
    const availH = Math.max(96, pwH - effBaseBottom - 14);
    const scale = Math.max(0.38, Math.min(desired, availH / Math.max(1, natPx)));
    const maxBottom = Math.max(12, pwH - natPx * scale - 10);
    const effBottom = Math.round(Math.max(12, Math.min(effBaseBottom, maxBottom)));
    const prevScale = ovCurrentScale;
    ovCurrentScale = scale;
    ovEl.style.setProperty('--ov-scale', scale.toFixed(3));
    ovEl.style.setProperty('--ov-bottom', effBottom + 'px');
    ovEl.style.setProperty('--ov-alpha', String(ovAlpha));
    if (Math.abs(scale - prevScale) > 0.015) ovRenderedStart = -1;
    return effBottom;
  }
  function setOvBottom(px, persist) {
    const v = Math.max(12, Math.round(Number(px) || 0));
    if (isPlayerFullscreen()) ovBottomFs = v;
    else ovBottomWin = v;
    const eff = fitOverlayLayout();
    if (persist) {
      if (isPlayerFullscreen()) {
        ovBottomFs = eff;
        localStorage.setItem('ovpub.ovBottomFs', String(ovBottomFs));
      } else {
        ovBottomWin = eff;
        localStorage.setItem('ovpub.ovBottomWin', String(ovBottomWin));
      }
    }
    if (showOverlay && ovRenderedStart < 0) updateOverlayTimeline(currentTime(), true);
  }
  function setOvFontScale(val) {
    ovFontScale = Math.max(0.75, Math.min(1.35, Number(val) || 1));
    localStorage.setItem('ovpub.ovFont', String(ovFontScale));
    fitOverlayLayout();
    if (showOverlay) {
      ovRenderedStart = -1;
      updateOverlayTimeline(currentTime(), true);
    }
  }
  function setOvAlpha(val) {
    ovAlpha = Math.max(0.55, Math.min(0.98, Number(val) || 0.88));
    localStorage.setItem('ovpub.ovAlpha', String(ovAlpha));
    fitOverlayLayout();
  }
  function syncFullscreenButtons() {
    const fs = isPlayerFullscreen();
    const btn = document.getElementById('toggle-fullscreen');
    if (btn) {
      btn.setAttribute('aria-pressed', String(fs));
      btn.textContent = fs ? 'Exit Full' : 'Fullscreen';
    }
    const ovBtn = document.getElementById('ov-fs-btn');
    if (ovBtn) {
      ovBtn.textContent = fs ? 'Exit' : '⛶';
      ovBtn.setAttribute('aria-pressed', String(fs));
      ovBtn.title = fs ? 'Exit fullscreen (F)' : 'Fullscreen video with overlay (F)';
    }
  }
  function togglePlayerFullscreen() {
    const pw = document.getElementById('player-wrap');
    if (!pw) return;
    if (isPlayerFullscreen()) {
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) Promise.resolve(exit.call(document)).catch(() => {});
      return;
    }
    if (!showOverlay) {
      autoOverlayForFs = true;
      showOverlay = true;
      applyOverlayState();
    } else {
      autoOverlayForFs = false;
    }
    const req = pw.requestFullscreen || pw.webkitRequestFullscreen;
    if (req) {
      Promise.resolve(req.call(pw)).catch(() => {
        if (autoOverlayForFs) {
          autoOverlayForFs = false;
          showOverlay = false;
          applyOverlayState();
        }
      });
    }
  }
  function onFullscreenChange() {
    syncFullscreenButtons();
    closeStreamMenu();
    const menu = document.getElementById('stream-ctx-menu');
    const host = document.fullscreenElement || document.webkitFullscreenElement || document.body;
    if (menu && menu.parentElement !== host) host.appendChild(menu);
    const fs = isPlayerFullscreen();
    if (!fs && autoOverlayForFs) {
      autoOverlayForFs = false;
      showOverlay = false;
      applyOverlayState();
    } else if (showOverlay) {
      fitOverlayLayout();
      ovRenderedStart = -1;
      requestAnimationFrame(() => {
        fitOverlayLayout();
        updateOverlayTimeline(currentTime(), true);
      });
    }
    if (!fs) applySplitLayout();
  }

  function avatarHtml(spk, sizeCls) {
    const url = doc && doc.avatars && doc.avatars[spk];
    if (!url) return '';
    return '<img class="spk-avatar' + (sizeCls ? ' ' + sizeCls : '') + '" src="' + html(url)
      + '" alt="' + html(spk) + '" loading="lazy">';
  }
  function defaultRowsForSurface(surface) {
    if (!model) return [];
    const kinds = !hasStudy
      ? ['text']
      : surface === 'overlay'
        ? ['ja-furi', 'literal', 'en']
        : ['ja-furi', 'literal', 'en'];
    const out = [];
    for (const spk of model.speakers) {
      for (const kind of kinds) out.push({spk, kind});
    }
    return out;
  }
  function rowsStorageKey(surface) {
    return 'ovpub.' + (surface === 'overlay' ? 'ovRows.' : 'mainRows.') + doc.video_id;
  }
  function sanitizeRows(arr) {
    if (!Array.isArray(arr) || !model) return null;
    const validSpk = new Set(model.speakers);
    const validKinds = hasStudy ? new Set(STUDY_STREAMS.map(s => s.id)) : new Set(['text']);
    const seen = new Set();
    const out = [];
    for (const r of arr) {
      if (!r || typeof r !== 'object') continue;
      if (!validSpk.has(r.spk) || !validKinds.has(r.kind)) continue;
      const k = r.spk + '\0' + r.kind;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({spk: r.spk, kind: r.kind});
    }
    return out;
  }
  function loadSurfaceRows(surface) {
    const raw = localStorage.getItem(rowsStorageKey(surface));
    if (raw != null) {
      try {
        const clean = sanitizeRows(JSON.parse(raw));
        if (clean) return clean;
      } catch {}
    }
    return defaultRowsForSurface(surface);
  }
  function getSurfaceRows(surface) {
    return surface === 'overlay' ? overlayRows : mainRows;
  }
  function setSurfaceRows(surface, rows) {
    if (surface === 'overlay') overlayRows = rows;
    else mainRows = rows;
    localStorage.setItem(rowsStorageKey(surface), JSON.stringify(rows));
    if (surface === 'overlay') {
      fitOverlayLayout();
      ovRenderedStart = -1;
      if (showOverlay) updateOverlayTimeline(currentTime(), true);
    } else {
      renderLanesWindow(true);
    }
  }
  function isStreamGrouped(rows) {
    let sameKindAdj = 0;
    let sameSpkAdj = 0;
    for (let i = 1; i < rows.length; i++) {
      if (rows[i].kind === rows[i - 1].kind && rows[i].spk !== rows[i - 1].spk) sameKindAdj += 1;
      if (rows[i].spk === rows[i - 1].spk) sameSpkAdj += 1;
    }
    return sameKindAdj > 0 && sameSpkAdj === 0;
  }
  function sortRowsBySpeaker(rows) {
    const spkOrder = new Map(model.speakers.map((s, i) => [s, i]));
    return [...rows].sort((a, b) => {
      const sa = spkOrder.get(a.spk) ?? 99;
      const sb = spkOrder.get(b.spk) ?? 99;
      if (sa !== sb) return sa - sb;
      return (STREAM_ORDER.get(a.kind) ?? 99) - (STREAM_ORDER.get(b.kind) ?? 99);
    });
  }
  function sortRowsByStream(rows) {
    const spkOrder = new Map(model.speakers.map((s, i) => [s, i]));
    return [...rows].sort((a, b) => {
      const ka = STREAM_ORDER.get(a.kind) ?? 99;
      const kb = STREAM_ORDER.get(b.kind) ?? 99;
      if (ka !== kb) return ka - kb;
      return (spkOrder.get(a.spk) ?? 99) - (spkOrder.get(b.spk) ?? 99);
    });
  }
  function insertRowPreservingOrder(rows, spk, kind) {
    if (rows.some(r => r.spk === spk && r.kind === kind)) return rows;
    const next = [...rows];
    const kOrd = STREAM_ORDER.get(kind) ?? 99;
    if (isStreamGrouped(next)) {
      let lastKindIdx = -1;
      for (let i = 0; i < next.length; i++) {
        if (next[i].kind === kind) lastKindIdx = i;
      }
      if (lastKindIdx >= 0) {
        next.splice(lastKindIdx + 1, 0, {spk, kind});
        return next;
      }
      const afterIdx = next.findIndex(r => (STREAM_ORDER.get(r.kind) ?? 99) > kOrd);
      if (afterIdx >= 0) { next.splice(afterIdx, 0, {spk, kind}); return next; }
      next.push({spk, kind});
      return next;
    }
    let firstSpk = -1;
    let lastSpk = -1;
    for (let i = 0; i < next.length; i++) {
      if (next[i].spk === spk) {
        if (firstSpk < 0) firstSpk = i;
        lastSpk = i;
      }
    }
    if (firstSpk >= 0) {
      let ins = lastSpk + 1;
      for (let i = firstSpk; i <= lastSpk; i++) {
        if (next[i].spk === spk && (STREAM_ORDER.get(next[i].kind) ?? 99) > kOrd) {
          ins = i;
          break;
        }
      }
      next.splice(ins, 0, {spk, kind});
      return next;
    }
    next.push({spk, kind});
    return next;
  }
  function moveRowOnSurface(surface, fromIdx, toIdx) {
    const rows = [...getSurfaceRows(surface)];
    if (fromIdx < 0 || fromIdx >= rows.length) return;
    toIdx = Math.max(0, Math.min(rows.length - 1, toIdx));
    if (fromIdx === toIdx) return;
    const [item] = rows.splice(fromIdx, 1);
    rows.splice(toIdx, 0, item);
    setSurfaceRows(surface, rows);
  }
  function groupRowsBySpeaker(surface) {
    setSurfaceRows(surface, sortRowsBySpeaker(getSurfaceRows(surface)));
  }
  function groupRowsByStream(surface) {
    setSurfaceRows(surface, sortRowsByStream(getSurfaceRows(surface)));
  }
  function addRowToSurface(surface, spk, kind) {
    const rows = getSurfaceRows(surface);
    if (rows.some(r => r.spk === spk && r.kind === kind)) return;
    setSurfaceRows(surface, insertRowPreservingOrder(rows, spk, kind));
  }
  function removeRowFromSurface(surface, spk, kind) {
    const rows = getSurfaceRows(surface).filter(r => !(r.spk === spk && r.kind === kind));
    setSurfaceRows(surface, rows);
  }
  function addKindForActive(surface, kind) {
    const rows = getSurfaceRows(surface);
    const activeSpks = [...new Set(rows.map(r => r.spk))];
    const targetSpks = activeSpks.length ? activeSpks : model.speakers;
    let next = [...rows];
    for (const spk of targetSpks) {
      next = insertRowPreservingOrder(next, spk, kind);
    }
    setSurfaceRows(surface, next);
  }
  function toggleKindForAllActive(surface, kind) {
    const rows = getSurfaceRows(surface);
    const activeSpks = [...new Set(rows.map(r => r.spk))];
    const targetSpks = activeSpks.length ? activeSpks : model.speakers;
    const allHave = targetSpks.every(s => rows.some(r => r.spk === s && r.kind === kind));
    if (allHave) {
      setSurfaceRows(surface, rows.filter(r => r.kind !== kind));
    } else {
      addKindForActive(surface, kind);
    }
  }
  function toggleSpeakerOnSurface(surface, spk) {
    const rows = getSurfaceRows(surface);
    const hasSpk = rows.some(r => r.spk === spk);
    if (hasSpk) {
      setSurfaceRows(surface, rows.filter(r => r.spk !== spk));
    } else {
      const activeKinds = [...new Set(rows.map(r => r.kind))];
      const kinds = activeKinds.length ? activeKinds : (hasStudy ? ['ja-furi'] : ['text']);
      let next = [...rows];
      for (const kind of kinds) next = insertRowPreservingOrder(next, spk, kind);
      setSurfaceRows(surface, next);
    }
  }
  function closeStreamMenu() {
    const menu = document.getElementById('stream-ctx-menu');
    if (menu) { menu.hidden = true; menu.innerHTML = ''; }
    ctxMenuState = null;
  }
  function renderStreamMenu(anchorX, anchorY) {
    const menu = document.getElementById('stream-ctx-menu');
    if (!menu || !ctxMenuState || !model) return;
    const host = document.fullscreenElement || document.webkitFullscreenElement || document.body;
    if (menu.parentElement !== host) host.appendChild(menu);

    const {surface, hitSpk, hitKind} = ctxMenuState;
    const rows = getSurfaceRows(surface);
    const hitIdx = hitSpk && hitKind ? rows.findIndex(r => r.spk === hitSpk && r.kind === hitKind) : -1;
    ctxMenuState.hitIdx = hitIdx;
    const activeSpks = [...new Set(rows.map(r => r.spk))];
    const targetSpks = activeSpks.length ? activeSpks : model.speakers;
    const surfaceTitle = surface === 'overlay' ? 'Overlay streams' : 'Timeline streams';
    const streamGrouped = isStreamGrouped(rows);

    let out = '<div class="ctx-head">'
      + (hitSpk ? avatarHtml(hitSpk, 'av-xs') : '')
      + '<span>' + html(hitIdx >= 0 ? hitSpk + ' · ' + (STREAM_MAP.get(hitKind)?.label || surfaceTitle) : surfaceTitle) + '</span></div>';

    if (hitIdx >= 0) {
      const disUp = hitIdx <= 0 ? ' disabled' : '';
      const disDn = hitIdx >= rows.length - 1 ? ' disabled' : '';
      out += '<div class="ctx-inline-bar">'
        + '<span class="ctx-inline-lbl">Order</span>'
        + '<button type="button" class="ctx-chip-btn" data-act="move-up"' + disUp + ' title="Move row up">↑ Up</button>'
        + '<button type="button" class="ctx-chip-btn" data-act="move-down"' + disDn + ' title="Move row down">↓ Down</button>'
        + '<button type="button" class="ctx-chip-btn" data-act="move-top"' + disUp + ' title="Move row to top">⤒ Top</button>'
        + '<button type="button" class="ctx-chip-btn" data-act="move-bot"' + disDn + ' title="Move row to bottom">⤓ Bot</button>'
        + '</div>';
      out += '<button type="button" class="ctx-item ctx-danger" data-act="rm-one" data-spk="'
        + html(hitSpk) + '" data-kind="' + html(hitKind) + '">'
        + '<span>Remove this row</span><span class="ctx-act">− Remove</span></button>';
      if (hasStudy) {
        const missingForSpk = STUDY_STREAMS.filter(s => !rows.some(r => r.spk === hitSpk && r.kind === s.id));
        if (missingForSpk.length) {
          out += '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Add</span>';
          for (const st of missingForSpk) {
            out += '<button type="button" class="ctx-chip-btn is-off" data-act="add-one" data-spk="'
              + html(hitSpk) + '" data-kind="' + html(st.id) + '" title="' + html('Add ' + st.label + ' for ' + hitSpk)
              + '">+ ' + html(st.short) + '</button>';
          }
          out += '</div>';
        }
      }
      out += '<div class="ctx-sep"></div>';
    }

    if (rows.length > 1 && hasStudy) {
      out += '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Group</span>'
        + '<button type="button" class="ctx-chip-btn' + (!streamGrouped ? ' is-on' : '') + '" data-act="grp-spk">By speaker</button>'
        + '<button type="button" class="ctx-chip-btn' + (streamGrouped ? ' is-on' : '') + '" data-act="grp-stream">By stream</button>'
        + '</div><div class="ctx-sep"></div>';
    }

    if (hasStudy) {
      out += '<div class="ctx-sec">All speakers</div>';
      for (const st of STUDY_STREAMS) {
        const allHave = targetSpks.length > 0 && targetSpks.every(s => rows.some(r => r.spk === s && r.kind === st.id));
        out += '<button type="button" class="ctx-item ' + (allHave ? 'is-on' : 'is-off')
          + '" data-act="toggle-kind" data-kind="' + html(st.id) + '">'
          + '<span>' + html(st.label) + '</span>'
          + '<span class="ctx-act">' + (allHave ? '✓ Remove' : '+ Add') + '</span></button>';
      }
      out += '<div class="ctx-sep"></div>';
    }

    out += '<div class="ctx-sec">Voices / Speakers</div>';
    for (const spk of model.speakers) {
      const c = model.colorOf(spk);
      const present = rows.some(r => r.spk === spk);
      out += '<button type="button" class="ctx-item ' + (present ? 'is-on' : 'is-off')
        + '" data-act="toggle-spk" data-spk="' + html(spk) + '" style="--spk-color:' + c.fg + '">'
        + avatarHtml(spk, 'av-xs')
        + '<span style="color:' + c.fg + ';font-weight:700">' + html(spk) + '</span>'
        + '<span class="ctx-act">' + (present ? '✓ Remove' : '+ Add') + '</span></button>';
    }

    if (surface === 'overlay') {
      const fs = isPlayerFullscreen();
      const curBot = Math.round(getOvBottom());
      out += '<div class="ctx-sep"></div><div class="ctx-sec">Overlay layout</div>'
        + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Height</span>'
        + '<button type="button" class="ctx-chip-btn" data-act="ov-up">↑ Higher</button>'
        + '<button type="button" class="ctx-chip-btn" data-act="ov-down">↓ Lower</button>'
        + '<span class="ctx-meta">' + curBot + 'px</span></div>'
        + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Text</span>'
        + '<button type="button" class="ctx-chip-btn' + (ovFontScale < 0.92 ? ' is-on' : '') + '" data-act="ov-font" data-val="0.84">S</button>'
        + '<button type="button" class="ctx-chip-btn' + (ovFontScale >= 0.92 && ovFontScale <= 1.08 ? ' is-on' : '') + '" data-act="ov-font" data-val="1">M</button>'
        + '<button type="button" class="ctx-chip-btn' + (ovFontScale > 1.08 ? ' is-on' : '') + '" data-act="ov-font" data-val="1.2">L</button>'
        + '<span class="ctx-meta">' + Math.round(ovCurrentScale * 100) + '%</span></div>'
        + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">BG</span>'
        + '<button type="button" class="ctx-chip-btn' + (ovAlpha < 0.8 ? ' is-on' : '') + '" data-act="ov-alpha" data-val="0.72">Light</button>'
        + '<button type="button" class="ctx-chip-btn' + (ovAlpha >= 0.8 && ovAlpha <= 0.92 ? ' is-on' : '') + '" data-act="ov-alpha" data-val="0.88">Med</button>'
        + '<button type="button" class="ctx-chip-btn' + (ovAlpha > 0.92 ? ' is-on' : '') + '" data-act="ov-alpha" data-val="0.96">Dark</button>'
        + '</div>'
        + '<button type="button" class="ctx-item" data-act="toggle-fs"><span>'
        + (fs ? 'Exit Fullscreen (F)' : 'Enter Fullscreen (F)') + '</span></button>';
    }

    out += '<div class="ctx-sep"></div>'
      + '<button type="button" class="ctx-item" data-act="reset"><span>Reset defaults</span></button>';

    menu.innerHTML = out;
    menu.hidden = false;
    const mw = menu.offsetWidth || 260;
    const mh = menu.offsetHeight || 340;
    const x = Math.max(8, Math.min(anchorX, window.innerWidth - mw - 10));
    const y = Math.max(8, Math.min(anchorY, window.innerHeight - mh - 10));
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    ctxMenuState.x = x;
    ctxMenuState.y = y;
  }
  function showStreamMenu(ev, surface) {
    if (!model) return;
    ev.preventDefault();
    ev.stopPropagation();
    const rowEl = ev.target.closest('[data-row-spk]');
    const hitSpk = rowEl ? rowEl.dataset.rowSpk : '';
    const hitKind = rowEl ? rowEl.dataset.rowKind : '';
    ctxMenuState = {surface, hitSpk, hitKind, hitIdx: -1, x: ev.clientX, y: ev.clientY};
    renderStreamMenu(ev.clientX, ev.clientY);
  }

  function rubyHtml(ruby, fallback) {
    if (!ruby || !Array.isArray(ruby) || !ruby.length) return html(fallback || '');
    return ruby.map(pair => {
      const t = pair[0] || '';
      const r = pair[1] || '';
      if (!r) return html(t);
      return '<ruby>' + html(t) + '<rp>(</rp><rt>' + html(r) + '</rt><rp>)</rp></ruby>';
    }).join('');
  }
  function loadKnownVocab() {
    try {
      const arr = JSON.parse(localStorage.getItem('ovpub.knownVocab') || '[]');
      knownVocab = new Set(Array.isArray(arr) ? arr.map(String) : []);
    } catch { knownVocab = new Set(); }
  }
  function saveKnownVocab() {
    localStorage.setItem('ovpub.knownVocab', JSON.stringify([...knownVocab]));
  }
  function vocabKey(v) {
    return (v.base || '') + '|' + (v.kana || '');
  }

  const DEFAULT_CATEGORIES = [
    {id: 'overlap', label: 'Cooperative overlap', color: '#ffd240'},
    {id: 'reaction', label: 'Vocal reaction / response cry', color: '#ff85a2'},
    {id: 'buy-in', label: 'Decision buy-in / ratification', color: '#7ee787'},
    {id: 'counterfactual', label: 'Challenging habit / counterfactual', color: '#7cc4ff'},
    {id: 'humility', label: 'Post-decision humility', color: '#d2a8ff'},
    {id: 'scoping', label: 'Scoped disagreement', color: '#66e3d8'},
    {id: 'risk-bound', label: 'Worst-case / risk bounding', color: '#ffa07a'},
    {id: 'repair', label: 'Self-repair & blame-free absorb', color: '#9be9a8'},
    {id: 'shorthand', label: 'Conceptual pact / shorthand', color: '#a9d6ff'},
    {id: 'zoom-out', label: 'Tactical ↔ macro synthesis', color: '#f5e050'},
    {id: 'completion', label: 'Joint completion & cognitive space', color: '#c9b3ff'},
    {id: 'banter', label: 'Tension release / humor', color: '#ff9e64'},
    {id: 'collision', label: 'Breakdown / collision (control)', color: '#ff6b6b'},
    {id: 'other', label: 'Other', color: '#ffffff'},
  ];

  let categories = DEFAULT_CATEGORIES;
  let catMap = new Map(DEFAULT_CATEGORIES.map(c => [c.id, c]));
  let seedAnnotations = [];
  let annState = {edits: {}, deleted: [], added: []};
  let annFilter = 'all';
  let editingAnnId = null;
  let lastLiveAnnId = '';

  let lanesWinStart = 0;
  let renderedWinStart = -1;
  let renderedWinDur = -1;
  let lastLaneTrackPx = 0;
  let playheadEls = [];
  let blockElsByIndex = new Map();

  let ovWinStart = 0;
  let ovRenderedStart = -1;
  let ovRenderedTrackPx = 0;
  let ovPlayheadEls = [];
  let ovBlockElsByIndex = new Map();

  let laneMeasureCtx = null;
  let laneMeasureFont = '';
  let rafId = 0;

  function catInfo(id) {
    return catMap.get(id) || {id: id || 'other', label: id || 'Other', color: '#7cc4ff'};
  }
  function parseClockInput(str) {
    const s = String(str || '').trim();
    if (/^\d+(?:\.\d+)?$/.test(s)) return Number(s);
    const parts = s.split(':').map(Number);
    if (parts.some(n => !Number.isFinite(n) || n < 0)) return NaN;
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    return NaN;
  }
  function mergedAnnotations(includeSeed) {
    const deletedSet = new Set(annState.deleted || []);
    const edits = annState.edits || {};
    const out = [];
    if (includeSeed) {
      for (const item of seedAnnotations) {
        if (deletedSet.has(item.id)) continue;
        const patch = edits[item.id];
        out.push(patch ? {...item, ...patch, _origin: 'edited'} : {...item, _origin: 'ai'});
      }
    }
    for (const item of (annState.added || [])) {
      out.push({...item, _origin: 'user'});
    }
    out.sort((a, b) => (a.t - b.t) || String(a.id).localeCompare(String(b.id)));
    return out;
  }
  function visibleTimelineAnnotations() {
    if (!showAnnotations) return mergedAnnotations(false);
    const list = mergedAnnotations(true);
    return annFilter === 'all' ? list : list.filter(a => a.cat === annFilter);
  }

  function html(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function clock(t) {
    t = Math.max(0, Math.floor(Number(t) || 0));
    const h = Math.floor(t / 3600);
    const m = Math.floor(t / 60) % 60;
    const s = t % 60;
    return h ? h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0')
      : m + ':' + String(s).padStart(2, '0');
  }
  function clockPrecise(t) {
    t = Math.max(0, Number(t) || 0);
    const whole = Math.floor(t);
    const tenths = Math.floor((t - whole) * 10);
    return clock(whole) + '.' + tenths;
  }
  function clipText(text, maxLen) {
    const s = String(text || '').trim();
    return s.length > maxLen ? s.slice(0, maxLen - 1).trimEnd() + '…' : s;
  }
  function parseHashTime() {
    const m = location.hash.match(/(?:^#|&)t=([0-9]+(?:\.[0-9]+)?)/);
    return m ? Number(m[1]) : null;
  }
  function currentTime() {
    if (ytPlaying && ytStamp > 0) {
      const dt = Math.min(0.8, (performance.now() - ytStamp) / 1000);
      return Math.max(0, Math.min(model ? model.duration : 1e9, ytTime + dt * ytRate));
    }
    return ytTime;
  }

  function postYT(func, args) {
    if (!iframe || !iframe.contentWindow) return;
    iframe.contentWindow.postMessage(
      JSON.stringify({event: 'command', func, args: args || [], id: 1, channel: 'widget'}),
      YT_ORIGIN
    );
  }
  function seekAndPlay(t) {
    t = Math.max(0, Number(t) || 0);
    ytTime = t;
    ytStamp = performance.now();
    if (!ytReady) pendingUserSeek = t;
    postYT('seekTo', [t, true]);
    postYT('playVideo', []);
    syncAll(true);
  }

  function setupYouTube(videoId) {
    iframe = document.getElementById('yt-frame');
    if (pendingHashSeek != null && pendingHashSeek > 0) {
      ytTime = pendingHashSeek;
    }
    const originParam = /^https?:\/\//.test(location.origin)
      ? '&origin=' + encodeURIComponent(location.origin)
      : '';
    const startParam = pendingHashSeek != null ? '&start=' + Math.floor(pendingHashSeek) : '';
    iframe.src = YT_ORIGIN + '/embed/' + encodeURIComponent(videoId)
      + '?enablejsapi=1&rel=0&playsinline=1' + originParam + startParam;

    let handshakeTries = 0;
    let handshakeTimer = 0;
    function ping() {
      if (ytReady || handshakeTries > 40 || !iframe.contentWindow) {
        if (handshakeTimer) { clearInterval(handshakeTimer); handshakeTimer = 0; }
        return;
      }
      handshakeTries += 1;
      iframe.contentWindow.postMessage(
        JSON.stringify({event: 'listening', id: 1, channel: 'widget'}),
        YT_ORIGIN
      );
      postYT('addEventListener', ['onStateChange']);
    }
    iframe.addEventListener('load', () => {
      ping();
      if (!handshakeTimer) handshakeTimer = setInterval(ping, 350);
    });

    window.addEventListener('message', event => {
      if (event.origin !== YT_ORIGIN && event.origin !== 'https://www.youtube.com') return;
      if (typeof event.data !== 'string') return;
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      if (!msg || typeof msg !== 'object') return;
      if (!ytReady) {
        ytReady = true;
        if (handshakeTimer) { clearInterval(handshakeTimer); handshakeTimer = 0; }
        if (pendingUserSeek != null) {
          const target = pendingUserSeek;
          pendingUserSeek = null;
          postYT('seekTo', [target, true]);
          postYT('playVideo', []);
        } else if (pendingHashSeek != null && pendingHashSeek > 0) {
          ytTime = pendingHashSeek;
          ytStamp = performance.now();
          pendingHashSeek = null;
          syncAll(true);
        }
      }
      if ((msg.event === 'infoDelivery' || msg.event === 'initialDelivery') && msg.info) {
        if (typeof msg.info.currentTime === 'number') {
          ytTime = msg.info.currentTime;
          ytStamp = performance.now();
        }
        if (typeof msg.info.playbackRate === 'number' && msg.info.playbackRate > 0) {
          ytRate = msg.info.playbackRate;
        }
        if (typeof msg.info.playerState === 'number') {
          const playing = msg.info.playerState === 1;
          if (playing !== ytPlaying) {
            ytPlaying = playing;
            if (playing && !rafId) rafId = requestAnimationFrame(tick);
            if (!playing && rafId) { cancelAnimationFrame(rafId); rafId = 0; }
          }
        }
        syncAll(false);
      } else if (msg.event === 'onStateChange') {
        const playing = msg.info === 1;
        ytPlaying = playing;
        if (playing && !rafId) rafId = requestAnimationFrame(tick);
        if (!playing && rafId) { cancelAnimationFrame(rafId); rafId = 0; }
        syncAll(false);
      }
    });
  }

  function expandCompact(raw) {
    const spks = raw.speakers || [];
    const segments = (raw.segments || []).map(s => {
      const tokens = Array.isArray(s.tk)
        ? s.tk.map(tk => ({
            start: Number(tk[0]),
            end: Number(tk[1]),
            word: String(tk[2] || ''),
            ruby: tk[3] || '',
            gloss: String(tk[4] || ''),
            vid: tk[5] == null ? -1 : Number(tk[5]),
          }))
        : [];
      const words = tokens.length
        ? tokens
        : (s.w || []).map(w => ({start: Number(w[0]), end: Number(w[1]), word: String(w[2]), ruby: '', gloss: '', vid: -1}));
      return {
        index: s.i,
        start: Number(s.s),
        end: Number(s.e),
        speaker: spks[s.k] || '',
        text: s.t || '',
        en: String(s.en || ''),
        words,
        tokens,
      };
    });
    const vocab = (raw.vocab || []).map(v => ({
      id: Number(v.id),
      base: String(v.b || v.base || ''),
      ruby: v.rb || v.ruby || '',
      kana: String(v.k || v.kana || ''),
      romaji: String(v.rm || v.romaji || ''),
      diff: String(v.d || v.diff || 'basic'),
      gloss: String(v.g || v.gloss || ''),
      role: String(v.rl || v.role || ''),
      note: String(v.n || v.note || ''),
      count: Number(v.c || v.count || 0),
      times: Array.isArray(v.t || v.times) ? (v.t || v.times).map(Number) : [],
    }));
    const acousticOverlaps = (raw.overlaps || []).map(ov => ({
      start: Number(ov[0]),
      end: Number(ov[1]),
      speakers: (ov[2] || []).map(k => spks[k] || '').filter(Boolean),
    }));
    const pulses = (raw.pulses || []).map(p => ({
      start: Number(p[0]),
      end: Number(p[1]),
      speaker: spks[p[2]] || '',
    }));
    return {
      video_id: raw.video_id,
      language: String(raw.language || ''),
      title: raw.title,
      channel: raw.channel,
      upload_date: raw.upload_date,
      duration: Number(raw.duration) || 0,
      chapters: raw.chapters || [],
      avatars: raw.avatars && typeof raw.avatars === 'object' ? raw.avatars : {},
      segments,
      vocab,
      acousticOverlaps,
      pulses,
    };
  }

  function buildOverlapModel(data) {
    const segments = data.segments || [];
    let maxEnd = Number(data.duration) || 0;
    const speakers = [];
    const speakerCounts = new Map();
    const byIndex = new Map();
    const infoByIndex = new Map();
    for (const seg of segments) {
      if (seg.end > maxEnd) maxEnd = seg.end;
      byIndex.set(seg.index, seg);
      infoByIndex.set(seg.index, {partners: [], overlapsPrev: false, chipsByWord: new Map(), endChips: []});
      const spk = seg.speaker || '';
      if (spk) {
        if (!speakerCounts.has(spk)) speakers.push(spk);
        speakerCounts.set(spk, (speakerCounts.get(spk) || 0) + 1);
      }
    }
    const colorMap = new Map();
    speakers.forEach((s, i) => colorMap.set(s, SPEAKER_PALETTE[i % SPEAKER_PALETTE.length]));
    const colorOf = s => (s && colorMap.get(s)) || UNASSIGNED;

    const sorted = [...segments].sort((a, b) => (a.start - b.start) || (a.end - b.end));
    const active = [];
    const rawOverlaps = [];

    function attachChip(host, guest, ovStart, ovEnd) {
      const info = infoByIndex.get(host.index);
      const chip = {other: guest, color: colorOf(guest.speaker).fg, ovStart, ovEnd};
      const words = host.words || [];
      if (!words.length) { info.endChips.push(chip); return; }
      let anchor = -1;
      for (let i = 0; i < words.length; i++) {
        if (words[i].start < ovEnd && words[i].end > ovStart) anchor = i;
      }
      if (anchor === -1) {
        for (let i = 0; i < words.length; i++) {
          if (words[i].start <= guest.start) anchor = i; else break;
        }
        if (anchor === -1) anchor = 0;
      }
      if (!info.chipsByWord.has(anchor)) info.chipsByWord.set(anchor, []);
      info.chipsByWord.get(anchor).push(chip);
    }

    for (const seg of sorted) {
      for (let i = active.length - 1; i >= 0; i--) {
        if (active[i].end <= seg.start + 0.05) active.splice(i, 1);
      }
      for (const prev of active) {
        if ((prev.speaker || '') === (seg.speaker || '')) continue;
        const ovStart = Math.max(prev.start, seg.start);
        const ovEnd = Math.min(prev.end, seg.end);
        if (ovEnd - ovStart < 0.05) continue;
        infoByIndex.get(prev.index).partners.push({other: seg, ovStart, ovEnd});
        infoByIndex.get(seg.index).partners.push({other: prev, ovStart, ovEnd});
        infoByIndex.get(seg.index).overlapsPrev = true;
        rawOverlaps.push({
          start: ovStart, end: ovEnd,
          speakers: [prev.speaker || 'Unassigned', seg.speaker || 'Unassigned'],
          indices: [prev.index, seg.index],
        });
        if (seg.start >= prev.start + 0.15 || prev.end > seg.end + 0.8) attachChip(prev, seg, ovStart, ovEnd);
        else if (seg.end > prev.end + 0.8) attachChip(seg, prev, ovStart, ovEnd);
      }
      active.push(seg);
    }
    for (const ov of (data.acousticOverlaps || [])) {
      rawOverlaps.push({
        start: ov.start, end: ov.end,
        speakers: ov.speakers.length ? ov.speakers : ['Unassigned'],
        indices: [],
      });
    }
    rawOverlaps.sort((a, b) => a.start - b.start || a.end - b.end);
    const overlapSpans = [];
    for (const ov of rawOverlaps) {
      const last = overlapSpans[overlapSpans.length - 1];
      if (last && ov.start <= last.end + 0.15) {
        if (ov.end > last.end) last.end = ov.end;
        ov.speakers.forEach(s => last.speakers.add(s));
        ov.indices.forEach(id => last.indices.add(id));
      } else {
        overlapSpans.push({
          start: ov.start, end: ov.end,
          speakers: new Set(ov.speakers), indices: new Set(ov.indices),
        });
      }
    }
    const bySpeaker = new Map();
    const pulsesBySpeaker = new Map();
    for (const s of speakers) { bySpeaker.set(s, []); pulsesBySpeaker.set(s, []); }
    for (const seg of sorted) {
      if (seg.speaker && bySpeaker.has(seg.speaker)) bySpeaker.get(seg.speaker).push(seg);
    }
    for (const p of (data.pulses || [])) {
      if (p.speaker && pulsesBySpeaker.has(p.speaker)) pulsesBySpeaker.get(p.speaker).push(p);
    }
    // Tag segments that intersect an acoustic overlap span so transcript filter='overlaps' finds them too.
    if ((data.acousticOverlaps || []).length) {
      for (const seg of sorted) {
        const info = infoByIndex.get(seg.index);
        for (const ov of overlapSpans) {
          if (ov.end <= seg.start) continue;
          if (ov.start >= seg.end) break;
          if (ov.speakers.has(seg.speaker)) {
            info.hasAcousticOv = true;
            for (const otherSpk of ov.speakers) {
              if (otherSpk !== seg.speaker) {
                info.acousticBands = info.acousticBands || [];
                info.acousticBands.push({start: Math.max(seg.start, ov.start), end: Math.min(seg.end, ov.end), speaker: otherSpk});
              }
            }
          }
        }
      }
    }
    return {
      duration: Math.max(1, maxEnd),
      speakers, speakerCounts, colorOf, byIndex, infoByIndex, overlapSpans, sorted, bySpeaker, pulsesBySpeaker,
    };
  }

  function getLaneMeasurer(isOverlay, scaleOverride) {
    if (!laneMeasureCtx) {
      laneMeasureCtx = document.createElement('canvas').getContext('2d');
    }
    const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 15;
    const family = getComputedStyle(document.body).fontFamily || 'sans-serif';
    const scale = scaleOverride || (isOverlay ? 0.84 : 0.9);
    const font = '600 ' + (scale * rootPx).toFixed(2) + 'px ' + family;
    if (laneMeasureCtx && font !== laneMeasureFont) {
      laneMeasureCtx.font = font;
      laneMeasureFont = font;
    }
    return laneMeasureCtx;
  }

  function laneTokensForSpan(seg, s0, s1) {
    const rawWords = seg.words || [];
    const tokens = [];
    if (rawWords.length) {
      for (const w of rawWords) {
        const txt = String(w.word || '').trim();
        if (!txt) continue;
        if (!hasStudy && tokens.length && /^[,.;:!?)]/.test(txt)) {
          const prev = tokens[tokens.length - 1];
          prev.word += txt;
          if (w.end > prev.end) prev.end = w.end;
        } else {
          tokens.push({
            word: txt, start: w.start, end: Math.max(w.start, w.end),
            ruby: w.ruby || '', gloss: w.gloss || '', vid: w.vid == null ? -1 : w.vid,
          });
        }
      }
    } else {
      const parts = String(seg.text || '').trim().split(/\s+/).filter(Boolean);
      const totalWt = parts.reduce((a, w) => a + w.length + 1, 0) || 1;
      const dur = Math.max(0.001, seg.end - seg.start);
      let acc = 0;
      for (const p of parts) {
        const wt = p.length + 1;
        const ws = seg.start + (acc / totalWt) * dur;
        acc += wt;
        tokens.push({word: p, start: ws, end: seg.start + (acc / totalWt) * dur, ruby: '', gloss: '', vid: -1});
      }
    }
    const visible = tokens.filter(t => t.end > s0 + 0.005 && t.start < s1 - 0.005);
    return visible.length ? visible : tokens.slice(0, 1);
  }

  function solveTimedWordPositions(ideals, widths, spacePx, innerPx, totalPx) {
    const n = ideals.length;
    const maxShift = Math.max(0, innerPx - totalPx);
    const cum = new Array(n);
    const blocks = [];
    let cur = 0;
    for (let i = 0; i < n; i++) {
      cum[i] = cur;
      blocks.push({sum: ideals[i] - cur, count: 1});
      while (
        blocks.length >= 2
        && (blocks[blocks.length - 2].sum / blocks[blocks.length - 2].count)
           > (blocks[blocks.length - 1].sum / blocks[blocks.length - 1].count)
      ) {
        const last = blocks.pop();
        blocks[blocks.length - 1].sum += last.sum;
        blocks[blocks.length - 1].count += last.count;
      }
      cur += widths[i] + spacePx;
    }
    const xs = new Array(n);
    let idx = 0;
    for (const blk of blocks) {
      const shift = Math.max(0, Math.min(maxShift, blk.sum / blk.count));
      for (let k = 0; k < blk.count; k++) {
        xs[idx] = shift + cum[idx];
        idx += 1;
      }
    }
    return xs;
  }

  function wordOverlapColor(tok, info) {
    if (!info) return '';
    for (const p of (info.partners || [])) {
      if (tok.start < p.ovEnd && tok.end > p.ovStart) return model.colorOf(p.other.speaker).fg;
    }
    for (const ab of (info.acousticBands || [])) {
      if (tok.start < ab.end && tok.end > ab.start) return model.colorOf(ab.speaker).fg;
    }
    return '';
  }

  function buildLaneBlockContent(seg, s0, s1, blockPx, info, ctx, withFuri) {
    const pad = 6;
    const innerPx = blockPx - pad * 2;
    if (!ctx || innerPx < 28) return {cls: '', inner: html(seg.text)};
    const tokens = laneTokensForSpan(seg, s0, s1);
    if (!tokens.length) return {cls: '', inner: ''};

    const useFuri = Boolean(hasStudy && withFuri);
    const spacePx = hasStudy ? 3 : Math.max(4, Math.ceil(ctx.measureText(' ').width));
    const widths = tokens.map(t => {
      const baseW = Math.ceil(ctx.measureText(t.word).width);
      if (useFuri && Array.isArray(t.ruby)) {
        const reading = t.ruby.map(p => p[1] || p[0] || '').join('');
        return Math.max(baseW, Math.ceil(ctx.measureText(reading).width * 0.58));
      }
      return baseW;
    });
    const totalPx = widths.reduce((a, b) => a + b, 0) + Math.max(0, tokens.length - 1) * spacePx;
    const sDur = Math.max(0.001, s1 - s0);

    let xs;
    let clipped = false;
    if (totalPx <= innerPx) {
      const ideals = tokens.map((t, i) => {
        const raw = ((t.start - s0) / sDur) * innerPx;
        return Math.max(0, Math.min(innerPx - widths[i], raw));
      });
      xs = solveTimedWordPositions(ideals, widths, spacePx, innerPx, totalPx);
    } else {
      clipped = true;
      xs = [];
      let cur = 0;
      for (let i = 0; i < tokens.length; i++) {
        xs.push(cur);
        cur += widths[i] + spacePx;
      }
    }
    const spans = [];
    for (let i = 0; i < tokens.length; i++) {
      if (clipped && xs[i] >= innerPx) break;
      const tok = tokens[i];
      const ovColor = wordOverlapColor(tok, info);
      const leftPct = ((pad + xs[i]) / blockPx) * 100;
      const vAttr = tok.vid >= 0 ? ' data-v="' + tok.vid + '"' : '';
      const tipAttr = tok.gloss ? ' title="' + html(tok.word + ' — ' + tok.gloss) + '"' : '';
      const innerTxt = useFuri && tok.ruby ? rubyHtml(tok.ruby, tok.word) : html(tok.word);
      spans.push(
        '<span class="lane-word' + (ovColor ? ' lw-ov' : '') + (tok.vid >= 0 ? ' has-vocab' : '') + '"'
        + ' data-t="' + tok.start + '" data-e="' + tok.end + '"' + vAttr + tipAttr
        + ' style="left:' + leftPct.toFixed(3) + '%'
        + (ovColor ? ';--ov-color:' + ovColor : '') + '">'
        + innerTxt + '</span>'
      );
    }
    return {
      cls: ' has-words' + (clipped ? ' is-clipped' : ''),
      inner: '<div class="lane-words"' + (hasStudy ? ' lang="ja"' : '') + '>' + spans.join('') + '</div>',
    };
  }

  function buildGlossBlockContent(seg, s0, s1, blockPx, ctx) {
    const pad = 6;
    const innerPx = blockPx - pad * 2;
    const allTokens = laneTokensForSpan(seg, s0, s1).filter(t => t.gloss);
    if (!allTokens.length) return {cls: '', inner: ''};
    if (!ctx || innerPx < 28) {
      return {cls: '', inner: html(allTokens.map(t => t.gloss).join(' · '))};
    }
    const spacePx = 7;
    const measureList = list => {
      const ws = list.map(t => Math.ceil(ctx.measureText(t.gloss).width));
      return {ws, total: ws.reduce((a, b) => a + b, 0) + Math.max(0, list.length - 1) * spacePx};
    };
    let tokens = allTokens;
    let m = measureList(tokens);
    // When zoomed out, drop grammatical particle labels first so content meanings stay aligned and readable.
    if (m.total > innerPx) {
      const contentOnly = tokens.filter(t => {
        const v = t.vid >= 0 ? vocabById.get(t.vid) : null;
        return !v || !v.role.toLowerCase().startsWith('particle');
      });
      if (contentOnly.length) {
        tokens = contentOnly;
        m = measureList(tokens);
      }
    }
    const sDur = Math.max(0.001, s1 - s0);
    let xs;
    let clipped = false;
    if (m.total <= innerPx) {
      const ideals = tokens.map((t, i) => {
        const raw = ((t.start - s0) / sDur) * innerPx;
        return Math.max(0, Math.min(innerPx - m.ws[i], raw));
      });
      xs = solveTimedWordPositions(ideals, m.ws, spacePx, innerPx, m.total);
    } else {
      clipped = true;
      xs = [];
      let cur = 0;
      for (let i = 0; i < tokens.length; i++) {
        xs.push(cur);
        cur += m.ws[i] + spacePx;
      }
    }
    const spans = [];
    for (let i = 0; i < tokens.length; i++) {
      if (clipped && xs[i] >= innerPx) break;
      const tok = tokens[i];
      const leftPct = ((pad + xs[i]) / blockPx) * 100;
      const vAttr = tok.vid >= 0 ? ' data-v="' + tok.vid + '"' : '';
      spans.push(
        '<span class="lane-word' + (tok.vid >= 0 ? ' has-vocab' : '') + '"'
        + ' data-t="' + tok.start + '" data-e="' + tok.end + '"' + vAttr
        + ' style="left:' + leftPct.toFixed(3) + '%" title="' + html(tok.word + ' = ' + tok.gloss) + '">'
        + html(tok.gloss) + '</span>'
      );
    }
    return {
      cls: ' has-words' + (clipped ? ' is-clipped' : ''),
      inner: '<div class="lane-words">' + spans.join('') + '</div>',
    };
  }

  function clampWinStart(start) {
    if (!model) return 0;
    return Math.max(0, Math.min(start, Math.max(0, model.duration - lanesWindow)));
  }

  function renderTimelineInto(ruler, body, winStart, winDur, isOverlay) {
    const trackPx = ruler.getBoundingClientRect().width || 0;
    const winEnd = winStart + winDur;
    const ovMul = isOverlay ? ovCurrentScale : 1;
    const measurer = trackPx > 0
      ? getLaneMeasurer(isOverlay, isOverlay ? (hasStudy ? 0.86 : 0.84) * ovMul : (hasStudy ? 0.92 : 0.9))
      : null;
    const glossMeasurer = hasStudy && trackPx > 0
      ? getLaneMeasurer(isOverlay, (isOverlay ? 0.74 : 0.78) * ovMul)
      : null;
    const step = winDur <= 8 ? 1 : winDur <= 12 ? 2 : winDur <= 20 ? 4 : winDur <= 30 ? 5 : 10;
    const firstTick = Math.ceil(winStart / step) * step;
    const ticks = [];
    const gridlines = [];
    for (let t = firstTick; t <= winEnd; t += step) {
      const pct = ((t - winStart) / winDur) * 100;
      ticks.push('<span class="ruler-tick" style="left:' + pct.toFixed(3) + '%">' + clock(t) + '</span>');
      gridlines.push('<span class="lane-gridline" style="left:' + pct.toFixed(3) + '%"></span>');
    }
    const visibleOverlaps = model.overlapSpans.filter(ov => ov.end > winStart && ov.start < winEnd);
    const pills = showBadges ? visibleOverlaps.map(ov => {
      const left = ((Math.max(ov.start, winStart) - winStart) / winDur) * 100;
      const right = ((Math.min(ov.end, winEnd) - winStart) / winDur) * 100;
      const width = Math.max(0.25, right - left);
      if (width < (isOverlay ? 3.5 : 2.6)) return '';
      const dur = Math.max(0.1, ov.end - ov.start);
      return '<span class="ruler-ov-pill" style="left:' + left.toFixed(3)
        + '%;max-width:' + width.toFixed(3) + '%">∥ ' + dur.toFixed(1) + 's</span>';
    }).join('') : '';

    const visAnns = visibleTimelineAnnotations();
    const annSpans = visAnns.filter(a => a.end && a.end > a.t && a.end > winStart && a.t < winEnd).map(a => {
      const ci = catInfo(a.cat);
      const left = ((Math.max(a.t, winStart) - winStart) / winDur) * 100;
      const right = ((Math.min(a.end, winEnd) - winStart) / winDur) * 100;
      return '<span class="lane-ann-span" style="left:' + left.toFixed(3)
        + '%;width:' + Math.max(0.4, right - left).toFixed(3)
        + '%;--cat-color:' + ci.color + '"></span>';
    }).join('');
    const notePins = visAnns.filter(n => n.t >= winStart && n.t <= winEnd).map(n => {
      const ci = catInfo(n.cat);
      const pct = ((n.t - winStart) / winDur) * 100;
      const spanTxt = n.end && n.end > n.t ? clock(n.t) + '–' + clock(n.end) : clock(n.t);
      const tip = spanTxt + ' [' + ci.label + ']'
        + (n.title ? ' ' + n.title : '')
        + (n.note ? ' — ' + n.note : '');
      const label = clipText(n.title || ci.label, isOverlay ? 14 : 18);
      return '<span class="ruler-note-pin" data-ann-id="' + html(n.id) + '" data-t="' + n.t
        + '" style="left:' + pct.toFixed(3) + '%;--cat-color:' + ci.color + '" title="'
        + html(tip) + '">' + html(label) + '</span>';
    }).join('');

    ruler.innerHTML = ticks.join('') + pills + annSpans + notePins + '<div class="lane-playhead"></div>';

    const bands = visibleOverlaps.map(ov => {
      const left = ((Math.max(ov.start, winStart) - winStart) / winDur) * 100;
      const right = ((Math.min(ov.end, winEnd) - winStart) / winDur) * 100;
      return '<div class="lane-ov-band" style="left:' + left.toFixed(3)
        + '%;width:' + Math.max(0.25, right - left).toFixed(3) + '%"></div>';
    }).join('');
    const gridHtml = gridlines.join('');

    const filter = document.getElementById('transcript-filter')?.value || 'all';
    const rowCls = isOverlay ? 'ov-row' : 'lane-row';
    const lblCls = isOverlay ? 'ov-label' : 'lane-label';
    const trkCls = isOverlay ? 'ov-track' : 'lane-track';
    const rows = getSurfaceRows(isOverlay ? 'overlay' : 'main');
    if (!rows.length) {
      body.innerHTML = '<div class="lanes-empty">No streams shown — right-click here to add streams or speakers</div>';
      return trackPx;
    }
    const spkRowCount = new Map();
    for (const r of rows) spkRowCount.set(r.spk, (spkRowCount.get(r.spk) || 0) + 1);
    const streamGrouped = isStreamGrouped(rows);
    const seenPulseSpk = new Set();

    body.innerHTML = rows.map((row, ri) => {
      const spk = row.spk;
      const kind = row.kind;
      const c = model.colorOf(spk);
      const segs = model.bySpeaker.get(spk) || [];
      const sameAsPrev = ri > 0 && rows[ri - 1].spk === spk;
      const isGroupEnd = ri < rows.length - 1 && (
        streamGrouped ? rows[ri + 1].kind !== kind : rows[ri + 1].spk !== spk
      );
      const blocks = [];
      for (const seg of segs) {
        if (seg.end <= winStart) continue;
        if (seg.start >= winEnd) break;
        const s0 = Math.max(seg.start, winStart);
        const s1 = Math.min(seg.end, winEnd);
        const left = ((s0 - winStart) / winDur) * 100;
        const width = Math.max(0.4, ((s1 - s0) / winDur) * 100);
        const blockPx = trackPx > 0 ? (trackPx * width) / 100 : 0;
        const info = model.infoByIndex.get(seg.index);
        const hasOv = Boolean(info && (info.partners.length || info.hasAcousticOv));
        const title = spk + ' (' + clockPrecise(seg.start) + '–' + clockPrecise(seg.end) + '): '
          + seg.text + (seg.en ? ' — ' + seg.en : '');
        const posStyle = 'left:' + left.toFixed(3) + '%;width:' + width.toFixed(3) + '%;'
          + '--spk-color:' + c.fg + ';--spk-bg:' + c.bg + ';--spk-bg-hi:' + c.bgHi;
        if (kind === 'literal') {
          const gc = buildGlossBlockContent(seg, s0, s1, blockPx, glossMeasurer);
          if (gc.inner) {
            blocks.push(
              '<div class="lane-block lb-ww' + (hasOv ? ' has-ov' : '') + gc.cls + '" data-index="' + seg.index + '"'
              + ' style="' + posStyle + '" title="' + html(title) + '">' + gc.inner + '</div>'
            );
          }
        } else if (kind === 'en') {
          if (seg.en) {
            blocks.push(
              '<div class="lane-block lb-en' + (hasOv ? ' has-ov' : '') + '" data-index="' + seg.index + '"'
              + ' style="' + posStyle + '" title="' + html(spk + ': ' + seg.en) + '">'
              + html(seg.en) + '</div>'
            );
          }
        } else {
          const withFuri = kind === 'ja-furi';
          const content = buildLaneBlockContent(seg, s0, s1, blockPx, info, measurer, withFuri);
          const extraBlkCls = kind === 'ja-furi' ? ' ja-furi-blk' : kind === 'ja' ? ' ja-plain-blk' : '';
          blocks.push(
            '<div class="lane-block' + extraBlkCls + (hasOv ? ' has-ov' : '') + content.cls + '"'
            + ' data-index="' + seg.index + '" style="' + posStyle + '"'
            + ' title="' + html(title) + '">' + content.inner + '</div>'
          );
        }
      }
      const showPulses = !seenPulseSpk.has(spk) && (kind === 'ja-furi' || kind === 'ja' || kind === 'text');
      if (showPulses) seenPulseSpk.add(spk);
      const pulses = showPulses ? (model.pulsesBySpeaker?.get(spk) || []).filter(p => p.end > winStart && p.start < winEnd).map(p => {
        const p0 = Math.max(p.start, winStart);
        const p1 = Math.min(p.end, winEnd);
        const left = ((p0 - winStart) / winDur) * 100;
        const width = Math.max(0.4, ((p1 - p0) / winDur) * 100);
        const dur = (p.end - p.start).toFixed(1);
        return '<div class="lane-pulse" data-t="' + p.start + '" style="left:' + left.toFixed(3)
          + '%;width:' + width.toFixed(3) + '%;--spk-color:' + c.fg + ';--spk-bg:' + c.bg + '"'
          + ' title="' + html(spk + ' vocal overlap / backchannel (' + dur + 's)') + '"></div>';
      }).join('') : '';

      const av = avatarHtml(spk, isOverlay ? 'av-sm' : (sameAsPrev ? 'av-sm' : ''));
      const kindShort = STREAM_MAP.get(kind)?.short || '';
      const isFiltered = !isOverlay && filter === 'spk:' + spk;
      const rowKindCls = hasStudy ? ' row-' + kind : '';
      const grip = '<span class="lbl-grip" aria-hidden="true">⋮</span>';
      const labelHtml = !sameAsPrev
        ? grip + av + '<span class="lbl-txt">' + html(spk) + '</span>'
          + (hasStudy && (spkRowCount.get(spk) > 1 || kind !== 'ja-furi' || streamGrouped)
            ? '<span class="lbl-kind">' + html(kindShort) + '</span>' : '')
        : grip + av + '<span class="lbl-txt">' + html(kindShort) + '</span>';

      return '<div class="' + rowCls + rowKindCls + (isGroupEnd ? ' row-grp-end' : '') + '"'
        + ' data-row-idx="' + ri + '" data-row-spk="' + html(spk) + '" data-row-kind="' + html(kind) + '">'
        + '<div class="' + lblCls + (av ? ' has-avatar' : '') + (sameAsPrev ? ' sub-lbl' : '') + (isFiltered ? ' filtered' : '') + '"'
        + ' data-speaker="' + html(spk) + '" style="--spk-color:' + c.fg + '"'
        + ' title="Drag up/down to reorder · Right-click to add/remove streams or arrange">' + labelHtml + '</div>'
        + '<div class="' + trkCls + '">' + gridHtml + bands + pulses + blocks.join('')
        + '<div class="lane-playhead"></div></div></div>';
    }).join('');
    return trackPx;
  }

  function indexLaneBlocks(container) {
    const map = new Map();
    container.querySelectorAll('.lane-block').forEach(el => {
      const idx = Number(el.dataset.index);
      if (!map.has(idx)) map.set(idx, []);
      map.get(idx).push(el);
    });
    return map;
  }

  function renderLanesWindow(force) {
    if (!model) return;
    const ruler = document.getElementById('lanes-ruler');
    const body = document.getElementById('lanes-body');
    if (!ruler || !body) return;
    const winStart = clampWinStart(lanesWinStart);
    const winDur = lanesWindow;
    const trackPx = ruler.getBoundingClientRect().width || 0;
    if (!force && Math.abs(winStart - renderedWinStart) < 0.001 && winDur === renderedWinDur && Math.abs(trackPx - lastLaneTrackPx) < 1) {
      return;
    }
    lanesWinStart = winStart;
    renderedWinStart = winStart;
    renderedWinDur = winDur;
    lastLaneTrackPx = renderTimelineInto(ruler, body, winStart, winDur, false);
    playheadEls = [...document.querySelectorAll('#lanes-stage .lane-playhead')];
    blockElsByIndex = indexLaneBlocks(body);
    updatePlayheadOn(playheadEls, blockElsByIndex, currentTime(), lanesWinStart, lanesWindow);
  }

  function updatePlayheadOn(phEls, blkMap, time, winStart, winDur) {
    const pct = ((time - winStart) / winDur) * 100;
    const visible = pct >= 0 && pct <= 100;
    for (const el of phEls) {
      el.style.display = visible ? '' : 'none';
      if (visible) el.style.left = pct.toFixed(3) + '%';
    }
    for (const [idx, blks] of blkMap.entries()) {
      const seg = model.byIndex.get(idx);
      const isNow = Boolean(seg && time >= seg.start && time < seg.end);
      for (const blk of blks) {
        blk.classList.toggle('now', isNow);
        if (isNow) {
          blk.querySelectorAll('.lane-word').forEach(w => {
            const ws = Number(w.dataset.t);
            const we = Number(w.dataset.e);
            w.classList.toggle('lw-now', time >= ws && time <= Math.max(we, ws + 0.08));
          });
        } else {
          blk.querySelectorAll('.lane-word.lw-now').forEach(w => w.classList.remove('lw-now'));
        }
      }
    }
  }

  function updateLanesPlayhead(time) {
    if (!model) return;
    if (lanesFollow && (time < lanesWinStart + lanesWindow * 0.08 || time > lanesWinStart + lanesWindow * 0.85)) {
      const targetStart = clampWinStart(time - lanesWindow * 0.25);
      if (Math.abs(targetStart - lanesWinStart) > 0.25) {
        lanesWinStart = targetStart;
        renderLanesWindow(true);
        return;
      }
    }
    updatePlayheadOn(playheadEls, blockElsByIndex, time, lanesWinStart, lanesWindow);
    if (showMinimap) {
      const vp = document.getElementById('minimap-viewport');
      const mp = document.getElementById('minimap-playhead');
      if (vp) {
        vp.style.left = ((lanesWinStart / model.duration) * 100).toFixed(3) + '%';
        vp.style.width = Math.min(100, (lanesWindow / model.duration) * 100).toFixed(3) + '%';
      }
      if (mp) mp.style.left = Math.min(100, (time / model.duration) * 100).toFixed(3) + '%';
    }
    if (!hoveredIndices) paintLanesStatus(time);
  }

  function paintLanesStatus(time) {
    const status = document.getElementById('lanes-status');
    if (!status || !model) return;
    if (!showBadges) {
      status.innerHTML = '';
      return;
    }
    const t = time != null ? time : currentTime();
    const ovIdx = model.overlapSpans.findIndex(ov => t >= ov.start && t <= ov.end);
    if (ovIdx !== -1) {
      const ov = model.overlapSpans[ovIdx];
      const dur = Math.max(0.1, ov.end - ov.start);
      const who = [...ov.speakers].map(s => '<strong style="color:' + model.colorOf(s).fg + '">' + html(s) + '</strong>').join(' + ');
      status.innerHTML = '<span class="ov-badge">∥ ' + (ovIdx + 1) + '/' + model.overlapSpans.length + ' · ' + dur.toFixed(1) + 's</span>' + who;
      return;
    }
    status.textContent = model.overlapSpans.length + ' overlaps';
  }

  function applyOverlayState() {
    const ovEl = document.getElementById('ov-overlay');
    const btn = document.getElementById('toggle-overlay');
    if (btn) btn.setAttribute('aria-pressed', String(showOverlay));
    if (!ovEl) return;
    ovEl.hidden = !showOverlay;
    if (showOverlay) {
      fitOverlayLayout();
      ovRenderedStart = -1;
      requestAnimationFrame(() => {
        fitOverlayLayout();
        updateOverlayTimeline(currentTime(), true);
      });
    }
  }

  function applyMinimapState() {
    const wrap = document.getElementById('lanes-minimap-wrap');
    const btn = document.getElementById('toggle-minimap');
    if (btn) btn.setAttribute('aria-pressed', String(showMinimap));
    if (!wrap) return;
    wrap.hidden = !showMinimap;
    if (showMinimap) {
      requestAnimationFrame(() => {
        drawMinimap();
        updateLanesPlayhead(currentTime());
      });
    }
  }

  function applyBadgesState() {
    const btn = document.getElementById('toggle-badges');
    if (btn) btn.setAttribute('aria-pressed', String(showBadges));
    renderLanesWindow(true);
    if (showOverlay) updateOverlayTimeline(currentTime(), true);
    paintLanesStatus();
  }

  function updateOverlayTimeline(time, force) {
    const ovEl = document.getElementById('ov-overlay');
    if (!ovEl || ovEl.hidden || !model) return;
    const ruler = document.getElementById('ov-ruler');
    const body = document.getElementById('ov-body');
    if (!ruler || !body) return;
    fitOverlayLayout();
    const winDur = Math.min(OV_WINDOW_SEC, Math.max(4, lanesWindow));
    const maxStart = Math.max(0, model.duration - winDur);
    const trackPx = ruler.getBoundingClientRect().width || 0;
    const outOfView = ovRenderedStart < 0 || time < ovWinStart + winDur * 0.05 || time > ovWinStart + winDur * 0.84;
    if (force || outOfView || Math.abs(trackPx - ovRenderedTrackPx) >= 2) {
      ovWinStart = outOfView || ovRenderedStart < 0
        ? Math.max(0, Math.min(maxStart, time - winDur * 0.14))
        : Math.max(0, Math.min(maxStart, ovWinStart));
      ovRenderedStart = ovWinStart;
      ovRenderedTrackPx = renderTimelineInto(ruler, body, ovWinStart, winDur, true);
      ovPlayheadEls = [...ovEl.querySelectorAll('.lane-playhead')];
      ovBlockElsByIndex = indexLaneBlocks(body);
    }
    updatePlayheadOn(ovPlayheadEls, ovBlockElsByIndex, time, ovWinStart, winDur);
  }

  function drawMinimap() {
    if (!showMinimap) return;
    const canvas = document.getElementById('lanes-minimap');
    if (!canvas || !model) return;
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(200, Math.round(rect.width * (window.devicePixelRatio || 1)));
    const height = Math.round(22 * (window.devicePixelRatio || 1));
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#050608';
    ctx.fillRect(0, 0, width, height);
    const rowH = height / Math.max(1, model.speakers.length);
    model.speakers.forEach((spk, i) => {
      ctx.fillStyle = model.colorOf(spk).fg;
      const y = Math.floor(i * rowH) + 1;
      const h = Math.max(2, Math.floor(rowH) - 2);
      for (const seg of (model.bySpeaker.get(spk) || [])) {
        ctx.fillRect((seg.start / model.duration) * width, y, Math.max(1.5, ((seg.end - seg.start) / model.duration) * width), h);
      }
    });
    ctx.fillStyle = '#ffd240';
    for (const ov of model.overlapSpans) {
      const x0 = (ov.start / model.duration) * width;
      const w = Math.max(2, ((ov.end - ov.start) / model.duration) * width);
      ctx.fillRect(x0, 0, w, Math.max(3, Math.round(height * 0.22)));
      ctx.fillRect(x0, height - Math.max(3, Math.round(height * 0.22)), w, Math.max(3, Math.round(height * 0.22)));
    }
    if (showAnnotations) {
      for (const a of visibleTimelineAnnotations()) {
        const x0 = (a.t / model.duration) * width;
        const w = Math.max(3, (((a.end && a.end > a.t ? a.end : a.t) - a.t) / model.duration) * width);
        ctx.fillStyle = catInfo(a.cat).color;
        ctx.fillRect(x0, 0, w, Math.max(4, Math.round(height * 0.32)));
      }
    }
  }

  function chipHtml(chip) {
    const spk = chip.other.speaker || 'Other';
    return ' <span class="ov-chip" data-target="' + chip.other.index + '" style="--ov-color:' + chip.color + '"'
      + ' title="' + html(spk + ': ' + chip.other.text) + '">∥ <strong>' + html(spk) + '</strong>: “'
      + html(clipText(chip.other.text, 40)) + '”</span>';
  }

  function paintTranscript() {
    const box = document.getElementById('caption-box');
    const query = (document.getElementById('transcript-search').value || '').trim().toLowerCase();
    const filter = document.getElementById('transcript-filter').value || 'all';
    box.innerHTML = doc.segments.map(seg => {
      const info = model.infoByIndex.get(seg.index);
      const partners = info ? info.partners : [];
      const hasOv = partners.length > 0 || Boolean(info && info.hasAcousticOv);
      let passF = true;
      if (filter === 'overlaps') passF = hasOv;
      else if (filter.startsWith('spk:')) passF = seg.speaker === filter.slice(4);
      const passQ = !query
        || seg.text.toLowerCase().includes(query)
        || seg.speaker.toLowerCase().includes(query)
        || (seg.en && seg.en.toLowerCase().includes(query))
        || (hasStudy && seg.tokens.some(tk => {
          const v = tk.vid >= 0 ? vocabById.get(tk.vid) : null;
          return tk.gloss.toLowerCase().includes(query)
            || (v && (v.kana.includes(query) || v.romaji.toLowerCase().includes(query)));
        }));
      const vis = passF && passQ;
      const color = model.colorOf(seg.speaker);
      const partnerIds = partners.length ? [...new Set(partners.map(p => p.other.index))].join(',') : '';
      let body = '';
      if (seg.words.length) {
        body = seg.words.map((w, wi) => {
          const gap = !hasStudy && wi && !/^[,.;:!?)]/.test(w.word) ? ' ' : '';
          const ovColor = wordOverlapColor(w, info);
          const vAttr = w.vid >= 0 ? ' data-v="' + w.vid + '"' : '';
          const tipAttr = w.gloss ? ' title="' + html(w.word + ' — ' + w.gloss) + '"' : '';
          let span = '<span class="word' + (ovColor ? ' word-ov' : '') + (w.vid >= 0 ? ' has-vocab' : '') + '"'
            + ' data-t="' + w.start + '" data-e="' + w.end + '"' + vAttr + tipAttr;
          if (ovColor) span += ' style="--ov-color:' + ovColor + '"';
          span += '>' + (hasStudy && w.ruby ? rubyHtml(w.ruby, w.word) : html(w.word)) + '</span>';
          const chips = info && info.chipsByWord.get(wi);
          if (chips) span += chips.map(chipHtml).join('');
          return gap + span;
        }).join('');
      } else {
        body = html(seg.text);
      }
      let extraHtml = '';
      if (hasStudy && showGloss && seg.tokens.length) {
        const pairs = seg.tokens.filter(tk => tk.gloss).map(tk =>
          '<span class="ww-pair" data-t="' + tk.start + '"' + (tk.vid >= 0 ? ' data-v="' + tk.vid + '"' : '') + '>'
          + '<span class="ww-ja" lang="ja">' + html(tk.word) + '</span>' + html(tk.gloss) + '</span>'
        ).join('');
        if (pairs) extraHtml += '<div class="segment-ww">' + pairs + '</div>';
      }
      if (hasStudy && showEnglish && seg.en) {
        extraHtml += '<div class="segment-en">' + html(seg.en) + '</div>';
      }
      const ovMark = hasOv ? '<span class="ov-mark">∥</span>' : '';
      return '<div class="segment' + (query && vis ? ' match' : '') + (info && info.overlapsPrev ? ' ov-child' : '') + '"'
        + (vis ? '' : ' hidden') + ' style="--spk-color:' + color.fg + '"'
        + ' data-index="' + seg.index + '" data-start="' + seg.start + '" data-end="' + seg.end + '"'
        + (partnerIds ? ' data-partners="' + partnerIds + '"' : '') + '>'
        + '<span class="segment-time">' + clock(seg.start) + ovMark + '</span>'
        + '<span class="segment-speaker">' + avatarHtml(seg.speaker, 'av-xs') + '<span>' + html(seg.speaker || 'Unassigned') + '</span></span>'
        + '<div class="segment-text"><div' + (hasStudy ? ' class="segment-ja" lang="ja"' : '') + '>' + body + '</div>'
        + extraHtml + '</div></div>';
    }).join('');
    rowByIndex = new Map();
    box.querySelectorAll('.segment').forEach(r => rowByIndex.set(Number(r.dataset.index), r));
    activeKey = '';
    syncAll(true);
  }

  function syncTranscript(time, force) {
    const box = document.getElementById('caption-box');
    const activeIndices = [];
    for (const seg of doc.segments) {
      if (time >= seg.start && time < seg.end) activeIndices.push(seg.index);
    }
    const nextKey = activeIndices.join(',');
    if (force || nextKey !== activeKey) {
      activeKey = nextKey;
      const set = new Set(activeIndices);
      for (const [idx, row] of rowByIndex.entries()) row.classList.toggle('now', set.has(idx));
      if (autoScroll && activeIndices.length && !document.getElementById('transcript-search').value) {
        const first = rowByIndex.get(activeIndices[0]);
        if (first && !first.hidden && box) {
          const top = first.offsetTop - box.offsetTop;
          const viewTop = box.scrollTop;
          const viewBot = viewTop + box.clientHeight;
          if (top < viewTop + 20 || top + first.offsetHeight > viewBot - 20) {
            box.scrollTop = Math.max(0, top - box.clientHeight * 0.25);
          }
        }
      }
    }
    for (const w of activeWordSpans) w.classList.remove('word-now');
    activeWordSpans = [];
    for (const idx of activeIndices) {
      const row = rowByIndex.get(idx);
      if (!row || row.hidden) continue;
      row.querySelectorAll('.word').forEach(w => {
        const ws = Number(w.dataset.t), we = Number(w.dataset.e);
        if (time >= ws && time <= Math.max(we, ws + 0.08)) {
          w.classList.add('word-now');
          activeWordSpans.push(w);
        }
      });
    }
  }

  function syncAll(force) {
    if (!model) return;
    const t = currentTime();
    syncTranscript(t, force);
    if (showOverlay) updateOverlayTimeline(t, force);
    updateLanesPlayhead(t);
    updateLiveAnnotation(t, force);
  }

  function tick() {
    if (!ytPlaying) { rafId = 0; return; }
    const t = currentTime();
    if (showOverlay) updateOverlayTimeline(t, false);
    updateLanesPlayhead(t);
    syncTranscript(t, false);
    updateLiveAnnotation(t, false);
    rafId = requestAnimationFrame(tick);
  }

  function jumpOverlap(dir) {
    if (!model || !model.overlapSpans.length) return;
    const t = currentTime();
    const spans = model.overlapSpans;
    let target = null;
    if (dir > 0) target = spans.find(ov => ov.start > t + 0.5) || spans[0];
    else {
      for (let i = spans.length - 1; i >= 0; i--) {
        if (spans[i].start < t - 1.0) { target = spans[i]; break; }
      }
      if (!target) target = spans[spans.length - 1];
    }
    lanesFollow = true;
    document.getElementById('lanes-follow').setAttribute('aria-pressed', 'true');
    lanesWinStart = clampWinStart(target.start - lanesWindow * 0.25);
    renderLanesWindow(true);
    seekAndPlay(Math.max(0, target.start - 0.3));
  }

  function applySplitLayout() {
    if (isPlayerFullscreen()) {
      if (showOverlay) {
        fitOverlayLayout();
        updateOverlayTimeline(currentTime(), true);
      }
      return;
    }
    const main = document.querySelector('main');
    const pw = document.getElementById('player-wrap');
    const panel = document.getElementById('transcript-panel');
    const btn = document.getElementById('toggle-split-view');
    const active = splitView && window.innerWidth >= 1040;
    main.classList.toggle('split-view', active);
    btn.setAttribute('aria-pressed', String(splitView));
    btn.textContent = splitView ? 'Split' : 'Stacked';
    if (!active) panel.style.height = '';
    else {
      const h = Math.round(pw.getBoundingClientRect().height);
      if (h >= 200) panel.style.height = h + 'px';
    }
    requestAnimationFrame(() => {
      if (active) {
        const h = Math.round(pw.getBoundingClientRect().height);
        if (h >= 200) panel.style.height = h + 'px';
      }
      if (showMinimap) drawMinimap();
      renderLanesWindow(true);
      if (showOverlay) {
        fitOverlayLayout();
        updateOverlayTimeline(currentTime(), true);
      }
    });
  }

  function setHover(indices) {
    const key = indices && indices.length ? [...new Set(indices)].sort((a, b) => a - b).join(',') : '';
    if (key === hoveredKey) return;
    hoveredKey = key;
    hoveredIndices = key ? new Set(indices) : null;
    for (const [idx, row] of rowByIndex.entries()) row.classList.toggle('ov-linked', Boolean(hoveredIndices && hoveredIndices.has(idx)));
    for (const [idx, blks] of blockElsByIndex.entries()) {
      const linked = Boolean(hoveredIndices && hoveredIndices.has(idx));
      for (const blk of blks) blk.classList.toggle('ov-linked', linked);
    }
    const status = document.getElementById('lanes-status');
    if (status && showBadges && hoveredIndices && hoveredIndices.size) {
      const segs = [...hoveredIndices].map(i => model.byIndex.get(i)).filter(Boolean).sort((a, b) => a.start - b.start);
      status.innerHTML = segs.map(s =>
        '<strong style="color:' + model.colorOf(s.speaker).fg + '">' + html(s.speaker) + '</strong>: “' + html(clipText(s.text, 48)) + '”'
      ).join(' <span style="color:var(--gold)">∥</span> ');
    } else {
      paintLanesStatus();
    }
  }

  function annStorageKey() {
    return 'ovpub.annState.' + doc.video_id;
  }
  function loadAnnState() {
    annState = {edits: {}, deleted: [], added: []};
    const raw = localStorage.getItem(annStorageKey());
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object') {
          annState = {
            edits: parsed.edits && typeof parsed.edits === 'object' ? parsed.edits : {},
            deleted: Array.isArray(parsed.deleted) ? parsed.deleted : [],
            added: Array.isArray(parsed.added) ? parsed.added : [],
          };
          return;
        }
      } catch {}
    }
    try {
      const legacy = JSON.parse(localStorage.getItem('ovpub.notes.' + doc.video_id) || '[]');
      if (Array.isArray(legacy) && legacy.length) {
        annState.added = legacy.map((n, i) => ({
          id: 'u' + (i + 1),
          t: Number(n.t) || 0,
          end: Number(n.t) || 0,
          cat: n.tag || 'other',
          title: '',
          note: String(n.note || n.quote || '').trim(),
          quote: String(n.quote || ''),
        }));
      }
    } catch {}
  }
  function saveAnnState() {
    localStorage.setItem(annStorageKey(), JSON.stringify(annState));
    lastLiveAnnId = '';
    populateCategorySelects();
    paintNotes();
    renderLanesWindow(true);
    if (showOverlay) updateOverlayTimeline(currentTime(), true);
    if (showMinimap) drawMinimap();
    updateLiveAnnotation(currentTime(), true);
  }
  function populateCategorySelects() {
    const tagSel = document.getElementById('note-tag');
    if (tagSel) {
      const prev = tagSel.value;
      tagSel.innerHTML = categories.map(c =>
        '<option value="' + html(c.id) + '">' + html(c.label) + '</option>'
      ).join('');
      if (prev && categories.some(c => c.id === prev)) tagSel.value = prev;
    }
    const fltSel = document.getElementById('ann-filter');
    if (fltSel) {
      const all = mergedAnnotations(true);
      const counts = new Map();
      for (const a of all) counts.set(a.cat, (counts.get(a.cat) || 0) + 1);
      const prev = annFilter;
      fltSel.innerHTML = '<option value="all">all (' + all.length + ')</option>'
        + categories.map(c => {
          const n = counts.get(c.id) || 0;
          return '<option value="' + html(c.id) + '">' + html(c.label) + ' (' + n + ')</option>';
        }).join('');
      if (prev !== 'all' && categories.some(c => c.id === prev)) fltSel.value = prev;
      else { annFilter = 'all'; fltSel.value = 'all'; }
    }
  }
  function applyAnnotationsState() {
    const btn = document.getElementById('toggle-annotations');
    if (btn) {
      btn.setAttribute('aria-pressed', String(showAnnotations));
      const count = mergedAnnotations(true).length;
      btn.title = 'Show or hide ' + count + ' editable behavior annotations on timeline and page';
    }
    const bar = document.getElementById('ann-filter-bar');
    if (bar) bar.hidden = !showAnnotations;
    lastLiveAnnId = '';
    populateCategorySelects();
    paintNotes();
    renderLanesWindow(true);
    if (showOverlay) updateOverlayTimeline(currentTime(), true);
    if (showMinimap) drawMinimap();
    updateLiveAnnotation(currentTime(), true);
  }
  function updateLiveAnnotation(time, force) {
    const el = document.getElementById('ann-live');
    if (!el) return;
    if (!showAnnotations) {
      if (lastLiveAnnId !== '__off__') {
        el.hidden = true;
        el.innerHTML = '';
        lastLiveAnnId = '__off__';
        document.querySelectorAll('#notes-list .note-row.ann-active')
          .forEach(r => r.classList.remove('ann-active'));
      }
      return;
    }
    const vis = visibleTimelineAnnotations();
    let active = null;
    for (const a of vis) {
      const end = Math.max(Number(a.end) || a.t, a.t + 5);
      if (time >= a.t - 1.0 && time <= end + 1.0) {
        if (!active || a.t >= active.t) active = a;
      }
    }
    const key = active ? active.id : '';
    if (!force && key === lastLiveAnnId) return;
    lastLiveAnnId = key;
    document.querySelectorAll('#notes-list .note-row').forEach(r => {
      r.classList.toggle('ann-active', Boolean(active && r.dataset.annId === active.id));
    });
    if (!active) {
      el.hidden = true;
      el.innerHTML = '';
      return;
    }
    const ci = catInfo(active.cat);
    el.style.setProperty('--cat-color', ci.color);
    el.hidden = false;
    const spanTxt = active.end && active.end > active.t
      ? clock(active.t) + '–' + clock(active.end)
      : clock(active.t);
    el.innerHTML =
      '<span class="ann-live-cat">' + html(ci.label) + '</span>'
      + '<span class="ann-live-title">' + html(active.title || spanTxt) + '</span>'
      + '<span class="ann-live-note">' + html(active.note || active.quote || '') + '</span>'
      + '<button type="button" data-edit-id="' + html(active.id) + '">Edit</button>';
  }
  function paintNotes() {
    const list = document.getElementById('notes-list');
    const resetBtn = document.getElementById('reset-ann-btn');
    if (resetBtn) {
      const hasMods = Object.keys(annState.edits || {}).length > 0 || (annState.deleted || []).length > 0;
      resetBtn.hidden = !hasMods;
    }
    const items = showAnnotations ? visibleTimelineAnnotations() : mergedAnnotations(false);
    if (!items.length) {
      list.innerHTML = '';
      return;
    }
    list.innerHTML = items.map(n => {
      const ci = catInfo(n.cat);
      const isEditing = editingAnnId === n.id;
      const isActive = showAnnotations && lastLiveAnnId === n.id;
      const spanTxt = n.end && n.end > n.t ? clock(n.t) + '–' + clock(n.end) : clock(n.t);
      const srcBadge = n._origin === 'ai' ? 'AI' : n._origin === 'edited' ? 'edited' : 'yours';
      let editForm = '';
      if (isEditing) {
        const endClk = n.end && n.end > n.t ? clock(n.end) : '';
        const catOpts = categories.map(c =>
          '<option value="' + html(c.id) + '"' + (c.id === n.cat ? ' selected' : '') + '>' + html(c.label) + '</option>'
        ).join('') + (catMap.has(n.cat) ? '' : '<option value="' + html(n.cat) + '" selected>' + html(n.cat) + '</option>');
        editForm = '<div class="note-edit-form" data-edit-for="' + html(n.id) + '">'
          + '<div class="note-edit-row">'
          + '<input type="text" class="ed-time ed-start" value="' + html(clock(n.t))
          + '" data-orig-clock="' + html(clock(n.t)) + '" data-orig-val="' + n.t + '" placeholder="start" title="Start time (m:ss or h:mm:ss)">'
          + '<input type="text" class="ed-time ed-end" value="' + html(endClk)
          + '" data-orig-clock="' + html(endClk) + '" data-orig-val="' + (n.end || '') + '" placeholder="end" title="Optional end time">'
          + '<select class="ed-cat">' + catOpts + '</select>'
          + '<input type="text" class="ed-title" value="' + html(n.title || '') + '" placeholder="short title…">'
          + '</div>'
          + '<textarea class="ed-note" placeholder="note…">' + html(n.note || n.quote || '') + '</textarea>'
          + '<div class="note-edit-row">'
          + '<button type="button" class="ed-save">Save</button>'
          + '<button type="button" class="ed-cancel">Cancel</button>'
          + '</div></div>';
      }
      return '<div class="note-row' + (isActive ? ' ann-active' : '') + '"'
        + ' data-ann-id="' + html(n.id) + '" style="--cat-color:' + ci.color + '">'
        + '<span class="nt-time" data-t="' + n.t + '">' + html(spanTxt) + '</span>'
        + '<span class="nt-tag" title="' + html(ci.label) + '">' + html(ci.label) + '</span>'
        + '<span class="nt-body">'
        + (n.title ? '<span class="nt-title">' + html(n.title) + '</span>' : '')
        + html(n.note || n.quote || '')
        + '<span class="nt-src">' + srcBadge + '</span></span>'
        + '<span class="nt-btns">'
        + '<button type="button" class="nt-edit" data-edit="' + html(n.id) + '">Edit</button>'
        + '<button type="button" class="nt-del" data-del="' + html(n.id) + '" title="Remove annotation">×</button>'
        + '</span>'
        + editForm + '</div>';
    }).join('');
  }
  function openAnnotationEditor(id) {
    editingAnnId = id;
    paintNotes();
    const row = document.querySelector('#notes-list .note-row[data-ann-id="' + CSS.escape(id) + '"]');
    if (row) {
      row.scrollIntoView({behavior: 'smooth', block: 'nearest'});
      const focusEl = row.querySelector('.ed-note') || row.querySelector('.ed-title');
      if (focusEl) focusEl.focus();
    }
  }
  function saveAnnotationEdit(form) {
    const id = form.dataset.editFor;
    const startIn = form.querySelector('.ed-start');
    const endIn = form.querySelector('.ed-end');
    const cat = form.querySelector('.ed-cat').value;
    const title = form.querySelector('.ed-title').value.trim();
    const note = form.querySelector('.ed-note').value.trim();
    let t = Number(startIn.dataset.origVal) || 0;
    if (startIn.value.trim() !== startIn.dataset.origClock) {
      const parsed = parseClockInput(startIn.value);
      if (Number.isFinite(parsed)) t = Math.round(parsed * 10) / 10;
    }
    let end = 0;
    if (endIn.value.trim() === '') {
      end = 0;
    } else if (endIn.value.trim() === endIn.dataset.origClock && endIn.dataset.origVal) {
      end = Number(endIn.dataset.origVal) || 0;
    } else {
      const parsed = parseClockInput(endIn.value);
      if (Number.isFinite(parsed) && parsed > t) end = Math.round(parsed * 10) / 10;
    }
    const addedIdx = (annState.added || []).findIndex(a => a.id === id);
    if (addedIdx !== -1) {
      annState.added[addedIdx] = {...annState.added[addedIdx], t, end, cat, title, note};
    } else {
      annState.edits[id] = {t, end, cat, title, note};
    }
    editingAnnId = null;
    saveAnnState();
  }
  function deleteAnnotation(id) {
    const addedIdx = (annState.added || []).findIndex(a => a.id === id);
    if (addedIdx !== -1) {
      annState.added.splice(addedIdx, 1);
    } else {
      if (!annState.deleted.includes(id)) annState.deleted.push(id);
      delete annState.edits[id];
    }
    if (editingAnnId === id) editingAnnId = null;
    saveAnnState();
  }
  function addMomentNote() {
    const t = Math.round(currentTime() * 10) / 10;
    const cat = document.getElementById('note-tag').value || 'other';
    const titleIn = document.getElementById('note-title');
    const textIn = document.getElementById('note-text');
    const active = doc.segments.filter(s => t >= s.start - 0.5 && t <= s.end + 0.5)
      .map(s => s.speaker + ': "' + clipText(s.text, 60) + '"').join(' // ');
    const id = 'u' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
    annState.added.push({
      id, t, end: 0, cat,
      title: titleIn ? titleIn.value.trim() : '',
      note: textIn.value.trim(),
      quote: active,
    });
    if (titleIn) titleIn.value = '';
    textIn.value = '';
    saveAnnState();
  }
  function jumpAnnotation(dir) {
    const items = visibleTimelineAnnotations();
    if (!items.length) return;
    const t = currentTime();
    let target = null;
    if (dir > 0) target = items.find(a => a.t > t + 0.5) || items[0];
    else {
      for (let i = items.length - 1; i >= 0; i--) {
        if (items[i].t < t - 1.0) { target = items[i]; break; }
      }
      if (!target) target = items[items.length - 1];
    }
    lanesFollow = true;
    document.getElementById('lanes-follow').setAttribute('aria-pressed', 'true');
    lanesWinStart = clampWinStart(target.t - lanesWindow * 0.25);
    renderLanesWindow(true);
    seekAndPlay(Math.max(0, target.t - 0.3));
  }
  async function exportAnnotationsJson() {
    const payload = {
      version: 1,
      video_id: doc.video_id,
      categories,
      items: mergedAnnotations(true).map(a => ({
        id: a.id,
        t: a.t,
        ...(a.end && a.end > a.t ? {end: a.end} : {}),
        cat: a.cat,
        ...(a.title ? {title: a.title} : {}),
        note: a.note || a.quote || '',
        source: a._origin === 'ai' ? 'ai' : a._origin === 'edited' ? 'edited' : 'user',
      })),
    };
    const jsonText = JSON.stringify(payload, null, 2) + '\n';
    try { await navigator.clipboard.writeText(jsonText); } catch {}
    try {
      const blob = new Blob([jsonText], {type: 'application/json'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'annotations-' + doc.video_id + '.json';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {}
    const btn = document.getElementById('export-ann-btn');
    if (btn) {
      btn.textContent = 'Exported ' + payload.items.length;
      setTimeout(() => { btn.textContent = 'Export JSON'; }, 1500);
    }
  }

  function applyFuriganaState() {
    document.documentElement.classList.toggle('no-furigana', !showFurigana);
    const btn = document.getElementById('toggle-furigana');
    if (btn) btn.setAttribute('aria-pressed', String(showFurigana));
    if (model) {
      renderLanesWindow(true);
      if (showOverlay) updateOverlayTimeline(currentTime(), true);
    }
  }
  function applyStudyLayersState() {
    const gBtn = document.getElementById('toggle-gloss');
    const eBtn = document.getElementById('toggle-english');
    if (gBtn) gBtn.setAttribute('aria-pressed', String(showGloss));
    if (eBtn) eBtn.setAttribute('aria-pressed', String(showEnglish));
    if (model) {
      paintTranscript();
      renderLanesWindow(true);
      if (showOverlay) updateOverlayTimeline(currentTime(), true);
    }
  }
  function showWordInspector(vid) {
    const el = document.getElementById('word-inspector');
    if (!el) return;
    if (vid == null || vid < 0 || !vocabById.has(vid)) {
      inspectedVocabId = null;
      el.hidden = true;
      el.innerHTML = '';
      return;
    }
    const v = vocabById.get(vid);
    inspectedVocabId = vid;
    const isKnown = knownVocab.has(vocabKey(v));
    const reading = [v.kana && v.kana !== v.base ? v.kana : '', v.romaji].filter(Boolean).join(' · ');
    const diffLbl = DIFF_LABELS[v.diff] || v.diff;
    el.hidden = false;
    el.innerHTML =
      '<span class="wi-ja" lang="ja">' + (v.ruby ? rubyHtml(v.ruby, v.base) : html(v.base)) + '</span>'
      + (reading ? '<span class="wi-read">' + html(reading) + '</span>' : '')
      + '<span class="v-diff vd-' + html(v.diff) + '">' + html(diffLbl) + '</span>'
      + '<span class="wi-gloss">' + html(v.gloss) + '</span>'
      + (v.role ? '<span class="wi-role">(' + html(v.role) + ')</span>' : '')
      + (v.note ? '<span class="wi-note">' + html(v.note) + '</span>' : '')
      + '<span class="wi-btns">'
      + '<button type="button" class="wi-known" data-vid="' + v.id + '" aria-pressed="' + String(isKnown) + '">'
      + (isKnown ? 'Known ✓' : 'Mark known') + '</button>'
      + '<button type="button" class="wi-close" title="Close">×</button>'
      + '</span>';
  }
  function vocabMatchesDiff(diff, filter) {
    if (filter === 'all') return true;
    if (filter === 'mtg') return diff === 'mtg';
    if (filter === 'slang') return diff === 'slang';
    if (filter === 'n1+') return diff === 'n1' || diff === 'slang' || diff === 'mtg';
    if (filter === 'n2+') return diff === 'n2' || diff === 'n1' || diff === 'slang' || diff === 'mtg';
    return diff !== 'basic'; // n3+ default
  }
  function paintVocab() {
    const panel = document.getElementById('vocab-panel');
    if (!panel || !doc || !doc.vocab || !doc.vocab.length) return;
    panel.hidden = false;
    const q = (document.getElementById('vocab-search')?.value || '').trim().toLowerCase();
    const listEl = document.getElementById('vocab-list');
    const countEl = document.getElementById('vocab-count');
    const hideBtn = document.getElementById('vocab-hide-known');
    const resetBtn = document.getElementById('vocab-reset-known');

    let knownInDoc = 0;
    for (const v of doc.vocab) if (knownVocab.has(vocabKey(v))) knownInDoc += 1;
    if (hideBtn) {
      hideBtn.setAttribute('aria-pressed', String(hideKnown));
      hideBtn.textContent = knownInDoc ? 'Hide known (' + knownInDoc + ')' : 'Hide known';
    }
    if (resetBtn) resetBtn.hidden = knownInDoc === 0;

    let items = doc.vocab.filter(v => {
      if (!v.base || !v.gloss) return false;
      if (!vocabMatchesDiff(v.diff, vocabDiff)) return false;
      if (hideKnown && knownVocab.has(vocabKey(v))) return false;
      if (q) {
        const hay = (v.base + ' ' + v.kana + ' ' + v.romaji + ' ' + v.gloss + ' ' + v.note).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    if (vocabSort === 'freq') {
      items.sort((a, b) => (b.count - a.count) || (a.id - b.id));
    } else if (vocabSort === 'hard') {
      items.sort((a, b) => ((DIFF_ORDER[b.diff] || 0) - (DIFF_ORDER[a.diff] || 0)) || (b.count - a.count));
    } else {
      items.sort((a, b) => ((a.times[0] || 0) - (b.times[0] || 0)) || (a.id - b.id));
    }
    if (countEl) countEl.textContent = items.length + ' / ' + doc.vocab.length;
    if (!items.length) {
      listEl.innerHTML = '<div style="padding:.4rem;font-size:.86rem">No vocabulary items match this filter.</div>';
      return;
    }
    listEl.innerHTML = items.map(v => {
      const isKnown = knownVocab.has(vocabKey(v));
      const reading = [v.kana && v.kana !== v.base ? v.kana : '', v.romaji].filter(Boolean).join(' · ');
      const diffLbl = DIFF_LABELS[v.diff] || v.diff;
      const jumps = v.times.slice(0, 5).map(t =>
        '<button type="button" class="v-time" data-t="' + t + '" title="Jump to ' + clock(t) + '">' + clock(t) + '</button>'
      ).join('') + (v.times.length > 5 ? '<span class="v-read">+' + (v.times.length - 5) + '</span>' : '');
      return '<div class="vocab-row' + (isKnown ? ' is-known' : '') + '" data-vid="' + v.id + '">'
        + '<div class="v-word"><span class="v-ja" lang="ja">'
        + (v.ruby ? rubyHtml(v.ruby, v.base) : html(v.base)) + '</span>'
        + (reading ? '<span class="v-read">' + html(reading) + '</span>' : '')
        + '</div>'
        + '<span class="v-diff vd-' + html(v.diff) + '">' + html(diffLbl) + '</span>'
        + '<div class="v-meaning"><span class="v-gloss">' + html(v.gloss) + '</span>'
        + (v.role ? '<span class="v-role">' + html(v.role) + '</span>' : '')
        + (v.note ? '<span class="v-note">' + html(v.note) + '</span>' : '')
        + '</div>'
        + '<div class="v-times">' + jumps + '</div>'
        + '<button type="button" class="v-known" data-vid="' + v.id + '" aria-pressed="' + String(isKnown)
        + '" title="Mark word as already known">' + (isKnown ? 'Known ✓' : 'Know ✓') + '</button>'
        + '</div>';
    }).join('');
  }

  async function init() {
    const res = await fetch('transcript.json');
    if (!res.ok) throw new Error('Could not load transcript.json');
    doc = expandCompact(await res.json());
    hasStudy = Boolean(doc.language === 'ja' || (doc.vocab && doc.vocab.length) || doc.segments.some(s => s.en || s.tokens.length));
    vocabById = new Map((doc.vocab || []).map(v => [v.id, v]));
    loadKnownVocab();
    model = buildOverlapModel(doc);
    mainRows = loadSurfaceRows('main');
    overlayRows = loadSurfaceRows('overlay');

    const annUrl = document.querySelector('main')?.dataset.annotations || '';
    if (annUrl) {
      try {
        const annRes = await fetch(annUrl);
        if (annRes.ok) {
          const annDoc = await annRes.json();
          if (Array.isArray(annDoc.categories) && annDoc.categories.length) {
            categories = annDoc.categories;
            catMap = new Map(categories.map(c => [c.id, c]));
          }
          if (Array.isArray(annDoc.items)) {
            seedAnnotations = annDoc.items.map((it, idx) => ({
              id: String(it.id || ('s' + (idx + 1))),
              t: Number(it.t) || 0,
              end: Number(it.end) || 0,
              cat: String(it.cat || 'other'),
              title: String(it.title || ''),
              note: String(it.note || ''),
              quote: String(it.quote || ''),
            }));
          }
        }
      } catch {}
    }

    document.title = doc.title;
    document.getElementById('vid-title').textContent = doc.title;
    const ytLink = document.getElementById('yt-orig-link');
    ytLink.href = 'https://www.youtube.com/watch?v=' + encodeURIComponent(doc.video_id);

    if (doc.chapters.length) {
      const chEl = document.getElementById('chapters');
      chEl.hidden = false;
      chEl.innerHTML = doc.chapters.map(c =>
        '<button type="button" data-t="' + c.s + '">' + clock(c.s) + ' ' + html(c.t) + '</button>'
      ).join('');
      chEl.addEventListener('click', ev => {
        const b = ev.target.closest('button[data-t]');
        if (b) seekAndPlay(Number(b.dataset.t));
      });
    }

    const filterSel = document.getElementById('transcript-filter');
    filterSel.innerHTML = '<option value="all">all</option>'
      + '<option value="overlaps">overlaps (' + model.overlapSpans.length + ')</option>'
      + model.speakers.map(s => '<option value="spk:' + html(s) + '">' + html(s) + ' (' + model.speakerCounts.get(s) + ')</option>').join('');

    const winSel = document.getElementById('lanes-window');
    if (hasStudy && !localStorage.getItem('ovpub.window')) lanesWindow = 10;
    if (![...winSel.options].some(o => Number(o.value) === lanesWindow)) lanesWindow = hasStudy ? 10 : 20;
    winSel.value = String(lanesWindow);
    document.getElementById('toggle-autoscroll').setAttribute('aria-pressed', String(autoScroll));

    if (hasStudy) {
      for (const id of ['toggle-furigana', 'toggle-gloss', 'toggle-english']) {
        const b = document.getElementById(id);
        if (b) b.hidden = false;
      }
      const vDiffSel = document.getElementById('vocab-diff');
      if (vDiffSel) vDiffSel.value = vocabDiff;
      applyFuriganaState();
      const gBtn = document.getElementById('toggle-gloss');
      const eBtn = document.getElementById('toggle-english');
      if (gBtn) gBtn.setAttribute('aria-pressed', String(showGloss));
      if (eBtn) eBtn.setAttribute('aria-pressed', String(showEnglish));
    }

    loadAnnState();
    showAnnotations = seedAnnotations.length > 0
      && localStorage.getItem('ovpub.annotations.' + doc.video_id) === 'true';

    setupYouTube(doc.video_id);
    paintTranscript();
    applyOverlayState();
    applyMinimapState();
    applyBadgesState();
    applyAnnotationsState();
    applySplitLayout();
    if (hasStudy) paintVocab();

    const ovEl = document.getElementById('ov-overlay');
    if (ovEl) {
      ovEl.addEventListener('click', ev => {
        ev.stopPropagation();
        closeStreamMenu();
        if (ev.target.closest('#ov-drag-handle')) return;
        const pin = ev.target.closest('.ruler-note-pin');
        if (pin) { seekAndPlay(Number(pin.dataset.t)); return; }
        const lw = ev.target.closest('.lane-word[data-v]');
        if (lw) showWordInspector(Number(lw.dataset.v));
        const trk = ev.target.closest('.ov-track, .ov-ruler');
        if (!trk || !model) return;
        const rect = trk.getBoundingClientRect();
        if (rect.width <= 0) return;
        const frac = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
        const winDur = Math.min(OV_WINDOW_SEC, Math.max(4, lanesWindow));
        seekAndPlay(ovWinStart + frac * winDur);
      });
      ovEl.addEventListener('contextmenu', ev => showStreamMenu(ev, 'overlay'));
    }

    const ovDragHandle = document.getElementById('ov-drag-handle');
    if (ovDragHandle) {
      let ovPosDrag = null;
      ovDragHandle.addEventListener('pointerdown', ev => {
        if (ev.button !== 0 || ev.target.closest('button')) return;
        ev.stopPropagation();
        ev.preventDefault();
        closeStreamMenu();
        ovPosDrag = {startY: ev.clientY, startBottom: getOvBottom()};
        try { ovDragHandle.setPointerCapture(ev.pointerId); } catch {}
      });
      ovDragHandle.addEventListener('pointermove', ev => {
        if (!ovPosDrag) return;
        const dy = ovPosDrag.startY - ev.clientY;
        setOvBottom(ovPosDrag.startBottom + dy, false);
      });
      const finishOvPos = () => {
        if (!ovPosDrag) return;
        ovPosDrag = null;
        setOvBottom(getOvBottom(), true);
      };
      ovDragHandle.addEventListener('pointerup', finishOvPos);
      ovDragHandle.addEventListener('pointercancel', finishOvPos);
    }
    const ovFsBtn = document.getElementById('ov-fs-btn');
    if (ovFsBtn) {
      ovFsBtn.addEventListener('click', ev => {
        ev.stopPropagation();
        togglePlayerFullscreen();
      });
    }
    const fsBtn = document.getElementById('toggle-fullscreen');
    if (fsBtn) fsBtn.onclick = togglePlayerFullscreen;
    document.addEventListener('fullscreenchange', onFullscreenChange);
    document.addEventListener('webkitfullscreenchange', onFullscreenChange);
    syncFullscreenButtons();

    function setupRowDragReorder(stageEl, surface, labelSel, onLabelClick) {
      if (!stageEl) return;
      let rd = null;
      const clearCls = () => {
        stageEl.querySelectorAll('.is-dragging, .drag-over-top, .drag-over-bot')
          .forEach(el => el.classList.remove('is-dragging', 'drag-over-top', 'drag-over-bot'));
      };
      stageEl.addEventListener('pointerdown', ev => {
        if (ev.button !== 0) return;
        const lbl = ev.target.closest(labelSel);
        if (!lbl) return;
        const rowEl = lbl.closest('[data-row-idx]');
        if (!rowEl) return;
        const fromIdx = Number(rowEl.dataset.rowIdx);
        if (!Number.isInteger(fromIdx)) return;
        ev.stopPropagation();
        rd = {fromIdx, targetIdx: fromIdx, placeAfter: false, startY: ev.clientY, moved: false, rowEl, lbl};
        try { lbl.setPointerCapture(ev.pointerId); } catch {}
      });
      stageEl.addEventListener('pointermove', ev => {
        if (!rd) return;
        if (!rd.moved && Math.abs(ev.clientY - rd.startY) >= 5) {
          rd.moved = true;
          rd.rowEl.classList.add('is-dragging');
          closeStreamMenu();
        }
        if (!rd.moved) return;
        const rowEls = [...stageEl.querySelectorAll('[data-row-idx]')];
        for (const el of rowEls) el.classList.remove('drag-over-top', 'drag-over-bot');
        let overEl = rowEls[0];
        let placeAfter = false;
        for (const el of rowEls) {
          const r = el.getBoundingClientRect();
          if (ev.clientY < r.top + r.height / 2) { overEl = el; placeAfter = false; break; }
          overEl = el;
          placeAfter = true;
        }
        if (overEl) {
          const overIdx = Number(overEl.dataset.rowIdx);
          rd.targetIdx = overIdx;
          rd.placeAfter = placeAfter;
          if (overIdx !== rd.fromIdx) {
            overEl.classList.add(placeAfter ? 'drag-over-bot' : 'drag-over-top');
          }
        }
      });
      const finish = ev => {
        if (!rd) return;
        const d = rd;
        rd = null;
        clearCls();
        if (d.moved) {
          const ins = d.placeAfter ? d.targetIdx + 1 : d.targetIdx;
          const toIdx = ins > d.fromIdx ? ins - 1 : ins;
          moveRowOnSurface(surface, d.fromIdx, toIdx);
        } else if (ev && ev.type === 'pointerup' && onLabelClick) {
          onLabelClick(d.lbl);
        }
      };
      stageEl.addEventListener('pointerup', finish);
      stageEl.addEventListener('pointercancel', () => finish(null));
    }

    setupRowDragReorder(ovEl, 'overlay', '.ov-label', null);

    const lanesStageCtx = document.getElementById('lanes-stage');
    if (lanesStageCtx) {
      lanesStageCtx.addEventListener('contextmenu', ev => showStreamMenu(ev, 'main'));
      setupRowDragReorder(lanesStageCtx, 'main', '.lane-label', lbl => {
        if (!lbl.dataset.speaker) return;
        const val = 'spk:' + lbl.dataset.speaker;
        filterSel.value = filterSel.value === val ? 'all' : val;
        paintTranscript();
      });
    }
    const ctxMenu = document.getElementById('stream-ctx-menu');
    if (ctxMenu) {
      ctxMenu.addEventListener('click', ev => {
        ev.stopPropagation();
        const btn = ev.target.closest('button[data-act]');
        if (!btn || btn.disabled || !ctxMenuState) return;
        const {surface, hitIdx} = ctxMenuState;
        const act = btn.dataset.act;
        if (act === 'move-up' && hitIdx > 0) {
          moveRowOnSurface(surface, hitIdx, hitIdx - 1);
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'move-down' && hitIdx >= 0) {
          moveRowOnSurface(surface, hitIdx, hitIdx + 1);
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'move-top' && hitIdx > 0) {
          moveRowOnSurface(surface, hitIdx, 0);
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'move-bot' && hitIdx >= 0) {
          moveRowOnSurface(surface, hitIdx, getSurfaceRows(surface).length - 1);
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'grp-spk') {
          groupRowsBySpeaker(surface);
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'grp-stream') {
          groupRowsByStream(surface);
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'ov-up') {
          setOvBottom(getOvBottom() + 24, true);
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'ov-down') {
          setOvBottom(getOvBottom() - 24, true);
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'ov-font') {
          setOvFontScale(Number(btn.dataset.val));
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'ov-alpha') {
          setOvAlpha(Number(btn.dataset.val));
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'toggle-fs') {
          closeStreamMenu();
          togglePlayerFullscreen();
          return;
        }
        if (act === 'add-one') {
          addRowToSurface(surface, btn.dataset.spk, btn.dataset.kind);
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'toggle-kind') {
          toggleKindForAllActive(surface, btn.dataset.kind);
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'toggle-spk') {
          toggleSpeakerOnSurface(surface, btn.dataset.spk);
          renderStreamMenu(ctxMenuState.x, ctxMenuState.y);
          return;
        }
        if (act === 'rm-one') {
          removeRowFromSurface(surface, btn.dataset.spk, btn.dataset.kind);
        } else if (act === 'reset') {
          if (surface === 'overlay') {
            ovBottomWin = DEFAULT_OV_BOTTOM_WIN;
            ovBottomFs = DEFAULT_OV_BOTTOM_FS;
            ovFontScale = 1;
            ovAlpha = 0.88;
            localStorage.removeItem('ovpub.ovBottomWin');
            localStorage.removeItem('ovpub.ovBottomFs');
            localStorage.removeItem('ovpub.ovFont');
            localStorage.removeItem('ovpub.ovAlpha');
          }
          setSurfaceRows(surface, defaultRowsForSurface(surface));
        }
        closeStreamMenu();
      });
      ctxMenu.addEventListener('contextmenu', ev => ev.preventDefault());
    }
    document.addEventListener('click', closeStreamMenu);
    document.addEventListener('keydown', ev => {
      if (ev.key === 'Escape') { closeStreamMenu(); return; }
      if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
      const tag = ev.target && ev.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (ev.target && ev.target.isContentEditable)) return;
      if (ev.key === 'f' || ev.key === 'F') {
        ev.preventDefault();
        togglePlayerFullscreen();
      } else if (ev.key === 'o' || ev.key === 'O') {
        ev.preventDefault();
        autoOverlayForFs = false;
        showOverlay = !showOverlay;
        localStorage.setItem('ovpub.overlay', String(showOverlay));
        applyOverlayState();
      }
    });

    document.getElementById('transcript-search').addEventListener('input', paintTranscript);
    filterSel.addEventListener('change', paintTranscript);
    winSel.addEventListener('change', () => {
      lanesWindow = Number(winSel.value) || 20;
      localStorage.setItem('ovpub.window', String(lanesWindow));
      lanesWinStart = clampWinStart(currentTime() - lanesWindow * 0.25);
      renderLanesWindow(true);
      if (showOverlay) updateOverlayTimeline(currentTime(), true);
    });
    document.getElementById('lanes-follow').onclick = () => {
      lanesFollow = !lanesFollow;
      document.getElementById('lanes-follow').setAttribute('aria-pressed', String(lanesFollow));
      if (lanesFollow) updateLanesPlayhead(currentTime());
    };
    document.getElementById('prev-overlap').onclick = () => jumpOverlap(-1);
    document.getElementById('next-overlap').onclick = () => jumpOverlap(1);
    const furiBtn = document.getElementById('toggle-furigana');
    if (furiBtn) {
      furiBtn.onclick = () => {
        showFurigana = !showFurigana;
        localStorage.setItem('ovpub.furigana', String(showFurigana));
        if (hasStudy && model) {
          const from = showFurigana ? 'ja' : 'ja-furi';
          const to = showFurigana ? 'ja-furi' : 'ja';
          const next = [];
          const seen = new Set();
          for (const r of mainRows) {
            const k = r.kind === from ? to : r.kind;
            const key = r.spk + '\0' + k;
            if (!seen.has(key)) { seen.add(key); next.push({spk: r.spk, kind: k}); }
          }
          setSurfaceRows('main', next);
        }
        applyFuriganaState();
      };
    }
    const glossBtn = document.getElementById('toggle-gloss');
    if (glossBtn) {
      glossBtn.onclick = () => {
        showGloss = !showGloss;
        localStorage.setItem('ovpub.gloss', String(showGloss));
        if (hasStudy && model) {
          if (showGloss) addKindForActive('main', 'literal');
          else setSurfaceRows('main', mainRows.filter(r => r.kind !== 'literal'));
        }
        applyStudyLayersState();
      };
    }
    const engBtn = document.getElementById('toggle-english');
    if (engBtn) {
      engBtn.onclick = () => {
        showEnglish = !showEnglish;
        localStorage.setItem('ovpub.english', String(showEnglish));
        if (hasStudy && model) {
          if (showEnglish) addKindForActive('main', 'en');
          else setSurfaceRows('main', mainRows.filter(r => r.kind !== 'en'));
        }
        applyStudyLayersState();
      };
    }
    document.getElementById('toggle-overlay').onclick = () => {
      autoOverlayForFs = false;
      showOverlay = !showOverlay;
      localStorage.setItem('ovpub.overlay', String(showOverlay));
      applyOverlayState();
    };
    document.getElementById('toggle-minimap').onclick = () => {
      showMinimap = !showMinimap;
      localStorage.setItem('ovpub.minimap', String(showMinimap));
      applyMinimapState();
    };
    document.getElementById('toggle-badges').onclick = () => {
      showBadges = !showBadges;
      localStorage.setItem('ovpub.badges', String(showBadges));
      applyBadgesState();
    };
    const annBtn = document.getElementById('toggle-annotations');
    if (annBtn) {
      annBtn.onclick = () => {
        showAnnotations = !showAnnotations;
        localStorage.setItem('ovpub.annotations.' + doc.video_id, String(showAnnotations));
        applyAnnotationsState();
      };
    }
    document.getElementById('toggle-split-view').onclick = () => {
      splitView = !splitView;
      localStorage.setItem('ovpub.split', String(splitView));
      applySplitLayout();
    };
    document.getElementById('toggle-autoscroll').onclick = () => {
      autoScroll = !autoScroll;
      localStorage.setItem('ovpub.autoScroll', String(autoScroll));
      document.getElementById('toggle-autoscroll').setAttribute('aria-pressed', String(autoScroll));
    };
    document.getElementById('copy-link-btn').onclick = async () => {
      const t = Math.floor(currentTime());
      const url = location.origin + location.pathname + '#t=' + t;
      history.replaceState(null, '', '#t=' + t);
      try { await navigator.clipboard.writeText(url); } catch {}
      const btn = document.getElementById('copy-link-btn');
      btn.textContent = 'Copied ' + clock(t);
      setTimeout(() => { btn.textContent = 'Copy link @ time'; }, 1400);
    };

    const wiEl = document.getElementById('word-inspector');
    if (wiEl) {
      wiEl.addEventListener('click', ev => {
        if (ev.target.closest('.wi-close')) { showWordInspector(null); return; }
        const kb = ev.target.closest('.wi-known');
        if (kb) {
          const v = vocabById.get(Number(kb.dataset.vid));
          if (v) {
            const k = vocabKey(v);
            if (knownVocab.has(k)) knownVocab.delete(k); else knownVocab.add(k);
            saveKnownVocab();
            showWordInspector(v.id);
            paintVocab();
          }
        }
      });
    }
    const vDiffEl = document.getElementById('vocab-diff');
    if (vDiffEl) {
      vDiffEl.addEventListener('change', () => {
        vocabDiff = vDiffEl.value || 'n3+';
        localStorage.setItem('ovpub.vocabDiff', vocabDiff);
        paintVocab();
      });
    }
    const vSearchEl = document.getElementById('vocab-search');
    if (vSearchEl) vSearchEl.addEventListener('input', paintVocab);
    const vSortEl = document.getElementById('vocab-sort');
    if (vSortEl) vSortEl.addEventListener('change', () => { vocabSort = vSortEl.value || 'first'; paintVocab(); });
    const vHideBtn = document.getElementById('vocab-hide-known');
    if (vHideBtn) {
      vHideBtn.onclick = () => {
        hideKnown = !hideKnown;
        localStorage.setItem('ovpub.hideKnown', String(hideKnown));
        paintVocab();
      };
    }
    const vResetBtn = document.getElementById('vocab-reset-known');
    if (vResetBtn) {
      vResetBtn.onclick = () => {
        knownVocab.clear();
        saveKnownVocab();
        if (inspectedVocabId != null) showWordInspector(inspectedVocabId);
        paintVocab();
      };
    }
    const vListEl = document.getElementById('vocab-list');
    if (vListEl) {
      vListEl.addEventListener('click', ev => {
        const tm = ev.target.closest('.v-time');
        if (tm) { seekAndPlay(Number(tm.dataset.t)); return; }
        const kb = ev.target.closest('.v-known');
        if (kb) {
          const v = vocabById.get(Number(kb.dataset.vid));
          if (v) {
            const k = vocabKey(v);
            if (knownVocab.has(k)) knownVocab.delete(k); else knownVocab.add(k);
            saveKnownVocab();
            if (inspectedVocabId === v.id) showWordInspector(v.id);
            paintVocab();
          }
          return;
        }
        const row = ev.target.closest('.vocab-row');
        if (row) showWordInspector(Number(row.dataset.vid));
      });
    }

    document.getElementById('add-note-btn').onclick = addMomentNote;
    document.getElementById('note-text').addEventListener('keydown', ev => {
      if (ev.key === 'Enter') { ev.preventDefault(); addMomentNote(); }
    });
    const noteTitleEl = document.getElementById('note-title');
    if (noteTitleEl) {
      noteTitleEl.addEventListener('keydown', ev => {
        if (ev.key === 'Enter') { ev.preventDefault(); addMomentNote(); }
      });
    }
    document.getElementById('ann-filter').addEventListener('change', ev => {
      annFilter = ev.target.value || 'all';
      lastLiveAnnId = '';
      paintNotes();
      renderLanesWindow(true);
      if (showOverlay) updateOverlayTimeline(currentTime(), true);
      if (showMinimap) drawMinimap();
      updateLiveAnnotation(currentTime(), true);
    });
    document.getElementById('prev-ann').onclick = () => jumpAnnotation(-1);
    document.getElementById('next-ann').onclick = () => jumpAnnotation(1);
    document.getElementById('export-ann-btn').onclick = exportAnnotationsJson;
    document.getElementById('reset-ann-btn').onclick = () => {
      annState.edits = {};
      annState.deleted = [];
      editingAnnId = null;
      saveAnnState();
    };
    document.getElementById('ann-live').addEventListener('click', ev => {
      const btn = ev.target.closest('button[data-edit-id]');
      if (btn) openAnnotationEditor(btn.dataset.editId);
    });
    document.getElementById('copy-notes-btn').onclick = async () => {
      const base = location.origin + location.pathname;
      const items = showAnnotations ? visibleTimelineAnnotations() : mergedAnnotations(false);
      const text = items.map(n => {
        const ci = catInfo(n.cat);
        const spanTxt = n.end && n.end > n.t ? clock(n.t) + '–' + clock(n.end) : clock(n.t);
        return '- ' + spanTxt + ' (' + base + '#t=' + Math.floor(n.t) + ') [' + ci.label + '] '
          + (n.title ? '**' + n.title + '** — ' : '') + (n.note || n.quote || '');
      }).join('\n');
      try { await navigator.clipboard.writeText(text); } catch {}
      const btn = document.getElementById('copy-notes-btn');
      btn.textContent = 'Copied ' + items.length;
      setTimeout(() => { btn.textContent = 'Copy Markdown'; }, 1500);
    };
    document.getElementById('notes-list').addEventListener('click', ev => {
      const tm = ev.target.closest('.nt-time');
      if (tm) { seekAndPlay(Number(tm.dataset.t)); return; }
      const edit = ev.target.closest('.nt-edit');
      if (edit) { openAnnotationEditor(edit.dataset.edit); return; }
      const save = ev.target.closest('.ed-save');
      if (save) {
        const form = save.closest('.note-edit-form');
        if (form) saveAnnotationEdit(form);
        return;
      }
      const cancel = ev.target.closest('.ed-cancel');
      if (cancel) { editingAnnId = null; paintNotes(); return; }
      const del = ev.target.closest('.nt-del');
      if (del) { deleteAnnotation(del.dataset.del); }
    });

    const captionBox = document.getElementById('caption-box');
    captionBox.addEventListener('mouseover', ev => {
      const row = ev.target.closest('.segment');
      if (!row || !row.dataset.partners) { setHover(null); return; }
      setHover([Number(row.dataset.index), ...row.dataset.partners.split(',').map(Number)]);
    });
    captionBox.addEventListener('mouseleave', () => setHover(null));
    captionBox.addEventListener('click', ev => {
      const chip = ev.target.closest('.ov-chip');
      if (chip) {
        ev.stopPropagation();
        const seg = model.byIndex.get(Number(chip.dataset.target));
        if (seg) seekAndPlay(seg.start);
        return;
      }
      const row = ev.target.closest('.segment');
      if (!row) return;
      const tokEl = ev.target.closest('.word, .ww-pair');
      if (tokEl && tokEl.dataset.v != null) showWordInspector(Number(tokEl.dataset.v));
      seekAndPlay(tokEl ? Number(tokEl.dataset.t) : Number(row.dataset.start));
    });

    const lanesStage = document.getElementById('lanes-stage');
    lanesStage.addEventListener('mouseover', ev => {
      const blk = ev.target.closest('.lane-block');
      if (!blk) { setHover(null); return; }
      const idx = Number(blk.dataset.index);
      const info = model.infoByIndex.get(idx);
      if (info && info.partners.length) setHover([idx, ...info.partners.map(p => p.other.index)]);
      else setHover([idx]);
    });
    lanesStage.addEventListener('mouseleave', () => setHover(null));
    let drag = null;
    lanesStage.addEventListener('pointerdown', ev => {
      if (ev.button !== 0) return;
      const pin = ev.target.closest('.ruler-note-pin');
      if (pin) {
        seekAndPlay(Number(pin.dataset.t));
        return;
      }
      const trk = ev.target.closest('.lane-track, .lanes-ruler');
      if (!trk) return;
      const lw = ev.target.closest('.lane-word[data-v]');
      const vid = lw ? Number(lw.dataset.v) : null;
      drag = {startX: ev.clientX, startWin: lanesWinStart, rect: trk.getBoundingClientRect(), moved: false, vid};
      lanesStage.setPointerCapture(ev.pointerId);
    });
    lanesStage.addEventListener('pointermove', ev => {
      if (!drag) return;
      const dx = ev.clientX - drag.startX;
      if (!drag.moved && Math.abs(dx) >= 5) {
        drag.moved = true;
        lanesFollow = false;
        document.getElementById('lanes-follow').setAttribute('aria-pressed', 'false');
      }
      if (drag.moved && drag.rect.width > 0) {
        lanesWinStart = clampWinStart(drag.startWin - (dx / drag.rect.width) * lanesWindow);
        renderLanesWindow(true);
      }
    });
    lanesStage.addEventListener('pointerup', ev => {
      if (!drag) return;
      const wasDrag = drag.moved, rect = drag.rect, vid = drag.vid;
      drag = null;
      if (!wasDrag && rect.width > 0) {
        if (vid != null && vid >= 0) showWordInspector(vid);
        const frac = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
        seekAndPlay(lanesWinStart + frac * lanesWindow);
      }
    });
    lanesStage.addEventListener('pointercancel', () => { drag = null; });
    lanesStage.addEventListener('wheel', ev => {
      if (Math.abs(ev.deltaX) > Math.abs(ev.deltaY) || ev.shiftKey) {
        ev.preventDefault();
        const delta = Math.abs(ev.deltaX) > Math.abs(ev.deltaY) ? ev.deltaX : ev.deltaY;
        lanesFollow = false;
        document.getElementById('lanes-follow').setAttribute('aria-pressed', 'false');
        lanesWinStart = clampWinStart(lanesWinStart + (delta / 300) * lanesWindow * 0.4);
        renderLanesWindow(true);
      }
    }, {passive: false});

    const minimapWrap = document.getElementById('lanes-minimap-wrap');
    let scrubbing = false;
    const jumpMini = ev => {
      const rect = minimapWrap.getBoundingClientRect();
      if (rect.width <= 0) return;
      const t = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width)) * model.duration;
      lanesFollow = true;
      document.getElementById('lanes-follow').setAttribute('aria-pressed', 'true');
      lanesWinStart = clampWinStart(t - lanesWindow * 0.25);
      renderLanesWindow(true);
      seekAndPlay(t);
    };
    minimapWrap.addEventListener('pointerdown', ev => {
      if (ev.button !== 0) return;
      scrubbing = true;
      minimapWrap.setPointerCapture(ev.pointerId);
      jumpMini(ev);
    });
    minimapWrap.addEventListener('pointermove', ev => { if (scrubbing) jumpMini(ev); });
    minimapWrap.addEventListener('pointerup', () => { scrubbing = false; });
    minimapWrap.addEventListener('pointercancel', () => { scrubbing = false; });

    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => renderLanesWindow(false)).observe(document.getElementById('lanes-ruler'));
      const pwEl = document.getElementById('player-wrap');
      if (pwEl) {
        new ResizeObserver(() => {
          if (showOverlay) {
            fitOverlayLayout();
            updateOverlayTimeline(currentTime(), false);
          }
        }).observe(pwEl);
      }
    }
    window.addEventListener('resize', applySplitLayout);
    window.addEventListener('hashchange', () => {
      const t = parseHashTime();
      if (t != null) seekAndPlay(t);
    });
  }

  init().catch(err => {
    document.getElementById('vid-title').textContent = 'Error: ' + err.message;
  });
})();
"""


def _index_html(
    title: str,
    back_url: str,
    back_label: str,
    css_hash: str,
    js_hash: str,
    ann_url: str = "",
    has_study: bool = False,
) -> str:
    back = (
        f'<a href="{html.escape(back_url, quote=True)}">← {html.escape(back_label)}</a>'
        '<span class="sep">·</span>'
        if back_url
        else ""
    )
    ann_attr = f' data-annotations="{html.escape(ann_url, quote=True)}"' if ann_url else ""
    ann_btn_hidden = "" if ann_url else " hidden"
    study_hidden = "" if has_study else " hidden"
    sel10 = " selected" if has_study else ""
    sel20 = "" if has_study else " selected"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="app.css?v={css_hash}">
</head>
<body>
<header class="top">
  <div class="top-row">
    <h1 id="vid-title">{html.escape(title)}</h1>
    <div class="top-nav">
      {back}<a id="yt-orig-link" href="https://www.youtube.com/" target="_blank" rel="noopener noreferrer">YouTube</a>
      <span class="sep">·</span>
      <button type="button" id="copy-link-btn">Copy link @ time</button>
    </div>
  </div>
  <div class="chapters" id="chapters" hidden></div>
</header>
<main class="split-view"{ann_attr}>
  <div class="player-wrap" id="player-wrap">
    <div class="video-box">
      <iframe id="yt-frame" title="YouTube video player"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
        allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>
    </div>
    <div class="ov-overlay" id="ov-overlay" hidden>
      <div class="ov-ruler-row">
        <div class="ov-gutter" id="ov-drag-handle" title="Drag up or down to position overlay · Right-click for menu">
          <span class="ov-grip">↕<span class="ov-grip-txt"> Move</span></span>
          <button type="button" class="ov-fs-btn" id="ov-fs-btn" aria-pressed="false" title="Fullscreen video with overlay (F)">⛶</button>
        </div>
        <div class="ov-ruler" id="ov-ruler"></div>
      </div>
      <div class="ov-body" id="ov-body"></div>
    </div>
  </div>

  <section class="transcript" id="transcript-panel">
    <div class="transcript-controls">
      <input class="transcript-search" id="transcript-search" type="search"
        placeholder="search words…" autocomplete="off">
      <label>Filter
        <select id="transcript-filter"><option value="all">all</option></select>
      </label>
      <button type="button" id="toggle-autoscroll" aria-pressed="true">Auto-scroll</button>
    </div>
    <div class="caption-box" id="caption-box"></div>
  </section>

  <section class="viz-stage" id="viz-stage">
    <div class="speaker-lanes" id="speaker-lanes">
      <div class="lanes-stage" id="lanes-stage">
        <div class="lanes-ruler-row">
          <div class="lanes-gutter"></div>
          <div class="lanes-ruler" id="lanes-ruler"></div>
        </div>
        <div class="lanes-body" id="lanes-body"></div>
      </div>
      <div class="lanes-minimap-wrap" id="lanes-minimap-wrap" hidden title="Click or drag to jump anywhere">
        <canvas class="lanes-minimap" id="lanes-minimap" height="22"></canvas>
        <div class="minimap-viewport" id="minimap-viewport"></div>
        <div class="minimap-playhead" id="minimap-playhead"></div>
      </div>
      <div class="word-inspector" id="word-inspector" hidden></div>
      <div class="ann-live" id="ann-live" hidden></div>
      <div class="lanes-bar">
        <span class="lanes-status" id="lanes-status"></span>
        <div class="lanes-tools">
          <button type="button" id="prev-overlap" title="Jump to previous overlap">◀ Prev</button>
          <button type="button" id="next-overlap" title="Jump to next overlap">Next ▶</button>
          <label>Window
            <select id="lanes-window">
              <option value="6">6s</option>
              <option value="10"{sel10}>10s</option>
              <option value="20"{sel20}>20s</option>
              <option value="30">30s</option>
              <option value="60">60s</option>
            </select>
          </label>
          <button type="button" id="lanes-follow" aria-pressed="true" title="Keep timeline synced to playback">Follow</button>
          <button type="button" id="toggle-furigana" aria-pressed="true"{study_hidden} title="Show or hide furigana readings over kanji">Furigana</button>
          <button type="button" id="toggle-gloss" aria-pressed="true"{study_hidden} title="Show or hide word-by-word meaning stream">Word-by-word</button>
          <button type="button" id="toggle-english" aria-pressed="true"{study_hidden} title="Show or hide per-speaker English stream">English</button>
          <button type="button" id="toggle-overlay" aria-pressed="false" title="Show compact red-playhead swimlane over the video (O)">Overlay</button>
          <button type="button" id="toggle-fullscreen" aria-pressed="false" title="Fullscreen video with overlay (F)">Fullscreen</button>
          <button type="button" id="toggle-minimap" aria-pressed="false" title="Show or hide full-video overview bar">Minimap</button>
          <button type="button" id="toggle-badges" aria-pressed="false" title="Show or hide overlap duration badges and counters">Badges</button>
          <button type="button" id="toggle-annotations" aria-pressed="false"{ann_btn_hidden} title="Show or hide editable behavior annotations on timeline and page">Annotations</button>
          <button type="button" id="toggle-split-view" aria-pressed="true" title="Toggle side-by-side or stacked layout">Split</button>
        </div>
      </div>
    </div>
  </section>

  <section class="vocab-panel" id="vocab-panel" hidden>
    <div class="vocab-bar">
      <h2>Vocabulary</h2>
      <span class="vocab-count" id="vocab-count"></span>
      <label>Difficulty
        <select id="vocab-diff">
          <option value="n3+" selected>N3+ · Slang · MTG (hide basic)</option>
          <option value="n2+">N2+ · Slang · MTG</option>
          <option value="n1+">N1 · Slang · MTG only</option>
          <option value="mtg">MTG / domain jargon only</option>
          <option value="slang">Slang &amp; spoken only</option>
          <option value="all">All words (incl. N5–N4 basic)</option>
        </select>
      </label>
      <input type="search" id="vocab-search" placeholder="search word, kana, meaning…" autocomplete="off">
      <label>Sort
        <select id="vocab-sort">
          <option value="first" selected>First appearance</option>
          <option value="freq">Most frequent</option>
          <option value="hard">Hardest first</option>
        </select>
      </label>
      <button type="button" id="vocab-hide-known" aria-pressed="true" title="Hide words you marked as known">Hide known</button>
      <button type="button" id="vocab-reset-known" hidden title="Clear your known-words list">Reset known</button>
    </div>
    <div class="vocab-list" id="vocab-list"></div>
  </section>

  <section class="notes-panel" id="notes-panel">
    <div class="notes-bar">
      <h2>Notes</h2>
      <select id="note-tag"></select>
      <input type="text" class="note-title-in" id="note-title" placeholder="short title…">
      <input type="text" class="note-text-in" id="note-text" placeholder="note this moment…">
      <button type="button" id="add-note-btn">+ Note</button>
      <button type="button" id="copy-notes-btn">Copy Markdown</button>
    </div>
    <div class="ann-filter-bar" id="ann-filter-bar" hidden>
      <label>Category
        <select id="ann-filter"><option value="all">all</option></select>
      </label>
      <button type="button" id="prev-ann" title="Jump to previous annotation">◀ Prev note</button>
      <button type="button" id="next-ann" title="Jump to next annotation">Next note ▶</button>
      <span class="ann-actions">
        <button type="button" id="export-ann-btn" title="Download and copy current annotations as JSON">Export JSON</button>
        <button type="button" id="reset-ann-btn" hidden title="Restore deleted or edited built-in annotations">Reset edits</button>
      </span>
    </div>
    <div class="notes-list" id="notes-list"></div>
  </section>
</main>
<div class="stream-ctx-menu" id="stream-ctx-menu" hidden></div>
<script src="app.js?v={js_hash}" defer></script>
</body>
</html>
"""


def _load_annotations(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ValueError(f"Annotations file must be a JSON object with an 'items' list: {path}")
    for i, item in enumerate(raw["items"]):
        if not isinstance(item, dict) or "t" not in item or not isinstance(item["t"], (int, float)):
            raise ValueError(f"Annotation item {i} in {path} must be an object with numeric 't'")
    return raw


def _load_study(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
        raise ValueError(f"Study file must be a JSON object with a 'segments' list: {path}")
    if "vocab" in raw and not isinstance(raw["vocab"], list):
        raise ValueError(f"Study file 'vocab' must be a list: {path}")
    return raw


def _merge_study_into_compact(compact: dict, study: dict) -> None:
    """Attach language-study streams (furigana tokens, word glosses, English, vocab, overlaps) to compact payload."""
    compact["language"] = str(study.get("language") or "ja")
    spk_idx = {s: i for i, s in enumerate(compact["speakers"])}
    by_i = {int(s["i"]): s for s in study["segments"]}
    for seg in compact["segments"]:
        st = by_i.get(int(seg["i"]))
        if not st:
            continue
        if st.get("ja"):
            seg["t"] = str(st["ja"]).strip()
        if st.get("en"):
            seg["en"] = str(st["en"]).strip()
        toks = st.get("tokens") or []
        if toks:
            seg["tk"] = [
                [
                    round(float(tk["s"]), 2),
                    round(float(tk["e"]), 2),
                    str(tk["ja"]),
                    tk.get("ruby") or "",
                    str(tk.get("g") or ""),
                    int(tk["v"]) if tk.get("v") is not None else -1,
                ]
                for tk in toks
            ]
            # Drop redundant w array when tk already carries exact timed tokens.
            seg["w"] = []
    compact["vocab"] = [
        {
            "id": int(v["id"]),
            "b": str(v.get("base") or ""),
            "rb": v.get("ruby") or "",
            "k": str(v.get("kana") or ""),
            "rm": str(v.get("romaji") or ""),
            "d": str(v.get("diff") or "basic"),
            "g": str(v.get("gloss") or ""),
            "rl": str(v.get("role") or ""),
            "n": str(v.get("note") or ""),
            "c": int(v.get("count") or 0),
            "t": [round(float(x), 1) for x in (v.get("times") or [])],
        }
        for v in (study.get("vocab") or [])
    ]
    if study.get("acoustic_overlaps"):
        compact["overlaps"] = [
            [
                round(float(ov["start"]), 2),
                round(float(ov["end"]), 2),
                [spk_idx[s] for s in (ov.get("speakers") or []) if s in spk_idx],
            ]
            for ov in study["acoustic_overlaps"]
        ]
    if study.get("vocal_pulses"):
        compact["pulses"] = [
            [round(float(p["start"]), 2), round(float(p["end"]), 2), spk_idx[p["speaker"]]]
            for p in study["vocal_pulses"]
            if p.get("speaker") in spk_idx
        ]


def _copy_avatars(avatars_src: Path, out_dir: Path) -> dict[str, str]:
    """Copy speaker avatar images into out_dir/avatars/ and return {speaker: relative_url}."""
    if avatars_src.is_dir():
        manifest = avatars_src / "avatars.json"
        base_dir = avatars_src
    else:
        manifest = avatars_src
        base_dir = avatars_src.parent
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Avatars manifest must be a JSON object mapping speaker -> filename: {manifest}")
    av_out = out_dir / "avatars"
    av_out.mkdir(parents=True, exist_ok=True)
    result: dict[str, str] = {}
    for spk, rel in raw.items():
        fname = Path(str(rel)).name
        if not fname or Path(fname).suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            raise ValueError(f"Invalid avatar image filename for {spk!r}: {rel!r}")
        src_file = base_dir / str(rel)
        if not src_file.is_file():
            raise FileNotFoundError(f"Missing avatar image for {spk!r}: {src_file}")
        data = src_file.read_bytes()
        h = hashlib.sha256(data).hexdigest()[:10]
        (av_out / fname).write_bytes(data)
        result[str(spk)] = f"avatars/{fname}?v={h}"
    return result


def export_page(
    data_dir: Path,
    video_id: str,
    out_dir: Path,
    *,
    title: str = "",
    annotations: Path | None = None,
    study: Path | None = None,
    avatars: Path | None = None,
    back_url: str = "",
    back_label: str = "home",
) -> dict:
    video_id = parse_video_id(video_id)
    compact = _compact_transcript(data_dir, video_id)
    if title.strip():
        compact["video_title"] = compact["title"]
        compact["title"] = title.strip()
    vocab_count = 0
    if study is not None:
        study_doc = _load_study(study)
        _merge_study_into_compact(compact, study_doc)
        vocab_count = len(compact.get("vocab") or [])
    out_dir.mkdir(parents=True, exist_ok=True)
    avatar_count = 0
    if avatars is not None:
        av_map = _copy_avatars(avatars, out_dir)
        compact["avatars"] = av_map
        avatar_count = len(av_map)

    css_bytes = APP_CSS.encode("utf-8")
    js_bytes = APP_JS.encode("utf-8")
    css_hash = hashlib.sha256(css_bytes).hexdigest()[:10]
    js_hash = hashlib.sha256(js_bytes).hexdigest()[:10]

    ann_url = ""
    ann_count = 0
    if annotations is not None:
        ann_doc = _load_annotations(annotations)
        ann_bytes = json.dumps(ann_doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ann_hash = hashlib.sha256(ann_bytes).hexdigest()[:10]
        (out_dir / "annotations.json").write_bytes(ann_bytes)
        ann_url = f"annotations.json?v={ann_hash}"
        ann_count = len(ann_doc["items"])

    (out_dir / "app.css").write_bytes(css_bytes)
    (out_dir / "app.js").write_bytes(js_bytes)
    (out_dir / "transcript.json").write_text(
        json.dumps(compact, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (out_dir / "index.html").write_text(
        _index_html(
            compact["title"],
            back_url,
            back_label,
            css_hash,
            js_hash,
            ann_url,
            has_study=study is not None,
        ),
        encoding="utf-8",
    )
    return {
        "video_id": video_id,
        "segments": len(compact["segments"]),
        "speakers": compact["speakers"],
        "annotations": ann_count,
        "vocab": vocab_count,
        "avatars": avatar_count,
        "out_dir": str(out_dir),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", help="YouTube URL or archived video id")
    ap.add_argument("out_dir", type=Path, help="Output directory for static bundle")
    ap.add_argument("--data", type=Path, default=default_data_dir())
    ap.add_argument("--title", default="", help="Optional custom page title overriding video title")
    ap.add_argument("--annotations", type=Path, default=None, help="Optional JSON file of seed annotations")
    ap.add_argument("--study", type=Path, default=None, help="Optional language-study JSON (furigana, glosses, English, vocab)")
    ap.add_argument("--avatars", type=Path, default=None, help="Optional directory (with avatars.json) or avatars.json mapping speakers to face icons")
    ap.add_argument("--back-url", default="")
    ap.add_argument("--back-label", default="home")
    args = ap.parse_args(argv)
    info = export_page(
        args.data.expanduser().resolve(),
        args.target,
        args.out_dir.expanduser().resolve(),
        title=args.title,
        annotations=args.annotations.expanduser().resolve() if args.annotations else None,
        study=args.study.expanduser().resolve() if args.study else None,
        avatars=args.avatars.expanduser().resolve() if args.avatars else None,
        back_url=args.back_url,
        back_label=args.back_label,
    )
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
