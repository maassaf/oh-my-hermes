"""`omh_team`: the checked delegate lane (T1-T12) and the `omh_agent_board` golden pin.

Each class names the contract from the teammate-lane critique it pins and the
mutation that must turn it red. The engine is driven with a fake runner and a
fake working-tree fingerprint, so no test here spawns a process; the one real
spawn path (`evidence_tool.run_verification_command`) is exercised by its own
tests and by the handler test below through a patched runner.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from _hermes_session_rows import (
    compacted,
    row as hermes_row,
    within_seconds,
    write_sessions,
    write_unreadable_state_db,
)
from _local_package import load_local_package
from _module_patch import patch_modules

load_local_package()

from omh.plugin_bundle.omh import team_gate  # noqa: E402
from omh.plugin_bundle.omh import host_process  # noqa: E402
from omh.plugin_bundle.omh.live_session import compaction_ancestors  # noqa: E402
from omh.plugin_bundle.omh.hooks import nudge_budget  # noqa: E402
from omh.plugin_bundle.omh.todo_store import todo_items_digest  # noqa: E402
from omh.workflows import team  # noqa: E402

CHECK_A = "python -m unittest tests/test_a.py"
CHECK_B = "python -m unittest tests/test_b.py"
CHECK_C = "python -m unittest tests/test_c.py"
SESSION = "20260928_120000_team01"
# Subagent ids in the shape Hermes hands `subagent_start`, and the branches its
# worktree isolation names after them (`tools/subagent_worktree.py`).
SUBAGENT_A = "sa-0-aaaa1111"
SUBAGENT_B = "sa-1-bbbb2222"
BRANCH_A = "hermes-subagent/subagent-sa-0-aaaa1111"
BRANCH_B = "hermes-subagent/subagent-sa-1-bbbb2222"
GIT = shutil.which("git")


def plan_for(*commands: str, stage: str = "accepted", own: bool = True) -> dict[str, object]:
    items = [{"text": f"Part {index}: check: `{command}`", "state": "pending"}
             for index, command in enumerate(commands, start=1)]
    plan: dict[str, object] = {"own_record": own, "status": "established", "items": items}
    if stage:
        plan["plan_stage"] = stage
    plan["items_digest"] = todo_items_digest(items)
    return plan


def unit(unit_id: str, command: str, depends_on: list[str] | None = None) -> dict[str, object]:
    return {"unit_id": unit_id, "title": f"Part {unit_id}", "verification_command": command,
            "depends_on": depends_on or []}


class FakeRunner:
    """Exit codes per command, consumed in order; the last one repeats."""

    def __init__(self, script: dict[str, list[team.CheckRun]]) -> None:
        self.script = {command: list(runs) for command, runs in script.items()}
        self.calls: list[str] = []
        self.during: Callable[[], None] | None = None

    def __call__(self, tokens: list[str], workdir: Path, timeout: int) -> team.CheckRun:
        command = " ".join(tokens)
        self.calls.append(command)
        if self.during is not None:
            hook, self.during = self.during, None
            hook()
        runs = self.script[command]
        return runs.pop(0) if len(runs) > 1 else runs[0]


def exited(code: int) -> team.CheckRun:
    return team.CheckRun(outcome="exited", exit_code=code, output_tail=f"exit {code}")


class TeamHarness(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "omh"
        self.workdir = Path(temporary.name) / "work"
        self.workdir.mkdir()
        self.clock = 1_790_000_000.0
        self.runner = FakeRunner({CHECK_A: [exited(0)], CHECK_B: [exited(0)], CHECK_C: [exited(0)]})
        self.fingerprints: list[tuple[str, str | None]] = []
        # Process memory (`TeamContext`): one set pair per simulated Hermes process.
        self.approved: set[tuple[str, str, str]] = set()
        self.observed: set[str] = set()
        self.host_refuses: dict[str, str] = {}
        # What `helper_work` answers per helper branch; a branch not listed is absent.
        self.helper_states: dict[str, str] = {}
        self.helper_reads: list[str] = []
        # What `dispatcher_alive` answers per recorded pid; a pid not listed cannot be told (None).
        self.dispatchers: dict[int, bool | None] = {}

    def dispatcher_alive(self, host: dict[str, object]) -> bool | None:
        return self.dispatchers.get(int(host["pid"]))

    def now(self) -> float:
        self.clock += 1.0
        return self.clock

    def fingerprint(self, workdir: Path) -> tuple[str, str | None]:
        return self.fingerprints.pop(0) if self.fingerprints else ("clean", "tree-1")

    def host_guard(self, command: str) -> str | None:
        return self.host_refuses.get(command)

    def helper_work(self, workdir: Path, branch: str) -> str:
        self.helper_reads.append(branch)
        return self.helper_states.get(branch, "absent")

    def restart(self) -> None:
        """A new Hermes process: nothing approved, no check observed."""
        self.approved = set()
        self.observed = set()

    @property
    def ctx(self) -> team.TeamContext:
        return team.TeamContext(omh_home=self.home, session_ref=SESSION, runner=self.runner,
                                fingerprint=self.fingerprint, now=self.now, host_guard=self.host_guard,
                                helper_work=self.helper_work, approved=self.approved, observed_checks=self.observed,
                                dispatcher_alive=self.dispatcher_alive)

    def start(self, units: list[dict[str, object]], *, plan: dict[str, object] | None = None,
              **extra: object) -> dict[str, object]:
        commands = [str(item["verification_command"]) for item in units]
        plan = plan if plan is not None else plan_for(*commands)
        result = team.team_start(self.ctx, team_id="t1", units=units, plan=plan,
                                 plan_ref=extra.pop("plan_ref", plan["items_digest"]),
                                 workdir=self.workdir, **extra)
        # What the handler does once the host's approval gate let the call through.
        self.approved.add((SESSION, "t1", result["commands_digest"]))
        return result

    def dispatch_all(self, result: dict[str, object], subagent_ids: list[str] | None = None) -> list[str]:
        """Play the host: start every returned helper, then report each back."""
        children = []
        for index, entry in enumerate(self.entries(result)):
            child = f"child-{self.clock}-{index}"
            subagent_id = subagent_ids[index] if subagent_ids else None
            self.assertTrue(team.observe_dispatch(self.home, SESSION, child_session_id=child,
                                                  goal=entry["goal"], now=self.now(), subagent_id=subagent_id))
            children.append(child)
        for child in children:
            self.assertTrue(team.observe_return(self.home, SESSION, child_session_id=child, now=self.now()))
        return children

    @staticmethod
    def entries(result: dict[str, object]) -> list[dict[str, str]]:
        prepared = result.get("delegate_task")
        return list(prepared["arguments"]["tasks"]) if isinstance(prepared, dict) else []

    def reconcile(self) -> dict[str, object]:
        return team.team_reconcile(self.ctx, team_id="t1")

    def record(self) -> dict[str, object]:
        record = team.read_team(team.team_path(self.home, SESSION, "t1"))
        assert record is not None
        return record

    def states(self, result: dict[str, object]) -> dict[str, str]:
        return {view["unit_id"]: view["state"] for view in result["units"]}


class T1AcceptedOnlyOnObservedExitZero(TeamHarness):
    """Mutation: treat a helper's return (the host's `subagent_stop`) as acceptance."""

    def test_a_returned_helper_is_awaiting_check_not_accepted(self) -> None:
        started = self.start([unit("a", CHECK_A)])
        self.dispatch_all(started)
        status = team.team_status(self.ctx, team_id="t1")
        self.assertEqual(self.states(status), {"a": "awaiting_check"})
        self.assertEqual(status["team_state"], "running")
        self.assertEqual(self.runner.calls, [])

    def test_exit_zero_observed_by_omh_accepts_and_names_the_evidence(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        result = self.reconcile()
        self.assertEqual(self.runner.calls, [CHECK_A])
        self.assertEqual(self.states(result), {"a": "accepted"})
        self.assertEqual(result["team_state"], "done")
        self.assertEqual(result["units"][0]["evidence"], {"kind": "team_check", "ref": "t1/a/attempt-1/check"})
        self.assertEqual(result["say"], "The one part passed its check.")

    def test_non_zero_exit_is_not_acceptance(self) -> None:
        self.runner = FakeRunner({CHECK_A: [exited(1)]})
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        result = self.reconcile()
        self.assertNotEqual(self.states(result)["a"], "accepted")
        self.assertNotIn("evidence", result["units"][0])


class T2DownstreamWaitsForEveryParent(TeamHarness):
    """Mutation: `all` -> `any` in `_ready`, or release a child on a parent's return."""

    def test_child_waits_until_all_parents_are_accepted(self) -> None:
        self.runner = FakeRunner({CHECK_A: [exited(0)], CHECK_B: [exited(1), exited(0)], CHECK_C: [exited(0)]})
        started = self.start([unit("a", CHECK_A), unit("b", CHECK_B), unit("c", CHECK_C, ["a", "b"])])
        self.assertEqual([entry["goal"].rsplit(" ", 1)[1] for entry in self.entries(started)],
                         ["[omh-team:t1/a/attempt-1]", "[omh-team:t1/b/attempt-1]"])
        self.dispatch_all(started)
        first = self.reconcile()
        self.assertEqual(self.states(first), {"a": "accepted", "b": "repairing", "c": "waiting"})
        self.assertEqual([entry["goal"].rsplit(" ", 1)[1] for entry in self.entries(first)],
                         ["[omh-team:t1/b/attempt-2]"])
        self.dispatch_all(first)
        second = self.reconcile()
        self.assertEqual(self.states(second)["b"], "accepted")
        self.assertEqual([entry["goal"].rsplit(" ", 1)[1] for entry in self.entries(second)],
                         ["[omh-team:t1/c/attempt-1]"])

    def test_a_returned_but_unchecked_parent_releases_nothing(self) -> None:
        started = self.start([unit("a", CHECK_A), unit("c", CHECK_C, ["a"])])
        self.dispatch_all(started)
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1"))["c"], "waiting")


class T3AttemptBudget(TeamHarness):
    """Mutation: `<` -> `<=` on the attempt limit, or drop the cap."""

    def test_default_is_two_repairs_then_blocked_with_the_last_check(self) -> None:
        self.runner = FakeRunner({CHECK_A: [exited(3)]})
        result = self.start([unit("a", CHECK_A)])
        for _ in range(3):
            self.dispatch_all(result)
            result = self.reconcile()
        self.assertEqual(self.states(result), {"a": "blocked"})
        self.assertEqual(result["team_state"], "blocked")
        view = result["units"][0]
        self.assertEqual((view["attempts_used"], view["attempts_max"]), (3, 3))
        self.assertEqual(view["blocked"]["reason"], "repair_budget_exhausted")
        self.assertEqual(set(view["blocked"]["last_check"]), {"command", "exit_code", "observed_at"})
        self.assertEqual(view["blocked"]["last_check"]["exit_code"], 3)
        self.assertIsNone(result["delegate_task"])
        self.assertIn("still fails its check after 3 tries", result["say"])
        self.assertEqual(len(self.runner.calls), 3)

    def test_the_cap_is_three_repairs(self) -> None:
        with self.assertRaises(team.TeamRefusal) as refused:
            self.start([unit("a", CHECK_A)], max_repair_attempts=4)
        self.assertEqual(refused.exception.reason, "invalid_max_repair_attempts")
        started = self.start([unit("a", CHECK_A)], max_repair_attempts=3)
        self.assertEqual(started["units"][0]["attempts_max"], 4)


class T4RepairKeyCarriesTheAttempt(TeamHarness):
    """Mutation: drop the attempt number from the key, so a repair reuses the original."""

    def test_repair_entry_is_a_new_deterministic_attempt(self) -> None:
        self.runner = FakeRunner({CHECK_A: [exited(1), exited(0)]})
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        repair = self.entries(self.reconcile())
        self.assertEqual(len(repair), 1)
        # The key ends the goal, so Hermes' 40-character previews show the part's title first.
        self.assertEqual(repair[0]["goal"], "Part a [omh-team:t1/a/attempt-2]")
        self.assertIn('{"command": "python -m unittest tests/test_a.py", "exit_code": 1}', repair[0]["context"])
        self.assertNotIn("exit 1", repair[0]["context"])  # the output tail never travels
        # The original attempt's marker no longer binds: it is not the reserved attempt.
        self.assertFalse(team.observe_dispatch(self.home, SESSION, child_session_id="late",
                                               goal="Part a [omh-team:t1/a/attempt-1]", now=self.now()))
        self.assertEqual(team.attempt_key("t1", "a", 2), "t1/a/attempt-2")


class T5DoubleReconcileChecksOnce(TeamHarness):
    """Mutation: remove the lease, so a concurrent reconcile runs the check again."""

    def test_a_reconcile_during_a_running_check_skips_the_leased_unit(self) -> None:
        self.runner = FakeRunner({CHECK_A: [exited(1)]})
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        nested: list[dict[str, object]] = []
        self.runner.during = lambda: nested.append(self.reconcile())
        self.reconcile()
        self.assertEqual(self.runner.calls, [CHECK_A])
        self.assertEqual(nested[0]["reason"], "check_in_progress")
        self.assertEqual(len(self.record()["units"][0]["attempts"]), 2)

    def test_an_expired_lease_reruns_without_spending_an_attempt(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        path = team.team_path(self.home, SESSION, "t1")
        record = self.record()
        record["units"][0]["lease"] = {"attempt": 1, "nonce": "dead", "started_at": team._iso(self.clock - 3600)}
        team.write_team(path, record)
        result = self.reconcile()
        self.assertEqual(self.states(result), {"a": "accepted"})
        self.assertEqual(result["units"][0]["attempts_used"], 1)


class T6InconclusiveSpendsNothing(TeamHarness):
    """Mutation: treat a check that never ran (or ran on a moving tree) as a failure."""

    def run_one(self, run: team.CheckRun) -> dict[str, object]:
        self.runner = FakeRunner({CHECK_A: [run]})
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        return self.reconcile()

    def test_timeout_and_not_found_spend_no_attempt(self) -> None:
        for run, reason in ((team.CheckRun("timeout", None, ""), "check_timeout"),
                            (team.CheckRun("not_found", None, ""), "command_not_found")):
            with self.subTest(reason=reason):
                self.setUp()
                result = self.run_one(run)
                view = result["units"][0]
                self.assertEqual((view["state"], view["attempts_used"]), ("awaiting_check", 1))
                self.assertIsNone(result["delegate_task"])
                self.assertEqual(self.record()["units"][0]["attempts"][0]["inconclusive"][0]["reason"], reason)

    def test_missing_workspace_is_inconclusive(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        self.workdir.rmdir()
        result = self.reconcile()
        self.assertEqual(result["units"][0]["attempts_used"], 1)
        self.assertEqual(self.runner.calls, [])

    def test_a_tree_that_moved_during_the_check_is_inconclusive(self) -> None:
        self.runner = FakeRunner({CHECK_A: [exited(1)]})
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        self.fingerprints = [("dirty", "before"), ("dirty", "after")]
        result = self.reconcile()
        self.assertEqual(result["units"][0]["attempts_used"], 1)
        self.assertEqual(self.record()["units"][0]["attempts"][0]["inconclusive"][0]["reason"],
                         "workspace_changed_during_check")

    def test_three_inconclusive_checks_block_the_unit(self) -> None:
        self.runner = FakeRunner({CHECK_A: [team.CheckRun("timeout", None, "")]})
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        for _ in range(3):
            result = self.reconcile()
        self.assertEqual(result["units"][0]["blocked"]["reason"], "check_inconclusive")
        self.assertEqual(result["team_state"], "blocked")


class T7CommandsAreFrozen(TeamHarness):
    """Mutation: drop the frozen-digest checks on restart and on reconcile."""

    def test_restart_with_a_changed_command_is_refused(self) -> None:
        self.start([unit("a", CHECK_A)])
        changed = plan_for(CHECK_B)
        with self.assertRaises(team.TeamRefusal) as refused:
            team.team_start(self.ctx, team_id="t1", units=[unit("a", CHECK_B)], plan=changed,
                            plan_ref=changed["items_digest"], workdir=self.workdir)
        self.assertEqual(refused.exception.reason, "commands_frozen")

    def test_a_command_edited_in_the_record_runs_nothing(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        record = self.record()
        record["units"][0]["verification_command"] = "python -m unittest tests/test_other.py"
        team.write_team(team.team_path(self.home, SESSION, "t1"), record)
        with self.assertRaises(team.TeamRefusal) as refused:
            self.reconcile()
        self.assertEqual(refused.exception.reason, "frozen_commands_changed")
        self.assertEqual(self.runner.calls, [])


class T8ApprovalBinding(TeamHarness):
    """Mutation: remove any one predicate of the plan binding or the command policy."""

    def refused(self, **kwargs: object) -> str:
        with self.assertRaises(team.TeamRefusal) as refused:
            self.start(**kwargs)
        return refused.exception.reason

    def test_command_must_be_verbatim_in_the_accepted_plan(self) -> None:
        self.assertEqual(self.refused(units=[unit("a", CHECK_A)], plan=plan_for(CHECK_B)),
                         "command_not_in_accepted_plan")

    def test_plan_must_be_accepted_owned_and_the_same_plan(self) -> None:
        self.assertEqual(self.refused(units=[unit("a", CHECK_A)], plan=plan_for(CHECK_A, stage="")),
                         "plan_not_accepted")
        self.assertEqual(self.refused(units=[unit("a", CHECK_A)],
                                      plan=plan_for(CHECK_A, stage="awaiting_acceptance")), "plan_not_accepted")
        self.assertEqual(self.refused(units=[unit("a", CHECK_A)], plan=plan_for(CHECK_A, own=False)),
                         "plan_not_found")
        self.assertEqual(self.refused(units=[unit("a", CHECK_A)], plan_ref="0" * 32), "plan_ref_mismatch")

    def test_command_policy_refuses_forge_remote_git_shells_and_inline_python(self) -> None:
        cases = {
            "gh pr merge 12": "command_talks_to_a_forge",
            "uv run gh api repos": "command_talks_to_a_forge",
            "git push origin main": "command_moves_a_remote",
            "git -C repo pull": "command_moves_a_remote",
            "bash scripts/test.sh": "command_runs_a_shell",
            "/bin/sh -x run.sh": "command_runs_a_shell",
            "env FOO=1 pytest": "command_runs_a_shell",
            "python -c print": "command_inline_program",
            "uv run python3 -c print": "command_inline_program",
            "pytest && rm -rf x": "command_shell_syntax",
            "pytest | tee log": "command_shell_syntax",
            "xargs -a units.txt pytest": "command_runs_a_shell",
            "uv run xargs -n1 pytest": "command_runs_a_shell",
            "xargs -I{} sh -c {}": "command_shell_syntax",
            "find . -exec touch F +": "command_changes_files",
            "find . -name x -delete": "command_changes_files",
        }
        for command, reason in cases.items():
            with self.subTest(command=command):
                with self.assertRaises(team.TeamRefusal) as refused:
                    team.validate_team_command(command)
                self.assertEqual(refused.exception.reason, reason)
        self.assertEqual(team.validate_team_command("git diff --check"), ["git", "diff", "--check"])
        self.assertEqual(team.validate_team_command("npm test"), ["npm", "test"])

    def test_start_escalates_to_the_person_with_the_exact_commands(self) -> None:
        args = {"action": "team_start", "team_id": "t1", "plan_ref": "ref",
                "units": [unit("a", CHECK_A), unit("b", CHECK_B)]}
        directive = team_gate.team_start_directive(tool_name="omh_team", tool_input=args, escalation_allowed=True)
        assert directive is not None
        self.assertEqual(directive["action"], "approve")
        self.assertIn(f"1. {CHECK_A}\n2. {CHECK_B}", directive["message"])
        self.assertIn("real home folder", directive["message"])
        self.assertEqual(directive["rule_key"], team.approval_rule_key([CHECK_A, CHECK_B]))
        unattended = team_gate.team_start_directive(tool_name="omh_team", tool_input=args, escalation_allowed=False)
        self.assertEqual(unattended["action"], "block")
        # No plan_ref: the engine refuses before freezing anything, so the person is not asked twice.
        for other in ({**args, "action": "team_reconcile"}, {**args, "units": []}, {**args, "plan_ref": ""}):
            self.assertIsNone(team_gate.team_start_directive(tool_name="omh_team", tool_input=other,
                                                             escalation_allowed=True))
        self.assertIsNone(team_gate.team_start_directive(tool_name="omh_agent_board", tool_input=args,
                                                         escalation_allowed=True))

    def test_pre_tool_call_returns_the_team_start_approval(self) -> None:
        from omh.plugin_bundle.omh.hooks.tool_hooks import pre_tool_call

        with tempfile.TemporaryDirectory() as home:
            directive = pre_tool_call(tool_name="omh_team", session_id="s-approve", omh_home=home, hermes_home=home,
                                      args={"action": "team_start", "team_id": "t1", "plan_ref": "ref",
                                            "units": [unit("a", CHECK_A)]})
        assert directive is not None
        self.assertEqual(directive["action"], "approve")
        self.assertIn(CHECK_A, directive["message"])


class T9CallerGuard(unittest.TestCase):
    """Mutation: remove the board-worker or delegate-child refusal in the handler."""

    def setUp(self) -> None:
        nudge_budget.reset_nudge_budget()
        self.addCleanup(nudge_budget.reset_nudge_budget)

    def call(self, session: str) -> dict[str, object]:
        from omh.plugin_bundle.omh.tools.team_tool import omh_team_handler

        return json.loads(omh_team_handler({"action": "team_status", "team_id": "t1"}, session_id=session))

    def test_a_board_worker_is_refused(self) -> None:
        with mock.patch.dict(os.environ, {"HERMES_KANBAN_TASK": "task-7"}):
            result = self.call("worker-session")
        self.assertEqual((result["status"], result["reason"]), ("refused", "called_from_board_worker"))

    def test_a_delegate_child_is_refused(self) -> None:
        from omh.plugin_bundle.omh import runtime_paths

        nudge_budget.note_delegated_session("helper-session", omh_home=str(runtime_paths.plugin_home(None)))
        with mock.patch.dict(os.environ, {"HERMES_KANBAN_TASK": ""}):
            result = self.call("helper-session")
        self.assertEqual((result["status"], result["reason"]), ("refused", "called_from_helper"))

    def test_the_main_session_reaches_the_engine(self) -> None:
        with mock.patch.dict(os.environ, {"HERMES_KANBAN_TASK": ""}):
            result = self.call("main-session")
        self.assertEqual(result["reason"], "team_not_found")


class T10ResumeReEmitsWhatWasReserved(TeamHarness):
    """Mutation: bind an attempt the record never reserved, or forget reserved attempts on restart."""

    def test_restart_re_emits_the_identical_reserved_entries(self) -> None:
        first = self.start([unit("a", CHECK_A), unit("b", CHECK_B)])
        self.restart()
        again = self.start([unit("a", CHECK_A), unit("b", CHECK_B)])
        self.assertEqual(self.entries(again), self.entries(first))
        self.assertIn("already running", again["say"])

    def test_a_repeat_start_in_the_same_process_does_not_hand_out_entries_in_flight(self) -> None:
        """L4. Mutation: drop the emitted-but-not-started grace from `_reemit` or from the barrier."""
        self.start([unit("a", CHECK_A)])
        self.assertEqual(self.entries(self.start([unit("a", CHECK_A)])), [])
        waiting = self.reconcile()
        self.assertEqual((waiting["reason"], self.entries(waiting)), ("delegations_in_flight", []))
        self.clock += team.EMIT_GRACE_SECONDS
        self.assertEqual(len(self.entries(self.reconcile())), 1)

    def test_undispatched_attempts_block_after_the_re_emit_bound(self) -> None:
        self.start([unit("a", CHECK_A)])
        for _ in range(team.MAX_REEMITS):
            self.clock += team.EMIT_GRACE_SECONDS
            self.assertEqual(len(self.entries(self.reconcile())), 1)
        self.clock += team.EMIT_GRACE_SECONDS
        result = self.reconcile()
        self.assertEqual(result["units"][0]["blocked"]["reason"], "dispatch_not_observed")

    def test_a_marker_for_an_unreserved_attempt_binds_nothing(self) -> None:
        self.start([unit("a", CHECK_A)])
        for goal in ("forged [omh-team:t1/a/attempt-2]", "forged [omh-team:t1/zz/attempt-1]",
                     "forged [omh-team:other/a/attempt-1]", "[omh-team:t1/a/attempt-2] forged",
                     "Part a [omh-team:t1/a/attempt-1 cut",
                     # The last key counts: the reserved attempt before it does not bind.
                     "Part a [omh-team:t1/a/attempt-1] [omh-team:t1/a/attempt-2]"):
            with self.subTest(goal=goal):
                self.assertFalse(team.observe_dispatch(self.home, SESSION, child_session_id="c",
                                                       goal=goal, now=self.now()))
        self.assertFalse(team.observe_return(self.home, SESSION, child_session_id="never-started", now=self.now()))

    def test_the_last_whole_key_in_the_goal_binds(self) -> None:
        """Mutation: read only one end of the goal, or take the first key over the last one."""
        self.start([unit("a", CHECK_A), unit("b", CHECK_B), unit("c", CHECK_C), unit("d", CHECK_A)])
        goals = {
            # A goal handed out before an update opens with its key.
            "old": "[omh-team:t1/a/attempt-1] Part a",
            # One from before an update whose title ended with another key binds to that one, as the end key did.
            "both": "[omh-team:t1/a/attempt-1] Part b [omh-team:t1/b/attempt-1]",
            # A model that adds text after the key, or puts it mid-goal, leaves the binding as it was.
            "appended": "Part c [omh-team:t1/c/attempt-1]. Please hurry",
            "middle": "Part d [omh-team:t1/d/attempt-1] neither first nor last",
        }
        for child, goal in goals.items():
            self.assertTrue(team.observe_dispatch(self.home, SESSION, child_session_id=child, goal=goal,
                                                  now=self.now()), goal)
        bound = {item["unit_id"]: item["attempts"][0]["dispatch"]["child_session_id"] for item in self.record()["units"]}
        self.assertEqual(bound, {"a": "old", "b": "both", "c": "appended", "d": "middle"})

    def test_every_line_counts_and_a_goal_ending_in_a_dag_marker_binds_no_part(self) -> None:
        """Mutation: read team keys only, or the last key of the first line that has one."""
        self.start([unit("a", CHECK_A), unit("b", CHECK_B)])
        # A dag node's goal whose title, from before an update, quoted a key: the node's child, not a helper.
        for goal in ("Part a [omh-team:t1/a/attempt-1] [dag:feat/api]",
                     "Part a [omh-team:t1/a/attempt-1] [dag:feat/api]. Please hurry"):
            self.assertFalse(team.observe_dispatch(self.home, SESSION, child_session_id="dag-child", goal=goal,
                                                   now=self.now()), goal)
        self.assertTrue(team.observe_dispatch(
            self.home, SESSION, child_session_id="lines",
            goal="Part a [omh-team:t1/a/attempt-1]\nnotes\nPart b [omh-team:t1/b/attempt-1]", now=self.now()))
        self.assertFalse(team.observe_dispatch(self.home, SESSION, child_session_id="dag-child",
                                               goal="[omh-team:t1/a/attempt-1] Label [dag:feat/x]. Please hurry",
                                               now=self.now()))
        # A title from before an update that quoted a dag marker, with text after the key: still this helper.
        self.assertTrue(team.observe_dispatch(self.home, SESSION, child_session_id="old-title",
                                              goal="Title [dag:feat/api] [omh-team:t1/a/attempt-1]. Please hurry",
                                              now=self.now()))
        bound = {item["unit_id"]: (item["attempts"][0].get("dispatch") or {}).get("child_session_id")
                 for item in self.record()["units"]}
        self.assertEqual(bound, {"a": "old-title", "b": "lines"})

    def test_a_title_holding_a_key_or_a_dag_marker_is_refused(self) -> None:
        """Mutation: refuse only `[omh-team:`; a leading dag marker would bind this helper to a dag node."""
        for title in ("Part a, after [omh-team:t1/b/attempt-1]", "[dag:feat/api] Fix the parser"):
            with self.subTest(title=title):
                with self.assertRaises(team.TeamRefusal) as refused:
                    self.start([{**unit("a", CHECK_A), "title": title}])
                self.assertEqual(refused.exception.reason, "invalid_title")


class T11NoCheckWhileHelpersAreOut(TeamHarness):
    """Mutation: remove the in-flight barrier."""

    def test_reconcile_waits_for_every_team_helper(self) -> None:
        started = self.start([unit("a", CHECK_A), unit("b", CHECK_B)])
        goals = [entry["goal"] for entry in self.entries(started)]
        for index, goal in enumerate(goals):
            team.observe_dispatch(self.home, SESSION, child_session_id=f"c{index}", goal=goal, now=self.now())
        team.observe_return(self.home, SESSION, child_session_id="c0", now=self.now())
        result = self.reconcile()
        self.assertEqual(result["reason"], "delegations_in_flight")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(result["units"][0]["attempts_used"], 1)
        team.observe_return(self.home, SESSION, child_session_id="c1", now=self.now())
        self.assertEqual(self.reconcile()["team_state"], "done")

    def test_a_helper_that_never_returns_blocks_instead_of_holding_the_team(self) -> None:
        started = self.start([unit("a", CHECK_A)])
        team.observe_dispatch(self.home, SESSION, child_session_id="lost",
                              goal=self.entries(started)[0]["goal"], now=self.now())
        self.clock += team.DELEGATION_STALE_SECONDS + 10
        result = self.reconcile()
        self.assertEqual(result["units"][0]["blocked"]["reason"], "delegation_not_returned")
        self.assertEqual(result["team_state"], "blocked")


class ARestartHandsBackTheHelpersItEnded(TeamHarness):
    """A helper runs in the Hermes process that started it; a restart ends it and fires no `subagent_stop`.

    Mutation: drop `_hand_back_lost` from the resume, hand back on a live or unknown process, or in the same process.
    """

    PID = 4242
    # The engine reads the machine only through `dispatcher_alive`, so any name stands for one here.
    HOST = {"pid": PID, "started": 1_700_000_000, "machine": "linux:boot-1:pid:[4026531836]"}

    def out(self, units: list[dict[str, object]], hosts: dict[str, dict[str, int] | None]) -> dict[str, object]:
        """Start the team and play `subagent_start` for each part, recording the process ``hosts`` names."""
        started = self.start(units)
        for entry in self.entries(started):
            unit_id = entry["goal"].split("/")[1]
            self.assertTrue(team.observe_dispatch(self.home, SESSION, child_session_id=f"child-{unit_id}",
                                                  goal=entry["goal"], now=self.now(), host=hosts[unit_id]))
        return started

    def test_a_helper_whose_process_ended_goes_out_again_at_the_first_start_after_the_restart(self) -> None:
        first = self.out([unit("a", CHECK_A)], {"a": self.HOST})
        self.assertEqual(self.record()["units"][0]["attempts"][0]["dispatch"]["host"], self.HOST)
        self.restart()
        self.dispatchers[self.PID] = False
        again = self.start([unit("a", CHECK_A)])
        # The same attempt, under the same marker: no try was spent.
        self.assertEqual(self.entries(again), self.entries(first))
        self.assertEqual(again["say"], "Hermes restarted while 1 part was out; it starts again. "
                                       "0 of 1 part has passed its check so far.")
        self.assertEqual(self.states(again), {"a": "prepared"})
        attempt = self.record()["units"][0]["attempts"][0]
        # A hand-back is counted by the `lost` list, never as a re-emit.
        self.assertEqual((attempt["dispatch"], [item["child_session_id"] for item in attempt["lost"]],
                          attempt["reemits"]), (None, ["child-a"], 0))
        lost = again["events"][-1]
        self.assertEqual((lost["event"], lost["summary"], lost["detail_ref"]),
                         ("lost", "Its helper ended when Hermes restarted.", "t1/a/attempt-1"))
        # A stop naming the ended helper matches no part now.
        self.assertFalse(team.observe_return(self.home, SESSION, child_session_id="child-a", now=self.now()))
        self.dispatch_all(again)
        self.assertEqual(self.reconcile()["team_state"], "done")

    def test_only_the_parts_whose_process_ended_go_out_again(self) -> None:
        other = {"pid": 5151, "started": 1_700_000_500, "machine": "linux:boot-1:pid:[4026531836]"}
        self.out([unit("a", CHECK_A), unit("b", CHECK_B)], {"a": self.HOST, "b": other})
        self.restart()
        self.dispatchers.update({self.PID: False, 5151: True})
        again = self.start([unit("a", CHECK_A), unit("b", CHECK_B)])
        self.assertEqual([entry["goal"] for entry in self.entries(again)], ["Part a [omh-team:t1/a/attempt-1]"])
        self.assertEqual(self.states(again), {"a": "prepared", "b": "dispatched"})
        self.assertTrue(again["say"].startswith("Hermes restarted while 1 part was out; it starts again."))
        self.dispatchers[5151] = False
        self.restart()
        both = self.start([unit("a", CHECK_A), unit("b", CHECK_B)])
        self.assertEqual(both["say"], "Hermes restarted while 1 part was out; it starts again. "
                                      "0 of 2 parts have passed their checks so far.")

    def waits_for_the_bound(self, host: dict[str, int] | None, answer: bool | None) -> None:
        self.out([unit("a", CHECK_A)], {"a": host})
        self.restart()
        self.dispatchers[self.PID] = answer
        again = self.start([unit("a", CHECK_A)])
        self.assertEqual((self.entries(again), self.states(again)), ([], {"a": "dispatched"}))
        self.assertEqual(again["say"], "This team is already running: 0 of 1 part has passed its check.")
        self.assertEqual(self.reconcile()["reason"], "delegations_in_flight")
        self.clock += team.DELEGATION_STALE_SECONDS + 10
        self.assertEqual(self.reconcile()["units"][0]["blocked"]["reason"], "delegation_not_returned")

    def test_a_live_process_keeps_its_helper_out(self) -> None:
        self.waits_for_the_bound(self.HOST, True)

    def test_a_process_that_cannot_be_told_keeps_its_helper_out(self) -> None:
        self.waits_for_the_bound(self.HOST, None)

    def test_a_dispatch_with_no_recorded_process_is_never_asked_about(self) -> None:
        # The answer would say "gone"; with no process recorded it is never asked.
        self.waits_for_the_bound(None, False)

    def test_a_helper_that_came_back_before_the_restart_is_not_handed_back(self) -> None:
        """Mutation: drop the `returned_at` skip in `_hand_back_lost`."""
        self.out([unit("a", CHECK_A)], {"a": self.HOST})
        self.assertTrue(team.observe_return(self.home, SESSION, child_session_id="child-a", now=self.now()))
        self.restart()
        self.dispatchers[self.PID] = False
        again = self.start([unit("a", CHECK_A)])
        self.assertEqual((self.entries(again), self.states(again)), ([], {"a": "awaiting_check"}))
        self.assertEqual(again["say"], "This team is already running: 0 of 1 part has passed its check.")
        self.assertNotIn("lost", [item["event"] for item in again["events"]])
        attempt = self.record()["units"][0]["attempts"][0]
        self.assertEqual((attempt["dispatch"]["child_session_id"], "lost" in attempt), ("child-a", False))
        self.assertEqual(self.reconcile()["team_state"], "done")

    def test_a_repeat_start_in_the_same_process_hands_nothing_back(self) -> None:
        self.out([unit("a", CHECK_A)], {"a": self.HOST})
        self.dispatchers[self.PID] = False
        again = self.start([unit("a", CHECK_A)])
        self.assertEqual((self.entries(again), self.states(again)), ([], {"a": "dispatched"}))

    def test_restarts_past_the_bound_block_the_part_as_lost(self) -> None:
        self.dispatchers[self.PID] = False
        result = self.start([unit("a", CHECK_A)])
        for n in range(team.MAX_REEMITS + 1):
            self.assertTrue(team.observe_dispatch(self.home, SESSION, child_session_id=f"child-{n}",
                                                  goal=self.entries(result)[0]["goal"], now=self.now(),
                                                  host=self.HOST))
            self.restart()
            result = self.start([unit("a", CHECK_A)])
        self.assertEqual(result["units"][0]["blocked"]["reason"], "helper_lost")
        self.assertEqual(result["say"],
                         "Stopped: 'Part a' is blocked because Hermes kept restarting while its helper was out.")
        attempts = self.record()["units"][0]["attempts"]
        self.assertEqual((len(attempts), len(attempts[0]["lost"])), (1, team.MAX_REEMITS + 1))

    def test_earlier_re_emits_do_not_shorten_the_restart_bound(self) -> None:
        """L1. Mutation: count a restart's hand-back in `reemits`, the counter of re-emits for a slow start."""
        result = self.start([unit("a", CHECK_A)])
        for _ in range(team.MAX_REEMITS):
            self.clock += team.EMIT_GRACE_SECONDS
            result = self.reconcile()
            self.assertEqual(len(self.entries(result)), 1)
        self.dispatchers[self.PID] = False
        for n in range(team.MAX_REEMITS + 1):
            self.assertTrue(team.observe_dispatch(self.home, SESSION, child_session_id=f"child-{n}",
                                                  goal=self.entries(result)[0]["goal"], now=self.now(),
                                                  host=self.HOST))
            self.restart()
            result = self.start([unit("a", CHECK_A)])
            if n < team.MAX_REEMITS:
                self.assertEqual((len(self.entries(result)), self.states(result)), (1, {"a": "prepared"}))
        self.assertEqual(result["units"][0]["blocked"]["reason"], "helper_lost")

    def test_a_part_already_blocked_is_not_handed_back(self) -> None:
        """Mutation: drop the `blocked` skip in `_hand_back_lost`."""
        self.out([unit("a", CHECK_A)], {"a": self.HOST})
        self.clock += team.DELEGATION_STALE_SECONDS + 10
        self.assertEqual(self.reconcile()["units"][0]["blocked"]["reason"], "delegation_not_returned")
        self.restart()
        self.dispatchers[self.PID] = False
        again = self.start([unit("a", CHECK_A)])
        self.assertEqual(again["say"], "Stopped: 'Part a' is blocked because its helper never came back.")
        self.assertNotIn("lost", [item["event"] for item in again["events"]])
        self.assertEqual(self.record()["units"][0]["attempts"][0]["dispatch"]["child_session_id"], "child-a")

    def test_the_restart_line_counts_only_the_parts_that_go_out_again(self) -> None:
        """Mutation: count a part that blocked at the bound in the restart line."""
        self.dispatchers[self.PID] = False
        result = self.start([unit("a", CHECK_A), unit("b", CHECK_B)])
        for n in range(team.MAX_REEMITS + 1):
            goals = {entry["goal"].split("/")[1]: entry["goal"] for entry in self.entries(result)}
            ids = ["a", "b"] if n == team.MAX_REEMITS else ["a"]
            for unit_id in ids:
                self.assertTrue(team.observe_dispatch(self.home, SESSION, child_session_id=f"{unit_id}-{n}",
                                                      goal=goals[unit_id], now=self.now(), host=self.HOST))
            self.restart()
            result = self.start([unit("a", CHECK_A), unit("b", CHECK_B)])
        self.assertEqual(self.states(result), {"a": "blocked", "b": "prepared"})
        self.assertEqual(result["say"], "Hermes restarted while 1 part was out; it starts again. "
                                        "0 of 2 parts have passed their checks so far.")


class StartSaysHowManyPartsStartNow(TeamHarness):
    """Mutation: say every part starts in parallel when some wait on others, or write "1 parts"."""

    def say(self, team_id: str, units: list[dict[str, object]]) -> str:
        plan = plan_for(*[str(item["verification_command"]) for item in units])
        return team.team_start(self.ctx, team_id=team_id, units=units, plan=plan, plan_ref=plan["items_digest"],
                               workdir=self.workdir)["say"]

    def test_one_part_some_parts_and_every_part(self) -> None:
        self.assertEqual(self.say("one", [unit("a", CHECK_A)]), "Started 1 part; it is done only when its check passes.")
        self.assertEqual(
            self.say("chain", [unit("a", CHECK_A), unit("b", CHECK_B, ["a"]), unit("c", CHECK_C, ["b"])]),
            "Split into 3 parts; 1 starts now and the rest wait for the parts they depend on, and each part is done "
            "only when its check passes.")
        self.assertEqual(
            self.say("join", [unit("a", CHECK_A), unit("b", CHECK_B), unit("c", CHECK_C, ["a", "b"])]),
            "Split into 3 parts; 2 start now in parallel and the rest wait for the parts they depend on, and each "
            "part is done only when its check passes.")
        self.assertEqual(self.say("pair", [unit("a", CHECK_A), unit("b", CHECK_B)]),
                         "Split into 2 parts; 2 start now in parallel, and each part is done only when its check passes.")

    def test_a_one_part_team_never_says_1_parts(self) -> None:
        says = [self.start([unit("a", CHECK_A)])["say"], team.team_status(self.ctx, team_id="t1")["say"]]
        self.restart()
        resumed = self.start([unit("a", CHECK_A)])
        self.dispatch_all(resumed)
        says += [resumed["say"], self.reconcile()["say"], *(item["summary"] for item in self.record()["events"])]
        self.assertEqual(says[1:4], ["0 of 1 part has passed its check so far.",
                                     "This team is already running: 0 of 1 part has passed its check.",
                                     "The one part passed its check."])
        for say in says:
            self.assertNotIn("1 parts", say)


class T12TeamStateIsDerived(TeamHarness):
    """Mutation: store a team state and read it back instead of deriving it."""

    def test_no_state_is_written_and_a_planted_one_is_ignored(self) -> None:
        self.start([unit("a", CHECK_A)])
        record = self.record()
        self.assertNotIn("team_state", record)
        self.assertNotIn("state", record)
        record["team_state"] = "done"
        record["state"] = "done"
        team.write_team(team.team_path(self.home, SESSION, "t1"), record)
        self.assertEqual(team.team_status(self.ctx, team_id="t1")["team_state"], "running")


class TeamStatusSpeaksPlainly(TeamHarness):
    def test_status_carries_counts_caveat_cost_and_plain_say_lines(self) -> None:
        self.runner = FakeRunner({CHECK_A: [exited(2)], CHECK_B: [exited(0)]})
        started = self.start([unit("a", CHECK_A), unit("b", CHECK_B)])
        self.assertEqual(started["say"],
                         "Split into 2 parts; 2 start now in parallel, and each part is done only when its check passes.")
        self.dispatch_all(started)
        repaired = self.reconcile()
        self.assertIn("sending it back for a fix, try 2 of 3", repaired["say"])
        status = team.team_status(self.ctx, team_id="t1", cost={"status": "observed", "conversation_cost_usd": 0.5})
        self.assertEqual(status["counts"]["accepted"], 1)
        self.assertEqual(status["counts"]["repairing"], 1)
        self.assertEqual(status["cost"]["conversation_cost_usd"], 0.5)
        self.assertEqual(status["caveat"], team.TEAM_CAVEAT)
        self.assertEqual(status["units"][0]["last_check"]["exit_code"], 2)
        record_vocabulary = ("prepared_not_observed", "ledger", "receipt", "lane", "omh_team", "schema")
        for text in (started["say"], repaired["say"], status["say"], status["caveat"],
                     *team._BLOCKED_SAY.values(), *team._REASON_SAY.values()):
            for word in record_vocabulary:
                self.assertNotIn(word, text)


class TeamEventsAreRenderable(TeamHarness):
    """A header (teammate + event), one plain summary line, and a reference: nothing else."""

    def lifecycle(self) -> list[dict[str, object]]:
        self.runner = FakeRunner({CHECK_A: [exited(1), exited(0)]})
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        self.dispatch_all(self.reconcile())
        return list(self.reconcile()["events"])

    def test_one_lifecycle_in_order_with_a_monotonic_seq(self) -> None:
        events = self.lifecycle()
        self.assertEqual([item["event"] for item in events],
                         ["started", "finished", "check_failed", "repairing", "started", "finished",
                          "check_passed", "done"])
        seqs = [item["seq"] for item in events]
        self.assertEqual(seqs, sorted(set(seqs)))
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        for item in events:
            self.assertEqual(set(item), {"seq", "at", "unit_id", "teammate", "event", "summary", "detail_ref"})
            self.assertIn(item["event"], team.TEAM_EVENTS)
            self.assertNotEqual(item["teammate"], item["unit_id"])
        failed = events[2]
        self.assertEqual((failed["teammate"], failed["detail_ref"]), ("Part a", "t1/a/attempt-1/check"))
        self.assertEqual(events[-1]["teammate"], "The team")

    def test_since_seq_returns_only_newer_events(self) -> None:
        events = self.lifecycle()
        status = team.team_status(self.ctx, team_id="t1", since_seq=5)
        self.assertEqual([item["seq"] for item in status["events"]], [item["seq"] for item in events if item["seq"] > 5])
        self.assertEqual(status["last_seq"], events[-1]["seq"])
        self.assertFalse(status["events_truncated"])
        self.assertEqual(team.team_status(self.ctx, team_id="t1", since_seq=status["last_seq"])["events"], [])

    def test_every_summary_passes_the_reply_lint(self) -> None:
        from omh.quality.reply_lint import build_reply_lint

        events = self.lifecycle()
        self.runner = FakeRunner({CHECK_B: [exited(4)]})
        record = self.record()
        team._block(record, record["units"][0], self.now(), {"reason": "dispatch_not_observed"})
        summaries = {item["summary"] for item in events} | {record["events"][-1]["summary"]}
        summaries |= {f"Stopped: {why}." for why in team._BLOCKED_SAY.values()}
        summaries |= {f"A helper came back under a session OMH could not link to any part, because {why}."
                      for why in team._UNLINKED_SAY.values()}
        for summary in summaries:
            with self.subTest(summary=summary):
                self.assertTrue(build_reply_lint(summary)["ok"], build_reply_lint(summary)["findings"])
                self.assertNotIn("\n", summary)

    def test_the_list_is_capped_and_seq_keeps_counting(self) -> None:
        self.start([unit("a", CHECK_A)])
        record = self.record()
        for _ in range(team.MAX_TEAM_EVENTS + 6):
            team._emit(record, self.now(), unit=record["units"][0], event="started",
                       summary="Started working on this part.", detail_ref="t1/a/attempt-1")
        self.assertEqual(len(record["events"]), team.MAX_TEAM_EVENTS)
        self.assertEqual(record["events"][0]["seq"], 7)
        self.assertEqual(record["events"][-1]["seq"], team.MAX_TEAM_EVENTS + 6)
        view = team._events_since(record, 0)
        self.assertTrue(view["events_truncated"])
        self.assertFalse(team._events_since(record, 6)["events_truncated"])


def fake_hermes_floor(blocked: frozenset[str] = frozenset(), *, cron: bool = False,
                      raises: bool = False) -> dict[str, object]:
    """`tools.approval` / `tools.approval_context` as the Hermes process would provide them."""
    def floor(command: str) -> dict[str, object] | None:
        if raises:
            raise RuntimeError("floor exploded")
        return {"approved": False} if command in blocked else None

    approval = SimpleNamespace(_floor_block=floor, _user_deny_block=lambda command: None)
    context = SimpleNamespace(_is_cron_approval_context=lambda: cron)
    return {"tools": SimpleNamespace(approval=approval, approval_context=context),
            "tools.approval": approval, "tools.approval_context": context}


def hermes_process(*, started: int) -> dict[str, object]:
    """Hermes' `gateway.status` liveness probes in a Hermes process that started at ``started``.

    Every pid exists; a recorded start time other than ``started`` is another process, as Hermes' own comparator
    reads a reused pid.
    """
    status = SimpleNamespace(_pid_exists=lambda pid: True, get_process_start_time=lambda pid: started,
                             start_time_fingerprints_match=lambda recorded, current: abs(current - recorded) <= 200)
    return {"gateway": SimpleNamespace(status=status), "gateway.status": status}


class TeamToolHandler(unittest.TestCase):
    """The plugin handler end to end: plan read, start, the host floor, and process-memory approval."""

    SESSION = "20260928_130000_handler"

    def setUp(self) -> None:
        from omh.plugin_bundle.omh import runtime_paths
        from omh.plugin_bundle.omh.todo_store import build_todo_record, write_todo
        from omh.plugin_bundle.omh.tools import team_tool

        nudge_budget.reset_nudge_budget()
        self.addCleanup(nudge_budget.reset_nudge_budget)
        for memory in (team_tool._APPROVED_TEAMS, team_tool._OBSERVED_CHECKS):
            memory.clear()
            self.addCleanup(memory.clear)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workdir = Path(temporary.name)
        self.team_tool = team_tool
        # Homes of this test's own. The handler resolves both through the env,
        # and the process-wide ones `_local_package` sets outlive every test:
        # with the session and team ids fixed above, a later test resumed the
        # team an earlier one started instead of starting its own.
        homes = tempfile.TemporaryDirectory()
        self.addCleanup(homes.cleanup)
        env = mock.patch.dict(os.environ, {"HERMES_KANBAN_TASK": "", "HERMES_CRON_SESSION": "",
                                           "OMH_HOME": str(Path(homes.name) / "omh"),
                                           "HERMES_HOME": str(Path(homes.name) / "hermes")})
        cwd = mock.patch.object(runtime_paths, "runtime_cwd", return_value=self.workdir)
        env.start()
        cwd.start()
        self.addCleanup(env.stop)
        self.addCleanup(cwd.stop)
        record = build_todo_record("Team plan", [{"text": f"Build it; check: `{CHECK_A}`", "state": "active"}],
                                   source="omh_todo", session_ref=self.SESSION, plan_stage="accepted")
        write_todo(runtime_paths.default_omh_home(), record)
        self.args = {"action": "team_start", "team_id": "handler", "units": [unit("a", CHECK_A)],
                     "plan_ref": todo_items_digest(record["items"])}

    def call(self, args: dict[str, object], hermes: dict[str, object] | None = None,
             session: str = "") -> dict[str, object]:
        with patch_modules(hermes if hermes is not None else fake_hermes_floor()):
            return json.loads(self.team_tool.omh_team_handler(args, session_id=session or self.SESSION))

    def test_start_reads_this_sessions_accepted_plan_and_hands_back_the_plan_ref(self) -> None:
        refused = self.call({**self.args, "plan_ref": "x"})
        self.assertEqual(refused["reason"], "plan_ref_mismatch")
        started = self.call({**self.args, "plan_ref": refused["plan_ref"]})
        self.assertEqual(started["status"], "ok")
        self.assertEqual(started["team_state"], "running")
        # A start, not a resume of a team another test left in a shared home.
        self.assertEqual(started["say"], "Started 1 part; it is done only when its check passes.")
        self.assertEqual(len(started["delegate_task"]["arguments"]["tasks"]), 1)

    def test_no_reachable_hermes_floor_refuses_every_start(self) -> None:
        """H1(f). Mutation: let `_host_floor` return None when `tools.approval` cannot be imported."""
        missing = self.call(self.args, hermes={"tools": SimpleNamespace()})
        self.assertEqual((missing["status"], missing["reason"]), ("refused", "host_command_floor_unavailable"))
        raising = self.call(self.args, hermes=fake_hermes_floor(raises=True))
        self.assertEqual(raising["reason"], "host_command_floor_unavailable")
        self.assertEqual(self.team_tool._APPROVED_TEAMS, set())

    def test_the_hermes_floor_refuses_a_command_it_blocks(self) -> None:
        """H1(f). Mutation: ignore `_floor_block`'s answer."""
        refused = self.call(self.args, hermes=fake_hermes_floor(frozenset({CHECK_A})))
        self.assertEqual((refused["status"], refused["reason"]), ("refused", "command_refused_by_host"))

    def test_a_cron_session_cannot_start_a_team(self) -> None:
        """M3. Mutation: drop the cron refusal."""
        refused = self.call(self.args, hermes=fake_hermes_floor(cron=True))
        self.assertEqual((refused["status"], refused["reason"]), ("refused", "called_from_cron"))

    def test_only_a_start_in_this_process_approves_a_reconcile(self) -> None:
        """H2. Mutation: add to `_APPROVED_TEAMS` anywhere but after a successful start, or not at all."""
        reconcile = {"action": "team_reconcile", "team_id": "handler"}
        started = self.call(self.args)
        self.assertEqual((started["status"], started["team_state"]), ("ok", "running"))
        self.assertEqual(self.call(reconcile)["reason"], "delegations_in_flight")
        self.team_tool._APPROVED_TEAMS.clear()  # a Hermes restart
        self.assertEqual(self.call(reconcile)["reason"], "team_not_approved_here")
        resumed = self.call(self.args)
        self.assertEqual((resumed["status"], resumed["team_state"]), ("ok", "running"))
        self.assertEqual(self.call(reconcile)["status"], "ok")

    LATER = "20260928_140000_handler"

    def hermes_rows(self, *rows: dict[str, object], unreadable: bool = False) -> None:
        """This test's Hermes home gets a `state.db`, removed again after the test."""
        from omh.plugin_bundle.omh import runtime_paths

        hermes = runtime_paths.default_hermes_home()
        hermes.mkdir(parents=True, exist_ok=True)
        path = hermes / "state.db"
        path.unlink(missing_ok=True)
        self.addCleanup(path.unlink, missing_ok=True)
        if unreadable:
            write_unreadable_state_db(hermes)
        else:
            write_sessions(hermes, *rows)

    def rotated(self, *more: dict[str, object]) -> None:
        self.hermes_rows(compacted(self.SESSION, at=1100.0),
                         hermes_row(self.LATER, parent=self.SESSION, started_at=1100.0), *more)

    @staticmethod
    def reconcile_args(team_id: str) -> dict[str, object]:
        return {"action": "team_reconcile", "team_id": team_id}

    def test_a_compacted_parent_reaches_its_team_from_the_new_segment(self) -> None:
        """Mutation: look for the team only under the calling session."""
        from omh.plugin_bundle.omh import runtime_paths

        args = {**self.args, "team_id": "rotated-a"}
        self.assertEqual(self.call(args)["status"], "ok")
        self.rotated()
        self.assertEqual(self.call(self.reconcile_args("rotated-a"), session=self.LATER)["reason"],
                         "delegations_in_flight")
        status = self.call({"action": "team_status", "team_id": "rotated-a"}, session=self.LATER)
        self.assertEqual((status["status"], status["team_id"]), ("ok", "rotated-a"))
        resumed = self.call(args, session=self.LATER)
        self.assertEqual((resumed["status"], resumed["delegate_task"]), ("ok", None))
        self.assertFalse(team.teams_dir(runtime_paths.default_omh_home(), self.LATER).exists())

    def test_a_compacted_parent_starts_a_team_from_the_plan_it_accepted_before(self) -> None:
        """The accepted plan is under the segment Hermes compacted; a session that does not continue it has none.

        Mutation: read the plan only under the calling session.
        """
        self.rotated(hermes_row("20260928_140000_other2"))
        args = {**self.args, "team_id": "rotated-e"}
        started = self.call(args, session=self.LATER)
        self.assertEqual((started["status"], started["team_state"]), ("ok", "running"))
        self.assertEqual(self.call(self.reconcile_args("rotated-e"), session=self.LATER)["reason"],
                         "delegations_in_flight")
        self.assertEqual(self.call({**args, "team_id": "rotated-f"}, session="20260928_140000_other2")["reason"],
                         "plan_not_found")

    def test_sessions_that_do_not_continue_the_parent_do_not_reach_its_team(self) -> None:
        """An unrelated session and a fork of the parent find nothing; a forged continuation is said, not followed."""
        self.assertEqual(self.call({**self.args, "team_id": "rotated-b"})["status"], "ok")
        other, fork, forged = "20260928_140000_other", "20260928_140000_branch", "20260928_140000_forged"
        self.rotated(
            hermes_row(other),
            hermes_row(fork, parent=self.SESSION, config={"_branched_from": self.SESSION}, started_at=1100.0),
            hermes_row(forged, parent=self.SESSION, delegate_from="someone-else", started_at=1100.0),
        )
        for session in (other, fork):
            with self.subTest(session=session):
                self.assertEqual(self.call(self.reconcile_args("rotated-b"), session=session)["reason"],
                                 "team_not_found")
        self.assertEqual(self.call(self.reconcile_args("rotated-b"), session=forged)["reason"],
                         "team_lineage_unverified")

    def test_an_unreadable_lineage_is_said_and_never_read_as_no_team(self) -> None:
        """The in-place path is untouched: the team's own session needs no lineage read."""
        from omh.quality.reply_lint import build_reply_lint

        self.assertEqual(self.call({**self.args, "team_id": "rotated-c"})["status"], "ok")
        self.hermes_rows(unreadable=True)
        refused = self.call(self.reconcile_args("rotated-c"), session=self.LATER)
        self.assertEqual((refused["status"], refused["reason"]), ("refused", "team_lineage_unverified"))
        self.assertEqual(refused["say"], "No team with that name was started in this session, and OMH could not "
                                         "check whether this session continues an earlier one Hermes compacted, "
                                         "because Hermes' session records could not be read.")
        self.assertTrue(build_reply_lint(refused["say"])["ok"])
        self.assertEqual(self.call(self.reconcile_args("rotated-c"))["reason"], "delegations_in_flight")
        # Starting a new team needs no earlier segment, so an unreadable one refuses nothing.
        self.assertEqual(self.call({**self.args, "team_id": "rotated-d"})["status"], "ok")

    def test_a_resumed_team_is_approved_under_its_owner_after_a_restart(self) -> None:
        """Mutation N11: record the approval under the calling segment, not the session that owns the team."""
        args = {**self.args, "team_id": "rotated-r"}
        self.assertEqual(self.call(args)["status"], "ok")
        self.rotated()
        self.team_tool._APPROVED_TEAMS.clear()  # a Hermes restart
        self.assertEqual(self.call(self.reconcile_args("rotated-r"), session=self.LATER)["reason"],
                         "team_not_approved_here")
        self.assertEqual(self.call(args, session=self.LATER)["status"], "ok")
        self.assertEqual(self.call(self.reconcile_args("rotated-r"), session=self.LATER)["reason"],
                         "delegations_in_flight")

    def test_a_restart_hands_back_a_helper_whose_hermes_process_ended(self) -> None:
        """The hooks record this process through Hermes' own probes; the handler asks them after a restart.

        Mutation: drop `host` in `observe_team_dispatch`, or pass the engine a `dispatcher_alive` that never says gone.
        """
        from omh.plugin_bundle.omh import runtime_paths
        from omh.plugin_bundle.omh.hooks.session_hooks import subagent_start

        hooks = {"omh_home": str(runtime_paths.default_omh_home()),
                 "hermes_home": str(runtime_paths.default_hermes_home()), "parent_session_id": self.SESSION}
        for team_id, later_start, handed_back in (("restart-a", 5000, True), ("restart-b", 777, False)):
            with self.subTest(team_id=team_id):
                started = self.call({**self.args, "team_id": team_id})
                with patch_modules(hermes_process(started=777)):
                    subagent_start(child_session_id=f"child-{team_id}",
                                   child_goal=started["delegate_task"]["arguments"]["tasks"][0]["goal"], **hooks)
                record = team.read_team(team.team_path(runtime_paths.default_omh_home(), self.SESSION, team_id))
                self.assertEqual(record["units"][0]["attempts"][0]["dispatch"]["host"],
                                 {"pid": os.getpid(), "started": 777, "machine": host_process.machine()})
                self.team_tool._APPROVED_TEAMS.clear()  # a Hermes restart
                # The process now running started at `later_start`: another start time is another process.
                resumed = self.call({**self.args, "team_id": team_id},
                                    hermes={**fake_hermes_floor(), **hermes_process(started=later_start)})
                self.assertEqual(bool(resumed["delegate_task"]), handed_back)
                self.assertEqual(resumed["say"].startswith("Hermes restarted while 1 part was out"), handed_back)


class HostLifecycleHooksDriveTheBarrier(TeamHarness):
    """The registered `subagent_start` / `subagent_stop` hooks, not the engine calls, move a helper."""

    def test_hooks_bind_start_and_return(self) -> None:
        from omh.plugin_bundle.omh.hooks.session_hooks import subagent_start, subagent_stop

        nudge_budget.reset_nudge_budget()
        self.addCleanup(nudge_budget.reset_nudge_budget)
        goal = self.entries(self.start([unit("a", CHECK_A)]))[0]["goal"]
        common = {"omh_home": str(self.home), "hermes_home": str(self.home), "parent_session_id": SESSION}
        subagent_start(child_session_id="hook-child", child_goal=goal, **common)
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1")), {"a": "dispatched"})
        subagent_stop(child_session_id="other-child", **common)
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1")), {"a": "dispatched"})
        subagent_stop(child_session_id="hook-child", **common)
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1")), {"a": "awaiting_check"})


class CompactedHelperComesBack(TeamHarness):
    """A helper Hermes compacted into a new session id still comes back, and nothing looser counts.

    Hermes reads `child_session_id` off the child at `subagent_start` and again
    at `subagent_stop` (`tools/delegate_tool.py`, `tools/delegate_tool_results.py`);
    rotating compaction re-points the child at a new row in between, so the
    stop names a later segment and the part used to stay `dispatched` until
    the 4-hour block called it "never came back". The rows are in the shape
    Hermes' own `SessionDB` writes (`_hermes_session_rows`).

    Mutations: match the stop id only; drop the fork-marker or inherited-marker
    check; follow a parent Hermes did not close for compaction; drop the
    unlinked-return record or its block reason.
    """

    def setUp(self) -> None:
        super().setUp()
        self.hermes = self.home.parent / "hermes"
        self.hermes.mkdir()
        self.lineage_reads: list[str] = []

    def lineage(self, session_id: str) -> dict[str, object]:
        self.lineage_reads.append(session_id)
        return compaction_ancestors(str(self.hermes), session_id)

    def dispatch_one(self) -> None:
        goal = self.entries(self.start([unit("a", CHECK_A)]))[0]["goal"]
        self.assertTrue(team.observe_dispatch(self.home, SESSION, child_session_id="C1", goal=goal, now=self.now()))

    def stop(self, child: str) -> bool:
        return team.observe_return(self.home, SESSION, child_session_id=child, now=self.now(), lineage=self.lineage)

    def helper_rows(self, *later: dict[str, object]) -> None:
        write_sessions(
            self.hermes,
            hermes_row(SESSION),
            compacted("C1", at=1100.0, parent=SESSION, source="subagent", delegate_from=SESSION),
            *later,
        )

    def test_a_helper_compacted_into_a_new_session_comes_back(self) -> None:
        self.dispatch_one()
        self.helper_rows(hermes_row("C2", parent="C1", source="subagent", delegate_from=SESSION,
                                    started_at=1100.0, ended_at=1200.0, end_reason="agent_close"))
        self.assertTrue(self.stop("C2"))
        record = self.record()
        attempt = record["units"][0]["attempts"][-1]
        self.assertEqual((attempt["dispatch"]["child_session_id"], attempt["returned_as"]), ("C1", "C2"))
        self.assertEqual(record["events"][-1]["summary"],
                         "Came back after Hermes compacted its session; its check runs next.")
        from omh.quality.reply_lint import build_reply_lint

        self.assertTrue(build_reply_lint(record["events"][-1]["summary"])["ok"])
        self.assertEqual(self.states(self.reconcile()), {"a": "accepted"})
        self.assertEqual(self.runner.calls, [CHECK_A])

    def test_a_helper_compacted_twice_comes_back(self) -> None:
        self.dispatch_one()
        self.helper_rows(
            compacted("C2", at=1200.0, parent="C1", source="subagent", delegate_from=SESSION, started_at=1100.0),
            hermes_row("C3", parent="C2", source="subagent", delegate_from=SESSION, started_at=1200.0),
        )
        self.assertTrue(self.stop("C3"))
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1")), {"a": "awaiting_check"})

    def test_a_return_under_the_recorded_id_reads_no_lineage(self) -> None:
        self.dispatch_one()
        self.assertTrue(self.stop("C1"))
        self.assertEqual(self.lineage_reads, [])
        self.assertNotIn("returned_as", self.record()["units"][0]["attempts"][-1])
        self.assertEqual(self.record()["events"][-1]["summary"], "Came back; its check runs next.")

    def test_a_forged_continuation_is_not_its_return_and_is_said(self) -> None:
        # Names the helper's closed segment as its parent, but its inherited
        # `_delegate_from` is not the helper's: Hermes never writes that.
        self.dispatch_one()
        self.helper_rows(hermes_row("F", parent="C1", source="subagent", delegate_from="someone-else",
                                    started_at=1100.0))
        self.assertFalse(self.stop("F"))
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1")), {"a": "dispatched"})
        notes = self.record()["unlinked_returns"]
        self.assertEqual([(note["child_session_id"], note["reason"]) for note in notes],
                         [("F", "continuation_not_authenticated")])
        result = self.reconcile()
        self.assertEqual((result["reason"], self.runner.calls), ("delegations_in_flight", []))
        self.assertEqual(result["say"], "1 part(s) have not come back in a way OMH can confirm, so nothing is "
                                        "checked yet. A helper came back under a session OMH could not link to "
                                        "any part, because its session history does not match how Hermes "
                                        "records a compaction.")
        self.assertEqual([note["child_session_id"] for note in result["unlinked_returns"]], ["F"])

    def test_other_conversations_beside_the_helper_are_not_its_return(self) -> None:
        # A child the helper delegated to (a fork of it), a sibling under the
        # same parent, and a row naming the helper as parent while Hermes never
        # closed the helper's segment for compaction: same parent, near in
        # time, none of them the helper's continuation, and none unreadable.
        self.dispatch_one()
        write_sessions(
            self.hermes,
            hermes_row(SESSION),
            hermes_row("C1", parent=SESSION, source="subagent", delegate_from=SESSION),
            hermes_row("G", parent="C1", source="subagent", delegate_from="C1", started_at=1100.0),
            hermes_row("X", parent=SESSION, source="subagent", delegate_from=SESSION, started_at=1000.0),
            hermes_row("Y", parent="C1", source="subagent", delegate_from=SESSION, started_at=1100.0),
        )
        for child in ("G", "X", "Y"):
            with self.subTest(child=child):
                self.assertFalse(self.stop(child))
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1")), {"a": "dispatched"})
        self.assertNotIn("unlinked_returns", self.record())

    def test_a_fork_of_the_compacted_helper_is_not_its_return(self) -> None:
        # The helper's segment WAS closed for compaction, and these rows name
        # it as parent, but each is a conversation of its own: a child it
        # delegated to, a branch of it. Not its return, and not unreadable
        # either.
        self.dispatch_one()
        self.helper_rows(
            hermes_row("G", parent="C1", source="subagent", delegate_from="C1", started_at=1100.0),
            hermes_row("B", parent="C1", source="subagent", started_at=1100.0,
                       config={"_branched_from": "C1", "_delegate_from": SESSION}),
        )
        for child in ("G", "B"):
            with self.subTest(child=child):
                self.assertFalse(self.stop(child))
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1")), {"a": "dispatched"})
        self.assertNotIn("unlinked_returns", self.record())

    def test_a_compaction_reason_on_a_row_hermes_never_closed_is_not_a_closure(self) -> None:
        # Hermes writes `ended_at` and `end_reason` together, so a row carrying
        # the reason while still open is not a segment it closed. Mutation:
        # read the reason alone as the closure.
        self.dispatch_one()
        write_sessions(
            self.hermes,
            hermes_row(SESSION),
            hermes_row("C1", parent=SESSION, source="subagent", delegate_from=SESSION, end_reason="compression"),
            hermes_row("C2", parent="C1", source="subagent", delegate_from=SESSION, started_at=1100.0),
        )
        self.assertFalse(self.stop("C2"))
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1")), {"a": "dispatched"})
        self.assertNotIn("unlinked_returns", self.record())

    def test_a_tool_session_under_the_compacted_helper_is_said_not_linked(self) -> None:
        # Under `HERMES_SESSION_SOURCE=tool` a helper's rows are `tool` rows,
        # and Hermes copies a segment's source onto its continuation, so a
        # `tool` row under a compacted segment may be its continuation or a
        # tool session forked under it. Neither linked nor silent.
        # Mutation: read a `tool` row as a fork in the backward walk.
        self.dispatch_one()
        write_sessions(
            self.hermes,
            hermes_row(SESSION),
            compacted("C1", at=1100.0, parent=SESSION, source="tool", delegate_from=SESSION),
            hermes_row("C2", parent="C1", source="tool", delegate_from=SESSION, started_at=1100.0),
        )
        self.assertFalse(self.stop("C2"))
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1")), {"a": "dispatched"})
        self.assertEqual([note["reason"] for note in self.record()["unlinked_returns"]], ["tool_session_ambiguous"])
        self.assertIn("Hermes records it as a tool session", self.reconcile()["say"])

    def test_an_unreadable_lineage_stops_the_part_as_not_linked(self) -> None:
        # A `state.db` that cannot be read: the stop links to nothing and is
        # kept, and the stale bound names it instead of "never came back".
        self.dispatch_one()
        write_unreadable_state_db(self.hermes)
        self.assertFalse(self.stop("C2"))
        self.assertEqual(self.record()["unlinked_returns"][0]["reason"], "state_db_unavailable")
        self.clock += team.DELEGATION_STALE_SECONDS + 10
        result = self.reconcile()
        self.assertEqual(result["units"][0]["blocked"]["reason"], "return_not_linked")
        self.assertEqual(result["say"], "Stopped: 'Part a' is blocked because a helper came back that OMH "
                                        "could not link to it.")

    def test_a_home_with_no_state_db_has_no_lineage_to_doubt(self) -> None:
        # Rotation is a `state.db` write in the same home, so with none there
        # was no compaction: an unmatched stop is someone else's, not kept.
        self.dispatch_one()
        self.assertFalse(self.stop("C2"))
        self.assertNotIn("unlinked_returns", self.record())

    def test_every_reason_the_lineage_reader_gives_has_words(self) -> None:
        # `no_continuation` is the forward walk's alone; the backward walk a
        # return uses never gives it.
        from omh.plugin_bundle.omh.live_session import LINEAGE_UNVERIFIED_REASONS

        self.assertLessEqual(set(LINEAGE_UNVERIFIED_REASONS) - {"no_continuation"}, set(team._UNLINKED_SAY))

    def test_a_return_is_not_kept_when_no_helper_is_out(self) -> None:
        write_unreadable_state_db(self.hermes)
        self.start([unit("a", CHECK_A)])
        self.assertFalse(self.stop("C2"))
        self.assertNotIn("unlinked_returns", self.record())

    def _note_then_repair(self) -> None:
        """An unlinked return while try 1 is out, then try 1 back, failing, and try 2 out as R1."""
        self.runner = FakeRunner({CHECK_A: [exited(1), exited(0)]})
        self.dispatch_one()
        write_unreadable_state_db(self.hermes)
        self.assertFalse(self.stop("stray"))
        self.assertEqual([note["child_session_id"] for note in self.record()["unlinked_returns"]], ["stray"])
        self.assertTrue(self.stop("C1"))
        repair = self.reconcile()
        self.assertTrue(team.observe_dispatch(self.home, SESSION, child_session_id="R1",
                                              goal=self.entries(repair)[0]["goal"], now=self.now()))

    def test_an_unlinked_return_from_before_a_helper_started_is_not_said_for_it(self) -> None:
        self._note_then_repair()
        result = self.reconcile()
        self.assertEqual(result["say"], "1 part(s) are still being worked on; nothing is checked until they come back.")
        self.assertNotIn("unlinked_returns", result)

    def test_an_unlinked_return_from_before_a_helper_started_does_not_name_its_stop(self) -> None:
        # Mutation: count every unlinked return toward the block reason, not
        # only those seen since this try started.
        self._note_then_repair()
        self.clock += team.DELEGATION_STALE_SECONDS + 10
        result = self.reconcile()
        self.assertEqual(result["units"][0]["blocked"]["reason"], "delegation_not_returned")

    def test_a_lineage_cycle_answers_instead_of_hanging(self) -> None:
        # No shape Hermes writes, but this reader runs inside Hermes'
        # `subagent_stop`, so it must end on one. Mutation: drop the seen-set
        # check (the count alone never grows on a cycle).
        write_sessions(
            self.hermes,
            compacted("C1", at=1100.0, parent="C2", source="subagent", delegate_from=SESSION),
            compacted("C2", at=1200.0, parent="C1", source="subagent", delegate_from=SESSION, started_at=1100.0),
        )
        found = within_seconds(5.0, lambda: compaction_ancestors(str(self.hermes), "C1"))
        self.assertEqual((found["status"], found["reason"]), ("unverified", "lineage_too_long"))

    def test_the_stop_hook_follows_the_compaction(self) -> None:
        from omh.plugin_bundle.omh.hooks.session_hooks import subagent_start, subagent_stop

        nudge_budget.reset_nudge_budget()
        self.addCleanup(nudge_budget.reset_nudge_budget)
        goal = self.entries(self.start([unit("a", CHECK_A)]))[0]["goal"]
        self.helper_rows(hermes_row("C2", parent="C1", source="subagent", delegate_from=SESSION, started_at=1100.0))
        common = {"omh_home": str(self.home), "hermes_home": str(self.hermes), "parent_session_id": SESSION}
        subagent_start(child_session_id="C1", child_goal=goal, **common)
        subagent_stop(child_session_id="C2", **common)
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1")), {"a": "awaiting_check"})


class CompactedParentKeepsItsTeam(TeamHarness):
    """The parent compacts too: its hooks then name a later segment, and the team stays under the first.

    Hermes reads `parent_agent.session_id` at `subagent_stop` and the
    parent's id at `subagent_start` (`tools/delegate_tool_results.py`,
    `tools/delegate_tool.py`), so a parent that rotated after `team_start`
    names `P2` while the team record lives under `P`. The team is found only
    through the links Hermes wrote from `P2` back to `P`.

    Mutations: look only under the reporting session; follow any parent.
    """

    def setUp(self) -> None:
        super().setUp()
        self.hermes = self.home.parent / "hermes"
        self.hermes.mkdir()
        nudge_budget.reset_nudge_budget()
        self.addCleanup(nudge_budget.reset_nudge_budget)
        self.goal = self.entries(self.start([unit("a", CHECK_A)]))[0]["goal"]

    def hooks(self, parent: str) -> tuple[Callable[..., object], Callable[..., object], dict[str, str]]:
        from omh.plugin_bundle.omh.hooks.session_hooks import subagent_start, subagent_stop

        return subagent_start, subagent_stop, {"omh_home": str(self.home), "hermes_home": str(self.hermes),
                                               "parent_session_id": parent}

    def rotated(self, *more: dict[str, object]) -> None:
        write_sessions(self.hermes, compacted(SESSION, at=1100.0), hermes_row("P2", parent=SESSION, started_at=1100.0),
                       *more)

    def state(self) -> str:
        return self.states(team.team_status(self.ctx, team_id="t1"))["a"]

    def test_a_stop_naming_the_new_segment_brings_the_helper_back(self) -> None:
        start, stop, under_p = self.hooks(SESSION)
        start(child_session_id="C1", child_goal=self.goal, **under_p)
        self.rotated(hermes_row("C1", parent=SESSION, source="subagent", delegate_from=SESSION))
        stop(child_session_id="C1", **self.hooks("P2")[2])
        self.assertEqual(self.state(), "awaiting_check")

    def test_a_helper_dispatched_from_the_new_segment_binds_and_comes_back(self) -> None:
        start, stop, under_p2 = self.hooks("P2")
        self.rotated(hermes_row("C1", parent="P2", source="subagent", delegate_from="P2", started_at=1150.0))
        start(child_session_id="C1", child_goal=self.goal, **under_p2)
        self.assertEqual(self.state(), "dispatched")
        stop(child_session_id="C1", **under_p2)
        self.assertEqual(self.state(), "awaiting_check")
        self.assertFalse(team.teams_dir(self.home, "P2").exists())

    def test_sessions_that_do_not_continue_the_parent_find_nothing(self) -> None:
        # An unrelated session, a fork of the parent, and a forged row naming
        # the parent whose inherited marker is not the parent's.
        start, stop, under_p = self.hooks(SESSION)
        start(child_session_id="C1", child_goal=self.goal, **under_p)
        self.rotated(
            hermes_row("Q"),
            hermes_row("B", parent=SESSION, config={"_branched_from": SESSION}, started_at=1100.0),
            hermes_row("F", parent=SESSION, delegate_from="someone-else", started_at=1100.0),
        )
        for parent in ("Q", "B", "F"):
            with self.subTest(parent=parent):
                stop(child_session_id="C1", **self.hooks(parent)[2])
                self.assertEqual(self.state(), "dispatched")

    def test_a_parent_that_was_never_closed_for_compaction_is_not_an_earlier_segment(self) -> None:
        start, stop, under_p = self.hooks(SESSION)
        start(child_session_id="C1", child_goal=self.goal, **under_p)
        write_sessions(self.hermes, hermes_row(SESSION), hermes_row("Y", parent=SESSION, started_at=1100.0))
        stop(child_session_id="C1", **self.hooks("Y")[2])
        self.assertEqual(self.state(), "dispatched")


class HelperPromptIsTrueUnderEitherSetting(TeamHarness):
    """TEAM-1. Mutation: put back "in the shared workspace" or "in the same workspace" in the helper's context.

    With Hermes' worktree isolation on, a helper edits a worktree of its own, so
    a line telling it the workspace is shared is false. The context instead says
    where the check runs, which is true under both settings, and what an
    isolated helper must do for its edits to be checked.
    """

    def test_no_line_claims_a_shared_workspace(self) -> None:
        self.runner = FakeRunner({CHECK_A: [exited(1), exited(0)]})
        started = self.start([unit("a", CHECK_A)])
        self.dispatch_all(started)
        repair = self.reconcile()
        for entry in (*self.entries(started), *self.entries(repair)):
            with self.subTest(goal=entry["goal"][:30]):
                for claim in ("shared workspace", "same workspace"):
                    self.assertNotIn(claim, entry["context"])
                self.assertIn(f"When you stop, OMH runs this command in the parent session's workspace and the "
                              f"part counts as done only if it exits 0 there: {CHECK_A}", entry["context"])
                self.assertIn("If Hermes gave you a git worktree of your own, your edits reach that workspace only "
                              "when the parent session merges your branch, so commit them on your branch before "
                              "you stop.", entry["context"])


class UnmergedHelperWorkIsNeverChecked(TeamHarness):
    """TEAM-2. Mutation: drop `_require_helper_work_merged`, or read an unmerged state as merged.

    Before this guard a check ran in this workspace while the helper's work was
    still on its own branch, and a pass accepted a part whose work nobody checked.
    """

    def refused(self) -> team.TeamRefusal:
        with self.assertRaises(team.TeamRefusal) as caught:
            self.reconcile()
        return caught.exception

    def test_commits_not_in_this_workspace_refuse_and_name_the_branch(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]), [SUBAGENT_A])
        self.helper_states[BRANCH_A] = "commits_not_merged"
        refusal = self.refused()
        self.assertEqual(refusal.reason, "helper_branch_unmerged")
        self.assertEqual(refusal.unmerged, ({"unit_id": "a", "branch": BRANCH_A, "state": "commits_not_merged"},))
        self.assertIn(BRANCH_A, refusal.say)
        self.assertIn("If one cannot be merged, report that part as blocked and stop.", refusal.say)
        self.assertEqual(self.runner.calls, [])
        status = team.team_status(self.ctx, team_id="t1")
        self.assertEqual((self.states(status), status["units"][0]["attempts_used"]), ({"a": "awaiting_check"}, 1))
        # The session merged the branch: the same call now checks the part.
        self.helper_states[BRANCH_A] = "merged"
        self.assertEqual(self.states(self.reconcile()), {"a": "accepted"})
        self.assertEqual(self.runner.calls, [CHECK_A])

    def test_uncommitted_changes_in_the_helpers_worktree_refuse_too(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]), [SUBAGENT_A])
        self.helper_states[BRANCH_A] = "uncommitted_changes"
        refusal = self.refused()
        self.assertEqual(refusal.unmerged, ({"unit_id": "a", "branch": BRANCH_A, "state": "uncommitted_changes"},))
        self.assertIn("commit what belongs to the part on that branch, and remove what does not, or the whole "
                      "worktree once its work is merged", refusal.say)
        self.assertEqual(self.runner.calls, [])

    def test_one_unmerged_branch_holds_every_check(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A), unit("b", CHECK_B)]), [SUBAGENT_A, SUBAGENT_B])
        self.helper_states.update({BRANCH_A: "merged", BRANCH_B: "commits_not_merged"})
        refusal = self.refused()
        self.assertEqual([item["branch"] for item in refusal.unmerged], [BRANCH_B])
        self.assertEqual(self.runner.calls, [])

    def test_the_refusal_carries_the_branches_to_the_caller(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]), [SUBAGENT_A])
        self.helper_states[BRANCH_A] = "commits_not_merged"
        payload = team.refusal_result("team_reconcile", self.refused())
        self.assertEqual((payload["status"], payload["reason"], payload["delegate_task"]),
                         ("refused", "helper_branch_unmerged", None))
        self.assertEqual(payload["unmerged"], [{"unit_id": "a", "branch": BRANCH_A, "state": "commits_not_merged"}])
        other = team.refusal_result("team_reconcile", team.TeamRefusal("team_not_found", "No team."))
        self.assertNotIn("unmerged", other)

    def test_the_team_lane_reference_names_the_code_the_engine_emits(self) -> None:
        from omh.skills.packaging import builtin_skill_reference_templates

        self.dispatch_all(self.start([unit("a", CHECK_A)]), [SUBAGENT_A])
        self.helper_states[BRANCH_A] = "uncommitted_changes"
        refusal = self.refused()
        text = next(" ".join(template.content.split()) for template in builtin_skill_reference_templates()
                    if (template.skill_name, template.relative_path) == ("ultrawork", "references/team-lane.md"))
        self.assertIn(f"refused as `{refusal.reason}`", text)
        for state in ("commits_not_merged", "uncommitted_changes"):
            self.assertIn(f"`state: {state}`", text)
        self.assertIn("OMH never merges", text)


class MergedOrUnisolatedHelpersAreChecked(TeamHarness):
    """Positive controls: the guard refuses only work that is not in this workspace."""

    def test_isolation_off_or_a_pruned_branch_is_checked(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]), [SUBAGENT_A])
        self.assertEqual(self.states(self.reconcile()), {"a": "accepted"})
        self.assertEqual((self.helper_reads, self.runner.calls), ([BRANCH_A], [CHECK_A]))

    def test_a_merged_branch_is_checked(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]), [SUBAGENT_A])
        self.helper_states[BRANCH_A] = "merged"
        self.assertEqual(self.states(self.reconcile()), {"a": "accepted"})

    def test_a_host_that_reports_no_subagent_id_reads_no_branch(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        self.assertEqual(self.states(self.reconcile()), {"a": "accepted"})
        self.assertEqual(self.helper_reads, [])


class AnUnreadableHelperBranchIsInconclusive(TeamHarness):
    """Mutation: run the check when git could not read the branch, or refuse it on every call so the team never stops."""

    def test_it_spends_no_try_and_three_block_the_part(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]), [SUBAGENT_A])
        self.helper_states[BRANCH_A] = "unreadable"
        first = self.reconcile()
        self.assertEqual((self.states(first), first["units"][0]["attempts_used"]), ({"a": "awaiting_check"}, 1))
        self.assertIn("git could not say whether its helper's branch is merged", first["say"])
        self.reconcile()
        third = self.reconcile()
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(third["units"][0]["blocked"]["reason"], "check_inconclusive")
        self.assertEqual(third["units"][0]["blocked"]["last_reason"], "helper_branch_unreadable")
        self.assertEqual(third["team_state"], "blocked")

    def test_an_answer_outside_the_closed_states_is_unreadable(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]), [SUBAGENT_A])
        self.helper_states[BRANCH_A] = "probably fine"
        self.assertIn("git could not say", self.reconcile()["say"])
        self.assertEqual(self.runner.calls, [])


class TheHostsSubagentIdIsKept(TeamHarness):
    """Mutation: drop `child_subagent_id` in `observe_team_dispatch`, or keep an id outside the closed pattern."""

    def test_the_start_hook_records_it_and_the_guard_reads_its_branch(self) -> None:
        from omh.plugin_bundle.omh.hooks.session_hooks import subagent_start, subagent_stop

        nudge_budget.reset_nudge_budget()
        self.addCleanup(nudge_budget.reset_nudge_budget)
        goal = self.entries(self.start([unit("a", CHECK_A)]))[0]["goal"]
        common = {"omh_home": str(self.home), "hermes_home": str(self.home), "parent_session_id": SESSION}
        subagent_start(child_session_id="hook-child", child_goal=goal, child_subagent_id=SUBAGENT_A, **common)
        self.assertEqual(self.record()["units"][0]["attempts"][0]["dispatch"]["subagent_id"], SUBAGENT_A)
        subagent_stop(child_session_id="hook-child", **common)
        self.helper_states[BRANCH_A] = "commits_not_merged"
        with self.assertRaises(team.TeamRefusal) as caught:
            self.reconcile()
        self.assertEqual(caught.exception.reason, "helper_branch_unmerged")

    def test_an_id_outside_the_pattern_is_not_kept(self) -> None:
        for bad in ("../escape", "-rf", "", "sa 0", "a" * 65, "sa-0/x"):
            with self.subTest(bad=bad):
                self.assertIsNone(team._SUBAGENT_ID.fullmatch(bad))
        self.dispatch_all(self.start([unit("a", CHECK_A)]), ["../escape"])
        self.assertNotIn("subagent_id", self.record()["units"][0]["attempts"][0]["dispatch"])
        self.assertEqual(self.states(self.reconcile()), {"a": "accepted"})
        self.assertEqual(self.helper_reads, [])


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True, text=True).stdout


@unittest.skipUnless(GIT, "a local git binary is required to lay out helper worktrees")
class HelperWorkReadsRealWorktrees(TeamHarness):
    """`team_tool._helper_work` on worktrees laid out the way Hermes' isolation lays them out.

    Mutation: swap the merge-base answers, skip the worktree's own fingerprint,
    or read a missing branch as anything but absent.
    """

    def setUp(self) -> None:
        super().setUp()
        from omh.plugin_bundle.omh.tools import team_tool

        self.probe = team_tool._helper_work
        _git(self.workdir, "init", "-q")
        for key, value in (("user.email", "omh-tests@example.test"), ("user.name", "OMH Tests"),
                           ("core.autocrlf", "false"), ("commit.gpgsign", "false"), ("gc.auto", "0"),
                           ("maintenance.auto", "false")):
            _git(self.workdir, "config", key, value)
        (self.workdir / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
        (self.workdir / "app.txt").write_text("v1\n", encoding="utf-8")
        _git(self.workdir, "add", ".")
        _git(self.workdir, "commit", "-q", "-m", "base")

    def isolate(self, subagent_id: str) -> Path:
        """What Hermes' `create_subagent_worktree` does for one child."""
        worktree = self.workdir / ".worktrees" / f"subagent-{subagent_id}"
        _git(self.workdir, "worktree", "add", "-q", str(worktree), "-b", team.helper_branch(subagent_id), "HEAD")
        return worktree

    def commit_in(self, worktree: Path, text: str) -> None:
        (worktree / "app.txt").write_text(text, encoding="utf-8")
        _git(worktree, "commit", "-q", "-am", text.strip())

    def test_each_state_as_the_work_moves_into_this_workspace(self) -> None:
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "absent")
        worktree = self.isolate(SUBAGENT_A)
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "merged")
        (worktree / "app.txt").write_text("v2\n", encoding="utf-8")
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "uncommitted_changes")
        _git(worktree, "commit", "-q", "-am", "v2")
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "commits_not_merged")
        _git(self.workdir, "merge", "-q", "--no-ff", "-m", "merge helper", BRANCH_A)
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "merged")

    def test_a_branch_whose_worktree_was_removed_is_still_read(self) -> None:
        worktree = self.isolate(SUBAGENT_A)
        self.commit_in(worktree, "v2\n")
        _git(self.workdir, "worktree", "remove", "--force", str(worktree))
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "commits_not_merged")

    def test_the_branch_checked_out_here_is_this_workspaces_own_state(self) -> None:
        self.commit_in(self.isolate(SUBAGENT_A), "v2\n")
        _git(self.workdir, "worktree", "remove", "--force", str(self.workdir / ".worktrees" / f"subagent-{SUBAGENT_A}"))
        _git(self.workdir, "checkout", "-q", BRANCH_A)
        (self.workdir / "app.txt").write_text("session edit\n", encoding="utf-8")
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "merged")

    def leave_branch_and_commit(self, *move: str) -> Path:
        worktree = self.isolate(SUBAGENT_A)
        _git(worktree, *move)
        self.commit_in(worktree, "v2\n")
        return worktree

    def test_a_helper_that_committed_on_a_detached_head_is_unmerged(self) -> None:
        """P1: Hermes' branch still sits at the base commit; the work is on the worktree's HEAD."""
        self.leave_branch_and_commit("checkout", "-q", "--detach")
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "commits_not_merged")

    def test_a_helper_that_committed_on_a_branch_of_its_own_is_unmerged_until_that_is_merged(self) -> None:
        """P2, then its positive control: merging the helper's own branch is what clears it."""
        self.leave_branch_and_commit("checkout", "-q", "-b", "mine")
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "commits_not_merged")
        _git(self.workdir, "merge", "-q", "--no-ff", "-m", "merge helper", "mine")
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "merged")

    def test_an_untracked_file_in_a_merged_helpers_worktree_refuses_until_removed(self) -> None:
        worktree = self.isolate(SUBAGENT_A)
        self.commit_in(worktree, "v2\n")
        _git(self.workdir, "merge", "-q", "--no-ff", "-m", "merge helper", BRANCH_A)
        (worktree / "build.log").write_text("noise\n", encoding="utf-8")
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "uncommitted_changes")
        (worktree / "build.log").unlink()
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "merged")

    def test_a_renamed_branch_with_its_worktree_kept_is_found_by_the_path(self) -> None:
        """P4: Hermes' branch is gone, but the worktree at Hermes' path still holds the unmerged commit."""
        self.commit_in(self.isolate(SUBAGENT_A), "v2\n")
        _git(self.workdir, "branch", "-m", BRANCH_A, "renamed-by-helper")
        self.assertEqual(_git(self.workdir, "branch", "--list", BRANCH_A), "")
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "commits_not_merged")

    def test_a_worktree_moved_away_from_hermes_path_is_found_by_its_branch(self) -> None:
        moved = self.workdir.parent / "moved-helper"
        _git(self.workdir, "worktree", "move", str(self.isolate(SUBAGENT_A)), str(moved))
        (moved / "app.txt").write_text("v2\n", encoding="utf-8")
        self.assertEqual(self.probe(self.workdir, BRANCH_A), "uncommitted_changes")

    def test_output_that_will_not_decode_is_unreadable_not_a_store_failure(self) -> None:
        from omh.plugin_bundle.omh.tools import team_tool

        undecodable = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        with mock.patch.object(team_tool._evidence_tool, "run_verification_command", side_effect=undecodable):
            self.assertEqual(self.probe(self.workdir, BRANCH_A), "unreadable")

    def test_outside_git_is_unreadable(self) -> None:
        plain = self.workdir.parent / "plain"
        plain.mkdir()
        self.assertEqual(self.probe(plain, BRANCH_A), "unreadable")

    def test_the_engine_checks_nothing_until_the_branch_is_merged(self) -> None:
        self.helper_work = self.probe
        self.dispatch_all(self.start([unit("a", CHECK_A)]), [SUBAGENT_A])
        self.commit_in(self.isolate(SUBAGENT_A), "v2\n")
        with self.assertRaises(team.TeamRefusal) as caught:
            self.reconcile()
        self.assertEqual(caught.exception.unmerged,
                         ({"unit_id": "a", "branch": BRANCH_A, "state": "commits_not_merged"},))
        self.assertEqual(self.runner.calls, [])
        _git(self.workdir, "merge", "-q", "--no-ff", "-m", "merge helper", BRANCH_A)
        self.assertEqual(self.states(self.reconcile()), {"a": "accepted"})
        self.assertEqual(self.runner.calls, [CHECK_A])


class ForgedRecordsRunNothing(TeamHarness):
    """H2: the record on disk is not a trust root; approval and observed checks live in process memory."""

    def forge(self, team_id: str, command: str) -> None:
        """Write a well-formed record a person never approved, digest recomputed, ready to check."""
        units = [{"unit_id": "a", "title": "Part a", "depends_on": [], "verification_command": command,
                  "plan_item": f"check: `{command}`", "blocked": None, "lease": None,
                  "attempts": [{"n": 1, "reserved_at": "2026-09-28T00:00:00Z", "reemits": 0,
                                "dispatch": {"child_session_id": "c", "dispatched_at": "2026-09-28T00:00:01Z"},
                                "returned_at": "2026-09-28T00:00:02Z", "check": None, "inconclusive": []}]}]
        record = {"schema_version": team.TEAM_SCHEMA_VERSION, "team_id": team_id, "session_ref": SESSION,
                  "plan_ref": "forged", "workdir": str(self.workdir), "created_at": "2026-09-28T00:00:00Z",
                  "max_repair_attempts": 2, "commands_digest": team.frozen_commands_digest(units),
                  "check_timeout_seconds": 600, "units": units, "events": [], "next_seq": 1}
        path = team.team_path(self.home, SESSION, team_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        team.write_team(path, record)

    def test_probe6_a_forged_record_with_a_recomputed_digest_runs_nothing(self) -> None:
        """Mutation: drop `_require_approved` from phase A or from `_take_lease`."""
        self.runner = FakeRunner({"python -m unittest tests/test_evil.py": [exited(0)]})
        self.forge("forged", "python -m unittest tests/test_evil.py")
        with self.assertRaises(team.TeamRefusal) as refused:
            team.team_reconcile(self.ctx, team_id="forged")
        self.assertEqual(refused.exception.reason, "team_not_approved_here")
        self.assertEqual(self.runner.calls, [])

    def test_an_approved_team_after_a_restart_runs_nothing_until_approved_again(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        self.restart()
        with self.assertRaises(team.TeamRefusal) as refused:
            self.reconcile()
        self.assertEqual(refused.exception.reason, "team_not_approved_here")
        self.assertEqual(self.runner.calls, [])
        self.start([unit("a", CHECK_A)])
        self.assertEqual(self.states(self.reconcile()), {"a": "accepted"})
        self.assertEqual(self.runner.calls, [CHECK_A])

    def test_probe3_a_planted_pass_is_not_acceptance(self) -> None:
        """Mutation: trust a stored `passed` without an observed nonce (drop `_distrust_unobserved`)."""
        self.runner = FakeRunner({CHECK_A: [exited(1)]})
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        record = self.record()
        record["units"][0]["attempts"][0]["check"] = {
            "outcome": "passed", "command": CHECK_A, "exit_code": 0, "observed_at": "2026-09-28T00:00:03Z",
            "nonce": "planted"}
        team.write_team(team.team_path(self.home, SESSION, "t1"), record)
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1")), {"a": "awaiting_check"})
        result = self.reconcile()
        self.assertEqual(self.runner.calls, [CHECK_A])
        self.assertEqual(self.states(result), {"a": "repairing"})
        self.assertNotEqual(result["team_state"], "done")

    def test_a_pass_from_before_a_restart_is_earned_again(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        self.assertEqual(self.states(self.reconcile()), {"a": "accepted"})
        self.restart()
        self.start([unit("a", CHECK_A)])
        self.assertEqual(self.states(team.team_status(self.ctx, team_id="t1")), {"a": "awaiting_check"})
        self.assertEqual(self.states(self.reconcile()), {"a": "accepted"})
        self.assertEqual(self.runner.calls, [CHECK_A, CHECK_A])


class PlanBindingIsAnExactCheckField(TeamHarness):
    """H3. Mutation: bind on a substring of the item text instead of an exact `check:` field."""

    def bind(self, command: str, text: str) -> str:
        plan = plan_for(command)
        plan["items"] = [{"text": text, "state": "pending"}]
        plan["items_digest"] = todo_items_digest(plan["items"])
        try:
            self.start([unit("a", command)], plan=plan)
        except team.TeamRefusal as refused:
            return refused.reason
        return "bound"

    def test_prose_and_prefixes_bind_nothing(self) -> None:
        self.assertEqual(self.bind("make deploy", "Do NOT run make deploy under any circumstance"),
                         "command_not_in_accepted_plan")
        self.assertEqual(self.bind("make deploy", "Do NOT run `make deploy` under any circumstance"),
                         "command_not_in_accepted_plan")
        self.assertEqual(self.bind("make", "Run the suite; check: `make test`"), "command_not_in_accepted_plan")
        self.assertEqual(self.bind("make test", "Run the suite; check: `make test`"), "bound")


class ChecksAreBoundedAndLeasedOneAtATime(TeamHarness):
    """M2. Mutation: lease every unit up front, or drop MAX_CHECKS_PER_RECONCILE."""

    def test_one_reconcile_runs_at_most_the_cap_and_leases_each_just_before_it_runs(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A), unit("b", CHECK_B), unit("c", CHECK_C)]))
        leases_while_a_ran: list[object] = []
        self.runner.during = lambda: leases_while_a_ran.extend(
            item["lease"] for item in self.record()["units"])
        first = self.reconcile()
        self.assertEqual(self.runner.calls, [CHECK_A, CHECK_B][:team.MAX_CHECKS_PER_RECONCILE])
        self.assertEqual(first["reason"], "checks_pending")
        self.assertIsNotNone(leases_while_a_ran[0])
        self.assertEqual(leases_while_a_ran[1:], [None, None])
        self.assertEqual(self.states(self.reconcile()), {"a": "accepted", "b": "accepted", "c": "accepted"})
        self.assertEqual(self.runner.calls, [CHECK_A, CHECK_B, CHECK_C])


class InconclusiveOutsideGitAndOnASignal(TeamHarness):
    """M1 and L2. Mutation: run the check in a non-git workspace, or read a signal exit as a failure."""

    def test_a_workspace_outside_git_is_inconclusive_and_runs_nothing(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        for state in ("not_a_repository", "unsupported", "not_a_repository"):
            self.fingerprints = [(state, None)]
            result = self.reconcile()
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(result["units"][0]["attempts_used"], 1)
        self.assertEqual(result["units"][0]["blocked"],
                         {"reason": "check_inconclusive", "last_reason": "workspace_not_tracked",
                          "observed_at": result["units"][0]["blocked"]["observed_at"]})

    def test_a_negative_return_code_is_inconclusive(self) -> None:
        self.runner = FakeRunner({CHECK_A: [exited(-9), exited(0)]})
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        killed = self.reconcile()
        self.assertEqual((self.states(killed), killed["units"][0]["attempts_used"]), ({"a": "awaiting_check"}, 1))
        self.assertIn("stopped by a signal", killed["say"])
        self.assertEqual(self.states(self.reconcile()), {"a": "accepted"})


class HostFloorAtStartAndAtRun(TeamHarness):
    """H1(f) in the engine. Mutation: skip `host_guard` at start or before a check runs."""

    def test_a_command_the_host_refuses_never_starts(self) -> None:
        self.host_refuses[CHECK_A] = "command_refused_by_host"
        with self.assertRaises(team.TeamRefusal) as refused:
            self.start([unit("a", CHECK_A)])
        self.assertEqual(refused.exception.reason, "command_refused_by_host")

    def test_a_command_the_host_refuses_later_blocks_without_running(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        self.host_refuses[CHECK_A] = "host_command_floor_unavailable"
        result = self.reconcile()
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(result["units"][0]["blocked"]["reason"], "host_command_floor_unavailable")


class DamagedRecordsAreRefused(TeamHarness):
    """L1. Mutation: drop `_record_shape_ok` from `read_team`."""

    def test_a_record_missing_fields_is_a_refusal_not_a_crash(self) -> None:
        self.dispatch_all(self.start([unit("a", CHECK_A)]))
        path = team.team_path(self.home, SESSION, "t1")
        good = self.record()
        damages = {
            "title missing": lambda r: r["units"][0].pop("title"),
            "attempt number missing": lambda r: r["units"][0]["attempts"][0].pop("n"),
            "next_seq missing": lambda r: r.pop("next_seq"),
            "unknown parent": lambda r: r["units"][0].__setitem__("depends_on", ["ghost"]),
            "event without a seq": lambda r: r["events"].append({"seq": "x", "event": "started"}),
            "unlinked returns not a list": lambda r: r.__setitem__("unlinked_returns", "C2"),
            "unlinked return without a reason": lambda r: r.__setitem__(
                "unlinked_returns", [{"child_session_id": "C2", "observed_at": "2026-10-20T00:00:00Z"}]),
            "dispatch process without a pid": lambda r: r["units"][0]["attempts"][0]["dispatch"].__setitem__(
                "host", {"started": 1}),
            "lost helpers not a list": lambda r: r["units"][0]["attempts"][0].__setitem__("lost", "child-1"),
        }
        for name, damage in damages.items():
            record = json.loads(json.dumps(good))
            damage(record)
            path.write_text(json.dumps(record), encoding="utf-8", newline="\n")
            for call in (self.reconcile, lambda: team.team_status(self.ctx, team_id="t1")):
                with self.subTest(damage=name), self.assertRaises(team.TeamRefusal) as refused:
                    call()
                self.assertEqual(refused.exception.reason, "team_store_unreadable")


class HiddenCharactersAreRefused(TeamHarness):
    """L3. Mutation: drop `_HIDDEN_CHARACTERS` from the command or the title check."""

    def test_bidi_and_zero_width_characters(self) -> None:
        # One per range in `_HIDDEN_CHARACTERS`: soft hyphen, Arabic letter
        # mark, zero-width, bidi embedding/override, word joiner and invisible
        # operators, bidi isolates, line/paragraph separators, BOM.
        for character in ("\u00ad", "\u061c", "\u200b", "\u200f", "\u202e", "\u2060", "\u2064",
                          "\u2066", "\u2029", "\ufeff"):
            with self.subTest(character=repr(character)):
                with self.assertRaises(team.TeamRefusal) as refused:
                    team.validate_team_command(f"pytest{character} -q")
                self.assertEqual(refused.exception.reason, "command_hidden_characters")
                titled = {**unit("a", CHECK_A), "title": f"Part{character} a"}
                with self.assertRaises(team.TeamRefusal) as refused:
                    self.start([titled])
                self.assertEqual(refused.exception.reason, "invalid_title")


class AgentBoardStaysByteIdentical(unittest.TestCase):
    """`omh_agent_board` outputs as captured at origin/main 1239b36bd, compared as strings.

    Mutation: change any prepared field, the schema text, or an unavailable reason.
    """

    def test_every_pinned_output_is_byte_identical(self) -> None:
        from _agent_board_golden import agent_board_golden_outputs

        expected = json.loads((Path(__file__).parent / "fixtures/agent_board_golden.json").read_text(encoding="utf-8"))
        actual = agent_board_golden_outputs()
        self.assertEqual(sorted(actual), sorted(expected))
        for name, value in expected.items():
            with self.subTest(case=name):
                self.assertEqual(actual[name], value)


class ApprovalKeyParity(unittest.TestCase):
    def test_the_gate_and_the_engine_derive_the_same_always_key(self) -> None:
        commands = [CHECK_A, "npm test"]
        self.assertEqual(team_gate.team_start_rule_key(commands), team.approval_rule_key(commands))


if __name__ == "__main__":
    unittest.main()
