"""Cell widths and glyph-safe cutting for the OMH TUI (`omh.tui.width`).

The screen clips and wraps every row by these numbers, so a wrong width is a
row that wraps on the real terminal and shifts the whole live region. Every
expectation here is a literal, never a call back into the module.
"""

from __future__ import annotations

import unicodedata
import unittest

from omh.tui.style import Span, Style
from omh.tui.width import (
    cell_width,
    char_width,
    cluster_width,
    clusters,
    line_width,
    truncate,
    truncate_line,
    wrap,
    wrap_line,
)

HANGUL = "한글"
HANGUL_NFD = unicodedata.normalize("NFD", HANGUL)
FAMILY = "\U0001F468‍\U0001F469‍\U0001F467‍\U0001F466"  # man ZWJ woman ZWJ girl ZWJ boy
HEART_EMOJI = "❤️"  # heavy black heart + VS16
THUMBS_UP_TONED = "\U0001F44D\U0001F3FD"  # thumbs up + medium skin tone
FLAG_KR = "\U0001F1F0\U0001F1F7"  # regional indicators K R
Q_ACUTE = "q́"  # no precomposed form, so it stays two code points after NFC

RED = Style(fg="#ff0000")
BOLD = Style(bold=True)


class CharWidthTests(unittest.TestCase):
    def test_single_code_points(self) -> None:
        cases = {
            "a": 1,
            " ": 1,
            "한": 2,
            "中": 2,
            "ア": 2,  # fullwidth katakana
            "ｱ": 1,  # halfwidth katakana
            "Ａ": 2,  # fullwidth latin
            "\U0001F600": 2,  # grinning face: default emoji presentation
            "❤": 1,  # heart without VS16: text presentation
            "́": 0,  # combining acute
            "‍": 0,  # ZWJ
            "️": 0,  # VS16 on its own
            "\x07": 0,
            "\x1b": 0,
            "\x7f": 0,
            "\x9b": 0,  # C1 CSI
            "ᅡ": 0,  # Hangul medial vowel jamo
            "ᄒ": 2,  # Hangul leading consonant jamo
        }
        for ch, expected in cases.items():
            with self.subTest(code_point=f"U+{ord(ch):04X}"):
                self.assertEqual(char_width(ch), expected)


class CellWidthTests(unittest.TestCase):
    def test_hangul_counts_the_same_composed_or_decomposed(self) -> None:
        self.assertEqual(len(HANGUL_NFD), 6)  # what a macOS paste delivers: six jamo
        self.assertEqual(cell_width(HANGUL), 4)
        self.assertEqual(cell_width(HANGUL_NFD), 4)

    def test_emoji_clusters_are_two_cells(self) -> None:
        cases = {FAMILY: 2, HEART_EMOJI: 2, THUMBS_UP_TONED: 2, FLAG_KR: 2, "\U0001F600": 2}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(cell_width(text), expected)

    def test_combining_marks_add_nothing(self) -> None:
        self.assertEqual(cell_width("é"), 1)
        self.assertEqual(cell_width(Q_ACUTE * 3), 3)

    def test_controls_add_nothing(self) -> None:
        self.assertEqual(cell_width("a\x1b[31mb"), 6)  # ESC is 0; "[31m" is four printable cells
        self.assertEqual(cell_width("\t\r"), 0)

    def test_mixed_text(self) -> None:
        self.assertEqual(cell_width("a한\U0001F600"), 5)
        self.assertEqual(cell_width(""), 0)


class ClusterTests(unittest.TestCase):
    def test_clusters_keep_glyphs_whole(self) -> None:
        self.assertEqual(clusters(HANGUL_NFD), ["한", "글"])
        self.assertEqual(clusters(f"a{FAMILY}b"), ["a", FAMILY, "b"])
        self.assertEqual(clusters(f"{Q_ACUTE}x"), [Q_ACUTE, "x"])
        self.assertEqual(clusters(FLAG_KR + FLAG_KR), [FLAG_KR, FLAG_KR])
        self.assertEqual(clusters(THUMBS_UP_TONED), [THUMBS_UP_TONED])

    def test_a_control_never_absorbs_a_mark(self) -> None:
        self.assertEqual(clusters("\ń"), ["\n", "́"])


class KeptMeasureTests(unittest.TestCase):
    """Short non-ASCII text is measured once and kept, since every frame measures the same rules and rows again."""

    def test_a_kept_answer_is_handed_out_as_a_new_list_each_time(self) -> None:
        rule = "─" * 40
        first = clusters(rule)
        first.append("changed")
        self.assertEqual(clusters(rule), ["─"] * 40)
        self.assertEqual(clusters(HANGUL_NFD), ["한", "글"])  # kept answers are still the NFC clusters

    def test_long_text_is_measured_afresh_and_not_kept(self) -> None:
        from omh.tui import width

        long = "가" * (width._KEPT_TEXT_CHARS + 1)
        held = width._kept_clusters.cache_info().currsize
        self.assertEqual(len(clusters(long)), len(long))
        self.assertEqual(width._kept_clusters.cache_info().currsize, held)
        self.assertEqual(cell_width(long), 2 * len(long))


class AsciiTests(unittest.TestCase):
    """ASCII text skips normalizing and segmenting; each of its 128 code points must still measure as before."""

    def test_every_ascii_code_point(self) -> None:
        for code_point in range(128):
            ch = chr(code_point)
            cells = 0 if code_point < 0x20 or code_point == 0x7F else 1  # C0 controls and DEL draw nothing
            with self.subTest(code_point=code_point):
                self.assertEqual(clusters(ch), [ch])
                self.assertEqual(cluster_width(ch), cells)
                self.assertEqual(cell_width(ch), cells)
                self.assertEqual(char_width(ch), cells)

    def test_ascii_text_with_and_without_controls(self) -> None:
        every = "".join(map(chr, range(128)))
        self.assertEqual(clusters(every), list(every))
        self.assertEqual(cell_width(every), 95)
        self.assertEqual(cell_width("a\tb\x1bc\r\nd"), 4)
        self.assertEqual(cell_width("plain words"), 11)
        self.assertEqual(cluster_width("ab\x7f"), 2)

    def test_a_mark_after_ascii_still_joins_its_letter(self) -> None:
        self.assertEqual(clusters("e\u0301x"), ["é", "x"])
        self.assertEqual(clusters(Q_ACUTE + "x"), [Q_ACUTE, "x"])
        self.assertEqual(cell_width(Q_ACUTE + "x"), 2)


class TruncateTests(unittest.TestCase):
    def test_text_that_fits_is_returned_unchanged(self) -> None:
        self.assertEqual(truncate("abc", 3), "abc")
        self.assertEqual(truncate("한글", 4), "한글")

    def test_cut_text_ends_in_the_ellipsis(self) -> None:
        self.assertEqual(truncate("abcdef", 4), "abc…")
        self.assertEqual(truncate("한글입니다", 5), "한글…")

    def test_a_wide_glyph_is_dropped_whole_rather_than_split(self) -> None:
        # 3 cells left for text: "한" (2) fits, "글" would need 4.
        self.assertEqual(truncate("한글입니다", 4), "한…")
        self.assertEqual(cell_width(truncate("한글입니다", 4)), 3)

    def test_clusters_are_never_split(self) -> None:
        self.assertEqual(truncate(FAMILY * 3, 5), FAMILY * 2 + "…")
        self.assertEqual(truncate(FAMILY * 3, 4), FAMILY + "…")
        self.assertEqual(truncate(Q_ACUTE * 4, 3), Q_ACUTE * 2 + "…")

    def test_decomposed_input_comes_back_composed(self) -> None:
        self.assertEqual(truncate(HANGUL_NFD, 10), HANGUL)
        self.assertEqual(truncate(HANGUL_NFD * 2, 5), "한글…")

    def test_custom_and_empty_ellipsis(self) -> None:
        self.assertEqual(truncate("abcdef", 4, ellipsis=""), "abcd")
        self.assertEqual(truncate("abcdef", 5, ellipsis="..."), "ab...")
        self.assertEqual(truncate("한글한", 3, ellipsis=""), "한")

    def test_too_narrow_for_the_ellipsis_clips_without_it(self) -> None:
        self.assertEqual(truncate("abcdef", 2, ellipsis="..."), "ab")
        self.assertEqual(truncate("abc", 0), "")
        self.assertEqual(truncate("abc", -3), "")


class WrapTests(unittest.TestCase):
    def assertFits(self, rows: list[str], cells: int) -> None:
        for row in rows:
            self.assertLessEqual(cell_width(row), cells, row)

    def test_breaks_between_words(self) -> None:
        self.assertEqual(wrap("hello world foo", 11), ["hello world", "foo"])
        self.assertEqual(wrap("aaa bbb", 3), ["aaa", "bbb"])
        self.assertEqual(wrap("안녕하세요 반갑습니다", 12), ["안녕하세요", "반갑습니다"])

    def test_spaces_at_a_break_are_dropped(self) -> None:
        self.assertEqual(wrap("one   two", 4), ["one", "two"])

    def test_leading_indentation_and_trailing_spaces_that_fit_are_kept(self) -> None:
        self.assertEqual(wrap("  code here", 20), ["  code here"])
        self.assertEqual(wrap("a  ", 10), ["a  "])

    def test_long_words_are_hard_broken(self) -> None:
        self.assertEqual(wrap("abcdefghij", 4), ["abcd", "efgh", "ij"])
        self.assertEqual(wrap("go abcdefghij", 6), ["go", "abcdef", "ghij"])

    def test_a_long_path_breaks_after_a_slash_rather_than_mid_word(self) -> None:
        # Review B NIT 7: the /omh-model notice wrapped its store path as `…/routing/mo` / `del-chains.json`.
        path = "/Users/me/.omh/routing/model-chains.json"
        self.assertEqual(wrap(path, 30), ["/Users/me/.omh/routing/", "model-chains.json"])
        self.assertEqual(wrap(f"Cancelled; {path} was not changed.", 30),
                         ["Cancelled;", "/Users/me/.omh/routing/", "model-chains.json was not", "changed."])
        self.assertEqual(wrap("/abcdefghij", 4), ["/abc", "defg", "hij"])  # a slash first is no break

    def test_a_urls_scheme_is_never_left_alone_on_a_row(self) -> None:
        # The re-review: `https://` alone on a row. The `//` after a scheme is no place to break.
        self.assertEqual(wrap("https://exampleexample.org", 20), ["https://exampleexamp", "le.org"])
        self.assertEqual(wrap("https://ab.org/averylongsegment", 20), ["https://ab.org/", "averylongsegment"])
        self.assertEqual(wrap("ab/cd:/" + "/efghij", 7), ["ab/", "cd://ef", "ghij"])  # its first `/` ends the row
        self.assertFits(wrap("a/" + "b" * 20 + "/c", 6), 6)

    def test_wide_glyphs_are_never_split(self) -> None:
        self.assertEqual(wrap("한글한글한", 3), ["한", "글", "한", "글", "한"])
        self.assertEqual(wrap("한글한글한", 5), ["한글", "한글", "한"])
        self.assertFits(wrap("가나다라마바사아자차카타파하", 7), 7)

    def test_a_glyph_wider_than_the_width_gets_its_own_row(self) -> None:
        self.assertEqual(wrap("한a", 1), ["한", "a"])

    def test_clusters_are_never_split(self) -> None:
        self.assertEqual(wrap(FAMILY * 3, 5), [FAMILY * 2, FAMILY])
        self.assertEqual(wrap(Q_ACUTE * 3, 2), [Q_ACUTE * 2, Q_ACUTE])
        self.assertEqual(wrap(f"{FLAG_KR}{FLAG_KR}{FLAG_KR}", 3), [FLAG_KR, FLAG_KR, FLAG_KR])

    def test_newlines_are_hard_breaks(self) -> None:
        self.assertEqual(wrap("a\nb", 5), ["a", "b"])
        self.assertEqual(wrap("a\n\nb", 5), ["a", "", "b"])
        self.assertEqual(wrap("", 5), [""])

    def test_decomposed_input_comes_back_composed(self) -> None:
        self.assertEqual(wrap(f"{HANGUL_NFD} {HANGUL_NFD}", 4), [HANGUL, HANGUL])


class LineLevelTests(unittest.TestCase):
    def test_line_width_sums_its_spans(self) -> None:
        self.assertEqual(line_width((Span("abc", RED), Span(HANGUL_NFD, BOLD))), 7)
        self.assertEqual(line_width(()), 0)

    def test_truncate_line_keeps_span_styles(self) -> None:
        line = (Span("abc", RED), Span("한글", BOLD))
        self.assertEqual(truncate_line(line, 7), line)
        # The ellipsis takes the style of the text it replaces.
        self.assertEqual(truncate_line(line, 5), (Span("abc", RED), Span("…", BOLD)))
        self.assertEqual(truncate_line(line, 6), (Span("abc", RED), Span("한…", BOLD)))
        self.assertEqual(truncate_line(line, 4, ellipsis=""), (Span("abc", RED),))

    def test_wrap_line_keeps_span_styles_across_breaks(self) -> None:
        line = (Span("hello ", BOLD), Span("world wide", RED))
        self.assertEqual(
            wrap_line(line, 8),
            [(Span("hello", BOLD),), (Span("world", RED),), (Span("wide", RED),)],
        )

    def test_a_word_made_of_two_styles_is_not_broken_between_them(self) -> None:
        line = (Span("foo", BOLD), Span("bar", RED), Span(" x"))
        self.assertEqual(
            wrap_line(line, 6),
            [(Span("foo", BOLD), Span("bar", RED)), (Span("x"),)],
        )

    def test_wrap_line_never_splits_a_wide_glyph_inside_a_styled_span(self) -> None:
        rows = wrap_line((Span("ab", RED), Span("한글", BOLD)), 3)
        self.assertEqual(rows, [(Span("ab", RED),), (Span("한", BOLD),), (Span("글", BOLD),)])

    def test_empty_line_wraps_to_one_empty_row(self) -> None:
        self.assertEqual(wrap_line((), 5), [()])


class TruncateLeftTests(unittest.TestCase):
    def test_keeps_the_end_and_never_splits_a_wide_char(self) -> None:
        from omh.tui.width import cell_width, truncate_left

        self.assertEqual(truncate_left("/a/b/c/hello.py", 10), "…/hello.py")
        self.assertEqual(truncate_left("short", 10), "short")
        cut = truncate_left("/경로/한글/파일.py", 9)
        self.assertTrue(cut.endswith("파일.py"), cut)
        self.assertLessEqual(cell_width(cut), 9)
        self.assertEqual(truncate_left("abc", 0), "")


if __name__ == "__main__":
    unittest.main()
