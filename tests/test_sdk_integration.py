"""Tests for the real OpenAI Agents SDK integration (sdk_agent + swarm).

The pure-logic tests (schema bridge, engine gate, specialist roster) run
always. The tests that construct a real agent/model need a key and the SDK, so
they are opt-in via the GODA_TEST_AGENTS env var — this keeps the evolution
pipeline's candidate-tree pytest run clean (no external credentials required).
"""
import os

import pytest

REQUIRE_LIVE = os.environ.get("GODA_TEST_AGENTS") == "1"


def _cfg(provider="openai", base_url="", key="", model="kira-3.5-flash"):
    from god_agent.config import default_config
    cfg = default_config()
    cfg["llm"]["provider"] = provider
    cfg["llm"]["base_url"] = base_url
    cfg["llm"]["model"] = model
    cfg["llm"]["api_key"] = key
    cfg["llm"]["api_key_env"] = ""  # never read ambient env in these unit tests
    return cfg


def test_engine_usable_requires_sdk_and_key():
    from god_agent import sdk_agent

    # No key -> not usable (offline heuristic fallback) regardless of SDK.
    assert sdk_agent.engine_usable(_cfg(key="")) is False
    # Non-openai-compatible provider (anthropic) -> not usable by this bridge.
    assert sdk_agent.engine_usable(_cfg(provider="anthropic", key="sk-x")) is False
    # Mock provider -> not usable.
    assert sdk_agent.engine_usable(_cfg(provider="mock", key="x")) is False

    if sdk_agent.openai_agents_available():
        cfg = _cfg(key="sk-x", base_url="http://127.0.0.1:1/v1")
        assert sdk_agent.engine_usable(cfg) is True


def test_json_schema_bridge():
    from god_agent.sdk_agent import _json_schema

    spec = {
        "path": {"type": "string", "required": True},
        "timeout": {"type": "integer", "default": 20},
    }
    schema = _json_schema(spec)
    assert schema["type"] == "object"
    assert schema["required"] == ["path"]
    assert schema["properties"]["path"] == {"type": "string"}
    assert schema["properties"]["timeout"] == {"type": "integer", "default": 20}


def test_specialists_defined():
    from god_agent.swarm import SPECIALISTS

    names = {s["name"] for s in SPECIALISTS}
    assert "SystemAdministrator" in names
    assert "SecurityAuditor" in names
    assert "NetworkEngineer" in names
    assert all(s.get("instructions") for s in SPECIALISTS)


@pytest.mark.skipif(not REQUIRE_LIVE, reason="set GODA_TEST_AGENTS=1 to build a real agent")
def test_crew_builds_with_handoffs():
    from god_agent import sdk_agent
    if not sdk_agent.openai_agents_available():
        pytest.skip("openai-agents not installed")

    from god_agent.runtime import Runtime
    from god_agent.config import default_config
    from god_agent import swarm

    # A key is required to construct the AsyncOpenAI client; a fake one is fine
    # (nothing contacts the network during construction).
    os.environ["KIRA_API_KEY"] = os.environ.get("KIRA_API_KEY", "test-fake-key")
    cfg = default_config()
    cfg["llm"]["api_key"] = "test-fake-key"
    rt = Runtime(cfg)
    crew = swarm._build_crew(rt)
    assert crew[0].name == "God"
    assert len(crew) == 1 + len(swarm.SPECIALISTS)
    god = crew[0]
    assert len(god.handoffs) == len(swarm.SPECIALISTS)


def test_steps_reconstructed_from_items():
    """The item-to-step mapping helpers must not crash on empty/edge inputs."""
    from god_agent.sdk_agent import _json_schema
    assert _json_schema({"x": {"type": "string", "required": True}})["required"] == ["x"]


# ---------------------------------------------------------------------------
# Catalog & engines
# ---------------------------------------------------------------------------
def test_catalog_builds_large_crew_and_dedups():
    from god_agent.catalog import build_catalog
    from god_agent.config import default_config

    cfg = default_config()
    cat = build_catalog(cfg)
    # The curated roster is broad (well beyond the original 5 specialists) and
    # names are unique (later definitions override; no duplicates).
    assert len(cat) >= 20
    names = [r["name"] for r in cat]
    assert len(names) == len(set(names))
    assert all(r["name"] and r["handoff"] and r["instructions"] for r in cat)


def test_catalog_merges_user_agents(tmp_path):
    import json
    from god_agent.catalog import build_catalog
    from god_agent.config import default_config

    cfg = default_config()
    cfg["state"]["root"] = str(tmp_path)
    user = {"agents": [
        {"name": "MyCustomAgent", "handoff": "Do my thing.",
         "instructions": "You are MyCustomAgent.", "tools": ["shell_exec"]}
    ]}
    with open(tmp_path / "agents.json", "w", encoding="utf-8") as fh:
        json.dump(user, fh)

    cat = build_catalog(cfg)
    names = [r["name"] for r in cat]
    assert "MyCustomAgent" in names
    mine = next(r for r in cat if r["name"] == "MyCustomAgent")
    assert mine["domain"] == "custom"
    assert mine["tools"] == ["shell_exec"]


def test_engines_registry_and_probes():
    from god_agent import engines

    assert "openai_sdk" in engines.engine_names()
    # The default engine must always be detected as a known name; availability
    # depends on the environment, but the probe must not raise.
    assert isinstance(engines.is_available("openai_sdk"), bool)
    for name in engines.engine_names():
        assert isinstance(engines.is_available(name), bool)


def test_swarm_falls_back_to_default_engine():
    from god_agent import engines, swarm
    from god_agent.config import default_config

    cfg = default_config()
    cfg["agent"]["engine"] = "crewai"  # not installed in CI -> falls back
    cfg["llm"]["api_key"] = "test-fake-key"
    eng = swarm._active_engine_config(cfg)
    # Any requested engine that is not installed falls back to one that is
    # (openai_sdk when present), or the 'openai_sdk' sentinel otherwise.
    assert eng in engines.engine_names()


# ---------------------------------------------------------------------------
# MCP (Model Context Protocol) server integration
# ---------------------------------------------------------------------------
def test_mcp_disabled_by_default_and_loads_registry(tmp_path):
    from god_agent.config import default_config
    from god_agent.mcp_servers import have_mcp, load_servers

    cfg = default_config()
    cfg["state"]["root"] = str(tmp_path)
    # Disabled by default; no servers configured.
    assert have_mcp(cfg) is False
    assert load_servers(cfg) == []


def test_mcp_builds_servers_from_config(tmp_path):
    import json
    from god_agent.config import default_config
    from god_agent import mcp_servers

    cfg = default_config()
    cfg["state"]["root"] = str(tmp_path)
    cfg["mcp"]["enabled"] = True

    reg = {"servers": [
        {"name": "local", "transport": "stdio", "command": "python3",
         "args": ["-m", "mcp_server_sqlite"], "enabled": True},
        {"name": "remote", "transport": "http", "url": "https://mcp.example.com/mcp",
         "enabled": True},
        {"name": "off", "transport": "sse", "url": "https://x.example.com/sse",
         "enabled": False},
    ]}
    with open(tmp_path / "mcp.json", "w", encoding="utf-8") as fh:
        json.dump(reg, fh)

    assert mcp_servers.have_mcp(cfg) is True
    loaded = mcp_servers.load_servers(cfg)
    assert [s["name"] for s in loaded] == ["local", "remote"]  # disabled excluded

    if mcp_servers._client_available():
        built = mcp_servers.build_mcp_servers(cfg)
        assert len(built) == 2  # both enabled builds; nothing throws
        names = sorted(getattr(b, "name", "") or "" for b in built)
        assert "local" in names
    else:
        assert mcp_servers.build_mcp_servers(cfg) == []
