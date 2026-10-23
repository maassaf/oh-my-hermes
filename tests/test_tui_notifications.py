"""Hermes' out-of-band notices (`notification.show` / `notification.clear`) and plain-text call failures.

A notification is one live row above the composer (and above the queued
prompts), in its level's colour with a glyph, replaced in place by the next
one (latest wins, as Hermes' own TUI shows them), withdrawn by a clear that
names its key, gone after `ttl_ms` when its kind is ttl, and dropped with
the session. Frames are the derived ones in
`tests/fixtures/tui_gateway/notifications.jsonl`.
"""

from __future__ import annotations

import unittest

from test_tui_gateway_contract import recorded_events
from test_tui_modes import SID, answer, drawn, make_app, notices, type_text
from omh.tui.app import App
from omh.tui.gateway import Event, Response
from omh.tui.keys import ENTER, Key
from omh.tui.transcript import NOTICE_ERROR, CommandBlock

FRAMES = recorded_events("notifications")


def frame(index: int) -> Event:
    kind, payload = FRAMES[index]
    return Event(kind, SID, payload)


def notice_rows(app: App) -> list[str]:
    """The screen rows that hold the notification, by its text."""
    return [line for line in drawn(app) if "agent" in line or "Credit" in line or "Provider" in line]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class NotificationTests(unittest.TestCase):
    def test_a_notification_is_one_row_above_the_composer_and_never_a_transcript_block(self) -> None:
        app, _client = make_app()
        app._on_event(frame(0))
        rows = notice_rows(app)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].startswith("ℹ Still starting the agent"), rows)  # info gets a glyph of its own
        self.assertEqual(app.transcript.blocks, [])

    def test_a_clear_with_its_key_withdraws_it_and_one_with_another_key_does_not(self) -> None:
        app, _client = make_app()
        app._on_event(frame(0))
        app._on_event(Event("notification.clear", SID, {"key": "credits.depleted"}))
        self.assertEqual(len(notice_rows(app)), 1)
        app._on_event(frame(1))
        self.assertEqual(notice_rows(app), [])

    def test_the_next_notification_replaces_the_one_shown_and_a_glyph_in_the_text_is_kept(self) -> None:
        app, _client = make_app()
        app._on_event(frame(0))
        app._on_event(frame(2))
        rows = notice_rows(app)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].startswith("✕ Credit access paused · run /topup to top up"), rows)
        self.assertEqual(app.notification.level, "error")  # type: ignore[union-attr]
        self.assertEqual(app.notification.style(app.theme), app.theme.error)  # type: ignore[union-attr]

    def test_a_ttl_notification_leaves_after_its_time_and_a_sticky_one_stays(self) -> None:
        clock = Clock()
        app, _client = make_app(clock=clock)
        app._on_event(frame(4))  # credits.restored, ttl 8000 ms
        self.assertEqual(len(notice_rows(app)), 1)
        clock.now += 7.9
        self.assertAlmostEqual(app._read_timeout(), 0.1)  # the loop wakes for the expiry, sooner than when idle
        app._expire_notification()
        self.assertEqual(len(notice_rows(app)), 1)
        clock.now += 0.2
        app._expire_notification()
        self.assertEqual(notice_rows(app), [])
        app._on_event(frame(5))  # a sticky warning
        clock.now += 3600
        app._expire_notification()
        self.assertTrue(notice_rows(app)[0].startswith("⚠ Provider openrouter"))

    def test_a_notification_of_another_session_is_dropped_and_leaving_the_session_drops_the_one_shown(self) -> None:
        app, client = make_app()
        kind, payload = FRAMES[0]
        app._on_event(Event(kind, "another-session", payload))
        self.assertIsNone(app.notification)
        app._on_event(frame(2))
        app._new_session("")
        answer(app, client, {"session_id": "live-2", "info": {}})
        self.assertIsNone(app.notification)

    def test_a_question_hermes_asks_takes_the_slot_over_the_notification(self) -> None:
        app, _client = make_app()
        app._on_event(frame(2))
        from omh.tui.gateway import ServerRequest

        app._on_server_request(ServerRequest("srq-1", "approval", {"command": "rm -rf build", "description": "x"}))
        self.assertEqual(notice_rows(app), [])


class PlainFailureTests(unittest.TestCase):
    def test_a_commands_refusal_is_hermes_words_under_its_echo_without_the_method_or_code(self) -> None:
        app, client = make_app()
        type_text(app, "/undo")
        app._on_input(Key(ENTER))
        app._on_response(Response(1, None, {"code": 4018, "message": "not a slash.exec command"}))
        answer(app, client, error={"code": 4018, "message": "no user messages to undo"})
        block = app.transcript.blocks[-1]
        self.assertIsInstance(block, CommandBlock)
        self.assertEqual(block.parts, [("No user messages to undo.", NOTICE_ERROR)])

    def test_a_refused_call_outside_a_command_names_what_was_not_done_in_words(self) -> None:
        app, client = make_app(busy=True)
        app._interrupt()
        answer(app, client, error={"code": 4001, "message": "session not found"})
        self.assertEqual(notices(app, NOTICE_ERROR), ["Hermes did not interrupt the turn: session not found."])
        for said in notices(app):
            self.assertNotIn("session.interrupt", said)
            self.assertNotIn("4001", said)


if __name__ == "__main__":
    unittest.main()
