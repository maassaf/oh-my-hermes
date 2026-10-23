"""Contracts for one checklist: Hermes' `todo_list` projected into the OMH plan todo.

The defect: OMH told the model to declare every plan with `omh_todo` while
Hermes ships `todo_list` as a core tool that asks for the same plan, so a
session declared the same plan in both tools, in paraphrased items, and two
lists showed for one plan. The fix keeps one store and
lets either tool hold the steps -- `post_tool_call` projects each successful
`todo_list` write into the session's own plan record -- and these pin the
parts that make that safe:

* the projection itself, through the registered hook, scoped to the session
  that made the call, and declining every call that is not a write;
* a native status crossing over gains nothing: `completed` is a done mark
  judged like any other, `cancelled` reads as skipped;
* what `omh_todo` alone records survives native writes or is refused by them:
  an item's `blocked_reason` is carried while the item stays where it was or
  is paused, a deferral lapses on an item change, and a plan awaiting
  acceptance, an accepted plan or a template plan is not replaced, so the
  plan-stage gate and `omh_team` read exactly what they read before;
* the stall and continuation guards read one record whichever tool wrote it,
  and keep working when no native write ever arrives (`todo_list` deferred
  behind tool_search reaches the hook under its own name; absent, `omh_todo`
  is the only writer);
* the presence rule is one wording.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.awareness import PLAN_TODO_DECLARATION_RULE
from omh.plugin_bundle.omh.engagement_nudges import PLAN_NUDGE_TEXT
from omh.plugin_bundle.omh.hooks.llm_hooks import _todo_stall_status
from omh.plugin_bundle.omh.hooks.tool_hooks import post_tool_call
from omh.plugin_bundle.omh import native_todo
from omh.plugin_bundle.omh.native_todo import (
    NATIVE_CANCELLED_REASON,
    NATIVE_TODO_SOURCE,
    held_native_write_note,
    native_todo_items,
    project_native_todo,
)
from omh.plugin_bundle.omh.native_tools import (
    NATIVE_TODO_TOOL,
    OMH_TODO_HELD_PLANS,
    native_tool_presence_clause,
)
from omh.plugin_bundle.omh.plan_stage_gate import plan_stage_edit_directive
from omh.plugin_bundle.omh.runtime_reader import read_omh_hud, read_omh_todo
from omh.plugin_bundle.omh.todo_reconciliation import (
    TODO_CONTINUATION_RULE,
    open_todo_reminder,
    plan_continuation_reading,
)
from omh.plugin_bundle.omh.todo_store import (
    MAX_TODO_ITEMS,
    MAX_TODO_TEXT_CHARS,
    PLAN_STAGE_ACCEPTED,
    PLAN_STAGE_AWAITING_ACCEPTANCE,
    TODO_CLAIM_BOUNDARY,
    build_todo_record,
    todo_path,
    write_todo,
)
from omh.plugin_bundle.omh.tool_bursts import record_tool_call, record_tool_call_close
from omh.plugin_bundle.omh.tools.todo_tool import OMH_TODO_SCHEMA, omh_todo_handler

REPO = Path(__file__).resolve().parents[1]
SESSION = "20261013_101010_abc123"
OTHER_SESSION = "20261013_202020_def456"


def native(todo_id: str, content: str, status: str = "pending", parent: str = "") -> dict:
    item = {"id": todo_id, "content": content, "status": status}
    if parent:
        item["parent"] = parent
    return item


class _NativeTodoHomeTest(unittest.TestCase):
    """A real OMH home and an empty Hermes home; the tool reads both from the env."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "omh"
        self.hermes = Path(self._tmp.name) / "hermes"
        self.hermes.mkdir(parents=True)
        env = patch.dict(os.environ, {"OMH_HOME": str(self.home), "HERMES_HOME": str(self.hermes)})
        env.start()
        self.addCleanup(env.stop)
        self._call = 0
        native_todo._reset_held_notes()
        self.addCleanup(native_todo._reset_held_notes)

    def write_native(self, todos, *, session=SESSION, status="ok", args=None, result=None):
        """One `todo_list` write as Hermes reports it to the registered hook."""
        self._call += 1
        payload = {"todos": todos, "revision": self._call, "summary": {"total": len(todos)}}
        return post_tool_call(
            tool_name=NATIVE_TODO_TOOL,
            args={"todos": todos} if args is None else args,
            result=json.dumps(payload) if result is None else result,
            status=status,
            session_id=session,
            tool_call_id=f"call-{self._call}",
            turn_id="turn",
            omh_home=str(self.home),
            hermes_home=str(self.hermes),
        )

    def project(self, todos, *, session=SESSION):
        """The projection's own label, for the cases the hook's None cannot tell apart."""
        return project_native_todo(
            tool_name=NATIVE_TODO_TOOL,
            args={"todos": todos},
            result=json.dumps({"todos": todos}),
            status="ok",
            session_ref=session,
            omh_home=str(self.home),
            hermes_home=str(self.hermes),
        )

    def todo(self, session=SESSION) -> dict:
        return read_omh_todo(self.home, self.hermes, session_ref=session)

    def stored(self, session=SESSION) -> dict | None:
        path = todo_path(self.home, session)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def omh_todo(self, args: dict, session=SESSION) -> dict:
        return json.loads(omh_todo_handler(args, session_id=session))

    def age(self, minutes: int, session=SESSION) -> None:
        """Move the stored plan's stamp into the past, as time passing would."""
        path = todo_path(self.home, session)
        record = json.loads(path.read_text(encoding="utf-8"))
        stamp = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        record["updated_at"] = stamp.isoformat().replace("+00:00", "Z")
        path.write_text(json.dumps(record), encoding="utf-8")


class ProjectionTest(_NativeTodoHomeTest):
    """A native write lands in the session's own plan record, as the record's states."""

    def test_a_native_write_becomes_the_session_plan(self):
        self.write_native(
            [
                native("1", "land the fix", "completed"),
                native("2", "open the PR", "in_progress"),
                native("3", "watch CI", "pending", parent="2"),
            ]
        )

        todo = self.todo()
        self.assertEqual(todo["status"], "established")
        self.assertEqual(todo["source"], NATIVE_TODO_SOURCE)
        self.assertTrue(todo["own_record"])
        self.assertEqual(
            [(item["text"], item["state"], item.get("depth", 0)) for item in todo["items"]],
            [("land the fix", "done", 0), ("open the PR", "active", 0), ("watch CI", "pending", 1)],
        )

    def test_another_session_neither_sees_nor_loses_its_own_plan(self):
        _ = self.omh_todo(
            {"action": "set", "items": [{"text": "other work", "state": "active"}]},
            session=OTHER_SESSION,
        )
        self.write_native([native("1", "mine", "in_progress")])

        self.assertEqual([item["text"] for item in self.todo()["items"]], ["mine"])
        self.assertEqual([item["text"] for item in self.todo(OTHER_SESSION)["items"]], ["other work"])

    def test_subtasks_follow_their_parent_whatever_the_list_order(self):
        items = native_todo_items(
            [
                native("a", "first"),
                native("b", "second"),
                native("a1", "under first", parent="a"),
                native("a1x", "deeper", parent="a1"),
            ]
        )
        self.assertEqual(
            [(item["text"], item.get("depth", 0)) for item in items],
            [("first", 0), ("under first", 1), ("deeper", 2), ("second", 0)],
        )

    def test_overlong_text_is_bounded_not_refused(self):
        items = native_todo_items([native("1", "x" * (MAX_TODO_TEXT_CHARS + 50))])
        self.assertEqual(len(items[0]["text"]), MAX_TODO_TEXT_CHARS)
        self.assertTrue(items[0]["text"].endswith("…"))

    def test_over_the_cap_finished_items_leave_first(self):
        todos = [native(str(index), f"done {index}", "completed") for index in range(10)]
        todos += [native(f"o{index}", f"open {index}") for index in range(15)]

        items = native_todo_items(todos)

        self.assertEqual(len(items), MAX_TODO_ITEMS)
        self.assertEqual(sum(1 for item in items if item["state"] != "done"), 15)
        self.assertEqual(items[0]["text"], "done 5")

    def test_more_open_items_than_the_cap_never_reads_as_finished(self):
        todos = [native(str(index), f"open {index}") for index in range(MAX_TODO_ITEMS + 5)]
        items = native_todo_items(todos)
        self.assertEqual(len(items), MAX_TODO_ITEMS)
        self.assertTrue(all(item["state"] == "pending" for item in items))

    def test_an_emptied_native_list_clears_the_plan(self):
        self.write_native([native("1", "land the fix", "in_progress")])
        self.assertEqual(self.project([]), "cleared")
        self.assertEqual(self.todo()["status"], "absent")

    def test_an_emptied_native_list_never_removes_the_home_wide_record(self):
        # An operator's `omh runtime todo set` writes the unstamped home-wide
        # record; a session with no record of its own clearing its native list
        # has nothing of its own to remove.
        _ = write_todo(self.home, build_todo_record("operator plan", [{"text": "rotate keys"}], source="cli"))
        self.assertEqual(self.project([]), "already_absent")
        self.assertTrue(todo_path(self.home).is_file())

        self.write_native([native("1", "land the fix", "in_progress")])
        self.assertEqual(self.project([]), "cleared")
        self.assertTrue(todo_path(self.home).is_file())

    def test_a_json_string_list_is_a_write(self):
        # Hermes parses `todos` sent as a JSON string and writes it
        # (`tools/todo_tool.py` at v2026.9.7); the result carries the list.
        todos = [native("1", "land the fix", "in_progress")]
        self.write_native(todos, args={"todos": json.dumps(todos)})
        self.assertEqual([item["text"] for item in self.todo()["items"]], ["land the fix"])


class DeclinedCallTest(_NativeTodoHomeTest):
    """Every call that is not a successful native write leaves the record alone."""

    def test_each_non_write_is_declined_by_name(self):
        todos = [native("1", "land the fix", "in_progress")]
        cases = {
            "not_native_todo": dict(tool_name="omh_todo"),
            "no_session": dict(session_ref=""),
            "not_ok": dict(status="error"),
            "read_only": dict(args={}),
            "no_list": dict(result=json.dumps({"error": "TodoStore not initialized"})),
        }
        for label, override in cases.items():
            with self.subTest(label=label):
                call = dict(
                    tool_name=NATIVE_TODO_TOOL,
                    args={"todos": todos},
                    result=json.dumps({"todos": todos}),
                    status="ok",
                    session_ref=SESSION,
                    omh_home=str(self.home),
                    hermes_home=str(self.hermes),
                )
                call.update(override)
                self.assertEqual(project_native_todo(**call), label)
                self.assertIsNone(self.stored())

    def test_a_read_through_the_hook_writes_nothing(self):
        self.write_native([native("1", "land the fix")], args={})
        self.assertIsNone(self.stored())

    def test_the_bridge_name_is_not_the_tool(self):
        # Hermes peels the tool_search bridge before plugin hooks run, so a
        # deferred todo_list arrives as `todo_list`. A call still named
        # `tool_call` is the bridge itself, and is not read as a todo write.
        todos = [native("1", "land the fix")]
        _ = post_tool_call(
            tool_name="tool_call",
            args={"name": NATIVE_TODO_TOOL, "arguments": {"todos": todos}},
            result=json.dumps({"todos": todos}),
            status="ok",
            session_id=SESSION,
            tool_call_id="bridge",
            omh_home=str(self.home),
            hermes_home=str(self.hermes),
        )
        self.assertIsNone(self.stored())

    def test_an_unchanged_rewrite_does_not_restamp_the_plan(self):
        todos = [native("1", "land the fix", "in_progress"), native("2", "open the PR")]
        self.assertEqual(self.project(todos), "projected")
        before = self.stored()["updated_at"]
        self.assertEqual(self.project(todos), "unchanged")
        self.assertEqual(self.stored()["updated_at"], before)


class NativeStatusIsADeclarationTest(_NativeTodoHomeTest):
    """`completed` and `cancelled` cross over as the record's own declarations."""

    def test_completed_is_a_done_mark_the_stop_criterion_judges(self):
        self.write_native([native("1", "land the fix", "completed"), native("2", "open the PR")])
        done = self.stored()["items"][0]
        # Bound the way `omh_todo action=set` binds a newly done item: a window
        # and a done stamp, which is what makes the evidence check judge it
        # rather than count it done unexamined.
        self.assertEqual(done["state"], "done")
        self.assertIn("done_at", done)
        self.assertIn("window_start", done)
        self.assertEqual(self.stored()["claim_boundary"], TODO_CLAIM_BOUNDARY)

    def test_a_cancelled_item_brought_back_is_driven_again(self):
        self.write_native([native("1", "land the fix", "completed"), native("2", "beta", "cancelled"), native("3", "gamma", "completed")])
        self.write_native([native("1", "land the fix", "completed"), native("2", "beta", "pending"), native("3", "gamma", "completed")])

        beta = self.todo()["items"][1]
        self.assertEqual((beta["text"], beta["state"]), ("beta", "pending"))
        self.assertNotIn("blocked_reason", beta)
        message, _ = plan_continuation_reading(
            omh_home=str(self.home), hermes_home=str(self.hermes), session_ref=SESSION
        )
        self.assertIn("next: beta", message)

    def test_a_cancelled_item_completed_later_is_not_counted_skipped(self):
        self.write_native([native("1", "beta", "cancelled"), native("2", "gamma", "pending")])
        self.write_native([native("1", "beta", "completed"), native("2", "gamma", "pending")])
        todo = self.todo()
        self.assertNotIn("blocked_reason", todo["items"][0])
        self.assertEqual(todo["counts"]["skipped"], 0)

    def test_cancelled_reads_as_skipped(self):
        self.write_native([native("1", "old idea", "cancelled"), native("2", "open the PR")])
        todo = self.todo()
        self.assertEqual(todo["items"][0]["state"], "done")
        self.assertEqual(todo["items"][0]["blocked_reason"], NATIVE_CANCELLED_REASON)
        self.assertEqual(todo["counts"]["skipped"], 1)


class OmhTodoOnlyFieldsTest(_NativeTodoHomeTest):
    """What only `omh_todo` records survives a native write, or is not touched by one."""

    def test_a_blocked_reason_survives_while_the_item_status_holds(self):
        self.write_native([native("1", "land the fix", "in_progress"), native("2", "open the PR")])
        result = self.omh_todo(
            {
                "action": "advance",
                "item": 1,
                "item_text": "land the fix",
                "state": "active",
                "blocked_reason": "waiting on the owner's review",
            }
        )
        self.assertEqual(result["status"], "written")

        self.write_native([native("1", "land the fix", "in_progress"), native("2", "open the PR", "pending")])
        self.write_native([native("1", "land the fix", "in_progress"), native("2", "open the PR", "in_progress")])
        self.assertEqual(self.todo()["items"][0]["blocked_reason"], "waiting on the owner's review")

        self.write_native([native("1", "land the fix", "completed"), native("2", "open the PR", "in_progress")])
        self.assertNotIn("blocked_reason", self.todo()["items"][0])

    def test_a_resumed_item_loses_its_recorded_reason_and_is_driven(self):
        self.write_native([native("1", "land the fix", "completed"), native("2", "open the PR", "pending")])
        _ = self.omh_todo(
            {"action": "advance", "item": 2, "item_text": "open the PR", "state": "pending", "blocked_reason": "CI credentials"}
        )
        message, _ = plan_continuation_reading(
            omh_home=str(self.home), hermes_home=str(self.hermes), session_ref=SESSION
        )
        self.assertEqual(message, "")

        self.write_native([native("1", "land the fix", "completed"), native("2", "open the PR", "in_progress")])
        self.assertNotIn("blocked_reason", self.todo()["items"][1])
        message, _ = plan_continuation_reading(
            omh_home=str(self.home), hermes_home=str(self.hermes), session_ref=SESSION
        )
        self.assertIn("next: open the PR", message)

    def test_a_blocked_item_paused_for_the_next_one_stays_blocked(self):
        # Hermes allows one in_progress item: moving on from a blocked item
        # pauses it (in_progress -> pending), which does not unblock it.
        self.write_native([native("1", "a", "completed"), native("2", "b", "in_progress"), native("3", "c", "pending")])
        _ = self.omh_todo({"action": "advance", "item": 2, "item_text": "b", "state": "active", "blocked_reason": "owner review"})
        self.write_native([native("1", "a", "completed"), native("2", "b", "pending"), native("3", "c", "in_progress")])
        self.assertEqual(self.todo()["items"][1].get("blocked_reason"), "owner review")
        self.write_native([native("1", "a", "completed"), native("2", "b", "pending"), native("3", "c", "completed")])
        self.assertEqual(self.todo()["items"][1].get("blocked_reason"), "owner review")
        message, _ = plan_continuation_reading(
            omh_home=str(self.home), hermes_home=str(self.hermes), session_ref=SESSION
        )
        self.assertEqual(message, "")

    def test_a_reason_recorded_with_another_state_survives_an_untouched_item(self):
        # todo_list shows b in_progress; omh_todo records the reason with
        # state=pending. todo_list owns the state, so b stays active in the
        # record and the next todo_list write leaving b alone keeps the reason.
        self.write_native([native("1", "a", "completed"), native("2", "b", "in_progress"), native("3", "c", "pending")])
        _ = self.omh_todo({"action": "advance", "item": 2, "item_text": "b", "state": "pending", "blocked_reason": "owner review"})
        self.assertEqual(self.todo()["items"][1]["state"], "active")
        self.write_native([native("1", "a", "completed"), native("2", "b", "in_progress"), native("3", "c", "pending")])
        self.assertEqual(self.todo()["items"][1].get("blocked_reason"), "owner review")

    def test_a_reason_on_a_closed_item_outranks_a_later_native_cancel(self):
        self.write_native([native("1", "a", "in_progress"), native("2", "b", "completed")])
        _ = self.omh_todo(
            {"action": "advance", "item": 2, "item_text": "b", "state": "done", "blocked_reason": "no command can show a doc edit"}
        )
        self.write_native([native("1", "a", "in_progress"), native("2", "b", "cancelled")])
        self.assertEqual(self.todo()["items"][1]["blocked_reason"], "no command can show a doc edit")

    def test_a_reopened_item_loses_its_reason(self):
        self.write_native([native("1", "a", "in_progress"), native("2", "b", "completed")])
        _ = self.omh_todo(
            {"action": "advance", "item": 2, "item_text": "b", "state": "done", "blocked_reason": "no command can show a doc edit"}
        )
        self.write_native([native("1", "a", "in_progress"), native("2", "b", "pending")])
        self.assertNotIn("blocked_reason", self.todo()["items"][1])

    def test_a_native_cancel_replaces_a_reason_on_an_open_item(self):
        self.write_native([native("1", "a", "in_progress"), native("2", "b", "pending")])
        _ = self.omh_todo({"action": "advance", "item": 2, "item_text": "b", "state": "pending", "blocked_reason": "owner review"})
        self.write_native([native("1", "a", "in_progress"), native("2", "b", "cancelled")])
        self.assertEqual(self.todo()["items"][1]["blocked_reason"], NATIVE_CANCELLED_REASON)

    def test_an_advance_asking_for_another_state_is_told_todo_list_kept_it(self):
        self.write_native([native("1", "a", "in_progress"), native("2", "b", "pending")])
        result = self.omh_todo({"action": "advance", "item": 1, "item_text": "a", "state": "done"})
        self.assertEqual(result["status"], "written")
        self.assertEqual(result["state_kept"], "todo_list sets item states on this plan; item 1 stays in_progress")
        self.assertEqual(self.todo()["items"][0]["state"], "active")
        # The same advance on an omh_todo plan moves the state and says nothing.
        declared = self.omh_todo(
            {"action": "set", "items": [{"text": "a", "state": "active"}, {"text": "b"}]}, session=OTHER_SESSION
        )
        self.assertEqual(declared["status"], "written")
        moved = self.omh_todo({"action": "advance", "item": 1, "item_text": "a", "state": "done"}, session=OTHER_SESSION)
        self.assertNotIn("state_kept", moved)
        self.assertEqual(self.todo(OTHER_SESSION)["items"][0]["state"], "done")
        # The note speaks todo_list's vocabulary, cancelled included.
        self.write_native([native("1", "a", "completed"), native("2", "b", "cancelled"), native("3", "c", "pending")])
        for item, text, status in ((1, "a", "completed"), (2, "b", "cancelled")):
            kept = self.omh_todo({"action": "advance", "item": item, "item_text": text, "state": "pending"})
            self.assertEqual(kept["state_kept"], f"todo_list sets item states on this plan; item {item} stays {status}")
        # An advance that sends no reason does not turn the cancelled item into a completed one.
        self.assertEqual(self.todo()["items"][1]["blocked_reason"], NATIVE_CANCELLED_REASON)
        # Any other reason on a todo_list-held plan still clears when the call sends none.
        _ = self.omh_todo({"action": "advance", "item": 3, "item_text": "c", "state": "pending", "blocked_reason": "owner review"})
        self.assertEqual(self.todo()["items"][2]["blocked_reason"], "owner review")
        _ = self.omh_todo({"action": "advance", "item": 3, "item_text": "c", "state": "pending"})
        self.assertNotIn("blocked_reason", self.todo()["items"][2])
        # And on an omh_todo plan the same string is an ordinary reason: sending none clears it.
        _ = self.omh_todo(
            {
                "action": "set",
                "items": [{"text": "x", "state": "done", "blocked_reason": NATIVE_CANCELLED_REASON}, {"text": "y", "state": "active"}],
            },
            session=OTHER_SESSION,
        )
        _ = self.omh_todo({"action": "advance", "item": 1, "item_text": "x", "state": "done"}, session=OTHER_SESSION)
        self.assertNotIn("blocked_reason", self.todo(OTHER_SESSION)["items"][0])
        # The sentence is about todo_list-held plans and is never said of another record.
        from omh.plugin_bundle.omh.tools.todo_tool import _state_kept

        record = {"source": "omh_todo", "items": [{"text": "a", "state": "active"}]}
        self.assertEqual(_state_kept(record, 1, "done"), "")
        self.assertIn("stays in_progress", _state_kept({**record, "source": NATIVE_TODO_SOURCE}, 1, "done"))

    def test_adoption_keeps_every_omh_todo_field(self):
        # A finished planning plan is not held, so an identical native list
        # adopts it; the stage and title it carried stay on the record.
        _ = self.omh_todo(
            {
                "action": "set",
                "title": "Reviewed plan",
                "items": [{"text": "options and tradeoffs", "state": "done"}],
                "plan_stage": PLAN_STAGE_AWAITING_ACCEPTANCE,
            }
        )
        self.assertEqual(self.project([native("1", "options and tradeoffs", "completed")]), "adopted")
        self.assertEqual(self.stored()["plan_stage"], PLAN_STAGE_AWAITING_ACCEPTANCE)
        self.assertEqual(self.stored()["title"], "Reviewed plan")

    def test_an_identical_native_list_adopts_an_omh_todo_plan(self):
        _ = self.omh_todo({"action": "set", "items": [{"text": "land the fix", "state": "active"}, {"text": "open the PR"}]})
        before = self.stored()
        self.assertEqual(
            self.project([native("1", "land the fix", "in_progress"), native("2", "open the PR", "pending")]), "adopted"
        )
        after = self.stored()
        self.assertEqual(after["source"], NATIVE_TODO_SOURCE)
        self.assertEqual({k: v for k, v in after.items() if k != "source"}, {k: v for k, v in before.items() if k != "source"})

    def test_a_deferral_lapses_on_a_native_item_change_and_holds_without_one(self):
        todos = [native("1", "land the fix", "in_progress"), native("2", "open the PR")]
        self.write_native(todos)
        _ = self.omh_todo(
            {
                "action": "advance",
                "item": 1,
                "item_text": "land the fix",
                "state": "active",
                "deferred_reason": "the person asked for the release notes first",
            }
        )
        self.assertTrue(self.todo()["deferred_reason"])

        self.write_native(todos)
        self.assertTrue(self.todo()["deferred_reason"])

        self.write_native([native("1", "land the fix", "completed"), native("2", "open the PR", "in_progress")])
        self.assertEqual(self.todo()["deferred_reason"], "")

    def test_an_open_plan_awaiting_acceptance_keeps_its_record_and_its_gate(self):
        planning = [
            {"text": "repo facts and evidence check", "state": "done"},
            {"text": "options and tradeoffs", "state": "active"},
        ]
        _ = self.omh_todo(
            {"action": "set", "items": planning, "plan_stage": PLAN_STAGE_AWAITING_ACCEPTANCE}
        )
        before = self.stored()

        self.assertEqual(self.project([native("1", "write the code", "in_progress")]), "held_by_omh_todo")
        self.write_native([native("1", "write the code", "in_progress")])
        self.assertEqual(self.project([]), "held_by_omh_todo")

        self.assertEqual(self.stored(), before)
        directive = plan_stage_edit_directive(
            tool_name="write_file",
            session_id=SESSION,
            omh_home=str(self.home),
            hermes_home=str(self.hermes),
        )
        self.assertEqual((directive or {}).get("action"), "approve")

    def test_an_open_accepted_plan_keeps_its_record_for_the_team_lane(self):
        # `omh_team` binds its lanes to this session's plan with
        # plan_stage=accepted and its `check:` items (src/workflows/team.py),
        # so a todo_list write must not replace it while it is open.
        _ = self.omh_todo(
            {
                "action": "set",
                "items": [
                    {"text": "options and tradeoffs", "state": "done"},
                    {"text": "lane a check: `make test`", "state": "active"},
                ],
                "plan_stage": PLAN_STAGE_ACCEPTED,
            }
        )
        before = self.stored()

        self.assertEqual(self.project([native("1", "write the code", "in_progress")]), "held_by_omh_todo")
        self.write_native([native("1", "write the code", "in_progress")])

        self.assertEqual(self.stored(), before)
        plan = dict(self.todo())
        self.assertEqual(plan["plan_stage"], PLAN_STAGE_ACCEPTED)
        # And the team lane still binds to it -- the refusal measured in review
        # (`plan_not_accepted`) when a native write replaced it.
        from omh.plugin_bundle.omh.todo_store import todo_items_digest
        from omh.workflows.team import _bind_to_plan

        plan["items_digest"] = todo_items_digest(plan.get("items"))
        units = [{"unit_id": "a", "title": "lane a", "verification_command": "make test"}]
        _bind_to_plan(units, plan, plan["items_digest"])
        self.assertEqual(units[0]["plan_item"], "lane a check: `make test`")

    def test_a_finished_accepted_plan_is_held_until_stale_and_still_binds(self):
        # `omh_team` binds a finished accepted plan too (`all_done`), so a
        # native write must not replace it while it is fresh.
        _ = self.omh_todo(
            {
                "action": "set",
                "items": [{"text": "options and tradeoffs", "state": "done"}, {"text": "lane a check: `make test`", "state": "done"}],
                "plan_stage": PLAN_STAGE_ACCEPTED,
            }
        )
        before = self.stored()
        self.assertEqual(self.project([native("1", "write the code", "in_progress")]), "held_by_omh_todo")
        self.assertEqual(self.stored(), before)

        from omh.plugin_bundle.omh.todo_store import todo_items_digest
        from omh.workflows.team import _bind_to_plan

        plan = dict(self.todo())
        plan["items_digest"] = todo_items_digest(plan.get("items"))
        units = [{"unit_id": "a", "title": "lane a", "verification_command": "make test"}]
        _bind_to_plan(units, plan, plan["items_digest"])
        self.assertEqual(units[0]["plan_item"], "lane a check: `make test`")

    def test_a_held_plan_is_not_adopted_by_an_identical_native_list(self):
        # The hold is decided before adoption: an accepted plan whose items a
        # todo_list write repeats keeps its record and its source.
        _ = self.omh_todo(
            {"action": "set", "items": [{"text": "lane a check: `make test`", "state": "active"}], "plan_stage": PLAN_STAGE_ACCEPTED}
        )
        before = self.stored()
        self.assertEqual(self.project([native("1", "lane a check: `make test`", "in_progress")]), "held_by_omh_todo")
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.stored()["source"], "omh_todo")

    ACCEPTED_PLAN = {
        "action": "set",
        "items": [{"text": "lane a check: `make test`", "state": "active"}],
        "plan_stage": PLAN_STAGE_ACCEPTED,
    }

    def context(self, session=SESSION, **overrides) -> str:
        """One `pre_llm_call` for ``session``, as the host opens a later turn."""
        from omh.plugin_bundle.omh.hooks.llm_hooks import pre_llm_call

        call = dict(
            omh_home=str(self.home), hermes_home=str(self.hermes), session_id=session,
            user_message="continue", is_first_turn=False,
        )
        call.update(overrides)
        return str((pre_llm_call(**call) or {}).get("context", ""))

    def note(self, session=SESSION) -> str:
        return held_native_write_note(session, self.home)

    def test_a_held_write_is_named_once_on_the_next_turn(self):
        _ = self.omh_todo(self.ACCEPTED_PLAN)
        self.write_native([native("1", "unrelated work", "in_progress")])

        # Another session reading first neither gets the line nor spends it.
        self.assertEqual(self.note(OTHER_SESSION), "")
        self.assertNotIn("was not mirrored", self.context(OTHER_SESSION))

        first = self.context()
        self.assertIn("Your last todo_list write was not mirrored: this session's accepted plan is held in omh_todo", first)
        self.assertIn("omh_todo action=clear", first)
        self.assertNotIn("was not mirrored", self.context())

    def test_a_fork_sharing_the_session_id_does_not_spend_the_line(self):
        # Hermes' background review runs right after the turn under the
        # parent's own id (`session_id == parent_session_id`); the line is for
        # the person's next turn.
        _ = self.omh_todo(self.ACCEPTED_PLAN)
        self.write_native([native("1", "unrelated work", "in_progress")])

        fork = self.context(user_message="Review the conversation above", parent_session_id=SESSION)
        self.assertNotIn("was not mirrored", fork)
        self.assertIn("was not mirrored", self.context())
        # A delegate child names another parent; a compression continuation names none.
        self.write_native([native("1", "unrelated work", "in_progress")])
        self.assertIn("was not mirrored", self.context(parent_session_id=OTHER_SESSION))

    def test_the_line_names_the_kind_of_plan_that_holds(self):
        cases = {
            "accepted plan": self.ACCEPTED_PLAN,
            "plan awaiting acceptance": {**self.ACCEPTED_PLAN, "plan_stage": PLAN_STAGE_AWAITING_ACCEPTANCE},
            "code-story plan": {"action": "set", "template": "code-story"},
        }
        for index, (kind, plan) in enumerate(cases.items()):
            with self.subTest(kind=kind):
                session = f"{SESSION}-{index}"
                _ = self.omh_todo(plan, session=session)
                self.write_native([native("1", "unrelated work", "in_progress")], session=session)
                self.assertIn(f"this session's {kind} is held in omh_todo", self.note(session))

    def test_the_line_is_decided_from_the_record_as_it_stands(self):
        held = [native("1", "unrelated work", "in_progress")]
        cases = {
            "cleared since": lambda: self.omh_todo({"action": "clear"}),
            "re-declared as a plain plan": lambda: self.omh_todo({"action": "set", "items": [{"text": "x", "state": "active"}]}),
            "gone stale": lambda: self.age(minutes=25 * 60),
        }
        for name, after in cases.items():
            with self.subTest(case=name):
                _ = self.omh_todo(self.ACCEPTED_PLAN)
                self.write_native(held)
                _ = after()
                self.assertNotIn("was not mirrored", self.context())
                _ = self.omh_todo({"action": "clear"})

    def test_a_deferred_plan_gets_no_line(self):
        # The person paused the plan; the plan line says so and asks for
        # nothing, and "clear it" would be the wrong instruction.
        _ = self.omh_todo(self.ACCEPTED_PLAN)
        _ = self.omh_todo(
            {
                "action": "advance", "item": 1, "item_text": "lane a check", "state": "active",
                "deferred_reason": "the person asked for the release notes first",
            }
        )
        self.assertEqual(self.project([native("1", "release notes", "in_progress")]), "held_by_omh_todo")
        context = self.context(user_message="")
        self.assertIn("deferred: the person asked for the release notes first", context)
        self.assertNotIn("was not mirrored", context)

    def test_a_mirrored_write_forgets_an_earlier_held_write(self):
        # Held, cleared, mirrored, then a new accepted plan -- all before the
        # next turn. The last todo_list write WAS mirrored, so nothing is said.
        _ = self.omh_todo(self.ACCEPTED_PLAN)
        self.assertEqual(self.project([native("1", "unrelated work", "in_progress")]), "held_by_omh_todo")
        _ = self.omh_todo({"action": "clear"})
        self.assertEqual(self.project([native("1", "unrelated work", "in_progress")]), "projected")
        _ = self.omh_todo(self.ACCEPTED_PLAN)
        self.assertEqual(self.note(), "")

    def test_pending_lines_are_bounded_oldest_out_first(self):
        cap = native_todo._MAX_HELD_NOTES
        for index in range(cap):
            native_todo._note_held_write(f"s{index}")
        # Re-noting a session already pending moves it to newest and evicts
        # nobody: the table was full, and it still holds the same sessions.
        native_todo._note_held_write("s5")
        self.assertEqual(len(native_todo._HELD_NOTES), cap)
        self.assertEqual(next(iter(native_todo._HELD_NOTES)), "s0")
        self.assertEqual(list(native_todo._HELD_NOTES)[-1], "s5")
        # A new session at the cap pushes the oldest out, and only it.
        native_todo._note_held_write("one-more")
        self.assertEqual(len(native_todo._HELD_NOTES), cap)
        self.assertNotIn("s0", native_todo._HELD_NOTES)
        self.assertEqual(next(iter(native_todo._HELD_NOTES)), "s1")
        self.assertIn("s5", native_todo._HELD_NOTES)

    def test_a_stale_accepted_plan_is_replaced_and_its_acceptance_does_not_transfer(self):
        _ = self.omh_todo(
            {
                "action": "set",
                "items": [{"text": "options and tradeoffs", "state": "done"}, {"text": "go", "state": "done"}],
                "plan_stage": PLAN_STAGE_ACCEPTED,
            }
        )
        self.age(minutes=25 * 60)
        self.write_native([native("1", "write the code", "in_progress")])

        todo = self.todo()
        self.assertEqual([item["text"] for item in todo["items"]], ["write the code"])
        self.assertEqual(todo["plan_stage"], "")
        self.assertIsNone(
            plan_stage_edit_directive(
                tool_name="write_file",
                session_id=SESSION,
                omh_home=str(self.home),
                hermes_home=str(self.hermes),
            )
        )

    def test_a_cleared_accepted_plan_lets_the_native_list_in(self):
        _ = self.omh_todo(
            {"action": "set", "items": [{"text": "lane a check: `make test`", "state": "active"}], "plan_stage": PLAN_STAGE_ACCEPTED}
        )
        self.assertEqual(self.project([native("1", "unrelated work", "in_progress")]), "held_by_omh_todo")
        _ = self.omh_todo({"action": "clear"})
        self.assertEqual(self.project([native("1", "unrelated work", "in_progress")]), "projected")

    def test_an_open_template_plan_is_not_replaced(self):
        _ = self.omh_todo({"action": "set", "template": "code-story"})
        before = self.stored()
        self.assertEqual(self.project([native("1", "write the code", "in_progress")]), "held_by_omh_todo")
        self.assertEqual(self.stored(), before)

    def test_a_stale_held_plan_holds_nothing(self):
        _ = self.omh_todo(
            {
                "action": "set",
                "items": [{"text": "options and tradeoffs", "state": "active"}],
                "plan_stage": PLAN_STAGE_AWAITING_ACCEPTANCE,
            }
        )
        self.age(minutes=25 * 60)
        self.assertEqual(self.project([native("1", "write the code", "in_progress")]), "projected")


class HudPayloadTest(_NativeTodoHomeTest):
    """The payload names the tool holding the steps; each surface decides from that.

    The HUD payload stays whole -- the text lines (`omh runtime todo show`,
    `omh_hud`) are no host todo surface and keep every item -- and carries
    `source`, which the TUI dock and the Desktop pane read to leave a
    todo_list-held plan's items to Hermes' own panel.
    """

    def test_a_projected_plan_reaches_the_payload_as_todo_list_held_and_whole(self):
        self.write_native([native("1", "land the fix", "completed"), native("2", "open the PR", "in_progress")])
        payload = read_omh_hud(self.home, self.hermes, session_ref=SESSION)
        self.assertEqual(payload["todo"]["source"], NATIVE_TODO_SOURCE)
        self.assertEqual([item["text"] for item in payload["todo"]["items"]], ["land the fix", "open the PR"])
        self.assertTrue(any("open the PR" in line for line in payload["display"]["todo_lines"]))

    def test_recording_a_reason_with_omh_todo_keeps_the_plan_todo_list_held(self):
        self.write_native([native("1", "land the fix", "in_progress"), native("2", "open the PR")])
        _ = self.omh_todo(
            {"action": "advance", "item": 2, "item_text": "open the PR", "state": "pending", "blocked_reason": "CI credentials"}
        )
        self.assertEqual(self.stored()["source"], NATIVE_TODO_SOURCE)
        # A whole-list omh_todo set re-declares the plan, and then omh_todo holds it.
        _ = self.omh_todo({"action": "set", "items": [{"text": "land the fix", "state": "active"}]})
        self.assertEqual(self.stored()["source"], "omh_todo")

    def test_the_desktop_pane_keys_on_the_projection_source(self):
        self.assertIn(
            f"NATIVE_TODO_SOURCE = '{NATIVE_TODO_SOURCE}'",
            (REPO / "src/plugin_bundle/omh/desktop/plugin.js").read_text(encoding="utf-8"),
        )


class GuardsReadOneRecordTest(_NativeTodoHomeTest):
    """The continuation drive and the stall finding read the plan whichever tool wrote it."""

    ITEMS = [("land the fix", "done"), ("open the PR", "active"), ("watch CI", "pending")]
    NATIVE_STATUS = {"done": "completed", "active": "in_progress", "pending": "pending"}

    def declare_both(self) -> None:
        """The same plan in two sessions: one through omh_todo, one through todo_list."""
        _ = self.omh_todo(
            {"action": "set", "items": [{"text": text, "state": state} for text, state in self.ITEMS]},
            session=OTHER_SESSION,
        )
        self.write_native(
            [native(str(index), text, self.NATIVE_STATUS[state]) for index, (text, state) in enumerate(self.ITEMS)]
        )

    def test_the_continuation_drive_is_the_same_line_on_both_stores(self):
        self.declare_both()
        native_line = open_todo_reminder(omh_home=str(self.home), hermes_home=str(self.hermes), session_ref=SESSION)
        omh_line = open_todo_reminder(
            omh_home=str(self.home), hermes_home=str(self.hermes), session_ref=OTHER_SESSION
        )
        self.assertIn(TODO_CONTINUATION_RULE, native_line)
        self.assertIn("[OMH plan todo] 1/3 done · active: open the PR", native_line)
        self.assertEqual(native_line, omh_line)

    def test_the_turn_end_directive_drives_until_every_native_item_is_done(self):
        self.write_native([native("1", "land the fix", "completed"), native("2", "open the PR", "in_progress")])
        message, _ = plan_continuation_reading(
            omh_home=str(self.home), hermes_home=str(self.hermes), session_ref=SESSION
        )
        self.assertIn("next: open the PR", message)

        self.write_native([native("1", "land the fix", "completed"), native("2", "open the PR", "completed")])
        message, _ = plan_continuation_reading(
            omh_home=str(self.home), hermes_home=str(self.hermes), session_ref=SESSION
        )
        self.assertEqual(message, "")

    def test_a_recorded_block_stops_the_drive_on_a_native_plan(self):
        self.write_native([native("1", "land the fix", "completed"), native("2", "open the PR", "in_progress")])
        _ = self.omh_todo(
            {
                "action": "advance",
                "item": 2,
                "item_text": "open the PR",
                "state": "active",
                "blocked_reason": "waiting on CI credentials",
            }
        )
        self.write_native([native("1", "land the fix", "completed"), native("2", "open the PR", "in_progress")])
        message, _ = plan_continuation_reading(
            omh_home=str(self.home), hermes_home=str(self.hermes), session_ref=SESSION
        )
        self.assertEqual(message, "")

    def test_the_stall_finding_is_the_same_verdict_on_both_stores(self):
        self.declare_both()
        record_tool_call("Bash", omh_home=str(self.home), tool_call_id="probe")
        record_tool_call_close("probe", omh_home=str(self.home))
        self.age(minutes=60)
        self.age(minutes=60, session=OTHER_SESSION)

        native_stall = _todo_stall_status(str(self.home), str(self.hermes), SESSION)
        omh_stall = _todo_stall_status(str(self.home), str(self.hermes), OTHER_SESSION)
        self.assertEqual(native_stall, "unchanged")
        self.assertEqual(native_stall, omh_stall)

    def test_with_no_native_write_omh_todo_is_the_tracker(self):
        # `todo_list` absent, or deferred and never called: nothing reaches the
        # projection, and the guards read the omh_todo plan exactly as before.
        _ = self.omh_todo(
            {"action": "set", "items": [{"text": "land the fix", "state": "active"}, {"text": "open the PR"}]}
        )
        self.assertEqual(self.todo()["source"], "omh_todo")
        self.assertIn(
            TODO_CONTINUATION_RULE,
            open_todo_reminder(omh_home=str(self.home), hermes_home=str(self.hermes), session_ref=SESSION),
        )


class PresenceRuleTest(unittest.TestCase):
    """One wording for the presence rule, and no version in it."""

    def test_every_surface_that_names_todo_list_states_the_same_clause(self):
        clause = native_tool_presence_clause(NATIVE_TODO_TOOL)
        self.assertEqual(clause, "todo_list is in your tool list or tool_search finds it")
        for name, text in (
            ("awareness rule", PLAN_TODO_DECLARATION_RULE),
            ("omh_todo description", OMH_TODO_SCHEMA["description"]),
            ("plan nudge", PLAN_NUDGE_TEXT),
        ):
            with self.subTest(surface=name):
                self.assertIn(clause, text)
                self.assertIn("ONE tool", text)
        self.assertEqual(
            native_tool_presence_clause(NATIVE_TODO_TOOL, code=True),
            "`todo_list` is in your tool list or tool_search finds it",
        )

    def test_the_context_tool_hint_carries_the_carve_out(self):
        from omh.plugin_bundle.omh.awareness import awareness_primer_payload

        hint = next(line for line in awareness_primer_payload()["tool_hints"] if "plan checklist" in line)
        self.assertIn("omh_todo for a planning run, an accepted plan or a code-story template", hint)
        self.assertIn(native_tool_presence_clause(NATIVE_TODO_TOOL), hint)

    def test_the_four_surfaces_that_list_omh_todos_plans_name_the_same_three(self):
        # The plans `_held_by_omh_todo` holds a native write for -- a plan
        # stage or a template -- are the ones these four surfaces send to
        # omh_todo. The route hint and the context tool hint once named only
        # the first two, so a code-story run (`ultraqa`) was pointed at
        # todo_list and its write was then held. Other one-checklist lines list
        # no such plans: `ultrawork`'s opening step names only the accepted
        # plan it executes, the plan nudge fires when no plan exists, and the
        # board references only say which one tool holds the rows.
        from omh.plugin_bundle.omh.awareness import awareness_primer_payload
        from omh.skills.catalog import builtin_definitions

        hint = next(line for line in awareness_primer_payload()["tool_hints"] if "plan checklist" in line)
        policy = next(d.handoff_policy for d in builtin_definitions() if d.name == "todo-checklist")
        self.assertEqual(OMH_TODO_HELD_PLANS, "a planning run, an accepted plan or a code-story template")
        for name, text in (
            ("route hint plan_todo", PLAN_TODO_DECLARATION_RULE),
            ("context tool hint", hint),
            ("omh_todo description", OMH_TODO_SCHEMA["description"]),
            ("todo-checklist handoff policy", policy),
        ):
            with self.subTest(surface=name):
                self.assertIn(OMH_TODO_HELD_PLANS, text)

    def test_the_clause_states_another_tool_in_the_same_sentence_shape(self):
        # The shape a later surface (clarify, cronjob_manage, ...) states the
        # rule in, so every native tool reads under one sentence.
        self.assertEqual(
            f"when {native_tool_presence_clause('clarify', code=True)}",
            "when `clarify` is in your tool list or tool_search finds it",
        )


if __name__ == "__main__":
    unittest.main()
