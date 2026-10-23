"""The block logo a start shows above the banner.

The rows are the `banner_logo` of the shipped skin (read through the
package, as `skin_colors` reads the palette), its Rich markup taken off and
each cell painted in the launch look's colours (`theme.LAUNCH_PALETTES`: a
ramp per row, a sweep across the columns, or graphite's grey, white and
turquoise), a drop-shadow glyph darker than its face, every glyph lifted to
the 3:1 a graphical mark needs on the terminal's background. The skin's own
row colours stay in its file for Hermes' banner. It shows from
`LOGO_MIN_ROWS` rows and as many columns as the logo is wide, goes to
scrollback with the banner, comes back with a full redraw only on top of the
whole session it opened, and is not shown again for `/new` or a switch.
"""

from __future__ import annotations

import unittest
from unittest import mock

from _tui_lines import line_text
from test_tui_app import FakePoller, ScriptedTerminal
from test_tui_modes import SID, RecordingClient
from test_tui_theme import DARK_BACKGROUNDS, LIGHT_BACKGROUNDS, contrast
from omh.skin_pack import SKIN_THEMES, default_theme, logo_from_text, skin_colors, skin_logo
from omh.tui.app import BANNER_BRAND, LOGO_MIN_ROWS, App, banner_logo
from omh.tui.gateway import Response
from omh.tui.theme import (
    DEFAULT_LAUNCH,
    KIND_HUES,
    LAUNCH_PALETTES,
    LAUNCH_PRESETS,
    LOGO_BLOCK,
    is_light,
    launch_palette,
    logo_lines,
)
from omh.tui.width import line_width

SKY_GRADIENT = ["#B8F4FF", "#7FDBFF", "#40D6DE", "#00CED1", "#0FA3AF", "#0B7E8A"]
LOGO_WIDTH = 100


class StartClient(RecordingClient):
    def call(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        return {"session_id": SID, "stored_session_id": "stored-1", "info": {"model": "m", "cwd": "/p"}}


def started(rows: int, cols: int) -> tuple[App, list[str]]:
    """A launched app after its first frame, and everything on its terminal, scrollback first."""
    app = App(ScriptedTerminal(rows, cols), StartClient(), {}, poller=FakePoller())  # type: ignore[arg-type]
    app.startup()
    return app, shown(app)


def shown(app: App) -> list[str]:
    app._render()
    screen = app.terminal.screen  # type: ignore[attr-defined]
    return screen.history() + screen.lines()


def painted(look: str = DEFAULT_LAUNCH, background: str | None = None, skin=None):
    skin = skin or default_theme()
    surface = background or skin_colors(skin.skin_name)["status_bar_bg"]
    return logo_lines(skin, background, launch_palette(look, surface))


def logo_text() -> list[str]:
    return [line_text(row) for row in painted()]


def cells(row) -> list[tuple[str, str]]:
    """(character, colour) per cell of one logo row."""
    return [(char, span.style.fg) for span in row for char in span.text]


class ReaderTests(unittest.TestCase):
    def test_every_shipped_skin_has_six_rows_a_hundred_cells_wide_with_the_markup_off(self) -> None:
        for skin in SKIN_THEMES:
            with self.subTest(skin=skin.short_name):
                rows = skin_logo(skin.skin_name)
                self.assertEqual(len(rows), 6)
                self.assertEqual({line_width(row) for row in painted(skin=skin)}, {LOGO_WIDTH})
                for text, colour, _bold in rows:
                    self.assertNotIn("[", text)
                    self.assertNotIn("]", text)
                    self.assertTrue(colour.startswith("#"))
        self.assertEqual([colour for _text, colour, _bold in skin_logo("omh")], SKY_GRADIENT)
        self.assertEqual([bold for _text, _colour, bold in skin_logo("omh")], [True, True, False, False, False, False])

    def test_a_row_without_a_tag_or_a_colour_keeps_its_text_and_the_block_ends_at_the_next_key(self) -> None:
        document = "name: x\nbanner_logo: |-\n  plain row\n  [bold]bold row[/]\n  [#12AB34] lead[/]\ncolors:\n  a: \"#000000\"\n"
        self.assertEqual(
            logo_from_text(document), [("plain row", "", False), ("bold row", "", True), (" lead", "#12AB34", False)]
        )


class PaintTests(unittest.TestCase):
    def test_every_glyph_of_every_look_reaches_three_to_one_on_every_background(self) -> None:
        for look in LAUNCH_PRESETS:
            for background in (None, *LIGHT_BACKGROUNDS, *DARK_BACKGROUNDS):
                surface = background or skin_colors(default_theme().skin_name)["status_bar_bg"]
                for number, row in enumerate(painted(look, background)):
                    with self.subTest(look=look, background=background, row=number):
                        worst = min(contrast(colour, surface) for char, colour in cells(row) if char != " ")
                        self.assertGreaterEqual(worst, 3.0)

    def test_a_shadow_glyph_is_drawn_darker_than_the_face_of_its_row_on_a_dark_background(self) -> None:
        for look in ("violet", "gold"):  # a ramp per row: one face colour, one shadow colour on each row
            with self.subTest(look=look):
                row = painted(look, "#0E151E")[2]
                faces = {colour for char, colour in cells(row) if char == LOGO_BLOCK}
                shadows = {colour for char, colour in cells(row) if char not in (LOGO_BLOCK, " ")}
                self.assertEqual(len(faces), 1)
                self.assertEqual(len(shadows), 1)
                self.assertGreater(contrast(faces.pop(), "#0E151E"), contrast(shadows.pop(), "#0E151E"))

    def test_spectrum_sweeps_the_kind_hues_across_the_columns(self) -> None:
        row = painted("spectrum", "#0E151E")[2]
        faces = [colour for char, colour in cells(row) if char == LOGO_BLOCK]
        self.assertGreater(len(set(faces)), 40)  # a colour per column, not per row
        self.assertEqual(faces[0], KIND_HUES["skill"][0])  # the sweep starts on the skill hue, left
        self.assertNotEqual(faces[0], faces[-1])

    def test_a_ramp_keeps_each_rows_colour_where_it_already_reaches_three_to_one(self) -> None:
        for look in ("violet", "gold"):
            for background in ("#0E151E", "#FFFFFF"):
                palette = launch_palette(look, background)
                for number, row in enumerate(painted(look, background)[:5]):  # the sixth row is all shadow
                    faces = {colour for char, colour in cells(row) if char == LOGO_BLOCK}
                    with self.subTest(look=look, background=background, row=number):
                        self.assertEqual(faces, {palette.faces[number]})

    def test_graphite_draws_grey_oh_my_white_hermes_and_only_the_hyphens_in_colour(self) -> None:
        dark = LAUNCH_PALETTES["graphite"][0]
        row = cells(painted("graphite", "#0E151E")[2])
        sparks = [index for index, (char, colour) in enumerate(row) if colour == dark.spark]
        self.assertEqual(len(sparks), 10)  # two hyphens' five block cells; the H's crossbars are not hyphens
        self.assertEqual(row[0][1], dark.greys[2])  # O
        self.assertEqual(row[-2][1], dark.whites[2])  # S, the last block cell

    def test_a_light_background_takes_each_looks_light_set(self) -> None:
        for look in LAUNCH_PRESETS:
            with self.subTest(look=look):
                self.assertIs(launch_palette(look, "#FFFFFF"), LAUNCH_PALETTES[look][1])
                self.assertIs(launch_palette(look, "#0E151E"), LAUNCH_PALETTES[look][0])
                self.assertTrue(is_light("#FFFFFF"))
        # The kind hues' light lime is an olive; spectrum's light sweep ends on its own lime instead.
        self.assertNotEqual(LAUNCH_PALETTES["spectrum"][1].sweep[-1], KIND_HUES["web"][1])

    def test_no_escape_sequence_or_control_character_from_the_skin_reaches_the_terminal(self) -> None:
        planted = [("a\x1b[31mb\x07c\x1b]0;title\x07d\u009b2Je", "#123456", False)]
        with mock.patch("omh.tui.theme.skin_logo", return_value=planted):
            (row,) = painted()
        self.assertEqual(line_text(row), "abcde")


class StartTests(unittest.TestCase):
    def test_a_start_wide_and_tall_enough_shows_the_logo_above_the_banner(self) -> None:
        self.assertEqual(LOGO_MIN_ROWS, 24)
        _app, screen = started(LOGO_MIN_ROWS, LOGO_WIDTH)
        logo = logo_text()
        self.assertEqual(screen[: len(logo)], logo)
        self.assertEqual(screen[len(logo)], "")
        self.assertTrue(screen[len(logo) + 1].startswith(BANNER_BRAND))

    def test_a_terminal_narrower_than_the_logo_or_shorter_than_the_minimum_gets_the_banner_alone(self) -> None:
        for rows, cols in ((LOGO_MIN_ROWS, LOGO_WIDTH - 1), (LOGO_MIN_ROWS - 1, 160)):
            with self.subTest(rows=rows, cols=cols):
                _app, screen = started(rows, cols)
                self.assertTrue(screen[0].startswith(BANNER_BRAND), screen[:3])
                self.assertEqual(banner_logo(default_theme(), None, cols, rows), [])

    def test_a_full_redraw_shows_it_again_on_top_of_the_whole_session_only(self) -> None:
        app, _screen = started(30, 120)
        app.transcript.add_notice("one")
        app._full_redraw()
        self.assertEqual(shown(app)[:6], logo_text())
        app.transcript.add_notice("two")
        with mock.patch("omh.tui.app.REDRAW_TAIL_ROWS", 2):  # the redraw leaves "one" out
            app._full_redraw()
            screen = shown(app)
        self.assertTrue(screen[0].startswith(BANNER_BRAND), screen[:3])

    def test_a_new_session_does_not_repeat_it(self) -> None:
        app, _screen = started(30, 120)
        app._new_session("")
        calls = app.client.calls  # type: ignore[attr-defined]
        self.assertEqual(calls[-1][0], "session.create")
        app._on_response(Response(len(calls), {"session_id": "live-2", "stored_session_id": "s2"}, None))
        app._full_redraw()
        screen = shown(app)
        self.assertTrue(screen[0].startswith(BANNER_BRAND), screen[:3])
        self.assertNotIn(logo_text()[0], screen)


if __name__ == "__main__":
    unittest.main()
