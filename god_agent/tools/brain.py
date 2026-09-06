"""Brain tools: read / search / list, and the AI auto-write path.

The Brain's two-writer rule is enforced in brain.py:
  * operator commands are absolute (never blocked);
  * the agent can only auto-write entries it owns — it can never touch an
    operator-written or locked entry.
"""
from __future__ import annotations

import json
from typing import Any

from ..brain import KIND_CREDENTIAL, SOURCE_AI
from ..runtime import get_runtime


def brain_read(reg, name: str, args: dict) -> str:
    """Read a Brain entry (credentials are decrypted on read for use in tasks)."""
    rt = get_runtime()
    key = str(args.get("key", ""))
    if not key:
        return "ERROR: key required"
    entry = rt.brain.get(key)
    if entry is None:
        return f"ERROR: no brain entry '{key}'"
    if entry.get("kind") == KIND_CREDENTIAL:
        value = entry.get("value", "")
        rt.audit.append("goda", "brain_read", f"read credential '{key}'",
                        {"key": key}, outcome="secret-read")
        return json.dumps({"key": key, "kind": entry["kind"], "value": value,
                           "source": entry.get("source")}, ensure_ascii=False)
    return json.dumps({"key": key, "kind": entry["kind"], "value": entry.get("value", ""),
                       "source": entry.get("source")}, ensure_ascii=False)


def brain_search(reg, name: str, args: dict) -> str:
    """Search the Brain by text (returns metadata + non-secret values)."""
    rt = get_runtime()
    query = str(args.get("query", ""))
    if not query:
        return "ERROR: query required"
    hits = rt.brain.search(query)
    return json.dumps(hits, indent=2, ensure_ascii=False) or "(no matches)"


def brain_list(reg, name: str, args: dict) -> str:
    """List Brain entries (metadata only — values are NOT shown)."""
    rt = get_runtime()
    kind = args.get("kind")
    return json.dumps(rt.brain.list(kind=kind), indent=2, ensure_ascii=False)


def brain_write(reg, name: str, args: dict) -> str:
    """AI auto-write path: store something you deemed important or learned.

    Enforcement: cannot modify/delete operator-written or locked entries
    (operator's command is absolute)."""
    rt = get_runtime()
    key = str(args.get("key", ""))
    value = str(args.get("value", ""))
    kind = str(args.get("kind", "learning"))
    importance = str(args.get("importance", ""))
    if not key or not value:
        return "ERROR: key and value required"
    ok, msg = rt.brain.ai_write(key, value, kind=kind, importance=importance,
                                note=f"(dispatch {name})")
    rt.audit.append("goda", "brain_write", f"{'stored' if ok else 'rejected'} '{key}' "
                     f"({kind}) — {msg[:100]}", {"key": key, "kind": kind},
                    outcome="ok" if ok else "error")
    return ("ok: " if ok else "rejected: ") + msg
