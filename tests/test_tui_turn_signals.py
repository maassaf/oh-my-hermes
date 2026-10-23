"""Turn signals: the working row's tokens, the done line that closes a turn, the end-of-turn alert, the title.

The working row reads `✻ Verb… (12s · ↓1.2k tokens · esc to interrupt)`,
where the tokens are what the turn has written so far (the `output` counter
of Hermes' `session.usage`, from where it stood when the turn began). A turn
closes with a done line, `✻ 34s · 3 tools · ↓1.2k`. A turn that ran 10 s or
more ends with the terminal's attention signal (OSC 9 where the terminal
shows it as a notification, OSC 777 where that is its form, the bell
elsewhere; `OMH_TUI_BELL=0` keeps it quiet). The terminal title says the
session's title and what it is doing (OSC 2), set only when it changes and
put back as it was on the way out.
"""

from __future__ import annotations

import unittest

from _tui_lines import line_text
from test_tui_gateway_contract import recorded_events, recorded_rows
from test_tui_modes import SID, drawn, make_app
from omh.tui import gateway_contract as contract
from omh.tui.app import BELL, TITLE_POP, TITLE_PUSH, TURN_ALERT_SECONDS, App
from omh.tui.gateway import Event
from omh.tui.transcript import TurnSummaryBlock


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def usage(output: int) -> dict:
    return {"usage": {"output": output, "context_used": 1000, "context_max": 100_000}}


def writes(app: App) -> str:
    return "".join(app.terminal.writes)  # type: ignore[attr-defined]


def turn(app: App, clock: Clock, seconds: float, tools: int = 0, out: int | None = None) -> None:
    """A prompt sent, `tools` tool calls, `seconds` on the clock, and the turn's message.complete."""
    app._send_prompt("go")
    app._on_event(Event("message.start", SID, {}))
    for number in range(tools):
        app._on_event(Event("tool.start", SID, {"tool_id": f"t{number}", "name": "terminal", "context": "ls"}))
        app._on_event(Event("tool.complete", SID, {"tool_id": f"t{number}", "name": "terminal", "result": {"output": ""}}))
    if out is not None:
        app._on_event(Event("session.usage", SID, usage(out)))
    clock.now += seconds
    app._on_event(Event("message.complete", SID, {"text": "done", "status": "complete"}))


class WorkingRowTests(unittest.TestCase):
    def test_the_working_row_counts_what_the_turn_has_written(self) -> None:
        clock = Clock()
        app, _client = make_app(clock=clock)
        app._on_event(Event("session.info", SID, usage(500)))
        app._send_prompt("go")
        app._on_event(Event("message.start", SID, {}))
        clock.now += 12
        row = line_text(app._tail(80)[-2])  # the working row, its tip under it
        self.assertIn("(12s · esc to interrupt)", row)  # nothing written yet
        app._on_event(Event("session.usage", SID, usage(1700)))
        self.assertIn("(12s · ↓1.2k tokens · esc to interrupt)", line_text(app._tail(80)[-2]))


    def test_the_first_turn_of_a_fresh_session_counts_its_tokens_in_hermes_recorded_order(self) -> None:
        # Hermes 6c61c3e27d (parity.jsonl todo_turn): the session's first usage block, `session.info` with output 0,
        # comes after prompt.submit, so there is no counter yet when the turn begins.
        app, _client = make_app(clock=Clock())
        app._send_prompt("record a plan")
        rows = [row["frame"]["params"] for row in recorded_rows("parity", "todo_turn")
                if row["dir"] == "in" and row["frame"].get("method") == "event"]
        working = []
        for params in rows:
            app._on_event(Event(params["type"], SID, params.get("payload", {})))
            if params["type"] == "session.usage":
                working.append(line_text(app._tail(80)[-2]))
        self.assertIn("↓63 tokens", working[0])
        summary = [block for block in app.transcript.blocks if isinstance(block, TurnSummaryBlock)]
        self.assertEqual([block.output_tokens for block in summary], [238])

    def test_a_new_session_counts_from_its_own_counter_not_the_last_sessions(self) -> None:
        app, _client = make_app(clock=Clock())
        app._on_event(Event("session.info", SID, usage(5000)))  # the session being left had written 5000
        app._adopt_session({"session_id": "live-2", "stored_session_id": "stored-2", "info": {}})
        app._send_prompt("go")
        for out in (0, 63):
            app._on_event(Event("session.usage", "live-2", usage(out)))
        self.assertIn("↓63 tokens", line_text(app._tail(80)[-2]))


class DoneLineTests(unittest.TestCase):
    def test_a_turn_closes_with_how_long_it_took_its_tools_and_its_tokens(self) -> None:
        clock = Clock()
        app, _client = make_app(clock=clock)
        app._on_event(Event("session.info", SID, usage(100)))
        turn(app, clock, 34, tools=3, out=1300)
        block = app.transcript.blocks[-1]
        self.assertIsInstance(block, TurnSummaryBlock)
        drawn(app)
        screen = app.terminal.screen.history() + app.terminal.screen.lines()  # type: ignore[attr-defined]
        self.assertIn("  ✻ 34s · 3 tools · ↓1.2k", screen)

    def test_one_tool_and_no_tokens_read_as_such(self) -> None:
        clock = Clock()
        app, _client = make_app(clock=clock)
        turn(app, clock, 2, tools=1)
        block = app.transcript.blocks[-1]
        self.assertEqual((block.seconds, block.tools, block.output_tokens), (2.0, 1, None))
        from omh.tui.transcript import block_rows
        from omh.tui.theme import DEFAULT_THEME

        self.assertEqual(line_text(block_rows(block, 60, DEFAULT_THEME)[0]), "  ✻ 2s · 1 tool")


class AlertTests(unittest.TestCase):
    def ends(self, seconds: float, environ: dict[str, str]) -> str:
        clock = Clock()
        app, _client = make_app(clock=clock, environ=environ)
        before = len(writes(app))
        turn(app, clock, seconds)
        return writes(app)[before:]

    def test_a_long_turn_ends_with_the_signal_the_terminal_shows(self) -> None:
        self.assertIn(BELL, self.ends(TURN_ALERT_SECONDS, {"TERM_PROGRAM": "Apple_Terminal"}))
        self.assertIn("\x1b]9;", self.ends(TURN_ALERT_SECONDS, {"TERM_PROGRAM": "iTerm.app"}))
        self.assertIn("\x1b]777;notify;", self.ends(TURN_ALERT_SECONDS, {"TERM": "foot"}))

    def test_a_short_turn_and_a_quiet_setting_signal_nothing(self) -> None:
        self.assertNotIn(BELL, self.ends(TURN_ALERT_SECONDS - 0.5, {"TERM_PROGRAM": "Apple_Terminal"}))
        self.assertNotIn("\x1b]9;", self.ends(TURN_ALERT_SECONDS - 0.5, {"TERM_PROGRAM": "iTerm.app"}))
        quiet = self.ends(60, {"TERM_PROGRAM": "iTerm.app", "OMH_TUI_BELL": "0"})
        self.assertNotIn("\x1b]9;", quiet)
        self.assertNotIn(BELL, quiet)


class TitleTests(unittest.TestCase):
    def test_the_title_names_the_session_and_its_state_once_per_change(self) -> None:
        app, _client = make_app()
        (title_event,) = [payload for kind, payload in recorded_events("parity") if kind == "session.title"][-1:]
        app._on_event(Event("session.title", SID, title_event))
        drawn(app)
        drawn(app)
        written = writes(app)
        self.assertEqual(written.count(TITLE_PUSH), 1)
        self.assertEqual(written.count("\x1b]2;Create todo plan for ship change · idle\x1b\\"), 1)
        app.turn_pending = True
        drawn(app)
        self.assertIn("\x1b]2;Create todo plan for ship change · running\x1b\\", writes(app))
        app._shutdown()
        self.assertTrue(writes(app).endswith(TITLE_POP) or TITLE_POP in writes(app))

    def test_a_title_holds_no_control_characters_and_a_session_without_one_says_hermes(self) -> None:
        app, _client = make_app()
        drawn(app)
        self.assertIn("\x1b]2;Hermes · idle\x1b\\", writes(app))
        app._on_event(Event("session.title", SID, {"session_id": "stored", "title": "a\x07b\x1b]2;evil\x1b\\c"}))
        drawn(app)
        self.assertIn("\x1b]2;abc · idle\x1b\\", writes(app))  # the escape sequence goes whole, as in any gateway text

    def test_the_contract_declares_the_title_event(self) -> None:
        self.assertIn("session.title", contract.APP_EVENTS)
        self.assertEqual(contract.SESSION_TITLE, "title")


class PlanRefreshTests(unittest.TestCase):
    def test_hermes_todo_snapshot_has_the_hud_read_now_instead_of_at_its_next_poll(self) -> None:
        app, _client = make_app()
        (payload,) = [payload for kind, payload in recorded_events("parity") if kind == "todo.updated"]
        app._on_event(Event("todo.updated", SID, payload))
        self.assertEqual(app.poller.refreshes, 1)  # type: ignore[attr-defined]
        app._on_event(Event("todo.updated", "another-session", payload))
        self.assertEqual(app.poller.refreshes, 1)  # type: ignore[attr-defined]
        self.assertIn("todo.updated", contract.APP_EVENTS)


class DocumentedSignalTests(unittest.TestCase):
    def test_installation_names_the_threshold_the_code_uses(self) -> None:
        from pathlib import Path

        prose = " ".join((Path(__file__).resolve().parents[1] / "docs/INSTALLATION.md").read_text(encoding="utf-8").split())
        self.assertIn(f"a turn that ran {TURN_ALERT_SECONDS:g} s or more ends with the terminal's own notification", prose)


if __name__ == "__main__":
    unittest.main()
