"""Operational self-model.

This is the engineering analogue of "self-awareness": a structured, persistent,
introspectable representation of the agent — its identity, capabilities,
current state, statistics, recent reflections, and the boundaries it must
respect. The agent can read it, and (within limits) update its self-reported
capabilities/notes. Updating the immutable fields (schema, identity, stats
computed by the runtime) is rejected.

This is NOT consciousness. See docs/CONSCIOUSNESS.md.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from .utils import now_iso

IMMUTABLE_KEYS = {"schema_version", "identity", "version", "formed_at",
                  "stats", "boundaries", "state"}

ALLOWED_UPDATE_KEYS = {"capabilities", "learned", "notes", "current_goal"}


class SelfModel:
    def __init__(self, path: str, max_update_chars: int = 2000):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.max_update_chars = max_update_chars
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.isfile(self.path):
            self._write(self._fresh())
        self.data = self._read()

    # ------------------------------------------------------------------
    @staticmethod
    def _fresh() -> dict:
        return {
            "schema_version": 1,
            "identity": "God-Agent (goda)",
            "version": "0.1.0",
            "formed_at": now_iso(),
            "state": {"status": "booting", "mode": "supervised", "uptime_s": 0},
            "capabilities": {
                "shell": True,
                "files": True,
                "memory": True,
                "network": True,
                "brain": True,
                "gpu_hardware": True,
                "process_control": True,
                "kernel_tuning": True,
                "scheduling": True,
                "self_model": True,
                "evolution": True,
                "native": True,
            },
            "learned": [],
            "notes": "",
            "current_goal": "",
            "stats": {"tasks": 0, "successes": 0, "failures": 0, "reflections": 0,
                      "self_updates": 0, "evolutions": 0},
            "recent_reflections": [],
            "boundaries": [],
            "last_updated": now_iso(),
        }

    def _read(self) -> dict:
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _write(self, data: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, ensure_ascii=False)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    def current(self) -> dict:
        return self.data

    def snapshot(self, note: str = "") -> dict:
        return json.loads(json.dumps(self.data))

    def apply_update(self, patch: dict, note: str = "") -> tuple[bool, str]:
        """Apply a limited, validated patch. Returns (ok, message)."""
        raw_len = len(json.dumps(patch))
        if raw_len > self.max_update_chars:
            return False, f"update too large ({raw_len} bytes > {self.max_update_chars})"

        invalid = set(patch) - ALLOWED_UPDATE_KEYS
        if invalid:
            return False, f"keys not updatable by the agent: {sorted(invalid)}"

        for key in invalid & IMMUTABLE_KEYS:  # defensive double-check
            return False, f"immutable key: {key}"

        # Validate types/shape of allowed keys.
        caps = patch.get("capabilities", {})
        if caps:
            if not isinstance(caps, dict):
                return False, "capabilities must be an object"
            for k, v in caps.items():
                if not isinstance(v, bool):
                    return False, f"capability {k} must be boolean"
            self.data["capabilities"].update(caps)

        if "learned" in patch:
            learned = patch["learned"]
            if not isinstance(learned, list) or len(learned) > 50:
                return False, "learned must be a list (max 50 entries)"
            if any(not isinstance(x, str) or len(x) > 500 for x in learned):
                return False, "learned entries must be strings <= 500 chars"
            self.data["learned"] = list(learned)[:50]

        if "notes" in patch:
            if not isinstance(patch["notes"], str) or len(patch["notes"]) > 2000:
                return False, "notes must be a string <= 2000 chars"
            self.data["notes"] = patch["notes"]

        if "current_goal" in patch:
            if not isinstance(patch["current_goal"], str) or len(patch["current_goal"]) > 500:
                return False, "current_goal must be a string <= 500 chars"
            self.data["current_goal"] = patch["current_goal"]

        self.data["last_updated"] = now_iso()
        self.data["stats"]["self_updates"] = self.data["stats"]["self_updates"] + 1
        self._write(self.data)
        return True, "self-model updated"

    # ------------------------------------------------------------------
    def set_state(self, **kwargs: Any) -> None:
        self.data["state"].update(kwargs)
        self._write(self.data)

    def record_stats(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            if k in self.data["stats"]:
                self.data["stats"][k] = v
        self._write(self.data)

    def add_reflection(self, reflection: str, keep: int = 10) -> None:
        entry = {"ts": now_iso(), "text": reflection[:1000]}
        self.data["recent_reflections"] = (self.data.get("recent_reflections", []) + [entry])[-keep:]
        self._write(self.data)

    def set_boundaries(self, items: list[str]) -> None:
        self.data["boundaries"] = list(items)
        self._write(self.data)
