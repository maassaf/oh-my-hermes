"""AC-SLOP: a code-changing goal cannot complete until an ai-slop-cleaner outcome is recorded.

Armed two ways only -- by code when a loop allowed `repo_edit` binds the goal,
and by `omh goal create --obligation slop-cleanup` -- and satisfied only by a
cited run record carrying the cleaner skill, this goal's id, a typed
`cleanup_outcome`, and evidence the completion-integrity classifier accepts.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.goal_ledger import (
    MERGE_OBLIGATION_CRITERION_IDS,
    SLOP_CLEANUP_CRITERION_ID,
    arm_slop_cleanup_obligation,
    build_goal_completion_gate,
    build_goal_status_card,
    cancel_goal_ledger,
    create_goal_ledger,
    goal_ledger_path,
    merge_obligation_criterion,
    read_goal_ledger,
    record_goal_checkpoint,
    slop_cleanup_obligation_criterion,
)
from omh.goal_loop import create_loop_cycle, loop_cycle_path, read_loop_cycle, update_loop_permission
from omh.paths import resolve_paths
from omh.record_revision import applied_mutation_key
from omh.runtime.records import CLEANUP_OUTCOMES, RUN_STATUSES, build_run_record, validate_run_record
from omh.runtime_artifacts import create_run
from omh.workflows.goal_journey import build_goal_journey
from omh.workflows.loop_executor_observations import LoopDriverError

_SLOP = SLOP_CLEANUP_CRITERION_ID
_OBSERVED_TEST_RUN = "observed: PYTHONPATH=tests uv run python -m unittest tests/test_example.py"


def _paths(tmp: str):
    return resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")


def _goal(paths, goal_id: str = "goal-code", **kwargs):
    return create_goal_ledger(paths, f"objective for {goal_id}", ["Behavior is verified"], goal_id=goal_id, **kwargs)


def _loop(paths, loop_id: str = "loop-code", **kwargs):
    return create_loop_cycle(
        paths,
        goal_summary="Improve fixture correctness",
        goal_reframe="Verify the bounded fixture",
        success_criteria=["Observed fixture verification passes"],
        allow_unloopable=True,
        loop_id=loop_id,
        **kwargs,
    )


def _criterion_ids(paths, goal_id: str) -> list[str]:
    return [str(item["id"]) for item in read_goal_ledger(paths, goal_id)["acceptance_criteria"]]


def _cleaner_run(paths, goal_id: str, **overrides) -> str:
    metadata = {
        "skill": "ai-slop-cleaner",
        "harness": "coding-handling",
        "status": "completed",
        "goal_id": goal_id,
        "cleanup_outcome": "passed",
        "outputs_summary": "changed files: src/example.py; removed one dead helper; remaining risks: none named",
        "verification_summary": "PYTHONPATH=tests uv run python -m unittest tests/test_example.py exited 0",
    }
    metadata.update(overrides)
    return str(create_run(paths, metadata)["run_id"])


def _satisfy_plain_criterion(paths, goal_id: str) -> None:
    record_goal_checkpoint(paths, goal_id, "behavior checked", criteria_refs=["AC001"], evidence_refs=[_OBSERVED_TEST_RUN])


def _cite(paths, goal_id: str, *refs: str, criteria: tuple[str, ...] = (_SLOP,)) -> None:
    record_goal_checkpoint(paths, goal_id, "cleanup outcome recorded", criteria_refs=list(criteria), evidence_refs=list(refs))


def _armed_goal(paths, goal_id: str = "goal-code") -> str:
    _goal(paths, goal_id)
    arm_slop_cleanup_obligation(paths, goal_id)
    _satisfy_plain_criterion(paths, goal_id)
    return goal_id


class LoopArmingTests(unittest.TestCase):
    def test_a_repo_edit_loop_arms_ac_slop_on_its_linked_goal(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)
            _loop(paths, permission_profile="execute_with_gates", linked_goal_id="goal-code")

            criteria = {item["id"]: item for item in read_goal_ledger(paths, "goal-code")["acceptance_criteria"]}
            self.assertIn(_SLOP, criteria)
            self.assertTrue(criteria[_SLOP]["required"])
            self.assertEqual(criteria[_SLOP]["status"], "pending")
            gate = build_goal_completion_gate(paths, "goal-code")
            self.assertFalse(gate["ready"])
            self.assertFalse(gate["slop_cleanup_check"]["satisfied"])

    def test_a_loop_without_repo_edit_leaves_the_linked_goal_alone(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)
            _loop(paths, permission_profile="handoff_only", linked_goal_id="goal-code")

            self.assertEqual(_criterion_ids(paths, "goal-code"), ["AC001"])
            self.assertNotIn("slop_cleanup_check", build_goal_completion_gate(paths, "goal-code"))

    def test_a_repo_edit_loop_with_no_linked_goal_touches_no_goal(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths, "goal-elsewhere")
            before = goal_ledger_path(paths, "goal-elsewhere").read_bytes()
            _loop(paths, permission_profile="full_loop")

            self.assertEqual(goal_ledger_path(paths, "goal-elsewhere").read_bytes(), before)

    def test_a_coding_delegation_goal_is_never_armed(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            create_goal_ledger(
                paths,
                "coding-delegation:merge:goal-merge",
                [merge_obligation_criterion("merge")],
                goal_id="goal-merge",
                source="coding_delegation",
            )
            _loop(paths, permission_profile="full_loop", linked_goal_id="goal-merge")

            self.assertEqual(_criterion_ids(paths, "goal-merge"), [MERGE_OBLIGATION_CRITERION_IDS["merge"]])
            self.assertNotIn("slop_cleanup_check", build_goal_completion_gate(paths, "goal-merge"))

    def test_a_permit_that_newly_grants_repo_edit_arms_the_linked_goal(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)
            _loop(paths, permission_profile="handoff_only", linked_goal_id="goal-code")
            update_loop_permission(paths, "loop-code", allow_actions=["repo_edit"])

            self.assertEqual(_criterion_ids(paths, "goal-code"), ["AC001", _SLOP])

    def test_a_permit_without_repo_edit_does_not_arm(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)
            _loop(paths, permission_profile="handoff_only", linked_goal_id="goal-code")
            update_loop_permission(paths, "loop-code", allow_actions=["pr_creation", "executor_dispatch"])

            self.assertEqual(_criterion_ids(paths, "goal-code"), ["AC001"])

    def test_a_permit_that_does_not_newly_grant_repo_edit_leaves_an_unarmed_goal_alone(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)
            _loop(paths, permission_profile="execute_with_gates", linked_goal_id="goal-code")
            # A goal bound before the obligation existed: the loop already had
            # repo_edit, so an unrelated later grant is not the moment to arm it.
            # Such a ledger carries neither the criterion nor the arming replay
            # entry, so both are stripped here.
            path = goal_ledger_path(paths, "goal-code")
            ledger = json.loads(path.read_text(encoding="utf-8"))
            ledger["acceptance_criteria"] = [item for item in ledger["acceptance_criteria"] if item["id"] != _SLOP]
            ledger["applied_mutations"] = {
                key: value
                for key, value in ledger.get("applied_mutations", {}).items()
                if key != applied_mutation_key("arm_slop_cleanup_obligation", "slop-cleanup-loop-code")
            }
            path.write_text(json.dumps(ledger), encoding="utf-8")

            update_loop_permission(paths, "loop-code", allow_actions=["merge"])

            self.assertEqual(_criterion_ids(paths, "goal-code"), ["AC001"])

    def test_re_arming_is_idempotent_on_the_criterion_id(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)
            _loop(paths, "loop-one", permission_profile="execute_with_gates", linked_goal_id="goal-code")
            _loop(paths, "loop-two", permission_profile="full_loop", linked_goal_id="goal-code")
            revision = read_goal_ledger(paths, "goal-code")["record_revision"]

            again = arm_slop_cleanup_obligation(paths, "goal-code")

            self.assertEqual(_criterion_ids(paths, "goal-code"), ["AC001", _SLOP])
            self.assertEqual((again["armed"], again["added"]), (True, False))
            self.assertEqual(read_goal_ledger(paths, "goal-code")["record_revision"], revision)

    def test_a_terminal_goal_refuses_arming_and_no_loop_is_written(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)
            cancel_goal_ledger(paths, "goal-code", reason="Superseded")

            with self.assertRaisesRegex(ValueError, "arm the slop-cleanup obligation"):
                _loop(paths, permission_profile="execute_with_gates", linked_goal_id="goal-code")

            self.assertFalse(loop_cycle_path(paths, "loop-code").exists())
            self.assertEqual(_criterion_ids(paths, "goal-code"), ["AC001"])

    def test_a_terminal_goal_refuses_a_repo_edit_grant(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)
            _loop(paths, permission_profile="handoff_only", linked_goal_id="goal-code")
            cancel_goal_ledger(paths, "goal-code", reason="Superseded")

            with self.assertRaisesRegex(ValueError, "arm the slop-cleanup obligation"):
                update_loop_permission(paths, "loop-code", allow_actions=["repo_edit"])

            self.assertNotIn("repo_edit", read_loop_cycle(paths, "loop-code")["authority_envelope"]["allowed_actions"])
            self.assertEqual(_criterion_ids(paths, "goal-code"), ["AC001"])

    def test_an_already_armed_terminal_goal_is_a_no_op_not_a_refusal(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)
            arm_slop_cleanup_obligation(paths, "goal-code")
            cancel_goal_ledger(paths, "goal-code", reason="Superseded")

            result = arm_slop_cleanup_obligation(paths, "goal-code")

            self.assertEqual((result["armed"], result["added"]), (True, False))

    def test_a_failed_cycle_create_leaves_no_stray_criterion(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)
            _loop(paths, permission_profile="handoff_only")

            with self.assertRaisesRegex(LoopDriverError, "loop_already_exists"):
                _loop(paths, permission_profile="execute_with_gates", linked_goal_id="goal-code")

            self.assertEqual(_criterion_ids(paths, "goal-code"), ["AC001"])


    def test_a_permit_requesting_a_forbidden_repo_edit_does_not_arm(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)
            _loop(paths, permission_profile="handoff_only", forbid_actions=["repo_edit"], linked_goal_id="goal-code")
            update_loop_permission(paths, "loop-code", allow_actions=["repo_edit"])

            self.assertNotIn("repo_edit", read_loop_cycle(paths, "loop-code")["authority_envelope"]["allowed_actions"])
            self.assertEqual(_criterion_ids(paths, "goal-code"), ["AC001"])

    def test_an_arm_that_fails_after_loop_start_is_repaired_by_requesting_repo_edit(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)
            with patch(
                "omh.workflows.goal_loop.arm_slop_cleanup_obligation",
                side_effect=TimeoutError("goal.json lock timed out"),
            ):
                with self.assertRaises(TimeoutError):
                    _loop(paths, permission_profile="execute_with_gates", linked_goal_id="goal-code")

            # The loop was written with repo_edit and its goal was not armed.
            self.assertIn("repo_edit", read_loop_cycle(paths, "loop-code")["authority_envelope"]["allowed_actions"])
            self.assertEqual(_criterion_ids(paths, "goal-code"), ["AC001"])

            update_loop_permission(paths, "loop-code", allow_actions=["repo_edit"])

            self.assertEqual(_criterion_ids(paths, "goal-code"), ["AC001", _SLOP])


class DeclaredObligationTests(unittest.TestCase):
    def _create(self, tmp: str, *extra: str) -> dict:
        status, stdout, stderr = run_cli(
            ["--omh-home", str(Path(tmp) / ".omh"), "--hermes-home", str(Path(tmp) / ".hermes"),
             "goal", "create", "--goal-id", "goal-ulw", "--objective", "Ship the lane",
             "--criterion", "Behavior is verified", *extra]
        )
        self.assertEqual(status, 0, stderr)
        return json.loads(stdout)

    def test_the_slop_cleanup_flag_arms_ac_slop_at_creation(self) -> None:
        with TemporaryDirectory() as tmp:
            payload = self._create(tmp, "--obligation", "slop-cleanup")

            self.assertEqual([item["id"] for item in payload["goal"]["acceptance_criteria"]], ["AC001", _SLOP])
            self.assertFalse(payload["completion_gate"]["slop_cleanup_check"]["satisfied"])

    def test_a_goal_created_without_the_flag_carries_no_obligation(self) -> None:
        with TemporaryDirectory() as tmp:
            payload = self._create(tmp)

            self.assertEqual([item["id"] for item in payload["goal"]["acceptance_criteria"]], ["AC001"])
            self.assertNotIn("slop_cleanup_check", payload["completion_gate"])

    def test_only_the_closed_obligation_set_is_accepted(self) -> None:
        self.assertEqual(slop_cleanup_obligation_criterion()["id"], _SLOP)
        with self.assertRaises(ValueError):
            slop_cleanup_obligation_criterion("merge")


class CleanupRecordCheckTests(unittest.TestCase):
    def _check(self, paths, goal_id: str = "goal-code") -> dict:
        return build_goal_completion_gate(paths, goal_id)["slop_cleanup_check"]

    def _refusal(self, paths, goal_id: str = "goal-code") -> list[str]:
        return [record["refusal"] for record in self._check(paths, goal_id)["records"]]

    def test_a_missing_record_refuses_completion(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)

            gate = build_goal_completion_gate(paths, "goal-code")

            self.assertFalse(gate["ready"])
            self.assertEqual([item["id"] for item in gate["missing_required_criteria"]], [_SLOP])
            self.assertIn("no_cleanup_record_cited", gate["slop_cleanup_check"]["summary"])

    def test_satisfying_the_criterion_without_a_run_ref_still_refuses(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)
            _cite(paths, "goal-code", "observed: cleaner was skipped by the user")

            gate = build_goal_completion_gate(paths, "goal-code")

            self.assertEqual(gate["missing_required_criteria"], [])
            self.assertFalse(gate["ready"])
            self.assertEqual(gate["slop_cleanup_check"]["records"], [])
            self.assertEqual(gate["next_action"], "record_checkpoint")

    def test_each_outcome_satisfies_and_is_reported_verbatim(self) -> None:
        for outcome in CLEANUP_OUTCOMES:
            with self.subTest(outcome=outcome), TemporaryDirectory() as tmp:
                paths = _paths(tmp)
                _armed_goal(paths)
                run_id = _cleaner_run(paths, "goal-code", cleanup_outcome=outcome)
                _cite(paths, "goal-code", f"run:{run_id}")

                gate = build_goal_completion_gate(paths, "goal-code")
                check = gate["slop_cleanup_check"]

                self.assertTrue(gate["ready"], gate["summary"])
                self.assertEqual((check["cleanup_outcome"], check["run_id"]), (outcome, run_id))
                self.assertTrue(check["summary"].endswith(f"with cleanup outcome {outcome}."))
                if outcome != "passed":
                    self.assertNotIn("passed", check["summary"])

    def test_a_cited_run_that_does_not_exist_refuses(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)
            _cite(paths, "goal-code", "run:no-such-run")

            self.assertEqual(self._refusal(paths), ["run_record_not_found"])
            self.assertFalse(build_goal_completion_gate(paths, "goal-code")["ready"])

    def test_a_path_shaped_run_ref_is_not_followed(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)
            # A record that would pass every check, planted outside the runs
            # directory: only the storage-id check keeps `..` from reaching it.
            run_id = _cleaner_run(paths, "goal-code")
            planted = paths.runtime_runs_dir.parent / "planted"
            planted.mkdir()
            (planted / "run.json").write_bytes((paths.runtime_runs_dir / run_id / "run.json").read_bytes())
            _cite(paths, "goal-code", "run:../planted")

            self.assertEqual(self._refusal(paths), ["run_record_not_found"])

    def test_an_unreadable_run_record_refuses(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)
            run_id = _cleaner_run(paths, "goal-code")
            (paths.runtime_runs_dir / run_id / "run.json").write_text("{not json", encoding="utf-8")
            _cite(paths, "goal-code", f"run:{run_id}")

            self.assertEqual(self._refusal(paths), ["run_record_unreadable"])

    def test_a_record_from_another_skill_refuses(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)
            run_id = _cleaner_run(paths, "goal-code", skill="code-review")
            _cite(paths, "goal-code", f"run:{run_id}")

            self.assertEqual(self._refusal(paths), ["skill_mismatch"])
            self.assertFalse(build_goal_completion_gate(paths, "goal-code")["ready"])

    def test_a_record_for_a_different_goal_refuses(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)
            _goal(paths, "goal-other")
            run_id = _cleaner_run(paths, "goal-other")
            _cite(paths, "goal-code", f"run:{run_id}")

            self.assertEqual(self._refusal(paths), ["goal_mismatch"])
            self.assertFalse(build_goal_completion_gate(paths, "goal-code")["ready"])

    def test_a_record_without_a_typed_outcome_refuses(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)
            # The summary says "skipped"; only the typed field counts.
            run_id = _cleaner_run(paths, "goal-code", cleanup_outcome="", outputs_summary="skipped by the user")
            _cite(paths, "goal-code", f"run:{run_id}")

            self.assertEqual(self._refusal(paths), ["cleanup_outcome_missing"])
            self.assertFalse(build_goal_completion_gate(paths, "goal-code")["ready"])

    def test_a_record_with_no_evidence_refuses(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)
            run_id = _cleaner_run(paths, "goal-code", outputs_summary="", verification_summary="")
            _cite(paths, "goal-code", f"run:{run_id}")

            self.assertEqual(self._refusal(paths), ["evidence_empty"])
            self.assertFalse(build_goal_completion_gate(paths, "goal-code")["ready"])

    def test_a_placeholder_record_is_refused_by_integrity(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)
            run_id = _cleaner_run(paths, "goal-code", outputs_summary="TBD", verification_summary="")
            _cite(paths, "goal-code", f"run:{run_id}")

            self.assertEqual(self._refusal(paths), ["evidence_refused"])
            self.assertFalse(build_goal_completion_gate(paths, "goal-code")["ready"])

    def test_a_run_cited_for_another_criterion_does_not_satisfy_ac_slop(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)
            run_id = _cleaner_run(paths, "goal-code")
            _cite(paths, "goal-code", f"run:{run_id}", criteria=("AC001",))

            self.assertFalse(self._check(paths)["satisfied"])

    def test_the_status_card_names_the_cleanup_step_when_only_the_record_is_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)
            _cite(paths, "goal-code", "run:no-such-run")

            card = build_goal_status_card(paths, "goal-code")

            self.assertIn("--cleanup-outcome passed|skipped|reverted", card["safe_copy"]["next_step"])


    def test_a_run_that_did_not_complete_refuses_whatever_its_outcome(self) -> None:
        for status in RUN_STATUSES:
            if status == "completed":
                continue
            with self.subTest(status=status), TemporaryDirectory() as tmp:
                paths = _paths(tmp)
                _armed_goal(paths)
                run_id = _cleaner_run(paths, "goal-code", status=status, cleanup_outcome="passed")
                _cite(paths, "goal-code", f"run:{run_id}")

                self.assertEqual(self._refusal(paths), ["run_status_not_completed"])
                self.assertFalse(build_goal_completion_gate(paths, "goal-code")["ready"])

    def test_a_run_cited_by_a_checkpoint_that_is_not_done_does_not_count(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)
            run_id = _cleaner_run(paths, "goal-code")
            record_goal_checkpoint(
                paths, "goal-code", "cleaner blocked", status="blocked",
                criteria_refs=[_SLOP], evidence_refs=[f"run:{run_id}"],
            )
            _cite(paths, "goal-code", "observed: cleaner outcome noted outside the run record")

            gate = build_goal_completion_gate(paths, "goal-code")

            self.assertEqual(gate["missing_required_criteria"], [])
            self.assertEqual(gate["slop_cleanup_check"]["records"], [])
            self.assertFalse(gate["ready"])


class RunRecordFieldTests(unittest.TestCase):
    def _base(self, tmp: str) -> list[str]:
        return ["--omh-home", str(Path(tmp) / ".omh"), "--hermes-home", str(Path(tmp) / ".hermes")]

    def test_runtime_record_persists_the_goal_link_and_typed_outcome(self) -> None:
        with TemporaryDirectory() as tmp:
            _goal(_paths(tmp))
            status, stdout, stderr = run_cli(
                self._base(tmp)
                + ["runtime", "record", "--skill", "ai-slop-cleaner", "--harness", "coding-handling",
                   "--status", "completed", "--goal", "goal-code", "--cleanup-outcome", "skipped",
                   "--outputs-summary", "skipped: the cycle reported no changed_files"]
            )

            self.assertEqual(status, 0, stderr)
            run = json.loads(stdout)["run"]
            self.assertEqual((run["goal_id"], run["cleanup_outcome"], run["status"]), ("goal-code", "skipped", "completed"))
            self.assertEqual(RUN_STATUSES, ("started", "prepared", "completed", "blocked", "failed", "cancelled", "unknown"))

    def test_runtime_record_refuses_a_goal_that_does_not_exist(self) -> None:
        with TemporaryDirectory() as tmp:
            status, _stdout, stderr = run_cli(
                self._base(tmp)
                + ["runtime", "record", "--skill", "ai-slop-cleaner", "--harness", "coding-handling",
                   "--goal", "goal-missing", "--cleanup-outcome", "passed"]
            )

            self.assertNotEqual(status, 0)
            self.assertIn("--goal", stderr)

    def test_a_record_without_the_flags_keeps_its_shape(self) -> None:
        run = build_run_record({"skill": "x", "harness": "y", "status": "completed"}, "run-1")

        self.assertNotIn("goal_id", run)
        self.assertNotIn("cleanup_outcome", run)

    def test_an_unknown_cleanup_outcome_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_run_record({"skill": "x", "harness": "y", "cleanup_outcome": "clean"}, "run-1")
        run = build_run_record({"skill": "x", "harness": "y", "cleanup_outcome": "passed"}, "run-1")
        run["cleanup_outcome"] = "clean"
        self.assertIn("run cleanup_outcome is invalid: 'clean'", validate_run_record(run))

    def test_a_stored_record_with_an_empty_goal_link_is_invalid(self) -> None:
        run = build_run_record({"skill": "x", "harness": "y", "goal_id": "goal-code"}, "run-1")
        self.assertEqual(validate_run_record(run), [])
        run["goal_id"] = " "
        self.assertIn("run goal_id must be a non-empty string", validate_run_record(run))


class CompletionRefusalTests(unittest.TestCase):
    def test_goal_complete_names_ac_slop_and_exits_non_zero_until_the_record_is_cited(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            base = ["--omh-home", str(Path(tmp) / ".omh"), "--hermes-home", str(Path(tmp) / ".hermes")]
            _armed_goal(paths)
            complete = base + ["goal", "complete", "--goal", "goal-code", "--evidence-ref", _OBSERVED_TEST_RUN]

            status, stdout, _stderr = run_cli(complete)

            self.assertEqual(status, 1)
            refused = json.loads(stdout)
            self.assertFalse(refused["completed"])
            gate = refused["completion_gate"]
            self.assertIn(_SLOP, gate["summary"])
            self.assertIn("--cleanup-outcome passed|skipped|reverted", gate["slop_cleanup_check"]["next_step"])
            self.assertIn("decide to skip it", gate["slop_cleanup_check"]["next_step"])
            self.assertEqual(read_goal_ledger(paths, "goal-code")["status"], "active")

            run_id = _cleaner_run(paths, "goal-code", cleanup_outcome="reverted")
            _cite(paths, "goal-code", f"run:{run_id}")
            status, stdout, stderr = run_cli(complete)

            self.assertEqual(status, 0, stderr)
            done = json.loads(stdout)
            self.assertTrue(done["completed"])
            self.assertEqual(done["completion_gate"]["slop_cleanup_check"]["cleanup_outcome"], "reverted")


class GoalJourneyMirrorTests(unittest.TestCase):
    def test_the_journey_carries_the_cleanup_record_as_its_own_gate(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _armed_goal(paths)

            gates = {item["gate_id"]: item for item in build_goal_journey(paths, "goal-code")["required_gates"]}
            self.assertEqual(gates[f"slop_cleanup:{_SLOP}"]["kind"], "slop_cleanup_record")
            self.assertFalse(gates[f"slop_cleanup:{_SLOP}"]["evidence_accepted"])

            run_id = _cleaner_run(paths, "goal-code", cleanup_outcome="skipped")
            _cite(paths, "goal-code", f"run:{run_id}")
            journey = build_goal_journey(paths, "goal-code")
            gate = {item["gate_id"]: item for item in journey["required_gates"]}[f"slop_cleanup:{_SLOP}"]

            self.assertTrue(gate["evidence_accepted"])
            self.assertEqual(gate["evidence_refs"], [f"run:{run_id}"])
            self.assertTrue(journey["completion"]["ready"])

    def test_a_goal_without_the_obligation_has_no_cleanup_gate(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _goal(paths)

            kinds = {item["kind"] for item in build_goal_journey(paths, "goal-code")["required_gates"]}
            self.assertNotIn("slop_cleanup_record", kinds)


class SkillTextTests(unittest.TestCase):
    def test_the_cleaner_loop_and_ultrawork_bodies_name_the_record_and_cite_commands(self) -> None:
        from omh.skills.packaging import builtin_skill_templates

        bodies = {template.name: template.content for template in builtin_skill_templates()}
        cite = "omh goal checkpoint --goal <goal_id> --summary <outcome> --criterion AC-SLOP --evidence-ref run:<run_id>"
        for name in ("ai-slop-cleaner", "loop", "ultrawork"):
            with self.subTest(skill=name):
                self.assertIn("--cleanup-outcome passed|skipped|reverted", bodies[name])
        for name in ("ai-slop-cleaner", "loop"):
            with self.subTest(skill=name):
                self.assertIn(cite, bodies[name])
        # ultrawork defers the exact commands to the cleaner's closing line,
        # which keeps its shipped body under the byte ceiling.
        self.assertIn("omh goal create --obligation slop-cleanup", bodies["ultrawork"])
        self.assertIn("recorded and cited with the commands that skill closes on", bodies["ultrawork"])
        self.assertIn("Without the flag the goal is not gated.", bodies["ultrawork"])
        self.assertIn("--goal <linked goal id> --outputs-summary", bodies["loop"])
        self.assertNotIn("an optional `ai-slop-cleaner` pass", bodies["loop"])


if __name__ == "__main__":
    unittest.main()
