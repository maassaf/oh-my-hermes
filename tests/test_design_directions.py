"""Contract for the plural design direction set and its static preview.

The point of this family is that OMH can offer a choice rather than record one
that was already made, so the tests that matter are the ones about plurality:
how many options, that they differ, that the choice slot starts empty, and that
the preview a person opens is a file and only a file.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _cli_harness import run_cli

from omh.workflows.design_directions import (
    DESIGN_DIRECTION_SET_SCHEMA_VERSION,
    build_design_direction_set,
    choose_design_direction,
    compact_design_direction_set,
    render_design_direction_set_html,
    validate_design_direction_set,
)

_REFERENCE = ("design_system", "design_ref_a1b2c3d4e5f60718", "project_local")
_A = ("a", "task_first", "restrained_neutral", "system_sans", "single_column", "progress_trace",
      ("placeholder_copy", "weak_hierarchy"))
_B = ("b", "evidence_first", "contextual_accent", "editorial_serif", "split_panel", "evidence_rail",
      ("generic_glass", "card_wall"))
_C = ("c", "content_first", "high_contrast", "utilitarian_mono", "editorial_grid", "decision_map",
      ("decorative_gradient", "tiny_text"))


def _set(*options, chosen: str = ""):
    return build_design_direction_set(
        surface="workflow_screen",
        audience="operator",
        primary_task="decide",
        platform="web",
        mode="new",
        context_references=(_REFERENCE,),
        options=options or (_A, _B),
        chosen_option=chosen,
    )


class DesignDirectionSetShapeTests(unittest.TestCase):
    def test_a_prepared_set_validates_and_starts_with_no_choice(self) -> None:
        directions = _set()
        self.assertEqual(directions["schema_version"], DESIGN_DIRECTION_SET_SCHEMA_VERSION)
        self.assertEqual(directions["status"], "prepared_not_observed")
        self.assertEqual(directions["chosen_option"], "")
        self.assertEqual(directions["choice_status"], "awaiting_choice")
        self.assertEqual(validate_design_direction_set(directions), [])

    def test_one_option_is_not_a_choice_and_five_is_not_a_comparison(self) -> None:
        with self.assertRaises(ValueError):
            _set(_A)
        with self.assertRaises(ValueError):
            _set(_A, _B, _C, ("d", *_A[1:]), ("a", *_B[1:]))

    def test_option_ids_must_be_the_leading_ids_in_order(self) -> None:
        with self.assertRaises(ValueError):
            _set(_B, _C)
        with self.assertRaises(ValueError):
            _set(_B, _A)

    def test_two_identical_directions_are_refused(self) -> None:
        # Presenting the same direction twice makes the choice a coin flip and
        # the recorded answer meaningless.
        with self.assertRaises(ValueError):
            _set(_A, ("b", *_A[1:]))

    def test_a_choice_must_name_an_offered_option(self) -> None:
        with self.assertRaises(ValueError):
            _set(_A, _B, chosen="c")
        self.assertEqual(_set(_A, _B, chosen="b")["choice_status"], "chosen")

    def test_the_preview_block_states_what_was_not_done(self) -> None:
        preview = _set()["preview"]
        self.assertFalse(preview["server_bound"])
        self.assertFalse(preview["browser_launched"])
        self.assertFalse(preview["rendered_observed"])
        self.assertTrue(preview["self_contained"])


class DesignDirectionChoiceTests(unittest.TestCase):
    def test_choosing_records_the_pick_and_leaves_the_offer_intact(self) -> None:
        offered = _set(_A, _B, _C)
        chosen = choose_design_direction(offered, "c")
        self.assertEqual(chosen["chosen_option"], "c")
        self.assertEqual(chosen["options"], offered["options"])
        self.assertEqual(validate_design_direction_set(chosen), [])

    def test_a_drifted_artifact_cannot_be_laundered_by_writing_a_choice_on_it(self) -> None:
        drifted = _set()
        drifted["options"][0]["palette"] = "not_a_palette"
        with self.assertRaises(ValueError):
            choose_design_direction(drifted, "a")

    def test_compact_returns_nothing_for_an_invalid_set(self) -> None:
        self.assertEqual(compact_design_direction_set({"schema_version": "wrong"}), {})
        self.assertEqual(compact_design_direction_set(_set()), _set())


class DesignDirectionPreviewTests(unittest.TestCase):
    def test_the_preview_makes_no_external_request_of_any_kind(self) -> None:
        document = render_design_direction_set_html(_set(_A, _B, _C))
        for forbidden in ("http://", "https://", "<script", "<iframe", "src=", "@import", "url("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, document.lower())

    def test_each_direction_is_rendered_as_the_thing_it_names(self) -> None:
        document = render_design_direction_set_html(_set(_A, _B, _C))
        # The palettes and typefaces are applied, not merely listed.
        self.assertIn("#f7f7f5", document)
        self.assertIn("#4b3fa7", document)
        self.assertIn("Iowan Old Style", document)
        self.assertIn("ui-monospace", document)
        # Hierarchy reorders the blocks rather than relabelling them.
        self.assertLess(document.index("Primary action"), document.index("Evidence"))

    def test_the_chosen_option_is_marked_and_an_open_choice_says_so(self) -> None:
        self.assertIn("chosen", render_design_direction_set_html(_set(_A, _B, chosen="b")))
        self.assertIn("No option is chosen yet", render_design_direction_set_html(_set()))

    def test_the_claim_boundary_travels_with_the_document(self) -> None:
        self.assertIn("not evidence that anyone looked at it", render_design_direction_set_html(_set()))

    def test_an_invalid_set_is_refused_rather_than_half_rendered(self) -> None:
        with self.assertRaises(ValueError):
            render_design_direction_set_html({"schema_version": "design_direction_set/v1"})


class DesignDirectionsCliTests(unittest.TestCase):
    ARGS = [
        "ops", "design-directions",
        "--surface", "workflow_screen", "--audience", "operator", "--primary-task", "decide",
        "--platform", "web", "--mode", "new",
        "--context-reference", "design_system:design_ref_a1b2c3d4e5f60718:project_local",
        "--option", "a:task_first:restrained_neutral:system_sans:single_column:progress_trace:placeholder_copy|weak_hierarchy",
        "--option", "b:evidence_first:contextual_accent:editorial_serif:split_panel:evidence_rail:generic_glass|card_wall",
    ]

    def test_plain_text_is_the_default_and_names_the_open_choice(self) -> None:
        code, stdout, _ = run_cli(self.ARGS, output_json=False)
        self.assertEqual(code, 0)
        self.assertIn("option a, b", stdout)
        self.assertIn("No choice recorded yet", stdout)
        self.assertNotIn("schema_version", stdout)

    def test_json_carries_the_artifact(self) -> None:
        code, stdout, _ = run_cli(self.ARGS)
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["schema_version"], DESIGN_DIRECTION_SET_SCHEMA_VERSION)
        self.assertEqual(validate_design_direction_set(
            {key: value for key, value in payload.items() if key != "preview_path"}), [])

    def test_html_writes_a_file_and_says_nothing_was_launched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "directions.html"
            code, stdout, _ = run_cli([*self.ARGS, "--html", str(target)], output_json=False)
            self.assertEqual(code, 0)
            self.assertTrue(target.is_file())
            self.assertIn("nothing was served or launched", stdout)
            self.assertIn("<!DOCTYPE html>", target.read_text(encoding="utf-8"))

    def test_a_malformed_option_fails_with_a_usable_message_and_a_nonzero_code(self) -> None:
        code, _, stderr = run_cli([*self.ARGS[:-1], "b:evidence_first"], output_json=False)
        self.assertNotEqual(code, 0)
        self.assertIn("id:hierarchy:palette", stderr)

    def test_choosing_records_the_option(self) -> None:
        code, stdout, _ = run_cli([*self.ARGS, "--choose", "b"], output_json=False)
        self.assertEqual(code, 0)
        self.assertIn("b (chosen)", stdout)
        self.assertIn("Choice recorded: b.", stdout)


if __name__ == "__main__":
    unittest.main()
