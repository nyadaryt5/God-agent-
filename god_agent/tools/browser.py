"""Browser tools: real headless Chromium automation (Playwright-backed).

This is the missing capability God-Agent never had: every tool here drives a
genuine browser engine, so JavaScript runs, cookies persist, sessions survive,
and pages render exactly as a human would see them. `fetch_url` cannot do any
of that — it is a bare HTTP GET with no JS, no cookies, and no session.

Design notes
------------
* **Optional dependency.** Playwright is *not* a hard requirement. God-Agent
  keeps `dependencies = []`; the browser tools degrade to a clear, actionable
  error (`pip install 'god-agent[browser]' && playwright install chromium`)
  instead of breaking startup or the offline heuristic planner.
* **One persistent session.** A single browser context is reused across tool
  calls, so a login performed with `browser_type` is still valid three steps
  later. Cookies/localStorage are flushed to `browser_state.json` under the
  state root, so sessions even survive a restart.
* **Thread-safe enough.** Playwright's sync API is thread-affine. God-Agent
  rebinds runtimes across threads (`Runtime.rebind_thread`), so the session is
  keyed to the creating thread and transparently relaunched if the thread
  changes rather than raising a confusing greenlet error.
* **Policy-graded.** Unlike MCP browser servers — which the MCP module itself
  admits "cannot be graded by the local risk engine" — every tool here flows
  through `Policy.assess`, is bounded by config, and is written to the
  hash-chained audit log.
"""
from __future__ import annotations

import atexit
import os
from typing import Any, Optional

from ..runtime import get_runtime

# ---------------------------------------------------------------------------
# Module state: one browser session reused across tool calls.
# ---------------------------------------------------------------------------
_SESSION: dict[str, Any] = {
    "pw": None,        # sync_playwright() start handle
    "browser": None,   # Browser
    "context": None,   # BrowserContext (cookies/storage live here)
    "page": None,      # Page
    "thread": None,    # creating thread ident (Playwright sync is thread-affine)
    "state_path": "",  # where cookies/storage get flushed on save/close
}


class BrowserUnavailable(RuntimeError):
    """Raised when Playwright or a browser binary is missing."""


# ---------------------------------------------------------------------------
# Config + session plumbing
# ---------------------------------------------------------------------------
def _cfg(rt) -> dict[str, Any]:
    b = rt.cfg.get("browser") or {}
    root = os.path.expanduser(rt.cfg.get("state", {}).get("root", "~/.god-agent"))
    state_path = b.get("state_path") or os.path.join(root, "browser_state.json")
    return {
        "headless": bool(b.get("headless", True)),
        "timeout_ms": max(1, int(b.get("timeout_s", 30))) * 1000,
        "max_text_chars": int(b.get("max_text_chars", 200_000)),
        "viewport": b.get("viewport") or {"width": 1280, "height": 900},
        "user_agent": b.get("user_agent") or "",
        "allow_js": bool(b.get("allow_js", True)),
        "state_path": os.path.expanduser(state_path),
    }


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise BrowserUnavailable(
            "browser tools require Playwright. Install with:\n"
            "    pip install 'god-agent[browser]'   # or: pip install playwright\n"
            "    playwright install chromium"
        ) from exc
    return sync_playwright


def _teardown() -> None:
    """Close browser + Playwright, flushing session state to disk first."""
    _save_state()
    try:
        if _SESSION.get("context") is not None:
            _SESSION["context"].close()
    except Exception:
        pass
    try:
        if _SESSION.get("browser") is not None:
            _SESSION["browser"].close()
    except Exception:
        pass
    try:
        if _SESSION.get("pw") is not None:
            _SESSION["pw"].stop()
    except Exception:
        pass
    _SESSION.update(pw=None, browser=None, context=None, page=None, thread=None)


atexit.register(_teardown)


def _ensure(rt) -> Any:
    """Return a live Page, launching/relaunching the browser as needed."""
    import threading  # noqa: PLC0415

    conf = _cfg(rt)
    tid = threading.get_ident()

    # Playwright's sync API is bound to the thread that created it. If the
    # runtime moved to another thread, restart cleanly instead of exploding.
    if _SESSION["page"] is not None and _SESSION["thread"] != tid:
        _teardown()

    if _SESSION["page"] is not None:
        try:
            if _SESSION["page"].is_closed():
                _teardown()
        except Exception:
            _teardown()

    if _SESSION["page"] is None:
        sync_playwright = _import_playwright()
        pw = sync_playwright().start()
        try:
            launch_kwargs: dict[str, Any] = {
                "headless": conf["headless"],
                "args": ["--no-sandbox", "--disable-dev-shm-usage"],
            }
            browser = pw.chromium.launch(**launch_kwargs)
        except Exception:
            pw.stop()
            raise

        ctx_kwargs: dict[str, Any] = {
            "viewport": conf["viewport"],
            "ignore_https_errors": False,
        }
        if conf["user_agent"]:
            ctx_kwargs["user_agent"] = conf["user_agent"]
        if os.path.isfile(conf["state_path"]):
            # Resume the previous session (cookies + localStorage).
            try:
                ctx_kwargs["storage_state"] = conf["state_path"]
            except Exception:
                pass

        context = browser.new_context(**ctx_kwargs)
        context.set_default_timeout(conf["timeout_ms"])
        page = context.new_page()

        _SESSION.update(
            pw=pw,
            browser=browser,
            context=context,
            page=page,
            thread=tid,
            state_path=conf["state_path"],
        )
    return _SESSION["page"]


def _save_state() -> None:
    """Flush cookies/localStorage to disk so sessions survive restarts."""
    ctx = _SESSION.get("context")
    path = _SESSION.get("state_path")
    if ctx is None or not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        ctx.storage_state(path=path)
    except Exception:
        pass


def _clip(text: str, limit: int) -> str:
    if text is None:
        return ""
    if len(text) > limit:
        return text[:limit] + f"\n...[truncated at {limit} chars]"
    return text


def _check_url(url: str) -> Optional[str]:
    if not url:
        return "ERROR: url required"
    if not url.startswith(("http://", "https://")):
        return "ERROR: only http:// and https:// URLs are supported"
    return None


def _err(rt, action: str, exc: BaseException) -> str:
    """Uniform, audited error rendering."""
    if isinstance(exc, BrowserUnavailable):
        msg = f"ERROR: {exc}"
    else:
        msg = f"ERROR: {type(exc).__name__}: {exc}"
    try:
        rt.record(action, msg, {"error": type(exc).__name__})
    except Exception:
        pass
    return msg


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def browser_open(reg, name: str, args: dict) -> str:
    """Navigate to a URL in a real browser (JS executes, cookies persist)."""
    rt = get_runtime()
    conf = _cfg(rt)
    url = str(args.get("url", "")).strip()
    if bad := _check_url(url):
        return bad
    wait_until = str(args.get("wait_until", "domcontentloaded"))
    if wait_until not in ("load", "domcontentloaded", "networkidle", "commit"):
        return "ERROR: wait_until must be load|domcontentloaded|networkidle|commit"

    try:
        page = _ensure(rt)
        resp = page.goto(url, wait_until=wait_until, timeout=conf["timeout_ms"])
        title = page.title()
        final = page.url
        status = getattr(resp, "status", None)
        text = _clip(page.inner_text("body") or "", conf["max_text_chars"])
        _save_state()
        rt.record("browser_open", f"GET {url} -> {final} (status {status})",
                  {"url": url, "final_url": final, "status": status, "title": title})
        head = f"url: {final}\ntitle: {title}\nstatus: {status}\n\n--- text ---\n"
        return head + (text or "(empty page body)")
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "browser_open", exc)


def browser_click(reg, name: str, args: dict) -> str:
    """Click an element by CSS selector, text, or XPath."""
    rt = get_runtime()
    conf = _cfg(rt)
    sel = str(args.get("selector", "")).strip()
    if not sel:
        return "ERROR: selector required"
    try:
        page = _ensure(rt)
        page.click(sel, timeout=conf["timeout_ms"])
        page.wait_for_timeout(300)  # let click handlers settle
        _save_state()
        rt.record("browser_click", f"clicked {sel}", {"selector": sel, "url": page.url})
        return f"ok: clicked {sel} (now at {page.url})"
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "browser_click", exc)


def browser_type(reg, name: str, args: dict) -> str:
    """Type text into an input (optionally clearing it first and pressing Enter)."""
    rt = get_runtime()
    conf = _cfg(rt)
    sel = str(args.get("selector", "")).strip()
    text = str(args.get("text", ""))
    if not sel:
        return "ERROR: selector required"
    clear = bool(args.get("clear", True))
    press_enter = bool(args.get("press_enter", False))
    delay = int(args.get("delay_ms", 0) or 0)

    try:
        page = _ensure(rt)
        if clear:
            page.fill(sel, "", timeout=conf["timeout_ms"])
        page.type(sel, text, timeout=conf["timeout_ms"], delay=delay)
        if press_enter:
            page.press(sel, "Enter", timeout=conf["timeout_ms"])
            page.wait_for_timeout(500)
        _save_state()
        rt.record("browser_type", f"typed {len(text)} chars into {sel}",
                  {"selector": sel, "chars": len(text), "press_enter": press_enter,
                   "url": page.url})
        return f"ok: typed {len(text)} chars into {sel} (now at {page.url})"
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "browser_type", exc)


def browser_extract(reg, name: str, args: dict) -> str:
    """Extract rendered text/HTML/attribute from the page or a selector."""
    rt = get_runtime()
    conf = _cfg(rt)
    sel = str(args.get("selector", "body")).strip() or "body"
    mode = str(args.get("mode", "text")).lower()
    if mode not in ("text", "html", "value", "attribute"):
        return "ERROR: mode must be text|html|value|attribute"
    attr = str(args.get("attribute", "")).strip()

    try:
        page = _ensure(rt)
        if mode == "text":
            out = page.inner_text(sel, timeout=conf["timeout_ms"])
        elif mode == "html":
            out = page.inner_html(sel, timeout=conf["timeout_ms"])
        elif mode == "value":
            out = page.input_value(sel, timeout=conf["timeout_ms"])
        else:
            if not attr:
                return "ERROR: attribute mode requires 'attribute'"
            out = page.get_attribute(sel, attr, timeout=conf["timeout_ms"]) or ""
        out = _clip(str(out), conf["max_text_chars"])
        rt.record("browser_extract", f"extracted {mode} from {sel} ({len(out)} chars)",
                  {"selector": sel, "mode": mode, "url": page.url})
        return out or "(empty)"
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "browser_extract", exc)


def browser_links(reg, name: str, args: dict) -> str:
    """List every link on the current page — the map for navigating further."""
    rt = get_runtime()
    conf = _cfg(rt)
    limit = int(args.get("limit", 100) or 100)
    try:
        page = _ensure(rt)
        items = page.eval_on_selector_all(
            "a[href]",
            """els => els.map(e => ({text: (e.innerText||'').trim().slice(0,120),
                                     href: e.href}))""",
        )
        seen, lines = set(), []
        for it in items:
            href = (it.get("href") or "").strip()
            if not href or href in seen:
                continue
            seen.add(href)
            lines.append(f"{(it.get('text') or '')[:120]}\t{href}")
            if len(lines) >= limit:
                break
        rt.record("browser_links", f"listed {len(lines)} links from {page.url}",
                  {"url": page.url, "count": len(lines)})
        return "\n".join(lines) or "(no links found)"
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "browser_links", exc)


def browser_wait(reg, name: str, args: dict) -> str:
    """Wait for a selector to appear/disappear, or for a fixed duration."""
    rt = get_runtime()
    conf = _cfg(rt)
    sel = str(args.get("selector", "")).strip()
    state = str(args.get("state", "visible")).lower()
    if state not in ("visible", "hidden", "attached", "detached"):
        return "ERROR: state must be visible|hidden|attached|detached"
    try:
        page = _ensure(rt)
        if sel:
            page.wait_for_selector(sel, state=state, timeout=conf["timeout_ms"])
            msg = f"ok: '{sel}' is {state}"
        else:
            ms = min(int(args.get("ms", 1000) or 1000), conf["timeout_ms"])
            page.wait_for_timeout(ms)
            msg = f"ok: waited {ms}ms"
        rt.record("browser_wait", msg, {"selector": sel, "state": state, "url": page.url})
        return f"{msg} (now at {page.url})"
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "browser_wait", exc)


def browser_screenshot(reg, name: str, args: dict) -> str:
    """Save a PNG screenshot of the current page (full page or viewport)."""
    rt = get_runtime()
    conf = _cfg(rt)
    path = str(args.get("path", "")).strip()
    if not path:
        return "ERROR: path required"
    path = os.path.expanduser(path)
    full_page = bool(args.get("full_page", False))
    try:
        page = _ensure(rt)
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        page.screenshot(path=path, full_page=full_page, timeout=conf["timeout_ms"])
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        rt.record("browser_screenshot", f"saved screenshot {path} ({size} bytes)",
                  {"path": path, "full_page": full_page, "url": page.url})
        return f"ok: screenshot saved to {path} ({size} bytes)"
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "browser_screenshot", exc)


def browser_eval(reg, name: str, args: dict) -> str:
    """Run JavaScript in the page and return the JSON-serialised result.

    Escape hatch for anything the structured tools cannot express (dropdowns,
    scrolling, infinite lists). Disable with `browser.allow_js = false`.
    """
    rt = get_runtime()
    conf = _cfg(rt)
    script = str(args.get("script", "")).strip()
    if not script:
        return "ERROR: script required"
    if not conf["allow_js"] and not rt.dev_mode:
        msg = "ERROR: browser.allow_js is disabled in config"
        rt.record("browser_eval", msg, {"script": script[:200]})
        return msg
    try:
        page = _ensure(rt)
        result = page.evaluate(script)
        import json  # noqa: PLC0415

        try:
            out = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            out = str(result)
        out = _clip(out, conf["max_text_chars"])
        rt.record("browser_eval", f"evaluated JS ({len(script)} chars) on {page.url}",
                  {"url": page.url, "script": script[:500]})
        return out or "(undefined)"
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "browser_eval", exc)


def browser_close(reg, name: str, args: dict) -> str:
    """Close the browser, flushing cookies/session to disk."""
    rt = get_runtime()
    if _SESSION["page"] is None:
        return "ok: no browser session is open"
    try:
        url = _SESSION["page"].url
        _teardown()
        rt.record("browser_close", f"closed browser session (was at {url})", {"url": url})
        return "ok: browser closed, session state saved"
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "browser_close", exc)


def is_available() -> bool:
    """True when Playwright is importable (browser binaries may still be missing)."""
    try:
        _import_playwright()
        return True
    except BrowserUnavailable:
        return False


# Exposed for tests / diagnostics.
__all__ = [
    "browser_open", "browser_click", "browser_type", "browser_extract",
    "browser_links", "browser_wait", "browser_screenshot", "browser_eval",
    "browser_close", "is_available", "BrowserUnavailable", "_teardown",
]
