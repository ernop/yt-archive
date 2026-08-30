"""Local Whisper transcription. Spoken words and non-speech events stay separate."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import sysconfig
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .download import write_archive_json
from .locks import video_lock
from .paths import (
    find_video_file,
    soundtrack_path,
    transcript_json_path,
    transcript_vtt_path,
)

DEFAULT_CONFIG = {
    "model": "large-v3",
    "language": "",
    "beam_size": 5,
    "vad_filter": True,
}
MODELS = ("large-v3", "large-v3-turbo", "distil-large-v3", "medium", "small")
_NON_SPEECH = re.compile(
    r"[\[(]\s*(?:music|applause|laughter|laughing|cheering|"
    r"waves?(?: crashing)?|birds?(?: chirping)?|noise|silence|"
    r"inaudible|crosstalk)\s*[\])]",
    re.IGNORECASE,
)


def normalize_config(raw: dict | None = None) -> dict:
    raw = raw or {}
    model = str(raw.get("model") or DEFAULT_CONFIG["model"])
    if model not in MODELS:
        raise ValueError(f"unsupported Whisper model: {model}")
    language = str(raw.get("language") or "").strip().lower()
    if language and not re.fullmatch(r"[a-z]{2,3}", language):
        raise ValueError("language must be an ISO code such as en, fi, or de")
    try:
        beam_size = int(raw.get("beam_size", DEFAULT_CONFIG["beam_size"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("beam size must be a number") from exc
    if not 1 <= beam_size <= 10:
        raise ValueError("beam size must be between 1 and 10")
    return {
        "model": model,
        "language": language,
        "beam_size": beam_size,
        "vad_filter": bool(raw.get("vad_filter", True)),
    }


def transcribe_soundtrack(
    data_dir: Path,
    video_id: str,
    config: dict | None = None,
    *,
    log=print,
    progress=None,
    work_key: str = "",
    source_sha256: str = "",
    generation_id: str = "",
) -> dict:
    with video_lock(video_id):
        return _transcribe_soundtrack_locked(
            data_dir,
            video_id,
            config,
            log=log,
            progress=progress,
            work_key=work_key,
            source_sha256=source_sha256,
            generation_id=generation_id,
        )


def _transcribe_soundtrack_locked(
    data_dir: Path,
    video_id: str,
    config: dict | None = None,
    *,
    log=print,
    progress=None,
    work_key: str = "",
    source_sha256: str = "",
    generation_id: str = "",
) -> dict:
    """Transcribe one existing MP3 and atomically publish JSON + WebVTT."""
    config = normalize_config(config)
    audio = soundtrack_path(data_dir, video_id)
    if not audio.is_file():
        raise FileNotFoundError("MP3 audio is missing; create it before transcribing")

    try:
        import ctranslate2
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "local Whisper is not installed; run .venv/bin/pip install faster-whisper"
        ) from exc

    cuda_ready, cuda_error = _activate_cuda_libraries()
    has_cuda = cuda_ready and ctranslate2.get_cuda_device_count() > 0
    if not cuda_ready:
        log(f"CUDA runtime unavailable ({cuda_error}); using CPU")
    device = "cuda" if has_cuda else "cpu"
    compute_type = "float16" if has_cuda else "int8"
    threads = max(1, min(16, (os.cpu_count() or 2) // 2))
    log(
        f"loading Whisper {config['model']} on {device} "
        f"({compute_type}{f', {threads} threads' if device == 'cpu' else ''})"
    )
    try:
        model = WhisperModel(
            config["model"],
            device=device,
            compute_type=compute_type,
            cpu_threads=threads,
        )
    except Exception:
        if device != "cuda":
            raise
        log("CUDA model load failed; retrying safely on CPU int8")
        device = "cpu"
        compute_type = "int8"
        model = WhisperModel(
            config["model"],
            device=device,
            compute_type=compute_type,
            cpu_threads=threads,
        )

    kwargs = {
        "beam_size": config["beam_size"],
        "vad_filter": config["vad_filter"],
        "word_timestamps": True,
        "condition_on_previous_text": True,
    }
    if config["language"]:
        kwargs["language"] = config["language"]
    generated, detected = model.transcribe(str(audio), **kwargs)

    duration = float(getattr(detected, "duration", 0) or 0)
    records = []
    last_report = -1
    for raw_segment in generated:
        text = _spoken_text(raw_segment.text)
        if not text:
            continue
        words = _timed_spoken_words(
            raw_segment.words or [], str(raw_segment.text or "")
        )
        records.append(
            {
                "index": len(records),
                "start": _round_time(raw_segment.start),
                "end": _round_time(raw_segment.end),
                "text": text,
                "speaker": "",
                "words": words,
            }
        )
        pct = int(min(99, (float(raw_segment.end) / duration) * 100)) if duration else 0
        if pct >= last_report + 5:
            last_report = pct
            message = f"transcribing {_clock(raw_segment.end)} / {_clock(duration)}"
            log(message)
            if progress:
                progress(pct / 100, message)

    if not records:
        raise RuntimeError("Whisper found no spoken language")
    full_text = "\n".join(record["text"] for record in records)
    artifact = {
        "format_version": 1,
        "video_id": video_id,
        "work_key": work_key,
        "generation_id": generation_id,
        "source_sha256": source_sha256,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": "faster-whisper",
        "model": config["model"],
        "device": device,
        "compute_type": compute_type,
        "language": getattr(detected, "language", "") or config["language"],
        "language_probability": round(
            float(getattr(detected, "language_probability", 0) or 0), 4
        ),
        "duration": duration,
        "config": config,
        "full_text": full_text,
        "segments": records,
    }
    json_path = transcript_json_path(data_dir, video_id)
    vtt_path = transcript_vtt_path(data_dir, video_id)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    vtt_text = _webvtt(records)
    artifact["vtt_sha256"] = hashlib.sha256(vtt_text.encode("utf-8")).hexdigest()
    _atomic_text(vtt_path, vtt_text)
    # JSON is the publication marker and is written last.
    _atomic_text(json_path, json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")

    video = find_video_file(data_dir, video_id)
    if not video:
        raise FileNotFoundError("archived video disappeared while transcribing")
    write_archive_json(
        data_dir,
        video_id,
        video,
        extra={"transcript": json_path.name, "captions": vtt_path.name},
    )
    from .db import replace_transcript

    replace_transcript(data_dir, artifact)
    if progress:
        progress(1.0, "transcript and captions saved")
    log(f"saved {len(records)} spoken segments → {json_path.name}, {vtt_path.name}")
    return artifact


def update_segment_speaker(
    data_dir: Path, video_id: str, segment_index: int, speaker: str
) -> dict:
    with video_lock(video_id):
        artifact = load_transcript(data_dir, video_id)
        segments = artifact.get("segments") or []
        if segment_index < 0 or segment_index >= len(segments):
            raise ValueError("unknown transcript segment")
        segments[segment_index]["speaker"] = _speaker_name(speaker)
        _save_edited(data_dir, artifact)
        return artifact


def rename_speaker(data_dir: Path, video_id: str, old: str, new: str) -> dict:
    old = _speaker_name(old)
    new = _speaker_name(new)
    if not old:
        raise ValueError("choose an existing speaker to rename")
    with video_lock(video_id):
        artifact = load_transcript(data_dir, video_id)
        changed = False
        for segment in artifact.get("segments") or []:
            if segment.get("speaker", "") == old:
                segment["speaker"] = new
                changed = True
        if not changed:
            raise ValueError(f"speaker not found: {old}")
        _save_edited(data_dir, artifact)
        return artifact


def load_transcript(data_dir: Path, video_id: str) -> dict:
    path = transcript_json_path(data_dir, video_id)
    if not path.is_file():
        raise FileNotFoundError("no transcript")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_edited(data_dir: Path, artifact: dict) -> None:
    video_id = artifact["video_id"]
    records = artifact.get("segments") or []
    artifact["full_text"] = "\n".join(record.get("text", "") for record in records)
    vtt_text = _webvtt(records)
    artifact["vtt_sha256"] = hashlib.sha256(vtt_text.encode("utf-8")).hexdigest()
    _atomic_text(transcript_vtt_path(data_dir, video_id), vtt_text)
    _atomic_text(
        transcript_json_path(data_dir, video_id),
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
    )
    from .db import replace_transcript

    replace_transcript(data_dir, artifact)


def _speaker_name(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(value) > 80:
        raise ValueError("speaker name is too long")
    return value


def _spoken_text(value: str) -> str:
    text = _NON_SPEECH.sub("", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _timed_spoken_words(raw_words, segment_text: str) -> list[dict]:
    raw_words = list(raw_words)
    joined = "".join(str(word.word or "") for word in raw_words)
    spans = [(match.start(), match.end()) for match in _NON_SPEECH.finditer(joined)]
    # If the segment has a known event but token joining cannot locate it, favor
    # clean speech text over leaking the event through word-level rendering.
    if _NON_SPEECH.search(segment_text) and not spans:
        return []
    records = []
    offset = 0
    for word in raw_words:
        raw = str(word.word or "")
        start_offset, end_offset = offset, offset + len(raw)
        offset = end_offset
        if any(start_offset < span_end and end_offset > span_start for span_start, span_end in spans):
            continue
        token = _spoken_text(raw)
        if not token:
            continue
        records.append(
            {
                "start": _round_time(word.start),
                "end": _round_time(word.end),
                "word": token,
                "probability": round(float(word.probability or 0), 4),
            }
        )
    return records


def _round_time(value) -> float:
    return round(float(value or 0), 3)


def _clock(value) -> str:
    seconds = max(0, int(float(value or 0)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (
        f"{hours}:{minutes:02d}:{seconds:02d}"
        if hours
        else f"{minutes}:{seconds:02d}"
    )


def _vtt_time(value) -> str:
    millis = max(0, int(round(float(value or 0) * 1000)))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _webvtt(records: list[dict]) -> str:
    chunks = ["WEBVTT\n"]
    for record in records:
        speaker = (
            str(record.get("speaker") or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        prefix = f"<v {speaker}>" if speaker else ""
        text = (
            str(record.get("text") or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        chunks.append(
            f"{_vtt_time(record.get('start'))} --> {_vtt_time(record.get('end'))}\n"
            f"{prefix}{text}\n"
        )
    return "\n".join(chunks)


def _atomic_text(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".part", dir=path.parent
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def _activate_cuda_libraries() -> tuple[bool, str]:
    """Load pip-installed CUDA libraries without requiring global LD_LIBRARY_PATH."""
    root = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
    libraries = (
        ("libcublasLt.so.12", root / "cublas" / "lib" / "libcublasLt.so.12"),
        ("libcublas.so.12", root / "cublas" / "lib" / "libcublas.so.12"),
        ("libcudnn.so.9", root / "cudnn" / "lib" / "libcudnn.so.9"),
    )
    try:
        for soname, packaged in libraries:
            try:
                ctypes.CDLL(soname, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                ctypes.CDLL(str(packaged), mode=ctypes.RTLD_GLOBAL)
    except OSError as exc:
        return False, str(exc)
    return True, ""
