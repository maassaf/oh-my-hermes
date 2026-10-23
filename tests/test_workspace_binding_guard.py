"""Contracts for `workspace_binding_guard/v1` (issue #820).

The acceptance criteria this holds, and where each one is asserted:

AC1  two active handoffs cannot bind the same workspace or branch
     -> `TwoActiveHandoffsCannotShareOneWorkspace`,
        `TwoActiveHandoffsCannotShareOneBranch`,
        `OneDirectoryHasOneIdentityHoweverItIsSpelled`
AC2  every bound handoff includes binding id, owner, and base revision
     -> `EveryBoundHandoffNamesItsBindingOwnerAndBaseRevision`
AC3  stale bindings block reuse and return recovery guidance
     -> `StaleBindingsBlockReuseAndSayHowToRecover`

Plus the two things a guard is worthless without: that a release really frees
the workspace (`ReleaseFreesTheWorkspace`), and that holding a binding is never
mistaken for having dispatched anything (`ABindingIsNotDispatchEvidence`).

Store mechanics -- concurrent appends, torn tails, supersession forks -- are
inherited from `system/append_only_store.py` and proved there. What is asserted
here is the *wiring*: that this family reaches the shared walk with its own key
names, that its chain is judged at all, and that its exclusivity survives the
one thing a path-keyed guard is most easily defeated by, which is spelling the
path differently.
"""

from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from _local_package import load_local_package

load_local_package()
from omh.coding.action_gate import (
    WORKSPACE_RUNTIME_BLOCKER,
    build_task_authority_envelope,
    build_task_handoff_safety_contract,
)
from omh.runtime.artifacts import validate_runtime
from omh.runtime.records import (
    OPTIONAL_RUNTIME_STORE_VALIDATORS,
    WORKSPACE_BINDING_RECORD_KEYS,
)
from omh.system.append_only_store import RAW_OR_HIDDEN_KEYS
from omh.system.paths import OmhPaths
from omh.workflows.approval_receipts import _EXECUTION_CLAIM_KEYS as APPROVAL_EXECUTION_CLAIM_KEYS
from omh.workflows.workspace_bindings import (
    BINDING_STALE_AFTER_SECONDS,
    BINDING_STATES,
    CLAIM_BOUNDARY,
    EXECUTION_CLAIM_KEYS,
    OUTCOME_ACQUIRED,
    OUTCOME_ALREADY_HELD,
    OUTCOME_ALREADY_RELEASED,
    OUTCOME_REFUSED,
    OUTCOME_RELEASED,
    OUTCOME_UNBOUND,
    RECOVERY_ACTIONS,
    RECOVERY_EXPLICIT_RELEASE,
    RECOVERY_GUIDANCE,
    RECOVERY_REACQUIRE,
    RECOVERY_RELEASE_STALE,
    RECOVERY_WAIT,
    REFUSAL_BRANCH_BOUND,
    REFUSAL_BRANCH_STALE,
    REFUSAL_CODES,
    REFUSAL_CONDITION_UNMET,
    REFUSAL_MALFORMED,
    REFUSAL_NOT_HELD,
    REFUSAL_REASONS,
    REFUSAL_RECOVERY,
    REFUSAL_TERMS_CHANGED,
    REFUSAL_WORKSPACE_BOUND,
    REFUSAL_WORKSPACE_STALE,
    RELEASE_CONDITIONS,
    WORKSPACE_BINDING_CONSTANT_KEYS,
    WORKSPACE_BINDING_KEYS,
    WORKSPACE_BINDING_SCHEMA_VERSION,
    WorkspaceBindingError,
    acquire_workspace_binding,
    active_workspace_bindings,
    binding_age_seconds,
    binding_is_stale,
    build_workspace_binding,
    canonical_workspace_path,
    compact_workspace_binding,
    inspect_workspace_binding,
    read_workspace_bindings,
    release_workspace_binding,
    validate_workspace_binding,
    validate_workspace_binding_store,
    workspace_ref,
)

_NOW = "2026-08-09T12:00:00Z"


def _later(seconds: int) -> str:
    """`_NOW` moved forward, so freshness is compared against an injected clock."""
    moment = datetime.fromisoformat(_NOW.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return moment.isoformat().replace("+00:00", "Z")


def _paths(root: Path) -> OmhPaths:
    return OmhPaths(omh_home=root, hermes_home=root / "hermes")


def _claim(**overrides: object) -> dict[str, object]:
    """One well-formed acquisition, as keyword arguments."""
    claim: dict[str, object] = {
        "repository_ref": "rlaope/oh-my-hermes",
        "branch_ref": "agent/issue-820",
        "base_revision": "c7f9218b",
        "handoff_ref": "handoff-alpha",
        "owner": "codex",
        "safety_profile_revision": "rev-1",
        "now": _NOW,
    }
    claim.update(overrides)
    return claim


def _record(workspace: Path, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "repository_ref": "rlaope/oh-my-hermes",
        "workspace_path": workspace,
        "branch_ref": "agent/issue-820",
        "base_revision": "c7f9218b",
        "handoff_ref": "handoff-alpha",
        "owner": "codex",
        "safety_profile_revision": "rev-1",
        "recorded_at": _NOW,
    }
    kwargs.update(overrides)
    return build_workspace_binding(**kwargs)  # type: ignore[arg-type]


class TwoActiveHandoffsCannotShareOneWorkspace(unittest.TestCase):
    """AC1, the workspace axis: the collision #820 is named after."""

    def test_the_first_handoff_acquires_and_the_second_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"

            first = acquire_workspace_binding(paths, workspace_path=workspace, **_claim())
            second = acquire_workspace_binding(
                paths,
                workspace_path=workspace,
                **_claim(handoff_ref="handoff-beta", owner="claude-code", branch_ref="agent/other"),
            )

            self.assertTrue(first["bound"])
            self.assertEqual(first["outcome"], OUTCOME_ACQUIRED)
            self.assertFalse(second["bound"])
            self.assertEqual(second["outcome"], OUTCOME_REFUSED)
            self.assertEqual(second["reason_code"], REFUSAL_WORKSPACE_BOUND)
            # Only one record reached the store: a refusal is not an event that
            # gets written into the reservation's own chain.
            self.assertEqual(len(read_workspace_bindings(paths)), 1)

    def test_the_refusal_is_readable_and_names_who_owns_it(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())

            refusal = acquire_workspace_binding(
                paths,
                workspace_path=workspace,
                **_claim(handoff_ref="handoff-beta", owner="claude-code"),
            )

            self.assertEqual(refusal["reason"], REFUSAL_REASONS[REFUSAL_WORKSPACE_BOUND])
            self.assertIn("two handoffs cannot share one checkout", refusal["reason"])
            # "explains who owns it" is the native-experience half of #820.
            self.assertEqual(refusal["holder_owner"], "codex")
            self.assertEqual(refusal["holder_handoff_ref"], "handoff-alpha")
            self.assertEqual(refusal["holder_branch_ref"], "agent/issue-820")
            self.assertEqual(refusal["requested_owner"], "claude-code")

    def test_the_same_handoff_restating_its_own_claim_is_not_a_conflict(self) -> None:
        """Idempotence. A retry must not be refused as a collision with itself."""
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            first = acquire_workspace_binding(paths, workspace_path=workspace, **_claim())

            again = acquire_workspace_binding(
                paths, workspace_path=workspace, **_claim(now=_later(60))
            )

            self.assertTrue(again["bound"])
            self.assertEqual(again["outcome"], OUTCOME_ALREADY_HELD)
            self.assertEqual(again["reason_code"], "")
            self.assertEqual(again["record_id"], first["record_id"])
            self.assertEqual(len(read_workspace_bindings(paths)), 1)

    def test_a_second_owner_quoting_the_same_handoff_is_still_refused(self) -> None:
        """Ownership is what a binding records; quoting a handoff cannot inherit it."""
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())

            refusal = acquire_workspace_binding(
                paths, workspace_path=workspace, **_claim(owner="claude-code")
            )

            self.assertFalse(refusal["bound"])
            self.assertEqual(refusal["reason_code"], REFUSAL_WORKSPACE_BOUND)

    def test_the_same_handoff_on_new_terms_must_release_first(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())

            moved = acquire_workspace_binding(
                paths, workspace_path=workspace, **_claim(base_revision="deadbeef")
            )

            self.assertFalse(moved["bound"])
            self.assertEqual(moved["reason_code"], REFUSAL_TERMS_CHANGED)
            self.assertEqual(moved["recovery_action"], RECOVERY_REACQUIRE)


class TwoActiveHandoffsCannotShareOneBranch(unittest.TestCase):
    """AC1, the branch axis: two checkouts on one branch is the other half."""

    def test_a_second_workspace_on_a_held_branch_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            acquire_workspace_binding(paths, workspace_path=Path(tmp) / "one", **_claim())

            refusal = acquire_workspace_binding(
                paths,
                workspace_path=Path(tmp) / "two",
                **_claim(handoff_ref="handoff-beta", owner="claude-code"),
            )

            self.assertFalse(refusal["bound"])
            self.assertEqual(refusal["reason_code"], REFUSAL_BRANCH_BOUND)
            self.assertIn("two checkouts cannot share one branch", refusal["reason"])
            self.assertEqual(refusal["holder_owner"], "codex")

    def test_a_second_workspace_on_another_branch_is_allowed(self) -> None:
        """The guard bounds collisions, not parallelism. The negative case."""
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            acquire_workspace_binding(paths, workspace_path=Path(tmp) / "one", **_claim())

            second = acquire_workspace_binding(
                paths,
                workspace_path=Path(tmp) / "two",
                **_claim(branch_ref="agent/issue-821", handoff_ref="handoff-beta", owner="claude-code"),
            )

            self.assertTrue(second["bound"])
            self.assertEqual(second["outcome"], OUTCOME_ACQUIRED)
            self.assertEqual(len(active_workspace_bindings(read_workspace_bindings(paths))), 2)

    def test_the_same_branch_in_another_repository_is_allowed(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            acquire_workspace_binding(paths, workspace_path=Path(tmp) / "one", **_claim())

            other = acquire_workspace_binding(
                paths,
                workspace_path=Path(tmp) / "two",
                **_claim(repository_ref="rlaope/other", handoff_ref="handoff-beta"),
            )

            self.assertTrue(other["bound"])

    def test_the_workspace_axis_is_reported_before_the_branch_axis(self) -> None:
        """The more specific answer first: fixing the branch alone would not help."""
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())

            both = acquire_workspace_binding(
                paths, workspace_path=workspace, **_claim(handoff_ref="handoff-beta")
            )

            self.assertEqual(both["reason_code"], REFUSAL_WORKSPACE_BOUND)


class AcquisitionIsAtomicByWorkspaceIdentity(unittest.TestCase):
    """The whole guard rests on this: a check-then-write with a gap is no guard."""

    def test_barrier_synced_acquisitions_produce_one_winner(self) -> None:
        """Unlocked, every racer reads an empty store and every racer writes.

        `file_lock` opens its own handle per call, so `flock` really does
        serialize threads in one process -- the same property
        `tests/test_append_only_store.py::ConcurrentAppendTests` relies on.
        """
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "contested"
            racers = 8
            barrier = threading.Barrier(racers)
            decisions: list[dict[str, object]] = []
            raised: list[Exception] = []
            guard = threading.Lock()

            def race(index: int) -> None:
                barrier.wait()
                try:
                    decision = acquire_workspace_binding(
                        paths,
                        workspace_path=workspace,
                        **_claim(handoff_ref=f"handoff-{index}", owner=f"owner-{index}"),
                    )
                except Exception as exc:  # reported on the main thread, never dropped
                    with guard:
                        raised.append(exc)
                    return
                with guard:
                    decisions.append(decision)

            threads = [threading.Thread(target=race, args=(index,)) for index in range(racers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(raised, [])
            self.assertEqual(len(decisions), racers)
            winners = [decision for decision in decisions if decision["bound"]]
            self.assertEqual(len(winners), 1)
            for decision in decisions:
                if decision is winners[0]:
                    continue
                self.assertEqual(decision["reason_code"], REFUSAL_WORKSPACE_BOUND)
            # One reservation on disk, and it is the winner's.
            records = read_workspace_bindings(paths)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["record_id"], winners[0]["record_id"])


class OneDirectoryHasOneIdentityHoweverItIsSpelled(unittest.TestCase):
    """A guard defeated by typing the path differently would be decoration."""

    def test_two_spellings_of_one_directory_collide(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            root = Path(tmp) / "work"
            root.mkdir()
            (root / "nested").mkdir()
            spellings = (
                root,
                Path(tmp) / "." / "work",
                root / "nested" / "..",
                Path(f"{root}{Path('/')}"),
            )
            acquire_workspace_binding(paths, workspace_path=root, **_claim())

            for spelling in spellings:
                with self.subTest(spelling=str(spelling)):
                    self.assertEqual(workspace_ref(spelling), workspace_ref(root))
                    refusal = acquire_workspace_binding(
                        paths,
                        workspace_path=spelling,
                        **_claim(handoff_ref="handoff-beta", branch_ref="agent/other"),
                    )
                    self.assertFalse(refusal["bound"])
                    self.assertEqual(refusal["reason_code"], REFUSAL_WORKSPACE_BOUND)

    def test_a_sibling_directory_is_a_different_workspace(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertNotEqual(
                workspace_ref(Path(tmp) / "work"), workspace_ref(Path(tmp) / "work-2")
            )

    def test_no_path_byte_reaches_the_store(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            # A directory name that shares no substring with the record's own
            # vocabulary, so the assertion below fails only on a real leak.
            workspace = Path(tmp) / "zephyr-checkout"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())

            raw = paths.runtime_workspace_bindings_path.read_text(encoding="utf-8")

            self.assertNotIn(str(workspace), raw)
            self.assertNotIn(workspace.name, raw)
            self.assertNotIn(tmp, raw)
            self.assertEqual(json.loads(raw)["workspace_ref"], workspace_ref(workspace))

    def test_a_stored_reference_that_is_a_path_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            record = _record(Path(tmp) / "work")
            record["workspace_ref"] = "home/someone/work"

            errors = validate_workspace_binding(record)

            self.assertTrue(any("canonical-path digest handle" in error for error in errors), errors)

    def test_an_empty_workspace_path_is_refused_rather_than_resolved(self) -> None:
        """`Path("")` resolves to the current directory; binding it would be silent."""
        with self.assertRaises(WorkspaceBindingError):
            canonical_workspace_path("")
        with TemporaryDirectory() as tmp:
            refusal = acquire_workspace_binding(_paths(Path(tmp)), workspace_path="", **_claim())
            self.assertEqual(refusal["reason_code"], REFUSAL_MALFORMED)
            self.assertTrue(refusal["error"])


class EveryBoundHandoffNamesItsBindingOwnerAndBaseRevision(unittest.TestCase):
    """AC2, on the record and on the verdict a caller is handed."""

    def test_the_record_carries_all_three(self) -> None:
        with TemporaryDirectory() as tmp:
            record = _record(Path(tmp) / "work")

            self.assertTrue(record["binding_id"].startswith("binding-"))
            self.assertEqual(record["owner"], "codex")
            self.assertEqual(record["base_revision"], "c7f9218b")
            self.assertEqual(record["state"], "held")
            self.assertEqual(validate_workspace_binding(record), [])

    def test_the_acquisition_verdict_carries_all_three(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))

            decision = acquire_workspace_binding(paths, workspace_path=Path(tmp) / "work", **_claim())

            self.assertTrue(decision["binding_id"].startswith("binding-"))
            self.assertEqual(decision["holder_owner"], "codex")
            self.assertEqual(decision["holder_base_revision"], "c7f9218b")

    def test_every_dimension_the_issue_names_is_a_stored_field(self) -> None:
        """Repository, path, branch, base revision, handoff, owner, revision, condition."""
        for field in (
            "repository_ref",
            "workspace_ref",
            "branch_ref",
            "base_revision",
            "handoff_ref",
            "owner",
            "safety_profile_revision",
            "release_condition",
        ):
            with self.subTest(field=field):
                self.assertIn(field, WORKSPACE_BINDING_KEYS)

    def test_a_missing_identifier_is_refused_rather_than_defaulted(self) -> None:
        with TemporaryDirectory() as tmp:
            for field in ("repository_ref", "branch_ref", "base_revision", "handoff_ref", "owner"):
                with self.subTest(field=field), self.assertRaises(WorkspaceBindingError):
                    _record(Path(tmp) / "work", **{field: ""})

    def test_the_key_set_is_closed_and_every_key_renders(self) -> None:
        with TemporaryDirectory() as tmp:
            record = _record(Path(tmp) / "work")
            self.assertEqual(sorted(record), sorted(WORKSPACE_BINDING_KEYS))

            rendered = compact_workspace_binding(record, now=_NOW)

            # A key stored and never rendered is a field nobody can see. The
            # three constants are the deliberate exception.
            for key in WORKSPACE_BINDING_KEYS:
                if key in WORKSPACE_BINDING_CONSTANT_KEYS:
                    continue
                with self.subTest(key=key):
                    self.assertIn(key, rendered)

    def test_an_unsupported_key_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            record = dict(_record(Path(tmp) / "work"))
            record["lease_holder_pid"] = "4242"

            errors = validate_workspace_binding(record)

            self.assertTrue(any("unsupported keys" in error for error in errors), errors)

    def test_raw_and_hidden_keys_are_refused_by_name(self) -> None:
        with TemporaryDirectory() as tmp:
            record = dict(_record(Path(tmp) / "work"))
            record["prompt"] = "do the thing"

            errors = validate_workspace_binding(record)

            self.assertTrue(any("raw or hidden keys" in error for error in errors), errors)
            self.assertIn("prompt", RAW_OR_HIDDEN_KEYS)


class StaleBindingsBlockReuseAndSayHowToRecover(unittest.TestCase):
    """AC3. Both halves: reuse is blocked, and the way out comes back with it."""

    def test_a_stale_binding_refuses_reuse_by_another_handoff(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())

            refusal = acquire_workspace_binding(
                paths,
                workspace_path=workspace,
                **_claim(
                    handoff_ref="handoff-beta",
                    owner="claude-code",
                    branch_ref="agent/other",
                    now=_later(BINDING_STALE_AFTER_SECONDS + 1),
                ),
            )

            self.assertFalse(refusal["bound"])
            self.assertEqual(refusal["outcome"], OUTCOME_REFUSED)
            self.assertEqual(refusal["reason_code"], REFUSAL_WORKSPACE_STALE)
            self.assertTrue(refusal["stale"])

    def test_the_stale_refusal_returns_recovery_guidance(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())

            refusal = acquire_workspace_binding(
                paths,
                workspace_path=workspace,
                **_claim(handoff_ref="handoff-beta", now=_later(BINDING_STALE_AFTER_SECONDS + 1)),
            )

            self.assertEqual(refusal["recovery_action"], RECOVERY_RELEASE_STALE)
            self.assertEqual(refusal["recovery_guidance"], RECOVERY_GUIDANCE[RECOVERY_RELEASE_STALE])
            # Guidance, not an identifier: an id alone is not guidance.
            self.assertIn("explicit_safe_release", refusal["recovery_guidance"])
            self.assertEqual(refusal["stale_after_seconds"], BINDING_STALE_AFTER_SECONDS)
            self.assertGreater(refusal["age_seconds"], BINDING_STALE_AFTER_SECONDS)

    def test_the_stale_refusal_is_a_different_code_from_a_fresh_one(self) -> None:
        """Different recoveries: one is waited out, the other is released."""
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())
            second = _claim(handoff_ref="handoff-beta", branch_ref="agent/other")

            fresh = acquire_workspace_binding(
                paths, workspace_path=workspace, **{**second, "now": _later(60)}
            )
            stale = acquire_workspace_binding(
                paths,
                workspace_path=workspace,
                **{**second, "now": _later(BINDING_STALE_AFTER_SECONDS + 1)},
            )

            self.assertEqual(fresh["reason_code"], REFUSAL_WORKSPACE_BOUND)
            self.assertEqual(fresh["recovery_action"], RECOVERY_WAIT)
            self.assertEqual(stale["reason_code"], REFUSAL_WORKSPACE_STALE)
            self.assertEqual(stale["recovery_action"], RECOVERY_RELEASE_STALE)

    def test_a_stale_branch_conflict_has_its_own_code(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            acquire_workspace_binding(paths, workspace_path=Path(tmp) / "one", **_claim())

            refusal = acquire_workspace_binding(
                paths,
                workspace_path=Path(tmp) / "two",
                **_claim(handoff_ref="handoff-beta", now=_later(BINDING_STALE_AFTER_SECONDS + 1)),
            )

            self.assertEqual(refusal["reason_code"], REFUSAL_BRANCH_STALE)
            self.assertEqual(refusal["recovery_action"], RECOVERY_RELEASE_STALE)

    def test_staleness_never_releases_the_binding_by_itself(self) -> None:
        """No background lease service: #820 puts one out of scope, and it would be wrong."""
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())

            acquire_workspace_binding(
                paths,
                workspace_path=workspace,
                **_claim(handoff_ref="handoff-beta", now=_later(BINDING_STALE_AFTER_SECONDS * 10)),
            )

            still_held = active_workspace_bindings(read_workspace_bindings(paths))
            self.assertEqual(len(still_held), 1)
            self.assertEqual(still_held[0]["handoff_ref"], "handoff-alpha")

    def test_the_stale_workspace_can_be_taken_after_an_explicit_release(self) -> None:
        """The recovery the guidance names actually works."""
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())
            stale_now = _later(BINDING_STALE_AFTER_SECONDS + 1)

            released = release_workspace_binding(
                paths,
                repository_ref="rlaope/oh-my-hermes",
                workspace_path=workspace,
                handoff_ref="handoff-beta",
                owner="claude-code",
                release_reason="explicit_safe_release",
                now=stale_now,
            )
            taken = acquire_workspace_binding(
                paths,
                workspace_path=workspace,
                **_claim(handoff_ref="handoff-beta", owner="claude-code", now=stale_now),
            )

            self.assertEqual(released["outcome"], OUTCOME_RELEASED)
            self.assertTrue(taken["bound"])

    def test_an_unreadable_or_future_stamp_reads_as_stale(self) -> None:
        """Neither can be shown to be fresh, and clamping would widen the window from disk."""
        self.assertTrue(binding_is_stale({"recorded_at": "not-a-date"}, _NOW))
        self.assertTrue(binding_is_stale({"recorded_at": _later(3600)}, _NOW))
        self.assertEqual(binding_age_seconds(_later(3600), _NOW), -1)
        self.assertFalse(binding_is_stale({"recorded_at": _NOW}, _later(60)))

    def test_no_stored_field_declares_when_a_binding_expires(self) -> None:
        """Freshness is derived at read time, never a deadline a reader must trust."""
        with TemporaryDirectory() as tmp:
            record = _record(Path(tmp) / "work")
            for key in record:
                with self.subTest(key=key):
                    self.assertNotIn("expire", key)
                    self.assertNotIn("deadline", key)
                    self.assertNotIn("valid_until", key)
                    self.assertNotIn("ttl", key)


class EveryRefusalCarriesAWayOut(unittest.TestCase):
    """A refusal with no recovery is what makes a guard unusable."""

    def test_every_refusal_code_has_a_reason_and_a_recovery(self) -> None:
        for code in REFUSAL_CODES:
            with self.subTest(code=code):
                self.assertIn(code, REFUSAL_REASONS)
                self.assertTrue(REFUSAL_REASONS[code].strip())
                self.assertIn(code, REFUSAL_RECOVERY)
                recovery = REFUSAL_RECOVERY[code]
                self.assertIn(recovery, RECOVERY_ACTIONS)
                self.assertTrue(RECOVERY_GUIDANCE[recovery].strip())

    def test_every_recovery_id_is_reachable_from_some_refusal(self) -> None:
        self.assertEqual(sorted(set(REFUSAL_RECOVERY.values())), sorted(RECOVERY_ACTIONS))

    def test_no_reason_line_interpolates_a_caller_value(self) -> None:
        """A refusal is rendered to a person; a formatted line is a path for text."""
        for text in (*REFUSAL_REASONS.values(), *RECOVERY_GUIDANCE.values()):
            with self.subTest(text=text[:40]):
                self.assertNotIn("{", text)
                self.assertNotIn("%s", text)


class ReleaseFreesTheWorkspace(unittest.TestCase):
    """Release only after an observed terminal state or an explicit safe release."""

    def test_a_released_binding_can_be_re_acquired(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())

            released = release_workspace_binding(
                paths,
                repository_ref="rlaope/oh-my-hermes",
                workspace_path=workspace,
                handoff_ref="handoff-alpha",
                owner="codex",
                release_reason="observed_terminal_state",
                now=_later(60),
            )
            retaken = acquire_workspace_binding(
                paths,
                workspace_path=workspace,
                **_claim(handoff_ref="handoff-beta", owner="claude-code", now=_later(120)),
            )

            self.assertEqual(released["outcome"], OUTCOME_RELEASED)
            self.assertFalse(released["bound"])
            self.assertTrue(retaken["bound"])
            self.assertEqual(retaken["outcome"], OUTCOME_ACQUIRED)
            # Three records, one chain, nothing rewritten.
            records = read_workspace_bindings(paths)
            self.assertEqual([record["state"] for record in records], ["held", "released", "held"])
            self.assertEqual(records[1]["supersedes_binding_ref"], records[0]["record_id"])
            self.assertEqual(records[2]["supersedes_binding_ref"], records[1]["record_id"])

    def test_releasing_frees_the_branch_too(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            acquire_workspace_binding(paths, workspace_path=Path(tmp) / "one", **_claim())
            release_workspace_binding(
                paths,
                repository_ref="rlaope/oh-my-hermes",
                workspace_path=Path(tmp) / "one",
                handoff_ref="handoff-alpha",
                owner="codex",
                release_reason="observed_terminal_state",
                now=_later(60),
            )

            elsewhere = acquire_workspace_binding(
                paths,
                workspace_path=Path(tmp) / "two",
                **_claim(handoff_ref="handoff-beta", now=_later(120)),
            )

            self.assertTrue(elsewhere["bound"])

    def test_a_terminal_state_release_is_accepted_only_from_the_holder(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())

            refusal = release_workspace_binding(
                paths,
                repository_ref="rlaope/oh-my-hermes",
                workspace_path=workspace,
                handoff_ref="handoff-beta",
                owner="claude-code",
                release_reason="observed_terminal_state",
                now=_later(60),
            )

            self.assertEqual(refusal["outcome"], OUTCOME_REFUSED)
            self.assertEqual(refusal["reason_code"], REFUSAL_NOT_HELD)
            self.assertEqual(len(active_workspace_bindings(read_workspace_bindings(paths))), 1)

    def test_a_binding_taken_under_explicit_release_refuses_a_terminal_release(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(
                paths,
                workspace_path=workspace,
                **_claim(release_condition="explicit_safe_release"),
            )

            refusal = release_workspace_binding(
                paths,
                repository_ref="rlaope/oh-my-hermes",
                workspace_path=workspace,
                handoff_ref="handoff-alpha",
                owner="codex",
                release_reason="observed_terminal_state",
                now=_later(60),
            )

            self.assertEqual(refusal["reason_code"], REFUSAL_CONDITION_UNMET)
            self.assertEqual(refusal["recovery_action"], RECOVERY_EXPLICIT_RELEASE)

    def test_releasing_a_workspace_nobody_holds_is_refused_with_the_way_out(self) -> None:
        with TemporaryDirectory() as tmp:
            refusal = release_workspace_binding(
                _paths(Path(tmp)),
                repository_ref="rlaope/oh-my-hermes",
                workspace_path=Path(tmp) / "work",
                handoff_ref="handoff-alpha",
                owner="codex",
                release_reason="explicit_safe_release",
                now=_NOW,
            )

            self.assertEqual(refusal["reason_code"], REFUSAL_NOT_HELD)
            self.assertTrue(refusal["recovery_guidance"].strip())

    def test_releasing_twice_is_idempotent_and_not_a_refusal(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())
            release = {
                "repository_ref": "rlaope/oh-my-hermes",
                "workspace_path": workspace,
                "handoff_ref": "handoff-alpha",
                "owner": "codex",
                "release_reason": "observed_terminal_state",
            }
            release_workspace_binding(paths, now=_later(60), **release)  # type: ignore[arg-type]

            again = release_workspace_binding(paths, now=_later(120), **release)  # type: ignore[arg-type]

            self.assertEqual(again["outcome"], OUTCOME_ALREADY_RELEASED)
            self.assertEqual(again["reason_code"], "")
            self.assertEqual(len(read_workspace_bindings(paths)), 2)

    def test_a_release_never_rewrites_the_terms_it_ends(self) -> None:
        """The branch and revision come off the record, never off the caller."""
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())
            release_workspace_binding(
                paths,
                repository_ref="rlaope/oh-my-hermes",
                workspace_path=workspace,
                handoff_ref="handoff-alpha",
                owner="codex",
                release_reason="observed_terminal_state",
                now=_later(60),
            )

            held, released = read_workspace_bindings(paths)

            for field in ("branch_ref", "base_revision", "handoff_ref", "owner", "release_condition"):
                with self.subTest(field=field):
                    self.assertEqual(released[field], held[field])

    def test_a_held_record_cannot_name_why_it_ended(self) -> None:
        with TemporaryDirectory() as tmp:
            record = dict(_record(Path(tmp) / "work"))
            record["release_reason"] = "explicit_safe_release"

            errors = validate_workspace_binding(record)

            self.assertTrue(any("must be empty while the binding is held" in e for e in errors), errors)

    def test_a_released_record_must_name_why(self) -> None:
        with TemporaryDirectory() as tmp:
            record = dict(_record(Path(tmp) / "work"))
            record["state"] = "released"

            errors = validate_workspace_binding(record)

            self.assertTrue(any("must name why a released binding ended" in e for e in errors), errors)

    def test_a_release_reason_outside_the_stored_condition_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            record = dict(_record(Path(tmp) / "work"))
            record["state"] = "released"
            record["release_condition"] = "explicit_safe_release"
            record["release_reason"] = "observed_terminal_state"

            errors = validate_workspace_binding(record)

            self.assertTrue(any("does not satisfy release_condition" in e for e in errors), errors)


class ABindingIsNotDispatchEvidence(unittest.TestCase):
    """Existence, ownership, dispatch, and result stay four separate claims."""

    def test_the_claim_boundary_denies_all_three_other_claims_on_every_record(self) -> None:
        with TemporaryDirectory() as tmp:
            record = _record(Path(tmp) / "work")

            self.assertEqual(record["claim_boundary"], CLAIM_BOUNDARY)
            self.assertIn("not evidence that the workspace exists", CLAIM_BOUNDARY)
            self.assertIn("any executor was dispatched", CLAIM_BOUNDARY)
            self.assertIn("any work ran or finished", CLAIM_BOUNDARY)

    def test_the_decision_carries_the_same_boundary(self) -> None:
        with TemporaryDirectory() as tmp:
            decision = acquire_workspace_binding(
                _paths(Path(tmp)), workspace_path=Path(tmp) / "work", **_claim()
            )

            self.assertEqual(decision["claim_boundary"], CLAIM_BOUNDARY)

    def test_a_record_shaped_to_assert_execution_is_refused_by_key_name(self) -> None:
        with TemporaryDirectory() as tmp:
            for key in ("dispatched", "executed", "exit_code", "result", "observed"):
                record = dict(_record(Path(tmp) / "work"))
                record[key] = "yes"
                with self.subTest(key=key):
                    errors = validate_workspace_binding(record)
                    self.assertTrue(any("execution-claim keys" in error for error in errors), errors)

    def test_the_execution_claim_guard_matches_the_family_that_first_wrote_it(self) -> None:
        """Mirrored rather than imported privately; the pin is what stops drift."""
        self.assertEqual(EXECUTION_CLAIM_KEYS, APPROVAL_EXECUTION_CLAIM_KEYS)

    def test_no_stored_field_could_be_read_as_an_outcome(self) -> None:
        with TemporaryDirectory() as tmp:
            record = _record(Path(tmp) / "work")
            self.assertFalse(set(record) & EXECUTION_CLAIM_KEYS)
            self.assertEqual(record["privacy"], "metadata_only")

    def test_binding_a_workspace_never_asks_whether_it_exists(self) -> None:
        """A path that is not there binds exactly as well, and says nothing about it."""
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            absent = Path(tmp) / "not-created-by-anything"

            decision = acquire_workspace_binding(paths, workspace_path=absent, **_claim())

            self.assertFalse(absent.exists())
            self.assertTrue(decision["bound"])


class TheGuardCanBeAskedWithoutReserving(unittest.TestCase):
    """"Confirm the workspace is reserved, or explain who owns it", read-only."""

    def test_inspecting_a_free_workspace_reports_unbound_and_writes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))

            verdict = inspect_workspace_binding(
                paths,
                repository_ref="rlaope/oh-my-hermes",
                workspace_path=Path(tmp) / "work",
                branch_ref="agent/issue-820",
                base_revision="c7f9218b",
                handoff_ref="handoff-alpha",
                owner="codex",
                now=_NOW,
            )

            self.assertEqual(verdict["outcome"], OUTCOME_UNBOUND)
            self.assertEqual(verdict["reason_code"], "")
            self.assertFalse(paths.runtime_workspace_bindings_path.exists())

    def test_inspecting_a_held_workspace_names_the_owner_and_writes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            acquire_workspace_binding(paths, workspace_path=workspace, **_claim())

            verdict = inspect_workspace_binding(
                paths,
                repository_ref="rlaope/oh-my-hermes",
                workspace_path=workspace,
                branch_ref="agent/other",
                base_revision="c7f9218b",
                handoff_ref="handoff-beta",
                owner="claude-code",
                now=_later(60),
            )

            self.assertEqual(verdict["reason_code"], REFUSAL_WORKSPACE_BOUND)
            self.assertEqual(verdict["holder_owner"], "codex")
            self.assertEqual(len(read_workspace_bindings(paths)), 1)


class TheStoreIsRegisteredBesideItsSiblings(unittest.TestCase):
    """The wiring: without it nothing in production ever runs these validators."""

    def test_the_family_registers_its_validator_under_its_own_store(self) -> None:
        with TemporaryDirectory() as tmp:
            entries = {entry.store_name: entry for entry in OPTIONAL_RUNTIME_STORE_VALIDATORS}
            self.assertIn("workspace_bindings.jsonl", entries)
            entry = entries["workspace_bindings.jsonl"]
            # `record_id`, not the `receipt_id` two of its siblings carry, so the
            # label a fault is reported under is part of the registration.
            self.assertEqual(entry.record_id_key, "record_id")
            self.assertEqual(entry.validator(_record(Path(tmp) / "work")), [])
            self.assertTrue(entry.validator({"schema_version": "approval_receipt/v1"}))
            self.assertEqual(WORKSPACE_BINDING_RECORD_KEYS, WORKSPACE_BINDING_KEYS)

    def test_the_store_lives_beside_its_siblings_and_not_inside_a_run(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            self.assertEqual(
                paths.runtime_workspace_bindings_path.parent,
                paths.runtime_blocked_work_records_path.parent,
            )
            self.assertNotIn("runs", paths.runtime_workspace_bindings_path.parts)

    def test_the_store_report_reaches_omh_runtime_validate(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            store = paths.runtime_workspace_bindings_path
            store.parent.mkdir(parents=True, exist_ok=True)
            store.write_text(json.dumps({"schema_version": "nonsense"}) + "\n", encoding="utf-8")

            report = validate_runtime(paths)

            self.assertIn("workspace_bindings", report)
            self.assertFalse(report["workspace_bindings"]["ok"])
            self.assertFalse(report["ok"])

    def test_a_forked_supersede_chain_is_reported(self) -> None:
        """Two records claiming to be the current state of one directory."""
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            workspace = Path(tmp) / "work"
            first = _record(workspace)
            fork_a = _record(workspace, recorded_at=_later(1), supersedes_binding_ref=first["record_id"])
            fork_b = _record(workspace, recorded_at=_later(2), supersedes_binding_ref=first["record_id"])
            store = paths.runtime_workspace_bindings_path
            store.parent.mkdir(parents=True, exist_ok=True)
            store.write_text(
                "".join(json.dumps(record, sort_keys=True) + "\n" for record in (first, fork_a, fork_b)),
                encoding="utf-8",
            )

            report = validate_workspace_binding_store(store)

            self.assertFalse(report["ok"])
            self.assertTrue(any("forks the chain" in error for error in report["errors"]), report)
            self.assertEqual(report["binding_count"], 3)

    def test_a_clean_store_validates(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            acquire_workspace_binding(paths, workspace_path=Path(tmp) / "work", **_claim())

            report = validate_workspace_binding_store(paths.runtime_workspace_bindings_path)

            self.assertTrue(report["ok"], report["errors"])
            self.assertEqual(report["binding_count"], 1)
            self.assertEqual(report["mint_failure_count"], 0)

    def test_the_schema_version_is_pinned(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(_record(Path(tmp) / "work")["schema_version"], WORKSPACE_BINDING_SCHEMA_VERSION)
            self.assertEqual(WORKSPACE_BINDING_SCHEMA_VERSION, "workspace_binding_guard/v1")

    def test_the_vocabularies_are_closed(self) -> None:
        self.assertEqual(BINDING_STATES, ("held", "released"))
        self.assertEqual(RELEASE_CONDITIONS, ("observed_terminal_state", "explicit_safe_release"))


class TheWorkspaceBoundaryStaysHonestAboutWhatIsStillOpen(unittest.TestCase):
    """#820 closed the pre-dispatch half and could never close the other one."""

    def _workspace_boundary(self) -> dict[str, object]:
        envelope = build_task_authority_envelope(
            denied=False,
            delegation_action="delegate",
            intent="coding",
            review_required=False,
            work_owner_mode="external_executor",
            selected_executor_profile="codex",
            dispatchable=True,
            choice_required=False,
            isolation_plan={"strategy": "worktree_recommended"},
            message="refactor the login flow in src/login.py",
            safety_profile_revision="rev-frozen",
        )
        contract = build_task_handoff_safety_contract(envelope)
        return next(
            entry for entry in contract["boundaries"] if entry["boundary"] == "workspace"
        )

    def test_the_boundary_is_still_declared_not_enforced(self) -> None:
        """Nothing on this side of the wall can constrain a process already running."""
        boundary = self._workspace_boundary()

        self.assertEqual(boundary["enforcement"], "declared_not_enforced")
        self.assertEqual(boundary["enforced_by"], [])

    def test_the_blocker_is_a_reason_and_not_a_closed_issue(self) -> None:
        boundary = self._workspace_boundary()

        self.assertEqual(boundary["blocked_by"], WORKSPACE_RUNTIME_BLOCKER)
        self.assertNotIn("#", str(boundary["blocked_by"]))
        self.assertEqual(
            WORKSPACE_RUNTIME_BLOCKER, "no_omh_side_constraint_can_bind_a_running_executor_process"
        )

    def test_the_statement_names_the_half_that_now_exists_and_the_half_that_does_not(self) -> None:
        statement = str(self._workspace_boundary()["statement"])

        self.assertIn("workspace_binding_guard/v1", statement)
        self.assertIn("nothing binds a running executor", statement)
