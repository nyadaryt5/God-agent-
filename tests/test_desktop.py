"""Desktop tests run headlessly; the real Tk smoke test is optional."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from god_agent.config import default_config
from god_agent.desktop import DesktopController, launch
from god_agent.loop import TaskResult
from god_agent.providers import default_profile
from god_agent.runtime import Runtime, get_runtime


def isolated_config(root):
    cfg = default_config()
    cfg["state"] = {"root": str(root), "tasks_dir": str(root / "tasks")}
    for section, key, filename in (
        ("brain", "path", "brain.json"), ("memory", "db_path", "memory.db"),
        ("self_model", "path", "self.json"), ("audit", "path", "audit.jsonl"),
        ("kill_switch", "path", "DISABLED"),
    ):
        cfg[section][key] = str(root / filename)
    return cfg


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {k: "" for k in os.environ
                                          if k.startswith(("GODA_", "KIRA_"))})
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = isolated_config(Path(self.temp.name))
        self.rt = Runtime(self.cfg)
        self.controller = DesktopController(self.rt)

    def tearDown(self):
        if self.controller.worker:
            self.controller.worker.join(timeout=5)
        self.controller.close()
        self.temp.cleanup()
        self.env.stop()

    def finish(self):
        self.controller.worker.join(timeout=5)
        self.assertFalse(self.controller.busy, "worker did not finish")
        events = []
        while True:
            try:
                events.append(self.controller.events.get_nowait())
            except queue.Empty:
                return events

    def test_offline_task_runs_natively_without_http_or_browser(self):
        self.cfg["api"]["enabled"] = True  # even a legacy config must not start a desktop listener
        with patch("socket.socket.bind", side_effect=AssertionError("no listener allowed")), \
                patch("webbrowser.open", side_effect=AssertionError("no browser allowed")):
            self.assertTrue(self.controller.submit("status"))
            events = self.finish()
        results = [value for kind, value in events if kind == "result"]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertEqual(results[0].steps[0].tool, "system_info")
        self.assertTrue(any(kind == "progress" for kind, _ in events))
        self.assertFalse(any(kind == "error" for kind, _ in events))
        self.assertEqual([m["role"] for m in self.rt.memory.chat_history("desktop")], ["user", "assistant"])
        self.assertTrue(self.rt.audit.verify()[0])

    def test_worker_binds_runtime_serializes_tasks_and_preserves_history(self):
        self.rt.memory.add_chat("desktop", "user", "previous question")
        started = threading.Event()
        release = threading.Event()
        observed = {}
        main_thread = threading.get_ident()

        def run(task, history):
            observed.update(runtime=get_runtime(), thread=threading.get_ident(), history=history)
            started.set()
            release.wait(timeout=3)
            return TaskResult(task, success=True, summary="done")

        with patch("god_agent.desktop.Agent") as agent:
            agent.return_value.run.side_effect = run
            self.assertFalse(self.controller.submit("   "))
            self.assertTrue(self.controller.submit("test"))
            self.assertTrue(started.wait(timeout=2))
            try:
                self.assertFalse(self.controller.submit("another task"))
                self.assertFalse(self.controller.close())
                with self.assertRaises(RuntimeError):
                    self.controller.save_provider(default_profile())
            finally:
                release.set()
            self.finish()
        self.assertIs(observed["runtime"], self.rt)
        self.assertNotEqual(observed["thread"], main_thread)
        self.assertEqual(observed["history"][0]["content"], "previous question")
        self.assertTrue(self.controller.close())
        self.assertTrue(self.controller.close())  # idempotent
        self.assertFalse(self.controller.submit("after close"))

    def test_worker_errors_are_redacted_and_do_not_wedge_the_app(self):
        fake_key = "kira_" + "synthetic_test_value"
        with patch("god_agent.desktop.Agent") as agent:
            agent.return_value.run.side_effect = RuntimeError("failure: " + fake_key)
            self.assertTrue(self.controller.submit("test failure"))
            events = self.finish()
        error = next(value for kind, value in events if kind == "error")
        self.assertNotIn(fake_key, error)
        self.assertIn("kira_***", error)
        self.assertEqual(self.rt.self_model.current()["state"]["status"], "idle")
        self.assertTrue(self.controller.submit("status"))
        self.assertTrue(any(kind == "result" for kind, _ in self.finish()))

    def test_approval_round_trip_and_disable_deny_pending_requests(self):
        observed = []

        def run(task, history):
            observed.append(self.rt.request_approval("shell_exec", {"command": "example only"}, None))
            return TaskResult(task, success=True, summary="approval tested; no command executed")

        with patch("god_agent.desktop.Agent") as agent:
            agent.return_value.run.side_effect = run
            for approve in (True, False):
                self.assertTrue(self.controller.submit("approval test"))
                while True:
                    kind, request = self.controller.events.get(timeout=2)
                    if kind == "approval":
                        break
                if approve:
                    request.respond(True)
                else:
                    self.controller.set_disabled(True)
                self.finish()
        self.assertEqual(observed, [True, False])
        self.assertTrue(self.controller.disabled)
        self.assertFalse(self.controller.submit("status"))
        self.controller.set_disabled(False)
        self.assertFalse(self.controller.disabled)

    def test_saved_key_is_masked_preserved_and_can_be_removed(self):
        profile = default_profile()
        profile["api_key"] = "synthetic-test-key"
        self.controller.save_provider(profile)
        blank = dict(profile, api_key="", model="another-model")
        prepared = self.controller.prepare_profile(blank)
        self.assertEqual(prepared["api_key"], "synthetic-test-key")
        self.assertEqual(self.controller.prepare_profile(blank, clear_key=True)["api_key"], "")
        changed = dict(blank, base_url="https://different.example/v1")
        self.assertEqual(self.controller.prepare_profile(changed)["api_key"], "")
        self.assertNotIn("synthetic-test-key", Path(self.cfg["audit"]["path"]).read_text())
        self.assertIs(self.rt.registry.policy, self.rt.policy)

    def test_profile_validation_and_optional_model_discovery(self):
        for url in ("file:///etc/passwd", "https://user:secret@example.com/v1", "https://example.com/v1?key=secret"):
            with self.assertRaises(ValueError):
                self.controller.prepare_profile(dict(default_profile(), base_url=url))
        with self.assertRaises(ValueError):
            self.controller.prepare_profile(dict(default_profile(), model=""))
        prepared = self.controller.prepare_profile(dict(default_profile(), model=""), require_model=False)
        self.assertEqual(prepared["model"], "")
        profile_before = Path(self.rt.providers.path).read_bytes()
        with patch("god_agent.desktop.probe_profile", return_value={"ok": True, "detail": "reachable", "models": ["test-model"]}):
            self.assertTrue(self.controller.test_provider(prepared))
            events = self.finish()
        self.assertTrue(any(kind == "provider_test" and value["models"] == ["test-model"] for kind, value in events))
        self.assertEqual(Path(self.rt.providers.path).read_bytes(), profile_before)

    @unittest.skipUnless(os.environ.get("DISPLAY") and importlib.util.find_spec("tkinter"),
                         "requires Tk and a graphical display (or xvfb-run)")
    def test_real_tk_window_settings_chat_and_clean_shutdown(self):
        import tkinter as tk
        from god_agent.desktop_ui import DesktopWindow

        root = tk.Tk()
        root.withdraw()
        window = DesktopWindow(root, self.controller)
        try:
            self.assertEqual(window.fields["name"].get(), "Kira")
            self.assertEqual(window.fields["api_key"].get(), "")
            window.send("status")
            self.controller.worker.join(timeout=5)
            root.after_cancel(window._poll_id)
            window._poll()
            self.assertIn("Completed", window.transcript.get("1.0", "end"))
            self.assertIn("system_info", window.activity.get("1.0", "end"))
            window.toggle_disabled()
            self.assertTrue(self.controller.disabled)
            window.toggle_disabled()
            window.save_provider()
        finally:
            window.close()


class DesktopEntryTests(unittest.TestCase):
    def test_cli_desktop_routes_without_creating_runtime_or_server(self):
        from god_agent.cli import main
        with patch("god_agent.desktop.launch", return_value=0) as start, patch("god_agent.cli.Runtime") as runtime:
            self.assertEqual(main(["desktop"]), 0)
            start.assert_called_once()
            runtime.assert_not_called()

    def test_graphical_no_argument_launch_and_headless_cli_help(self):
        from god_agent.cli import main
        with patch("god_agent.desktop.launch", return_value=0) as start:
            with patch.dict(os.environ, {"DISPLAY": ":example", "WAYLAND_DISPLAY": ""}):
                self.assertEqual(main([]), 0)
                start.assert_called_once()
            start.reset_mock()
            with patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([]), 0)
                start.assert_not_called()

    def test_tk_optional_and_unsupported_os_errors_are_actionable(self):
        with patch("sys.platform", "win32"), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(launch(default_config()), 1)
            self.assertIn("Linux", err.getvalue())
        with patch("sys.platform", "linux"), patch.dict(sys.modules, {"tkinter": None}), \
                patch("god_agent.desktop.Runtime") as runtime, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(launch(default_config()), 1)
            self.assertIn("python3-tk", err.getvalue())
            runtime.assert_not_called()

    def test_import_and_help_do_not_require_tk_or_load_http_server(self):
        script = "import sys; import god_agent.desktop; assert 'tkinter' not in sys.modules; assert 'god_agent.api' not in sys.modules"
        subprocess.run([sys.executable, "-c", script], check=True, timeout=10)
        proc = subprocess.run([sys.executable, "-m", "god_agent.desktop", "--help"],
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("no web server", proc.stdout)


if __name__ == "__main__":
    unittest.main()
