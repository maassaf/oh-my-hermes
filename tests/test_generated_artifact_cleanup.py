"""Contracts for `generated_artifact/v1` and its cleanup preview (issue #835).

Three acceptance criteria and one structural guard:

    AC1  the current and the superseded revision are identifiable, per kind
    AC2  every listed artifact says in words why it got the verdict it got
    AC3  a current, a referenced, and an in-retention artifact are each absent
         from the eligible set -- three separate tests, because they are three
         separate ways to lose an artifact somebody still needs
    GUARD  the module has no deletion path at all

The failure this whole family exists to prevent is a cleanup list that names the
artifact a live handoff points at. Every AC3 test is written so that removing
the guard it covers makes it fail while the other two stay green.
"""

from __future__ import annotations

import ast
import copy
import importlib
import inspect
import json
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.commands import runtime as runtime_command  # noqa: E402
from omh.commands.main import build_parser  # noqa: E402
from omh.local_store import atomic_write_text  # noqa: E402
from omh.paths import project_artifact_dir, resolve_paths  # noqa: E402
from omh.runtime import generated_artifacts as generated_artifacts_module  # noqa: E402
from omh.runtime.generated_artifacts import (  # noqa: E402
    CLEANUP_PREVIEW_CLAIM_BOUNDARY,
    DEFAULT_RETENTION_DAYS,
    GENERATED_ARTIFACT_CLEANUP_PREVIEW_SCHEMA_VERSION,
    GENERATED_ARTIFACT_KINDS,
    GENERATED_ARTIFACT_PRODUCERS,
    GENERATED_ARTIFACT_SCHEMA_VERSION,
    build_generated_artifact_cleanup_preview,
    project_generated_artifacts,
    render_generated_artifact_cleanup_preview_text,
    validate_generated_artifact,
    validate_generated_artifact_cleanup_preview,
)
from omh.workflows.hermes_planning import (  # noqa: E402
    build_hermes_plan_payload,
    read_hermes_plan_artifact,
    write_hermes_plan,
    write_plan_handoff_context_pack,
)
from omh.workflows.operations import build_operation_artifact, write_operation_artifact  # noqa: E402
from omh.workflows.plan_variants import build_plan_variant, write_plan_variant  # noqa: E402
from omh.workflows.skill_draft import build_skill_draft, write_skill_draft  # noqa: E402


PLAN_TASK = "implement the coding delegation flow with tests"
VARIANT_PARENT_TASK = "refactor the runtime observation journal and add tests"

# Creation stamps, four days apart, both far enough in the past that a 30-day
# window can be shown either open or closed by moving `now` alone.
OLD_STAMP = "2026-01-01T000000000000Z"
NEW_STAMP = "2026-01-05T000000000000Z"
OLD_ISO = "2026-01-01T00:00:00Z"
NEW_ISO = "2026-01-05T00:00:00Z"

# Inside the older artifact's 30-day window (which closes 2026-01-31).
NOW_INSIDE_RETENTION = datetime(2026, 1, 10, tzinfo=timezone.utc)
# Well past it.
NOW_AFTER_RETENTION = datetime(2026, 3, 1, tzinfo=timezone.utc)

TEACH_REQUEST = "turn this into a skill: our release smoke checklist, run after every tag"

OWNER_DELTA = {
    "dimension": "coding_owner",
    "label": "selected executor",
    "parent_value": "codex",
    "variant_value": "claude-code",
}
SCOPE_DELTA = {
    "dimension": "scope",
    "label": "docs",
    "parent_value": "ship docs in the same PR",
    "variant_value": "defer docs to a follow-up",
}

# The calls a dry-run preview must never contain. Matched on the callee name
# through the AST rather than by grepping the text, so the module's own prose
# about not deleting anything cannot trip the gate and a call hidden behind an
# alias cannot slip past it.
FORBIDDEN_CALLS = frozenset(
    {
        "unlink",
        "remove",
        "removedirs",
        "rmdir",
        "rmtree",
        "write_text",
        "write_bytes",
        "touch",
        "mkdir",
        "atomic_write_text",
        "atomic_write_json",
        "ensure_dir",
        "ensure_file",
        "chmod",
    }
)
FORBIDDEN_IMPORTS = frozenset({"os", "shutil"})


def _paths(tmp: str):
    return resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")


def _artifacts_parser():
    """The real `omh runtime artifacts` subparser, as the CLI builds it."""
    runtime = build_parser()._subparsers._group_actions[0].choices["runtime"]  # type: ignore[union-attr]
    return runtime._subparsers._group_actions[0].choices["artifacts"]  # type: ignore[union-attr]


def _plan_text(task: str, status: str) -> str:
    return "\n".join(
        [
            "---",
            "schema_version: hermes_plan/v1",
            f"status: {status}",
            "source: generic",
            "---",
            "",
            f"# Hermes Plan: {task}",
            "",
            "## Task Statement",
            "",
            task,
            "",
        ]
    )


def _write_plan(paths, *, stamp: str, token: str, task: str = PLAN_TASK, status: str = "draft") -> Path:
    """One plan artifact with a caller-chosen creation stamp.

    Written through `atomic_write_text` rather than `Path.write_text`: the bytes
    are hashed by the projection, and a host that translated the newlines would
    produce a different digest for the same fixture.
    """
    path = project_artifact_dir(paths, "plans") / f"{stamp}-delegation-flow-{token}.md"
    atomic_write_text(path, _plan_text(task, status), private=True)
    return path


def _write_operation(paths, created_at: str) -> dict[str, Any]:
    return write_operation_artifact(
        paths,
        build_operation_artifact(
            surface="report-package",
            kind="weekly-report",
            title="Delegation weekly status",
            summary="Weekly status for the delegation lane.",
            source="local run ledger",
            created_at=created_at,
        ),
    )


def _write_skill_draft(paths, *, created_at: str, instruction: str) -> dict[str, Any]:
    draft = build_skill_draft(
        TEACH_REQUEST,
        source_runs=["run-alpha", "run-beta"],
        proposed_skill_name="release-smoke-check",
        fixed_instructions=["Read the release checklist.", instruction],
        declared_inputs=[
            {"name": "release_tag", "description": "The tag under test."},
            {"name": "target_host", "description": "Host the smoke suite runs against."},
        ],
        preconditions=["A tagged release candidate exists."],
        stop_conditions=["Stop when the smoke suite fails twice in a row."],
        verification_steps=["PYTHONPATH=tests uv run python -m unittest discover -s tests"],
        created_at=created_at,
    )
    assert draft is not None
    return write_skill_draft(paths, draft)


def _write_plan_variants(paths) -> tuple[str, str]:
    parent_path = _write_plan(
        paths, stamp="2025-12-01T000000000000Z", token="parent", task=VARIANT_PARENT_TASK, status="accepted"
    )
    parent = read_hermes_plan_artifact(parent_path)
    older = write_plan_variant(
        paths,
        build_plan_variant(parent_artifact=parent, name="claude-code owner", deltas=[OWNER_DELTA], created_at=OLD_ISO),
    )
    newer = write_plan_variant(
        paths,
        build_plan_variant(
            parent_artifact=parent,
            name="claude-code owner",
            deltas=[OWNER_DELTA, SCOPE_DELTA],
            created_at=NEW_ISO,
        ),
    )
    return str(older["variant_id"]), str(newer["variant_id"])


def _two_revisions_of_every_kind(paths) -> dict[str, tuple[str, str]]:
    """One store holding an older and a newer revision of each supported kind."""
    old_plan = _write_plan(paths, stamp=OLD_STAMP, token="aaa111")
    new_plan = _write_plan(paths, stamp=NEW_STAMP, token="bbb222")
    old_operation = _write_operation(paths, OLD_ISO)
    new_operation = _write_operation(paths, NEW_ISO)
    old_draft = _write_skill_draft(paths, created_at=OLD_ISO, instruction="Run the smoke suite against the tag.")
    new_draft = _write_skill_draft(
        paths, created_at=NEW_ISO, instruction="Run the smoke suite against the tag, then diff the report."
    )
    old_variant, new_variant = _write_plan_variants(paths)
    return {
        "hermes_plan": (old_plan.stem, new_plan.stem),
        "operation_artifact": (str(old_operation["artifact_id"]), str(new_operation["artifact_id"])),
        "plan_variant": (old_variant, new_variant),
        "skill_draft": (str(old_draft["draft_id"]), str(new_draft["draft_id"])),
    }


def _by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(record["artifact_id"]): record for record in records}


class CurrentAndSupersededRevisionTests(unittest.TestCase):
    """AC1: for every supported kind, which revision is current is answerable."""

    def test_every_supported_kind_names_its_current_and_superseded_revision(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            expected = _two_revisions_of_every_kind(paths)
            records = _by_id(project_generated_artifacts(paths, now=NOW_AFTER_RETENTION))

            self.assertEqual(sorted(expected), sorted(GENERATED_ARTIFACT_KINDS))
            for kind, (older_id, newer_id) in expected.items():
                with self.subTest(artifact_kind=kind):
                    older = records[older_id]
                    newer = records[newer_id]
                    self.assertEqual(older["artifact_kind"], kind)
                    self.assertEqual(newer["artifact_kind"], kind)
                    # Two revisions of one thing, not two unrelated artifacts.
                    self.assertEqual(older["revision_line"], newer["revision_line"])
                    self.assertEqual((older["revision_index"], newer["revision_index"]), (1, 2))
                    self.assertEqual(older["revision_count"], 2)
                    self.assertEqual(older["lifecycle"], "superseded")
                    self.assertEqual(newer["lifecycle"], "current")
                    # The replacement relationship is named in both directions.
                    self.assertEqual(older["replaced_by"], newer_id)
                    self.assertEqual(newer["replaces"], older_id)
                    self.assertEqual(newer["replaced_by"], "")

    def test_every_projected_record_carries_provenance_a_reader_can_follow(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _two_revisions_of_every_kind(paths)
            for record in project_generated_artifacts(paths, now=NOW_AFTER_RETENTION):
                with self.subTest(artifact_id=record["artifact_id"]):
                    self.assertEqual(record["schema_version"], GENERATED_ARTIFACT_SCHEMA_VERSION)
                    self.assertEqual(validate_generated_artifact(record), [])
                    self.assertTrue(Path(str(record["path"])).is_file())
                    self.assertEqual(len(str(record["content_digest"])), 64)
                    self.assertEqual(record["producer"], GENERATED_ARTIFACT_PRODUCERS[record["artifact_kind"]])
                    self.assertTrue(str(record["created_at"]).endswith("Z"))
                    self.assertTrue(str(record["retention_reason"]).strip())

    def test_a_plan_the_producer_already_marked_superseded_is_never_current(self) -> None:
        """The producer's own word wins even when nothing newer sits beside it."""
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _write_plan(paths, stamp=OLD_STAMP, token="aaa111", status="superseded")

            record = project_generated_artifacts(paths, now=NOW_AFTER_RETENTION)[0]

            self.assertEqual(record["declared_status"], "superseded")
            self.assertEqual(record["lifecycle"], "superseded")

    def test_a_line_with_no_distinct_creation_times_keeps_every_member_current(self) -> None:
        """Fail closed: unorderable revisions are never called superseded."""
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _write_plan(paths, stamp=OLD_STAMP, token="aaa111")
            _write_plan(paths, stamp=OLD_STAMP, token="bbb222")

            records = project_generated_artifacts(paths, now=NOW_AFTER_RETENTION)

            self.assertEqual(len(records), 2)
            for record in records:
                with self.subTest(artifact_id=record["artifact_id"]):
                    self.assertEqual(record["lifecycle"], "current")
                    self.assertFalse(record["cleanup_eligible"])
                    self.assertIn("do not carry", record["cleanup_reason"])

    def test_the_plan_writer_still_produces_a_filename_this_projection_can_date(self) -> None:
        """The one coupling this module has to a producer, pinned.

        Plan creation time lives only in the filename `write_hermes_plan`
        chooses. If that format moves, every plan silently loses its creation
        time and no plan can ever be shown out of retention -- safe, but the
        feature would quietly stop working. This fails instead.
        """
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            write_hermes_plan(paths, build_hermes_plan_payload(PLAN_TASK))

            record = project_generated_artifacts(paths, now=NOW_AFTER_RETENTION)[0]

            self.assertTrue(record["created_at"], "the plan filename no longer carries a readable stamp")
            self.assertTrue(record["retention_expires_at"])


class CleanupPreviewExplainsEveryEntryTests(unittest.TestCase):
    """AC2: nothing is listed without a reason a person can read."""

    def test_every_previewed_artifact_explains_its_verdict_in_words(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _two_revisions_of_every_kind(paths)

            preview = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)

            self.assertEqual(validate_generated_artifact_cleanup_preview(preview), [])
            self.assertEqual(preview["schema_version"], GENERATED_ARTIFACT_CLEANUP_PREVIEW_SCHEMA_VERSION)
            self.assertTrue(preview["eligible"], "the fixture is supposed to produce an eligible artifact")
            self.assertTrue(preview["retained"])
            for group in ("eligible", "retained"):
                for record in preview[group]:
                    with self.subTest(group=group, artifact_id=record["artifact_id"]):
                        reason = str(record["cleanup_reason"])
                        self.assertTrue(reason.strip())
                        self.assertTrue(reason.startswith("Eligible: " if group == "eligible" else "Kept: "))
            for record in preview["eligible"]:
                with self.subTest(artifact_id=record["artifact_id"]):
                    # The eligible sentence names all three conditions that had
                    # to hold, so the reader can check the projection's work.
                    reason = str(record["cleanup_reason"])
                    self.assertIn("superseded by", reason)
                    self.assertIn("no local artifact references it", reason)
                    self.assertIn("retention window ended", reason)

    def test_an_entry_with_no_eligibility_reason_fails_validation(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _two_revisions_of_every_kind(paths)
            preview = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)

            damaged = copy.deepcopy(preview)
            damaged["eligible"][0]["cleanup_reason"] = ""

            self.assertEqual(validate_generated_artifact(preview["eligible"][0]), [])
            self.assertIn(
                "cleanup_reason must explain the verdict in words",
                validate_generated_artifact(damaged["eligible"][0]),
            )
            errors = validate_generated_artifact_cleanup_preview(damaged)
            self.assertTrue(
                any("cleanup_reason must explain the verdict in words" in error for error in errors), errors
            )

    def test_the_preview_says_which_kinds_it_cannot_answer_for(self) -> None:
        """A kind left out is a stated boundary, never a silent omission."""
        with TemporaryDirectory() as tmp:
            preview = build_generated_artifact_cleanup_preview(_paths(tmp), now=NOW_AFTER_RETENTION)

            unsupported = {str(row["artifact_kind"]): str(row["reason"]) for row in preview["unsupported_kinds"]}
            self.assertEqual(sorted(unsupported), ["handoff_context_pack", "role_context_pack"])
            for kind, reason in unsupported.items():
                with self.subTest(artifact_kind=kind):
                    self.assertTrue(reason.strip())
                    self.assertNotIn(kind, GENERATED_ARTIFACT_KINDS)


class CurrentArtifactIsNeverEligibleTests(unittest.TestCase):
    """AC3, first way to lose something: listing the revision still in use."""

    def test_the_current_revision_is_absent_from_the_eligible_set(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            old_plan = _write_plan(paths, stamp=OLD_STAMP, token="aaa111")
            new_plan = _write_plan(paths, stamp=NEW_STAMP, token="bbb222")

            preview = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)
            eligible = {str(record["artifact_id"]) for record in preview["eligible"]}
            retained = _by_id(preview["retained"])

            self.assertNotIn(new_plan.stem, eligible)
            self.assertIn(old_plan.stem, eligible)
            self.assertEqual(retained[new_plan.stem]["lifecycle"], "current")
            self.assertIn("this is the current revision", retained[new_plan.stem]["cleanup_reason"])

    def test_a_sole_artifact_with_nothing_to_replace_it_is_never_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            only = _write_plan(paths, stamp=OLD_STAMP, token="aaa111")

            preview = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)

            self.assertEqual(preview["eligible"], [])
            self.assertEqual([record["artifact_id"] for record in preview["retained"]], [only.stem])


class ReferencedArtifactIsNeverEligibleTests(unittest.TestCase):
    """AC3, second way: listing an artifact a live handoff still points at."""

    def test_a_superseded_plan_a_handoff_pack_pins_is_absent_from_the_eligible_set(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            old_plan = _write_plan(paths, stamp=OLD_STAMP, token="aaa111")
            _write_plan(paths, stamp=NEW_STAMP, token="bbb222")

            # Without a pin the old plan is eligible; this is the control that
            # makes the assertion below about the pin and not about the fixture.
            before = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)
            self.assertIn(old_plan.stem, {str(record["artifact_id"]) for record in before["eligible"]})

            write_plan_handoff_context_pack(paths, read_hermes_plan_artifact(old_plan))

            after = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)
            eligible = {str(record["artifact_id"]) for record in after["eligible"]}
            retained = _by_id(after["retained"])

            self.assertNotIn(old_plan.stem, eligible)
            pinned = retained[old_plan.stem]
            self.assertEqual(pinned["lifecycle"], "superseded")
            self.assertEqual(pinned["reference_count"], len(pinned["referenced_by"]))
            self.assertGreaterEqual(pinned["reference_count"], 1)
            self.assertEqual(
                {str(reference["ref_kind"]) for reference in pinned["referenced_by"]},
                {"handoff_context_pack"},
            )
            self.assertIn("still point at it", pinned["cleanup_reason"])
            self.assertIn("handoff_context_pack", pinned["cleanup_reason"])

    def test_a_variant_pinning_its_parent_plan_keeps_that_plan_off_the_eligible_set(self) -> None:
        """A second, independent reference family, matched on a different field."""
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            parent_path = _write_plan(
                paths, stamp=OLD_STAMP, token="parent", task=VARIANT_PARENT_TASK, status="accepted"
            )
            # A different status, so the two revisions are not byte-identical and
            # the parent's digest is unambiguous -- otherwise the digest half of
            # the assertion below would be dropped for the reason the next test
            # covers, and this test would silently stop exercising it.
            _write_plan(paths, stamp=NEW_STAMP, token="newer", task=VARIANT_PARENT_TASK, status="revised")
            write_plan_variant(
                paths,
                build_plan_variant(
                    parent_artifact=read_hermes_plan_artifact(parent_path),
                    name="claude-code owner",
                    deltas=[OWNER_DELTA],
                    created_at=OLD_ISO,
                ),
            )

            preview = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)
            retained = _by_id(preview["retained"])

            self.assertNotIn(parent_path.stem, {str(record["artifact_id"]) for record in preview["eligible"]})
            pinned = retained[parent_path.stem]
            self.assertEqual(
                {str(reference["ref_kind"]) for reference in pinned["referenced_by"]}, {"plan_variant"}
            )
            # Both the path and the digest the variant recorded are matched, so
            # a rename on either side alone cannot silently drop the reference.
            self.assertEqual(
                {str(reference["matched_on"]) for reference in pinned["referenced_by"]},
                {"path", "content_digest"},
            )

    def test_a_digest_shared_by_several_revisions_is_not_a_reference_key(self) -> None:
        """Re-planning one task writes byte-identical files; a shared digest names none of them.

        The pin still holds the file it actually meant, by path. Without this
        rule the pack's digest would be credited to all three revisions, every
        duplicate would be kept forever, and each one would print a reason
        pointing at a sibling's pin.
        """
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            oldest = _write_plan(paths, stamp=OLD_STAMP, token="aaa111")
            middle = _write_plan(paths, stamp=NEW_STAMP, token="bbb222")
            newest = _write_plan(paths, stamp="2026-02-01T000000000000Z", token="ccc333")
            digests = {read_hermes_plan_artifact(path)["sha256"] for path in (oldest, middle, newest)}
            self.assertEqual(len(digests), 1, "the fixture must produce byte-identical revisions")

            write_plan_handoff_context_pack(paths, read_hermes_plan_artifact(middle))

            preview = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)
            eligible = _by_id(preview["eligible"])
            retained = _by_id(preview["retained"])

            # The pinned revision is held by its path match, and only it.
            self.assertEqual(
                {str(reference["matched_on"]) for reference in retained[middle.stem]["referenced_by"]}, {"path"}
            )
            self.assertIn(oldest.stem, eligible)
            self.assertEqual(eligible[oldest.stem]["referenced_by"], [])
            self.assertEqual(retained[newest.stem]["lifecycle"], "current")

    def test_an_artifact_that_only_names_itself_is_not_treated_as_referenced(self) -> None:
        """Self-reference is not reference; otherwise nothing is ever eligible."""
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            older = _write_operation(paths, OLD_ISO)
            _write_operation(paths, NEW_ISO)

            preview = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)
            eligible = _by_id(preview["eligible"])

            self.assertIn(str(older["artifact_id"]), eligible)
            self.assertEqual(eligible[str(older["artifact_id"])]["referenced_by"], [])


class RetainedArtifactIsNeverEligibleTests(unittest.TestCase):
    """AC3, third way: listing an artifact whose retention window is still open."""

    def test_a_superseded_artifact_inside_its_retention_window_is_absent_from_the_eligible_set(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            old_plan = _write_plan(paths, stamp=OLD_STAMP, token="aaa111")
            _write_plan(paths, stamp=NEW_STAMP, token="bbb222")

            inside = build_generated_artifact_cleanup_preview(paths, now=NOW_INSIDE_RETENTION)
            retained = _by_id(inside["retained"])

            self.assertEqual(inside["eligible"], [])
            held = retained[old_plan.stem]
            self.assertEqual(held["lifecycle"], "superseded")
            self.assertEqual(held["reference_count"], 0)
            self.assertEqual(held["retention_days"], DEFAULT_RETENTION_DAYS)
            self.assertEqual(held["retention_expires_at"], "2026-01-31T00:00:00Z")
            self.assertIn("retention window until 2026-01-31T00:00:00Z", held["cleanup_reason"])

            # Same store, same artifact, only the clock moved.
            after = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)
            self.assertIn(old_plan.stem, {str(record["artifact_id"]) for record in after["eligible"]})

    def test_a_shorter_window_moves_the_boundary_and_nothing_else(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            old_plan = _write_plan(paths, stamp=OLD_STAMP, token="aaa111")
            _write_plan(paths, stamp=NEW_STAMP, token="bbb222")

            preview = build_generated_artifact_cleanup_preview(
                paths, now=NOW_INSIDE_RETENTION, retention_days=5
            )
            eligible = _by_id(preview["eligible"])

            self.assertIn(old_plan.stem, eligible)
            self.assertEqual(eligible[old_plan.stem]["retention_expires_at"], "2026-01-06T00:00:00Z")

    def test_an_artifact_with_no_readable_creation_time_is_kept(self) -> None:
        """No creation time means no window, and no window means never eligible."""
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            undated = project_artifact_dir(paths, "plans") / "undated-plan.md"
            atomic_write_text(undated, _plan_text(PLAN_TASK, "draft"), private=True)
            _write_plan(paths, stamp=NEW_STAMP, token="bbb222")

            preview = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)
            retained = _by_id(preview["retained"])

            self.assertEqual(preview["eligible"], [])
            held = retained["undated-plan"]
            self.assertEqual(held["created_at"], "")
            self.assertEqual(held["retention_expires_at"], "")
            self.assertIn("cannot be evaluated", held["retention_reason"])

    def test_the_validator_refuses_an_eligible_verdict_on_an_unsafe_artifact(self) -> None:
        """AC3 as a contract rule, not only as a computation.

        The projection is one code path; a record can also arrive from a
        transport or a hand edit. Each of the three guards is checked
        independently so a single blanket rule cannot stand in for all of them.
        """
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _write_plan(paths, stamp=OLD_STAMP, token="aaa111")
            _write_plan(paths, stamp=NEW_STAMP, token="bbb222")
            eligible = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)["eligible"][0]

            for mutation, expected in (
                ({"lifecycle": "current"}, "a current artifact must never be cleanup_eligible"),
                (
                    {
                        "referenced_by": [
                            {
                                "schema_version": "generated_artifact_reference/v1",
                                "ref_kind": "coding_delegation",
                                "ref_path": "/tmp/run/coding_delegation.json",
                                "matched_on": "path",
                            }
                        ],
                        "reference_count": 1,
                    },
                    "a referenced artifact must never be cleanup_eligible",
                ),
                (
                    {"retention_expires_at": ""},
                    "an artifact with no evaluated retention window must never be cleanup_eligible",
                ),
            ):
                with self.subTest(expected=expected):
                    self.assertIn(expected, validate_generated_artifact({**eligible, **mutation}))


class NoDeletionPathTests(unittest.TestCase):
    """The guard that makes "dry run" structural instead of a promise."""

    def test_the_projection_module_contains_no_deletion_or_write_call(self) -> None:
        source = Path(inspect.getsourcefile(generated_artifacts_module) or "").read_text(encoding="utf-8")
        tree = ast.parse(source)

        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func
                if isinstance(function, ast.Attribute):
                    called.add(function.attr)
                elif isinstance(function, ast.Name):
                    called.add(function.id)
        self.assertEqual(sorted(called & FORBIDDEN_CALLS), [])

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
                imported.update(alias.name for alias in node.names)
        self.assertEqual(sorted(imported & FORBIDDEN_IMPORTS), [])

    def test_the_cli_command_neither_removes_anything_nor_offers_to(self) -> None:
        source = textwrap.dedent(inspect.getsource(runtime_command.cmd_runtime_artifacts))
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call):
                function = node.func
                name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
                self.assertNotIn(name, FORBIDDEN_CALLS)

        # The flags an operator could reach are the other half: a command that
        # removes nothing today but advertises `--delete` is one commit away
        # from being the thing this issue said not to build.
        flags = set()
        for action in _artifacts_parser()._actions:
            flags.update(action.option_strings)
        self.assertEqual(flags & {"--delete", "--remove", "--prune", "--confirm", "--force", "--yes"}, set())
        self.assertEqual(sorted(flags), ["--all", "--help", "--json", "--limit", "--retention-days", "-h"])

    def test_a_preview_leaves_every_artifact_byte_identical(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _two_revisions_of_every_kind(paths)
            root = Path(tmp)
            before = {
                str(path.relative_to(root)): path.read_bytes()
                for path in sorted(root.rglob("*"))
                if path.is_file()
            }

            preview = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)
            self.assertTrue(preview["eligible"])

            after = {
                str(path.relative_to(root)): path.read_bytes()
                for path in sorted(root.rglob("*"))
                if path.is_file()
            }
            self.assertEqual(after, before)

    def test_the_preview_declares_itself_a_dry_run(self) -> None:
        with TemporaryDirectory() as tmp:
            preview = build_generated_artifact_cleanup_preview(_paths(tmp), now=NOW_AFTER_RETENTION)

            self.assertIs(preview["dry_run"], True)
            self.assertEqual(preview["claim_boundary"], CLEANUP_PREVIEW_CLAIM_BOUNDARY)
            self.assertIn("deletes nothing", preview["claim_boundary"])
            self.assertIn("OMH does not remove them", preview["next_action"])
            damaged = {**preview, "dry_run": False}
            self.assertIn(
                "dry_run must be True; this preview never removes anything",
                validate_generated_artifact_cleanup_preview(damaged),
            )


class ProvenanceAndDeterminismTests(unittest.TestCase):
    def test_every_named_producer_resolves_to_a_real_symbol(self) -> None:
        """The check that stops the provenance table drifting into fiction."""
        for kind, symbol in sorted(GENERATED_ARTIFACT_PRODUCERS.items()):
            with self.subTest(artifact_kind=kind):
                module_name, _, attribute = symbol.rpartition(".")
                self.assertTrue(module_name.startswith("omh."), symbol)
                self.assertTrue(hasattr(importlib.import_module(module_name), attribute), f"{symbol} is gone")
        self.assertEqual(sorted(GENERATED_ARTIFACT_PRODUCERS), sorted(GENERATED_ARTIFACT_KINDS))

    def test_the_same_store_and_the_same_stamp_answer_identically(self) -> None:
        """`now` is a parameter, so nothing here moves between two calls."""
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _two_revisions_of_every_kind(paths)

            first = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)
            second = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)

            self.assertEqual(first, second)
            self.assertEqual(first["evaluated_at"], "2026-03-01T00:00:00Z")

    def test_a_naive_stamp_is_read_as_utc_rather_than_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _write_plan(paths, stamp=OLD_STAMP, token="aaa111")

            naive = build_generated_artifact_cleanup_preview(paths, now=datetime(2026, 3, 1))
            aware = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)

            self.assertEqual(naive, aware)

    def test_a_non_positive_retention_window_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                project_generated_artifacts(_paths(tmp), now=NOW_AFTER_RETENTION, retention_days=0)


class CleanupPreviewCommandTests(unittest.TestCase):
    def test_the_command_renders_plain_text_by_default(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            old_plan = _write_plan(paths, stamp=OLD_STAMP, token="aaa111")
            new_plan = _write_plan(paths, stamp=NEW_STAMP, token="bbb222")

            status, stdout, _ = run_cli(
                [
                    "--omh-home",
                    str(paths.omh_home),
                    "--hermes-home",
                    str(paths.hermes_home),
                    "runtime",
                    "artifacts",
                ],
                output_json=False,
            )

            self.assertEqual(status, 0)
            self.assertIn("Generated artifacts", stdout)
            self.assertIn("Safe to remove", stdout)
            self.assertIn(old_plan.stem, stdout)
            self.assertIn(new_plan.stem, stdout)
            self.assertIn("dry run", stdout)

    def test_the_command_emits_a_payload_that_passes_its_own_validator(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _write_plan(paths, stamp=OLD_STAMP, token="aaa111")
            _write_plan(paths, stamp=NEW_STAMP, token="bbb222")

            status, stdout, _ = run_cli(
                [
                    "--omh-home",
                    str(paths.omh_home),
                    "--hermes-home",
                    str(paths.hermes_home),
                    "runtime",
                    "artifacts",
                    "--retention-days",
                    "1",
                    "--json",
                ]
            )

            payload = json.loads(stdout)
            self.assertEqual(status, 0)
            self.assertEqual(validate_generated_artifact_cleanup_preview(payload), [])
            self.assertEqual(payload["retention_days"], 1)
            self.assertEqual(payload["artifact_count"], 2)
            self.assertEqual(payload["eligible_count"], 1)

    def test_the_rendered_text_names_a_reason_for_every_listed_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _two_revisions_of_every_kind(paths)
            preview = build_generated_artifact_cleanup_preview(paths, now=NOW_AFTER_RETENTION)

            rendered = render_generated_artifact_cleanup_preview_text(preview)

            for record in preview["eligible"] + preview["retained"]:
                with self.subTest(artifact_id=record["artifact_id"]):
                    self.assertIn(str(record["artifact_id"]), rendered)
                    self.assertIn(str(record["cleanup_reason"]), rendered)


if __name__ == "__main__":
    unittest.main()
