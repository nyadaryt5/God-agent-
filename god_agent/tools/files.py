"""File tools: read, list, search, write (bounded, policy-checked)."""
from __future__ import annotations

import fnmatch
import os
from typing import Any

from ..runtime import get_runtime


def _safe_join(base: str, rel: str) -> str:
    path = os.path.abspath(os.path.join(base, rel))
    if not (path == os.path.abspath(base) or path.startswith(os.path.abspath(base) + os.sep)):
        raise ValueError("path escapes the allowed root")
    return path


def read_file(reg, name: str, args: dict) -> str:
    """Read a file (bounded). Returns content or error."""
    rt = get_runtime()
    path = os.path.expanduser(str(args.get("path", "")))
    limit = int(rt.cfg["policy"]["files"].get("max_read_chars", 200_000))
    if not path:
        return "ERROR: path required"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read(limit + 1)
    except OSError as e:
        return f"ERROR: {e}"
    if len(data) > limit:
        data = data[:limit] + f"\n...[truncated at {limit} chars]"
    return data


def list_dir(reg, name: str, args: dict) -> str:
    """List a directory. Optional pattern filter."""
    rt = get_runtime()
    path = os.path.expanduser(str(args.get("path", ".")))
    pattern = args.get("pattern", "*")
    try:
        entries = sorted(os.listdir(path))
    except OSError as e:
        return f"ERROR: {e}"
    lines = []
    for e in entries:
        if not fnmatch.fnmatch(e, pattern):
            continue
        full = os.path.join(path, e)
        kind = "d" if os.path.isdir(full) else ("l" if os.path.islink(full) else "f")
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        lines.append(f"{kind} {size:>10} {e}")
    return "\n".join(lines[:500]) or "(empty)"


def file_search(reg, name: str, args: dict) -> str:
    """Recursively search a directory for files matching a pattern."""
    root = os.path.expanduser(str(args.get("path", ".")))
    pattern = args.get("pattern", "*")
    max_depth = int(args.get("max_depth", 3) or 3)
    limit = int(args.get("limit", 100) or 100)
    hits: list[str] = []

    def walk(base: str, depth: int) -> None:
        if len(hits) >= limit:
            return
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            return
        for e in entries:
            if len(hits) >= limit:
                return
            if e.startswith(".git") or e in ("node_modules", ".venv", "venv", "__pycache__", "dist", "build"):
                continue
            full = os.path.join(base, e)
            if os.path.isdir(full) and not os.path.islink(full):
                if depth < max_depth:
                    walk(full, depth + 1)
            elif fnmatch.fnmatch(e, pattern):
                hits.append(full)

    walk(root, 0)
    return "\n".join(hits) or "(no matches)"


def write_file(reg, name: str, args: dict) -> str:
    """Write text to a file. Paths under policy protection are denied earlier."""
    rt = get_runtime()
    path = os.path.expanduser(str(args.get("path", "")))
    content = str(args.get("content", ""))
    mode = str(args.get("mode", "w"))
    if not path:
        return "ERROR: path required"
    if mode not in ("w", "a"):
        return "ERROR: mode must be 'w' or 'a'"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, mode, encoding="utf-8") as fh:
            fh.write(content)
    except OSError as e:
        return f"ERROR: {e}"
    rt.record("write_file", f"wrote {path} ({len(content)} chars)", {"path": path, "mode": mode})
    return f"ok: wrote {len(content)} chars to {path}"
