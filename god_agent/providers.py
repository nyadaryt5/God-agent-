"""Provider manager — custom LLM providers made first-class.

Profiles live in ~/.god-agent/providers.json (or cfg.state.root/providers.json):

  {
    "active": "OpenAI",
    "profiles": [
      {"name": "OpenAI", "type": "openai",
       "base_url": "https://api.openai.com/v1", "api_key": "sk-...",
       "model": "gpt-4o-mini"},
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
import re
import urllib.error
import urllib.request
from typing import Any, Optional

from .utils import now_iso

VALID_TYPES = {"openai", "anthropic", "mock", "custom"}


def _normalize_base(url: str, ptype: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return url
    if ptype == "openai" and not url.endswith("/v1"):
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
            self.profiles = [default_profile()]
            self.active = self.profiles[0]["name"]
            self.save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.profiles = data.get("profiles", [])
            self.active = data.get("active", "")
            if not self.profiles:
                self.profiles = [default_profile()]
                self.active = self.profiles[0]["name"]
                self.save()
            if not any(p["name"] == self.active for p in self.profiles):
                self.active = self.profiles[0]["name"]
        except (json.JSONDecodeError, OSError):
            self.profiles = [default_profile()]
            self.active = self.profiles[0]["name"]
            self.save()

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "updated": now_iso(),
                       "active": self.active, "profiles": self.profiles},
                      fh, indent=2, sort_keys=True, ensure_ascii=False)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

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
                    self.active = name
                self.save()
                return self._get(name)
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
        if not key and p.get("key_env"):
            key = os.environ.get(p["key_env"], "")
        cfg["llm"]["provider"] = ptype
        cfg["llm"]["base_url"] = p.get("base_url", "")
        cfg["llm"]["api_key"] = key
        if p.get("model"):
            cfg["llm"]["model"] = p["model"]
            cfg["agent"]["model"] = p["model"]

    # ------------------------------------------------------------------
    def test(self, name: Optional[str] = None) -> dict:
        """Connection test. Returns {ok, detail, models?}."""
        profile = self.get(name) if name else self.active_profile()
        ptype = profile.get("type", "openai")
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
                models = [m.get("id", "") for m in data.get("data", [])][:20]
                return {"ok": True, "detail": f"connected — {len(data.get('data', []))} models available",
                        "models": models}
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    return {"ok": False, "detail": f"authentication failed (HTTP {e.code}) — check API key"}
                if e.code == 404:
                    pass  # no /models; fall through to chat probe
                else:
                    return {"ok": False, "detail": f"HTTP {e.code}: {e.read().decode()[:200]}"}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "detail": f"cannot reach {base}: {e}"}

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
            return {"ok": False, "detail": f"chat probe HTTP {e.code}: {e.read().decode()[:200]}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": f"cannot reach provider: {e}"}


def default_profile() -> dict:
    return {"name": "OpenAI", "type": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "", "model": "gpt-4o-mini", "key_env": "GODA_API_KEY"}
