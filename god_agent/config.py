"""Configuration loading, defaults, and environment overrides.

Config is JSON. Search order:
  1. --config PATH (CLI)
  2. $GODA_CONFIG
  3. /etc/god-agent/config.json
  4. ~/.config/god-agent/config.json
  5. ~/.god-agent/config.json (local/desktop install)
  6. ./config/default.json (repo default; kept for development)
Environment variables override individual keys (GODA_*).
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Optional

DEFAULT_LLM_BASE_URL = "https://kiraai.vn/api/v1"
DEFAULT_LLM_MODEL = "kira-3.5-flash"
DEFAULT_LLM_KEY_ENV = "KIRA_API_KEY"

DEFAULT_CONFIG: dict[str, Any] = {
    "agent": {
        "name": "God-Agent",
        "max_steps": 24,
        "model": DEFAULT_LLM_MODEL,
        "temperature": 0.2,
        "max_context_tokens": 60_000,
        "task_timeout_s": 0,  # 0 = no artificial timeout — full native execution
        "reflection": {"enabled": True, "after_every_task": True, "max_chars": 600},
        "swarm": True,  # run a God orchestrator + specialist crew (multi-agent)
        "engine": "openai_sdk",  # openai_sdk | crewai | langgraph | smolagents | autogen | hermes | ui_tars | grok
        "language": "en",
    },
    "llm": {
        "provider": "openai",  # openai | anthropic | mock
        "api_key_env": DEFAULT_LLM_KEY_ENV,
        "api_key": "",
        "base_url": DEFAULT_LLM_BASE_URL,  # OpenAI-compatible Kira endpoint
        "model": DEFAULT_LLM_MODEL,  # overrides agent.model
        "timeout_s": 120,
        "max_retries": 2,
    },
    "execution": {
        # native, unsandboxed by default. Set any of these to a positive number
        # to impose a limit; -1/0 means NO limit (full RAM/CPU/GPU/nproc).
        "memory_limit_mb": -1,
        "cpu_limit_s": -1,
        "max_processes": -1,
        "kill_switch_interval_s": 2,
    },
    "policy": {
        "autonomy": "autonomous",
        # autonomous: the agent acts natively, no approval gate (still audited)
        # supervised: high-risk actions ask the human (CLI/API)
        # sovereign:  everything allowed automatically except the Constitution
        "approval": "auto",  # auto | ask | deny
        "sandbox": "none",   # none (native) | docker | local — no sandbox by default
        "shell": {
            "enabled": True,
            "allow_network": True,
            "max_output_chars": 500_000,
            "danger_keywords": True,
        },
        "files": {
            "enabled": True,
            "protected": [],  # no artificial restrictions on file paths
            "max_read_chars": 2_000_000,
        },
        "network": {"enabled": True, "max_bytes": 8_000_000, "timeout_s": 60},
        "evolution": {"enabled": True, "auto_apply": False, "max_attempts_per_day": 10},
    },
    "memory": {
        "db_path": "~/.god-agent/memory.db",
        "max_episodes": 5000,
        "max_reflections": 10_000,
    },
    "brain": {
        "path": "~/.god-agent/brain.json",
        "max_entries": 5000,
        "encryption": "auto",   # auto (openssl/cryptography) | none
        "inject_credentials": False,  # secret VALUES are never auto-injected
        "inject_prompts": True,       # operator prompts are injected
        "auto_write": True,           # AI may auto-write learnings/important facts
    },
    "self_model": {
        "path": "~/.god-agent/self.json",
        "max_update_chars": 2000,
        "max_versions": 100,
    },
    "audit": {
        "path": "~/.god-agent/audit.jsonl",
        "max_mb": 256,
    },
    "api": {
        "enabled": False,
        "host": "0.0.0.0",
        "port": 8765,
        "token": "",  # generated at install if empty
        "tls": False,
    },
    "mcp": {
        "enabled": False,  # operator must opt in to connect external MCP servers
        "servers": [],     # defined in ~/.god-agent/mcp.json (see config/mcp.example.json)
    },
    # UI-TARS (ByteDance) vision GUI-agent endpoint. Falls back to the same
    # OpenAI-compatible provider as "llm" when these are left empty.
    "ui_tars": {
        "base_url": "",
        "model": "",
        "api_key": "",
    },
    # Real browser automation (Playwright + headless Chromium). Optional: the
    # browser tools return a clear install hint when Playwright is absent.
    # Install with: pip install 'god-agent[browser]' && playwright install chromium
    "browser": {
        "headless": True,          # False shows a visible window (needs a display)
        "timeout_s": 30,           # per-action / navigation timeout
        "max_text_chars": 200_000, # cap on extracted text returned to the model
        "viewport": {"width": 1280, "height": 900},
        "user_agent": "",          # empty = use the stealth profile's UA
        "allow_js": True,          # browser_eval escape hatch; disable to lock it down
        "state_path": "",          # empty = <state.root>/browser_state.json (cookies/session)
        # Anti-bot-detection hardening (see god_agent/tools/stealth.py).
        # Defeats passive fingerprinting: navigator.webdriver, missing
        # window.chrome, HeadlessChrome UA, cdc_ markers, software WebGL,
        # Client-Hints mismatch, and the missing browser-chrome offset.
        # It does NOT solve CAPTCHAs and does not rotate proxies/identities.
        "stealth": {
            "enabled": True,
            "profile": "windows-chrome",  # windows-chrome | macos-chrome | linux-chrome
            "locale": "en-US",
            "timezone": "",               # empty = the profile's default
            "webgl_vendor": "",           # empty = the profile's default
            "webgl_renderer": "",         # empty = the profile's default
            "humanize": {
                "enabled": True,
                "typing_delay_ms": [40, 130],  # random per-keystroke delay
                "mouse_steps": [8, 25],        # intermediate cursor move steps
                "pause_ms": [300, 1200],       # idle between actions
            },
        },
        # A single static proxy (corporate egress, geo-testing, your own exit
        # IP). Deliberately not a rotation pool.
        "proxy": {"server": "", "username": "", "password": "", "bypass": ""},
        # Being a good citizen. Independent of stealth: looking like a human
        # browser is not a licence to hammer someone's server.
        "polite": {
            "enabled": True,
            "robots_txt": True,             # honour Disallow rules
            "user_agent_token": "GodAgent", # which robots.txt group we match
            "min_delay_ms": 1000,           # minimum gap between requests/host
            "max_requests_per_minute": 30,
        },
    },
    "kill_switch": {"path": "~/.god-agent/DISABLED"},
    "state": {"root": "~/.god-agent", "tasks_dir": "~/.god-agent/tasks"},
}


def expand(path: str) -> str:
    return os.path.expanduser(path)


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def _deep_set(d: dict, key: str, value: Any, sep: str = ".") -> None:
    parts = key.split(sep)
    node = d
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def load_config(path: Optional[str] = None) -> dict[str, Any]:
    """Load config; deep-merges found file over defaults, then env over file."""
    cfg = default_config()

    candidates: list[str] = []
    if path:
        candidates.append(path)
    elif os.environ.get("GODA_CONFIG"):
        candidates.append(os.environ["GODA_CONFIG"])
    else:
        candidates += [
            "/etc/god-agent/config.json",
            os.path.expanduser("~/.config/god-agent/config.json"),
            os.path.expanduser("~/.god-agent/config.json"),
            "config/default.json",
        ]

    for cand in candidates:
        if os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as fh:
                    file_cfg = json.load(fh)
                _merge(cfg, file_cfg)
                cfg["_source"] = cand
                break
            except (json.JSONDecodeError, OSError):
                # Sourced dirs like /etc may not exist yet; keep going
                continue

    # Environment overrides
    _env_overrides(cfg)
    _finalize(cfg)
    return cfg


def _merge(base: dict, overlay: dict) -> dict:
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def _env_overrides(cfg: dict) -> None:
    mapping = {
        "GODA_PROVIDER": "llm.provider",
        "GODA_API_KEY": "llm.api_key",
        "GODA_BASE_URL": "llm.base_url",
        "GODA_MODEL": "llm.model",
        "GODA_AUTONOMY": "policy.autonomy",
        "GODA_SANDBOX": "policy.sandbox",
        "GODA_APPROVAL": "policy.approval",
        "GODA_API_TOKEN": "api.token",
        "GODA_API_PORT": "api.port",
        "GODA_TASK_TIMEOUT": "agent.task_timeout_s",
        "GODA_UITARS_BASE_URL": "ui_tars.base_url",
        "GODA_UITARS_MODEL": "ui_tars.model",
        "GODA_UITARS_API_KEY": "ui_tars.api_key",
    }
    for env_name, key in mapping.items():
        if os.environ.get(env_name):
            value: Any = os.environ[env_name]
            if key in ("api.port", "agent.task_timeout_s") and value.isdigit():
                value = int(value)
            _deep_set(cfg, key, value)


def _finalize(cfg: dict) -> None:
    if not cfg["llm"]["model"]:
        cfg["llm"]["model"] = cfg["agent"]["model"]

    # Normalize state paths under a single root
    root = cfg["state"].get("root", "~/.god-agent")
    for section, key in [
        ("memory", "db_path"),
        ("self_model", "path"),
        ("audit", "path"),
        ("kill_switch", "path"),
        ("brain", "path"),
    ]:
        p = cfg[section][key]
        if p.startswith("~/"):
            cfg[section][key] = os.path.join(expand(root), os.path.relpath(p, "~/.god-agent"))
    cfg["state"]["root"] = expand(root)
    cfg["state"]["tasks_dir"] = expand(cfg["state"]["tasks_dir"])

    if cfg["api"]["port"]:
        cfg["api"]["port"] = int(cfg["api"]["port"])
    cfg["agent"]["task_timeout_s"] = int(cfg["agent"]["task_timeout_s"])
    cfg["agent"]["max_steps"] = int(cfg["agent"]["max_steps"])


def save_config(cfg: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, sort_keys=True)


def api_key(cfg: dict) -> str:
    key = cfg["llm"].get("api_key") or ""
    if not key and cfg["llm"].get("api_key_env"):
        key = os.environ.get(cfg["llm"]["api_key_env"], "")
    return key
