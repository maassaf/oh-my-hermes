from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _local_package import load_local_package

load_local_package()

from omh.coding.executor_auth_signals import (  # noqa: E402
    EXECUTOR_AUTH_SIGNALS_SCHEMA_VERSION,
    auth_signal_for_profile,
    executor_auth_signals,
    last_limit_signal_for_profile,
)
from omh.coding.executor_readiness import (  # noqa: E402
    executor_choice_context,
    live_readiness_binding,
    probe_executor_readiness,
)
from omh.system.local_store import atomic_write_json, utc_now  # noqa: E402
from omh.system.paths import OmhPaths  # noqa: E402


class AuthSignalMarkerTests(unittest.TestCase):
    def test_absent_markers_when_nothing_is_installed(self) -> None:
        with TemporaryDirectory() as tmp:
            signals = executor_auth_signals(home=Path(tmp))
            self.assertEqual(signals["schema_version"], EXECUTOR_AUTH_SIGNALS_SCHEMA_VERSION)
            self.assertEqual(signals["profiles"]["codex"]["login_marker"], "absent")
            self.assertEqual(signals["profiles"]["claude-code"]["login_marker"], "absent")
            self.assertIn("not subscription tier", signals["claim_boundary"])

    def test_present_markers_read_shape_only(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude.json").write_text(json.dumps({"oauthAccount": {"x": 1}}), encoding="utf-8")
            (home / ".codex").mkdir()
            (home / ".codex" / "auth.json").write_text("{\"tokens\": \"sk-SECRET-VALUE-12345\"}", encoding="utf-8")
            signals = executor_auth_signals(home=home)
            self.assertEqual(signals["profiles"]["claude-code"]["login_marker"], "present")
            self.assertEqual(signals["profiles"]["codex"]["login_marker"], "present")
            # No credential value may appear anywhere in the payload.
            self.assertNotIn("sk-SECRET-VALUE-12345", json.dumps(signals))

    def test_unreadable_claude_config_is_unknown(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude.json").write_text("not json at all {", encoding="utf-8")
            signals = executor_auth_signals(home=home)
            self.assertEqual(signals["profiles"]["claude-code"]["login_marker"], "unknown")

    def test_claude_config_without_login_key_is_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude.json").write_text(json.dumps({"projects": {}}), encoding="utf-8")
            signals = executor_auth_signals(home=home)
            self.assertEqual(signals["profiles"]["claude-code"]["login_marker"], "absent")

    def test_non_cli_profile_is_not_applicable(self) -> None:
        entry = auth_signal_for_profile("hermes")
        self.assertEqual(entry["login_marker"], "not_applicable")

    def test_senpi_marker_is_presence_only(self) -> None:
        """omo-runtime's marker is the first omo host credential store found
        (pi, senpi, then opencode): present iff a non-empty JSON object
        exists; provider names and values never read."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(executor_auth_signals(home=home)["profiles"]["omo-runtime"]["login_marker"], "absent")
            senpi_dir = home / ".senpi" / "agent"
            senpi_dir.mkdir(parents=True)
            (senpi_dir / "auth.json").write_text("{}", encoding="utf-8")
            self.assertEqual(executor_auth_signals(home=home)["profiles"]["omo-runtime"]["login_marker"], "absent")
            (senpi_dir / "auth.json").write_text(
                json.dumps({"kimi-coding": {"key": "sk-SECRET-VALUE-12345"}}), encoding="utf-8"
            )
            signals = executor_auth_signals(home=home)
            self.assertEqual(signals["profiles"]["omo-runtime"]["login_marker"], "present")
            self.assertNotIn("sk-SECRET-VALUE-12345", json.dumps(signals))
            self.assertNotIn("kimi-coding", json.dumps(signals))
            (senpi_dir / "auth.json").write_text("not json {", encoding="utf-8")
            self.assertEqual(executor_auth_signals(home=home)["profiles"]["omo-runtime"]["login_marker"], "unknown")

    def test_opencode_auth_store_marks_omo_runtime_present(self) -> None:
        """An opencode-hosted omo login is a first-class marker: the opencode
        auth store alone flips omo-runtime to present, so it no longer ranks
        below logged-in codex/claude-code in executor_choice_context."""
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            auth_dir = home / ".local" / "share" / "opencode"
            auth_dir.mkdir(parents=True)
            (auth_dir / "auth.json").write_text(
                json.dumps({"anthropic": {"type": "oauth", "access": "sk-SECRET-VALUE-12345"}}),
                encoding="utf-8",
            )
            signals = executor_auth_signals(home=home)
            self.assertEqual(signals["profiles"]["omo-runtime"]["login_marker"], "present")
            self.assertNotIn("sk-SECRET-VALUE-12345", json.dumps(signals))
            self.assertNotIn("anthropic", json.dumps(signals))
            entry = auth_signal_for_profile("omo-runtime", home=home)
            self.assertEqual(entry["login_marker"], "present")


class LimitSignalReadTests(unittest.TestCase):
    def test_missing_state_reads_as_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            self.assertEqual(last_limit_signal_for_profile(paths, "codex"), {})

    def test_recorded_signal_round_trips(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            atomic_write_json(
                paths.executor_limit_signals_path,
                {
                    "schema_version": "executor_limit_signals/v1",
                    "profiles": {"codex": {"last_limit_shaped_at": "2026-07-27T00:00:00Z", "pattern_label": "rate_limit"}},
                },
                private=True,
            )
            entry = last_limit_signal_for_profile(paths, "codex")
            self.assertEqual(entry["pattern_label"], "rate_limit")


class ReadinessAdvisoryFreshnessTests(unittest.TestCase):
    def test_advisories_refresh_while_probe_stays_cached(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            # Seed a cached observed_once probe result so no subprocess runs.
            # `updated_at` and `readiness_binding` are part of the fixture as of
            # #837: `observed_once` alone no longer keeps a decision usable, so
            # a cache entry has to be both fresh and bound to this machine to
            # exercise the advisory-refresh path this test is about.
            atomic_write_json(
                paths.executor_readiness_path,
                {
                    "schema_version": "executor_readiness_cache/v1",
                    "profiles": {
                        "codex": {
                            "schema_version": "executor_readiness/v1",
                            "profile": "codex",
                            "status": "ready",
                            "observed_once": True,
                            "updated_at": utc_now(),
                            "readiness_binding": live_readiness_binding(paths, "codex"),
                        }
                    },
                },
                private=True,
            )
            first = probe_executor_readiness(paths, "codex")
            self.assertEqual(first["cache_status"], "cached")
            self.assertIn("auth_signal", first)
            self.assertEqual(first["last_limit_signal"], {})
            # A limit signal lands later; the cached probe must surface it
            # without --force.
            atomic_write_json(
                paths.executor_limit_signals_path,
                {
                    "schema_version": "executor_limit_signals/v1",
                    "profiles": {"codex": {"pattern_label": "quota"}},
                },
                private=True,
            )
            second = probe_executor_readiness(paths, "codex")
            self.assertEqual(second["cache_status"], "cached")
            self.assertEqual(second["last_limit_signal"]["pattern_label"], "quota")

    def test_advisories_are_not_persisted_into_the_cache(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            probe_executor_readiness(paths, "omx-runtime", force=True)
            stored = json.loads(paths.executor_readiness_path.read_text(encoding="utf-8"))
            profile_entry = stored["profiles"]["omx-runtime"]
            self.assertNotIn("auth_signal", profile_entry)
            self.assertNotIn("last_limit_signal", profile_entry)


class ExecutorChoiceContextTests(unittest.TestCase):
    def test_choice_context_lists_every_cli_candidate_without_probing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            context = executor_choice_context(paths)
            profiles = [entry["profile"] for entry in context["candidates"]]
            # Ranking may reorder by live local login markers; membership and
            # count are the contract.
            self.assertEqual(sorted(profiles), ["claude-code", "codex", "omo-runtime"])
            for entry in context["candidates"]:
                self.assertEqual(entry["readiness_status"], "not_observed")
                self.assertIn("auth_signal", entry)
                self.assertIn("last_limit_signal", entry)
            self.assertIn("never removes a candidate", context["claim_boundary"])

    def test_choice_context_carries_model_inventory_hint(self) -> None:
        """Hermes answers the choose-executor question from what the user
        actually has: the compact inventory hint rides the context, and the
        full report stays behind the named command. Structure-only assertions
        — hint content varies with the local machine by design."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            hint = executor_choice_context(paths)["model_inventory_hint"]
        self.assertEqual(hint["full_report_command"], "omh coding model-inventory")
        self.assertIsInstance(hint["families_present"], list)
        self.assertIsInstance(hint["model_count"], int)
        self.assertIn("reporting-only", hint["claim_boundary"])


if __name__ == "__main__":
    unittest.main()
