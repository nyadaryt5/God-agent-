"""Tests for web search, vision, image generation, and speech.

These are the capabilities a general agent has that a shell does not. The
tests are hermetic: HTTP is mocked, no API key is needed, and the DuckDuckGo
parser is fed a static HTML fixture rather than hitting the network.
"""
import base64
import json
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

from god_agent.config import default_config  # noqa: E402
from god_agent.policy import Policy, TOOL_RISK, NETWORK_TOOLS  # noqa: E402
from god_agent.runtime import Runtime  # noqa: E402
from god_agent.tools import media as M  # noqa: E402
from god_agent.tools import search as S  # noqa: E402

from test_browser import make_cfg, make_rt  # noqa: E402

NEW_TOOLS = ["web_search", "image_analyze", "image_generate", "speak"]


# ---------------------------------------------------------------------------
# Registration / policy
# ---------------------------------------------------------------------------
def test_new_tools_registered():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        names = {t.name for t in rt.registry.all()}
        for tool in NEW_TOOLS:
            assert tool in names, f"{tool} not registered"


def test_new_tools_are_risk_graded():
    for tool in NEW_TOOLS:
        assert tool in TOOL_RISK, f"{tool} is ungraded"


def test_new_tools_are_network_gated():
    for tool in NEW_TOOLS:
        assert tool in NETWORK_TOOLS, f"{tool} escapes the network gate"
    cfg = default_config()
    cfg["policy"]["network"]["enabled"] = False
    p = Policy(cfg)
    for tool in NEW_TOOLS:
        assert not p.assess(tool, {}).allowed, f"{tool} survives network.enabled=false"


def test_dispatch_respects_network_kill_switch():
    with tempfile.TemporaryDirectory() as d:
        cfg = make_cfg(d)
        cfg["policy"]["network"]["enabled"] = False
        rt = Runtime(cfg)
        res = rt.registry.dispatch("web_search", {"query": "nginx 502"})
        assert not res.ok and "DENIED" in res.output


def test_capabilities_reflected_in_self_model():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        caps = rt.self_model.data.get("capabilities", {})
        assert "search" in caps and "vision" in caps


# ---------------------------------------------------------------------------
# Search: the DuckDuckGo HTML parser
# ---------------------------------------------------------------------------
DDG_HTML = """
<html><body>
<div class="result results_links">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fnginx&amp;rut=deadbeef">Nginx 502 docs</a>
  <div class="result__snippet">Upstream sent too big header while reading response.</div>
</div>
<div class="result results_links">
  <a rel="nofollow" class="result__a" href="https://direct.example.org/page">Direct link</a>
  <div class="result__snippet">A snippet with <b>markup</b>.</div>
</div>
</body></html>
"""


def test_ddg_parser_extracts_results():
    p = S._DDGParser()
    p.feed(DDG_HTML)
    assert len(p.results) == 2
    first = p.results[0]
    assert first["title"] == "Nginx 502 docs"
    # The redirect wrapper must be unwrapped to the real destination.
    assert first["url"] == "https://example.com/nginx"
    assert "too big header" in first["snippet"]
    # A plain href passes through untouched.
    assert p.results[1]["url"] == "https://direct.example.org/page"


def test_clean_ddg_url_handles_plain_and_wrapped():
    assert S._clean_ddg_url("") == ""
    assert S._clean_ddg_url("https://x.test/a") == "https://x.test/a"
    assert S._clean_ddg_url(
        "//duckduckgo.com/l/?uddg=https%3A%2F%2Fx.test%2Fp") == "https://x.test/p"


def test_duckduckgo_backend_uses_parser_and_browser_ua():
    with mock.patch.object(S, "_http_get", return_value=DDG_HTML) as g:
        out = S.search("nginx 502", {"max_results": 5, "timeout_s": 5})
    assert len(out) == 2
    assert out[0]["title"] == "Nginx 502 docs"
    kwargs = g.call_args.kwargs
    assert "html.duckduckgo.com" in g.call_args.args[0]
    # DuckDuckGo blocks python-urllib's default agent, so a browser UA is sent.
    assert "Mozilla/5.0" in kwargs["headers"]["User-Agent"]


def test_results_capped_at_max_results():
    with mock.patch.object(S, "_http_get", return_value=DDG_HTML):
        out = S.search("x", {"max_results": 1, "timeout_s": 5})
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Search: the tool surface
# ---------------------------------------------------------------------------
def test_web_search_requires_query():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        assert S.web_search(rt.registry, "web_search", {}).startswith("ERROR:")


def test_web_search_formats_results():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        rt.cfg["search"]["backend"] = "mock"
        out = S.web_search(rt.registry, "web_search", {"query": "nginx 502"})
        assert "2 result(s)" in out
        assert "[1]" in out and "[2]" in out
        assert "https://example.com/1" in out


def test_web_search_audited():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        rt.cfg["search"]["backend"] = "mock"
        S.web_search(rt.registry, "web_search", {"query": "disk full"})
        with open(rt.cfg["audit"]["path"]) as fh:
            blob = fh.read()
        assert "web_search" in blob
        assert "disk full" in blob  # the query itself is recorded


def test_web_search_disabled():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        rt.cfg["search"]["enabled"] = False
        out = S.web_search(rt.registry, "web_search", {"query": "x"})
        assert "disabled" in out


def test_web_search_reports_zero_results():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        with mock.patch.object(S, "search", return_value=[]):
            out = S.web_search(rt.registry, "web_search", {"query": "obscure"})
        assert "no results" in out


def test_key_backends_demand_credentials():
    for backend in ("brave", "tavily"):
        with tempfile.TemporaryDirectory() as d:
            rt = make_rt(d)
            rt.cfg["search"] = {"backend": backend, "api_key": "", "enabled": True,
                                "max_results": 5, "timeout_s": 5}
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("BRAVE_API_KEY", None)
                os.environ.pop("TAVILY_API_KEY", None)
                out = S.web_search(rt.registry, "web_search", {"query": "x"})
            assert out.startswith("ERROR:") and "api_key" in out, out


def test_searxng_needs_base_url():
    conf = {"backend": "searxng", "base_url": "", "max_results": 5, "timeout_s": 5}
    try:
        S.search("x", conf)
        raise AssertionError("should have raised")
    except S.SearchError as e:
        assert "base_url" in str(e)


def test_unknown_backend_is_rejected():
    try:
        S.search("x", {"backend": "nope", "max_results": 5, "timeout_s": 5})
        raise AssertionError("should have raised")
    except S.SearchError as e:
        assert "unknown search backend" in str(e)


def test_search_available_reflects_credentials():
    assert S.search_available({"backend": "duckduckgo"}) is True
    assert S.search_available({"backend": "mock"}) is True
    assert S.search_available({"backend": "brave", "api_key": ""}) is False
    assert S.search_available({"backend": "brave", "api_key": "k"}) is True
    assert S.search_available({"backend": "searxng", "base_url": ""}) is False


def test_snippets_are_truncated():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        rt.cfg["search"]["max_snippet_chars"] = 10
        with mock.patch.object(S, "search", return_value=[
                {"title": "t", "url": "https://x", "snippet": "y" * 200}]):
            out = S.web_search(rt.registry, "web_search", {"query": "x"})
        assert "y" * 10 + "..." in out
        assert "y" * 50 not in out


# ---------------------------------------------------------------------------
# Media: helpers
# ---------------------------------------------------------------------------
def _png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/wD/9p0AAAAASUVORK5CYII="
    )


def _write_png(path: str) -> str:
    with open(path, "wb") as fh:
        fh.write(_png_bytes())
    return path


# ---------------------------------------------------------------------------
# Media: vision
# ---------------------------------------------------------------------------
def test_image_analyze_requires_existing_file():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        out = M.image_analyze(rt.registry, "image_analyze",
                              {"path": os.path.join(d, "nope.png")})
        assert out.startswith("ERROR:") and "no such file" in out


def test_image_analyze_enforces_size_cap():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        rt.cfg["media"]["max_image_mb"] = 1
        big = os.path.join(d, "big.png")
        with open(big, "wb") as fh:
            fh.write(b"\x89PNG" + b"0" * (2 * 1024 * 1024))
        out = M.image_analyze(rt.registry, "image_analyze", {"path": big})
        assert "over the" in out and "cap" in out


def test_image_analyze_embeds_image_and_returns_text():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        png = _write_png(os.path.join(d, "shot.png"))
        captured = {}

        def fake_post(conf, path, payload):
            captured["path"] = path
            captured["payload"] = payload
            return json.dumps({"choices": [{"message": {"content": "A login form."}}]}).encode()

        with mock.patch.object(M, "_post", fake_post):
            out = M.image_analyze(rt.registry, "image_analyze",
                                  {"path": png, "question": "What is this?"})

        assert out == "A login form."
        assert captured["path"] == "/chat/completions"
        content = captured["payload"]["messages"][0]["content"]
        assert content[0]["text"] == "What is this?"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_image_analyze_reports_unsupported_provider():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        png = _write_png(os.path.join(d, "shot.png"))
        with mock.patch.object(M, "_post", side_effect=M.MediaError(
                "this provider does not implement /chat/completions (HTTP 404)")):
            out = M.image_analyze(rt.registry, "image_analyze", {"path": png})
        assert out.startswith("ERROR:") and "does not implement" in out


def test_image_analyze_audited():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        png = _write_png(os.path.join(d, "shot.png"))
        with mock.patch.object(M, "_post", return_value=json.dumps(
                {"choices": [{"message": {"content": "ok"}}]}).encode()):
            M.image_analyze(rt.registry, "image_analyze", {"path": png})
        with open(rt.cfg["audit"]["path"]) as fh:
            assert "image_analyze" in fh.read()


# ---------------------------------------------------------------------------
# Media: image generation
# ---------------------------------------------------------------------------
def test_image_generate_saves_b64_payload():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        rt.cfg["media"]["output_dir"] = os.path.join(d, "media")
        png = _png_bytes()
        with mock.patch.object(M, "_post", return_value=json.dumps(
                {"data": [{"b64_json": base64.b64encode(png).decode()}]}).encode()):
            out = M.image_generate(rt.registry, "image_generate",
                                   {"prompt": "a network diagram"})
        assert out.startswith("ok: saved")
        saved = out.split("ok: saved ")[1].split("\n")[0]
        assert os.path.isfile(saved)
        with open(saved, "rb") as fh:
            assert fh.read() == png


def test_image_generate_downloads_url_payload():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        rt.cfg["media"]["output_dir"] = os.path.join(d, "media")
        png = _png_bytes()
        with mock.patch.object(M, "_post", return_value=json.dumps(
                {"data": [{"url": "https://example.com/x.png"}]}).encode()):
            with mock.patch.object(M.urllib.request, "urlopen",
                                   mock.mock_open(read_data=png)):
                out = M.image_generate(rt.registry, "image_generate",
                                       {"prompt": "diagram"})
        assert out.startswith("ok: saved")
        assert os.path.isfile(out.split("ok: saved ")[1].split("\n")[0])


def test_image_generate_rejects_empty_payload():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        with mock.patch.object(M, "_post", return_value=json.dumps({"data": [{}]}).encode()):
            out = M.image_generate(rt.registry, "image_generate", {"prompt": "x"})
        assert out.startswith("ERROR:") and "neither b64_json nor url" in out


def test_image_generate_requires_prompt():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        assert M.image_generate(rt.registry, "image_generate", {}).startswith("ERROR:")


# ---------------------------------------------------------------------------
# Media: speech
# ---------------------------------------------------------------------------
def test_speak_saves_audio():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        rt.cfg["media"]["output_dir"] = os.path.join(d, "media")
        captured = {}

        def fake_post(conf, path, payload):
            captured.update(path=path, payload=payload)
            return b"ID3fake-audio"

        with mock.patch.object(M, "_post", fake_post):
            out = M.speak(rt.registry, "speak", {"text": "disk critical on db-01"})
        assert out.startswith("ok: saved")
        saved = out.split("ok: saved ")[1].split(" ")[0]
        assert os.path.isfile(saved)
        assert saved.endswith(".mp3")
        assert captured["path"] == "/audio/speech"
        assert captured["payload"]["input"] == "disk critical on db-01"
        assert captured["payload"]["voice"] == "alloy"


def test_speak_rejects_bad_format():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        out = M.speak(rt.registry, "speak", {"text": "hi", "format": "exe"})
        assert out.startswith("ERROR:") and "format must be" in out


def test_speak_requires_text():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        assert M.speak(rt.registry, "speak", {}).startswith("ERROR:")


def test_speak_plays_when_asked():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        rt.cfg["media"]["output_dir"] = os.path.join(d, "media")
        with mock.patch.object(M, "_post", return_value=b"audio"):
            with mock.patch.object(M, "_play", return_value="played via paplay") as pl:
                out = M.speak(rt.registry, "speak", {"text": "alert", "play": True})
        assert pl.called
        assert "played via paplay" in out


def test_speak_does_not_play_by_default():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        rt.cfg["media"]["output_dir"] = os.path.join(d, "media")
        with mock.patch.object(M, "_post", return_value=b"audio"):
            with mock.patch.object(M, "_play") as pl:
                M.speak(rt.registry, "speak", {"text": "alert"})
        assert not pl.called


def test_play_reports_missing_player():
    with mock.patch.object(M.shutil, "which", return_value=None):
        assert "no audio player" in M._play("/tmp/x.mp3")


def test_media_disabled_blocks_all_tools():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        rt.cfg["media"]["enabled"] = False
        for fn, args in ((M.image_analyze, {"path": "/x.png"}),
                         (M.image_generate, {"prompt": "x"}),
                         (M.speak, {"text": "x"})):
            out = fn(rt.registry, fn.__name__, args)
            assert "disabled" in out, f"{fn.__name__}: {out}"


def test_media_output_dir_defaults_under_state_root():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        conf = M._cfg(rt)
        assert conf["output_dir"].startswith(d)
        assert conf["output_dir"].endswith("media")


def test_media_inherits_llm_endpoint():
    with tempfile.TemporaryDirectory() as d:
        rt = make_rt(d)
        rt.cfg["llm"]["base_url"] = "https://llm.example/v1"
        rt.cfg["llm"]["model"] = "some-model"
        conf = M._cfg(rt)
        assert conf["base_url"] == "https://llm.example/v1"
        assert conf["vision_model"] == "some-model"


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
