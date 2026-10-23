"""Contracts for a detached fork's `pre_llm_call`: it is not one of the session's turns.

Hermes builds its background review, and the `/btw` side question, as a fork
of the live agent that keeps the parent's session id and names that same id
as its parent (`build_cache_parity_fork`, `agent/background_review.py`). On
the plugin's floor, Hermes 0.21.1 (tag v2026.9.7), the fork's turn reaches
`pre_llm_call` with `session_id == parent_session_id`; from 0.21.2 (tag
v2026.9.11) Hermes skips the hook for a persistence-disabled agent. The review
runs right after a turn and the side question during one, so on the floor a
fork spent every once-per-session fact the hook keeps for the person's next
turn before that turn arrived.

Each fact is pinned in both directions: a fork call leaves it for the person's
turn, and an ordinary turn still spends it. The fork itself is answered with
nothing, which is what the newer host gives it.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh import jev_consent, skill_shortlist
from omh.plugin_bundle.omh.awareness_delivery import read_awareness_delivery
from omh.plugin_bundle.omh.hooks import llm_hooks, nudge_budget
from omh.plugin_bundle.omh.hooks.llm_hooks import pre_llm_call
from omh.plugin_bundle.omh.jev_consent import arm_tool_call, consent_observed, disarm_tool_call
from omh.plugin_bundle.omh.todo_reconciliation import TODO_RECONCILIATION_FULL_TURNS
from omh.plugin_bundle.omh.todo_store import build_todo_record, write_todo
from test_kanban_board_reader import build_board, task
from test_plugin_hermes_delegation import PARENT_ID, _build_state_db

SESSION = PARENT_ID
# What a review fork is asked; any text will do, since the fork reads none.
FORK_REQUEST = "Look back over this chat and keep anything worth remembering."
# A request that matches the awareness vocabulary on a later turn, so it
# carries a route hint, and one that ranks a candidate set.
ROUTED_REQUEST = "review this pull request for security issues"
RANKED_REQUEST = "refactor the auth module and update the callers"
ROUTE_HINT = "[OMH Route Hint]"
CANDIDATE_LINE = "Skills that may fit this request:"
BOARD_CARD = "[OMH board]"
# Only the full reconciliation rule says this; the budgeted first clause does not.
FULL_RULE = "visible contradiction"


def _context(payload: object) -> str:
    return str(payload.get("context", "")) if isinstance(payload, dict) else ""


class ForkTurnStateTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.hermes = root / ".hermes"
        self.hermes.mkdir()
        self.omh = root / ".omh"
        _build_state_db(self.hermes, [])
        env = mock.patch.dict(os.environ, {"HERMES_KANBAN_HOME": ""})
        env.start()
        self.addCleanup(env.stop)
        # Both mark a process with no person typing; the Jev case needs one.
        for name in (jev_consent.KANBAN_TASK_ENV, jev_consent.SINGLE_QUERY_ENV):
            os.environ.pop(name, None)
        for reset in (
            llm_hooks._reset_board_card_state,
            llm_hooks._reset_awareness_section_state,
            skill_shortlist.reset_candidate_line_state,
            nudge_budget.reset_nudge_budget,
            jev_consent.reset_turn_markers,
        ):
            reset()
            self.addCleanup(reset)

    def turn(self, message: str, *, turn_id: str = "t1", **extra: object) -> dict | None:
        """One later turn of the session, as the host opens it on a TUI."""
        call: dict[str, object] = dict(
            session_id=SESSION,
            user_message=message,
            is_first_turn=False,
            platform="tui",
            turn_id=turn_id,
            omh_home=str(self.omh),
            hermes_home=str(self.hermes),
        )
        call.update(extra)
        return pre_llm_call(**call)

    def fork(self, message: str = FORK_REQUEST) -> dict | None:
        """The fork's turn on the floor: its own turn id, the parent's id twice."""
        return self.turn(message, turn_id="fork", parent_session_id=SESSION)

    def open_plan(self) -> None:
        record = build_todo_record(
            "plan",
            [{"text": "write the parser", "state": "done"}, {"text": "open the PR", "state": "active"}],
            source="test",
            session_ref=SESSION,
        )
        _ = write_todo(self.omh, record)

    def own_lane(self) -> None:
        build_board(self.hermes / "kanban.db", [task("t_aaaa0001", "running", session_id=SESSION)])

    def test_the_fork_is_answered_with_nothing(self) -> None:
        self.open_plan()
        self.own_lane()
        self.assertIsNone(self.fork(ROUTED_REQUEST))
        self.assertIsNone(self.fork(RANKED_REQUEST))
        self.assertEqual(read_awareness_delivery(str(self.omh))["delivery_count"], 0)

    def test_route_guidance_is_left_for_the_persons_turn(self) -> None:
        _ = self.fork(ROUTED_REQUEST)
        self.assertIn(ROUTE_HINT, _context(self.turn(ROUTED_REQUEST)))
        # An ordinary turn spends it: the same route does not go out twice.
        self.assertNotIn(ROUTE_HINT, _context(self.turn(ROUTED_REQUEST, turn_id="t2")))
        self.assertEqual(read_awareness_delivery(str(self.omh))["route_hint_count"], 1)

    def test_the_candidate_line_is_left_for_the_persons_turn(self) -> None:
        _ = self.fork(RANKED_REQUEST)
        self.assertIn(CANDIDATE_LINE, _context(self.turn(RANKED_REQUEST)))
        self.assertNotIn(CANDIDATE_LINE, _context(self.turn(RANKED_REQUEST, turn_id="t2")))

    def test_the_board_card_is_left_for_the_persons_turn(self) -> None:
        self.own_lane()
        _ = self.fork()
        self.assertIn(BOARD_CARD, _context(self.turn("where were we")))
        self.assertNotIn(BOARD_CARD, _context(self.turn("where were we", turn_id="t2")))

    def test_the_plan_lines_full_rule_turns_are_the_persons(self) -> None:
        self.open_plan()
        for index in range(TODO_RECONCILIATION_FULL_TURNS):
            with self.subTest(turn=index):
                self.assertIn(FULL_RULE, _context(self.turn("", turn_id=f"t{index}")))
            _ = self.fork()
        # The person's own turns spent the budget, so the next one is budgeted.
        last = _context(self.turn("", turn_id="last"))
        self.assertIn("[OMH plan todo]", last)
        self.assertNotIn(FULL_RULE, last)

    def test_the_jev_marker_is_left_for_the_persons_turn(self) -> None:
        # A side question runs while the person's turn is still in flight.
        _ = self.turn("ask jev whether this is done", turn_id="t1")
        _ = self.fork()
        arm_tool_call(SESSION, "t1", "call-1")
        self.assertTrue(consent_observed(SESSION))
        disarm_tool_call(SESSION, "call-1")
        # The person's next turn replaces it.
        _ = self.turn("carry on", turn_id="t2")
        arm_tool_call(SESSION, "t2", "call-2")
        self.assertFalse(consent_observed(SESSION))

    def test_a_later_turn_naming_no_parent_is_a_turn(self) -> None:
        # The ordinary shape: a real session id and the empty parent the host
        # sends for an agent that is not a child.
        self.own_lane()
        self.assertIn(BOARD_CARD, _context(self.turn("where were we", parent_session_id="")))

    def test_a_turn_naming_another_parent_or_none_is_a_turn(self) -> None:
        self.own_lane()
        # A delegate child names its parent's id, never its own.
        self.assertIn(BOARD_CARD, _context(self.turn("where were we", parent_session_id="20260818_090000_other")))
        # No ids at all is not a fork either: the first turn still gets the primer.
        self.assertIsNotNone(
            pre_llm_call(
                session_id="",
                parent_session_id="",
                user_message="add a retry to the upload handler",
                is_first_turn=True,
                omh_home=str(self.omh),
                hermes_home=str(self.hermes),
            )
        )


if __name__ == "__main__":
    unittest.main()
