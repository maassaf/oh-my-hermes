"""A delegation route is the route of the conversation that set it (#2251).

The three `delegation.*` keys are one per profile, and a released Hermes reads
a child's model from them alone. Two sessions routing close together lost the
earlier session's route between its `set` and its `delegate_task`: the child
ran on the other session's model with nothing reported, and the other
session's turn-end restore could take the route out from under it.

Each test drives the real host surfaces: the tool for `set`/`clear`/
`fallback`, `pre_tool_call` for the moment just before Hermes reads the keys
for a `delegate_task`, and `on_session_end` for the turn end. What a child
runs on is what the keys hold when `pre_tool_call` returns.

Every test passes explicit temporary homes. Nothing here may read or write
the developer's own `~/.hermes` or `~/.omh`.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from _hermes_session_rows import compacted, row, write_sessions
from omh.plugin_bundle.omh import delegation_route_restore
from omh.plugin_bundle.omh.awareness_delivery import _awareness_delivery_lock
from omh.plugin_bundle.omh.delegation_route_restore import (
    conversation_routes_path,
    delegation_route_restore_path,
    load_conversation_routes,
    load_route_restore_record,
)
from omh.plugin_bundle.omh.delegation_routing import read_delegation_route
from omh.plugin_bundle.omh.hermes_delegation import load_delegation_route_provenance
from omh.plugin_bundle.omh.hooks.session_hooks import on_session_end, on_session_start
from omh.plugin_bundle.omh.hooks.tool_hooks import pre_tool_call
from omh.plugin_bundle.omh.tools.delegate_route_tool import omh_delegate_route_handler

BASE_CONFIG = (
    "model:\n"
    "  provider: test-parent\n"
    "delegation:\n"
    "  max_concurrent_children: 4\n"
)

CHAINS = {
    "schema_version": "mixture_chain_overrides/v1",
    "categories": {
        "deep": [
            {"model": "deep-head", "reasoning_effort": "high"},
            {"model": "deep-next", "reasoning_effort": "high"},
        ],
        "quick": [
            {"model": "quick-head", "reasoning_effort": "low"},
            {"model": "quick-next", "reasoning_effort": "low"},
        ],
    },
}

DEEP = {"model": "deep-head", "reasoning_effort": "high"}
DEEP_NEXT = {"model": "deep-next", "reasoning_effort": "high"}
QUICK = {"model": "quick-head", "reasoning_effort": "low"}


class PerConversationRouteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.hermes_home = root / "hermes"
        self.omh_home = root / "omh"
        self.hermes_home.mkdir()
        (self.hermes_home / "config.yaml").write_text(BASE_CONFIG, encoding="utf-8")
        chains = self.omh_home / "routing" / "model-chains.json"
        chains.parent.mkdir(parents=True)
        chains.write_text(json.dumps(CHAINS), encoding="utf-8")

    def sessions(self, *rows: dict) -> None:
        """The `state.db` rows Hermes writes before a session's first tool call."""
        write_sessions(self.hermes_home, *rows)

    def top_level(self, *ids: str) -> None:
        self.sessions(*(row(session_id, last_activity_at=time.time()) for session_id in ids))

    def homes(self) -> dict[str, str]:
        return {"omh_home": str(self.omh_home), "hermes_home": str(self.hermes_home)}

    def tool(self, session: str | None, task: str | None, **args) -> dict:
        """One `omh_delegate_route` call; `None` is a keyword the host did not pass."""
        dispatch = {} if session is None else {"session_id": session}
        if task is not None:
            dispatch["task_id"] = task
        return json.loads(omh_delegate_route_handler({**args, **self.homes()}, **dispatch))

    def set_route(self, session: str | None, task: str | None, category: str) -> None:
        result = self.tool(session, task, action="set", category=category)
        self.assertEqual(result["status"], "routed", result)
        self.assertEqual(result["conversation_route"], "recorded", result)

    def dispatch(self, session: str, task: str, **args) -> dict | None:
        """`pre_tool_call` for a `delegate_task`: the host's own call shape."""
        return pre_tool_call(
            tool_name="delegate_task",
            args={"goal": "lane", **args},
            session_id=session,
            task_id=task,
            tool_call_id=f"{session}-{task}-call",
            turn_id=f"{session}-{task}-turn",
            **self.homes(),
        )

    def end_turn(self, session: str, task: str) -> dict:
        return on_session_end(session_id=session, task_id=task, **self.homes())

    def keys(self) -> dict[str, str]:
        """What Hermes reads for the child when `pre_tool_call` has returned."""
        return read_delegation_route(self.hermes_home)


class RouteSurvivesAnotherSessionTest(PerConversationRouteTestCase):
    def test_another_sessions_set_does_not_route_this_sessions_dispatch(self) -> None:
        # The issue's reproduction: A sets, B sets before A dispatches.
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")
        self.assertEqual(self.keys(), QUICK)

        directive = self.dispatch("A", "A-t1")

        self.assertIsNone(directive)
        self.assertEqual(self.keys(), DEEP)
        record = load_route_restore_record(self.omh_home)
        self.assertEqual((record["writer_session_id"], record["writer_task_id"]), ("A", "A-t1"))
        # The newest provenance record names the route really in the keys,
        # which is what the leftover check and the HUD label read.
        newest = load_delegation_route_provenance(self.omh_home)[-1]
        self.assertEqual((newest["alias"], newest["session_id"]), ("deep-head", "A"))

        # And B's own dispatch still runs on B's route, not on A's.
        self.assertIsNone(self.dispatch("B", "B-t1"))
        self.assertEqual(self.keys(), QUICK)

    def test_another_sessions_turn_end_does_not_remove_this_sessions_route(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        ended = self.end_turn("B", "B-t1")

        # B was the last writer, so its turn end put the baseline back...
        self.assertEqual(ended["route_restore"]["status"], "cleared", ended)
        self.assertEqual(ended["route_restore"]["writer_routes"], "released")
        self.assertEqual(self.keys(), {})
        # ...and A's dispatch still runs on A's route.
        self.assertIsNone(self.dispatch("A", "A-t1"))
        self.assertEqual(self.keys(), DEEP)
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"A"})

    def test_a_turn_end_after_this_sessions_dispatch_leaves_its_route(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")
        self.assertIsNone(self.dispatch("A", "A-t1"))

        ended = self.end_turn("B", "B-t1")

        self.assertEqual(ended["route_restore"]["status"], "not_last_writer", ended)
        self.assertEqual(self.keys(), DEEP)
        # A's own turn end is what takes A's route back out.
        ended = self.end_turn("A", "A-t1")
        self.assertEqual(ended["route_restore"]["status"], "cleared", ended)
        self.assertEqual(self.keys(), {})
        self.assertFalse(conversation_routes_path(self.omh_home).exists())

    def test_another_sessions_clear_does_not_remove_this_sessions_route(self) -> None:
        self.set_route("B", "B-t1", "quick")

        cleared = self.tool("C", "C-t1", action="clear")

        self.assertEqual(cleared["status"], "cleared", cleared)
        self.assertEqual(self.keys(), {})
        self.assertIsNone(self.dispatch("B", "B-t1"))
        self.assertEqual(self.keys(), QUICK)

    def test_this_sessions_clear_releases_only_its_own_route(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        cleared = self.tool("A", "A-t1", action="clear")

        self.assertEqual(cleared["conversation_route"]["released"], 1, cleared)
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"B"})

    def test_another_sessions_fallback_does_not_route_this_sessions_dispatch(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        fell_back = self.tool("A", "A-t2", action="fallback", category="deep")

        self.assertEqual(fell_back["status"], "fell_back", fell_back)
        self.assertEqual(self.keys(), DEEP_NEXT)
        self.assertIsNone(self.dispatch("B", "B-t1"))
        self.assertEqual(self.keys(), QUICK)
        self.assertIsNone(self.dispatch("A", "A-t2"))
        self.assertEqual(self.keys(), DEEP_NEXT)


class ContinuationTest(PerConversationRouteTestCase):
    def test_a_compression_split_keeps_the_route_by_its_task(self) -> None:
        # The host moves the session id mid-turn; the task id stays.
        self.set_route("A0", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("A1", "A-t1"))

        self.assertEqual(self.keys(), DEEP)

    def test_a_route_with_no_task_is_found_through_the_continuation(self) -> None:
        write_sessions(
            self.hermes_home,
            compacted("A0", at=1050.0),
            row("A1", parent="A0", started_at=1050.0),
            row("B"),
        )
        self.set_route("A0", None, "deep")
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("A1", "A-t9"))

        self.assertEqual(self.keys(), DEEP)


class UnroutedDispatchTest(PerConversationRouteTestCase):
    def test_a_session_that_set_no_route_does_not_run_on_another_sessions(self) -> None:
        self.top_level("B", "C")
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("C", "C-t1"))

        # C runs on the profile's default, and B gets its route back at its
        # own next dispatch.
        self.assertEqual(self.keys(), {})
        self.assertFalse(delegation_route_restore_path(self.omh_home).exists())
        self.assertIsNone(self.dispatch("B", "B-t1"))
        self.assertEqual(self.keys(), QUICK)

    def test_a_route_from_an_earlier_turn_is_not_this_turns(self) -> None:
        # TUI: a route lives one turn. A restore missed at that turn's end
        # (the keys still hold it) is not a route for the next turn's dispatch.
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")

        self.assertIsNone(self.dispatch("A", "A-t2"))

        self.assertEqual(self.keys(), {})

    def test_a_route_whose_writer_recorded_no_conversation_is_left_alone(self) -> None:
        # An older OMH still running, or an entry that failed to write: the
        # owner could not put its route back, so nobody else takes it out.
        self.top_level("B", "C")
        self.set_route("B", "B-t1", "quick")
        conversation_routes_path(self.omh_home).unlink()
        record = load_route_restore_record(self.omh_home)

        self.assertIsNone(self.dispatch("C", "C-t1"))

        self.assertEqual(self.keys(), QUICK)
        self.assertEqual(load_route_restore_record(self.omh_home), record)


class SingleSessionUnchangedTest(PerConversationRouteTestCase):
    def test_a_dispatch_on_its_own_route_writes_nothing(self) -> None:
        self.set_route("A", "A-t1", "deep")
        config = (self.hermes_home / "config.yaml").read_bytes()
        record = load_route_restore_record(self.omh_home)
        provenance = load_delegation_route_provenance(self.omh_home)

        with mock.patch.object(
            delegation_route_restore, "_awareness_delivery_lock", side_effect=AssertionError("locked")
        ):
            self.assertIsNone(self.dispatch("A", "A-t1"))

        self.assertEqual((self.hermes_home / "config.yaml").read_bytes(), config)
        self.assertEqual(load_route_restore_record(self.omh_home), record)
        self.assertEqual(load_delegation_route_provenance(self.omh_home), provenance)

    def test_no_route_anywhere_reads_two_absent_files_and_nothing_else(self) -> None:
        with mock.patch.object(
            delegation_route_restore, "read_delegation_route", side_effect=AssertionError("read")
        ):
            self.assertIsNone(self.dispatch("A", "A-t1"))

    def test_a_turn_end_still_puts_the_baseline_back(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.assertIsNone(self.dispatch("A", "A-t1"))

        ended = self.end_turn("A", "A-t1")

        self.assertEqual(ended["route_restore"]["status"], "cleared", ended)
        self.assertEqual(self.keys(), {})
        self.assertFalse(conversation_routes_path(self.omh_home).exists())

    def test_control_actions_start_no_child_and_read_no_route(self) -> None:
        self.set_route("B", "B-t1", "quick")
        for action in ("list", "steer", "stop"):
            with self.subTest(action=action):
                self.assertIsNone(self.dispatch("C", "C-t1", action=action))
                self.assertEqual(self.keys(), QUICK)

    def test_other_tools_are_not_touched(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")
        self.assertIsNone(
            pre_tool_call(tool_name="read_file", args={"path": "x"}, session_id="A", task_id="A-t1", **self.homes())
        )
        self.assertEqual(self.keys(), QUICK)


class RefusedRatherThanMisroutedTest(PerConversationRouteTestCase):
    def blocked(self, session: str, task: str) -> dict:
        lock = _awareness_delivery_lock(delegation_route_restore_path(self.omh_home))
        with mock.patch.object(delegation_route_restore, "_DISPATCH_LOCK_SECONDS", 0.05), lock:
            directive = self.dispatch(session, task)
        self.assertIsNotNone(directive)
        self.assertEqual(directive["action"], "block")
        return json.loads(directive["message"])

    def test_a_route_that_cannot_be_put_back_refuses_the_dispatch(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        refusal = self.blocked("A", "A-t1")

        self.assertEqual(refusal["status"], "route_overwritten_by_other_session")
        self.assertEqual((refusal["session_id"], refusal["live_route_writer"]), ("A", "B"))
        self.assertEqual(refusal["reason"], "lock_unavailable")
        self.assertFalse(refusal["dispatched"])
        self.assertIn("'B'", refusal["error"])
        self.assertEqual(self.keys(), QUICK)

    def test_an_unrouted_dispatch_that_cannot_clear_another_route_is_refused(self) -> None:
        self.top_level("B", "C")
        self.set_route("B", "B-t1", "quick")

        refusal = self.blocked("C", "C-t1")

        self.assertEqual(refusal["status"], "other_session_route_live")
        self.assertEqual((refusal["session_id"], refusal["live_route_writer"]), ("C", "B"))
        self.assertEqual(self.keys(), QUICK)


class UnresolvedLineageTest(PerConversationRouteTestCase):
    def test_a_caller_whose_row_cannot_be_read_leaves_the_route_alone(self) -> None:
        # It might be that route's child, so the keys are not stripped: the
        # behaviour before conversation routes existed.
        self.top_level("B")
        self.set_route("B", "B-t1", "quick")
        record = load_route_restore_record(self.omh_home)

        with self.assertLogs("omh.delegation_route", level="WARNING") as logged:
            result = self.dispatch("C", "C-t1")

        # Not blocked (no `action`), but named: in the hook result and the log.
        self.assertNotIn("action", result)
        fallback = result["omh_route_fallback"]
        self.assertEqual(fallback["status"], "lineage_unresolved")
        self.assertEqual((fallback["session_id"], fallback["live_route_writer"]), ("C", "B"))
        self.assertTrue(fallback["dispatched_on_other_route"])
        self.assertIn("'C'", logged.output[0])
        self.assertEqual(self.keys(), QUICK)
        self.assertEqual(load_route_restore_record(self.omh_home), record)

    def test_no_state_db_at_all_leaves_the_route_alone(self) -> None:
        self.set_route("B", "B-t1", "quick")

        with self.assertLogs("omh.delegation_route", level="WARNING"):
            result = self.dispatch("C", "C-t1")

        self.assertEqual(result["omh_route_fallback"]["status"], "lineage_unresolved")
        self.assertEqual(self.keys(), QUICK)

    def test_an_approve_keeps_its_action_and_carries_the_fallback(self) -> None:
        self.set_route("B", "B-t1", "quick")
        approve = {"action": "approve", "message": "repeat", "rule_key": "k"}
        with mock.patch(
            "omh.plugin_bundle.omh.hooks.tool_hooks.repeat_call_directive", return_value=approve
        ), self.assertLogs("omh.delegation_route", level="WARNING"):
            result = self.dispatch("C", "C-t1")

        self.assertEqual(result["action"], "approve")
        self.assertEqual(result["omh_route_fallback"]["status"], "lineage_unresolved")

    def test_a_resolved_unrouted_dispatch_carries_no_note(self) -> None:
        self.top_level("B", "C")
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("C", "C-t1"))


class NestedDelegationTest(PerConversationRouteTestCase):
    """`max_spawn_depth` > 1: an orchestrator child dispatches its own workers."""

    def setUp(self) -> None:
        super().setUp()
        self.sessions(
            row("P", last_activity_at=time.time()),
            row("B", last_activity_at=time.time()),
            row("O", parent="P", source="subagent", delegate_from="P", started_at=1050.0),
            row("W", parent="O", source="subagent", delegate_from="O", started_at=1060.0),
        )

    def test_workers_under_a_routed_parent_run_on_the_parents_route(self) -> None:
        self.set_route("P", "P-t1", "deep")
        self.assertIsNone(self.dispatch("P", "P-t1"))

        self.assertIsNone(self.dispatch("O", "O-task"))

        self.assertEqual(self.keys(), DEEP)
        record = load_route_restore_record(self.omh_home)
        self.assertEqual((record["writer_session_id"], record["writer_task_id"]), ("P", "P-t1"))

    def test_a_deeper_worker_still_finds_the_routed_ancestor(self) -> None:
        self.set_route("P", "P-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("W", "W-task"))

        self.assertEqual(self.keys(), DEEP)

    def test_the_parents_route_comes_back_over_another_sessions_and_stays_the_parents(self) -> None:
        self.set_route("P", "P-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("O", "O-task"))

        self.assertEqual(self.keys(), DEEP)
        # Written on the parent's behalf, so the parent's own turn end takes
        # it back out, and the child's does not.
        record = load_route_restore_record(self.omh_home)
        self.assertEqual((record["writer_session_id"], record["writer_task_id"]), ("P", "P-t1"))
        self.assertEqual(self.end_turn("O", "O-task")["route_restore"]["status"], "not_last_writer")
        self.assertEqual(self.end_turn("P", "P-t1")["route_restore"]["status"], "cleared")
        self.assertEqual(self.keys(), {})

    def test_a_parent_route_written_without_a_conversation_entry_is_left_for_its_workers(self) -> None:
        self.set_route("P", "P-t1", "deep")
        conversation_routes_path(self.omh_home).unlink()

        self.assertIsNone(self.dispatch("O", "O-task"))

        self.assertEqual(self.keys(), DEEP)
        self.assertTrue(delegation_route_restore_path(self.omh_home).exists())


class GatewayStaleRouteTest(PerConversationRouteTestCase):
    """Gateway shape: the task id IS the session id, so every turn shares it."""

    def test_the_turn_end_releases_the_route_with_the_restore(self) -> None:
        self.top_level("S")
        self.set_route("S", "S", "deep")

        ended = self.end_turn("S", "S")

        self.assertEqual(ended["route_restore"]["status"], "cleared", ended)
        self.assertEqual(ended["route_restore"]["writer_routes"], "released")
        # The next turn dispatches without setting a route.
        self.assertIsNone(self.dispatch("S", "S"))
        self.assertEqual(self.keys(), {})

    def test_a_turn_end_that_cannot_take_the_lock_releases_nothing_and_restores_nothing(self) -> None:
        # One lock for both, so they cannot come apart: before, a release
        # that timed out after a restore succeeded left the route recorded,
        # and the next turn's dispatch wrote it back.
        self.top_level("S")
        self.set_route("S", "S", "deep")
        with _awareness_delivery_lock(delegation_route_restore_path(self.omh_home)):
            ended = self.end_turn("S", "S")

        self.assertEqual(ended["route_restore"]["status"], "lock_unavailable", ended)
        self.assertNotIn("conversation_route", ended["route_restore"])
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"S"})
        self.assertEqual(self.keys(), DEEP)

    def test_the_session_start_restore_releases_a_dead_writers_route(self) -> None:
        old = time.time() - 3 * 24 * 3600
        with mock.patch.object(delegation_route_restore.time, "time", return_value=old):
            self.set_route("S", "S", "quick")

        started = on_session_start(session_id="NEW", **self.homes())

        self.assertEqual(started["route_restore"]["status"], "cleared", started)
        self.assertEqual(started["route_restore"]["writer_routes"], "released")
        self.assertEqual(load_conversation_routes(self.omh_home), {})
        self.top_level("S")
        self.assertIsNone(self.dispatch("S", "S"))
        self.assertEqual(self.keys(), {})

    def test_an_entry_older_than_the_liveness_bound_is_not_a_route(self) -> None:
        # Nothing has stored a route since (a store prunes old entries), so
        # the entry is still on disk; only its age says it is gone.
        self.top_level("S", "T")
        old = time.time() - 3 * 24 * 3600
        with mock.patch.object(delegation_route_restore.time, "time", return_value=old):
            self.set_route("S", "S", "quick")
        with mock.patch.object(delegation_route_restore.time, "time", return_value=old + 1):
            self.set_route("T", "T", "deep")
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"S", "T"})

        self.assertIsNone(self.dispatch("S", "S"))

        # S's three-day-old route is not written back over the keys.
        self.assertEqual(self.keys(), DEEP)


class ApprovalGateTest(PerConversationRouteTestCase):
    """An earlier OMH gate that asks a person (`approve`) still gets the route gate."""

    def approved(self, session: str, task: str) -> dict | None:
        approve = {"action": "approve", "message": "repeat", "rule_key": "k"}
        with mock.patch(
            "omh.plugin_bundle.omh.hooks.tool_hooks.repeat_call_directive", return_value=approve
        ):
            return self.dispatch(session, task)

    def test_an_approved_dispatch_runs_on_its_own_route(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        directive = self.approved("A", "A-t1")

        self.assertEqual(directive["action"], "approve")
        self.assertEqual(self.keys(), DEEP)

    def test_a_route_refusal_wins_over_the_approve(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")
        lock = _awareness_delivery_lock(delegation_route_restore_path(self.omh_home))
        with mock.patch.object(delegation_route_restore, "_DISPATCH_LOCK_SECONDS", 0.05), lock:
            directive = self.approved("A", "A-t1")

        self.assertEqual(directive["action"], "block")
        self.assertEqual(json.loads(directive["message"])["status"], "route_overwritten_by_other_session")
        self.assertEqual(self.keys(), QUICK)


class RecordingIdentityTest(PerConversationRouteTestCase):
    def test_a_host_that_names_no_session_is_recorded_by_its_task(self) -> None:
        self.set_route(None, "T1", "deep")
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"task:T1"})
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.dispatch("", "T1"))

        self.assertEqual(self.keys(), DEEP)

    def test_a_route_with_no_identity_says_it_was_not_recorded(self) -> None:
        result = self.tool(None, None, action="set", category="deep")

        self.assertEqual(result["status"], "routed", result)
        self.assertEqual(result["conversation_route"], "unrecorded: no session or task")
        self.assertIn("will not be put back", result["conversation_route_note"])

    def test_the_turn_end_releases_an_entry_recorded_before_a_compression_split(self) -> None:
        self.sessions(compacted("A0", at=1050.0), row("A1", parent="A0", started_at=1050.0))
        self.set_route("A0", None, "deep")

        ended = self.end_turn("A1", "A-t9")

        self.assertEqual(ended["route_restore"]["conversation_route"]["released"], 1, ended)
        self.assertFalse(conversation_routes_path(self.omh_home).exists())


class ReleaseCoherenceTest(PerConversationRouteTestCase):
    """The caller's own release and the restore it follows cannot come apart."""

    def test_clear_releases_in_the_restores_own_lock(self) -> None:
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")
        real = delegation_route_restore._awareness_delivery_lock
        with mock.patch.object(delegation_route_restore, "_awareness_delivery_lock", wraps=real) as lock:
            cleared = self.tool("A", "A-t1", action="clear")

        self.assertEqual(cleared["status"], "cleared", cleared)
        self.assertEqual(cleared["conversation_route"]["status"], "released", cleared)
        self.assertEqual(lock.call_count, 1)
        # The same task's next dispatch does not put the cleared route back.
        self.assertIsNone(self.dispatch("A", "A-t1"))
        self.assertEqual(self.keys(), {})

    def test_a_clear_that_cannot_take_the_lock_releases_nothing(self) -> None:
        # The probe's shape: before, the restore could succeed and a second
        # lock for the release time out, so a "cleared" route came back.
        self.set_route("A", "A-t1", "deep")
        with _awareness_delivery_lock(delegation_route_restore_path(self.omh_home)):
            cleared = self.tool("A", "A-t1", action="clear")

        self.assertEqual(cleared["status"], "lock_unavailable", cleared)
        self.assertNotIn("conversation_route", cleared)
        self.assertEqual(self.keys(), DEEP)
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"A"})

    def test_an_exhausted_chain_does_not_come_back_on_the_same_task(self) -> None:
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")
        self.assertEqual(self.tool("A", "A-t1", action="fallback")["status"], "fell_back")

        exhausted = self.tool("A", "A-t1", action="fallback")

        self.assertEqual(exhausted["status"], "exhausted_to_inherit", exhausted)
        self.assertEqual(exhausted["conversation_route"]["status"], "released", exhausted)
        self.assertIsNone(self.dispatch("A", "A-t1"))
        self.assertEqual(self.keys(), {})

    def test_another_sessions_clear_keeps_this_sessions_route(self) -> None:
        self.set_route("B", "B-t1", "quick")

        cleared = self.tool("C", "C-t1", action="clear")

        self.assertEqual(cleared["writer_routes"], "kept", cleared)
        self.assertEqual(cleared["conversation_route"]["released"], 0, cleared)
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"B"})

    def test_a_refused_turn_end_restore_keeps_the_route_recorded(self) -> None:
        self.top_level("A")
        self.set_route("A", "A-t1", "deep")
        refused = {"status": "error", "error": "config changed during route read"}
        with mock.patch.object(delegation_route_restore, "_put_baseline_back", return_value=refused):
            ended = self.end_turn("A", "A-t1")

        self.assertEqual(ended["route_restore"]["status"], "error", ended)
        self.assertNotIn("conversation_route", ended["route_restore"])
        # The keys still hold the route, and so does the record of it.
        self.assertEqual(self.keys(), DEEP)
        self.assertEqual(set(load_conversation_routes(self.omh_home)), {"A"})
        # The next turn end does both.
        ended = self.end_turn("A", "A-t1")
        self.assertEqual(ended["route_restore"]["status"], "cleared", ended)
        self.assertFalse(conversation_routes_path(self.omh_home).exists())

    def test_a_session_start_release_that_fails_is_reported(self) -> None:
        old = time.time() - 3 * 24 * 3600
        with mock.patch.object(delegation_route_restore.time, "time", return_value=old):
            self.set_route("S", "S", "quick")
        with mock.patch.object(
            delegation_route_restore, "_release_locked", side_effect=PermissionError("denied")
        ), self.assertLogs("omh.delegation_route", level="WARNING"):
            started = on_session_start(session_id="NEW", **self.homes())

        self.assertEqual(started["route_restore"]["status"], "cleared", started)
        self.assertEqual(started["route_restore"]["writer_routes"], "unreleased: PermissionError")
        components = started["omh_degradation"]["components"]
        self.assertEqual(components[0]["error_type"], "PermissionError", started)


class LineageCycleTest(PerConversationRouteTestCase):
    def test_a_corrupt_cycle_does_not_make_a_session_its_own_parent(self) -> None:
        # A route from an earlier turn is not this turn's (task-first); a
        # `_delegate_from` cycle through A must not hand it back as a parent's.
        self.sessions(
            row("A", delegate_from="P", last_activity_at=time.time()),
            row("P", delegate_from="A", last_activity_at=time.time()),
        )
        self.set_route("A", "A-t1", "deep")

        self.assertIsNone(self.dispatch("A", "A-t2"))

        self.assertEqual(self.keys(), {})


class StatusTest(PerConversationRouteTestCase):
    def status(self, session: str, task: str) -> dict:
        return self.tool(session, task, action="status")

    def test_status_reports_this_conversations_route_beside_the_keys(self) -> None:
        self.set_route("A", "A-t1", "deep")
        self.set_route("B", "B-t1", "quick")

        mine = self.status("A", "A-t1")

        self.assertEqual(mine["route"]["model"], "quick-head")
        self.assertEqual(mine["conversation_route"]["route"], DEEP)
        self.assertFalse(mine["conversation_route"]["in_keys"])
        self.assertTrue(self.status("B", "B-t1")["conversation_route"]["in_keys"])

    def test_status_without_a_route_of_ones_own_says_none(self) -> None:
        self.set_route("B", "B-t1", "quick")

        self.assertIsNone(self.status("C", "C-t1")["conversation_route"])
        self.assertIsNone(self.status("B", "B-t2")["conversation_route"])


if __name__ == "__main__":
    unittest.main()
