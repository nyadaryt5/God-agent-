"""Memory tools: recall past episodes/reflections and record learnings."""
from __future__ import annotations

import json
from typing import Any

from ..runtime import get_runtime


def search_memory(reg, name: str, args: dict) -> str:
    """Semantic-ish search across past tasks and reflections."""
    rt = get_runtime()
    query = str(args.get("query", ""))
    if not query:
        return "ERROR: query required"
    limit = min(int(args.get("limit", 5) or 5), 20)
    hits = rt.memory.search(query, limit=limit)
    if not hits:
        return "(no memories found)"
    return json.dumps(hits, indent=2, ensure_ascii=False)


def remember(reg, name: str, args: dict) -> str:
    """Store an explicit lesson or fact for future recall."""
    rt = get_runtime()
    summary = str(args.get("summary", ""))
    if not summary:
        return "ERROR: summary required"
    task = str(args.get("task", "")) or summary[:200]
    outcome = str(args.get("outcome", "ok"))
    eid = rt.memory.add_episode(task, summary, outcome)
    rt.record("remember", f"stored episode #{eid}: {summary[:120]}", {})
    return f"stored as episode #{eid}"


def reflect(reg, name: str, args: dict) -> str:
    """Store direct feedback from the operator (used to steer behavior)."""
    rt = get_runtime()
    text = str(args.get("reflection", "")).strip()
    if not text:
        return "ERROR: reflection text required"
    rid = rt.memory.add_reflection(text)
    rt.self_model.add_reflection(text)
    rt.record("reflect", f"operator feedback #{rid}: {text[:120]}", {}, actor="operator")
    return f"reflection #{rid} stored"
