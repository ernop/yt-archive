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

main { padding: .6rem 1rem 2rem; max-width: 1800px; margin: 0 auto; }
main.split-view { display: block; }

.player-wrap {
  position: relative; background: #000; border: 1px solid var(--border);
  border-radius: 7px; overflow: hidden;
}
.video-box { position: relative; width: 100%; aspect-ratio: 16 / 9; background: #000; }
.video-box iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }

/* Overlay timeline over the bottom of the YouTube player (opt-in via Overlay button) */
.ov-overlay {
  position: absolute; left: .7rem; right: .7rem; bottom: 3.6rem; z-index: 20;
  background: rgba(7, 8, 10, 0.88); border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: 7px; overflow: hidden; pointer-events: auto; backdrop-filter: blur(5px);
  user-select: none;
}
.ov-ruler-row, .ov-row { display: grid; grid-template-columns: 7.4rem minmax(0, 1fr); }
.ov-ruler-row {
  height: 1.3rem; background: rgba(14, 16, 20, 0.94);
  border-bottom: 1px solid rgba(255, 255, 255, 0.14);
}
.ov-gutter { border-right: 1px solid rgba(255, 255, 255, 0.14); }
.ov-ruler { position: relative; overflow: hidden; cursor: pointer; }
.ov-row { height: 2.55rem; border-bottom: 1px solid rgba(255, 255, 255, 0.1); }
.ov-row:last-child { border-bottom: 0; }
.ov-label {
  display: flex; align-items: center; padding: 0 .48rem;
  border-right: 1px solid rgba(255, 255, 255, 0.14); font-size: .82rem; font-weight: 800;
  color: var(--spk-color, #fff); background: rgba(10, 12, 16, 0.86);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ov-track { position: relative; overflow: hidden; cursor: pointer; }
.ov-overlay .lane-block { top: 4px; bottom: 4px; line-height: calc(2.55rem - 11px); font-size: .84rem; }

/* Primary speech-overlap swimlane visualization */
.viz-stage { margin: .65rem 0 .8rem; }
.speaker-lanes {
  border: 1px solid var(--border); border-radius: 7px;
  background: #08090c; overflow: hidden;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.4);
}
.lanes-stage { position: relative; user-select: none; }
.lanes-ruler-row, .lane-row { display: grid; grid-template-columns: 8.6rem minmax(0, 1fr); }
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
.lane-row:last-child { border-bottom: 0; }
.lane-label {
  display: flex; align-items: center; gap: .45rem; padding: 0 .6rem;
  border-right: 1px solid #222730; font-size: .88rem; font-weight: 800;
  color: var(--spk-color, #fff); background: #0c0e12;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; cursor: pointer;
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
  color: var(--spk-color, #a9d6ff); font-size: .84rem; font-weight: 800;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.segment-text { line-height: 1.46; }
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
    display: grid; grid-template-columns: minmax(0, 3fr) minmax(22rem, 2fr);
    column-gap: .85rem; row-gap: .65rem; align-items: start;
  }
  main.split-view > .player-wrap { grid-column: 1; grid-row: 1; }
  main.split-view > .transcript {
    grid-column: 2; grid-row: 1; margin: 0;
    display: flex; flex-direction: column; overflow: hidden;
  }
  main.split-view > .transcript .caption-box { flex: 1 1 0; max-height: none; min-height: 0; }
  main.split-view > .viz-stage,
  main.split-view > .notes-panel { grid-column: 1 / -1; margin-top: 0; }
}
@media (max-width: 760px) {
  .lanes-ruler-row, .lane-row { grid-template-columns: 5.8rem minmax(0, 1fr); }
  .ov-ruler-row, .ov-row { grid-template-columns: 5.2rem minmax(0, 1fr); }
  .segment { grid-template-columns: 4.2rem 5.5rem minmax(0, 1fr); gap: .38rem; }
  .note-row { grid-template-columns: 4rem 6rem minmax(0, 1fr) auto; }
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
    const segments = (raw.segments || []).map(s => ({
      index: s.i,
      start: Number(s.s),
      end: Number(s.e),
      speaker: spks[s.k] || '',
      text: s.t || '',
      words: (s.w || []).map(w => ({start: Number(w[0]), end: Number(w[1]), word: String(w[2])})),
    }));
    return {
      video_id: raw.video_id,
      title: raw.title,
      channel: raw.channel,
      upload_date: raw.upload_date,
      duration: Number(raw.duration) || 0,
      chapters: raw.chapters || [],
      segments,
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
    for (const s of speakers) bySpeaker.set(s, []);
    for (const seg of sorted) {
      if (seg.speaker && bySpeaker.has(seg.speaker)) bySpeaker.get(seg.speaker).push(seg);
    }
    return {
      duration: Math.max(1, maxEnd),
      speakers, speakerCounts, colorOf, byIndex, infoByIndex, overlapSpans, sorted, bySpeaker,
    };
  }

  function getLaneMeasurer(isOverlay) {
    if (!laneMeasureCtx) {
      laneMeasureCtx = document.createElement('canvas').getContext('2d');
    }
    const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 15;
    const family = getComputedStyle(document.body).fontFamily || 'sans-serif';
    const scale = isOverlay ? 0.84 : 0.9;
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
        if (tokens.length && /^[,.;:!?)]/.test(txt)) {
          const prev = tokens[tokens.length - 1];
          prev.word += txt;
          if (w.end > prev.end) prev.end = w.end;
        } else {
          tokens.push({word: txt, start: w.start, end: Math.max(w.start, w.end)});
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
        tokens.push({word: p, start: ws, end: seg.start + (acc / totalWt) * dur});
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

  function buildLaneBlockContent(seg, s0, s1, blockPx, info, ctx) {
    const pad = 6;
    const innerPx = blockPx - pad * 2;
    if (!ctx || innerPx < 28) return {cls: '', inner: html(seg.text)};
    const tokens = laneTokensForSpan(seg, s0, s1);
    if (!tokens.length) return {cls: '', inner: ''};

    const spacePx = Math.max(4, Math.ceil(ctx.measureText(' ').width));
    const widths = tokens.map(t => Math.ceil(ctx.measureText(t.word).width));
    const totalPx = widths.reduce((a, b) => a + b, 0) + Math.max(0, tokens.length - 1) * spacePx;
    const sDur = Math.max(0.001, s1 - s0);
    const partners = info ? info.partners : [];

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
      let ovColor = '';
      for (const p of partners) {
        if (tok.start < p.ovEnd && tok.end > p.ovStart) {
          ovColor = model.colorOf(p.other.speaker).fg;
          break;
        }
      }
      const leftPct = ((pad + xs[i]) / blockPx) * 100;
      spans.push(
        '<span class="lane-word' + (ovColor ? ' lw-ov' : '') + '"'
        + ' data-t="' + tok.start + '" data-e="' + tok.end + '"'
        + ' style="left:' + leftPct.toFixed(3) + '%'
        + (ovColor ? ';--ov-color:' + ovColor : '') + '">'
        + html(tok.word) + '</span>'
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
    const measurer = trackPx > 0 ? getLaneMeasurer(isOverlay) : null;
    const step = winDur <= 10 ? 2 : winDur <= 20 ? 4 : winDur <= 30 ? 5 : 10;
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
    body.innerHTML = model.speakers.map(spk => {
      const c = model.colorOf(spk);
      const segs = model.bySpeaker.get(spk) || [];
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
        const hasOv = Boolean(info && info.partners.length);
        const title = spk + ' (' + clockPrecise(seg.start) + '–' + clockPrecise(seg.end) + '): ' + seg.text;
        const content = buildLaneBlockContent(seg, s0, s1, blockPx, info, measurer);
        blocks.push(
          '<div class="lane-block' + (hasOv ? ' has-ov' : '') + content.cls + '"'
          + ' data-index="' + seg.index + '"'
          + ' style="left:' + left.toFixed(3) + '%;width:' + width.toFixed(3) + '%;'
          + '--spk-color:' + c.fg + ';--spk-bg:' + c.bg + ';--spk-bg-hi:' + c.bgHi + '"'
          + ' title="' + html(title) + '">' + content.inner + '</div>'
        );
      }
      const isFiltered = !isOverlay && filter === 'spk:' + spk;
      return '<div class="' + rowCls + '">'
        + '<div class="' + lblCls + (isFiltered ? ' filtered' : '') + '"'
        + ' data-speaker="' + html(spk) + '" style="--spk-color:' + c.fg + '">' + html(spk) + '</div>'
        + '<div class="' + trkCls + '">' + gridHtml + bands + blocks.join('')
        + '<div class="lane-playhead"></div></div></div>';
    }).join('');
    return trackPx;
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
    blockElsByIndex = new Map();
    body.querySelectorAll('.lane-block').forEach(el => blockElsByIndex.set(Number(el.dataset.index), el));
    updatePlayheadOn(playheadEls, blockElsByIndex, currentTime(), lanesWinStart, lanesWindow);
  }

  function updatePlayheadOn(phEls, blkMap, time, winStart, winDur) {
    const pct = ((time - winStart) / winDur) * 100;
    const visible = pct >= 0 && pct <= 100;
    for (const el of phEls) {
      el.style.display = visible ? '' : 'none';
      if (visible) el.style.left = pct.toFixed(3) + '%';
    }
    for (const [idx, blk] of blkMap.entries()) {
      const seg = model.byIndex.get(idx);
      const isNow = Boolean(seg && time >= seg.start && time < seg.end);
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
      ovRenderedStart = -1;
      requestAnimationFrame(() => updateOverlayTimeline(currentTime(), true));
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
      ovBlockElsByIndex = new Map();
      body.querySelectorAll('.lane-block').forEach(el => ovBlockElsByIndex.set(Number(el.dataset.index), el));
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
      let passF = true;
      if (filter === 'overlaps') passF = partners.length > 0;
      else if (filter.startsWith('spk:')) passF = seg.speaker === filter.slice(4);
      const passQ = !query || seg.text.toLowerCase().includes(query) || seg.speaker.toLowerCase().includes(query);
      const vis = passF && passQ;
      const color = model.colorOf(seg.speaker);
      const partnerIds = partners.length ? [...new Set(partners.map(p => p.other.index))].join(',') : '';
      let body = '';
      if (seg.words.length) {
        body = seg.words.map((w, wi) => {
          const gap = wi && !/^[,.;:!?)]/.test(w.word) ? ' ' : '';
          let ovP = null;
          for (const p of partners) { if (w.start < p.ovEnd && w.end > p.ovStart) { ovP = p; break; } }
          let span = '<span class="word' + (ovP ? ' word-ov' : '') + '" data-t="' + w.start + '" data-e="' + w.end + '"';
          if (ovP) span += ' style="--ov-color:' + model.colorOf(ovP.other.speaker).fg + '"';
          span += '>' + html(w.word) + '</span>';
          const chips = info && info.chipsByWord.get(wi);
          if (chips) span += chips.map(chipHtml).join('');
          return gap + span;
        }).join('');
      } else {
        body = html(seg.text);
      }
      const ovMark = partners.length ? '<span class="ov-mark">∥</span>' : '';
      return '<div class="segment' + (query && vis ? ' match' : '') + (info && info.overlapsPrev ? ' ov-child' : '') + '"'
        + (vis ? '' : ' hidden') + ' style="--spk-color:' + color.fg + '"'
        + ' data-index="' + seg.index + '" data-start="' + seg.start + '" data-end="' + seg.end + '"'
        + (partnerIds ? ' data-partners="' + partnerIds + '"' : '') + '>'
        + '<span class="segment-time">' + clock(seg.start) + ovMark + '</span>'
        + '<span class="segment-speaker">' + html(seg.speaker || 'Unassigned') + '</span>'
        + '<div class="segment-text">' + body + '</div></div>';
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
      if (showOverlay) updateOverlayTimeline(currentTime(), true);
    });
  }

  function setHover(indices) {
    const key = indices && indices.length ? [...new Set(indices)].sort((a, b) => a - b).join(',') : '';
    if (key === hoveredKey) return;
    hoveredKey = key;
    hoveredIndices = key ? new Set(indices) : null;
    for (const [idx, row] of rowByIndex.entries()) row.classList.toggle('ov-linked', Boolean(hoveredIndices && hoveredIndices.has(idx)));
    for (const [idx, blk] of blockElsByIndex.entries()) blk.classList.toggle('ov-linked', Boolean(hoveredIndices && hoveredIndices.has(idx)));
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

  async function init() {
    const res = await fetch('transcript.json');
    if (!res.ok) throw new Error('Could not load transcript.json');
    doc = expandCompact(await res.json());
    model = buildOverlapModel(doc);

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
    if (![...winSel.options].some(o => Number(o.value) === lanesWindow)) lanesWindow = 20;
    winSel.value = String(lanesWindow);
    document.getElementById('toggle-autoscroll').setAttribute('aria-pressed', String(autoScroll));

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

    const ovEl = document.getElementById('ov-overlay');
    if (ovEl) {
      ovEl.addEventListener('click', ev => {
        ev.stopPropagation();
        const pin = ev.target.closest('.ruler-note-pin');
        if (pin) { seekAndPlay(Number(pin.dataset.t)); return; }
        const trk = ev.target.closest('.ov-track, .ov-ruler');
        if (!trk || !model) return;
        const rect = trk.getBoundingClientRect();
        if (rect.width <= 0) return;
        const frac = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
        const winDur = Math.min(OV_WINDOW_SEC, Math.max(4, lanesWindow));
        seekAndPlay(ovWinStart + frac * winDur);
      });
    }

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
    document.getElementById('toggle-overlay').onclick = () => {
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
      const w = ev.target.closest('.word');
      seekAndPlay(w ? Number(w.dataset.t) : Number(row.dataset.start));
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
      const lbl = ev.target.closest('.lane-label');
      if (lbl) {
        const val = 'spk:' + lbl.dataset.speaker;
        filterSel.value = filterSel.value === val ? 'all' : val;
        paintTranscript();
        return;
      }
      const trk = ev.target.closest('.lane-track, .lanes-ruler');
      if (!trk) return;
      drag = {startX: ev.clientX, startWin: lanesWinStart, rect: trk.getBoundingClientRect(), moved: false};
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
      const wasDrag = drag.moved, rect = drag.rect;
      drag = null;
      if (!wasDrag && rect.width > 0) {
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
) -> str:
    back = (
        f'<a href="{html.escape(back_url, quote=True)}">← {html.escape(back_label)}</a>'
        '<span class="sep">·</span>'
        if back_url
        else ""
    )
    ann_attr = f' data-annotations="{html.escape(ann_url, quote=True)}"' if ann_url else ""
    ann_btn_hidden = "" if ann_url else " hidden"
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
      <div class="ov-ruler-row"><div class="ov-gutter"></div><div class="ov-ruler" id="ov-ruler"></div></div>
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
      <div class="ann-live" id="ann-live" hidden></div>
      <div class="lanes-bar">
        <span class="lanes-status" id="lanes-status"></span>
        <div class="lanes-tools">
          <button type="button" id="prev-overlap" title="Jump to previous overlap">◀ Prev</button>
          <button type="button" id="next-overlap" title="Jump to next overlap">Next ▶</button>
          <label>Window
            <select id="lanes-window">
              <option value="10">10s</option>
              <option value="20" selected>20s</option>
              <option value="30">30s</option>
              <option value="60">60s</option>
            </select>
          </label>
          <button type="button" id="lanes-follow" aria-pressed="true" title="Keep timeline synced to playback">Follow</button>
          <button type="button" id="toggle-overlay" aria-pressed="false" title="Show compact red-playhead swimlane over the video">Overlay</button>
          <button type="button" id="toggle-minimap" aria-pressed="false" title="Show or hide full-video overview bar">Minimap</button>
          <button type="button" id="toggle-badges" aria-pressed="false" title="Show or hide overlap duration badges and counters">Badges</button>
          <button type="button" id="toggle-annotations" aria-pressed="false"{ann_btn_hidden} title="Show or hide editable behavior annotations on timeline and page">Annotations</button>
          <button type="button" id="toggle-split-view" aria-pressed="true" title="Toggle side-by-side or stacked layout">Split</button>
        </div>
      </div>
    </div>
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


def export_page(
    data_dir: Path,
    video_id: str,
    out_dir: Path,
    *,
    title: str = "",
    annotations: Path | None = None,
    back_url: str = "",
    back_label: str = "home",
) -> dict:
    video_id = parse_video_id(video_id)
    compact = _compact_transcript(data_dir, video_id)
    if title.strip():
        compact["video_title"] = compact["title"]
        compact["title"] = title.strip()
    out_dir.mkdir(parents=True, exist_ok=True)

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
        _index_html(compact["title"], back_url, back_label, css_hash, js_hash, ann_url),
        encoding="utf-8",
    )
    return {
        "video_id": video_id,
        "segments": len(compact["segments"]),
        "speakers": compact["speakers"],
        "annotations": ann_count,
        "out_dir": str(out_dir),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", help="YouTube URL or archived video id")
    ap.add_argument("out_dir", type=Path, help="Output directory for static bundle")
    ap.add_argument("--data", type=Path, default=default_data_dir())
    ap.add_argument("--title", default="", help="Optional custom page title overriding video title")
    ap.add_argument("--annotations", type=Path, default=None, help="Optional JSON file of seed annotations")
    ap.add_argument("--back-url", default="")
    ap.add_argument("--back-label", default="home")
    args = ap.parse_args(argv)
    info = export_page(
        args.data.expanduser().resolve(),
        args.target,
        args.out_dir.expanduser().resolve(),
        title=args.title,
        annotations=args.annotations.expanduser().resolve() if args.annotations else None,
        back_url=args.back_url,
        back_label=args.back_label,
    )
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
