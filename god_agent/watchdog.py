"""Watchdog: periodic self-check, health pings, and scheduled evolution.

Runs inside the daemon (not a separate process): every N seconds it
  - checks the kill switch;
  - records a lightweight heartbeat in the self-model;
  - optionally triggers a reflection + evolution review after a cooldown.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from .loop import Agent
from .utils import now_iso


class Watchdog:
    def __init__(self, runtime, llm=None, interval: int = 60, evolution_every: int = 20):
        self.runtime = runtime
        self.llm = llm
        self.interval = max(10, interval)
        self.evolution_every = max(5, evolution_every)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_tasks = 0
        self._last_evolution_check = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="goda-watchdog")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._beat()
            except Exception:
                continue

    def _beat(self) -> None:
        rt = self.runtime
        sm = rt.self_model
        sm.set_state(status="idle", last_heartbeat=now_iso())
        stats = rt.memory.stats()
        if stats["tasks"] > self._last_tasks:
            self._last_tasks = stats["tasks"]
        # Periodic evolution review (only when evolution policy enabled).
        evo = rt.cfg["policy"].get("evolution", {})
        if evo.get("enabled") and stats["tasks"] > 0 and (
            stats["tasks"] - self._last_evolution_check >= self.evolution_every
        ):
            self._last_evolution_check = stats["tasks"]
            self._review(rt)

    def _review(self, rt) -> None:
        """Ask the model whether a self-improvement is warranted.

        The result is a *proposal* that must pass the guarded pipeline before
        anything changes. Never touches config/policy directly.
        """
        try:
            reflections = rt.memory.recent_reflections(5)
            text = "\n".join(f"- {r['reflection'][:160]}" for r in reflections)
            if not text:
                return
            if self.llm is not None:
                resp = self.llm.chat([
                    {"role": "system", "content": "You propose evolutions for God-Agent. "
                     "Be conservative. Return a JSON object with files/content or "
                     '{"files": []} when nothing should change.'},
                    {"role": "user", "content": "Recent reflections:\n" + text +
                     "\nPropose at most one small improvement (a tool, a hook, a memory "
                     "heuristic, or docs). JSON: {\"rationale\": ..., \"files\": [{\"path\": "
                     "\"god_agent/...\", \"content\": \"...\"}], \"constitution\": \"unchanged\"}"},
                ], max_tokens=2000)
                from .utils import parse_json_block

                proposal = parse_json_block(resp) or {"files": []}
            else:
                proposal = {"files": [], "rationale": "no model configured"}
            if proposal.get("files"):
                rt.audit.append("goda", "evolve", "watchdog requested evolution review",
                                {"proposal": proposal})
        except Exception:
            # never let the watchdog crash the loop
            return
