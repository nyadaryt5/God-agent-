"""Web search — one of the things a general agent can do that a shell cannot.

God-Agent could already fetch a URL and, more recently, drive a browser. But
"go find out why nginx throws 502" still meant guessing a URL or scraping a
search engine by hand. This is a first-class search tool.

Backends, in the God-Agent spirit (works with zero API keys, upgrades when you
supply one):

  duckduckgo  default, no API key required — scrapes the HTML endpoint
  brave       Brave Search API, needs `search.api_key`
  tavily      Tavily Search API, needs `search.api_key`
  searxng     self-hosted SearXNG, needs `search.base_url`
  mock        offline — returns a fixed stub so tests and demos are hermetic

Stdlib only. Web search stays an optional capability: it is gated by
`policy.network.enabled` like every other network tool, and every query is
recorded in the audit log (the query text itself, so "no hiding actions"
holds here too).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Optional

from ..runtime import get_runtime

# DuckDuckGo blocks the default python-urllib agent. Reuse a real browser UA so
# the no-key default actually works (and stays consistent with the stealth
# profile's identity).
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BACKENDS = ("duckduckgo", "brave", "tavily", "searxng", "mock")


class SearchError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# DuckDuckGo HTML scraping
# ---------------------------------------------------------------------------
class _DDGParser(HTMLParser):
    """Extract result titles, URLs, and snippets from the HTML endpoint."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._in_title = False
        self._in_snippet = False
        self._href: Optional[str] = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        d = {k: (v or "") for k, v in attrs}
        cls = d.get("class", "")
        if tag == "a" and "result__a" in cls:
            self._in_title = True
            self._href = d.get("href", "")
            self._buf = []
        elif "result__snippet" in cls:
            self._in_snippet = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if self._in_title and tag == "a":
            self.results.append({
                "title": "".join(self._buf).strip(),
                "url": _clean_ddg_url(self._href or ""),
                "snippet": "",
            })
            self._in_title = False
            self._buf = []
        elif self._in_snippet:
            if self.results and not self.results[-1]["snippet"]:
                self.results[-1]["snippet"] = "".join(self._buf).strip()
            self._in_snippet = False
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_title or self._in_snippet:
            self._buf.append(data)


def _clean_ddg_url(href: str) -> str:
    """Unwrap DuckDuckGo's redirect links into the real destination."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if "uddg=" in href:
        parsed = urllib.parse.urlparse(href)
        q = urllib.parse.parse_qs(parsed.query)
        if q.get("uddg"):
            return urllib.parse.unquote(q["uddg"][0])
    return href


def _http_get(url: str, *, headers: dict, timeout: int, max_bytes: int = 2_000_000) -> str:
    req = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310
    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return resp.read(max_bytes).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = SearchError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
            if e.code in (400, 401, 403, 404, 429):
                break
            time.sleep(1.0 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = SearchError(f"network error: {e}")
            time.sleep(1.0 * (attempt + 1))
    raise last if last else SearchError("unknown search error")


def _http_post_json(url: str, payload: dict, *, headers: dict, timeout: int) -> dict:
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")  # noqa: S310
    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return json.loads(resp.read(2_000_000).decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = SearchError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
            if e.code in (400, 401, 403, 404, 429):
                break
            time.sleep(1.0 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            last = SearchError(f"network error: {e}")
            time.sleep(1.0 * (attempt + 1))
    raise last if last else SearchError("unknown search error")


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
def _search_duckduckgo(query: str, conf: dict) -> list[dict]:
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    html = _http_get(url, headers={"User-Agent": _BROWSER_UA}, timeout=conf["timeout_s"])
    parser = _DDGParser()
    parser.feed(html)
    out = []
    for r in parser.results:
        if r["url"] and r["title"]:
            out.append({"title": r["title"], "url": r["url"], "snippet": r["snippet"]})
        if len(out) >= conf["max_results"]:
            break
    return out


def _search_brave(query: str, conf: dict) -> list[dict]:
    key = conf.get("api_key") or os.environ.get("BRAVE_API_KEY", "")
    if not key:
        raise SearchError("brave backend needs search.api_key (or $BRAVE_API_KEY)")
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
        {"q": query, "count": conf["max_results"]})
    data = _http_get(url, headers={"X-Subscription-Token": key,
                                   "Accept": "application/json",
                                   "User-Agent": _BROWSER_UA},
                     timeout=conf["timeout_s"])
    items = json.loads(data).get("web", {}).get("results", []) or []
    return [{"title": i.get("title", ""), "url": i.get("url", ""),
             "snippet": (i.get("description") or "").replace("<strong>", "").replace("</strong>", "")}
            for i in items]


def _search_tavily(query: str, conf: dict) -> list[dict]:
    key = conf.get("api_key") or os.environ.get("TAVILY_API_KEY", "")
    if not key:
        raise SearchError("tavily backend needs search.api_key (or $TAVILY_API_KEY)")
    data = _http_post_json("https://api.tavily.com/search",
                           {"api_key": key, "query": query,
                            "max_results": conf["max_results"]},
                           headers={"User-Agent": _BROWSER_UA},
                           timeout=conf["timeout_s"])
    return [{"title": i.get("title", ""), "url": i.get("url", ""),
             "snippet": i.get("content", "")} for i in (data.get("results") or [])]


def _search_searxng(query: str, conf: dict) -> list[dict]:
    base = (conf.get("base_url") or "").rstrip("/")
    if not base:
        raise SearchError("searxng backend needs search.base_url")
    url = f"{base}/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "safesearch": 1 if conf.get("safe_search", True) else 0})
    data = _http_get(url, headers={"User-Agent": _BROWSER_UA}, timeout=conf["timeout_s"])
    return [{"title": i.get("title", ""), "url": i.get("url", ""),
             "snippet": i.get("content", "")} for i in (json.loads(data).get("results") or [])]


def _search_mock(query: str, conf: dict) -> list[dict]:
    """Offline stub: keeps tests hermetic and demos working with no network."""
    return [{"title": f"[mock] result 1 for {query}",
             "url": "https://example.com/1", "snippet": "offline mock result"},
            {"title": f"[mock] result 2 for {query}",
             "url": "https://example.com/2", "snippet": "offline mock result"}]


_BACKENDS = {
    "duckduckgo": _search_duckduckgo,
    "brave": _search_brave,
    "tavily": _search_tavily,
    "searxng": _search_searxng,
    "mock": _search_mock,
}


def search(query: str, conf: dict) -> list[dict]:
    backend = str(conf.get("backend") or "duckduckgo").lower()
    fn = _BACKENDS.get(backend)
    if fn is None:
        raise SearchError(f"unknown search backend: {backend}. Options: {', '.join(BACKENDS)}")
    return fn(query, conf)[: int(conf.get("max_results", 8))]


def _cfg(rt) -> dict:
    s = rt.cfg.get("search") or {}
    return {
        "enabled": bool(s.get("enabled", True)),
        "backend": str(s.get("backend") or "duckduckgo"),
        "api_key": s.get("api_key", ""),
        "base_url": s.get("base_url", ""),
        "max_results": max(1, min(int(s.get("max_results", 8)), 25)),
        "timeout_s": int(s.get("timeout_s", 20)),
        "safe_search": bool(s.get("safe_search", True)),
        "max_snippet_chars": int(s.get("max_snippet_chars", 400)),
    }


def _err(rt, action: str, exc: BaseException) -> str:
    msg = f"ERROR: {type(exc).__name__}: {exc}" if not isinstance(exc, SearchError) else f"ERROR: {exc}"
    try:
        rt.record(action, msg, {"error": type(exc).__name__})
    except Exception:
        pass
    return msg


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
def web_search(reg, name: str, args: dict) -> str:
    """Search the web and return titles, URLs, and snippets.

    Use this to answer "why does X happen" questions, find documentation, or
    discover a URL before opening it in the browser. Follow up with
    `fetch_url`, `browser_open`, or `browser_extract` to read a result.
    """
    rt = get_runtime()
    conf = _cfg(rt)
    query = str(args.get("query", "")).strip()
    if not query:
        return "ERROR: query required"
    if not conf["enabled"]:
        return "ERROR: search is disabled (set search.enabled=true)"
    # A per-call override is fine, but never above the configured ceiling.
    if args.get("max_results"):
        conf["max_results"] = max(1, min(int(args["max_results"]), 25))

    try:
        results = search(query, conf)
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "web_search", exc)

    if not results:
        rt.record("web_search", f"search '{query}' -> 0 results", {"query": query})
        return f"(no results for: {query})"

    cap = conf["max_snippet_chars"]
    lines = [f"{len(results)} result(s) for: {query}\n"]
    for i, r in enumerate(results, 1):
        snip = (r.get("snippet") or "").strip().replace("\n", " ")
        if len(snip) > cap:
            snip = snip[:cap] + "..."
        lines.append(f"[{i}] {r.get('title', '(untitled)')}")
        lines.append(f"    {r.get('url', '')}")
        if snip:
            lines.append(f"    {snip}")
    rt.record("web_search", f"search '{query}' -> {len(results)} results",
              {"query": query, "count": len(results), "backend": conf["backend"]})
    return "\n".join(lines)


__all__ = ["web_search", "search", "SearchError", "BACKENDS", "search_available"]


def search_available(conf: dict) -> bool:
    """True unless the backend needs a credential that isn't configured."""
    backend = str(conf.get("backend") or "duckduckgo").lower()
    if backend in ("brave", "tavily"):
        return bool(conf.get("api_key")) or bool(
            os.environ.get("BRAVE_API_KEY" if backend == "brave" else "TAVILY_API_KEY"))
    if backend == "searxng":
        return bool(conf.get("base_url"))
    return backend in _BACKENDS
