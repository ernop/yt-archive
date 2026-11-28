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
body.is-dragging-layout { user-select: none; }
body.is-dragging-layout #yt-frame { pointer-events: none !important; }
a { color: #7cc4ff; text-decoration: none; }
a:hover { text-decoration: underline; }
button, select, input {
  font: inherit; color: #fff; background: #14171d; border: 1px solid #383f4b;
  border-radius: 5px; padding: .26rem .52rem; cursor: pointer;
}
button:hover, select:hover { background: #1e232c; border-color: #637083; }
button:disabled { opacity: .45; border-color: #252a32; background: #101216; cursor: not-allowed; }
button[aria-pressed="true"] { border-color: #68c77b; background: #142618; }
input[type="search"], input[type="text"] { cursor: text; }
input[type="range"] { padding: 0; accent-color: #7cc4ff; cursor: pointer; }
input::placeholder { color: #94a3b8; opacity: 1; }
[hidden] { display: none !important; }
[lang="ja"], [lang="ja"] * {
  font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Hiragino Kaku Gothic ProN",
    "Yu Gothic UI", "Yu Gothic", Meiryo, "Noto Sans CJK JP", "Noto Sans JP", sans-serif;
}
ruby { ruby-position: over; }
rt { font-size: .56em; font-weight: 600; color: #dbeafe; user-select: none; }
html.no-furigana rt { display: none; }

header.top {
  padding: .34rem .85rem .28rem; border-bottom: 1px solid #1d2129; background: #0a0b0e;
}
.top-row {
  display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
  gap: .3rem .9rem;
}
header.top h1 { margin: 0; font-size: 1.04rem; line-height: 1.25; font-weight: 700; }
.top-nav { display: flex; flex-wrap: wrap; align-items: center; gap: .38rem .72rem; font-size: .82rem; }
.top-nav .sep { opacity: .45; }
.top-nav button { font-size: .78rem; padding: .14rem .44rem; }
.chapters { display: flex; flex-wrap: wrap; align-items: center; gap: .24rem; margin-top: .25rem; }
.chapters button { font-size: .74rem; padding: .1rem .38rem; background: #111419; border-color: #2d333e; }

main { padding: .4rem .85rem 1.2rem; width: 100%; max-width: none; margin: 0 auto; }
main.split-view { display: block; }

/* Monitor Stage: strictly aspect-contained video + optional below/top dock + floating speaker layer */
.player-wrap {
  position: relative; display: flex; flex-direction: column;
  background: #000; border: 1px solid var(--border);
  border-radius: 7px; overflow: hidden;
  width: 100%;
}
.player-wrap:fullscreen,
.player-wrap:-webkit-full-screen {
  width: 100vw !important; height: 100vh !important;
  max-width: none !important; max-height: none !important;
  border: 0; border-radius: 0; background: #000;
}
.stage-video-area {
  position: relative; flex: 1 1 0; min-height: 0; min-width: 0;
  width: 100%; display: flex;
  align-items: center; justify-content: center;
  background: #000; overflow: hidden; order: 2;
}
.video-box {
  position: relative; background: #000; flex: 0 0 auto;
  width: 100%; height: 100%; max-width: 100%; max-height: 100%;
}
.video-box iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }

/* Draggable splitter between video and docked scrollers inside Fullscreen */
.stage-splitter {
  flex: 0 0 8px; height: 8px; background: #141923;
  border-top: 1px solid #2b3444; border-bottom: 1px solid #2b3444;
  cursor: ns-resize; position: relative; z-index: 15; order: 3; touch-action: none;
}
.stage-splitter::after {
  content: ""; position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  width: 48px; height: 3px; border-radius: 99px; background: #5b6980;
}
.stage-splitter:hover, .stage-splitter.is-active { background: #1f2b3d; }
.stage-splitter:hover::after, .stage-splitter.is-active::after { background: #7cc4ff; }

.stage-dock {
  flex: 0 0 auto; max-height: 62vh; overflow-y: auto;
  background: #07090d; z-index: 14; order: 4;
}
.player-wrap.dock-top .stage-dock { order: 0; }
.player-wrap.dock-top .stage-splitter { order: 1; }
.player-wrap.dock-top .stage-video-area { order: 2; }
.stage-dock .speaker-lanes { border: 0; border-radius: 0; box-shadow: none; }

/* Windowed Theater stage height resizer along bottom edge of player-wrap */
.stage-win-resize {
  position: absolute; left: 0; right: 0; bottom: 0; height: 7px; z-index: 28;
  cursor: ns-resize; touch-action: none; background: transparent;
}
.stage-win-resize::after {
  content: ""; position: absolute; left: 50%; bottom: 2px; transform: translateX(-50%);
  width: 44px; height: 3px; border-radius: 99px; background: rgba(124, 196, 255, 0.45);
  opacity: 0; transition: opacity .15s;
}
.player-wrap:fullscreen .stage-win-resize,
.player-wrap:-webkit-full-screen .stage-win-resize { display: none; }
html.handles-show .stage-win-resize::after,
.stage-win-resize:hover::after { opacity: 1; }
html.handles-hidden .stage-win-resize { pointer-events: none; display: none; }

/* Minimal floating corner HUD in Fullscreen when dock is hidden */
.fs-corner-hud {
  position: absolute; top: 8px; right: 10px; z-index: 40;
  display: flex; align-items: center; gap: .3rem;
  padding: .22rem .36rem; border-radius: 6px;
  background: rgba(10, 13, 19, 0.84); border: 1px solid rgba(124, 196, 255, 0.42);
  backdrop-filter: blur(5px); opacity: .28; transition: opacity .18s;
}
.fs-corner-hud:hover, html.handles-show .fs-corner-hud { opacity: 1; }
.fs-corner-hud button { font-size: .74rem; padding: .14rem .42rem; }

/* Independent floating per-speaker scroller layer (covers entire stage incl. ultrawide & bottom matte) */
.stage-float-layer {
  position: absolute; inset: 0; pointer-events: none; z-index: 24; overflow: hidden;
}
.spk-float-card {
  --fc-scale: 1;
  --fc-alpha: 0.90;
  position: absolute; pointer-events: auto;
  display: flex; flex-direction: column;
  background: rgba(7, 9, 13, var(--fc-alpha));
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-top: 2px solid var(--spk-color, #7cc4ff);
  border-radius: 7px; backdrop-filter: blur(5px);
  box-shadow: 0 8px 26px rgba(0, 0, 0, 0.56);
  user-select: none; overflow: visible;
  min-width: 170px;
}
.spk-float-card.is-moving {
  box-shadow: 0 12px 34px rgba(0, 0, 0, 0.78), 0 0 0 1.5px var(--spk-color, #7cc4ff);
  z-index: 32;
}
.fc-bar {
  display: flex; align-items: center; gap: .3rem;
  padding: .16rem .34rem; min-height: 22px;
  background: rgba(14, 18, 26, 0.94);
  border-bottom: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 5px 5px 0 0; cursor: move; touch-action: none;
  font-size: calc(.75rem * var(--fc-scale));
}
.fc-bar-name {
  font-weight: 800; color: var(--spk-color, #fff);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.fc-bar-grip { color: #94a3b8; font-weight: 700; flex: 0 0 auto; letter-spacing: -.5px; }
.fc-bar-btns { display: inline-flex; align-items: center; gap: .2rem; margin-left: auto; flex: 0 0 auto; }
.fc-bar-btn {
  padding: .04rem .3rem; font-size: .7rem; line-height: 1.25;
  border-radius: 3px; border: 1px solid rgba(255, 255, 255, 0.22);
  background: rgba(20, 26, 36, 0.92); color: #e2e8f0;
}
.fc-bar-btn:hover { border-color: #7cc4ff; color: #fff; background: #223047; }

/* Handles visibility modes (Show / Hover / Locked-Hidden) */
html.handles-hover .spk-float-card .fc-bar {
  position: absolute; left: 0; right: 0; bottom: 100%; margin-bottom: 2px;
  border-radius: 5px; border: 1px solid rgba(255, 255, 255, 0.24);
  opacity: 0; pointer-events: none; transition: opacity .15s;
}
html.handles-hover .spk-float-card:hover .fc-bar,
html.handles-hover .spk-float-card.is-moving .fc-bar,
html.handles-hover .spk-float-card:focus-within .fc-bar {
  opacity: 1; pointer-events: auto;
}
html.handles-hidden .spk-float-card .fc-bar,
html.handles-hidden .fc-rz,
html.handles-hidden .spk-deck-resizer,
html.handles-hidden .lbl-grip,
html.handles-hidden .lbl-cfg-btn,
html.handles-hidden .stage-splitter {
  display: none !important;
}
html.handles-hidden .lane-label,
html.handles-hidden .fc-label { cursor: pointer; }
html.handles-hover .spk-deck-resizer { opacity: .12; transition: opacity .15s; }
html.handles-hover .spk-deck:hover .spk-deck-resizer,
html.handles-hover .spk-deck-resizer:hover { opacity: 1; }
html.handles-hover .lbl-cfg-btn { opacity: .28; transition: opacity .15s; }
html.handles-hover .lane-label:hover .lbl-cfg-btn,
html.handles-hover .fc-label:hover .lbl-cfg-btn,
html.handles-show .lbl-cfg-btn { opacity: 1; }

.fc-stage { display: flex; flex-direction: column; overflow: hidden; border-radius: 0 0 6px 6px; }
html.handles-hover .fc-stage, html.handles-hidden .fc-stage { border-radius: 5px; }
.fc-rows { display: flex; flex-direction: column; order: 2; }
.fc-ruler-row {
  display: grid; grid-template-columns: var(--fc-gutter, 6.2rem) minmax(0, 1fr);
  height: calc(1.3rem * var(--fc-scale)); background: rgba(12, 15, 21, 0.94);
  border-top: 1px solid rgba(255, 255, 255, 0.14); order: 3;
}
.fc-stage.timer-top .fc-ruler-row {
  order: 1; border-top: 0; border-bottom: 1px solid rgba(255, 255, 255, 0.14);
}
.fc-stage.timer-hidden .fc-ruler-row { display: none; }
.fc-stage.no-gutter .fc-ruler-row,
.fc-stage.no-gutter .fc-row { grid-template-columns: minmax(0, 1fr); }
.fc-stage.no-gutter .fc-gutter,
.fc-stage.no-gutter .fc-label { display: none; }
.fc-gutter {
  display: flex; align-items: center; justify-content: center;
  padding: 0 .3rem; border-right: 1px solid rgba(255, 255, 255, 0.12);
  font: 700 calc(.68rem * var(--fc-scale))/1 var(--mono); color: #9ec5fe;
  font-variant-numeric: tabular-nums; cursor: pointer;
}
.fc-ruler { position: relative; overflow: hidden; cursor: pointer; }
.fc-row {
  display: grid; grid-template-columns: var(--fc-gutter, 6.2rem) minmax(0, 1fr);
  height: calc(var(--row-base-rem, 2.55rem) * var(--fc-scale));
  border-bottom: 1px solid rgba(255, 255, 255, 0.09);
}
.fc-row:last-child { border-bottom: 0; }
.fc-label {
  display: flex; align-items: center; gap: .26rem; padding: 0 .3rem 0 .24rem;
  border-right: 1px solid rgba(255, 255, 255, 0.12);
  font-size: calc(.76rem * var(--fc-scale)); font-weight: 800;
  color: var(--spk-color, #fff); background: rgba(10, 13, 18, 0.88);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.fc-label.sub-lbl {
  font-size: calc(.7rem * var(--fc-scale)); font-weight: 700;
  box-shadow: inset 3px 0 var(--spk-color, #fff);
}
.fc-track { position: relative; overflow: hidden; cursor: pointer; }
.spk-float-card .ruler-tick {
  font-size: calc(.68rem * var(--fc-scale)); line-height: calc(1.3rem * var(--fc-scale));
}
.spk-float-card .lane-block {
  top: 2px; bottom: 2px;
  font-size: calc(.84rem * var(--fc-scale));
  line-height: calc(var(--row-base-rem, 2.55rem) * var(--fc-scale) - 6px);
}
.spk-float-card .lane-block.has-tone-blk {
  line-height: calc(1.48rem * var(--fc-scale));
}
.spk-float-card .lane-block.has-tone-blk .lane-words {
  height: 52%; line-height: calc(1.48rem * var(--fc-scale));
}
.spk-float-card .lane-block.lb-ww { font-size: calc(.74rem * var(--fc-scale)); }
.spk-float-card .lane-block.lb-en,
.spk-float-card .lane-block.lb-std,
.spk-float-card .lane-block.lb-custom-text { font-size: calc(.78rem * var(--fc-scale)); }
.spk-float-card .spk-avatar {
  width: calc(19px * var(--fc-scale)); height: calc(19px * var(--fc-scale));
  min-width: 13px; min-height: 13px; border-width: 1.5px;
}

/* Edge & corner resize handles on each floating speaker card */
.fc-rz { position: absolute; z-index: 30; touch-action: none; }
.fc-rz-w { top: 4px; bottom: 4px; left: -4px; width: 8px; cursor: ew-resize; }
.fc-rz-e { top: 4px; bottom: 4px; right: -4px; width: 8px; cursor: ew-resize; }
.fc-rz-s { left: 8px; right: 14px; bottom: -4px; height: 8px; cursor: ns-resize; }
.fc-rz-se {
  right: -3px; bottom: -3px; width: 14px; height: 14px; cursor: nwse-resize;
  border-right: 2.5px solid var(--spk-color, #7cc4ff);
  border-bottom: 2.5px solid var(--spk-color, #7cc4ff);
  border-radius: 0 0 4px 0; opacity: .75;
}
html.handles-hover .fc-rz-se { opacity: 0; transition: opacity .15s; }
html.handles-hover .spk-float-card:hover .fc-rz-se,
html.handles-hover .spk-float-card.is-moving .fc-rz-se { opacity: .9; }

/* Primary docked speech-overlap swimlane visualization */
.viz-stage { margin: .55rem 0 .7rem; }
.speaker-lanes {
  border: 1px solid var(--border); border-radius: 7px;
  background: #08090c; overflow: hidden;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.4);
}
.lanes-stage {
  position: relative; user-select: none; display: flex; flex-direction: column;
}
.lanes-body { order: 2; display: flex; flex-direction: column; }
.lanes-ruler-row {
  display: grid; grid-template-columns: 10.6rem minmax(0, 1fr);
  border-top: 1px solid #222730; background: #0d1015; height: 1.52rem; order: 3;
}
.lanes-stage.timer-top .lanes-ruler-row {
  order: 1; border-top: 0; border-bottom: 1px solid #222730;
}
.lanes-stage.timer-hidden .lanes-ruler-row { display: none; }
.lanes-gutter {
  display: flex; align-items: center; justify-content: space-between; gap: .25rem;
  padding: 0 .45rem; border-right: 1px solid #222730;
  font: 700 .75rem/1.52rem var(--mono); color: #9ec5fe;
  font-variant-numeric: tabular-nums; cursor: pointer;
}
.lanes-gutter:hover { background: #151b26; color: #fff; }
.lanes-ruler { position: relative; overflow: hidden; cursor: pointer; }
.ruler-tick {
  position: absolute; top: 0; bottom: 0; border-left: 1px solid #2a303b;
  padding-left: .28rem; font: 700 .72rem/1.52rem var(--mono); color: #fff;
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

/* Per-speaker deck inside the docked swimlane (supports separate height, width & subcomponents) */
.spk-deck {
  --spk-scale: 1;
  position: relative; display: flex; flex-direction: column;
  border-bottom: 2px solid #293242; background: #08090c;
}
.spk-deck:last-child { border-bottom: 0; }
.spk-deck.deck-narrow {
  border-left: 1px solid #293242; border-right: 1px solid #293242;
}
.spk-deck-resizer {
  height: 5px; background: transparent; cursor: ns-resize; touch-action: none;
  position: relative; z-index: 6; margin-top: -2px;
}
.spk-deck-resizer::after {
  content: ""; position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  width: 36px; height: 2px; border-radius: 99px; background: var(--spk-color, #64748b);
}
.spk-deck-resizer:hover { background: rgba(124, 196, 255, 0.16); }

.lane-row {
  display: grid; grid-template-columns: 10.6rem minmax(0, 1fr);
  border-bottom: 1px solid #1b1f27;
  height: calc(var(--row-base-rem, 3.0rem) * var(--spk-scale, 1));
}
.spk-deck.no-gutter .lane-row { grid-template-columns: minmax(0, 1fr); }
.spk-deck.no-gutter .lane-label { display: none; }
.lane-row:last-of-type { border-bottom: 0; }
.deck-mini-ruler {
  display: grid; grid-template-columns: 10.6rem minmax(0, 1fr);
  height: 1.25rem; background: #0d1015; border-top: 1px solid #222730;
}
.spk-deck.no-gutter .deck-mini-ruler { grid-template-columns: minmax(0, 1fr); }
.spk-deck.no-gutter .deck-mini-ruler .lanes-gutter { display: none; }
.deck-mini-ruler .ruler-tick { font-size: .68rem; line-height: 1.25rem; }

.lane-label {
  display: flex; align-items: center; gap: .32rem; padding: 0 .35rem 0 .28rem;
  border-right: 1px solid #222730; font-size: calc(.86rem * min(1.25, var(--spk-scale, 1)));
  font-weight: 800; color: var(--spk-color, #fff); background: #0c0e12;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; cursor: grab; touch-action: none;
}
.lane-label::before {
  content: ""; width: .48rem; height: .48rem; border-radius: 999px; flex: 0 0 auto;
  background: var(--spk-color, #fff);
}
.lane-label:hover { background: #151922; }
.lane-label.filtered { background: #182130; box-shadow: inset 3px 0 var(--spk-color, #fff); }
.lane-label.sub-lbl {
  font-size: calc(.74rem * min(1.2, var(--spk-scale, 1))); font-weight: 700;
  color: var(--spk-color, #fff); background: #090b0f;
  padding-left: .48rem; box-shadow: inset 3px 0 var(--spk-color, #fff); cursor: pointer;
}
.lane-label.sub-lbl::before, .lane-label.has-avatar::before { display: none; }
.lbl-txt { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.lbl-kind {
  margin-left: auto; font: 700 .65rem/1.2 var(--mono); padding: .04rem .24rem;
  border-radius: 3px; border: 1px solid var(--spk-color, #7cc4ff); color: var(--spk-color, #fff);
  opacity: .9; flex: 0 0 auto;
}
.lbl-cfg-btn {
  margin-left: auto; padding: .02rem .24rem; font-size: .72rem; line-height: 1.2;
  border-radius: 3px; border: 1px solid #334155; background: #111620; color: #cbd5e1;
  flex: 0 0 auto; cursor: pointer;
}
.lbl-cfg-btn:hover { border-color: #7cc4ff; color: #fff; background: #1d283a; }
.lbl-kind + .lbl-cfg-btn { margin-left: .18rem; }
.lbl-grip {
  color: rgba(255, 255, 255, 0.34); font-size: .8em; line-height: 1; flex: 0 0 auto;
  letter-spacing: -1px;
}
.lane-label:hover .lbl-grip { color: rgba(255, 255, 255, 0.88); }
.spk-deck.is-dragging { opacity: .45; }
.spk-deck.drag-over-top { box-shadow: inset 0 2.5px 0 var(--gold); }
.spk-deck.drag-over-bot { box-shadow: inset 0 -2.5px 0 var(--gold); }

.spk-avatar {
  width: 24px; height: 24px; border-radius: 999px; object-fit: cover; flex: 0 0 auto;
  border: 2px solid var(--spk-color, #7cc4ff); background: #141820;
}
.spk-avatar.av-sm { width: 19px; height: 19px; border-width: 1.5px; }
.spk-avatar.av-xs { width: 17px; height: 17px; border-width: 1.5px; }

.lane-track { position: relative; overflow: hidden; cursor: pointer; background: #08090c; }
.lane-ov-band {
  position: absolute; top: 0; bottom: 0;
  background: linear-gradient(180deg, rgba(255, 210, 64, 0.14), rgba(255, 210, 64, 0.08));
  border-top: 1px solid rgba(255, 210, 64, 0.55);
  border-bottom: 1px solid rgba(255, 210, 64, 0.55);
  pointer-events: none; z-index: 1;
}
.lane-block {
  position: absolute; top: 4px; bottom: 4px; min-width: 4px; border-radius: 5px;
  border: 1.5px solid var(--spk-color, #7cc4ff);
  background: var(--spk-bg, rgba(124, 196, 255, 0.2));
  color: #fff; padding: 0 .4rem;
  font-size: calc(.88rem * var(--spk-scale, 1)); font-weight: 600;
  line-height: calc(var(--row-base-rem, 3.0rem) * var(--spk-scale, 1) - 10px);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; z-index: 2; cursor: pointer;
}
.lane-block.has-words { padding: 0; text-overflow: clip; }
.lane-words { position: relative; width: 100%; height: 100%; overflow: hidden; z-index: 2; }
.lane-block.has-tone-blk {
  line-height: calc(1.7rem * var(--spk-scale, 1));
}
.lane-block.has-tone-blk .lane-words {
  height: 52%; line-height: calc(1.7rem * var(--spk-scale, 1));
}
.lane-block.ja-furi-blk { line-height: 1.24; font-size: calc(.9rem * var(--spk-scale, 1)); }
.lane-block.ja-furi-blk .lane-word { top: auto; bottom: 3px; line-height: 1.24; }
.lane-block.ja-furi-blk.has-tone-blk .lane-words { height: 55%; }
.lane-block.ja-furi-blk rt { display: ruby-text !important; }
.lane-block.ja-plain-blk rt { display: none !important; }
.lane-block.lb-ww {
  top: 3px; bottom: 3px; border: 1px dashed var(--spk-color, #7cc4ff);
  background: var(--spk-bg, rgba(124, 196, 255, 0.16));
  font-size: calc(.77rem * var(--spk-scale, 1)); font-weight: 600;
}
.lane-block.lb-ww .lane-word { color: #fff; font-weight: 600; }
.lane-block.lb-ww.now { border-style: solid; border-color: #fff; background: var(--spk-bg-hi, rgba(124, 196, 255, 0.36)); }
.lane-block.lb-ww .lane-word.lw-now { color: #fff; background: rgba(255, 255, 255, 0.32); }
.lane-block.lb-en {
  top: 3px; bottom: 3px; border: 1px solid var(--spk-color, #7cc4ff);
  border-left: 3px solid var(--spk-color, #7cc4ff);
  background: var(--spk-bg, rgba(124, 196, 255, 0.14)); color: #fff;
  font-size: calc(.82rem * var(--spk-scale, 1)); font-weight: 500; padding: 0 .45rem;
}
.lane-block.lb-en.now { border-color: #fff; background: var(--spk-bg-hi, rgba(124, 196, 255, 0.38)); }
.lane-block.lb-std {
  top: 3px; bottom: 3px; border: 1px dotted var(--spk-color, #7cc4ff);
  border-left: 3px solid var(--spk-color, #7cc4ff);
  background: rgba(20, 26, 38, 0.72); color: #e2e8f0;
  font-size: calc(.8rem * var(--spk-scale, 1)); font-weight: 500; padding: 0 .45rem;
}
.lane-block.lb-std.now { border-style: solid; border-color: #fff; background: var(--spk-bg-hi, rgba(124, 196, 255, 0.36)); color: #fff; }

/* Extensible data/analysis track blocks (text, tag, meter, analysis, delivery) */
.lane-block.lb-custom-text {
  top: 3px; bottom: 3px; border: 1px solid var(--trk-color, #a9d6ff);
  border-left: 3px solid var(--trk-color, #a9d6ff);
  background: rgba(18, 24, 36, 0.84); color: #f1f5f9;
  font-size: calc(.78rem * var(--spk-scale, 1)); font-weight: 500; padding: 0 .42rem;
}
.lane-block.lb-custom-tag {
  top: 3px; bottom: 3px; border: 1px solid var(--trk-color, #ff85a2);
  background: rgba(20, 18, 28, 0.88); color: #fff;
  font-size: calc(.74rem * var(--spk-scale, 1)); font-weight: 700; padding: 0 .38rem;
  display: flex; align-items: center; gap: .28rem;
}
.tag-dot {
  width: 7px; height: 7px; border-radius: 999px; flex: 0 0 auto;
  background: var(--trk-color, #ff85a2);
}
.lane-block.lb-custom-meter {
  top: 3px; bottom: 3px; border: 1px solid rgba(255, 255, 255, 0.24);
  background: rgba(10, 14, 22, 0.9); color: #fff; padding: 0;
  font-size: calc(.72rem * var(--spk-scale, 1)); font-weight: 700;
}
.meter-fill {
  position: absolute; top: 0; bottom: 0; pointer-events: none; opacity: .55;
}
.meter-mid {
  position: absolute; top: 0; bottom: 0; left: 50%; width: 1px;
  background: rgba(255, 255, 255, 0.35); pointer-events: none;
}
.meter-lbl {
  position: relative; z-index: 2; padding: 0 .38rem;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block;
}
.lane-block.lb-analysis {
  top: 3px; bottom: 3px; border: 1px solid var(--cat-color, #7cc4ff);
  border-left: 3px solid var(--cat-color, #7cc4ff);
  background: rgba(17, 22, 33, 0.92); color: #fff;
  font-size: calc(.75rem * var(--spk-scale, 1)); font-weight: 700; padding: 0 .4rem;
}
.lane-block.lb-delivery {
  top: 3px; bottom: 3px; border: 1px dashed rgba(148, 163, 184, 0.5);
  background: rgba(12, 16, 24, 0.88); color: #cbd5e1;
  font: 700 calc(.7rem * var(--spk-scale, 1))/calc(var(--row-base-rem, 1.9rem) * var(--spk-scale, 1) - 8px) var(--mono);
  padding: 0 .38rem;
}

.tone-svg {
  position: absolute; left: 0; right: 0; bottom: 0; height: 48%; width: 100%;
  background: rgba(5, 7, 11, 0.64); border-top: 1px solid rgba(255, 255, 255, 0.14);
  pointer-events: none; z-index: 1; display: block;
}
.tone-badges-wrap {
  position: absolute; z-index: 4; display: inline-flex; align-items: center; gap: 2px;
  pointer-events: auto;
}
.tone-badges-wrap.pos-br { bottom: 2px; right: 3px; }
.tone-badges-wrap.pos-tr { top: 2px; right: 3px; }
.tone-badges-wrap.pos-tl { top: 2px; left: 3px; }
.tone-badge {
  padding: 0 .26rem; border-radius: 3px; font: 800 .62rem/1.28 var(--mono);
  background: rgba(7, 8, 12, 0.92); border: 1px solid currentColor;
  white-space: nowrap;
}
.tone-badge.tb-falsetto { color: #ff5ca8; }
.tone-badge.tb-high { color: #ffd240; }
.tone-badge.tb-low { color: #9aa7ff; }
.tone-badge.tb-quiet { color: #94a3b8; }
.tone-badge.tb-fast { color: #66e3d8; }
.tone-badge.tb-slow { color: #c9b3ff; }
.tone-legend {
  display: flex; flex-wrap: wrap; align-items: center; gap: .25rem .7rem;
  padding: .26rem .6rem; background: #0b0e14; border-top: 1px solid #1e2430;
  font-size: .74rem; color: #cbd5e1;
}
.tone-legend .tl-spk { font-family: var(--mono); font-size: .72rem; }
.tone-swatch {
  display: inline-block; width: .68rem; height: 3px; border-radius: 2px;
  vertical-align: middle; margin-right: .22rem;
}
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
.lanes-empty {
  padding: .55rem .9rem; font-size: .82rem; color: #cbd5e1; text-align: center;
  background: rgba(12, 15, 21, 0.85); cursor: context-menu;
}

.lanes-bar {
  display: flex; flex-wrap: wrap; align-items: center; gap: .32rem .55rem;
  padding: .34rem .58rem; background: #0d1015; border-top: 1px solid #20252e;
}
.lanes-status {
  flex: 1 1 8rem; min-width: 0; font-size: .8rem;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.lanes-status .ov-badge { color: var(--gold); font-weight: 800; margin-right: .35rem; }
.lanes-tools { display: flex; flex-wrap: wrap; align-items: center; gap: .28rem; margin-left: auto; }
.lanes-tools button, .lanes-tools select { padding: .18rem .44rem; font-size: .79rem; }
.lanes-tools label { display: inline-flex; align-items: center; gap: .26rem; font-size: .79rem; font-weight: 700; }

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

/* Context menu & Config popovers (Layout, Labels, Per-speaker settings) */
.stream-ctx-menu, .cfg-popover {
  position: fixed; z-index: 260; min-width: 17.5rem; max-width: 24rem;
  max-height: calc(100vh - 16px); overflow-y: auto;
  background: #10141c; border: 1px solid #49566b; border-radius: 8px;
  box-shadow: 0 14px 36px rgba(0, 0, 0, 0.75); padding: .36rem 0;
  color: #fff; font-size: .83rem; user-select: none;
}
.ctx-head {
  display: flex; align-items: center; gap: .4rem; padding: .3rem .75rem .36rem;
  font-size: .8rem; font-weight: 800; color: #a9d6ff; border-bottom: 1px solid #232a36;
}
.ctx-head-close {
  margin-left: auto; padding: .04rem .36rem; font-size: .78rem;
  border: 1px solid #334155; background: transparent; color: #cbd5e1;
}
.ctx-sec {
  padding: .34rem .75rem .14rem; font-size: .69rem; font-weight: 800;
  text-transform: uppercase; letter-spacing: .04em; color: #94a3b8;
}
.ctx-sep { height: 1px; background: #232a36; margin: .28rem 0; }
.ctx-item {
  display: flex; align-items: center; gap: .45rem; width: 100%; border: 0;
  border-radius: 0; background: transparent; color: #fff; text-align: left;
  padding: .3rem .75rem; font-size: .82rem; cursor: pointer;
}
.ctx-item:hover { background: #1c2433; }
.ctx-item.ctx-danger { color: #fca5a5; }
.ctx-item .ctx-act {
  margin-left: auto; font: 700 .7rem/1.2 var(--mono); padding: .04rem .3rem;
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
  min-width: 3.4rem;
}
.ctx-meta {
  margin-left: auto; font: 700 .72rem/1.2 var(--mono); color: #94a3b8;
}
.ctx-chip-btn {
  padding: .15rem .42rem; font-size: .75rem; font-weight: 700;
  border-radius: 4px; border: 1px solid #3c4657; background: #161c27; color: #fff;
  cursor: pointer;
}
.ctx-chip-btn:hover:not(:disabled) { background: #222c3e; border-color: #7cc4ff; }
.ctx-chip-btn.is-on { border-color: #68c77b; background: #132818; color: #7ee787; }
.ctx-chip-btn.is-off { border-color: #7cc4ff; background: #102036; color: #a9d6ff; }
.ctx-slider-row {
  display: flex; align-items: center; gap: .45rem; padding: .2rem .75rem .26rem;
}
.ctx-slider-row input[type="range"] { flex: 1 1 6rem; min-width: 5rem; }
.ctx-check-row {
  display: flex; align-items: center; gap: .45rem; padding: .22rem .75rem;
  cursor: pointer; font-size: .81rem;
}
.ctx-check-row:hover { background: #182030; }
.ctx-check-row input[type="checkbox"] { margin: 0; width: 14px; height: 14px; cursor: pointer; }
.ctx-check-hint { margin-left: auto; font-size: .7rem; color: #94a3b8; font-family: var(--mono); }

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

.transcript {
  margin-top: .65rem; padding: .58rem .72rem; border: 1px solid var(--border);
  border-radius: 7px; background: var(--panel);
}
.transcript-controls { display: flex; flex-wrap: wrap; align-items: center; gap: .36rem; }
.transcript-controls label { display: inline-flex; align-items: center; gap: .28rem; font-size: .8rem; font-weight: 700; }
.transcript-controls button, .transcript-controls select, .transcript-controls input {
  font-size: .81rem; padding: .2rem .44rem;
}
.transcript-search { flex: 1 1 10rem; min-width: 7.5rem; }
.caption-box {
  margin-top: .42rem; border: 1px solid #222730; border-radius: 6px;
  background: #08090c; max-height: 28rem; overflow-y: auto;
}
.segment {
  display: grid; grid-template-columns: 4.5rem 7rem minmax(0, 1fr); gap: .48rem;
  align-items: baseline; padding: .38rem .58rem; border-bottom: 1px solid #191d24; cursor: pointer;
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
.segment-std { margin-top: .16rem; font-size: .84rem; line-height: 1.4; color: #a9d6ff; }
.segment-en { margin-top: .2rem; font-size: .88rem; line-height: 1.42; color: #e2e8f0; }
.segment-fields {
  margin-top: .22rem; display: flex; flex-wrap: wrap; gap: .25rem;
}
.seg-field-chip {
  display: inline-flex; align-items: center; gap: .22rem; padding: .04rem .36rem;
  border-radius: 4px; border: 1px solid var(--trk-color, #7cc4ff);
  background: #111622; font-size: .74rem; color: #e2e8f0;
}
.seg-field-chip strong { color: var(--trk-color, #7cc4ff); }

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
.vd-mtg, .vd-ar { border-color: #ffd240; color: #ffd240; background: #241c06; }
.vd-slang, .vd-econ { border-color: #ff85a2; color: #ff85a2; background: #261018; }
.vd-n1, .vd-c1 { border-color: #d2a8ff; color: #d2a8ff; background: #1e132b; }
.vd-n2, .vd-b2 { border-color: #7cc4ff; color: #7cc4ff; background: #102033; }
.vd-n3, .vd-b1 { border-color: #7ee787; color: #7ee787; background: #102414; }
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
    grid-template-columns: var(--split-col-w, minmax(22rem, min(62vw, 960px))) minmax(20rem, 1fr);
    column-gap: .75rem; row-gap: .5rem; align-items: start;
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
  .lanes-ruler-row, .lane-row, .deck-mini-ruler { grid-template-columns: 6.8rem minmax(0, 1fr); }
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
  const FLOAT_WINDOW_MAX = 14;

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
  let showMinimap = localStorage.getItem('ovpub.minimap') === 'true';
  let showBadges = localStorage.getItem('ovpub.badges') === 'true';
  let showAnnotations = false;
  let splitView = localStorage.getItem('ovpub.split') !== 'false';
  let autoScroll = localStorage.getItem('ovpub.autoScroll') !== 'false';

  // Stage & monitor layout settings (persisted globally)
  const DEFAULT_STAGE_CFG = {
    videoScale: 1.0,        // 0.40 .. 1.00 (strictly aspect-contained, never overzooms)
    videoAlignX: 'center',  // 'left' | 'center' | 'right'
    videoAlignY: 'center',  // 'top' | 'center' | 'bottom'
    videoAspect: '16:9',    // '16:9' | '4:3' | '21:9'
    fsDock: 'bottom',       // 'bottom' (below video) | 'top' | 'off' (float only)
    fsDockH: 0,             // 0 = auto, >0 = explicit px height dragged via splitter
    winStageH: 0,           // 0 = auto single-screen fit in stacked/theater mode, >0 = dragged px
    timerPos: 'bottom',     // 'bottom' (DEFAULT) | 'top' | 'hidden'
    handlesMode: 'hover',   // 'hover' | 'show' | 'hidden' (locked)
  };
  let stageCfg = loadJsonStorage('ovpub.stageCfg', DEFAULT_STAGE_CFG);

  // Meta-label settings for prosody badges ('loud', 'falsetto', pitch, pace, etc.)
  const DEFAULT_META_LABELS = {
    enabled: true,
    format: 'words',        // 'words' | 'numeric' | 'both'
    position: 'br',         // 'br' | 'tr' | 'tl'
    sensitivity: 'normal',  // 'strict' | 'normal' | 'all'
    maxPerBlock: 1,         // 1 | 2
    kinds: {
      falsetto: true,
      high: true,
      low: true,
      loud: true,
      quiet: true,
      fast: true,
      slow: false,
    },
  };
  let metaLabels = loadMetaLabels();

  function loadJsonStorage(key, defaults) {
    try {
      const raw = JSON.parse(localStorage.getItem(key) || 'null');
      if (raw && typeof raw === 'object') return {...defaults, ...raw};
    } catch {}
    return {...defaults};
  }
  function saveStageCfg() {
    localStorage.setItem('ovpub.stageCfg', JSON.stringify(stageCfg));
  }
  function loadMetaLabels() {
    try {
      const raw = JSON.parse(localStorage.getItem('ovpub.metaLabels') || 'null');
      if (raw && typeof raw === 'object') {
        return {
          ...DEFAULT_META_LABELS,
          ...raw,
          kinds: {...DEFAULT_META_LABELS.kinds, ...(raw.kinds || {})},
        };
      }
    } catch {}
    return {...DEFAULT_META_LABELS, kinds: {...DEFAULT_META_LABELS.kinds}};
  }
  function saveMetaLabels() {
    localStorage.setItem('ovpub.metaLabels', JSON.stringify(metaLabels));
  }

  let hasStudy = false;
  let isCjk = false;
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
    ar: 'AR / Rioplatense', econ: 'Econ / Pol', c1: 'C1–C2', b2: 'B2', b1: 'B1',
  };
  const DIFF_ORDER = {
    mtg: 5, ar: 5, slang: 4, econ: 4, n1: 3, c1: 3, n2: 2, b2: 2, n3: 1, b1: 1, basic: 0,
  };

  // Unified subcomponent/stream registry (built-in speech/study + analysis + delivery + custom --tracks)
  let AVAILABLE_STREAMS = [];
  let STREAM_MAP = new Map();
  let STREAM_ORDER = new Map();
  let CUSTOM_TRACKS = [];
  let CUSTOM_TRACK_MAP = new Map();

  // Independent per-speaker state (order, dock/float placement, per-speaker geometry & subcomponents)
  let speakerOrder = [];
  let speakerConfigs = {};
  let ctxMenuState = null;
  let openPopoverKind = ''; // '' | 'layout' | 'labels' | 'speaker:<name>'

  let hasProsody = false;
  let prosodyDt = 0.06;
  let prosodyByIndex = new Map();
  let prosodySpeakers = {};
  let showTone = localStorage.getItem('ovpub.tone') !== 'false';

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
  let lastClockStr = '';

  // Per-floating-speaker render tracking
  let floatRenderState = new Map(); // spk -> {winStart, winDur, trackPx, playheadEls, blockElsByIndex, clockEl}

  let laneMeasureCtx = null;
  let laneMeasureFont = '';
  let rafId = 0;

  function isPrimaryStreamKind(kind) {
    return kind === 'text' || kind === 'ja' || kind === 'ja-furi' || kind === 'es';
  }
  function defaultPrimaryKind() {
    if (!hasStudy) return 'text';
    if (isCjk) return showFurigana ? 'ja-furi' : 'ja';
    if (doc && doc.language === 'es') return 'es';
    return AVAILABLE_STREAMS[0]?.id || 'text';
  }

  function buildStreamRegistry() {
    const list = [];
    if (hasStudy && doc && doc.language === 'es') {
      const origLbl = doc.dialect === 'ar' ? 'Original Spanish (AR)' : 'Original Spanish';
      const origShort = doc.dialect === 'ar' ? 'ES (AR)' : 'ES';
      list.push({id: 'es', label: origLbl, short: origShort, group: 'speech', baseRem: 2.9});
      if (doc.segments.some(s => s.std)) {
        list.push({id: 'es-std', label: 'Neutral Spanish', short: 'Neutral', group: 'study', baseRem: 2.05});
      }
      if (doc.segments.some(s => s.tokens.length && s.tokens.some(t => t.gloss))) {
        list.push({id: 'literal', label: 'Word-by-word English', short: 'literal', group: 'study', baseRem: 2.0});
      }
      if (doc.segments.some(s => s.en)) {
        list.push({id: 'en', label: 'Natural English', short: 'English', group: 'study', baseRem: 2.1});
      }
    } else if (hasStudy && isCjk) {
      list.push({id: 'ja-furi', label: 'Japanese + Furigana', short: 'JP+furi', group: 'speech', baseRem: 3.45});
      list.push({id: 'ja', label: 'Native Japanese', short: 'JP', group: 'speech', baseRem: 2.9});
      if (doc.segments.some(s => s.tokens.length && s.tokens.some(t => t.gloss))) {
        list.push({id: 'literal', label: 'English literal (word-by-word)', short: 'literal', group: 'study', baseRem: 2.0});
      }
      if (doc.segments.some(s => s.en)) {
        list.push({id: 'en', label: 'English translation', short: 'English', group: 'study', baseRem: 2.1});
      }
    } else {
      list.push({id: 'text', label: 'Transcript', short: 'Text', group: 'speech', baseRem: 2.9});
      if (doc && doc.segments.some(s => s.en)) {
        list.push({id: 'en', label: 'English translation', short: 'English', group: 'study', baseRem: 2.1});
      }
    }

    // Custom extensible tracks from transcript.json (e.g. emotion, sarcasm, mood, status, translation)
    CUSTOM_TRACKS = (doc && Array.isArray(doc.tracks) ? doc.tracks : []).map(t => ({
      id: String(t.id || '').trim(),
      label: String(t.label || t.id || '').trim(),
      short: String(t.short || t.label || t.id || '').trim(),
      type: t.type === 'meter' || t.type === 'tag' ? t.type : 'text',
      color: String(t.color || '#a9d6ff'),
      defaultOn: Boolean(t.defaultOn),
      unsigned: Boolean(t.unsigned),
    })).filter(t => t.id);
    CUSTOM_TRACK_MAP = new Map(CUSTOM_TRACKS.map(t => [t.id, t]));
    for (const trk of CUSTOM_TRACKS) {
      list.push({
        id: 'track:' + trk.id,
        trackId: trk.id,
        label: trk.label,
        short: trk.short,
        group: 'custom',
        trackType: trk.type,
        color: trk.color,
        defaultOn: trk.defaultOn,
        baseRem: trk.type === 'text' ? 2.05 : 1.85,
      });
    }

    // Built-in analytical subcomponent rows when annotations or prosody are present
    if (seedAnnotations.length || (annState.added && annState.added.length)) {
      list.push({
        id: 'analysis', label: 'Analysis / Annotations', short: 'Analysis',
        group: 'analysis', baseRem: 1.9,
      });
    }
    if (hasProsody) {
      list.push({
        id: 'delivery', label: 'Delivery Metrics (pitch/vol/pace)', short: 'Delivery',
        group: 'analysis', baseRem: 1.85,
      });
    }

    AVAILABLE_STREAMS = list;
    STREAM_MAP = new Map(list.map(s => [s.id, s]));
    STREAM_ORDER = new Map(list.map((s, i) => [s.id, i]));
  }

  function defaultFloatSlot(idx, total) {
    if (total === 1) return {x: 4, y: 74, w: 92};
    if (total === 2) {
      return idx === 0 ? {x: 1, y: 75, w: 48.5} : {x: 50.5, y: 75, w: 48.5};
    }
    const cols = total <= 4 ? 2 : 3;
    const col = idx % cols;
    const row = Math.floor(idx / cols);
    const totalRows = Math.ceil(total / cols);
    const w = Math.floor((98 - (cols - 1)) / cols * 10) / 10;
    const x = Math.round((1 + col * (w + 1)) * 10) / 10;
    const bandH = 19;
    const y = Math.max(8, 96 - (totalRows - row) * bandH);
    return {x, y, w};
  }

  function defaultSpeakerRows(spk, spkIndex) {
    const out = [];
    if (hasStudy && doc && doc.language === 'es') {
      const hasEn = STREAM_MAP.has('en');
      if (model.speakers.length >= 4) {
        let primary = model.speakers[0];
        let maxCnt = -1;
        for (const s of model.speakers) {
          const c = model.speakerCounts.get(s) || 0;
          if (c > maxCnt) { maxCnt = c; primary = s; }
        }
        out.push('es');
        if (hasEn && spk === primary) out.push('en');
      } else {
        out.push('es');
        if (hasEn) out.push('en');
      }
    } else if (hasStudy && isCjk) {
      out.push('ja-furi');
      if (STREAM_MAP.has('literal')) out.push('literal');
      if (STREAM_MAP.has('en')) out.push('en');
    } else {
      out.push('text');
    }
    for (const trk of CUSTOM_TRACKS) {
      if (trk.defaultOn) out.push('track:' + trk.id);
    }
    return out;
  }

  function defaultSpeakerOrder() {
    if (!model) return [];
    if (hasStudy && doc && doc.language === 'es' && model.speakers.length >= 4) {
      let primary = model.speakers[0];
      let maxCnt = -1;
      for (const s of model.speakers) {
        const c = model.speakerCounts.get(s) || 0;
        if (c > maxCnt) { maxCnt = c; primary = s; }
      }
      return [primary, ...model.speakers.filter(s => s !== primary)];
    }
    return [...model.speakers];
  }

  function makeDefaultSpeakerCfg(spk, idx, total) {
    const slot = defaultFloatSlot(idx, total);
    return {
      visible: true,
      placement: 'dock', // 'dock' | 'float'
      rows: defaultSpeakerRows(spk, idx),
      showTone: true,
      showBadges: true,
      showPulses: true,
      showLabel: true,
      timerPos: 'inherit', // 'inherit' | 'bottom' | 'top' | 'hidden'
      dockScale: 1.0,
      dockWidth: 100,
      dockAlign: 'left',
      x: slot.x,
      y: slot.y,
      w: slot.w,
      scale: 1.0,
      alpha: 0.90,
      windowSec: 0,
    };
  }

  function spkStorageKey() {
    return 'ovpub.spkCfg.v2.' + doc.video_id;
  }
  function loadSpeakerConfigs() {
    const order = defaultSpeakerOrder();
    const total = order.length;
    const cfgs = {};
    order.forEach((spk, idx) => { cfgs[spk] = makeDefaultSpeakerCfg(spk, idx, total); });
    try {
      const raw = JSON.parse(localStorage.getItem(spkStorageKey()) || 'null');
      if (raw && typeof raw === 'object') {
        if (Array.isArray(raw.order)) {
          const valid = new Set(order);
          const cleanOrder = raw.order.filter(s => valid.has(s));
          for (const s of order) if (!cleanOrder.includes(s)) cleanOrder.push(s);
          speakerOrder = cleanOrder;
        } else {
          speakerOrder = order;
        }
        const saved = raw.configs || {};
        for (const spk of order) {
          const sc = saved[spk];
          if (!sc || typeof sc !== 'object') continue;
          const base = cfgs[spk];
          const rows = Array.isArray(sc.rows)
            ? [...new Set(sc.rows.filter(r => STREAM_MAP.has(r)))]
            : base.rows;
          cfgs[spk] = {
            ...base,
            visible: sc.visible !== false,
            placement: sc.placement === 'float' ? 'float' : 'dock',
            rows: rows.length ? rows : base.rows,
            showTone: sc.showTone !== false,
            showBadges: sc.showBadges !== false,
            showPulses: sc.showPulses !== false,
            showLabel: sc.showLabel !== false,
            timerPos: ['inherit', 'bottom', 'top', 'hidden'].includes(sc.timerPos) ? sc.timerPos : 'inherit',
            dockScale: clampNum(sc.dockScale, 0.65, 2.0, base.dockScale),
            dockWidth: clampNum(sc.dockWidth, 35, 100, base.dockWidth),
            dockAlign: ['left', 'center', 'right'].includes(sc.dockAlign) ? sc.dockAlign : 'left',
            x: clampNum(sc.x, 0, 88, base.x),
            y: clampNum(sc.y, 0, 90, base.y),
            w: clampNum(sc.w, 18, 100, base.w),
            scale: clampNum(sc.scale, 0.6, 1.8, base.scale),
            alpha: clampNum(sc.alpha, 0.45, 0.98, base.alpha),
            windowSec: Number(sc.windowSec) || 0,
          };
        }
        speakerConfigs = cfgs;
        return;
      }
    } catch {}
    speakerOrder = order;
    speakerConfigs = cfgs;
  }
  function saveSpeakerConfigs() {
    localStorage.setItem(spkStorageKey(), JSON.stringify({order: speakerOrder, configs: speakerConfigs}));
  }
  function clampNum(v, lo, hi, fallback) {
    const n = Number(v);
    return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : fallback;
  }
  function anyFloatingSpeakers() {
    return speakerOrder.some(s => speakerConfigs[s]?.visible && speakerConfigs[s]?.placement === 'float');
  }
  function anyDockedSpeakers() {
    return speakerOrder.some(s => speakerConfigs[s]?.visible && speakerConfigs[s]?.placement === 'dock');
  }
  function rowBaseRem(kind, withTone) {
    const st = STREAM_MAP.get(kind);
    const base = st ? st.baseRem : 2.9;
    const toneExtra = withTone && isPrimaryStreamKind(kind) ? 0.95 : 0;
    return base + toneExtra;
  }
  function effectiveTimerPos(spkCfg) {
    if (spkCfg && spkCfg.timerPos && spkCfg.timerPos !== 'inherit') return spkCfg.timerPos;
    return stageCfg.timerPos || 'bottom';
  }

  function isPlayerFullscreen() {
    const pw = document.getElementById('player-wrap');
    return Boolean(pw && (document.fullscreenElement === pw || document.webkitFullscreenElement === pw));
  }
  function parseAspect(a) {
    if (a === '4:3') return 4 / 3;
    if (a === '21:9') return 21 / 9;
    return 16 / 9;
  }
  function fitVideoBox() {
    const area = document.getElementById('stage-video-area');
    const box = document.getElementById('video-box');
    if (!area || !box) return;
    const aw = area.clientWidth || area.getBoundingClientRect().width;
    const ah = area.clientHeight || area.getBoundingClientRect().height;
    if (aw <= 0 || ah <= 0) return;
    const ar = parseAspect(stageCfg.videoAspect);
    const scale = clampNum(stageCfg.videoScale, 0.4, 1.0, 1.0);
    let vw = aw * scale;
    let vh = vw / ar;
    if (vh > ah * scale) {
      vh = ah * scale;
      vw = vh * ar;
    }
    box.style.width = Math.max(160, Math.floor(vw)) + 'px';
    box.style.height = Math.max(90, Math.floor(vh)) + 'px';
    const ax = {left: 'flex-start', center: 'center', right: 'flex-end'};
    const ay = {top: 'flex-start', center: 'center', bottom: 'flex-end'};
    area.style.justifyContent = ax[stageCfg.videoAlignX] || 'center';
    area.style.alignItems = ay[stageCfg.videoAlignY] || 'center';
  }

  function applyHandlesMode() {
    const m = stageCfg.handlesMode || 'hover';
    document.documentElement.classList.toggle('handles-hover', m === 'hover');
    document.documentElement.classList.toggle('handles-show', m === 'show');
    document.documentElement.classList.toggle('handles-hidden', m === 'hidden');
  }
  function applyTimerPosClasses() {
    const ls = document.getElementById('lanes-stage');
    if (ls) {
      const p = stageCfg.timerPos || 'bottom';
      ls.classList.toggle('timer-bottom', p === 'bottom');
      ls.classList.toggle('timer-top', p === 'top');
      ls.classList.toggle('timer-hidden', p === 'hidden');
    }
  }
  function syncFloatButton() {
    const btn = document.getElementById('toggle-overlay');
    if (btn) btn.setAttribute('aria-pressed', String(anyFloatingSpeakers()));
  }
  function syncFullscreenButtons() {
    const fs = isPlayerFullscreen();
    const btn = document.getElementById('toggle-fullscreen');
    if (btn) {
      btn.setAttribute('aria-pressed', String(fs));
      btn.textContent = fs ? 'Exit Full' : 'Fullscreen';
    }
  }
  function syncDockHost() {
    const fs = isPlayerFullscreen();
    const pw = document.getElementById('player-wrap');
    const stageDock = document.getElementById('stage-dock');
    const splitter = document.getElementById('stage-splitter');
    const vizStage = document.getElementById('viz-stage');
    const speakerLanes = document.getElementById('speaker-lanes');
    const hud = document.getElementById('fs-corner-hud');
    if (!pw || !stageDock || !vizStage || !speakerLanes) return;

    pw.classList.toggle('dock-top', fs && stageCfg.fsDock === 'top');
    if (fs) {
      if (stageCfg.fsDock === 'off') {
        if (speakerLanes.parentElement !== vizStage) vizStage.appendChild(speakerLanes);
        stageDock.hidden = true;
        if (splitter) splitter.hidden = true;
        if (hud) hud.hidden = false;
      } else {
        if (speakerLanes.parentElement !== stageDock) stageDock.appendChild(speakerLanes);
        stageDock.hidden = false;
        if (splitter) splitter.hidden = false;
        if (hud) hud.hidden = true;
        if (stageCfg.fsDockH > 0) {
          stageDock.style.height = Math.min(Math.round(window.innerHeight * 0.65), Math.max(88, stageCfg.fsDockH)) + 'px';
        } else {
          stageDock.style.height = '';
        }
      }
    } else {
      if (speakerLanes.parentElement !== vizStage) vizStage.appendChild(speakerLanes);
      stageDock.hidden = true;
      stageDock.style.height = '';
      if (splitter) splitter.hidden = true;
      if (hud) hud.hidden = true;
    }
    fitVideoBox();
  }
  function togglePlayerFullscreen() {
    const pw = document.getElementById('player-wrap');
    if (!pw) return;
    if (isPlayerFullscreen()) {
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) Promise.resolve(exit.call(document)).catch(() => {});
      return;
    }
    const req = pw.requestFullscreen || pw.webkitRequestFullscreen;
    if (req) Promise.resolve(req.call(pw)).catch(() => {});
  }
  function onFullscreenChange() {
    syncFullscreenButtons();
    closeAllPopovers();
    const host = document.fullscreenElement || document.webkitFullscreenElement || document.body;
    for (const id of ['stream-ctx-menu', 'cfg-popover']) {
      const el = document.getElementById(id);
      if (el && el.parentElement !== host) host.appendChild(el);
    }
    syncDockHost();
    applySplitLayout();
    requestAnimationFrame(() => {
      fitVideoBox();
      renderLanesWindow(true);
      renderAllFloatCards(true);
    });
  }

  function avatarHtml(spk, sizeCls) {
    const url = doc && doc.avatars && doc.avatars[spk];
    if (!url) return '';
    return '<img class="spk-avatar' + (sizeCls ? ' ' + sizeCls : '') + '" src="' + html(url)
      + '" alt="' + html(spk) + '" loading="lazy">';
  }

  function syncStudyToolbarButtons() {
    if (!hasStudy) return;
    const allRows = speakerOrder.flatMap(s => (speakerConfigs[s]?.visible ? speakerConfigs[s].rows : []));
    const stdBtn = document.getElementById('toggle-std');
    if (stdBtn && !stdBtn.hidden) stdBtn.setAttribute('aria-pressed', String(allRows.includes('es-std')));
    const gBtn = document.getElementById('toggle-gloss');
    if (gBtn && !gBtn.hidden) {
      showGloss = allRows.includes('literal');
      gBtn.setAttribute('aria-pressed', String(showGloss));
    }
    const eBtn = document.getElementById('toggle-english');
    if (eBtn && !eBtn.hidden) {
      showEnglish = allRows.includes('en');
      eBtn.setAttribute('aria-pressed', String(showEnglish));
    }
  }

  function refreshAllTimelines(recalcLayout) {
    saveSpeakerConfigs();
    syncFloatButton();
    syncStudyToolbarButtons();
    syncDockHost();
    renderLanesWindow(true);
    renderAllFloatCards(true);
    paintTranscript();
    if (recalcLayout) applySplitLayout();
    else fitVideoBox();
  }

  function addRowToSpeaker(spk, kind) {
    const cfg = speakerConfigs[spk];
    if (!cfg || !STREAM_MAP.has(kind)) return;
    if (cfg.rows.includes(kind)) return;
    const kOrd = STREAM_ORDER.get(kind) ?? 99;
    const next = [...cfg.rows];
    const ins = next.findIndex(r => (STREAM_ORDER.get(r) ?? 99) > kOrd);
    if (ins >= 0) next.splice(ins, 0, kind);
    else next.push(kind);
    cfg.rows = next;
    cfg.visible = true;
    refreshAllTimelines(true);
  }
  function removeRowFromSpeaker(spk, kind) {
    const cfg = speakerConfigs[spk];
    if (!cfg) return;
    cfg.rows = cfg.rows.filter(r => r !== kind);
    if (!cfg.rows.length) cfg.visible = false;
    refreshAllTimelines(true);
  }
  function toggleRowOnSpeaker(spk, kind) {
    const cfg = speakerConfigs[spk];
    if (!cfg) return;
    if (cfg.rows.includes(kind)) removeRowFromSpeaker(spk, kind);
    else addRowToSpeaker(spk, kind);
  }
  function toggleRowForAllSpeakers(kind) {
    const visSpks = speakerOrder.filter(s => speakerConfigs[s]?.visible);
    const targets = visSpks.length ? visSpks : speakerOrder;
    const allHave = targets.length > 0 && targets.every(s => speakerConfigs[s].rows.includes(kind));
    for (const s of targets) {
      const cfg = speakerConfigs[s];
      if (allHave) {
        cfg.rows = cfg.rows.filter(r => r !== kind);
        if (!cfg.rows.length) cfg.rows = [defaultPrimaryKind()];
      } else if (!cfg.rows.includes(kind)) {
        const kOrd = STREAM_ORDER.get(kind) ?? 99;
        const ins = cfg.rows.findIndex(r => (STREAM_ORDER.get(r) ?? 99) > kOrd);
        if (ins >= 0) cfg.rows.splice(ins, 0, kind);
        else cfg.rows.push(kind);
      }
    }
    refreshAllTimelines(true);
  }
  function moveSpeakerOrder(fromIdx, toIdx) {
    if (fromIdx < 0 || fromIdx >= speakerOrder.length) return;
    toIdx = Math.max(0, Math.min(speakerOrder.length - 1, toIdx));
    if (fromIdx === toIdx) return;
    const [spk] = speakerOrder.splice(fromIdx, 1);
    speakerOrder.splice(toIdx, 0, spk);
    refreshAllTimelines(false);
  }
  function setAllSpeakersPlacement(placement) {
    const total = speakerOrder.length;
    speakerOrder.forEach((spk, idx) => {
      const cfg = speakerConfigs[spk];
      cfg.placement = placement;
      if (placement === 'float' && (!Number.isFinite(cfg.w) || cfg.w > 98)) {
        const slot = defaultFloatSlot(idx, total);
        cfg.x = slot.x; cfg.y = slot.y; cfg.w = slot.w;
      }
    });
    refreshAllTimelines(true);
  }

  function closeStreamMenu() {
    const menu = document.getElementById('stream-ctx-menu');
    if (menu) { menu.hidden = true; menu.innerHTML = ''; }
    ctxMenuState = null;
  }
  function closeCfgPopover() {
    const pop = document.getElementById('cfg-popover');
    if (pop) { pop.hidden = true; pop.innerHTML = ''; }
    openPopoverKind = '';
  }
  function closeAllPopovers() {
    closeStreamMenu();
    closeCfgPopover();
  }
  function positionFixedPopover(el, anchorX, anchorY) {
    const host = document.fullscreenElement || document.webkitFullscreenElement || document.body;
    if (el.parentElement !== host) host.appendChild(el);
    el.hidden = false;
    const mw = el.offsetWidth || 300;
    const mh = el.offsetHeight || 360;
    const x = Math.max(8, Math.min(anchorX, window.innerWidth - mw - 10));
    const y = Math.max(8, Math.min(anchorY, window.innerHeight - mh - 10));
    el.style.left = x + 'px';
    el.style.top = y + 'px';
    return {x, y};
  }

  // Per-speaker config popover (also opened from right-click on any speaker or its ⚙ button)
  function renderSpeakerConfigPopover(spk, anchorX, anchorY) {
    const pop = document.getElementById('cfg-popover');
    const cfg = speakerConfigs[spk];
    if (!pop || !cfg || !model) return;
    closeStreamMenu();
    openPopoverKind = 'speaker:' + spk;
    const c = model.colorOf(spk);
    const idx = speakerOrder.indexOf(spk);
    const isFloat = cfg.placement === 'float';

    let out = '<div class="ctx-head">'
      + avatarHtml(spk, 'av-xs')
      + '<span style="color:' + c.fg + '">' + html(spk) + ' — Scroller</span>'
      + '<button type="button" class="ctx-head-close" data-pop-close="1">×</button></div>';

    // Placement: Dock below vs Float independently
    out += '<div class="ctx-sec">Position &amp; Mode</div>'
      + '<div class="ctx-inline-bar">'
      + '<button type="button" class="ctx-chip-btn' + (!isFloat ? ' is-on' : '') + '" data-sc="place" data-spk="' + html(spk) + '" data-val="dock">Dock below</button>'
      + '<button type="button" class="ctx-chip-btn' + (isFloat ? ' is-on' : '') + '" data-sc="place" data-spk="' + html(spk) + '" data-val="float">Float separate</button>'
      + '<button type="button" class="ctx-chip-btn" data-sc="order-up" data-spk="' + html(spk) + '"' + (idx <= 0 ? ' disabled' : '') + '>↑</button>'
      + '<button type="button" class="ctx-chip-btn" data-sc="order-dn" data-spk="' + html(spk) + '"' + (idx >= speakerOrder.length - 1 ? ' disabled' : '') + '>↓</button>'
      + '</div>';

    // Independent Size controls
    out += '<div class="ctx-sec">Size (' + (isFloat ? 'Floating card' : 'Docked deck') + ')</div>';
    if (isFloat) {
      out += '<div class="ctx-slider-row"><span class="ctx-inline-lbl">Width</span>'
        + '<input type="range" min="18" max="100" step="1" value="' + Math.round(cfg.w) + '" data-sc-rng="w" data-spk="' + html(spk) + '">'
        + '<span class="ctx-meta">' + Math.round(cfg.w) + '%</span></div>'
        + '<div class="ctx-slider-row"><span class="ctx-inline-lbl">Scale</span>'
        + '<input type="range" min="60" max="180" step="5" value="' + Math.round(cfg.scale * 100) + '" data-sc-rng="scale" data-spk="' + html(spk) + '">'
        + '<span class="ctx-meta">' + Math.round(cfg.scale * 100) + '%</span></div>'
        + '<div class="ctx-slider-row"><span class="ctx-inline-lbl">BG</span>'
        + '<input type="range" min="45" max="98" step="2" value="' + Math.round(cfg.alpha * 100) + '" data-sc-rng="alpha" data-spk="' + html(spk) + '">'
        + '<span class="ctx-meta">' + Math.round(cfg.alpha * 100) + '%</span></div>';
    } else {
      out += '<div class="ctx-slider-row"><span class="ctx-inline-lbl">Height</span>'
        + '<input type="range" min="65" max="200" step="5" value="' + Math.round(cfg.dockScale * 100) + '" data-sc-rng="dockScale" data-spk="' + html(spk) + '">'
        + '<span class="ctx-meta">' + Math.round(cfg.dockScale * 100) + '%</span></div>'
        + '<div class="ctx-slider-row"><span class="ctx-inline-lbl">Width</span>'
        + '<input type="range" min="35" max="100" step="5" value="' + Math.round(cfg.dockWidth) + '" data-sc-rng="dockWidth" data-spk="' + html(spk) + '">'
        + '<span class="ctx-meta">' + Math.round(cfg.dockWidth) + '%</span></div>';
      if (cfg.dockWidth < 100) {
        out += '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Align</span>'
          + ['left', 'center', 'right'].map(a =>
            '<button type="button" class="ctx-chip-btn' + (cfg.dockAlign === a ? ' is-on' : '') + '" data-sc="dockAlign" data-spk="' + html(spk) + '" data-val="' + a + '">' + a + '</button>'
          ).join('') + '</div>';
      }
    }

    // Independent Subcomponents (rows + inline elements)
    out += '<div class="ctx-sep"></div><div class="ctx-sec">Subcomponents for ' + html(spk) + '</div>';
    for (const st of AVAILABLE_STREAMS) {
      const on = cfg.rows.includes(st.id);
      out += '<label class="ctx-check-row">'
        + '<input type="checkbox"' + (on ? ' checked' : '') + ' data-sc-row="' + html(st.id) + '" data-spk="' + html(spk) + '">'
        + '<span>' + html(st.label) + '</span>'
        + '<span class="ctx-check-hint">' + html(st.short) + '</span></label>';
    }
    if (hasProsody) {
      out += '<label class="ctx-check-row">'
        + '<input type="checkbox"' + (cfg.showTone ? ' checked' : '') + ' data-sc-flag="showTone" data-spk="' + html(spk) + '">'
        + '<span>Pitch &amp; loudness curve</span><span class="ctx-check-hint">SVG</span></label>';
      out += '<label class="ctx-check-row">'
        + '<input type="checkbox"' + (cfg.showBadges ? ' checked' : '') + ' data-sc-flag="showBadges" data-spk="' + html(spk) + '">'
        + '<span>Meta-labels (loud, falsetto…)</span><span class="ctx-check-hint">badges</span></label>';
    }
    if ((model.pulsesBySpeaker?.get(spk) || []).length) {
      out += '<label class="ctx-check-row">'
        + '<input type="checkbox"' + (cfg.showPulses ? ' checked' : '') + ' data-sc-flag="showPulses" data-spk="' + html(spk) + '">'
        + '<span>Vocal overlap pulses</span><span class="ctx-check-hint">pulses</span></label>';
    }
    out += '<label class="ctx-check-row">'
      + '<input type="checkbox"' + (cfg.showLabel ? ' checked' : '') + ' data-sc-flag="showLabel" data-spk="' + html(spk) + '">'
      + '<span>Speaker name &amp; avatar column</span><span class="ctx-check-hint">gutter</span></label>';

    if (isFloat) {
      out += '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Timer</span>'
        + [['inherit', 'Auto'], ['bottom', 'Bottom'], ['top', 'Top'], ['hidden', 'Off']].map(([v, lbl]) =>
          '<button type="button" class="ctx-chip-btn' + (cfg.timerPos === v ? ' is-on' : '') + '" data-sc="timerPos" data-spk="' + html(spk) + '" data-val="' + v + '">' + lbl + '</button>'
        ).join('') + '</div>';
    }

    out += '<div class="ctx-sep"></div>'
      + '<div class="ctx-inline-bar">'
      + '<button type="button" class="ctx-chip-btn" data-sc="reset-spk" data-spk="' + html(spk) + '">Reset ' + html(spk) + '</button>'
      + '<button type="button" class="ctx-chip-btn" style="color:#fca5a5" data-sc="hide-spk" data-spk="' + html(spk) + '">Hide speaker</button>'
      + '</div>';

    pop.innerHTML = out;
    pop.dataset.anchorX = String(anchorX);
    pop.dataset.anchorY = String(anchorY);
    positionFixedPopover(pop, anchorX, anchorY);
  }

  // Monitor & Video Layout Popover (Issues 1, 5, 6)
  function renderLayoutPopover(anchorX, anchorY) {
    const pop = document.getElementById('cfg-popover');
    if (!pop || !model) return;
    closeStreamMenu();
    openPopoverKind = 'layout';
    const fs = isPlayerFullscreen();

    let out = '<div class="ctx-head"><span>Monitor &amp; Stage Layout</span>'
      + '<button type="button" class="ctx-head-close" data-pop-close="1">×</button></div>';

    out += '<div class="ctx-sec">Video Frame (strict aspect fit — no overzoom)</div>'
      + '<div class="ctx-slider-row"><span class="ctx-inline-lbl">Scale</span>'
      + '<input type="range" min="40" max="100" step="2" value="' + Math.round(stageCfg.videoScale * 100) + '" data-lay-rng="videoScale">'
      + '<span class="ctx-meta">' + Math.round(stageCfg.videoScale * 100) + '%</span></div>'
      + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Presets</span>'
      + [0.6, 0.75, 0.85, 1.0].map(v =>
        '<button type="button" class="ctx-chip-btn' + (Math.abs(stageCfg.videoScale - v) < 0.02 ? ' is-on' : '')
        + '" data-lay="videoScale" data-val="' + v + '">' + (v === 1 ? 'Fit 100%' : Math.round(v * 100) + '%') + '</button>'
      ).join('') + '</div>'
      + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Horiz</span>'
      + [['left', 'Left'], ['center', 'Center'], ['right', 'Right']].map(([v, lbl]) =>
        '<button type="button" class="ctx-chip-btn' + (stageCfg.videoAlignX === v ? ' is-on' : '') + '" data-lay="videoAlignX" data-val="' + v + '">' + lbl + '</button>'
      ).join('') + '</div>'
      + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Vert</span>'
      + [['top', 'Top'], ['center', 'Center'], ['bottom', 'Bottom']].map(([v, lbl]) =>
        '<button type="button" class="ctx-chip-btn' + (stageCfg.videoAlignY === v ? ' is-on' : '') + '" data-lay="videoAlignY" data-val="' + v + '">' + lbl + '</button>'
      ).join('') + '</div>'
      + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Aspect</span>'
      + ['16:9', '4:3', '21:9'].map(v =>
        '<button type="button" class="ctx-chip-btn' + (stageCfg.videoAspect === v ? ' is-on' : '') + '" data-lay="videoAspect" data-val="' + v + '">' + v + '</button>'
      ).join('') + '</div>';

    out += '<div class="ctx-sep"></div><div class="ctx-sec">Scrollers Placement</div>'
      + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Page</span>'
      + '<button type="button" class="ctx-chip-btn' + (splitView ? ' is-on' : '') + '" data-lay="split" data-val="true">Split + Transcript</button>'
      + '<button type="button" class="ctx-chip-btn' + (!splitView ? ' is-on' : '') + '" data-lay="split" data-val="false">Below video</button>'
      + '</div>'
      + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Fullscr</span>'
      + [['bottom', 'Dock below'], ['top', 'Dock above'], ['off', 'Float only']].map(([v, lbl]) =>
        '<button type="button" class="ctx-chip-btn' + (stageCfg.fsDock === v ? ' is-on' : '') + '" data-lay="fsDock" data-val="' + v + '">' + lbl + '</button>'
      ).join('') + '</div>'
      + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Speakers</span>'
      + '<button type="button" class="ctx-chip-btn' + (!anyFloatingSpeakers() ? ' is-on' : '') + '" data-lay="all-dock">Dock all below</button>'
      + '<button type="button" class="ctx-chip-btn' + (anyFloatingSpeakers() && !anyDockedSpeakers() ? ' is-on' : '') + '" data-lay="all-float">Float separately</button>'
      + '<button type="button" class="ctx-chip-btn" data-lay="matte-float" title="Pin video to top at 76% and float speakers in the clean black band below it">Matte below + Float</button>'
      + '</div>';

    out += '<div class="ctx-sep"></div><div class="ctx-sec">Timer &amp; Move / Resize Handles</div>'
      + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Timer</span>'
      + [['bottom', 'Bottom'], ['top', 'Top'], ['hidden', 'Hidden']].map(([v, lbl]) =>
        '<button type="button" class="ctx-chip-btn' + (stageCfg.timerPos === v ? ' is-on' : '') + '" data-lay="timerPos" data-val="' + v + '">' + lbl + '</button>'
      ).join('') + '</div>'
      + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Handles</span>'
      + [['hover', 'Hover'], ['show', 'Show'], ['hidden', 'Locked']].map(([v, lbl]) =>
        '<button type="button" class="ctx-chip-btn' + (stageCfg.handlesMode === v ? ' is-on' : '') + '" data-lay="handlesMode" data-val="' + v + '">' + lbl + '</button>'
      ).join('') + '</div>';

    out += '<div class="ctx-sep"></div>'
      + '<div class="ctx-inline-bar">'
      + '<button type="button" class="ctx-chip-btn" data-lay="toggle-fs">' + (fs ? 'Exit Fullscreen (F)' : 'Enter Fullscreen (F)') + '</button>'
      + '<button type="button" class="ctx-chip-btn" data-lay="reset-layout">Reset layout</button>'
      + '</div>';

    pop.innerHTML = out;
    pop.dataset.anchorX = String(anchorX);
    pop.dataset.anchorY = String(anchorY);
    positionFixedPopover(pop, anchorX, anchorY);
  }

  // Meta-labels config popover (Issue 4: toggle & explain 'loud' + pitch/pace badges)
  function renderLabelsPopover(anchorX, anchorY) {
    const pop = document.getElementById('cfg-popover');
    if (!pop) return;
    closeStreamMenu();
    openPopoverKind = 'labels';
    const ml = metaLabels;

    let out = '<div class="ctx-head"><span>Meta-Labels Config</span>'
      + '<button type="button" class="ctx-head-close" data-pop-close="1">×</button></div>';

    out += '<label class="ctx-check-row">'
      + '<input type="checkbox"' + (ml.enabled ? ' checked' : '') + ' data-ml-flag="enabled">'
      + '<span><strong>Show meta-labels on turns</strong></span></label>';

    out += '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Style</span>'
      + [['words', 'Plain words'], ['numeric', 'Numeric'], ['both', 'Both']].map(([v, lbl]) =>
        '<button type="button" class="ctx-chip-btn' + (ml.format === v ? ' is-on' : '') + '" data-ml="format" data-val="' + v + '">' + lbl + '</button>'
      ).join('') + '</div>'
      + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Corner</span>'
      + [['br', 'Bot-right'], ['tr', 'Top-right'], ['tl', 'Top-left']].map(([v, lbl]) =>
        '<button type="button" class="ctx-chip-btn' + (ml.position === v ? ' is-on' : '') + '" data-ml="position" data-val="' + v + '">' + lbl + '</button>'
      ).join('') + '</div>'
      + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Trigger</span>'
      + [['strict', 'Major only'], ['normal', 'Normal'], ['all', 'Sensitive']].map(([v, lbl]) =>
        '<button type="button" class="ctx-chip-btn' + (ml.sensitivity === v ? ' is-on' : '') + '" data-ml="sensitivity" data-val="' + v + '">' + lbl + '</button>'
      ).join('') + '</div>'
      + '<div class="ctx-inline-bar"><span class="ctx-inline-lbl">Max/turn</span>'
      + [1, 2].map(v =>
        '<button type="button" class="ctx-chip-btn' + (ml.maxPerBlock === v ? ' is-on' : '') + '" data-ml="maxPerBlock" data-val="' + v + '">' + v + '</button>'
      ).join('') + '</div>';

    out += '<div class="ctx-sep"></div><div class="ctx-sec">Label types (vs speaker baseline)</div>';
    const defs = [
      ['falsetto', 'Falsetto / high pitch peak', '+9.5st+ (semitones)'],
      ['high', 'Rising / elevated pitch', '+6st rising'],
      ['low', 'Low pitch drop', '−3.5st+'],
      ['loud', 'Loud volume spike', '+7.5dB peak'],
      ['quiet', 'Quiet / soft voice', '−6.5dB'],
      ['fast', 'Fast speaking pace', '1.45×+ speed'],
      ['slow', 'Slow / drawn-out pace', '≤0.62× speed'],
    ];
    for (const [k, lbl, hint] of defs) {
      out += '<label class="ctx-check-row">'
        + '<input type="checkbox"' + (ml.kinds[k] ? ' checked' : '') + ' data-ml-kind="' + k + '">'
        + '<span>' + lbl + '</span><span class="ctx-check-hint">' + hint + '</span></label>';
    }

    pop.innerHTML = out;
    pop.dataset.anchorX = String(anchorX);
    pop.dataset.anchorY = String(anchorY);
    positionFixedPopover(pop, anchorX, anchorY);
  }

  // Global Layers / Speakers menu (also right-click anywhere on swimlane)
  function renderStreamMenu(anchorX, anchorY) {
    const menu = document.getElementById('stream-ctx-menu');
    if (!menu || !ctxMenuState || !model) return;
    closeCfgPopover();
    const {hitSpk, hitKind} = ctxMenuState;
    const visSpks = speakerOrder.filter(s => speakerConfigs[s]?.visible);
    const targets = visSpks.length ? visSpks : speakerOrder;

    let out = '<div class="ctx-head">'
      + (hitSpk ? avatarHtml(hitSpk, 'av-xs') : '')
      + '<span>' + html(hitSpk ? hitSpk + ' · Layers' : 'Speakers &amp; Data Layers') + '</span></div>';

    if (hitSpk && speakerConfigs[hitSpk]) {
      const sc = speakerConfigs[hitSpk];
      out += '<button type="button" class="ctx-item" data-act="open-spk-cfg" data-spk="' + html(hitSpk) + '">'
        + '<span>⚙ Configure ' + html(hitSpk) + ' (size, position, parts)…</span></button>';
      out += '<button type="button" class="ctx-item" data-act="toggle-spk-float" data-spk="' + html(hitSpk) + '">'
        + '<span>' + (sc.placement === 'float' ? 'Dock ' + html(hitSpk) + ' below video' : 'Float ' + html(hitSpk) + ' independently') + '</span></button>';
      if (hitKind && sc.rows.includes(hitKind) && sc.rows.length > 1) {
        out += '<button type="button" class="ctx-item ctx-danger" data-act="rm-one" data-spk="'
          + html(hitSpk) + '" data-kind="' + html(hitKind) + '">'
          + '<span>Remove “' + html(STREAM_MAP.get(hitKind)?.short || hitKind) + '” from ' + html(hitSpk) + '</span></button>';
      }
      out += '<div class="ctx-sep"></div>';
    }

    out += '<div class="ctx-sec">Data &amp; Subtitle Layers (all active speakers)</div>';
    for (const st of AVAILABLE_STREAMS) {
      const allHave = targets.length > 0 && targets.every(s => speakerConfigs[s].rows.includes(st.id));
      out += '<button type="button" class="ctx-item ' + (allHave ? 'is-on' : 'is-off')
        + '" data-act="toggle-kind" data-kind="' + html(st.id) + '">'
        + '<span>' + html(st.label) + '</span>'
        + '<span class="ctx-act">' + (allHave ? '✓ On' : '+ Add') + '</span></button>';
    }

    out += '<div class="ctx-sep"></div><div class="ctx-sec">Speakers (click ⚙ for per-speaker settings)</div>';
    for (const spk of speakerOrder) {
      const sc = speakerConfigs[spk];
      const c = model.colorOf(spk);
      out += '<div class="ctx-inline-bar" style="padding:.16rem .75rem">'
        + avatarHtml(spk, 'av-xs')
        + '<span style="color:' + c.fg + ';font-weight:800;margin-right:auto">' + html(spk) + '</span>'
        + '<button type="button" class="ctx-chip-btn' + (sc.visible ? ' is-on' : ' is-off') + '" data-act="toggle-spk" data-spk="' + html(spk) + '">'
        + (sc.visible ? 'Shown' : 'Hidden') + '</button>'
        + '<button type="button" class="ctx-chip-btn' + (sc.placement === 'float' ? ' is-on' : '') + '" data-act="toggle-spk-float" data-spk="' + html(spk) + '" title="Toggle docked below vs floating card">'
        + (sc.placement === 'float' ? 'Float' : 'Dock') + '</button>'
        + '<button type="button" class="ctx-chip-btn" data-act="open-spk-cfg" data-spk="' + html(spk) + '" title="Independent size &amp; subcomponents">⚙</button>'
        + '</div>';
    }

    out += '<div class="ctx-sep"></div>'
      + '<div class="ctx-inline-bar">'
      + '<button type="button" class="ctx-chip-btn" data-act="open-layout">Monitor Layout…</button>'
      + (hasProsody ? '<button type="button" class="ctx-chip-btn" data-act="open-labels">Meta-Labels…</button>' : '')
      + '<button type="button" class="ctx-chip-btn" data-act="reset-all">Reset</button>'
      + '</div>';

    menu.innerHTML = out;
    const pos = positionFixedPopover(menu, anchorX, anchorY);
    ctxMenuState.x = pos.x;
    ctxMenuState.y = pos.y;
  }
  function showStreamMenu(ev) {
    if (!model) return;
    ev.preventDefault();
    ev.stopPropagation();
    const rowEl = ev.target.closest('[data-row-spk]');
    const deckEl = ev.target.closest('[data-spk]');
    const hitSpk = rowEl ? rowEl.dataset.rowSpk : (deckEl ? deckEl.dataset.spk : '');
    const hitKind = rowEl ? rowEl.dataset.rowKind : '';
    ctxMenuState = {hitSpk, hitKind, x: ev.clientX, y: ev.clientY};
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
    for (const item of (annState.added || [])) out.push({...item, _origin: 'user'});
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
    if (pendingHashSeek == null && model && model.clipStart > 0) {
      pendingHashSeek = model.clipStart;
    }
    if (pendingHashSeek != null && pendingHashSeek > 0) ytTime = pendingHashSeek;
    const originParam = /^https?:\/\//.test(location.origin)
      ? '&origin=' + encodeURIComponent(location.origin)
      : '';
    const startParam = pendingHashSeek != null ? '&start=' + Math.floor(pendingHashSeek) : '';
    iframe.src = YT_ORIGIN + '/embed/' + encodeURIComponent(videoId)
      + '?enablejsapi=1&rel=0&playsinline=1&cc_load_policy=0&iv_load_policy=3' + originParam + startParam;

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
        std: String(s.std || ''),
        en: String(s.en || ''),
        fields: s.f && typeof s.f === 'object' ? s.f : {},
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
      dialect: String(raw.dialect || ''),
      title: raw.title,
      channel: raw.channel,
      upload_date: raw.upload_date,
      duration: Number(raw.duration) || 0,
      clip_start: raw.clip_start != null ? Number(raw.clip_start) : null,
      clip_end: raw.clip_end != null ? Number(raw.clip_end) : null,
      chapters: raw.chapters || [],
      avatars: raw.avatars && typeof raw.avatars === 'object' ? raw.avatars : {},
      tracks: Array.isArray(raw.tracks) ? raw.tracks : [],
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
    const clipStart = data.clip_start != null && Number.isFinite(data.clip_start) ? Math.max(0, data.clip_start) : 0;
    const clipEnd = data.clip_end != null && Number.isFinite(data.clip_end) && data.clip_end > clipStart
      ? data.clip_end
      : Math.max(clipStart + 1, maxEnd);
    return {
      duration: Math.max(1, maxEnd),
      clipStart, clipEnd,
      speakers, speakerCounts, colorOf, byIndex, infoByIndex, overlapSpans, sorted, bySpeaker, pulsesBySpeaker,
    };
  }

  function getLaneMeasurer(scaleRem) {
    if (!laneMeasureCtx) {
      laneMeasureCtx = document.createElement('canvas').getContext('2d');
    }
    const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 15;
    const family = getComputedStyle(document.body).fontFamily || 'sans-serif';
    const font = '600 ' + (scaleRem * rootPx).toFixed(2) + 'px ' + family;
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
        if (!isCjk && tokens.length && /^[,.;:!?)]/.test(txt)) {
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

    const useFuri = Boolean(isCjk && withFuri);
    const spacePx = isCjk ? 3 : Math.max(4, Math.ceil(ctx.measureText(' ').width));
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
    const langAttr = doc && doc.language ? ' lang="' + html(doc.language) + '"' : '';
    return {
      cls: ' has-words' + (clipped ? ' is-clipped' : ''),
      inner: '<div class="lane-words"' + langAttr + '>' + spans.join('') + '</div>',
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

  function fmtSigned(n) {
    const v = Number(n) || 0;
    return (v >= 0 ? '+' : '') + v.toFixed(1);
  }
  function buildMetaBadgesHtml(pr, blockPx, spkCfg) {
    if (!pr || !metaLabels.enabled || (spkCfg && !spkCfg.showBadges) || blockPx < 64) return '';
    const sens = metaLabels.sensitivity || 'normal';
    const th = sens === 'strict'
      ? {fal: 13.0, hiPk: 8.5, hiSl: 4.0, lo: -5.0, loud: 10.5, quiet: -8.5, fast: 1.65, slow: 0.52}
      : sens === 'all'
        ? {fal: 8.0, hiPk: 4.5, hiSl: 2.0, lo: -2.8, loud: 5.5, quiet: -5.0, fast: 1.32, slow: 0.72}
        : {fal: 9.5, hiPk: 6.0, hiSl: 3.5, lo: -3.5, loud: 7.5, quiet: -6.5, fast: 1.45, slow: 0.62};

    const hits = [];
    const k = metaLabels.kinds || {};
    if (k.falsetto && pr.pk >= th.fal) {
      hits.push({
        cls: 'tb-falsetto', word: 'falsetto', num: '↑+' + Math.round(pr.pk) + 'st',
        tip: 'Falsetto / pitch peak: +' + pr.pk.toFixed(1) + ' semitones above baseline',
      });
    } else if (k.high && pr.pk >= th.hiPk && (pr.sl >= th.hiSl || sens === 'all')) {
      hits.push({
        cls: 'tb-high', word: 'rising', num: '↗+' + Math.round(pr.pk) + 'st',
        tip: 'Rising / high pitch: +' + pr.pk.toFixed(1) + ' semitones above baseline',
      });
    }
    if (k.low && pr.md <= th.lo) {
      hits.push({
        cls: 'tb-low', word: 'low', num: '↓' + Math.round(pr.md) + 'st',
        tip: 'Low pitch drop: ' + pr.md.toFixed(1) + ' semitones below baseline',
      });
    }
    if (k.loud && pr.pdb >= th.loud) {
      hits.push({
        cls: 'tb-high', word: 'loud', num: '+' + Math.round(pr.pdb) + 'dB',
        tip: 'Loud peak: +' + pr.pdb.toFixed(1) + ' dB above speaker normal volume',
      });
    } else if (k.quiet && pr.db <= th.quiet) {
      hits.push({
        cls: 'tb-quiet', word: 'quiet', num: Math.round(pr.db) + 'dB',
        tip: 'Quiet voice: ' + pr.db.toFixed(1) + ' dB below speaker normal volume',
      });
    }
    if (k.fast && pr.rt >= th.fast) {
      hits.push({
        cls: 'tb-fast', word: 'fast', num: pr.rt.toFixed(1) + '×',
        tip: 'Fast pace: ' + pr.rt.toFixed(2) + '× speaker normal syllables/sec',
      });
    } else if (k.slow && pr.rt > 0 && pr.rt <= th.slow) {
      hits.push({
        cls: 'tb-slow', word: 'slow', num: pr.rt.toFixed(1) + '×',
        tip: 'Slow / drawn-out pace: ' + pr.rt.toFixed(2) + '× speaker normal syllables/sec',
      });
    }
    if (!hits.length) return '';
    const maxB = Number(metaLabels.maxPerBlock) || 1;
    const fmt = metaLabels.format || 'words';
    const pos = metaLabels.position || 'br';
    const badges = hits.slice(0, maxB).map(h => {
      const txt = fmt === 'numeric' ? h.num : fmt === 'both' ? (h.word + ' ' + h.num) : h.word;
      return '<span class="tone-badge ' + h.cls + '" title="' + html(h.tip) + '">' + html(txt) + '</span>';
    }).join('');
    return '<span class="tone-badges-wrap pos-' + html(pos) + '">' + badges + '</span>';
  }

  function buildToneOverlay(seg, s0, s1, blockPx, spkColor, spkCfg) {
    if (!hasProsody) return null;
    const pr = prosodyByIndex.get(seg.index);
    if (!pr || !Array.isArray(pr.p) || !pr.p.length) return null;
    const wantCurve = showTone && (!spkCfg || spkCfg.showTone);
    const badgeHtml = buildMetaBadgesHtml(pr, blockPx, spkCfg);
    const tags = Array.isArray(pr.tg) && pr.tg.length ? ' [' + pr.tg.join(', ') + ']' : '';
    const tip = ' | pitch ' + fmtSigned(pr.md) + 'st (peak ' + fmtSigned(pr.pk) + 'st) · vol '
      + fmtSigned(pr.db) + 'dB · ' + (Number(pr.rt) || 1).toFixed(2) + '× pace' + tags;
    if (!wantCurve) {
      return badgeHtml ? {svg: '', badge: badgeHtml, tip, hasCurve: false} : null;
    }

    const span = Math.max(0.01, s1 - s0);
    const dt = prosodyDt || 0.06;
    const pArr = pr.p;
    const dArr = pr.d || [];
    const kStart = Math.max(0, Math.floor((s0 - seg.start) / dt));
    const kEnd = Math.min(pArr.length, Math.ceil((s1 - seg.start) / dt) + 1);
    if (kEnd <= kStart) return null;

    const pts = [];
    for (let k = kStart; k < kEnd; k++) {
      const t = seg.start + (k + 0.5) * dt;
      const x = Math.max(0, Math.min(1000, ((t - s0) / span) * 1000));
      const pRaw = pArr[k];
      const st = pRaw == null ? null : pRaw / 10;
      const db = dArr[k] != null ? dArr[k] / 2 : -14;
      pts.push({x, st, db});
    }
    if (!pts.length) return null;

    const polyPts = [pts[0].x.toFixed(1) + ',98'];
    for (const pt of pts) {
      const frac = Math.max(0.04, Math.min(1, (pt.db + 16) / 28));
      const ly = 98 - frac * 86;
      polyPts.push(pt.x.toFixed(1) + ',' + ly.toFixed(1));
    }
    polyPts.push(pts[pts.length - 1].x.toFixed(1) + ',98');

    const stToY = st => Math.max(5, Math.min(95, 65 - st * 3.333));
    const pitchClr = st => (st >= 9.5 ? '#ff5ca8' : st >= 5.0 ? '#ffd240' : st <= -3.5 ? '#9aa7ff' : spkColor);
    const dbStyle = db => (db >= 4.5 ? {w: 4.8, o: 1.0} : db <= -5.0 ? {w: 2.2, o: 0.78} : {w: 3.2, o: 0.96});

    const paths = [];
    let curKey = '';
    let curD = '';
    let curMeta = null;
    const flush = () => {
      if (curD && curMeta) {
        paths.push(
          '<path d="' + curD + '" fill="none" stroke="' + curMeta.clr + '" stroke-width="' + curMeta.w
          + '" stroke-opacity="' + curMeta.o + '" stroke-linecap="round" stroke-linejoin="round"/>'
        );
      }
      curKey = ''; curD = ''; curMeta = null;
    };
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i];
      if (a.st == null) continue;
      const b = i + 1 < pts.length ? pts[i + 1] : null;
      const ya = stToY(a.st);
      if (b && b.st != null) {
        const yb = stToY(b.st);
        const midSt = (a.st + b.st) * 0.5;
        const midDb = (a.db + b.db) * 0.5;
        const clr = pitchClr(midSt);
        const ds = dbStyle(midDb);
        const key = clr + '|' + ds.w;
        if (key !== curKey) {
          flush();
          curKey = key;
          curMeta = {clr, w: ds.w, o: ds.o};
          curD = 'M' + a.x.toFixed(1) + ' ' + ya.toFixed(1) + 'L' + b.x.toFixed(1) + ' ' + yb.toFixed(1);
        } else {
          curD += 'M' + a.x.toFixed(1) + ' ' + ya.toFixed(1) + 'L' + b.x.toFixed(1) + ' ' + yb.toFixed(1);
        }
      } else if (i === 0 || pts[i - 1].st == null) {
        const clr = pitchClr(a.st);
        const ds = dbStyle(a.db);
        paths.push('<circle cx="' + a.x.toFixed(1) + '" cy="' + ya.toFixed(1) + '" r="' + (ds.w * 0.6).toFixed(1)
          + '" fill="' + clr + '" fill-opacity="' + ds.o + '"/>');
      }
    }
    flush();

    const svg = '<svg class="tone-svg" viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">'
      + '<line x1="0" y1="31.7" x2="1000" y2="31.7" stroke="rgba(255,92,168,0.18)" stroke-dasharray="10,12" stroke-width="1"/>'
      + '<line x1="0" y1="65" x2="1000" y2="65" stroke="rgba(255,255,255,0.28)" stroke-dasharray="14,10" stroke-width="1"/>'
      + '<polygon points="' + polyPts.join(' ') + '" fill="' + spkColor + '" fill-opacity="0.26"/>'
      + paths.join('') + '</svg>';
    return {svg, badge: badgeHtml, tip, hasCurve: true};
  }

  function buildCustomTrackBlock(seg, trk, posStyle, hasOv) {
    const raw = seg.fields ? seg.fields[trk.id] : null;
    if (raw == null || raw === '') return '';
    const isObj = typeof raw === 'object' && !Array.isArray(raw);
    const clr = (isObj && raw.color) || trk.color || '#a9d6ff';
    const note = isObj && raw.note ? ' — ' + raw.note : '';
    const style = posStyle + ';--trk-color:' + clr;

    if (trk.type === 'meter') {
      const val = isObj ? Number(raw.value ?? raw.score ?? 0) : Number(raw);
      if (!Number.isFinite(val)) return '';
      const lbl = (isObj && raw.label) ? String(raw.label) : ((val >= 0 && !trk.unsigned ? '+' : '') + val.toFixed(2));
      let fillHtml = '';
      if (trk.unsigned) {
        const pct = Math.max(0, Math.min(100, val * 100));
        fillHtml = '<span class="meter-fill" style="left:0;width:' + pct.toFixed(1) + '%;background:' + clr + '"></span>';
      } else {
        const clamped = Math.max(-1, Math.min(1, val));
        const pct = Math.abs(clamped) * 50;
        const left = clamped >= 0 ? 50 : 50 - pct;
        const barClr = clamped >= 0 ? '#ffd240' : '#7cc4ff';
        fillHtml = '<span class="meter-mid"></span><span class="meter-fill" style="left:'
          + left.toFixed(1) + '%;width:' + pct.toFixed(1) + '%;background:' + barClr + '"></span>';
      }
      return '<div class="lane-block lb-custom-meter' + (hasOv ? ' has-ov' : '') + '" data-index="' + seg.index
        + '" style="' + style + '" title="' + html(trk.label + ': ' + lbl + note) + '">'
        + fillHtml + '<span class="meter-lbl">' + html(lbl) + '</span></div>';
    }
    if (trk.type === 'tag') {
      const lbl = isObj ? String(raw.label ?? raw.value ?? '') : String(raw);
      if (!lbl) return '';
      const scoreTxt = isObj && raw.score != null ? ' ' + Math.round(Number(raw.score) * 100) + '%' : '';
      return '<div class="lane-block lb-custom-tag' + (hasOv ? ' has-ov' : '') + '" data-index="' + seg.index
        + '" style="' + style + '" title="' + html(trk.label + ': ' + lbl + scoreTxt + note) + '">'
        + '<span class="tag-dot"></span><span>' + html(lbl + scoreTxt) + '</span></div>';
    }
    const txt = isObj ? String(raw.text ?? raw.label ?? '') : String(raw);
    if (!txt) return '';
    return '<div class="lane-block lb-custom-text' + (hasOv ? ' has-ov' : '') + '" data-index="' + seg.index
      + '" style="' + style + '" title="' + html(trk.label + ': ' + txt + note) + '">' + html(txt) + '</div>';
  }

  function applyToneState() {
    const active = hasProsody && showTone;
    const btn = document.getElementById('toggle-tone');
    if (btn) {
      btn.hidden = !hasProsody;
      btn.setAttribute('aria-pressed', String(active));
    }
    const lBtn = document.getElementById('open-labels-menu');
    if (lBtn) {
      lBtn.hidden = !hasProsody;
      lBtn.setAttribute('aria-pressed', String(hasProsody && metaLabels.enabled));
    }
    const leg = document.getElementById('tone-legend');
    if (leg) {
      leg.hidden = !active;
      if (active) {
        const spkNotes = Object.entries(prosodySpeakers || {}).map(([s, st]) => {
          const c = model ? model.colorOf(s).fg : '#fff';
          return '<span class="tl-spk" style="color:' + c + '">' + html(s) + ' '
            + Math.round(st.median_f0_hz || 0) + 'Hz · ' + (st.median_sps || 0).toFixed(1) + 'syl/s</span>';
        }).join('');
        leg.innerHTML =
          '<span><strong>Tone:</strong> height = pitch vs baseline</span>'
          + '<span><span class="tone-swatch" style="background:#9aa7ff"></span>low (&le;−3.5st)</span>'
          + '<span><span class="tone-swatch" style="background:#7cc4ff"></span>normal</span>'
          + '<span><span class="tone-swatch" style="background:#ffd240"></span>high (+5st)</span>'
          + '<span><span class="tone-swatch" style="background:#ff5ca8"></span>falsetto (+9.5st+)</span>'
          + '<span>thickness/fill = loudness</span>'
          + spkNotes;
      }
    }
    if (model) {
      renderLanesWindow(true);
      renderAllFloatCards(true);
      applySplitLayout();
    }
  }

  function clampWinStart(start, winDur) {
    if (!model) return 0;
    const dur = winDur || lanesWindow;
    const lo = model.clipStart || 0;
    const hi = Math.max(lo, (model.clipEnd || model.duration) - dur);
    return Math.max(lo, Math.min(start, hi));
  }

  function buildRulerHtml(winStart, winDur, isCompact) {
    const winEnd = winStart + winDur;
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
      if (width < (isCompact ? 3.5 : 2.6)) return '';
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
      const label = clipText(n.title || ci.label, isCompact ? 13 : 18);
      return '<span class="ruler-note-pin" data-ann-id="' + html(n.id) + '" data-t="' + n.t
        + '" style="left:' + pct.toFixed(3) + '%;--cat-color:' + ci.color + '" title="'
        + html(tip) + '">' + html(label) + '</span>';
    }).join('');
    const bands = visibleOverlaps.map(ov => {
      const left = ((Math.max(ov.start, winStart) - winStart) / winDur) * 100;
      const right = ((Math.min(ov.end, winEnd) - winStart) / winDur) * 100;
      return '<div class="lane-ov-band" style="left:' + left.toFixed(3)
        + '%;width:' + Math.max(0.25, right - left).toFixed(3) + '%"></div>';
    }).join('');

    return {
      rulerHtml: ticks.join('') + pills + annSpans + notePins + '<div class="lane-playhead"></div>',
      gridHtml: gridlines.join(''),
      bandsHtml: bands,
    };
  }

  function buildSpeakerRowsHtml(spk, spkCfg, winStart, winDur, trackPx, gridHtml, bandsHtml, isFloat) {
    const winEnd = winStart + winDur;
    const c = model.colorOf(spk);
    const segs = model.bySpeaker.get(spk) || [];
    const scaleMul = isFloat ? (spkCfg.scale || 1) : (spkCfg.dockScale || 1);
    const measurer = trackPx > 0
      ? getLaneMeasurer((isFloat ? 0.84 : (hasStudy ? 0.9 : 0.88)) * scaleMul)
      : null;
    const glossMeasurer = hasStudy && trackPx > 0
      ? getLaneMeasurer((isFloat ? 0.74 : 0.77) * scaleMul)
      : null;
    const filter = document.getElementById('transcript-filter')?.value || 'all';
    const isFiltered = !isFloat && filter === 'spk:' + spk;
    const rowCls = isFloat ? 'fc-row' : 'lane-row';
    const lblCls = isFloat ? 'fc-label' : 'lane-label';
    const trkCls = isFloat ? 'fc-track' : 'lane-track';
    const rows = spkCfg.rows || [defaultPrimaryKind()];
    let renderedPulses = false;
    const allAnns = mergedAnnotations(true);

    return rows.map((kind, ri) => {
      const sameAsPrev = ri > 0;
      const hasToneCurve = hasProsody && showTone && spkCfg.showTone && isPrimaryStreamKind(kind);
      const baseRem = rowBaseRem(kind, hasToneCurve);
      const blocks = [];

      if (kind === 'analysis') {
        for (const a of allAnns) {
          const aEnd = Math.max(a.end || 0, a.t + 3.0);
          if (aEnd <= winStart || a.t >= winEnd) continue;
          const s0 = Math.max(a.t, winStart);
          const s1 = Math.min(aEnd, winEnd);
          const left = ((s0 - winStart) / winDur) * 100;
          const width = Math.max(0.8, ((s1 - s0) / winDur) * 100);
          const ci = catInfo(a.cat);
          const label = a.title || ci.label;
          blocks.push(
            '<div class="lane-block lb-analysis" data-t="' + a.t + '" style="left:' + left.toFixed(3)
            + '%;width:' + width.toFixed(3) + '%;--cat-color:' + ci.color + '"'
            + ' title="' + html('[' + ci.label + '] ' + label + (a.note ? ' — ' + a.note : '')) + '">'
            + html(label) + '</div>'
          );
        }
      } else {
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
          } else if (kind === 'es-std') {
            if (seg.std) {
              blocks.push(
                '<div class="lane-block lb-std' + (hasOv ? ' has-ov' : '') + '" data-index="' + seg.index + '"'
                + ' style="' + posStyle + '" title="' + html(spk + ' (neutral ES): ' + seg.std) + '">'
                + html(seg.std) + '</div>'
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
          } else if (kind === 'delivery') {
            const pr = prosodyByIndex.get(seg.index);
            if (pr) {
              const parts = [];
              if (pr.pk >= 6) parts.push('pk ' + fmtSigned(pr.pk) + 'st');
              else parts.push(fmtSigned(pr.md) + 'st');
              parts.push(fmtSigned(pr.db) + 'dB');
              parts.push((Number(pr.rt) || 1).toFixed(2) + '×');
              if (Array.isArray(pr.tg) && pr.tg.length) parts.push(pr.tg.slice(0, 2).join(','));
              blocks.push(
                '<div class="lane-block lb-delivery' + (hasOv ? ' has-ov' : '') + '" data-index="' + seg.index + '"'
                + ' style="' + posStyle + '" title="' + html(parts.join(' · ')) + '">'
                + html(parts.join(' · ')) + '</div>'
              );
            }
          } else if (kind.startsWith('track:')) {
            const trk = CUSTOM_TRACK_MAP.get(kind.slice(6));
            if (trk) {
              const b = buildCustomTrackBlock(seg, trk, posStyle, hasOv);
              if (b) blocks.push(b);
            }
          } else {
            const withFuri = kind === 'ja-furi';
            const content = buildLaneBlockContent(seg, s0, s1, blockPx, info, measurer, withFuri);
            const extraBlkCls = kind === 'ja-furi' ? ' ja-furi-blk' : kind === 'ja' ? ' ja-plain-blk' : '';
            const tone = buildToneOverlay(seg, s0, s1, blockPx, c.fg, spkCfg);
            const toneCls = tone && tone.hasCurve ? ' has-tone-blk' : '';
            const fullTitle = title + (tone ? tone.tip : '');
            blocks.push(
              '<div class="lane-block' + extraBlkCls + toneCls + (hasOv ? ' has-ov' : '') + content.cls + '"'
              + ' data-index="' + seg.index + '" style="' + posStyle + '"'
              + ' title="' + html(fullTitle) + '">' + (tone ? tone.svg + tone.badge : '') + content.inner + '</div>'
            );
          }
        }
      }

      const showPulses = !renderedPulses && spkCfg.showPulses && isPrimaryStreamKind(kind);
      if (showPulses) renderedPulses = true;
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

      const av = avatarHtml(spk, isFloat ? 'av-sm' : (sameAsPrev ? 'av-sm' : ''));
      const kindShort = STREAM_MAP.get(kind)?.short || kind;
      const grip = !isFloat && !sameAsPrev ? '<span class="lbl-grip" aria-hidden="true">⋮</span>' : '';
      const cfgBtn = !sameAsPrev
        ? '<button type="button" class="lbl-cfg-btn" data-cfg-spk="' + html(spk) + '" title="Configure ' + html(spk) + ' size, position &amp; subcomponents">⚙</button>'
        : '';
      const labelHtml = !sameAsPrev
        ? grip + av + '<span class="lbl-txt">' + html(spk) + '</span>'
          + (rows.length > 1 || !isPrimaryStreamKind(kind) ? '<span class="lbl-kind">' + html(kindShort) + '</span>' : '')
          + cfgBtn
        : av + '<span class="lbl-txt">' + html(kindShort) + '</span>';

      return '<div class="' + rowCls + '" style="--row-base-rem:' + baseRem.toFixed(2) + 'rem"'
        + ' data-row-spk="' + html(spk) + '" data-row-kind="' + html(kind) + '">'
        + '<div class="' + lblCls + (av ? ' has-avatar' : '') + (sameAsPrev ? ' sub-lbl' : '') + (isFiltered ? ' filtered' : '') + '"'
        + ' data-speaker="' + html(spk) + '" style="--spk-color:' + c.fg + '"'
        + ' title="' + (isFloat ? 'Right-click or ⚙ to configure' : 'Drag up/down to reorder · Click to filter · Right-click or ⚙ to configure') + '">'
        + labelHtml + '</div>'
        + '<div class="' + trkCls + '">' + gridHtml + bandsHtml + pulses + blocks.join('')
        + '<div class="lane-playhead"></div></div></div>';
    }).join('');
  }

  function indexLaneBlocks(container) {
    const map = new Map();
    container.querySelectorAll('.lane-block[data-index]').forEach(el => {
      const idx = Number(el.dataset.index);
      if (!map.has(idx)) map.set(idx, []);
      map.get(idx).push(el);
    });
    return map;
  }

  function renderLanesWindow(force) {
    if (!model) return;
    const stage = document.getElementById('lanes-stage');
    const ruler = document.getElementById('lanes-ruler');
    const body = document.getElementById('lanes-body');
    if (!stage || !ruler || !body) return;
    applyTimerPosClasses();

    const dockedSpks = speakerOrder.filter(s => speakerConfigs[s]?.visible && speakerConfigs[s]?.placement === 'dock');
    const winStart = clampWinStart(lanesWinStart, lanesWindow);
    const winDur = lanesWindow;
    const trackPx = ruler.getBoundingClientRect().width || stage.getBoundingClientRect().width - 170 || 600;
    if (!force && Math.abs(winStart - renderedWinStart) < 0.001 && winDur === renderedWinDur && Math.abs(trackPx - lastLaneTrackPx) < 1) {
      return;
    }
    lanesWinStart = winStart;
    renderedWinStart = winStart;
    renderedWinDur = winDur;
    lastLaneTrackPx = trackPx;

    const rData = buildRulerHtml(winStart, winDur, false);
    ruler.innerHTML = rData.rulerHtml;

    if (!dockedSpks.length) {
      if (anyFloatingSpeakers()) {
        body.innerHTML = '';
        stage.classList.add('all-floating');
        document.getElementById('lanes-ruler-row').hidden = true;
      } else {
        stage.classList.remove('all-floating');
        document.getElementById('lanes-ruler-row').hidden = false;
        body.innerHTML = '<div class="lanes-empty">No speakers shown — click Layers or right-click here to add speakers</div>';
      }
      playheadEls = [...stage.querySelectorAll('.lane-playhead')];
      blockElsByIndex = new Map();
      return;
    }
    stage.classList.remove('all-floating');
    document.getElementById('lanes-ruler-row').hidden = stageCfg.timerPos === 'hidden';

    const fullW = stage.getBoundingClientRect().width || 900;
    body.innerHTML = dockedSpks.map((spk, di) => {
      const sc = speakerConfigs[spk];
      const c = model.colorOf(spk);
      const isNarrow = sc.dockWidth < 99.5;
      const deckW = isNarrow ? sc.dockWidth + '%' : '100%';
      const mLeft = isNarrow ? (sc.dockAlign === 'center' ? 'auto' : sc.dockAlign === 'right' ? 'auto' : '0') : '0';
      const mRight = isNarrow ? (sc.dockAlign === 'center' ? 'auto' : sc.dockAlign === 'left' ? 'auto' : '0') : '0';
      const estTrackPx = Math.max(120, fullW * (sc.dockWidth / 100) - (sc.showLabel ? 170 : 0));
      const rowsHtml = buildSpeakerRowsHtml(spk, sc, winStart, winDur, estTrackPx, rData.gridHtml, rData.bandsHtml, false);
      const miniRuler = isNarrow && stageCfg.timerPos !== 'hidden'
        ? '<div class="deck-mini-ruler"><div class="lanes-gutter"><span class="live-clock">' + clock(currentTime()) + '</span></div>'
          + '<div class="lanes-ruler">' + rData.rulerHtml + '</div></div>'
        : '';
      return '<div class="spk-deck' + (isNarrow ? ' deck-narrow' : '') + (sc.showLabel ? '' : ' no-gutter') + '"'
        + ' data-spk="' + html(spk) + '" data-deck-idx="' + di + '"'
        + ' style="--spk-scale:' + sc.dockScale.toFixed(2) + ';--spk-color:' + c.fg
        + ';width:' + deckW + ';margin-left:' + mLeft + ';margin-right:' + mRight + '">'
        + rowsHtml + miniRuler
        + '<div class="spk-deck-resizer" data-resize-spk="' + html(spk) + '" title="Drag to resize ' + html(spk) + ' scroller height"></div>'
        + '</div>';
    }).join('');

    playheadEls = [...stage.querySelectorAll('.lane-playhead')];
    blockElsByIndex = indexLaneBlocks(body);
    updatePlayheadOn(playheadEls, blockElsByIndex, currentTime(), lanesWinStart, lanesWindow);
  }

  // Independent floating per-speaker cards on #stage-float-layer
  function renderAllFloatCards(force) {
    const layer = document.getElementById('stage-float-layer');
    if (!layer || !model) return;
    const floatSpks = speakerOrder.filter(s => speakerConfigs[s]?.visible && speakerConfigs[s]?.placement === 'float');
    if (!floatSpks.length) {
      if (layer.childElementCount) layer.innerHTML = '';
      floatRenderState.clear();
      return;
    }

    // Remove stale cards
    const wantSet = new Set(floatSpks);
    for (const child of [...layer.children]) {
      const s = child.dataset.spk;
      if (!wantSet.has(s)) { child.remove(); floatRenderState.delete(s); }
    }

    const t = currentTime();
    const stageW = layer.clientWidth || document.getElementById('player-wrap')?.clientWidth || 900;
    for (const spk of floatSpks) {
      const sc = speakerConfigs[spk];
      const c = model.colorOf(spk);
      let card = [...layer.children].find(el => el.dataset && el.dataset.spk === spk) || null;
      if (!card) {
        card = document.createElement('div');
        card.className = 'spk-float-card';
        card.setAttribute('data-spk', spk);
        card.dataset.spk = spk;
        card.innerHTML =
          '<div class="fc-bar" data-move-spk="' + html(spk) + '" title="Drag to move ' + html(spk) + ' anywhere on stage">'
          + '<span class="fc-bar-grip">⋮⋮</span>'
          + avatarHtml(spk, 'av-xs')
          + '<span class="fc-bar-name">' + html(spk) + '</span>'
          + '<span class="fc-bar-btns">'
          + '<button type="button" class="fc-bar-btn" data-fc-act="cfg" data-spk="' + html(spk) + '" title="Subcomponents &amp; size">⚙</button>'
          + '<button type="button" class="fc-bar-btn" data-fc-act="dock" data-spk="' + html(spk) + '" title="Dock below video">⊟</button>'
          + '<button type="button" class="fc-bar-btn" data-fc-act="hide" data-spk="' + html(spk) + '" title="Hide speaker">×</button>'
          + '</span></div>'
          + '<div class="fc-stage"><div class="fc-rows"></div>'
          + '<div class="fc-ruler-row"><div class="fc-gutter"><span class="live-clock">0:00</span></div>'
          + '<div class="fc-ruler"></div></div></div>'
          + '<div class="fc-rz fc-rz-w" data-rz="w" data-spk="' + html(spk) + '"></div>'
          + '<div class="fc-rz fc-rz-e" data-rz="e" data-spk="' + html(spk) + '"></div>'
          + '<div class="fc-rz fc-rz-s" data-rz="s" data-spk="' + html(spk) + '"></div>'
          + '<div class="fc-rz fc-rz-se" data-rz="se" data-spk="' + html(spk) + '"></div>';
        layer.appendChild(card);
      }
      card.style.left = sc.x.toFixed(2) + '%';
      card.style.top = sc.y.toFixed(2) + '%';
      card.style.width = sc.w.toFixed(2) + '%';
      card.style.setProperty('--spk-color', c.fg);
      card.style.setProperty('--fc-scale', sc.scale.toFixed(3));
      card.style.setProperty('--fc-alpha', String(sc.alpha));
      card.style.setProperty('--spk-scale', sc.scale.toFixed(3));

      const fcStage = card.querySelector('.fc-stage');
      const tPos = effectiveTimerPos(sc);
      fcStage.classList.toggle('timer-bottom', tPos === 'bottom');
      fcStage.classList.toggle('timer-top', tPos === 'top');
      fcStage.classList.toggle('timer-hidden', tPos === 'hidden');
      fcStage.classList.toggle('no-gutter', !sc.showLabel);

      const winDur = sc.windowSec > 0 ? sc.windowSec : Math.min(FLOAT_WINDOW_MAX, Math.max(4, lanesWindow));
      const prev = floatRenderState.get(spk);
      const cardPx = Math.max(170, stageW * (sc.w / 100));
      const trackPx = Math.max(100, cardPx - (sc.showLabel ? 98 : 0));
      const outOfView = !prev || t < prev.winStart + winDur * 0.06 || t > prev.winStart + winDur * 0.84;
      if (force || !prev || outOfView || prev.winDur !== winDur || Math.abs(trackPx - prev.trackPx) >= 6) {
        const winStart = clampWinStart(outOfView || !prev ? t - winDur * 0.16 : prev.winStart, winDur);
        const rData = buildRulerHtml(winStart, winDur, true);
        const fcRuler = card.querySelector('.fc-ruler');
        const fcRows = card.querySelector('.fc-rows');
        fcRuler.innerHTML = rData.rulerHtml;
        fcRows.innerHTML = buildSpeakerRowsHtml(spk, sc, winStart, winDur, trackPx, rData.gridHtml, rData.bandsHtml, true);
        const st = {
          winStart, winDur, trackPx,
          playheadEls: [...card.querySelectorAll('.lane-playhead')],
          blockElsByIndex: indexLaneBlocks(fcRows),
          clockEl: card.querySelector('.live-clock'),
        };
        floatRenderState.set(spk, st);
        updatePlayheadOn(st.playheadEls, st.blockElsByIndex, t, winStart, winDur);
        if (st.clockEl) st.clockEl.textContent = clock(t);
      } else {
        updatePlayheadOn(prev.playheadEls, prev.blockElsByIndex, t, prev.winStart, prev.winDur);
        if (prev.clockEl) prev.clockEl.textContent = clock(t);
      }
    }
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

  function updateClocks(time) {
    const s = clock(time);
    if (s === lastClockStr) return;
    lastClockStr = s;
    const mainClk = document.getElementById('lanes-live-clock');
    if (mainClk) mainClk.textContent = s;
    document.querySelectorAll('.deck-mini-ruler .live-clock').forEach(el => { el.textContent = s; });
    for (const st of floatRenderState.values()) {
      if (st.clockEl) st.clockEl.textContent = s;
    }
  }

  function updateLanesPlayhead(time) {
    if (!model) return;
    updateClocks(time);
    if (lanesFollow && (time < lanesWinStart + lanesWindow * 0.08 || time > lanesWinStart + lanesWindow * 0.85)) {
      const targetStart = clampWinStart(time - lanesWindow * 0.25, lanesWindow);
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
      const c0 = model.clipStart || 0;
      const cSpan = Math.max(1, (model.clipEnd || model.duration) - c0);
      if (vp) {
        vp.style.left = Math.max(0, Math.min(100, ((lanesWinStart - c0) / cSpan) * 100)).toFixed(3) + '%';
        vp.style.width = Math.min(100, (lanesWindow / cSpan) * 100).toFixed(3) + '%';
      }
      if (mp) mp.style.left = Math.max(0, Math.min(100, ((time - c0) / cSpan) * 100)).toFixed(3) + '%';
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
    if (model) applySplitLayout();
  }
  function applyBadgesState() {
    const btn = document.getElementById('toggle-badges');
    if (btn) btn.setAttribute('aria-pressed', String(showBadges));
    renderLanesWindow(true);
    renderAllFloatCards(true);
    paintLanesStatus();
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
    const c0 = model.clipStart || 0;
    const cSpan = Math.max(1, (model.clipEnd || model.duration) - c0);
    model.speakers.forEach((spk, i) => {
      ctx.fillStyle = model.colorOf(spk).fg;
      const y = Math.floor(i * rowH) + 1;
      const h = Math.max(2, Math.floor(rowH) - 2);
      for (const seg of (model.bySpeaker.get(spk) || [])) {
        ctx.fillRect(((seg.start - c0) / cSpan) * width, y, Math.max(1.5, ((seg.end - seg.start) / cSpan) * width), h);
      }
    });
    ctx.fillStyle = '#ffd240';
    for (const ov of model.overlapSpans) {
      const x0 = ((ov.start - c0) / cSpan) * width;
      const w = Math.max(2, ((ov.end - ov.start) / cSpan) * width);
      ctx.fillRect(x0, 0, w, Math.max(3, Math.round(height * 0.22)));
      ctx.fillRect(x0, height - Math.max(3, Math.round(height * 0.22)), w, Math.max(3, Math.round(height * 0.22)));
    }
    if (showAnnotations) {
      for (const a of visibleTimelineAnnotations()) {
        const x0 = ((a.t - c0) / cSpan) * width;
        const w = Math.max(3, (((a.end && a.end > a.t ? a.end : a.t) - a.t) / cSpan) * width);
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

  function activeCustomTrackIds() {
    const out = new Set();
    for (const s of speakerOrder) {
      const sc = speakerConfigs[s];
      if (!sc || !sc.visible) continue;
      for (const r of sc.rows) {
        if (r.startsWith('track:')) out.add(r.slice(6));
      }
    }
    return [...out];
  }

  function paintTranscript() {
    const box = document.getElementById('caption-box');
    const query = (document.getElementById('transcript-search').value || '').trim().toLowerCase();
    const filter = document.getElementById('transcript-filter').value || 'all';
    const shownTracks = activeCustomTrackIds();

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
          const gap = !isCjk && wi && !/^[,.;:!?)]/.test(w.word) ? ' ' : '';
          const ovColor = wordOverlapColor(w, info);
          const vAttr = w.vid >= 0 ? ' data-v="' + w.vid + '"' : '';
          const tipAttr = w.gloss ? ' title="' + html(w.word + ' — ' + w.gloss) + '"' : '';
          let span = '<span class="word' + (ovColor ? ' word-ov' : '') + (w.vid >= 0 ? ' has-vocab' : '') + '"'
            + ' data-t="' + w.start + '" data-e="' + w.end + '"' + vAttr + tipAttr;
          if (ovColor) span += ' style="--ov-color:' + ovColor + '"';
          span += '>' + (isCjk && w.ruby ? rubyHtml(w.ruby, w.word) : html(w.word)) + '</span>';
          const chips = info && info.chipsByWord.get(wi);
          if (chips) span += chips.map(chipHtml).join('');
          return gap + span;
        }).join('');
      } else {
        body = html(seg.text);
      }
      let extraHtml = '';
      if (hasStudy && seg.std && seg.std !== seg.text) {
        extraHtml += '<div class="segment-std" title="Neutral Spanish equivalent">→ ' + html(seg.std) + '</div>';
      }
      if (hasStudy && showGloss && seg.tokens.length) {
        const pairs = seg.tokens.filter(tk => tk.gloss).map(tk =>
          '<span class="ww-pair" data-t="' + tk.start + '"' + (tk.vid >= 0 ? ' data-v="' + tk.vid + '"' : '') + '>'
          + '<span class="ww-ja"' + (doc.language ? ' lang="' + html(doc.language) + '"' : '') + '>' + html(tk.word) + '</span>' + html(tk.gloss) + '</span>'
        ).join('');
        if (pairs) extraHtml += '<div class="segment-ww">' + pairs + '</div>';
      }
      if (hasStudy && showEnglish && seg.en) {
        extraHtml += '<div class="segment-en">' + html(seg.en) + '</div>';
      }
      if (shownTracks.length && seg.fields) {
        const chips = [];
        for (const tid of shownTracks) {
          const val = seg.fields[tid];
          if (val == null || val === '') continue;
          const trk = CUSTOM_TRACK_MAP.get(tid);
          if (!trk) continue;
          const txt = typeof val === 'object' ? (val.label ?? val.text ?? val.value ?? '') : String(val);
          if (txt === '') continue;
          chips.push('<span class="seg-field-chip" style="--trk-color:' + html(trk.color) + '"><strong>'
            + html(trk.short) + ':</strong> ' + html(txt) + '</span>');
        }
        if (chips.length) extraHtml += '<div class="segment-fields">' + chips.join('') + '</div>';
      }
      const ovMark = hasOv ? '<span class="ov-mark">∥</span>' : '';
      const segLang = doc.language ? ' lang="' + html(doc.language) + '"' : '';
      return '<div class="segment' + (query && vis ? ' match' : '') + (info && info.overlapsPrev ? ' ov-child' : '') + '"'
        + (vis ? '' : ' hidden') + ' style="--spk-color:' + color.fg + '"'
        + ' data-index="' + seg.index + '" data-start="' + seg.start + '" data-end="' + seg.end + '"'
        + (partnerIds ? ' data-partners="' + partnerIds + '"' : '') + '>'
        + '<span class="segment-time">' + clock(seg.start) + ovMark + '</span>'
        + '<span class="segment-speaker">' + avatarHtml(seg.speaker, 'av-xs') + '<span>' + html(seg.speaker || 'Unassigned') + '</span></span>'
        + '<div class="segment-text"><div' + (isCjk ? ' class="segment-ja"' : '') + segLang + '>' + body + '</div>'
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
    updateLanesPlayhead(t);
    renderAllFloatCards(force);
    updateLiveAnnotation(t, force);
  }

  function tick() {
    if (!ytPlaying) { rafId = 0; return; }
    const t = currentTime();
    updateLanesPlayhead(t);
    renderAllFloatCards(false);
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
    lanesWinStart = clampWinStart(target.start - lanesWindow * 0.25, lanesWindow);
    renderLanesWindow(true);
    seekAndPlay(Math.max(0, target.start - 0.3));
  }

  function applySplitLayout() {
    const main = document.querySelector('main');
    const pw = document.getElementById('player-wrap');
    const panel = document.getElementById('transcript-panel');
    const btn = document.getElementById('toggle-split-view');
    if (isPlayerFullscreen()) {
      if (pw) pw.style.height = '';
      syncDockHost();
      fitVideoBox();
      renderLanesWindow(true);
      renderAllFloatCards(true);
      return;
    }
    const active = splitView && window.innerWidth >= 1040;
    main.classList.toggle('split-view', active);
    if (btn) {
      btn.setAttribute('aria-pressed', String(splitView));
      btn.textContent = splitView ? 'Split' : 'Below';
    }
    const hdr = document.querySelector('header.top');
    const viz = document.getElementById('viz-stage');
    const hdrH = hdr ? Math.ceil(hdr.getBoundingClientRect().height) : 50;
    const vizH = viz ? Math.ceil(viz.getBoundingClientRect().height) : 210;
    const budgetH = Math.max(220, window.innerHeight - hdrH - vizH - 22);
    const ar = parseAspect(stageCfg.videoAspect);

    if (!active) {
      // Stacked / Below-video Theater mode: strictly cap height so wide monitors NEVER overzoom!
      main.style.removeProperty('--split-col-w');
      const autoH = Math.max(220, Math.min(Math.round(window.innerHeight * 0.72), budgetH));
      const targetH = stageCfg.winStageH > 0
        ? Math.max(180, Math.min(Math.round(window.innerHeight * 0.88), stageCfg.winStageH))
        : autoH;
      if (pw) pw.style.height = targetH + 'px';
      if (panel) panel.style.height = '';
    } else {
      const autoH = Math.max(230, Math.min(Math.round(window.innerHeight * 0.66), budgetH));
      const targetH = stageCfg.winStageH > 0
        ? Math.max(200, Math.min(Math.round(window.innerHeight * 0.85), stageCfg.winStageH))
        : autoH;
      const maxColW = Math.min(Math.round(window.innerWidth * 0.65), Math.round(targetH * ar));
      main.style.setProperty('--split-col-w', Math.max(360, maxColW) + 'px');
      if (pw) pw.style.height = targetH + 'px';
      if (panel) panel.style.height = targetH + 'px';
    }

    fitVideoBox();
    requestAnimationFrame(() => {
      fitVideoBox();
      if (active && pw && panel) {
        const h = Math.round(pw.getBoundingClientRect().height);
        if (h >= 200) panel.style.height = h + 'px';
      }
      if (showMinimap) drawMinimap();
      renderLanesWindow(false);
      renderAllFloatCards(true);
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
    buildStreamRegistry();
    populateCategorySelects();
    paintNotes();
    renderLanesWindow(true);
    renderAllFloatCards(true);
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
    renderAllFloatCards(true);
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
          + '" data-orig-clock="' + html(clock(n.t)) + '" data-orig-val="' + n.t + '" placeholder="start">'
          + '<input type="text" class="ed-time ed-end" value="' + html(endClk)
          + '" data-orig-clock="' + html(endClk) + '" data-orig-val="' + (n.end || '') + '" placeholder="end">'
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
    if (addedIdx !== -1) annState.added.splice(addedIdx, 1);
    else {
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
    lanesWinStart = clampWinStart(target.t - lanesWindow * 0.25, lanesWindow);
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
      renderAllFloatCards(true);
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
    const langAttr = doc && doc.language ? ' lang="' + html(doc.language) + '"' : '';
    el.hidden = false;
    el.innerHTML =
      '<span class="wi-ja"' + langAttr + '>' + (isCjk && v.ruby ? rubyHtml(v.ruby, v.base) : html(v.base)) + '</span>'
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
    if (filter === 'ar') return diff === 'ar';
    if (filter === 'econ') return diff === 'econ';
    if (filter === 'c1+') return diff === 'c1' || diff === 'econ' || diff === 'ar';
    if (filter === 'b2+') return diff === 'b2' || diff === 'c1' || diff === 'econ' || diff === 'ar';
    if (filter === 'n1+') return diff === 'n1' || diff === 'slang' || diff === 'mtg';
    if (filter === 'n2+') return diff === 'n2' || diff === 'n1' || diff === 'slang' || diff === 'mtg';
    return diff !== 'basic';
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
      const vLangAttr = doc && doc.language ? ' lang="' + html(doc.language) + '"' : '';
      return '<div class="vocab-row' + (isKnown ? ' is-known' : '') + '" data-vid="' + v.id + '">'
        + '<div class="v-word"><span class="v-ja"' + vLangAttr + '>'
        + (isCjk && v.ruby ? rubyHtml(v.ruby, v.base) : html(v.base)) + '</span>'
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

  function wirePopoverInteractions() {
    const pop = document.getElementById('cfg-popover');
    if (!pop) return;
    pop.addEventListener('click', ev => {
      ev.stopPropagation();
      if (ev.target.closest('[data-pop-close]')) { closeCfgPopover(); return; }

      const ax = Number(pop.dataset.anchorX) || 120;
      const ay = Number(pop.dataset.anchorY) || 120;

      // Layout popover actions
      const layBtn = ev.target.closest('button[data-lay]');
      if (layBtn) {
        const k = layBtn.dataset.lay;
        const v = layBtn.dataset.val;
        if (k === 'videoScale') stageCfg.videoScale = clampNum(v, 0.4, 1.0, 1.0);
        else if (k === 'videoAlignX') stageCfg.videoAlignX = v;
        else if (k === 'videoAlignY') stageCfg.videoAlignY = v;
        else if (k === 'videoAspect') stageCfg.videoAspect = v;
        else if (k === 'fsDock') stageCfg.fsDock = v;
        else if (k === 'timerPos') stageCfg.timerPos = v;
        else if (k === 'handlesMode') stageCfg.handlesMode = v;
        else if (k === 'split') {
          splitView = v === 'true';
          localStorage.setItem('ovpub.split', String(splitView));
        } else if (k === 'all-dock') {
          setAllSpeakersPlacement('dock');
          renderLayoutPopover(ax, ay);
          return;
        } else if (k === 'all-float') {
          setAllSpeakersPlacement('float');
          renderLayoutPopover(ax, ay);
          return;
        } else if (k === 'matte-float') {
          stageCfg.videoScale = 0.76;
          stageCfg.videoAlignY = 'top';
          stageCfg.videoAlignX = 'center';
          stageCfg.handlesMode = 'show';
          saveStageCfg();
          applyHandlesMode();
          const total = speakerOrder.length;
          speakerOrder.forEach((spk, idx) => {
            const sc = speakerConfigs[spk];
            const slot = defaultFloatSlot(idx, total);
            sc.placement = 'float';
            sc.visible = true;
            sc.x = slot.x;
            sc.w = slot.w;
            sc.y = Math.max(77, slot.y);
          });
          refreshAllTimelines(true);
          renderLayoutPopover(ax, ay);
          return;
        } else if (k === 'toggle-fs') {
          closeCfgPopover();
          togglePlayerFullscreen();
          return;
        } else if (k === 'reset-layout') {
          stageCfg = {...DEFAULT_STAGE_CFG};
          saveStageCfg();
          applyHandlesMode();
          applyTimerPosClasses();
          setAllSpeakersPlacement('dock');
          applySplitLayout();
          renderLayoutPopover(ax, ay);
          return;
        }
        saveStageCfg();
        applyHandlesMode();
        applyTimerPosClasses();
        syncDockHost();
        applySplitLayout();
        renderLanesWindow(true);
        renderAllFloatCards(true);
        renderLayoutPopover(ax, ay);
        return;
      }

      // Meta-labels popover buttons
      const mlBtn = ev.target.closest('button[data-ml]');
      if (mlBtn) {
        const k = mlBtn.dataset.ml;
        const v = mlBtn.dataset.val;
        metaLabels[k] = k === 'maxPerBlock' ? Number(v) : v;
        saveMetaLabels();
        applyToneState();
        renderLabelsPopover(ax, ay);
        return;
      }

      // Per-speaker config buttons
      const scBtn = ev.target.closest('button[data-sc]');
      if (scBtn) {
        const spk = scBtn.dataset.spk;
        const act = scBtn.dataset.sc;
        const cfg = speakerConfigs[spk];
        if (!cfg) return;
        if (act === 'place') {
          cfg.placement = scBtn.dataset.val === 'float' ? 'float' : 'dock';
          refreshAllTimelines(true);
        } else if (act === 'order-up') {
          const i = speakerOrder.indexOf(spk);
          moveSpeakerOrder(i, i - 1);
        } else if (act === 'order-dn') {
          const i = speakerOrder.indexOf(spk);
          moveSpeakerOrder(i, i + 1);
        } else if (act === 'dockAlign') {
          cfg.dockAlign = scBtn.dataset.val;
          refreshAllTimelines(false);
        } else if (act === 'timerPos') {
          cfg.timerPos = scBtn.dataset.val;
          refreshAllTimelines(false);
        } else if (act === 'hide-spk') {
          cfg.visible = false;
          refreshAllTimelines(true);
          closeCfgPopover();
          return;
        } else if (act === 'reset-spk') {
          const idx = speakerOrder.indexOf(spk);
          speakerConfigs[spk] = makeDefaultSpeakerCfg(spk, idx, speakerOrder.length);
          refreshAllTimelines(true);
        }
        renderSpeakerConfigPopover(spk, ax, ay);
      }
    });

    pop.addEventListener('change', ev => {
      ev.stopPropagation();
      const t = ev.target;
      if (t.dataset.scRow) {
        const spk = t.dataset.spk;
        if (t.checked) addRowToSpeaker(spk, t.dataset.scRow);
        else removeRowFromSpeaker(spk, t.dataset.scRow);
        return;
      }
      if (t.dataset.scFlag) {
        const cfg = speakerConfigs[t.dataset.spk];
        if (cfg) {
          cfg[t.dataset.scFlag] = Boolean(t.checked);
          refreshAllTimelines(true);
        }
        return;
      }
      if (t.dataset.mlFlag) {
        metaLabels[t.dataset.mlFlag] = Boolean(t.checked);
        saveMetaLabels();
        applyToneState();
        return;
      }
      if (t.dataset.mlKind) {
        metaLabels.kinds[t.dataset.mlKind] = Boolean(t.checked);
        saveMetaLabels();
        applyToneState();
      }
    });

    pop.addEventListener('input', ev => {
      ev.stopPropagation();
      const t = ev.target;
      const metaEl = t.parentElement?.querySelector('.ctx-meta');
      if (t.dataset.layRng === 'videoScale') {
        stageCfg.videoScale = clampNum(Number(t.value) / 100, 0.4, 1.0, 1.0);
        if (metaEl) metaEl.textContent = Math.round(stageCfg.videoScale * 100) + '%';
        saveStageCfg();
        fitVideoBox();
        return;
      }
      if (t.dataset.scRng) {
        const cfg = speakerConfigs[t.dataset.spk];
        if (!cfg) return;
        const k = t.dataset.scRng;
        const raw = Number(t.value);
        if (k === 'w') {
          cfg.w = clampNum(raw, 18, 100, 48);
          cfg.x = Math.min(cfg.x, 100 - cfg.w);
          if (metaEl) metaEl.textContent = Math.round(cfg.w) + '%';
        } else if (k === 'scale') {
          cfg.scale = clampNum(raw / 100, 0.6, 1.8, 1.0);
          if (metaEl) metaEl.textContent = Math.round(cfg.scale * 100) + '%';
        } else if (k === 'alpha') {
          cfg.alpha = clampNum(raw / 100, 0.45, 0.98, 0.9);
          if (metaEl) metaEl.textContent = Math.round(cfg.alpha * 100) + '%';
        } else if (k === 'dockScale') {
          cfg.dockScale = clampNum(raw / 100, 0.65, 2.0, 1.0);
          if (metaEl) metaEl.textContent = Math.round(cfg.dockScale * 100) + '%';
        } else if (k === 'dockWidth') {
          cfg.dockWidth = clampNum(raw, 35, 100, 100);
          if (metaEl) metaEl.textContent = Math.round(cfg.dockWidth) + '%';
        }
        saveSpeakerConfigs();
        renderLanesWindow(true);
        renderAllFloatCards(true);
        if (k === 'dockScale') applySplitLayout();
      }
    });
    pop.addEventListener('contextmenu', ev => ev.preventDefault());
  }

  function wireFloatingCardInteractions() {
    const layer = document.getElementById('stage-float-layer');
    if (!layer) return;

    layer.addEventListener('contextmenu', ev => showStreamMenu(ev));

    layer.addEventListener('click', ev => {
      ev.stopPropagation();
      const actBtn = ev.target.closest('button[data-fc-act]');
      if (actBtn) {
        const spk = actBtn.dataset.spk;
        const act = actBtn.dataset.fcAct;
        const cfg = speakerConfigs[spk];
        if (!cfg) return;
        if (act === 'cfg') {
          const r = actBtn.getBoundingClientRect();
          renderSpeakerConfigPopover(spk, r.left, Math.max(12, r.top - 260));
        } else if (act === 'dock') {
          cfg.placement = 'dock';
          refreshAllTimelines(true);
        } else if (act === 'hide') {
          cfg.visible = false;
          refreshAllTimelines(true);
        }
        return;
      }
      const cfgBtn = ev.target.closest('button[data-cfg-spk]');
      if (cfgBtn) {
        const r = cfgBtn.getBoundingClientRect();
        renderSpeakerConfigPopover(cfgBtn.dataset.cfgSpk, r.left, Math.max(12, r.top - 260));
        return;
      }
      closeAllPopovers();
      const pin = ev.target.closest('.ruler-note-pin, .lb-analysis, .lane-pulse');
      if (pin && pin.dataset.t != null) { seekAndPlay(Number(pin.dataset.t)); return; }
      const lw = ev.target.closest('.lane-word[data-v]');
      if (lw) showWordInspector(Number(lw.dataset.v));
      const trk = ev.target.closest('.fc-track, .fc-ruler');
      if (!trk || !model) return;
      const card = trk.closest('.spk-float-card');
      const st = card ? floatRenderState.get(card.dataset.spk) : null;
      if (!st) return;
      const rect = trk.getBoundingClientRect();
      if (rect.width <= 0) return;
      const frac = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
      seekAndPlay(st.winStart + frac * st.winDur);
    });

    // Independent Move & Resize per floating speaker card
    let fDrag = null;
    layer.addEventListener('pointerdown', ev => {
      if (ev.button !== 0 || ev.target.closest('button')) return;
      if (stageCfg.handlesMode === 'hidden') return;
      const rzEl = ev.target.closest('.fc-rz[data-rz]');
      const mvEl = ev.target.closest('[data-move-spk]');
      if (!rzEl && !mvEl) return;
      const spk = rzEl ? rzEl.dataset.spk : mvEl.dataset.moveSpk;
      const cfg = speakerConfigs[spk];
      const card = [...layer.children].find(el => el.dataset && el.dataset.spk === spk) || null;
      if (!cfg || !card) return;
      ev.preventDefault();
      ev.stopPropagation();
      closeAllPopovers();
      const stageRect = layer.getBoundingClientRect();
      if (stageRect.width <= 0 || stageRect.height <= 0) return;
      fDrag = {
        mode: rzEl ? 'rz-' + rzEl.dataset.rz : 'move',
        spk, card, stageRect,
        startX: ev.clientX, startY: ev.clientY,
        origX: cfg.x, origY: cfg.y, origW: cfg.w, origScale: cfg.scale,
      };
      card.classList.add('is-moving');
      document.body.classList.add('is-dragging-layout');
      try { (rzEl || mvEl).setPointerCapture(ev.pointerId); } catch {}
    });

    window.addEventListener('pointermove', ev => {
      if (!fDrag) return;
      const cfg = speakerConfigs[fDrag.spk];
      if (!cfg) return;
      const dxPct = ((ev.clientX - fDrag.startX) / fDrag.stageRect.width) * 100;
      const dyPct = ((ev.clientY - fDrag.startY) / fDrag.stageRect.height) * 100;
      const dyPx = ev.clientY - fDrag.startY;
      const cardHPct = Math.min(85, ((fDrag.card.offsetHeight || 70) / fDrag.stageRect.height) * 100);

      if (fDrag.mode === 'move') {
        cfg.x = clampNum(fDrag.origX + dxPct, 0, Math.max(0, 100 - cfg.w), cfg.x);
        cfg.y = clampNum(fDrag.origY + dyPct, 0, Math.max(0, 100 - cardHPct), cfg.y);
        fDrag.card.style.left = cfg.x.toFixed(2) + '%';
        fDrag.card.style.top = cfg.y.toFixed(2) + '%';
      } else if (fDrag.mode === 'rz-e') {
        cfg.w = clampNum(fDrag.origW + dxPct, 18, 100 - cfg.x, cfg.w);
        fDrag.card.style.width = cfg.w.toFixed(2) + '%';
      } else if (fDrag.mode === 'rz-w') {
        const rightEdge = fDrag.origX + fDrag.origW;
        const nextX = clampNum(fDrag.origX + dxPct, 0, rightEdge - 18, cfg.x);
        cfg.x = nextX;
        cfg.w = clampNum(rightEdge - nextX, 18, 100, cfg.w);
        fDrag.card.style.left = cfg.x.toFixed(2) + '%';
        fDrag.card.style.width = cfg.w.toFixed(2) + '%';
      } else if (fDrag.mode === 'rz-s') {
        cfg.scale = clampNum(fDrag.origScale + dyPx / 120, 0.6, 1.8, cfg.scale);
        fDrag.card.style.setProperty('--fc-scale', cfg.scale.toFixed(3));
        fDrag.card.style.setProperty('--spk-scale', cfg.scale.toFixed(3));
      } else if (fDrag.mode === 'rz-se') {
        cfg.w = clampNum(fDrag.origW + dxPct, 18, 100 - cfg.x, cfg.w);
        cfg.scale = clampNum(fDrag.origScale + dyPx / 120, 0.6, 1.8, cfg.scale);
        fDrag.card.style.width = cfg.w.toFixed(2) + '%';
        fDrag.card.style.setProperty('--fc-scale', cfg.scale.toFixed(3));
        fDrag.card.style.setProperty('--spk-scale', cfg.scale.toFixed(3));
      }
    });

    const finishFloatDrag = () => {
      if (!fDrag) return;
      fDrag.card.classList.remove('is-moving');
      document.body.classList.remove('is-dragging-layout');
      fDrag = null;
      saveSpeakerConfigs();
      renderAllFloatCards(true);
    };
    window.addEventListener('pointerup', finishFloatDrag);
    window.addEventListener('pointercancel', finishFloatDrag);
  }

  function wireStageSplitters() {
    // 1) Fullscreen dock <-> video splitter
    const splitter = document.getElementById('stage-splitter');
    const stageDock = document.getElementById('stage-dock');
    if (splitter && stageDock) {
      let spDrag = null;
      splitter.addEventListener('pointerdown', ev => {
        if (ev.button !== 0) return;
        ev.preventDefault();
        spDrag = {
          startY: ev.clientY,
          startH: stageDock.getBoundingClientRect().height || 200,
          isTop: stageCfg.fsDock === 'top',
        };
        splitter.classList.add('is-active');
        document.body.classList.add('is-dragging-layout');
        try { splitter.setPointerCapture(ev.pointerId); } catch {}
      });
      splitter.addEventListener('pointermove', ev => {
        if (!spDrag) return;
        const dy = spDrag.isTop ? (ev.clientY - spDrag.startY) : (spDrag.startY - ev.clientY);
        const nextH = clampNum(spDrag.startH + dy, 84, Math.round(window.innerHeight * 0.65), 220);
        stageCfg.fsDockH = Math.round(nextH);
        stageDock.style.height = stageCfg.fsDockH + 'px';
        fitVideoBox();
      });
      const done = () => {
        if (!spDrag) return;
        spDrag = null;
        splitter.classList.remove('is-active');
        document.body.classList.remove('is-dragging-layout');
        saveStageCfg();
        fitVideoBox();
        renderLanesWindow(true);
      };
      splitter.addEventListener('pointerup', done);
      splitter.addEventListener('pointercancel', done);
    }

    // 2) Windowed theater stage bottom edge resizer
    const winRz = document.getElementById('stage-win-resize');
    const pw = document.getElementById('player-wrap');
    if (winRz && pw) {
      let wDrag = null;
      winRz.addEventListener('pointerdown', ev => {
        if (ev.button !== 0 || isPlayerFullscreen() || stageCfg.handlesMode === 'hidden') return;
        ev.preventDefault();
        wDrag = {startY: ev.clientY, startH: pw.getBoundingClientRect().height || 400};
        document.body.classList.add('is-dragging-layout');
        try { winRz.setPointerCapture(ev.pointerId); } catch {}
      });
      winRz.addEventListener('pointermove', ev => {
        if (!wDrag) return;
        const nextH = clampNum(wDrag.startH + (ev.clientY - wDrag.startY), 180, Math.round(window.innerHeight * 0.88), 420);
        stageCfg.winStageH = Math.round(nextH);
        pw.style.height = stageCfg.winStageH + 'px';
        if (splitView && window.innerWidth >= 1040) {
          const panel = document.getElementById('transcript-panel');
          if (panel) panel.style.height = stageCfg.winStageH + 'px';
        }
        fitVideoBox();
      });
      const done = () => {
        if (!wDrag) return;
        wDrag = null;
        document.body.classList.remove('is-dragging-layout');
        saveStageCfg();
        fitVideoBox();
        renderAllFloatCards(true);
      };
      winRz.addEventListener('pointerup', done);
      winRz.addEventListener('pointercancel', done);
    }
  }

  async function init() {
    const res = await fetch('transcript.json');
    if (!res.ok) throw new Error('Could not load transcript.json');
    doc = expandCompact(await res.json());
    isCjk = doc.language === 'ja' || doc.language === 'zh';
    hasStudy = Boolean(isCjk || (doc.vocab && doc.vocab.length) || doc.segments.some(s => s.en || s.std || s.tokens.length));
    vocabById = new Map((doc.vocab || []).map(v => [v.id, v]));
    loadKnownVocab();
    model = buildOverlapModel(doc);
    lanesWinStart = model.clipStart || 0;

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

    const prosodyUrl = document.querySelector('main')?.dataset.prosody || '';
    if (prosodyUrl) {
      try {
        const prRes = await fetch(prosodyUrl);
        if (prRes.ok) {
          const prDoc = await prRes.json();
          if (prDoc && Array.isArray(prDoc.segments) && prDoc.segments.length) {
            hasProsody = true;
            prosodyDt = Number(prDoc.dt) || 0.06;
            prosodySpeakers = prDoc.speakers && typeof prDoc.speakers === 'object' ? prDoc.speakers : {};
            prosodyByIndex = new Map(prDoc.segments.map(s => [Number(s.i), s]));
          }
        }
      } catch {}
    }

    loadAnnState();
    buildStreamRegistry();
    loadSpeakerConfigs();
    applyHandlesMode();
    applyTimerPosClasses();

    document.title = doc.title;
    document.getElementById('vid-title').textContent = doc.title;
    const ytLink = document.getElementById('yt-orig-link');
    ytLink.href = 'https://www.youtube.com/watch?v=' + encodeURIComponent(doc.video_id);

    if (doc.chapters.length) {
      const c0 = model.clipStart || 0;
      const c1 = model.clipEnd || model.duration;
      const chs = c0 > 0 || (doc.clip_end != null)
        ? doc.chapters.filter(c => c.e > c0 && c.s < c1)
        : doc.chapters;
      if (chs.length) {
        const chEl = document.getElementById('chapters');
        chEl.hidden = false;
        chEl.innerHTML = chs.map(c =>
          '<button type="button" data-t="' + c.s + '">' + clock(c.s) + ' ' + html(c.t) + '</button>'
        ).join('');
        chEl.addEventListener('click', ev => {
          const b = ev.target.closest('button[data-t]');
          if (b) seekAndPlay(Number(b.dataset.t));
        });
      }
    }

    const filterSel = document.getElementById('transcript-filter');
    filterSel.innerHTML = '<option value="all">all</option>'
      + '<option value="overlaps">overlaps (' + model.overlapSpans.length + ')</option>'
      + model.speakers.map(s => '<option value="spk:' + html(s) + '">' + html(s) + ' (' + model.speakerCounts.get(s) + ')</option>').join('');

    const winSel = document.getElementById('lanes-window');
    if (hasStudy && !localStorage.getItem('ovpub.window')) lanesWindow = isCjk ? 10 : 12;
    if (![...winSel.options].some(o => Number(o.value) === lanesWindow)) lanesWindow = isCjk ? 10 : 20;
    winSel.value = String(lanesWindow);
    document.getElementById('toggle-autoscroll').setAttribute('aria-pressed', String(autoScroll));

    if (hasStudy) {
      const fBtn = document.getElementById('toggle-furigana');
      if (fBtn) fBtn.hidden = !isCjk;
      const stdBtn = document.getElementById('toggle-std');
      const gBtn = document.getElementById('toggle-gloss');
      const eBtn = document.getElementById('toggle-english');
      if (stdBtn) stdBtn.hidden = !STREAM_MAP.has('es-std');
      if (gBtn) gBtn.hidden = !STREAM_MAP.has('literal');
      if (eBtn) eBtn.hidden = !STREAM_MAP.has('en');
      syncStudyToolbarButtons();
      const vDiffSel = document.getElementById('vocab-diff');
      if (vDiffSel) {
        if (doc.language === 'es') {
          vDiffSel.innerHTML =
            '<option value="b2+">AR slang · Econ · C1–B2 (hide basic)</option>'
            + '<option value="c1+">AR slang · Econ · C1 only</option>'
            + '<option value="ar">Rioplatense / Argentinian idioms</option>'
            + '<option value="econ">Politics &amp; economics jargon</option>'
            + '<option value="all">All words (incl. basic)</option>';
          const saved = localStorage.getItem('ovpub.vocabDiff.' + doc.language);
          vocabDiff = saved && [...vDiffSel.options].some(o => o.value === saved) ? saved : 'b2+';
        }
        vDiffSel.value = vocabDiff;
      }
      if (isCjk) applyFuriganaState();
    }

    showAnnotations = seedAnnotations.length > 0
      && localStorage.getItem('ovpub.annotations.' + doc.video_id) === 'true';

    setupYouTube(doc.video_id);
    paintTranscript();
    applyToneState();
    syncFloatButton();
    applyMinimapState();
    applyBadgesState();
    applyAnnotationsState();
    applySplitLayout();
    if (hasStudy) paintVocab();

    wirePopoverInteractions();
    wireFloatingCardInteractions();
    wireStageSplitters();

    // Fullscreen corner HUD buttons (when dock is hidden)
    const hudLayout = document.getElementById('hud-layout-btn');
    if (hudLayout) {
      hudLayout.onclick = ev => {
        ev.stopPropagation();
        const r = hudLayout.getBoundingClientRect();
        renderLayoutPopover(r.left - 220, r.bottom + 6);
      };
    }
    const hudExit = document.getElementById('hud-exit-fs');
    if (hudExit) hudExit.onclick = togglePlayerFullscreen;

    const fsBtn = document.getElementById('toggle-fullscreen');
    if (fsBtn) fsBtn.onclick = togglePlayerFullscreen;
    document.addEventListener('fullscreenchange', onFullscreenChange);
    document.addEventListener('webkitfullscreenchange', onFullscreenChange);
    syncFullscreenButtons();

    // Docked per-speaker deck reordering + independent height resizing + ⚙ button
    const lanesStage = document.getElementById('lanes-stage');
    if (lanesStage) {
      lanesStage.addEventListener('contextmenu', ev => showStreamMenu(ev));

      // Per-speaker height resize handle in dock
      let deckRz = null;
      lanesStage.addEventListener('pointerdown', ev => {
        if (ev.button !== 0 || stageCfg.handlesMode === 'hidden') return;
        const rz = ev.target.closest('.spk-deck-resizer[data-resize-spk]');
        if (!rz) return;
        const spk = rz.dataset.resizeSpk;
        const sc = speakerConfigs[spk];
        if (!sc) return;
        ev.preventDefault();
        ev.stopPropagation();
        deckRz = {spk, startY: ev.clientY, origScale: sc.dockScale, deckEl: rz.closest('.spk-deck')};
        document.body.classList.add('is-dragging-layout');
        try { rz.setPointerCapture(ev.pointerId); } catch {}
      });
      window.addEventListener('pointermove', ev => {
        if (!deckRz) return;
        const sc = speakerConfigs[deckRz.spk];
        if (!sc) return;
        sc.dockScale = clampNum(deckRz.origScale + (ev.clientY - deckRz.startY) / 110, 0.65, 2.0, sc.dockScale);
        if (deckRz.deckEl) deckRz.deckEl.style.setProperty('--spk-scale', sc.dockScale.toFixed(2));
      });
      const finishDeckRz = () => {
        if (!deckRz) return;
        deckRz = null;
        document.body.classList.remove('is-dragging-layout');
        saveSpeakerConfigs();
        renderLanesWindow(true);
        applySplitLayout();
      };
      window.addEventListener('pointerup', finishDeckRz);
      window.addEventListener('pointercancel', finishDeckRz);

      // Drag speaker label to reorder decks, or click to filter speaker
      let rd = null;
      lanesStage.addEventListener('pointerdown', ev => {
        if (ev.button !== 0 || ev.target.closest('button')) return;
        const lbl = ev.target.closest('.lane-label');
        if (!lbl) return;
        const deckEl = lbl.closest('.spk-deck');
        if (!deckEl) return;
        const spk = deckEl.dataset.spk;
        const fromIdx = speakerOrder.indexOf(spk);
        if (fromIdx < 0) return;
        ev.stopPropagation();
        rd = {
          spk, fromIdx, targetSpk: spk, placeAfter: false, startY: ev.clientY, moved: false,
          deckEl, canDrag: stageCfg.handlesMode !== 'hidden',
        };
        try { lbl.setPointerCapture(ev.pointerId); } catch {}
      });
      lanesStage.addEventListener('pointermove', ev => {
        if (!rd || !rd.canDrag) return;
        if (!rd.moved && Math.abs(ev.clientY - rd.startY) >= 6) {
          rd.moved = true;
          rd.deckEl.classList.add('is-dragging');
          closeAllPopovers();
        }
        if (!rd.moved) return;
        const decks = [...lanesStage.querySelectorAll('.spk-deck')];
        for (const el of decks) el.classList.remove('drag-over-top', 'drag-over-bot');
        let overEl = decks[0];
        let placeAfter = false;
        for (const el of decks) {
          const r = el.getBoundingClientRect();
          if (ev.clientY < r.top + r.height / 2) { overEl = el; placeAfter = false; break; }
          overEl = el;
          placeAfter = true;
        }
        if (overEl) {
          rd.targetSpk = overEl.dataset.spk;
          rd.placeAfter = placeAfter;
          if (rd.targetSpk !== rd.spk) {
            overEl.classList.add(placeAfter ? 'drag-over-bot' : 'drag-over-top');
          }
        }
      });
      const finishDeckReorder = ev => {
        if (!rd) return;
        const d = rd;
        rd = null;
        lanesStage.querySelectorAll('.is-dragging, .drag-over-top, .drag-over-bot')
          .forEach(el => el.classList.remove('is-dragging', 'drag-over-top', 'drag-over-bot'));
        if (d.moved && d.targetSpk && d.targetSpk !== d.spk) {
          const targetIdx = speakerOrder.indexOf(d.targetSpk);
          const ins = d.placeAfter ? targetIdx + 1 : targetIdx;
          const toIdx = ins > d.fromIdx ? ins - 1 : ins;
          moveSpeakerOrder(d.fromIdx, toIdx);
        } else if (ev && ev.type === 'pointerup') {
          const val = 'spk:' + d.spk;
          filterSel.value = filterSel.value === val ? 'all' : val;
          paintTranscript();
        }
      };
      lanesStage.addEventListener('pointerup', finishDeckReorder);
      lanesStage.addEventListener('pointercancel', () => finishDeckReorder(null));

      // Per-speaker ⚙ gear click inside docked deck + gutter clock click
      lanesStage.addEventListener('click', ev => {
        const cfgBtn = ev.target.closest('button[data-cfg-spk]');
        if (cfgBtn) {
          ev.stopPropagation();
          const r = cfgBtn.getBoundingClientRect();
          renderSpeakerConfigPopover(cfgBtn.dataset.cfgSpk, r.right + 6, Math.max(12, r.top - 220));
          return;
        }
        if (ev.target.closest('#lanes-gutter')) {
          ev.stopPropagation();
          document.getElementById('copy-link-btn')?.click();
        }
      });

      // Timeline seek, pan, and hover
      lanesStage.addEventListener('mouseover', ev => {
        const blk = ev.target.closest('.lane-block[data-index]');
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
        const pin = ev.target.closest('.ruler-note-pin, .lb-analysis, .lane-pulse');
        if (pin && pin.dataset.t != null) { seekAndPlay(Number(pin.dataset.t)); return; }
        const trk = ev.target.closest('.lane-track, .lanes-ruler');
        if (!trk) return;
        const lw = ev.target.closest('.lane-word[data-v]');
        const vid = lw ? Number(lw.dataset.v) : null;
        drag = {startX: ev.clientX, startWin: lanesWinStart, rect: trk.getBoundingClientRect(), moved: false, vid};
        try { lanesStage.setPointerCapture(ev.pointerId); } catch {}
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
          lanesWinStart = clampWinStart(drag.startWin - (dx / drag.rect.width) * lanesWindow, lanesWindow);
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
          lanesWinStart = clampWinStart(lanesWinStart + (delta / 300) * lanesWindow * 0.4, lanesWindow);
          renderLanesWindow(true);
        }
      }, {passive: false});
    }

    // Stream / Layers context menu actions
    const ctxMenu = document.getElementById('stream-ctx-menu');
    if (ctxMenu) {
      ctxMenu.addEventListener('click', ev => {
        ev.stopPropagation();
        const btn = ev.target.closest('button[data-act]');
        if (!btn || !ctxMenuState) return;
        const act = btn.dataset.act;
        const spk = btn.dataset.spk;
        const x = ctxMenuState.x;
        const y = ctxMenuState.y;
        if (act === 'open-spk-cfg' && spk) {
          renderSpeakerConfigPopover(spk, x, y);
          return;
        }
        if (act === 'open-layout') { renderLayoutPopover(x, y); return; }
        if (act === 'open-labels') { renderLabelsPopover(x, y); return; }
        if (act === 'toggle-kind') {
          toggleRowForAllSpeakers(btn.dataset.kind);
          renderStreamMenu(x, y);
          return;
        }
        if (act === 'toggle-spk' && spk && speakerConfigs[spk]) {
          speakerConfigs[spk].visible = !speakerConfigs[spk].visible;
          refreshAllTimelines(true);
          renderStreamMenu(x, y);
          return;
        }
        if (act === 'toggle-spk-float' && spk && speakerConfigs[spk]) {
          speakerConfigs[spk].placement = speakerConfigs[spk].placement === 'float' ? 'dock' : 'float';
          speakerConfigs[spk].visible = true;
          refreshAllTimelines(true);
          renderStreamMenu(x, y);
          return;
        }
        if (act === 'rm-one' && spk) {
          removeRowFromSpeaker(spk, btn.dataset.kind);
          renderStreamMenu(x, y);
          return;
        }
        if (act === 'reset-all') {
          localStorage.removeItem(spkStorageKey());
          loadSpeakerConfigs();
          refreshAllTimelines(true);
          closeStreamMenu();
        }
      });
      ctxMenu.addEventListener('contextmenu', ev => ev.preventDefault());
    }

    document.addEventListener('click', closeAllPopovers);
    document.addEventListener('keydown', ev => {
      if (ev.key === 'Escape') { closeAllPopovers(); return; }
      if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
      const tag = ev.target && ev.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (ev.target && ev.target.isContentEditable)) return;
      if (ev.key === 'f' || ev.key === 'F') {
        ev.preventDefault();
        togglePlayerFullscreen();
      } else if (ev.key === 'o' || ev.key === 'O') {
        ev.preventDefault();
        setAllSpeakersPlacement(anyFloatingSpeakers() ? 'dock' : 'float');
      } else if ((ev.key === 't' || ev.key === 'T') && hasProsody) {
        ev.preventDefault();
        showTone = !showTone;
        localStorage.setItem('ovpub.tone', String(showTone));
        applyToneState();
      } else if (ev.key === 'l' || ev.key === 'L') {
        ev.preventDefault();
        const order = ['hover', 'show', 'hidden'];
        stageCfg.handlesMode = order[(order.indexOf(stageCfg.handlesMode) + 1) % order.length];
        saveStageCfg();
        applyHandlesMode();
      }
    });

    // Toolbar controls
    document.getElementById('transcript-search').addEventListener('input', paintTranscript);
    filterSel.addEventListener('change', paintTranscript);
    winSel.addEventListener('change', () => {
      lanesWindow = Number(winSel.value) || 20;
      localStorage.setItem('ovpub.window', String(lanesWindow));
      lanesWinStart = clampWinStart(currentTime() - lanesWindow * 0.25, lanesWindow);
      renderLanesWindow(true);
      renderAllFloatCards(true);
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
          for (const s of speakerOrder) {
            const cfg = speakerConfigs[s];
            if (!cfg) continue;
            cfg.rows = [...new Set(cfg.rows.map(r => (r === from ? to : r)))];
          }
          saveSpeakerConfigs();
        }
        applyFuriganaState();
      };
    }
    const stdBtn = document.getElementById('toggle-std');
    if (stdBtn) stdBtn.onclick = () => { if (hasStudy && model) toggleRowForAllSpeakers('es-std'); };
    const glossBtn = document.getElementById('toggle-gloss');
    if (glossBtn) glossBtn.onclick = () => { if (hasStudy && model) toggleRowForAllSpeakers('literal'); };
    const engBtn = document.getElementById('toggle-english');
    if (engBtn) engBtn.onclick = () => { if (hasStudy && model) toggleRowForAllSpeakers('en'); };

    const rowsMenuBtn = document.getElementById('open-rows-menu');
    if (rowsMenuBtn) {
      rowsMenuBtn.onclick = ev => {
        ev.stopPropagation();
        const r = rowsMenuBtn.getBoundingClientRect();
        ctxMenuState = {hitSpk: '', hitKind: '', x: r.left, y: Math.max(12, r.top - 340)};
        renderStreamMenu(r.left, Math.max(12, r.top - 340));
      };
    }
    const layoutBtn = document.getElementById('open-layout-menu');
    if (layoutBtn) {
      layoutBtn.onclick = ev => {
        ev.stopPropagation();
        if (openPopoverKind === 'layout') { closeCfgPopover(); return; }
        const r = layoutBtn.getBoundingClientRect();
        renderLayoutPopover(r.left, Math.max(12, r.top - 390));
      };
    }
    const labelsBtn = document.getElementById('open-labels-menu');
    if (labelsBtn) {
      labelsBtn.onclick = ev => {
        ev.stopPropagation();
        if (openPopoverKind === 'labels') { closeCfgPopover(); return; }
        const r = labelsBtn.getBoundingClientRect();
        renderLabelsPopover(r.left, Math.max(12, r.top - 380));
      };
    }
    const toneBtn = document.getElementById('toggle-tone');
    if (toneBtn) {
      toneBtn.onclick = () => {
        showTone = !showTone;
        localStorage.setItem('ovpub.tone', String(showTone));
        applyToneState();
      };
    }
    document.getElementById('toggle-overlay').onclick = () => {
      setAllSpeakersPlacement(anyFloatingSpeakers() ? 'dock' : 'float');
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
        vocabDiff = vDiffEl.value || (doc && doc.language === 'es' ? 'b2+' : 'n3+');
        localStorage.setItem(doc && doc.language === 'es' ? 'ovpub.vocabDiff.es' : 'ovpub.vocabDiff', vocabDiff);
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
      renderAllFloatCards(true);
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
      if (del) deleteAnnotation(del.dataset.del);
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

    const minimapWrap = document.getElementById('lanes-minimap-wrap');
    let scrubbing = false;
    const jumpMini = ev => {
      const rect = minimapWrap.getBoundingClientRect();
      if (rect.width <= 0) return;
      const c0 = model.clipStart || 0;
      const cSpan = Math.max(1, (model.clipEnd || model.duration) - c0);
      const t = c0 + Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width)) * cSpan;
      lanesFollow = true;
      document.getElementById('lanes-follow').setAttribute('aria-pressed', 'true');
      lanesWinStart = clampWinStart(t - lanesWindow * 0.25, lanesWindow);
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
      const lr = document.getElementById('lanes-ruler');
      if (lr) new ResizeObserver(() => renderLanesWindow(false)).observe(lr);
      const sva = document.getElementById('stage-video-area');
      if (sva) {
        new ResizeObserver(() => {
          fitVideoBox();
          renderAllFloatCards(false);
        }).observe(sva);
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
    prosody_url: str = "",
) -> str:
    back = (
        f'<a href="{html.escape(back_url, quote=True)}">← {html.escape(back_label)}</a>'
        '<span class="sep">·</span>'
        if back_url
        else ""
    )
    ann_attr = f' data-annotations="{html.escape(ann_url, quote=True)}"' if ann_url else ""
    prosody_attr = f' data-prosody="{html.escape(prosody_url, quote=True)}"' if prosody_url else ""
    ann_btn_hidden = "" if ann_url else " hidden"
    tone_btn_hidden = "" if prosody_url else " hidden"
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
<main class="split-view"{ann_attr}{prosody_attr}>
  <div class="player-wrap" id="player-wrap">
    <div class="stage-video-area" id="stage-video-area">
      <div class="video-box" id="video-box">
        <iframe id="yt-frame" title="YouTube video player"
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
          allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>
      </div>
    </div>
    <div class="stage-splitter" id="stage-splitter" hidden title="Drag up or down to resize video vs scrollers below"></div>
    <div class="stage-dock" id="stage-dock" hidden></div>
    <div class="stage-float-layer" id="stage-float-layer"></div>
    <div class="stage-win-resize" id="stage-win-resize" title="Drag to adjust video stage height"></div>
    <div class="fs-corner-hud" id="fs-corner-hud" hidden>
      <button type="button" id="hud-layout-btn">Layout</button>
      <button type="button" id="hud-exit-fs">Exit (F)</button>
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
      <div class="lanes-stage timer-bottom" id="lanes-stage">
        <div class="lanes-body" id="lanes-body"></div>
        <div class="lanes-ruler-row" id="lanes-ruler-row">
          <div class="lanes-gutter" id="lanes-gutter" title="Current time — click to copy timestamp link">
            <span class="live-clock" id="lanes-live-clock">0:00</span>
          </div>
          <div class="lanes-ruler" id="lanes-ruler"></div>
        </div>
      </div>
      <div class="lanes-minimap-wrap" id="lanes-minimap-wrap" hidden title="Click or drag to jump anywhere">
        <canvas class="lanes-minimap" id="lanes-minimap" height="22"></canvas>
        <div class="minimap-viewport" id="minimap-viewport"></div>
        <div class="minimap-playhead" id="minimap-playhead"></div>
      </div>
      <div class="word-inspector" id="word-inspector" hidden></div>
      <div class="ann-live" id="ann-live" hidden></div>
      <div class="tone-legend" id="tone-legend" hidden></div>
      <div class="lanes-bar">
        <span class="lanes-status" id="lanes-status"></span>
        <div class="lanes-tools">
          <button type="button" id="prev-overlap" title="Jump to previous overlap">◀ Prev</button>
          <button type="button" id="next-overlap" title="Jump to next overlap">Next ▶</button>
          <label>Window
            <select id="lanes-window">
              <option value="6">6s</option>
              <option value="10"{sel10}>10s</option>
              <option value="12">12s</option>
              <option value="20"{sel20}>20s</option>
              <option value="30">30s</option>
              <option value="60">60s</option>
            </select>
          </label>
          <button type="button" id="lanes-follow" aria-pressed="true" title="Keep timeline synced to playback">Follow</button>
          <button type="button" id="open-rows-menu" title="Speakers, subcomponents, translations &amp; analysis tracks">Layers</button>
          <button type="button" id="open-layout-menu" title="Video aspect fit, placement below/over, separate speakers, timer &amp; handles">Layout</button>
          <button type="button" id="toggle-tone" aria-pressed="true"{tone_btn_hidden} title="Show or hide live pitch &amp; loudness tone curve (T)">Tone</button>
          <button type="button" id="open-labels-menu" aria-pressed="true"{tone_btn_hidden} title="Configure or hide meta-labels (loud, falsetto, pitch, pace)">Labels</button>
          <button type="button" id="toggle-furigana" aria-pressed="true"{study_hidden} title="Show or hide furigana readings over kanji">Furigana</button>
          <button type="button" id="toggle-std" aria-pressed="false" hidden title="Show or hide neutral Spanish rewrite layer">Neutral ES</button>
          <button type="button" id="toggle-gloss" aria-pressed="true"{study_hidden} title="Show or hide word-by-word meaning stream">Word-by-word</button>
          <button type="button" id="toggle-english" aria-pressed="true"{study_hidden} title="Show or hide per-speaker English stream">English</button>
          <button type="button" id="toggle-overlay" aria-pressed="false" title="Float speakers as independent movable &amp; resizable scrollers (O)">Float</button>
          <button type="button" id="toggle-fullscreen" aria-pressed="false" title="Fullscreen monitor stage with video + scrollers below or floating (F)">Fullscreen</button>
          <button type="button" id="toggle-minimap" aria-pressed="false" title="Show or hide full-video overview bar">Minimap</button>
          <button type="button" id="toggle-badges" aria-pressed="false" title="Show or hide overlap duration badges and counters">Badges</button>
          <button type="button" id="toggle-annotations" aria-pressed="false"{ann_btn_hidden} title="Show or hide editable behavior annotations on timeline and page">Annotations</button>
          <button type="button" id="toggle-split-view" aria-pressed="true" title="Toggle side-by-side Split or Below-video Theater layout">Split</button>
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
<div class="cfg-popover" id="cfg-popover" hidden></div>
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


def _load_prosody(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
        raise ValueError(f"Prosody file must be a JSON object with a 'segments' list: {path}")
    if not isinstance(raw.get("dt"), (int, float)) or float(raw["dt"]) <= 0:
        raise ValueError(f"Prosody file must define positive numeric 'dt': {path}")
    return raw


def _load_tracks(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("tracks"), list):
        raise ValueError(f"Tracks file must be a JSON object with a 'tracks' list: {path}")
    for i, trk in enumerate(raw["tracks"]):
        if not isinstance(trk, dict) or not str(trk.get("id") or "").strip():
            raise ValueError(f"Track {i} in {path} must be an object with non-empty 'id'")
        ttype = str(trk.get("type") or "text")
        if ttype not in ("text", "tag", "meter"):
            raise ValueError(f"Track {trk['id']!r} has invalid type {ttype!r} (expected text, tag, or meter)")
    segs = raw.get("segments")
    if segs is not None and not isinstance(segs, (dict, list)):
        raise ValueError(f"Tracks file 'segments' must be a dict or list: {path}")
    return raw


def _merge_tracks_into_compact(compact: dict, tracks_doc: dict) -> int:
    """Attach extensible per-segment data/analysis tracks (translations, emotion, sarcasm, mood, status, ...)."""
    existing = list(compact.get("tracks") or [])
    seen_ids = {t["id"] for t in existing}
    track_ids: list[str] = []
    for trk in tracks_doc["tracks"]:
        tid = str(trk["id"]).strip()
        track_ids.append(tid)
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        entry = {
            "id": tid,
            "label": str(trk.get("label") or tid).strip(),
            "short": str(trk.get("short") or trk.get("label") or tid).strip(),
            "type": str(trk.get("type") or "text"),
            "color": str(trk.get("color") or "#a9d6ff"),
            "defaultOn": bool(trk.get("defaultOn", False)),
        }
        if trk.get("unsigned"):
            entry["unsigned"] = True
        existing.append(entry)
    compact["tracks"] = existing

    seg_data = tracks_doc.get("segments")
    by_i: dict[int, dict] = {}
    if isinstance(seg_data, dict):
        for k, v in seg_data.items():
            if isinstance(v, dict):
                by_i[int(k)] = v
    elif isinstance(seg_data, list):
        for item in seg_data:
            if isinstance(item, dict) and "i" in item:
                by_i[int(item["i"])] = {k: v for k, v in item.items() if k != "i"}

    valid_ids = set(track_ids)
    for seg in compact["segments"]:
        fields = by_i.get(int(seg["i"]))
        if not fields:
            continue
        merged = dict(seg.get("f") or {})
        for k, v in fields.items():
            if k in valid_ids and v is not None and v != "":
                merged[k] = v
        if merged:
            seg["f"] = merged
    return len(existing)


def _merge_study_into_compact(compact: dict, study: dict) -> None:
    """Attach language-study streams (tokens, word glosses, neutral rewrite, English, vocab, overlaps)."""
    compact["language"] = str(study.get("language") or "ja")
    if study.get("dialect"):
        compact["dialect"] = str(study["dialect"])
    spk_idx = {s: i for i, s in enumerate(compact["speakers"])}
    by_i = {int(s["i"]): s for s in study["segments"]}
    for seg in compact["segments"]:
        st = by_i.get(int(seg["i"]))
        if not st:
            continue
        orig_txt = st.get("ja") or st.get("es") or st.get("text")
        if orig_txt:
            seg["t"] = str(orig_txt).strip()
        if st.get("std"):
            seg["std"] = str(st["std"]).strip()
        if st.get("en"):
            seg["en"] = str(st["en"]).strip()
        toks = st.get("tokens") or []
        if toks:
            seg["tk"] = [
                [
                    round(float(tk["s"]), 2),
                    round(float(tk["e"]), 2),
                    str(tk.get("ja") or tk.get("w") or tk.get("es") or ""),
                    tk.get("ruby") or "",
                    str(tk.get("g") or ""),
                    int(tk["v"]) if tk.get("v") is not None else -1,
                ]
                for tk in toks
            ]
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


def _merge_overlaps_into_compact(compact: dict, ov_doc: dict) -> None:
    spk_idx = {s: i for i, s in enumerate(compact["speakers"])}
    if ov_doc.get("acoustic_overlaps"):
        compact["overlaps"] = [
            [
                round(float(ov["start"]), 2),
                round(float(ov["end"]), 2),
                [spk_idx[s] for s in (ov.get("speakers") or []) if s in spk_idx],
            ]
            for ov in ov_doc["acoustic_overlaps"]
        ]
    if ov_doc.get("vocal_pulses"):
        compact["pulses"] = [
            [round(float(p["start"]), 2), round(float(p["end"]), 2), spk_idx[p["speaker"]]]
            for p in ov_doc["vocal_pulses"]
            if p.get("speaker") in spk_idx
        ]


def export_page(
    data_dir: Path,
    video_id: str,
    out_dir: Path,
    *,
    title: str = "",
    annotations: Path | None = None,
    study: Path | None = None,
    overlaps: Path | None = None,
    prosody: Path | None = None,
    tracks: Path | None = None,
    avatars: Path | None = None,
    clip_start: float | None = None,
    clip_end: float | None = None,
    back_url: str = "",
    back_label: str = "home",
) -> dict:
    video_id = parse_video_id(video_id)
    compact = _compact_transcript(data_dir, video_id)
    if title.strip():
        compact["video_title"] = compact["title"]
        compact["title"] = title.strip()
    if clip_start is not None and clip_start > 0:
        compact["clip_start"] = round(float(clip_start), 2)
    if clip_end is not None and clip_end > 0:
        compact["clip_end"] = round(float(clip_end), 2)
    vocab_count = 0
    if study is not None:
        study_doc = _load_study(study)
        _merge_study_into_compact(compact, study_doc)
        vocab_count = len(compact.get("vocab") or [])
    if overlaps is not None:
        ov_doc = json.loads(overlaps.read_text(encoding="utf-8"))
        _merge_overlaps_into_compact(compact, ov_doc)
    tracks_count = 0
    if tracks is not None:
        tracks_doc = _load_tracks(tracks)
        tracks_count = _merge_tracks_into_compact(compact, tracks_doc)
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

    prosody_url = ""
    prosody_count = 0
    if prosody is not None:
        pr_doc = _load_prosody(prosody)
        pr_bytes = json.dumps(pr_doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        pr_hash = hashlib.sha256(pr_bytes).hexdigest()[:10]
        (out_dir / "prosody.json").write_bytes(pr_bytes)
        prosody_url = f"prosody.json?v={pr_hash}"
        prosody_count = len(pr_doc["segments"])

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
            prosody_url=prosody_url,
        ),
        encoding="utf-8",
    )
    return {
        "video_id": video_id,
        "segments": len(compact["segments"]),
        "speakers": compact["speakers"],
        "annotations": ann_count,
        "prosody": prosody_count,
        "tracks": tracks_count,
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
    ap.add_argument("--overlaps", type=Path, default=None, help="Optional acoustic overlaps & vocal pulses JSON")
    ap.add_argument("--prosody", type=Path, default=None, help="Optional prosody JSON (per-segment pitch & loudness contours)")
    ap.add_argument("--tracks", type=Path, default=None, help="Optional custom per-segment tracks JSON (translations, emotion, sarcasm, mood, status)")
    ap.add_argument("--avatars", type=Path, default=None, help="Optional directory (with avatars.json) or avatars.json mapping speakers to face icons")
    ap.add_argument("--clip-start", type=float, default=None, help="Optional focus start timestamp in seconds")
    ap.add_argument("--clip-end", type=float, default=None, help="Optional focus end timestamp in seconds")
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
        overlaps=args.overlaps.expanduser().resolve() if args.overlaps else None,
        prosody=args.prosody.expanduser().resolve() if args.prosody else None,
        tracks=args.tracks.expanduser().resolve() if args.tracks else None,
        avatars=args.avatars.expanduser().resolve() if args.avatars else None,
        clip_start=args.clip_start,
        clip_end=args.clip_end,
        back_url=args.back_url,
        back_label=args.back_label,
    )
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
