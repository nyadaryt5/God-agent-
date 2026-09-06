"""Regression: candidate pytest suites must not recursively spawn themselves."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from god_agent.config import default_config
from god_agent.evolution import EvolutionPipeline


class EvolutionTestRunnerTests(unittest.TestCase):
    def test_nested_candidates_keep_selftests_but_do_not_launch_pytest_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = default_config()
            cfg["state"]["root"] = tmp
            pipe = EvolutionPipeline(cfg)
            tree = Path(tmp) / "candidate"
            tree.mkdir()
            for nested in (False, True):
                with patch.dict(os.environ, {"GODA_EVOLUTION_TEST": "1" if nested else ""}), \
                        patch("god_agent.evolution.shutil.which", return_value="/test/pytest"), \
                        patch("god_agent.evolution.run_command", return_value={"exit_code": 0}) as run:
                    self.assertTrue(pipe._test_tree(str(tree))["ok"])
                    calls = run.call_args_list
                    self.assertIn("god_agent.selftest", calls[0].args[0][-1])
                    self.assertEqual(len(calls), 1 if nested else 2)
                    if not nested:
                        self.assertEqual(calls[1].kwargs["env"], {"GODA_EVOLUTION_TEST": "1"})


if __name__ == "__main__":
    unittest.main()
