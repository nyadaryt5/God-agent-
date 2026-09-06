"""Tool registry.

A Tool is a thin, typed wrapper the model can call. Every tool goes through
the Policy engine before execution; the runtime injects approvals, memory,
audit, and the self-model into each tool call.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..policy import Policy, TOOL_RISK, format_decision

ToolFn = Callable[..., str]


@dataclass
class Tool:
    name: str
    description: str
    args_schema: dict
    risk: int = 3
    fn: Optional[ToolFn] = None

    def spec(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "args": self.args_schema,
            "risk": self.risk,
        }


@dataclass
class ToolResult:
    ok: bool
    output: str
    meta: dict = field(default_factory=dict)


class ToolRegistry:
    def __init__(self, policy: Policy):
        self.policy = policy
        self._tools: dict[str, Tool] = {}
        self._register_all()

    def _add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]

    def _register_all(self) -> None:
        from . import brain, files, memory, network, selfmodel, shell, system
        from ..evolution import evolve

        TOOL_META = {
            files.read_file: ("Read a file (bounded output).",
                              {"path": {"type": "string", "required": True}}),
            files.list_dir: ("List a directory with optional pattern filter.",
                             {"path": {"type": "string", "required": True},
                              "pattern": {"type": "string", "default": "*"}}),
            files.file_search: ("Recursively find files by name pattern.",
                                {"path": {"type": "string", "required": True},
                                 "pattern": {"type": "string", "required": True},
                                 "max_depth": {"type": "integer", "default": 3}}),
            files.write_file: ("Write text to a file (policy-checked).",
                               {"path": {"type": "string", "required": True},
                                "content": {"type": "string", "required": True},
                                "mode": {"type": "string", "enum": ["w", "a"], "default": "w"}}),
            shell.shell_exec: ("Run a shell command natively with no artificial limits.",
                               {"command": {"type": "string", "required": True},
                                "timeout": {"type": "integer"},
                                "cwd": {"type": "string"}}),
            system.system_info: ("Collect host inventory: OS, kernel, CPU, memory, disk, load, processes, GPUs.",
                                 {}),
            system.hardware_info: ("Full hardware inventory: CPU model/cores, RAM, GPU, disks, PCI, sensors.",
                                   {}),
            system.gpu_info: ("Live GPU status: model, memory, utilization, temperature, processes.",
                              {}),
            system.service_action: ("Start/stop/restart/status a systemd service.",
                                    {"action": {"type": "string", "enum": ["status", "start", "stop",
                                                                            "restart", "reload", "enable", "disable"]},
                                     "service": {"type": "string", "required": True}}),
            system.package_install: ("Install packages with the distro package manager.",
                                     {"packages": {"type": "array", "required": True}}),
            system.process_control: ("Native process control: list/kill/nice/renice/affinity.",
                                     {"action": {"type": "string", "enum": ["list", "kill", "nice",
                                                                             "renice", "affinity"]},
                                      "target": {"type": "string"},
                                      "value": {"type": "string"}}),
            system.sysctl_set: ("Apply kernel parameters natively (sysctl -w).",
                                {"key": {"type": "string", "required": True},
                                 "value": {"type": "string", "required": True},
                                 "persist": {"type": "boolean", "default": False}}),
            system.system_tune: ("Tune CPU governor / IO scheduler / hugepages for max performance.",
                                 {"governor": {"type": "string"},
                                  "io_scheduler": {"type": "string"},
                                  "transparent_hugepage": {"type": "string"}}),
            system.schedule_job: ("Manage cron jobs natively: add/remove/list.",
                                  {"action": {"type": "string", "enum": ["add", "remove", "list"]},
                                   "schedule": {"type": "string"},
                                   "command": {"type": "string"},
                                   "comment": {"type": "string"}}),
            memory.search_memory: ("Search past task episodes and reflections.",
                                   {"query": {"type": "string", "required": True},
                                    "limit": {"type": "integer", "default": 5}}),
            memory.remember: ("Store an explicit lesson/fact for future recall.",
                              {"summary": {"type": "string", "required": True},
                               "task": {"type": "string"}, "outcome": {"type": "string"}}),
            memory.reflect: ("Store direct operator feedback.",
                             {"reflection": {"type": "string", "required": True}}),
            brain.brain_read: ("Read a Brain entry (credentials decrypted for use).",
                               {"key": {"type": "string", "required": True}}),
            brain.brain_search: ("Search the Brain by text.",
                                 {"query": {"type": "string", "required": True}}),
            brain.brain_list: ("List Brain entries (metadata only, never values).",
                               {"kind": {"type": "string"}}),
            brain.brain_write: ("AI auto-write: store something important you learned. "
                                "Cannot touch operator/locked entries.",
                                {"key": {"type": "string", "required": True},
                                 "value": {"type": "string", "required": True},
                                 "kind": {"type": "string", "enum": ["learning", "fact", "prompt", "credential"]},
                                 "importance": {"type": "string"}}),
            selfmodel.read_self: ("Read your identity, capabilities, stats, and boundaries.", {}),
            selfmodel.update_self: ("Update your self-reported capabilities/learnings.",
                                    {"patch": {"type": "object", "required": True},
                                     "note": {"type": "string"}}),
            network.fetch_url: ("Fetch a URL (GET, size-bounded; network must be enabled).",
                                {"url": {"type": "string", "required": True}}),
            evolve: ("Evolve yourself: propose validated, tested changes to your own source.",
                     {"proposal": {"type": "object", "required": True}}),
        }

        for fn, (desc, schema) in TOOL_META.items():
            risk = TOOL_RISK.get(fn.__name__, 3)
            self._add(Tool(name=fn.__name__, description=desc, args_schema=schema,
                           risk=risk, fn=fn))

    # ------------------------------------------------------------------
    def dispatch(self, name: str, args: dict) -> ToolResult:
        """Policy check + execution. Returns a ToolResult matching policy."""
        tool = self.get(name)
        if tool is None:
            return ToolResult(False, f"unknown tool: {name}")

        decision = self.policy.assess(name, args or {})
        if not decision.allowed:
            return ToolResult(False, f"DENIED: {format_decision(decision, name, args or {})}")

        if decision.human_required:
            # Rating was "ask": route through the runtime's approval broker.
            from ..runtime import get_runtime
            rt = get_runtime()
            ok = rt.request_approval(name, args, decision)
            if not ok:
                return ToolResult(False, f"APPROVAL DENIED by operator: {format_decision(decision, name, args or {})}")

        try:
            if tool.fn is None:
                return ToolResult(False, "tool not implemented")
            output = tool.fn(self, name, args or {})
            return ToolResult(True, output)
        except Exception as e:  # tools must never crash the loop
            return ToolResult(False, f"tool error: {type(e).__name__}: {e}")
