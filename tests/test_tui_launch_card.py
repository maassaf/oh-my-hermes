"""The launch looks and the launch card.

A fresh session's launch draws the logo and the banner in the launch look in use
(spectrum unless one is saved under the OMH home), and the launch card in the
live region under them: the session, the skills Hermes has (regrouped by OMH's
capability family) and the first keys, fitted to the rows above the composer.
The card grows as `session.info` and `session.list` answer, goes to scrollback
above the first prompt, and comes back with a full redraw of the whole session.
`/theme` alone opens a picker that previews each look on the logo and the card;
Enter keeps the highlighted one (saved beside the theme), Esc puts the one in
use back, a click moves the highlight, and `/theme <look>` keeps one at once.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from _tui_lines import line_text
from test_tui_app import FakePoller, ScriptedTerminal
from test_tui_modes import SID, RecordingClient
from test_tui_theme import contrast
from omh.skin_pack import (
    THEME_STORE_SCHEMA,
    default_theme,
    read_launch_choice,
    read_theme_choice,
    theme_for_name,
    theme_store_path,
    write_launch_choice,
    write_theme_choice,
)
from omh.tui.app import CARD_KEPT, CARD_LIVE, CARD_NONE, CLEAR_SCREEN_AND_SCROLLBACK, LAUNCH_UNKNOWN_NOTICE, App
from omh.tui.gateway import Event, Response
from omh.tui.keys import BUTTON_LEFT, CHAR, DOWN, ENTER, ESCAPE, Key, Mouse
from omh.tui.launch_card import Facts, Paint, family_members, grid, launch_card, skill_rows
from omh.tui.shortcuts import SHORTCUTS
from omh.tui.style import NO_COLOR
from omh.tui.theme import (
    DEFAULT_LAUNCH,
    LAUNCH_PALETTES,
    LAUNCH_PRESETS,
    build_theme,
    launch_palette,
    launch_styles,
)
from omh.tui.width import line_width

MEMBERS = (("plan", ("omh-plan", "dag-ulw", "ulw-plan")), ("ship", ("omh-code-review", "omh-backend")))
SKILLS = {"general": ["omh-plan", "dag-ulw", "ulw-plan", "ulw-work", "omh-code-review", "omh-backend", "omh-lone"],
          "devops": ["docker"], "creative": ["poem", "omh-backend"]}
LAST = {"sessions": [{"id": "20261022_121251_cf45f40d", "title": "Check the proactive alerts", "message_count": 14}]}


def paint(look: str = DEFAULT_LAUNCH, background: str = "#0E151E") -> Paint:
    theme = build_theme(default_theme(), background)
    return Paint(theme, launch_styles(default_theme(), background, launch_palette(look, background)))


def texts(lines) -> list[str]:
    return [line_text(line) for line in lines]


class StartClient(RecordingClient):
    def call(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        self.calls.append((method, params))
        return {"session_id": SID, "stored_session_id": "stored-1", "info": {"model": "openai/gpt-6.1-sol", "cwd": "/p"}}


class Launched:
    """A launched app on a scripted terminal, its OMH home a temporary directory."""

    def __init__(self, rows: int = 42, cols: int = 150, look: str | None = None, env: dict | None = None) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="omh-launch-test-")) / ".omh"
        if look is not None:
            write_launch_choice(self.home, look)
        self.client = StartClient()
        self.app = App(ScriptedTerminal(rows, cols), self.client, {"OMH_HOME": str(self.home), **(env or {})},
                       poller=FakePoller())  # type: ignore[arg-type]
        self.app.startup()  # as `_run` starts it: the session, then the background, then the cleared screen
        self.app.adopt_background("#0E151E", {})
        self.app.clear_launch_screen(1)

    def answer(self, method: str, result: dict) -> None:
        number = next(index for index, (name, _params) in enumerate(self.client.calls, 1) if name == method)
        self.app._on_response(Response(number, result, None))

    def info(self, skills: dict | None = None) -> None:
        self.app._on_event(Event("session.info", SID, {"model": "openai/gpt-6.1-sol", "skills": skills or SKILLS}))

    def screen(self) -> list[str]:
        self.app._render()
        return self.app.terminal.screen.lines()  # type: ignore[attr-defined]

    def everything(self) -> list[str]:
        self.app._render()
        screen = self.app.terminal.screen  # type: ignore[attr-defined]
        return screen.history() + screen.lines()

    def press(self, *keys) -> None:
        for key in keys:
            self.app._on_input(key if isinstance(key, Key | Mouse) else Key(CHAR, key))


class StoreTests(unittest.TestCase):
    def test_the_launch_look_is_saved_beside_the_theme_and_neither_write_drops_the_other(self) -> None:
        home = Path(tempfile.mkdtemp()) / ".omh"
        write_theme_choice(home, theme_for_name("amber"))
        write_launch_choice(home, "violet")
        self.assertEqual(read_launch_choice(home), "violet")
        self.assertEqual(read_theme_choice(home).short_name, "amber")
        write_theme_choice(home, theme_for_name("mono"))
        self.assertEqual(read_launch_choice(home), "violet")
        record = json.loads(theme_store_path(home).read_text(encoding="utf-8"))
        self.assertEqual(record, {"schema_version": THEME_STORE_SCHEMA, "theme": "mono", "launch": "violet"})

    def test_nothing_saved_or_another_schema_reads_as_no_launch_look(self) -> None:
        home = Path(tempfile.mkdtemp()) / ".omh"
        self.assertIsNone(read_launch_choice(home))
        theme_store_path(home).parent.mkdir(parents=True)
        theme_store_path(home).write_text(json.dumps({"schema_version": "other/v9", "launch": "gold"}), encoding="utf-8")
        self.assertIsNone(read_launch_choice(home))


class PaletteTests(unittest.TestCase):
    def test_every_looks_card_colours_reach_their_floors_on_dark_and_light(self) -> None:
        for look in LAUNCH_PRESETS:
            for background in ("#0E151E", "#0D1117", "#FFFFFF", "#F6F8FA"):
                with self.subTest(look=look, background=background):
                    styles = launch_styles(default_theme(), background, launch_palette(look, background))
                    self.assertGreaterEqual(contrast(styles.brand.fg, background), 4.5)
                    self.assertGreaterEqual(contrast(styles.label.fg, background), 4.5)
                    self.assertGreaterEqual(contrast(styles.edge.fg, background), 3.0)

    def test_spectrum_is_the_default_and_the_first_look(self) -> None:
        self.assertEqual(DEFAULT_LAUNCH, "spectrum")
        self.assertEqual(LAUNCH_PRESETS, ("spectrum", "violet", "gold", "graphite"))
        self.assertEqual(set(LAUNCH_PALETTES), set(LAUNCH_PRESETS))


class SkillRowTests(unittest.TestCase):
    def test_omh_skills_regroup_by_family_with_the_ulw_lanes_first_and_hermes_own_by_category(self) -> None:
        rows = skill_rows(SKILLS, members=lambda: MEMBERS)
        self.assertEqual(rows.groups, (("ulw", ("dag-ulw", "ulw-plan")), ("plan", ("omh-plan",)),
                                       ("ship", ("omh-code-review", "omh-backend")),
                                       ("other", ("ulw-work", "omh-lone"))))
        self.assertEqual((rows.omh, rows.hermes, rows.hermes_categories), (7, 2, ("creative", "devops")))

    def test_names_show_whole_in_columns_and_a_full_group_ends_with_how_many_more(self) -> None:
        names = ("omh-a-name-wider-than-one-column-x",) + tuple(f"omh-skill-number-{index}" for index in range(30))
        rows = texts(grid([("plan", names)], 120, 2, paint()))
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].startswith("plan     omh-a-name-wider-than-one-column-x  "))  # it spans two columns
        self.assertTrue(rows[1].rstrip().endswith("more"))
        self.assertNotIn("…", "".join(rows))
        self.assertEqual({line_width(line) for line in grid([("plan", names)], 120, 2, paint())}, {120})


class CardTests(unittest.TestCase):
    FACTS = Facts(model="openai/gpt-6.1-sol", effort="high", session="20261023_000000_aaaaaaaa",
                  last_title="Check the proactive alerts", last_id="20261022_121251_cf45f40d", last_count=14)

    def test_every_look_fits_the_rows_it_is_given_and_its_width(self) -> None:
        skills = skill_rows(SKILLS, members=lambda: MEMBERS)
        for look in LAUNCH_PRESETS:
            for width, room in ((150, 30), (100, 18), (100, 12), (60, 9)):
                with self.subTest(look=look, width=width, room=room):
                    card = launch_card(look, self.FACTS, paint(look), width, room, skills)
                    self.assertTrue(card)
                    self.assertLessEqual(len(card), room)
                    self.assertTrue(all(line_width(line) <= width for line in card))

    def test_past_every_boxed_form_the_last_session_keeps_its_row(self) -> None:
        (row,) = texts(launch_card("violet", self.FACTS, paint("violet"), 100, 1, None))
        self.assertIn("Check the proactive alerts", row)
        self.assertIn("/resume 20261022_121251_cf45f40d", row)
        self.assertEqual(launch_card("violet", Facts("m", "", ""), paint("violet"), 100, 1, None), [])

    def test_each_look_has_its_own_layout(self) -> None:
        skills = skill_rows(SKILLS, members=lambda: MEMBERS)
        first = {look: texts(launch_card(look, self.FACTS, paint(look), 120, 30, skills))[0] for look in LAUNCH_PRESETS}
        self.assertTrue(first["spectrum"].startswith("╭─ session"))  # a box per section
        self.assertTrue(first["violet"].startswith("╭─ gpt-6.1-sol"))  # one box, the model in its edge
        self.assertTrue(first["gold"].startswith("╭─ gpt-6.1-sol"))
        self.assertTrue(first["graphite"].startswith("── gpt-6.1-sol"))  # rules, no side edges
        gold = texts(launch_card("gold", self.FACTS, paint("gold"), 120, 30, skills))
        self.assertTrue(any("│  skills" in line for line in gold))  # two columns, a rule between them
        self.assertFalse(any(line.startswith("│") for line in texts(launch_card("graphite", self.FACTS,
                                                                                 paint("graphite"), 120, 30, skills))))


class LaunchTests(unittest.TestCase):
    def test_the_card_is_drawn_under_the_banner_and_grows_when_the_skills_arrive_late(self) -> None:
        run = Launched()
        screen = run.screen()
        banner = next(index for index, line in enumerate(screen) if line.startswith("⚚ OH-MY-HERMES"))
        self.assertTrue(screen[banner + 2].startswith("╭─ session"), screen)
        self.assertIn("skills loading…", "\n".join(screen))
        run.info()
        screen = "\n".join(run.screen())
        self.assertNotIn("loading…", screen)
        self.assertIn("dag-ulw", screen)
        self.assertIn("hermes   creative", screen)
        # Every skill is found by typing / (the completion menu); Hermes' /skills only prints its help here.
        self.assertIn("type / to find one", screen)
        self.assertNotIn("/skills", screen)
        self.assertEqual(run.app.card_state, CARD_LIVE)

    def test_the_last_session_goes_into_the_card_instead_of_a_notice(self) -> None:
        run = Launched()
        run.answer("session.list", LAST)
        screen = "\n".join(run.screen())
        self.assertIn("/resume 20261022_121251_cf45f40d", screen)
        self.assertNotIn("last session:", screen)  # the notice it replaces
        self.assertEqual(run.app.transcript.blocks, [])

    def test_the_card_never_reaches_the_composer(self) -> None:
        for look in LAUNCH_PRESETS:
            for rows, cols in ((42, 150), (30, 100)):
                with self.subTest(look=look, rows=rows, cols=cols):
                    run = Launched(rows, cols, look=look)
                    run.info({"general": [name for _label, names in family_members() for name in names],
                              **{key: value for key, value in SKILLS.items() if key != "general"}})
                    run.answer("session.list", LAST)
                    screen = run.screen()
                    prompt = next(index for index, line in enumerate(screen) if line.startswith("❯"))
                    rule = prompt - 1
                    self.assertTrue(screen[rule].startswith("────"), screen)
                    drawn = [index for index, line in enumerate(screen[:rule]) if line.strip()]
                    self.assertLess(max(drawn), rule - 1, screen)  # a blank row at least between the card and it
                    self.assertEqual(len(screen), rows)

    def test_the_first_prompt_keeps_the_card_above_it_and_a_full_redraw_prints_it_again(self) -> None:
        run = Launched()
        run.info()
        run.screen()
        run.app._submit_prompt("hello")
        for moment in ("sent", "redrawn"):
            screen = run.screen()
            with self.subTest(moment=moment):
                self.assertEqual(run.app.card_state, CARD_KEPT)
                self.assertEqual(sum(line.startswith("╭─ session") for line in screen), 1, screen)
                card = next(index for index, line in enumerate(screen) if line.startswith("╭─ session"))
                banner = next(index for index, line in enumerate(screen) if line.startswith("⚚ OH-MY-HERMES"))
                prompt = next(index for index, line in enumerate(screen) if "hello" in line)
                self.assertLess(banner, card)
                self.assertLess(card, prompt)
            run.app._full_redraw()

    def test_a_resumed_session_has_no_card(self) -> None:
        run = Launched(env={"HERMES_TUI_RESUME": "stored-1"})
        run.info()
        self.assertEqual(run.app.card_state, CARD_NONE)
        self.assertNotIn("╭─ session", "\n".join(run.screen()))

    def test_the_saved_look_draws_the_logo_and_the_card(self) -> None:
        run = Launched(look="violet")
        run.info()
        screen = run.screen()
        self.assertTrue(any(line.startswith("╭─ gpt-6.1-sol") for line in screen), screen)
        writes = "".join(run.app.terminal.writes)  # type: ignore[attr-defined]
        r, g, b = (int(LAUNCH_PALETTES["violet"][0].faces[2][i : i + 2], 16) for i in (1, 3, 5))
        self.assertIn(f"38;2;{r};{g};{b}", writes)  # the logo's third row in violet's ramp

    def test_a_saved_look_is_read_whatever_its_case(self) -> None:
        home = Path(tempfile.mkdtemp()) / ".omh"
        theme_store_path(home).parent.mkdir(parents=True)
        theme_store_path(home).write_text(json.dumps({"schema_version": THEME_STORE_SCHEMA, "launch": "GRAPHITE"}),
                                          encoding="utf-8")
        run = Launched(env={"OMH_HOME": str(home)})
        self.assertEqual(run.app.launch_look, "graphite")
        self.assertEqual(run.app.transcript.blocks, [])  # no unknown-look notice
        run.app._theme("graphite")
        self.assertEqual(json.loads(theme_store_path(home).read_text(encoding="utf-8"))["launch"], "graphite")

    def test_a_saved_look_omh_does_not_have_says_so_once_and_spectrum_is_used(self) -> None:
        run = Launched(look="neon")
        self.assertEqual(run.app.launch_look, "spectrum")
        said = [getattr(block, "text", "") for block in run.app.transcript.blocks]
        self.assertEqual(said, [LAUNCH_UNKNOWN_NOTICE.format(name="neon", path=theme_store_path(run.home))])

    def test_no_color_draws_the_logo_banner_and_card_without_colour(self) -> None:
        run = Launched(env={"NO_COLOR": "1"})
        run.info()
        run.screen()
        writes = "".join(run.app.terminal.writes)  # type: ignore[attr-defined]
        self.assertNotIn("38;2;", writes)
        self.assertNotIn("38;5;", writes)
        self.assertEqual(run.app._launch_paint().look.brand.depth, NO_COLOR)
        self.assertTrue(run.app._launch_paint().look.brand.bold)


class SwitchTests(unittest.TestCase):
    def test_theme_alone_opens_the_picker_and_each_move_previews_the_look(self) -> None:
        run = Launched()
        run.info()
        run.screen()
        run.app._theme("")
        self.assertIsNotNone(run.app.launch_picker)
        screen = "\n".join(run.screen())
        self.assertIn("Launch look", screen)
        before = sum(CLEAR_SCREEN_AND_SCROLLBACK in write for write in run.app.terminal.writes)  # type: ignore[attr-defined]
        run.press(Key(DOWN))
        self.assertEqual(run.app._shown_look(), "violet")
        self.assertEqual(run.app.launch_look, "spectrum")  # previewed, not kept
        screen = run.screen()
        cleared = sum(CLEAR_SCREEN_AND_SCROLLBACK in write for write in run.app.terminal.writes)  # type: ignore[attr-defined]
        self.assertGreater(cleared, before)  # the logo printed again in the look under the highlight
        self.assertTrue(any(line.startswith("╭─ gpt-6.1-sol") for line in screen), screen)  # violet's one box
        run.press(Key(ESCAPE))
        self.assertIsNone(run.app.launch_picker)
        self.assertEqual(run.app._shown_look(), "spectrum")
        self.assertIsNone(read_launch_choice(run.home))

    def test_enter_keeps_the_highlighted_look_and_saves_it(self) -> None:
        run = Launched()
        run.app._theme("")
        run.press(Key(DOWN), Key(DOWN), Key(ENTER))
        self.assertIsNone(run.app.launch_picker)
        self.assertEqual(run.app.launch_look, "gold")
        self.assertEqual(read_launch_choice(run.home), "gold")

    def test_theme_with_a_look_keeps_it_at_once(self) -> None:
        run = Launched()
        run.app._theme("graphite")
        self.assertIsNone(run.app.launch_picker)
        self.assertEqual(run.app.launch_look, "graphite")
        self.assertEqual(read_launch_choice(run.home), "graphite")
        self.assertIn("── gpt-6.1-sol", "\n".join(run.screen()))

    def test_a_click_on_a_look_moves_the_highlight_and_reports_are_on_only_while_it_is_open(self) -> None:
        run = Launched()
        run.screen()
        self.assertFalse(run.app.terminal.mouse_reporting)  # type: ignore[attr-defined]
        run.app._theme("")
        screen = run.screen()
        self.assertTrue(run.app.terminal.mouse_reporting)  # type: ignore[attr-defined]
        row = next(index for index, line in enumerate(screen) if line.lstrip().startswith("graphite"))
        run.press(Mouse(BUTTON_LEFT, 4, row + 1))
        self.assertEqual(run.app._shown_look(), "graphite")
        run.press(Key(ENTER))
        run.screen()
        self.assertFalse(run.app.terminal.mouse_reporting)  # type: ignore[attr-defined]
        self.assertEqual(read_launch_choice(run.home), "graphite")

    def test_esc_and_ctrl_c_close_the_picker_put_the_look_back_and_turn_reports_off(self) -> None:
        for name, key in (("esc", Key(ESCAPE)), ("ctrl+c", Key(CHAR, "c", ctrl=True))):
            with self.subTest(key=name):
                run = Launched()
                run.screen()
                run.app._theme("")
                run.screen()
                self.assertTrue(run.app.terminal.mouse_reporting)  # type: ignore[attr-defined]
                run.press(Key(DOWN), Key(DOWN))  # gold, previewed
                self.assertEqual(run.app._shown_look(), "gold")
                run.press(key)
                self.assertIsNone(run.app.launch_picker)
                self.assertEqual(run.app._shown_look(), "spectrum")  # the look from before the picker opened
                run.screen()
                self.assertFalse(run.app.terminal.mouse_reporting)  # type: ignore[attr-defined]
                self.assertIsNone(read_launch_choice(run.home))

    def test_j_and_k_move_as_the_arrows_do(self) -> None:
        run = Launched()
        run.app._theme("")
        run.press("j", "j")
        self.assertEqual(run.app._shown_look(), "gold")
        run.press("k")
        self.assertEqual(run.app._shown_look(), "violet")

    def test_past_the_launch_a_compact_preview_above_the_picker_follows_the_highlight_without_a_redraw(self) -> None:
        run = Launched()
        run.info()
        run.screen()
        run.app._submit_prompt("hello")
        run.screen()
        run.app._theme("")
        screen = run.screen()
        header = next(index for index, line in enumerate(screen) if line.strip() == "Launch look")
        self.assertTrue(screen[header - 4].startswith("⚚ OH-MY-HERMES"), screen)  # the wordmark, then the frame
        self.assertTrue(screen[header - 3].startswith("╭─ gpt-6.1-sol"), screen)
        before = sum(CLEAR_SCREEN_AND_SCROLLBACK in write for write in run.app.terminal.writes)  # type: ignore[attr-defined]
        run.press("k", Key(DOWN), Key(DOWN), Key(DOWN))
        screen = run.screen()
        header = next(index for index, line in enumerate(screen) if line.strip() == "Launch look")
        self.assertTrue(screen[header - 3].startswith("── gpt-6.1-sol"), screen)  # graphite's rules
        after = sum(CLEAR_SCREEN_AND_SCROLLBACK in write for write in run.app.terminal.writes)  # type: ignore[attr-defined]
        self.assertEqual(after, before)  # nothing on screen to repaint: the preview is the next frame's
        row = next(index for index, line in enumerate(screen) if line.lstrip().startswith("violet"))
        run.press(Mouse(BUTTON_LEFT, 4, row + 1))  # under the preview, a click still lands on its row
        self.assertEqual(run.app._shown_look(), "violet")

    def test_under_no_color_the_highlight_is_reverse_video(self) -> None:
        run = Launched(env={"NO_COLOR": "1"})
        run.app._theme("")
        rows = run.app.launch_picker.lines(150, run.app.theme, 10, run.app._launch_swatches())
        highlighted = [span for span in rows[run.app.launch_picker.first_row] if span.text.strip()]
        self.assertTrue(highlighted[0].style.reverse)
        self.assertFalse(rows[run.app.launch_picker.first_row + 1][0].style.reverse)

    def test_tab_completes_a_look_after_theme(self) -> None:
        run = Launched()
        run.app._set_composer("/theme gr")
        self.assertEqual([entry.name for entry in run.app._menu_entries()], ["theme graphite"])
        run.app._accept_completion()
        self.assertEqual(run.app.composer.text, "/theme graphite ")
        run.app._set_composer("/theme ")
        self.assertEqual([entry.name for entry in run.app._menu_entries()],
                         [f"theme {name}" for name in LAUNCH_PRESETS])
        run.app._set_composer("/theme graphite")
        self.assertEqual(run.app._menu_entries(), [])  # typed whole: Enter sends it

    def test_the_shortcuts_name_the_picker(self) -> None:
        self.assertTrue(any("/theme picker" in does for _keys, does in SHORTCUTS))


if __name__ == "__main__":
    unittest.main()
