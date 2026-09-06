"""Network tool: bounded HTTP fetch (denied by default via policy)."""
from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Any

from ..runtime import get_runtime


def fetch_url(reg, name: str, args: dict) -> str:
    """Fetch a URL (GET only, size-bounded). Only allowed if network is enabled in policy."""
    rt = get_runtime()
    url = str(args.get("url", ""))
    if not url:
        return "ERROR: url required"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "ERROR: only http/https URLs"
    timeout = min(int(rt.cfg["policy"]["network"].get("timeout_s", 30)), 60)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (policy-checked)
            data = resp.read(rt.cfg["policy"]["network"].get("max_bytes", 1_000_000) + 1)
    except Exception as e:
        return f"ERROR: {e}"
    text = data.decode("utf-8", "replace")
    if len(data) > rt.cfg["policy"]["network"].get("max_bytes", 1_000_000):
        text = text[: rt.cfg["policy"]["network"].get("max_bytes", 1_000_000)] + "...[truncated]"
    rt.record("fetch_url", f"GET {url} ({len(data)} bytes)", {"url": url})
    return text
