from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.paths import resolve_paths
from omh.skills.catalog import builtin_definitions, installable_skill_names, omh_skill_display_name
from omh.workflow_learning import WorkflowLearningError
from omh.workflows.skill_draft import (
    SKILL_DRAFT_ACTIVE_STATE,
    SKILL_DRAFT_CATALOG_INDEX_LINE_BYTE_LIMIT,
    SKILL_DRAFT_CHECK_NAMES,
    SKILL_DRAFT_INACTIVE_STATE,
    build_skill_draft,
    check_skill_draft_generated_output,
    denied_payload_keys,
    list_skill_drafts,
    project_skill_draft_definition,
    review_skill_draft,
    show_skill_draft,
    skill_draft_is_active,
    skill_draft_path,
    validate_skill_draft,
    write_skill_draft,
)


EXPLICIT_TEACH_REQUEST = "turn this into a skill: our release smoke checklist, run after every tag"

# Real work happening around the user, none of it a request to learn anything.
PASSIVE_ACTIVITY = (
    "we deployed the release and it worked",
    "the smoke suite is green on main",
    "I rebased onto origin/main and force-pushed",
    "CI finished in four minutes",
    "reviewing the diff now",
    "배포 끝났고 스모크 테스트 통과했어요",
)

DRAFT_SECTIONS = {
    "proposed_skill_name": "release-smoke-check",
    "fixed_instructions": ["Read the release checklist.", "Run the smoke suite against the tag."],
    "declared_inputs": [
        {"name": "release_tag", "description": "The tag under test."},
        {"name": "target_host", "description": "Host the smoke suite runs against."},
    ],
    "preconditions": ["A tagged release candidate exists."],
    "stop_conditions": ["Stop when the smoke suite fails twice in a row."],
    "verification_steps": ["PYTHONPATH=tests uv run python -m unittest discover -s tests"],
}


def build_draft(message: str = EXPLICIT_TEACH_REQUEST, **overrides: object) -> dict[str, object] | None:
    kwargs: dict[str, object] = {"source_runs": ["run-alpha", "run-beta"], **DRAFT_SECTIONS}
    kwargs.update(overrides)
    return build_skill_draft(message, **kwargs)  # type: ignore[arg-type]


class SkillDraftExplicitRequestTests(unittest.TestCase):
    """AC1: passive activity is never recorded or converted without an explicit request."""

    def test_passive_activity_produces_no_draft(self) -> None:
        for message in PASSIVE_ACTIVITY:
            with self.subTest(message=message):
                self.assertIsNone(build_draft(message))

    def test_explicit_request_produces_a_draft(self) -> None:
        for message in (
            EXPLICIT_TEACH_REQUEST,
            "make a skill from this release smoke checklist",
            "save this as a skill",
            "방금 한 방식 스킬로 남겨",
        ):
            with self.subTest(message=message):
                draft = build_draft(message)
                self.assertIsNotNone(draft)
                assert draft is not None
                self.assertTrue(str(draft["provenance"]["learning_signal"]["matched"]).strip())  # type: ignore[index]

    def test_cli_refuses_passive_activity_and_writes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            status, stdout, stderr = run_cli(base + _new_command("we deployed the release and it worked"))

            self.assertEqual(status, 1, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["status"], "no_explicit_learning_signal")
            self.assertFalse(payload["recorded"])
            self.assertEqual(list_skill_drafts(resolve_paths(str(root / ".omh"), str(root / ".hermes"))), [])

    def test_draft_records_the_signal_that_authorized_it(self) -> None:
        draft = build_draft()
        assert draft is not None

        provenance = draft["provenance"]
        self.assertEqual(provenance["selection"], "explicit_user_selection")  # type: ignore[index]
        self.assertEqual(provenance["learning_signal"]["matched"], "turn this into a skill")  # type: ignore[index]

        stripped = copy.deepcopy(draft)
        stripped["provenance"]["learning_signal"] = {}  # type: ignore[index]
        self.assertIn(
            "provenance.learning_signal must record the explicit user request that started the draft",
            validate_skill_draft(stripped),
        )


class SkillDraftContentTests(unittest.TestCase):
    """AC2: the draft shows its source runs, redactions, variable inputs, and required verification."""

    def test_draft_names_runs_redactions_inputs_and_verification(self) -> None:
        draft = build_draft("turn this into a skill: smoke checklist we ran for PR #412 on branch release/1.2")
        assert draft is not None
        provenance = draft["provenance"]
        sections = draft["instruction_set"]

        self.assertEqual(
            [run["run_id"] for run in provenance["source_runs"]],  # type: ignore[index]
            ["run-alpha", "run-beta"],
        )
        redactions = provenance["redactions"]  # type: ignore[index]
        self.assertIn("pull_request_number", redactions["transient_identifier_categories"])
        self.assertIn("branch_ref", redactions["transient_identifier_categories"])
        self.assertEqual(redactions["denied_payload_keys"], denied_payload_keys())
        self.assertFalse(redactions["raw_prompt_stored"])
        self.assertFalse(redactions["raw_transcript_stored"])
        self.assertEqual(
            [item["name"] for item in sections["declared_inputs"]],  # type: ignore[index]
            ["release_tag", "target_host"],
        )
        self.assertEqual(
            sections["verification_steps"],  # type: ignore[index]
            ["PYTHONPATH=tests uv run python -m unittest discover -s tests"],
        )
        self.assertNotIn("412", json.dumps(draft))

    def test_fixed_instructions_stay_separate_from_declared_inputs(self) -> None:
        draft = build_draft()
        assert draft is not None
        sections = draft["instruction_set"]

        self.assertEqual(sorted(sections), sorted(  # type: ignore[arg-type]
            [
                "declared_inputs",
                "fixed_instructions",
                "preconditions",
                "stop_conditions",
                "verification_steps",
            ]
        ))
        self.assertTrue(all(isinstance(step, str) for step in sections["fixed_instructions"]))  # type: ignore[index]
        self.assertTrue(all(set(item) == {"name", "description"} for item in sections["declared_inputs"]))  # type: ignore[index]

    def test_payload_missing_any_required_element_fails_validation(self) -> None:
        draft = build_draft()
        assert draft is not None
        cases = {
            "source runs": (
                lambda record: record["provenance"].__setitem__("source_runs", []),
                "provenance.source_runs must name at least one user-selected source run",
            ),
            "redactions": (
                lambda record: record["provenance"].pop("redactions"),
                "provenance.redactions must record what was removed from the selected runs",
            ),
            "variable inputs": (
                lambda record: record["instruction_set"].__setitem__("declared_inputs", []),
                "instruction_set.declared_inputs must name at least one variable input",
            ),
            "required verification": (
                lambda record: record["instruction_set"].__setitem__("verification_steps", []),
                "instruction_set.verification_steps must be a non-empty list",
            ),
            "fixed instructions": (
                lambda record: record["instruction_set"].__setitem__("fixed_instructions", []),
                "instruction_set.fixed_instructions must be a non-empty list",
            ),
            "preconditions": (
                lambda record: record["instruction_set"].__setitem__("preconditions", []),
                "instruction_set.preconditions must be a non-empty list",
            ),
            "stop conditions": (
                lambda record: record["instruction_set"].__setitem__("stop_conditions", []),
                "instruction_set.stop_conditions must be a non-empty list",
            ),
        }
        for label, (mutate, expected_error) in cases.items():
            with self.subTest(missing=label):
                record = copy.deepcopy(draft)
                mutate(record)
                self.assertIn(expected_error, validate_skill_draft(record))

    def test_builder_refuses_to_emit_an_incomplete_draft(self) -> None:
        with self.assertRaises(WorkflowLearningError):
            build_draft(verification_steps=[])
        with self.assertRaises(WorkflowLearningError):
            build_draft(source_runs=[])

    def test_draft_rejects_raw_content_keys_from_the_shared_denylist(self) -> None:
        draft = build_draft()
        assert draft is not None
        record = copy.deepcopy(draft)
        record["provenance"]["transcript"] = "everything the user typed"  # type: ignore[index]

        self.assertIn(
            "provenance.transcript is a denied raw-content key for metadata-only records",
            validate_skill_draft(record),
        )

    def test_draft_is_deterministic_and_carries_no_wall_clock(self) -> None:
        first = build_draft()
        second = build_draft()

        self.assertEqual(first, second)
        assert first is not None
        self.assertEqual(first["created_at"], "")
        stamped = build_draft(created_at="2026-01-02T03:04:05Z")
        assert stamped is not None
        self.assertEqual(stamped["draft_id"], first["draft_id"])


class SkillDraftActivationTests(unittest.TestCase):
    """AC3: activation requires successful generated-output checks and explicit review."""

    def test_generated_output_checks_run_every_named_check(self) -> None:
        draft = build_draft()
        assert draft is not None

        checks = check_skill_draft_generated_output(draft)

        self.assertTrue(checks["ok"], checks["errors"])
        self.assertEqual([check["check"] for check in checks["checks"]], list(SKILL_DRAFT_CHECK_NAMES))

    def test_draft_failing_generated_output_checks_cannot_activate(self) -> None:
        # A summary this long renders a catalog-index line past the byte ceiling
        # that surface enforces, so the draft would not render valid generated
        # output. The draft is still reviewable; it just cannot be activated.
        oversized = "turn this into a skill: " + ("release smoke verification across every supported host " * 8)
        draft = build_draft(oversized)
        assert draft is not None

        checks = check_skill_draft_generated_output(draft)
        self.assertFalse(checks["ok"])
        self.assertIn("rendered_catalog_index_line", " ".join(checks["errors"]))
        index_line = f"- `{omh_skill_display_name('release-smoke-check')}`: {project_skill_draft_definition(draft).description}"
        self.assertGreaterEqual(len(index_line.encode("utf-8")), SKILL_DRAFT_CATALOG_INDEX_LINE_BYTE_LIMIT)

        with self.assertRaisesRegex(WorkflowLearningError, "rendered_catalog_index_line"):
            review_skill_draft(draft, decision="approve", reviewer_ref="maintainer", reviewed_at="2026-01-01T00:00:00Z")

    def test_review_alone_without_passing_checks_cannot_activate(self) -> None:
        draft = build_draft()
        assert draft is not None
        # A draft whose stored payload lost its verification steps would not
        # render a valid skill; an explicit approval must not rescue it.
        tampered = copy.deepcopy(draft)
        tampered["instruction_set"]["verification_steps"] = []  # type: ignore[index]

        self.assertFalse(check_skill_draft_generated_output(tampered)["ok"])
        with self.assertRaises(WorkflowLearningError):
            review_skill_draft(tampered, decision="approve", reviewer_ref="maintainer", reviewed_at="t")

    def test_passing_checks_without_review_leave_the_draft_inactive(self) -> None:
        draft = build_draft()
        assert draft is not None

        self.assertTrue(check_skill_draft_generated_output(draft)["ok"])
        self.assertEqual(draft["lifecycle"]["state"], SKILL_DRAFT_INACTIVE_STATE)  # type: ignore[index]
        self.assertFalse(skill_draft_is_active(draft))
        self.assertNotIn("activation", draft)
        self.assertNotIn("proposal", draft)

    def test_passing_checks_plus_explicit_approval_activate_the_draft(self) -> None:
        draft = build_draft()
        assert draft is not None
        note = "checked the verification step by hand"

        activated = review_skill_draft(
            draft,
            decision="approve",
            reviewer_ref="maintainer",
            reviewed_at="2026-01-01T00:00:00Z",
            review_note=note,
        )

        self.assertTrue(skill_draft_is_active(activated))
        self.assertEqual(activated["lifecycle"]["state"], SKILL_DRAFT_ACTIVE_STATE)
        self.assertTrue(activated["activation"]["generated_output_check"]["ok"])
        self.assertEqual(
            activated["activation"]["requirements_met"],
            ["generated_output_checks_pass", "explicit_human_review_approval"],
        )
        self.assertEqual(activated["provenance"]["review"]["reviewer_ref"], "maintainer")
        self.assertEqual(activated["provenance"]["review"]["decision"], "approve")
        self.assertEqual(activated["provenance"]["review"]["review_note_length"], len(note))
        self.assertNotIn(note, json.dumps(activated))
        self.assertIn("release_tag: The tag under test.", activated["proposal"]["copy_text"])
        self.assertIn("run-alpha", activated["proposal"]["copy_text"])

    def test_revise_and_reject_never_activate(self) -> None:
        draft = build_draft()
        assert draft is not None
        for decision in ("revise", "reject"):
            with self.subTest(decision=decision):
                reviewed = review_skill_draft(draft, decision=decision, reviewer_ref="maintainer", reviewed_at="t")

                self.assertFalse(skill_draft_is_active(reviewed))
                self.assertEqual(reviewed["lifecycle"]["state"], SKILL_DRAFT_INACTIVE_STATE)
                self.assertNotIn("proposal", reviewed)

    def test_a_later_reject_deactivates_an_activated_draft(self) -> None:
        draft = build_draft()
        assert draft is not None
        activated = review_skill_draft(draft, decision="approve", reviewer_ref="maintainer", reviewed_at="t")

        reverted = review_skill_draft(activated, decision="reject", reviewer_ref="maintainer", reviewed_at="t2")

        self.assertFalse(skill_draft_is_active(reverted))
        self.assertNotIn("activation", reverted)
        self.assertNotIn("proposal", reverted)

    def test_review_needs_an_explicit_reviewer_and_a_known_decision(self) -> None:
        draft = build_draft()
        assert draft is not None
        with self.assertRaises(WorkflowLearningError):
            review_skill_draft(draft, decision="approve", reviewer_ref="  ", reviewed_at="t")
        with self.assertRaises(WorkflowLearningError):
            review_skill_draft(draft, decision="install", reviewer_ref="maintainer", reviewed_at="t")

    def test_an_activation_receipt_cannot_be_forged_onto_an_inactive_draft(self) -> None:
        draft = build_draft()
        assert draft is not None
        forged = copy.deepcopy(draft)
        forged["activation"] = {"activated": True, "requirements_met": [], "generated_output_check": {"ok": True}}

        self.assertIn("an inactive skill draft must not carry an activation receipt", validate_skill_draft(forged))


class SkillDraftInactiveByConstructionTests(unittest.TestCase):
    """Guards: a draft is never written under skills/ and never reads as an installed capability."""

    def test_draft_storage_never_lands_under_skills(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(str(root / ".omh"), str(root / ".hermes"))
            draft = build_draft()
            assert draft is not None

            written = write_skill_draft(paths, draft)
            path = skill_draft_path(paths, str(written["draft_id"]))

            self.assertTrue(path.is_file())
            self.assertNotIn("skills", [part.casefold() for part in path.parts])
            self.assertEqual(path.parent.name, "skill-drafts")
            self.assertEqual(path.parent.parent.name, "learning")
            # Both separators are spelled out so the assertion means the same
            # thing on POSIX and Windows.
            self.assertNotIn("skills/", str(path))
            self.assertNotIn("skills\\", str(path))
            self.assertEqual(written["lifecycle"]["generated_skill_path"], "")
            self.assertEqual(list(paths.omh_home.glob("**/skills")), [])
            self.assertEqual(sorted(item.name for item in paths.learning_dir.iterdir()), ["skill-drafts"])

    def test_write_refuses_a_path_inside_a_skills_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(str(root / ".omh" / "skills"), str(root / ".hermes"))
            draft = build_draft()
            assert draft is not None

            # The guard is relative to the OMH home, so a home that is itself
            # named `skills` is allowed; a draft nested under a `skills`
            # directory inside that home is not.
            write_skill_draft(paths, draft)
            hostile = resolve_paths(str(root / ".omh2"), str(root / ".hermes"))
            with self.assertRaisesRegex(WorkflowLearningError, "never be written under a skills/ directory"):
                from omh.workflows.skill_draft import _reject_installed_skill_location

                _reject_installed_skill_location(hostile, hostile.omh_home / "skills" / "release-smoke-check.json")

    def test_draft_never_reads_as_an_installed_capability(self) -> None:
        draft = build_draft()
        assert draft is not None
        activated = review_skill_draft(draft, decision="approve", reviewer_ref="maintainer", reviewed_at="t")
        catalog_names = {definition.name for definition in builtin_definitions()}

        for record in (draft, activated):
            with self.subTest(state=record["lifecycle"]["state"]):
                self.assertNotIn("name", record)
                self.assertEqual(record["record_type"], "skill_draft")
                self.assertEqual(record["status"], "prepared_not_observed")
                self.assertFalse(record["lifecycle"]["installed"])
                self.assertFalse(record["lifecycle"]["catalog_registered"])
                self.assertEqual(record["lifecycle"]["generated_skill_path"], "")
                self.assertNotIn(record["proposed_skill_name"], catalog_names)
                self.assertNotIn(record["proposed_skill_name"], installable_skill_names())
                self.assertIn("skills/", record["claim_boundary"])
                self.assertIn("prepared_not_observed", record["claim_boundary"])

    def test_a_draft_cannot_claim_an_installed_skill_name(self) -> None:
        with self.assertRaises(WorkflowLearningError):
            build_draft(proposed_skill_name="memory-new")
        with self.assertRaises(WorkflowLearningError):
            build_draft(proposed_skill_name="omh-memory-new")

    def test_a_draft_cannot_declare_itself_installed(self) -> None:
        draft = build_draft()
        assert draft is not None
        for field, value in (("installed", True), ("catalog_registered", True)):
            with self.subTest(field=field):
                record = copy.deepcopy(draft)
                record["lifecycle"][field] = value  # type: ignore[index]
                self.assertIn(
                    f"lifecycle.{field} must be false; a draft is never an installed skill",
                    validate_skill_draft(record),
                )
        rendered = copy.deepcopy(draft)
        rendered["lifecycle"]["generated_skill_path"] = "skills/omh-release-smoke-check/SKILL.md"  # type: ignore[index]
        self.assertIn(
            "lifecycle.generated_skill_path must stay empty; a draft never renders under skills/",
            validate_skill_draft(rendered),
        )

    def test_building_a_draft_does_not_grow_the_catalog(self) -> None:
        before = len(builtin_definitions())

        draft = build_draft()
        assert draft is not None
        review_skill_draft(draft, decision="approve", reviewer_ref="maintainer", reviewed_at="t")

        self.assertEqual(len(builtin_definitions()), before)
        self.assertNotIn("release-smoke-check", {definition.name for definition in builtin_definitions()})


class SkillDraftCliTests(unittest.TestCase):
    def test_cli_drafts_reviews_and_activates_one_workflow(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            paths = resolve_paths(str(root / ".omh"), str(root / ".hermes"))

            status, stdout, stderr = run_cli(base + _new_command(EXPLICIT_TEACH_REQUEST))
            self.assertEqual(status, 0, stderr)
            created = json.loads(stdout)
            draft_id = created["draft"]["draft_id"]
            self.assertEqual(created["status"], "drafted")
            self.assertTrue(created["recorded"])
            self.assertTrue(created["generated_output_check"]["ok"])
            self.assertEqual(created["draft"]["lifecycle"]["state"], SKILL_DRAFT_INACTIVE_STATE)
            self.assertEqual(created["skill_draft_ref"], f"omh-skill-draft:{draft_id}")

            status, stdout, stderr = run_cli(base + ["learning", "skill-draft", "show", draft_id])
            self.assertEqual(status, 0, stderr)
            shown = json.loads(stdout)
            self.assertFalse(shown["active"])
            self.assertEqual(shown["activation_blockers"], [])

            status, stdout, stderr = run_cli(base + ["learning", "skill-draft", "list"])
            self.assertEqual(status, 0, stderr)
            listed = json.loads(stdout)["drafts"]
            self.assertEqual([item["draft_id"] for item in listed], [draft_id])
            self.assertEqual(listed[0]["review_decision"], "pending")

            status, stdout, stderr = run_cli(
                base + ["learning", "skill-draft", "review", draft_id, "--decision", "approve", "--reviewer-ref", "maintainer"]
            )
            self.assertEqual(status, 0, stderr)
            reviewed = json.loads(stdout)
            self.assertTrue(reviewed["activated"])
            self.assertEqual(reviewed["draft"]["lifecycle"]["state"], SKILL_DRAFT_ACTIVE_STATE)
            self.assertFalse(reviewed["draft"]["lifecycle"]["installed"])

            stored = show_skill_draft(paths, draft_id)
            self.assertTrue(skill_draft_is_active(stored))
            self.assertEqual(stored["provenance"]["review"]["reviewer_ref"], "maintainer")

    def test_cli_dry_run_records_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            status, stdout, stderr = run_cli(base + _new_command(EXPLICIT_TEACH_REQUEST) + ["--dry-run"])

            self.assertEqual(status, 0, stderr)
            self.assertFalse(json.loads(stdout)["recorded"])
            self.assertEqual(list_skill_drafts(resolve_paths(str(root / ".omh"), str(root / ".hermes"))), [])

    def test_cli_rejects_a_malformed_declared_input(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            command = _new_command(EXPLICIT_TEACH_REQUEST)
            command[command.index("--input") + 1] = "release_tag"

            status, _, stderr = run_cli(base + command)

            self.assertNotEqual(status, 0)
            self.assertIn("--input must be given as name=description", stderr)


def _new_command(message: str) -> list[str]:
    command = [
        "learning",
        "skill-draft",
        "new",
        message,
        "--name",
        str(DRAFT_SECTIONS["proposed_skill_name"]),
        "--source-run",
        "run-alpha",
        "--source-run",
        "run-beta",
    ]
    for instruction in DRAFT_SECTIONS["fixed_instructions"]:  # type: ignore[union-attr]
        command += ["--instruction", instruction]
    for item in DRAFT_SECTIONS["declared_inputs"]:  # type: ignore[union-attr]
        command += ["--input", f"{item['name']}={item['description']}"]
    for precondition in DRAFT_SECTIONS["preconditions"]:  # type: ignore[union-attr]
        command += ["--precondition", precondition]
    for stop_condition in DRAFT_SECTIONS["stop_conditions"]:  # type: ignore[union-attr]
        command += ["--stop-condition", stop_condition]
    for verification in DRAFT_SECTIONS["verification_steps"]:  # type: ignore[union-attr]
        command += ["--verification", verification]
    return command


if __name__ == "__main__":
    unittest.main()
