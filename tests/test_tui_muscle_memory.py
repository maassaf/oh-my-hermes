"""Claude Code's command names, where Hermes has the command under another name or not at all.

`/cost` runs Hermes' `/usage` and `/permissions` its `/approvals`, under the
echo of the name typed. `/todos` prints this session's whole plan. Bare
`/model` opens a picker of the models Hermes offers (`model.options`) and
switches this session to the one picked with Hermes' own `/model <name>
--provider <slug> --session`; `/model <name>` goes to Hermes as typed.
`/export` first says what Hermes exports (a profile archive, not this
transcript) and runs on the second try. Answers are the recorded Hermes
6c61c3e27d frames in `tests/fixtures/tui_gateway/parity.jsonl`.
"""

from __future__ import annotations

import unittest

from _tui_lines import lines_text
from test_tui_gateway_contract import recorded_rows
from test_tui_modes import SID, answer, drawn, make_app, notices, type_text
from test_tui_panels import hud_payload
from omh.tui import gateway_contract as contract
from omh.tui import slash
from omh.tui.app import PLACEHOLDER, App
from omh.tui.keys import CHAR, ENTER, ESCAPE, Key
from omh.tui.model_picker import CLOSE, PICK, ModelPicker, model_rows
from omh.tui.panels import HudSnapshot, plan_text
from omh.tui.theme import DEFAULT_THEME
from omh.tui.transcript import NOTICE_INFO, NOTICE_WARN, CommandBlock


def recorded(segment: str) -> tuple[dict, dict]:
    """The recorded call's params and its result."""
    rows = recorded_rows("parity", segment)
    call = next(row["frame"] for row in rows if row["dir"] == "out")
    reply = next(row["frame"] for row in rows if row["dir"] == "in")
    return call["params"], reply["result"]


OPTIONS_PARAMS, OPTIONS = recorded("model_options")


def send(app: App, text: str) -> None:
    type_text(app, text)
    app._on_input(Key(ENTER))


def commands(app: App) -> list[str]:
    return [block.command for block in app.transcript.blocks if isinstance(block, CommandBlock)]


class RenamedCommandTests(unittest.TestCase):
    def test_claude_codes_names_run_hermes_commands_under_the_name_typed(self) -> None:
        for typed, sent in (("/cost", "usage"), ("/permissions smart", "approvals smart")):
            with self.subTest(typed=typed):
                app, client = make_app()
                send(app, typed)
                self.assertEqual(client.calls, [("slash.exec", {"command": sent, "session_id": SID})])
                self.assertEqual(commands(app), [typed])
                answer(app, client, {"output": "Session cost: $0.01"})
                self.assertEqual(app.transcript.blocks[-1].parts, [("Session cost: $0.01", NOTICE_INFO)])

    def test_the_menu_offers_them_and_says_which_hermes_command_runs(self) -> None:
        offered = {completion.name: completion.summary for completion in slash.completions("")}
        self.assertIn("/usage", offered["cost"])
        self.assertIn("/approvals", offered["permissions"])
        for name, target in (("cost", "usage"), ("permissions", "approvals")):
            command = slash.parse_slash(f"/{name}").command
            self.assertEqual((command.route, command.target), (slash.RENAMED, target))


class TodosTests(unittest.TestCase):
    ITEMS = [
        {"text": "Read the router", "state": "done", "phase": "Survey"},
        {"text": "Split chat.py", "state": "active", "phase": "Change"},
        {"text": "Move the helpers", "state": "pending", "phase": "Change", "depth": 1},
        {"text": "Run the suite", "state": "pending", "phase": "Change"},
    ]

    def test_the_whole_plan_prints_under_the_echo(self) -> None:
        app, client = make_app()
        app.hud = HudSnapshot("stored", hud_payload(self.ITEMS, title="Router split"), 0.0)
        send(app, "/todos")
        self.assertEqual(client.calls, [])
        (block,) = [block for block in app.transcript.blocks if isinstance(block, CommandBlock)]
        self.assertEqual(block.command, "/todos")
        self.assertEqual(
            block.parts[0][0].split("\n"),
            [
                "[Plan] Router split │ 1/4",  # the panel's own header
                "Survey",
                "  [✓] Read the router",
                "Change",
                "  [•] Split chat.py",
                "    [ ] Move the helpers",
                "  [ ] Run the suite",
            ],
        )

    def test_no_plan_says_so(self) -> None:
        self.assertEqual(plan_text(None), "")
        self.assertEqual(plan_text(hud_payload()), "")
        app, _client = make_app()
        send(app, "/todos")
        self.assertIn("No plan in this session yet", notices(app)[-1])


class ModelPickerTests(unittest.TestCase):
    def test_the_rows_are_every_providers_models_in_hermes_order(self) -> None:
        listed = {**OPTIONS, "model": "gpt-5.4-mini", "provider": "copilot"}  # a current model a provider lists
        rows = model_rows(listed)
        self.assertEqual(len(rows), sum(len(provider["models"]) for provider in OPTIONS["providers"]))
        self.assertEqual((rows[0].model, rows[0].provider, rows[0].provider_name), ("claude-fable-5.1", "anthropic", "Anthropic"))
        self.assertEqual(rows[2].arg, "claude-opus-5-5 --provider anthropic --session")

    def test_a_name_that_is_not_one_plain_word_is_left_out(self) -> None:
        result = {
            "model": "m",
            "provider": "p",
            "providers": [
                {"slug": "p", "name": "P", "models": ["m", "x --global", "-v", "a\x1b[31mb", "", 3, "ok/model"]},
                {"slug": "--global", "name": "Bad", "models": ["m"]},
                {"slug": "q", "name": "Q\x1b]0;t\x07", "models": "not a list"},
                # Hermes' /model reads a leading U+2012-U+2015 dash before a flag word as `--` (model_switch.py).
                {"slug": "p", "name": "P", "models": [DASH + "global" for DASH in "\u2012\u2013\u2014\u2015\u2010"]},
                {"slug": "\u2014session", "name": "Bad", "models": ["m"]},
                {"slug": "p", "name": "P", "models": ["model\u2014v2"]},  # a dash inside a word stays one word
            ],
        }
        rows = model_rows(result)
        self.assertEqual(
            [(row.model, row.provider, row.current) for row in rows],
            [("m", "p", True), ("ok/model", "p", False), ("model\u2014v2", "p", False)],
        )

    def test_the_current_model_is_marked_and_highlighted_typing_filters_and_esc_closes(self) -> None:
        result = {**OPTIONS, "model": "gpt-5.4-mini", "provider": "copilot"}
        picker = ModelPicker(model_rows(result), current="gpt-5.4-mini")
        rows = lines_text(picker.lines(100, DEFAULT_THEME, 20))
        self.assertIn("now gpt-5.4-mini", rows[0])
        highlighted = [row for row in rows[1:] if row.lstrip().startswith("●")]
        self.assertEqual(len(highlighted), 1)
        self.assertIn("gpt-5.4-mini", highlighted[0])
        self.assertEqual(picker.handle_key(Key(ENTER)).row.model, "gpt-5.4-mini")
        picker = ModelPicker(model_rows(OPTIONS), current=OPTIONS["model"])
        for char in "opus-5-5":
            picker.handle_key(Key(CHAR, char))
        action = picker.handle_key(Key(ENTER))
        self.assertEqual((action.kind, action.row.provider), (PICK, "anthropic"))
        self.assertEqual(ModelPicker(model_rows(OPTIONS), current="").handle_key(Key(ESCAPE)).kind, CLOSE)


class ModelCommandTests(unittest.TestCase):
    def opened(self) -> tuple[App, object]:
        app, client = make_app()
        send(app, "/model")
        self.assertEqual(client.calls, [("model.options", {"session_id": SID})])  # the keys the recording sent
        answer(app, client, OPTIONS)
        self.assertIsNotNone(app.model_picker)
        screen = drawn(app)
        self.assertTrue(any(line.startswith(" Model (") for line in screen), screen)  # in the slot, over the composer
        self.assertFalse(any(PLACEHOLDER in line for line in screen), screen)  # the picker has the keys, not the composer
        return app, client

    def test_bare_model_lists_hermes_models_and_the_pick_switches_this_session(self) -> None:
        app, client = self.opened()
        for char in "opus-5-5":
            app._on_input(Key(CHAR, char))
        app._on_input(Key(ENTER))
        self.assertIsNone(app.model_picker)
        self.assertEqual(
            client.calls[-1],
            ("slash.exec", {"command": "model claude-opus-5-5 --provider anthropic --session", "session_id": SID}),
        )
        self.assertEqual(commands(app), ["/model claude-opus-5-5 --provider anthropic --session"])  # one echo, the pick's

    def test_esc_closes_the_picker_and_switches_nothing(self) -> None:
        app, client = self.opened()
        calls = len(client.calls)
        app._on_input(Key(ESCAPE))
        self.assertIsNone(app.model_picker)
        self.assertEqual(len(client.calls), calls)
        self.assertEqual(app.composer.text, "")
        self.assertEqual(commands(app), [])  # no `› /model` left standing empty

    def test_model_with_a_name_goes_to_hermes_as_typed(self) -> None:
        app, client = make_app()
        send(app, "/model gpt-5.4 --provider copilot")
        self.assertEqual(client.calls, [("slash.exec", {"command": "model gpt-5.4 --provider copilot", "session_id": SID})])

    def test_bare_model_waits_for_the_turn_to_end(self) -> None:
        app, client = make_app(busy=True)
        send(app, "/model")
        self.assertEqual(client.calls, [])
        self.assertIn("between turns", notices(app, NOTICE_WARN)[-1])

    def test_a_hermes_without_model_options_costs_the_picker_not_the_session(self) -> None:
        app, client = make_app()
        send(app, "/model")
        answer(app, client, error={"code": -32601, "message": "Method not found"})
        self.assertIsNone(app.exit_code)
        self.assertIsNone(app.model_picker)

    def test_the_contract_declares_model_options_with_the_session_only(self) -> None:
        self.assertEqual(contract.CLIENT_METHODS["model.options"], {"session_id"})
        self.assertEqual(set(OPTIONS_PARAMS), {"session_id"})
        self.assertEqual(contract.call_problem("model.options", OPTIONS_PARAMS), "")
        self.assertIn("model.options", contract.OPTIONAL_METHODS)


class ExportTests(unittest.TestCase):
    def test_the_first_export_says_what_hermes_exports_and_the_second_runs_it(self) -> None:
        app, client = make_app()
        send(app, "/export")
        self.assertEqual(client.calls, [])
        self.assertIn("profile archive", notices(app)[-1])
        self.assertIn("/export again", notices(app)[-1])
        send(app, "/export -o profile.tar.gz")
        self.assertEqual(client.calls, [("slash.exec", {"command": "export -o profile.tar.gz", "session_id": SID})])
        send(app, "/export")
        self.assertEqual(client.calls[-1], ("slash.exec", {"command": "export", "session_id": SID}))


if __name__ == "__main__":
    unittest.main()
