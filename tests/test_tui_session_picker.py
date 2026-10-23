"""The `/sessions` picker (`omh.tui.session_picker`): rows, keys, and what it draws."""

from __future__ import annotations

import unittest

from _tui_lines import line_text
from omh.tui.keys import BACKSPACE, CHAR, DOWN, ENTER, ESCAPE, PAGE_DOWN, PAGE_UP, UP, Key, Paste
from omh.tui.session_picker import CLOSE, PICK, PickerAction, SessionPicker, age_text, session_rows
from omh.tui.style import Span
from omh.tui.theme import DEFAULT_THEME
from omh.tui.width import line_width

NOW = 1_791_900_000.0

# The recorded `session.list` row shape (Hermes 6c61c3e27d), plus synthesized neighbours.
LISTED = {
    "sessions": [
        {"id": "20261013_115656_e709ea", "title": "Reply with ok", "preview": "Reply with exactly the word: ok",
         "started_at": NOW - 30, "message_count": 14, "source": "tui", "_lineage_root_id": None},
        {"id": "20261012_090000_aaaaaa", "title": "", "preview": "Refactor the router\nsecond line",
         "started_at": NOW - 7200, "message_count": 1, "source": "cli"},
        {"id": "20261001_090000_bbbbbb", "title": "Release notes", "preview": "",
         "started_at": NOW - 3 * 86400, "message_count": 40, "source": "tui"},
        {"title": "no id"},
        "not a row",
    ]
}


def picker(**kwargs: object) -> SessionPicker:
    return SessionPicker(session_rows(LISTED), current="20261013_115656_e709ea", **kwargs)


class SessionRowsTests(unittest.TestCase):
    def test_rows_keep_the_list_order_and_skip_rows_without_an_id(self) -> None:
        rows = session_rows(LISTED)
        self.assertEqual([row.id for row in rows],
                         ["20261013_115656_e709ea", "20261012_090000_aaaaaa", "20261001_090000_bbbbbb"])
        self.assertEqual([row.label for row in rows], ["Reply with ok", "Refactor the router", "Release notes"])
        self.assertEqual(session_rows({"sessions": "nope"}), [])

    def test_age_reads_in_the_largest_whole_unit(self) -> None:
        cases = {NOW - 5: "just now", NOW - 300: "5m ago", NOW - 7200: "2h ago", NOW - 3 * 86400: "3d ago", 0: ""}
        for started, expected in cases.items():
            with self.subTest(started=started):
                self.assertEqual(age_text(started, NOW), expected)


class KeyTests(unittest.TestCase):
    def test_arrows_and_pages_move_within_the_rows_and_enter_picks(self) -> None:
        chooser = picker(page=2)
        for key, index in ((UP, 0), (DOWN, 1), (DOWN, 2), (DOWN, 2), (PAGE_UP, 0), (PAGE_DOWN, 2)):
            with self.subTest(key=key):
                self.assertIsNone(chooser.handle_key(Key(key)))
                self.assertEqual(chooser.index, index)
        self.assertEqual(chooser.handle_key(Key(ENTER)), PickerAction(PICK, "20261001_090000_bbbbbb"))

    def test_typing_filters_by_title_preview_or_id_and_backspace_widens_again(self) -> None:
        chooser = picker()
        for char in "ROUT":
            chooser.handle_key(Key(CHAR, char))
        self.assertEqual([row.id for row in chooser.visible()], ["20261012_090000_aaaaaa"])
        self.assertEqual(chooser.handle_key(Key(ENTER)), PickerAction(PICK, "20261012_090000_aaaaaa"))
        for _ in range(4):
            chooser.handle_key(Key(BACKSPACE))
        chooser.handle_key(Paste("bbbbbb\nignored"))
        self.assertEqual([row.id for row in chooser.visible()], ["20261001_090000_bbbbbb"])

    def test_enter_with_nothing_matching_picks_nothing(self) -> None:
        chooser = picker(query="zzz")
        self.assertIsNone(chooser.handle_key(Key(ENTER)))

    def test_esc_and_ctrl_c_close(self) -> None:
        for key in (Key(ESCAPE), Key(CHAR, "c", ctrl=True)):
            with self.subTest(key=key):
                self.assertEqual(picker().handle_key(key), PickerAction(CLOSE))


class LinesTests(unittest.TestCase):
    def test_a_header_then_one_row_per_session_with_the_current_one_marked(self) -> None:
        lines = picker().lines(60, DEFAULT_THEME, 8, NOW)
        self.assertTrue(line_text(lines[0]).startswith(" Sessions (3)"))
        self.assertEqual(len(lines), 5)
        self.assertEqual(line_text(lines[-1]), "❯ type to filter")  # the filter, where the composer was
        current, other, old = (line_text(line) for line in lines[1:4])
        self.assertTrue(current.startswith(" ● Reply with ok"))
        self.assertTrue(current.rstrip().endswith("just now · 14 messages"))
        self.assertTrue(other.startswith("   Refactor the router"))
        self.assertTrue(other.rstrip().endswith("2h ago · 1 message"))
        self.assertTrue(old.rstrip().endswith("3d ago · 40 messages"))
        self.assertTrue(all(line_width(line) <= 60 for line in lines))
        self.assertEqual(lines[1][0].style, DEFAULT_THEME.menu_current)
        self.assertEqual(lines[2][0].style, DEFAULT_THEME.menu)

    def test_rows_scroll_to_keep_the_highlighted_one_and_the_page_follows_the_room(self) -> None:
        chooser = picker(index=2)
        lines = chooser.lines(60, DEFAULT_THEME, 4, NOW)
        self.assertEqual(len(lines), 4)
        self.assertIn("Release notes", line_text(lines[-2]))
        self.assertEqual(chooser.page, 2)

    def test_a_filter_that_matches_nothing_says_so(self) -> None:
        lines = picker(query="zzz").lines(60, DEFAULT_THEME, 5, NOW)
        self.assertIn("Sessions (0/3)", line_text(lines[0]))
        self.assertIn("no session matches", line_text(lines[1]))
        self.assertEqual(line_text(lines[-1]), "❯ zzz")

    def test_the_filter_row_is_last_at_every_budget_and_says_what_typing_filters_by(self) -> None:
        chooser = picker(query="re")
        for max_rows in range(2, 9):
            with self.subTest(max_rows=max_rows):
                lines = chooser.lines(60, DEFAULT_THEME, max_rows, NOW)
                self.assertLessEqual(len(lines), max_rows)
                self.assertEqual(line_text(lines[-1]), "❯ re")
                self.assertNotIn("filter", line_text(lines[0]))
        self.assertEqual(len(chooser.lines(60, DEFAULT_THEME, 1, NOW)), 1)
        filtered = picker()
        filtered.handle_key(Key(CHAR, "R"))
        self.assertEqual(line_text(filtered.lines(60, DEFAULT_THEME, 6, NOW)[-1]), "❯ R")


class FrameTests(unittest.TestCase):
    """The keys ladder in the header, the highlighted row on a tiny budget, and the caret at the end of the filter."""

    def test_the_header_is_the_title_alone(self) -> None:
        # The keys are the info line's.
        self.assertEqual(line_text(picker().lines(100, DEFAULT_THEME, 6, NOW)[0]).rstrip(), " Sessions (3)")
        self.assertEqual(line_width(picker().lines(8, DEFAULT_THEME, 6, NOW)[0]), 8)

    def test_two_rows_or_fewer_keep_the_highlighted_row_and_drop_the_header(self) -> None:
        chooser = picker(index=1)
        one = chooser.lines(60, DEFAULT_THEME, 1, NOW)
        self.assertEqual(len(one), 1)
        self.assertTrue(line_text(one[0]).startswith("   Refactor the router"), one)
        self.assertEqual(one[0][0].style, DEFAULT_THEME.menu_current)
        two = [line_text(line) for line in chooser.lines(60, DEFAULT_THEME, 2, NOW)]
        self.assertTrue(two[0].startswith("   Refactor the router"), two)
        self.assertEqual(two[1], "❯ type to filter")
        self.assertIn("no session matches", line_text(picker(query="zzz").lines(60, DEFAULT_THEME, 1, NOW)[0]))

    def test_the_caret_sits_at_the_end_of_the_filter_row(self) -> None:
        for query, budget in (("", 6), ("re", 6), ("re", 2), ("한글", 6)):
            with self.subTest(query=query, budget=budget):
                chooser = picker(query=query)
                lines = chooser.lines(60, DEFAULT_THEME, budget, NOW)
                self.assertEqual(chooser.caret, (len(lines) - 1, line_width((Span("❯ " + query),))))
        chooser = picker()
        chooser.lines(60, DEFAULT_THEME, 1, NOW)
        self.assertIsNone(chooser.caret)  # no filter row drawn, no caret


if __name__ == "__main__":
    unittest.main()
