from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from _cli_harness import run_cli


class ProbeCliTests(unittest.TestCase):
    def test_read_only_probe_does_not_execute_configured_buzz_cli(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            hermes_home = root / ".hermes"
            hermes_home.mkdir()
            marker = root / "buzz-executed"
            executable = root / "buzz"
            executable.write_text(f"#!/bin/sh\n: > '{marker}'\n", encoding="utf-8")
            executable.chmod(0o755)
            (hermes_home / "config.yaml").write_text(
                "gateway:\n"
                "  platforms:\n"
                "    buzz:\n"
                "      enabled: true\n"
                "      extra:\n"
                f"        cli_path: {executable}\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"BUZZ_CLI_PATH": ""}):
                status, stdout, stderr = run_cli(
                    ["--omh-home", str(root / ".omh"), "--hermes-home", str(hermes_home), "probe"]
                )

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            self.assertFalse(marker.exists())
            buzz = json.loads(stdout)["buzz"]
            self.assertTrue(buzz["read_only"])
            self.assertTrue(buzz["configured"])
            self.assertTrue(buzz["installed"])
            self.assertEqual(buzz["status"], "unverified")
            self.assertEqual(buzz["reason_code"], "buzz_cli_present_not_executed")
            self.assertEqual(buzz["executable"], str(executable.resolve()))
            self.assertIsNone(buzz["version"])
            self.assertFalse(buzz["observed"])
            capability = next(
                item for item in json.loads(stdout)["capabilities"] if item["name"] == "buzz_platform_diagnostics"
            )
            self.assertEqual(capability["status"], "unverified")
            self.assertEqual(capability["evidence"], "buzz_cli_present_not_executed")

    def test_probe_reports_unknown_and_missing_without_install(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            status, stdout, stderr = run_cli(["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes"), "probe"])

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            caps = {capability["name"]: capability for capability in payload["capabilities"]}
            self.assertEqual(caps["external_skill_dirs"]["status"], "unknown")
            self.assertEqual(caps["managed_skills"]["status"], "missing")
            self.assertEqual(caps["native_hooks"]["status"], "unknown")
            self.assertEqual(caps["mcp_preference"]["status"], "unknown")
            self.assertEqual(caps["mcp_host_session"]["status"], "unverified")
            self.assertEqual(caps["mcp_host_config"]["status"], "unknown")
            self.assertEqual(caps["omh_plugin_bundle"]["status"], "missing")
            self.assertEqual(caps["plugin_import_smoke"]["status"], "unknown")
            self.assertEqual(caps["target_registry"]["status"], "missing")
            self.assertEqual(caps["buzz_platform_diagnostics"]["status"], "missing")
            self.assertEqual(payload["buzz"]["schema_version"], "omh_buzz_probe/v1")
            self.assertEqual(payload["buzz"]["reason_code"], "buzz_cli_missing")
            self.assertTrue(payload["buzz"]["read_only"])
            self.assertEqual(payload["target_topology"]["mode"], "unknown")
            self.assertFalse(payload["plugin_distribution_ready"])
            self.assertFalse(payload["native_integration_claim_ready"])

    def test_probe_roadmap_separates_product_setup_from_runtime_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            status, stdout, stderr = run_cli(base + ["probe", "--roadmap", "--json"])

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            roadmap = json.loads(stdout)["capability_gap_roadmap"]
            self.assertEqual(roadmap["schema_version"], "omh_capability_gap_roadmap/v1")
            self.assertGreaterEqual(roadmap["summary"]["baseline_product_gaps"], 2)
            self.assertFalse(roadmap["summary"]["baseline_ready"])
            self.assertEqual(roadmap["next_actions"][0]["id"], "run_setup")
            self.assertIn("omh_plugin_bundle", roadmap["next_actions"][0]["capabilities"])
            self.assertIn("not workflow execution evidence", roadmap["next_actions"][0]["boundary"])

            self.assertEqual(run_cli(base + ["setup", "--with-plugin"])[0], 0)
            status, stdout, stderr = run_cli(base + ["probe", "--parity", "--json"])

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            roadmap = payload["capability_gap_roadmap"]
            self.assertTrue(roadmap["summary"]["baseline_ready"])
            self.assertEqual(roadmap["summary"]["baseline_product_gaps"], 0)
            self.assertGreaterEqual(roadmap["summary"]["evidence_gaps"], 1)
            self.assertIn("parity_matrix", payload)
            actions = {action["id"]: action for action in roadmap["next_actions"]}
            self.assertIn("observe_plugin_runtime", actions)
            self.assertIn("record_wrapper_usage", actions)
            self.assertIn("not coding execution", actions["observe_plugin_runtime"]["boundary"])
            self.assertNotIn("command", actions["record_wrapper_usage"])
            self.assertIn("operator_instruction", actions["record_wrapper_usage"])

    def test_probe_reports_available_local_evidence_after_install_and_wrapper_record(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            omh_home = root / ".omh"
            hermes_home = root / ".hermes"

            self.assertEqual(run_cli(["--omh-home", str(omh_home), "--hermes-home", str(hermes_home), "install"])[0], 0)
            self.assertEqual(run_cli(["--omh-home", str(omh_home), "--hermes-home", str(hermes_home), "apply"])[0], 0)
            status, stdout, _ = run_cli(
                [
                    "--omh-home",
                    str(omh_home),
                    "runtime",
                    "record",
                    "--skill",
                    "oh-my-hermes",
                    "--harness",
                    "coding-handling",
                ]
            )
            self.assertEqual(status, 0)
            run_id = json.loads(stdout)["run"]["run_id"]
            self.assertEqual(run_cli(["--omh-home", str(omh_home), "runtime", "wrapper", "--run", run_id, "--prompt-dispatched"])[0], 0)

            status, stdout, stderr = run_cli(["--omh-home", str(omh_home), "--hermes-home", str(hermes_home), "probe"])

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            caps = {capability["name"]: capability for capability in json.loads(stdout)["capabilities"]}
            self.assertEqual(caps["external_skill_dirs"]["status"], "available")
            self.assertEqual(caps["managed_skills"]["status"], "available")
            self.assertEqual(caps["wrapper_metadata"]["status"], "available")
            self.assertEqual(caps["omh_plugin_bundle"]["status"], "missing")
            self.assertEqual(caps["target_registry"]["status"], "missing")
            self.assertFalse(json.loads(stdout)["plugin_distribution_ready"])

            self.assertEqual(run_cli(["--omh-home", str(omh_home), "--hermes-home", str(hermes_home), "setup"])[0], 0)
            status, stdout, stderr = run_cli(["--omh-home", str(omh_home), "--hermes-home", str(hermes_home), "probe"])
            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            caps = {capability["name"]: capability for capability in payload["capabilities"]}
            self.assertEqual(caps["target_registry"]["status"], "available")
            self.assertEqual(payload["target_topology"]["mode"], "single_agent_target")

    def test_probe_counts_wrapper_sessions_as_wrapper_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            omh_home = root / ".omh"
            hermes_home = root / ".hermes"
            base = ["--omh-home", str(omh_home), "--hermes-home", str(hermes_home)]

            self.assertEqual(run_cli(base + ["setup"])[0], 0)
            status, stdout, stderr = run_cli(
                base + ["chat", "session", "start", "--source", "discord", "I want to safely add a feature"]
            )

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            session = json.loads(stdout)["session"]
            self.assertTrue((omh_home / "runtime" / "wrapper_sessions" / session["session_id"] / "session.json").exists())

            status, stdout, stderr = run_cli(base + ["probe", "--roadmap", "--json"])

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            caps = {capability["name"]: capability for capability in payload["capabilities"]}
            self.assertEqual(caps["wrapper_metadata"]["status"], "available")
            actions = {action["id"] for action in payload["capability_gap_roadmap"]["next_actions"]}
            self.assertNotIn("record_wrapper_usage", actions)

    def test_probe_separates_mcp_preference_from_host_config(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            omh_home = root / ".omh"
            hermes_home = root / ".hermes"

            self.assertEqual(run_cli(["--omh-home", str(omh_home), "--hermes-home", str(hermes_home), "setup", "--with-mcp"])[0], 0)

            status, stdout, stderr = run_cli(["--omh-home", str(omh_home), "--hermes-home", str(hermes_home), "probe"])

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            caps = {capability["name"]: capability for capability in payload["capabilities"]}
            self.assertEqual(caps["mcp_preference"]["status"], "unverified")
            self.assertIn("bridge preference was requested", caps["mcp_preference"]["message"])
            self.assertIn("runtime/state.json", caps["mcp_preference"]["evidence"])
            self.assertEqual(caps["mcp_bridge_server"]["status"], "available")
            self.assertIn("omh_status", caps["mcp_bridge_server"]["message"])
            self.assertEqual(caps["mcp_bridge_runtime"]["status"], "unverified")
            self.assertEqual(caps["mcp_host_session"]["status"], "unverified")
            self.assertEqual(caps["mcp_host_config"]["status"], "unknown")
            self.assertIn("No Hermes MCP host config", caps["mcp_host_config"]["message"])
            self.assertFalse(payload["native_integration_claim_ready"])

            (hermes_home / ".mcp.json").write_text("{}", encoding="utf-8")
            status, stdout, stderr = run_cli(["--omh-home", str(omh_home), "--hermes-home", str(hermes_home), "probe"])

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            caps = {capability["name"]: capability for capability in json.loads(stdout)["capabilities"]}
            self.assertEqual(caps["mcp_preference"]["status"], "unverified")
            self.assertEqual(caps["mcp_host_config"]["status"], "unverified")
            self.assertIn("MCP host config exists", caps["mcp_host_config"]["message"])
            self.assertNotIn("mcp", {capability["name"] for capability in json.loads(stdout)["capabilities"]})

    def test_probe_reports_plugin_distribution_without_native_runtime_claim(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            omh_home = root / ".omh"
            hermes_home = root / ".hermes"

            self.assertEqual(run_cli(["--omh-home", str(omh_home), "--hermes-home", str(hermes_home), "setup", "--with-plugin"])[0], 0)

            status, stdout, stderr = run_cli(["--omh-home", str(omh_home), "--hermes-home", str(hermes_home), "probe"])

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            caps = {capability["name"]: capability for capability in payload["capabilities"]}
            self.assertIn("dedicated plugin capabilities", caps["plugin_bundles"]["message"])
            self.assertNotIn("no stable Hermes plugin bundle contract", caps["plugin_bundles"]["message"])
            self.assertEqual(caps["omh_plugin_bundle"]["status"], "available")
            self.assertEqual(caps["plugin_import_smoke"]["status"], "available")
            self.assertEqual(caps["plugin_register_smoke"]["status"], "available")
            self.assertEqual(caps["plugin_runtime_observed"]["status"], "unverified")
            self.assertTrue(payload["plugin_distribution_ready"])
            self.assertFalse(payload["native_integration_claim_ready"])

    def test_probe_roadmap_points_to_repair_for_broken_plugin_bridge(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            omh_home = root / ".omh"
            hermes_home = root / ".hermes"
            base = ["--omh-home", str(omh_home), "--hermes-home", str(hermes_home)]

            self.assertEqual(run_cli(base + ["setup", "--with-plugin"])[0], 0)
            (hermes_home / "plugins" / "omh" / "__init__.py").write_text("definitely not python: nope\n", encoding="utf-8")

            status, stdout, stderr = run_cli(base + ["probe", "--roadmap", "--json"])

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            caps = {capability["name"]: capability for capability in payload["capabilities"]}
            self.assertEqual(caps["omh_plugin_bundle"]["status"], "available")
            self.assertEqual(caps["plugin_import_smoke"]["status"], "missing")
            self.assertEqual(caps["plugin_register_smoke"]["status"], "missing")
            self.assertFalse(payload["plugin_distribution_ready"])
            roadmap = payload["capability_gap_roadmap"]
            self.assertEqual(roadmap["summary"]["baseline_product_gaps"], 2)
            actions = {action["id"]: action for action in roadmap["next_actions"]}
            self.assertIn("repair_plugin_bridge", actions)
            self.assertEqual(actions["repair_plugin_bridge"]["command"], "omh setup")
            self.assertEqual(actions["repair_plugin_bridge"]["fallback_command"], "omh setup --force")
            self.assertIn("not proof that Hermes loaded", actions["repair_plugin_bridge"]["boundary"])


if __name__ == "__main__":
    unittest.main()
