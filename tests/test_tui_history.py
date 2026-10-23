"""Hermes' input history file and the Ctrl+R search over the composer's history (`omh.tui.history`).

The file is `$HERMES_HOME/.hermes_history`, the one `hermes --tui` reads and
writes (Hermes' ui-tui `lib/history.ts`): an entry is a `# <time>` line and
one `+`-prefixed line per line of it. Every case works in its own temporary
HERMES_HOME; nothing here reads or writes the person's own.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import unittest
from unittest.mock import patch

from _test_temp_root import make_test_tempdir
from _tui_gateway_replay import Scenario, spans
from test_tui_app import NO_TERMIOS, AppTestCase, Step, startup_answers
from omh.tui.history import (
    HISTORY_FILE,
    HISTORY_READ_BYTES,
    SEARCH_ACCEPT,
    SEARCH_CANCEL,
    HistorySearch,
    append_entry,
    history_path,
    load_history,
    parse_entries,
)
from omh.tui.keys import BACKSPACE, CHAR, ENTER, ESCAPE, Key, Paste
from omh.tui.theme import DEFAULT_THEME
from _tui_lines import lines_text

POSIX = os.name == "posix"
# What Hermes' Ink app appends for `ls -la` and then a two-line prompt (appendFileSync in lib/history.ts). Ink writes
# LF only, on every platform, so the fixtures write these bytes (`write_ink`), never text-mode lines that Windows
# would turn into CRLF, and read the file's bytes back (`file_text`) so a CRLF on disk shows.
INK_FILE = "\n# 2026-10-14 09:00:00.123\n+ls -la\n\n# 2026-10-14 09:01:00.456\n+first line\n+second line\n"


def write_ink(path: Path, text: str = INK_FILE) -> None:
    path.write_bytes(text.encode("utf-8"))


def file_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


class PathTests(unittest.TestCase):
    def test_the_file_is_under_hermes_home_or_the_default_home(self) -> None:
        self.assertEqual(history_path({"HERMES_HOME": "/x/h"}), Path("/x/h") / HISTORY_FILE)
        self.assertEqual(history_path({"HOME": "/home/me"}), Path("/home/me") / ".hermes" / HISTORY_FILE)
        self.assertIsNone(history_path({}))


class ReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(make_test_tempdir("omh-tui-history-")))
        self.path = self.home / HISTORY_FILE

    def test_entries_are_read_in_inks_format(self) -> None:
        self.assertEqual(parse_entries(INK_FILE), ["ls -la", "first line\nsecond line"])
        self.assertEqual(parse_entries("+a\n+b"), ["a\nb"])  # a file cut before its last newline
        self.assertEqual(parse_entries("# only a header\n\n"), [])

    def test_a_missing_or_unreadable_file_is_no_history(self) -> None:
        self.assertEqual(load_history(self.path), [])
        self.path.mkdir()
        self.assertEqual(load_history(self.path), [])

    @unittest.skipUnless(POSIX and hasattr(os, "mkfifo"), "FIFOs are POSIX's")
    def test_a_fifo_in_its_place_is_no_history_and_never_blocks(self) -> None:
        os.mkfifo(self.path)
        self.assertEqual(load_history(self.path), [])  # a plain open would wait for a writer forever

    @unittest.skipUnless(POSIX and hasattr(os, "O_NOFOLLOW"), "O_NOFOLLOW is POSIX's")
    def test_a_symlink_in_its_place_is_not_read_as_it_is_not_appended_to(self) -> None:
        target = self.home / "elsewhere"
        write_ink(target)
        self.path.symlink_to(target)
        self.assertEqual(load_history(self.path), [])

    def test_only_the_newest_entries_are_kept(self) -> None:
        write_ink(self.path, "".join(f"\n# t\n+prompt {number}\n" for number in range(30)))
        self.assertEqual(load_history(self.path, limit=3), ["prompt 27", "prompt 28", "prompt 29"])

    def test_a_large_file_is_read_from_its_last_bytes_and_a_cut_entry_is_dropped(self) -> None:
        big = "".join(f"\n# t\n+line a {number}\n+line b {number}\n" for number in range(40_000))
        write_ink(self.path, big)
        self.assertGreater(len(big), HISTORY_READ_BYTES)
        entries = load_history(self.path, limit=100_000)
        self.assertEqual(entries[-1], "line a 39999\nline b 39999")
        self.assertTrue(all(entry.startswith("line a ") and "\nline b " in entry for entry in entries))
        self.assertLess(sum(len(entry) for entry in entries), HISTORY_READ_BYTES)

    def test_a_crlf_file_reads_as_its_lf_twin(self) -> None:
        # Hand-edited on Windows, or written by an OMH TUI before its appends were binary: each line's CR goes.
        write_ink(self.path, INK_FILE.replace("\n", "\r\n"))
        self.assertEqual(load_history(self.path), ["ls -la", "first line\nsecond line"])
        self.assertEqual(parse_entries("+a\r\n+b\r"), ["a\nb"])
        self.assertEqual(parse_entries("+a\r\r\n"), ["a\r"])  # one CR per line, the one a CRLF ending brings


class AppendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(make_test_tempdir("omh-tui-history-")))
        self.path = self.home / HISTORY_FILE

    def test_an_entry_is_appended_in_inks_format_and_reads_back(self) -> None:
        write_ink(self.path)
        self.assertTrue(append_entry(self.path, "  fix the test\nthen run it  "))
        text = file_text(self.path)  # the bytes: LF only, as Ink writes, on every platform
        self.assertTrue(text.startswith(INK_FILE))  # what was there is untouched
        added = text[len(INK_FILE):]
        self.assertRegex(added, r"^\n# \d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}\n\+fix the test\n\+then run it\n$")
        self.assertEqual(load_history(self.path)[-1], "fix the test\nthen run it")

    def test_a_blank_entry_writes_nothing(self) -> None:
        self.assertFalse(append_entry(self.path, " \n "))
        self.assertFalse(self.path.exists())

    @unittest.skipUnless(POSIX, "file modes are POSIX's")
    def test_a_new_file_is_the_owners_alone(self) -> None:
        append_entry(self.path, "first")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    @unittest.skipUnless(POSIX and hasattr(os, "O_NOFOLLOW"), "O_NOFOLLOW is POSIX's")
    def test_a_symlink_in_its_place_is_not_followed(self) -> None:
        target = self.home / "elsewhere"
        target.write_text("untouched", encoding="utf-8")
        self.path.symlink_to(target)
        self.assertFalse(append_entry(self.path, "a prompt"))
        self.assertEqual(target.read_text(encoding="utf-8"), "untouched")

    def test_appends_from_two_writers_interleave_as_whole_entries(self) -> None:
        # Hermes' own TUI appends with appendFileSync (O_APPEND) too: entries land whole, in the order written.
        append_entry(self.path, "from omh 1")
        with self.path.open("ab") as other:
            other.write(b"\n# 2026-10-14 09:00:00.000\n+from ink\n")
        append_entry(self.path, "from omh 2")
        self.assertEqual(load_history(self.path), ["from omh 1", "from ink", "from omh 2"])

    def test_both_opens_ask_for_binary_where_the_platform_has_o_binary(self) -> None:
        # Windows' text-mode descriptor turns each LF written into CRLF; only O_BINARY keeps the file as Ink's.
        # POSIX has no O_BINARY, so a stand-in flag shows what Linux and macOS CI cannot see.
        stand_in = 1 << 30
        seen: list[int] = []
        real_open = os.open

        def recording(path: object, flags: int, *rest: int) -> int:
            seen.append(flags)
            return real_open(path, flags & ~stand_in, *rest)

        with patch.object(os, "O_BINARY", stand_in, create=True), patch.object(os, "open", recording):
            self.assertTrue(append_entry(self.path, "a prompt"))
            self.assertEqual(load_history(self.path), ["a prompt"])
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(flags & stand_in for flags in seen), [hex(flags) for flags in seen])

    def test_a_home_that_does_not_exist_is_not_created(self) -> None:
        missing = self.home / "no-such-home" / HISTORY_FILE
        self.assertFalse(append_entry(missing, "a prompt"))
        self.assertFalse(missing.parent.exists())


def key(char: str, **modifiers: bool) -> Key:
    return Key(CHAR, char, **modifiers)


class SearchTests(unittest.TestCase):
    ENTRIES = ["git status", "run the tests", "git push", "fix the flaky test\nand rerun it"]

    def search(self, original: str = "half typed") -> HistorySearch:
        return HistorySearch(self.ENTRIES, original)

    def typed(self, search: HistorySearch, text: str) -> None:
        for char in text:
            self.assertIsNone(search.handle_key(key(char)))

    def test_typing_finds_the_newest_entry_that_holds_it_ignoring_case(self) -> None:
        search = self.search()
        self.assertEqual(search.current(), "fix the flaky test\nand rerun it")  # nothing typed: the newest
        self.typed(search, "GIT")
        self.assertEqual(search.current(), "git push")

    def test_ctrl_r_goes_to_older_matches_and_stops_at_the_oldest(self) -> None:
        search = self.search()
        self.typed(search, "git")
        search.handle_key(key("r", ctrl=True))
        self.assertEqual(search.current(), "git status")
        search.handle_key(key("r", ctrl=True))
        self.assertEqual(search.current(), "git status")
        self.typed(search, " s")  # a new character starts from the newest again
        self.assertEqual(search.current(), "git status")

    def test_enter_takes_the_match_and_esc_or_ctrl_c_cancels(self) -> None:
        search = self.search()
        self.typed(search, "tests")
        self.assertEqual(search.handle_key(Key(ENTER)), SEARCH_ACCEPT)
        self.assertEqual(search.current(), "run the tests")
        for cancel in (Key(ESCAPE), key("c", ctrl=True), key("g", ctrl=True)):
            with self.subTest(cancel=cancel):
                self.assertEqual(self.search().handle_key(cancel), SEARCH_CANCEL)

    def test_backspace_takes_a_character_back_and_on_an_empty_query_cancels(self) -> None:
        search = self.search()
        self.typed(search, "gitx")
        self.assertIsNone(search.current())
        self.assertIsNone(search.handle_key(Key(BACKSPACE)))
        self.assertEqual(search.current(), "git push")
        search.handle_key(Key(BACKSPACE))
        search.handle_key(Key(BACKSPACE))
        search.handle_key(Key(BACKSPACE))
        self.assertEqual(search.query, "")
        self.assertEqual(search.handle_key(Key(BACKSPACE)), SEARCH_CANCEL)

    def test_a_paste_searches_for_its_first_line(self) -> None:
        search = self.search()
        search.handle_key(Paste("flaky\nignored"))
        self.assertEqual(search.query, "flaky")

    def test_the_slot_shows_the_match_then_the_query(self) -> None:
        search = self.search()
        self.typed(search, "git")
        rows = lines_text(search.lines(80, DEFAULT_THEME, 4))
        self.assertEqual(rows[0].rstrip(), "❯ git push  1/2")
        self.assertEqual(rows[1].rstrip(), "⌕ search history: git")  # its keys are the info line's
        empty = HistorySearch(self.ENTRIES, "")
        self.typed(empty, "nothing like it")
        self.assertEqual(lines_text(empty.lines(80, DEFAULT_THEME, 4))[0], "  no earlier prompt holds it")


class AppHistoryTests(unittest.TestCase):
    """The composer's history in the app: Hermes' file read at start, each line the person sends appended."""

    def setUp(self) -> None:
        from test_tui_modes import make_app

        self.home = Path(self.enterContext(make_test_tempdir("omh-tui-history-")))
        self.path = self.home / HISTORY_FILE
        write_ink(self.path)
        self.app, self.client = make_app(environ={"HERMES_HOME": str(self.home)})

    def send(self, text: str) -> None:
        from test_tui_modes import type_text

        type_text(self.app, text)
        self.app._on_input(Key(ENTER))

    def test_up_walks_hermes_own_history_from_the_start(self) -> None:
        from omh.tui.keys import UP

        self.app._on_input(Key(UP))
        self.assertEqual(self.app.composer.text, "first line\nsecond line")
        self.app._on_input(Key(UP))  # the caret is on the first row now; Up goes further back
        self.app._on_input(Key(UP))
        self.assertEqual(self.app.composer.text, "ls -la")

    def test_each_line_sent_is_appended_once_and_answers_and_secrets_never(self) -> None:
        from omh.tui.gateway import ServerRequest

        self.send("a new prompt")
        self.send("a new prompt")  # the same again: the newest entry already says it
        self.send("/usage")
        self.app._on_server_request(ServerRequest("srq-1", "clarify", {"questions": [{"qid": "q1", "question": "Which?"}]}))
        self.app.slot_opened_at = -10.0  # past the arm delay
        self.send("the answer to Hermes")
        self.app._on_server_request(ServerRequest("srq-2", "secret", {"env_var": "TOKEN"}))
        self.app.slot_opened_at = -10.0
        self.send("hunter2")
        self.assertEqual(load_history(self.path), ["ls -la", "first line\nsecond line", "a new prompt", "/usage"])
        self.assertNotIn("hunter2", file_text(self.path))

    def test_a_draft_cleared_with_esc_esc_stays_out_of_the_file(self) -> None:
        from test_tui_modes import type_text

        type_text(self.app, "a draft never sent")
        self.app._on_input(Key(ESCAPE))
        self.app._on_input(Key(ESCAPE))
        self.assertEqual(self.app.composer.history[-1], "a draft never sent")  # Up still has it
        self.assertEqual(file_text(self.path), INK_FILE)

    def test_what_the_tell_row_field_held_reaches_the_file_only_when_sent(self) -> None:
        from test_tui_choice_ui import armed, press
        from test_tui_modes import type_text
        from test_tui_questions import TELL_APPROVAL
        from omh.tui.keys import DOWN

        def tell(text: str, key: Key) -> None:
            self.app._on_server_request(TELL_APPROVAL)
            armed(self.app)
            press(self.app, *[Key(DOWN)] * 3, Key(ENTER))
            type_text(self.app, text)
            self.app._on_input(key)

        tell("half a thought", Key(ESCAPE))  # left unsent: this session's history only
        self.assertEqual(self.app.composer.history[-1], "half a thought")
        self.assertEqual(file_text(self.path), INK_FILE)
        tell("use ./tmp instead", Key(ENTER))  # sent from the composer, like any line
        self.assertEqual(load_history(self.path)[-1], "use ./tmp instead")
        self.assertNotIn("half a thought", file_text(self.path))

    def test_ctrl_r_searches_and_enter_takes_the_match_into_the_composer(self) -> None:
        from test_tui_modes import type_text

        type_text(self.app, "half")
        self.app._on_input(key("r", ctrl=True))
        self.assertIsNotNone(self.app.search)
        type_text(self.app, "ls")
        rows = lines_text(self.app._slot_lines(80, 24))
        self.assertEqual(rows[-1].rstrip(), "⌕ search history: ls", rows)  # its keys are on the info line
        self.assertEqual(self.app.composer.text, "half")  # untouched while searching
        self.app._on_input(Key(ENTER))
        self.assertIsNone(self.app.search)
        self.assertEqual(self.app.composer.text, "ls -la")
        self.assertEqual(self.client.calls, [])  # Enter took the match; it sent nothing

    def test_esc_leaves_the_search_and_the_composer_as_it_was(self) -> None:
        from test_tui_modes import type_text

        type_text(self.app, "half")
        self.app._on_input(key("r", ctrl=True))
        type_text(self.app, "first")
        self.app._on_input(Key(ESCAPE))
        self.assertIsNone(self.app.search)
        self.assertEqual(self.app.composer.text, "half")

    def test_without_a_home_nothing_is_read_or_written(self) -> None:
        from test_tui_modes import make_app, type_text

        app, _client = make_app(environ={})
        self.assertEqual(app.composer.history, [])
        type_text(app, "a prompt")
        app._on_input(Key(ENTER))
        self.assertEqual(app.composer.history, ["a prompt"])  # memory only


@unittest.skipIf(NO_TERMIOS, "the OMH TUI needs termios")
class ReplayHistoryTests(AppTestCase):
    """In the real loop over the replay gateway: the prompt sent is in the file under the launch's HERMES_HOME."""

    def test_a_prompt_sent_lands_in_hermes_history_under_hermes_home(self) -> None:
        run = self.prepare(Scenario("session", spans("session", "startup", "create", "plain_turn"), answers=startup_answers()))
        prompt = "Reply with exactly the word: ok"
        history = Path(run.environ["HERMES_HOME"]) / HISTORY_FILE
        write_ink(history)
        run.terminal.script(
            self.submit(run, prompt),
            Step(b"\x04", when=lambda: "later." in run.terminal.history() and run.terminal.idle(), label="the reply"),
        )
        self.run_main(run)
        self.assert_clean_run(run)
        self.assertEqual(load_history(history), ["ls -la", "first line\nsecond line", prompt])


if __name__ == "__main__":
    unittest.main()
