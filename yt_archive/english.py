"""English translation, captions, multi-speaker Kokoro dubbing, and MKV export."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import db, gpu_gate, llm
from .download import write_archive_json
from .locks import video_lock
from .paths import (
    archive_json_path,
    english_ass_path,
    english_dub_path,
    english_has_dub,
    english_is_complete,
    english_mkv_path,
    english_srt_path,
    english_transcript_json_path,
    english_transcript_vtt_path,
    find_video_file,
    orig_srt_path,
    soundtrack_path,
    transcript_is_complete,
    transcript_json_path,
    video_dir,
)
from .settings import load_settings
from .transcribe import (
    _atomic_text,
    _hold_gpu,
    _speaker_name,
    _webvtt,
    transcribe_soundtrack,
)

DIARIZE_MODEL_ID = "pyannote/speaker-diarization-community-1"
DEFAULT_KOKORO_DIR = Path.home() / ".cache" / "kokoro-onnx"

FEMALE_VOICES = (
    ("af_heart", "en-us", 1.0, "&H80E0FF&"),
    ("af_bella", "en-us", 1.0, "&HB0FFC0&"),
    ("af_sarah", "en-us", 1.0, "&HE0C0FF&"),
    ("bf_emma", "en-gb", 0.98, "&HB8F0FF&"),
    ("af_nova", "en-us", 1.0, "&HFFD0A0&"),
    ("bf_alice", "en-gb", 0.98, "&HD0FFB8&"),
    ("af_jessica", "en-us", 1.0, "&HC8E0FF&"),
)
MALE_VOICES = (
    ("am_michael", "en-us", 1.0, "&HFFE0A0&"),
    ("am_adam", "en-us", 0.98, "&H90B0FF&"),
    ("bm_george", "en-gb", 0.98, "&HC0D8FF&"),
    ("am_eric", "en-us", 0.98, "&HD0E0B0&"),
    ("am_fenrir", "en-us", 0.98, "&HC8D0FF&"),
    ("am_liam", "en-us", 1.0, "&HA0FFD8&"),
    ("bm_daniel", "en-gb", 0.98, "&HFFE0D0&"),
)
_FEMALE_HINT = re.compile(
    r"\b(?:female|woman|girl|mother|mom|mama|sister|wife|daughter|aunt|grandmother|"
    r"hostess|actress|interviewer|reporter|ms\.?|mrs\.?)\b",
    re.IGNORECASE,
)
_MALE_HINT = re.compile(
    r"\b(?:male|man|boy|father|dad|papa|brother|husband|son|uncle|grandfather|"
    r"mr\.?|narrator|announcer|expert|doctor|dr\.?|professor|policeman|officer)\b",
    re.IGNORECASE,
)

TRANSLATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "integer"},
                    "speaker": {"type": "string"},
                    "en_sub": {"type": "string"},
                    "en_dub": {"type": "string"},
                },
                "required": ["id", "speaker", "en_sub", "en_dub"],
            },
        }
    },
    "required": ["items"],
}

CHAPTERS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "titles": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
    "required": ["titles"],
}


def normalize_english_job_config(raw: dict | None = None) -> dict:
    raw = raw or {}
    source_language = str(raw.get("source_language") or "").strip().lower()
    if source_language and not re.fullmatch(r"[a-z]{2,3}", source_language):
        raise ValueError("source_language must be an ISO code such as zh, ja, or de")
    return {
        "include_dub": bool(raw.get("include_dub", True)),
        "source_language": source_language,
    }


def estimate_minutes(duration_seconds: float | None, *, include_dub: bool = True, has_transcript: bool = False) -> int:
    """Rough wall-clock minutes for the video page confirmation dialog."""
    mins = max(1.0, float(duration_seconds or 0) / 60.0)
    whisper = 0.0 if has_transcript else (0.3 + mins / 10.5)
    diarize = 0.2 + mins / 30.0
    translate = 0.4 + mins / 45.0
    dub = (0.4 + mins / 13.0) if include_dub else 0.0
    return max(1, int(round(whisper + diarize + translate + dub)))


# ---------------------------------------------------------------- speaker diarization


def _best_speaker(turns: list[dict], start: float, end: float) -> int:
    overlaps: dict[int, float] = defaultdict(float)
    for t in turns:
        if t["end"] <= start:
            continue
        if t["start"] >= end:
            break
        ov = min(end, t["end"]) - max(start, t["start"])
        if ov > 0:
            overlaps[t["speaker_idx"]] += ov
    if overlaps:
        return max(overlaps.items(), key=lambda kv: kv[1])[0]
    mid = 0.5 * (start + end)
    best_dist = 1.5
    best_spk = -1
    for t in turns:
        dist = 0.0 if t["start"] <= mid <= t["end"] else min(abs(mid - t["start"]), abs(mid - t["end"]))
        if dist < best_dist:
            best_dist = dist
            best_spk = t["speaker_idx"]
    return best_spk


def split_segments_by_speaker(whisper_segments: list[dict], turns: list[dict]) -> list[dict]:
    """Attach speaker_idx to each Whisper segment, splitting at clear word-level speaker changes."""
    items: list[dict] = []
    for seg in whisper_segments:
        if not turns:
            items.append({
                "id": len(items),
                "start": round(float(seg["start"]), 3),
                "end": round(float(seg["end"]), 3),
                "duration": round(max(0.0, float(seg["end"]) - float(seg["start"])), 3),
                "speaker_idx": 0,
                "text_orig": str(seg.get("text") or "").strip(),
            })
            continue
        words = seg.get("words") or []
        if len(words) < 6:
            spk = _best_speaker(turns, float(seg["start"]), float(seg["end"]))
            items.append({
                "id": len(items),
                "start": round(float(seg["start"]), 3),
                "end": round(float(seg["end"]), 3),
                "duration": round(max(0.0, float(seg["end"]) - float(seg["start"])), 3),
                "speaker_idx": max(0, spk),
                "text_orig": str(seg.get("text") or "").strip(),
            })
            continue

        word_spks = [(w, _best_speaker(turns, float(w["start"]), float(w["end"]))) for w in words]
        for i, (w, spk) in enumerate(word_spks):
            if spk == -1:
                if i > 0 and word_spks[i - 1][1] != -1:
                    word_spks[i] = (w, word_spks[i - 1][1])
                else:
                    for j in range(i + 1, len(word_spks)):
                        if word_spks[j][1] != -1:
                            word_spks[i] = (w, word_spks[j][1])
                            break

        runs: list[tuple[int, list[dict]]] = []
        for w, spk in word_spks:
            if runs and runs[-1][0] == spk:
                runs[-1][1].append(w)
            else:
                runs.append((spk, [w]))

        changed = True
        while changed and len(runs) > 1:
            changed = False
            for i, (spk, ws) in enumerate(runs):
                dur = float(ws[-1]["end"]) - float(ws[0]["start"])
                txt = "".join(str(x.get("word") or "") for x in ws).strip()
                if dur < 0.7 or len(txt) < 3:
                    target = i - 1 if i > 0 else i + 1
                    if target < i:
                        runs[target] = (runs[target][0], runs[target][1] + ws)
                    else:
                        runs[target] = (runs[target][0], ws + runs[target][1])
                    runs.pop(i)
                    changed = True
                    break
            merged: list[tuple[int, list[dict]]] = []
            for spk, ws in runs:
                if merged and merged[-1][0] == spk:
                    merged[-1] = (spk, merged[-1][1] + ws)
                else:
                    merged.append((spk, ws))
            runs = merged

        if len(runs) == 1:
            items.append({
                "id": len(items),
                "start": round(float(seg["start"]), 3),
                "end": round(float(seg["end"]), 3),
                "duration": round(max(0.0, float(seg["end"]) - float(seg["start"])), 3),
                "speaker_idx": max(0, runs[0][0]),
                "text_orig": str(seg.get("text") or "").strip(),
            })
        else:
            for spk, ws in runs:
                txt = "".join(str(x.get("word") or "") for x in ws).strip()
                if txt:
                    s0 = round(float(ws[0]["start"]), 3)
                    s1 = round(float(ws[-1]["end"]), 3)
                    items.append({
                        "id": len(items),
                        "start": s0,
                        "end": s1,
                        "duration": round(max(0.0, s1 - s0), 3),
                        "speaker_idx": max(0, spk),
                        "text_orig": txt,
                    })
    return items


def _diarize_in_process(audio_path: Path, out_path: Path, gpu: dict) -> None:
    """Run local cached pyannote diarization (invoked in a subprocess so GPU frees on exit)."""
    os.environ["PYANNOTE_METRICS_ENABLED"] = "false"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import numpy as np
    import torch
    from huggingface_hub import snapshot_download
    from pyannote.audio import Pipeline

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(audio_path), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    )
    audio = np.frombuffer(proc.stdout, dtype=np.float32).copy()
    use_cuda = torch.cuda.is_available()
    gate = gpu_gate.load() if use_cuda else None
    held = "pyannote diarizer" if gate else ""
    if held:
        gate.hold(
            held,
            gpu_gate.DIARIZE_GB,
            lane=gpu["lane"],
            who=gpu["who"],
            minutes=max(5.0, round(len(audio) / 16000 / 60 * 0.25 + 3, 1)),
            wait=None,
        )
    try:
        model_dir = snapshot_download(DIARIZE_MODEL_ID, local_files_only=True)
        pipeline = Pipeline.from_pretrained(model_dir)
        if use_cuda:
            pipeline.to(torch.device("cuda"))
        output = pipeline({"waveform": torch.from_numpy(audio)[None], "sample_rate": 16000})
        talk: dict[str, float] = {}
        tracks: list[tuple[float, float, str]] = []
        for seg, _, label in output.exclusive_speaker_diarization.itertracks(yield_label=True):
            tracks.append((float(seg.start), float(seg.end), str(label)))
            talk[str(label)] = talk.get(str(label), 0.0) + float(seg.duration)
        labels = sorted(talk, key=lambda lbl: (-talk[lbl], lbl))
        index = {lbl: i for i, lbl in enumerate(labels)}
        turns = sorted(
            [
                {"start": round(s, 3), "end": round(e, 3), "speaker_idx": index[lbl]}
                for s, e, lbl in tracks
            ],
            key=lambda x: (x["start"], x["end"]),
        )
        out_path.write_text(
            json.dumps({"speakers": len(labels), "turns": turns}),
            encoding="utf-8",
        )
    finally:
        if held:
            gate.let_go(held)


def run_diarization(audio_path: Path, cfg: dict, gpu: dict, log) -> list[dict]:
    """Run pyannote diarization in a child process when configured or installed."""
    explicit = (cfg.get("diarize_python") or "").strip()
    if explicit:
        py = str(Path(explicit).expanduser())
        if not Path(py).is_file():
            raise RuntimeError(f"settings.json: english.diarize_python not found: {py}")
    else:
        try:
            import pyannote.audio  # noqa: F401
        except ImportError:
            log("speaker diarization skipped (pyannote not installed and english.diarize_python unset)")
            return []
        py = sys.executable

    repo_root = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = repo_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with tempfile.TemporaryDirectory(prefix="ytarchive-diarize-") as tmp:
        out_json = Path(tmp) / "diarization.json"
        spec = json.dumps({"audio": str(audio_path), "out": str(out_json), "gpu": gpu})
        proc = subprocess.run(
            [py, "-m", "yt_archive.english", "--diarize", spec],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = err[-1] if err else f"exit code {proc.returncode}"
            raise RuntimeError(f"speaker diarization failed: {tail}")
        data = json.loads(out_json.read_text(encoding="utf-8"))
        log(f"diarized {len(data['turns'])} turns across {data['speakers']} speakers")
        return data["turns"]


# ---------------------------------------------------------------- LLM translation


def _fmt_clock(sec: float) -> str:
    s = max(0, int(sec))
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}"


def _build_instructions(info: dict, source_language: str) -> str:
    title = (info.get("title") or "").strip()
    channel = (info.get("channel") or "").strip()
    desc = (info.get("description") or "").strip()[:1500]
    meta = [f"Title: {title}" if title else "", f"Channel: {channel}" if channel else ""]
    if source_language:
        meta.append(f"Source language: {source_language}")
    if desc:
        meta.append(f"Description excerpt:\n{desc}")
    meta_block = "\n".join(m for m in meta if m)
    return (
        "You translate spoken video transcripts into natural, documentary-grade English subtitles "
        "and concise voiceover dubbing lines.\n\n"
        f"{meta_block}\n\n"
        "Rules:\n"
        "1. Translate EVERY segment listed under SEGMENTS TO TRANSLATE 1-to-1 by exact integer id. "
        "Never omit, combine, or renumber any id.\n"
        "2. Silently repair obvious speech-recognition homophones using context and video metadata "
        "(names, institutions, places, technical or historical terms).\n"
        "3. When one spoken sentence spans multiple consecutive short segments, split the English "
        "cleanly across those segments so consecutive lines flow smoothly without repeating clauses.\n"
        "4. en_sub: accurate, idiomatic English subtitles.\n"
        "5. en_dub: concise, speakable English for TTS voiceover paced to roughly match the "
        "segment's duration in seconds. No brackets, stage directions, or untranslated words.\n"
        "6. speaker: assign a consistent concise English role/name per diarized S# tag (e.g. host "
        "name from metadata, interviewee name, 'Narrator', 'Male Speaker', 'Female Speaker', "
        "'Female Parent', 'Male Student'). Refine S# from dialogue context when archival clips "
        "contain back-and-forth exchanges.\n"
        "Return ONLY valid JSON matching the schema."
    )


def _format_chunk_prompt(ctx: list[dict], body: list[dict]) -> str:
    parts = []
    if ctx:
        parts.append("PRECEDING CONTEXT (for continuity only; do NOT include in output):")
        for s in ctx:
            parts.append(f"  [CTX id={s['id']} {_fmt_clock(s['start'])}-{_fmt_clock(s['end'])} S{s['speaker_idx']}] {s['text_orig']}")
        parts.append("")
    parts.append(f"SEGMENTS TO TRANSLATE ({len(body)} items):")
    for s in body:
        parts.append(
            f"id={s['id']} | {_fmt_clock(s['start'])}-{_fmt_clock(s['end'])} ({s['duration']:.1f}s) "
            f"| S{s['speaker_idx']} | {s['text_orig']}"
        )
    return "\n".join(parts)


def translate_segments(
    segments: list[dict],
    info: dict,
    source_language: str,
    cfg: dict,
    *,
    gpu: dict,
    log=print,
    progress=None,
) -> tuple[list[dict], dict]:
    """Translate diarized segments in chunks using the configured LLM backend."""
    instructions = _build_instructions(info, source_language)
    chunk_size = int(cfg.get("chunk_size") or 120)
    chunks = []
    for start_i in range(0, len(segments), chunk_size):
        end_i = min(len(segments), start_i + chunk_size)
        chunks.append((segments[max(0, start_i - 5):start_i], segments[start_i:end_i]))

    is_ollama = cfg["provider"] == "ollama"
    workers = 1 if is_ollama else min(int(cfg.get("concurrency") or 6), max(1, len(chunks)))
    log(f"translating {len(segments)} segments in {len(chunks)} chunks via {cfg['provider']}/{cfg['model']} (workers={workers})")

    by_id: dict[int, dict] = {}
    tokens_in = 0
    tokens_out = 0
    gate = gpu_gate.load() if is_ollama else None
    try:
        def _run_chunk(idx_pair):
            idx, (ctx, body) = idx_pair
            ans = llm.ask_json(cfg, instructions, _format_chunk_prompt(ctx, body), TRANSLATION_SCHEMA, gpu=gpu)
            return idx, ans

        done = 0
        if workers == 1:
            for item in enumerate(chunks):
                idx, ans = _run_chunk(item)
                tokens_in += ans.input_tokens
                tokens_out += ans.output_tokens
                for rec in ans.data.get("items") or []:
                    by_id[int(rec["id"])] = rec
                done += 1
                msg = f"translating chunk {done}/{len(chunks)}"
                log(msg)
                if progress:
                    progress(done / len(chunks), msg)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(_run_chunk, item) for item in enumerate(chunks)]
                for fut in concurrent.futures.as_completed(futs):
                    idx, ans = fut.result()
                    tokens_in += ans.input_tokens
                    tokens_out += ans.output_tokens
                    for rec in ans.data.get("items") or []:
                        by_id[int(rec["id"])] = rec
                    done += 1
                    msg = f"translating chunk {done}/{len(chunks)}"
                    log(msg)
                    if progress:
                        progress(done / len(chunks), msg)

        missing = [s for s in segments if s["id"] not in by_id or not str(by_id[s["id"]].get("en_sub") or "").strip()]
        if missing:
            log(f"repairing {len(missing)} missing translated segment(s)")
            for start_i in range(0, len(missing), 60):
                batch = missing[start_i:start_i + 60]
                ans = llm.ask_json(cfg, instructions, _format_chunk_prompt([], batch), TRANSLATION_SCHEMA, gpu=gpu)
                tokens_in += ans.input_tokens
                tokens_out += ans.output_tokens
                for rec in ans.data.get("items") or []:
                    by_id[int(rec["id"])] = rec
    finally:
        if is_ollama:
            llm.unload_ollama(cfg.get("ollama_url") or "http://127.0.0.1:11434", cfg["model"], gate=gate)

    still_missing = [s["id"] for s in segments if s["id"] not in by_id or not str(by_id[s["id"]].get("en_sub") or "").strip()]
    if still_missing:
        raise RuntimeError(f"translation left {len(still_missing)} segment(s) untranslated: {still_missing[:10]}")

    out = []
    for s in segments:
        rec = by_id[s["id"]]
        en_sub = str(rec.get("en_sub") or "").strip()
        en_dub = str(rec.get("en_dub") or en_sub).strip()
        spk = _speaker_name(str(rec.get("speaker") or ""))
        out.append({
            "index": len(out),
            "start": s["start"],
            "end": s["end"],
            "text": en_sub,
            "dub_text": en_dub,
            "text_orig": s["text_orig"],
            "speaker": spk,
            "speaker_idx": s["speaker_idx"],
            "words": [],
        })
    return out, {"input_tokens": tokens_in, "output_tokens": tokens_out}


def _translate_chapters(chapters: list[dict], cfg: dict, gpu: dict) -> list[dict]:
    if not chapters:
        return []
    titles = [str(c.get("title") or "").strip() for c in chapters]
    if all(t.isascii() for t in titles if t):
        return chapters
    ans = llm.ask_json(
        cfg,
        "Translate these video chapter titles into concise English. Return one title per input line in the exact same order.",
        json.dumps({"titles": titles}, ensure_ascii=False),
        CHAPTERS_SCHEMA,
        gpu=gpu,
    )
    translated = ans.data.get("titles") or []
    if len(translated) != len(chapters):
        return chapters
    return [
        {**c, "title": str(t).strip() or c.get("title", "")}
        for c, t in zip(chapters, translated)
    ]


# ---------------------------------------------------------------- Kokoro dubbing


def cast_voices(segments: list[dict]) -> dict[str, tuple[str, str, float, str]]:
    """Deterministically assign distinct Kokoro voices and ASS colors to each speaker."""
    talk: dict[str, float] = defaultdict(float)
    for seg in segments:
        spk = seg.get("speaker") or "Speaker"
        talk[spk] += max(0.1, float(seg["end"]) - float(seg["start"]))
    ordered = sorted(talk, key=lambda s: (-talk[s], s))
    casting: dict[str, tuple[str, str, float, str]] = {}
    f_idx = 0
    m_idx = 0
    for i, spk in enumerate(ordered):
        if _FEMALE_HINT.search(spk):
            casting[spk] = FEMALE_VOICES[f_idx % len(FEMALE_VOICES)]
            f_idx += 1
        elif _MALE_HINT.search(spk):
            casting[spk] = MALE_VOICES[m_idx % len(MALE_VOICES)]
            m_idx += 1
        else:
            # Alternate female/male for unlabeled distinct speakers so dialogues separate clearly.
            if i % 2 == 0:
                casting[spk] = FEMALE_VOICES[f_idx % len(FEMALE_VOICES)]
                f_idx += 1
            else:
                casting[spk] = MALE_VOICES[m_idx % len(MALE_VOICES)]
                m_idx += 1
    return casting


def merge_utterances(segments: list[dict], total_duration: float) -> list[dict]:
    """Merge consecutive short same-speaker segments for smooth TTS delivery."""
    utterances: list[dict] = []
    for seg in segments:
        text = (seg.get("dub_text") or seg.get("text") or "").strip()
        if not text:
            continue
        spk = seg.get("speaker") or "Speaker"
        start = float(seg["start"])
        end = float(seg["end"])
        if utterances:
            prev = utterances[-1]
            gap = start - prev["end"]
            merged_span = end - prev["start"]
            prev_ends = prev["text_dub"][-1:] in ".?!"
            max_gap = 0.55 if prev_ends else 0.90
            if (
                prev["speaker"] == spk
                and gap <= max_gap
                and merged_span <= 14.0
                and not (prev_ends and (prev["end"] - prev["start"]) >= 5.5)
            ):
                prev["end"] = end
                prev["text_dub"] = prev["text_dub"] + " " + text
                continue
        utterances.append({
            "uid": len(utterances),
            "speaker": spk,
            "start": start,
            "end": end,
            "text_dub": text,
        })
    for i, u in enumerate(utterances):
        next_start = utterances[i + 1]["start"] if i + 1 < len(utterances) else max(u["end"] + 1.0, total_duration - 0.1)
        u["window"] = round(max(0.45, (next_start - 0.10) - u["start"]), 3)
    return utterances


def kokoro_model_paths(cfg: dict) -> tuple[Path, Path]:
    base = Path(cfg["kokoro_model_dir"]).expanduser() if cfg.get("kokoro_model_dir") else DEFAULT_KOKORO_DIR
    model = base / "kokoro-v1.0.onnx"
    voices = base / "voices-v1.0.bin"
    if not model.is_file() or not voices.is_file():
        raise FileNotFoundError(
            f"Kokoro v1.0 model files missing in {base} "
            "(need kokoro-v1.0.onnx and voices-v1.0.bin; see docs/english.md)"
        )
    return model, voices


def _clean_for_tts(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = text.replace("—", ", ").replace("–", ", ").replace("...", ", ")
    return re.sub(r"\s+", " ", text).strip()


def _trim_silence(wav, sr: int, thresh: float = 0.004, pad_sec: float = 0.025):
    import numpy as np

    idx = np.where(np.abs(wav) > thresh)[0]
    if len(idx) == 0:
        return wav[: min(len(wav), int(0.1 * sr))]
    pad = int(pad_sec * sr)
    return wav[max(0, int(idx[0]) - pad): min(len(wav), int(idx[-1]) + pad)]


def _normalize_clip(wav, target_rms: float = 0.125):
    import numpy as np

    if len(wav) == 0:
        return wav
    rms = float(np.sqrt(np.mean(wav.astype(np.float64) ** 2)))
    if rms > 1e-5:
        wav = wav * min(3.5, target_rms / rms)
    peak = float(np.max(np.abs(wav)))
    if peak > 0.88:
        wav = wav * (0.88 / peak)
    return wav.astype(np.float32)


def synthesize_dub(
    video_path: Path,
    segments: list[dict],
    total_duration: float,
    cfg: dict,
    out_m4a: Path,
    *,
    gpu: dict,
    log=print,
    progress=None,
) -> dict:
    """Synthesize multi-speaker Kokoro dub and mix over smoothly ducked original stereo audio."""
    import numpy as np
    import onnxruntime as ort
    import soundfile as sf
    from kokoro_onnx import Kokoro

    model_path, voices_path = kokoro_model_paths(cfg)
    casting = cast_voices(segments)
    utterances = merge_utterances(segments, total_duration)
    if not utterances:
        raise RuntimeError("no spoken English lines to synthesize")

    use_cuda = "CUDAExecutionProvider" in ort.get_available_providers()
    if use_cuda and hasattr(ort, "preload_dlls"):
        ort.preload_dlls()
    gate = gpu_gate.load() if use_cuda else None
    held = "Kokoro v1.0 ONNX" if gate else ""
    if held:
        os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
        _hold_gpu_claim(gate, held, gpu_gate.KOKORO_GB, gpu, max(3.0, total_duration / 60 / 8 + 2), log, progress)
    elif use_cuda:
        os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"

    with tempfile.TemporaryDirectory(prefix="ytarchive-dub-") as tmp_dir:
        clips_dir = Path(tmp_dir)
        try:
            kokoro = Kokoro(str(model_path), str(voices_path))
            active_ep = kokoro.sess.get_providers()[0]
            if use_cuda and active_ep != "CUDAExecutionProvider":
                raise RuntimeError(f"Kokoro failed to start on CUDA (got {active_ep})")
            log(f"synthesizing {len(utterances)} utterances across {len(casting)} voices ({active_ep})")
            t0 = time.monotonic()
            for idx, u in enumerate(utterances):
                wav_path = clips_dir / f"u_{u['uid']:04d}.wav"
                text = _clean_for_tts(u["text_dub"])
                voice, lang, base_speed, _ = casting.get(u["speaker"], MALE_VOICES[0])
                window = float(u["window"])
                if not text:
                    sf.write(str(wav_path), np.zeros(240, dtype=np.float32), 24000)
                    continue
                words = max(1, len(text.split()))
                init_speed = base_speed
                est_dur = words / 2.65
                if window > 0.4 and est_dur > window * 1.05:
                    init_speed = min(1.22, max(base_speed, (est_dur / window) * 0.98))
                samples, sr = kokoro.create(text, voice=voice, speed=float(init_speed), lang=lang)
                samples = _trim_silence(np.asarray(samples, dtype=np.float32), sr)
                dur = len(samples) / sr
                if window > 0.4 and dur > window * 1.03 and init_speed < 1.24:
                    new_speed = min(1.25, init_speed * (dur / window) * 1.02)
                    if new_speed > init_speed + 0.03:
                        samples2, sr = kokoro.create(text, voice=voice, speed=float(new_speed), lang=lang)
                        samples = _trim_silence(np.asarray(samples2, dtype=np.float32), sr)
                        dur = len(samples) / sr
                samples = _normalize_clip(samples)
                sf.write(str(wav_path), samples, sr)
                if window > 0.35 and dur > window * 1.02:
                    atempo = min(1.38, dur / window)
                    if atempo > 1.03:
                        tempo_wav = wav_path.with_suffix(".tempo.wav")
                        subprocess.run(
                            ["ffmpeg", "-v", "error", "-y", "-i", str(wav_path),
                             "-filter:a", f"atempo={atempo:.4f}", "-ar", str(sr), "-ac", "1", str(tempo_wav)],
                            check=True,
                        )
                        tempo_wav.replace(wav_path)
                if (idx + 1) % 25 == 0 or idx + 1 == len(utterances):
                    frac = (idx + 1) / len(utterances)
                    msg = f"synthesizing dub {idx + 1}/{len(utterances)}"
                    if progress:
                        progress(frac * 0.8, msg)
                    if (idx + 1) % 100 == 0 or idx + 1 == len(utterances):
                        log(f"{msg} ({time.monotonic() - t0:.1f}s)")
            del kokoro
        finally:
            if held:
                gate.let_go(held)

        if progress:
            progress(0.85, "mixing English dub over original audio")
        log("mixing English dub over ducked original audio")
        return _mix_dub_audio(video_path, utterances, clips_dir, out_m4a)


def _hold_gpu_claim(gate, what: str, gb: float, gpu: dict, minutes: float, log, progress) -> None:
    said = False

    def report(why: str) -> None:
        nonlocal said
        message = f"waiting for the GPU: {why}"
        if not said:
            log(message)
            said = True
        if progress:
            progress(0, message)

    gate.hold(what, gb, lane=gpu["lane"], who=gpu["who"], minutes=minutes, wait=None, report=report)


def _mix_dub_audio(video_path: Path, utterances: list[dict], clips_dir: Path, out_m4a: Path) -> dict:
    import numpy as np
    import soundfile as sf

    sr = 48000
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(video_path),
        "-vn", "-ac", "2", "-ar", str(sr), "-f", "f32le", "-",
    ])
    orig = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()
    n_samples = orig.shape[0]
    dub = np.zeros(n_samples, dtype=np.float32)
    mask = np.zeros(n_samples, dtype=np.float32)

    cursor_end = 0.0
    shifted = 0
    for u in utterances:
        wav_path = clips_dir / f"u_{u['uid']:04d}.wav"
        if not wav_path.is_file():
            continue
        clip, clip_sr = sf.read(str(wav_path), dtype="float32")
        if clip.ndim > 1:
            clip = clip.mean(axis=1)
        if clip_sr != sr:
            x = np.linspace(0, 1, len(clip), dtype=np.float64)
            x2 = np.linspace(0, 1, int(round(len(clip) * sr / clip_sr)), dtype=np.float64)
            clip = np.interp(x2, x, clip).astype(np.float32)
        place_start = u["start"]
        if place_start < cursor_end + 0.06:
            place_start = cursor_end + 0.06
            shifted += 1
        s0 = int(round(place_start * sr))
        if s0 >= n_samples:
            continue
        s1 = min(n_samples, s0 + len(clip))
        seg_len = s1 - s0
        if seg_len <= 0:
            continue
        dub[s0:s1] += clip[:seg_len]
        mask[s0:s1] = 1.0
        cursor_end = s1 / sr

    ctrl_rate = 100
    step = sr // ctrl_rate
    n_ctrl = int(math.ceil(n_samples / step))
    pad_len = n_ctrl * step - n_samples
    m_pad = np.pad(mask, (0, pad_len)) if pad_len > 0 else mask[: n_ctrl * step]
    ctrl = m_pad.reshape(n_ctrl, step).max(axis=1)

    dilated = np.zeros_like(ctrl)
    for idx in np.where(ctrl > 0.5)[0]:
        dilated[max(0, idx - 12): min(n_ctrl, idx + 23)] = 1.0
    kernel = np.hanning(21)[1:-1]
    kernel /= kernel.sum()
    smooth_ctrl = np.clip(np.convolve(dilated, kernel, mode="same"), 0.0, 1.0)
    duck_gain = 1.0 - smooth_ctrl * (1.0 - 0.13)
    gain_48k = np.interp(
        np.linspace(0, n_ctrl - 1, n_samples, dtype=np.float32),
        np.arange(n_ctrl, dtype=np.float32),
        duck_gain,
    ).astype(np.float32)

    orig *= gain_48k[:, None]
    orig[:, 0] += dub * 0.88
    orig[:, 1] += dub * 0.88
    peak = float(np.max(np.abs(orig)))
    if peak > 0.96:
        orig *= 0.96 / peak

    out_m4a.parent.mkdir(parents=True, exist_ok=True)
    tmp_wav = out_m4a.with_suffix(".mix.wav")
    tmp_m4a = out_m4a.with_suffix(".part.m4a")
    try:
        sf.write(str(tmp_wav), orig, sr, subtype="PCM_16")
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y", "-i", str(tmp_wav),
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                str(tmp_m4a),
            ],
            check=True,
        )
        tmp_m4a.replace(out_m4a)
    finally:
        tmp_wav.unlink(missing_ok=True)
        tmp_m4a.unlink(missing_ok=True)
    return {"utterances": len(utterances), "shifted": shifted, "duration": round(n_samples / sr, 1)}


# ---------------------------------------------------------------- Subtitles & MKV export


def _fmt_srt_time(sec: float) -> str:
    ms = max(0, int(round(float(sec or 0) * 1000)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_ass_time(sec: float) -> str:
    cs = max(0, int(round(float(sec or 0) * 100)))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_srt(segments: list[dict], field: str = "text", *, include_speaker: bool = False) -> str:
    lines = []
    last_spk = ""
    idx = 1
    for seg in segments:
        txt = str(seg.get(field) or "").strip()
        if not txt:
            continue
        spk = str(seg.get("speaker") or "").strip()
        body = f"[{spk}] {txt}" if (include_speaker and spk and spk != last_spk) else txt
        last_spk = spk
        lines.append(f"{idx}\n{_fmt_srt_time(seg['start'])} --> {_fmt_srt_time(seg['end'])}\n{body}\n")
        idx += 1
    return "\n".join(lines)


def build_ass(segments: list[dict], title: str = "English Subtitles") -> str:
    casting = cast_voices(segments)
    header = (
        "[Script Info]\n"
        f"Title: {title}\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Box,DejaVu Sans,20,&H0A000000,&H00000000,&H0A000000,&H0A000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
        "Style: Default,DejaVu Sans,44,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,5,180,180,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    last_spk = ""
    for seg in segments:
        txt = str(seg.get("text") or "").strip()
        if not txt:
            continue
        t0 = _fmt_ass_time(seg["start"])
        t1 = _fmt_ass_time(seg["end"])
        spk = str(seg.get("speaker") or "").strip()
        color = casting.get(spk, MALE_VOICES[0])[3]
        prefix = f"{spk}: " if (spk and spk != last_spk) else ""
        last_spk = spk
        wrapped = textwrap.wrap(prefix + txt, width=54)
        if len(wrapped) > 2:
            wrapped = textwrap.wrap(prefix + txt, width=64)
        joined = "\\N".join(
            w.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}") for w in wrapped
        )
        if prefix:
            joined = f"{{\\c{color}}}{spk}:{{\\c&H00FFFFFF&}}" + joined[len(spk) + 1:]
        events.append(f"Dialogue: 0,{t0},{t1},Box,,0,0,0,,{{\\p1\\pos(0,0)}}m 190 868 l 1730 868 l 1730 1036 l 190 1036{{\\p0}}")
        events.append(f"Dialogue: 1,{t0},{t1},Default,{spk},0,0,0,,{{\\an5\\pos(960,952)}}{joined}")
    return header + "\n".join(events) + "\n"


def _read_video_chapters(data_dir: Path, video_id: str) -> list[dict]:
    folder = video_dir(data_dir, video_id)
    for p in sorted(folder.glob("*.info.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            chapters = raw.get("chapters") or []
            if isinstance(chapters, list) and chapters:
                return [
                    {
                        "start": float(c.get("start_time") or 0),
                        "end": float(c.get("end_time") or 0),
                        "title": str(c.get("title") or "").strip(),
                    }
                    for c in chapters
                    if c.get("title")
                ]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return []


def _ffmetadata(title: str, artist: str, chapters: list[dict], duration: float) -> str:
    def _esc_meta(s: str) -> str:
        return re.sub(r"([=;#\\\n])", r"\\\1", str(s or ""))

    lines = [";FFMETADATA1", f"title={_esc_meta(title)}", f"artist={_esc_meta(artist)}", ""]
    for c in chapters:
        s = max(0, int(round(float(c.get("start") or 0))))
        e = min(int(math.ceil(duration)), int(round(float(c.get("end") or s))))
        if e <= s:
            continue
        lines.extend(["[CHAPTER]", "TIMEBASE=1/1", f"START={s}", f"END={e}", f"title={_esc_meta(c.get('title') or '')}", ""])
    return "\n".join(lines)


def export_english_mkv(data_dir: Path, video_id: str) -> Path:
    """Losslessly mux original video + English dub (if present) + Original audio + subtitles into english.mkv."""
    with video_lock(video_id):
        video = find_video_file(data_dir, video_id)
        if not video:
            raise FileNotFoundError("archived video is missing")
        if not english_is_complete(data_dir, video_id):
            raise FileNotFoundError("no English translation yet")
        en_json = english_transcript_json_path(data_dir, video_id)
        artifact = json.loads(en_json.read_text(encoding="utf-8"))
        segments = artifact.get("segments") or []
        ass_path = english_ass_path(data_dir, video_id)
        srt_path = english_srt_path(data_dir, video_id)
        orig_srt = orig_srt_path(data_dir, video_id)
        if not ass_path.is_file():
            _atomic_text(ass_path, build_ass(segments, title=video_id))
        if not srt_path.is_file():
            _atomic_text(srt_path, build_srt(segments, "text", include_speaker=True))
        if not orig_srt.is_file() and any(s.get("text_orig") for s in segments):
            _atomic_text(orig_srt, build_srt(segments, "text_orig", include_speaker=False))

        dub = english_dub_path(data_dir, video_id)
        has_dub = dub.is_file() and dub.stat().st_size > 0
        out_mkv = english_mkv_path(data_dir, video_id)
        inputs_mtime = max(
            p.stat().st_mtime_ns for p in (video, en_json, ass_path, srt_path, dub) if p.is_file()
        )
        if out_mkv.is_file() and out_mkv.stat().st_mtime_ns >= inputs_mtime and out_mkv.stat().st_size > 0:
            return out_mkv

        info = {}
        try:
            info = json.loads(archive_json_path(data_dir, video_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        chapters = artifact.get("chapters") or []
        duration = float(artifact.get("duration") or info.get("duration") or 0)
        src_lang = str(artifact.get("source_language") or "und")

        with tempfile.TemporaryDirectory(prefix="ytarchive-mkv-") as tmp:
            meta_file = Path(tmp) / "chapters.ffmeta"
            meta_file.write_text(
                _ffmetadata(info.get("title") or video_id, info.get("channel") or "", chapters, duration),
                encoding="utf-8",
            )
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(video)]
            idx = 1
            dub_idx = None
            if has_dub:
                cmd.extend(["-i", str(dub)])
                dub_idx = idx
                idx += 1
            cmd.extend(["-i", str(ass_path)])
            ass_idx = idx
            idx += 1
            cmd.extend(["-i", str(srt_path)])
            srt_idx = idx
            idx += 1
            orig_idx = None
            if orig_srt.is_file() and orig_srt.stat().st_size > 0:
                cmd.extend(["-i", str(orig_srt)])
                orig_idx = idx
                idx += 1
            cmd.extend(["-i", str(meta_file)])
            meta_idx = idx

            cmd.extend(["-map", "0:v:0"])
            if dub_idx is not None:
                cmd.extend(["-map", f"{dub_idx}:a:0", "-map", "0:a:0?"])
            else:
                cmd.extend(["-map", "0:a:0?"])
            cmd.extend(["-map", f"{ass_idx}:s:0", "-map", f"{srt_idx}:s:0"])
            if orig_idx is not None:
                cmd.extend(["-map", f"{orig_idx}:s:0"])
            cmd.extend(["-map_metadata", str(meta_idx), "-map_chapters", str(meta_idx)])
            cmd.extend(["-c:v", "copy", "-c:a", "copy", "-c:s:0", "ass", "-c:s:1", "srt"])
            if orig_idx is not None:
                cmd.extend(["-c:s:2", "srt"])
            if dub_idx is not None:
                cmd.extend([
                    "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=English (AI Dub)",
                    "-metadata:s:a:1", f"language={src_lang}", "-metadata:s:a:1", "title=Original",
                    "-disposition:a:0", "default", "-disposition:a:1", "0",
                ])
            else:
                cmd.extend(["-metadata:s:a:0", f"language={src_lang}", "-metadata:s:a:0", "title=Original"])
            cmd.extend([
                "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=English (Styled)",
                "-metadata:s:s:1", "language=eng", "-metadata:s:s:1", "title=English (Plain)",
                "-disposition:s:0", "default", "-disposition:s:1", "0",
            ])
            if orig_idx is not None:
                cmd.extend([
                    "-metadata:s:s:2", f"language={src_lang}", "-metadata:s:s:2", "title=Original",
                    "-disposition:s:2", "0",
                ])
            tmp_mkv = out_mkv.with_suffix(".part.mkv")
            cmd.append(str(tmp_mkv))
            try:
                subprocess.run(cmd, check=True)
                tmp_mkv.replace(out_mkv)
            finally:
                tmp_mkv.unlink(missing_ok=True)
        db.upsert(data_dir, video_id)
        return out_mkv


# ---------------------------------------------------------------- Orchestrator + child entry


def _publish_english_files(
    data_dir: Path,
    video_id: str,
    artifact: dict,
    *,
    has_dub: bool | None = None,
) -> None:
    records = artifact.get("segments") or []
    vtt_text = _webvtt(records)
    artifact["full_text"] = "\n".join(str(r.get("text") or "") for r in records)
    artifact["vtt_sha256"] = hashlib.sha256(vtt_text.encode("utf-8")).hexdigest()

    vtt_path = english_transcript_vtt_path(data_dir, video_id)
    json_path = english_transcript_json_path(data_dir, video_id)
    ass_path = english_ass_path(data_dir, video_id)
    srt_path = english_srt_path(data_dir, video_id)
    orig_srt = orig_srt_path(data_dir, video_id)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    _atomic_text(vtt_path, vtt_text)
    _atomic_text(ass_path, build_ass(records, title=artifact.get("title") or video_id))
    _atomic_text(srt_path, build_srt(records, "text", include_speaker=True))
    if any(r.get("text_orig") for r in records):
        _atomic_text(orig_srt, build_srt(records, "text_orig", include_speaker=False))
    # JSON is the publication marker, written last.
    _atomic_text(json_path, json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")

    video = find_video_file(data_dir, video_id)
    if video:
        extras = {
            "english_transcript": json_path.name,
            "english_captions": vtt_path.name,
        }
        if english_has_dub(data_dir, video_id):
            extras["english_dub"] = english_dub_path(data_dir, video_id).name
        write_archive_json(data_dir, video_id, video, extra=extras)
    db.replace_english_transcript(data_dir, artifact, has_dub=has_dub)
    db.upsert(data_dir, video_id)


def update_english_segment_speaker(data_dir: Path, video_id: str, segment_index: int, speaker: str) -> dict:
    with video_lock(video_id):
        path = english_transcript_json_path(data_dir, video_id)
        if not path.is_file():
            raise FileNotFoundError("no English transcript")
        artifact = json.loads(path.read_text(encoding="utf-8"))
        segments = artifact.get("segments") or []
        if segment_index < 0 or segment_index >= len(segments):
            raise ValueError("unknown transcript segment")
        segments[segment_index]["speaker"] = _speaker_name(speaker)
        _publish_english_files(data_dir, video_id, artifact)
        return artifact


def rename_english_speaker(data_dir: Path, video_id: str, old: str, new: str) -> dict:
    old = _speaker_name(old)
    new = _speaker_name(new)
    if not old:
        raise ValueError("choose an existing speaker to rename")
    with video_lock(video_id):
        path = english_transcript_json_path(data_dir, video_id)
        if not path.is_file():
            raise FileNotFoundError("no English transcript")
        artifact = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for seg in artifact.get("segments") or []:
            if seg.get("speaker", "") == old:
                seg["speaker"] = new
                changed = True
        if not changed:
            raise ValueError(f"speaker not found: {old}")
        _publish_english_files(data_dir, video_id, artifact)
        return artifact


def build_english(
    data_dir: Path,
    video_id: str,
    config: dict | None = None,
    *,
    log=print,
    progress=None,
    work_key: str = "",
    source_sha256: str = "",
    generation_id: str = "",
    gpu: dict | None = None,
    settings_path: Path | None = None,
) -> dict:
    with video_lock(video_id):
        return _build_english_locked(
            data_dir,
            video_id,
            config,
            log=log,
            progress=progress,
            work_key=work_key,
            source_sha256=source_sha256,
            generation_id=generation_id,
            gpu=gpu or gpu_gate.ENGLISH_BY_HAND,
            settings_path=settings_path,
        )


def _build_english_locked(
    data_dir: Path,
    video_id: str,
    config: dict | None,
    *,
    log,
    progress,
    work_key: str,
    source_sha256: str,
    generation_id: str,
    gpu: dict,
    settings_path: Path | None,
) -> dict:
    job_cfg = normalize_english_job_config(config)
    en_cfg = load_settings(settings_path)["english"]
    video = find_video_file(data_dir, video_id)
    audio = soundtrack_path(data_dir, video_id)
    if not video or not audio.is_file():
        raise FileNotFoundError("video and MP3 audio must exist before building an English version")

    # Fail fast before spending GPU time if the translation backend or Kokoro models are missing.
    llm.check_configured(en_cfg)
    if job_cfg["include_dub"]:
        kokoro_model_paths(en_cfg)

    def stage_progress(lo: float, hi: float):
        def _cb(frac: float, msg: str) -> None:
            if progress:
                progress(lo + (hi - lo) * max(0.0, min(1.0, float(frac))), msg)
        return _cb

    # Stage 1: Original Whisper transcript (reused if already present and matching language).
    if transcript_is_complete(data_dir, video_id):
        orig = json.loads(transcript_json_path(data_dir, video_id).read_text(encoding="utf-8"))
        log(f"reusing existing {orig.get('language') or 'unknown'} transcript ({len(orig.get('segments') or [])} segments)")
    else:
        if progress:
            progress(0.02, "transcribing original speech with Whisper")
        orig = transcribe_soundtrack(
            data_dir,
            video_id,
            {"model": "large-v3", "language": job_cfg["source_language"], "beam_size": 5, "vad_filter": True},
            log=log,
            progress=stage_progress(0.02, 0.35),
            gpu=gpu,
        )

    duration = float(orig.get("duration") or 0)
    source_lang = str(orig.get("language") or job_cfg["source_language"] or "")
    info = {}
    try:
        info = json.loads(archive_json_path(data_dir, video_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    if not duration:
        duration = float(info.get("duration") or 0)

    # Stage 2: Speaker diarization (optional unless diarize_python is explicitly configured).
    if progress:
        progress(0.36, "diarizing speakers")
    turns = run_diarization(audio, en_cfg, gpu, log)
    diarized = split_segments_by_speaker(orig.get("segments") or [], turns)
    if not diarized:
        raise RuntimeError("original transcript has no spoken segments")

    # Stage 3: LLM translation.
    if progress:
        progress(0.45, f"translating with {en_cfg['model']}")
    translated, usage = translate_segments(
        diarized,
        info,
        source_lang,
        en_cfg,
        gpu=gpu,
        log=log,
        progress=stage_progress(0.45, 0.72),
    )
    chapters = _translate_chapters(_read_video_chapters(data_dir, video_id), en_cfg, gpu)

    artifact = {
        "format_version": 1,
        "video_id": video_id,
        "title": info.get("title") or video_id,
        "work_key": work_key,
        "generation_id": generation_id,
        "source_sha256": source_sha256,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": f"whisper+{en_cfg['provider']}",
        "provider": en_cfg["provider"],
        "model": en_cfg["model"],
        "language": "en",
        "source_language": source_lang,
        "duration": duration,
        "tokens": usage,
        "chapters": chapters,
        "segments": translated,
    }
    _publish_english_files(data_dir, video_id, artifact, has_dub=english_has_dub(data_dir, video_id))
    log(
        f"saved {len(translated)} English segments "
        f"(tokens: {usage['input_tokens']} in, {usage['output_tokens']} out)"
    )

    # Stage 4: Optional Kokoro multi-speaker voiceover dub.
    if job_cfg["include_dub"]:
        dub_path = english_dub_path(data_dir, video_id)
        stats = synthesize_dub(
            video,
            translated,
            duration,
            en_cfg,
            dub_path,
            gpu=gpu,
            log=log,
            progress=stage_progress(0.73, 0.99),
        )
        _publish_english_files(data_dir, video_id, artifact, has_dub=True)
        log(f"saved English dub -> {dub_path.name} ({stats['utterances']} utterances)")

    if progress:
        progress(1.0, "English version ready")
    return artifact


def build_english_in_child(
    data_dir: Path,
    video_id: str,
    config: dict,
    *,
    log,
    progress,
    work_key: str,
    source_sha256: str,
    generation_id: str,
    gpu: dict | None = None,
) -> None:
    """Run build_english in a subprocess so CUDA memory is always released on exit."""
    spec = {
        "data_dir": str(Path(data_dir).resolve()),
        "video_id": video_id,
        "config": config,
        "work_key": work_key,
        "source_sha256": source_sha256,
        "generation_id": generation_id,
        "gpu": gpu or gpu_gate.ENGLISH_CLICKED,
    }
    error = ""
    with subprocess.Popen(
        [sys.executable, "-m", "yt_archive.english", json.dumps(spec)],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as child:
        for line in child.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                event = None
            if not isinstance(event, dict):
                log(line.rstrip())
            elif "progress" in event:
                progress(float(event["progress"]), str(event.get("message") or ""))
            elif "error" in event:
                error = str(event["error"])
            else:
                log(str(event.get("log", "")))
    if child.returncode:
        raise RuntimeError(error or f"English build stopped with exit code {child.returncode}")


def _child_main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "--diarize":
        spec = json.loads(argv[1])
        _diarize_in_process(Path(spec["audio"]), Path(spec["out"]), spec["gpu"])
        return 0
    with os.fdopen(os.dup(1), "w", encoding="utf-8") as events:
        os.dup2(2, 1)

        def send(**event) -> None:
            events.write(json.dumps(event) + "\n")
            events.flush()

        spec = json.loads(argv[0])
        try:
            build_english(
                Path(spec["data_dir"]),
                spec["video_id"],
                spec["config"],
                log=lambda message: send(log=str(message)),
                progress=lambda value, message: send(progress=value, message=message),
                work_key=spec["work_key"],
                source_sha256=spec["source_sha256"],
                generation_id=spec["generation_id"],
                gpu=spec["gpu"],
            )
        except Exception as exc:
            traceback.print_exc()
            send(error=str(exc))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv[1:]))
