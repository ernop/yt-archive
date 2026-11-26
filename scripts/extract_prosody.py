#!/usr/bin/env python3
"""Extract speaker-normalized prosody (pitch F0, loudness dB, pace, contours) from an archived video.

Uses a vectorized YIN pitch tracker (PyTorch on CPU) over the archived MP3 soundtrack
and aligns acoustic contours to the multi-speaker transcript segments.

Usage:
    .venv/bin/python scripts/extract_prosody.py <video_id> [--out path/to/prosody.json] [--report]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yt_archive.multitalker import decode_audio_16k
from yt_archive.paths import (
    condensed_dir,
    default_data_dir,
    parse_video_id,
    soundtrack_path,
    transcript_json_path,
)

SR = 16000
HOP = 320        # 20 ms
WIN = 640        # 40 ms
FMIN = 65.0      # Hz (captures deep male voices & vocal fry)
FMAX = 650.0     # Hz (captures high emphasis & falsetto)
TAU_MIN = int(SR / FMAX)   # 24
TAU_MAX = int(SR / FMIN)   # 246
INT_LEN = WIN - TAU_MAX    # 394 (~24.6 ms)
N_FFT = 1024
OUT_STRIDE = 3   # 3 * 20 ms = 60 ms trace grid for compact web visualization

_VOWEL_RE = re.compile(r"[aeiouyáéíóúü]+", re.IGNORECASE)


def _est_syllables(word: str) -> int:
    clean = re.sub(r"[^a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]", "", word)
    if not clean:
        return 0
    groups = _VOWEL_RE.findall(clean)
    n = len(groups)
    # Trim silent trailing 'e' for rough English syllable counts.
    if n > 1 and clean.lower().endswith("e") and not clean.lower().endswith("le"):
        n -= 1
    return max(1, n)


def _yin_track_chunk(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute per-frame (f0_hz, rms_db, aperiodicity) for a 16kHz mono float32 chunk."""
    n = len(audio)
    if n < WIN:
        empty = np.empty(0, dtype=np.float32)
        return empty, empty, empty
    n_frames = 1 + (n - WIN) // HOP
    t_audio = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))
    frames = t_audio.unfold(0, WIN, HOP)  # (F, WIN)

    # Frame RMS in dB
    rms = torch.sqrt(torch.mean(frames * frames, dim=1) + 1e-12)
    rms_db = (20.0 * torch.log10(rms + 1e-6)).numpy()

    # Vectorized YIN difference function d(tau) via FFT cross-correlation + prefix sums
    a = frames[:, :INT_LEN]  # (F, M)
    sq = frames * frames
    cs = torch.zeros((n_frames, WIN + 1), dtype=torch.float32)
    cs[:, 1:] = torch.cumsum(sq, dim=1)

    e0 = cs[:, INT_LEN : INT_LEN + 1]  # (F, 1)
    taus = torch.arange(TAU_MAX + 1, dtype=torch.long)
    e_tau = cs[:, taus + INT_LEN] - cs[:, taus]  # (F, TAU_MAX + 1)

    fa = torch.fft.rfft(a, n=N_FFT, dim=1)
    fb = torch.fft.rfft(frames, n=N_FFT, dim=1)
    xcorr = torch.fft.irfft(torch.conj(fa) * fb, n=N_FFT, dim=1)[:, : TAU_MAX + 1]

    diff = torch.clamp(e0 + e_tau - 2.0 * xcorr, min=0.0)
    # Cumulative mean normalized difference function (CMNDF)
    cum = torch.cumsum(diff[:, 1:], dim=1)
    denom = torch.clamp(cum, min=1e-9)
    idx = torch.arange(1, TAU_MAX + 1, dtype=torch.float32).unsqueeze(0)
    cmndf = np.ones((n_frames, TAU_MAX + 1), dtype=np.float32)
    cmndf[:, 1:] = (diff[:, 1:] * idx / denom).numpy()

    sub = cmndf[:, TAU_MIN : TAU_MAX + 1]  # lags TAU_MIN..TAU_MAX
    # Local minimum mask inside sub (avoid picking non-minimum slope edge)
    is_lmin = np.zeros_like(sub, dtype=bool)
    is_lmin[:, 1:-1] = (sub[:, 1:-1] <= sub[:, :-2]) & (sub[:, 1:-1] <= sub[:, 2:])
    below = (sub < 0.15) & is_lmin
    has_below = below.any(axis=1)
    first_below = below.argmax(axis=1)

    global_idx = sub.argmin(axis=1)
    global_val = sub[np.arange(n_frames), global_idx]

    chosen = np.where(has_below, first_below, global_idx)
    aper = sub[np.arange(n_frames), chosen]
    voiced = has_below | (global_val < 0.24)
    # Gate out near-silence frames
    voiced &= rms_db > -48.0

    tau_raw = chosen + TAU_MIN
    f0 = np.full(n_frames, np.nan, dtype=np.float32)
    vi = np.nonzero(voiced)[0]
    for i in vi:
        k = int(tau_raw[i])
        if TAU_MIN < k < TAU_MAX:
            y0, y1, y2 = cmndf[i, k - 1], cmndf[i, k], cmndf[i, k + 1]
            d2 = y0 - 2.0 * y1 + y2
            shift = 0.5 * (y0 - y2) / d2 if abs(d2) > 1e-9 else 0.0
            shift = max(-0.5, min(0.5, float(shift)))
            f0[i] = SR / (k + shift)
        else:
            f0[i] = SR / float(k)

    return f0, rms_db.astype(np.float32), aper.astype(np.float32)


def extract_raw_tracks(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run YIN across the full 16 kHz mono signal in chunks and apply median smoothing."""
    n = len(audio)
    if n < WIN:
        empty = np.empty(0, dtype=np.float32)
        return empty, empty, empty
    total_frames = 1 + (n - WIN) // HOP
    f0 = np.full(total_frames, np.nan, dtype=np.float32)
    rms_db = np.full(total_frames, -96.0, dtype=np.float32)
    aper = np.ones(total_frames, dtype=np.float32)

    chunk_frames = 2000  # 40 s chunks -> tiny memory footprint
    for start_f in range(0, total_frames, chunk_frames):
        end_f = min(total_frames, start_f + chunk_frames)
        s0 = start_f * HOP
        s1 = (end_f - 1) * HOP + WIN
        cf0, cdb, caper = _yin_track_chunk(audio[s0:s1])
        m = min(len(cf0), end_f - start_f)
        f0[start_f : start_f + m] = cf0[:m]
        rms_db[start_f : start_f + m] = cdb[:m]
        aper[start_f : start_f + m] = caper[:m]

    # 5-frame (100 ms) median filter in log2-F0 space; drop isolated 1-frame glitches
    log_f0 = np.log2(f0)
    smooth = np.full_like(log_f0, np.nan)
    for i in range(total_frames):
        if math.isnan(log_f0[i]):
            continue
        lo = max(0, i - 2)
        hi = min(total_frames, i + 3)
        win = log_f0[lo:hi]
        valid = win[~np.isnan(win)]
        if len(valid) >= 2:
            smooth[i] = float(np.median(valid))
    f0_clean = np.where(np.isnan(smooth), np.nan, np.power(2.0, smooth)).astype(np.float32)
    return f0_clean, rms_db, aper


def _build_overlap_mask(segments: list[dict], n_frames: int) -> tuple[np.ndarray, set[int]]:
    """Mark frames where >=2 distinct speakers are active simultaneously."""
    active_counts = np.zeros(n_frames, dtype=np.int16)
    by_spk: dict[str, list[tuple[int, int]]] = {}
    for seg in segments:
        spk = str(seg.get("speaker") or "").strip()
        if not spk:
            continue
        f0 = max(0, int(round(float(seg["start"]) * SR / HOP)))
        f1 = min(n_frames, int(round(float(seg["end"]) * SR / HOP)))
        if f1 > f0:
            by_spk.setdefault(spk, []).append((f0, f1))
    for intervals in by_spk.values():
        spk_mask = np.zeros(n_frames, dtype=bool)
        for f0, f1 in intervals:
            spk_mask[f0:f1] = True
        active_counts += spk_mask.astype(np.int16)
    ov_frame = active_counts >= 2

    ov_seg_ids: set[int] = set()
    sorted_segs = sorted(segments, key=lambda s: (float(s["start"]), float(s["end"])))
    live: list[dict] = []
    for seg in sorted_segs:
        s0 = float(seg["start"])
        s1 = float(seg["end"])
        spk = str(seg.get("speaker") or "").strip()
        live = [x for x in live if float(x["end"]) > s0 + 0.05]
        for prev in live:
            if str(prev.get("speaker") or "").strip() != spk:
                if min(float(prev["end"]), s1) - max(float(prev["start"]), s0) >= 0.05:
                    ov_seg_ids.add(int(prev.get("index", 0)))
                    ov_seg_ids.add(int(seg.get("index", 0)))
        live.append(seg)
    return ov_frame, ov_seg_ids


def _seg_syllable_rate(seg: dict) -> float | None:
    words = seg.get("words") or []
    dur = float(seg.get("end", 0)) - float(seg.get("start", 0))
    if dur < 0.35:
        return None
    if words:
        sylls = sum(_est_syllables(str(w.get("word") or "")) for w in words)
        if len(words) < 3 or sylls < 3:
            return None
        # Use spanned word duration when available to avoid trailing pauses skewing rate
        w0 = float(words[0].get("start", seg["start"]))
        w1 = float(words[-1].get("end", seg["end"]))
        span = max(0.3, w1 - w0)
        return sylls / span
    toks = str(seg.get("text") or "").split()
    if len(toks) < 3:
        return None
    sylls = sum(_est_syllables(t) for t in toks)
    return sylls / dur


def compute_prosody(data_dir: Path, video_id: str) -> dict:
    video_id = parse_video_id(video_id)
    audio_file = soundtrack_path(data_dir, video_id)
    tpath = transcript_json_path(data_dir, video_id)
    if not audio_file.is_file():
        raise FileNotFoundError(f"missing soundtrack: {audio_file}")
    if not tpath.is_file():
        raise FileNotFoundError(f"missing transcript: {tpath}")

    doc = json.loads(tpath.read_text(encoding="utf-8"))
    segments = doc.get("segments") or []
    audio = decode_audio_16k(audio_file)
    f0_hz, rms_db, aper = extract_raw_tracks(audio)
    n_frames = len(f0_hz)
    ov_frame, ov_seg_ids = _build_overlap_mask(segments, n_frames)

    # 1. Calibrate each speaker strictly on non-overlapping solo speech frames.
    speakers_seen: list[str] = []
    for seg in segments:
        spk = str(seg.get("speaker") or "").strip()
        if spk and spk not in speakers_seen:
            speakers_seen.append(spk)

    spk_stats: dict[str, dict] = {}
    for spk in speakers_seen:
        solo_f0 = []
        solo_db = []
        solo_rates = []
        for seg in segments:
            if str(seg.get("speaker") or "").strip() != spk:
                continue
            i0 = max(0, int(round(float(seg["start"]) * SR / HOP)))
            i1 = min(n_frames, int(round(float(seg["end"]) * SR / HOP)))
            if i1 <= i0:
                continue
            solo_mask = ~ov_frame[i0:i1]
            seg_f0 = f0_hz[i0:i1][solo_mask]
            seg_db = rms_db[i0:i1][solo_mask]
            seg_ap = aper[i0:i1][solo_mask]
            good_f0 = seg_f0[(~np.isnan(seg_f0)) & (seg_f0 >= 70.0) & (seg_f0 <= 450.0) & (seg_ap < 0.2)]
            if len(good_f0):
                solo_f0.append(good_f0)
            loud_db = seg_db[seg_db > -45.0]
            if len(loud_db):
                solo_db.append(loud_db)
            if int(seg.get("index", -1)) not in ov_seg_ids:
                r = _seg_syllable_rate(seg)
                if r is not None and 1.0 <= r <= 12.0:
                    solo_rates.append(r)

        all_f0 = np.concatenate(solo_f0) if solo_f0 else np.array([150.0], dtype=np.float32)
        all_db = np.concatenate(solo_db) if solo_db else np.array([-24.0], dtype=np.float32)
        med_f0 = float(np.median(all_f0))
        all_st = 12.0 * np.log2(all_f0 / med_f0)
        med_db = float(np.median(all_db))
        med_sps = float(np.median(solo_rates)) if solo_rates else 4.2
        spk_stats[spk] = {
            "median_f0_hz": round(med_f0, 1),
            "p10_st": round(float(np.percentile(all_st, 10)), 1),
            "p90_st": round(float(np.percentile(all_st, 90)), 1),
            "median_db": round(med_db, 1),
            "median_sps": round(med_sps, 2),
        }

    # 2. Extract compact per-segment contours (60 ms grid) + acoustic summary & word peaks.
    dt = (HOP * OUT_STRIDE) / SR  # 0.06 s
    seg_out: list[dict] = []
    for pos, seg in enumerate(segments):
        seg_idx = int(seg.get("index", pos))
        spk = str(seg.get("speaker") or "").strip()
        base = spk_stats.get(spk) or {"median_f0_hz": 150.0, "median_db": -24.0, "median_sps": 4.2}
        med_f0 = base["median_f0_hz"]
        med_db = base["median_db"]
        med_sps = base["median_sps"]

        s0 = float(seg["start"])
        s1 = float(seg["end"])
        i0 = max(0, int(round(s0 * SR / HOP)))
        i1 = min(n_frames, int(round(s1 * SR / HOP)))
        if i1 <= i0:
            continue

        raw_f0 = f0_hz[i0:i1]
        raw_db = rms_db[i0:i1]
        raw_ap = aper[i0:i1]
        raw_st = np.where(np.isnan(raw_f0), np.nan, 12.0 * np.log2(raw_f0 / med_f0))
        # Reject extreme isolated single-frame octave glitches (>+16 st or <-10 st unless strongly sustained)
        extreme = (~np.isnan(raw_st)) & ((raw_st > 16.0) | (raw_st < -10.0)) & (raw_ap > 0.12)
        raw_st = np.where(extreme, np.nan, raw_st)
        raw_rel_db = raw_db - med_db

        # Downsample onto 60 ms grid (3-frame bins)
        p_trace: list[int | None] = []
        d_trace: list[int] = []
        for k in range(0, len(raw_st), OUT_STRIDE):
            st_bin = raw_st[k : k + OUT_STRIDE]
            db_bin = raw_rel_db[k : k + OUT_STRIDE]
            v = st_bin[~np.isnan(st_bin)]
            if len(v):
                p_trace.append(int(round(max(-12.0, min(18.0, float(np.median(v)))) * 10)))
            else:
                p_trace.append(None)
            d_val = float(np.max(db_bin)) if len(db_bin) else -20.0
            d_trace.append(int(round(max(-24.0, min(18.0, d_val)) * 2)))

        # Segment summary metrics (prefer solo frames inside segment if enough exist)
        seg_ov = seg_idx in ov_seg_ids
        solo_m = ~ov_frame[i0:i1]
        use_solo = int(solo_m.sum()) >= max(5, int(0.35 * len(solo_m)))
        st_eval = raw_st[solo_m] if use_solo else raw_st
        db_eval = raw_rel_db[solo_m] if use_solo else raw_rel_db
        ap_eval = raw_ap[solo_m] if use_solo else raw_ap
        voiced_st = st_eval[~np.isnan(st_eval)]

        pk_st = 0.0
        mn_st = 0.0
        md_st = 0.0
        end_slope = 0.0
        if len(voiced_st) >= 3:
            # Sustained peak: 3-frame rolling min of voiced pitch or 92nd percentile
            pk_st = float(np.percentile(voiced_st, 93))
            mn_st = float(np.percentile(voiced_st, 10))
            md_st = float(np.median(voiced_st))
            # Terminal contour slope: last 0.45 s (~22 frames) vs preceding 0.45 s
            tail_n = min(22, len(raw_st) // 2)
            if tail_n >= 4:
                tail_v = raw_st[-tail_n:]
                tail_v = tail_v[~np.isnan(tail_v)]
                prev_v = raw_st[max(0, len(raw_st) - 2 * tail_n) : len(raw_st) - tail_n]
                prev_v = prev_v[~np.isnan(prev_v)]
                if len(tail_v) >= 2 and len(prev_v) >= 2:
                    end_slope = float(np.median(tail_v) - np.median(prev_v))

        loud_frames = db_eval[db_eval > -22.0]
        md_db = float(np.median(loud_frames)) if len(loud_frames) else float(np.median(db_eval))
        pk_db = float(np.percentile(loud_frames, 92)) if len(loud_frames) else md_db
        breathy_frac = float(np.mean((db_eval > -12.0) & (ap_eval > 0.35))) if len(db_eval) else 0.0

        rate = _seg_syllable_rate(seg)
        rate_ratio = round(rate / med_sps, 2) if (rate is not None and med_sps > 0) else 1.0

        tags: list[str] = []
        if pk_st >= 8.5 and len(voiced_st) >= 5:
            tags.append("falsetto")
        elif pk_st >= 5.5 and len(voiced_st) >= 5:
            tags.append("high-pitch")
        if mn_st <= -4.5 and md_st <= -2.2 and len(voiced_st) >= 5:
            tags.append("low-drop")
        if pk_db >= 6.5 or md_db >= 4.2:
            tags.append("loud")
        elif md_db <= -5.5:
            tags.append("quiet")
        if rate_ratio >= 1.38:
            tags.append("fast")
        elif rate_ratio <= 0.68:
            tags.append("slow")
        if end_slope >= 3.0:
            tags.append("rising")
        elif end_slope <= -3.2:
            tags.append("falling")
        if breathy_frac >= 0.35 and md_db >= -8.0:
            tags.append("breathy")

        # Identify peak word inside segment for pinpoint inspection
        peak_word = ""
        peak_word_t = s0
        best_w_st = -99.0
        for w in seg.get("words") or []:
            ws = float(w.get("start", s0))
            we = float(w.get("end", ws))
            wi0 = max(i0, int(round(ws * SR / HOP))) - i0
            wi1 = min(i1, max(wi0 + 1, int(round(we * SR / HOP)))) - i0
            wv = raw_st[wi0:wi1]
            wv = wv[~np.isnan(wv)]
            if len(wv) >= 2:
                m_st = float(np.percentile(wv, 85))
                if m_st > best_w_st:
                    best_w_st = m_st
                    peak_word = str(w.get("word") or "").strip()
                    peak_word_t = ws

        entry: dict = {
            "i": seg_idx,
            "p": p_trace,
            "d": d_trace,
            "pk": round(pk_st, 1),
            "mn": round(mn_st, 1),
            "md": round(md_st, 1),
            "sl": round(end_slope, 1),
            "db": round(md_db, 1),
            "pdb": round(pk_db, 1),
            "rt": rate_ratio,
        }
        if seg_ov:
            entry["ov"] = 1
        if tags:
            entry["tg"] = tags
        if peak_word and best_w_st >= 4.5:
            entry["pw"] = peak_word
            entry["pt"] = round(peak_word_t, 2)
        seg_out.append(entry)

    return {
        "version": 1,
        "video_id": video_id,
        "dt": dt,
        "speakers": spk_stats,
        "segments": seg_out,
    }


def _print_report(prosody: dict, transcript: dict) -> None:
    by_i = {int(s.get("index", idx)): s for idx, s in enumerate(transcript.get("segments") or [])}
    segs = prosody["segments"]
    print("=== Speaker Baselines ===")
    for spk, st in prosody["speakers"].items():
        print(
            f"  {spk}: median F0 = {st['median_f0_hz']} Hz "
            f"(typical {st['p10_st']:+.1f}..{st['p90_st']:+.1f} st), "
            f"median dB = {st['median_db']:.1f}, pace = {st['median_sps']:.2f} syl/s"
        )

    def fmt(e: dict) -> str:
        s = by_i.get(e["i"], {})
        t = float(s.get("start", 0))
        mm, ss = divmod(int(t), 60)
        hh, mm = divmod(mm, 60)
        clk = f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}"
        pw = f" [peak word: {e['pw']!r} @ {e['pt']}s]" if e.get("pw") else ""
        ov = " [OV]" if e.get("ov") else ""
        tags = ",".join(e.get("tg") or [])
        txt = str(s.get("text") or "").strip()[:95]
        return (
            f"  {clk} ({t:6.1f}s) {s.get('speaker',''):15s} "
            f"pk={e['pk']:+5.1f}st md={e['md']:+5.1f}st db={e['db']:+5.1f}/{e['pdb']:+5.1f} "
            f"sl={e['sl']:+4.1f} rt={e['rt']:.2f}x [{tags}]{ov}{pw}\n"
            f"      \"{txt}\""
        )

    solo = [e for e in segs if not e.get("ov") and len(e["p"]) >= 8]
    print("\n=== Top High-Pitch / Falsetto Spikes (Solo) ===")
    for e in sorted(solo, key=lambda x: x["pk"], reverse=True)[:12]:
        print(fmt(e))

    print("\n=== Top Loud Surges (Solo) ===")
    for e in sorted(solo, key=lambda x: x["pdb"], reverse=True)[:10]:
        print(fmt(e))

    print("\n=== Top Quiet Backchannels During Overlaps ===")
    ov_quiet = [e for e in segs if e.get("ov") and e["db"] <= -4.5 and len(e["p"]) >= 4]
    for e in sorted(ov_quiet, key=lambda x: x["db"])[:8]:
        print(fmt(e))

    print("\n=== Top Low / Deadpan / Vocal-Fry Drops (Solo) ===")
    for e in sorted(solo, key=lambda x: x["md"])[:8]:
        print(fmt(e))

    print("\n=== Top Rising-Intonation / Checking Contours (Solo) ===")
    for e in sorted(solo, key=lambda x: x["sl"], reverse=True)[:8]:
        print(fmt(e))

    print("\n=== Top Fast Bursts (Solo) ===")
    for e in sorted(solo, key=lambda x: x["rt"], reverse=True)[:8]:
        print(fmt(e))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", help="YouTube URL or archived video id")
    ap.add_argument("--data", type=Path, default=default_data_dir())
    ap.add_argument("--out", type=Path, default=None, help="Output JSON path (default: data/<id>/_condensed/prosody.json)")
    ap.add_argument("--report", action="store_true", help="Print top prosodic highlights")
    args = ap.parse_args(argv)

    data_dir = args.data.expanduser().resolve()
    vid = parse_video_id(args.target)
    result = compute_prosody(data_dir, vid)
    out_path = args.out.expanduser().resolve() if args.out else (condensed_dir(data_dir, vid) / "prosody.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024.0
    print(f"wrote {out_path} ({len(result['segments'])} segments, {size_kb:.1f} KB)")
    if args.report:
        tr = json.loads(transcript_json_path(data_dir, vid).read_text(encoding="utf-8"))
        _print_report(result, tr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
