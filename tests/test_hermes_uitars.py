"""Tests for the Hermes Agent and UI-TARS engine adapters.

These engines are genuine, installed-only extras (like crewai/langgraph). The
pure-logic tests (engine-name registry, availability probes) run always; the ones
that need `ui_tars` / `hermes-agent` are guarded with importorskip so a clean CI
without the optional packages still passes.
"""
import pytest

from god_agent import engines


def _runtime(overrides=None):
    from god_agent.config import default_config
    from god_agent.runtime import Runtime

    cfg = default_config()
    cfg["llm"]["api_key"] = "test-fake-key"
    cfg["llm"]["api_key_env"] = ""  # never read ambient env in unit tests
    if overrides:
        cfg.update(overrides)
    return Runtime(cfg)


def test_hermes_and_uitars_are_registered_engines():
    names = engines.engine_names()
    assert "hermes" in names
    assert "ui_tars" in names
    assert "grok" in names


def test_engine_probes_return_bool():
    # Probes must not raise for any registered engine name.
    for name in engines.engine_names():
        assert isinstance(engines.is_available(name), bool)


def test_catalog_lists_new_engines():
    from god_agent.catalog import available_engines

    avail = available_engines()
    assert isinstance(avail.get("hermes"), bool)
    assert isinstance(avail.get("ui_tars"), bool)
    assert isinstance(avail.get("grok"), bool)


def test_grok_adapter_errors_cleanly_when_not_installed(monkeypatch):
    # Force "not installed" and ensure _run_grok raises a clear RuntimeError
    # (mirrors smolagents/autogen: detected-but-not-installed engines fail fast).
    monkeypatch.setattr(engines, "is_available", lambda name: False)
    rt = _runtime()
    with pytest.raises(RuntimeError, match="grok"):
        engines.run_engine("grok", rt, "report system status")


def test_uitars_adapter_errors_cleanly_when_not_runnable():
    # With no desktop/pyautogui (or no ui_tars package) the adapter must raise a
    # clear RuntimeError rather than silently doing nothing.
    rt = _runtime()
    with pytest.raises(RuntimeError):
        engines.run_engine("ui_tars", rt, "open the terminal")


def test_hermes_adapter_errors_cleanly_when_not_installed(monkeypatch):
    # Force "not installed" and ensure _run_hermes raises a clear RuntimeError.
    monkeypatch.setattr(engines, "is_available", lambda name: False)
    rt = _runtime()
    with pytest.raises(RuntimeError, match="hermes"):
        engines.run_engine("hermes", rt, "report system status")


@pytest.mark.skipif(not engines.is_available("ui_tars"), reason="ui-tars not installed")
def test_uitars_code_generation_pure():
    # The parse -> pyautogui code step is side-effect free and unit-testable
    # without any display or model.
    from god_agent.engines import _ui_tars_code

    sample = (
        "Thought: I need to open the terminal to inspect status.\n"
        "Action: click(point='<point>100 120</point>')"
    )
    code = _ui_tars_code(sample, 1280, 720)
    assert "import pyautogui" in code
    assert "pyautogui.click" in code
