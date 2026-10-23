"""`fallback` advances only the calling conversation's own route (#2175).

The `delegation.*` keys, the restore record and route provenance are one per
profile, shared by every session on it. `fallback` took its chain position
from the newest of them, so with two sessions on one profile the other
session's route decided this one's fallback: an error naming a model this
conversation never routed, a fallback from the other session's model, an
exhausted chain that still had candidates, or the other session's live route
advanced as if it were this one's.

The conversation is the host's `session_id` keyword plus its compression
continuations in `state.db`. A turn end here is the real hook
(`on_session_end`), not `clear`, so the turn-end restore is what takes the
route back out.

Every test passes explicit temporary homes. Nothing here may read or write
the developer's own `~/.hermes` or `~/.omh`.
"""

from __future__ import annotations

import itertools
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from _hermes_session_rows import compacted, row, write_sessions
from omh.plugin_bundle.omh.delegation_route_restore import (
    LEFTOVER_MATCH,
    delegation_route_restore_path,
    leftover_verdict,
    load_route_restore_record,
    newest_written_provenance,
)
from omh.plugin_bundle.omh.delegation_routing import read_delegation_route
from omh.plugin_bundle.omh.hermes_delegation import (
    append_delegation_route_provenance,
    delegation_route_provenance_path,
    load_delegation_route_provenance,
)
from omh.plugin_bundle.omh.hooks.session_hooks import on_session_end
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
            {"model": "deep-last", "reasoning_effort": "high"},
        ],
        "quick": [
            {"model": "quick-head", "reasoning_effort": "low"},
            {"model": "quick-next", "reasoning_effort": "low"},
        ],
    },
}

NO_POSITION = "no active route to fall back from; use set with a category first"


class FallbackPositionTestCase(unittest.TestCase):
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
        self._turns = itertools.count(1)

    def call(self, session: str | None, task: str = "", **args) -> dict:
        """A tool dispatch: `session` and `task` are the host's keywords."""
        args.setdefault("hermes_home", str(self.hermes_home))
        args.setdefault("omh_home", str(self.omh_home))
        dispatch = {} if session is None else {"session_id": session, "task_id": task or session}
        return json.loads(omh_delegate_route_handler(args, **dispatch))

    def route(self, session: str, *, end_turn: bool = True, **args) -> None:
        """One TUI turn that routes: a fresh task, then the turn-end restore."""
        task = f"{session}-turn-{next(self._turns)}"
        routed = self.call(session, task, action="set", **args)
        self.assertEqual(routed["status"], "routed", routed)
        if end_turn:
            ended = on_session_end(
                session_id=session,
                task_id=task,
                omh_home=str(self.omh_home),
                hermes_home=str(self.hermes_home),
            )
            self.assertEqual(ended["route_restore"]["status"], "cleared", ended)

    def fallback(self, session: str | None, **args) -> dict:
        return self.call(session, f"{session}-turn-{next(self._turns)}", action="fallback", **args)

    def current(self) -> dict:
        return read_delegation_route(self.hermes_home)


class FallbackAcrossConversationsTest(FallbackPositionTestCase):
    def test_another_conversations_newer_route_is_not_this_ones_position(self) -> None:
        # Scenario 1 of the issue: both routes are back out of the file, and
        # B's provenance record is the newest. On 4.0.0 A got "current
        # route model 'quick-head' does not match category 'deep'".
        self.route("A", category="deep")
        self.route("B", category="quick")

        result = self.fallback("A", category="deep")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["position_source"], "provenance")
        self.assertEqual(result["from"], "deep-head")
        self.assertEqual(result["applied"]["alias"], "deep-next")

    def test_without_a_category_the_position_is_still_this_conversations(self) -> None:
        # The same, with no category: on 4.0.0 A advanced inside B's chain.
        self.route("A", category="deep")
        self.route("B", category="quick")

        result = self.fallback("A")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["category"], "deep")
        self.assertEqual(result["from"], "deep-head")

    def test_another_conversations_live_route_is_not_this_ones_position(self) -> None:
        # Scenario 2: B's route is still in the keys, as a gateway route
        # that lasts the session is. On 4.0.0 A read it as `live_route`.
        self.route("A", category="deep")
        self.route("B", category="quick", end_turn=False)

        result = self.fallback("A", category="deep")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["position_source"], "provenance")
        self.assertEqual(result["from"], "deep-head")
        # Writing A's next route into the profile-wide keys is unchanged.
        self.assertEqual(self.current()["model"], "deep-next")

    def test_a_conversation_that_never_routed_fails_closed_and_leaves_the_live_route(self) -> None:
        # Scenario 3: on 4.0.0 A advanced B's live route one step and became
        # its recorded writer.
        self.route("B", category="deep", end_turn=False)
        keys, record = self.current(), load_route_restore_record(self.omh_home)
        records = load_delegation_route_provenance(self.omh_home)

        result = self.fallback("A", category="deep")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], NO_POSITION)
        self.assertEqual(result["position_source"], "none")
        self.assertEqual(result["from"], "")
        self.assertNotIn("applied", result)
        self.assertEqual(self.current(), keys)
        self.assertEqual(load_route_restore_record(self.omh_home), record)
        self.assertEqual(record["writer_session_id"], "B")
        self.assertEqual(load_delegation_route_provenance(self.omh_home), records)

    def test_a_chain_walk_steps_from_this_conversations_newest_record(self) -> None:
        # B routes between every step of A's walk, so A holds several
        # records with foreign ones between them; each step must start
        # from the one A wrote last.
        self.route("A", category="deep")
        walked = []
        for _ in range(3):
            self.route("B", category="quick")
            task = f"A-turn-{next(self._turns)}"
            result = self.call("A", task, action="fallback", category="deep")
            walked.append((result["status"], result["from"], result.get("applied", {}).get("alias", "")))
            on_session_end(
                session_id="A",
                task_id=task,
                omh_home=str(self.omh_home),
                hermes_home=str(self.hermes_home),
            )

        self.assertEqual(
            walked,
            [
                ("fell_back", "deep-head", "deep-next"),
                ("fell_back", "deep-next", "deep-last"),
                ("exhausted_to_inherit", "deep-last", ""),
            ],
        )

    def test_another_conversations_last_entry_does_not_exhaust_this_chain(self) -> None:
        # Inferred in the issue and observed while reproducing it: B's
        # position at the end of the chain restored the baseline for A,
        # whose own chain still had two candidates.
        self.route("A", category="deep")
        self.route("B", category="deep", model="deep-last", reasoning_effort="high")

        result = self.fallback("A", category="deep")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["from"], "deep-head")
        self.assertEqual(result["applied"]["alias"], "deep-next")

    def test_a_live_route_another_conversation_wrote_is_read_from_provenance(self) -> None:
        # B's live route holds the very model A routed, so the answer is the
        # same either way; where it came from is what proves whose it is.
        self.route("A", category="deep")
        self.route("B", category="deep", end_turn=False)

        result = self.fallback("A", category="deep")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["position_source"], "provenance")
        self.assertEqual(result["from"], "deep-head")

    def test_a_value_changed_since_this_conversations_write_is_not_its_live_position(self) -> None:
        # The record names A as the writer, but the file no longer holds
        # what A wrote.
        self.route("A", category="deep", end_turn=False)
        config = self.hermes_home / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("deep-head", "person-pin"),
            encoding="utf-8",
        )

        result = self.fallback("A", category="deep")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["position_source"], "provenance")
        self.assertEqual(result["from"], "deep-head")


class FallbackOwnershipTest(FallbackPositionTestCase):
    def test_one_conversation_alone_falls_back_after_its_turn_end(self) -> None:
        # The control: the issue's single-session case, unchanged.
        self.route("A", category="deep")

        result = self.fallback("A", category="deep")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["position_source"], "provenance")
        self.assertEqual(result["from"], "deep-head")

    def test_one_conversation_alone_reads_its_live_route_in_the_same_turn(self) -> None:
        self.route("A", category="deep", end_turn=False)

        result = self.fallback("A", category="deep")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["position_source"], "live_route")
        self.assertEqual(result["from"], "deep-head")

    def test_a_route_record_that_names_no_session_is_nobodys_position(self) -> None:
        # Written before the field existed, or by a writer that could not
        # name its session: unknown ownership is not this conversation's.
        append_delegation_route_provenance(
            {
                "origin": "head",
                "category": "deep",
                "alias": "deep-head",
                "wire_model": "deep-head",
                "provider": "",
                "reasoning_effort": "high",
                "written_at": 1.0,
            },
            self.omh_home,
        )

        result = self.fallback("A", category="deep")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["position_source"], "none")

    def test_a_caller_the_host_did_not_name_has_no_position(self) -> None:
        self.call(None, action="set", category="deep")
        self.assertEqual(self.current()["model"], "deep-head")

        result = self.call(None, action="fallback", category="deep")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["position_source"], "none")
        self.assertEqual(self.current()["model"], "deep-head")

    def test_a_model_supplied_session_id_cannot_claim_a_position(self) -> None:
        # Tool args come from the model; only the host keyword names the
        # caller.
        self.route("A", category="deep")

        result = self.call("C", action="fallback", category="deep", session_id="A")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["position_source"], "none")

    def test_the_leftover_check_still_reads_every_conversations_records(self) -> None:
        # The `delegation.*` keys are profile-wide, so recognising a leaked
        # OMH route is not one conversation's question. B's route leaks: its
        # record is lost and the keys still hold it.
        self.route("B", category="quick", end_turn=False)
        delegation_route_restore_path(self.omh_home).unlink()
        leaked = self.current()

        self.assertEqual(leftover_verdict(leaked, self.omh_home), LEFTOVER_MATCH)
        self.assertEqual(newest_written_provenance(self.omh_home)["session_id"], "B")
        self.assertEqual(newest_written_provenance(self.omh_home, session_ids={"A"}), {})
        self.assertEqual(newest_written_provenance(self.omh_home, session_ids=set()), {})

        routed = self.call("A", "A-turn", action="set", category="deep")

        self.assertEqual(routed["route_restore"], "baseline_captured_over_omh_leftover")


SHARED_HEAD_CHAINS = {
    "schema_version": "mixture_chain_overrides/v1",
    "categories": {
        "deep": [
            {"model": "shared-head", "reasoning_effort": "high"},
            {"model": "deep-next", "reasoning_effort": "high"},
        ],
        "quick": [
            {"model": "shared-head", "reasoning_effort": "low"},
            {"model": "quick-next", "reasoning_effort": "low"},
        ],
    },
}
AMBIGUOUS = "current route model 'shared-head' has ambiguous origins across deep, quick; pass category explicitly"


class FallbackCategoryTest(FallbackPositionTestCase):
    """With no category passed, the one this conversation's route was set under decides.

    A model in two chains used to fail as ambiguous although the
    conversation's own provenance record names its category (#2177 LOW-5).
    """

    def setUp(self) -> None:
        super().setUp()
        (self.omh_home / "routing" / "model-chains.json").write_text(
            json.dumps(SHARED_HEAD_CHAINS), encoding="utf-8"
        )

    def test_a_shared_model_falls_back_inside_the_category_this_conversation_set(self) -> None:
        self.route("A", category="deep")
        self.route("B", category="quick")

        result = self.fallback("A")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["position_source"], "provenance")
        self.assertEqual((result["category"], result["from"]), ("deep", "shared-head"))
        self.assertEqual(result["applied"]["alias"], "deep-next")

    def test_a_live_route_carries_the_category_it_was_set_under(self) -> None:
        self.route("A", category="quick", end_turn=False)

        result = self.fallback("A")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["position_source"], "live_route")
        self.assertEqual(result["applied"]["alias"], "quick-next")

    def test_a_category_passed_wins_over_the_recorded_one(self) -> None:
        self.route("A", category="deep")

        result = self.fallback("A", category="quick")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["applied"]["alias"], "quick-next")

    def test_a_route_set_without_a_category_is_still_ambiguous(self) -> None:
        # Nothing records which chain an explicit model came from, and
        # another conversation's category is not this one's.
        self.route("A", model="shared-head", reasoning_effort="high")
        self.route("B", category="deep")
        keys = self.current()

        result = self.fallback("A")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], AMBIGUOUS)
        self.assertEqual(self.current(), keys)

    def test_a_model_outside_the_recorded_category_is_refused(self) -> None:
        # A lane labelled deep that pinned a quick model: the recorded
        # category is used, and the model is not in its chain.
        self.route("A", category="deep", model="quick-next", reasoning_effort="low", end_turn=False)
        keys = self.current()

        result = self.fallback("A")

        self.assertEqual(result["status"], "error")
        self.assertEqual(
            result["error"],
            "current route model 'quick-next' does not match category 'deep', the one this "
            "conversation routed it under",
        )
        self.assertEqual(self.current(), keys)

    def test_a_record_that_did_not_write_the_live_route_lends_it_no_category(self) -> None:
        # The provenance append is unlocked and can be lost. A's deep route
        # is live but its record is gone, so A's newest record is the earlier
        # quick one, which does not describe the live route.
        self.route("A", category="quick")
        self.route("A", category="deep", end_turn=False)
        path = delegation_route_provenance_path(self.omh_home)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["records"].pop()
        path.write_text(json.dumps(payload), encoding="utf-8")

        result = self.fallback("A")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], AMBIGUOUS)

    def test_a_recorded_category_no_chain_has_is_not_used(self) -> None:
        append_delegation_route_provenance(
            {
                "origin": "head",
                "category": "retired-category",
                "alias": "deep-next",
                "wire_model": "deep-next",
                "provider": "",
                "reasoning_effort": "high",
                "session_id": "A",
                "written_at": 1.0,
            },
            self.omh_home,
        )

        result = self.fallback("A")

        self.assertEqual(result["status"], "exhausted_to_inherit", result)
        self.assertEqual(result["category"], "deep")


class FallbackAcrossCompressionTest(FallbackPositionTestCase):
    """A conversation Hermes rotated is still one conversation.

    With `compression.in_place: false`, Hermes closes the segment with
    `end_reason = 'compression'` and goes on in a new row whose
    `parent_session_id` names it; the host's `session_id` keyword then
    names the new row. The lineage is the one the HUD already owns route
    records with (`conversation_session_ids`).
    """

    def setUp(self) -> None:
        super().setUp()
        connection = sqlite3.connect(self.hermes_home / "state.db")
        try:
            connection.execute(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, parent_session_id TEXT,"
                " end_reason TEXT, ended_at REAL, model_config TEXT, started_at REAL)"
            )
            connection.executemany(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("A-before", "tui", None, "compression", 10.0, "{}", 1.0),
                    ("A-after", "tui", "A-before", None, None, "{}", 10.0),
                    ("A-branch", "tui", "A-before", None, None,
                     json.dumps({"_branched_from": "A-before"}), 11.0),
                ],
            )
            connection.commit()
        finally:
            connection.close()

    def test_a_continuation_falls_back_from_the_route_its_earlier_segment_wrote(self) -> None:
        self.route("A-before", category="deep")
        self.route("B", category="quick")

        result = self.fallback("A-after", category="deep")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["position_source"], "provenance")
        self.assertEqual(result["from"], "deep-head")

    def test_a_continuation_reads_the_live_route_its_earlier_segment_wrote(self) -> None:
        # The split lands mid-turn: the route is still in the file under the
        # pre-split writer.
        self.route("A-before", category="deep", end_turn=False)

        result = self.fallback("A-after", category="deep")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["position_source"], "live_route")
        self.assertEqual(result["from"], "deep-head")

    def test_a_branch_of_the_conversation_is_not_its_continuation(self) -> None:
        self.route("A-before", category="deep")

        result = self.fallback("A-branch", category="deep")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["position_source"], "none")



class FallbackAcrossADelegateChildsCompressionTest(FallbackPositionTestCase):
    """A delegate child Hermes rotated keeps the position its earlier segment routed.

    The child's continuation inherits `_delegate_from`, which the conversation
    identity once read as a fork, so the child lost its own pre-split route.
    It now shares the compaction walkers' link rule
    (`test_compaction_lineage_agreement`). Rows are in the shape Hermes'
    `SessionDB` writes.
    """

    def setUp(self) -> None:
        super().setUp()
        write_sessions(
            self.hermes_home,
            row("P"),
            compacted("C1", at=1050.0, parent="P", source="subagent", delegate_from="P"),
            row("C2", parent="C1", source="subagent", delegate_from="P", started_at=1050.0),
        )

    def test_a_rotated_delegate_child_falls_back_from_its_own_earlier_route(self) -> None:
        self.route("C1", category="deep")
        self.route("B", category="quick")

        result = self.fallback("C2", category="deep")

        self.assertEqual(result["status"], "fell_back", result)
        self.assertEqual(result["position_source"], "provenance")
        self.assertEqual(result["from"], "deep-head")

    def test_the_parent_is_not_the_childs_conversation(self) -> None:
        self.route("C1", category="deep")

        result = self.fallback("P", category="deep")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["position_source"], "none")


if __name__ == "__main__":
    unittest.main()
