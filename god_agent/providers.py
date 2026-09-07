"""Provider manager — custom LLM providers made first-class.

Profiles live in ~/.god-agent/providers.json (or cfg.state.root/providers.json):

  {
    "active": "Kira",
    "profiles": [
      {"name": "Kira", "type": "openai",
       "base_url": "https://kiraai.vn/api/v1", "api_key": "",
       "model": "kira-3.5-flash", "key_env": "KIRA_API_KEY"},
      {"name": "OpenAI", "type": "openai",
       "base_url": "https://api.openai.com/v1", "api_key": "sk-...",
       "model": "gpt-4o-mini"},
      {"name": "Grok", "type": "openai",
       "base_url": "https://api.x.ai/v1", "api_key": "",
       "model": "grok-build-0.1", "key_env": "XAI_API_KEY"},
      {"name": "Ollama", "type": "openai",
       "base_url": "http://localhost:11434/v1", "api_key": "",
       "model": "llama3.1"},
      {"name": "OpenRouter", "type": "openai",
       "base_url": "https://openrouter.ai/api/v1", "api_key": "sk-or-...",
       "model": "anthropic/claude-3.5-sonnet"},
      {"name": "Custom OpenAI-compatible", ...},
      {"name": "Anthropic", "type": "anthropic", ...}
    ]
  }

type: openai (anything OpenAI-compatible: OpenAI, Ollama, vLLM, LM Studio,
LocalAI, OpenRouter, Groq, Together...) | anthropic | mock.

The active profile is applied to the runtime's llm config on startup, so the
agent immediately starts using it — no code changes, no restart needed.
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from typing import Any, Optional

from .config import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, DEFAULT_LLM_KEY_ENV
from .utils import now_iso, redact

VALID_TYPES = {"openai", "anthropic", "mock", "custom"}


def _normalize_base(url: str, ptype: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return url
    if ptype in ("openai", "custom") and not url.endswith("/v1"):
        # Ollama/LM Studio users typically give the bare origin
        url = url + "/v1"
    return url


class ProviderManager:
    def __init__(self, path: str):
        self.path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.active = ""
        self.profiles: list[dict] = []
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        if not os.path.isfile(self.path):
            self.profiles = [default_profile(), openai_profile(), grok_profile()]
            self.active = self.profiles[0]["name"]
            self.save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.profiles = data.get("profiles", [])
            self.active = data.get("active", "")
            if not self.profiles:
                self.profiles = [default_profile(), openai_profile(), grok_profile()]
                self.active = self.profiles[0]["name"]
                self.save()
            if not any(p["name"] == self.active for p in self.profiles):
                self.active = self.profiles[0]["name"]
        except (json.JSONDecodeError, OSError):
            self.profiles = [default_profile(), openai_profile(), grok_profile()]
            self.active = self.profiles[0]["name"]
            self.save()

    def save(self) -> None:
        # Create the temporary file private from the outset, not only after
        # replacement: profiles may contain operator-supplied API keys.
        fd, tmp = tempfile.mkstemp(prefix=".providers-", dir=os.path.dirname(self.path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "updated": now_iso(),
                           "active": self.active, "profiles": self.profiles},
                          fh, indent=2, sort_keys=True, ensure_ascii=False)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ------------------------------------------------------------------
    def list(self) -> list[dict]:
        return [
            {"name": p["name"], "type": p.get("type", "openai"),
             "base_url": p.get("base_url", ""), "model": p.get("model", ""),
             "has_key": bool(p.get("api_key", "")),
             "active": p["name"] == self.active,
             "key_env": p.get("key_env", "")}
            for p in self.profiles
        ]

    def add(self, name: str, *, ptype: str = "openai", base_url: str = "",
            api_key: str = "", model: str = "", key_env: str = "",
            active: bool = False) -> dict:
        name = name.strip()[:80]
        if not name:
            raise ValueError("provider name required")
        if ptype not in VALID_TYPES:
            raise ValueError(f"type must be one of {sorted(VALID_TYPES)}")
        for p in self.profiles:
            if p["name"].lower() == name.lower():
                p.update({"type": ptype, "base_url": _normalize_base(base_url, ptype),
                          "api_key": api_key, "model": model, "key_env": key_env})
                if active:
                    self.active = p["name"]
                self.save()
                return self._get(p["name"])
        profile = {"name": name, "type": ptype,
                   "base_url": _normalize_base(base_url, ptype),
                   "api_key": api_key, "model": model, "key_env": key_env}
        self.profiles.append(profile)
        if active or not self.active:
            self.active = name
        self.save()
        return profile

    def remove(self, name: str) -> bool:
        before = len(self.profiles)
        self.profiles = [p for p in self.profiles if p["name"] != name]
        if self.active == name:
            self.active = self.profiles[0]["name"] if self.profiles else ""
        self.save()
        return len(self.profiles) < before

    def use(self, name: str) -> bool:
        if not any(p["name"] == name for p in self.profiles):
            return False
        self.active = name
        self.save()
        return True

    def get(self, name: str) -> dict:
        for p in self.profiles:
            if p["name"] == name:
                return dict(p)
        raise KeyError(name)

    def active_profile(self) -> dict:
        for p in self.profiles:
            if p["name"] == self.active:
                return dict(p)
        return dict(self.profiles[0]) if self.profiles else default_profile()

    def _get(self, name: str) -> dict:
        return self.get(name)

    # ------------------------------------------------------------------
    def apply_to(self, cfg: dict) -> None:
        """Push the active profile into the runtime llm config."""
        p = self.active_profile()
        ptype = p.get("type", "openai")
        if ptype == "custom":
            ptype = "openai"
        key = p.get("api_key", "") or ""
        cfg["llm"]["provider"] = ptype
        cfg["llm"]["base_url"] = p.get("base_url", "")
        cfg["llm"]["api_key"] = key
        # Resolve environment credentials only at request time. Never copy them
        # into config files, or reuse Kira's key when switching to another host.
        cfg["llm"]["api_key_env"] = p.get("key_env", "")
        if p.get("model"):
            cfg["llm"]["model"] = p["model"]
            cfg["agent"]["model"] = p["model"]

    # ------------------------------------------------------------------
    def test(self, name: Optional[str] = None) -> dict:
        """Connection test. Returns {ok, detail, models?}; /models may be public."""
        profile = self.get(name) if name else self.active_profile()
        return probe_profile(profile)


def _safe_error(text: str, key: str) -> str:
    if key:
        text = text.replace(key, "***")
    return redact(text)[:200]


def probe_profile(profile: dict) -> dict:
    """Probe an unsaved profile without logging or persisting its credentials.

    A public /models endpoint only proves reachability, not key validity.
    """
    ptype = profile.get("type", "openai")
    if ptype == "custom":
        ptype = "openai"
    base = _normalize_base(profile.get("base_url", ""), ptype)
    key = profile.get("api_key", "") or (
        os.environ.get(profile.get("key_env", ""), "") if profile.get("key_env") else "")
    model = profile.get("model", "")

    if ptype == "mock" or (ptype == "openai" and not base):
        return {"ok": True, "detail": "offline mode (no base URL — heuristic planner)", "models": []}

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if ptype == "anthropic":
        headers.pop("Authorization", None)
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"

    # 1) try the OpenAI-compatible /models endpoint
    if ptype == "openai":
        try:
            req = urllib.request.Request(f"{base}/models", headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
            models = [m["id"] for m in data.get("data", [])
                      if isinstance(m, dict) and isinstance(m.get("id"), str)]
            return {"ok": True, "detail": f"connected — {len(data.get('data', []))} models available",
                    "models": models}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return {"ok": False, "detail": f"authentication failed (HTTP {e.code}) — check API key"}
            if e.code == 404:
                pass  # no /models; fall through to chat probe
            else:
                return {"ok": False, "detail": f"HTTP {e.code}: {_safe_error(e.read().decode('utf-8', 'replace'), key)}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": f"cannot reach {base}: {_safe_error(str(e), key)}"}

    # 2) minimal chat probe (1 token)
    try:
        if ptype == "anthropic":
            body = {"model": model, "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}]}
        else:
            body = {"model": model, "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}]}
        req = urllib.request.Request(
            f"{base}/chat/completions" if ptype != "anthropic" else base,
            data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            return {"ok": True, "detail": f"chat completions reachable (model '{model}')",
                    "models": [model] if model else []}
    except urllib.error.HTTPError as e:
        return {"ok": False, "detail": f"chat probe HTTP {e.code}: {_safe_error(e.read().decode('utf-8', 'replace'), key)}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"cannot reach provider: {_safe_error(str(e), key)}"}


def default_profile() -> dict:
    return {"name": "Kira", "type": "openai",
            "base_url": DEFAULT_LLM_BASE_URL, "api_key": "",
            "model": DEFAULT_LLM_MODEL, "key_env": DEFAULT_LLM_KEY_ENV}


def openai_profile() -> dict:
    return {"name": "OpenAI", "type": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "", "model": "gpt-4o-mini", "key_env": "GODA_API_KEY"}


def grok_profile() -> dict:
    """xAI's Grok — OpenAI-compatible endpoint (https://api.x.ai/v1).

    God speaks to xAI like any OpenAI-compatible host, so Grok models
    (grok-4, grok-build-0.1, ...) work as the LLM backend for every engine.
    The key comes from XAI_API_KEY (or the active profile's key/key_env).
    """
    return {"name": "Grok", "type": "openai",
            "base_url": "https://api.x.ai/v1",
            "api_key": "", "model": "grok-build-0.1", "key_env": "XAI_API_KEY"}
