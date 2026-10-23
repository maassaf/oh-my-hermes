"""The run rules of `omh_dag` that a goal loop leans on: repairs, re-verification, blocked nodes, the stored record.

Each test drives the tool the way Hermes calls it (`test_dag_tool.call`) and
reads what the parent would read: the task context of the next call, the run
state, the rows, and the stored record.
"""

from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from _delegation_state import PARENT, homes  # noqa: E402
from omh.plugin_bundle.omh import dag_run  # noqa: E402
from omh.plugin_bundle.omh.tools import dag_tool  # noqa: E402
from test_dag_tool import call, child_started, handoff, node, slots_table  # noqa: E402


def calls_by_node(result: dict) -> dict[str, dict]:
    return {node_id: task for call_row in result.get("calls", [])
            for node_id, task in zip(call_row["nodes"], call_row["tasks"])}


def states(result: dict) -> dict[str, str]:
    return {row["id"]: row["state"] for row in result["nodes"]}


def record_of(home, run: str = "feat") -> dict:
    return json.loads(dag_run.record_path(home.omh, PARENT, run).read_text(encoding="utf-8"))


class SavedGoalLifecycleTests(unittest.TestCase):
    """Delivery checks only: no live worker compliance is inferred."""

    def test_retry_repair_reverification_and_extend_keep_the_saved_goal(self) -> None:
        goal = "Retain executable engine and output quality; compare the same task before accepting."
        with homes() as home:
            slots_table(home)
            definitions = [node("api", scope=("a.py",)), node("verify", depends_on=("api",))]
            call(action="define", run="feat", goal=goal, nodes=definitions)
            # A repeated definition does not replace the immutable saved goal.
            call(action="define", run="feat", goal="Only package tests now", nodes=definitions)
            retried = call(action="settle", run="feat", node="api", outcome="failed", provider_error=True)
            self.assertEqual(retried["calls"][0]["route"]["action"], "fallback")
            call(action="settle", run="feat", node="api", outcome="failed")
            repaired = call(action="repair", run="feat", node="api", nodes=[node("api-fix", scope=("a.py",))])
            handoff(home, "feat", "api-fix")
            verified = call(action="settle", run="feat", node="api-fix", outcome="accepted")
            self.assertIn("api-fix.md", calls_by_node(verified)["verify"]["context"])
            call(action="settle", run="feat", node="verify", outcome="failed")
            verify_repair = call(action="repair", run="feat", node="verify", nodes=[node("fix", scope=("a.py",))])
            handoff(home, "feat", "fix")
            reverified = call(action="settle", run="feat", node="fix", outcome="accepted")
            handoff(home, "feat", "verify")
            done = call(action="settle", run="feat", node="verify", outcome="accepted")
            self.assertEqual(done["state"], "done")
            extended = call(action="extend", run="feat", nodes=[node("craft", depends_on=("verify",), scope=("craft.py",)),
                            node("verify-output", depends_on=("craft",))])
            handoff(home, "feat", "craft")
            extend_verified = call(action="settle", run="feat", node="craft", outcome="accepted")
            for result, node_id in ((retried, "api"), (repaired, "api-fix"), (verified, "verify"),
                                    (verify_repair, "fix"), (reverified, "verify"), (extended, "craft"),
                                    (extend_verified, "verify-output")):
                with self.subTest(node=node_id, result=result):
                    self.assertEqual(result["status"], "ok", result)
                    context = calls_by_node(result)[node_id]["context"]
                    self.assertIn(f"Run goal (saved):\n{goal}\n", context)
                    self.assertIn("not permission to widen your SCOPE", context)
                    if "verify" in node_id:
                        self.assertIn("Check the complete run goal", context)
            self.assertEqual(record_of(home)["goal"], goal)

    def test_ready_reoffers_reloaded_legacy_records_without_inventing_a_goal(self) -> None:
        for legacy in (None, "", "   "):
            with self.subTest(goal=legacy), homes() as home:
                slots_table(home)
                call(action="define", run="feat", nodes=[node("api")])
                path = dag_run.record_path(home.omh, PARENT, "feat")
                record = record_of(home)
                if legacy is None:
                    record.pop("goal")
                else:
                    record["goal"] = legacy
                path.write_text(json.dumps(record), encoding="utf-8")
                with patch.object(dag_tool.time, "time", return_value=time.time() + dag_run.HANDOUT_STALE_SECONDS + 1):
                    ready = call(action="ready", run="feat")
                task = calls_by_node(ready)["api"]
                self.assertNotIn("Run goal (saved):", task["context"])
                self.assertNotIn("None", task["context"])
                self.assertEqual(task["goal"], "Node api [dag:feat/api]")

    def test_ready_reoffers_saved_goal_at_its_existing_limit(self) -> None:
        goal = "Q" * dag_run.MAX_GOAL_CHARS
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", goal=goal, nodes=[node("api")])
            with patch.object(dag_tool.time, "time", return_value=time.time() + dag_run.HANDOUT_STALE_SECONDS + 1):
                ready = call(action="ready", run="feat")
            self.assertIn(f"Run goal (saved):\n{goal}\n", calls_by_node(ready)["api"]["context"])


class RepairDataFlowTests(unittest.TestCase):
    def test_a_dependent_reads_the_repair_groups_handoff_not_the_failed_nodes(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=[node("a", scope=("a.py",)), node("b", depends_on=("a",)),
                                                     node("verify", depends_on=("b",), label="Verify")])
            call(action="settle", run="feat", node="a", outcome="blocked", reason="no key")
            call(action="repair", run="feat", node="a", nodes=[node("a-fix", scope=("a.py",))])
            handoff(home, "feat", "a-fix")
            result = call(action="settle", run="feat", node="a-fix", outcome="accepted")
            context = calls_by_node(result)["b"]["context"]
        read_first = next(line for line in context.splitlines() if line.startswith("Read first:"))
        paths = read_first.removeprefix("Read first: ").split(". A missing file")[0].split(", ")
        self.assertEqual([Path(path).name for path in paths], ["a-fix.md"])

    def test_a_repair_may_write_what_the_failed_nodes_dependents_write(self) -> None:
        # b waits for a, and a waits for its repair group: the order is already there, so the scope is not refused.
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=[node("a", scope=("x.py",)),
                                                     node("b", depends_on=("a",), scope=("x.py",))])
            call(action="settle", run="feat", node="a", outcome="failed")
            repaired = call(action="repair", run="feat", node="a", nodes=[node("a-fix", scope=("x.py",))])
        self.assertEqual(repaired["status"], "ok", repaired)
        self.assertEqual([c["nodes"] for c in repaired["calls"]], [["a-fix"]])


class ReVerificationTests(unittest.TestCase):
    """A failed verification is verified again: its repair never stands in for it."""

    def test_verifier_receives_whole_goal_acceptance_instructions_after_reload(self) -> None:
        goal = "Keep the source engine and craft quality, not only a working package."
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", goal=goal, nodes=[node("api", scope=("a.py",)),
                 node("verify", depends_on=("api",), label="Verify the feature")])
            handoff(home, "feat", "api", "Package tests pass; output comparison not run.")
            result = call(action="settle", run="feat", node="api", outcome="accepted")
            context = calls_by_node(result)["verify"]["context"]
        self.assertIn(f"Run goal (saved):\n{goal}\n", context)
        self.assertIn("Check the complete run goal, not only this node's local VERIFY.", context)
        self.assertIn("Name each unmet or unobserved goal criterion; producer claims are not verification evidence.",
                      context)

    def _failed_verify(self, home) -> None:
        slots_table(home)
        call(action="define", run="feat", nodes=[node("api", scope=("a.py",)),
                                                 node("verify", depends_on=("api",), label="Verify the feature")])
        handoff(home, "feat", "api")
        call(action="settle", run="feat", node="api", outcome="accepted")
        call(action="settle", run="feat", node="verify", outcome="failed", reason="2 tests red")

    def test_a_repaired_verification_runs_again_before_the_run_is_done(self) -> None:
        with homes() as home:
            self._failed_verify(home)
            repaired = call(action="repair", run="feat", node="verify", nodes=[node("fix", scope=("a.py",))])
            handoff(home, "feat", "fix")
            fixed = call(action="settle", run="feat", node="fix", outcome="accepted")
            rerun = calls_by_node(fixed).get("verify")
            handoff(home, "feat", "verify")
            done = call(action="settle", run="feat", node="verify", outcome="accepted")
            stored = record_of(home)
        self.assertNotIn("verification_misses:fix", repaired["lint"])
        self.assertEqual(fixed["state"], "running")
        self.assertIsNotNone(rerun, fixed)
        self.assertTrue(rerun["goal"].endswith(" [dag:feat/verify]"))
        self.assertEqual(states(fixed)["verify"], "handed_out")
        self.assertEqual(done["state"], "done")
        self.assertEqual(states(done)["verify"], "accepted")
        verify = next(item for item in stored["nodes"] if item["id"] == "verify")
        self.assertEqual([attempt["outcome"] for attempt in verify["attempts"]], ["failed", "accepted"])

    def test_a_failed_reverification_takes_the_second_cycle_then_the_run_stops(self) -> None:
        with homes() as home:
            self._failed_verify(home)
            call(action="repair", run="feat", node="verify", nodes=[node("fix", scope=("a.py",))])
            handoff(home, "feat", "fix")
            call(action="settle", run="feat", node="fix", outcome="accepted")
            again = call(action="settle", run="feat", node="verify", outcome="failed", reason="1 test red")
            call(action="repair", run="feat", node="verify", nodes=[node("fix2", scope=("a.py",))])
            handoff(home, "feat", "fix2")
            call(action="settle", run="feat", node="fix2", outcome="accepted")
            last = call(action="settle", run="feat", node="verify", outcome="failed", reason="still red")
            refused = call(action="repair", run="feat", node="verify", nodes=[node("fix3")])
        self.assertEqual(again["state"], "needs_repair")
        self.assertEqual(last["state"], "stopped")
        self.assertEqual(last["open"], ["verify"])
        self.assertEqual(refused["reason"], "repair_limit_reached")

    def test_a_repaired_producer_still_stands_in_for_its_node(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=[node("api", scope=("a.py",)),
                                                     node("verify", depends_on=("api",), label="Verify")])
            call(action="settle", run="feat", node="api", outcome="failed")
            call(action="repair", run="feat", node="api", nodes=[node("api-fix", scope=("a.py",))])
            handoff(home, "feat", "api-fix")
            result = call(action="settle", run="feat", node="api-fix", outcome="accepted")
        self.assertEqual(states(result)["api"], "repaired")
        self.assertEqual(list(calls_by_node(result)), ["verify"])

    def test_repair_and_extend_return_the_lint(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=[node("api", scope=("a.py",)),
                                                     node("verify", depends_on=("api",), label="Verify")])
            call(action="settle", run="feat", node="api", outcome="failed")
            repaired = call(action="repair", run="feat", node="api", nodes=[node("api-fix", scope=("a.py",),
                                                                               prompt="just fix it")])
        self.assertIn("missing_markers:api-fix:TASK,DELIVERABLE,SCOPE,VERIFY,STOP_WHEN", repaired["lint"])
        self.assertIn("topology", repaired)


class BlockedNodeTests(unittest.TestCase):
    def test_a_blocked_node_is_never_counted_done_and_holds_everything_below_it(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=[node("a", scope=("a.py",)), node("b", depends_on=("a",)),
                                                     node("c", depends_on=("b",)), node("side")])
            handoff(home, "feat", "side")
            call(action="settle", run="feat", node="side", outcome="accepted")
            blocked = call(action="settle", run="feat", node="a", outcome="blocked", reason="no key")
        rows = {row["id"]: row for row in blocked["nodes"]}
        self.assertEqual(blocked["counts"]["done"], 1)
        self.assertEqual(blocked["counts"]["failed"], 1)
        self.assertEqual(blocked["state"], "needs_repair")
        self.assertEqual((rows["b"]["state"], rows["b"]["held_by"]), ("held", ["a"]))
        # Held through b: the failed node is an ancestor, not a parent.
        self.assertEqual((rows["c"]["state"], rows["c"]["held_by"]), ("held", ["a"]))
        self.assertFalse(blocked["calls"])


class AttemptBindingTests(unittest.TestCase):
    def test_a_retry_does_not_take_the_child_of_the_attempt_before_it(self) -> None:
        with homes() as home:
            slots_table(home)
            moment = time.time()
            with patch.object(dag_tool.time, "time", return_value=moment):
                call(action="define", run="feat", nodes=[node("a", scope=("a.py",))])
            child_started(home, "feat", "a", now=moment + 1)
            with patch.object(dag_tool.time, "time", return_value=moment + 60):
                retried = call(action="settle", run="feat", node="a", outcome="failed", provider_error=True)
        # The first attempt's child started before the retry was handed out: the retry has no child yet.
        self.assertEqual(states(retried)["a"], "handed_out")
        self.assertEqual(retried["calls"][0]["route"]["action"], "fallback")


class ProviderErrorTests(unittest.TestCase):
    def test_provider_error_is_a_bool_or_its_two_words(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=[node("a"), node("b"), node("c")])
            word_false = call(action="settle", run="feat", node="a", outcome="failed", provider_error="false")
            word_true = call(action="settle", run="feat", node="b", outcome="failed", provider_error="true")
            other = call(action="settle", run="feat", node="c", outcome="failed", provider_error="yes")
        self.assertEqual(word_false["settled"]["outcome"], "failed")
        self.assertEqual(word_true["settled"]["outcome"], "retry")
        self.assertEqual(other["reason"], "invalid_provider_error")


class RecordStorageTests(unittest.TestCase):
    def test_the_record_is_private_from_its_first_byte(self) -> None:
        with homes() as home:
            slots_table(home)
            # chmod never runs: the file must still be created 0600, not widened by the umask and narrowed later.
            with patch.object(dag_run.os, "chmod"):
                call(action="define", run="feat", nodes=[node("a")])
            mode = dag_run.record_path(home.omh, PARENT, "feat").stat().st_mode & 0o777
        if os.name != "nt":  # Windows has no POSIX mode bits to check
            self.assertEqual(mode, 0o600)

    def test_a_record_too_large_or_holding_a_cycle_is_not_read(self) -> None:
        with homes() as home:
            slots_table(home)
            call(action="define", run="feat", nodes=[node("a"), node("b", depends_on=("a",))])
            path = dag_run.record_path(home.omh, PARENT, "feat")
            good = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNotNone(dag_run.read_run(path))
            cyclic = json.loads(json.dumps(good))
            cyclic["nodes"][0]["depends_on"] = ["b"]
            path.write_text(json.dumps(cyclic), encoding="utf-8")
            self.assertIsNone(dag_run.read_run(path))
            padded = json.loads(json.dumps(good))
            padded["pad"] = "x" * dag_run.MAX_RECORD_BYTES
            path.write_text(json.dumps(padded), encoding="utf-8")
            self.assertIsNone(dag_run.read_run(path))

    def test_a_full_session_drops_its_oldest_finished_run_for_a_new_one(self) -> None:
        with homes() as home:
            slots_table(home)
            for index in range(dag_run.MAX_RUNS_PER_SESSION):
                call(action="define", run=f"r{index}", nodes=[node("a")])
                if index == 0:
                    call(action="cancel", run="r0")
            newest = call(action="define", run="newest", nodes=[node("a")])
            runs = sorted(record["run"] for record, _path in dag_run.session_runs(home.omh, PARENT))
        self.assertEqual(newest["status"], "ok", newest)
        self.assertEqual(len(runs), dag_run.MAX_RUNS_PER_SESSION)
        self.assertNotIn("r0", runs)
        self.assertIn("newest", runs)

    def test_a_full_session_with_no_finished_run_refuses(self) -> None:
        with homes() as home:
            slots_table(home)
            for index in range(dag_run.MAX_RUNS_PER_SESSION):
                call(action="define", run=f"r{index}", nodes=[node("a")])
            refused = call(action="define", run="newest", nodes=[node("a")])
        self.assertEqual(refused["reason"], "too_many_runs")


class LimitTests(unittest.TestCase):
    def test_forty_eight_nodes_fit_and_forty_nine_do_not(self) -> None:
        with homes() as home:
            slots_table(home)
            fits = call(action="define", run="big", nodes=[node(f"n{index}") for index in range(dag_run.MAX_NODES)])
            over = call(action="define", run="bigger",
                        nodes=[node(f"n{index}") for index in range(dag_run.MAX_NODES + 1)])
        self.assertEqual(fits["status"], "ok", fits)
        self.assertEqual(over["reason"], "too_many_nodes")

    def test_one_node_needs_no_verification_node(self) -> None:
        self.assertNotIn("no_verification_node", dag_run.lint([dag_run._validated_node(node("a"))]))

    def test_the_configured_cap_wins_over_the_environment(self) -> None:
        with homes() as home:
            home.hermes.joinpath("config.yaml").write_text("delegation:\n  max_concurrent_children: 3\n")
            with patch.dict(os.environ, {dag_tool.MAX_CONCURRENT_ENV: "7"}):
                self.assertEqual(dag_tool.free_slots(home.hermes)["cap"], 3)
            home.hermes.joinpath("config.yaml").write_text("delegation:\n  other: 1\n")
            with patch.dict(os.environ, {dag_tool.MAX_CONCURRENT_ENV: "7"}):
                self.assertEqual(dag_tool.free_slots(home.hermes)["cap"], 7)


if __name__ == "__main__":
    unittest.main()
