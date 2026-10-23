"""The isolated loader probe must reproduce the binding the real home declares.

`omh setup` writes `plugins.entries.omh.settings.omh_home` into every home it
registers. The plugin refuses to load in a native host whose profile names no
store (#2037), so a probe whose synthetic config omits that binding can only
ever report a load failure -- on a machine where the plugin loads fine.
"""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from omh.install.plugin_loader_observation import observe_real_loader_registration
from omh.install.release_smoke_core import CommandResult
from omh.plugin_bundle.omh.metadata import PROVIDED_HOOKS, PROVIDED_TOOLS

OBSERVATION_PREFIX = "OMH_PLUGIN_LOADER_OBSERVATION="


class ObserveRealLoaderRegistrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory(prefix="omh-loader-test-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.plugin_dir = root / "plugins" / "omh"
        self.plugin_dir.mkdir(parents=True)
        (self.plugin_dir / "plugin.yaml").write_text("name: omh\n", encoding="utf-8")
        self.code_root = root / "hermes-agent"
        (self.code_root / "hermes_cli").mkdir(parents=True)
        self.python = root / "python.exe"
        self.python.write_bytes(b"stub")
        self.config_seen = ""
        self.env_seen: dict[str, str] = {}

    def _runner(self, *, tools=None, hooks=None, returncode=0, stderr=""):
        def run(command, timeout_seconds, env):
            self.env_seen = dict(env)
            self.config_seen = (Path(env["HERMES_HOME"]) / "config.yaml").read_text(encoding="utf-8")
            payload = {
                "enabled": True,
                "error": None,
                "hermes_version": "0.22.2",
                "tools": sorted(PROVIDED_TOOLS if tools is None else tools),
                "hooks": sorted(PROVIDED_HOOKS if hooks is None else hooks),
            }
            stdout = OBSERVATION_PREFIX + json.dumps(payload)
            return CommandResult(list(command), returncode, stdout, stderr)

        return run

    def observe(self, *, omh_home=None, **runner_kwargs):
        return observe_real_loader_registration(
            self.plugin_dir,
            python_executable=self.python,
            hermes_code_root=self.code_root,
            omh_home=omh_home,
            runner=self._runner(**runner_kwargs),
        )

    def test_binding_is_written_into_the_isolated_config(self):
        result = self.observe(omh_home=Path("/stores/profile-a"))

        self.assertIn("  entries:", self.config_seen)
        self.assertIn("        omh_home: \"/stores/profile-a\"", self.config_seen)
        self.assertTrue(result["observed"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "registration_matches_manifest")

    def test_a_windows_path_is_written_with_forward_slashes(self):
        result = self.observe(omh_home=r"C:\Users\Admin\.omh")

        self.assertIn('        omh_home: "C:/Users/Admin/.omh"', self.config_seen)
        self.assertTrue(result["ok"])

    def test_without_a_binding_the_config_keeps_its_old_shape(self):
        result = self.observe()

        self.assertNotIn("entries:", self.config_seen)
        self.assertTrue(result["observed"])
        self.assertTrue(result["ok"])

    def test_a_missing_tool_is_a_mismatch_not_a_load_failure(self):
        result = self.observe(tools=PROVIDED_TOOLS[:-1])

        self.assertTrue(result["observed"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "registration_mismatch")

    def test_windows_baseline_reaches_the_probe(self):
        baseline = {"SystemRoot": r"C:\Windows", "windir": r"C:\Windows", "TEMP": r"C:\Temp"}
        with mock.patch.dict(os.environ, baseline, clear=False):
            self.observe()

        for key, value in baseline.items():
            self.assertEqual(self.env_seen.get(key), value)

    def test_absent_baseline_variables_are_not_invented(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.observe()

        self.assertNotIn("SystemRoot", self.env_seen)
        self.assertNotIn("windir", self.env_seen)

    def test_a_failed_probe_reports_a_bounded_detail(self):
        result = self.observe(returncode=1, stderr="x" * 4000)

        self.assertFalse(result["observed"])
        self.assertEqual(result["reason"], "loader_probe_failed")
        self.assertLessEqual(len(result["detail"]), 400)


if __name__ == "__main__":
    unittest.main()
