"""Self-model tools: introspection and bounded self-update."""
from __future__ import annotations

import json
from typing import Any

from ..runtime import get_runtime


def read_self(reg, name: str, args: dict) -> str:
    """Read your own identity, capabilities, stats, and boundaries."""
    rt = get_runtime()
    return json.dumps(rt.self_model.current(), indent=2, ensure_ascii=False)


def update_self(reg, name: str, args: dict) -> str:
    """Update your self-reported capabilities/learnings (immutable fields rejected)."""
    rt = get_runtime()
    patch = args.get("patch", {})
    if isinstance(patch, str):
        try:
            patch = json.loads(patch)
        except json.JSONDecodeError:
            return "ERROR: patch must be a JSON object"
    if not isinstance(patch, dict):
        return "ERROR: patch must be a JSON object"
    note = str(args.get("note", ""))[:300]
    ok, msg = rt.self_model.apply_update(patch, note)
    rt.record("update_self", f"self-model update: {msg}", {"patch": patch}, outcome="ok" if ok else "error")
    return ("ok: " if ok else "rejected: ") + msg
