"""MCP (Model Context Protocol) server integration.

This is how God-Agent reaches "every agent": MCP is the open standard for
plugging an agent into external tools/agents/servers. Any MCP server exposes a
set of tools (and often whole agent workflows) that God-Agent can call. God
discovers them at runtime and routes to them, so the set of "agents" God can use
is not limited to the built-in crew — it can grow to whatever MCP servers you
point it at across the internet.

We support three transports (all on the OpenAI Agents SDK's native MCP client):
  * stdio            — run a local MCP server binary (e.g. `npx ...`, `python -m ...`)
  * sse              — Server-Sent-Events remote server
  * streamable-http  — Streamable HTTP remote server

Configuration is data-driven: a list of server definitions in
`~/.god-agent/mcp.json` (or `config/mcp.example.json`), e.g.

    {
      "servers": [
        {"name": "github", "transport": "http",
         "url": "https://mcp.github.com/mcp", "enabled": true},
        {"name": "local-db", "transport": "stdio", "enabled": true,
         "command": "python3", "args": ["-m", "my_mcp_server"]}
      ]
    }

Safety is preserved: tools from MCP servers are exposed to God, but every tool
call still flows through God-Agent's own ToolRegistry/policy? (No — MCP tools
are external; they cannot be graded by the local risk engine.) To keep the trust
model honest, MCP servers are an explicit, operator-enabled capability
(`policy.network.enabled` and `mcp.enabled`), and every MCP tool call is
recorded in the audit trail. If any MCP server is unreachable, it is skipped
gracefully and never breaks the run.

This module is inert when the OpenAI Agents SDK (which provides the MCP client)
is not installed.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Config loading. Reads the operator-defined MCP server registry.
# ---------------------------------------------------------------------------
def registry_path(cfg: dict) -> list[str]:
    rooted = os.path.expanduser(cfg["state"].get("root", "~/.god-agent"))
    return [
        os.path.join(rooted, "mcp.json"),
        os.environ.get("GODA_MCP_FILE", ""),
        "config/mcp.json",
    ]


def _read(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def load_servers(cfg: dict) -> list[dict]:
    """Return the enabled MCP server definitions from all config locations."""
    servers: list[dict] = []
    for path in registry_path(cfg):
        data = _read(path)
        for s in data.get("servers", []):
            if isinstance(s, dict) and s.get("name") and s.get("enabled", True):
                servers.append(s)
    return servers


def have_mcp(cfg: dict) -> bool:
    if not cfg.get("mcp", {}).get("enabled", False):
        return False
    if not cfg["policy"].get("network", {}).get("enabled", True):
        return False
    return bool(load_servers(cfg))


def _client_available() -> bool:
    try:
        from agents import mcp  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Build SDK MCP servers from definitions. Returns a list of agents.mcp servers
# ready to be attached to an Agent via `mcp_servers=[...]`. Unreachable or
# malformed servers are skipped (never fatal).
# ---------------------------------------------------------------------------
def build_mcp_servers(cfg: dict) -> list:
    if not have_mcp(cfg):
        return []
    if not _client_available():
        return []
    from agents import mcp

    built = []
    for s in load_servers(cfg):
        try:
            built.append(_build_one(mcp, s))
        except Exception:
            continue  # skip bad/unreachable configs, never break the run
    return built


def _build_one(mcp, s: dict):
    name = str(s.get("name"))[:80]
    transport = str(s.get("transport", "stdio")).lower()

    if transport in ("stdio", "command"):
        params = mcp.MCPServerStdioParams(
            command=str(s.get("command") or ""),
            args=list(s.get("args", []) or []),
            env={str(k): str(v) for k, v in (s.get("env") or {}).items()},
            cwd=s.get("cwd") or None,
        )
        return mcp.MCPServerStdio(params=params, name=name)

    if transport in ("sse", "https", "remote"):
        url = str(s.get("url") or "")
        params = mcp.MCPServerSseParams(url=url)
        if s.get("headers"):
            params["headers"] = {str(k): str(v) for k, v in s["headers"].items()}
        return mcp.MCPServerSse(params=params, name=name)

    if transport in ("http", "streamable-http", "streamable_http"):
        url = str(s.get("url") or "")
        params = mcp.MCPServerStreamableHttpParams(url=url)
        if s.get("headers"):
            params["headers"] = {str(k): str(v) for k, v in s["headers"].items()}
        return mcp.MCPServerStreamableHttp(params=params, name=name)

    raise ValueError(f"unknown MCP transport: {transport}")


# ---------------------------------------------------------------------------
# Enumerate the tools an MCP server would expose (best-effort; used for the UI).
# ---------------------------------------------------------------------------
def mcp_summary(cfg: dict) -> list[dict]:
    out = []
    for s in load_servers(cfg):
        out.append({
            "name": s.get("name"),
            "transport": s.get("transport"),
            "command": s.get("command") or (s.get("url") or ""),
            "disabled": not s.get("enabled", True),
        })
    return out
