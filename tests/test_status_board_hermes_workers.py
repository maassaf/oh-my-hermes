"""`omh coding status-board` lists Hermes' own workers beside the fanout units.

The board reads Hermes delegate children and kanban lanes through the two
readers `omh_hud` uses (`hermes_delegation`, `kanban_board_reader`). These cases
build a throwaway Hermes home holding a `state.db` and a `kanban.db` in the
shapes Hermes writes, and pin what the board adds: each worker row carries its
source and state, rows keep the board's running-first order, a Hermes database
that is missing, unreadable, refused or read in part is named as such instead
of rendering as "nothing running", a reader limit turns the counts into lower
bounds, and every database the board opens is opened as a `mode=ro` URI.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()

from omh.coding import status_board  # noqa: E402
from omh.coding.status_board import (  # noqa: E402
    build_status_board,
    render_status_board_text,
    status_board_messenger_body,
)
from omh.plugin_bundle.omh import hermes_delegation, kanban_board_reader  # noqa: E402
from omh.system.paths import OmhPaths  # noqa: E402

_NOW = "2026-08-03T12:00:00Z"
NOW = 1_785_758_400  # _NOW as epoch seconds
PARENT_ID = "20260803_110000_parent"
RUNNING_CHILD = "20260803_115800_childrun"
DONE_CHILD = "20260803_114000_childend"

# One state.db serves both readers, as it does on a host whose kanban workers
# run under the default profile: delegate children are rows whose model_config
# names `_delegate_from`, and a worker's own session is a `source = 'kanban'` row.
STATE_DDL = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, source TEXT NOT NULL DEFAULT 'cli', model TEXT, model_config TEXT,
    started_at REAL NOT NULL, ended_at REAL, tool_call_count INTEGER DEFAULT 0,
    api_call_count INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cwd TEXT, title TEXT, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT
);
CREATE TABLE session_model_usage (
    session_id TEXT NOT NULL, model TEXT NOT NULL, billing_provider TEXT NOT NULL DEFAULT '',
    api_call_count INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    actual_cost_usd REAL NOT NULL DEFAULT 0, estimated_cost_usd REAL NOT NULL DEFAULT 0,
    cost_status TEXT, cost_source TEXT, first_seen REAL, last_seen REAL
);
CREATE TABLE async_delegations (
    delegation_id TEXT PRIMARY KEY, state TEXT NOT NULL, dispatched_at REAL NOT NULL
);
CREATE TABLE messages (session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT);
"""

# Hermes' `tasks`, `task_links` and `task_runs` columns the board reader selects.
KANBAN_DDL = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, assignee TEXT, status TEXT NOT NULL,
    created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER, workspace_path TEXT,
    claim_lock TEXT, worker_pid INTEGER, last_failure_error TEXT, last_heartbeat_at INTEGER,
    skills TEXT, model_override TEXT, provider_override TEXT, reasoning_effort TEXT, session_id TEXT
);
CREATE TABLE task_links (parent_id TEXT NOT NULL, child_id TEXT NOT NULL, PRIMARY KEY (parent_id, child_id));
CREATE TABLE task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, status TEXT NOT NULL,
    started_at INTEGER NOT NULL, metadata TEXT
);
"""


def _insert(connection: sqlite3.Connection, table: str, row: dict) -> None:
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(row.values()))


def _child(session_id: str, *, started: float, last_seen: float, goal: str) -> list[tuple[str, dict]]:
    config = {"_delegate_from": PARENT_ID, "reasoning_config": {"enabled": True, "effort": "high"}}
    return [
        ("sessions", {"id": session_id, "model": "gpt-5.6-sol", "model_config": json.dumps(config), "started_at": started}),
        ("session_model_usage", {
            "session_id": session_id, "model": "gpt-5.6-sol", "api_call_count": 3,
            "input_tokens": 1000, "output_tokens": 200, "first_seen": started, "last_seen": last_seen,
            "cost_status": "estimated",
        }),
        ("messages", {"session_id": session_id, "role": "user", "content": goal}),
    ]


def build_state_db(hermes: Path, *, with_async_delegations: bool = True, now: float = NOW) -> None:
    hermes.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, dict]] = [
        ("sessions", {"id": PARENT_ID, "model": "gpt-5.6-sol", "model_config": "", "started_at": now - 4000}),
        *_child(RUNNING_CHILD, started=now - 120, last_seen=now - 30, goal="Fix the parser"),
        *_child(DONE_CHILD, started=now - 700, last_seen=now - 400, goal="Write the release note"),
        # The running kanban worker's own session, linked by the stamp on its run.
        ("sessions", {
            "id": "kanban_worker_1", "source": "kanban", "model": "claude-sonnet-5",
            "model_config": json.dumps({"reasoning_config": {"enabled": True, "effort": "medium"}}),
            "started_at": now - 295, "api_call_count": 4, "tool_call_count": 7,
            "input_tokens": 3000, "output_tokens": 400,
        }),
    ]
    with closing(sqlite3.connect(hermes / "state.db")) as connection, connection:
        ddl = STATE_DDL if with_async_delegations else STATE_DDL.split("CREATE TABLE async_delegations")[0] + (
            "CREATE TABLE messages (session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT);"
        )
        connection.executescript(ddl)
        for table, row in rows:
            _insert(connection, table, row)


def _task(identity: str, status: str, now: float, **fields) -> dict:
    row = {"id": identity, "title": f"Task {identity}", "assignee": "default", "status": status, "created_at": now - 600}
    row.update(fields)
    return row


def build_kanban_db(hermes: Path, *, now: float = NOW) -> None:
    hermes.mkdir(parents=True, exist_ok=True)
    tasks = [
        _task("t_aaaa0001", "running", now, title="Write the docs", started_at=now - 300, worker_pid=41,
              last_heartbeat_at=now - 5, skills='["docs"]'),
        _task("t_bbbb0002", "running", now, started_at=now - 3000, worker_pid=42, last_heartbeat_at=now - 16 * 60),
        _task("t_cccc0003", "ready", now, last_failure_error="worker crashed"),
        _task("t_dddd0004", "done", now, started_at=now - 800, completed_at=now - 60),
    ]
    with closing(sqlite3.connect(hermes / "kanban.db")) as connection, connection:
        connection.executescript(KANBAN_DDL)
        for row in tasks:
            _insert(connection, "tasks", row)
        _insert(connection, "task_runs", {
            "task_id": "t_aaaa0001", "status": "running", "started_at": now - 300,
            "metadata": json.dumps({"worker_session_id": "kanban_worker_1"}),
        })


class _HermesHomeCase(unittest.TestCase):
    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.hermes = self.root / "hermes"
        self.paths = OmhPaths(omh_home=self.root / "omh", hermes_home=self.hermes)
        # The board reader honors Hermes' own board overrides; a developer shell
        # that sets one must not point these cases at a real board.
        env = mock.patch.dict(os.environ, {"HERMES_KANBAN_HOME": "", "HERMES_KANBAN_BOARD": ""})
        env.start()
        self.addCleanup(env.stop)
        markers = mock.patch.object(status_board, "read_inflight_markers", return_value=[])
        markers.start()
        self.addCleanup(markers.stop)

    def board(self, **kwargs) -> dict:
        return build_status_board(self.paths, now=_NOW, **kwargs)

    def sources(self, payload: dict) -> dict[str, dict]:
        return {record["source"]: record for record in payload["hermes_workers"]["sources"]}


class BothSourcesPresentTests(_HermesHomeCase):
    def setUp(self) -> None:
        super().setUp()
        build_state_db(self.hermes)
        build_kanban_db(self.hermes)

    def test_each_worker_carries_its_source_and_state(self) -> None:
        payload = self.board()
        workers = payload["hermes_workers"]["rows"]
        self.assertEqual(
            [(worker["source"], worker["worker_id"], worker["state"]) for worker in workers],
            [
                # Running first, as the unit table orders its rows; Hermes still
                # calls a lane whose heartbeat stopped running, so it sorts there.
                ("hermes_delegate_children", "childrun", "running"),
                ("hermes_kanban_board", "aaaa0001", "running"),
                ("hermes_kanban_board", "bbbb0002", "stale"),
                ("hermes_delegate_children", "childend", "done"),
                ("hermes_kanban_board", "cccc0003", "queued"),
                ("hermes_kanban_board", "dddd0004", "done"),
            ],
        )
        self.assertEqual(
            {record["source"]: record["read_status"] for record in payload["hermes_workers"]["sources"]},
            {"hermes_delegate_children": "read", "hermes_kanban_board": "read"},
        )
        self.assertEqual(
            payload["sources_used"], ["hermes_delegate_children", "hermes_kanban_board"]
        )

    def test_the_rows_carry_what_the_readers_observed(self) -> None:
        workers = {worker["worker_id"]: worker for worker in self.board()["hermes_workers"]["rows"]}
        child = workers["childrun"]
        self.assertEqual(child["model_label"], "gpt-5.6-sol high")
        self.assertEqual((child["tokens_total"], child["tokens_text"]), (1200, "1,200"))
        self.assertEqual(child["elapsed_text"], "2m")
        self.assertEqual(child["summary"], "Fix the parser")
        # The delegate reader reports no tool-call count; nothing invents one.
        self.assertEqual(child["tool_count_text"], "unknown")
        lane = workers["aaaa0001"]
        self.assertEqual(lane["model_label"], "claude-sonnet-5 medium")
        self.assertEqual((lane["tokens_text"], lane["tool_count_text"]), ("3,400", "7"))
        self.assertEqual(lane["summary"], "Write the docs")
        # An unlinked lane has no usage: unknown, never zero.
        self.assertEqual(workers["dddd0004"]["tokens_text"], "unknown")
        self.assertEqual(workers["cccc0003"]["state_detail"], "retry pending")
        # A queued lane never started: the reader's 0.0 is not an observed zero.
        self.assertEqual(workers["cccc0003"]["elapsed_text"], "unknown")
        self.assertEqual(workers["dddd0004"]["elapsed_text"], "12m")

    def test_the_board_renders_one_headline_and_a_hermes_table(self) -> None:
        text = render_status_board_text(self.board())
        lines = text.splitlines()
        # Two running Hermes workers, the stale lane named as stuck, six observed.
        self.assertEqual(lines[0].split(" — ")[0], "Coding status board (2 running, 1 stuck of 6 observed)")
        self.assertNotIn("No coding work observed.", text)
        self.assertEqual(lines[1].split(), ["SOURCE", "ID", "MODEL", "STATE", "ELAPSED", "TOKENS", "TOOLS"])
        rows = {line.split()[1]: line for line in lines[2:8]}
        self.assertIn("hermes-delegate", rows["childrun"])
        self.assertIn("hermes-kanban", rows["aaaa0001"])
        # Hermes' `done` is Hermes' report, so it reads as one.
        self.assertIn("reported done", rows["childend"])
        self.assertIn("queued (retry pending)", rows["cccc0003"])
        self.assertIn("stale", rows["bbbb0002"])
        self.assertNotIn("not available", text)

    def test_the_messenger_bullets_name_the_source(self) -> None:
        body = status_board_messenger_body(self.board(), render_profile="limited_markdown")
        bullets = [line for line in body.splitlines() if line.startswith("- ")]
        self.assertEqual(len(bullets), 6)
        self.assertTrue(bullets[0].startswith("- childrun — "), bullets[0])
        self.assertIn("hermes-delegate (gpt-5.6-sol high) — running — 2m — tools unknown — 1,200 tokens — Fix the parser", bullets[0])

    def test_hermes_rows_leave_the_unit_table_unchanged(self) -> None:
        # Compared with the same board over a home with no Hermes databases,
        # not with origin/main; the before/after against main is in the PR.
        marker = {
            "owner": "claude", "owner_host": "local", "model": "fable-5", "reasoning_effort": "high",
            "run_ref": "run-research", "worktree": "/w/research", "started_at": "2026-08-03T11:25:00Z",
            "unit_id": "research", "fanout_id": "fanout-0123456789ab",
        }
        with mock.patch.object(status_board, "read_inflight_markers", return_value=[marker]):
            with_hermes = render_status_board_text(self.board()).splitlines()
            alone = OmhPaths(omh_home=self.root / "omh", hermes_home=self.root / "no-hermes")
            without = render_status_board_text(build_status_board(alone, now=_NOW)).splitlines()
        # The unit table (column header plus its row) is byte-identical; only the
        # headline count and the Hermes lines after it differ.
        self.assertEqual(with_hermes[1:3], without[1:3])
        self.assertTrue(with_hermes[0].startswith("Coding status board (3 running, 1 stuck of 7 observed)"))

    def test_the_limit_caps_each_table_and_says_so(self) -> None:
        payload = self.board(limit=2)
        self.assertEqual(len(payload["hermes_workers"]["rows"]), 2)
        self.assertEqual(payload["hermes_workers"]["worker_count"], 6)
        line = "Showing 2 of 6 Hermes workers; 4 not shown because of the display limit."
        self.assertIn(line, render_status_board_text(payload))
        self.assertIn(line, status_board_messenger_body(payload, render_profile="limited_markdown"))
        # Nothing was capped, so nothing reads as a lower bound.
        self.assertFalse(payload["hermes_workers"]["counts_are_lower_bounds"])
        self.assertNotIn("at least", render_status_board_text(payload))

    def test_every_connect_is_a_mode_ro_uri_and_these_fixtures_stay_unchanged(self) -> None:
        # The fixtures are rollback-journal databases. A WAL database's reader
        # writes read-marks to its `-shm` sidecar even under `mode=ro`; that
        # case is not covered here.
        opened: list[tuple[object, dict]] = []
        real_connect = sqlite3.connect

        def recording(database, *args, **kwargs):
            opened.append((database, kwargs))
            return real_connect(database, *args, **kwargs)

        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.hermes.iterdir()}
        with mock.patch("sqlite3.connect", side_effect=recording):
            payload = self.board()
        self.assertTrue(payload["hermes_workers"]["rows"])
        self.assertTrue(opened)
        for database, kwargs in opened:
            with self.subTest(database=database):
                self.assertTrue(str(database).startswith("file:"))
                self.assertTrue(str(database).endswith("?mode=ro"))
                self.assertIs(kwargs.get("uri"), True)
        after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.hermes.iterdir()}
        self.assertEqual(after, before)


class NeitherSourcePresentTests(_HermesHomeCase):
    def test_a_home_with_no_hermes_databases_says_so(self) -> None:
        payload = self.board()
        self.assertEqual(payload["hermes_workers"]["rows"], [])
        self.assertEqual(payload["hermes_workers"]["worker_count"], 0)
        self.assertEqual(
            {source: record["read_status"] for source, record in self.sources(payload).items()},
            {"hermes_delegate_children": "absent", "hermes_kanban_board": "absent"},
        )
        self.assertEqual(payload["sources_used"], [])
        text = render_status_board_text(payload)
        self.assertIn("No coding work observed.", text)
        self.assertIn("Hermes delegate children: not available — state.db does not exist.", text)
        self.assertIn("Hermes kanban board (default): not available — kanban.db does not exist.", text)
        # Named by file, never by the local path, which a messenger body would leak.
        self.assertNotIn(str(self.root), text)
        body = status_board_messenger_body(payload, render_profile="limited_markdown")
        self.assertIn("Hermes delegate children: not available — state.db does not exist.", body)
        self.assertIn("0 running of 0 observed", text)


class UnreadableSourceTests(_HermesHomeCase):
    def test_a_file_that_is_not_a_database_is_not_available_not_empty(self) -> None:
        self.hermes.mkdir(parents=True)
        (self.hermes / "state.db").write_bytes(b"not a sqlite database at all, just bytes")
        (self.hermes / "kanban.db").write_bytes(b"not a sqlite database at all, just bytes")
        payload = self.board()
        self.assertEqual(payload["hermes_workers"]["rows"], [])
        self.assertEqual(
            {source: record["read_status"] for source, record in self.sources(payload).items()},
            {"hermes_delegate_children": "unreadable", "hermes_kanban_board": "unreadable"},
        )
        text = render_status_board_text(payload)
        self.assertIn("Hermes delegate children: not available — state.db could not be read.", text)
        self.assertIn("Hermes kanban board (default): not available — kanban.db could not be read.", text)

    def test_a_board_without_the_tasks_columns_is_unreadable(self) -> None:
        self.hermes.mkdir(parents=True)
        with closing(sqlite3.connect(self.hermes / "kanban.db")) as connection, connection:
            connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
        self.assertEqual(self.sources(self.board())["hermes_kanban_board"]["read_status"], "unreadable")

    def test_a_state_db_without_async_delegations_is_read_in_part(self) -> None:
        build_state_db(self.hermes, with_async_delegations=False)
        payload = self.board()
        self.assertEqual(self.sources(payload)["hermes_delegate_children"]["read_status"], "partial")
        # The children it did read are still shown.
        self.assertEqual(
            [worker["worker_id"] for worker in payload["hermes_workers"]["rows"]], ["childrun", "childend"]
        )
        self.assertIn(
            "Hermes delegate children: read in part — state.db did not answer every query.",
            render_status_board_text(payload),
        )

    def test_a_read_status_the_board_does_not_know_still_prints_a_line(self) -> None:
        # Fail closed: a reader word added later must not render as silence.
        payload = self.board()
        payload["hermes_workers"]["sources"][0]["read_status"] = "some_new_status"
        self.assertIn(
            "Hermes delegate children: not available — state.db could not be read.",
            render_status_board_text(payload),
        )

    @unittest.skipIf(sys.platform == "win32", "symlink creation needs privileges on Windows")
    def test_a_symlinked_board_is_refused_and_says_so(self) -> None:
        elsewhere = self.root / "elsewhere"
        build_kanban_db(elsewhere)
        self.hermes.mkdir(parents=True)
        (self.hermes / "kanban.db").symlink_to(elsewhere / "kanban.db")
        payload = self.board()
        self.assertEqual(self.sources(payload)["hermes_kanban_board"]["read_status"], "refused")
        self.assertEqual(payload["hermes_workers"]["rows"], [])
        self.assertIn(
            "Hermes kanban board (default): not available — its path is a symbolic link",
            render_status_board_text(payload),
        )


def build_many(hermes: Path, *, children: int, lanes: int) -> None:
    """`children` running delegate children and `lanes` running kanban lanes."""
    hermes.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(hermes / "state.db")) as connection, connection:
        connection.executescript(STATE_DDL)
        _insert(connection, "sessions", {"id": PARENT_ID, "model": "gpt-5.6-sol", "model_config": "", "started_at": NOW - 4000})
        for index in range(children):
            for table, row in _child(f"20260803_1158{index:02d}_c{index:07d}", started=NOW - 120 - index,
                                     last_seen=NOW - 30, goal=f"child {index}"):
                _insert(connection, table, row)
    with closing(sqlite3.connect(hermes / "kanban.db")) as connection, connection:
        connection.executescript(KANBAN_DDL)
        for index in range(lanes):
            _insert(connection, "tasks", _task(f"t_{index:08x}", "running", NOW, started_at=NOW - 300,
                                              worker_pid=1000 + index, last_heartbeat_at=NOW - 5))


class ReaderLimitTests(_HermesHomeCase):
    def test_more_workers_than_the_readers_select_read_as_lower_bounds(self) -> None:
        build_many(self.hermes, children=40, lanes=100)
        payload = self.board()
        block = payload["hermes_workers"]
        self.assertEqual(
            {source: (record["selection_capped"], record["worker_count"]) for source, record in self.sources(payload).items()},
            {"hermes_delegate_children": (True, 32), "hermes_kanban_board": (True, 64)},
        )
        self.assertTrue(block["counts_are_lower_bounds"])
        text = render_status_board_text(payload)
        self.assertEqual(
            text.splitlines()[0].split(" — ")[0],
            "Coding status board (at least 96 running of at least 96 observed)",
        )
        self.assertIn(
            "Showing 20 of at least 96 Hermes workers; at least 76 not shown because of the display limit.", text
        )
        for name in ("Hermes delegate children", "Hermes kanban board (default)"):
            self.assertIn(f"{name}: read up to a reader limit; more may exist, so these counts are lower bounds.", text)
        body = status_board_messenger_body(payload, render_profile="limited_markdown")
        self.assertIn("at least 96 running of at least 96 observed", body)

    def test_a_selection_exactly_at_the_bound_is_not_capped(self) -> None:
        build_many(self.hermes, children=32, lanes=64)
        payload = self.board()
        self.assertEqual(
            {source: (record["selection_capped"], record["worker_count"]) for source, record in self.sources(payload).items()},
            {"hermes_delegate_children": (False, 32), "hermes_kanban_board": (False, 64)},
        )
        self.assertNotIn("at least", render_status_board_text(payload))

    def test_the_hud_reads_the_same_selection_as_before(self) -> None:
        # The readers fetch one row past their bound and drop it, so what the
        # HUD projects from a capped board is the bounded selection it always
        # read: 64 lanes counted, 32 children listed.
        build_many(self.hermes, children=40, lanes=100)
        lanes = kanban_board_reader.read_kanban_lanes(self.hermes, now=NOW, limit=8)
        self.assertEqual((lanes["running"], len(lanes["rows"]), lanes["hidden"]), (64, 8, 56))
        children = hermes_delegation.read_hermes_native_subagents(
            self.hermes, now=NOW, limit=40, omh_home=self.root / "omh"
        )
        self.assertEqual((children["running"], len(children["rows"])), (32, 32))

    def test_hidden_rows_a_reader_reports_are_counted_and_make_the_counts_floors(self) -> None:
        lanes = kanban_board_reader.read_kanban_lanes(self.hermes, now=NOW)
        reported = {**lanes, "rows": [], "hidden": 5, "read_status": "read", "scope": "global"}
        with mock.patch.object(status_board, "read_kanban_lanes", return_value=reported):
            payload = self.board()
        self.assertEqual(self.sources(payload)["hermes_kanban_board"]["worker_count"], 5)
        self.assertEqual(payload["hermes_workers"]["worker_count"], 5)
        self.assertTrue(payload["hermes_workers"]["counts_are_lower_bounds"])
        self.assertIn("of at least 5 observed", render_status_board_text(payload))


class NeverStartedLaneTests(_HermesHomeCase):
    def test_a_lane_that_never_started_has_unknown_elapsed_not_zero(self) -> None:
        # Hermes creates tasks already `blocked`, and one can finish without a
        # recorded start; neither has an elapsed time to show.
        self.hermes.mkdir(parents=True)
        with closing(sqlite3.connect(self.hermes / "kanban.db")) as connection, connection:
            connection.executescript(KANBAN_DDL)
            _insert(connection, "tasks", _task("t_b10c0001", "blocked", NOW))
            _insert(connection, "tasks", _task("t_d0e00001", "done", NOW, completed_at=NOW - 60))
        lanes = kanban_board_reader.read_kanban_lanes(self.hermes, now=NOW)
        self.assertEqual(
            {row["task_id"]: row["elapsed_seconds"] for row in lanes["rows"]},
            {"b10c0001": None, "d0e00001": None},
        )
        payload = self.board()
        workers = {worker["worker_id"]: worker for worker in payload["hermes_workers"]["rows"]}
        self.assertEqual(
            {identity: (worker["state"], worker["elapsed_text"]) for identity, worker in workers.items()},
            {"b10c0001": ("blocked", "unknown"), "d0e00001": ("done", "unknown")},
        )
        self.assertNotIn(" 0s ", render_status_board_text(payload))

    def test_a_requeued_lane_shows_no_elapsed_from_its_earlier_run(self) -> None:
        # The reader freezes a settled lane's elapsed at its last heartbeat, so
        # a lane the dispatcher requeued after a failed run carries that run's
        # figure; no worker runs it now, and the HUD leaves the cell blank.
        self.hermes.mkdir(parents=True)
        with closing(sqlite3.connect(self.hermes / "kanban.db")) as connection, connection:
            connection.executescript(KANBAN_DDL)
            _insert(connection, "tasks", _task("t_5e9e0001", "ready", NOW, started_at=NOW - 900,
                                              last_heartbeat_at=NOW - 600, last_failure_error="worker crashed"))
        lanes = kanban_board_reader.read_kanban_lanes(self.hermes, now=NOW)
        self.assertEqual([(row["state"], row["elapsed_seconds"]) for row in lanes["rows"]], [("queued", 300.0)])
        worker = self.board()["hermes_workers"]["rows"][0]
        self.assertEqual((worker["state"], worker["elapsed_text"]), ("queued", "unknown"))


class ReaderReadStatusTests(unittest.TestCase):
    """The readers `omh_hud` uses report how their read went, without changing their rows."""

    def test_the_delegate_reader_tells_a_missing_file_from_an_unreadable_one(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            missing = hermes_delegation.read_hermes_native_subagents(home, now=NOW, omh_home=home / "omh")
            (home / "state.db").write_bytes(b"garbage")
            garbage = hermes_delegation.read_hermes_native_subagents(home, now=NOW, omh_home=home / "omh")
        self.assertEqual((missing["read_status"], missing["rows"]), ("absent", []))
        self.assertEqual((garbage["read_status"], garbage["rows"]), ("unreadable", []))

    def test_the_board_reader_tells_a_missing_file_from_an_unreadable_one(self) -> None:
        with TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"HERMES_KANBAN_HOME": "", "HERMES_KANBAN_BOARD": ""}
        ):
            home = Path(tmp)
            missing = kanban_board_reader.read_kanban_lanes(home, now=NOW)
            (home / "kanban.db").write_bytes(b"garbage")
            garbage = kanban_board_reader.read_kanban_lanes(home, now=NOW)
            build_kanban_db(home / "fresh")
            read = kanban_board_reader.read_kanban_lanes(home / "fresh", now=NOW)
        self.assertEqual((missing["read_status"], missing["rows"]), ("absent", []))
        self.assertEqual((garbage["read_status"], garbage["rows"]), ("unreadable", []))
        self.assertEqual(read["read_status"], "read")
        self.assertTrue(read["rows"])


class StatusBoardCliHermesTests(unittest.TestCase):
    def test_the_command_lists_hermes_workers_and_exits_zero(self) -> None:
        with TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"HERMES_KANBAN_HOME": "", "HERMES_KANBAN_BOARD": ""}
        ):
            root = Path(tmp)
            # The command reads the clock, so the fixture is dated from it.
            build_state_db(root / ".hermes", now=time.time())
            build_kanban_db(root / ".hermes", now=time.time())
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            status, stdout, stderr = run_cli(base + ["coding", "status-board", "--json"], output_json=False)
        self.assertEqual((status, stderr), (0, ""))
        payload = json.loads(stdout)
        sources = {worker["source"] for worker in payload["hermes_workers"]["rows"]}
        self.assertEqual(sources, {"hermes_delegate_children", "hermes_kanban_board"})

    def test_an_unreadable_hermes_database_is_reported_and_the_read_still_exits_zero(self) -> None:
        # The command reports; a source it could not read is a line on the
        # board, not a failed command, as `fanout status` without --wait is.
        with TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"HERMES_KANBAN_HOME": "", "HERMES_KANBAN_BOARD": ""}
        ):
            root = Path(tmp)
            (root / ".hermes").mkdir()
            (root / ".hermes" / "state.db").write_bytes(b"garbage")
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            status, stdout, stderr = run_cli(base + ["coding", "status-board"], output_json=False)
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("Hermes delegate children: not available — state.db could not be read.", stdout)
        self.assertIn("Hermes kanban board (default): not available — kanban.db does not exist.", stdout)


if __name__ == "__main__":
    unittest.main()
