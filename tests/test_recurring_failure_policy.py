"""Failure policy for recurring intents: overlap, missed run, retry, backfill, pause.

`tests/test_recurring_intents.py` owns the lifecycle, revision-linkage, store,
and general CLI surface of `hermes_recurring_intent/v1`. This file owns only
what the failure policy adds: the activation gate over the five decisions, the
decision function an approved runtime surface calls, and the deterministic
safety pause.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.paths import OmhPaths
from omh.workflows.recurring_intents import (
    BACKFILL_POSTURES,
    FAILURE_PAUSE_POSTURES,
    FAILURE_POLICY_DECISIONS,
    MISSED_RUN_POSTURES,
    OVERLAP_POSTURES,
    RETRY_POSTURES,
    UNSET_POSTURE,
    activate_recurring_intent,
    applied_recurring_failure_policy,
    build_recurring_intent,
    decide_recurring_occurrence,
    failure_pause_holds_this_revision,
    record_recurring_intent_occurrence,
    recurring_failure_pause_state,
    revise_recurring_intent,
    summarize_recurring_intent,
    unset_failure_policy_decisions,
    update_recurring_intent,
    validate_recurring_intent,
    write_recurring_intent,
)


REQUEST = "every weekday morning at 9am sweep stale pull requests and send a Slack digest only if something changed"

# Every posture set to the safest option the vocabulary offers. These are
# explicit choices, not defaults the builder filled in: `build_recurring_intent`
# leaves all five at `unspecified` when the caller says nothing.
SAFE_POLICY = {
    "overlap_posture": "skip_when_running",
    "missed_run_posture": "skip_missed_window",
    "retry_posture": "no_retry",
    "backfill_posture": "no_backfill",
    "failure_pause_posture": "pause_after_consecutive_failures",
    "failure_pause_threshold": 3,
}

# Which builder keyword feeds which decision, so a test can leave exactly one
# decision unset and keep the other four explicit.
POLICY_KEYWORD_BY_DECISION = {
    "overlap": "overlap_posture",
    "missed_run": "missed_run_posture",
    "retry": "retry_posture",
    "backfill": "backfill_posture",
    "failure_pause": "failure_pause_posture",
}


class FailurePolicyActivationGateTests(unittest.TestCase):
    """AC1: an intent cannot activate until every failure-policy field is explicit."""

    def test_a_fresh_intent_leaves_all_five_decisions_unset(self) -> None:
        intent = build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z")

        self.assertEqual(
            [item.decision for item in unset_failure_policy_decisions(intent)],
            ["overlap", "missed_run", "retry", "backfill", "failure_pause"],
        )
        policy = applied_recurring_failure_policy(intent)
        for decision in ("overlap", "missed_run", "retry", "backfill", "failure_pause"):
            self.assertEqual(policy[decision], UNSET_POSTURE)
        self.assertFalse(summarize_recurring_intent(intent)["failure_policy_complete"])
        self.assertEqual(validate_recurring_intent(intent), [])

    def test_activation_names_each_missing_decision_one_at_a_time(self) -> None:
        for decision in FAILURE_POLICY_DECISIONS:
            with self.subTest(decision=decision.decision):
                kwargs = dict(SAFE_POLICY)
                kwargs[POLICY_KEYWORD_BY_DECISION[decision.decision]] = UNSET_POSTURE
                if decision.decision == "failure_pause":
                    kwargs["failure_pause_threshold"] = 0
                intent = build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **kwargs)

                self.assertEqual(
                    [item.decision for item in unset_failure_policy_decisions(intent)],
                    [decision.decision],
                )
                with self.assertRaises(ValueError) as caught:
                    _activate(intent)
                self.assertEqual(
                    str(caught.exception),
                    f"recurring intent activation requires an explicit {decision.label}",
                )

    def test_an_explicitly_chosen_default_is_accepted_while_an_unset_field_is_not(self) -> None:
        """The gate reads a choice, not a value: `no_retry` passes, silence does not."""
        chosen = build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY)
        unset = build_recurring_intent(
            REQUEST,
            created_at="2026-06-16T00:00:00Z",
            **{**SAFE_POLICY, "retry_posture": UNSET_POSTURE},
        )

        activated = _activate(chosen)

        self.assertEqual(activated["lifecycle"]["state"], "activated")
        self.assertEqual(activated["failure_policy"]["retry"]["posture"], "no_retry")
        self.assertTrue(activated["failure_policy"]["retry"]["explicit"])
        self.assertFalse(activated["failure_policy"]["retry"]["requires_confirmation"])
        self.assertFalse(unset["failure_policy"]["retry"]["explicit"])
        with self.assertRaises(ValueError):
            _activate(unset)

    def test_a_bounded_posture_without_its_count_is_not_a_choice(self) -> None:
        for keyword, posture, message in (
            ("retry_posture", "retry_bounded", "retry_bounded requires a maximum attempt count between 1 and 10"),
            (
                "backfill_posture",
                "backfill_bounded_window",
                "backfill_bounded_window requires a window count between 1 and 10",
            ),
        ):
            with self.subTest(keyword=keyword):
                with self.assertRaises(ValueError) as caught:
                    build_recurring_intent(
                        REQUEST,
                        created_at="2026-06-16T00:00:00Z",
                        **{**SAFE_POLICY, keyword: posture},
                    )

                self.assertEqual(str(caught.exception), message)

    def test_the_record_has_no_vocabulary_for_unlimited_retry_or_full_catch_up(self) -> None:
        self.assertNotIn("retry_unbounded", RETRY_POSTURES)
        self.assertNotIn("backfill_all", BACKFILL_POSTURES)
        self.assertEqual(BACKFILL_POSTURES, (UNSET_POSTURE, "no_backfill", "backfill_bounded_window"))
        intent = build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY)
        self.assertFalse(intent["failure_policy"]["unbounded_retry_supported"])
        self.assertFalse(intent["failure_policy"]["unbounded_backfill_supported"])

    def test_a_hand_edited_activated_record_is_still_refused_by_the_validator(self) -> None:
        activated = _activate(build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY))
        activated["failure_policy"]["missed_run"]["posture"] = UNSET_POSTURE

        errors = validate_recurring_intent(activated)

        self.assertIn("an activated recurring intent must carry an explicit missed-run posture", errors)

    def test_open_decisions_ask_for_each_unset_policy_field_in_plain_language(self) -> None:
        intent = build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z")

        decisions = intent["open_decisions"]

        self.assertIn("choose the overlap posture: skip_when_running, queue_after_running, or allow_concurrent", decisions)
        self.assertIn("choose what a missed window does: skip_missed_window or run_once_when_late", decisions)
        self.assertIn("choose how many consecutive failures pause this intent", decisions)
        self.assertIn("Its failure policy is incomplete, so it cannot be activated yet", intent["human_summary"])

    def test_a_complete_policy_is_read_back_to_the_user_before_activation(self) -> None:
        intent = build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY)

        summary = intent["human_summary"]

        self.assertIn("If a prior run is still active it will skip this window", summary)
        self.assertIn("if a window is missed, the missed window is skipped", summary)
        self.assertIn("a failed occurrence is not retried", summary)
        self.assertIn("older missed windows are never backfilled", summary)
        self.assertIn("pauses itself after 3 consecutive failures", summary)


class OverlapAndMissedRunDecisionTests(unittest.TestCase):
    """AC2: overlapping or missed occurrences never produce silent duplicate work."""

    def test_each_overlap_posture_yields_its_declared_decision(self) -> None:
        expected = {
            "skip_when_running": ("skip", "skipped"),
            "queue_after_running": ("queue", "queued"),
            "allow_concurrent": ("start_concurrent", "ran"),
        }
        for posture, (decision_name, record_as) in expected.items():
            with self.subTest(posture=posture):
                intent = _activate(
                    build_recurring_intent(
                        REQUEST,
                        created_at="2026-06-16T00:00:00Z",
                        **{**SAFE_POLICY, "overlap_posture": posture},
                    )
                )

                decision = decide_recurring_occurrence(
                    intent,
                    situation="prior_run_active",
                    active_run_ref="run-active",
                    now="2026-06-17T09:00:00Z",
                )

                self.assertEqual(decision["decision"], decision_name)
                self.assertEqual(decision["record_occurrence_as"], record_as)
                self.assertEqual(decision["applied_policy"]["overlap"], posture)
                # None of the three is silent: every one of them names the run it
                # would have overlapped and produces an occurrence to record.
                self.assertEqual(decision["overlapped_run_ref"], "run-active")
                self.assertIn("run-active", decision["reason"])
                self.assertNotEqual(decision["record_occurrence_as"], "")

    def test_an_overlap_decision_cannot_be_asked_without_naming_the_active_run(self) -> None:
        intent = _activate(build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY))

        with self.assertRaises(ValueError) as caught:
            decide_recurring_occurrence(intent, situation="prior_run_active", now="2026-06-17T09:00:00Z")

        self.assertIn("requires the run reference of the occurrence still running", str(caught.exception))

    def test_a_skipped_occurrence_is_recorded_without_borrowing_execution_evidence(self) -> None:
        intent = _activate(build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY))
        decision = decide_recurring_occurrence(
            intent,
            situation="prior_run_active",
            active_run_ref="run-active",
            now="2026-06-17T09:00:00Z",
        )

        recorded = record_recurring_intent_occurrence(
            intent,
            runtime_surface="hermes-runtime",
            observer="hermes-runtime",
            outcome=decision["record_occurrence_as"],
            reason=decision["reason"],
            overlapped_run_ref=decision["overlapped_run_ref"],
            observed_at="2026-06-17T09:00:01Z",
        )

        occurrence = recorded["occurrences"][-1]
        self.assertEqual(occurrence["outcome"], "skipped")
        self.assertFalse(occurrence["executed"])
        self.assertEqual(occurrence["runtime_run"]["run_ref"], "")
        self.assertEqual(occurrence["overlapped_run_ref"], "run-active")
        self.assertEqual(occurrence["evidence_authority"], "local_policy_decision")
        self.assertIn("Nothing ran for this occurrence", occurrence["claim_boundary"])
        self.assertEqual(recorded["occurrences_recorded_total"], 1)
        self.assertEqual(validate_recurring_intent(recorded), [])

    def test_a_non_executing_occurrence_may_not_carry_a_run_reference_or_stay_silent(self) -> None:
        intent = _activate(build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY))

        with self.assertRaises(ValueError) as borrowed:
            record_recurring_intent_occurrence(
                intent,
                runtime_run_ref="run-1",
                runtime_surface="hermes-runtime",
                observer="hermes-runtime",
                outcome="skipped",
                reason="overlap",
            )
        with self.assertRaises(ValueError) as silent:
            record_recurring_intent_occurrence(
                intent,
                runtime_surface="hermes-runtime",
                observer="hermes-runtime",
                outcome="missed",
            )

        self.assertIn("must not carry a runtime run reference", str(borrowed.exception))
        self.assertIn("must record why the policy did not run it", str(silent.exception))

    def test_a_forged_skipped_occurrence_with_a_run_reference_is_refused_by_the_validator(self) -> None:
        intent = _activate(build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY))
        recorded = record_recurring_intent_occurrence(
            intent,
            runtime_surface="hermes-runtime",
            observer="hermes-runtime",
            outcome="skipped",
            reason="a prior run was still active",
            observed_at="2026-06-17T09:00:00Z",
        )
        recorded["occurrences"][0]["runtime_run"]["run_ref"] = "run-borrowed"

        errors = validate_recurring_intent(recorded)

        self.assertIn("$.occurrences[0] is a skipped occurrence and must not carry a runtime run reference", errors)

    def test_two_concurrent_runs_stay_distinguishable_instead_of_collapsing(self) -> None:
        intent = _activate(
            build_recurring_intent(
                REQUEST,
                created_at="2026-06-16T00:00:00Z",
                **{**SAFE_POLICY, "overlap_posture": "allow_concurrent"},
            )
        )
        first = record_recurring_intent_occurrence(
            intent,
            runtime_run_ref="run-1",
            runtime_surface="hermes-runtime",
            observer="hermes-runtime",
            observed_at="2026-06-17T09:00:00Z",
        )

        second = record_recurring_intent_occurrence(
            first,
            runtime_run_ref="run-2",
            runtime_surface="hermes-runtime",
            observer="hermes-runtime",
            overlapped_run_ref="run-1",
            observed_at="2026-06-17T09:05:00Z",
        )

        self.assertEqual(second["occurrences"][1]["overlapped_run_ref"], "run-1")
        self.assertEqual(second["occurrences_recorded_total"], 2)
        with self.assertRaises(ValueError):
            record_recurring_intent_occurrence(
                second,
                runtime_run_ref="run-1",
                runtime_surface="hermes-runtime",
                observer="hermes-runtime",
                observed_at="2026-06-17T09:10:00Z",
            )

    def test_each_missed_run_posture_yields_its_declared_decision(self) -> None:
        expected = {
            "skip_missed_window": ("skip", "missed"),
            "run_once_when_late": ("start", "ran"),
        }
        for posture, (decision_name, record_as) in expected.items():
            with self.subTest(posture=posture):
                intent = _activate(
                    build_recurring_intent(
                        REQUEST,
                        created_at="2026-06-16T00:00:00Z",
                        **{**SAFE_POLICY, "missed_run_posture": posture},
                    )
                )

                decision = decide_recurring_occurrence(
                    intent,
                    situation="missed_window",
                    missed_window_count=4,
                    now="2026-06-17T09:00:00Z",
                )

                self.assertEqual(decision["decision"], decision_name)
                self.assertEqual(decision["record_occurrence_as"], record_as)
                self.assertEqual(decision["applied_policy"]["missed_run"], posture)
                self.assertIn("4 window(s) were missed", decision["reason"])

    def test_backfill_is_bounded_by_the_declared_window_count(self) -> None:
        intent = _activate(
            build_recurring_intent(
                REQUEST,
                created_at="2026-06-16T00:00:00Z",
                **{
                    **SAFE_POLICY,
                    "missed_run_posture": "run_once_when_late",
                    "backfill_posture": "backfill_bounded_window",
                    "backfill_max_windows": 2,
                },
            )
        )

        many = decide_recurring_occurrence(
            intent, situation="missed_window", missed_window_count=9, now="2026-06-17T09:00:00Z"
        )
        few = decide_recurring_occurrence(
            intent, situation="missed_window", missed_window_count=2, now="2026-06-17T09:00:00Z"
        )

        self.assertEqual(many["backfill_windows_allowed"], 2)
        self.assertEqual(few["backfill_windows_allowed"], 1)

    def test_no_backfill_allows_nothing_even_when_many_windows_were_missed(self) -> None:
        intent = _activate(
            build_recurring_intent(
                REQUEST,
                created_at="2026-06-16T00:00:00Z",
                **{**SAFE_POLICY, "missed_run_posture": "run_once_when_late"},
            )
        )

        decision = decide_recurring_occurrence(
            intent, situation="missed_window", missed_window_count=9, now="2026-06-17T09:00:00Z"
        )

        self.assertEqual(decision["decision"], "start")
        self.assertEqual(decision["backfill_windows_allowed"], 0)

    def test_retry_allowance_comes_from_the_declared_posture(self) -> None:
        bounded = _activate(
            build_recurring_intent(
                REQUEST,
                created_at="2026-06-16T00:00:00Z",
                **{**SAFE_POLICY, "retry_posture": "retry_bounded", "retry_max_attempts": 2},
            )
        )
        none = _activate(build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY))

        self.assertEqual(
            decide_recurring_occurrence(bounded, situation="on_time", now="2026-06-17T09:00:00Z")[
                "retry_attempts_allowed"
            ],
            2,
        )
        self.assertEqual(
            decide_recurring_occurrence(none, situation="on_time", now="2026-06-17T09:00:00Z")[
                "retry_attempts_allowed"
            ],
            0,
        )

    def test_an_incomplete_policy_refuses_to_decide_anything(self) -> None:
        intent = build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z")

        decision = decide_recurring_occurrence(intent, situation="on_time", now="2026-06-17T09:00:00Z")

        self.assertEqual(decision["decision"], "do_not_start")
        self.assertEqual(decision["record_occurrence_as"], "")
        self.assertIn("its failure policy is incomplete", decision["reason"])
        self.assertEqual(
            decision["unset_policy_decisions"],
            ["overlap", "missed_run", "retry", "backfill", "failure_pause"],
        )

    def test_a_decision_never_claims_omh_ran_or_skipped_anything(self) -> None:
        intent = _activate(build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY))

        decision = decide_recurring_occurrence(intent, situation="on_time", now="2026-06-17T09:00:00Z")

        self.assertEqual(decision["authority"], "local_policy_projection")
        self.assertEqual(decision["enforced_by"], "approved_runtime_surface_outside_omh")
        self.assertIn("OMH runs no scheduler", decision["claim_boundary"])
        self.assertTrue(intent["claim_boundary"]["failure_policy_decided_here_enforced_elsewhere"])

    def test_the_decision_payload_carries_no_wall_clock_of_its_own(self) -> None:
        intent = _activate(build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY))

        first = decide_recurring_occurrence(intent, situation="on_time", now="2026-06-17T09:00:00Z")
        second = decide_recurring_occurrence(intent, situation="on_time", now="2026-06-17T09:00:00Z")

        self.assertEqual(first, second)
        self.assertEqual(first["decided_at"], "2026-06-17T09:00:00Z")


class DeterministicFailurePauseTests(unittest.TestCase):
    """AC3: repeated failure reaches a paused state with a readable explanation."""

    def test_the_threshold_minus_one_does_not_pause(self) -> None:
        record = _fail_times(2)

        pause = recurring_failure_pause_state(record)

        self.assertEqual(record["lifecycle"]["state"], "activated")
        self.assertFalse(pause["paused"])
        self.assertEqual(pause["consecutive_failures"], 2)
        self.assertEqual(pause["threshold"], 3)
        self.assertIn("2 of 3 consecutive failures are recorded", pause["explanation"])
        self.assertFalse(failure_pause_holds_this_revision(record))

    def test_the_threshold_pauses_the_record_with_a_reason_a_person_can_read(self) -> None:
        record = _fail_times(3)

        pause = recurring_failure_pause_state(record)

        self.assertEqual(record["lifecycle"]["state"], "paused")
        self.assertTrue(pause["paused"])
        self.assertEqual(pause["consecutive_failures"], 3)
        transition = record["lifecycle"]["transitions"][-1]
        self.assertEqual(transition["transition"], "paused_by_failure_policy")
        self.assertEqual(transition["intent_revision_id"], record["revision"]["revision_id"])
        self.assertIn("paused itself", transition["reason"])
        self.assertIn("3 consecutive failed occurrences", transition["reason"])
        self.assertIn("reaching the declared threshold of 3", transition["reason"])
        self.assertEqual(record["status_card"]["headline"], "Recurring intent paused by its own failure policy")
        self.assertIn("paused itself", record["human_summary"])
        self.assertFalse(record["required_approval"]["granted"])
        self.assertEqual(validate_recurring_intent(record), [])

    def test_a_success_resets_the_streak_so_the_pause_is_about_consecutive_failures(self) -> None:
        record = _fail_times(2)

        record = record_recurring_intent_occurrence(
            record,
            runtime_run_ref="run-ok",
            runtime_surface="hermes-runtime",
            observer="hermes-runtime",
            outcome="ran",
            observed_at="2026-06-17T12:00:00Z",
        )
        record = record_recurring_intent_occurrence(
            record,
            runtime_run_ref="run-after",
            runtime_surface="hermes-runtime",
            observer="hermes-runtime",
            outcome="failed",
            observed_at="2026-06-17T13:00:00Z",
        )

        self.assertEqual(record["lifecycle"]["state"], "activated")
        self.assertEqual(recurring_failure_pause_state(record)["consecutive_failures"], 1)

    def test_a_skipped_occurrence_neither_breaks_nor_extends_the_failure_streak(self) -> None:
        record = _fail_times(2)

        record = record_recurring_intent_occurrence(
            record,
            runtime_surface="hermes-runtime",
            observer="hermes-runtime",
            outcome="skipped",
            reason="a prior run was still active",
            observed_at="2026-06-17T12:00:00Z",
        )

        self.assertEqual(recurring_failure_pause_state(record)["consecutive_failures"], 2)
        self.assertEqual(record["lifecycle"]["state"], "activated")

    def test_a_paused_intent_cannot_be_re_activated_on_the_same_policy_revision(self) -> None:
        record = _fail_times(3)

        with self.assertRaises(ValueError) as caught:
            _activate(record, approval_ref="approval-2")

        self.assertIn("paused itself after repeated failures under this revision", str(caught.exception))
        self.assertIn("revise the failure policy", str(caught.exception))

    def test_revising_the_policy_clears_the_pause_and_needs_a_fresh_activation(self) -> None:
        record = _fail_times(3)
        paused_revision = record["revision"]["revision_id"]

        revised = revise_recurring_intent(
            record,
            revised_at="2026-06-18T00:00:00Z",
            retry_posture="retry_bounded",
            retry_max_attempts=2,
        )

        self.assertNotEqual(revised["revision"]["revision_id"], paused_revision)
        self.assertFalse(failure_pause_holds_this_revision(revised))
        self.assertEqual(recurring_failure_pause_state(revised)["consecutive_failures"], 0)
        self.assertEqual(revised["lifecycle"]["state"], "paused")
        reactivated = _activate(revised, approval_ref="approval-2")
        self.assertEqual(reactivated["lifecycle"]["state"], "activated")
        self.assertEqual(validate_recurring_intent(reactivated), [])

    def test_a_decision_taken_while_paused_refuses_to_start_and_explains_why(self) -> None:
        record = _fail_times(3)

        decision = decide_recurring_occurrence(record, situation="on_time", now="2026-06-18T09:00:00Z")

        self.assertEqual(decision["decision"], "do_not_start")
        self.assertEqual(decision["record_occurrence_as"], "")
        self.assertIn("paused itself", decision["reason"])
        self.assertTrue(decision["failure_pause"]["paused"])

    def test_a_hand_edited_record_cannot_stay_activated_through_its_own_pause(self) -> None:
        record = _fail_times(3)
        record["lifecycle"]["state"] = "activated"
        record["required_approval"]["granted"] = True

        errors = validate_recurring_intent(record)

        self.assertIn(
            "an activated recurring intent must not carry a failure pause at its current revision: "
            "revise the failure policy and record a fresh activation instead",
            errors,
        )


class PolicyRevisionEvidenceTests(unittest.TestCase):
    """Guards: a policy change bumps the revision, and old occurrences stay old."""

    def test_every_policy_field_change_produces_a_new_revision(self) -> None:
        changes = (
            {"overlap_posture": "allow_concurrent"},
            {"missed_run_posture": "run_once_when_late"},
            {"retry_posture": "retry_bounded", "retry_max_attempts": 2},
            {"backfill_posture": "backfill_bounded_window", "backfill_max_windows": 3},
            {"failure_pause_threshold": 5},
        )
        base = build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY)
        for change in changes:
            with self.subTest(change=sorted(change)):
                revised = revise_recurring_intent(base, revised_at="2026-06-18T00:00:00Z", **change)

                self.assertNotEqual(revised["revision"]["revision_id"], base["revision"]["revision_id"])

    def test_only_the_bound_changing_is_enough_to_bump_the_revision(self) -> None:
        base = build_recurring_intent(
            REQUEST,
            created_at="2026-06-16T00:00:00Z",
            **{**SAFE_POLICY, "retry_posture": "retry_bounded", "retry_max_attempts": 2},
        )

        revised = revise_recurring_intent(base, revised_at="2026-06-18T00:00:00Z", retry_max_attempts=3)

        self.assertNotEqual(revised["revision"]["revision_id"], base["revision"]["revision_id"])
        self.assertEqual(revised["failure_policy"]["retry"]["posture"], "retry_bounded")

    def test_an_occurrence_under_an_old_policy_is_not_evidence_for_the_new_one(self) -> None:
        record = _fail_times(2)
        old_revision = record["revision"]["revision_id"]

        revised = revise_recurring_intent(
            record,
            revised_at="2026-06-18T00:00:00Z",
            failure_pause_threshold=2,
        )
        reactivated = _activate(revised, approval_ref="approval-2")

        # Two failures already exist and the new threshold is two, but they were
        # recorded under the old policy, so they cannot pause the new one.
        self.assertEqual(reactivated["occurrences"][0]["intent_revision_id"], old_revision)
        self.assertEqual(recurring_failure_pause_state(reactivated)["consecutive_failures"], 0)
        self.assertEqual(reactivated["lifecycle"]["state"], "activated")

        failed_once = record_recurring_intent_occurrence(
            reactivated,
            runtime_run_ref="run-new-1",
            runtime_surface="hermes-runtime",
            observer="hermes-runtime",
            outcome="failed",
            observed_at="2026-06-19T09:00:00Z",
        )

        self.assertEqual(failed_once["lifecycle"]["state"], "activated")
        self.assertEqual(recurring_failure_pause_state(failed_once)["consecutive_failures"], 1)

    def test_the_revision_digest_names_every_policy_field(self) -> None:
        intent = build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY)

        digest_inputs = intent["revision"]["digest_inputs"]

        for decision in FAILURE_POLICY_DECISIONS:
            self.assertIn(decision.path, digest_inputs)
        self.assertIn("failure_policy.retry.max_attempts", digest_inputs)
        self.assertIn("failure_policy.backfill.max_windows", digest_inputs)
        self.assertIn("failure_policy.failure_pause.consecutive_failure_threshold", digest_inputs)


class FailurePolicyCliTests(unittest.TestCase):
    def test_the_cli_gates_activation_then_decides_and_records_a_skip(self) -> None:
        with TemporaryDirectory() as tmp:
            base = ["--omh-home", str(Path(tmp) / ".omh"), "ops"]

            status, stdout, stderr = run_cli(base + ["recurring-intent", REQUEST, "--overlap-posture", "skip_when_running"])
            self.assertEqual(status, 0, stderr)
            intent_id = json.loads(stdout)["intent"]["intent_id"]

            status, _stdout, stderr = run_cli(
                base
                + [
                    "recurring-intent-activate",
                    intent_id,
                    "--observer",
                    "hermes-runtime",
                    "--approval-ref",
                    "approval-1",
                    "--activation-surface",
                    "hermes-automation",
                ]
            )
            self.assertNotEqual(status, 0)
            self.assertIn("activation requires an explicit missed-run posture", stderr)
            self.assertIn("activation requires an explicit failure-pause threshold", stderr)

            status, _stdout, stderr = run_cli(
                base
                + [
                    "recurring-intent-revise",
                    intent_id,
                    "--missed-run-posture",
                    "skip_missed_window",
                    "--retry-posture",
                    "no_retry",
                    "--backfill-posture",
                    "no_backfill",
                    "--failure-pause-posture",
                    "pause_after_consecutive_failures",
                    "--failure-pause-threshold",
                    "2",
                ]
            )
            self.assertEqual(status, 0, stderr)

            status, _stdout, stderr = run_cli(
                base
                + [
                    "recurring-intent-activate",
                    intent_id,
                    "--observer",
                    "hermes-runtime",
                    "--approval-ref",
                    "approval-1",
                    "--activation-surface",
                    "hermes-automation",
                ]
            )
            self.assertEqual(status, 0, stderr)

            status, stdout, stderr = run_cli(
                base
                + [
                    "recurring-intent-decide",
                    intent_id,
                    "--situation",
                    "prior_run_active",
                    "--active-run-ref",
                    "run-active",
                    "--now",
                    "2026-06-17T09:00:00Z",
                ]
            )
            self.assertEqual(status, 0, stderr)
            decision = json.loads(stdout)["decision"]
            self.assertEqual(decision["decision"], "skip")
            self.assertEqual(decision["record_occurrence_as"], "skipped")
            self.assertEqual(decision["decided_at"], "2026-06-17T09:00:00Z")

            status, stdout, stderr = run_cli(
                base
                + [
                    "recurring-intent-occurrence",
                    intent_id,
                    "--runtime-surface",
                    "hermes-runtime",
                    "--observer",
                    "hermes-runtime",
                    "--outcome",
                    "skipped",
                    "--reason",
                    decision["reason"],
                    "--overlapped-run-ref",
                    "run-active",
                ]
            )
            self.assertEqual(status, 0, stderr)
            occurrence = json.loads(stdout)["intent"]["occurrences"][0]
            self.assertEqual(occurrence["outcome"], "skipped")
            self.assertFalse(occurrence["executed"])

            status, stdout, stderr = run_cli(base + ["validate"])
            self.assertEqual(status, 0, stderr)
            self.assertTrue(json.loads(stdout)["ok"])

    def test_the_cli_reaches_the_deterministic_pause_and_says_so_in_plain_text(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = OmhPaths(Path(tmp) / ".omh", Path(tmp) / ".hermes")
            base = ["--omh-home", str(paths.omh_home), "ops"]
            record = _activate(
                write_recurring_intent(
                    paths,
                    build_recurring_intent(
                        REQUEST,
                        created_at="2026-06-16T00:00:00Z",
                        **{**SAFE_POLICY, "failure_pause_threshold": 2},
                    ),
                )
            )
            update_recurring_intent(paths, record)
            intent_id = record["intent_id"]

            for index in range(2):
                status, _stdout, stderr = run_cli(
                    base
                    + [
                        "recurring-intent-occurrence",
                        intent_id,
                        "--runtime-run-ref",
                        f"run-{index}",
                        "--runtime-surface",
                        "hermes-runtime",
                        "--observer",
                        "hermes-runtime",
                        "--outcome",
                        "failed",
                    ]
                )
                self.assertEqual(status, 0, stderr)

            status, stdout, stderr = run_cli(base + ["recurring-intent-show", intent_id], output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertIn("lifecycle: paused", stdout)
            self.assertIn("failure pause: pause_after_consecutive_failures (threshold 2)", stdout)
            self.assertIn("consecutive failures at this revision: 2", stdout)
            self.assertIn("paused itself", stdout)

            status, stdout, stderr = run_cli(base + ["recurring-intent-list"], output_json=False)

            self.assertEqual(status, 0, stderr)
            self.assertIn("failure_policy=complete", stdout)
            self.assertIn("paused by its failure policy after 2 consecutive failures", stdout)

    def test_the_cli_refuses_an_overlap_decision_with_no_active_run(self) -> None:
        with TemporaryDirectory() as tmp:
            base = ["--omh-home", str(Path(tmp) / ".omh"), "ops"]
            status, stdout, stderr = run_cli(
                base
                + [
                    "recurring-intent",
                    REQUEST,
                    "--overlap-posture",
                    "skip_when_running",
                    "--missed-run-posture",
                    "skip_missed_window",
                    "--retry-posture",
                    "no_retry",
                    "--backfill-posture",
                    "no_backfill",
                    "--failure-pause-posture",
                    "pause_after_consecutive_failures",
                    "--failure-pause-threshold",
                    "3",
                ]
            )
            self.assertEqual(status, 0, stderr)
            intent_id = json.loads(stdout)["intent"]["intent_id"]

            status, _stdout, stderr = run_cli(
                base + ["recurring-intent-decide", intent_id, "--situation", "prior_run_active"]
            )

            self.assertNotEqual(status, 0)
            self.assertIn("requires the run reference of the occurrence still running", stderr)


class FailurePolicyVocabularyTests(unittest.TestCase):
    def test_every_posture_family_starts_with_the_same_unset_sentinel(self) -> None:
        for postures in (OVERLAP_POSTURES, MISSED_RUN_POSTURES, RETRY_POSTURES, BACKFILL_POSTURES, FAILURE_PAUSE_POSTURES):
            with self.subTest(postures=postures):
                self.assertEqual(postures[0], UNSET_POSTURE)

    def test_the_decision_table_covers_the_five_fields_the_gate_reports(self) -> None:
        self.assertEqual(
            [item.decision for item in FAILURE_POLICY_DECISIONS],
            ["overlap", "missed_run", "retry", "backfill", "failure_pause"],
        )
        for item in FAILURE_POLICY_DECISIONS:
            with self.subTest(decision=item.decision):
                self.assertIn(f"explicit_{item.decision}_decision", _lifecycle_requirements())


def _lifecycle_requirements() -> list[str]:
    return list(
        build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z")["lifecycle"]["activation_requires"]
    )


def _activate(record: dict, *, approval_ref: str = "approval-1") -> dict:
    return activate_recurring_intent(
        record,
        observer="hermes-runtime",
        approval_ref=approval_ref,
        activation_surface="hermes-automation",
        observed_at="2026-06-16T02:00:00Z",
    )


def _fail_times(count: int) -> dict:
    """An activated intent with `count` consecutive failed occurrences recorded."""
    record = _activate(build_recurring_intent(REQUEST, created_at="2026-06-16T00:00:00Z", **SAFE_POLICY))
    for index in range(count):
        record = record_recurring_intent_occurrence(
            record,
            runtime_run_ref=f"run-{index}",
            runtime_surface="hermes-runtime",
            observer="hermes-runtime",
            outcome="failed",
            observed_at=f"2026-06-17T0{index}:00:00Z",
        )
    return record


if __name__ == "__main__":
    unittest.main()
