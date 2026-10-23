"""Contracts for `policy_change_review/v1` (issue #798).

Grouped by acceptance criterion:

- AC1: a proposed policy change lists every affected intent and both sides of
  its behavior. An intent carrying only a before, or only an after, is a
  validation error naming the intent and the missing side.
- AC2: a rejected or superseded review cannot alter active routing or guidance.
  Asserted across the whole state vocabulary rather than on one state, because
  the claim is that being active is unreachable from those two rather than
  merely discouraged.
- AC3: `applied` requires the matching baseline, observed repository changes,
  and named regression evidence. Three tests drop exactly one requirement each
  and a fourth supplies all three, so no single requirement can be the one
  carrying the gate.

Plus the guards the family exists for: a caller-supplied `applied` on an
incomplete review is refused, a baseline mismatch is refused, and a review never
reads as having changed policy source, routing, or a test run.

No test here writes a file. The digests compared are all between payloads built
in the same run, and `prepared_at` is a parameter rather than a clock, so
nothing in this file depends on the newline convention or the wall clock of the
platform it runs on.
"""

from __future__ import annotations

import inspect
import unittest
from typing import Any

from _local_package import load_local_package

load_local_package()
from omh.coding.executors import EXECUTOR_PROFILES  # noqa: E402
from omh.quality.skill_governance import policy_decision_digest  # noqa: E402
from omh.system.append_only_store import RAW_OR_HIDDEN_KEYS  # noqa: E402
from omh.workflows.policy_change_review import (  # noqa: E402
    ACTIVE_REVIEW_STATE,
    BASELINE_KEYS,
    DERIVED_REVIEW_KEYS,
    INTENT_KEYS,
    MATERIAL_REVIEW_KEYS,
    NOT_OBSERVED_SURFACES,
    OUT_OF_SCOPE_SURFACES,
    OWNER_BOUNDARY_CHANGES,
    OWNER_BOUNDARY_KEYS,
    POLICY_CHANGE_OWNERS,
    POLICY_CHANGE_REVIEW_CLAIM_BOUNDARY,
    POLICY_CHANGE_REVIEW_KEYS,
    POLICY_CHANGE_REVIEW_SCHEMA_VERSION,
    POLICY_SURFACES,
    REFUSED_REVIEW_STATES,
    REGRESSION_EVIDENCE_KEYS,
    REGRESSION_OUTCOMES,
    REVIEW_STATE_CLAIMS,
    REVIEW_STATE_KEYS,
    REVIEW_STATES,
    REVIEWER_DECISION_KEYS,
    REVIEWER_DECISION_STATES,
    REVIEWER_DECISIONS,
    ROLLOUT_CLAIM_KEYS,
    ROLLOUT_KEYS,
    ROLLOUT_REQUIREMENTS,
    PolicyChangeReviewError,
    affected_intents,
    build_policy_change_review,
    derive_review_state,
    empty_rollout,
    material_review_content,
    owner_boundary_impact,
    policy_change_baseline_digest,
    policy_change_review_digest,
    record_policy_change_decision,
    record_policy_change_rollout,
    review_reads_as_active,
    review_state_claim,
    reviewer_decision_state,
    rollout_requirements,
    supersede_policy_change_review,
    unmet_rollout_requirements,
    validate_policy_change_review,
)


BASELINE_POLICY = {
    "clarify_threshold": "ambiguous_only",
    "max_questions": 2,
    "runtime_priority": "builtin",
}

MOVED_BASELINE_POLICY = {
    "clarify_threshold": "always",
    "max_questions": 2,
    "runtime_priority": "builtin",
}


def intent_rows() -> list[dict[str, Any]]:
    """Two affected intents: one the change moves, one it deliberately does not."""
    return [
        {
            "intent_ref": "ship-a-small-fix",
            "before": "Hermes asks one scoping question before it prepares the coding handoff.",
            "after": "Hermes prepares the coding handoff and asks nothing.",
        },
        {
            "intent_ref": "plan-a-release",
            "before": "Hermes asks two scoping questions before it drafts the plan.",
            "after": "Hermes asks two scoping questions before it drafts the plan.",
        },
    ]


def owner_rows(**overrides: str) -> list[dict[str, Any]]:
    """One row per coding owner, unaffected unless the caller says otherwise."""
    changes = {owner: "no_boundary_change" for owner in POLICY_CHANGE_OWNERS}
    changes.update(overrides)
    return [{"owner": owner, "boundary_change": change} for owner, change in changes.items()]


def review_kwargs(**overrides: Any) -> dict[str, Any]:
    """A complete, valid proposal's arguments, minimally overridable."""
    base: dict[str, Any] = {
        "review_ref": "policy-review-clarify-01",
        "policy_surface": "clarification",
        "baseline_ref": "clarification-policy-2026-08",
        "baseline_policy": dict(BASELINE_POLICY),
        "intents": intent_rows(),
        "owner_boundaries": owner_rows(**{"codex": "clarification_no_longer_required"}),
        "prepared_at": "2026-08-09T00:00:00Z",
    }
    base.update(overrides)
    return base


def proposed(**overrides: Any) -> dict[str, Any]:
    return build_policy_change_review(**review_kwargs(**overrides))


def approved(**overrides: Any) -> dict[str, Any]:
    return record_policy_change_decision(
        proposed(**overrides), decided_by="maintainer-1", decision="approve_policy_change"
    )


def rejected(**overrides: Any) -> dict[str, Any]:
    return record_policy_change_decision(
        proposed(**overrides), decided_by="maintainer-1", decision="reject_policy_change"
    )


def rolled_out(
    review: dict[str, Any] | None = None,
    *,
    baseline: str | None = None,
    changes: tuple[str, ...] = ("repo-change-9f21c4",),
    evidence: tuple[dict[str, Any], ...] = ({"case_ref": "regression-clarify-01", "outcome": "passed"},),
) -> dict[str, Any]:
    """An approved review carrying rollout evidence, complete unless narrowed."""
    review = review or approved()
    digest = baseline if baseline is not None else str(review["baseline"]["baseline_digest"])
    return record_policy_change_rollout(
        review,
        observed_baseline_digest=digest,
        observed_repository_changes=list(changes),
        regression_evidence=[dict(row) for row in evidence],
    )


def content_moved(review: dict[str, Any]) -> dict[str, Any]:
    """The same review after its material content changed under a live decision.

    Re-seals the digest the way a rebuild from a store would, so the payload is
    self-consistent and the only thing the validator can object to is the state.
    """
    moved = dict(review)
    moved["intents"] = [
        dict(intent_rows()[0], after="Hermes asks two scoping questions and then prepares the handoff."),
        intent_rows()[1],
    ]
    moved["review_digest"] = policy_change_review_digest(moved)
    moved["review_state"] = derive_review_state(moved)
    return moved


def review_in_state(state: str) -> dict[str, Any]:
    """One valid review for each member of the state vocabulary."""
    builders = {
        "awaiting_review": proposed,
        "rejected": rejected,
        "superseded": lambda: supersede_policy_change_review(
            approved(), superseded_by_review_ref="policy-review-clarify-02"
        ),
        "superseded_by_content_change": lambda: content_moved(approved()),
        "approved_not_rolled_out": approved,
        "applied": rolled_out,
    }
    return builders[state]()


class PolicyChangeReviewShapeTests(unittest.TestCase):
    def test_a_proposal_validates_and_carries_the_closed_key_set(self) -> None:
        review = proposed()

        self.assertEqual(validate_policy_change_review(review), [])
        self.assertEqual(sorted(review), sorted(POLICY_CHANGE_REVIEW_KEYS))
        self.assertEqual(review["schema_version"], POLICY_CHANGE_REVIEW_SCHEMA_VERSION)
        self.assertEqual(review["privacy"], "metadata_only")
        self.assertEqual(review["claim_boundary"], POLICY_CHANGE_REVIEW_CLAIM_BOUNDARY)
        self.assertEqual(sorted(review["baseline"]), sorted(BASELINE_KEYS))
        self.assertEqual(sorted(review["rollout"]), sorted(ROLLOUT_KEYS))
        self.assertEqual(review["rollout"], empty_rollout())
        self.assertEqual(review["reviewer_decision"], {})
        self.assertEqual(review["superseded_by_review_ref"], "")

    def test_an_unsupported_key_and_a_missing_key_are_both_refused(self) -> None:
        review = proposed()

        added = dict(review, reviewer_notes="looks fine")
        self.assertIn(
            "policy change review has unsupported keys: ['reviewer_notes']",
            validate_policy_change_review(added),
        )

        removed = {key: value for key, value in review.items() if key != "owner_boundaries"}
        self.assertIn(
            "policy change review is missing keys: ['owner_boundaries']",
            validate_policy_change_review(removed),
        )

    def test_a_payload_asserting_the_change_is_live_is_refused_by_key_name(self) -> None:
        review = dict(proposed(), rolled_out=True)

        errors = validate_policy_change_review(review)

        self.assertTrue(
            any("must not carry rollout-claim keys: ['rolled_out']" in error for error in errors), errors
        )

    def test_a_payload_carrying_raw_or_hidden_content_is_refused(self) -> None:
        review = dict(proposed(), transcript="the whole review call")

        errors = validate_policy_change_review(review)

        self.assertTrue(any("must not carry raw or hidden keys" in error for error in errors), errors)

    def test_rollout_claim_keys_do_not_collide_with_the_schema_or_the_raw_guard(self) -> None:
        self.assertEqual(ROLLOUT_CLAIM_KEYS & set(POLICY_CHANGE_REVIEW_KEYS), set())
        self.assertEqual(ROLLOUT_CLAIM_KEYS & RAW_OR_HIDDEN_KEYS, set())

    def test_the_reviewed_surfaces_are_the_four_the_issue_scopes(self) -> None:
        self.assertEqual(POLICY_SURFACES, ("clarification", "routing", "handoff", "verification"))
        for surface in POLICY_SURFACES:
            with self.subTest(surface=surface):
                self.assertEqual(validate_policy_change_review(proposed(policy_surface=surface)), [])

    def test_reusable_skill_creation_is_refused_as_out_of_scope_by_name(self) -> None:
        for surface in OUT_OF_SCOPE_SURFACES:
            with self.subTest(surface=surface):
                with self.assertRaises(PolicyChangeReviewError) as caught:
                    proposed(policy_surface=surface)
                message = str(caught.exception)
                self.assertIn("out of scope for this family", message)
                self.assertIn("teach-workflow capability", message)

    def test_not_observed_names_every_surface_omh_did_not_touch(self) -> None:
        review = proposed()

        self.assertEqual(
            review["not_observed"],
            {surface: {"status": "not_observed"} for surface in NOT_OBSERVED_SURFACES},
        )
        self.assertIn("policy_source_edit", NOT_OBSERVED_SURFACES)
        self.assertIn("routing_behavior_change", NOT_OBSERVED_SURFACES)
        self.assertIn("repository_write", NOT_OBSERVED_SURFACES)

        broken = dict(review, not_observed={"policy_source_edit": {"status": "observed"}})
        self.assertTrue(
            any("must mark every one of" in error for error in validate_policy_change_review(broken))
        )


class PolicyChangeReviewIntentTests(unittest.TestCase):
    """#798 AC1: every affected intent, each with a before and an after."""

    def test_a_review_lists_every_affected_intent_with_both_behaviors(self) -> None:
        review = proposed()

        self.assertEqual(affected_intents(review), ("ship-a-small-fix", "plan-a-release"))
        for intent in review["intents"]:
            with self.subTest(intent=intent["intent_ref"]):
                self.assertEqual(sorted(intent), sorted(INTENT_KEYS))
                self.assertTrue(intent["before"].strip())
                self.assertTrue(intent["after"].strip())

    def test_an_intent_with_only_a_before_is_refused_naming_the_intent(self) -> None:
        rows = [{"intent_ref": "ship-a-small-fix", "before": "Hermes asks one scoping question."}]

        with self.assertRaises(PolicyChangeReviewError) as caught:
            proposed(intents=rows)

        message = str(caught.exception)
        self.assertIn("'ship-a-small-fix'", message)
        self.assertIn("states no after behavior", message)

        payload = dict(proposed(), intents=rows)
        errors = validate_policy_change_review(payload)
        self.assertTrue(
            any("'ship-a-small-fix' states no after behavior" in error for error in errors), errors
        )

    def test_an_intent_with_only_an_after_is_refused_naming_the_intent(self) -> None:
        rows = [{"intent_ref": "plan-a-release", "after": "Hermes asks nothing."}]

        with self.assertRaises(PolicyChangeReviewError) as caught:
            proposed(intents=rows)

        message = str(caught.exception)
        self.assertIn("'plan-a-release'", message)
        self.assertIn("states no before behavior", message)

        payload = dict(proposed(), intents=rows)
        errors = validate_policy_change_review(payload)
        self.assertTrue(any("'plan-a-release' states no before behavior" in error for error in errors), errors)

    def test_an_intent_whose_behavior_is_blank_on_one_side_is_refused(self) -> None:
        rows = [{"intent_ref": "ship-a-small-fix", "before": "Hermes asks one question.", "after": "   "}]

        with self.assertRaises(PolicyChangeReviewError) as caught:
            proposed(intents=rows)

        self.assertIn("states no after behavior", str(caught.exception))

    def test_an_intent_the_change_does_not_move_may_state_the_same_behavior_twice(self) -> None:
        # Recording "I checked this one and it does not move" is how a review
        # shows the check happened, so identical sides are allowed on purpose.
        rows = [
            {
                "intent_ref": "plan-a-release",
                "before": "Hermes asks two scoping questions.",
                "after": "Hermes asks two scoping questions.",
            }
        ]

        review = proposed(intents=rows)

        self.assertEqual(validate_policy_change_review(review), [])

    def test_a_review_naming_no_intent_is_refused(self) -> None:
        errors = validate_policy_change_review(dict(proposed(), intents=[]))

        self.assertTrue(any("must name at least 1 affected intent" in error for error in errors), errors)

    def test_the_same_intent_is_not_answered_twice(self) -> None:
        rows = [intent_rows()[0], dict(intent_rows()[0], after="Hermes asks three questions.")]

        errors = validate_policy_change_review(dict(proposed(), intents=rows))

        self.assertTrue(
            any("names the same intent more than once: ['ship-a-small-fix']" in error for error in errors),
            errors,
        )

    def test_a_behavior_line_carrying_a_path_or_a_secret_is_refused(self) -> None:
        for bad in ("Hermes reads src/routing/chat.py first.", "Hermes forwards the api_key downstream."):
            with self.subTest(bad=bad):
                with self.assertRaises(PolicyChangeReviewError):
                    proposed(intents=[{"intent_ref": "ship-a-small-fix", "before": bad, "after": "Hermes stops."}])


class PolicyChangeReviewOwnerBoundaryTests(unittest.TestCase):
    def test_owner_boundary_impact_answers_for_every_coding_owner(self) -> None:
        review = proposed()

        impact = owner_boundary_impact(review)

        self.assertEqual(sorted(impact), sorted(EXECUTOR_PROFILES))
        self.assertEqual(impact["codex"], "clarification_no_longer_required")
        self.assertEqual(impact["hermes"], "no_boundary_change")
        for row in review["owner_boundaries"]:
            with self.subTest(owner=row["owner"]):
                self.assertEqual(sorted(row), sorted(OWNER_BOUNDARY_KEYS))

    def test_the_owner_vocabulary_is_the_delegation_one_rather_than_a_second_list(self) -> None:
        self.assertEqual(POLICY_CHANGE_OWNERS, EXECUTOR_PROFILES)
        self.assertIn("claude-code", POLICY_CHANGE_OWNERS)
        self.assertIn("hermes", POLICY_CHANGE_OWNERS)
        self.assertIn("generic", POLICY_CHANGE_OWNERS)

    def test_a_review_that_skips_a_coding_owner_is_refused_naming_the_owner(self) -> None:
        rows = [row for row in owner_rows() if row["owner"] != "claude-code"]

        with self.assertRaises(PolicyChangeReviewError) as caught:
            proposed(owner_boundaries=rows)

        message = str(caught.exception)
        self.assertIn("does not answer for every coding owner: ['claude-code']", message)
        self.assertIn("no_boundary_change", message)

    def test_an_unknown_owner_and_an_unknown_boundary_change_are_both_refused(self) -> None:
        with self.assertRaises(PolicyChangeReviewError):
            proposed(owner_boundaries=[*owner_rows(), {"owner": "gemini", "boundary_change": "no_boundary_change"}])
        with self.assertRaises(PolicyChangeReviewError):
            proposed(owner_boundaries=owner_rows(**{"codex": "everything_changes"}))

    def test_owner_rows_are_ordered_so_two_equal_answers_share_a_digest(self) -> None:
        forward = proposed()
        reversed_rows = list(reversed(owner_rows(**{"codex": "clarification_no_longer_required"})))
        backward = proposed(owner_boundaries=reversed_rows)

        self.assertEqual(forward["owner_boundaries"], backward["owner_boundaries"])
        self.assertEqual(forward["review_digest"], backward["review_digest"])

    def test_no_boundary_change_is_a_real_answer_and_not_an_omission(self) -> None:
        self.assertIn("no_boundary_change", OWNER_BOUNDARY_CHANGES)

        unaffected = proposed(owner_boundaries=owner_rows())
        skipped = [row for row in owner_rows() if row["owner"] != "codex"]

        self.assertEqual(validate_policy_change_review(unaffected), [])
        self.assertTrue(validate_policy_change_review(dict(unaffected, owner_boundaries=skipped)))


class PolicyChangeReviewBaselineTests(unittest.TestCase):
    def test_the_baseline_digest_reuses_the_repository_policy_digest_scheme(self) -> None:
        review = proposed()

        self.assertEqual(
            review["baseline"]["baseline_digest"], policy_decision_digest(dict(BASELINE_POLICY))
        )
        self.assertEqual(
            policy_change_baseline_digest(BASELINE_POLICY), policy_decision_digest(dict(BASELINE_POLICY))
        )

    def test_a_different_policy_produces_a_different_baseline_digest(self) -> None:
        self.assertNotEqual(
            policy_change_baseline_digest(BASELINE_POLICY),
            policy_change_baseline_digest(MOVED_BASELINE_POLICY),
        )

    def test_a_review_measured_against_nothing_is_refused(self) -> None:
        for empty in ({}, None, "clarification-policy"):
            with self.subTest(empty=empty):
                with self.assertRaises(PolicyChangeReviewError) as caught:
                    proposed(baseline_policy=empty)
                self.assertIn("baseline_policy must be a non-empty object", str(caught.exception))

    def test_a_baseline_digest_that_is_not_a_digest_is_refused(self) -> None:
        review = proposed()
        broken = dict(review, baseline=dict(review["baseline"], baseline_digest="not-a-digest"))

        errors = validate_policy_change_review(broken)

        self.assertTrue(any("baseline_digest must be a sha256 hex digest" in error for error in errors), errors)


class PolicyChangeReviewInactivityTests(unittest.TestCase):
    """#798 AC2: a rejected or superseded review is never the active policy."""

    def test_exactly_one_state_reads_as_active_across_the_whole_vocabulary(self) -> None:
        for state in REVIEW_STATES:
            with self.subTest(state=state):
                review = review_in_state(state)
                self.assertEqual(validate_policy_change_review(review), [])
                self.assertEqual(review["review_state"], state)
                self.assertEqual(review_reads_as_active(review), state == ACTIVE_REVIEW_STATE)

    def test_a_rejected_review_cannot_read_as_active_even_with_full_evidence(self) -> None:
        review = rejected()

        self.assertEqual(review["review_state"], "rejected")
        self.assertFalse(review_reads_as_active(review))

        # Rollout evidence is refused on a rejected review, and a hand-written
        # payload that carries it anyway still derives `rejected`.
        with self.assertRaises(PolicyChangeReviewError) as caught:
            rolled_out(review)
        self.assertIn("may only be recorded on a review a reviewer approved", str(caught.exception))

        stapled = dict(review, rollout=rolled_out()["rollout"])
        self.assertEqual(derive_review_state(stapled), "rejected")
        self.assertFalse(review_reads_as_active(stapled))
        self.assertEqual(validate_policy_change_review(stapled), [])

    def test_a_superseded_review_cannot_read_as_active_even_after_being_applied(self) -> None:
        applied = rolled_out()
        self.assertTrue(review_reads_as_active(applied))

        superseded = supersede_policy_change_review(
            applied, superseded_by_review_ref="policy-review-clarify-02"
        )

        self.assertEqual(superseded["review_state"], "superseded")
        self.assertFalse(review_reads_as_active(superseded))
        self.assertEqual(superseded["rollout"], applied["rollout"])
        self.assertEqual(validate_policy_change_review(superseded), [])

    def test_a_superseded_review_is_neither_re_decided_nor_rolled_out(self) -> None:
        superseded = supersede_policy_change_review(
            proposed(), superseded_by_review_ref="policy-review-clarify-02"
        )

        with self.assertRaises(PolicyChangeReviewError) as decision_error:
            record_policy_change_decision(
                superseded, decided_by="maintainer-1", decision="approve_policy_change"
            )
        self.assertIn("cannot record a decision on a review superseded by", str(decision_error.exception))

        with self.assertRaises(PolicyChangeReviewError) as rollout_error:
            rolled_out(superseded)
        self.assertIn("cannot roll out a review superseded by", str(rollout_error.exception))

    def test_a_review_cannot_supersede_itself(self) -> None:
        review = proposed()

        with self.assertRaises(PolicyChangeReviewError) as caught:
            supersede_policy_change_review(review, superseded_by_review_ref=review["review_ref"])
        self.assertIn("must not name the review itself", str(caught.exception))

        self_linked = dict(review, superseded_by_review_ref=review["review_ref"])
        self.assertTrue(
            any("must not name the review itself" in error for error in validate_policy_change_review(self_linked))
        )

    def test_content_that_moves_after_a_decision_supersedes_the_decision(self) -> None:
        applied = rolled_out()
        self.assertEqual(reviewer_decision_state(applied), "current")

        moved = content_moved(applied)

        self.assertEqual(reviewer_decision_state(moved), "stale")
        self.assertEqual(moved["review_state"], "superseded_by_content_change")
        self.assertFalse(review_reads_as_active(moved))
        self.assertEqual(validate_policy_change_review(moved), [])

    def test_the_state_vocabulary_never_borrows_a_word_that_asserts_liveness(self) -> None:
        self.assertEqual(set(REVIEW_STATES) & set(REFUSED_REVIEW_STATES), set())
        for word in REFUSED_REVIEW_STATES:
            with self.subTest(word=word):
                errors = validate_policy_change_review(dict(proposed(), review_state=word))
                self.assertTrue(
                    any("may not assert that the change is" in error for error in errors), errors
                )

    def test_every_state_renders_one_sentence_and_no_other(self) -> None:
        self.assertEqual(sorted(REVIEW_STATE_CLAIMS), sorted(REVIEW_STATES))
        for state in REVIEW_STATES:
            with self.subTest(state=state):
                self.assertTrue(review_state_claim(state).strip())
        with self.assertRaises(PolicyChangeReviewError):
            review_state_claim("live")

    def test_the_decision_state_vocabulary_is_the_declared_one(self) -> None:
        self.assertEqual(REVIEWER_DECISION_STATES, ("absent", "current", "stale"))
        self.assertEqual(reviewer_decision_state(proposed()), "absent")
        self.assertEqual(reviewer_decision_state(approved()), "current")
        self.assertEqual(reviewer_decision_state(content_moved(approved())), "stale")


class PolicyChangeReviewRolloutTests(unittest.TestCase):
    """#798 AC3: applied requires the baseline, the changes, and the evidence."""

    def test_a_rollout_missing_the_matching_baseline_cannot_be_applied(self) -> None:
        review = rolled_out(baseline="")

        self.assertEqual(review["review_state"], "approved_not_rolled_out")
        self.assertFalse(review_reads_as_active(review))
        self.assertEqual(unmet_rollout_requirements(review), ("baseline_matches",))
        self.assertFalse(rollout_requirements(review)["baseline_matches"])
        self.assertTrue(rollout_requirements(review)["repository_changes_observed"])
        self.assertTrue(rollout_requirements(review)["regression_evidence_named"])

    def test_a_rollout_missing_observed_repository_changes_cannot_be_applied(self) -> None:
        review = rolled_out(changes=())

        self.assertEqual(review["review_state"], "approved_not_rolled_out")
        self.assertFalse(review_reads_as_active(review))
        self.assertEqual(unmet_rollout_requirements(review), ("repository_changes_observed",))
        self.assertEqual(review["rollout"]["observed_repository_changes"], [])

    def test_a_rollout_missing_named_regression_evidence_cannot_be_applied(self) -> None:
        review = rolled_out(evidence=())

        self.assertEqual(review["review_state"], "approved_not_rolled_out")
        self.assertFalse(review_reads_as_active(review))
        self.assertEqual(unmet_rollout_requirements(review), ("regression_evidence_named",))
        self.assertEqual(review["rollout"]["regression_evidence"], [])

    def test_all_three_together_reach_applied(self) -> None:
        review = rolled_out()

        self.assertEqual(validate_policy_change_review(review), [])
        self.assertEqual(review["review_state"], ACTIVE_REVIEW_STATE)
        self.assertTrue(review_reads_as_active(review))
        self.assertEqual(unmet_rollout_requirements(review), ())
        self.assertEqual(
            rollout_requirements(review), {name: True for name in ROLLOUT_REQUIREMENTS}
        )
        self.assertEqual(
            review["rollout"]["observed_baseline_digest"], review["baseline"]["baseline_digest"]
        )
        self.assertEqual(
            review["rollout"]["regression_evidence"],
            [{"case_ref": "regression-clarify-01", "outcome": "passed"}],
        )

    def test_a_baseline_observed_from_a_policy_that_moved_is_refused(self) -> None:
        review = rolled_out(baseline=policy_change_baseline_digest(MOVED_BASELINE_POLICY))

        self.assertEqual(review["review_state"], "approved_not_rolled_out")
        self.assertFalse(review_reads_as_active(review))
        self.assertEqual(unmet_rollout_requirements(review), ("baseline_matches",))

        stapled = dict(review, review_state=ACTIVE_REVIEW_STATE)
        errors = validate_policy_change_review(stapled)
        self.assertTrue(any("unmet rollout requirements: ['baseline_matches']" in error for error in errors), errors)

    def test_a_failing_named_regression_case_is_not_evidence_for_a_rollout(self) -> None:
        review = rolled_out(
            evidence=(
                {"case_ref": "regression-clarify-01", "outcome": "passed"},
                {"case_ref": "regression-plan-02", "outcome": "failed"},
            )
        )

        self.assertEqual(review["review_state"], "approved_not_rolled_out")
        self.assertEqual(unmet_rollout_requirements(review), ("regression_evidence_named",))
        self.assertEqual(REGRESSION_OUTCOMES, ("passed", "failed"))

    def test_a_caller_supplied_applied_on_an_incomplete_review_is_refused(self) -> None:
        for narrowed, requirement in (
            (rolled_out(baseline=""), "baseline_matches"),
            (rolled_out(changes=()), "repository_changes_observed"),
            (rolled_out(evidence=()), "regression_evidence_named"),
            (approved(), "baseline_matches"),
            (proposed(), "baseline_matches"),
        ):
            with self.subTest(requirement=requirement):
                stapled = dict(narrowed, review_state=ACTIVE_REVIEW_STATE)
                errors = validate_policy_change_review(stapled)
                self.assertTrue(
                    any("never what a payload asserts" in error for error in errors), errors
                )
                self.assertTrue(any(requirement in error for error in errors), errors)
                self.assertFalse(review_reads_as_active(stapled))

    def test_rollout_evidence_is_refused_on_a_review_no_current_approval_covers(self) -> None:
        for review, label in ((proposed(), "awaiting_review"), (content_moved(approved()), "stale")):
            with self.subTest(label=label):
                with self.assertRaises(PolicyChangeReviewError) as caught:
                    rolled_out(review)
                self.assertIn("may only be recorded on a review a reviewer approved", str(caught.exception))

    def test_partial_rollout_evidence_is_recorded_rather_than_refused(self) -> None:
        review = record_policy_change_rollout(approved(), observed_repository_changes=["repo-change-9f21c4"])

        self.assertEqual(review["review_state"], "approved_not_rolled_out")
        self.assertEqual(
            unmet_rollout_requirements(review), ("baseline_matches", "regression_evidence_named")
        )
        self.assertEqual(validate_policy_change_review(review), [])

    def test_more_rollout_evidence_can_be_added_to_an_already_applied_review(self) -> None:
        review = rolled_out(rolled_out())

        self.assertEqual(review["review_state"], ACTIVE_REVIEW_STATE)
        self.assertTrue(review_reads_as_active(review))

    def test_rollout_evidence_rows_are_closed_and_named(self) -> None:
        review = rolled_out()
        for row in review["rollout"]["regression_evidence"]:
            with self.subTest(case=row["case_ref"]):
                self.assertEqual(sorted(row), sorted(REGRESSION_EVIDENCE_KEYS))

        with self.assertRaises(PolicyChangeReviewError):
            rolled_out(evidence=({"case_ref": "regression-clarify-01", "outcome": "maybe"},))
        with self.assertRaises(PolicyChangeReviewError):
            rolled_out(evidence=({"case_ref": "", "outcome": "passed"},))
        with self.assertRaises(PolicyChangeReviewError):
            rolled_out(changes=("https://example.invalid?pr=1",))

    def test_the_same_regression_case_is_not_counted_twice(self) -> None:
        duplicated = [
            {"case_ref": "regression-clarify-01", "outcome": "passed"},
            {"case_ref": "regression-clarify-01", "outcome": "passed"},
        ]
        review = dict(rolled_out())
        review["rollout"] = dict(review["rollout"], regression_evidence=duplicated)

        errors = validate_policy_change_review(review)

        self.assertTrue(
            any("names the same regression case more than once" in error for error in errors), errors
        )


class PolicyChangeReviewDerivationTests(unittest.TestCase):
    def test_the_builder_exposes_no_state_decision_or_rollout_parameter(self) -> None:
        parameters = set(inspect.signature(build_policy_change_review).parameters)

        self.assertEqual(parameters & set(REVIEW_STATE_KEYS), set())
        self.assertNotIn("review_state", parameters)
        self.assertNotIn("state", parameters)
        self.assertNotIn("status", parameters)

    def test_every_transition_re_derives_the_state(self) -> None:
        review = proposed()
        self.assertEqual(review["review_state"], derive_review_state(review))

        decided = record_policy_change_decision(
            review, decided_by="maintainer-1", decision="approve_policy_change"
        )
        self.assertEqual(decided["review_state"], derive_review_state(decided))

        applied = rolled_out(decided)
        self.assertEqual(applied["review_state"], derive_review_state(applied))

        superseded = supersede_policy_change_review(applied, superseded_by_review_ref="policy-review-02")
        self.assertEqual(superseded["review_state"], derive_review_state(superseded))

    def test_an_unsupported_state_is_refused(self) -> None:
        errors = validate_policy_change_review(dict(proposed(), review_state="under_discussion"))

        self.assertTrue(any("review_state is unsupported" in error for error in errors), errors)

    def test_the_decision_vocabulary_is_the_two_declared_verdicts(self) -> None:
        self.assertEqual(REVIEWER_DECISIONS, ("approve_policy_change", "reject_policy_change"))
        with self.assertRaises(PolicyChangeReviewError):
            record_policy_change_decision(proposed(), decided_by="maintainer-1", decision="looks_fine")

    def test_a_decision_is_bound_to_the_content_it_covers(self) -> None:
        review = approved()

        self.assertEqual(sorted(review["reviewer_decision"]), sorted(REVIEWER_DECISION_KEYS))
        self.assertEqual(
            review["reviewer_decision"]["reviewed_digest"], policy_change_review_digest(review)
        )

        forged = dict(review)
        forged["reviewer_decision"] = dict(review["reviewer_decision"], reviewed_digest="short")
        self.assertTrue(
            any("reviewed_digest must be the sha256 digest" in error for error in validate_policy_change_review(forged))
        )


class PolicyChangeReviewDeterminismTests(unittest.TestCase):
    def test_the_digest_covers_the_material_content_and_nothing_else(self) -> None:
        review = proposed()

        self.assertEqual(sorted(material_review_content(review)), sorted(MATERIAL_REVIEW_KEYS))
        self.assertEqual(set(MATERIAL_REVIEW_KEYS) & set(REVIEW_STATE_KEYS), set())
        self.assertEqual(set(MATERIAL_REVIEW_KEYS) & set(DERIVED_REVIEW_KEYS), set())
        self.assertEqual(
            set(MATERIAL_REVIEW_KEYS) | set(REVIEW_STATE_KEYS) | set(DERIVED_REVIEW_KEYS),
            set(POLICY_CHANGE_REVIEW_KEYS),
        )

    def test_two_reviews_built_from_the_same_content_share_a_digest(self) -> None:
        self.assertEqual(proposed()["review_digest"], proposed()["review_digest"])

    def test_the_clock_field_is_a_parameter_and_stays_out_of_the_digest(self) -> None:
        early = proposed(prepared_at="2026-08-09T00:00:00Z")
        late = proposed(prepared_at="2026-12-31T23:59:59Z")
        unstamped = proposed(prepared_at="")

        self.assertEqual(early["review_digest"], late["review_digest"])
        self.assertEqual(early["review_digest"], unstamped["review_digest"])
        self.assertEqual(unstamped["prepared_at"], "")

    def test_recording_a_decision_or_a_rollout_does_not_move_the_digest(self) -> None:
        review = proposed()
        decided = record_policy_change_decision(
            review, decided_by="maintainer-1", decision="approve_policy_change"
        )
        applied = rolled_out(decided)
        superseded = supersede_policy_change_review(applied, superseded_by_review_ref="policy-review-02")

        for later in (decided, applied, superseded):
            with self.subTest(state=later["review_state"]):
                self.assertEqual(later["review_digest"], review["review_digest"])
                self.assertEqual(reviewer_decision_state(applied), "current")

    def test_a_review_edited_after_it_was_minted_is_refused(self) -> None:
        review = proposed()
        edited = dict(review, policy_surface="routing")

        errors = validate_policy_change_review(edited)

        self.assertTrue(any("does not match the content it seals" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
