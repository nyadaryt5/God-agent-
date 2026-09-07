"""Tests for the anti-bot-detection layer.

Scope of what these prove: that the hardening is *wired up correctly* — the
right launch flags are passed, the right context options are set, the init
script is installed, human timing is applied, and the verification tool
correctly interprets what it sees.

They do not prove the JavaScript patches defeat a given vendor, because the
fake driver cannot execute JS. That is what `browser_stealth_check` is for:
run it against a real target and read the report.
"""
import json
import os
import sys
import tempfile
import time
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

from god_agent.config import default_config  # noqa: E402
from god_agent.runtime import Runtime  # noqa: E402
from god_agent.tools import browser as B  # noqa: E402
from god_agent.tools import stealth as S  # noqa: E402

from test_browser import (  # noqa: E402
    install_fake_playwright, uninstall_fake_playwright, make_cfg, make_rt,
)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
def test_all_profiles_are_internally_consistent():
    """The whole point: UA, Client Hints, and WebGL must describe one machine."""
    for name in S.profile_names():
        p = S.get_profile(name)
        ua = p["user_agent"]
        if "Windows" in ua:
            assert p["platform"] == "Win32"
            assert p["ua_data_platform"] == "Windows"
        elif "Macintosh" in ua:
            assert p["platform"] == "MacIntel"
            assert p["ua_data_platform"] == "macOS"
        else:
            assert p["platform"].startswith("Linux")
            assert p["ua_data_platform"] == "Linux"
        assert "Headless" not in ua, f"{name} advertises headless"
        assert "Chrome/" in ua
        assert p["vendor"] == "Google Inc."
        # Software renderers are the classic headless tell.
        assert not any(t in p["webgl_renderer"]
                       for t in ("SwiftShader", "llvmpipe", "Software"))
        assert p["hardware_concurrency"] >= 2
        assert p["device_memory"] >= 2
        assert p["screen"]["height"] >= 720


def test_profile_overrides_apply():
    p = S.get_profile("windows-chrome", {"webgl_renderer": "ANGLE (Custom)",
                                         "timezone": "Europe/Berlin"})
    assert p["webgl_renderer"] == "ANGLE (Custom)"
    assert p["timezone"] == "Europe/Berlin"
    # Empty overrides must not clobber defaults.
    p2 = S.get_profile("windows-chrome", {"webgl_renderer": "", "timezone": ""})
    assert p2["webgl_renderer"] != ""


def test_unknown_profile_falls_back():
    p = S.get_profile("does-not-exist")
    assert p["user_agent"] == S.PROFILES[S.DEFAULT_PROFILE]["user_agent"]


# ---------------------------------------------------------------------------
# Launch / context wiring
# ---------------------------------------------------------------------------
def test_launch_args_defeat_automation_flags():
    kw = S.launch_kwargs({"headless": True})
    joined = " ".join(kw["args"])
    assert "--disable-blink-features=AutomationControlled" in joined
    assert "--enable-automation" in kw["ignore_default_args"]
    assert "--disable-infobars" in joined


def test_stealth_applied_on_launch():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            ctx = B._SESSION["context"]
            browser = B._SESSION["browser"]

            assert len(ctx.init_scripts) == 1, "stealth init script not installed"
            assert "--disable-blink-features=AutomationControlled" in " ".join(browser.kwargs["args"])
            assert "--enable-automation" in browser.kwargs["ignore_default_args"]
            # UA comes from the profile, not Playwright's headless default.
            prof = S.PROFILES["windows-chrome"]
            assert ctx.kwargs["user_agent"] == prof["user_agent"]
            assert "Headless" not in ctx.kwargs["user_agent"]
            assert ctx.kwargs["locale"] == "en-US"
    finally:
        uninstall_fake_playwright()


def test_init_script_covers_the_major_signals():
    prof = S.get_profile("windows-chrome")
    js = S.init_script(prof, {"width": 1280, "height": 900})

    assert "webdriver" in js
    assert "Object.getPrototypeOf(navigator).webdriver" in js
    assert "/^(cdc_|\\$cdc_" in js
    assert "window.chrome" in js
    assert "userAgentData" in js
    assert "37445" in js and "37446" in js          # WebGL vendor + renderer
    assert "outerHeight" in js                       # browser-chrome offset
    assert "native code" in js                       # toString integrity
    # Values must be baked in, not left as placeholders.
    assert "__PROFILE__" not in js
    assert "__VIEWPORT__" not in js
    assert prof["webgl_renderer"] in js


def test_stealth_disabled_keeps_plain_launch():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            rt.cfg["browser"]["stealth"]["enabled"] = False
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            ctx = B._SESSION["context"]
            browser = B._SESSION["browser"]
            assert ctx.init_scripts == []
            assert "user_agent" not in ctx.kwargs
            assert browser.kwargs["args"] == ["--no-sandbox", "--disable-dev-shm-usage"]
    finally:
        uninstall_fake_playwright()


def test_profile_selection_changes_identity():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            rt.cfg["browser"]["stealth"]["profile"] = "macos-chrome"
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            ctx = B._SESSION["context"]
            assert "Macintosh" in ctx.kwargs["user_agent"]
            assert ctx.kwargs["locale"] == "en-US"
            assert "MacIntel" in ctx.init_scripts[0]
    finally:
        uninstall_fake_playwright()


def test_explicit_user_agent_overrides_stealth():
    """An operator UA wins — and we skip the init script rather than ship a
    UA that contradicts the spoofed navigator fields."""
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            rt.cfg["browser"]["user_agent"] = "MyCorpBot/1.0"
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            ctx = B._SESSION["context"]
            assert ctx.kwargs["user_agent"] == "MyCorpBot/1.0"
            assert ctx.init_scripts == []
    finally:
        uninstall_fake_playwright()


def test_proxy_is_single_and_static():
    kw = S.launch_kwargs({"headless": True}, {"server": "http://proxy:8080",
                                              "username": "u", "password": "p"})
    assert kw["proxy"]["server"] == "http://proxy:8080"
    assert kw["proxy"]["username"] == "u"
    # No rotation pool: one server, full stop.
    assert isinstance(kw["proxy"]["server"], str)
    assert "proxy" not in S.launch_kwargs({"headless": True}, {})


# ---------------------------------------------------------------------------
# Human-like timing
# ---------------------------------------------------------------------------
def test_humanize_types_per_character():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            hum = rt.cfg["browser"]["stealth"]["humanize"]
            hum["enabled"] = True
            hum["typing_delay_ms"] = [0, 0]      # keep the test fast
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            B.browser_type(rt.registry, "browser_type",
                           {"selector": "#q", "text": "abc"})
            types = [c for c in B._SESSION["page"].calls if c[0] == "type"]
            assert len(types) == 3, "expected one call per keystroke"
            assert [t[2] for t in types] == ["a", "b", "c"]
    finally:
        uninstall_fake_playwright()


def test_humanize_disabled_types_in_one_call():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            rt.cfg["browser"]["stealth"]["humanize"]["enabled"] = False
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            B.browser_type(rt.registry, "browser_type",
                           {"selector": "#q", "text": "abc"})
            types = [c for c in B._SESSION["page"].calls if c[0] == "type"]
            assert len(types) == 1
    finally:
        uninstall_fake_playwright()


def test_humanized_click_moves_mouse_first():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            B.browser_click(rt.registry, "browser_click", {"selector": "button"})
            page = B._SESSION["page"]
            assert page.mouse.moves, "cursor teleported instead of moving"
            assert len(page.mouse.clicks) == 1
            # The click lands inside the element box, not at a fixed origin.
            x, y = page.mouse.clicks[0]
            assert 100 <= x <= 220 and 200 <= y <= 240
    finally:
        uninstall_fake_playwright()


def test_plain_click_when_humanize_off():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            rt.cfg["browser"]["stealth"]["humanize"]["enabled"] = False
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            B.browser_click(rt.registry, "browser_click", {"selector": "button"})
            page = B._SESSION["page"]
            assert page.mouse.clicks == []
            assert ("click", "button") in page.calls
    finally:
        uninstall_fake_playwright()


def test_char_delay_and_pause_are_random_within_range():
    hum = {"enabled": True, "typing_delay_ms": [40, 130], "pause_ms": [0, 0],
           "mouse_steps": [8, 25]}
    vals = {S.char_delay(hum) for _ in range(50)}
    assert len(vals) > 1, "keystroke delay is constant — machine cadence"
    # Seconds, not milliseconds: 40-130ms == 0.04-0.13s.
    assert all(0.04 <= v <= 0.13 for v in vals), \
        "char_delay must return seconds; values look like milliseconds"


def test_typing_with_default_delays_is_fast():
    """Guards the units bug: ms config must not become seconds of sleeping.

    A 10-character password at the default 40-130ms/keystroke is ~0.9s. If
    this ever takes minutes, someone divided (or failed to divide) by 1000.
    """
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            hum = rt.cfg["browser"]["stealth"]["humanize"]
            hum["pause_ms"] = [0, 0]
            # Check the unit before using it, so a regression fails here in
            # milliseconds instead of hanging for 10 x 85 seconds.
            assert S.char_delay(hum) < 1.0, "char_delay is in ms, not seconds"
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            start = time.time()
            out = B.browser_type(rt.registry, "browser_type",
                                 {"selector": "#pwd", "text": "0123456789"})
            elapsed = time.time() - start
            assert out.startswith("ok:"), out
            assert elapsed < 5.0, f"typing 10 chars took {elapsed:.1f}s — unit bug"
    finally:
        uninstall_fake_playwright()


def test_pause_disabled_skips_sleep():
    with mock.patch("time.sleep") as slp:
        S.human_pause({"enabled": True, "pause_ms": [0, 0]})
        assert slp.called
        slp.reset_mock()
        S.human_pause({"enabled": False})
        assert not slp.called


# ---------------------------------------------------------------------------
# The verification tool
# ---------------------------------------------------------------------------
def test_stealth_check_reports_clean_when_hardened():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            out = B.browser_stealth_check(rt.registry, "browser_stealth_check", {})
            assert "CLEAN" in out, out
            assert "FAIL" not in out, out
    finally:
        uninstall_fake_playwright()


def test_stealth_check_detects_a_leaking_browser():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            rt.cfg["browser"]["stealth"]["enabled"] = False
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            out = B.browser_stealth_check(rt.registry, "browser_stealth_check", {})
            assert "LEAKING" in out or "FAIL" in out, out
            assert "navigator.webdriver" in out
    finally:
        uninstall_fake_playwright()


def test_score_probe_flags_known_bad_values():
    checks, passed, total = S.score_probe({
        "userAgent": "Mozilla/5.0 HeadlessChrome/131", "headlessUA": True,
        "webdriver": True, "webdriverIn": True, "chrome": False, "plugins": 0,
        "languages": [], "hardwareConcurrency": 1, "deviceMemory": None,
        "outerMinusInner": 0, "cdc": 3, "permToStringNative": False,
        "platformConsistent": False, "webglSoftware": True, "webglRenderer": "SwiftShader",
    })
    assert total >= 13
    assert passed <= 1, f"a stock headless browser scored {passed}/{total}"


def test_score_probe_accepts_a_clean_report():
    checks, passed, total = S.score_probe({
        "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0",
        "headlessUA": False, "webdriver": None, "webdriverIn": False,
        "chrome": True, "plugins": 5, "languages": ["en-US", "en"],
        "hardwareConcurrency": 8, "deviceMemory": 8, "outerMinusInner": 85,
        "cdc": 0, "permToStringNative": True, "platformConsistent": True,
        "webglSoftware": False, "webglRenderer": "ANGLE (Intel, ...)",
    })
    assert passed == total, [c for c in checks if not c[1]]


def test_stealth_check_is_audited_and_low_risk():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            B.browser_open(rt.registry, "browser_open", {"url": "https://example.com"})
            B.browser_stealth_check(rt.registry, "browser_stealth_check", {})
            with open(rt.cfg["audit"]["path"]) as fh:
                assert "browser_stealth_check" in fh.read()
    finally:
        uninstall_fake_playwright()


def test_stealth_check_available_with_network_off():
    """A local integrity probe must not need the network."""
    with tempfile.TemporaryDirectory() as d:
        cfg = make_cfg(d)
        cfg["policy"]["network"]["enabled"] = False
        rt = Runtime(cfg)
        res = rt.registry.dispatch("browser_stealth_check", {})
        # No browser open, so it errors — but it must NOT be DENIED by policy.
        assert "DENIED" not in res.output


# ---------------------------------------------------------------------------
# robots.txt + rate limiting
# ---------------------------------------------------------------------------
def test_robots_disallow_is_respected():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            rt.cfg["browser"]["polite"]["min_delay_ms"] = 0
            body = "User-agent: *\nDisallow: /private/\n"
            with mock.patch("urllib.request.urlopen",
                            mock.mock_open(read_data=body.encode())):
                ok, why = B._robots_allows("https://example.com/private/x",
                                           rt.cfg["browser"]["polite"])
                assert not ok and "disallowed" in why
                ok2, why2 = B._robots_allows("https://example.com/public/x",
                                             rt.cfg["browser"]["polite"])
                assert ok2
    finally:
        uninstall_fake_playwright()


def test_robots_disabled_allows_everything():
    polite = {"enabled": False, "robots_txt": False}
    assert B._robots_allows("https://example.com/private", polite)[0] is True
    polite2 = {"enabled": True, "robots_txt": False}
    assert B._robots_allows("https://example.com/private", polite2)[0] is True


def test_missing_robots_txt_fails_open():
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 404, "nope", None, None)

    with mock.patch("urllib.request.urlopen", boom):
        ok, why = B._robots_allows("https://example.com/anything",
                                   {"enabled": True, "robots_txt": True})
        assert ok and "no robots.txt" in why


def test_browser_open_refuses_disallowed_path():
    install_fake_playwright()
    try:
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            rt.cfg["browser"]["polite"]["min_delay_ms"] = 0
            with mock.patch("urllib.request.urlopen",
                            mock.mock_open(read_data=b"User-agent: *\nDisallow: /\n")):
                out = B.browser_open(rt.registry, "browser_open",
                                     {"url": "https://example.com/anything"})
                assert out.startswith("ERROR:") and "disallowed" in out
    finally:
        uninstall_fake_playwright()


def test_rate_limit_blocks_over_the_ceiling():
    polite = {"enabled": True, "max_requests_per_minute": 2, "min_delay_ms": 0}
    B._HITS.clear()
    assert B._rate_limit("a.example", polite) is None
    assert B._rate_limit("a.example", polite) is None
    blocked = B._rate_limit("a.example", polite)
    assert blocked and "rate limit" in blocked
    # A different host has its own budget.
    assert B._rate_limit("b.example", polite) is None


def test_rate_limit_disabled():
    B._HITS.clear()
    polite = {"enabled": False, "max_requests_per_minute": 1, "min_delay_ms": 0}
    for _ in range(5):
        assert B._rate_limit("c.example", polite) is None


def test_polite_defaults_are_on():
    cfg = default_config()
    assert cfg["browser"]["polite"]["enabled"] is True
    assert cfg["browser"]["polite"]["robots_txt"] is True
    assert cfg["browser"]["polite"]["max_requests_per_minute"] == 30
    assert cfg["browser"]["stealth"]["enabled"] is True


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
