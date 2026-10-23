"""Contracts for `workflow_composition/v1` (issue #816).

Three acceptance criteria, three classes, and two guard classes for the failures
the record exists to prevent: a coding step quietly left with the Hermes turn
that is narrating the workflow, and a single capability dressed up as a
multi-step workflow because the request happened to contain the word "and".

The determinism criterion is tested against the catalog revision as a real
input, not just as a field: the revision is patched and the composition id has
to move. A test that only rebuilt twice would pass against a builder that
ignored the catalog entirely.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from unittest import mock

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.capabilities.families import capability_family_projection  # noqa: E402
from omh.routing.compound_intent import compound_request_segments  # noqa: E402
from omh.workflows import workflow_composition as composition_module  # noqa: E402
from omh.workflows.hermes_planning import build_hermes_plan_payload, render_plan_markdown  # noqa: E402
from omh.workflows.workflow_composition import (  # noqa: E402
    CODING_OWNER_CHOICE_PENDING,
    DELEGATED_CODING_FAMILY,
    HERMES_RETAINED_OWNER,
    WORKFLOW_COMPOSITION_CLAIM_BOUNDARY,
    WORKFLOW_COMPOSITION_CODING_OWNERS,
    WORKFLOW_COMPOSITION_FAMILY_ORDER,
    WORKFLOW_COMPOSITION_GAP_KEYS,
    WORKFLOW_COMPOSITION_INSTALL_POLICY,
    WORKFLOW_COMPOSITION_INTENT_KEYS,
    WORKFLOW_COMPOSITION_KEYS,
    WORKFLOW_COMPOSITION_NOT_OBSERVED,
    WORKFLOW_COMPOSITION_REQUIRED_STEP_FIELDS,
    WORKFLOW_COMPOSITION_SCHEMA_VERSION,
    WORKFLOW_COMPOSITION_STEP_KEYS,
    build_workflow_composition,
    render_workflow_composition_text,
    validate_workflow_composition,
)

# One compound outcome used across the suite: three fragments that resolve to
# three different capability families, one of which is coding.
COMPOUND_OUTCOME = "Research the payment providers, write the migration plan, and implement the winner."


def _clear_caches() -> None:
    composition_module._build_workflow_composition_cached.cache_clear()


class DeterministicCompositionTests(unittest.TestCase):
    """AC1: the same request and catalog revision produce the same composition."""

    def setUp(self) -> None:
        _clear_caches()

    def test_repeated_builds_of_one_request_are_identical(self) -> None:
        first = build_workflow_composition(COMPOUND_OUTCOME, constraints=("no new dependencies",))
        _clear_caches()
        second = build_workflow_composition(COMPOUND_OUTCOME, constraints=("no new dependencies",))

        self.assertEqual(first, second)
        self.assertEqual(
            json.dumps(first, sort_keys=True, ensure_ascii=False),
            json.dumps(second, sort_keys=True, ensure_ascii=False),
        )

    def test_composition_records_the_catalog_revision_it_was_built_from(self) -> None:
        payload = build_workflow_composition(COMPOUND_OUTCOME)

        self.assertEqual(payload["catalog_revision"], composition_module.catalog_revision())

    def test_a_different_catalog_revision_produces_a_different_composition(self) -> None:
        pinned = "a" * 64
        with mock.patch.object(composition_module, "catalog_revision", return_value=pinned):
            _clear_caches()
            other = build_workflow_composition(COMPOUND_OUTCOME)
        _clear_caches()
        current = build_workflow_composition(COMPOUND_OUTCOME)

        self.assertEqual(other["catalog_revision"], pinned)
        self.assertNotEqual(other["catalog_revision"], current["catalog_revision"])
        self.assertNotEqual(other["composition_id"], current["composition_id"])

    def test_the_returned_payload_is_not_the_cached_one(self) -> None:
        first = build_workflow_composition(COMPOUND_OUTCOME)
        steps = first["steps"]
        assert isinstance(steps, list)
        steps.clear()
        second = build_workflow_composition(COMPOUND_OUTCOME)

        self.assertEqual(second["step_count"], len(second["steps"]))
        self.assertGreaterEqual(len(second["steps"]), 2)

    def test_stated_order_does_not_change_the_composed_order(self) -> None:
        forward = build_workflow_composition("research the OAuth options, plan the rollout, and ship the change")
        reordered = build_workflow_composition("ship the change, plan the rollout, and research the OAuth options")

        self.assertEqual(
            [step["capability_family"] for step in forward["steps"]],
            [step["capability_family"] for step in reordered["steps"]],
        )

    def test_composition_makes_no_clock_or_random_call(self) -> None:
        source = Path(composition_module.__file__).read_text(encoding="utf-8")
        imported = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import) for alias in node.names
        }

        self.assertEqual(imported & {"datetime", "random", "secrets", "time", "socket", "urllib", "urllib.request"}, set())

    def test_phase_order_covers_the_family_projection_in_both_directions(self) -> None:
        projected = {str(family_id) for family_id in capability_family_projection()["family_order"]}

        self.assertEqual(set(WORKFLOW_COMPOSITION_FAMILY_ORDER), projected)
        self.assertEqual(len(WORKFLOW_COMPOSITION_FAMILY_ORDER), len(set(WORKFLOW_COMPOSITION_FAMILY_ORDER)))


class StepFieldTests(unittest.TestCase):
    """AC2: every step names capability, owner, inputs, output, and evidence boundary."""

    def setUp(self) -> None:
        _clear_caches()
        self.payload = build_workflow_composition(COMPOUND_OUTCOME, constraints=("no new dependencies",))

    def test_a_composed_workflow_validates(self) -> None:
        self.assertEqual(validate_workflow_composition(self.payload), [])
        self.assertEqual(self.payload["state"], "composed")
        self.assertGreaterEqual(len(self.payload["steps"]), 2)

    def test_every_step_names_all_five_fields(self) -> None:
        for step in self.payload["steps"]:
            with self.subTest(step=step["step_id"]):
                self.assertEqual(sorted(step), sorted(WORKFLOW_COMPOSITION_STEP_KEYS))
                for field in WORKFLOW_COMPOSITION_REQUIRED_STEP_FIELDS:
                    value = step[field]
                    if isinstance(value, list):
                        self.assertTrue(value, f"{field} must not be empty")
                        self.assertTrue(all(str(item).strip() for item in value))
                    else:
                        self.assertTrue(str(value).strip(), f"{field} must not be empty")

    def test_a_step_missing_a_required_field_fails_validation_naming_it(self) -> None:
        for field in WORKFLOW_COMPOSITION_REQUIRED_STEP_FIELDS:
            with self.subTest(field=field):
                broken = json.loads(json.dumps(self.payload))
                del broken["steps"][0][field]
                errors = validate_workflow_composition(broken)

                self.assertTrue(errors)
                self.assertTrue(
                    any(field in error for error in errors),
                    f"no error named the missing field {field}: {errors}",
                )

    def test_a_step_with_an_emptied_required_field_fails_validation_naming_it(self) -> None:
        for field in WORKFLOW_COMPOSITION_REQUIRED_STEP_FIELDS:
            with self.subTest(field=field):
                broken = json.loads(json.dumps(self.payload))
                broken["steps"][0][field] = [] if isinstance(self.payload["steps"][0][field], list) else "  "
                errors = validate_workflow_composition(broken)

                self.assertIn(f"steps[0]: step must name {field}", errors)

    def test_step_inputs_chain_the_previous_step_and_carry_constraints(self) -> None:
        steps = self.payload["steps"]
        self.assertIn(f"requested outcome: {COMPOUND_OUTCOME}", steps[0]["inputs"])
        for index, step in enumerate(steps[1:], start=1):
            self.assertIn(f"output of step-{index}", step["inputs"])
        for step in steps:
            self.assertIn("constraint: no new dependencies", step["inputs"])

    def test_step_evidence_boundary_states_what_it_does_and_does_not_prove(self) -> None:
        for step in self.payload["steps"]:
            with self.subTest(step=step["step_id"]):
                boundary = str(step["evidence_boundary"])
                self.assertIn("proves only that", boundary)
                self.assertIn("does not prove", boundary)
                self.assertIn(str(step["capability"]), boundary)

    def test_the_record_key_set_is_closed_in_both_directions(self) -> None:
        self.assertEqual(sorted(self.payload), sorted(WORKFLOW_COMPOSITION_KEYS))
        self.assertEqual(sorted(self.payload["compound_intent"]), sorted(WORKFLOW_COMPOSITION_INTENT_KEYS))

        extra = json.loads(json.dumps(self.payload))
        extra["surprise"] = True
        self.assertIn("workflow_composition has unexpected keys: surprise", validate_workflow_composition(extra))

        missing = json.loads(json.dumps(self.payload))
        del missing["ownership_rule"]
        self.assertIn("workflow_composition is missing keys: ownership_rule", validate_workflow_composition(missing))

        step_extra = json.loads(json.dumps(self.payload))
        step_extra["steps"][0]["surprise"] = True
        self.assertIn("steps[0]: step has unexpected keys: surprise", validate_workflow_composition(step_extra))

    def test_claim_boundary_denies_every_downstream_claim(self) -> None:
        self.assertEqual(self.payload["claim_boundary"], WORKFLOW_COMPOSITION_CLAIM_BOUNDARY)
        self.assertEqual(self.payload["schema_version"], WORKFLOW_COMPOSITION_SCHEMA_VERSION)
        self.assertEqual(
            sorted(self.payload["not_evidence_until_observed"]),
            sorted(WORKFLOW_COMPOSITION_NOT_OBSERVED),
        )

        weakened = json.loads(json.dumps(self.payload))
        weakened["claim_boundary"] = "A composition means the work happened."
        self.assertIn(
            "claim_boundary must state the composition contract verbatim",
            validate_workflow_composition(weakened),
        )


class MissingCapabilityTests(unittest.TestCase):
    """AC3: missing capabilities are surfaced and never silently installed."""

    def setUp(self) -> None:
        _clear_caches()

    def test_an_unavailable_capability_is_reported_with_a_reason(self) -> None:
        full = build_workflow_composition(COMPOUND_OUTCOME)
        kept = str(full["steps"][1]["capability"])
        reduced = build_workflow_composition(COMPOUND_OUTCOME, available_capabilities=(kept,))

        self.assertEqual(validate_workflow_composition(reduced), [])
        self.assertEqual(reduced["state"], "composed")
        self.assertEqual(reduced["next_action"], "report_missing_capabilities")
        gaps = reduced["missing_capabilities"]
        self.assertTrue(gaps)
        for gap in gaps:
            self.assertEqual(sorted(gap), sorted(WORKFLOW_COMPOSITION_GAP_KEYS))
            self.assertIn("not among the capabilities available", str(gap["reason"]))
            self.assertEqual(gap["next_action"], "report_gap_and_wait_for_an_explicit_install_decision")
        self.assertEqual(
            {gap["needed_for_step"] for gap in gaps},
            {step["step_id"] for step in reduced["steps"] if step["capability_status"] == "missing"},
        )

    def test_a_missing_capability_keeps_its_place_in_the_ordered_workflow(self) -> None:
        full = build_workflow_composition(COMPOUND_OUTCOME)
        kept = str(full["steps"][1]["capability"])
        reduced = build_workflow_composition(COMPOUND_OUTCOME, available_capabilities=(kept,))

        self.assertEqual(
            [step["capability"] for step in reduced["steps"]],
            [step["capability"] for step in full["steps"]],
        )

    def test_composition_completes_when_every_installer_entry_point_raises(self) -> None:
        """The installer is not merely unused; the composition cannot reach it."""
        from omh.install import installer as installer_module

        entry_points = ("install_skill_pack", "reconcile_skill_profile", "uninstall_skill_pack")
        patches = [
            mock.patch.object(
                installer_module,
                name,
                side_effect=AssertionError("workflow composition must never install a capability"),
            )
            for name in entry_points
            if callable(getattr(installer_module, name, None))
        ]
        self.assertEqual(len(patches), len(entry_points))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        _clear_caches()
        payload = build_workflow_composition(COMPOUND_OUTCOME, available_capabilities=("plan",))

        self.assertEqual(validate_workflow_composition(payload), [])
        self.assertEqual(payload["state"], "composed")
        self.assertTrue(payload["missing_capabilities"])

    def test_the_module_imports_nothing_that_could_install(self) -> None:
        source = Path(composition_module.__file__).read_text(encoding="utf-8")
        imported = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }

        self.assertNotIn("..install.installer", imported)
        self.assertNotIn("..install.plugin_pack", imported)
        self.assertNotIn("..skills.packaging", imported)

    def test_install_policy_says_installation_stays_a_user_action(self) -> None:
        payload = build_workflow_composition(COMPOUND_OUTCOME)

        self.assertEqual(payload["install_policy"], WORKFLOW_COMPOSITION_INSTALL_POLICY)
        self.assertIn("never installs", str(payload["install_policy"]))


class OwnershipGuardTests(unittest.TestCase):
    """A coding step is never owned by Hermes."""

    def setUp(self) -> None:
        _clear_caches()

    def test_a_coding_step_is_delegated_and_every_other_step_is_retained(self) -> None:
        payload = build_workflow_composition(COMPOUND_OUTCOME, coding_owner="claude-code")

        delegated = [step for step in payload["steps"] if step["owner_kind"] == "delegated_coding"]
        self.assertTrue(delegated)
        for step in payload["steps"]:
            with self.subTest(step=step["step_id"]):
                if step["capability_family"] == DELEGATED_CODING_FAMILY:
                    self.assertEqual(step["owner_kind"], "delegated_coding")
                    self.assertEqual(step["owner"], "claude-code")
                    self.assertNotEqual(step["owner"], HERMES_RETAINED_OWNER)
                else:
                    self.assertEqual(step["owner_kind"], "hermes_retained")
                    self.assertEqual(step["owner"], HERMES_RETAINED_OWNER)

    def test_hermes_is_not_a_selectable_coding_owner(self) -> None:
        self.assertNotIn(HERMES_RETAINED_OWNER, WORKFLOW_COMPOSITION_CODING_OWNERS)
        with self.assertRaises(ValueError) as caught:
            build_workflow_composition(COMPOUND_OUTCOME, coding_owner=HERMES_RETAINED_OWNER)

        self.assertIn("cannot assign coding to hermes", str(caught.exception))

    def test_validation_rejects_a_coding_step_reassigned_to_hermes(self) -> None:
        payload = build_workflow_composition(COMPOUND_OUTCOME, coding_owner="codex")
        broken = json.loads(json.dumps(payload))
        index = next(
            position
            for position, step in enumerate(broken["steps"])
            if step["capability_family"] == DELEGATED_CODING_FAMILY
        )
        broken["steps"][index]["owner"] = HERMES_RETAINED_OWNER

        self.assertIn(
            f"steps[{index}]: a delegated coding step must not be owned by hermes",
            validate_workflow_composition(broken),
        )

    def test_validation_rejects_a_coding_family_step_marked_retained(self) -> None:
        payload = build_workflow_composition(COMPOUND_OUTCOME, coding_owner="codex")
        broken = json.loads(json.dumps(payload))
        index = next(
            position
            for position, step in enumerate(broken["steps"])
            if step["capability_family"] == DELEGATED_CODING_FAMILY
        )
        broken["steps"][index]["owner_kind"] = "hermes_retained"
        broken["steps"][index]["owner"] = HERMES_RETAINED_OWNER

        self.assertIn(
            f"steps[{index}]: a {DELEGATED_CODING_FAMILY} step must be delegated, not retained by hermes",
            validate_workflow_composition(broken),
        )

    def test_validation_rejects_a_record_whose_coding_owner_is_hermes(self) -> None:
        payload = build_workflow_composition(COMPOUND_OUTCOME)
        broken = json.loads(json.dumps(payload))
        broken["coding_owner"] = HERMES_RETAINED_OWNER

        self.assertIn(
            "coding_owner must not be hermes: coding is delegated, never retained by the chat orchestrator",
            validate_workflow_composition(broken),
        )

    def test_an_unselected_coding_owner_asks_for_the_choice(self) -> None:
        payload = build_workflow_composition(COMPOUND_OUTCOME)

        self.assertEqual(payload["coding_owner"], CODING_OWNER_CHOICE_PENDING)
        self.assertEqual(payload["next_action"], "choose_coding_owner")


class NoComposablePathTests(unittest.TestCase):
    """A request with no composable path says so instead of faking a workflow."""

    def setUp(self) -> None:
        _clear_caches()

    def test_a_single_outcome_request_is_not_compound_and_carries_no_steps(self) -> None:
        payload = build_workflow_composition("fix the login bug")

        self.assertEqual(validate_workflow_composition(payload), [])
        self.assertEqual(payload["state"], "not_compound")
        self.assertEqual(payload["steps"], [])
        self.assertEqual(payload["step_count"], 0)
        self.assertEqual(payload["next_action"], "use_single_capability_plan")
        self.assertFalse(payload["compound_intent"]["recognized"])
        self.assertIn("one outcome", str(payload["compound_intent"]["reason"]))

    def test_fragments_that_all_resolve_to_one_family_are_not_a_workflow(self) -> None:
        payload = build_workflow_composition("fix the login bug and add the regression test")

        self.assertEqual(validate_workflow_composition(payload), [])
        self.assertEqual(payload["state"], "no_composable_path")
        self.assertEqual(payload["steps"], [])
        self.assertEqual(payload["next_action"], "ask_which_outcomes_to_compose")
        self.assertTrue(payload["compound_intent"]["recognized"])
        reason = str(payload["compound_intent"]["reason"])
        self.assertIn(DELEGATED_CODING_FAMILY, reason)
        self.assertIn("not a workflow", reason)

    def test_a_request_with_no_routable_signal_says_so(self) -> None:
        payload = build_workflow_composition("!!!")

        self.assertEqual(validate_workflow_composition(payload), [])
        self.assertEqual(payload["state"], "not_compound")
        self.assertIn("no routable capability signal", str(payload["compound_intent"]["reason"]))

    def test_validation_rejects_steps_smuggled_onto_a_declining_state(self) -> None:
        composed = build_workflow_composition(COMPOUND_OUTCOME)
        declined = json.loads(json.dumps(build_workflow_composition("fix the login bug")))
        declined["steps"] = json.loads(json.dumps(composed["steps"]))[:1]
        declined["step_count"] = 1

        self.assertIn(
            "state not_compound must not carry steps: a single capability is not a workflow",
            validate_workflow_composition(declined),
        )

    def test_validation_rejects_a_one_step_composed_record(self) -> None:
        payload = build_workflow_composition(COMPOUND_OUTCOME)
        broken = json.loads(json.dumps(payload))
        broken["steps"] = broken["steps"][:1]
        broken["step_count"] = 1
        broken["missing_capabilities"] = []

        self.assertIn("a composed workflow must carry at least two steps", validate_workflow_composition(broken))

    def test_an_empty_outcome_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build_workflow_composition("   ")


class CompoundIntentSegmentationTests(unittest.TestCase):
    """How compound intent is recognised, including where it deliberately is not."""

    def test_connectors_split_a_compound_request_into_its_fragments(self) -> None:
        segments = compound_request_segments(COMPOUND_OUTCOME)

        self.assertEqual(
            segments.segments,
            ("Research the payment providers", "write the migration plan", "implement the winner"),
        )
        self.assertEqual(segments.connectors, (",", "and"))
        self.assertTrue(segments.segmented)

    def test_a_connector_inside_a_word_is_not_a_separator(self) -> None:
        segments = compound_request_segments("understand the router and land the change")

        self.assertEqual(segments.segments, ("understand the router", "land the change"))

    def test_a_single_clause_request_stays_whole(self) -> None:
        segments = compound_request_segments("fix the login bug")

        self.assertEqual(segments.segments, ("fix the login bug",))
        self.assertEqual(segments.connectors, ())
        self.assertFalse(segments.segmented)

    def test_a_request_with_no_routable_token_yields_no_segment(self) -> None:
        self.assertEqual(compound_request_segments("!!!").segments, ())
        self.assertEqual(compound_request_segments("   ").segments, ())

    def test_a_fragment_repeated_in_another_case_is_counted_once(self) -> None:
        segments = compound_request_segments(
            "research the OAuth options, plan the rollout, and Research The OAuth Options"
        )

        self.assertEqual(segments.segments, ("research the OAuth options", "plan the rollout"))

    def test_a_request_that_only_repeats_itself_is_one_outcome(self) -> None:
        segments = compound_request_segments("plan the rollout and Plan The Rollout")

        self.assertFalse(segments.segmented)
        self.assertEqual(build_workflow_composition(segments.segments[0])["state"], "not_compound")

    def test_a_non_english_compound_request_reads_as_one_segment(self) -> None:
        """Per the Routing Language Policy: a visible gap, not a wrong composition."""
        segments = compound_request_segments("결제 실패를 조사하고 계획을 세운 다음 구현해줘")

        self.assertFalse(segments.segmented)
        self.assertEqual(build_workflow_composition(segments.segments[0])["state"], "not_compound")


class CompositionSurfaceTests(unittest.TestCase):
    """The composition reaches a Hermes plan and the CLI."""

    def setUp(self) -> None:
        _clear_caches()

    def test_a_compound_hermes_plan_records_the_composition(self) -> None:
        payload = build_hermes_plan_payload(COMPOUND_OUTCOME)
        composition = payload.get("workflow_composition")

        self.assertIsInstance(composition, dict)
        assert isinstance(composition, dict)
        self.assertEqual(validate_workflow_composition(composition), [])
        self.assertEqual(composition["state"], "composed")

    def test_a_single_outcome_hermes_plan_records_no_composition(self) -> None:
        payload = build_hermes_plan_payload("fix the login bug")

        self.assertNotIn("workflow_composition", payload)

    def test_the_recorded_plan_artifact_carries_the_composed_workflow(self) -> None:
        payload = build_hermes_plan_payload(COMPOUND_OUTCOME)
        markdown = render_plan_markdown(payload, "plan.md")
        composition = payload["workflow_composition"]
        assert isinstance(composition, dict)

        self.assertIn("## Composed Workflow", markdown)
        for step in composition["steps"]:
            self.assertIn(f"`{step['capability']}`", markdown)
            self.assertIn(str(step["evidence_boundary"]), markdown)
        self.assertIn(str(composition["ownership_rule"]), markdown)
        self.assertIn(str(composition["claim_boundary"]), markdown)

    def test_a_single_outcome_plan_artifact_has_no_composed_workflow_section(self) -> None:
        markdown = render_plan_markdown(build_hermes_plan_payload("fix the login bug"), "plan.md")

        self.assertNotIn("## Composed Workflow", markdown)

    def test_a_hermes_executor_target_falls_back_to_an_unmade_choice(self) -> None:
        payload = build_hermes_plan_payload(COMPOUND_OUTCOME, executor_target="hermes")
        composition = payload["workflow_composition"]
        assert isinstance(composition, dict)

        self.assertEqual(composition["coding_owner"], CODING_OWNER_CHOICE_PENDING)
        self.assertEqual(validate_workflow_composition(composition), [])

    def test_cli_prints_plain_text_by_default(self) -> None:
        status, stdout, stderr = run_cli(["hermes", "compose", COMPOUND_OUTCOME], output_json=False)

        self.assertEqual(status, 0, stderr)
        self.assertIn("Ordered workflow:", stdout)
        self.assertIn("Next action:", stdout)
        self.assertNotIn('"schema_version"', stdout)

    def test_cli_json_prints_a_valid_record(self) -> None:
        status, stdout, stderr = run_cli(["hermes", "compose", "--json", COMPOUND_OUTCOME], output_json=False)

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(validate_workflow_composition(payload), [])
        self.assertEqual(payload["schema_version"], WORKFLOW_COMPOSITION_SCHEMA_VERSION)

    def test_cli_carries_constraints_and_the_selected_coding_owner(self) -> None:
        status, stdout, stderr = run_cli(
            [
                "hermes",
                "compose",
                "--json",
                "--constraint",
                "no new dependencies",
                "--coding-owner",
                "codex",
                COMPOUND_OUTCOME,
            ],
            output_json=False,
        )

        self.assertEqual(status, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["constraints"], ["no new dependencies"])
        self.assertEqual(payload["coding_owner"], "codex")
        self.assertEqual(payload["next_action"], "present_ordered_workflow")

    def test_cli_refuses_hermes_as_the_coding_owner(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            run_cli(["hermes", "compose", "--coding-owner", "hermes", COMPOUND_OUTCOME], output_json=False)

        self.assertNotEqual(caught.exception.code, 0)

    def test_plain_text_shows_the_boundaries_and_the_gaps(self) -> None:
        payload = build_workflow_composition(COMPOUND_OUTCOME, available_capabilities=("plan",))
        text = render_workflow_composition_text(payload)

        self.assertIn("Missing capabilities (reported, not installed):", text)
        self.assertIn("[capability not available]", text)
        self.assertIn(WORKFLOW_COMPOSITION_CLAIM_BOUNDARY, text)
        self.assertIn(WORKFLOW_COMPOSITION_INSTALL_POLICY, text)


if __name__ == "__main__":
    unittest.main()
