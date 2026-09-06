"""Kira defaults, provider isolation, and HTTP contracts (no live API calls)."""
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from god_agent.config import api_key, default_config
from god_agent.llm import get_llm, LLMError, MockLLM, OpenAIProvider
from god_agent.providers import default_profile, ProviderManager, probe_profile
from god_agent.runtime import Runtime
from test_desktop import isolated_config


class KiraProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {k: "" for k in os.environ if k.startswith(("GODA_", "KIRA_"))})
        self.env.start()
        self.pm = ProviderManager(str(self.root / "providers.json"))

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_consistent_kira_defaults_without_bundled_credentials(self):
        cfg = default_config()
        repo_cfg = json.loads((Path(__file__).parents[1] / "config/default.json").read_text())
        for llm in (cfg["llm"], repo_cfg["llm"]):
            self.assertEqual(llm["base_url"], "https://kiraai.vn/api/v1")
            self.assertEqual(llm["model"], "kira-3.5-flash")
            self.assertEqual(llm["api_key"], "")
            self.assertEqual(llm["api_key_env"], "KIRA_API_KEY")
        self.assertEqual(self.pm.active, "Kira")
        self.assertEqual(self.pm.active_profile(), default_profile())
        self.assertTrue(self.pm.use("OpenAI"))  # previous built-in remains available
        self.assertIsInstance(get_llm(cfg), MockLLM)

    def test_environment_key_is_resolved_not_persisted_and_not_reused_on_other_hosts(self):
        cfg = default_config()
        with patch.dict(os.environ, {"KIRA_API_KEY": "synthetic-env-credential", "GODA_API_KEY": ""}):
            self.pm.apply_to(cfg)
            client = get_llm(cfg)
            self.assertIsInstance(client, OpenAIProvider)
            self.assertEqual(client.api_key, "synthetic-env-credential")
            self.assertEqual(cfg["llm"]["api_key"], "")
            self.assertNotIn("synthetic-env-credential", Path(self.pm.path).read_text())
            self.pm.use("OpenAI")
            self.pm.apply_to(cfg)
            self.assertEqual(api_key(cfg), "")
            self.assertEqual(cfg["llm"]["api_key_env"], "GODA_API_KEY")

    def test_explicit_environment_overrides_still_win_after_profile_application(self):
        with patch.dict(os.environ, {"GODA_BASE_URL": "https://override.example/v1",
                                   "GODA_MODEL": "override-model", "GODA_API_KEY": "test-override-key"}):
            rt = Runtime(isolated_config(self.root))
            try:
                for _ in range(2):
                    self.assertEqual(rt.cfg["llm"]["base_url"], "https://override.example/v1")
                    self.assertEqual(rt.cfg["llm"]["model"], "override-model")
                    self.assertEqual(api_key(rt.cfg), "test-override-key")
                    rt.reload()
            finally:
                rt.close()

    def test_existing_configured_provider_is_not_replaced_on_upgrade(self):
        self.pm.add("My endpoint", base_url="https://existing.example/v1", model="my-model", active=True)
        before = Path(self.pm.path).read_bytes()
        upgraded = ProviderManager(self.pm.path)
        self.assertEqual(upgraded.active, "My endpoint")
        self.assertEqual(before, Path(self.pm.path).read_bytes())
        upgraded.add("MY ENDPOINT", base_url="https://existing.example/v1", model="new-model", active=True)
        self.assertEqual(upgraded.active, "My endpoint")
        self.assertEqual(upgraded.active_profile()["model"], "new-model")

    def test_secret_file_is_private_even_while_being_written(self):
        original_dump = json.dump
        modes = []

        def checked_dump(data, fh, **kwargs):
            modes.append(stat.S_IMODE(os.fstat(fh.fileno()).st_mode))
            return original_dump(data, fh, **kwargs)

        with patch("god_agent.providers.json.dump", side_effect=checked_dump):
            self.pm.add("Kira", base_url=default_profile()["base_url"], api_key="test-local-credential",
                        model="kira-3.5-flash", key_env="KIRA_API_KEY", active=True)
        self.assertEqual(modes, [0o600])
        self.assertEqual(stat.S_IMODE(os.stat(self.pm.path).st_mode), 0o600)
        self.assertNotIn("api_key", self.pm.list()[0])
        self.assertEqual(list(self.root.glob(".providers-*")), [])

    def test_kira_chat_completion_url_model_and_bearer_header(self):
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": "test response"}}]}).encode())
        cfg = default_config()
        cfg["llm"]["api_key"] = "test-local-credential"
        with patch("urllib.request.urlopen", return_value=response) as request:
            result = get_llm(cfg).chat([{"role": "user", "content": "hello"}])
        self.assertEqual(result, "test response")
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, "https://kiraai.vn/api/v1/chat/completions")
        self.assertEqual(req.get_header("Authorization"), "Bearer test-local-credential")
        self.assertEqual(json.loads(req.data)["model"], "kira-3.5-flash")

    def test_model_discovery_accepts_custom_type_and_does_not_duplicate_v1(self):
        data = {"data": [{"id": "kira-3.5-flash"}, {"id": "kira-3.5-pro"}]}
        profile = dict(default_profile(), type="custom", api_key="test-discovery-key")
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(data).encode())) as request:
            result = probe_profile(profile)
        self.assertTrue(result["ok"])
        self.assertEqual(result["models"], ["kira-3.5-flash", "kira-3.5-pro"])
        self.assertEqual(request.call_args.args[0].full_url, "https://kiraai.vn/api/v1/models")

    def test_authentication_failure_and_provider_errors_do_not_expose_key(self):
        profile = dict(default_profile(), api_key="synthetic-credential-not-a-real-key")
        error = urllib.error.HTTPError(profile["base_url"], 401, "unauthorized", {}, io.BytesIO(b"no access"))
        with patch("urllib.request.urlopen", side_effect=error):
            result = probe_profile(profile)
        self.assertFalse(result["ok"])
        self.assertIn("authentication failed", result["detail"])
        body = ("invalid credential " + profile["api_key"]).encode()
        for probing in (True, False):
            error = urllib.error.HTTPError(profile["base_url"], 400, "bad request", {}, io.BytesIO(body))
            with patch("urllib.request.urlopen", side_effect=error):
                if probing:
                    detail = probe_profile(profile)["detail"]
                else:
                    with self.assertRaises(LLMError) as caught:
                        OpenAIProvider(profile["api_key"], profile["model"], profile["base_url"]).chat([])
                    detail = str(caught.exception)
            self.assertNotIn(profile["api_key"], detail)


if __name__ == "__main__":
    unittest.main()
