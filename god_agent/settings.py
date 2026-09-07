"""Settings: whitelisted runtime options, persisted to the user's config file.

Settings can be changed from the chat ("settings"), the CLI (`goda settings`),
or the dashboard panel, and take effect immediately (providers are hot-swapped,
llm client rebuilt, policy re-instantiated).
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

# dotted-path -> (type, choices, label)
SETTINGS: dict[str, tuple[str, list[str] | None, str]] = {
    "policy.autonomy": ("str", ["autonomous", "supervised", "sovereign"], "autonomy mode"),
    "policy.approval": ("str", ["auto", "ask", "deny"], "high-risk approval mode"),
    "policy.sandbox": ("str", ["none", "docker", "local"], "execution sandbox (none = native)"),
    "policy.network.enabled": ("bool", None, "allow network access"),
    "policy.evolution.enabled": ("bool", None, "enable self-evolution"),
    "policy.evolution.auto_apply": ("bool", None, "auto-apply validated evolution"),
    "policy.shell.allow_network": ("bool", None, "shell commands may use network"),
    "agent.model": ("str", None, "default model"),
    "agent.max_steps": ("int", None, "max steps per task"),
    "agent.swarm": ("bool", None, "run God orchestrator + specialist crew (multi-agent)"),
    "agent.engine": ("str", ["openai_sdk", "crewai", "langgraph", "smolagents", "autogen"],
                     "agent engine (only installed ones are used)"),
    "brain.auto_write": ("bool", None, "AI auto-writes important learnings"),
    "brain.inject_prompts": ("bool", None, "inject operator prompts into context"),
    "brain.inject_credentials": ("bool", None, "inject credential values into context"),
    "execution.memory_limit_mb": ("int", None, "RAM cap MB (-1 = unlimited)"),
    "execution.cpu_limit_s": ("int", None, "CPU seconds cap (-1 = unlimited)"),
    "execution.max_processes": ("int", None, "child process cap (-1 = unlimited)"),
    "api.port": ("int", None, "API port"),
    "mcp.enabled": ("bool", None, "connect external MCP agent/tool servers"),
}


def get_setting(cfg: dict, key: str) -> Any:
    node = cfg
    for part in key.split("."):
        node = node[part]
    return node


def set_setting(cfg: dict, key: str, value: Any) -> tuple[bool, str]:
    info = SETTINGS.get(key)
    if info is None:
        return False, f"unknown setting '{key}' (see goda settings list)"
    kind, choices, label = info

    if kind == "bool":
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        value = bool(value)
    elif kind == "int":
        try:
            value = int(value)
        except (TypeError, ValueError):
            return False, f"'{key}' must be an integer"
    elif kind == "str":
        value = str(value)
        if choices and value not in choices:
            return False, f"'{key}' must be one of {choices}"

    node = cfg
    parts = key.split(".")
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            return False, f"missing config section '{'.'.join(parts[:-1])}'"
        node = node[part]
    node[parts[-1]] = value
    return True, f"{label} set to {value}"


def settings_table(cfg: dict) -> list[dict]:
    return [{"key": k, "label": lbl, "value": get_setting(cfg, k), "choices": choices}
            for k, (kind, choices, lbl) in SETTINGS.items()]


def persisted_path(cfg: dict) -> str:
    """Where settings are saved: the config file that was loaded, or the
    user's own ~/.god-agent/config.json."""
    src = cfg.get("_source", "")
    if src and "default.json" not in src:
        return src
    return os.path.expanduser("~/.god-agent/config.json")


def save(cfg: dict) -> str:
    path = persisted_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Never persist the internal source marker.
    data = {k: v for k, v in cfg.items() if not k.startswith("_")}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True, ensure_ascii=False)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path
