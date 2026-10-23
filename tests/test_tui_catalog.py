"""Hermes' command catalog in the OMH TUI: `/reload-skills`, the catalog's warning and refusal, `/help`, the menu.

Every case drives an `App` over the recording client from `test_tui_modes`.
The catalogs are built in the shape Hermes 6c61c3e27d's `commands.catalog`
returns (`tui_gateway/methods_tools.py`): every command and skill is a pair,
categories hold the commands and no skill, and `skill_count` counts the
skills. The recorded catalog (`tests/fixtures/tui_gateway/session.jsonl`)
holds no skill, so these name some.
"""

from __future__ import annotations

import unittest

from _tui_lines import line_text
from test_tui_modes import SID, RecordingClient, answer, make_app, notices, type_text
from omh.tui import gateway_contract as contract
from omh.tui.app import (
    CATALOG_REFUSED_NOTICE,
    COLLISION_NOTES_HEADING,
    MENU_NAME_CELLS,
    RELOAD_REFUSED_NOTICE,
    App,
    _menu_lines,
    collision_notes,
)
from omh.tui.keys import ENTER, Key
from omh.tui.theme import DEFAULT_THEME
from omh.tui.transcript import NOTICE_WARN

SKILLS = [["/omh-codebase-uml", "Draw UML from the code"], ["/ulw-review", "Review the change"]]
CATALOG = {
    "pairs": [["/config", "Show current configuration"], ["/tools", "Manage tools"], *SKILLS],
    "categories": [
        {"name": "Configuration", "pairs": [["/config", "Show current configuration"]]},
        {"name": "Tools & Skills", "pairs": [["/tools", "Manage tools"]]},
    ],
    "skill_count": 2,
    "warning": "",
}
ADDED = ["/omh-spend-gate", "Show what a spend will cost"]
# Hermes 6c61c3e27d's `skill_command_collision_note` (`agent/skill_commands.py`), as its own test spells two of them;
# `commands.catalog` joins them with "; " when no discovery failed. Its bundled `plan` skill sends the first at
# every launch.
PLAN_NOTE = "slash command /plan unavailable — name taken by built-in; use /skill plan"
HANDOFF_NOTE = "slash command /handoff unavailable — name taken by built-in; use /skill handoff"
RELOADED = {**CATALOG, "pairs": [*CATALOG["pairs"], ADDED], "skill_count": 3}
RESCAN_OUTPUT = "Reloading skills...\nAdded skills:\n  - omh-spend-gate\n3 skill(s) available"


def catalog_app(catalog: dict = CATALOG, **options: object) -> tuple[App, RecordingClient]:
    app, client = make_app(**options)
    app._take_catalog(catalog)
    return app, client


def menu_names(app: App, typed: str) -> list[str]:
    app._set_composer("")
    type_text(app, typed)
    return [label for label, _summary in app._menu_rows()]


class ReloadSkillsTests(unittest.TestCase):
    def test_it_rescans_in_the_gateway_then_reads_the_catalog_and_the_new_skill_completes(self) -> None:
        app, client = catalog_app()
        self.assertNotIn("/omh-spend-gate", menu_names(app, "/spend"))
        app._set_composer("")
        type_text(app, "/reload-skills")
        app._on_input(Key(ENTER))
        self.assertEqual(client.calls, [("skills.reload", {"session_id": SID})])
        answer(app, client, {"output": RESCAN_OUTPUT, "result": {"total": 3}})
        self.assertIn(RESCAN_OUTPUT, notices(app))
        self.assertEqual(client.calls[-1], ("commands.catalog", {"session_id": SID}))
        answer(app, client, RELOADED)
        self.assertEqual(menu_names(app, "/spend"), ["/omh-spend-gate"])
        self.assertEqual(app.skill_count, 3)

    def test_a_hermes_that_rejects_the_session_id_leaves_the_session_running_with_a_notice(self) -> None:
        # Hermes 0250c8bcae's SkillsReloadParams takes no field, so it answers 4000; an unknown method is -32601.
        for code in (contract.PARAM_REJECTED, contract.METHOD_NOT_FOUND):
            with self.subTest(code=code):
                app, client = catalog_app()
                type_text(app, "/reload-skills")
                app._on_input(Key(ENTER))
                answer(app, client, error={"code": code, "message": "extra fields not permitted"})
                self.assertIsNone(app.exit_code)
                self.assertEqual(
                    notices(app, NOTICE_WARN),
                    [RELOAD_REFUSED_NOTICE.format(code=code, message="extra fields not permitted")],
                )
                self.assertEqual(client.calls, [("skills.reload", {"session_id": SID})])  # no catalog read after it

    def test_the_alias_runs_it_too_and_without_a_session_nothing_is_sent(self) -> None:
        app, client = catalog_app()
        type_text(app, "/reload_skills")
        app._on_input(Key(ENTER))
        self.assertEqual(client.calls, [("skills.reload", {"session_id": SID})])
        idle, idle_client = catalog_app()
        idle.sid = ""
        type_text(idle, "/reload-skills")
        idle._on_input(Key(ENTER))
        self.assertEqual(idle_client.calls, [])


class CatalogAnswerTests(unittest.TestCase):
    def test_a_warning_is_one_warning_notice_and_a_repeat_says_nothing(self) -> None:
        warned = {**CATALOG, "warning": "skill discovery unavailable: boom"}
        app, _client = catalog_app(warned)
        app._take_catalog(warned)
        self.assertEqual(notices(app, NOTICE_WARN), ["Hermes' command catalog: skill discovery unavailable: boom"])
        app._take_catalog({**CATALOG, "warning": "/review: a built-in command; run the skill as /skill review"})
        self.assertEqual(len(notices(app, NOTICE_WARN)), 2)

    def test_hermes_built_in_name_notes_are_no_notice_and_help_lists_them_in_hermes_words(self) -> None:
        for warning, notes in ((PLAN_NOTE, [PLAN_NOTE]), (f"{PLAN_NOTE}; {HANDOFF_NOTE}", [PLAN_NOTE, HANDOFF_NOTE])):
            with self.subTest(warning=warning):
                app, _client = catalog_app({**CATALOG, "warning": warning})
                self.assertEqual(notices(app), [])
                app._help("")
                (output,) = notices(app)
                rows = output.split("\n")
                at = rows.index(COLLISION_NOTES_HEADING)
                self.assertEqual(rows[at + 1 : at + 1 + len(notes)], [f"  {note}" for note in notes])
                self.assertEqual(rows[at - 1], "2 skill commands available: type / and part of a skill's name to find one.")
        app, _client = catalog_app()
        app._help("")
        self.assertNotIn(COLLISION_NOTES_HEADING, notices(app)[0])  # none to list: no heading

    def test_a_discovery_failure_stays_a_notice_and_only_hermes_exact_note_is_taken_for_one(self) -> None:
        failures = (
            "skill discovery unavailable: boom",
            "quick_commands discovery unavailable: bad yaml",
            "plugin command discovery unavailable: ImportError",
            f"{PLAN_NOTE}; quick_commands discovery unavailable: bad yaml",  # anything beside the notes: not only notes
            f"skill discovery unavailable: boom; {PLAN_NOTE}",
            "slash command /plan is taken by a built-in",  # near the phrase, not Hermes' sentence
            "/review: a built-in command; run the skill as /skill review",
        )
        for warning in failures:
            with self.subTest(warning=warning):
                self.assertEqual(collision_notes(warning), ())
                app, _client = catalog_app({**CATALOG, "warning": warning})
                self.assertEqual(notices(app, NOTICE_WARN), [f"Hermes' command catalog: {warning}"])

    def test_a_failure_after_the_notes_is_still_said_and_the_notes_after_a_failure_say_nothing(self) -> None:
        app, _client = catalog_app({**CATALOG, "warning": PLAN_NOTE})
        app._take_catalog({**CATALOG, "warning": "skill discovery unavailable: boom"})
        app._take_catalog({**CATALOG, "warning": PLAN_NOTE})
        self.assertEqual(notices(app, NOTICE_WARN), ["Hermes' command catalog: skill discovery unavailable: boom"])
        self.assertEqual(app.catalog_collisions, (PLAN_NOTE,))

    def test_a_refusal_keeps_the_known_list_and_says_so(self) -> None:
        app, client = catalog_app()
        app._refresh_catalog()
        answer(app, client, error={"code": 5020, "message": "catalog failed"})
        self.assertEqual(notices(app, NOTICE_WARN), [CATALOG_REFUSED_NOTICE.format(code=5020, message="catalog failed")])
        self.assertEqual(menu_names(app, "/uml"), ["/omh-codebase-uml"])

    def test_a_drift_answer_is_a_notice_and_the_session_goes_on(self) -> None:
        for code in (contract.PARAM_REJECTED, contract.METHOD_NOT_FOUND):
            with self.subTest(code=code):
                app, client = catalog_app()
                app._refresh_catalog()
                answer(app, client, error={"code": code, "message": "extra fields not permitted"})
                self.assertIsNone(app.exit_code)
                self.assertEqual(
                    notices(app, NOTICE_WARN),
                    [CATALOG_REFUSED_NOTICE.format(code=code, message="extra fields not permitted")],
                )

    def test_drift_on_a_call_the_session_needs_still_ends_it(self) -> None:
        app, client = catalog_app()
        type_text(app, "/xyz")
        app._on_input(Key(ENTER))
        answer(app, client, error={"code": contract.PARAM_REJECTED, "message": "extra fields not permitted"})
        self.assertEqual(app.exit_code, 1)

    def test_an_answer_for_a_session_since_left_is_dropped(self) -> None:
        for frame in ({"result": RELOADED}, {"error": {"code": 5020, "message": "catalog failed"}}):
            with self.subTest(frame=frame):
                app, client = catalog_app()
                app._refresh_catalog()
                app.sid = "live-2"
                answer(app, client, frame.get("result"), frame.get("error"))
                self.assertEqual((app.skill_count, notices(app)), (2, []))


class HelpTests(unittest.TestCase):
    def test_it_lists_hermes_commands_by_category_and_counts_the_skills_without_naming_them(self) -> None:
        app, _client = catalog_app(rows=60)
        app._help("")
        (text,) = notices(app)
        self.assertIn("  Configuration: /config", text)
        self.assertIn("  Tools & Skills: /tools", text)
        self.assertIn("2 skill commands available: type / and part of a skill's name to find one.", text)
        self.assertNotIn("omh-codebase-uml", text)

    def test_long_output_stays_whole_in_the_transcript(self) -> None:
        app, _client = catalog_app(rows=24)
        app._help("")
        self.assertIsNone(app.pager)  # a pager opens only when asked for
        (text,) = notices(app)
        self.assertIn("skill commands available", text)
        self.assertTrue(app.transcript.blocks[-1].whole)


class MenuColumnTests(unittest.TestCase):
    def summary_columns(self, entries: list[tuple[str, str]], width: int = 80) -> list[int]:
        rows = _menu_lines(entries, 0, width, len(entries), DEFAULT_THEME)
        return [line_text(row).index(summary) for row, (_label, summary) in zip(rows, entries)]

    def test_a_skill_name_longer_than_the_default_column_widens_it_for_every_row(self) -> None:
        entries = [("/help", "Show commands"), ("/omh-codebase-uml", "Draw UML from the code"), ("/new", "Start")]
        columns = self.summary_columns(entries)
        self.assertEqual(len(set(columns)), 1)
        self.assertGreater(columns[0], MENU_NAME_CELLS)

    def test_short_names_keep_the_default_column(self) -> None:
        self.assertEqual(set(self.summary_columns([("/help", "Show commands"), ("/new", "Start")])), {MENU_NAME_CELLS + 1})

    def test_a_name_past_half_the_width_pushes_only_its_own_summary(self) -> None:
        long_label = "/" + "x" * 50
        columns = self.summary_columns([("/help", "Show commands"), (long_label, "Long"), ("/new", "Start")])
        self.assertEqual(columns[0], columns[2])
        self.assertGreater(columns[1], columns[0])


if __name__ == "__main__":
    unittest.main()
