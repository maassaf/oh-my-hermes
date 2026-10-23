"""NO_COLOR (no-color.org): set to anything but "", the whole TUI renders with no colour (#2223 review item 17).

Before, only motion and the delegation graph read it; the transcript, the
composer, the agent view, the pager and the pickers kept painting truecolour.
Now the app's theme is `theme.without_colour` at the `style.NO_COLOR` depth:
bold, dim, italic, underline and reverse stay, the menu's current row is
reversed, and every state keeps the glyph it carries. Unset, nothing changes.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from _delegation_graphs import dag_export_run
from test_tui_delegation_block import with_hud
from test_tui_modes import FakePoller, RecordingClient, ScriptedTerminal, make_app, type_text
from test_tui_subagents import feed
from omh.skin_pack import SKIN_THEMES
from omh.tui.app import App
from omh.tui.dag_pager import DAG_KEYS, DagPager
from omh.tui.gateway import Event
from omh.tui.keys import ENTER, Key
from omh.tui.pager import FOOTER_KEYS
from omh.tui.panels import dag_style
from omh.tui.style import Style
from omh.tui.theme import build_theme, on_band, without_colour
from omh.tui.transcript import NOTICE_ERROR, NOTICE_OK, TOOL_OK, NoticeBlock, ToolGroup, ToolRow, block_rows

SID = "s"
RESUME = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "tui_gateway" / "resume_history.json").read_text(encoding="utf-8")
)["messages"]
SGR = re.compile(r"\x1b\[([0-9;]*)m")


def colour_codes(text: str) -> list[str]:
    """Every SGR in `text` that sets a colour: 30-37, 38 (;5 or ;2), 40-47, 48, 90-97 or 100-107."""
    found = []
    for match in SGR.finditer(text):
        codes = [int(code) for code in match[1].split(";") if code]
        if any(30 <= code <= 38 or 40 <= code <= 48 or 90 <= code <= 97 or 100 <= code <= 107 for code in codes):
            found.append(match[0])
    return found


def scene_app(environ: dict[str, str]) -> App:
    """Scene A: the logo and banner, a resumed turn (calls, an edit's diff), then a live turn with a running and a failed call, a reply
    and two subagents, on a 46 x 150 terminal."""
    app = App(ScriptedTerminal(46, 150), RecordingClient(), environ, poller=FakePoller())  # type: ignore[arg-type]
    app.sid = SID
    app.adopt_background("#0D1117", environ)
    app.banner_facts, app.logo_due = {"cwd": "~/demo", "resumed": "stored"}, True  # the banner and the skin's logo
    app._draw_history(RESUME)
    app.turn_pending = True
    for name, event, payload in (
        ("message.start", "message.start", {}),
        ("run", "tool.start", {"tool_id": "a", "name": "terminal", "args": {"command": "pytest -q"}, "context": "pytest"}),
        ("fail", "tool.start", {"tool_id": "b", "name": "read_file", "context": "missing.py"}),
        ("fail", "tool.complete", {"tool_id": "b", "name": "read_file", "duration_s": 0.1,
                                   "result": {"success": False, "error": "No such file"}}),
    ):
        app.transcript.apply(Event(event, SID, payload))
    feed(app.roster, "start", 10.0, "sa-a", goal="Fix the flaky parser test", model="gpt-6.1-sol")
    feed(app.roster, "start", 10.0, "sa-b", goal="Audit the auth flow", model="deepseek-v4.1-flash")
    feed(app.roster, "complete", 12.0, "sa-b", status="failed")
    return app


def written(app: App) -> str:
    return "".join(app.terminal.writes)  # type: ignore[attr-defined]


class NoColourTests(unittest.TestCase):
    def test_a_whole_session_under_no_color_paints_no_colour(self) -> None:
        app = scene_app({"NO_COLOR": "1"})
        app._render()  # scene A
        type_text(app, "/")  # scene B: the completion menu, then /history's pager over the session
        app._render()
        for key in (Key("char", char) for char in "history"):
            app._on_input(key)
        app._on_input(Key(ENTER))
        app._render()
        self.assertIsNotNone(app.pager)
        self.assertIn("pytest", written(app))  # the scene drew
        # #2230 review F6: the pager's footer, which only its background marked as a bar, is reversed.
        footer = re.search(r"\x1b\[([0-9;]*)m[^\x1b]*" + re.escape(FOOTER_KEYS), written(app))
        self.assertIsNotNone(footer)
        self.assertIn("7", footer[1].split(";"))
        self.assertEqual(colour_codes(written(app)), [])

    def test_the_menus_current_row_is_reversed_and_bold(self) -> None:
        app = scene_app({"NO_COLOR": "1"})
        app.turn_pending = False
        type_text(app, "/")
        app._render()
        current = [match for match in SGR.finditer(written(app)) if {"1", "7"} <= set(match[1].split(";"))]
        self.assertTrue(current, "the menu's current row is drawn reversed and bold")
        self.assertTrue(app.theme.menu_current.reverse and app.theme.menu_current.bold)
        self.assertFalse(app.theme.menu.reverse)  # only the current row

    def test_the_rows_on_a_band_paint_no_colour(self) -> None:
        # #2230 review F1: `_banded` gave a band's spans its background at their own depth, the default truecolour.
        theme = scene_app({"NO_COLOR": "1"}).theme
        allowed = ToolGroup([ToolRow("t", "terminal", "ls", TOOL_OK, duration_s=0.1, output=("a.txt",),
                                     answers=[("allowed once · ls", NOTICE_OK)])], done=True)
        wide = NoticeBlock("The turn ended with an error. " * 6, NOTICE_ERROR)  # wider than the screen: it wraps
        for name, rows in (("approval", block_rows(allowed, 60, theme)), ("notice", block_rows(wide, 60, theme))):
            with self.subTest(rows=name):
                self.assertGreater(len(rows), 1)
                self.assertEqual(colour_codes("".join(span.style.sgr() for row in rows for span in row)), [])

    def test_the_dag_pagers_footer_is_reversed(self) -> None:
        # nocolor-review R7 on #2230: /dag's footer, the same `status` bar as the pager's, under NO_COLOR.
        app, _client = make_app(rows=60, cols=100, environ={"NO_COLOR": "1"})
        with_hud(app, dag_export_run())
        app._dag("")
        app._render()
        self.assertIsInstance(app.pager, DagPager)
        keys = re.search(r"\x1b\[([0-9;]*)m " + re.escape(DAG_KEYS), written(app))
        self.assertIsNotNone(keys, "the keys row is drawn in a style of its own")
        self.assertIn("7", keys[1].split(";"))
        # nocolor-review R7b on #2233: the row under the keys, the title and the rows shown, is reversed too.
        title = re.search(r"\x1b\[([0-9;]*)m " + re.escape(f"{app.pager.title} · rows "), written(app))
        self.assertIsNotNone(title, "the title row is drawn in a style of its own")
        self.assertIn("7", title[1].split(";"))
        self.assertEqual(colour_codes(written(app)), [])

    def test_unset_the_rows_on_a_band_keep_its_colour(self) -> None:
        # nocolor-review R8 on #2230: `_banded` gives a band's spans its depth; unset, that is truecolour, so every
        # word on the band keeps the band's background.
        theme = scene_app({}).theme
        wide = NoticeBlock("The turn ended with an error. " * 6, NOTICE_ERROR)  # wider than the screen: it wraps
        rows = block_rows(wide, 60, theme)
        words = [span for row in rows for span in row if span.text.strip()]
        self.assertGreater(len(rows), 1)
        self.assertTrue(words)
        self.assertEqual([span.text for span in words if "48;2;" not in span.style.sgr()], [])

    def test_a_theme_switch_keeps_no_colour(self) -> None:
        # #2230 review F2: `/theme <name>` and `/theme light` build the theme again.
        for arg in (SKIN_THEMES[1].short_name, "light"):
            with self.subTest(theme=arg):
                app = scene_app({"NO_COLOR": "1"})
                app._theme(arg)
                app._render()
                self.assertIn("pytest", written(app))
                self.assertEqual(colour_codes(written(app)), [])

    def test_every_skin_dark_or_light_has_no_colour_left(self) -> None:
        for skin in SKIN_THEMES:
            for background in ("#0D1117", "#FFFFFF", None):
                theme = without_colour(build_theme(skin, background))
                styles = []
                for roles in (theme, on_band(theme, theme.error_band)):  # the words drawn on a band too
                    styles += [value for value in vars(roles).values() if isinstance(value, Style)]
                    styles += [style for value in vars(roles).values() if isinstance(value, tuple) for style in value]
                # The delegation graph's roles, which a caller may ask for at truecolour (the transcript's box graph).
                styles += [dag_style(role, theme, 0, 3) for role in ("edge", "node:running", "glyph:failed", "frontier")]
                with self.subTest(theme=skin.short_name, background=background):
                    self.assertEqual([style for style in styles if colour_codes(style.sgr())], [])
                    self.assertTrue(theme.tool.bold and theme.brand.bold)  # what is bold stays bold

    def test_unset_or_empty_no_color_keeps_every_colour(self) -> None:
        for environ in ({}, {"NO_COLOR": ""}):
            with self.subTest(environ=environ):
                app = scene_app(environ)
                app._render()
                self.assertEqual(app.theme, build_theme(app._skin(), app._surface()))
                self.assertIn("38;2;", written(app))


if __name__ == "__main__":
    unittest.main()
