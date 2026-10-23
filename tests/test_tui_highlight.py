"""Syntax colour for fenced code (`omh.tui.highlight`) and its line-by-line carry.

Tokens are checked against a theme whose five roles all differ, so a piece's
role reads straight off its style (the shipped palettes paint numbers in the
text colour, which would merge the two). Every expectation is a literal list
of (text, role) pieces, never a call back into the module.
"""

from __future__ import annotations

from dataclasses import replace
import random
import unittest

from _tui_lines import line_text
from omh.tui.highlight import LANGUAGES, highlight_line, highlights
from omh.tui.style import Span, Style
from omh.tui.theme import DEFAULT_THEME

THEME = replace(
    DEFAULT_THEME,
    text=Style(fg="#000001"),
    syntax_keyword=Style(fg="#000002"),
    syntax_string=Style(fg="#000003"),
    syntax_number=Style(fg="#000004"),
    syntax_comment=Style(fg="#000005"),
    code=Style(fg="#000006"),
)
ROLE = {
    THEME.text: "p",
    THEME.syntax_keyword: "k",
    THEME.syntax_string: "s",
    THEME.syntax_number: "n",
    THEME.syntax_comment: "c",
    THEME.code: "code",
}
OPENERS = frozenset({"", '"""', "'''", "`", "/*"})


def pieces(text: str, lang: str, carry: str = "") -> tuple[list[tuple[str, str]], str]:
    spans, carry = highlight_line(text, lang, carry, THEME)
    return [(span.text, ROLE[span.style]) for span in spans], carry


def lines(sources: list[str], lang: str) -> list[list[tuple[str, str]]]:
    """Each source line's pieces, threading the carry the way MarkdownState does."""
    carry = ""
    result = []
    for source in sources:
        line, carry = pieces(source, lang, carry)
        result.append(line)
    return result


class LanguageTests(unittest.TestCase):
    def test_python(self) -> None:
        self.assertEqual(
            pieces("def f(x=1):  # doc", "python"),
            ([("def", "k"), (" f(x=", "p"), ("1", "n"), ("):  ", "p"), ("# doc", "c")], ""),
        )
        self.assertEqual(
            pieces("return \"a\\\"b\" + 'c#'", "py"),
            ([("return", "k"), (" ", "p"), ('"a\\"b"', "s"), (" + ", "p"), ("'c#'", "s")], ""),
        )
        self.assertEqual(
            lines(['s = """first', "middle # not a comment", 'end""" + 1', "x = '''a'''"], "python"),
            [
                [("s = ", "p"), ('"""first', "s")],
                [("middle # not a comment", "s")],
                [('end"""', "s"), (" + ", "p"), ("1", "n")],
                [("x = ", "p"), ("'''a'''", "s")],
            ],
        )

    def test_javascript_and_typescript(self) -> None:
        self.assertEqual(
            lines(["const t = `a ${b}", "c`; // done", "/* start", "still */ let y = 'z'"], "js"),
            [
                [("const", "k"), (" t = ", "p"), ("`a ${b}", "s")],
                [("c`", "s"), ("; ", "p"), ("// done", "c")],
                [("/* start", "c")],
                [("still */", "c"), (" ", "p"), ("let", "k"), (" y = ", "p"), ("'z'", "s")],
            ],
        )
        self.assertEqual(pieces("promise.catch(e).finally(f)", "javascript"), ([("promise.catch(e).finally(f)", "p")], ""))
        self.assertEqual(
            pieces("export interface Foo { n: 2 }", "tsx"),
            ([("export", "k"), (" ", "p"), ("interface", "k"), (" Foo { n: ", "p"), ("2", "n"), (" }", "p")], ""),
        )

    def test_json(self) -> None:
        self.assertEqual(
            pieces('{"key": [true, null, -1.5e3], "s": "x\\"y"}', "json")[0],
            [
                ("{", "p"),
                ('"key"', "s"),
                (": [", "p"),
                ("true", "k"),
                (", ", "p"),
                ("null", "k"),
                (", -", "p"),
                ("1.5e3", "n"),
                ("], ", "p"),
                ('"s"', "s"),
                (": ", "p"),
                ('"x\\"y"', "s"),
                ("}", "p"),
            ],
        )

    def test_shell(self) -> None:
        self.assertEqual(
            pieces('echo "$HOME" a#b # note', "bash")[0],
            [("echo ", "p"), ('"$HOME"', "s"), (" a#b ", "p"), ("# note", "c")],
        )
        self.assertEqual(
            pieces("if [ $# -gt 0 ]; then", "sh")[0],
            [("if", "k"), (" [ $# -gt ", "p"), ("0", "n"), (" ]; ", "p"), ("then", "k")],
        )
        self.assertEqual(pieces("echo 'no \\' escape", "zsh")[0], [("echo ", "p"), ("'no \\'", "s"), (" escape", "p")])
        self.assertEqual(pieces('echo "open', "shell"), ([("echo ", "p"), ('"open', "s")], ""))

    def test_go(self) -> None:
        self.assertEqual(
            lines(["func main() {", "\ts := `raw \\n", 'end` + "x" // c'], "go"),
            [
                [("func", "k"), (" main() {", "p")],
                [("\ts := ", "p"), ("`raw \\n", "s")],
                [("end`", "s"), (" + ", "p"), ('"x"', "s"), (" ", "p"), ("// c", "c")],
            ],
        )

    def test_rust(self) -> None:
        self.assertEqual(
            pieces("fn f<'a>(x: &'a str) -> char { 'x' }", "rust")[0],
            [("fn", "k"), (" f<'a>(x: &'a str) -> char { ", "p"), ("'x'", "s"), (" }", "p")],
        )
        self.assertEqual(
            pieces("let c = '\\u{1F600}'; let q = '\\'';", "rs")[0],
            [("let", "k"), (" c = ", "p"), ("'\\u{1F600}'", "s"), ("; ", "p"), ("let", "k"), (" q = ", "p"),
             ("'\\''", "s"), (";", "p")],
        )
        self.assertEqual(lines(["/* a", "b */ 7"], "rust"), [[("/* a", "c")], [("b */", "c"), (" ", "p"), ("7", "n")]])

    def test_yaml(self) -> None:
        self.assertEqual(
            pieces("name: \"Don't stop\"  # quoted", "yaml")[0],
            [("name: ", "p"), ('"Don\'t stop"', "s"), ("  ", "p"), ("# quoted", "c")],
        )
        self.assertEqual(pieces("title: Don't stop", "yml")[0], [("title: Don't stop", "p")])
        self.assertEqual(pieces("enabled: true", "yaml")[0], [("enabled: ", "p"), ("true", "k")])
        self.assertEqual(pieces("url: http://x#frag", "yaml")[0], [("url: http://x#frag", "p")])
        self.assertEqual(pieces("- 'it''s' # c", "yaml")[0], [("- ", "p"), ("'it''s'", "s"), (" ", "p"), ("# c", "c")])

    def test_generic_languages_colour_only_strings_numbers_and_comments(self) -> None:
        self.assertEqual(
            pieces("int x = 0x1F; // hex", "c")[0], [("int x = ", "p"), ("0x1F", "n"), ("; ", "p"), ("// hex", "c")]
        )
        self.assertEqual(
            pieces("SELECT 'a' FROM t -- note", "sql")[0],
            [("SELECT ", "p"), ("'a'", "s"), (" FROM t ", "p"), ("-- note", "c")],
        )
        self.assertEqual(pieces('puts "hi" # greet', "ruby")[0], [("puts ", "p"), ('"hi"', "s"), (" ", "p"), ("# greet", "c")])
        self.assertEqual(lines(["/* a", "b */ return 1;"], "java")[1], [("b */", "c"), (" return ", "p"), ("1", "n"), (";", "p")])

    def test_an_unknown_language_is_one_plain_code_span_and_never_carries(self) -> None:
        for lang in ("", "brainfuck", "text", "{python}"):
            with self.subTest(lang=lang):
                self.assertFalse(highlights(lang))
                self.assertEqual(highlight_line('x = "1" # c /*', lang, "", THEME), ([Span('x = "1" # c /*', THEME.code)], ""))
                self.assertEqual(highlight_line("", lang, "", THEME), ([], ""))

    def test_the_named_aliases_are_all_known(self) -> None:
        for lang in ("python", "javascript", "typescript", "js", "ts", "jsx", "tsx", "json", "bash", "sh", "shell",
                     "zsh", "go", "rust", "yaml"):
            with self.subTest(lang=lang):
                self.assertTrue(highlights(lang))


class RobustnessTests(unittest.TestCase):
    def test_random_lines_never_raise_and_pieces_cover_the_line_exactly(self) -> None:
        generator = random.Random(2086)
        alphabet = ['"', "'", "`", '"""', "'''", "/*", "*/", "//", "#", "--", "\\", " ", "\t", "a", "def", "if",
                    "true", "0", "1.5", "x", ".", "$", "{", "한", "\U0001F30F", "́", ":", "-", "\x00"]
        languages = sorted(LANGUAGES) + ["unknown"]
        for _ in range(500):
            text = "".join(generator.choice(alphabet) for _ in range(generator.randrange(0, 30)))
            lang = generator.choice(languages)
            carry = generator.choice(sorted(OPENERS))
            with self.subTest(text=text, lang=lang, carry=carry):
                spans, after = highlight_line(text, lang, carry, THEME)
                self.assertEqual(line_text(spans), text)
                self.assertTrue(all(span.text for span in spans))
                self.assertIn(after, OPENERS)


if __name__ == "__main__":
    unittest.main()
