"""Contracts for `recovery_anchor/v1` (issue #821).

Grouped by acceptance criterion:

- AC1: an anchor identifies one run, one workspace, and one observed baseline.
- AC2: guidance refuses a mismatched workspace, in words a person can read.
- AC3: no status claims recoverability from an absent or prepared-only anchor.

Plus the boundary the whole contract rests on: OMH records how to undo work and
performs none of it. The module runs no command, opens no socket, and creates,
writes, or deletes nothing -- proven here by building an anchor and its guidance
with `subprocess`, `socket`, and every filesystem write raising on contact.

Nothing in this file writes a file. That is deliberate as well: the anchor and
its guidance are compared by value throughout, and `Path.write_text` rewrites
"\\n" as CRLF on Windows, so a fixture written that way would compare equal on
macOS and unequal on the Windows job. There is no fixture to get wrong.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import socket
import subprocess
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()
from omh.workflows.recovery_anchor import (  # noqa: E402
    ANCHOR_STATES,
    ANCHOR_TARGET_TYPES,
    BASELINE_SOURCES,
    CLAIM_BOUNDARY,
    DIRTY_STATE_KEYS,
    DIRTY_STATE_STATES,
    GUIDANCE_REASONS,
    MAX_DIRTY_PATHS,
    MAX_RECIPE_STEPS,
    OBSERVING_BASELINE_SOURCES,
    RECOVERABLE_GUIDANCE_STATUSES,
    RECOVERY_ANCHOR_KEYS,
    RECOVERY_ANCHOR_PRIVACY,
    RECOVERY_ANCHOR_SCHEMA_VERSION,
    RECOVERY_GUIDANCE_KEYS,
    RECOVERY_GUIDANCE_SCHEMA_VERSION,
    RECOVERY_GUIDANCE_STATUSES,
    RecoveryAnchorError,
    build_dirty_state_digest,
    build_recovery_anchor,
    build_recovery_guidance,
    not_observed_dirty_state,
    validate_recovery_anchor,
    validate_recovery_guidance,
    workspace_reference,
)


_WORKSPACE = os.path.join(os.sep, "srv", "checkouts", "omh-i821")
_OTHER_WORKSPACE = os.path.join(os.sep, "srv", "checkouts", "omh-i820")
_BASE_REVISION = "9f1c2b7ae3d4f5091234ab"
_CREATED_AT = "2026-08-09T12:00:00Z"
_OBSERVED_AT = "2026-08-09T11:59:00Z"
_MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "workflows" / "recovery_anchor.py"


def _prepared(**overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "target_type": "handoff",
        "target_id": "handoff-i821-01",
        "workspace_path": _WORKSPACE,
        "created_at": _CREATED_AT,
    }
    arguments.update(overrides)
    return build_recovery_anchor(**arguments)  # type: ignore[arg-type]


def _observed(**overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "target_type": "run",
        "target_id": "run-i821-01",
        "workspace_path": _WORKSPACE,
        "created_at": _CREATED_AT,
        "base_revision": _BASE_REVISION,
        "baseline_source": "worktree_observation",
        "baseline_observed_at": _OBSERVED_AT,
        "dirty_state": build_dirty_state_digest(
            observed=True,
            changed_paths=("src/auth.py",),
            untracked_paths=("notes.md",),
        ),
        "evidence_refs": ("git-worktree:agent-issue-821",),
    }
    arguments.update(overrides)
    return build_recovery_anchor(**arguments)  # type: ignore[arg-type]


class AnchorIdentityTests(unittest.TestCase):
    """AC1: an anchor identifies one run, one workspace, one observed baseline."""

    def test_an_observed_anchor_names_its_run_workspace_and_baseline(self) -> None:
        anchor = _observed()
        self.assertEqual(anchor["schema_version"], RECOVERY_ANCHOR_SCHEMA_VERSION)
        self.assertEqual(anchor["anchor_state"], "baseline_observed")
        self.assertEqual(anchor["target_type"], "run")
        self.assertEqual(anchor["target_id"], "run-i821-01")
        self.assertEqual(anchor["workspace_ref"], workspace_reference(_WORKSPACE))
        self.assertEqual(anchor["base_revision"], _BASE_REVISION)
        self.assertEqual(anchor["baseline_source"], "worktree_observation")
        self.assertEqual(anchor["baseline_observed_at"], _OBSERVED_AT)
        self.assertEqual(anchor["privacy"], RECOVERY_ANCHOR_PRIVACY)
        self.assertEqual(anchor["claim_boundary"], CLAIM_BOUNDARY)
        self.assertEqual(validate_recovery_anchor(anchor), [])

    def test_the_key_set_is_closed_in_both_directions(self) -> None:
        anchor = _observed()
        self.assertEqual(set(anchor), set(RECOVERY_ANCHOR_KEYS))
        self.assertEqual(set(anchor["dirty_state"]), set(DIRTY_STATE_KEYS))  # type: ignore[arg-type]

        extra = {**anchor, "rollback_command": "git reset --hard"}
        self.assertTrue(
            any("unsupported keys" in error for error in validate_recovery_anchor(extra)),
            validate_recovery_anchor(extra),
        )
        for key in RECOVERY_ANCHOR_KEYS:
            with self.subTest(missing=key):
                dropped = {name: value for name, value in anchor.items() if name != key}
                self.assertTrue(
                    any("is missing keys" in error for error in validate_recovery_anchor(dropped)),
                    key,
                )

    def test_a_raw_or_hidden_key_is_refused_by_name(self) -> None:
        anchor = {**_observed(), "raw_output": "git status output"}
        errors = validate_recovery_anchor(anchor)
        self.assertTrue(any("raw or hidden keys" in error for error in errors), errors)

    def test_the_anchor_id_is_derived_from_identity_and_not_from_the_clock(self) -> None:
        # A wall-clock value inside a compared payload is a race, so neither
        # timestamp reaches the id seed. Two anchors for the same work recorded
        # at different moments are the same anchor.
        early = _observed(created_at="2026-01-01T00:00:00Z", baseline_observed_at="2025-12-31T23:59:00Z")
        late = _observed(created_at="2026-12-31T23:59:59Z", baseline_observed_at="2026-12-31T23:00:00Z")
        self.assertEqual(early["anchor_id"], late["anchor_id"])
        self.assertEqual(_observed(), _observed())
        self.assertNotEqual(_observed()["anchor_id"], _observed(target_id="run-i821-02")["anchor_id"])
        self.assertNotEqual(_observed()["anchor_id"], _observed(workspace_path=_OTHER_WORKSPACE)["anchor_id"])

    def test_a_baseline_that_no_surface_observed_is_refused(self) -> None:
        with self.assertRaises(RecoveryAnchorError) as caught:
            _observed(baseline_source="unknown")
        self.assertIn("observing baseline_source", str(caught.exception))

    def test_a_baseline_that_is_not_an_exact_object_name_is_refused(self) -> None:
        for revision in ("main", "HEAD", "v1.2.3", "abc", "zzzzzzz"):
            with self.subTest(revision=revision):
                with self.assertRaises(RecoveryAnchorError) as caught:
                    _observed(base_revision=revision)
                self.assertIn("exact object name", str(caught.exception))

    def test_an_anchor_without_a_workspace_is_refused(self) -> None:
        with self.assertRaises(RecoveryAnchorError) as caught:
            _prepared(workspace_path="  ")
        self.assertIn("workspace_path is required", str(caught.exception))

    def test_the_target_and_baseline_vocabularies_are_closed(self) -> None:
        self.assertEqual(ANCHOR_TARGET_TYPES, ("handoff", "run"))
        self.assertEqual(ANCHOR_STATES, ("prepared", "baseline_observed"))
        self.assertEqual(set(OBSERVING_BASELINE_SOURCES) | {"unknown"}, set(BASELINE_SOURCES))
        with self.assertRaises(RecoveryAnchorError):
            _prepared(target_type="workspace")
        with self.assertRaises(RecoveryAnchorError):
            _observed(baseline_source="operator_said_so")


class WorkspaceMismatchTests(unittest.TestCase):
    """AC2: guidance refuses a workspace the anchor was not taken in."""

    def test_a_mismatched_workspace_is_refused_with_a_readable_reason(self) -> None:
        guidance = build_recovery_guidance(_observed(), workspace_path=_OTHER_WORKSPACE)
        self.assertEqual(guidance["status"], "workspace_mismatch")
        self.assertFalse(guidance["recoverable"])
        self.assertEqual(guidance["steps"], [])
        self.assertEqual(guidance["base_revision"], "")
        self.assertIn("different workspace", str(guidance["reason"]))
        self.assertIn("cannot describe this one", str(guidance["reason"]))
        self.assertEqual(validate_recovery_guidance(guidance), [])

    def test_a_refusal_echoes_nothing_about_the_other_workspace(self) -> None:
        # The asker is not in the anchor's workspace, so its dirty state and its
        # baseline describe a checkout they cannot act on. Neither is returned.
        guidance = build_recovery_guidance(_observed(), workspace_path=_OTHER_WORKSPACE)
        self.assertEqual(guidance["dirty_state"], not_observed_dirty_state())
        self.assertEqual(guidance["workspace_ref"], workspace_reference(_OTHER_WORKSPACE))
        self.assertNotIn(_BASE_REVISION, repr(guidance))

    def test_an_empty_workspace_question_is_a_mismatch_not_an_answer(self) -> None:
        guidance = build_recovery_guidance(_observed(), workspace_path="")
        self.assertEqual(guidance["status"], "workspace_mismatch")
        self.assertFalse(guidance["recoverable"])

    def test_the_matching_workspace_is_answered_with_the_baseline_and_the_recipe(self) -> None:
        guidance = build_recovery_guidance(_observed(), workspace_path=_WORKSPACE)
        self.assertEqual(guidance["status"], "baseline_available")
        self.assertTrue(guidance["recoverable"])
        self.assertEqual(guidance["base_revision"], _BASE_REVISION)
        self.assertEqual(guidance["steps"], _observed()["recovery_recipe"])
        self.assertEqual(set(guidance), set(RECOVERY_GUIDANCE_KEYS))
        self.assertEqual(guidance["schema_version"], RECOVERY_GUIDANCE_SCHEMA_VERSION)
        self.assertEqual(validate_recovery_guidance(guidance), [])

    def test_workspace_references_normalize_without_touching_the_filesystem(self) -> None:
        # Same logical workspace, three spellings. Normalization is expanduser +
        # normpath + normcase and never `resolve()`, so no stat, no symlink
        # follow, and the same value twice on one machine.
        base = workspace_reference(_WORKSPACE)
        self.assertEqual(workspace_reference(_WORKSPACE + os.sep), base)
        self.assertEqual(workspace_reference(os.path.join(_WORKSPACE, "docs", "..")), base)
        self.assertNotEqual(workspace_reference(_OTHER_WORKSPACE), base)
        self.assertEqual(workspace_reference(""), "")
        self.assertFalse(os.sep in base or "srv" in base)


class NoRecoverabilityWithoutAnObservedBaselineTests(unittest.TestCase):
    """AC3: absent and prepared-only anchors never read as recoverable."""

    def test_an_absent_anchor_never_yields_a_recoverable_status(self) -> None:
        for anchor in (None, {}):
            with self.subTest(anchor=anchor):
                guidance = build_recovery_guidance(anchor, workspace_path=_WORKSPACE)
                self.assertEqual(guidance["status"], "anchor_absent")
                self.assertFalse(guidance["recoverable"])
                self.assertEqual(guidance["steps"], [])
                self.assertEqual(guidance["base_revision"], "")
                self.assertIn("nothing to return to", str(guidance["reason"]))
                self.assertEqual(validate_recovery_guidance(guidance), [])

    def test_a_prepared_only_anchor_never_yields_a_recoverable_status(self) -> None:
        anchor = _prepared()
        self.assertEqual(anchor["anchor_state"], "prepared")
        guidance = build_recovery_guidance(anchor, workspace_path=_WORKSPACE)
        self.assertEqual(guidance["status"], "baseline_not_observed")
        self.assertFalse(guidance["recoverable"])
        self.assertEqual(guidance["steps"], [])
        self.assertEqual(guidance["base_revision"], "")
        self.assertIn("not a recoverable baseline", str(guidance["reason"]))
        self.assertEqual(validate_recovery_guidance(guidance), [])

    def test_a_prepared_anchor_structurally_carries_nothing_to_read_as_recovery(self) -> None:
        # The refusal is not a status the caller must remember to check: a
        # prepared anchor has no baseline and no recipe to misread.
        anchor = _prepared()
        self.assertEqual(anchor["base_revision"], "")
        self.assertEqual(anchor["baseline_source"], "unknown")
        self.assertEqual(anchor["baseline_observed_at"], "")
        self.assertEqual(anchor["recovery_recipe"], [])
        self.assertEqual(anchor["dirty_state"], not_observed_dirty_state())
        self.assertEqual(validate_recovery_anchor(anchor), [])

    def test_a_prepared_anchor_that_grew_a_recipe_or_a_baseline_is_rejected(self) -> None:
        for field, value in (
            ("base_revision", _BASE_REVISION),
            ("baseline_source", "worktree_observation"),
            ("baseline_observed_at", _OBSERVED_AT),
            ("recovery_recipe", ["git restore --source deadbeef --staged --worktree ."]),
        ):
            with self.subTest(field=field):
                tampered = {**_prepared(), field: value}
                errors = validate_recovery_anchor(tampered)
                self.assertTrue(any("prepared anchor" in error for error in errors), errors)

    def test_an_observed_anchor_stripped_of_its_baseline_is_rejected(self) -> None:
        for field, value in (
            ("base_revision", ""),
            ("baseline_source", "unknown"),
            ("baseline_observed_at", ""),
            ("recovery_recipe", []),
        ):
            with self.subTest(field=field):
                tampered = {**_observed(), field: value}
                errors = validate_recovery_anchor(tampered)
                self.assertTrue(any("observed baseline" in error for error in errors), errors)

    def test_exactly_one_guidance_status_may_report_recoverable(self) -> None:
        self.assertEqual(RECOVERABLE_GUIDANCE_STATUSES, ("baseline_available",))
        self.assertEqual(set(GUIDANCE_REASONS), set(RECOVERY_GUIDANCE_STATUSES))
        for status in RECOVERY_GUIDANCE_STATUSES:
            with self.subTest(status=status):
                self.assertTrue(GUIDANCE_REASONS[status].strip())

    def test_guidance_claiming_recoverability_outside_that_status_is_rejected(self) -> None:
        for status in RECOVERY_GUIDANCE_STATUSES:
            if status in RECOVERABLE_GUIDANCE_STATUSES:
                continue
            with self.subTest(status=status):
                refusal = build_recovery_guidance(None, workspace_path=_WORKSPACE)
                tampered = {**refusal, "status": status, "recoverable": True}
                errors = validate_recovery_guidance(tampered)
                self.assertTrue(any("must not report recoverable" in error for error in errors), errors)

    def test_a_refusal_that_smuggled_in_steps_or_a_baseline_is_rejected(self) -> None:
        refusal = build_recovery_guidance(_prepared(), workspace_path=_WORKSPACE)
        with_steps = {**refusal, "steps": ["git restore --source deadbeef --staged --worktree ."]}
        self.assertTrue(
            any("must carry no steps" in error for error in validate_recovery_guidance(with_steps)),
            validate_recovery_guidance(with_steps),
        )
        with_baseline = {**refusal, "base_revision": _BASE_REVISION}
        self.assertTrue(
            any("must name no base_revision" in error for error in validate_recovery_guidance(with_baseline)),
            validate_recovery_guidance(with_baseline),
        )

    def test_an_invalid_anchor_is_refused_rather_than_read(self) -> None:
        guidance = build_recovery_guidance({**_observed(), "anchor_state": "definitely_fine"}, workspace_path=_WORKSPACE)
        self.assertEqual(guidance["status"], "anchor_invalid")
        self.assertFalse(guidance["recoverable"])
        self.assertEqual(guidance["steps"], [])
        self.assertEqual(validate_recovery_guidance(guidance), [])


class BoundedDirtyStateTests(unittest.TestCase):
    def test_the_digest_covers_every_observed_path_and_the_sample_is_bounded(self) -> None:
        many = tuple(f"src/module_{index:03d}.py" for index in range(MAX_DIRTY_PATHS + 5))
        state = build_dirty_state_digest(observed=True, changed_paths=many)
        self.assertEqual(state["state"], "dirty")
        self.assertEqual(state["changed_file_count"], len(many))
        self.assertEqual(len(state["paths"]), MAX_DIRTY_PATHS)  # type: ignore[arg-type]
        self.assertTrue(state["paths_truncated"])
        # A change past the sample bound still changes the digest.
        beyond = build_dirty_state_digest(observed=True, changed_paths=(*many[:-1], "src/module_999.py"))
        self.assertNotEqual(state["digest"], beyond["digest"])
        self.assertEqual(state["paths"], beyond["paths"])

    def test_counts_come_from_the_observed_lists_and_cannot_disagree_with_them(self) -> None:
        state = build_dirty_state_digest(
            observed=True,
            changed_paths=("src/a.py", "src/a.py", " src/b.py "),
            untracked_paths=("notes.md",),
        )
        self.assertEqual(state["changed_file_count"], 2)
        self.assertEqual(state["untracked_file_count"], 1)
        self.assertEqual(state["paths"], ["notes.md", "src/a.py", "src/b.py"])

    def test_a_path_that_is_not_project_relative_is_stored_as_a_handle(self) -> None:
        unsafe = (
            os.path.join(os.sep, "etc", "shadow"),
            os.path.join("~", "private", "diary.md"),
            os.path.join("..", "..", "other-repo", "secrets.env"),
            "https://example.invalid/leak.txt",
            "src/x.py\nsrc/y.py",
            "x" * 400,
        )
        state = build_dirty_state_digest(observed=True, changed_paths=unsafe)
        for path in state["paths"]:  # type: ignore[union-attr]
            with self.subTest(path=path):
                self.assertTrue(str(path).startswith("ref-"), path)
        self.assertEqual(validate_recovery_anchor(_observed(dirty_state=state)), [])

    def test_a_root_anchored_path_folds_in_either_separator_spelling(self) -> None:
        # Both spellings on both platforms, not `os.sep`. `\etc\shadow` is
        # root-anchored on Windows but was read here as project-relative, so an
        # absolute path was stored verbatim in a metadata-only record. Windows CI
        # was the only place `os.sep` produced that spelling, which is why the
        # bound is asserted against literals instead of the platform separator.
        for path in ("/etc/shadow", "\\etc\\shadow", "C:/Windows/system32", "\\\\server\\share\\x"):
            with self.subTest(path=path):
                state = build_dirty_state_digest(observed=True, changed_paths=(path,))
                stored = state["paths"]  # type: ignore[index]
                self.assertEqual(len(stored), 1)
                self.assertTrue(str(stored[0]).startswith("ref-"), stored[0])
                self.assertNotIn("etc", str(stored[0]))
                self.assertNotIn("Windows", str(stored[0]))

    def test_a_stored_path_that_never_went_through_the_bound_is_rejected(self) -> None:
        anchor = _observed()
        tampered = {**anchor, "dirty_state": {**anchor["dirty_state"], "paths": ["/etc/shadow"]}}  # type: ignore[dict-item]
        errors = validate_recovery_anchor(tampered)
        self.assertTrue(any("bounded, redacted path" in error for error in errors), errors)

    def test_nothing_looked_is_not_the_same_as_nothing_was_there(self) -> None:
        self.assertEqual(DIRTY_STATE_STATES, ("not_observed", "clean", "dirty"))
        unobserved = build_dirty_state_digest(observed=False, changed_paths=("src/a.py",))
        self.assertEqual(unobserved, not_observed_dirty_state())
        self.assertEqual(unobserved["digest"], "")
        clean = build_dirty_state_digest(observed=True)
        self.assertEqual(clean["state"], "clean")
        self.assertTrue(clean["digest"])
        # The recipe reads the difference: an unobserved workspace is told to
        # save work first, a clean one is told there is nothing to save.
        unobserved_recipe = _observed(dirty_state=unobserved)["recovery_recipe"]
        clean_recipe = _observed(dirty_state=clean)["recovery_recipe"]
        self.assertIn("No dirty-state observation was recorded", " ".join(unobserved_recipe))  # type: ignore[arg-type]
        self.assertIn("nothing to save first", " ".join(clean_recipe))  # type: ignore[arg-type]

    def test_a_not_observed_dirty_state_carrying_a_digest_is_rejected(self) -> None:
        anchor = _observed()
        tampered = {
            **anchor,
            "dirty_state": {**not_observed_dirty_state(), "digest": "dirty-0000000000000000"},
        }
        errors = validate_recovery_anchor(tampered)
        self.assertTrue(any("not_observed must carry no digest" in error for error in errors), errors)


class RecipeTests(unittest.TestCase):
    def test_the_recipe_is_bounded_instruction_lines_naming_the_baseline(self) -> None:
        recipe = _observed()["recovery_recipe"]
        self.assertTrue(recipe)
        self.assertLessEqual(len(recipe), MAX_RECIPE_STEPS)  # type: ignore[arg-type]
        joined = " ".join(recipe)  # type: ignore[arg-type]
        self.assertIn(_BASE_REVISION, joined)
        self.assertIn("git stash push --include-untracked", joined)
        self.assertIn("performs no rollback", joined)

    def test_no_step_carries_a_path_a_link_or_a_control_character(self) -> None:
        for step in _observed()["recovery_recipe"]:  # type: ignore[union-attr]
            with self.subTest(step=step):
                self.assertNotIn("://", step)
                self.assertNotIn("?", step)
                self.assertNotIn("#", step)
                self.assertEqual(step, " ".join(step.split()))

    def test_a_step_that_is_a_link_or_a_body_is_rejected(self) -> None:
        anchor = _observed()
        for step in ("https://example.invalid/undo", "line one\nline two", "x" * 400, "   "):
            with self.subTest(step=step):
                errors = validate_recovery_anchor({**anchor, "recovery_recipe": [step]})
                self.assertTrue(errors)


class NoMutationBoundaryTests(unittest.TestCase):
    """The module records how to undo work and performs none of it."""

    def test_building_an_anchor_and_its_guidance_touches_no_process_socket_or_file(self) -> None:
        def refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError(f"recovery_anchor performed an effect: {args!r} {kwargs!r}")

        with (
            patch.object(subprocess, "run", refuse),
            patch.object(subprocess, "Popen", refuse),
            patch.object(socket, "socket", refuse),
            patch.object(Path, "open", refuse),
            patch.object(Path, "write_text", refuse),
            patch.object(Path, "write_bytes", refuse),
            patch.object(Path, "mkdir", refuse),
            patch.object(Path, "unlink", refuse),
            patch.object(os, "remove", refuse),
            patch("builtins.open", refuse),
        ):
            anchor = _observed()
            guidance = build_recovery_guidance(anchor, workspace_path=_WORKSPACE)
            refusal = build_recovery_guidance(anchor, workspace_path=_OTHER_WORKSPACE)
            self.assertEqual(validate_recovery_anchor(anchor), [])
            self.assertEqual(validate_recovery_guidance(guidance), [])
            self.assertEqual(refusal["status"], "workspace_mismatch")

    def test_the_module_imports_nothing_that_executes_or_reaches_the_network(self) -> None:
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        forbidden = {"subprocess", "socket", "shutil", "urllib", "http", "asyncio", "multiprocessing", "tempfile"}
        self.assertEqual(imported & forbidden, set(), sorted(imported))

    def test_every_answer_states_that_omh_performs_no_rollback(self) -> None:
        for anchor in (None, _prepared(), _observed()):
            for workspace in (_WORKSPACE, _OTHER_WORKSPACE):
                with self.subTest(anchor=anchor, workspace=workspace):
                    guidance = build_recovery_guidance(anchor, workspace_path=workspace)
                    self.assertIs(guidance["performs_rollback"], False)
                    self.assertEqual(guidance["claim_boundary"], CLAIM_BOUNDARY)
        self.assertIn("performs no rollback", CLAIM_BOUNDARY)
        self.assertIn("creates or deletes nothing", CLAIM_BOUNDARY)

    def test_guidance_that_claims_it_rolled_back_is_rejected(self) -> None:
        guidance = build_recovery_guidance(_observed(), workspace_path=_WORKSPACE)
        errors = validate_recovery_guidance({**guidance, "performs_rollback": True})
        self.assertTrue(any("performs_rollback must be false" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
