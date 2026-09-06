"""Native desktop entry point and thread-safe controller (no HTTP server).

Tk is imported only when launching a window, so CLI/server installs still work
without a graphical environment. The controller is also testable without Tk.
"""
from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

from .config import load_config
from .llm import get_llm
from .loop import Agent
from .providers import VALID_TYPES, _normalize_base, probe_profile
from .runtime import Runtime
from .utils import now_iso, redact


@dataclass
class ApprovalRequest:
    tool: str
    args: dict
    reason: str
    approved: bool = False
    answered: threading.Event = field(default_factory=threading.Event)

    def respond(self, approved: bool) -> None:
        self.approved = approved
        self.answered.set()


class DesktopController:
    """Run one operation at a time; workers communicate with Tk via a queue."""

    session = "desktop"

    def __init__(self, runtime: Runtime):
        self.rt = runtime
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.closed = False
        self._job_lock = threading.Lock()
        self._approval: ApprovalRequest | None = None
        self.rt.approver = self._approve

    @property
    def busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    @property
    def disabled(self) -> bool:
        return os.path.isfile(os.path.expanduser(self.rt.cfg["kill_switch"]["path"]))

    def _start(self, job: Callable[[], None]) -> bool:
        with self._job_lock:
            if self.closed or self.busy:
                return False

            def run() -> None:
                self.rt.rebind_thread()
                try:
                    job()
                except Exception as exc:  # keep errors inside the desktop, not Tk callbacks
                    self.events.put(("error", redact(str(exc))))
                finally:
                    self.events.put(("idle", None))

            self.worker = threading.Thread(target=run, name="goda-desktop-task", daemon=True)
            self.worker.start()
        return True

    def submit(self, task: str) -> bool:
        task = task.strip()
        if not task or self.disabled:
            return False

        def run_task() -> None:
            history = self.rt.memory.chat_history(self.session, limit=12)
            self.rt.memory.add_chat(self.session, "user", redact(task))
            self.rt.record("chat", "desktop task: " + redact(task)[:120], actor="operator")
            agent = Agent(self.rt, get_llm(self.rt.cfg),
                          progress=lambda text: self.events.put(("progress", redact(text))))
            try:
                result = agent.run(task, history=history)
                self.rt.memory.add_chat(self.session, "assistant", redact(result.summary))
                self.events.put(("result", result))
            finally:
                self.rt.self_model.set_state(status="idle", current_task="")

        return self._start(run_task)

    def _approve(self, tool: str, args: dict, decision) -> bool:
        request = ApprovalRequest(tool, args, getattr(decision, "reason", ""))
        self._approval = request
        self.events.put(("approval", request))
        try:
            answered = request.answered.wait(timeout=180)
            if not answered:
                request.respond(False)
            return answered and request.approved and not self.disabled
        finally:
            self._approval = None

    def set_disabled(self, disabled: bool) -> None:
        path = os.path.expanduser(self.rt.cfg["kill_switch"]["path"])
        if disabled:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"disabled at {now_iso()} by desktop operator\n")
            if self._approval:
                self._approval.respond(False)
        elif os.path.exists(path):
            os.remove(path)
        self.rt.record("kill_switch", "agent " + ("DISABLED" if disabled else "enabled") +
                       " via desktop", actor="operator")

    def prepare_profile(self, fields: dict, *, clear_key: bool = False,
                        require_model: bool = True) -> dict:
        """Validate form data; a blank password field keeps the saved key.

        A stored key is not carried across to a different endpoint or protocol.
        Environment-variable names are explicit fields, never resolved here.
        """
        profile = {key: str(fields.get(key, "")).strip() for key in
                   ("name", "type", "base_url", "model", "api_key", "key_env")}
        if not profile["name"] or len(profile["name"]) > 80:
            raise ValueError("Enter a provider name (1–80 characters).")
        if profile["type"] not in VALID_TYPES:
            raise ValueError("Choose an OpenAI-compatible, Anthropic, or offline provider.")
        base = _normalize_base(profile["base_url"], profile["type"])
        if profile["type"] != "mock":
            url = urlsplit(base)
            if (url.scheme not in ("http", "https") or not url.hostname or
                    url.username or url.password or url.query or url.fragment):
                raise ValueError("Enter an HTTP(S) base URL without credentials, a query, or a fragment.")
            if require_model and not profile["model"]:
                raise ValueError("Enter a model ID or load one from the provider.")
        profile["base_url"] = base
        existing = next((p for p in self.rt.providers.profiles
                         if p["name"].lower() == profile["name"].lower()), {})
        if clear_key:
            profile["api_key"] = ""
        elif (not profile["api_key"] and existing.get("base_url") == base and
              existing.get("type") == profile["type"]):
            profile["api_key"] = existing.get("api_key", "")
        return profile

    def save_provider(self, profile: dict) -> None:
        if self.closed or self.busy:
            raise RuntimeError("Wait for the current operation to finish before changing providers.")
        self.rt.providers.add(
            profile["name"], ptype=profile["type"], base_url=profile["base_url"],
            model=profile["model"], api_key=profile["api_key"],
            key_env=profile["key_env"], active=True,
        )
        self.rt.reload()
        self.rt.record("providers", "desktop selected provider " + profile["name"], actor="operator")

    def test_provider(self, profile: dict) -> bool:
        # Copy form data before the worker starts; no Tk variables off the UI thread.
        profile = dict(profile)
        return self._start(lambda: self.events.put(("provider_test", probe_profile(profile))))

    def close(self) -> bool:
        # Never close the database or abandon a native command while it is running.
        with self._job_lock:
            if self.busy:
                return False
            if not self.closed:
                self.rt.close()
                self.closed = True
        return True


def launch(cfg: dict) -> int:
    if not sys.platform.startswith("linux"):
        print("God-Agent's system tools currently require Linux. Native Windows/macOS "
              "system control is not supported yet.", file=sys.stderr)
        return 1
    try:
        import tkinter as tk
        from tkinter import messagebox
        from .desktop_ui import DesktopWindow
    except ImportError:
        print("The desktop app requires Python's Tk support.\n"
              "Ubuntu/Debian: sudo apt install python3-tk\n"
              "Fedora: sudo dnf install python3-tkinter\n"
              "Arch: sudo pacman -S tk\n"
              "CLI commands such as `goda chat` do not require Tk.", file=sys.stderr)
        return 1
    try:
        root = tk.Tk(className="GodAgent")
    except tk.TclError:
        print("Cannot open a desktop display. Run `goda desktop` in a terminal on your "
              "Linux graphical desktop, not in a headless server/SSH session.\n"
              "Use `goda chat` for terminal-only access.", file=sys.stderr)
        return 1

    controller = None
    try:
        controller = DesktopController(Runtime(cfg))
        DesktopWindow(root, controller)
        root.mainloop()
        return 0
    except Exception as exc:
        messagebox.showerror("God-Agent could not start", redact(str(exc)), parent=root)
        return 1
    finally:
        if controller is not None:
            controller.close()
        try:
            root.destroy()
        except tk.TclError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="God-Agent native Linux desktop app (no web server)")
    parser.add_argument("--config", help="path to config.json")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print("Configuration error: " + redact(str(exc)), file=sys.stderr)
        return 1
    return launch(cfg)


if __name__ == "__main__":
    sys.exit(main())
