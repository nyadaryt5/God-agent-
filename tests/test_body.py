"""Tests for the God-Agent body: 100 specialists compressed into 6 super-agents.

The body is a pure compression of the existing roster (no new agents, none
removed), so these tests assert exact coverage and that the swarm routes to 6
parts. They don't need the agent SDK or a provider.
"""
import os
import sys

import pytest

# The evolution pipeline runs `pytest` (console script) in a candidate tree,
# which does not add the repo root to sys.path. Add it so `import god_agent`
# resolves under both `python -m pytest` and the `pytest` console script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from god_agent import body as body_mod  # noqa: E402


def test_body_has_exactly_six_parts():
    parts = body_mod.build_body()
    assert len(parts) == 6
    names = [p["name"] for p in parts]
    assert names == ["Brain", "LeftHand", "RightHand", "LeftLeg", "RightLeg", "Torso"]


def test_body_identity_is_humanoid():
    ident = body_mod.body_identity()
    for token in ("Brain", "Hands", "Legs", "Torso"):
        assert token in ident


def test_body_compresses_all_specialists_without_loss_or_dupes():
    cov = body_mod.coverage()
    assert cov["catalog"] == 100
    assert cov["folded"] == 100
    assert cov["missing"] == []
    assert cov["duplicates"] == []


def test_every_body_part_has_valid_tools_and_members():
    from god_agent.config import default_config
    from god_agent.runtime import Runtime
    rt = Runtime(default_config())
    valid = {s["name"] for s in rt.registry.specs()}

    for p in body_mod.build_body():
        assert p["name"]
        assert p["handoff"]
        assert p["instructions"]
        assert p["members"], f"{p['name']} has no specialists"
        assert set(p["tools"]) <= valid, f"{p['name']} tools not in registry"
        assert set(p["tools"]), f"{p['name']} has no tools"


def test_build_catalog_still_lists_full_roster():
    from god_agent.catalog import build_catalog
    from god_agent.config import default_config
    # The roster (portfolio) is preserved even though the body compresses it.
    cat = build_catalog(default_config())
    assert len(cat) == 100
    assert len({r["name"] for r in cat}) == 100


def test_swarm_extracts_only_builtin_portfolio_for_extras():
    """The built-in 100 are folded into the body; only operator-drop-ins are extras."""
    import god_agent.swarm as swarm_mod
    builtin = swarm_mod._builtin_names()
    assert len(builtin) == 100  # the whole roster is built-in and compressed
    # Body parts' member names are all built-in; the union equals the roster.
    members = [m for p in body_mod.build_body() for m in p["members"]]
    assert set(members) == builtin
