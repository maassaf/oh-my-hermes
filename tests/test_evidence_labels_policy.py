"""The friendly evidence labels, gated against the ways they could lie.

The wire vocabulary (`prepared_not_observed`, `worktree_failed`, …) is a
contract external wrappers gate on. This module renames nothing on that side;
it owns what a PERSON reads. That split is the whole design, so the first thing
tested is that the split held: every wire value still ships verbatim.

The rest is about the one failure that matters. A label is read faster than it
is thought about, so the only interesting question is whether a state that
never ran can be scanned as work that happened. Every assertion below is
pointed at that, including the ones that look like style checks:

* labels are re-derived from the producing source, so a new state added to
  `STATUS_VOCABULARY` or `_usage_evidence_state` fails here instead of silently
  rendering as the fail-closed default;
* the negation survives every truncation budget in the repo, because a cell
  clipped to `Plan · not r` is fine and one clipped to `Plan` is a lie;
* `ready` is absent, and that is not taste — `operator_productivity`
  rejects it as an unbacked observation claim.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from _local_package import load_local_package

load_local_package()

from omh.coding.status_board import STATUS_VOCABULARY, status_text_for  # noqa: E402
from omh.evidence.labels import (  # noqa: E402
    CONFIDENCE_NOT_RUN,
    CONFIDENCE_PROSE,
    CONFIDENCES,
    PHASE_CODE,
    PHASES,
    SEPARATOR,
    confidence_label,
    phase_label,
    status_label,
    status_prose,
)
from omh.plugin_bundle.omh.status_board_reader import _status_text  # noqa: E402
from omh.workflows.operator_productivity import OBSERVED_CLAIM_STATUSES  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"

# Words that would let a not-run state be scanned as work that happened.
_SUCCESS_WORDS = ("done", "complete", "completed", "ready", "passed", "success", "ok", "finished")


def _usage_evidence_states() -> set[str]:
    """Re-derive the states `_usage_evidence_state` can return, from its source.

    Re-derived rather than restated so a new branch added to that function
    arrives here as a failure instead of quietly taking the fail-closed label.
    """
    source = (SRC / "wrapper" / "contract.py").read_text(encoding="utf-8")
    body = source.split("def _usage_evidence_state(", 1)[1].split("\ndef ", 1)[0]
    return set(re.findall(r'return "([a-z_]+)"', body))


class TheWireSideDidNotMoveTests(unittest.TestCase):
    def test_every_evidence_state_still_ships_verbatim(self) -> None:
        # The premise of the whole change: renaming what a human reads must not
        # rename what a wrapper parses.
        states = _usage_evidence_states()
        self.assertIn("prepared_not_observed", states)
        self.assertIn("routing_not_execution", states)
        self.assertIn("observed_partial", states)
        self.assertIn("observed_reportable", states)

    def test_the_closed_status_vocabulary_still_ships_verbatim(self) -> None:
        self.assertEqual(
            STATUS_VOCABULARY,
            ("running", "completed", "failed", "worktree_failed", "prepared_not_observed"),
        )


class EveryStateHasALabelTests(unittest.TestCase):
    def _states(self) -> set[str]:
        return _usage_evidence_states() | set(STATUS_VOCABULARY)

    def test_no_state_falls_through_to_the_default_by_accident(self) -> None:
        # `not run` is the fail-closed answer for an UNKNOWN state. A state this
        # repo actually emits reaching it means someone added a state and no
        # label, which is exactly what this test exists to catch. The two
        # genuinely-pre-run states are named as the deliberate exceptions.
        deliberate = {"prepared_not_observed", "routing_not_execution"}
        for state in sorted(self._states() - deliberate):
            with self.subTest(state=state):
                self.assertNotEqual(
                    confidence_label(state),
                    CONFIDENCE_NOT_RUN,
                    f"{state!r} has no label of its own; add one in omh/evidence/labels.py",
                )

    def test_every_label_is_from_the_declared_vocabulary(self) -> None:
        for state in sorted(self._states()):
            with self.subTest(state=state):
                self.assertIn(confidence_label(state), CONFIDENCES)

    def test_every_confidence_label_has_a_prose_long_form(self) -> None:
        for confidence in CONFIDENCES:
            with self.subTest(confidence=confidence):
                self.assertTrue(CONFIDENCE_PROSE.get(confidence))


class ANotRunStateCannotBeScannedAsDoneTests(unittest.TestCase):
    def test_the_not_run_label_carries_no_success_word(self) -> None:
        for word in _SUCCESS_WORDS:
            self.assertNotIn(word, CONFIDENCE_NOT_RUN)

    def test_the_label_fits_every_real_truncation_budget_whole(self) -> None:
        # The budgets that actually clip a status elsewhere in the repo:
        # `_pad(..., 20)` in src/commands/menubar.py:130, `_short_text(limit=32)`
        # in src/surfaces/menubar_status.py:588, and `_STATUS_SOURCE_LIMIT = 80`
        # in src/coding/status_board.py:97. Fitting WHOLE is the property worth
        # holding — a clipped `Plan` with the negation cut off is the failure,
        # and no partial-survival rule is needed if nothing ever clips.
        for state in ("prepared_not_observed", "routing_not_execution", "worktree_failed", "completed"):
            for limit in (20, 32, 80):
                with self.subTest(state=state, limit=limit):
                    rendered = status_label(state, default_phase=PHASE_CODE)
                    self.assertLessEqual(len(rendered), limit, rendered)

    def test_a_not_run_label_keeps_its_negation_under_a_hostile_clip(self) -> None:
        # Belt and braces for a surface that clips tighter than any today.
        rendered = status_label("prepared_not_observed")
        for limit in (14, 16, 20):
            with self.subTest(limit=limit):
                self.assertIn("not", rendered[:limit], rendered[:limit])

    def test_the_friendly_label_is_no_longer_than_the_wire_value(self) -> None:
        # A friendlier label that blows a column budget gets truncated back into
        # jargon, so this is a correctness property rather than a nicety.
        for state in ("prepared_not_observed", "routing_not_execution", "worktree_failed"):
            with self.subTest(state=state):
                self.assertLessEqual(len(status_label(state)), len(state))

    def test_an_unknown_state_fails_closed_rather_than_open(self) -> None:
        for state in ("", "   ", "some_future_state", "completed_and_verified", "PREPARED_NOT_OBSERVED"):
            with self.subTest(state=state):
                self.assertEqual(confidence_label(state), CONFIDENCE_NOT_RUN)

    def test_a_self_reported_finish_names_its_claimant(self) -> None:
        # `completed` is the executor's own claim about itself; OMH observed the
        # claim and never the result. Rendering it as a bare success word is how
        # a self-report gets promoted to a verified one.
        label = confidence_label("completed")
        self.assertIn("reported", label)
        self.assertNotEqual(label, "done")

    def test_no_label_is_ever_the_bare_word_unknown(self) -> None:
        # `message_gate` raises `message_gate_missing_status` on exactly that
        # value, so a label of `unknown` would turn every row into a warning.
        for state in ("", "junk", *STATUS_VOCABULARY, *_usage_evidence_states()):
            with self.subTest(state=state):
                self.assertNotEqual(status_label(state), "unknown")
                self.assertNotIn("unknown", status_label(state))


class ReadyIsRefusedByTheProjectsOwnGuardTests(unittest.TestCase):
    def test_ready_is_a_claim_word_this_repo_rejects(self) -> None:
        # Not a style choice: `_nested_observed_claim_errors` refuses any payload
        # whose `status` field carries a member of this set.
        self.assertIn("ready", OBSERVED_CLAIM_STATUSES)

    def test_no_pre_run_label_collides_with_a_claim_word(self) -> None:
        # Precise property. `running` IS a member of that set and IS a correct
        # label, because the board only renders it when an in-flight marker was
        # actually observed — the guard is about UNBACKED claims. What must
        # never collide is a label for a state that did not run, and no phase
        # word may collide at all, since the phase axis is meant to be incapable
        # of asserting activity.
        self.assertNotIn(CONFIDENCE_NOT_RUN, OBSERVED_CLAIM_STATUSES)
        for phase in PHASES:
            with self.subTest(phase=phase):
                self.assertNotIn(phase.strip().lower(), OBSERVED_CLAIM_STATUSES)

    def test_ready_survives_only_as_future_tense_prose(self) -> None:
        # The owner asked for `ready`. It lives where it cannot be read as an
        # achievement: a sentence that says nothing has happened yet.
        prose = CONFIDENCE_PROSE[CONFIDENCE_NOT_RUN]
        self.assertIn("ready to run", prose)
        self.assertIn("nothing has run yet", prose)


class PhaseAxisCannotAssertActivityTests(unittest.TestCase):
    def test_no_phase_label_is_a_gerund(self) -> None:
        # "Coding · not run" reads as someone coding right now on a fast scan.
        # "Code · not run" cannot. Every phase label is a noun.
        for phase in PHASES:
            with self.subTest(phase=phase):
                self.assertFalse(phase.lower().endswith("ing"), phase)

    def test_a_more_specific_next_action_wins_over_the_implied_phase(self) -> None:
        self.assertEqual(phase_label("observed_partial", next_action="record_review_evidence"), "Review")
        self.assertEqual(phase_label("observed_partial", next_action="record_ci_evidence"), "Test")

    def test_a_surface_default_applies_only_when_nothing_more_specific_exists(self) -> None:
        # A coding board knows every row is Code; it must not overrule a value
        # that already named its own phase.
        self.assertEqual(phase_label("running", default=PHASE_CODE), PHASE_CODE)
        self.assertEqual(phase_label("worktree_failed", default=PHASE_CODE), "Setup")

    def test_an_unrecognized_default_is_ignored_rather_than_rendered(self) -> None:
        self.assertEqual(phase_label("running", default="Deploying"), "")


class PluginBundleMirrorParityTests(unittest.TestCase):
    """The bundle cannot import `omh.*`, so it hand-mirrors the map."""

    def test_both_renderers_agree_on_every_state(self) -> None:
        states = ["", "junk", *STATUS_VOCABULARY, *sorted(_usage_evidence_states())]
        for state in states:
            with self.subTest(state=state):
                unit = {"status": state}
                self.assertEqual(status_text_for(unit), _status_text(unit))

    def test_both_renderers_agree_when_a_word_was_refused(self) -> None:
        unit = {"status": "prepared_not_observed", "unmapped_source_status": "quiesced"}
        self.assertEqual(status_text_for(unit), _status_text(unit))
        self.assertIn("(reported quiesced)", status_text_for(unit))

    def test_the_separator_is_identical_on_both_sides(self) -> None:
        unit = {"status": "running"}
        self.assertIn(SEPARATOR, status_text_for(unit))
        self.assertIn(SEPARATOR, _status_text(unit))


class RenderedShapeTests(unittest.TestCase):
    def test_the_combined_label_reads_phase_then_confidence(self) -> None:
        self.assertEqual(status_label("prepared_not_observed"), f"Plan{SEPARATOR}not run")
        self.assertEqual(status_label("routing_not_execution"), f"Route{SEPARATOR}not run")
        self.assertEqual(status_label("worktree_failed"), f"Setup{SEPARATOR}failed")
        self.assertEqual(status_label("running", default_phase=PHASE_CODE), f"Code{SEPARATOR}running")

    def test_a_confidence_with_no_phase_renders_alone_not_with_a_dangling_separator(self) -> None:
        self.assertEqual(status_label("observed_partial"), "partly seen")
        self.assertNotIn(SEPARATOR, status_label("observed_partial"))

    def test_prose_form_is_a_sentence_a_person_can_read_aloud(self) -> None:
        self.assertEqual(
            status_prose("prepared_not_observed"),
            "plan: ready to run, nothing has run yet",
        )

    def test_labels_are_english_and_single_line(self) -> None:
        # User-facing output is English by default; localized copy is opt-in via
        # --language / OMH_LANG only, and a newline would break every table row.
        for label in (*CONFIDENCES, *PHASES, *CONFIDENCE_PROSE.values()):
            with self.subTest(label=label):
                self.assertNotIn("\n", label)
                self.assertTrue(all(ord(char) < 128 or char == "·" for char in label), label)


if __name__ == "__main__":
    unittest.main()
