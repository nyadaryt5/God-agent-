"""Tests for the browser toolset.

These exercise God-Agent's real browser code paths without needing a Chromium
binary, by injecting a fake `playwright.sync_api` module into `sys.modules`.
That covers what actually matters and is easy to get wrong:

  * the 9 tools register, are policy-graded, and honour `network.enabled`
  * one browser session is reused across calls (so logins persist)
  * sessions are flushed to disk and reload on the next launch
  * the session relaunches when the thread changes (Playwright is thread-affine)
  * a missing Playwright degrades to an actionable error, never a crash

A live end-to-end test runs only when a real Chromium is installed and is
skipped otherwise, so CI without browser binaries stays green.
"""
import json
import os
import sys
import tempfile
import threading
import types
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import pytest  # noqa: E402
except ImportError:  # pragma: no cover
    pytest = None

from god_agent.config import default_config  # noqa: E402
from god_agent.policy import Policy, TOOL_RISK, NETWORK_TOOLS  # noqa: E402
from god_agent.runtime import Runtime  # noqa: E402
from god_agent.tools import browser as B  # noqa: E402

BROWSER_TOOLS = [
    "browser_open", "browser_click", "browser_type", "browser_extract",
    "browser_links", "browser_wait", "browser_screenshot", "browser_eval",
    "browser_close",
]


# ---------------------------------------------------------------------------
# Fake Playwright driver
# ---------------------------------------------------------------------------
class FakePage:
    """Records interactions and serves canned content."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.url = "about:blank"
        self.title_text = "Fake Page"
        self.is_closed_flag = False
        self.calls = []
        self.content = {
            "body": "Welcome back, operator.\nYour last login was Tuesday.",
            "h1": "Dashboard",
            "#secret": "classified",
        }
        self.links = [
            {"text": "Home", "href": "https://example.com/"},
            {"text": "Login", "href": "https://example.com/login"},
            {"text": "Login", "href": "https://example.com/login"},  # duplicate on purpose
        ]

    def is_closed(self):
        return self.is_closed_flag

    def goto(self, url, wait_until=None, timeout=None):
        self.calls.append(("goto", url))
        self.url = url
        resp = types.SimpleNamespace(status=200)
        return resp

    def title(self):
        return self.title_text

    def inner_text(self, sel="body", timeout=None):
        self.calls.append(("inner_text", sel))
        return self.content.get(sel, "")

    def inner_html(self, sel="body", timeout=None):
        return f"<div>{self.content.get(sel, '')}</div>"

    def input_value(self, sel, timeout=None):
        return self.ctx.typed.get(sel, "")

    def get_attribute(self, sel, attr, timeout=None):
        return "https://example.com/avatar.png"

    def click(self, sel, timeout=None):
        self.calls.append(("click", sel))

    def fill(self, sel, value, timeout=None):
        self.calls.append(("fill", sel))

    def type(self, sel, text, timeout=None, delay=None):
        self.calls.append(("type", sel, text))
        self.ctx.typed[sel] = self.ctx.typed.get(sel, "") + text

    def press(self, sel, key, timeout=None):
        self.calls.append(("press", sel, key))

    def wait_for_timeout(self, ms):
        self.calls.append(("wait", ms))

    def wait_for_selector(self, sel, state=None, timeout=None):
        self.calls.append(("wait_for_selector", sel, state))

    def eval_on_selector_all(self, sel, script):
        return self.links

    def evaluate(self, script):
        self.calls.append(("evaluate", script))
        return {"evaluated": True, "len": len(script)}

    def screenshot(self, path=None, full_page=False, timeout=None):
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\nFAKE")
        self.calls.append(("screenshot", path, full_page))


class FakeContext:
    def __init__(self, browser, **kwargs):
        self.browser = browser
        self.kwargs = kwargs
        self.typed = {}
        self.closed = False
        self.storage_writes = []
        self.pages = []

    def set_default_timeout(self, ms):
        self.default_timeout = ms

    def new_page(self):
        p = FakePage(self)
        self.pages.append(p)
        return p

    def storage_state(self, path=None):
        self.storage_writes.append(path)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"cookies": [{"name": "sessionid", "value": "abc123"}],
                       "origins": []}, fh)

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, pw, **kwargs):
        self.pw = pw
        self.kwargs = kwargs
        self.contexts = []
        self.closed = False

    def new_context(self, **kwargs):
        ctx = FakeContext(self, **kwargs)
        self.contexts.append(ctx)
        return ctx

    def close(self):
        self.closed = True


class FakePlaywright:
    """Stands in for the object returned by sync_playwright().start()."""

    instances = []

    def __init__(self):
        self.chromium = self
        self.started = []
        self.stopped = False
        FakePlaywright.instances.append(self)

    def start(self):
        return self

    def stop(self):
        self.stopped = True

    def launch(self, **kwargs):
        b = FakeBrowser(self, **kwargs)
        self.started.append(b)
        return b


def install_fake_playwright():
    """Inject the fake driver and reset browser session state."""
    FakePlaywright.instances.clear()
    mod = types.ModuleType("playwright")
    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = lambda: FakePlaywright()
    mod.sync_api = api
    sys.modules["playwright"] = mod
    sys.modules["playwright.sync_api"] = api
    B._teardown()
    return mod, api


def uninstall_fake_playwright():
    for key in ("playwright", "playwright.sync_api"):
        sys.modules.pop(key, None)
    B._teardown()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def make_cfg(tmp_path):
    cfg = default_config()
    root = str(tmp_path)
    cfg["state"]["root"] = root
    cfg["memory"]["db_path"] = os.path.join(root, "memory.db")
    cfg["brain"]["path"] = os.path.join(root, "brain.json")
    cfg["self_model"]["path"] = os.path.join(root, "self.json")
    cfg["audit"]["path"] = os.path.join(root, "audit.jsonl")
    cfg["kill_switch"]["path"] = os.path.join(root, "DISABLED")
    cfg["browser"]["state_path"] = os.path.join(root, "browser_state.json")
    return cfg


def make_rt(tmp_path):
    rt = Runtime(make_cfg(tmp_path))
    return rt


# ---------------------------------------------------------------------------
# Registration + policy
# ---------------------------------------------------------------------------
def test_browser_tools_registered():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        names = {t.name for t in rt.registry.all()}
        for tool in BROWSER_TOOLS:
            assert tool in names, f"{tool} not registered"


def test_browser_tools_are_risk_graded():
    for tool in BROWSER_TOOLS:
        assert tool in TOOL_RISK, f"{tool} missing from TOOL_RISK"
    # Reading is low risk; submitting data and running JS are not.
    assert TOOL_RISK["browser_extract"] == 1
    assert TOOL_RISK["browser_type"] == 4
    assert TOOL_RISK["browser_eval"] == 5


def test_browser_tools_gated_by_network_policy():
    cfg = default_config()
    cfg["policy"]["network"]["enabled"] = False
    p = Policy(cfg)
    d = p.assess("browser_open", {"url": "https://example.com"})
    assert not d.allowed, "browser must respect network.enabled=false"
    assert "network disabled" in d.reason


def test_all_network_reaching_browser_tools_are_gated():
    for tool in BROWSER_TOOLS:
        if tool == "browser_close":
            continue
        assert tool in NETWORK_TOOLS, f"{tool} escapes the network gate"


def test_browser_close_stays_available_when_network_off():
    """Closing is local cleanup, not network access.

    If an operator disables `network.enabled` mid-run, the agent must still be
    able to tear the browser down — otherwise the process leaks. So close is
    deliberately *outside* the network gate even though it touches the session.
    """
    assert "browser_close" not in NETWORK_TOOLS
    cfg = default_config()
    cfg["policy"]["network"]["enabled"] = False
    p = Policy(cfg)
    assert p.assess("browser_close", {}).allowed


def test_developer_mode_lifts_network_gate():
    cfg = default_config()
    cfg["policy"]["network"]["enabled"] = False
    p = Policy(cfg, dev_mode=True)
    assert p.assess("browser_open", {"url": "https://example.com"}).allowed


def test_default_config_has_browser_section():
    cfg = default_config()
    assert cfg["browser"]["headless"] is True
    assert cfg["browser"]["timeout_s"] == 30
    assert cfg["browser"]["max_text_chars"] == 200_000
    assert cfg["browser"]["allow_js"] is True


# ---------------------------------------------------------------------------
# Graceful degradation (no Playwright installed)
# ---------------------------------------------------------------------------
def test_missing_playwright_gives_actionable_error():
    uninstall_fake_playwright()
    real = sys.modules.pop("playwright", None)
    try:
        with mock.patch.dict(sys.modules, {"playwright": None}):
            with tempfile.TemporaryDirectory() as d:
                rt = make_rt(d)
                out = B.browser_open(rt.registry, "browser_open",
                                     {"url": "https://example.com"})
                assert out.startswith("ERROR:")
                assert "playwright install chromium" in out
                assert "god-agent[browser]" in out
    finally:
        if real is not None:
            sys.modules["playwright"] = real


def test_is_available_reflects_playwright_presence():
    uninstall_fake_playwright()
    try:
        with mock.patch.dict(sys.modules, {"playwright": None}):
            assert B.is_available() is False
        install_fake_playwright()
        assert B.is_available() is True
    finally:
        uninstall_fake_playwright()


def test_browser_close_when_no_session_is_safe():
    uninstall_fake_playwright()
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        out = B.browser_close(rt.registry, "browser_close", {})
        assert out.startswith("ok:")


# ---------------------------------------------------------------------------
# Behaviour with the fake driver
# ---------------------------------------------------------------------------
def test_open_executes_and_returns_rendered_text():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            out = B.browser_open(rt.registry, "browser_open",
                                 {"url": "https://example.com"})
            assert "https://example.com" in out
            assert "Welcome back, operator." in out
            assert "status: 200" in out
            page = B._SESSION["page"]
            assert ("goto", "https://example.com") in page.calls
    finally:
        uninstall_fake_playwright()


def test_session_is_reused_across_calls():
    """A login typed in one call must still be there in the next."""
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com/login"})
            first = B._SESSION["page"]
            B.browser_type(rt.registry, "browser_type",
                           {"selector": "#user", "text": "operator"})
            B.browser_type(rt.registry, "browser_type",
                           {"selector": "#pass", "text": "s3cret"})
            B.browser_click(rt.registry, "browser_click", {"selector": "button[type=submit]"})
            assert B._SESSION["page"] is first, "session must be reused, not relaunched"
            typed = B._SESSION["context"].typed
            assert typed["#user"] == "operator"
            assert typed["#pass"] == "s3cret"
            assert len(FakePlaywright.instances[0].started) == 1, "launched more than one browser"
    finally:
        uninstall_fake_playwright()


def test_type_press_enter_and_clear():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            B.browser_type(rt.registry, "browser_type",
                           {"selector": "#q", "text": "god agent", "press_enter": True})
            calls = B._SESSION["page"].calls
            assert ("fill", "#q") in calls          # cleared first
            assert ("type", "#q", "god agent") in calls
            assert ("press", "#q", "Enter") in calls
    finally:
        uninstall_fake_playwright()


def test_extract_modes():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            reg = rt.registry
            assert "classified" in B.browser_extract(reg, "browser_extract",
                                                     {"selector": "#secret"})
            assert "<div>" in B.browser_extract(reg, "browser_extract",
                                                {"selector": "h1", "mode": "html"})
            assert "avatar.png" in B.browser_extract(
                reg, "browser_extract",
                {"selector": "img", "mode": "attribute", "attribute": "src"})
            assert B.browser_extract(reg, "browser_extract",
                                     {"mode": "attribute"}).startswith("ERROR:")
    finally:
        uninstall_fake_playwright()


def test_extract_truncates_to_config_limit():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            rt.cfg["browser"]["max_text_chars"] = 10
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            out = B.browser_extract(rt.registry, "browser_extract", {"selector": "body"})
            assert len(out) <= 10 + len("\n...[truncated at 10 chars]") + 5
            assert "truncated" in out
    finally:
        uninstall_fake_playwright()


def test_links_deduplicates_and_limits():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            out = B.browser_links(rt.registry, "browser_links", {"limit": 10})
            lines = [ln for ln in out.splitlines() if ln.strip()]
            assert len(lines) == 2, "duplicate hrefs must collapse"
            out2 = B.browser_links(rt.registry, "browser_links", {"limit": 1})
            assert len([ln for ln in out2.splitlines() if ln.strip()]) == 1
    finally:
        uninstall_fake_playwright()


def test_screenshot_writes_file():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            path = os.path.join(d, "shot.png")
            out = B.browser_screenshot(rt.registry, "browser_screenshot",
                                       {"path": path, "full_page": True})
            assert "ok:" in out
            assert os.path.isfile(path)
            assert ("screenshot", path, True) in B._SESSION["page"].calls
    finally:
        uninstall_fake_playwright()


def test_eval_respects_allow_js():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            assert "evaluated" in B.browser_eval(rt.registry, "browser_eval",
                                                 {"script": "1+1"})
            rt.cfg["browser"]["allow_js"] = False
            out = B.browser_eval(rt.registry, "browser_eval", {"script": "1+1"})
            assert out.startswith("ERROR:") and "allow_js" in out
    finally:
        uninstall_fake_playwright()


def test_url_scheme_validation_rejects_non_http():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            for bad in ("file:///etc/passwd", "javascript:alert(1)", ""):
                out = B.browser_open(rt.registry, "browser_open", {"url": bad})
                assert out.startswith("ERROR:"), f"{bad!r} should be rejected"
    finally:
        uninstall_fake_playwright()


def test_wait_modes():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            assert "visible" in B.browser_wait(rt.registry, "browser_wait",
                                               {"selector": "#loaded"})
            assert "waited" in B.browser_wait(rt.registry, "browser_wait", {"ms": 250})
            assert B.browser_wait(rt.registry, "browser_wait",
                                  {"state": "bogus"}).startswith("ERROR:")
    finally:
        uninstall_fake_playwright()


def test_session_state_persists_to_disk_and_reloads():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            state = rt.cfg["browser"]["state_path"]
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            B.browser_close(rt.registry, "browser_close", {})

            assert os.path.isfile(state), "cookies must be flushed on close"
            with open(state) as fh:
                assert json.load(fh)["cookies"][0]["name"] == "sessionid"

            # A fresh launch must resume the saved session.
            FakePlaywright.instances.clear()
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            ctx = B._SESSION["context"]
            assert ctx.kwargs.get("storage_state") == state
    finally:
        uninstall_fake_playwright()


def test_session_relaunches_when_thread_changes():
    """Playwright's sync API is thread-affine; the session must restart cleanly."""
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            first_page = B._SESSION["page"]

            result = {}

            def other_thread():
                rt.rebind_thread()
                result["out"] = B.browser_extract(rt.registry, "browser_extract",
                                                  {"selector": "body"})

            t = threading.Thread(target=other_thread)
            t.start()
            t.join(timeout=15)

            assert not t.is_alive(), "browser call deadlocked on a new thread"
            assert "Welcome back" in result["out"]
            assert B._SESSION["page"] is not first_page, "must relaunch on thread change"
            assert len(FakePlaywright.instances) >= 2
    finally:
        uninstall_fake_playwright()


def test_actions_are_audited():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            audit_path = rt.cfg["audit"]["path"]
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            B.browser_type(rt.registry, "browser_type",
                           {"selector": "#q", "text": "hello"})
            with open(audit_path) as fh:
                blob = fh.read()
            assert "browser_open" in blob
            assert "browser_type" in blob
    finally:
        uninstall_fake_playwright()


def test_errors_never_raise_out_of_the_tool():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})

            def boom(*a, **k):
                raise RuntimeError("selector not found")

            B._SESSION["page"].click = boom
            out = B.browser_click(rt.registry, "browser_click", {"selector": "#nope"})
            assert out.startswith("ERROR:")
            assert "selector not found" in out
    finally:
        uninstall_fake_playwright()


def test_dispatch_respects_policy_denial():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            cfg = make_cfg(d)
            cfg["policy"]["network"]["enabled"] = False
            rt = Runtime(cfg)
            res = rt.registry.dispatch("browser_open", {"url": "https://example.com"})
            assert not res.ok
            assert "DENIED" in res.output
    finally:
        uninstall_fake_playwright()


def test_headless_flag_reaches_launch():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            rt.cfg["browser"]["headless"] = False
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            launched = FakePlaywright.instances[0].started[0]
            assert launched.kwargs["headless"] is False
    finally:
        uninstall_fake_playwright()


# ---------------------------------------------------------------------------
# Live end-to-end (skipped unless a real Chromium is installed)
# ---------------------------------------------------------------------------
def test_live_browser_if_chromium_present():
    uninstall_fake_playwright()
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        pw = sync_playwright().start()
        try:
            pw.chromium.launch(headless=True)
        finally:
            pw.stop()
    except Exception as exc:  # noqa: BLE001
        if pytest is not None:
            pytest.skip(f"no Chromium binary available: {exc}")
        return

    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        reg = rt.registry
        B.browser_open(reg, "browser_open", {"url": "https://example.com"})
        out = B.browser_extract(reg, "browser_extract", {"selector": "body"})
        assert "Example Domain" in out or "example" in out.lower()
        B.browser_close(reg, "browser_close", {})


if __name__ == "__main__":  # pragma: no cover
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{'FAILURES: ' + str(failures) if failures else 'all passed'}")
    sys.exit(1 if failures else 0)
