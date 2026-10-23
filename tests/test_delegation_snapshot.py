"""Delegated work where people see it: in a chat reply at three moments, in the HUD, printed by `omh hud`.

A chat reply goes through OMH's own `transform_llm_output` handler with the
keyword arguments Hermes passes it (`agent/turn_finalizer.py`). On a chat
platform a run gets a start block once, a plain count line on a turn its
counts changed, and an end block once; never the box drawing, and nothing
anywhere else.
"""

from __future__ import annotations

import json
import os
import time
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from _cli_harness import run_cli  # noqa: E402
from _delegation_graphs import dag_export_run, graph, lane, nested, owner_mock, root, with_tokens  # noqa: E402
from _delegation_state import PARENT, dispatched, homes, route, start, stop  # noqa: E402
from omh.plugin_bundle.omh.delegation_ledger import ledger_key  # noqa: E402
from omh.plugin_bundle.omh.delegation_ledger import posted_record, read_ledger  # noqa: E402
from omh.plugin_bundle.omh.delegation_snapshot import (  # noqa: E402
    LINE_CELLS,
    MESSENGER_PLATFORMS,
    STALL_SECONDS,
    added_clause,
    count_line,
    end_block,
    lane_key,
    lanes_of,
    start_block,
    tally,
)
from omh.plugin_bundle.omh.delegation_view import UNICODE, cells  # noqa: E402
from omh.plugin_bundle.omh.hooks import result_transforms  # noqa: E402
from omh.plugin_bundle.omh.runtime_reader import _hud_graph_preference, read_omh_hud  # noqa: E402

BOX_GLYPHS = frozenset(UNICODE.lines.values()) | {UNICODE.arrow}


def reply(text: str = "Dispatched three lanes.", *, platform: str = "discord", session: str = PARENT,
          turn: str = "turn-3") -> str | None:
    return result_transforms.transform_llm_output(
        response_text=text, session_id=session, model="claude-opus-5-5", platform=platform, turn_id=turn
    )


def three_lanes(home, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    home.session(PARENT, "claude-opus-5-5", effort="high")
    for index, (title, category, model) in enumerate((("Refresh HUD docs", "writing", "claude-opus-5-5"),
                                                      ("Polish HUD CLI", "quick", "glm-5.3-flash"),
                                                      ("Update HUD tests", "deep", "claude-opus-5-5"))):
        route(home, category, model, "medium", written_at=now - 5 + index)
        start(home, f"child-{index}", f"sa-{index}", title, now=now - 4 + index)
        home.session(f"child-{index}", model, parent=PARENT, effort="medium")
    dispatched(home, ["sa-0", "sa-1", "sa-2"], now=now)


START3 = """\
Dispatched three lanes.

```
DAG · delegation · 3 tasks · 1 layer
1  Refresh HUD docs · Polish HUD CLI · Update HUD tests
```"""
END3 = """\
All lanes are back.

```
DAG · delegation · ended
✓ Done     Polish HUD CLI
✗ Failed   Update HUD tests (no model call)
▪ Stopped  Refresh HUD docs (interrupted)
first failure: Update HUD tests (no model call)
3 tasks · 1 done · 1 failed · 1 stopped
```"""


def write_team(home, units: list[dict], *, at: float) -> None:
    """The conversation's `omh_team/v1` record, written as the team writes it, last changed at ``at``."""
    from test_delegation_graph import team_record

    directory = home.omh / "runtime" / "teams" / ledger_key(PARENT)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "hud-graph.json"
    path.write_text(json.dumps(team_record(units)), encoding="utf-8")
    os.utime(path, (at, at))


def at(moment: float):
    """The wall clock the chat post reads, set to ``moment``."""
    return patch("omh.plugin_bundle.omh.delegation_snapshot.time.time", return_value=moment)


def chain(depth: int, width: int = 1) -> dict:
    """``depth`` layers of ``width`` lanes, each layer depending on the whole layer above it."""
    nodes, edges, above = [root()], [], ["root"]
    for layer in range(depth):
        ids = [f"n{layer}-{index}" for index in range(width)]
        nodes += [lane(node_id, f"Stage {layer} part {index}", "deep", "claude-opus-5-5", "high", "waiting",
                       order=len(nodes)) for index, node_id in enumerate(ids)]
        edges += [(upper, lower, "dispatch" if upper == "root" else "depends_on") for upper in above for lower in ids]
        above = ids
    from omh.plugin_bundle.omh.delegation_graph import _layers

    built = graph(nodes, edges, name="team ulw")
    built["layers"] = _layers(built["nodes"], built["edges"])
    return built


class MomentTests(unittest.TestCase):
    """The three moments of a run, each pinned as the text a chat user reads."""

    def test_a_run_posts_its_start_once_a_count_line_per_change_and_its_end_once(self) -> None:
        with homes() as home:
            three_lanes(home)
            self.assertEqual(reply(), START3)
            self.assertIsNone(reply("Still working.", turn="turn-4"), "nothing changed: nothing is posted")
            home.usage("child-0", "claude-opus-5-5", last_seen=time.time())
            self.assertEqual(reply("Lane one runs.", turn="turn-5"),
                             "Lane one runs.\n\nDAG delegation · 0/3 done · 1 running")
            home.usage("child-1", "glm-5.3-flash", last_seen=time.time())
            stop(home, "child-1", now=time.time())
            self.assertEqual(reply("Polish finished.", turn="turn-6"),
                             "Polish finished.\n\nDAG delegation · 1/3 done · 1 running")
            stop(home, "child-2", now=time.time())  # completed with no model call: a failure
            self.assertEqual(reply("Tests returned.", turn="turn-7"),
                             "Tests returned.\n\nDAG delegation · 1/3 done · 1 running · 1 failed (Update HUD tests)")
            stop(home, "child-0", status="interrupted", now=time.time())
            self.assertEqual(reply("All lanes are back.", turn="turn-8"), END3)
            self.assertIsNone(reply("Anything else?", turn="turn-9"), "the end is posted once")
            self.assertEqual(posted_record(home.omh, PARENT)["phase"], "closed")

    def test_a_run_goes_on_across_a_rotating_compaction_of_its_parent(self) -> None:
        # Hermes' rotating compaction (`compression.in_place: false`) moves the conversation to a new session id
        # mid-run, and names that segment as the parent when a child stops: the run neither starts over nor stalls.
        later = "20261020_150000_later"
        with homes() as home:
            three_lanes(home)
            self.assertEqual(reply(), START3)
            home.rotate(PARENT, later)
            home.usage("child-1", "glm-5.3-flash", last_seen=time.time())
            stop(home, "child-1", parent=later, now=time.time())

            def hud() -> dict:
                return read_omh_hud(home.omh, home.hermes, session_ref=later, status={"runs": []})["delegation_graph"]

            self.assertEqual((hud()["status"], len(hud()["nodes"]), hud()["chat"]["moment"]), ("active", 4, "start"))
            self.assertEqual(reply("Polish finished.", session=later, turn="turn-6"),
                             "Polish finished.\n\nDAG delegation · 1/3 done · 0 running")
            self.assertIsNone(reply("Still working.", session=later, turn="turn-7"), "the new segment's post counts")
            self.assertEqual(hud()["chat"]["moment"], "count")

    def test_a_chat_post_never_carries_the_box_drawing(self) -> None:
        with homes() as home:
            three_lanes(home)
            posts = [reply()]
            home.usage("child-0", "claude-opus-5-5", last_seen=time.time())
            posts.append(reply("Running.", turn="turn-4"))
            for child in ("child-0", "child-1", "child-2"):
                home.usage(child, "claude-opus-5-5", last_seen=time.time())
                stop(home, child, now=time.time())
            posts.append(reply("Done.", turn="turn-5"))
        self.assertTrue(all(posts), posts)
        for post in posts:
            self.assertFalse(BOX_GLYPHS & set(post), post)

    def test_a_run_its_first_reply_finds_settled_gets_the_end_alone(self) -> None:
        with homes() as home:
            three_lanes(home)
            for child in ("child-0", "child-1", "child-2"):
                home.usage(child, "claude-opus-5-5", last_seen=time.time())
                stop(home, child, now=time.time())
            post = reply("A synchronous call came back.")
            self.assertIn("DAG · delegation · ended", post)
            self.assertIn("3 tasks · 3 done · 0 failed · 0 stopped", post)
            self.assertNotIn("layer", post)
            self.assertIsNone(reply("Next.", turn="turn-4"))

    def test_an_open_run_gets_its_end_however_long_it_took_to_settle(self) -> None:
        with homes() as home:
            now = time.time() - 3 * 3600
            three_lanes(home, now=now)
            with patch("omh.plugin_bundle.omh.delegation_snapshot.time.time", return_value=now + 1):
                self.assertIn("1  Refresh HUD docs", reply())
            for child in ("child-0", "child-1", "child-2"):
                home.usage(child, "claude-opus-5-5", last_seen=now + 60)
                stop(home, child, now=now + 60)
            self.assertIn("DAG · delegation · ended", reply("Back after lunch.", turn="turn-4"),
                          "settled hours ago, past the graph's linger: the run's end still posts once")

    def test_a_new_run_after_an_end_lists_only_its_own_lanes(self) -> None:
        with homes() as home:
            three_lanes(home)
            reply()
            for child in ("child-0", "child-1", "child-2"):
                home.usage(child, "claude-opus-5-5", last_seen=time.time())
                stop(home, child, now=time.time())
            self.assertIn("ended", reply("Done.", turn="turn-4"))
            start(home, "child-9", "sa-9", "Write the release note", turn="turn-5", now=time.time())
            home.session("child-9", "claude-opus-5-5", parent=PARENT)
            dispatched(home, ["sa-9"], delegation_id="deleg-2", turn="turn-5", now=time.time())
            post = reply("One more lane.", turn="turn-5")
            self.assertIn("DAG · delegation · 1 task · 1 layer\n1  Write the release note\n", post)
            self.assertNotIn("Polish HUD CLI", post)

    def test_the_fingerprint_and_the_counts_keep_a_post_from_repeating(self) -> None:
        with homes() as home:
            three_lanes(home)
            self.assertIsNotNone(reply())
            self.assertIsNone(reply("Same graph.", turn="turn-4"), "same fingerprint")
            home.session("child-1", "glm-5.3-flash", parent=PARENT, effort="high")  # a label moves, no count does
            fingerprint = posted_record(home.omh, PARENT)["fingerprint"]
            self.assertIsNone(reply("Effort observed.", turn="turn-5"), "a new fingerprint with the same counts")
            self.assertEqual(posted_record(home.omh, PARENT)["fingerprint"], fingerprint)
            home.usage("child-1", "glm-5.3-flash", last_seen=time.time())
            self.assertIsNotNone(reply("Running now.", turn="turn-6"), "the counts moved")

    def test_a_run_whose_team_part_never_moves_ends_stalled_with_what_settled(self) -> None:
        from test_delegation_graph import unit

        with homes() as home:
            now = time.time()
            home.session(PARENT, "claude-opus-5-5", effort="high")
            start(home, "child-0", "sa-0", "[omh-team:hud-graph/a/attempt-1] Port the parser", now=now - 5)
            home.session("child-0", "claude-opus-5-5", parent=PARENT)
            sent = {"n": 1, "dispatch": {"child_session_id": "child-0", "subagent_id": "sa-0"}, "inconclusive": []}
            write_team(home, [unit("a", "Port the parser", [], [sent]), unit("b", "Wire the CLI", ["a"])], at=now - 5)
            with at(now):
                self.assertIn("DAG · team hud-graph · 2 tasks · 2 layers\n1  Port the parser\n2  Wire the CLI", reply())
            home.usage("child-0", "claude-opus-5-5", last_seen=now + 10)
            stop(home, "child-0", now=now + 20)
            passed = {**sent, "returned_at": "t", "check": {"outcome": "passed"}}
            write_team(home, [unit("a", "Port the parser", [], [passed]), unit("b", "Wire the CLI", ["a"])],
                       at=now + 30)
            with at(now + 40):
                self.assertEqual(reply("The parser passed.", turn="turn-4"),
                                 "The parser passed.\n\nDAG team hud-graph · 1/2 done · 0 running")
            stalled = now + 30 + STALL_SECONDS + 60
            with at(stalled - 120):
                self.assertIsNone(reply("Waiting on the CLI part.", turn="turn-5"), "a quiet minute is not a stall")
            with at(stalled):
                post = reply("Anything new?", turn="turn-6")
            self.assertTrue(post.endswith("```\nDAG · team hud-graph · stalled\n✓ Done     Port the parser\n"
                                          "▪ Stopped  Wire the CLI (waiting)\n2 tasks · 1 done · 0 failed · 1 stopped\n```"),
                            post)
            renamed = {**passed}
            write_team(home, [unit("a", "Port the parser module", [], [renamed]), unit("b", "Wire the CLI", ["a"])],
                       at=now + 30)
            with at(stalled + 60):
                self.assertIsNone(reply("Still nothing.", turn="turn-7"), "a part still not moving is not news")
            start(home, "child-1", "sa-1", "[omh-team:hud-graph/b/attempt-1] Wire the CLI", now=stalled + 100)
            home.session("child-1", "claude-opus-5-5", parent=PARENT)
            wiring = {"n": 1, "dispatch": {"child_session_id": "child-1", "subagent_id": "sa-1"}, "inconclusive": []}
            write_team(home, [unit("a", "Port the parser module", [], [renamed]),
                              unit("b", "Wire the CLI", ["a"], [wiring])], at=stalled + 100)
            with at(stalled + 120):
                post = reply("Wiring the CLI now.", turn="turn-8")
            self.assertIn("DAG · team hud-graph · 1 task · 1 layer\n1  Wire the CLI\n", post,
                          "the part that moved opens a run of its own; the closed part stays out")

    def test_a_team_that_has_sent_nothing_yet_posts_its_start(self) -> None:
        from test_delegation_graph import unit

        with homes() as home:
            now = time.time()
            home.session(PARENT, "claude-opus-5-5", effort="high")
            write_team(home, [unit("a", "Port the parser", []), unit("b", "Wire the CLI", ["a"])], at=now - 5)
            with at(now):
                post = reply("Planned two parts.")
            self.assertIn("DAG · team hud-graph · 2 tasks · 2 layers", post)
            self.assertEqual(posted_record(home.omh, PARENT)["moment"], "start")

    def test_a_post_that_could_not_be_recorded_is_not_posted(self) -> None:
        with homes() as home, patch(
            "omh.plugin_bundle.omh.delegation_snapshot.note_posted", return_value=False
        ):
            three_lanes(home)
            self.assertIsNone(reply())


class CapTests(unittest.TestCase):
    def test_a_block_over_the_cap_falls_back_to_the_count_line_and_the_moment_passes(self) -> None:
        with homes() as home:
            three_lanes(home)
            long_reply = "x" * 1_700  # 1,700 + the start block passes 90% of Discord's 2,000; the line does not
            self.assertEqual(reply(long_reply), f"{long_reply}\n\nDAG delegation · 0/3 done · 0 running")
            self.assertEqual(posted_record(home.omh, PARENT)["moment"], "start")
            self.assertIsNone(reply("Short now.", turn="turn-4"), "the start was posted as its count line")

    def test_with_no_room_even_for_the_count_line_nothing_is_appended_until_a_reply_fits(self) -> None:
        with homes() as home:
            three_lanes(home)
            self.assertIsNone(reply("x" * 1_790))
            record = read_ledger(home.omh, PARENT)
            self.assertEqual((record["posted"], record["skipped"]["reason"]), ({}, "graph_skipped_cap"))
            self.assertEqual(reply("Short.", turn="turn-4"), START3.replace("Dispatched three lanes.", "Short."))
            self.assertEqual(read_ledger(home.omh, PARENT)["skipped"], {})
        with homes() as home:
            three_lanes(home)
            self.assertIsNotNone(reply("x" * 1_790, platform="slack"), "Slack's cap is 39,000")

    def test_an_end_over_the_cap_falls_back_to_the_count_line(self) -> None:
        with homes() as home:
            three_lanes(home)
            reply()
            for child in ("child-0", "child-1", "child-2"):
                home.usage(child, "claude-opus-5-5", last_seen=time.time())
                stop(home, child, now=time.time())
            long_reply = "y" * 1_700
            self.assertEqual(reply(long_reply, turn="turn-4"), f"{long_reply}\n\nDAG delegation · 3/3 done · 0 running")
            self.assertEqual(posted_record(home.omh, PARENT)["phase"], "closed")

    def test_telegram_is_measured_in_utf16_units_and_matrix_in_bytes(self) -> None:
        from omh.plugin_bundle.omh.delegation_snapshot import measured, message_cap

        self.assertEqual(measured("a😀", "utf16"), 3)
        self.assertEqual(measured("a😀", "utf8"), 5)
        self.assertEqual(measured("a😀", "chars"), 2)
        self.assertEqual(message_cap("telegram"), (4_096, "utf16"))
        with patch.dict(os.environ, {"MATRIX_MAX_MESSAGE_LENGTH": "800"}):
            self.assertEqual(message_cap("matrix"), (800, "utf8"))
        with patch.dict(os.environ, {"MATRIX_MAX_MESSAGE_LENGTH": "1"}):
            self.assertEqual(message_cap("matrix"), (500, "utf8"), "Hermes clamps it to 500")
        with homes() as home:
            three_lanes(home)
            self.assertIsNone(reply("😀" * 1_840, platform="telegram"), "1,840 emoji are 3,680 UTF-16 units")

    def test_a_reply_ending_in_an_open_fence_goes_out_alone_and_trailing_space_is_kept(self) -> None:
        with homes() as home:
            three_lanes(home)
            self.assertIsNone(reply("Here is the diff:\n```python\nprint(1)\n"))
            self.assertEqual(read_ledger(home.omh, PARENT)["skipped"]["reason"], "graph_skipped_open_fence")
            kept = reply("Closed.\n```\ncode\n```\n  ", turn="turn-4")
            self.assertTrue(kept.startswith("Closed.\n```\ncode\n```\n  \n\n```\nDAG ·"), kept[:60])


class SurfaceTests(unittest.TestCase):
    def test_every_supported_platform_and_no_other(self) -> None:
        for platform in sorted(MESSENGER_PLATFORMS):
            with self.subTest(platform=platform), homes() as home:
                three_lanes(home)
                self.assertIn("```\nDAG ·", reply(platform=platform))
        for platform in ("tui", "cli", "desktop", "subagent", "cron", "api_server", "", "Discord-ish"):
            with self.subTest(platform=platform), homes() as home:
                three_lanes(home)
                self.assertIsNone(reply(platform=platform))

    def test_the_graph_switch_turns_the_post_off(self) -> None:
        for value in ("off", "OFF", " Off "):
            with self.subTest(value=value), homes() as home, patch.dict(os.environ, {"OMH_SUBAGENT_GRAPH": value}):
                three_lanes(home)
                self.assertIsNone(reply())
        with homes() as home, patch.dict(os.environ, {"OMH_SUBAGENT_GRAPH": "ON"}):
            three_lanes(home)
            self.assertIn("```\nDAG ·", reply())

    def test_a_childs_own_turn_carries_nothing(self) -> None:
        with homes() as home:
            three_lanes(home)
            self.assertIsNone(reply(session="child-0"))

    def test_the_post_follows_the_honest_stop_and_a_failure_in_it_keeps_the_stop(self) -> None:
        with homes() as home, patch.object(
            result_transforms, "honest_stop_output", return_value="Dispatched three lanes.\n\nStopping here."
        ):
            three_lanes(home)
            composed = reply()
            self.assertTrue(composed.startswith("Dispatched three lanes.\n\nStopping here.\n\n```\nDAG ·"))
            with patch("omh.plugin_bundle.omh.delegation_snapshot.build_delegation_graph",
                       side_effect=AttributeError("bug")):
                self.assertEqual(reply(turn="turn-9"), "Dispatched three lanes.\n\nStopping here.")
            self.assertGreaterEqual(result_transforms.snapshot_failures["AttributeError"], 1)

    def test_no_delegation_no_post(self) -> None:
        with homes():
            self.assertIsNone(reply())


class TextTests(unittest.TestCase):
    """The start block, count line and end block as pure functions of a graph."""

    def test_a_start_block_lists_eight_layers_and_counts_the_rest(self) -> None:
        deep = chain(11)
        block = start_block(deep, lanes_of(deep)).splitlines()
        self.assertEqual(block[1], "DAG · team ulw · 11 tasks · 11 layers")
        self.assertEqual(block[2:4], ["1  Stage 0 part 0", "2  Stage 1 part 0"])
        self.assertEqual(block[9], "8  Stage 7 part 0")
        self.assertEqual(block[10:], ["+3 more", "```"])

    def test_a_wide_layer_counts_the_titles_that_do_not_fit_its_line(self) -> None:
        wide = chain(2, width=9)
        lines = start_block(wide, lanes_of(wide)).splitlines()[2:4]
        for line in lines:
            self.assertLessEqual(cells(line), LINE_CELLS)
            shown = line.count("Stage")
            self.assertTrue(line.endswith(f" +{9 - shown}"), line)

    def test_a_lane_key_survives_a_rename_for_a_native_child_only(self) -> None:
        self.assertEqual(lane_key("delegation", "s:sa-1"), lane_key("team hud-graph", "s:sa-1"),
                         "a native child closed by an end stays closed when a team record renames the graph")
        self.assertNotEqual(lane_key("team a", "t:u1"), lane_key("team b", "t:u1"), "two teams' parts are two lanes")

    def test_the_first_failure_is_the_one_that_failed_first(self) -> None:
        later = lane("a", "Port the auth module", "deep", "m", "", "failed", order=1, status="timeout")
        earlier = lane("b", "Fix the parser", "deep", "m", "", "failed", order=2)
        later["ended_at"], earlier["ended_at"] = 200.0, 100.0
        self.assertEqual(tally([later, earlier])["first_failed"], "Fix the parser")
        unstamped = lane("c", "Rotate keys", "deep", "m", "", "failed", order=3)
        self.assertEqual(tally([unstamped, later])["first_failed"], "Port the auth module",
                         "a failure with no stop time comes after one with a time")

    def test_the_count_line(self) -> None:
        lanes = [lane("a", "Port the auth module", "deep", "m", "", "failed", order=1, status="timeout"),
                 lane("b", "Refresh docs", "quick", "m", "", "running", order=2),
                 lane("c", "Fix the parser", "deep", "m", "", "failed", order=3),
                 lane("d", "Write notes", "quick", "m", "", "done", order=4)]
        self.assertEqual(count_line("delegation", tally(lanes)),
                         "DAG delegation · 1/4 done · 1 running · 2 failed (Port the auth module)")
        self.assertEqual(count_line("delegation", tally(lanes[1:2] + lanes[3:])),
                         "DAG delegation · 1/2 done · 1 running")

    def test_a_lanes_token_count_never_reaches_a_chat_post(self) -> None:
        # Messengers do not change: the start, count and end texts read no count, so a lane's `output_tokens`
        # leaves every one of them as it was.
        for make in (dag_export_run, nested, owner_mock):
            plain = make()
            counted = with_tokens(make(), {node["id"]: 1234 for node in make()["nodes"]})
            with self.subTest(graph=make.__name__):
                for render in (
                    lambda payload: start_block(payload, lanes_of(payload)),
                    lambda payload: end_block(payload, lanes_of(payload)),
                    lambda payload: count_line("delegation", tally(lanes_of(payload))),
                ):
                    self.assertEqual(render(counted), render(plain))
                    self.assertNotIn("↓", render(counted))

    def test_an_end_block_lists_working_only_while_a_lane_still_runs(self) -> None:
        lanes = [lane("a", "Port the auth module", "deep", "m", "", "failed", order=1, status="timeout"),
                 lane("b", "Refresh docs", "quick", "m", "", "unknown", order=2, status="no news"),
                 lane("c", "Write notes", "quick", "m", "", "done", order=3)]
        settled = end_block(graph([root(), *lanes], []), lanes)
        self.assertEqual(settled.splitlines()[1:-1], [
            "DAG · delegation · ended",
            "✓ Done     Write notes",
            "✗ Failed   Port the auth module (timeout)",
            "▪ Stopped  Refresh docs (no news)",
            "first failure: Port the auth module (timeout)",
            "3 tasks · 1 done · 1 failed · 1 stopped",
        ])
        running = lanes + [lane("d", "Rotate keys", "deep", "m", "", "running", order=4)]
        lines = end_block(graph([root(), *running], []), running).splitlines()
        self.assertEqual(lines[1], "DAG · delegation · so far")
        self.assertIn("● Working  Rotate keys", lines)
        self.assertEqual(lines[-2], "4 tasks · 1 done · 1 failed · 1 stopped · 1 working")

    def test_a_team_part_sent_with_no_child_seen_never_reads_dispatched_under_stopped(self) -> None:
        done = {**lane("a", "Export API", "quick", "m", "", "done", order=1, status="accepted"), "team_unit": "a"}
        for status in ("dispatched", "repair dispatched"):
            sent = {**lane("b", "Export button", "quick", "m", "", "dispatched", order=2, status=status),
                    "team_unit": "b"}
            with self.subTest(status=status):
                self.assertIn("▪ Stopped  Export button (sent, no child seen)",
                              end_block(graph([root(), done, sent], []), [done, sent]).splitlines())
        child = {**lane("c", "Export docs", "quick", "m", "", "dispatched", order=3), "session_tail": "7c41e9a2"}
        handed = lane("d", "Export CLI flag", "quick", "m", "", "dispatched", order=4, status="handed out")
        lines = end_block(graph([root(), done, child, handed], []), [done, child, handed]).splitlines()
        self.assertIn("● Working  Export docs", lines, "a child Hermes recorded is in flight, its word unchanged")
        self.assertIn("▪ Stopped  Export CLI flag (handed out)", lines, "only a team part's sent words are reworded")

    def test_a_lane_with_no_title_is_untitled_in_every_post(self) -> None:
        nameless = [lane("a", "", "quick", "m", "", "failed", order=1, status="timeout")]
        layered = {**graph([root(), *nameless], []), "layers": [["a"]]}
        self.assertIn("1  untitled", start_block(layered, nameless).splitlines())
        self.assertIn("✗ Failed   untitled (timeout)", end_block(layered, nameless).splitlines())
        self.assertEqual(count_line("delegation", tally(nameless)),
                         "DAG delegation · 0/1 done · 0 running · 1 failed (untitled)")
        self.assertEqual(added_clause([("repair", ""), ("extend", "CSV export")]),
                         "+2 added (repair: untitled, extend: CSV export)")


class HudTests(unittest.TestCase):
    def test_the_hud_carries_the_reading_sessions_graph(self) -> None:
        with homes() as home:
            three_lanes(home)
            payload = read_omh_hud(home.omh, home.hermes, session_ref=PARENT, status={"runs": []})
            graph = payload["delegation_graph"]
            self.assertEqual((graph["status"], graph["scope"], graph["source"]), ("active", "session", "native"))
            self.assertEqual(len(graph["nodes"]), 4)
            other = read_omh_hud(home.omh, home.hermes, session_ref="someone-else", status={"runs": []})
            self.assertEqual(other["delegation_graph"]["status"], "inactive")
            unscoped = read_omh_hud(home.omh, home.hermes, status={"runs": []})
            self.assertEqual(unscoped["delegation_graph"]["scope"], "global")
            self.assertEqual(len(unscoped["delegation_graph"]["nodes"]), 4)
            for value in ("off", "OFF", " Off "):
                with self.subTest(value=value), patch.dict(os.environ, {"OMH_SUBAGENT_GRAPH": value}):
                    off = read_omh_hud(home.omh, home.hermes, session_ref=PARENT, status={"runs": []})
                    self.assertEqual((off["delegation_graph"]["status"], off["delegation_graph"]["reason"]),
                                     ("inactive", "explicit_off"))
            with patch.dict(os.environ, {"OMH_SUBAGENT_GRAPH": "ON"}):
                self.assertEqual(_hud_graph_preference("auto"), "on")

    def test_the_hud_says_what_the_chat_last_carried_and_why_one_was_skipped(self) -> None:
        with homes() as home:
            three_lanes(home)

            def chat() -> dict:
                return read_omh_hud(home.omh, home.hermes, session_ref=PARENT, status={"runs": []})[
                    "delegation_graph"]["chat"]

            self.assertEqual((chat()["moment"], chat()["skipped"]), ("", ""))
            self.assertIsNone(reply("x" * 1_790))
            self.assertEqual(chat()["skipped"], "graph_skipped_cap")
            reply("Short.", turn="turn-4")
            self.assertEqual((chat()["moment"], chat()["phase"], chat()["skipped"]), ("start", "open", ""))

    def test_rows_hidden_past_the_eight_row_cap_that_the_graph_draws_are_not_other_agents(self) -> None:
        from omh.tui.panels import panel_lines
        from test_tui_panels import THEME

        def header(payload: dict) -> str:
            lines = ["".join(span.text for span in line) for line in panel_lines(payload, 120, 60, THEME, 0.0)]
            return next(line for line in lines if line.startswith("● main │"))

        for in_ledger, expected_hidden_in_graph, expected in ((20, 12, "lanes in the graph below"),
                                                              (12, 4, "8 other agents")):
            with self.subTest(in_ledger=in_ledger), homes() as home:
                now = time.time()
                home.session(PARENT, "claude-opus-5-5", effort="high")
                # The oldest children have no ledger row (they started before the hooks were registered).
                for index in range(20):
                    child = f"20261020_170501_k{index:02d}"
                    if index >= 20 - in_ledger:
                        start(home, child, f"sa-{index}", f"Lane {index}", now=now - 40 + index)
                    home.session(child, "claude-opus-5-5", parent=PARENT, started=now - 40 + index)
                    home.execute("UPDATE sessions SET parent_session_id = ? WHERE id = ?", (PARENT, child))
                    home.usage(child, "claude-opus-5-5", last_seen=now - 1)
                payload = read_omh_hud(home.omh, home.hermes, session_ref=PARENT, status={"runs": []})
                agents = payload["subagents"]
                self.assertEqual((len(agents["rows"]), agents["hidden_rows"]), (8, 12))
                self.assertEqual(agents["hidden_in_graph"], expected_hidden_in_graph)
                self.assertTrue(header(payload).replace("[global] ", "").startswith(f"● main │ {expected}"),
                                header(payload))

    def test_the_model_facing_and_hot_path_readers_leave_the_graph_unread(self) -> None:
        from omh.plugin_bundle.omh.tools import probe_tool
        from omh.plugin_bundle.omh.tools.hud_tool import omh_hud_handler

        with homes() as home:
            three_lanes(home)
            with patch("omh.plugin_bundle.omh.runtime_reader.build_delegation_graph") as build:
                tool = json.loads(omh_hud_handler({"omh_home": str(home.omh), "hermes_home": str(home.hermes)},
                                                  session_id=PARENT))
                lean = read_omh_hud(home.omh, home.hermes, session_ref=PARENT, status={"runs": []},
                                    delegation_graph=False)
                probe_tool._standalone_probe(omh_home=str(home.omh), hermes_home=str(home.hermes),
                                             include_parity=False, include_roadmap=False)
            build.assert_not_called()
            for payload in (tool, lean):
                self.assertEqual((payload["delegation_graph"]["status"], payload["delegation_graph"]["reason"]),
                                 ("inactive", "not_requested"))

    def test_omh_hud_graph_prints_the_view(self) -> None:
        with homes() as home:
            three_lanes(home)
            status, stdout, stderr = run_cli(
                ["--omh-home", str(home.omh), "--hermes-home", str(home.hermes), "hud", "--graph"],
                output_json=False,
            )
            self.assertEqual((status, stderr), (0, ""))
            # The harness's stdout names no encoding, so the drawing is the ASCII one.
            lines = stdout.splitlines()
            self.assertIn("OMH / DAG - 3 tasks", lines, stdout)
            self.assertIn("[global] the newest conversation with delegated work", lines, "no session named")
            self.assertTrue(any("Polish HUD CLI" in line for line in lines))
            self.assertTrue(stdout.isascii())
            with patch("omh.commands.hud._encodes", return_value=True):
                _status, stdout, _stderr = run_cli(
                    ["--omh-home", str(home.omh), "--hermes-home", str(home.hermes), "hud", "--graph"],
                    output_json=False,
                )
            self.assertIn("OMH / DAG · 3 tasks", stdout)
            self.assertIn("┌", stdout)
            status, stdout, _stderr = run_cli(
                ["--omh-home", str(home.omh), "--hermes-home", str(home.hermes), "hud", "--graph", "--json"],
                output_json=False,
            )
            self.assertEqual(json.loads(stdout)["delegation_graph"]["status"], "active")


if __name__ == "__main__":
    unittest.main()
