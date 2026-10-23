"""Memory evidence: what OMH memory did, counted from structured local records.

The fixture state.db has Hermes' own shape for the columns the reader uses:
assistant rows carry ``tool_calls`` (call id, function name, JSON arguments)
and tool rows carry ``tool_name``, ``tool_call_id`` and the result JSON. The
fixture OMH store holds records of several sources and the two journals the
memory provider appends. Every expected value below is a hand count of those
rows, and every message carries a canary phrase that must never reach output.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from tempfile import TemporaryDirectory
import unittest

from _cli_harness import run_cli
from omh.paths import OmhPaths
from omh.quality.memory_evidence import (
    MEMORY_EVIDENCE_SCHEMA_VERSION,
    MemoryEvidenceError,
    build_memory_evidence,
    format_memory_evidence_summary,
    memory_evidence_headline,
)
from omh.commands.quality_evidence import _memory_evidence_exit_code
from omh.plugin_bundle.omh.memory_prefetch_receipt import prefetch_receipt_path
from omh.plugin_bundle.omh.memory_provider import JOURNAL_LIMIT


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400.0
IN_WINDOW = NOW - 2 * DAY
OUT_OF_WINDOW = NOW - 9 * DAY
CANARY = "CANARY-private-fact-7f3a"


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _call(call_id: str, name: str, arguments: dict) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def _write_state_db(home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "state.db"
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL, last_activity_at REAL, "
            "ended_at REAL, archived INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0)"
        )
        connection.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, "
            "tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO sessions (id, source, started_at, last_activity_at) VALUES (?, ?, ?, ?)",
            [
                ("tui1", "tui", IN_WINDOW, IN_WINDOW),
                ("cli1", "cli", IN_WINDOW, IN_WINDOW),
                ("bare", None, IN_WINDOW, IN_WINDOW),
                ("old", "tui", OUT_OF_WINDOW, OUT_OF_WINDOW),
            ],
        )
        remembered = {"status": "remembered", "action": "capture", "display_line": f"📌 OMH remembered: {CANARY}"}
        rows = [
            # tui1: capture remembered (re-persisted by a compaction), demote demoted, a native add.
            (1, "tui1", "user", f"remember {CANARY}", None, None, None, IN_WINDOW),
            (2, "tui1", "assistant", "", None, json.dumps([_call("c1", "omh_memory", {"action": "capture", "summary": CANARY})]), None, IN_WINDOW),
            (3, "tui1", "tool", json.dumps(remembered), "c1", None, "omh_memory", IN_WINDOW),
            # The compaction copies differ: truncated arguments and a text result. The first row decides.
            (4, "tui1", "assistant", "", None, json.dumps([{"id": "c1", "function": {"name": "omh_memory", "arguments": '{"action": "sta'}}]), None, IN_WINDOW),
            (5, "tui1", "tool", "[omh_memory] result truncated", "c1", None, "omh_memory", IN_WINDOW),
            (6, "tui1", "assistant", "", None, json.dumps([_call("c2", "omh_memory", {"action": "demote", "entry_sha256": "ab"})]), None, IN_WINDOW),
            (7, "tui1", "tool", json.dumps({"status": "demoted", "action": "demote", "display_line": CANARY}), "c2", None, "omh_memory", IN_WINDOW),
            (8, "tui1", "assistant", "", None, json.dumps([_call("c3", "memory", {"action": "remove", "target": "memory", "old_text": CANARY})]), None, IN_WINDOW),
            (9, "tui1", "tool", json.dumps({"success": True, "message": CANARY}), "c3", None, "memory", IN_WINDOW),
            # cli1: capture already_remembered (no write), status (no status field), a batched native call that failed,
            # and an unrelated tool.
            (10, "cli1", "assistant", "", None, json.dumps([_call("c4", "omh_memory", {"action": "capture", "summary": CANARY}), _call("c5", "read_file", {"path": CANARY})]), None, IN_WINDOW),
            (11, "cli1", "tool", json.dumps({"status": "already_remembered", "action": "capture"}), "c4", None, "omh_memory", IN_WINDOW),
            (12, "cli1", "tool", json.dumps({"content": CANARY}), "c5", None, "read_file", IN_WINDOW),
            (13, "cli1", "tool", json.dumps({"status": "ok", "action": "status"}), "c6", None, "omh_memory", IN_WINDOW),
            (14, "cli1", "assistant", "", None, json.dumps([_call("c7", "memory", {"operations": [{"action": "replace"}, {"action": "add"}], "target": "user"})]), None, IN_WINDOW),
            (15, "cli1", "tool", json.dumps({"success": False, "error": CANARY}), "c7", None, "memory", IN_WINDOW),
            # bare: a pending-review capture (a write) and a native add whose result was compacted to text.
            (16, "bare", "tool", json.dumps({"status": "pending_review", "action": "capture"}), "c8", None, "omh_memory", IN_WINDOW),
            # A known action in mixed case still counts under its label.
            (17, "bare", "assistant", "", None, json.dumps([_call("c9", "memory", {"action": "ADD", "content": CANARY})]), None, IN_WINDOW),
            (18, "bare", "tool", f"[memory] {CANARY}", "c9", None, "memory", IN_WINDOW),
            # A call with a label that is not an identifier, and one with no result row.
            (19, "bare", "tool", json.dumps({"status": f"weird {CANARY}", "action": "capture"}), "c10", None, "omh_memory", IN_WINDOW),
            (20, "bare", "assistant", "", None, json.dumps([_call("c11", "omh_memory", {"action": "demote"})]), None, IN_WINDOW),
            # Hermes' generic wrapper: an omh_memory call with no tool row, a native add with its result,
            # a one-entry calls list, a native action that is not one of the tool's own, and a refused
            # multi-entry batch that is no call at all.
            (23, "bare", "assistant", "", None, json.dumps([_call("c14", "tool_call", {"name": "omh_memory", "arguments": {"action": "capture", "summary": CANARY}})]), None, IN_WINDOW),
            (24, "bare", "assistant", "", None, json.dumps([_call("c15", "tool_call", {"name": "memory", "arguments": json.dumps({"action": "add", "content": CANARY})})]), None, IN_WINDOW),
            (25, "bare", "tool", json.dumps({"success": True}), "c15", None, "memory", IN_WINDOW),
            (26, "bare", "assistant", "", None, json.dumps([_call("c16", "tool_call", {"calls": [{"name": "memory", "arguments": {"action": "replace", "old_text": CANARY}}]})]), None, IN_WINDOW),
            (27, "bare", "tool", json.dumps({"success": True}), "c16", None, "memory", IN_WINDOW),
            (28, "bare", "assistant", "", None, json.dumps([_call("c17", "memory", {"action": CANARY.lower(), "content": CANARY})]), None, IN_WINDOW),
            (29, "bare", "assistant", "", None, json.dumps([_call("c18", "tool_call", {"calls": [{"name": "memory", "arguments": {"action": "add"}}, {"name": "omh_memory", "arguments": {"action": "capture"}}]})]), None, IN_WINDOW),
            # A compaction copy of c3's assistant row with truncated arguments: the first row by id decides.
            (30, "tui1", "assistant", "", None, json.dumps([{"id": "c3", "function": {"name": "memory", "arguments": '{"action": "rem'}}]), None, IN_WINDOW),
            # A demote that staged a candidate is a write.
            (31, "cli1", "tool", json.dumps({"status": "pending_review", "action": "demote"}), "c19", None, "omh_memory", IN_WINDOW),
            # A one-entry wrapper Hermes refused: its tool row is named tool_call and the call never ran.
            (32, "bare", "assistant", "", None, json.dumps([_call("c20", "tool_call", {"name": "omh_memory", "arguments": {"action": "capture"}})]), None, IN_WINDOW),
            (33, "bare", "tool", json.dumps({"error": "tool_call refused"}), "c20", None, "tool_call", IN_WINDOW),
            # old: outside the window.
            (21, "old", "tool", json.dumps(remembered), "c12", None, "omh_memory", OUT_OF_WINDOW),
            (22, "old", "tool", json.dumps({"success": True}), "c13", None, "memory", OUT_OF_WINDOW),
        ]
        connection.executemany(
            "INSERT INTO messages (id, session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    connection.close()
    return path


def _record(record_id: str, source: str, state: str, created_at: float) -> dict:
    return {
        "schema_version": "project_memory_record/v2",
        "record_id": record_id,
        "summary": CANARY,
        "source": source,
        "admission": {"state": state, "review_id": f"rev_{record_id}"},
        "created_at": _iso(created_at),
    }


def _write_store(omh_home: Path) -> None:
    memory = omh_home / "memory"
    records = memory / "records"
    records.mkdir(parents=True)
    for record in (
        _record("r1", "hermes_model", "auto_approved", IN_WINDOW),
        _record("r2", "hermes_model", "auto_approved", OUT_OF_WINDOW),
        _record("r3", "hermes_demotion", "auto_approved", IN_WINDOW),
        _record("r4", "cli", "approved", OUT_OF_WINDOW),
    ):
        (records / f"{record['record_id']}.json").write_text(json.dumps(record), encoding="utf-8")
    consolidation = [
        {"raised_at": _iso(IN_WINDOW), "reasons": ["headroom_below_floor:45<=300", "headroom_below_floor:USER.md:2<=200", "duplicate_records:2"]},
        {"raised_at": _iso(IN_WINDOW), "reasons": ["session_ending_with_unconsolidated_turns:1"]},
        {"raised_at": _iso(OUT_OF_WINDOW), "reasons": ["duplicate_records:1"]},
    ]
    (memory / "consolidation.jsonl").write_text("".join(json.dumps(row) + "\n" for row in consolidation), encoding="utf-8")
    writes = [
        {"observed_at": _iso(IN_WINDOW), "action": "remove", "removed_entry_demoted": True},
        {"observed_at": _iso(IN_WINDOW), "action": "remove", "removed_entry_demoted": True},
        {"observed_at": _iso(IN_WINDOW), "action": "remove", "removed_entry_demoted": False},
        {"observed_at": _iso(IN_WINDOW), "action": "remove"},
        {"observed_at": _iso(IN_WINDOW), "action": "add"},
        {"observed_at": _iso(OUT_OF_WINDOW), "action": "remove", "removed_entry_demoted": True},
    ]
    (memory / "write_journal.jsonl").write_text("".join(json.dumps(row) + "\n" for row in writes), encoding="utf-8")


def _write_native(hermes_home: Path) -> None:
    memories = hermes_home / "memories"
    memories.mkdir(parents=True, exist_ok=True)
    # Bytes, not text: write_text would emit CRLF on Windows and split no entries.
    (memories / "MEMORY.md").write_bytes("alpha\n§\nbeta".encode("utf-8"))


def _home(tmp: str) -> OmhPaths:
    root = Path(tmp)
    paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
    _write_state_db(paths.hermes_home)
    _write_store(paths.omh_home)
    _write_native(paths.hermes_home)
    return paths


class MemoryEvidenceCountTests(unittest.TestCase):
    def test_state_db_counts_are_the_hand_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            payload = build_memory_evidence(_home(tmp), now=NOW)

        state_db = payload["state_db"]
        self.assertEqual(state_db["read_state"], "read")
        self.assertEqual(
            state_db["omh_memory"],
            {
                "calls": 9,  # c1 once despite the compaction, c2, c4, c6, c8, c10, c11, c19, wrapped c14; c20 was refused
                "by_action_status": {
                    "capture": {"(no_result)": 1, "(other)": 1, "already_remembered": 1, "pending_review": 1, "remembered": 1},
                    "demote": {"(no_result)": 1, "demoted": 1, "pending_review": 1},
                    "status": {"ok": 1},
                },
            },
        )
        self.assertEqual(
            state_db["native_memory"],
            {
                "calls": 6,  # c3, c7, c9, wrapped c15 and c16, c17; c13 is outside the window, c18 was refused
                "operations_by_action": {"(other)": 1, "add": 3, "remove": 1, "replace": 2},
                "by_outcome": {"(no_result)": 1, "(unparsed)": 1, "failed": 1, "succeeded": 3},
            },
        )
        self.assertEqual(state_db["sessions_with_omh_memory_write"], {"total": 3, "by_source": {"(none)": 1, "cli": 1, "tui": 1}})

    def test_lifecycle_actions_are_labelled_and_only_correct_and_retire_count_as_writes(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = OmhPaths(omh_home=Path(tmp) / ".omh", hermes_home=Path(tmp) / ".hermes")
            paths.hermes_home.mkdir(parents=True)
            connection = sqlite3.connect(paths.hermes_home / "state.db")
            with connection:
                connection.execute(
                    "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL, last_activity_at REAL, "
                    "ended_at REAL, archived INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0)"
                )
                connection.execute(
                    "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, "
                    "tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL NOT NULL)"
                )
                # One lifecycle result per session, so each write decision shows as its own session.
                results = (
                    ("s_recall", "recall", "found"),
                    ("s_confirm", "confirm", "confirmed"),
                    ("s_corrected", "correct", "corrected"),
                    ("s_held", "correct", "pending_review"),
                    ("s_refused", "correct", "refused"),
                    ("s_retired", "retire", "retired"),
                    ("s_retire_refused", "retire", "refused"),
                )
                connection.executemany(
                    "INSERT INTO sessions (id, source, started_at, last_activity_at) VALUES (?, ?, ?, ?)",
                    [(session, "tui", IN_WINDOW, IN_WINDOW) for session, _action, _status in results],
                )
                connection.executemany(
                    "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp) "
                    "VALUES (?, 'tool', ?, ?, NULL, 'omh_memory', ?)",
                    [
                        (session, json.dumps({"action": action, "status": status, "display_line": CANARY}), f"call_{session}", IN_WINDOW)
                        for session, action, status in results
                    ],
                )
            connection.close()
            payload = build_memory_evidence(paths, now=NOW)

        state_db = payload["state_db"]
        self.assertEqual(
            state_db["omh_memory"]["by_action_status"],
            {
                "confirm": {"confirmed": 1},
                "correct": {"corrected": 1, "pending_review": 1, "refused": 1},
                "recall": {"found": 1},
                "retire": {"refused": 1, "retired": 1},
            },
        )
        self.assertEqual(state_db["sessions_with_omh_memory_write"], {"total": 3, "by_source": {"tui": 3}})
        self.assertNotIn(CANARY, json.dumps(payload))

    def test_store_counts_are_the_hand_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            payload = build_memory_evidence(_home(tmp), now=NOW)

        store = payload["omh_store"]
        self.assertEqual(store["read_state"], "read")
        self.assertEqual(
            store["records"],
            {
                "total": 4,
                "by_source": {"cli": 1, "hermes_demotion": 1, "hermes_model": 2},
                "by_admission_state": {"approved": 1, "auto_approved": 3},
                "created_in_window": 2,
                "refused_files_by_reason": {},
            },
        )
        self.assertEqual(
            store["consolidation_journal"],
            {
                "briefs_in_window": 2,
                # A family counts once per brief, even when two of its reasons fired.
                "by_reason_family": {"duplicate_records": 1, "headroom_below_floor": 1, "session_ending_with_unconsolidated_turns": 1},
                "unparsed_lines": 0,
            },
        )
        self.assertEqual(
            store["write_journal"],
            {
                "entries_in_window": 5,
                "by_action": {"add": 1, "remove": 4},
                "removed_entry_demoted": {"true": 2, "false": 1, "absent": 1},
                "unparsed_lines": 0,
                # Below the provider's retention cap, so no line has been dropped.
                "journal_at_cap": False,
                "journal_limit": JOURNAL_LIMIT,
            },
        )
        self.assertEqual(store["last_prefetch"]["state"], "never_served")
        files = {row["label"]: row for row in payload["hermes_memory_files"]["files"]}
        self.assertEqual(files["MEMORY.md"]["chars"], len("alpha\n§\nbeta"))
        self.assertEqual(files["MEMORY.md"]["headroom_chars"], files["MEMORY.md"]["cap"] - files["MEMORY.md"]["chars"])
        self.assertFalse(files["USER.md"]["exists"])

    def test_the_window_boundary_includes_its_start_and_drops_older_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _home(tmp)
            wide = build_memory_evidence(paths, days=10, now=NOW)
            narrow = build_memory_evidence(paths, days=7, now=NOW)
            exact = build_memory_evidence(paths, days=9, now=NOW)  # since == OUT_OF_WINDOW exactly

        self.assertEqual(wide["state_db"]["omh_memory"]["calls"], 10)
        self.assertEqual(narrow["state_db"]["omh_memory"]["calls"], 9)
        self.assertEqual(exact["state_db"]["omh_memory"]["calls"], 10)
        self.assertEqual(wide["omh_store"]["records"]["created_in_window"], 4)
        self.assertEqual(wide["omh_store"]["write_journal"]["removed_entry_demoted"]["true"], 3)
        self.assertEqual(narrow["window"]["since"], "2026-10-05T12:00:00Z")

    def test_headline_is_derived_from_the_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            payload = build_memory_evidence(_home(tmp), now=NOW)

        self.assertEqual([row["count"] for row in payload["headline"]], [1, 1, 2])
        self.assertEqual(payload["headline"], memory_evidence_headline(payload))
        for row in payload["headline"]:
            self.assertIn("last 7 days", row["claim"])
        remembered, stored, freed = payload["headline"]
        self.assertIn(payload["homes"]["hermes_home"], remembered["claim"])
        self.assertIn("Hermes entries OMH stored a verified copy of", stored["claim"])
        self.assertNotIn("moved", stored["claim"])
        self.assertIn("No removal from Hermes memory is implied", stored["meaning"])
        self.assertIn(f"OMH home {payload['homes']['omh_home']}", freed["claim"])
        payload["state_db"]["omh_memory"]["by_action_status"]["capture"]["remembered"] = 5
        payload["omh_store"]["write_journal"]["removed_entry_demoted"]["true"] = 9
        mutated = memory_evidence_headline(payload)
        self.assertEqual([row["count"] for row in mutated], [5, 1, 9])
        self.assertTrue(mutated[0]["claim"].startswith("5 facts remembered"))

    def test_no_free_text_from_messages_or_records_reaches_the_output(self) -> None:
        with TemporaryDirectory() as tmp:
            payload = build_memory_evidence(_home(tmp), now=NOW)
            text = format_memory_evidence_summary(payload)

        output = (json.dumps(payload, ensure_ascii=False) + text).lower()
        self.assertNotIn(CANARY.lower(), output)
        self.assertNotIn("weird", output)
        self.assertNotIn("None", text)


class MemoryEvidenceJournalTests(unittest.TestCase):
    def _with_write_journal(self, lines: list[str]) -> dict:
        with TemporaryDirectory() as tmp:
            paths = _home(tmp)
            (paths.omh_home / "memory" / "write_journal.jsonl").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
            return build_memory_evidence(paths, now=NOW)

    def test_any_unparsed_journal_line_marks_the_store_partial(self) -> None:
        # The provider rewrites a journal whole and atomically, so even a
        # trailing torn line is damage, not an append race.
        good = json.dumps({"observed_at": _iso(IN_WINDOW), "action": "add"})
        for lines in ([good, '{"observed_at": "2026-10'], [good, "not json", good], [good, "not json", "[1, 2]"]):
            with self.subTest(lines=lines):
                payload = self._with_write_journal(lines)
                self.assertEqual(payload["omh_store"]["read_state"], "partial")
                self.assertEqual(payload["omh_store"]["write_journal"]["unparsed_lines"], len(lines) - lines.count(good))
                self.assertEqual(payload["omh_store"]["write_journal"]["entries_in_window"], lines.count(good))
                self.assertIsNone(payload["headline"][2]["count"])
                self.assertEqual(_memory_evidence_exit_code(payload), 3)

    def test_an_unparsed_record_file_marks_the_store_partial(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _home(tmp)
            (paths.omh_home / "memory" / "records" / "broken.json").write_text("{not json", encoding="utf-8")
            payload = build_memory_evidence(paths, now=NOW)

        self.assertEqual(payload["omh_store"]["read_state"], "partial")
        self.assertEqual(payload["omh_store"]["records"]["refused_files_by_reason"], {"unreadable_file": 1})
        self.assertEqual(payload["omh_store"]["records"]["total"], 4)
        self.assertEqual(_memory_evidence_exit_code(payload), 3)


class MemoryEvidenceReadFailureTests(unittest.TestCase):
    def test_an_empty_but_readable_home_is_an_honest_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            paths.hermes_home.mkdir()
            # Closed before the temp dir is removed: Windows will not unlink an open database.
            connection = sqlite3.connect(paths.hermes_home / "state.db")
            connection.executescript(
                "CREATE TABLE sessions (id TEXT, source TEXT);"
                "CREATE TABLE messages (id INTEGER, session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT, "
                "tool_calls TEXT, tool_name TEXT, timestamp REAL);"
            )
            connection.close()
            payload = build_memory_evidence(paths, now=NOW)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["unread_sources"], [])
        self.assertEqual(payload["state_db"]["omh_memory"]["calls"], 0)
        self.assertEqual(payload["omh_store"]["records"]["total"], 0)
        self.assertEqual([row["count"] for row in payload["headline"]], [0, 0, 0])
        self.assertEqual(_memory_evidence_exit_code(payload), 0)

    def test_a_missing_or_unreadable_state_db_is_named_and_not_a_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            paths.hermes_home.mkdir()
            missing = build_memory_evidence(paths, now=NOW)
            (paths.hermes_home / "state.db").write_bytes(b"not a database" * 100)
            corrupt = build_memory_evidence(paths, now=NOW)

        self.assertEqual(missing["state_db"]["read_state"], "missing")
        self.assertEqual(corrupt["state_db"]["read_state"], "unreadable")
        for payload in (missing, corrupt):
            self.assertFalse(payload["ok"])
            self.assertEqual([row["source"] for row in payload["unread_sources"]], ["state_db"])
            self.assertIsNone(payload["headline"][0]["count"])
            self.assertIn("not observed", payload["headline"][0]["claim"])
            self.assertEqual(_memory_evidence_exit_code(payload), 3)

    @unittest.skipIf(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0), "needs POSIX permissions")
    def test_an_unreadable_journal_marks_the_store_partial(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _home(tmp)
            journal = paths.omh_home / "memory" / "write_journal.jsonl"
            journal.chmod(0)
            try:
                payload = build_memory_evidence(paths, now=NOW)
            finally:
                journal.chmod(stat.S_IRUSR | stat.S_IWUSR)

        self.assertEqual(payload["omh_store"]["read_state"], "partial")
        self.assertIn("write_journal.jsonl", payload["omh_store"]["error"])
        self.assertEqual(_memory_evidence_exit_code(payload), 3)

    @unittest.skipIf(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0), "needs POSIX permissions")
    def test_a_directory_it_may_not_list_is_unread_not_empty(self) -> None:
        # Path.exists swallows EACCES on Python 3.14 and raises it on 3.11, and
        # stat succeeds on a mode-0 directory, so only listing first tells a
        # refused directory from an empty one the same way on every version.
        # A memory directory nobody may list is unreadable as a whole; records
        # alone leave the journals readable, so the store is partial.
        cases = (
            ("omh memory", lambda paths: paths.omh_home / "memory", "omh_store", "unreadable"),
            ("records only", lambda paths: paths.omh_home / "memory" / "records", "omh_store", "partial"),
            ("hermes memories", lambda paths: paths.hermes_home / "memories", "hermes_memory_files", "unreadable"),
            ("hermes MEMORY.md", lambda paths: paths.hermes_home / "memories" / "MEMORY.md", "hermes_memory_files", "unreadable"),
        )
        for label, directory_of, section, state in cases:
            with self.subTest(directory=label), TemporaryDirectory() as tmp:
                paths = _home(tmp)
                directory = directory_of(paths)
                directory.chmod(0)
                try:
                    payload = build_memory_evidence(paths, now=NOW)
                finally:
                    directory.chmod(stat.S_IRWXU)

                self.assertEqual(payload[section]["read_state"], state)
                self.assertFalse(payload["ok"])
                self.assertEqual(_memory_evidence_exit_code(payload), 3)

    def test_an_unreadable_prefetch_receipt_marks_the_store_partial(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _home(tmp)
            prefetch_receipt_path(paths.omh_home).write_text("{not a receipt", encoding="utf-8")
            payload = build_memory_evidence(paths, now=NOW)
            text = format_memory_evidence_summary(payload)

        self.assertEqual(payload["omh_store"]["last_prefetch"]["state"], "unreadable")
        self.assertEqual(payload["omh_store"]["read_state"], "partial")
        self.assertIn("prefetch receipt", payload["omh_store"]["error"])
        self.assertEqual(_memory_evidence_exit_code(payload), 3)
        self.assertNotIn("None", text)

    def test_a_bad_window_is_a_named_error(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = OmhPaths(omh_home=Path(tmp) / ".omh", hermes_home=Path(tmp) / ".hermes")
            with self.assertRaises(MemoryEvidenceError):
                build_memory_evidence(paths, days=0, now=NOW)
            with self.assertRaises(MemoryEvidenceError):
                build_memory_evidence(paths, since="last tuesday", now=NOW)

    def test_since_sets_the_window_like_session_usage(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _home(tmp)
            by_iso = build_memory_evidence(paths, since=_iso(OUT_OF_WINDOW), now=NOW)
            by_epoch = build_memory_evidence(paths, since=str(NOW - 7 * DAY), now=NOW)

        self.assertIsNone(by_iso["window"]["days"])
        self.assertEqual(by_iso["state_db"]["omh_memory"]["calls"], 10)
        self.assertEqual(by_epoch["state_db"]["omh_memory"]["calls"], 9)
        self.assertIn(f"since {_iso(OUT_OF_WINDOW)}", by_iso["headline"][0]["claim"])


class MemoryEvidenceCliTests(unittest.TestCase):
    def test_cli_prints_json_and_text_and_exits_on_what_it_read(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _home(tmp)
            base = ["--omh-home", str(paths.omh_home), "--hermes-home", str(paths.hermes_home), "quality-evidence", "memory"]
            json_status, json_stdout, _ = run_cli([*base, "--json"], output_json=False)
            text_status, text_stdout, _ = run_cli(base, output_json=False)
            (paths.hermes_home / "state.db").unlink()
            missing_status, missing_stdout, _ = run_cli([*base, "--json"], output_json=False)
            bad_status, _, bad_stderr = run_cli([*base, "--days", "0"], output_json=False)
            since_status, since_stdout, _ = run_cli([*base, "--since", _iso(NOW - DAY), "--json"], output_json=False)

        self.assertEqual(json_status, 0)
        self.assertEqual(json.loads(json_stdout)["schema_version"], MEMORY_EVIDENCE_SCHEMA_VERSION)
        self.assertEqual(text_status, 0)
        self.assertIn("Headline", text_stdout)
        self.assertNotIn(CANARY, json_stdout + text_stdout)
        self.assertEqual(missing_status, 3)
        self.assertEqual(json.loads(missing_stdout)["unread_sources"][0]["read_state"], "missing")
        self.assertEqual(bad_status, 2)
        self.assertIn("--days", bad_stderr)
        self.assertEqual(since_status, 3)  # the db was removed above; the window still parses
        self.assertIsNone(json.loads(since_stdout)["window"]["days"])


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
