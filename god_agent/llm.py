"""LLM provider adapters (stdlib HTTP only).

Supported providers:
  openai    — OpenAI-compatible /v1/chat/completions (Kira (default), OpenAI, OpenRouter, Ollama,
              LM Studio, vLLM, LocalAI ...). Set GODA_BASE_URL for non-OpenAI hosts.
  anthropic — Anthropic Messages API.
  mock      — deterministic stand-in for tests/offline demo.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from .utils import now_iso, redact


class LLMError(RuntimeError):
    pass


class LLMClient:
    provider = "base"

    def chat(self, messages: list[dict], max_tokens: int = 2048) -> str:
        raise NotImplementedError


# ---------------------------------------------------------------- OpenAI ---- #
class OpenAIProvider(LLMClient):
    provider = "openai"

    def __init__(self, api_key: str, model: str, base_url: str = "", timeout: int = 120, temperature: float = 0.2):
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout
        self.temperature = temperature

    def chat(self, messages: list[dict], max_tokens: int = 2048) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        raw = self._open(req)
        data = json.loads(raw)
        return data["choices"][0]["message"]["content"] or ""

    def _open(self, req: urllib.request.Request) -> str:
        last: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (intended)
                    return resp.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                if self.api_key:
                    detail = detail.replace(self.api_key, "***")
                detail = redact(detail)[:500]
                last = LLMError(f"HTTP {e.code}: {detail}")
                if e.code in (400, 401, 403, 404):
                    break
                time.sleep(1.5 * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = LLMError(f"network error: {e}")
                time.sleep(1.5 * (attempt + 1))
        raise last if last else LLMError("unknown LLM error")


# -------------------------------------------------------------- Anthropic --- #
class AnthropicProvider(LLMClient):
    provider = "anthropic"
    _URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str, base_url: str = "", timeout: int = 120, temperature: float = 0.2):
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or self._URL).rstrip("/")
        self.timeout = timeout
        self.temperature = temperature

    def chat(self, messages: list[dict], max_tokens: int = 2048) -> str:
        system = ""
        msgs: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                system = (system + "\n" + m["content"]).strip()
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "messages": msgs,
        }
        if system:
            payload["system"] = system
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        opener = OpenAIProvider(  # reuse same retry loop
            api_key=self.api_key, model=self.model, base_url=self.base_url, timeout=self.timeout,
            temperature=self.temperature,
        )
        raw = opener._open(req)  # noqa: SLF001 (shared retry helper)
        data = json.loads(raw)
        blocks = data.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


# ------------------------------------------------------------------- Mock --- #
class MockLLM(LLMClient):
    """Deterministic scripted model for tests and offline demos.

    `script` is a list of responses; each may be:
      - a plain string
      - a callable(messages) -> str
    The last entry repeats if exhausted.
    """

    provider = "mock"

    def __init__(self, script: list[Any], name: str = "mock"):
        # An empty script signals "no model intelligence": the agent loop then
        # falls back to its built-in deterministic heuristic planner.
        self.heuristic = not bool(script)
        self.script = script or ["I am the mock model."]
        self.name = name
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict], max_tokens: int = 2048) -> str:
        self.calls.append(messages)
        idx = min(len(self.calls) - 1, len(self.script) - 1)
        entry = self.script[idx]
        if callable(entry):
            return str(entry(messages))
        return str(entry)


def get_llm(cfg: dict, mock: Optional[MockLLM] = None) -> LLMClient:
    if mock is not None:
        return mock
    provider = cfg["llm"]["provider"]
    model = cfg["llm"]["model"]
    temp = cfg["agent"]["temperature"]

    if provider == "mock":
        # Offline mode: built-in deterministic planner, no LLM required.
        fallback = MockLLM([], name="offline")
        fallback.note = "offline mode — no LLM API key; using the built-in deterministic planner"
        return fallback

    from .config import api_key

    key = api_key(cfg)
    if not key:
        # No key configured → run fully offline with the deterministic planner
        # instead of failing. Set the active profile key (KIRA_API_KEY by default) to go live.
        fallback = MockLLM([], name="offline-heuristic")
        fallback.note = "no LLM API key configured — running with the built-in offline heuristic planner"
        return fallback

    if provider == "anthropic":
        return AnthropicProvider(
            api_key=key, model=model, base_url=cfg["llm"].get("base_url", ""),
            timeout=cfg["llm"]["timeout_s"], temperature=temp,
        )
    if provider in ("openai", "compatible"):
        return OpenAIProvider(
            api_key=key, model=model, base_url=cfg["llm"].get("base_url", ""),
            timeout=cfg["llm"]["timeout_s"], temperature=temp,
        )
    raise LLMError(f"unsupported LLM provider: {provider}")
