"""Contracts for `native_capability_blueprint/v1` (issue #791).

Grouped by acceptance criterion:

- AC1: a blueprint identifies every affected native OMH surface, and the
  surface vocabulary it is checked against is re-derived from this repository
  rather than asserted.
- AC2: every blueprint carries natural-language examples and an unobserved-work
  claim boundary.
- AC3: a source-host mechanic may be recorded as observed inspiration and may
  never appear as an OMH runtime requirement. Both directions.

Plus the guard the whole family exists for: a blueprint is a design artifact for
work nobody has done, so nothing in it may read as an implemented capability.

Nothing in this file writes a file. The blueprint is compared by value
throughout, and `Path.write_text` rewrites "\\n" as CRLF on Windows, so a
fixture written that way would compare equal on macOS and unequal on the Windows
job. There is no fixture to get wrong.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from typing import Any

from _local_package import load_local_package

load_local_package()
from omh.workflows.native_capability_blueprint import (  # noqa: E402
    BLUEPRINT_DIGEST_KEYS,
    BLUEPRINT_PRIVACY,
    CLAIM_BOUNDARY,
    CLARIFICATION_POLICIES,
    DEGRADATION_BEHAVIORS,
    DELEGATED_WORK,
    HERMES_RETAINED_WORK,
    IMPLEMENTATION_CLAIM_KEYS,
    NATIVE_CAPABILITY_BLUEPRINT_KEYS,
    NATIVE_CAPABILITY_BLUEPRINT_SCHEMA_VERSION,
    NATIVE_CAPABILITY_SURFACE_ANCHORS,
    NATIVE_CAPABILITY_SURFACES,
    OMH_RUNTIME_REQUIREMENTS,
    OPTIONAL_NATIVE_CAPABILITY_SURFACES,
    REQUIRED_NATIVE_CAPABILITY_SURFACES,
    SOURCE_HOST_MECHANICS,
    NativeCapabilityBlueprintError,
    blueprint_digest_of,
    blueprint_expected_surfaces,
    blueprint_surface_anchor,
    build_native_capability_blueprint,
    missing_required_surfaces,
    unknown_surfaces,
    validate_native_capability_blueprint,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "src" / "workflows" / "native_capability_blueprint.py"

# A repository path named inside an anchor sentence.
_ANCHOR_PATH = re.compile(r"\b[\w./*-]+\.(?:py|md|json)\b")
# A module-level constant an anchor points at: either underscore-prefixed or
# SCREAMING_SNAKE with a separator. Bare acronyms in prose ("OMH") are excluded
# by requiring the underscore, so the check stays about identifiers.
_ANCHOR_CONSTANT = re.compile(r"\b_[A-Z][A-Z0-9_]{2,}\b|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")
_ANCHOR_CLASS = re.compile(r"\b[A-Z][a-zA-Z0-9]*[a-z][A-Z][a-zA-Z0-9]*\b")
_ANCHOR_CALL = re.compile(r"\b[a-z_][a-z0-9_]*\(\)")


def blueprint_kwargs(**overrides: Any) -> dict[str, Any]:
    """A complete, valid blueprint's arguments, minimally overridable."""
    base: dict[str, Any] = {
        "capability_id": "native-capability-blueprint",
        "intent_summary": (
            "Turn a feature demonstrated in another agent tool into a Hermes-native experience blueprint."
        ),
        "example_requests": (
            "Give Hermes the useful behavior from this tool.",
            "Design the native version of that plugin feature.",
        ),
        "clarification_policy": "clarify_then_answer",
        "clarifying_questions": ("Which behavior did you actually see, and where did you see it?",),
        "hermes_retained_work": ("chat_intake", "clarification", "planning"),
        "delegated_work": ("main_implementation_work",),
        "expected_outputs": (
            "native_capability_blueprint/v1",
            "the affected native surfaces",
            "the claim boundary",
        ),
        "affected_surfaces": REQUIRED_NATIVE_CAPABILITY_SURFACES,
        "observed_source_mechanics": ("host_plugin_manifest", "host_tool_definition"),
        "omh_runtime_requirements": ("local_catalog_metadata", "local_deterministic_contract"),
        "evidence_steps": ("observed_material_scoped", "surfaces_identified", "blueprint_rendered"),
        "degradation_behavior": "ask_for_missing_input",
        "non_goals": ("Importing, launching, proxying, or requiring a source extension.",),
        "prepared_at": "2026-08-09T00:00:00Z",
    }
    base.update(overrides)
    return base


def blueprint(**overrides: Any) -> dict[str, Any]:
    return build_native_capability_blueprint(**blueprint_kwargs(**overrides))


def resealed(**overrides: Any) -> dict[str, Any]:
    """A payload edited after minting, with the digest re-derived to match.

    Lets a test fail on the rule under test rather than on the tamper check.
    """
    payload = blueprint()
    payload.update(overrides)
    payload["blueprint_digest"] = blueprint_digest_of(payload)
    return payload


class SurfaceVocabularyTests(unittest.TestCase):
    """The vocabulary AC1 is checked against is this repository's, not a wish list."""

    def test_every_surface_anchors_to_a_file_that_exists(self) -> None:
        for surface in NATIVE_CAPABILITY_SURFACES:
            with self.subTest(surface=surface):
                anchor = blueprint_surface_anchor(surface)
                self.assertTrue(anchor, f"{surface} must say where it lives")
                for token in _ANCHOR_PATH.findall(anchor):
                    if "*" in token:
                        self.assertTrue(
                            list(REPO_ROOT.glob(token)),
                            f"{surface} anchors {token}, which matches nothing",
                        )
                        continue
                    self.assertTrue(
                        (REPO_ROOT / token).is_file(),
                        f"{surface} anchors {token}, which is not a file in this repository",
                    )

    def test_every_anchored_structure_is_present_in_the_file_it_names(self) -> None:
        for surface in NATIVE_CAPABILITY_SURFACES:
            with self.subTest(surface=surface):
                anchor = blueprint_surface_anchor(surface)
                paths = _ANCHOR_PATH.findall(anchor)
                prose = _ANCHOR_PATH.sub(" ", anchor)
                structures = (
                    set(_ANCHOR_CONSTANT.findall(prose))
                    | set(_ANCHOR_CLASS.findall(prose))
                    | set(_ANCHOR_CALL.findall(prose))
                )
                if not structures:
                    continue
                haystack = "\n".join(
                    (REPO_ROOT / token).read_text(encoding="utf-8")
                    for token in paths
                    if "*" not in token and (REPO_ROOT / token).is_file()
                )
                for structure in sorted(structures):
                    self.assertIn(
                        structure.removesuffix("()"),
                        haystack,
                        f"{surface} anchors {structure}, which is absent from {paths}",
                    )

    def test_required_and_optional_surfaces_partition_the_vocabulary(self) -> None:
        self.assertEqual(len(NATIVE_CAPABILITY_SURFACES), 16)
        self.assertEqual(len(REQUIRED_NATIVE_CAPABILITY_SURFACES), 12)
        self.assertEqual(len(OPTIONAL_NATIVE_CAPABILITY_SURFACES), 4)
        self.assertEqual(
            sorted({*REQUIRED_NATIVE_CAPABILITY_SURFACES, *OPTIONAL_NATIVE_CAPABILITY_SURFACES}),
            sorted(NATIVE_CAPABILITY_SURFACES),
        )
        self.assertEqual(
            set(REQUIRED_NATIVE_CAPABILITY_SURFACES) & set(OPTIONAL_NATIVE_CAPABILITY_SURFACES), set()
        )
        self.assertEqual(sorted(NATIVE_CAPABILITY_SURFACE_ANCHORS), sorted(NATIVE_CAPABILITY_SURFACES))

    def test_the_four_optional_surfaces_are_the_conditional_ones(self) -> None:
        # Named rather than derived: a capability that mints no artifact, reads
        # no reviewed memory, prepares no coding handoff, and needs no bespoke
        # card genuinely does not touch these. Moving one into the required set
        # is a deliberate act with a visible line to change.
        self.assertEqual(
            sorted(OPTIONAL_NATIVE_CAPABILITY_SURFACES),
            ["chat_card", "coding_handoff", "memory_policy", "runtime_artifact"],
        )


class AffectedSurfaceTests(unittest.TestCase):
    """AC1: the blueprint identifies every affected native OMH surface."""

    def test_a_blueprint_naming_every_required_surface_validates(self) -> None:
        payload = blueprint()

        self.assertEqual(validate_native_capability_blueprint(payload), [])
        self.assertEqual(
            blueprint_expected_surfaces(payload), tuple(REQUIRED_NATIVE_CAPABILITY_SURFACES)
        )

    def test_a_blueprint_may_also_name_the_conditional_surfaces(self) -> None:
        payload = blueprint(affected_surfaces=NATIVE_CAPABILITY_SURFACES)

        self.assertEqual(validate_native_capability_blueprint(payload), [])
        self.assertEqual(blueprint_expected_surfaces(payload), NATIVE_CAPABILITY_SURFACES)

    def test_omitting_a_required_surface_fails_and_names_the_missing_surface(self) -> None:
        for surface in REQUIRED_NATIVE_CAPABILITY_SURFACES:
            with self.subTest(surface=surface):
                kept = [item for item in REQUIRED_NATIVE_CAPABILITY_SURFACES if item != surface]
                with self.assertRaises(NativeCapabilityBlueprintError) as raised:
                    blueprint(affected_surfaces=kept)
                message = str(raised.exception)
                self.assertIn("missing required surfaces", message)
                self.assertIn(surface, message)
                self.assertIn(blueprint_surface_anchor(surface), message)

    def test_omitting_an_optional_surface_is_not_an_error(self) -> None:
        self.assertEqual(missing_required_surfaces(REQUIRED_NATIVE_CAPABILITY_SURFACES), ())
        self.assertEqual(validate_native_capability_blueprint(blueprint()), [])

    def test_an_unknown_surface_name_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityBlueprintError) as raised:
            blueprint(affected_surfaces=(*REQUIRED_NATIVE_CAPABILITY_SURFACES, "vscode_agent_plugin"))
        message = str(raised.exception)
        self.assertIn("do not exist in this repository", message)
        self.assertIn("vscode_agent_plugin", message)
        self.assertEqual(unknown_surfaces(["skill_catalog", "vscode_agent_plugin"]), ("vscode_agent_plugin",))

    def test_the_surface_accessor_refuses_an_invalid_blueprint(self) -> None:
        # A downstream gate must not receive a partial surface list from a
        # payload that would not validate.
        broken = resealed(affected_surfaces=["skill_catalog"])

        with self.assertRaises(NativeCapabilityBlueprintError):
            blueprint_expected_surfaces(broken)


class ExampleAndClaimBoundaryTests(unittest.TestCase):
    """AC2: natural-language examples and an unobserved-work claim boundary."""

    def test_a_blueprint_without_examples_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityBlueprintError) as raised:
            blueprint(example_requests=())

        self.assertIn("example_requests must name at least 2", str(raised.exception))

    def test_a_single_example_is_not_enough(self) -> None:
        with self.assertRaises(NativeCapabilityBlueprintError):
            blueprint(example_requests=("Give Hermes the useful behavior from this tool.",))

    def test_examples_must_be_sentences_rather_than_commands(self) -> None:
        for command in ("omh chat route 'design this'", "/native-capability-blueprint", "--capability foo"):
            with self.subTest(command=command):
                with self.assertRaises(NativeCapabilityBlueprintError) as raised:
                    blueprint(
                        example_requests=("Give Hermes the useful behavior from this tool.", command)
                    )
                self.assertIn("natural-language requests, not commands", str(raised.exception))

    def test_a_blueprint_without_the_claim_boundary_is_refused(self) -> None:
        errors = validate_native_capability_blueprint(resealed(claim_boundary=""))

        self.assertTrue(any("claim_boundary" in error for error in errors), errors)

    def test_a_softened_claim_boundary_is_refused(self) -> None:
        errors = validate_native_capability_blueprint(
            resealed(claim_boundary="A blueprint describes a capability.")
        )

        self.assertTrue(any("claim_boundary" in error for error in errors), errors)

    def test_the_claim_boundary_denies_evidence_existence_and_host_requirements(self) -> None:
        for phrase in (
            "not dispatch, execution, verification, review, CI, merge-readiness, or merge evidence",
            "not a claim that the capability exists",
            "never makes a source host",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, CLAIM_BOUNDARY)

    def test_clarification_behavior_is_recorded_in_both_directions(self) -> None:
        direct = blueprint(clarification_policy="answer_directly", clarifying_questions=())
        self.assertEqual(validate_native_capability_blueprint(direct), [])

        with self.assertRaises(NativeCapabilityBlueprintError) as asks_nothing:
            blueprint(clarification_policy="clarify_before_handoff", clarifying_questions=())
        self.assertIn("what Hermes asks before it answers", str(asks_nothing.exception))

        with self.assertRaises(NativeCapabilityBlueprintError) as asks_anyway:
            blueprint(
                clarification_policy="answer_directly",
                clarifying_questions=("Which behavior did you see?",),
            )
        self.assertIn("must be empty when the policy answers directly", str(asks_anyway.exception))

    def test_expected_outputs_non_goals_and_degradation_are_all_required(self) -> None:
        for field, value, fragment in (
            ("expected_outputs", (), "expected_outputs must name at least 1"),
            ("non_goals", (), "non_goals must name at least 1"),
            ("degradation_behavior", "best_effort", "degradation_behavior is unsupported"),
            ("hermes_retained_work", (), "hermes_retained_work must name at least 1"),
            ("evidence_steps", ("only_one",), "evidence_steps must name at least 2"),
            ("intent_summary", "", "intent_summary must be a non-empty string"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(NativeCapabilityBlueprintError) as raised:
                    blueprint(**{field: value})
                self.assertIn(fragment, str(raised.exception))

    def test_delegated_work_may_be_empty_for_a_capability_that_delegates_nothing(self) -> None:
        payload = blueprint(delegated_work=())

        self.assertEqual(validate_native_capability_blueprint(payload), [])
        self.assertEqual(payload["delegated_work"], [])


class SourceMechanicBoundaryTests(unittest.TestCase):
    """AC3: observed there never becomes required here."""

    def test_a_source_mechanic_as_observed_inspiration_is_fine(self) -> None:
        for mechanic in SOURCE_HOST_MECHANICS:
            with self.subTest(mechanic=mechanic):
                payload = blueprint(observed_source_mechanics=(mechanic,))
                self.assertEqual(validate_native_capability_blueprint(payload), [])
                self.assertEqual(payload["observed_source_mechanics"], [mechanic])

    def test_the_same_mechanic_as_a_runtime_requirement_is_a_validation_error(self) -> None:
        for mechanic in SOURCE_HOST_MECHANICS:
            with self.subTest(mechanic=mechanic):
                with self.assertRaises(NativeCapabilityBlueprintError) as raised:
                    blueprint(omh_runtime_requirements=("local_catalog_metadata", mechanic))
                message = str(raised.exception)
                self.assertIn("must not name a source-host mechanic", message)
                self.assertIn(mechanic, message)
                self.assertIn("separate approved boundary", message)

    def test_a_mechanic_can_be_observed_and_refused_as_a_requirement_at_once(self) -> None:
        errors = validate_native_capability_blueprint(
            resealed(
                observed_source_mechanics=["host_plugin_registry"],
                omh_runtime_requirements=["host_plugin_registry"],
            )
        )

        self.assertTrue(any("must not name a source-host mechanic" in error for error in errors), errors)
        self.assertFalse(any("observed_source_mechanics has unsupported" in error for error in errors), errors)

    def test_an_omh_requirement_listed_as_an_observed_mechanic_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityBlueprintError) as raised:
            blueprint(observed_source_mechanics=("local_catalog_metadata",))

        self.assertIn("observed_source_mechanics has unsupported entries", str(raised.exception))

    def test_the_two_vocabularies_are_disjoint(self) -> None:
        self.assertEqual(set(SOURCE_HOST_MECHANICS) & set(OMH_RUNTIME_REQUIREMENTS), set())

    def test_a_blueprint_must_state_at_least_one_omh_runtime_requirement(self) -> None:
        with self.assertRaises(NativeCapabilityBlueprintError) as raised:
            blueprint(omh_runtime_requirements=())

        self.assertIn("omh_runtime_requirements must name at least 1", str(raised.exception))

    def test_no_omh_runtime_requirement_needs_a_foreign_host(self) -> None:
        # The vocabulary is the enforcing half of AC3: widening it is a reviewed
        # diff, which is the "separate approved boundary" the criterion names.
        for requirement in OMH_RUNTIME_REQUIREMENTS:
            with self.subTest(requirement=requirement):
                self.assertNotIn("host", requirement)
                self.assertNotIn("remote", requirement)
                self.assertNotIn("network", requirement)


class BlueprintIsNotACapabilityTests(unittest.TestCase):
    """A blueprint never reads as an implemented capability."""

    def test_an_implementation_claim_key_is_refused_by_name(self) -> None:
        for key in sorted(IMPLEMENTATION_CLAIM_KEYS):
            with self.subTest(key=key):
                errors = validate_native_capability_blueprint({**blueprint(), key: True})
                self.assertTrue(
                    any("implementation-claim keys" in error and key in error for error in errors), errors
                )

    def test_a_raw_or_hidden_key_is_refused(self) -> None:
        errors = validate_native_capability_blueprint({**blueprint(), "transcript": "..."})

        self.assertTrue(any("raw or hidden keys" in error for error in errors), errors)

    def test_the_key_set_is_closed_in_both_directions(self) -> None:
        payload = blueprint()
        self.assertEqual(sorted(payload), sorted(NATIVE_CAPABILITY_BLUEPRINT_KEYS))

        extra = validate_native_capability_blueprint({**payload, "connector_manifest": "x"})
        self.assertTrue(any("unsupported keys" in error for error in extra), extra)

        for key in NATIVE_CAPABILITY_BLUEPRINT_KEYS:
            with self.subTest(key=key):
                short = {name: value for name, value in payload.items() if name != key}
                errors = validate_native_capability_blueprint(short)
                self.assertTrue(any("is missing keys" in error and key in error for error in errors), errors)

    def test_the_payload_is_metadata_only_and_schema_versioned(self) -> None:
        payload = blueprint()

        self.assertEqual(payload["schema_version"], NATIVE_CAPABILITY_BLUEPRINT_SCHEMA_VERSION)
        self.assertEqual(NATIVE_CAPABILITY_BLUEPRINT_SCHEMA_VERSION, "native_capability_blueprint/v1")
        self.assertEqual(payload["privacy"], BLUEPRINT_PRIVACY)
        self.assertEqual(BLUEPRINT_PRIVACY, "metadata_only")
        self.assertTrue(any("privacy" in error for error in validate_native_capability_blueprint(resealed(privacy="raw"))))

    def test_a_non_mapping_is_refused_rather_than_crashing(self) -> None:
        self.assertEqual(
            validate_native_capability_blueprint(["not", "a", "blueprint"]),  # type: ignore[arg-type]
            ["native_capability_blueprint must be an object"],
        )


class DeterminismTests(unittest.TestCase):
    """No clock reaches a compared value."""

    def test_the_module_reads_no_clock(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertEqual(imported & {"time", "datetime", "random", "secrets"}, set())
        self.assertNotIn("utc_now", called)

    def test_two_builds_of_one_design_are_identical(self) -> None:
        self.assertEqual(blueprint(), blueprint())

    def test_the_digest_ignores_the_prepared_at_stamp(self) -> None:
        early = blueprint(prepared_at="2026-01-01T00:00:00Z")
        late = blueprint(prepared_at="2026-12-31T23:59:59Z")

        self.assertNotEqual(early["prepared_at"], late["prepared_at"])
        self.assertEqual(early["blueprint_digest"], late["blueprint_digest"])
        self.assertNotIn("prepared_at", BLUEPRINT_DIGEST_KEYS)
        self.assertNotIn("blueprint_digest", BLUEPRINT_DIGEST_KEYS)

    def test_closed_vocabulary_order_does_not_change_the_digest(self) -> None:
        forward = blueprint(affected_surfaces=REQUIRED_NATIVE_CAPABILITY_SURFACES)
        backward = blueprint(affected_surfaces=tuple(reversed(REQUIRED_NATIVE_CAPABILITY_SURFACES)))

        self.assertEqual(forward, backward)

    def test_an_edited_payload_fails_its_own_digest(self) -> None:
        payload = blueprint()
        payload["intent_summary"] = "Something else entirely."

        errors = validate_native_capability_blueprint(payload)
        self.assertTrue(any("does not match the design it seals" in error for error in errors), errors)

    def test_prepared_at_is_a_parameter_and_defaults_to_empty(self) -> None:
        payload = build_native_capability_blueprint(
            **{key: value for key, value in blueprint_kwargs().items() if key != "prepared_at"}
        )

        self.assertEqual(payload["prepared_at"], "")
        self.assertEqual(validate_native_capability_blueprint(payload), [])


class OwnershipVocabularyTests(unittest.TestCase):
    """The ownership split is `docs/DIRECTION.md`'s, not a second one."""

    def test_the_vocabularies_cover_the_direction_ownership_boundary(self) -> None:
        direction = (REPO_ROOT / "docs" / "DIRECTION.md").read_text(encoding="utf-8")
        section = direction.split("## Ownership Boundary", 1)[1].split("## Direction Rules", 1)[0]
        # The doc writes some of these hyphenated ("source-backed research"),
        # so the comparison is over words rather than over separators.
        words = section.lower().replace("-", " ")

        for term in (*HERMES_RETAINED_WORK, *DELEGATED_WORK):
            with self.subTest(term=term):
                self.assertIn(term.replace("_", " "), words)

    def test_every_vocabulary_member_is_unique_and_lowercase(self) -> None:
        for name, vocabulary in (
            ("NATIVE_CAPABILITY_SURFACES", NATIVE_CAPABILITY_SURFACES),
            ("SOURCE_HOST_MECHANICS", SOURCE_HOST_MECHANICS),
            ("OMH_RUNTIME_REQUIREMENTS", OMH_RUNTIME_REQUIREMENTS),
            ("CLARIFICATION_POLICIES", CLARIFICATION_POLICIES),
            ("DEGRADATION_BEHAVIORS", DEGRADATION_BEHAVIORS),
            ("HERMES_RETAINED_WORK", HERMES_RETAINED_WORK),
            ("DELEGATED_WORK", DELEGATED_WORK),
        ):
            with self.subTest(vocabulary=name):
                self.assertEqual(len(set(vocabulary)), len(vocabulary))
                self.assertEqual(list(vocabulary), [item.lower() for item in vocabulary])


if __name__ == "__main__":
    unittest.main()
