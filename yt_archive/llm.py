"""Plain-HTTPS structured-JSON LLM client for English translation (no SDK dependencies).

Supports Anthropic Messages API, OpenAI Responses API, Google Gemini GenerateContent API,
and local Ollama (holding the GPU alone via gpu_gate). No silent provider fallbacks."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import gpu_gate

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1/responses"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

MAX_OUTPUT_TOKENS = 16000
REQUEST_TIMEOUT = 300
UNLOAD_TIMEOUT = 60
_KEY_SCRUB = re.compile(r"\b(?:sk-[A-Za-z0-9_.*…-]+|AIza[A-Za-z0-9_-]+)")


class LLMError(RuntimeError):
    """Raised when the configured translation backend is unavailable or returns invalid JSON."""


@dataclass(frozen=True)
class LLMAnswer:
    data: dict
    input_tokens: int
    output_tokens: int


def _scrub(text: str) -> str:
    return _KEY_SCRUB.sub("<redacted>", str(text or ""))[:300]


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout: int = REQUEST_TIMEOUT) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body).get("error", body)
            if isinstance(err, dict):
                err = err.get("message") or err.get("type") or err
        except ValueError:
            err = body
        raise LLMError(f"HTTP {exc.code}: {_scrub(err)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"request failed: {_scrub(exc)}") from exc
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise LLMError(f"provider returned non-JSON response: {exc}") from exc


def _parse_json_object(content: str, label: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise LLMError(f"{label} returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError(f"{label} returned JSON that is not an object")
    return parsed


def _strip_additional_properties(schema: dict) -> dict:
    """Gemini responseSchema does not accept JSON Schema's additionalProperties keyword."""
    out = {}
    for k, v in schema.items():
        if k == "additionalProperties":
            continue
        if isinstance(v, dict):
            out[k] = _strip_additional_properties(v)
        elif isinstance(v, list):
            out[k] = [_strip_additional_properties(x) if isinstance(x, dict) else x for x in v]
        else:
            out[k] = v
    return out


def check_settings(cfg: dict) -> None:
    """Validate translation backend config locally without network I/O."""
    provider = cfg.get("provider") or ""
    model = cfg.get("model") or ""
    if not provider or not model:
        raise LLMError("settings.json: english.provider and english.model are required")
    if provider == "ollama":
        base = (cfg.get("ollama_url") or "").strip()
        if not base.startswith(("http://", "https://")):
            raise LLMError("settings.json: english.ollama_url must be an http(s) URL")
        return
    if not (cfg.get("api_key") or "").strip():
        raise LLMError(
            f"settings.json: english.api_key is empty for provider {provider!r}"
        )


def check_configured(cfg: dict) -> None:
    """Fail fast before running Whisper/diarization if the translation backend is not ready."""
    check_settings(cfg)
    provider = cfg.get("provider") or ""
    model = cfg.get("model") or ""
    if provider == "ollama":
        base = (cfg.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")
        try:
            with urllib.request.urlopen(f"{base}/api/tags", timeout=5) as resp:
                models = json.loads(resp.read().decode("utf-8")).get("models") or []
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise LLMError(f"Ollama is not reachable at {base}: {exc}") from exc
        names = {m.get("name") for m in models}
        if model not in names and f"{model}:latest" not in names:
            raise LLMError(f"Ollama model {model!r} is not pulled at {base}")


def _ask_anthropic(cfg: dict, instructions: str, prompt: str, schema: dict) -> LLMAnswer:
    key = cfg["api_key"]
    model = cfg["model"]
    effort = cfg.get("effort") or "low"
    body = _post_json(
        ANTHROPIC_URL,
        {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": instructions,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        },
        {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
    )
    stop = body.get("stop_reason")
    if stop not in ("end_turn", "stop_sequence"):
        raise LLMError(f"{model} stopped unexpectedly: {stop}")
    used = body.get("usage") or {}
    tok_in = sum(
        int(used.get(k) or 0)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    tok_out = int(used.get("output_tokens") or 0)
    txt = "".join(
        b.get("text", "") for b in (body.get("content") or []) if b.get("type") == "text"
    )
    return LLMAnswer(_parse_json_object(txt, model), tok_in, tok_out)


def _ask_openai(cfg: dict, instructions: str, prompt: str, schema: dict) -> LLMAnswer:
    key = cfg["api_key"]
    model = cfg["model"]
    effort = cfg.get("effort") or "low"
    payload = {
        "model": model,
        "instructions": instructions,
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "translation",
                "schema": schema,
                "strict": True,
            }
        },
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "store": False,
    }
    if effort:
        payload["reasoning"] = {"effort": effort}
    body = _post_json(OPENAI_URL, payload, {"Authorization": f"Bearer {key}"})
    if body.get("status") != "completed":
        reason = (body.get("incomplete_details") or {}).get("reason")
        raise LLMError(f"{model} stopped: {body.get('status')} ({reason})")
    parts = [
        c
        for item in (body.get("output") or [])
        if item.get("type") == "message"
        for c in (item.get("content") or [])
    ]
    refusal = next((c.get("refusal") for c in parts if c.get("type") == "refusal"), "")
    if refusal:
        raise LLMError(f"{model} refused: {_scrub(refusal)}")
    txt = "".join(c.get("text", "") for c in parts if c.get("type") == "output_text")
    used = body.get("usage") or {}
    return LLMAnswer(
        _parse_json_object(txt, model),
        int(used.get("input_tokens") or 0),
        int(used.get("output_tokens") or 0),
    )


def _ask_gemini(cfg: dict, instructions: str, prompt: str, schema: dict) -> LLMAnswer:
    key = cfg["api_key"]
    model = cfg["model"]
    url = GEMINI_URL.format(model=model)
    body = _post_json(
        url,
        {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
                "responseSchema": _strip_additional_properties(schema),
            },
        },
        {"x-goog-api-key": key},
    )
    candidates = body.get("candidates") or []
    if not candidates:
        raise LLMError(f"{model} returned no candidates")
    cand = candidates[0]
    reason = cand.get("finishReason")
    if reason and reason not in ("STOP", "MAX_TOKENS"):
        raise LLMError(f"{model} stopped: {reason}")
    if reason == "MAX_TOKENS":
        raise LLMError(f"{model} hit MAX_TOKENS output limit")
    parts = (cand.get("content") or {}).get("parts") or []
    txt = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    meta = body.get("usageMetadata") or {}
    return LLMAnswer(
        _parse_json_object(txt, model),
        int(meta.get("promptTokenCount") or 0),
        int(meta.get("candidatesTokenCount") or 0),
    )


def unload_ollama(ollama_url: str, model: str, gate=None) -> None:
    base = ollama_url.rstrip("/")
    hold = f"{model} in Ollama"
    try:
        _post_json(f"{base}/api/generate", {"model": model, "keep_alive": 0}, {}, timeout=30)
        deadline = time.monotonic() + UNLOAD_TIMEOUT
        while time.monotonic() < deadline:
            with urllib.request.urlopen(f"{base}/api/ps", timeout=5) as resp:
                loaded = json.loads(resp.read().decode("utf-8")).get("models") or []
            if not any(m.get("name") == model or m.get("model") == model for m in loaded):
                break
            time.sleep(0.5)
    except Exception:
        pass
    finally:
        if gate is not None:
            gate.let_go(hold)


def _ask_ollama(
    cfg: dict, instructions: str, prompt: str, schema: dict, *, gpu: dict | None = None
) -> LLMAnswer:
    base = (cfg.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")
    model = cfg["model"]
    gate = gpu_gate.load()
    hold = f"{model} in Ollama"
    if gate is not None:
        who = (gpu or gpu_gate.ENGLISH_BY_HAND)["who"]
        lane = (gpu or gpu_gate.ENGLISH_BY_HAND)["lane"]
        gate.hold(hold, gpu_gate.OLLAMA_LEAST_GB, alone=True, via="ollama", lane=lane, who=who, minutes=10, wait=None)
    body = _post_json(
        f"{base}/api/chat",
        {
            "model": model,
            "stream": False,
            "think": False,
            "format": schema,
            "keep_alive": "5m",
            "options": {"temperature": 0.2, "num_ctx": 16384},
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
        },
        {},
        timeout=REQUEST_TIMEOUT,
    )
    if body.get("done_reason") == "length":
        raise LLMError(f"{model} hit context or output length limit")
    txt = (body.get("message") or {}).get("content", "")
    return LLMAnswer(
        _parse_json_object(txt, model),
        int(body.get("prompt_eval_count") or 0),
        int(body.get("eval_count") or 0),
    )


def ask_json(
    cfg: dict,
    instructions: str,
    prompt: str,
    schema: dict,
    *,
    gpu: dict | None = None,
) -> LLMAnswer:
    """Send instructions + prompt to the configured provider and return parsed JSON."""
    provider = cfg["provider"]
    if provider == "anthropic":
        return _ask_anthropic(cfg, instructions, prompt, schema)
    if provider == "openai":
        return _ask_openai(cfg, instructions, prompt, schema)
    if provider == "gemini":
        return _ask_gemini(cfg, instructions, prompt, schema)
    if provider == "ollama":
        return _ask_ollama(cfg, instructions, prompt, schema, gpu=gpu)
    raise LLMError(f"unsupported translation provider: {provider!r}")
