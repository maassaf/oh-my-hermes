from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from omh.maintenance.doctor import doctor_ok, run_doctor
from omh.maintenance.hermes_model_routing import (
    HERMES_MODEL_ROUTING_SCHEMA_VERSION,
    hermes_model_routing_preflight,
    model_routing_consistent_summary,
    model_routing_disagreements,
    model_routing_next_action,
)
from omh.paths import OmhPaths


def _make_paths(root: Path) -> OmhPaths:
    resolved = root.resolve()
    return OmhPaths(resolved / ".omh", resolved / ".hermes")


def _write_config(paths: OmhPaths, body: str) -> None:
    paths.hermes_home.mkdir(parents=True, exist_ok=True)
    paths.hermes_config_path.write_text(body, encoding="utf-8")


def _write_auth(paths: OmhPaths, providers: list[str], active: str = "") -> None:
    paths.hermes_home.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        # Values mirror the real file's shape so a regression that starts
        # reading them would surface the marker instead of a provider id.
        "credential_pool": {name: [{"token": f"secret-{name}"}] for name in providers},
    }
    if active:
        payload["active_provider"] = active
    (paths.hermes_home / "auth.json").write_text(json.dumps(payload), encoding="utf-8")


# The reported config: the default names one family, the base URL routes to a
# second host, and the provider names neither.
_INCONSISTENT = """model:
  default: anthropic/claude-opus-4.6
  provider: auto
  base_url: https://openrouter.ai/api/v1
"""


class ModelRoutingDetectionTests(unittest.TestCase):
    def test_reported_three_way_inconsistency_is_named_from_observed_values(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _write_config(paths, _INCONSISTENT)
            _write_auth(paths, ["copilot", "openai-codex"], active="openai-codex")

            preflight = hermes_model_routing_preflight(paths)
            clauses = model_routing_disagreements(preflight)

            self.assertEqual(preflight["schema_version"], HERMES_MODEL_ROUTING_SCHEMA_VERSION)
            self.assertEqual(preflight["default_model"]["family"], "anthropic")
            self.assertFalse(preflight["provider"]["pinned"])
            self.assertEqual(preflight["base_url"]["host"], "openrouter.ai")
            self.assertEqual(len(clauses), 2)
            self.assertIn("anthropic/claude-opus-4.6", clauses[0])
            self.assertIn("openrouter.ai", clauses[0])
            self.assertIn("`auto`", clauses[0])
            self.assertIn("does not change `model.default`", clauses[0])
            self.assertIn("`copilot`, `openai-codex`", clauses[1])
            self.assertIn("active: `openai-codex`", clauses[1])
            self.assertIn("openrouter.ai", model_routing_next_action(preflight))

    def test_credential_clause_is_omitted_when_the_family_is_credentialed(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _write_config(paths, _INCONSISTENT)
            _write_auth(paths, ["anthropic", "openai-codex"], active="anthropic")

            clauses = model_routing_disagreements(hermes_model_routing_preflight(paths))

            self.assertEqual(len(clauses), 1)
            self.assertIn("openrouter.ai", clauses[0])

    def test_base_url_userinfo_never_reaches_the_finding(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _write_config(
                paths,
                "model:\n"
                "  default: anthropic/claude-opus-4.6\n"
                "  provider: auto\n"
                "  base_url: https://user:hunter2@gateway.example.net/v1\n",
            )

            preflight = hermes_model_routing_preflight(paths)
            clauses = model_routing_disagreements(preflight)

            self.assertEqual(preflight["base_url"]["host"], "gateway.example.net")
            self.assertEqual(len(clauses), 1)
            self.assertNotIn("hunter2", clauses[0])

    def test_credential_reader_returns_provider_names_only(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _write_config(paths, _INCONSISTENT)
            _write_auth(paths, ["copilot"], active="copilot")

            credentials = hermes_model_routing_preflight(paths)["credentials"]

            self.assertEqual(credentials["providers"], ["copilot"])
            self.assertNotIn("secret-copilot", json.dumps(credentials))


class ModelRoutingNegativeControlTests(unittest.TestCase):
    """Consistent, unpinnable, and unreadable configs must report nothing."""

    def _clauses(self, body: str) -> list[str]:
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _write_config(paths, body)
            return model_routing_disagreements(hermes_model_routing_preflight(paths))

    def test_base_url_on_the_same_family_is_consistent(self) -> None:
        self.assertEqual(
            self._clauses(
                "model:\n"
                "  default: anthropic/claude-opus-4.6\n"
                "  provider: auto\n"
                "  base_url: https://api.anthropic.com/v1\n"
            ),
            [],
        )

    def test_pinned_gateway_provider_makes_the_prefix_a_namespace(self) -> None:
        # `provider: openrouter` with an `anthropic/` model is the shape Hermes'
        # own config comments document; flagging it would be a false positive.
        self.assertEqual(
            self._clauses(
                "model:\n"
                "  default: anthropic/claude-sonnet-4\n"
                "  provider: openrouter\n"
                "  base_url: https://openrouter.ai/api/v1\n"
            ),
            [],
        )

    def test_custom_provider_pin_with_any_endpoint_is_not_a_finding(self) -> None:
        self.assertEqual(
            self._clauses(
                "model:\n"
                "  default: anthropic/claude-opus-4.6\n"
                "  provider: custom\n"
                "  base_url: https://gateway.internal.example/v1\n"
            ),
            [],
        )

    def test_no_base_url_is_not_a_finding(self) -> None:
        self.assertEqual(
            self._clauses("model:\n  default: anthropic/claude-opus-4.6\n  provider: auto\n"),
            [],
        )

    def test_model_default_without_a_family_prefix_is_not_a_finding(self) -> None:
        self.assertEqual(
            self._clauses(
                "model:\n  default: gpt-5.5\n  provider: auto\n  base_url: https://openrouter.ai/api/v1\n"
            ),
            [],
        )

    def test_missing_model_block_is_not_a_finding(self) -> None:
        self.assertEqual(self._clauses("display:\n  interface: tui\n"), [])

    def test_two_letter_tld_never_matches_a_family_by_accident(self) -> None:
        # `.ai` is a substring of "anthropic"; dropping the suffix label is what
        # keeps `openrouter.ai` from reading as an anthropic host.
        clauses = self._clauses(_INCONSISTENT)
        self.assertEqual(len(clauses), 1)

    def test_duplicate_model_blocks_report_nothing(self) -> None:
        self.assertEqual(
            self._clauses(
                "model:\n  default: anthropic/claude-opus-4.6\n"
                "model:\n  provider: auto\n  base_url: https://openrouter.ai/api/v1\n"
            ),
            [],
        )

    def test_tab_indented_config_reports_nothing(self) -> None:
        self.assertEqual(
            self._clauses("model:\n\tdefault: anthropic/claude-opus-4.6\n\tbase_url: https://openrouter.ai/v1\n"),
            [],
        )

    def test_missing_config_reports_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            preflight = hermes_model_routing_preflight(paths)
            self.assertFalse(preflight["config"]["found"])
            self.assertEqual(model_routing_disagreements(preflight), [])

    def test_unreadable_auth_file_degrades_to_no_credential_clause(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _write_config(paths, _INCONSISTENT)
            paths.hermes_home.joinpath("auth.json").write_text("not json {", encoding="utf-8")

            preflight = hermes_model_routing_preflight(paths)

            self.assertFalse(preflight["credentials"]["observed"])
            self.assertEqual(len(model_routing_disagreements(preflight)), 1)

    def test_consistent_summary_names_the_reason_that_applies(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _write_config(paths, "model:\n  default: anthropic/claude-opus-4.6\n  provider: anthropic\n")
            summary = model_routing_consistent_summary(hermes_model_routing_preflight(paths))
            self.assertIn("pinned to `anthropic`", summary)

        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _write_config(paths, "display:\n  interface: tui\n")
            summary = model_routing_consistent_summary(hermes_model_routing_preflight(paths))
            self.assertIn("no `model.default` is pinned", summary)


class DoctorModelRoutingCheckTests(unittest.TestCase):
    def _check(self, paths: OmhPaths):
        return next(item for item in run_doctor(paths) if item.name == "hermes_model_routing")

    def test_doctor_warns_without_flipping_the_exit_code(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _write_config(paths, _INCONSISTENT)
            _write_auth(paths, ["copilot", "openai-codex"], active="openai-codex")

            checks = run_doctor(paths)
            check = next(item for item in checks if item.name == "hermes_model_routing")

            # A Hermes user-config fault is not an OMH install failure.
            self.assertTrue(check.ok)
            self.assertEqual(check.severity, "warning")
            self.assertIn("anthropic/claude-opus-4.6", check.message)
            self.assertIn("openrouter.ai", check.message)
            self.assertIn("openai-codex", check.message)
            self.assertIn("hermes config set model.provider", check.next_action)
            self.assertEqual(
                doctor_ok(checks),
                doctor_ok([item for item in checks if item.name != "hermes_model_routing"]),
            )

    def test_doctor_reports_ok_for_a_consistent_config(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _make_paths(Path(tmp))
            _write_config(
                paths,
                "model:\n  default: anthropic/claude-opus-4.6\n"
                "  provider: auto\n  base_url: https://api.anthropic.com/v1\n",
            )

            check = self._check(paths)

            self.assertTrue(check.ok)
            self.assertEqual(check.severity, "ok")
            self.assertTrue(check.observed)

    def test_doctor_skips_quietly_when_the_hermes_config_is_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            check = self._check(_make_paths(Path(tmp)))
            self.assertTrue(check.ok)
            self.assertFalse(check.observed)
            self.assertEqual(check.severity, "ok")


if __name__ == "__main__":
    unittest.main()
