"""Contracts for `external_action_readiness/v1` (issue #809).

Grouped by acceptance criterion:

- AC1: configuration alone never produces "ready now".
- AC2: installed, host-observed, usable-observed, used, and stale are five
  distinguishable answers, not one boolean.
- AC3: invalid evidence preserves the last valid state and explains the gap.

Plus the two guards the answer would be useless without: a stale observation
never reads as ready, and a receipt for a different outcome never answers for
this one.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.coding.context_safety import coding_progress_policy_enforcement
from omh.local_store import atomic_write_text
from omh.mcp.bridge import record_mcp_host_session
from omh.paths import resolve_paths
from omh.plugin_observations import record_plugin_host_observation
from omh.workflows.external_action_readiness import (
    DISTINGUISHED_EVIDENCE_TIERS,
    EVIDENCE_SOURCES,
    EVIDENCE_TIERS,
    EXTERNAL_ACTION_EVIDENCE_KEYS,
    EXTERNAL_ACTION_EVIDENCE_SCHEMA_VERSION,
    EXTERNAL_ACTION_READINESS_CLAIM_BOUNDARY,
    EXTERNAL_ACTION_READINESS_KEYS,
    EXTERNAL_ACTION_READINESS_SCHEMA_VERSION,
    EXTERNAL_ACTION_READINESS_STATES,
    EXTERNAL_ACTION_STALE_AFTER_SECONDS,
    SOURCE_TIERS,
    ExternalActionReadinessError,
    answer_external_action_readiness,
    build_external_action_evidence,
    evidence_from_external_effect_receipt,
    evidence_from_mcp_host_session,
    evidence_from_plugin_host_observation,
    external_action_evidence_errors,
    validate_external_action_readiness_answer,
)
from omh.workflows.external_effect_receipts import build_external_effect_receipt, external_effect_id


NOW = "2026-08-09T12:00:00Z"
FRESH = "2026-08-09T11:00:00Z"
STALE = "2026-08-01T00:00:00Z"
OUTCOME = "message-sent:run-809"
SURFACE = "claude-code"
ACTION = "send the release note to the team channel"


def evidence(
    tier: str,
    source: str,
    result: str = "observed",
    *,
    surface: str = SURFACE,
    outcome_id: str = "",
    observed_at: str = FRESH,
) -> dict:
    return build_external_action_evidence(
        tier=tier,
        source=source,
        result=result,
        surface=surface,
        outcome_id=outcome_id,
        observed_at=observed_at,
    )


def answer(records: list, *, prior: dict | None = None, now: str = NOW, outcome_id: str = OUTCOME) -> dict:
    return answer_external_action_readiness(
        outcome_id=outcome_id,
        action=ACTION,
        surface=SURFACE,
        evidence=records,
        prior=prior,
        now=now,
    )


class ConfigurationIsNeverReadyTests(unittest.TestCase):
    """AC1: configuration alone never produces "ready now"."""

    def test_configuration_present_and_nothing_observed_is_not_ready(self) -> None:
        result = answer([evidence("installed", "local_configuration")])

        self.assertEqual(result["state"], "not_observed")
        self.assertEqual(result["evidence_tier"], "installed")
        self.assertIn("configured locally", result["reason"])
        self.assertEqual(result["next_action"], "observe_the_surface_before_acting")

    def test_the_configuration_source_cannot_claim_a_tier_above_installed(self) -> None:
        # The mechanism behind AC1: a configuration record is refused at the
        # schema, so no caller can route around the rule by relabelling one.
        self.assertEqual(SOURCE_TIERS["local_configuration"], ("installed",))
        for tier in ("host_observed", "usable_observed", "used"):
            with self.subTest(tier=tier):
                with self.assertRaises(ExternalActionReadinessError):
                    evidence(tier, "local_configuration", outcome_id=OUTCOME)

    def test_no_amount_of_configuration_adds_up_to_ready(self) -> None:
        result = answer([evidence("installed", "local_configuration", observed_at=FRESH) for _ in range(5)])

        self.assertEqual(result["state"], "not_observed")
        self.assertEqual(result["accepted_count"], 5)

    def test_a_ready_answer_is_rejected_when_its_tier_cannot_support_one(self) -> None:
        forged = answer([evidence("usable_observed", "plugin_host_observation")])
        forged["state"] = "ready"
        forged["evidence_tier"] = "installed"

        self.assertIn("ready requires fresh usable_observed or used evidence", validate_external_action_readiness_answer(forged))


class EvidenceTierTests(unittest.TestCase):
    """AC2: installed, host-observed, usable-observed, used, and stale."""

    def test_installed_is_a_local_fact_and_not_a_capability(self) -> None:
        result = answer([evidence("installed", "local_configuration")])

        self.assertEqual(result["evidence_tier"], "installed")
        self.assertEqual(result["state"], "not_observed")

    def test_host_observed_means_a_host_reported_loading_it(self) -> None:
        result = answer([evidence("host_observed", "plugin_host_observation")])

        self.assertEqual(result["evidence_tier"], "host_observed")
        # A load is presence, not use, so it still cannot answer "yes, now".
        self.assertEqual(result["state"], "not_observed")
        self.assertIn("reported loading", result["reason"])

    def test_usable_observed_means_a_host_reported_using_it(self) -> None:
        result = answer([evidence("usable_observed", "plugin_host_observation")])

        self.assertEqual(result["evidence_tier"], "usable_observed")
        self.assertEqual(result["state"], "ready")

    def test_used_means_a_receipt_records_this_outcome_succeeding(self) -> None:
        result = answer([evidence("used", "external_effect_receipt", outcome_id=OUTCOME)])

        self.assertEqual(result["evidence_tier"], "used")
        self.assertEqual(result["state"], "ready")

    def test_stale_means_what_was_true_is_past_its_horizon(self) -> None:
        result = answer([evidence("used", "external_effect_receipt", outcome_id=OUTCOME, observed_at=STALE)])

        self.assertEqual(result["evidence_tier"], "stale")
        self.assertEqual(result["state"], "stale")
        self.assertGreater(result["age_seconds"], EXTERNAL_ACTION_STALE_AFTER_SECONDS)

    def test_all_five_tiers_are_distinguishable_from_one_another(self) -> None:
        cases = {
            "installed": [evidence("installed", "local_configuration")],
            "host_observed": [evidence("host_observed", "mcp_host_session")],
            "usable_observed": [evidence("usable_observed", "mcp_host_session")],
            "used": [evidence("used", "external_effect_receipt", outcome_id=OUTCOME)],
            "stale": [evidence("host_observed", "mcp_host_session", observed_at=STALE)],
        }
        observed = {name: answer(records) for name, records in cases.items()}

        self.assertEqual(
            {name: result["evidence_tier"] for name, result in observed.items()},
            {name: name for name in cases},
        )
        self.assertEqual(sorted(cases), sorted(DISTINGUISHED_EVIDENCE_TIERS))
        # Distinguishable is stronger than "five labels exist": no two of the
        # five may collapse into the same (state, tier) pair.
        pairs = [(result["state"], result["evidence_tier"]) for result in observed.values()]
        self.assertEqual(len(set(pairs)), len(pairs))

    def test_the_strongest_fresh_evidence_decides_and_a_weaker_record_cannot_lower_it(self) -> None:
        result = answer(
            [
                evidence("installed", "local_configuration"),
                evidence("host_observed", "plugin_host_observation"),
                evidence("usable_observed", "plugin_host_observation"),
            ]
        )

        self.assertEqual(result["evidence_tier"], "usable_observed")
        self.assertEqual(result["state"], "ready")

    def test_a_blocked_record_is_neither_ready_nor_a_failure(self) -> None:
        result = answer(
            [
                evidence("usable_observed", "plugin_host_observation"),
                evidence("host_observed", "plugin_host_observation", "blocked"),
            ]
        )

        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["next_action"], "clear_the_recorded_blocker")

    def test_a_failed_attempt_outranks_reachability(self) -> None:
        result = answer(
            [
                evidence("usable_observed", "plugin_host_observation"),
                evidence("used", "external_effect_receipt", "failed", outcome_id=OUTCOME),
            ]
        )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["next_action"], "read_the_recorded_failure_before_retrying")

    def test_every_state_carries_the_next_action_it_calls_for(self) -> None:
        cases = {
            "ready": [evidence("usable_observed", "plugin_host_observation")],
            "blocked": [evidence("host_observed", "plugin_host_observation", "blocked")],
            "not_observed": [],
            "stale": [evidence("host_observed", "plugin_host_observation", observed_at=STALE)],
            "failed": [evidence("used", "external_effect_receipt", "failed", outcome_id=OUTCOME)],
        }
        for state, records in cases.items():
            with self.subTest(state=state):
                result = answer(records)
                self.assertEqual(result["state"], state)
                self.assertTrue(result["next_step"].strip())
        self.assertEqual(sorted(cases), sorted(EXTERNAL_ACTION_READINESS_STATES))


class InvalidEvidencePreservesTheLastValidStateTests(unittest.TestCase):
    """AC3: bad evidence explains itself and never overwrites what was known."""

    def _ready(self) -> dict:
        return answer([evidence("used", "external_effect_receipt", outcome_id=OUTCOME)])

    def test_the_prior_state_survives_unreadable_evidence(self) -> None:
        prior = self._ready()

        result = answer([{"schema_version": "external_action_evidence/v1", "tier": "used"}], prior=prior)

        self.assertEqual(result["state"], prior["state"])
        self.assertEqual(result["evidence_tier"], prior["evidence_tier"])
        self.assertEqual(result["state_source"], "preserved_prior")

    def test_the_gap_is_explained_rather_than_silently_absorbed(self) -> None:
        result = answer([{"nonsense": True}], prior=self._ready())

        self.assertEqual(result["evidence_integrity"], "unusable")
        self.assertEqual(result["rejected_count"], 1)
        self.assertEqual([row["index"] for row in result["rejected_evidence"]], [0])
        self.assertTrue(result["rejected_evidence"][0]["reason"].strip())
        self.assertIn("could not be read", result["reason"])

    def test_invalid_evidence_never_upgrades_a_weaker_state(self) -> None:
        prior = answer([evidence("installed", "local_configuration")])
        # A record that would have said "used" if it were readable. It is not.
        forged = {
            "schema_version": EXTERNAL_ACTION_EVIDENCE_SCHEMA_VERSION,
            "tier": "used",
            "source": "external_effect_receipt",
            "result": "observed",
            "surface": SURFACE,
            "outcome_id": OUTCOME,
            "observed_at": FRESH,
            "evidence_ref": "",
            "note": "an unsupported key",
        }

        result = answer([forged], prior=prior)

        self.assertEqual(result["state"], "not_observed")
        self.assertEqual(result["evidence_tier"], "installed")
        self.assertNotEqual(result["state"], "ready")

    def test_invalid_evidence_beside_valid_evidence_never_raises_the_answer(self) -> None:
        result = answer(
            [
                evidence("installed", "local_configuration"),
                {"schema_version": "external_action_evidence/v1"},
            ],
            prior=self._ready(),
        )

        # Valid evidence spoke, so the prior is not consulted; the unreadable
        # record is reported and contributed nothing.
        self.assertEqual(result["state"], "not_observed")
        self.assertEqual(result["state_source"], "derived")
        self.assertEqual(result["evidence_integrity"], "partial")
        self.assertEqual(result["rejected_count"], 1)
        self.assertEqual(result["accepted_count"], 1)

    def test_an_unreadable_prior_is_not_a_weaker_prior(self) -> None:
        result = answer([{"nonsense": True}], prior={"state": "ready", "evidence_tier": "used"})

        self.assertEqual(result["state"], "not_observed")
        self.assertEqual(result["state_source"], "no_valid_evidence")

    def test_a_prior_about_another_outcome_is_never_preserved_into_this_one(self) -> None:
        other = answer_external_action_readiness(
            outcome_id="message-sent:another-run",
            action=ACTION,
            surface=SURFACE,
            evidence=[evidence("used", "external_effect_receipt", outcome_id="message-sent:another-run")],
            now=NOW,
        )
        self.assertEqual(other["state"], "ready")

        result = answer([{"nonsense": True}], prior=other)

        self.assertEqual(result["state"], "not_observed")
        self.assertEqual(result["state_source"], "no_valid_evidence")

    def test_the_rejected_row_list_is_bounded(self) -> None:
        result = answer([{"nonsense": index} for index in range(40)])

        self.assertEqual(result["rejected_count"], 40)
        self.assertEqual(len(result["rejected_evidence"]), 8)


class ReadinessGuardTests(unittest.TestCase):
    def test_stale_never_reads_as_ready(self) -> None:
        for tier, source in (("usable_observed", "plugin_host_observation"), ("used", "external_effect_receipt")):
            with self.subTest(tier=tier):
                result = answer(
                    [evidence(tier, source, outcome_id=OUTCOME if tier == "used" else "", observed_at=STALE)]
                )
                self.assertNotEqual(result["state"], "ready")
                self.assertEqual(result["state"], "stale")
                self.assertEqual(result["evidence_tier"], "stale")

    def test_a_record_exactly_on_the_horizon_is_still_fresh(self) -> None:
        result = answer(
            [evidence("usable_observed", "plugin_host_observation", observed_at="2026-08-09T06:00:00Z")]
        )

        self.assertEqual(result["age_seconds"], EXTERNAL_ACTION_STALE_AFTER_SECONDS)
        self.assertEqual(result["state"], "ready")

    def test_a_receipt_for_a_different_outcome_does_not_satisfy_this_one(self) -> None:
        result = answer([evidence("used", "external_effect_receipt", outcome_id="message-sent:some-other-run")])

        self.assertEqual(result["state"], "not_observed")
        self.assertEqual(result["evidence_tier"], "none")
        self.assertEqual(result["accepted_count"], 0)
        # Not rejected either: it is valid evidence about somebody else's
        # outcome, which is a different thing from unreadable evidence.
        self.assertEqual(result["rejected_count"], 0)

    def test_two_outcomes_over_one_surface_can_answer_differently(self) -> None:
        shared = [
            evidence("usable_observed", "plugin_host_observation"),
            evidence("used", "external_effect_receipt", "failed", outcome_id=OUTCOME),
        ]

        blocked_outcome = answer(shared)
        other_outcome = answer(shared, outcome_id="message-sent:other-run")

        self.assertEqual(blocked_outcome["state"], "failed")
        self.assertEqual(other_outcome["state"], "ready")

    def test_surface_evidence_for_another_surface_is_out_of_scope(self) -> None:
        result = answer([evidence("usable_observed", "plugin_host_observation", surface="codex")])

        self.assertEqual(result["state"], "not_observed")
        self.assertEqual(result["accepted_count"], 0)

    def test_an_unreadable_or_future_timestamp_is_never_fresh(self) -> None:
        for stamp in ("not-a-timestamp", "2027-01-01T00:00:00Z"):
            with self.subTest(stamp=stamp):
                result = answer([evidence("used", "external_effect_receipt", outcome_id=OUTCOME, observed_at=stamp)])
                self.assertEqual(result["state"], "stale")

    def test_freshness_is_derived_at_read_time_and_never_stored(self) -> None:
        record = evidence("used", "external_effect_receipt", outcome_id=OUTCOME)

        self.assertEqual(sorted(record), sorted(EXTERNAL_ACTION_EVIDENCE_KEYS))
        self.assertNotIn("expires_at", record)
        # The same record, read at two different times, answers differently.
        self.assertEqual(answer([record], now=FRESH)["state"], "ready")
        self.assertEqual(answer([record], now="2026-08-10T00:00:00Z")["state"], "stale")


class SchemaTests(unittest.TestCase):
    def test_the_answer_key_set_is_closed_in_both_directions(self) -> None:
        result = answer([evidence("usable_observed", "plugin_host_observation")])

        self.assertEqual(sorted(result), sorted(EXTERNAL_ACTION_READINESS_KEYS))
        self.assertEqual(result["schema_version"], EXTERNAL_ACTION_READINESS_SCHEMA_VERSION)
        self.assertEqual(result["claim_boundary"], EXTERNAL_ACTION_READINESS_CLAIM_BOUNDARY)
        self.assertEqual(validate_external_action_readiness_answer(result), [])

        missing = {key: value for key, value in result.items() if key != "reason"}
        self.assertIn(
            "external action readiness answer is missing keys: ['reason']",
            validate_external_action_readiness_answer(missing),
        )
        extra = {**result, "surprise": 1}
        self.assertIn(
            "external action readiness answer has unsupported keys: ['surprise']",
            validate_external_action_readiness_answer(extra),
        )

    def test_the_evidence_key_set_is_closed_in_both_directions(self) -> None:
        record = evidence("usable_observed", "plugin_host_observation")

        self.assertEqual(external_action_evidence_errors(record), [])
        self.assertTrue(external_action_evidence_errors({**record, "surprise": 1}))
        self.assertTrue(external_action_evidence_errors({key: value for key, value in record.items() if key != "tier"}))

    def test_the_answer_is_metadata_only(self) -> None:
        with self.assertRaises(ExternalActionReadinessError):
            answer_external_action_readiness(
                outcome_id=OUTCOME,
                action="send https://example.invalid/hook",
                surface=SURFACE,
                evidence=[],
                now=NOW,
            )
        with self.assertRaises(ExternalActionReadinessError):
            evidence("installed", "local_configuration", surface="https://example.invalid/host")

    def test_raw_and_hidden_keys_are_refused_by_name(self) -> None:
        record = {**evidence("installed", "local_configuration"), "prompt": "..."}

        self.assertTrue(any("raw or hidden keys" in error for error in external_action_evidence_errors(record)))

    def test_used_tier_evidence_must_name_the_outcome_it_observed(self) -> None:
        with self.assertRaises(ExternalActionReadinessError):
            evidence("used", "external_effect_receipt")

    def test_every_source_declares_the_tiers_it_may_claim(self) -> None:
        self.assertEqual(sorted(SOURCE_TIERS), sorted(EVIDENCE_SOURCES))
        for source, tiers in SOURCE_TIERS.items():
            with self.subTest(source=source):
                self.assertTrue(tiers)
                self.assertLessEqual(set(tiers), set(EVIDENCE_TIERS))


class StoreAdapterTests(unittest.TestCase):
    """The recorded shapes the four evidence tiers actually come from."""

    def test_a_plugin_tool_call_reads_as_usable_observed(self) -> None:
        record = {
            "host": SURFACE,
            "session_id": "session-1",
            "event": "tool_call",
            "status": "observed",
            "observed_at": FRESH,
        }

        self.assertEqual(evidence_from_plugin_host_observation(record)["tier"], "usable_observed")

    def test_a_plugin_load_reads_as_host_observed(self) -> None:
        record = {"host": SURFACE, "session_id": "s", "event": "plugin_load", "status": "observed", "observed_at": FRESH}

        self.assertEqual(evidence_from_plugin_host_observation(record)["tier"], "host_observed")

    def test_an_unobserved_plugin_event_reads_as_installed_only(self) -> None:
        record = {"host": SURFACE, "session_id": "s", "event": "plugin_load", "status": "not_observed", "recorded_at": FRESH}
        adapted = evidence_from_plugin_host_observation(record)

        self.assertEqual(adapted["tier"], "installed")
        self.assertEqual(adapted["result"], "observed")

    def test_a_blocked_host_event_reads_as_a_blocker(self) -> None:
        record = {"host": SURFACE, "session_id": "s", "event": "tool_call", "status": "blocked", "recorded_at": FRESH}

        self.assertEqual(evidence_from_plugin_host_observation(record)["result"], "blocked")

    def test_an_mcp_tool_call_reads_as_usable_observed_and_a_load_does_not(self) -> None:
        base = {"host": SURFACE, "session_id": "s", "status": "observed", "observed_at": FRESH}

        self.assertEqual(evidence_from_mcp_host_session({**base, "event": "tool_call"})["tier"], "usable_observed")
        self.assertEqual(evidence_from_mcp_host_session({**base, "event": "host_load"})["tier"], "host_observed")

    def test_a_succeeded_receipt_reads_as_used_and_a_failed_one_as_a_failure(self) -> None:
        succeeded = build_external_effect_receipt(
            effect_id=external_effect_id("ci", "run-809"),
            action="ci_run",
            acting_surface="runtime_ci_record",
            observed_result="succeeded",
            run_id="run-809",
            external_ref="check-1",
            observed_at=FRESH,
        )
        failed = build_external_effect_receipt(
            effect_id=external_effect_id("ci", "run-809"),
            action="ci_run",
            acting_surface="runtime_ci_record",
            observed_result="failed",
            run_id="run-809",
            observed_at=FRESH,
        )

        self.assertEqual(evidence_from_external_effect_receipt(succeeded)["result"], "observed")
        self.assertEqual(evidence_from_external_effect_receipt(succeeded)["outcome_id"], "ci:run-809")
        self.assertEqual(evidence_from_external_effect_receipt(failed)["result"], "failed")

    def test_an_unclassifiable_receipt_is_not_evidence_either_way(self) -> None:
        for observed_result in ("attempted", "unknown"):
            with self.subTest(observed_result=observed_result):
                receipt = build_external_effect_receipt(
                    effect_id=external_effect_id("ci", "run-809"),
                    action="ci_run",
                    acting_surface="runtime_ci_record",
                    observed_result=observed_result,
                    run_id="run-809",
                    observed_at=FRESH,
                )
                self.assertIsNone(evidence_from_external_effect_receipt(receipt))

    def test_a_record_the_adapter_cannot_read_is_dropped_rather_than_raised(self) -> None:
        self.assertIsNone(evidence_from_plugin_host_observation({"host": "", "event": "tool_call", "status": "observed"}))
        self.assertIsNone(evidence_from_plugin_host_observation("not a record"))
        self.assertIsNone(evidence_from_mcp_host_session(None))


class ActionReadinessCliTests(unittest.TestCase):
    def _paths(self, root: Path):
        return resolve_paths(root / ".omh", root / ".hermes")

    def _base(self, paths) -> list[str]:
        return ["--omh-home", str(paths.omh_home), "--hermes-home", str(paths.hermes_home)]

    def test_plain_text_is_the_default_and_json_is_the_opt_in(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            record_plugin_host_observation(
                paths,
                host=SURFACE,
                session_id="session-1",
                event="plugin_load",
                status="not_observed",
            )
            command = ["runtime", "action-readiness", "--outcome", OUTCOME, "--surface", SURFACE, "--action", ACTION]

            status, stdout, stderr = run_cli(self._base(paths) + command, output_json=False)

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            self.assertNotIn("{", stdout)
            self.assertIn("Can Hermes do this now?", stdout)
            self.assertIn("State: not_observed (strongest evidence: installed)", stdout)

            status, stdout, stderr = run_cli(self._base(paths) + command + ["--json"])

            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["schema_version"], "runtime_action_readiness_view/v1")
            self.assertEqual(payload["answer"]["state"], "not_observed")
            self.assertEqual(payload["answer"]["evidence_tier"], "installed")
            self.assertEqual(payload["evidence_sources"]["plugin_host_observation"], 1)

    def test_an_observed_host_tool_call_makes_the_answer_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            record_mcp_host_session(
                paths,
                host=SURFACE,
                session_id="session-1",
                event="tool_call",
                status="observed",
                tool="omh_status",
                evidence_refs=["host-session-log-1"],
            )

            status, stdout, _ = run_cli(
                self._base(paths)
                + ["runtime", "action-readiness", "--outcome", OUTCOME, "--surface", SURFACE, "--json"]
            )

            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["answer"]["state"], "ready")
            self.assertEqual(payload["answer"]["evidence_tier"], "usable_observed")
            self.assertEqual(payload["evidence_sources"]["mcp_host_session"], 1)

    def test_a_corrupt_store_line_is_reported_and_does_not_erase_valid_records(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            record_plugin_host_observation(
                paths,
                host=SURFACE,
                session_id="session-1",
                event="tool_call",
                status="observed",
                tool="omh_status",
                evidence_refs=["host-log-1"],
            )
            store = paths.runtime_plugin_host_observations_path
            atomic_write_text(store, store.read_text(encoding="utf-8") + "not json at all\n", private=True)

            status, stdout, _ = run_cli(
                self._base(paths)
                + ["runtime", "action-readiness", "--outcome", OUTCOME, "--surface", SURFACE, "--json"]
            )

            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            # The valid observation still answers; the unreadable line is a
            # reported fault, not a silent downgrade.
            self.assertEqual(payload["answer"]["state"], "ready")
            self.assertEqual(payload["store_error_count"], 1)
            self.assertTrue(payload["store_errors"])

    def test_the_surface_is_read_only_and_scopes_by_host(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            record_plugin_host_observation(
                paths,
                host="codex",
                session_id="session-1",
                event="tool_call",
                status="observed",
                tool="omh_status",
                evidence_refs=["host-log-1"],
            )

            status, stdout, _ = run_cli(
                self._base(paths)
                + ["runtime", "action-readiness", "--outcome", OUTCOME, "--surface", SURFACE, "--json"]
            )

            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["answer"]["state"], "not_observed")
            self.assertEqual(payload["evidence_sources"]["plugin_host_observation"], 0)

    def test_the_polled_surface_is_registered_as_bounded(self) -> None:
        self.assertIn("omh runtime action-readiness", coding_progress_policy_enforcement()["bounded_surfaces"])


if __name__ == "__main__":
    unittest.main()
