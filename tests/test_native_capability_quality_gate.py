"""Contracts for `native_capability_quality_gate/v1` (issue #795).

Grouped by acceptance criterion:

- AC1: a missing native surface fails and names itself, or carries an explicit
  exemption with a reason. An exemption without a reason is a validation error.
- AC2: generated guidance is reported as reproducible for a clean tree and as
  non-reproducible for a tampered one, through the repository's own checks.
- AC3: structural success never reads as runtime, review, CI, or release
  evidence, and a caller cannot staple `pass` onto an incomplete gate.

Plus the binding this gate exists on top of: the expected surface set is #791's
vocabulary, imported rather than restated.

Two file-writing rules this file follows. Nothing under the repository is ever
written: the AC2 tamper happens in a copy under a temporary directory, so the
byte gates cannot be left failing for the next run. And the one write it does
make goes through `atomic_write_text`, which pins "\\n" on Windows -- the bytes
it writes are compared against rendered template bytes moments later, and
`Path.write_text` would rewrite them as CRLF and make the comparison decide the
platform rather than the tamper.
"""

from __future__ import annotations

import ast
import inspect
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from _local_package import load_local_package

load_local_package()
from omh.local_store import atomic_write_text  # noqa: E402
from omh.maintenance.drift import drift_report, generated_artifacts  # noqa: E402
from omh.skills.validation import validate_catalog_contract  # noqa: E402
from omh.workflows.native_capability_blueprint import (  # noqa: E402
    IMPLEMENTATION_CLAIM_KEYS,
    NATIVE_CAPABILITY_SURFACES,
    OPTIONAL_NATIVE_CAPABILITY_SURFACES,
    REQUIRED_NATIVE_CAPABILITY_SURFACES,
    NativeCapabilityBlueprintError,
    blueprint_surface_anchor,
    build_native_capability_blueprint,
)
from omh.workflows.native_capability_quality_gate import (  # noqa: E402
    GENERATED_GUIDANCE_KEYS,
    NATIVE_CAPABILITY_QUALITY_GATE_CLAIM_BOUNDARY,
    NATIVE_CAPABILITY_QUALITY_GATE_KEYS,
    NATIVE_CAPABILITY_QUALITY_GATE_SCHEMA_VERSION,
    QUALITY_GATE_PRIVACY,
    QUALITY_GATE_VERDICT_CLAIMS,
    QUALITY_GATE_VERDICTS,
    REFUSED_QUALITY_GATE_VERDICTS,
    SURFACE_FINDING_KEYS,
    SURFACE_STATES,
    VERDICT_CLAIM_DENIAL,
    NativeCapabilityQualityGateError,
    build_native_capability_quality_gate,
    derive_quality_gate_verdict,
    expected_gate_surfaces,
    generated_guidance_reproducibility,
    quality_gate_unmet_surfaces,
    quality_gate_verdict_claim,
    unmet_surface_anchors,
    validate_native_capability_quality_gate,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "src" / "workflows" / "native_capability_quality_gate.py"

CAPABILITY_ID = "native-capability-quality-gate"
PREPARED_AT = "2026-08-09T00:00:00Z"
REASON = "This capability mints no local artifact, so there is nothing to version."

# The four families the copied tree needs for the drift probe to see the same
# thing it sees in the checkout.
COPIED_TREE_DIRECTORIES = ("skills",)


def finding(surface: str, state: str = "present", reason: str = "") -> dict[str, Any]:
    return {"surface": surface, "state": state, "reason": reason}


def findings(*, states: dict[str, tuple[str, str]] | None = None, drop: str = "") -> list[dict[str, Any]]:
    """One row per required surface, `present` unless overridden."""
    overrides = states or {}
    rows = []
    for surface in REQUIRED_NATIVE_CAPABILITY_SURFACES:
        if surface == drop:
            continue
        state, reason = overrides.get(surface, ("present", ""))
        rows.append(finding(surface, state, reason))
    return rows


def gate(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "capability_id": CAPABILITY_ID,
        "surfaces": findings(),
        "repo_root": REPO_ROOT,
        "prepared_at": PREPARED_AT,
    }
    arguments.update(overrides)
    return build_native_capability_quality_gate(**arguments)


def edited(payload: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """A payload changed after minting, with nothing re-derived.

    The shape a hand-written or forwarded payload arrives in, and the only way
    to hand the validator a verdict that does not follow from its findings.
    """
    return {**payload, **overrides}


def blueprint_naming(surfaces: tuple[str, ...]) -> dict[str, Any]:
    return build_native_capability_blueprint(
        capability_id=CAPABILITY_ID,
        intent_summary="Report whether one native capability is genuinely complete across OMH.",
        example_requests=(
            "Is the memory sync capability actually finished?",
            "Check whether that new workflow is complete everywhere.",
        ),
        clarification_policy="clarify_then_answer",
        clarifying_questions=("Which capability should Hermes gate?",),
        hermes_retained_work=("chat_intake", "clarification"),
        expected_outputs=("native_capability_quality_gate/v1", "the unanswered surfaces"),
        affected_surfaces=surfaces,
        omh_runtime_requirements=("local_catalog_metadata", "local_deterministic_contract"),
        evidence_steps=("surfaces_answered", "generated_guidance_checked", "verdict_derived"),
        degradation_behavior="explain_gap_and_stop",
        non_goals=("Certifying, ranking, or admitting an external package.",),
        prepared_at=PREPARED_AT,
    )


def copied_tree(root: Path) -> Path:
    """A byte-for-byte copy of every tree the generated-output checks read."""
    for name in COPIED_TREE_DIRECTORIES:
        shutil.copytree(REPO_ROOT / name, root / name)
    for artifact in generated_artifacts():
        source = REPO_ROOT / artifact.path
        target = root / artifact.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return root


class SurfaceVocabularyBindingTests(unittest.TestCase):
    """The expected set is #791's vocabulary, not a second copy of it."""

    def test_the_vocabulary_is_imported_from_the_blueprint_module(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.endswith("native_capability_blueprint")
            for alias in node.names
        }

        self.assertLessEqual(
            {
                "NATIVE_CAPABILITY_SURFACES",
                "REQUIRED_NATIVE_CAPABILITY_SURFACES",
                "blueprint_expected_surfaces",
                "blueprint_surface_anchor",
            },
            imported,
        )

    def test_the_module_writes_no_surface_name_of_its_own(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for surface in NATIVE_CAPABILITY_SURFACES:
            with self.subTest(surface=surface):
                self.assertNotIn(f'"{surface}"', source)
                self.assertNotIn(f"'{surface}'", source)

    def test_the_default_expected_set_is_the_required_surfaces(self) -> None:
        self.assertEqual(expected_gate_surfaces(), tuple(REQUIRED_NATIVE_CAPABILITY_SURFACES))

    def test_a_blueprint_widens_the_expected_set_with_what_it_promised(self) -> None:
        promised = blueprint_naming(NATIVE_CAPABILITY_SURFACES)

        self.assertEqual(expected_gate_surfaces(blueprint=promised), NATIVE_CAPABILITY_SURFACES)

        with self.assertRaises(NativeCapabilityQualityGateError) as raised:
            gate(blueprint=promised)
        message = str(raised.exception)
        for surface in OPTIONAL_NATIVE_CAPABILITY_SURFACES:
            self.assertIn(surface, message)
            self.assertIn(blueprint_surface_anchor(surface), message)

    def test_a_blueprint_that_promised_nothing_extra_leaves_the_expected_set_alone(self) -> None:
        promised = blueprint_naming(REQUIRED_NATIVE_CAPABILITY_SURFACES)
        payload = gate(blueprint=promised)

        self.assertEqual(payload["expected_surfaces"], list(REQUIRED_NATIVE_CAPABILITY_SURFACES))
        self.assertEqual(payload["verdict"], "pass")

    def test_an_invalid_blueprint_is_refused_by_the_791_accessor(self) -> None:
        broken = {**blueprint_naming(REQUIRED_NATIVE_CAPABILITY_SURFACES), "intent_summary": ""}

        with self.assertRaises(NativeCapabilityBlueprintError):
            expected_gate_surfaces(blueprint=broken)

    def test_answering_a_conditional_surface_without_a_blueprint_is_allowed(self) -> None:
        extra = OPTIONAL_NATIVE_CAPABILITY_SURFACES[0]
        payload = gate(surfaces=[*findings(), finding(extra)])

        self.assertIn(extra, payload["expected_surfaces"])
        self.assertEqual(payload["verdict"], "pass")


class MissingSurfaceTests(unittest.TestCase):
    """AC1: absent surfaces fail and name themselves, or carry an exemption."""

    def test_a_capability_answering_every_required_surface_passes(self) -> None:
        payload = gate()

        self.assertEqual(validate_native_capability_quality_gate(payload), [])
        self.assertEqual(payload["verdict"], "pass")
        self.assertEqual(payload["unmet_surfaces"], [])

    def test_a_missing_surface_fails_and_names_the_surface(self) -> None:
        for surface in REQUIRED_NATIVE_CAPABILITY_SURFACES:
            with self.subTest(surface=surface):
                payload = gate(surfaces=findings(states={surface: ("missing", "")}))

                self.assertEqual(payload["verdict"], "revise")
                self.assertEqual(payload["unmet_surfaces"], [surface])
                self.assertIn(
                    f"{surface} ({blueprint_surface_anchor(surface)})", unmet_surface_anchors(payload)
                )
                self.assertEqual(validate_native_capability_quality_gate(payload), [])

    def test_omitting_a_required_surface_entirely_is_refused_by_name(self) -> None:
        for surface in REQUIRED_NATIVE_CAPABILITY_SURFACES:
            with self.subTest(surface=surface):
                with self.assertRaises(NativeCapabilityQualityGateError) as raised:
                    gate(surfaces=findings(drop=surface))
                message = str(raised.exception)
                self.assertIn("does not answer for every expected surface", message)
                self.assertIn(surface, message)
                self.assertIn(blueprint_surface_anchor(surface), message)

    def test_the_same_surface_with_a_recorded_exemption_passes(self) -> None:
        for surface in REQUIRED_NATIVE_CAPABILITY_SURFACES:
            with self.subTest(surface=surface):
                payload = gate(surfaces=findings(states={surface: ("exempt", REASON)}))

                self.assertEqual(payload["verdict"], "pass")
                self.assertEqual(payload["unmet_surfaces"], [])
                self.assertEqual(validate_native_capability_quality_gate(payload), [])

    def test_an_exemption_without_a_reason_is_refused(self) -> None:
        surface = REQUIRED_NATIVE_CAPABILITY_SURFACES[0]

        with self.assertRaises(NativeCapabilityQualityGateError) as raised:
            gate(surfaces=findings(states={surface: ("exempt", "")}))
        message = str(raised.exception)
        self.assertIn("must record why it does not apply", message)
        self.assertIn("an exemption without a reason is an omission", message)

        forwarded = edited(gate(), surfaces=findings(states={surface: ("exempt", "")}))
        errors = validate_native_capability_quality_gate(forwarded)
        self.assertTrue(any("must record why it does not apply" in error for error in errors), errors)

    def test_a_shrug_is_not_a_reason(self) -> None:
        surface = REQUIRED_NATIVE_CAPABILITY_SURFACES[0]
        for shrug in ("n/a", "none", "-", "skip", "no"):
            with self.subTest(shrug=shrug):
                with self.assertRaises(NativeCapabilityQualityGateError) as raised:
                    gate(surfaces=findings(states={surface: ("exempt", shrug)}))
                self.assertIn("exemption reason must be", str(raised.exception))

    def test_a_reason_carrying_a_path_or_a_link_is_refused(self) -> None:
        surface = REQUIRED_NATIVE_CAPABILITY_SURFACES[0]
        for unsafe in (
            "Covered by src/quality/chat_card_coverage.py instead",
            "Explained at https://example.invalid/decision",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(NativeCapabilityQualityGateError) as raised:
                    gate(surfaces=findings(states={surface: ("exempt", unsafe)}))
                self.assertIn("one bounded metadata line", str(raised.exception))

    def test_a_reason_on_a_surface_that_is_not_exempt_is_refused(self) -> None:
        surface = REQUIRED_NATIVE_CAPABILITY_SURFACES[0]
        for state in ("present", "missing"):
            with self.subTest(state=state):
                with self.assertRaises(NativeCapabilityQualityGateError) as raised:
                    gate(surfaces=findings(states={surface: (state, REASON)}))
                self.assertIn("must not carry a reason", str(raised.exception))

    def test_an_unknown_surface_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityQualityGateError) as raised:
            gate(surfaces=[*findings(), finding("vscode_agent_plugin")])
        message = str(raised.exception)
        self.assertIn("does not exist in this repository", message)
        self.assertIn("vscode_agent_plugin", message)

        forwarded = edited(gate(), surfaces=[*findings(), finding("vscode_agent_plugin")])
        errors = validate_native_capability_quality_gate(forwarded)
        self.assertTrue(any("vscode_agent_plugin" in error for error in errors), errors)

    def test_an_unknown_surface_state_is_refused(self) -> None:
        surface = REQUIRED_NATIVE_CAPABILITY_SURFACES[0]

        with self.assertRaises(NativeCapabilityQualityGateError) as raised:
            gate(surfaces=findings(states={surface: ("partial", "")}))
        message = str(raised.exception)
        self.assertIn("unsupported state", message)
        self.assertIn(str(list(SURFACE_STATES)), message)

    def test_answering_for_one_surface_twice_is_refused(self) -> None:
        surface = REQUIRED_NATIVE_CAPABILITY_SURFACES[0]

        with self.assertRaises(NativeCapabilityQualityGateError) as raised:
            gate(surfaces=[*findings(), finding(surface)])
        self.assertIn("more than once", str(raised.exception))

    def test_rows_are_normalized_into_the_declared_surface_order(self) -> None:
        payload = gate(surfaces=list(reversed(findings())))

        self.assertEqual(
            [row["surface"] for row in payload["surfaces"]], list(REQUIRED_NATIVE_CAPABILITY_SURFACES)
        )
        self.assertEqual(sorted(payload["surfaces"][0]), sorted(SURFACE_FINDING_KEYS))


class GeneratedGuidanceTests(unittest.TestCase):
    """AC2: reproducibility is reported through the repository's own checks."""

    def test_the_probe_reuses_the_catalog_and_drift_checks_and_adds_nothing(self) -> None:
        block = generated_guidance_reproducibility(repo_root=REPO_ROOT)
        validation = validate_catalog_contract()
        report = drift_report(repo_root=REPO_ROOT, counts=(), budgets=())

        self.assertEqual(sorted(block), sorted(GENERATED_GUIDANCE_KEYS))
        self.assertEqual(block["catalog_validation_ok"], validation["ok"] is True)
        self.assertEqual(block["catalog_validation_errors"], list(validation["errors"]))
        self.assertEqual(block["checked"], ["catalog_validation", *report["checked"]])
        self.assertEqual(len(block["stale_artifacts"]), report["drift_count"])

    def test_the_committed_tree_reports_reproducible_generated_guidance(self) -> None:
        block = generated_guidance_reproducibility(repo_root=REPO_ROOT)

        self.assertTrue(
            block["reproducible"],
            f"regenerate the stale artifacts before running this suite: {block['stale_artifacts']}",
        )
        self.assertEqual(block["stale_artifacts"], [])
        self.assertEqual(gate()["verdict"], "pass")

    def test_a_tampered_generated_file_reports_non_reproducible_and_blocks(self) -> None:
        artifact = generated_artifacts()[0]
        with tempfile.TemporaryDirectory(prefix="omh-quality-gate-") as raw_root:
            root = copied_tree(Path(raw_root))

            clean = gate(repo_root=root)
            self.assertTrue(clean["generated_guidance"]["reproducible"])
            self.assertEqual(clean["verdict"], "pass")

            target = root / artifact.path
            atomic_write_text(target, target.read_text(encoding="utf-8") + "\ndrifted\n")
            tampered = gate(repo_root=root)

        self.assertFalse(tampered["generated_guidance"]["reproducible"])
        self.assertEqual(tampered["generated_guidance"]["stale_artifacts"], [artifact.name])
        self.assertEqual(tampered["verdict"], "blocked")
        self.assertEqual(validate_native_capability_quality_gate(tampered), [])
        self.assertEqual(tampered["unmet_surfaces"], [])

    def test_a_stapled_reproducible_flag_is_refused(self) -> None:
        payload = gate()
        block = {**payload["generated_guidance"], "stale_artifacts": ["workflows_doc"]}

        errors = validate_native_capability_quality_gate(edited(payload, generated_guidance=block))
        self.assertTrue(
            any("reproducible must be derived from the checks it reports" in error for error in errors),
            errors,
        )

    def test_a_failed_catalog_validation_blocks_rather_than_revises(self) -> None:
        payload = gate()
        block = {
            **payload["generated_guidance"],
            "catalog_validation_ok": False,
            "catalog_validation_errors": ["skill demo description must be a non-empty string"],
            "reproducible": False,
        }

        blocked = edited(payload, generated_guidance=block)
        self.assertEqual(derive_quality_gate_verdict(blocked), "blocked")

    def test_a_report_that_checked_nothing_is_not_reproducible(self) -> None:
        payload = gate()
        block = {**payload["generated_guidance"], "checked": [], "reproducible": False}

        self.assertEqual(derive_quality_gate_verdict(edited(payload, generated_guidance=block)), "blocked")

    def test_a_stale_artifact_that_was_never_checked_is_refused(self) -> None:
        payload = gate()
        block = {**payload["generated_guidance"], "stale_artifacts": ["a_file_nobody_checked"], "reproducible": False}

        errors = validate_native_capability_quality_gate(edited(payload, generated_guidance=block))
        self.assertTrue(
            any("reports stale artifacts it did not check" in error for error in errors), errors
        )


class VerdictTests(unittest.TestCase):
    """AC3: the verdict is derived, and none of it reads as evidence."""

    def test_all_three_verdicts_are_reachable_and_distinguishable(self) -> None:
        passing = gate()
        revising = gate(surfaces=findings(states={REQUIRED_NATIVE_CAPABILITY_SURFACES[0]: ("missing", "")}))
        blocked = edited(
            passing,
            generated_guidance={**passing["generated_guidance"], "reproducible": False, "checked": []},
        )

        self.assertEqual(passing["verdict"], "pass")
        self.assertEqual(revising["verdict"], "revise")
        self.assertEqual(derive_quality_gate_verdict(blocked), "blocked")
        self.assertEqual(sorted({"pass", "revise", "blocked"}), sorted(QUALITY_GATE_VERDICTS))

    def test_the_builder_accepts_no_verdict_from_the_caller(self) -> None:
        parameters = inspect.signature(build_native_capability_quality_gate).parameters

        self.assertNotIn("verdict", parameters)
        self.assertNotIn("verdict_claim", parameters)
        self.assertNotIn("unmet_surfaces", parameters)
        with self.assertRaises(TypeError):
            build_native_capability_quality_gate(  # type: ignore[call-arg]
                capability_id=CAPABILITY_ID,
                surfaces=findings(),
                repo_root=REPO_ROOT,
                verdict="pass",
            )

    def test_a_caller_supplied_pass_on_an_incomplete_gate_is_refused(self) -> None:
        surface = REQUIRED_NATIVE_CAPABILITY_SURFACES[0]
        incomplete = gate(surfaces=findings(states={surface: ("missing", "")}))
        stapled = edited(incomplete, verdict="pass", unmet_surfaces=[], verdict_claim=QUALITY_GATE_VERDICT_CLAIMS["pass"])

        errors = validate_native_capability_quality_gate(stapled)
        self.assertTrue(
            any("verdict must be derived from the findings" in error and "revise" in error for error in errors),
            errors,
        )
        self.assertTrue(any("unmet_surfaces must be derived" in error for error in errors), errors)
        self.assertEqual(derive_quality_gate_verdict(stapled), "revise")

    def test_a_caller_supplied_pass_over_a_dropped_surface_is_refused(self) -> None:
        surface = REQUIRED_NATIVE_CAPABILITY_SURFACES[-1]
        passing = gate()
        stapled = edited(
            passing, surfaces=[row for row in passing["surfaces"] if row["surface"] != surface]
        )

        errors = validate_native_capability_quality_gate(stapled)
        self.assertTrue(any("does not answer for every expected surface" in error for error in errors), errors)
        self.assertTrue(any("verdict must be derived from the findings" in error for error in errors), errors)
        self.assertEqual(quality_gate_unmet_surfaces(stapled), (surface,))

    def test_a_caller_supplied_pass_over_a_reasonless_exemption_is_refused(self) -> None:
        surface = REQUIRED_NATIVE_CAPABILITY_SURFACES[0]
        passing = gate()
        stapled = edited(
            passing,
            surfaces=[
                {**row, "state": "exempt"} if row["surface"] == surface else row
                for row in passing["surfaces"]
            ],
        )

        self.assertEqual(derive_quality_gate_verdict(stapled), "revise")
        errors = validate_native_capability_quality_gate(stapled)
        self.assertTrue(any("must record why it does not apply" in error for error in errors), errors)

    def test_a_caller_supplied_pass_over_a_blocked_tree_is_refused(self) -> None:
        passing = gate()
        stapled = edited(
            passing,
            generated_guidance={
                **passing["generated_guidance"],
                "stale_artifacts": ["workflows_doc"],
                "reproducible": False,
            },
        )

        errors = validate_native_capability_quality_gate(stapled)
        self.assertTrue(
            any("verdict must be derived from the findings" in error and "blocked" in error for error in errors),
            errors,
        )

    def test_an_unknown_verdict_is_refused(self) -> None:
        errors = validate_native_capability_quality_gate(edited(gate(), verdict="mostly_fine"))

        self.assertTrue(any("verdict is unsupported" in error for error in errors), errors)
        with self.assertRaises(NativeCapabilityQualityGateError):
            quality_gate_verdict_claim("mostly_fine")

    def test_a_verdict_claiming_evidence_is_refused_by_name(self) -> None:
        for refused in REFUSED_QUALITY_GATE_VERDICTS:
            with self.subTest(refused=refused):
                errors = validate_native_capability_quality_gate(edited(gate(), verdict=refused))
                self.assertTrue(
                    any("may not claim the capability was run, reviewed, or released" in error for error in errors),
                    errors,
                )

    def test_a_mismatched_verdict_claim_is_refused(self) -> None:
        errors = validate_native_capability_quality_gate(
            edited(gate(), verdict_claim="This capability is complete and shipped.")
        )

        self.assertTrue(any("verdict_claim must be the one sentence" in error for error in errors), errors)


class StructureIsNotEvidenceTests(unittest.TestCase):
    """AC3: nothing in the vocabulary reads as runtime, review, CI, or release."""

    def test_every_verdict_claim_denies_run_review_ci_and_release(self) -> None:
        self.assertEqual(sorted(QUALITY_GATE_VERDICT_CLAIMS), sorted(QUALITY_GATE_VERDICTS))
        for verdict in QUALITY_GATE_VERDICTS:
            with self.subTest(verdict=verdict):
                claim = quality_gate_verdict_claim(verdict)
                self.assertIn(VERDICT_CLAIM_DENIAL, claim)
                for denied in ("run", "reviewed", "CI", "merged", "released"):
                    self.assertIn(denied, claim)

    def test_no_verdict_word_reads_as_evidence(self) -> None:
        self.assertEqual(set(QUALITY_GATE_VERDICTS) & set(REFUSED_QUALITY_GATE_VERDICTS), set())
        for verdict in QUALITY_GATE_VERDICTS:
            with self.subTest(verdict=verdict):
                self.assertNotIn(verdict, ("verified", "tested", "passing", "green", "approved"))

    def test_the_claim_boundary_denies_execution_review_ci_merge_and_release(self) -> None:
        for phrase in (
            "never execution, runtime, test, code-review, CI, merge-readiness, merge, or release evidence",
            "A pass means the structure is complete and nothing more",
            "never certifies, ranks, or admits an external package",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, NATIVE_CAPABILITY_QUALITY_GATE_CLAIM_BOUNDARY)

    def test_a_softened_claim_boundary_is_refused(self) -> None:
        for boundary in ("", "This capability passed its quality gate."):
            with self.subTest(boundary=boundary):
                errors = validate_native_capability_quality_gate(edited(gate(), claim_boundary=boundary))
                self.assertTrue(any("claim_boundary" in error for error in errors), errors)

    def test_an_implementation_claim_key_is_refused_by_name(self) -> None:
        for key in sorted(IMPLEMENTATION_CLAIM_KEYS):
            with self.subTest(key=key):
                errors = validate_native_capability_quality_gate({**gate(), key: True})
                self.assertTrue(
                    any("implementation-claim keys" in error and key in error for error in errors), errors
                )

    def test_a_raw_or_hidden_key_is_refused(self) -> None:
        errors = validate_native_capability_quality_gate({**gate(), "transcript": "..."})

        self.assertTrue(any("raw or hidden keys" in error for error in errors), errors)

    def test_the_key_set_is_closed_in_both_directions(self) -> None:
        payload = gate()
        self.assertEqual(sorted(payload), sorted(NATIVE_CAPABILITY_QUALITY_GATE_KEYS))

        extra = validate_native_capability_quality_gate({**payload, "ci_run_url": "x"})
        self.assertTrue(any("unsupported keys" in error for error in extra), extra)

        for key in NATIVE_CAPABILITY_QUALITY_GATE_KEYS:
            with self.subTest(key=key):
                short = {name: value for name, value in payload.items() if name != key}
                errors = validate_native_capability_quality_gate(short)
                self.assertTrue(any("is missing keys" in error and key in error for error in errors), errors)

    def test_the_payload_is_metadata_only_and_schema_versioned(self) -> None:
        payload = gate()

        self.assertEqual(payload["schema_version"], NATIVE_CAPABILITY_QUALITY_GATE_SCHEMA_VERSION)
        self.assertEqual(NATIVE_CAPABILITY_QUALITY_GATE_SCHEMA_VERSION, "native_capability_quality_gate/v1")
        self.assertEqual(payload["privacy"], QUALITY_GATE_PRIVACY)
        self.assertEqual(QUALITY_GATE_PRIVACY, "metadata_only")
        errors = validate_native_capability_quality_gate(edited(payload, privacy="raw"))
        self.assertTrue(any("privacy" in error for error in errors), errors)

    def test_a_non_mapping_is_refused_rather_than_crashing(self) -> None:
        self.assertEqual(
            validate_native_capability_quality_gate(["not", "a", "gate"]),
            ["native_capability_quality_gate must be an object"],
        )

    def test_a_capability_id_that_is_not_the_canonical_slug_is_refused(self) -> None:
        for identifier in ("", "Native Capability Quality Gate", "x"):
            with self.subTest(identifier=identifier):
                errors = validate_native_capability_quality_gate(edited(gate(), capability_id=identifier))
                self.assertTrue(any("capability_id" in error for error in errors), errors)


class ForwardedPayloadTests(unittest.TestCase):
    """A payload the builder did not mint still has to hold together."""

    def test_an_expected_set_that_drops_a_required_surface_is_refused(self) -> None:
        surface = REQUIRED_NATIVE_CAPABILITY_SURFACES[0]
        payload = gate()
        narrowed = edited(
            payload, expected_surfaces=[item for item in payload["expected_surfaces"] if item != surface]
        )

        errors = validate_native_capability_quality_gate(narrowed)
        self.assertTrue(
            any("expected_surfaces is missing required surfaces" in error for error in errors), errors
        )
        self.assertTrue(any(blueprint_surface_anchor(surface) in error for error in errors), errors)

    def test_an_expected_set_out_of_declared_order_is_refused(self) -> None:
        payload = gate()

        errors = validate_native_capability_quality_gate(
            edited(payload, expected_surfaces=list(reversed(payload["expected_surfaces"])))
        )
        self.assertTrue(
            any("expected_surfaces must be listed in the declared surface order" in error for error in errors),
            errors,
        )

    def test_findings_out_of_declared_order_are_refused(self) -> None:
        payload = gate()

        errors = validate_native_capability_quality_gate(
            edited(payload, surfaces=list(reversed(payload["surfaces"])))
        )
        self.assertTrue(
            any("surfaces must be listed in the declared surface order" in error for error in errors), errors
        )

    def test_a_passing_catalog_validation_alongside_errors_is_refused(self) -> None:
        payload = gate()
        block = {
            **payload["generated_guidance"],
            "catalog_validation_ok": True,
            "catalog_validation_errors": ["harness planning evidence_ladder is missing gate steps"],
        }

        errors = validate_native_capability_quality_gate(edited(payload, generated_guidance=block))
        self.assertTrue(
            any("passing catalog validation alongside" in error for error in errors), errors
        )

    def test_a_malformed_generated_guidance_block_is_refused_rather_than_crashing(self) -> None:
        payload = gate()

        for block in ("not an object", {}, {**payload["generated_guidance"], "checked": "everything"}):
            with self.subTest(block=block):
                errors = validate_native_capability_quality_gate(edited(payload, generated_guidance=block))
                self.assertTrue(any("generated_guidance" in error for error in errors), errors)

    def test_a_malformed_surface_row_is_refused_rather_than_crashing(self) -> None:
        payload = gate()

        for rows in ("not a list", [{"surface": REQUIRED_NATIVE_CAPABILITY_SURFACES[0]}], [{}]):
            with self.subTest(rows=rows):
                errors = validate_native_capability_quality_gate(edited(payload, surfaces=rows))
                self.assertTrue(errors)


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

    def test_two_gates_over_one_set_of_findings_are_identical(self) -> None:
        self.assertEqual(gate(), gate())

    def test_prepared_at_is_a_parameter_and_defaults_to_empty(self) -> None:
        payload = build_native_capability_quality_gate(
            capability_id=CAPABILITY_ID, surfaces=findings(), repo_root=REPO_ROOT
        )

        self.assertEqual(payload["prepared_at"], "")
        self.assertEqual(validate_native_capability_quality_gate(payload), [])
        self.assertEqual(payload["surfaces"], gate()["surfaces"])


if __name__ == "__main__":
    unittest.main()
