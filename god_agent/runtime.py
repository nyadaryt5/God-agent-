"""Runtime: the shared context injected into every tool call.

Holds config, policy, memory, self-model, audit log, executor, and the tool
registry. A ContextVar makes the current runtime available to tools without
threading it through every signature (also works in the API server threads).
"""
from __future__ import annotations

import contextvars
import os
import threading
from typing import Callable, Optional

from .audit import AuditLog
from .brain import Brain
from .config import load_config
from .executor import Executor
from .memory import MemoryStore
from .policy import Policy, Decision
from .providers import ProviderManager
from .selfmodel import SelfModel
from .tools import ToolRegistry

_current: contextvars.ContextVar[Optional["Runtime"]] = contextvars.ContextVar("goda_runtime", default=None)


def get_runtime() -> "Runtime":
    rt = _current.get()
    if rt is None:
        raise RuntimeError("no active God-Agent runtime in this context")
    return rt


class Runtime:
    def __init__(self, cfg: Optional[dict] = None, *, approver: Optional[Callable[[str, dict, Decision], bool]] = None):
        self.cfg = cfg or load_config()
        providers_path = os.path.join(self.cfg["state"]["root"], "providers.json")
        self.providers = ProviderManager(providers_path)
        self.providers.apply_to(self.cfg)
        self.memory = MemoryStore(
            self.cfg["memory"]["db_path"],
            self.cfg["memory"]["max_episodes"],
            self.cfg["memory"]["max_reflections"],
        )
        self.brain = Brain(self.cfg)
        self.dev_mode = False
        self._load_dev_mode()
        self.policy = Policy(self.cfg, dev_mode=self.dev_mode)
        self.self_model = SelfModel(self.cfg["self_model"]["path"])
        self.audit = AuditLog(self.cfg["audit"]["path"], self.cfg["audit"]["max_mb"])
        self.executor = Executor(self.cfg, approver)
        self.registry = ToolRegistry(self.policy)
        self._sync_capabilities()
        self.approver = approver
        self._thread_tokens = threading.local()
        self._activate()
        self._task_lock = threading.Lock()
        self._task_id = 0

    def _activate(self) -> None:
        """Bind this runtime to the current thread's context."""
        self._thread_tokens.token = _current.set(self)

    def rebind_thread(self) -> None:
        """Call this at the top of worker threads that will use tools."""
        self._activate()

    def set_dev_mode(self, enabled: bool) -> None:
        """Operator-only dev mode (stored in the Brain; AI cannot write it)."""
        self.brain.operator_set("developer_mode", "on" if enabled else "off",
                                kind="fact", locked=True,
                                note="developer mode (operator-controlled)")
        self.reload()
        self.audit.append("operator", "developer_mode",
                          "developer mode " + ("ENABLED" if enabled else "disabled"),
                          {"enabled": enabled})

    def _load_dev_mode(self) -> None:
        self.dev_mode = self.brain.developer_mode()

    def reload(self) -> None:
        """Re-read providers + dev mode and rebuild policy after a change."""
        self.providers.apply_to(self.cfg)
        self._load_dev_mode()
        self.policy = Policy(self.cfg, dev_mode=self.dev_mode)
        self._sync_capabilities()

    def _sync_capabilities(self) -> None:
        """Reflect current config in the self-model's capability view."""
        caps = {
            "shell": True,
            "files": True,
            "memory": True,
            "network": bool(self.cfg["policy"]["network"].get("enabled", True)),
            "brain": True,
            "gpu_hardware": True,
            "process_control": True,
            "kernel_tuning": True,
            "scheduling": True,
            "self_model": True,
            "evolution": bool(self.cfg["policy"]["evolution"].get("enabled", True)),
            "native": self.cfg["policy"].get("sandbox", "none") == "none",
            "developer_mode": self.dev_mode,
        }
        self.self_model.apply_update({"capabilities": caps},
                                     note="synced from config")

    # ------------------------------------------------------------------
    def request_approval(self, name: str, args: dict, decision: Decision) -> bool:
        if self.approver is not None:
            return bool(self.approver(name, args, decision))
        return False  # SAFE DEFAULT: deny when nobody is listening

    def record(self, action: str, summary: str, details: Optional[dict] = None,
               actor: str = "goda", outcome: str = "ok") -> None:
        self.audit.append(actor, action, summary, details, outcome)

    def next_task_id(self) -> int:
        with self._task_lock:
            self._task_id += 1
            return self._task_id

    def close(self) -> None:
        if hasattr(self._thread_tokens, "token"):
            _current.reset(self._thread_tokens.token)
        self.memory.close()
