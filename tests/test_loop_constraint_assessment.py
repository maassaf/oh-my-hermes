from __future__ import annotations

import copy
import unittest
from typing import Any

from _local_package import load_local_package

load_local_package()
from omh.goal_loop import (
    LOOP_ACTIONS,
    LOOP_CONSTRAINT_ASSESSMENT_CLAIM_BOUNDARY,
    LOOP_CONSTRAINT_CLASSES,
    LOOP_CONSTRAINT_NEXT_ACTION_RELATIONSHIP,
    assess_loop_constraint,
    validate_loop_constraint_assessment,
)
from omh.workflows.goal_loop import _LOOP_CONSTRAINT_GUIDANCE


def _clean_card(**overrides: Any) -> dict[str, Any]:
    """A genuinely clean loop_status_card/v1-shaped fixture.

    The linked block is a REAL completion-gate shape - active status, no
    missing required criteria, no active blockers, every linked runtime check
    satisfied - because a card carrying the unlinked fallback is NOT clean: it
    fires `goal_link_missing` at the last rank, which is the whole point of
    that class existing.
    """
    card: dict[str, Any] = {
        "schema_version": "loop_status_card/v1",
        "loop_id": "loop-clean",
        "wait_reason": "none",
        "blocked_actions": [],
        "next_action": "continue_loop",
        "runtime_summary": {
            "pending_queue_count": 0,
            "unobserved_queue_count": 0,
            "blocked_queue_count": 0,
        },
        "failure_mode_summary": {"warnings": []},
        "linked_goal_completion": {
            "goal_status": "active",
            "missing_required_criteria": [],
            "active_blockers": [],
            "linked_runtime_checks": [
                {"run_id": "run-1", "satisfied": True, "summary": "Observed runtime evidence recorded."},
            ],
        },
    }
    card.update(overrides)
    return card


def _warning(warning_id: str, detail: str) -> dict[str, str]:
    return {"id": warning_id, "state": "warning", "detail": detail, "next_action": "show_loop_status"}


class LoopConstraintAssessmentTests(unittest.TestCase):
    def test_capacity_exhaustion_outranks_every_other_candidate(self) -> None:
        card = _clean_card(
            wait_reason="context_exhausted",
            runtime_summary={"pending_queue_count": 2, "unobserved_queue_count": 3, "blocked_queue_count": 1},
            failure_mode_summary={
                "warnings": [
                    _warning("verification_gap", "A prepared queue item is waiting for observed work."),
                    _warning("comprehension_debt", "Several ticks exist without a feedback checkpoint."),
                ]
            },
        )
        card["linked_goal_completion"]["missing_required_criteria"] = [
            {"id": "crit-1", "summary": "Docs page is published."}
        ]

        payload = assess_loop_constraint(card)

        self.assertEqual(payload["binding_constraint"]["constraint_class"], "capacity_exhausted")
        self.assertEqual(payload["binding_constraint"]["rank"], 1)
        self.assertIn("context_exhausted", payload["binding_constraint"]["summary"])
        self.assertGreater(len(payload["candidates"]), 1)

    def test_permission_block_binds_when_the_envelope_closes_the_next_action(self) -> None:
        card = _clean_card(
            wait_reason="permission_required",
            blocked_actions=["merge", "external_posting"],
            runtime_summary={"pending_queue_count": 1, "unobserved_queue_count": 1, "blocked_queue_count": 0},
        )

        payload = assess_loop_constraint(card)

        self.assertEqual(payload["binding_constraint"]["constraint_class"], "permission_envelope")
        self.assertEqual(payload["binding_constraint"]["evidence_source"], "blocked_actions")
        self.assertIn("2 action(s)", payload["binding_constraint"]["summary"])

    def test_blocked_queue_items_outrank_the_observation_backlog(self) -> None:
        card = _clean_card(
            runtime_summary={"pending_queue_count": 1, "unobserved_queue_count": 2, "blocked_queue_count": 1},
        )

        payload = assess_loop_constraint(card)

        self.assertEqual(payload["binding_constraint"]["constraint_class"], "blocked_queue_item")
        classes = [candidate["constraint_class"] for candidate in payload["candidates"]]
        self.assertLess(classes.index("blocked_queue_item"), classes.index("observation_backlog"))

    def test_pending_queue_ranks_observation_backlog_above_verification_gap(self) -> None:
        """Ranks 5 and 7 co-fire by construction and are emitted, not deduped.

        _verification_gap_mode warns whenever a prepared_not_observed item
        exists - the same predicate as pending_queue_count > 0 - so the pair
        is intentional: rank 5 names the actionable handle (the queue item)
        and rank 7 names the safety consequence (a completion claim would be
        unearned). Do not "fix" this into a dedupe.
        """
        detail = "A prepared queue item is waiting for observed work and verification evidence before the loop can advance."
        card = _clean_card(
            runtime_summary={"pending_queue_count": 1, "unobserved_queue_count": 1, "blocked_queue_count": 0},
            failure_mode_summary={"warnings": [_warning("verification_gap", detail)]},
        )

        payload = assess_loop_constraint(card)

        self.assertEqual(payload["binding_constraint"]["constraint_class"], "observation_backlog")
        classes = [candidate["constraint_class"] for candidate in payload["candidates"]]
        self.assertIn("verification_gap", classes)
        self.assertLess(classes.index("observation_backlog"), classes.index("verification_gap"))
        gap = payload["candidates"][classes.index("verification_gap")]
        self.assertEqual(gap["summary"], detail)

    def test_external_wait_binds_when_no_prepared_work_remains(self) -> None:
        card = _clean_card(wait_reason="waiting_external_observation")

        payload = assess_loop_constraint(card)

        self.assertEqual(payload["binding_constraint"]["constraint_class"], "external_wait")
        self.assertEqual(payload["binding_constraint"]["evidence_source"], "wait_reason")

    def test_cancelled_goal_status_binds_above_every_queue_rank(self) -> None:
        card = _clean_card(
            runtime_summary={"pending_queue_count": 2, "unobserved_queue_count": 3, "blocked_queue_count": 1},
        )
        card["linked_goal_completion"]["goal_status"] = "cancelled"

        payload = assess_loop_constraint(card)

        self.assertEqual(payload["binding_constraint"]["constraint_class"], "goal_status_gap")
        self.assertIn("`cancelled`", payload["binding_constraint"]["summary"])
        self.assertEqual(payload["binding_constraint"]["evidence_source"], "linked_goal_completion.goal_status")

    def test_unsatisfied_required_criterion_comes_from_the_completion_gate(self) -> None:
        """The candidate quotes the gate's own id and summary, so no third
        definition of "unsatisfied required criterion" can appear."""
        card = _clean_card()
        card["linked_goal_completion"]["missing_required_criteria"] = [
            {"id": "crit-docs", "summary": "Docs page is published with observed evidence."},
        ]

        payload = assess_loop_constraint(card)

        binding = payload["binding_constraint"]
        self.assertEqual(binding["constraint_class"], "unsatisfied_required_criterion")
        self.assertIn("crit-docs", binding["summary"])
        self.assertIn("Docs page is published with observed evidence.", binding["summary"])
        self.assertEqual(binding["evidence_source"], "linked_goal_completion.missing_required_criteria")

    def test_unsatisfied_runtime_check_is_named_from_the_gates_own_check(self) -> None:
        card = _clean_card()
        card["linked_goal_completion"]["linked_runtime_checks"] = [
            {"run_id": "run-ok", "satisfied": True, "summary": "Observed."},
            {"run_id": "run-gap", "satisfied": False, "summary": "Linked runtime run was not found."},
            {"run_id": "run-gap-2", "satisfied": False, "summary": "Runtime next action is record_runtime_evidence."},
        ]

        payload = assess_loop_constraint(card)

        binding = payload["binding_constraint"]
        self.assertEqual(binding["constraint_class"], "unsatisfied_runtime_check")
        self.assertIn("run-gap", binding["summary"])
        self.assertIn("Linked runtime run was not found.", binding["summary"])
        self.assertIn("2 unsatisfied runtime checks", binding["summary"])

    def test_only_the_first_entry_of_a_list_source_becomes_a_candidate(self) -> None:
        card = _clean_card()
        card["linked_goal_completion"]["missing_required_criteria"] = [
            {"id": "crit-1", "summary": "First open criterion."},
            {"id": "crit-2", "summary": "Second open criterion."},
            {"id": "crit-3", "summary": "Third open criterion."},
        ]

        payload = assess_loop_constraint(card)

        classes = [candidate["constraint_class"] for candidate in payload["candidates"]]
        self.assertEqual(classes.count("unsatisfied_required_criterion"), 1)
        binding = payload["binding_constraint"]
        self.assertIn("crit-1", binding["summary"])
        self.assertIn("3 missing required criteria", binding["summary"])

    def test_missing_goal_link_is_named_as_a_constraint(self) -> None:
        card = _clean_card(
            runtime_summary={"pending_queue_count": 1, "unobserved_queue_count": 1, "blocked_queue_count": 0},
            linked_goal_completion={"observed": False, "reason": "no linked goal ledger"},
        )

        payload = assess_loop_constraint(card)

        classes = [candidate["constraint_class"] for candidate in payload["candidates"]]
        self.assertIn("goal_link_missing", classes)
        self.assertEqual(classes[-1], "goal_link_missing")
        missing = payload["candidates"][classes.index("goal_link_missing")]
        self.assertEqual(missing["summary"], "no linked goal ledger")
        self.assertEqual(missing["evidence_source"], "linked_goal_completion.reason")

    def test_unlinked_loop_names_goal_link_missing_as_the_only_candidate(self) -> None:
        card = _clean_card(linked_goal_completion={"observed": False, "reason": "no linked goal ledger"})

        payload = assess_loop_constraint(card)

        self.assertEqual(len(payload["candidates"]), 1)
        self.assertEqual(payload["binding_constraint"]["constraint_class"], "goal_link_missing")

    def test_clean_cycle_reports_no_binding_constraint_with_a_reason(self) -> None:
        payload = assess_loop_constraint(_clean_card())

        self.assertIsNone(payload["binding_constraint"])
        self.assertEqual(payload["candidates"], [])
        self.assertNotEqual(payload["no_binding_constraint_reason"], "")

    def test_no_binding_reason_names_every_class_in_the_tuple(self) -> None:
        payload = assess_loop_constraint(_clean_card())

        for name in LOOP_CONSTRAINT_CLASSES:
            self.assertIn(name, payload["no_binding_constraint_reason"])

    def test_every_class_in_the_tuple_has_exploit_subordinate_and_elevate_guidance(self) -> None:
        self.assertEqual(set(_LOOP_CONSTRAINT_GUIDANCE), set(LOOP_CONSTRAINT_CLASSES))
        for name in LOOP_CONSTRAINT_CLASSES:
            guidance = _LOOP_CONSTRAINT_GUIDANCE[name]
            for key in ("exploit", "subordinate", "elevate"):
                self.assertTrue(guidance[key].strip(), f"{name}.{key} must carry drafted guidance")

    def test_assessment_may_disagree_with_next_action_and_says_so(self) -> None:
        """The divergence from _next_action's ordering is deliberate.

        _next_action tests waiting_external_observation FIRST, so the card
        records next_action == "record_external_wait"; the assessment tests
        the queue ranks first, so it names observation_backlog as binding.
        Both are right: the loop's recorded directive is to record the wait,
        and the element actually gating throughput is the prepared work
        nobody has observed.
        """
        card = _clean_card(
            wait_reason="waiting_external_observation",
            next_action="record_external_wait",
            runtime_summary={"pending_queue_count": 1, "unobserved_queue_count": 1, "blocked_queue_count": 0},
        )

        payload = assess_loop_constraint(card)

        self.assertEqual(card["next_action"], "record_external_wait")
        self.assertEqual(payload["binding_constraint"]["constraint_class"], "observation_backlog")
        self.assertEqual(payload["next_action_relationship"], LOOP_CONSTRAINT_NEXT_ACTION_RELATIONSHIP)

    def test_assessment_does_not_mutate_its_input(self) -> None:
        card = _clean_card(
            wait_reason="permission_required",
            blocked_actions=["merge"],
            runtime_summary={"pending_queue_count": 1, "unobserved_queue_count": 1, "blocked_queue_count": 1},
            failure_mode_summary={"warnings": [_warning("verification_gap", "A prepared queue item is waiting.")]},
        )
        snapshot = copy.deepcopy(card)

        assess_loop_constraint(card)

        self.assertEqual(card, snapshot)

    def test_assessment_never_names_a_route_or_a_dispatch(self) -> None:
        card = _clean_card(
            wait_reason="context_exhausted",
            runtime_summary={"pending_queue_count": 1, "unobserved_queue_count": 2, "blocked_queue_count": 1},
            failure_mode_summary={
                "warnings": [
                    _warning("verification_gap", "A prepared queue item is waiting."),
                    _warning("comprehension_debt", "Ticks exist without a feedback checkpoint."),
                    _warning("cognitive_surrender", "Refresh human-owned judgment before self-steering."),
                ]
            },
        )
        card["linked_goal_completion"]["missing_required_criteria"] = [{"id": "crit-1", "summary": "Open."}]
        card["linked_goal_completion"]["active_blockers"] = [{"id": "blk-1", "summary": "Blocked on review."}]
        card["linked_goal_completion"]["linked_runtime_checks"] = [
            {"run_id": "run-gap", "satisfied": False, "summary": "Missing."}
        ]

        payload = assess_loop_constraint(card)

        self.assertEqual(payload["claim_boundary"], LOOP_CONSTRAINT_ASSESSMENT_CLAIM_BOUNDARY)
        for candidate in payload["candidates"]:
            self.assertNotIn(candidate["constraint_class"], LOOP_ACTIONS)
            for key in ("summary", "exploit", "subordinate", "elevate"):
                self.assertNotIn(candidate[key], LOOP_ACTIONS)

    def test_validate_accepts_the_builders_own_output_and_rejects_a_broken_rank(self) -> None:
        card = _clean_card(
            runtime_summary={"pending_queue_count": 1, "unobserved_queue_count": 1, "blocked_queue_count": 1},
        )
        payload = assess_loop_constraint(card)

        self.assertEqual(validate_loop_constraint_assessment(payload), [])
        self.assertEqual(validate_loop_constraint_assessment(assess_loop_constraint(_clean_card())), [])

        broken = copy.deepcopy(payload)
        broken["candidates"][1]["rank"] = 5
        self.assertNotEqual(validate_loop_constraint_assessment(broken), [])


if __name__ == "__main__":
    unittest.main()
