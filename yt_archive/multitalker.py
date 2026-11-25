"""Multi-speaker transcription with overlap recovery via ONNX Runtime.

Pipeline (all ungated ONNX checkpoints, cached locally after first download):
  1. NVIDIA Nemotron-3-Diarization (Sortformer v3, OpenMDW-1.1): 10 ms frame-level
     diarization for up to 8 speakers, including simultaneous speech.
  2. Multitalker Parakeet Ultra (CC-BY-4.0, speaker-conditioned Parakeet TDT):
     transcribes one speaker at a time in ~20 s pause-aligned chunks using target
     and background speaker activity masks, preserving overlapping speech and
     word-level timestamps.
"""
from __future__ import annotations

import bisect
import gc
import math
import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import numpy as np

MODEL_REPO = "altunenes/parakeet-rs"
DIAR_MODEL_FILE = "nemotron-3-diarization/nemotron3_diar_v3.onnx"
ASR_MODEL_DIR = "multitalker-parakeet-ultra"
ASR_MODEL_FILES = (
    "encoder-model.onnx",
    "encoder-model.onnx.data",
    "decoder_joint-model.onnx",
    "vocab.txt",
)

SAMPLE_RATE = 16000
N_FFT = 512
WIN_LENGTH = 400
HOP_LENGTH = 160
N_MELS = 128
PREEMPH = 0.97
LOG_ZERO_GUARD = float(2.0 ** -24)

# Sortformer v3 / Nemotron-3-Diarization constants (defaults, overridden by ONNX metadata)
SUBSAMPLING = 8
UPSAMPLE_FACTOR = 8
EMB_DIM = 512
NUM_SPEAKERS = 8
FRAME_DURATION = 0.08
SPKCACHE_SIL_FRAMES_PER_SPK = 1
PRED_SCORE_THRESHOLD = 0.25
STRONG_BOOST_RATE = 0.75
WEAK_BOOST_RATE = 1.5
MIN_POS_SCORES_RATE = 0.5
SCORES_BOOST_LATEST = 0.05
MAX_INDEX = 99999

# Diarization binarization defaults: suppress <150 ms micro-blips, bridge <100 ms gaps.
DEFAULT_ONSET = 0.55
DEFAULT_OFFSET = 0.45
DEFAULT_MIN_ON = 0.15
DEFAULT_MIN_OFF = 0.10


def _round_bf16(arr: np.ndarray) -> np.ndarray:
    """Round float32 values to nearest bfloat16 (matches NeMo's stored bf16 STFT/mel tables)."""
    bits = np.ascontiguousarray(arr, dtype=np.float32).view(np.uint32)
    rounded = (bits + np.uint32(0x7FFF) + ((bits >> np.uint32(16)) & np.uint32(1))) & np.uint32(0xFFFF0000)
    return rounded.view(np.float32)


def _slaney_mel_filterbank(
    n_fft: int = N_FFT, n_mels: int = N_MELS, sample_rate: int = SAMPLE_RATE
) -> np.ndarray:
    """Slaney-normalized mel filterbank identical to librosa.filters.mel(..., norm='slaney')."""
    f_sp = 200.0 / 3.0
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    log_step = math.log(6.4) / 27.0

    def hz_to_mel(hz: float) -> float:
        return hz / f_sp if hz < min_log_hz else min_log_mel + math.log(hz / min_log_hz) / log_step

    def mel_to_hz(mel: float) -> float:
        return mel * f_sp if mel < min_log_mel else min_log_hz * math.exp((mel - min_log_mel) * log_step)

    mel_min = hz_to_mel(0.0)
    mel_max = hz_to_mel(sample_rate / 2.0)
    mel_pts = np.array(
        [mel_to_hz(mel_min + (mel_max - mel_min) * i / (n_mels + 1)) for i in range(n_mels + 2)],
        dtype=np.float64,
    )
    freq_bins = n_fft // 2 + 1
    fft_freqs = np.arange(freq_bins, dtype=np.float64) * (sample_rate / n_fft)
    fdiff = np.diff(mel_pts)

    fb = np.zeros((n_mels, freq_bins), dtype=np.float64)
    for i in range(n_mels):
        lower = (fft_freqs - mel_pts[i]) / fdiff[i]
        upper = (mel_pts[i + 2] - fft_freqs) / fdiff[i + 1]
        fb[i] = np.maximum(0.0, np.minimum(lower, upper))
        fb[i] *= 2.0 / (mel_pts[i + 2] - mel_pts[i])
    return fb.astype(np.float32)


def _apply_preemphasis(audio: np.ndarray, coef: float = PREEMPH) -> np.ndarray:
    if len(audio) == 0:
        return np.empty(0, dtype=np.float32)
    out = np.empty_like(audio, dtype=np.float32)
    out[0] = audio[0]
    out[1:] = audio[1:] - coef * audio[:-1]
    return out


def _stft_power(audio: np.ndarray, window: np.ndarray, *, drop_last: bool) -> np.ndarray:
    """Power spectrogram (freq_bins, frames) using torch.stft on CPU."""
    import torch

    spec = torch.stft(
        torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32)),
        n_fft=N_FFT,
        hop_length=HOP_LENGTH,
        win_length=WIN_LENGTH,
        window=torch.from_numpy(np.ascontiguousarray(window, dtype=np.float32)),
        center=True,
        pad_mode="constant",
        return_complex=True,
    )
    power = (spec.real.square() + spec.imag.square()).numpy().astype(np.float32, copy=False)
    if drop_last:
        power = power[:, : len(audio) // HOP_LENGTH]
    return power


def decode_audio_16k(audio_path: Path) -> np.ndarray:
    """Decode any audio file to 16 kHz mono float32 via ffmpeg."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(audio_path),
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-",
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg failed decoding {audio_path.name}: {detail or proc.returncode}")
    if not proc.stdout:
        raise RuntimeError(f"ffmpeg produced no audio from {audio_path.name}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _hf_file(filename: str) -> Path:
    """Return cached Hugging Face checkpoint path; download only if missing locally."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        return Path(hf_hub_download(MODEL_REPO, filename, local_files_only=True))
    except LocalEntryNotFoundError:
        return Path(hf_hub_download(MODEL_REPO, filename, local_files_only=False))


def ensure_models(log=print) -> tuple[Path, Path]:
    """Ensure Nemotron-3-Diarization and Multitalker Parakeet Ultra ONNX files are cached.

    ONNX Runtime rejects external data (.onnx.data) whose resolved symlink escapes the
    model file's real directory (Hugging Face stores each blob under blobs/<prefix>/).
    We hardlink the 4 ASR files into one flat sibling directory inside the HF cache so
    both files share a real parent without duplicating the 2.5 GB weights on disk.
    """
    log("checking cached multi-speaker ONNX models")
    diar_path = _hf_file(DIAR_MODEL_FILE)
    asr_paths = {name: _hf_file(f"{ASR_MODEL_DIR}/{name}") for name in ASR_MODEL_FILES}

    # Derive a stable cache key from the snapshot directory name (commit hash).
    snapshot_name = asr_paths["encoder-model.onnx"].parent.parent.name
    from huggingface_hub.constants import HF_HUB_CACHE

    staged = Path(HF_HUB_CACHE) / "yt_archive_staged" / snapshot_name / ASR_MODEL_DIR
    staged.mkdir(parents=True, exist_ok=True)
    for name, cached_link in asr_paths.items():
        real_src = cached_link.resolve()
        dst = staged / name
        if dst.exists() and dst.stat().st_ino == real_src.stat().st_ino:
            continue
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        try:
            os.link(real_src, dst)
        except OSError:
            shutil.copy2(real_src, dst)
    return diar_path, staged


def _ort_session(onnx_path: Path, *, use_cuda: bool):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    if use_cuda:
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    else:
        providers = ["CPUExecutionProvider"]
    sess = ort.InferenceSession(str(onnx_path), sess_options=opts, providers=providers)
    if use_cuda and "CUDAExecutionProvider" not in sess.get_providers():
        raise RuntimeError(
            "ONNX Runtime CUDAExecutionProvider failed to initialize; "
            "install onnxruntime-gpu with CUDA 12 runtime/cufft/curand/cublas/cudnn wheels"
        )
    return sess


def detect_onnx_cuda() -> bool:
    """Return True when ONNX Runtime CUDA libraries can be preloaded and a GPU is present."""
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError(
            "onnxruntime is not installed; install onnxruntime-gpu to use multi-speaker mode"
        ) from exc

    if hasattr(ort, "preload_dlls"):
        try:
            ort.preload_dlls()
        except Exception:
            pass
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        return False
    # Check nvidia-smi / ctranslate2 so a CPU-only machine with onnxruntime-gpu doesn't fail trying CUDA.
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return shutil.which("nvidia-smi") is not None


class NemotronDiarizer:
    """Streaming/offline ONNX runner for NVIDIA Nemotron-3-Diarization (Sortformer v3)."""

    def __init__(self, onnx_path: Path, *, use_cuda: bool):
        self.sess = _ort_session(onnx_path, use_cuda=use_cuda)
        meta = self.sess.get_modelmeta().custom_metadata_map
        self.chunk_len = int(meta.get("chunk_len", 340))
        self.fifo_len = int(meta.get("fifo_len", 40))
        self.spkcache_len = int(meta.get("spkcache_len", 264))
        self.right_context = int(meta.get("right_context", 40))
        self.spkcache_update_period = int(meta.get("spkcache_update_period", 300))
        raw_sil = meta.get("learnable_sil_emb", "")
        vals = [float(x.strip()) for x in raw_sil.split(",") if x.strip()]
        self.sil_emb = (
            np.array(vals, dtype=np.float32)
            if len(vals) == EMB_DIM
            else np.zeros(EMB_DIM, dtype=np.float32)
        )
        self.spkcache = np.zeros((1, 0, EMB_DIM), dtype=np.float32)
        self.spkcache_preds: np.ndarray | None = None
        self.fifo = np.zeros((1, 0, EMB_DIM), dtype=np.float32)
        self.fifo_preds = np.zeros((1, 0, NUM_SPEAKERS), dtype=np.float32)

    @staticmethod
    def extract_features(audio: np.ndarray) -> np.ndarray:
        """Compute (1, T, 128) unnormalized bf16-rounded log-mel features on CPU."""
        pre = _apply_preemphasis(audio, PREEMPH)
        win = _round_bf16(np.hanning(WIN_LENGTH).astype(np.float32))
        mel_basis = _round_bf16(_slaney_mel_filterbank(N_FFT, N_MELS, SAMPLE_RATE))
        power = _stft_power(pre, win, drop_last=False)  # (257, T)
        mel = mel_basis @ power
        log_mel = np.log(mel + LOG_ZERO_GUARD).astype(np.float32)
        return np.ascontiguousarray(log_mel.T[None, :, :])

    def _compress_spkcache(self) -> None:
        assert self.spkcache_preds is not None
        n_frames = self.spkcache.shape[1]
        per_spk = self.spkcache_len // NUM_SPEAKERS
        spkcache_len_per_spk = per_spk - SPKCACHE_SIL_FRAMES_PER_SPK
        strong_boost = int(spkcache_len_per_spk * STRONG_BOOST_RATE)
        weak_boost = int(spkcache_len_per_spk * WEAK_BOOST_RATE)
        min_pos = int(spkcache_len_per_spk * MIN_POS_SCORES_RATE)

        preds = self.spkcache_preds[0]
        log_p = np.log(np.maximum(preds, PRED_SCORE_THRESHOLD))
        log_1_p = np.log(np.maximum(1.0 - preds, PRED_SCORE_THRESHOLD))
        log_1_sum = log_1_p.sum(axis=1, keepdims=True)
        scores = log_p - log_1_p + log_1_sum - math.log(0.5)

        pos_count = (scores > 0.0).sum(axis=0)
        for s in range(NUM_SPEAKERS):
            mask = (preds[:, s] <= 0.5) | ((scores[:, s] <= 0.0) & (pos_count[s] >= min_pos))
            scores[mask, s] = -np.inf

        if SCORES_BOOST_LATEST > 0.0 and self.spkcache_len < n_frames:
            scores[self.spkcache_len : n_frames, :] += SCORES_BOOST_LATEST

        for k, scale in ((strong_boost, 2.0), (weak_boost, 1.0)):
            for s in range(NUM_SPEAKERS):
                order = np.argsort(-scores[:, s])[: min(k, n_frames)]
                valid = np.isfinite(scores[order, s])
                scores[order[valid], s] -= scale * math.log(0.5)

        if SPKCACHE_SIL_FRAMES_PER_SPK > 0:
            sil_pad = np.full((SPKCACHE_SIL_FRAMES_PER_SPK, NUM_SPEAKERS), np.inf, dtype=np.float32)
            scores = np.concatenate([scores, sil_pad], axis=0)

        t_tot = scores.shape[0]
        flat = scores.T.reshape(-1)
        order = np.argsort(-flat, kind="stable")[: self.spkcache_len]
        topk = np.where(np.isneginf(flat[order]), MAX_INDEX, order)
        topk.sort()

        new_embs = np.zeros((1, self.spkcache_len, EMB_DIM), dtype=np.float32)
        new_preds = np.zeros((1, self.spkcache_len, NUM_SPEAKERS), dtype=np.float32)
        for i, flat_idx in enumerate(topk):
            if flat_idx == MAX_INDEX:
                new_embs[0, i] = self.sil_emb
            else:
                f_idx = int(flat_idx % t_tot)
                if f_idx >= n_frames:
                    new_embs[0, i] = self.sil_emb
                else:
                    new_embs[0, i] = self.spkcache[0, f_idx]
                    new_preds[0, i] = self.spkcache_preds[0, f_idx]
        self.spkcache = new_embs
        self.spkcache_preds = new_preds

    def predict_features(self, features: np.ndarray, *, on_chunk=None) -> np.ndarray:
        """Run streaming Sortformer over precomputed (1, T, 128) features."""
        self.spkcache = np.zeros((1, 0, EMB_DIM), dtype=np.float32)
        self.spkcache_preds = None
        self.fifo = np.zeros((1, 0, EMB_DIM), dtype=np.float32)
        self.fifo_preds = np.zeros((1, 0, NUM_SPEAKERS), dtype=np.float32)

        total_frames = features.shape[1]
        chunk_stride = self.chunk_len * SUBSAMPLING
        feed_size = (self.chunk_len + self.right_context) * SUBSAMPLING
        num_chunks = math.ceil(total_frames / chunk_stride)

        all_preds = []
        for ci in range(num_chunks):
            start = ci * chunk_stride
            end = min(start + feed_size, total_frames)
            cur_len = end - start
            chunk = features[:, start:end, :]
            target = math.ceil(cur_len / SUBSAMPLING) * SUBSAMPLING
            if cur_len < target:
                pad = np.zeros((1, target - cur_len, N_MELS), dtype=np.float32)
                chunk = np.concatenate([chunk, pad], axis=1)

            sc_len = self.spkcache.shape[1]
            fi_len = self.fifo.shape[1]
            preds_diar, preds_hires, new_embs, _ = self.sess.run(
                None,
                {
                    "chunk": np.ascontiguousarray(chunk, dtype=np.float32),
                    "chunk_lengths": np.array([cur_len], dtype=np.int64),
                    "spkcache": np.ascontiguousarray(self.spkcache, dtype=np.float32),
                    "spkcache_lengths": np.array([sc_len], dtype=np.int64),
                    "fifo": np.ascontiguousarray(self.fifo, dtype=np.float32),
                    "fifo_lengths": np.array([fi_len], dtype=np.int64),
                },
            )
            valid_frames = math.ceil(cur_len / SUBSAMPLING)
            keep = min(self.chunk_len, valid_frames)

            fifo_p = (
                preds_diar[0, sc_len : sc_len + fi_len, :]
                if fi_len > 0
                else np.zeros((0, NUM_SPEAKERS), dtype=np.float32)
            )
            chunk_p = preds_diar[0, sc_len + fi_len : sc_len + fi_len + keep, :]
            chunk_e = new_embs[0, :keep, :]

            hires_start = (sc_len + fi_len) * UPSAMPLE_FACTOR
            chunk_hires = preds_hires[0, hires_start : hires_start + keep * UPSAMPLE_FACTOR, :]
            all_preds.append(chunk_hires)

            self.fifo = np.concatenate([self.fifo, chunk_e[None, :, :]], axis=1)
            self.fifo_preds = np.concatenate([fifo_p, chunk_p], axis=0)[None, :, :]

            if self.fifo.shape[1] > self.fifo_len:
                pop_len = max(self.spkcache_update_period, max(0, keep + fi_len - self.fifo_len))
                pop_len = min(pop_len, self.fifo.shape[1])
                pop_e = self.fifo[:, :pop_len, :]
                pop_p = self.fifo_preds[:, :pop_len, :]
                self.fifo = self.fifo[:, pop_len:, :]
                self.fifo_preds = self.fifo_preds[:, pop_len:, :]
                self.spkcache = np.concatenate([self.spkcache, pop_e], axis=1)
                if self.spkcache_preds is not None:
                    self.spkcache_preds = np.concatenate([self.spkcache_preds, pop_p], axis=1)
                if self.spkcache.shape[1] > self.spkcache_len:
                    if self.spkcache_preds is None:
                        init_p = preds_diar[:, :sc_len, :]
                        self.spkcache_preds = np.concatenate([init_p, pop_p], axis=1)
                    self._compress_spkcache()

            if on_chunk is not None:
                on_chunk(ci + 1, num_chunks, min(total_frames, (ci + 1) * chunk_stride) * 0.01)

        if not all_preds:
            return np.zeros((0, NUM_SPEAKERS), dtype=np.float32)
        return np.concatenate(all_preds, axis=0)[:total_frames]


def binarize_predictions(
    preds: np.ndarray,
    n_samples: int,
    *,
    onset: float = DEFAULT_ONSET,
    offset: float = DEFAULT_OFFSET,
    min_on: float = DEFAULT_MIN_ON,
    min_off: float = DEFAULT_MIN_OFF,
) -> list[dict]:
    """Convert 10 ms per-speaker probabilities (T, 8) into overlapping speaker turns."""
    frame_sec = FRAME_DURATION / UPSAMPLE_FACTOR  # 0.01 s
    max_t = round(n_samples / SAMPLE_RATE, 3)
    segments: list[dict] = []
    for spk in range(preds.shape[1]):
        p = preds[:, spk]
        if len(p) == 0 or float(p.max()) <= onset:
            continue
        hi = p > onset
        lo = p < offset
        chg = np.flatnonzero(hi | lo)
        if len(chg) == 0:
            continue
        vals = hi[chg]
        runs: list[tuple[int, int]] = []
        in_seg = False
        s0 = 0
        for idx, is_hi in zip(chg.tolist(), vals.tolist()):
            if is_hi and not in_seg:
                s0 = idx
                in_seg = True
            elif not is_hi and in_seg:
                runs.append((s0, idx))
                in_seg = False
        if in_seg:
            runs.append((s0, len(p)))

        spk_segs: list[dict] = []
        for a, b in runs:
            st = round(a * frame_sec, 3)
            en = min(round(b * frame_sec, 3), max_t)
            if en - st >= min_on and en > st:
                spk_segs.append({"start": st, "end": en, "speaker": spk})
        if min_off > 0 and len(spk_segs) > 1:
            merged = [spk_segs[0]]
            for seg in spk_segs[1:]:
                if seg["start"] - merged[-1]["end"] < min_off:
                    merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
                else:
                    merged.append(seg)
            spk_segs = merged
        segments.extend(spk_segs)
    segments.sort(key=lambda seg: (seg["start"], seg["speaker"]))
    return segments


class MultitalkerParakeetUltra:
    """Target-speaker TDT ASR: transcribes each active speaker separately per ~20 s chunk."""

    def __init__(self, model_dir: Path, *, use_cuda: bool):
        self.enc = _ort_session(model_dir / "encoder-model.onnx", use_cuda=use_cuda)
        # Joint decoder runs one frame/token at a time: CPU avoids per-token GPU sync overhead.
        self.dec = _ort_session(model_dir / "decoder_joint-model.onnx", use_cuda=False)
        self.vocab: list[str] = []
        for line in (model_dir / "vocab.txt").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            token, _ = line.rsplit(" ", 1)
            self.vocab.append(token)
        self.vocab_size = len(self.vocab)
        self.blank_id = self.vocab_size - 1
        self.mel_basis = _slaney_mel_filterbank(N_FFT, N_MELS, SAMPLE_RATE)
        self.window = np.hanning(WIN_LENGTH).astype(np.float32)

    def extract_features(self, audio: np.ndarray) -> np.ndarray:
        pre = _apply_preemphasis(audio, PREEMPH)
        power = _stft_power(pre, self.window, drop_last=True)
        mel = self.mel_basis @ power
        log_mel = np.log(mel + LOG_ZERO_GUARD).astype(np.float32)
        if log_mel.shape[1] > 1:
            mean = log_mel.mean(axis=1, keepdims=True)
            std = log_mel.std(axis=1, ddof=1, keepdims=True) + 1e-5
            log_mel = (log_mel - mean) / std
        return np.ascontiguousarray(log_mel[None, :, :], dtype=np.float32)

    def transcribe_chunk(
        self, audio: np.ndarray, spk_mask: np.ndarray | None, bg_mask: np.ndarray | None
    ) -> list[dict]:
        feats = self.extract_features(audio)
        t_len = feats.shape[2]
        if t_len == 0:
            return []
        if spk_mask is None or bg_mask is None:
            spk_t = np.array([[1.0]], dtype=np.float32)
            bg_t = np.array([[0.0]], dtype=np.float32)
            gate = np.array([0.0], dtype=np.float32)
        else:
            spk_t = spk_mask[None, :].astype(np.float32)
            bg_t = bg_mask[None, :].astype(np.float32)
            gate = np.array([1.0], dtype=np.float32)

        enc_out, _ = self.enc.run(
            None,
            {
                "audio_signal": feats,
                "length": np.array([t_len], dtype=np.int64),
                "spk_targets": spk_t,
                "bg_spk_targets": bg_t,
                "speaker_gate": gate,
            },
        )
        time_steps = enc_out.shape[2]
        state_h = np.zeros((2, 1, 640), dtype=np.float32)
        state_c = np.zeros((2, 1, 640), dtype=np.float32)
        target_len = np.array([1], dtype=np.int32)

        tokens: list[int] = []
        frames: list[int] = []
        durs: list[int] = []
        t = 0
        emitted = 0
        last_tok = self.blank_id
        while t < time_steps:
            frame = np.ascontiguousarray(enc_out[:, :, t : t + 1])
            outs = self.dec.run(
                None,
                {
                    "encoder_outputs": frame,
                    "targets": np.array([[last_tok]], dtype=np.int32),
                    "target_length": target_len,
                    "input_states_1": state_h,
                    "input_states_2": state_c,
                },
            )
            logits = outs[0].reshape(-1)
            tok_id = int(np.argmax(logits[: self.vocab_size]))
            dur_step = int(np.argmax(logits[self.vocab_size :])) if len(logits) > self.vocab_size else 0
            if tok_id != self.blank_id:
                tokens.append(tok_id)
                frames.append(t)
                durs.append(dur_step)
                last_tok = tok_id
                state_h, state_c = outs[2], outs[3]
                emitted += 1
            if dur_step > 0:
                t += dur_step
                emitted = 0
            elif tok_id == self.blank_id or emitted >= 10:
                t += 1
                emitted = 0

        words: list[dict] = []
        for tid, fr, dr in zip(tokens, frames, durs):
            raw = self.vocab[tid]
            if raw.startswith("<") and raw.endswith(">") and raw != "<unk>":
                continue
            st = fr * FRAME_DURATION
            en = (fr + max(1, dr)) * FRAME_DURATION
            is_new = raw.startswith("\u2581") or not words
            piece = raw.replace("\u2581", "")
            if not piece:
                continue
            if is_new:
                words.append({"start": st, "end": en, "word": piece, "probability": 1.0})
            else:
                words[-1]["word"] += piece
                words[-1]["end"] = en
        return words

    def transcribe_by_speaker(
        self,
        audio: np.ndarray,
        diar_segments: list[dict],
        *,
        chunk_seconds: float = 20.0,
        min_speech_seconds: float = 0.4,
        on_chunk=None,
    ) -> dict[int, list[dict]]:
        """Return {speaker_id: [timed word dicts]} across the whole recording."""
        speakers = sorted({seg["speaker"] for seg in diar_segments})
        if not speakers:
            return {}
        frame_samples = int(FRAME_DURATION * SAMPLE_RATE)  # 1280 (80 ms)
        chunk_samples = int(chunk_seconds * SAMPLE_RATE)
        min_frames = min_speech_seconds / FRAME_DURATION
        cut_reach = 3 * SAMPLE_RATE
        cut_step = 640

        seg_samples = [
            (int(seg["start"] * SAMPLE_RATE), int(seg["end"] * SAMPLE_RATE), seg["speaker"])
            for seg in diar_segments
        ]
        # Merge all speaker intervals once so silence-edge search is O(log N) per candidate.
        merged_busy: list[tuple[int, int]] = []
        for a, b, _ in sorted(seg_samples):
            if merged_busy and a <= merged_busy[-1][1]:
                merged_busy[-1] = (merged_busy[-1][0], max(merged_busy[-1][1], b))
            else:
                merged_busy.append((a, b))
        busy_starts = [a for a, _ in merged_busy]

        def busy(pos: int) -> bool:
            idx = bisect.bisect_left(busy_starts, pos) - 1
            return idx >= 0 and merged_busy[idx][0] < pos < merged_busy[idx][1]

        edges = [0]
        n = len(audio)
        while n - edges[-1] > chunk_samples:
            last = edges[-1]
            target = last + chunk_samples
            lo = max(last + cut_step, target - cut_reach)
            hi = min(n - SAMPLE_RATE, target + cut_reach)
            cands = [p for p in range(lo, hi, cut_step) if not busy(p)]
            edges.append(min(cands, key=lambda p: abs(p - target)) if cands else target)
        edges.append(n)

        words_by_speaker: dict[int, list[dict]] = {spk: [] for spk in speakers}
        total_chunks = max(1, len(edges) - 1)
        for ci, (start, end) in enumerate(zip(edges, edges[1:]), start=1):
            if end - start < SAMPLE_RATE // 2:
                continue
            n_frames = math.ceil((end - start) / frame_samples)
            masks = []
            for spk in speakers:
                mask = np.zeros(n_frames, dtype=np.float32)
                for a, b, s_id in seg_samples:
                    if s_id != spk or b <= start or a >= end:
                        continue
                    f0 = max(0, (a - start) // frame_samples)
                    f1 = min(n_frames, math.ceil((b - start) / frame_samples))
                    mask[f0:f1] = 1.0
                masks.append(mask)
            active = [i for i, m in enumerate(masks) if float(m.sum()) >= min_frames]
            single = len(active) == 1
            offset = start / SAMPLE_RATE
            for i in active:
                spk = speakers[i]
                if single:
                    words = self.transcribe_chunk(audio[start:end], None, None)
                else:
                    others = np.zeros(n_frames, dtype=np.float32)
                    for j, m in enumerate(masks):
                        if j != i:
                            others = np.maximum(others, m)
                    words = self.transcribe_chunk(audio[start:end], masks[i], others)
                for w in words:
                    w["start"] = round(w["start"] + offset, 3)
                    w["end"] = round(w["end"] + offset, 3)
                words_by_speaker[spk].extend(words)
            if on_chunk is not None:
                on_chunk(ci, total_chunks, end / SAMPLE_RATE)
        return words_by_speaker


def group_speaker_utterances(
    words_by_speaker: dict[int, list[dict]],
    *,
    max_gap: float = 0.8,
    interjection_gap: float = 0.25,
    sentence_min_seconds: float = 1.6,
    sentence_min_words: int = 7,
    hard_max_seconds: float = 12.0,
) -> list[dict]:
    """Turn per-speaker timed word streams into natural, overlap-preserving transcript segments.

    Splits at pauses, interjections from another speaker, and sentence boundaries once a
    clause is long enough so an overlapping interjection sits beside the exact sentence it
    interrupted rather than at the bottom of a long monologue.
    """
    cleaned: dict[int, list[dict]] = {}
    for spk, words in words_by_speaker.items():
        if not words:
            continue
        ordered = sorted(words, key=lambda w: (w["start"], w["end"]))
        cleaned[spk] = ordered
    if not cleaned:
        return []

    # Label speakers in arrival order: Speaker 1 is whoever speaks first.
    arrival = sorted(cleaned, key=lambda spk: cleaned[spk][0]["start"])
    speaker_names = {spk: f"Speaker {i}" for i, spk in enumerate(arrival, start=1)}
    other_starts = {
        spk: sorted(w["start"] for s2, ws in cleaned.items() if s2 != spk for w in ws)
        for spk in cleaned
    }

    records: list[dict] = []
    for spk in arrival:
        words = cleaned[spk]
        ost = other_starts[spk]
        name = speaker_names[spk]
        cur_words: list[dict] = []

        def flush() -> None:
            nonlocal cur_words
            if not cur_words:
                return
            text = _join_words(cur_words)
            if text:
                records.append(
                    {
                        "start": round(float(cur_words[0]["start"]), 3),
                        "end": round(float(cur_words[-1]["end"]), 3),
                        "text": text,
                        "speaker": name,
                        "words": [
                            {
                                "start": round(float(w["start"]), 3),
                                "end": round(float(w["end"]), 3),
                                "word": w["word"],
                                "probability": round(float(w.get("probability", 1.0)), 4),
                            }
                            for w in cur_words
                        ],
                    }
                )
            cur_words = []

        for w in words:
            if not cur_words:
                cur_words.append(w)
                continue
            prev = cur_words[-1]
            gap = w["start"] - prev["end"]
            dur = prev["end"] - cur_words[0]["start"]
            prev_text = prev["word"].rstrip()
            sentence_end = bool(prev_text) and prev_text[-1] in ".?!"
            clause_end = bool(prev_text) and prev_text[-1] in ",;:"
            other_interjected = False
            if gap > interjection_gap and ost:
                lo_t = prev["end"] - 0.05
                hi_t = w["start"] + 0.05
                pos = bisect.bisect_left(ost, lo_t)
                other_interjected = pos < len(ost) and ost[pos] <= hi_t
            should_split = (
                gap > max_gap
                or other_interjected
                or (sentence_end and (dur >= sentence_min_seconds or len(cur_words) >= sentence_min_words))
                or (dur >= hard_max_seconds and (clause_end or gap > 0.12))
                or dur >= hard_max_seconds + 4.0
            )
            if should_split:
                flush()
            cur_words.append(w)
        flush()

    records.sort(key=lambda r: (r["start"], r["end"], r["speaker"]))
    for index, rec in enumerate(records):
        rec["index"] = index
    return records


def _join_words(words: list[dict]) -> str:
    parts: list[str] = []
    for w in words:
        tok = str(w.get("word") or "").strip()
        if not tok:
            continue
        if parts and tok[0] in ",.;:!?)]":
            parts[-1] += tok
        else:
            parts.append(tok)
    return " ".join(parts).strip()
