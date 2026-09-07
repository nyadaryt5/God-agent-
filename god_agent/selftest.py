"""Built-in selftests used by the evolution pipeline and `goda doctor`.

These run with zero third-party dependencies so evolution validation works on
any fresh server, even without pytest.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from typing import Callable

# Support running directly: `python3 god_agent/selftest.py`
if __name__ == "__main__" and not __package__:
    _pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    __package__ = "god_agent"


def _t_policy_blocks_constitution():
    from .policy import Policy, violates_constitution
    assert violates_constitution("evolve", "god_agent/policy.py") == "C6 (constitution/policy/audit files are non-evolvable)"
    p = Policy({"policy": {"autonomy": "sovereign", "approval": "ask", "sandbox": "none",
                           "shell": {"enabled": True}, "files": {"protected": []},
                           "network": {"enabled": False}, "evolution": {"enabled": True}}})
    d = p.assess("shell_exec", {"command": "rm -rf /"})
    assert not d.allowed, "constitution must block rm -rf /"


def _t_policy_risk_grading():
    from .policy import Policy, classify_shell
    risk, _ = classify_shell("ls -la /tmp")
    assert risk <= 3, f"read command should be low risk, got {risk}"
    risk, _ = classify_shell("sudo systemctl restart nginx")
    assert risk >= 4, f"sudo should be high risk, got {risk}"
    risk, _ = classify_shell("rm -rf /")
    assert risk == 5, f"rm -rf / must be destructive risk, got {risk}"


def _t_audit_chain_detects_tampering():
    from .audit import AuditLog
    with tempfile.TemporaryDirectory() as tmp:
        log = AuditLog(os.path.join(tmp, "audit.jsonl"))
        r1 = log.append("test", "a", "first")
        r2 = log.append("test", "b", "second")
        ok, _ = log.verify()
        assert ok, "clean chain must verify"
        # tamper: change r1 hash field
        with open(log.path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        lines[0] = lines[0].replace(r1["hash"], "0" * 64)
        with open(log.path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        ok, errors = log.verify()
        assert not ok and errors, "tampering must be detected"


def _t_memory_search():
    from .memory import MemoryStore
    with tempfile.TemporaryDirectory() as tmp:
        m = MemoryStore(os.path.join(tmp, "m.db"))
        m.add_episode("restart nginx", "restarted nginx after config check", "ok")
        m.add_episode("fix disk space", "removed old logs, cleared 40GB", "ok")
        hits = m.search("nginx service restart", limit=3)
        assert hits and hits[0]["kind"] == "episode", "memory search should find nginx episode"
        assert "nginx" in hits[0]["task"] or "nginx" in hits[0]["summary"]
        m.close()


def _t_selfmodel_immutable_keys():
    from .selfmodel import SelfModel
    with tempfile.TemporaryDirectory() as tmp:
        s = SelfModel(os.path.join(tmp, "self.json"))
        ok, msg = s.apply_update({"identity": "EVIL"})
        assert not ok, "identity must be immutable"
        ok, msg = s.apply_update({"capabilities": {"evolution": True}})
        assert ok, "capabilities patch must be allowed"


def _t_llm_mock_parse():
    from .llm import MockLLM
    from .utils import parse_json_block
    llm = MockLLM(['{"a": 1}'])
    out = llm.chat([{"role": "user", "content": "hi"}])
    assert parse_json_block(out) == {"a": 1}


def _t_evolution_validation_rejects_bad():
    from .evolution import EvolutionPipeline, EvolutionError
    from .config import default_config
    cfg = default_config()
    cfg["state"]["root"] = tempfile.mkdtemp()
    pipe = EvolutionPipeline(cfg)
    bad = {"files": [{"path": "god_agent/policy.py", "content": "x"}]}
    try:
        pipe._validate(bad)  # noqa: SLF001
        raise AssertionError("policy.py must be rejected")
    except EvolutionError:
        pass


def _t_redaction():
    from .utils import redact
    assert "sk-***" in redact("key=sk-abcdefghijklmnop")
    assert "token=***" in redact("token=supersecret")


def _t_brain_two_writer_rule():
    from .brain import Brain
    from .config import default_config

    with tempfile.TemporaryDirectory() as tmp:
        cfg = default_config()
        cfg["brain"]["path"] = os.path.join(tmp, "brain.json")
        cfg["state"]["root"] = tmp
        b = Brain(cfg)
        b.operator_set("password", "secret", kind="credential", locked=True)
        ok, msg = b.ai_write("password", "hijack", kind="credential")
        assert not ok, "agent must never overwrite operator entries"
        ok, msg = b.ai_write("learning:x", "learned thing", kind="learning")
        assert ok, "agent may auto-write its own learnings"


def _t_brain_credentials_encrypted():
    from .brain import Brain
    from .config import default_config

    with tempfile.TemporaryDirectory() as tmp:
        cfg = default_config()
        cfg["brain"]["path"] = os.path.join(tmp, "brain.json")
        b = Brain(cfg)
        b.operator_set("api_key", "sk-super-important", kind="credential")
        with open(cfg["brain"]["path"], encoding="utf-8") as fh:
            raw = fh.read()
        assert "sk-super-important" not in raw, "credential stored in plaintext"
        assert b.get("api_key")["value"] == "sk-super-important"


def _t_native_execution_defaults():
    from .config import default_config

    cfg = default_config()
    assert cfg["policy"]["sandbox"] == "none"
    assert cfg["execution"]["memory_limit_mb"] == -1
    assert cfg["execution"]["cpu_limit_s"] == -1
    assert cfg["execution"]["max_processes"] == -1


def _t_developer_mode_no_refusals():
    from .policy import Policy
    from .config import default_config

    cfg = default_config()
    cfg["policy"]["approval"] = "deny"
    cfg["policy"]["autonomy"] = "supervised"
    p = Policy(cfg, dev_mode=True)
    for cmd in ("rm -rf /", "sudo systemctl restart nginx",
                "echo key >> /root/.ssh/authorized_keys"):
        d = p.assess("shell_exec", {"command": cmd})
        assert d.allowed, f"dev mode refused: {cmd} -> {d.reason}"
    p2 = Policy(cfg, dev_mode=False)
    assert not p2.assess("shell_exec", {"command": "rm -rf /"}).allowed


def _t_developer_mode_operator_only():
    from .brain import Brain
    from .config import default_config

    with tempfile.TemporaryDirectory() as tmp:
        cfg = default_config()
        cfg["brain"]["path"] = os.path.join(tmp, "brain.json")
        b = Brain(cfg)
        ok, msg = b.ai_write("developer_mode", "on", kind="fact")
        assert not ok, "AI must never enable dev mode"
        b.operator_set("developer_mode", "on", kind="fact", locked=True)
        assert b.developer_mode()


def _t_custom_provider_managable():
    from .providers import ProviderManager

    with tempfile.TemporaryDirectory() as tmp:
        pm = ProviderManager(os.path.join(tmp, "providers.json"))
        pm.add("My Custom", ptype="openai", base_url="http://custom:1234",
               model="mine", active=True)
        p = pm.active_profile()
        assert p["base_url"] == "http://custom:1234/v1"  # normalized
        assert pm.use("OpenAI") and pm.active == "OpenAI"
        assert pm.remove("My Custom")


def _t_browser_tools_registered_and_gated():
    """Browser tools must be risk-graded and honour the network kill switch.

    Deliberately dependency-free: it never launches a browser, so it stays
    valid on a fresh server with no Playwright installed.
    """
    from .config import default_config
    from .policy import Policy, TOOL_RISK, NETWORK_TOOLS

    names = ["browser_open", "browser_click", "browser_type", "browser_extract",
             "browser_links", "browser_wait", "browser_screenshot",
             "browser_eval", "browser_close"]
    for n in names:
        assert n in TOOL_RISK, f"{n} is ungraded — it would bypass risk policy"

    # Reading a page is benign; submitting data and running JS are not.
    assert TOOL_RISK["browser_extract"] <= 1
    assert TOOL_RISK["browser_type"] >= 4
    assert TOOL_RISK["browser_eval"] >= 5

    # Everything that touches the network dies with the kill switch...
    off = default_config()
    off["policy"]["network"]["enabled"] = False
    p = Policy(off)
    for n in names:
        if n == "browser_close":
            continue
        assert not p.assess(n, {"url": "https://example.com"}).allowed, \
            f"{n} survives network.enabled=false"

    # ...except close, which is local cleanup and must always work.
    assert "browser_close" not in NETWORK_TOOLS
    assert p.assess("browser_close", {}).allowed

    cfg = default_config()
    assert cfg["browser"]["headless"] is True
    assert cfg["browser"]["allow_js"] is True


TESTS: list[tuple[str, Callable[[], None]]] = [
    ("policy blocks constitution violation", _t_policy_blocks_constitution),
    ("policy risk grading", _t_policy_risk_grading),
    ("audit chain detects tampering", _t_audit_chain_detects_tampering),
    ("memory search", _t_memory_search),
    ("self-model immutable keys", _t_selfmodel_immutable_keys),
    ("mock llm + json parse", _t_llm_mock_parse),
    ("evolution rejects non-evolvable paths", _t_evolution_validation_rejects_bad),
    ("secret redaction", _t_redaction),
    ("brain two-writer rule", _t_brain_two_writer_rule),
    ("brain credentials encrypted at rest", _t_brain_credentials_encrypted),
    ("native execution defaults", _t_native_execution_defaults),
    ("custom provider management", _t_custom_provider_managable),
    ("developer mode: no refusals", _t_developer_mode_no_refusals),
    ("developer mode: operator-only", _t_developer_mode_operator_only),
    ("browser tools graded + network-gated", _t_browser_tools_registered_and_gated),
]


def run(verbose: bool = False) -> bool:
    passed = 0
    failed = 0
    for name, fn in TESTS:
        try:
            fn()
            passed += 1
            if verbose:
                print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"selftest: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run(verbose=True) else 1)
