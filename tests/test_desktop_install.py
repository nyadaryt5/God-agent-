"""Exercise user installation/removal in a disposable HOME, never the host's."""
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class DesktopInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "desktop home % name"
        self.home.mkdir()
        self.data = self.root / "xdg data"
        self.fakebin = self.root / "bin"
        self.fakebin.mkdir()
        self.service_marker = self.root / "service-started"
        guard = self.fakebin / "systemctl"
        guard.write_text("#!/bin/sh\necho forbidden > " + shlex.quote(str(self.service_marker)) + "\nexit 1\n")
        guard.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GODA_", "KIRA_"))}
        self.env.update(HOME=str(self.home), XDG_DATA_HOME=str(self.data),
                        PATH=str(self.fakebin) + ":" + os.environ["PATH"], DISPLAY="", WAYLAND_DISPLAY="")

    def tearDown(self):
        self.temp.cleanup()

    def command(self, args, **kwargs):
        return subprocess.run(args, cwd=self.root, env=self.env, text=True,
                              capture_output=True, timeout=30, **kwargs)

    def install(self):
        result = self.command(["bash", str(REPO / "install.sh"), "--local"])
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse(self.service_marker.exists(), "desktop install must never start a service")
        return result

    def test_install_launchers_defaults_desktop_entry_and_safe_upgrade(self):
        result = self.install()
        self.assertIn("no browser / local HTTP server", result.stdout)
        conf_path = self.home / ".god-agent/config.json"
        cfg = json.loads(conf_path.read_text())
        self.assertFalse(cfg["api"]["enabled"])
        self.assertEqual(cfg["llm"]["base_url"], "https://kiraai.vn/api/v1")
        self.assertEqual(cfg["llm"]["model"], "kira-3.5-flash")
        self.assertEqual(cfg["llm"]["api_key"], "")
        self.assertEqual(stat.S_IMODE(conf_path.stat().st_mode), 0o600)
        self.assertNotIn(cfg["api"]["token"], result.stdout)
        for name in ("goda", "god-agent", "god-agent-desktop"):
            launcher = self.home / ".local/bin" / name
            self.assertTrue(os.access(launcher, os.X_OK))
            result = self.command([str(launcher), "--help"])
            self.assertEqual(result.returncode, 0, result.stderr)
        entry = (self.data / "applications/god-agent.desktop").read_text()
        self.assertIn("Terminal=false", entry)
        self.assertIn("StartupWMClass=GodAgent", entry)
        self.assertIn('Exec="', entry)
        self.assertIn("%% name", entry)  # literal % is not a field code
        self.assertNotIn("serve", entry)
        self.assertTrue((self.data / "icons/hicolor/scalable/apps/god-agent.svg").is_file())
        if subprocess.run(["sh", "-c", "command -v desktop-file-validate"], capture_output=True).returncode == 0:
            checked = self.command(["desktop-file-validate", str(self.data / "applications/god-agent.desktop")])
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

        # Local wrappers pin their own config, independent of cwd/system installs.
        result = self.command([str(self.home / ".local/bin/goda"), "providers", "list"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Kira", result.stdout)
        providers = self.home / ".god-agent/providers.json"
        data = json.loads(providers.read_text())
        data["active"] = "OpenAI"  # preserve an operator-selected existing provider
        providers.write_text(json.dumps(data))
        cfg["llm"]["model"] = "operator-selected-model"
        conf_path.write_text(json.dumps(cfg))
        config_before, providers_before = conf_path.read_bytes(), providers.read_bytes()
        self.install()
        self.assertEqual(conf_path.read_bytes(), config_before)
        self.assertEqual(providers.read_bytes(), providers_before)
        self.assertEqual((self.home / ".bashrc").read_text().count('export PATH='), 1)

        result = self.command(["bash", str(REPO / "uninstall.sh"), "--local", "--keep-data"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(conf_path.is_file())
        self.assertTrue(providers.is_file())
        self.assertFalse((self.home / ".god-agent/app").exists())
        self.assertFalse((self.data / "applications/god-agent.desktop").exists())
        self.assertFalse((self.home / ".local/bin/god-agent-desktop").exists())
        self.assertFalse(self.service_marker.exists())

    def test_desktop_preflight_explains_missing_tk_before_copying(self):
        python = self.fakebin / "python3"
        python.write_text("#!/bin/sh\nif [ \"$1\" = '-c' ] && [ \"$2\" = 'import tkinter' ]; then exit 1; fi\n"
                          "exec " + shlex.quote(sys.executable) + ' "$@"\n')
        python.chmod(0o755)
        result = self.command(["bash", str(REPO / "install_local.sh"), str(REPO), "--desktop"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("python3-tk", result.stderr)
        self.assertFalse((self.home / ".god-agent/app").exists())
        self.assertFalse(self.service_marker.exists())


if __name__ == "__main__":
    unittest.main()
