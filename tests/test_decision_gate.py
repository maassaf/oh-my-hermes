"""Contracts for `decision_gate/v1` (issue #825).

The acceptance criteria this holds, and where each one is asserted:

AC1  a blocked workflow identifies one open gate and checkpoint
     -> `ABlockedWorkflowIdentifiesOneOpenGateAndCheckpoint`
AC2  a resume records actor, choice, timestamp, and revisions
     -> `AResumeRecordsWhoChoseWhatWhenAndUnderWhichRevisions`
AC3  no gated action continues while the decision is pending or mismatched
     -> `APendingDecisionReleasesNothing`,
        `AnExpiredDecisionReleasesNothing`,
        `AMismatchedDecisionReleasesNothing`

Plus the three things this family is worthless without. That the question and
its answer survive a restart (`TheGateAndItsAnswerSurviveARestart`) -- durability
is the whole of what #825 asks for, and a store nobody re-reads proves none of
it. That the store is wired into the registry and the validator its siblings use
(`TheStoreIsRegisteredBesideItsSiblings`), without which nothing in production
ever runs these checks. And that the name `decision_gate` this repo was already
using in two other places is coexisted with rather than collided into
(`TheNameThatWasAlreadyHere`).

Store mechanics -- concurrent appends, torn tails, chain forks -- are inherited
from `system/append_only_store.py` and proved there. What is asserted here is
this family's own contribution: the binding between an answer and the one
transition it releases, the one-open-gate rule in both its enforcing and its
at-rest form, and the derivations that make an over-reaching answer
unconstructible rather than merely unusual.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from _local_package import load_local_package

load_local_package()
from omh.catalogs.playbooks import inspect_playbook, list_playbooks  # noqa: E402
from omh.local_store import atomic_write_text  # noqa: E402
from omh.runtime.artifacts import validate_runtime  # noqa: E402
from omh.runtime.records import (  # noqa: E402
    DECISION_GATE_RECORD_KEYS,
    OPTIONAL_RUNTIME_STORE_VALIDATORS,
)
from omh.system.append_only_store import RAW_OR_HIDDEN_KEYS  # noqa: E402
from omh.system.paths import OmhPaths  # noqa: E402
from omh.workflows.approval_receipts import (  # noqa: E402
    APPROVAL_TTL_SECONDS,
    REFUSAL_ABSENT,
    REFUSAL_CODES,
    REFUSAL_EXPIRED,
    REFUSAL_REVISION,
    REFUSAL_RUN,
    REFUSAL_SCOPE,
    REFUSAL_SUPERSEDED,
    SCOPE_CLASSES,
    SCOPE_REFUSAL_CODES,
)
from omh.workflows.decision_gates import (  # noqa: E402
    BLOCKED_TRANSITIONS,
    CHOICE_CONSEQUENCES,
    CHOICE_RELEASES,
    CLAIM_BOUNDARY,
    DECISION_GATE_KEYS,
    DECISION_GATE_SCHEMA_VERSION,
    EXECUTION_CLAIM_KEYS,
    GATE_CHOICES,
    GATE_REFUSAL_CODES,
    GATE_REFUSAL_REASONS,
    GATE_STATES,
    QUESTION_CODES,
    QUESTION_TEXT,
    REFUSAL_DECLINED,
    REFUSAL_PENDING,
    REFUSAL_TRANSITION,
    RESUME_DIGEST_KEYS,
    REUSED_REFUSAL_CODES,
    RISK_CLASSES,
    RISK_TEXT,
    SUBJECT_CLASSES,
    DecisionGateError,
    answer_decision_gate,
    append_decision_gate,
    blocked_decision_gate,
    build_decision_gate,
    build_decision_gate_answer,
    compact_decision_gate,
    gate_id,
    gate_subject_id,
    open_decision_gate,
    open_gates_in,
    project_blocked_decision_gate,
    read_decision_gates,
    resume_digest,
    resume_satisfies_gate,
    resume_satisfies_gate_in,
    validate_decision_gate,
    validate_decision_gate_store,
)
from omh.workflows.hermes_planning import build_hermes_plan_payload  # noqa: E402
from omh.workflows.run_lineage import EXECUTION_CLAIM_KEYS as LINEAGE_EXECUTION_CLAIM_KEYS  # noqa: E402
from omh.workflows.run_lineage import LINEAGE_TRANSITIONS  # noqa: E402


_RUN = "run-2026-08-09-aaaa"
_OTHER_RUN = "run-2026-08-09-bbbb"
_APPROVER = "operator-anna"
_OPENED = "2026-08-09T10:00:00Z"
_DECIDED = "2026-08-09T10:05:00Z"
# Six minutes after the answer: inside the window by a wide margin.
_SOON = "2026-08-09T10:06:00Z"
# Three hours after the answer, against a one-hour window.
_LATE = "2026-08-09T13:00:00Z"

_QUESTION: dict[str, Any] = {
    "run_id": _RUN,
    "subject_class": "filesystem_path",
    "subject_ref": "src/auth/session.py",
    "blocked_transition": "executor_dispatch_observed",
    "checkpoint_ref": "checkpoint-0004",
    "question_code": "confirm_destructive_action",
    "risk_class": "irreversible",
    "choices": ("approve", "decline"),
    "approver": _APPROVER,
    "safety_profile_revision": "safety-rev-7",
    "context_revision": "context-rev-3",
}

# The seven dimensions a resume is matched on, as a caller holds them.
_RESUME: dict[str, Any] = {
    "run_id": _RUN,
    "subject_class": "filesystem_path",
    "subject_ref": "src/auth/session.py",
    "blocked_transition": "executor_dispatch_observed",
    "checkpoint_ref": "checkpoint-0004",
    "safety_profile_revision": "safety-rev-7",
    "context_revision": "context-rev-3",
}


def _paths(root: Path) -> OmhPaths:
    return OmhPaths(root / ".omh", root / ".hermes")


def _gate(**overrides: Any) -> dict[str, Any]:
    """One open gate, built without touching a store."""
    return build_decision_gate(**{**_QUESTION, "opened_at": _OPENED, **overrides})


def _answered(**overrides: Any) -> dict[str, Any]:
    """One answered gate, built from the open record it answers."""
    choice = overrides.pop("choice", "approve")
    actor = overrides.pop("actor", _APPROVER)
    decided_at = overrides.pop("decided_at", _DECIDED)
    return build_decision_gate_answer(_gate(**overrides), actor=actor, choice=choice, decided_at=decided_at)


def _opened_and_answered(paths: OmhPaths, **overrides: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """One question on the store, and its answer, through the live entry points."""
    choice = overrides.pop("choice", "approve")
    actor = overrides.pop("actor", _APPROVER)
    gate = open_decision_gate(paths, now=_OPENED, **{**_QUESTION, **overrides})
    answer = answer_decision_gate(
        paths, gate_id_value=gate["gate_id"], actor=actor, choice=choice, now=_DECIDED
    )
    return gate, answer


# ---------------------------------------------------------------------------
# AC1
# ---------------------------------------------------------------------------


class ABlockedWorkflowIdentifiesOneOpenGateAndCheckpoint(unittest.TestCase):
    """#825 AC1. One question, one checkpoint, and no way to have two."""

    def test_a_blocked_run_names_exactly_one_open_gate_and_its_checkpoint(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            gate = open_decision_gate(paths, now=_OPENED, **_QUESTION)

            view = blocked_decision_gate(paths, run_id=_RUN, now=_SOON)

            self.assertTrue(view["ok"])
            self.assertTrue(view["blocked"])
            self.assertEqual(view["open_gate_count"], 1)
            self.assertEqual(view["gate"]["gate_id"], gate["gate_id"])
            self.assertEqual(view["checkpoint_ref"], "checkpoint-0004")
            self.assertEqual(view["blocked_transition"], "executor_dispatch_observed")
            # The question and its consequences are rendered from this module's
            # constants, which is what lets the record carry no free text.
            self.assertEqual(view["gate"]["question"], QUESTION_TEXT["confirm_destructive_action"])
            self.assertEqual(view["gate"]["risk"], RISK_TEXT["irreversible"])
            self.assertEqual(
                [choice["choice"] for choice in view["gate"]["choices"]], ["approve", "decline"]
            )
            self.assertEqual(
                [choice["releases"] for choice in view["gate"]["choices"]], [True, False]
            )
            for choice in view["gate"]["choices"]:
                self.assertEqual(choice["consequence"], CHOICE_CONSEQUENCES[choice["choice"]])

    def test_a_run_with_no_open_gate_is_not_blocked(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            view = blocked_decision_gate(paths, run_id=_RUN, now=_SOON)

            self.assertTrue(view["ok"])
            self.assertFalse(view["blocked"])
            self.assertEqual(view["open_gate_count"], 0)
            self.assertEqual(view["gate"], {})
            self.assertEqual(view["checkpoint_ref"], "")

    def test_answering_the_gate_leaves_the_run_unblocked(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _opened_and_answered(paths)

            view = blocked_decision_gate(paths, run_id=_RUN, now=_SOON)

            self.assertFalse(view["blocked"])
            self.assertEqual(view["open_gate_count"], 0)

    def test_a_second_open_gate_for_the_same_subject_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            open_decision_gate(paths, now=_OPENED, **_QUESTION)

            with self.assertRaises(DecisionGateError) as raised:
                open_decision_gate(
                    paths,
                    now=_OPENED,
                    **{
                        **_QUESTION,
                        "blocked_transition": "merge_observed",
                        "checkpoint_ref": "checkpoint-0009",
                    },
                )

            self.assertIn("open gate", str(raised.exception))
            self.assertEqual(len(read_decision_gates(paths)), 1)

    def test_reasking_the_same_question_returns_the_gate_already_on_record(self) -> None:
        # One block reported three times is one question, not three. Without
        # this a surface that retries would fill the store with duplicates and
        # then trip its own one-open-gate rule.
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            first = open_decision_gate(paths, now=_OPENED, **_QUESTION)
            again = open_decision_gate(paths, now=_SOON, **_QUESTION)

            self.assertEqual(again["record_id"], first["record_id"])
            self.assertEqual(len(read_decision_gates(paths)), 1)

    def test_another_subject_may_have_its_own_open_gate(self) -> None:
        # The rule is one gate per *subject*, not one per run. Two subjects
        # blocked at once is a normal state and must not be refused.
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            open_decision_gate(paths, now=_OPENED, **_QUESTION)
            open_decision_gate(paths, now=_OPENED, **{**_QUESTION, "subject_ref": "src/auth/rotate.py"})

            view = blocked_decision_gate(paths, run_id=_RUN, now=_SOON)

            self.assertEqual(view["open_gate_count"], 2)
            # Two subjects is legal; naming one of them as *the* question is not.
            self.assertFalse(view["ok"])
            self.assertEqual(view["gate"], {})

    def test_a_store_already_holding_two_open_gates_for_one_subject_is_a_fault(self) -> None:
        # `open_decision_gate` refuses to write the second one, so reaching this
        # state means the store was hand-edited or written by an older build.
        # The rule has to survive the file, not only the writer.
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            append_decision_gate(paths, _gate())
            append_decision_gate(
                paths,
                _gate(blocked_transition="merge_observed", checkpoint_ref="checkpoint-0009"),
            )

            report = validate_decision_gate_store(paths.runtime_decision_gates_path)

            self.assertFalse(report["ok"])
            self.assertEqual(report["open_gate_count"], 2)
            self.assertEqual(report["contested_subject_count"], 1)
            self.assertTrue(any("open gates" in error for error in report["errors"]), report["errors"])
            self.assertFalse(validate_runtime(paths)["ok"])

    def test_the_subject_id_counts_the_subject_and_not_the_question(self) -> None:
        one = gate_subject_id(run_id=_RUN, subject_class="filesystem_path", subject_ref="src/a.py")
        same_subject_other_question = _gate(
            blocked_transition="merge_observed", checkpoint_ref="checkpoint-0009"
        )
        self.assertEqual(_gate()["subject_id"], same_subject_other_question["subject_id"])
        self.assertNotEqual(_gate()["gate_id"], same_subject_other_question["gate_id"])
        self.assertNotEqual(
            one, gate_subject_id(run_id=_RUN, subject_class="filesystem_path", subject_ref="src/b.py")
        )
        self.assertNotEqual(
            one, gate_subject_id(run_id=_OTHER_RUN, subject_class="filesystem_path", subject_ref="src/a.py")
        )


# ---------------------------------------------------------------------------
# AC2
# ---------------------------------------------------------------------------


class AResumeRecordsWhoChoseWhatWhenAndUnderWhichRevisions(unittest.TestCase):
    """#825 AC2. An answer that is missing any of the four is not an answer."""

    def test_an_answer_records_the_actor_the_choice_the_time_and_both_revisions(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            gate, answer = _opened_and_answered(paths)

            self.assertEqual(answer["state"], "answered")
            self.assertEqual(answer["actor"], _APPROVER)
            self.assertEqual(answer["answered_choice"], "approve")
            self.assertEqual(answer["decided_at"], _DECIDED)
            self.assertEqual(answer["safety_profile_revision"], "safety-rev-7")
            self.assertEqual(answer["context_revision"], "context-rev-3")
            # The question as it was asked is still on record beside its answer.
            self.assertEqual(answer["supersedes_gate_ref"], gate["record_id"])
            self.assertEqual(gate["state"], "open")
            self.assertEqual(len(read_decision_gates(paths)), 2)

    def test_an_answer_cannot_name_a_subject_the_question_did_not(self) -> None:
        # Structural rather than checked: the answer is built *from* the gate,
        # so there is no argument through which it could name another subject,
        # another revision, or another transition.
        answer = _answered()
        gate = _gate()
        for field in RESUME_DIGEST_KEYS:
            with self.subTest(field=field):
                self.assertEqual(answer[field], gate[field])
        self.assertEqual(answer["resume_digest"], gate["resume_digest"])

    def test_an_answer_missing_the_actor_the_choice_or_the_stamp_is_refused(self) -> None:
        for field in ("actor", "answered_choice", "decided_at"):
            with self.subTest(field=field):
                errors = validate_decision_gate({**_answered(), field: ""})
                self.assertTrue(errors)
                self.assertTrue(
                    any("an answered gate must record" in error for error in errors), errors
                )

    def test_an_answer_from_someone_the_question_was_not_put_to_is_refused(self) -> None:
        # #825 puts unattended or inferred approval out of scope. An answer from
        # anyone but the named approver is exactly that.
        with self.assertRaises(DecisionGateError) as raised:
            build_decision_gate_answer(_gate(), actor="operator-bob", choice="approve", decided_at=_DECIDED)
        self.assertIn("approver", str(raised.exception))
        self.assertTrue(validate_decision_gate({**_answered(), "actor": "operator-bob"}))

    def test_an_answer_cannot_choose_something_the_gate_did_not_offer(self) -> None:
        with self.assertRaises(DecisionGateError) as raised:
            build_decision_gate_answer(_gate(), actor=_APPROVER, choice="defer", decided_at=_DECIDED)
        self.assertIn("not offered", str(raised.exception))
        self.assertTrue(validate_decision_gate({**_answered(), "answered_choice": "defer"}))

    def test_an_answered_gate_is_never_answered_twice(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            gate, _ = _opened_and_answered(paths)

            with self.assertRaises(DecisionGateError) as raised:
                answer_decision_gate(
                    paths, gate_id_value=gate["gate_id"], actor=_APPROVER, choice="decline", now=_SOON
                )

            self.assertIn("already answered", str(raised.exception))
            self.assertEqual(len(read_decision_gates(paths)), 2)

    def test_answering_a_question_nobody_asked_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            with self.assertRaises(DecisionGateError) as raised:
                answer_decision_gate(
                    paths, gate_id_value="gate-000000000000dead", actor=_APPROVER, choice="approve", now=_DECIDED
                )
            self.assertIn("no gate on record", str(raised.exception))

    def test_an_open_gate_carrying_an_answer_nobody_gave_is_refused(self) -> None:
        for field in ("actor", "answered_choice", "decided_at"):
            with self.subTest(field=field):
                value = "approve" if field == "answered_choice" else _DECIDED
                errors = validate_decision_gate({**_gate(), field: value})
                self.assertTrue(any("must carry no answer" in error for error in errors), errors)


# ---------------------------------------------------------------------------
# AC3
# ---------------------------------------------------------------------------


class APendingDecisionReleasesNothing(unittest.TestCase):
    """#825 AC3, first of three. A question is not an answer."""

    def test_an_open_gate_does_not_release_the_transition_it_blocks(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            open_decision_gate(paths, now=_OPENED, **_QUESTION)

            verdict = resume_satisfies_gate(paths, now=_SOON, **_RESUME)

            self.assertFalse(verdict["released"])
            self.assertEqual(verdict["reason_code"], REFUSAL_PENDING)
            self.assertEqual(verdict["reason"], GATE_REFUSAL_REASONS[REFUSAL_PENDING])
            self.assertEqual(verdict["state"], "open")
            self.assertEqual(verdict["answered_choice"], "")
            self.assertEqual(verdict["actor"], "")

    def test_a_declined_gate_does_not_release_the_transition(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _opened_and_answered(paths, choice="decline")

            verdict = resume_satisfies_gate(paths, now=_SOON, **_RESUME)

            self.assertFalse(verdict["released"])
            self.assertEqual(verdict["reason_code"], REFUSAL_DECLINED)
            self.assertEqual(verdict["answered_choice"], "decline")

    def test_a_superseded_answer_does_not_release_the_transition(self) -> None:
        # A later record for the same question is the current one. An answer the
        # operator replaced must not still be binding.
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _opened_and_answered(paths)
            reopened = open_decision_gate(paths, now=_SOON, **_QUESTION)
            self.assertEqual(reopened["state"], "open")

            verdict = resume_satisfies_gate(paths, now=_SOON, **_RESUME)

            self.assertFalse(verdict["released"])
            self.assertEqual(verdict["reason_code"], REFUSAL_PENDING)
            records = read_decision_gates(paths)
            self.assertEqual(len(records), 3)
            # The replaced answer is still on file, and is reported as replaced
            # rather than as current.
            superseded = resume_satisfies_gate_in(records[:2], now=_SOON, **_RESUME)
            self.assertTrue(superseded["released"])
            self.assertEqual(
                resume_satisfies_gate_in(records, now=_SOON, **_RESUME)["reason_code"], REFUSAL_PENDING
            )

    def test_a_replaced_answer_never_speaks_for_a_question_that_moved_on(self) -> None:
        # In a well-formed store the superseded branch cannot fire: every record
        # matching these dimensions shares one `gate_id`, so the last match is
        # the chain head and a replaced answer is simply never judged. The branch
        # exists for the store this family cannot assume it is reading -- edit
        # the head's subject on disk and it stops matching, which would otherwise
        # hand the resume back to the answer it replaced.
        gate = _gate()
        answer = _answered()
        moved_head = {
            **_gate(opened_at=_SOON),
            "record_id": "decision-gate-moved-000001",
            "subject_ref": "src/auth/rotate.py",
            "supersedes_gate_ref": answer["record_id"],
        }
        self.assertTrue(validate_decision_gate(moved_head), "the tampered head must not be a valid record")

        verdict = resume_satisfies_gate_in([gate, answer, moved_head], now=_SOON, **_RESUME)

        self.assertFalse(verdict["released"])
        self.assertEqual(verdict["reason_code"], REFUSAL_SUPERSEDED)
        self.assertEqual(verdict["reason"], GATE_REFUSAL_REASONS[REFUSAL_SUPERSEDED])


class AnExpiredDecisionReleasesNothing(unittest.TestCase):
    """#825 AC3, second of three. Consent has a clock, and it is not on disk."""

    def test_an_answer_outside_the_window_does_not_release_the_transition(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _opened_and_answered(paths)

            fresh = resume_satisfies_gate(paths, now=_SOON, **_RESUME)
            stale = resume_satisfies_gate(paths, now=_LATE, **_RESUME)

            self.assertTrue(fresh["released"])
            self.assertFalse(stale["released"])
            self.assertEqual(stale["reason_code"], REFUSAL_EXPIRED)
            self.assertEqual(stale["expires_after_seconds"], APPROVAL_TTL_SECONDS)
            self.assertGreater(stale["age_seconds"], APPROVAL_TTL_SECONDS)

    def test_expiry_is_recomputed_and_never_read_off_the_record(self) -> None:
        # There is no `expires_at` field to edit. The only way to widen the
        # window from disk would be to move the stamp forward, and a stamp in
        # the future cannot be shown to be fresh, so it refuses too.
        self.assertNotIn("expires_at", DECISION_GATE_KEYS)
        answer = _answered()
        future = {**answer, "decided_at": "2027-01-01T00:00:00Z"}
        verdict = resume_satisfies_gate_in([_gate(), future], now=_SOON, **_RESUME)
        self.assertFalse(verdict["released"])
        self.assertEqual(verdict["reason_code"], REFUSAL_EXPIRED)

    def test_the_window_is_the_approval_window_and_not_a_second_one(self) -> None:
        self.assertEqual(compact_decision_gate(_answered())["expires_after_seconds"], APPROVAL_TTL_SECONDS)


class AMismatchedDecisionReleasesNothing(unittest.TestCase):
    """#825 AC3, third of three. An answer releases what it was given for.

    Every dimension is compared by equality. There is no containment rule
    anywhere in the module, which is what makes widening structurally impossible
    rather than merely unimplemented.
    """

    def _verdict(self, paths: OmhPaths, **overrides: Any) -> dict[str, Any]:
        return resume_satisfies_gate(paths, now=_SOON, **{**_RESUME, **overrides})

    def test_a_wrong_subject_does_not_release_the_transition(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _opened_and_answered(paths)

            self.assertTrue(self._verdict(paths)["released"])
            for override in (
                {"subject_ref": "src/auth/rotate.py"},
                {"subject_ref": "src/auth"},
                {"subject_class": "tool"},
            ):
                with self.subTest(override=override):
                    verdict = self._verdict(paths, **override)
                    self.assertFalse(verdict["released"])
                    self.assertEqual(verdict["reason_code"], REFUSAL_SCOPE)

    def test_a_wrong_revision_does_not_release_the_transition(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _opened_and_answered(paths)

            for override in (
                {"safety_profile_revision": "safety-rev-8"},
                {"context_revision": "context-rev-4"},
                {"context_revision": ""},
            ):
                with self.subTest(override=override):
                    verdict = self._verdict(paths, **override)
                    self.assertFalse(verdict["released"])
                    self.assertEqual(verdict["reason_code"], REFUSAL_REVISION)

    def test_a_wrong_scope_of_run_or_position_does_not_release_the_transition(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _opened_and_answered(paths)

            self.assertEqual(self._verdict(paths, run_id=_OTHER_RUN)["reason_code"], REFUSAL_RUN)
            self.assertEqual(
                self._verdict(paths, blocked_transition="merge_observed")["reason_code"],
                REFUSAL_TRANSITION,
            )
            self.assertEqual(
                self._verdict(paths, checkpoint_ref="checkpoint-0009")["reason_code"],
                REFUSAL_TRANSITION,
            )

    def test_nothing_on_record_at_all_is_reported_as_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            verdict = resume_satisfies_gate(paths, now=_SOON, **_RESUME)
            self.assertFalse(verdict["released"])
            self.assertEqual(verdict["reason_code"], REFUSAL_ABSENT)
            self.assertEqual(verdict["gate_id"], "")

    def test_the_resume_digest_seals_every_matched_dimension_and_no_clock(self) -> None:
        base = resume_digest(**_RESUME)
        self.assertEqual(base, _gate()["resume_digest"])
        # Two gates for one question opened an hour apart seal identically: a
        # wall clock inside a compared value would make this a race.
        self.assertEqual(_gate(opened_at=_LATE)["resume_digest"], base)
        self.assertEqual(_answered()["resume_digest"], base)
        for field, value in (
            ("run_id", _OTHER_RUN),
            ("subject_class", "tool"),
            ("subject_ref", "src/auth/rotate.py"),
            ("blocked_transition", "merge_observed"),
            ("checkpoint_ref", "checkpoint-0009"),
            ("safety_profile_revision", "safety-rev-8"),
            ("context_revision", "context-rev-4"),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(resume_digest(**{**_RESUME, field: value}), base)

    def test_a_hand_edited_digest_cannot_move_an_answer_onto_another_transition(self) -> None:
        other = resume_digest(**{**_RESUME, "blocked_transition": "merge_observed"})
        errors = validate_decision_gate({**_answered(), "resume_digest": other})
        self.assertTrue(any("does not name the transition" in error for error in errors), errors)
        self.assertTrue(validate_decision_gate({**_answered(), "resume_digest": "not-a-digest"}))

    def test_a_hand_edited_identity_cannot_graft_a_question_onto_another_chain(self) -> None:
        moved = gate_id(
            run_id=_OTHER_RUN,
            subject_class="filesystem_path",
            subject_ref="src/auth/session.py",
            blocked_transition="executor_dispatch_observed",
            checkpoint_ref="checkpoint-0004",
        )
        self.assertTrue(
            any(
                "gate_id does not identify" in error
                for error in validate_decision_gate({**_gate(), "gate_id": moved})
            )
        )
        self.assertTrue(
            any(
                "subject_id does not identify" in error
                for error in validate_decision_gate(
                    {
                        **_gate(),
                        "subject_id": gate_subject_id(
                            run_id=_OTHER_RUN, subject_class="tool", subject_ref="x"
                        ),
                    }
                )
            )
        )


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------


class TheGateAndItsAnswerSurviveARestart(unittest.TestCase):
    """#825's whole point: the pause and the resume outlive the process."""

    def test_the_question_survives_being_re_read_from_disk(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            written = open_decision_gate(_paths(root), now=_OPENED, **_QUESTION)

            # A fresh `OmhPaths` and a fresh read: nothing in memory carries over.
            reread = read_decision_gates(_paths(root))

            self.assertEqual(len(reread), 1)
            self.assertEqual(reread[0], written)
            view = blocked_decision_gate(_paths(root), run_id=_RUN, now=_SOON)
            self.assertTrue(view["blocked"])
            self.assertEqual(view["gate"]["gate_id"], written["gate_id"])
            self.assertEqual(view["gate"]["question"], QUESTION_TEXT["confirm_destructive_action"])

    def test_the_answer_survives_and_still_releases_the_same_transition(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate, answer = _opened_and_answered(_paths(root))

            reread = read_decision_gates(_paths(root))
            verdict = resume_satisfies_gate(_paths(root), now=_SOON, **_RESUME)

            self.assertEqual([record["record_id"] for record in reread], [gate["record_id"], answer["record_id"]])
            self.assertEqual(reread[1]["actor"], _APPROVER)
            self.assertEqual(reread[1]["answered_choice"], "approve")
            self.assertEqual(reread[1]["decided_at"], _DECIDED)
            self.assertTrue(verdict["released"])
            self.assertEqual(verdict["reason_code"], "")

    def test_a_restart_between_the_question_and_the_answer_loses_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate = open_decision_gate(_paths(root), now=_OPENED, **_QUESTION)

            # Everything below runs against a store read from scratch, which is
            # the only thing a process that restarted has.
            pending = resume_satisfies_gate(_paths(root), now=_SOON, **_RESUME)
            self.assertEqual(pending["reason_code"], REFUSAL_PENDING)
            answer_decision_gate(
                _paths(root), gate_id_value=gate["gate_id"], actor=_APPROVER, choice="approve", now=_DECIDED
            )

            self.assertTrue(resume_satisfies_gate(_paths(root), now=_SOON, **_RESUME)["released"])

    def test_the_store_is_append_only_across_calls(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _opened_and_answered(_paths(root))
            lines = _paths(root).runtime_decision_gates_path.read_bytes().splitlines()

            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            second = json.loads(lines[1])
            self.assertEqual(first["state"], "open")
            self.assertEqual(second["state"], "answered")
            # The question as asked was never rewritten.
            self.assertEqual(first["actor"], "")
            self.assertEqual(second["supersedes_gate_ref"], first["record_id"])


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TheStoreIsRegisteredBesideItsSiblings(unittest.TestCase):
    """The wiring: without it nothing in production ever runs these validators."""

    def test_the_family_registers_its_validator_under_its_own_store(self) -> None:
        entries = {entry.store_name: entry for entry in OPTIONAL_RUNTIME_STORE_VALIDATORS}
        self.assertIn("decision_gates.jsonl", entries)
        entry = entries["decision_gates.jsonl"]
        self.assertEqual(entry.record_id_key, "record_id")
        self.assertEqual(entry.validator(_gate()), [])
        self.assertEqual(entry.validator(_answered()), [])
        self.assertTrue(entry.validator({"schema_version": "approval_receipt/v1"}))
        self.assertTrue(entry.validator({"schema_version": "run_lineage_checkpoint/v1"}))
        self.assertEqual(DECISION_GATE_RECORD_KEYS, DECISION_GATE_KEYS)

    def test_the_store_lives_beside_its_siblings_and_not_inside_a_run(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            self.assertEqual(
                paths.runtime_decision_gates_path.parent,
                paths.runtime_approval_receipts_path.parent,
            )
            self.assertNotIn("runs", paths.runtime_decision_gates_path.parts)
            self.assertEqual(paths.runtime_decision_gates_path.name, "decision_gates.jsonl")

    def test_the_store_report_reaches_omh_runtime_validate(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            store = paths.runtime_decision_gates_path
            store.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(store, json.dumps({"schema_version": "nonsense"}) + "\n")

            report = validate_runtime(paths)

            self.assertIn("decision_gates", report)
            self.assertFalse(report["decision_gates"]["ok"])
            self.assertFalse(report["ok"])

    def test_an_absent_store_is_not_a_fault(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            store = paths.runtime_decision_gates_path
            self.assertFalse(store.exists())

            report = validate_decision_gate_store(store)
            scoped = validate_decision_gate_store(store, run_id=_RUN)

            self.assertTrue(report["ok"], report["errors"])
            self.assertEqual(report["gate_count"], 0)
            self.assertEqual(report["open_gate_count"], 0)
            self.assertTrue(scoped["ok"], scoped["errors"])
            self.assertTrue(validate_runtime(paths)["ok"])

    def test_a_well_formed_store_validates_clean(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _opened_and_answered(paths)

            report = validate_decision_gate_store(paths.runtime_decision_gates_path)

            self.assertTrue(report["ok"], report["errors"])
            self.assertEqual(report["gate_count"], 2)
            self.assertEqual(report["open_gate_count"], 0)
            self.assertEqual(report["contested_subject_count"], 0)
            self.assertTrue(validate_runtime(paths)["ok"])

    def test_a_corrupt_line_faults_the_store_and_not_a_clean_run(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            store = paths.runtime_decision_gates_path
            store.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(store, "{ not json\n")

            unscoped = validate_decision_gate_store(store)
            scoped = validate_decision_gate_store(store, run_id=_RUN)

            self.assertFalse(unscoped["ok"])
            # A line that does not parse belongs to no run.
            self.assertTrue(scoped["ok"], scoped["errors"])


# ---------------------------------------------------------------------------
# The name that was already here, and the vocabularies that were already here
# ---------------------------------------------------------------------------


class TheNameThatWasAlreadyHere(unittest.TestCase):
    """`decision_gate` was a live string in two other places. Neither is this.

    Both are left alone on purpose, and this pins the boundary so a later reader
    cannot collapse them into one thing.
    """

    def test_the_playbook_stages_that_declared_this_contract_now_have_a_schema(self) -> None:
        # Three wrapper stages already declared `decision_gate/v1` as the kind of
        # evidence they produce, with nothing behind the name. This module is
        # that schema arriving under the name they were citing.
        declared = []
        for summary in list_playbooks()["playbooks"]:
            playbook = inspect_playbook(str(summary["id"]))["playbook"]
            for stage in playbook["stages"]:
                if stage["contract"] == DECISION_GATE_SCHEMA_VERSION:
                    declared.append((playbook["id"], stage["id"]))
        self.assertTrue(declared, "no playbook stage declares decision_gate/v1")
        self.assertEqual(
            sorted(stage for _, stage in declared),
            ["decision_gate", "delivery_decision", "deploy_decision"],
        )

    def test_the_wrapper_contract_clause_is_not_a_record_and_stays_untouched(self) -> None:
        # `hermes_planning` carries a `decision_gate` sub-dict in the wrapper
        # contract. It is a delegation condition rebuilt on every call, not a
        # durable record: it names no subject, no approver, and no answer, and
        # `validate_decision_gate` refuses it as the wrong shape entirely.
        payload = build_hermes_plan_payload("add a retry to the fetch helper", source="generic")
        contract = payload["wrapper_contract"]
        assert isinstance(contract, dict)
        clause = contract["decision_gate"]
        assert isinstance(clause, dict)
        self.assertEqual(sorted(clause), ["condition", "do_not_delegate_when", "required"])
        self.assertNotIn("schema_version", clause)
        # Not one key of the clause is a key of the record, and the record's own
        # required keys are all absent from it.
        self.assertEqual(set(clause) & set(DECISION_GATE_KEYS), set())
        self.assertTrue(validate_decision_gate(dict(clause)))


class TheVocabularyIsBorrowedAndNotForked(unittest.TestCase):
    """Reused vocabularies, pinned so a rename upstream is a failing test here."""

    def test_the_subject_classes_are_the_approval_scope_classes(self) -> None:
        self.assertEqual(SUBJECT_CLASSES, SCOPE_CLASSES)

    def test_the_blocked_transitions_are_the_lineage_transitions(self) -> None:
        self.assertEqual(BLOCKED_TRANSITIONS, LINEAGE_TRANSITIONS)

    def test_the_mismatch_codes_this_family_did_not_invent_belong_to_approvals(self) -> None:
        for code in REUSED_REFUSAL_CODES:
            with self.subTest(code=code):
                self.assertIn(code, SCOPE_REFUSAL_CODES)
                self.assertIn(code, REFUSAL_CODES)
        for code in (REFUSAL_EXPIRED, REFUSAL_SUPERSEDED, REFUSAL_ABSENT):
            with self.subTest(code=code):
                self.assertIn(code, REFUSAL_CODES)
        # And the two this family did have to add are not already spoken for.
        for code in (REFUSAL_PENDING, REFUSAL_DECLINED, REFUSAL_TRANSITION):
            with self.subTest(code=code):
                self.assertNotIn(code, REFUSAL_CODES)

    def test_every_refusal_code_has_a_reason_and_every_reason_a_code(self) -> None:
        self.assertEqual(sorted(GATE_REFUSAL_REASONS), sorted(GATE_REFUSAL_CODES))
        for code, reason in GATE_REFUSAL_REASONS.items():
            with self.subTest(code=code):
                self.assertTrue(reason.strip())
                # Nothing is interpolated into a refusal line.
                self.assertNotIn("{", reason)

    def test_the_execution_claim_keys_match_the_sibling_family(self) -> None:
        self.assertEqual(EXECUTION_CLAIM_KEYS, LINEAGE_EXECUTION_CLAIM_KEYS)


# ---------------------------------------------------------------------------
# The record's own shape
# ---------------------------------------------------------------------------


class TheRecordCarriesMetadataAndNeverContent(unittest.TestCase):
    """The closed key set, the closed vocabularies, and the absence of prose."""

    def test_the_key_set_is_closed_and_complete(self) -> None:
        for record in (_gate(), _answered()):
            with self.subTest(state=record["state"]):
                self.assertEqual(sorted(record), sorted(DECISION_GATE_KEYS))
                self.assertEqual(validate_decision_gate(record), [])
        self.assertTrue(validate_decision_gate({**_gate(), "note": "anything"}))
        self.assertTrue(validate_decision_gate({key: "" for key in DECISION_GATE_KEYS}))

    def test_raw_and_hidden_keys_are_refused_by_name(self) -> None:
        for key in sorted(RAW_OR_HIDDEN_KEYS):
            with self.subTest(key=key):
                errors = validate_decision_gate({**_gate(), key: "x"})
                self.assertTrue(any("raw or hidden keys" in error for error in errors), errors)

    def test_execution_claim_keys_are_refused_by_name(self) -> None:
        for key in sorted(EXECUTION_CLAIM_KEYS):
            with self.subTest(key=key):
                errors = validate_decision_gate({**_gate(), key: "x"})
                self.assertTrue(any("execution-claim keys" in error for error in errors), errors)

    def test_the_record_states_its_boundary_and_its_privacy(self) -> None:
        record = _answered()
        self.assertEqual(record["claim_boundary"], CLAIM_BOUNDARY)
        self.assertEqual(record["privacy"], "metadata_only")
        self.assertIn("not dispatch, execution", CLAIM_BOUNDARY)
        self.assertTrue(validate_decision_gate({**record, "claim_boundary": "it ran"}))
        self.assertTrue(validate_decision_gate({**record, "privacy": "full"}))
        # And every surface that renders a verdict repeats it.
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _opened_and_answered(paths)
            self.assertEqual(
                resume_satisfies_gate(paths, now=_SOON, **_RESUME)["claim_boundary"], CLAIM_BOUNDARY
            )
            self.assertEqual(blocked_decision_gate(paths, run_id=_RUN, now=_SOON)["claim_boundary"], CLAIM_BOUNDARY)

    def test_a_subject_cannot_reach_outside_the_workspace(self) -> None:
        for bad in ("../secrets.env", "src/../../etc/passwd", "C:/Windows/system32"):
            with self.subTest(subject_ref=bad), self.assertRaises(DecisionGateError):
                _gate(subject_ref=bad)

    def test_every_closed_vocabulary_is_checked_on_the_way_in_and_at_rest(self) -> None:
        for field, value in (
            ("question_code", "invent_a_question"),
            ("risk_class", "catastrophic"),
            ("subject_class", "database"),
            ("blocked_transition", "shipped"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(DecisionGateError):
                    _gate(**{field: value})
                self.assertTrue(validate_decision_gate({**_gate(), field: value}))
        self.assertTrue(validate_decision_gate({**_gate(), "state": "maybe"}))

    def test_every_vocabulary_member_has_the_prose_a_render_needs(self) -> None:
        self.assertEqual(sorted(QUESTION_TEXT), sorted(QUESTION_CODES))
        self.assertEqual(sorted(RISK_TEXT), sorted(RISK_CLASSES))
        self.assertEqual(sorted(CHOICE_CONSEQUENCES), sorted(GATE_CHOICES))
        self.assertEqual(sorted(CHOICE_RELEASES), sorted(GATE_CHOICES))
        self.assertEqual(sorted(GATE_STATES), ["answered", "open"])

    def test_a_question_must_offer_a_real_choice(self) -> None:
        # Every choice releasing is a formality, not a decision; none releasing
        # is a question that can never be resumed.
        with self.assertRaises(DecisionGateError):
            _gate(choices=("approve",))
        with self.assertRaises(DecisionGateError):
            _gate(choices=("decline", "defer"))
        self.assertEqual(_gate(choices=("defer", "approve", "approve"))["choices"], ["approve", "defer"])
        self.assertTrue(validate_decision_gate({**_gate(), "choices": ["decline", "approve"]}))
        self.assertTrue(validate_decision_gate({**_gate(), "choices": "approve"}))

    def test_a_hand_edited_store_renders_as_nothing_rather_than_as_itself(self) -> None:
        record = _answered()
        rendered = compact_decision_gate(record, now=_SOON)
        self.assertEqual(rendered["state"], "answered")
        self.assertEqual(rendered["answered_choice"], "approve")
        self.assertTrue(rendered["releases_transition"])
        self.assertEqual(rendered["age_seconds"], 60)
        self.assertFalse(rendered["expired"])
        # A value outside its vocabulary is not a new state, it is a value with
        # no meaning, so it renders empty.
        broken = compact_decision_gate({**record, "risk_class": "catastrophic", "state": "maybe"}, now=_SOON)
        self.assertEqual(broken["risk_class"], "")
        self.assertEqual(broken["risk"], "")
        self.assertEqual(broken["state"], "")
        # A string where the list belongs would otherwise render one row per
        # character.
        self.assertEqual(compact_decision_gate({**record, "choices": "approve"})["choices"], [])
        self.assertEqual(compact_decision_gate({**record, "resume_digest": "nope"})["resume_digest"], "")

    def test_the_compactor_covers_every_stored_field(self) -> None:
        rendered = compact_decision_gate(_answered(), now=_SOON)
        constants = {"claim_boundary", "privacy", "schema_version"}
        for key in DECISION_GATE_KEYS:
            if key in constants:
                continue
            with self.subTest(key=key):
                self.assertIn(key, rendered)

    def test_a_second_record_for_one_question_never_forks_the_chain(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            gate, answer = _opened_and_answered(paths)
            forked = {
                **_gate(opened_at=_SOON),
                "record_id": "decision-gate-fork-000001",
                "supersedes_gate_ref": gate["record_id"],
            }
            append_decision_gate(paths, forked)

            report = validate_decision_gate_store(paths.runtime_decision_gates_path)

            self.assertFalse(report["ok"])
            self.assertTrue(any("forks the chain" in error for error in report["errors"]), report["errors"])
            self.assertEqual(answer["supersedes_gate_ref"], gate["record_id"])

    def test_a_record_cannot_replace_itself(self) -> None:
        record = _gate()
        errors = validate_decision_gate({**record, "supersedes_gate_ref": record["record_id"]})
        self.assertTrue(any("must not name itself" in error for error in errors), errors)


class OpenGatesAreSelectedTheSameWayEverywhere(unittest.TestCase):
    """One selection rule, so two surfaces cannot disagree about one question."""

    def test_only_chain_heads_count_as_open(self) -> None:
        gate = _gate()
        answer = build_decision_gate_answer(gate, actor=_APPROVER, choice="approve", decided_at=_DECIDED)
        self.assertEqual([record["record_id"] for record in open_gates_in([gate])], [gate["record_id"]])
        # The open record is superseded by its own answer, so it is no longer a
        # question anybody is being asked.
        self.assertEqual(open_gates_in([gate, answer]), [])

    def test_the_view_and_the_store_report_agree_about_how_many_are_open(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            append_decision_gate(paths, _gate())
            append_decision_gate(paths, _gate(subject_ref="src/auth/rotate.py"))
            records = read_decision_gates(paths)

            view = project_blocked_decision_gate(records, run_id=_RUN, now=_SOON)
            report = validate_decision_gate_store(paths.runtime_decision_gates_path)

            self.assertEqual(view["open_gate_count"], report["open_gate_count"])
            self.assertEqual(view["open_gate_count"], 2)
            # Two subjects, so no subject is contested and the store is clean.
            self.assertTrue(report["ok"], report["errors"])
            # But no single question can be named, which is the state a renderer
            # must not resolve by guessing.
            self.assertFalse(view["ok"])

    def test_another_runs_gate_is_invisible_to_this_run(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            open_decision_gate(paths, now=_OPENED, **{**_QUESTION, "run_id": _OTHER_RUN})

            self.assertFalse(blocked_decision_gate(paths, run_id=_RUN, now=_SOON)["blocked"])
            self.assertTrue(blocked_decision_gate(paths, run_id=_OTHER_RUN, now=_SOON)["blocked"])


if __name__ == "__main__":
    unittest.main()
