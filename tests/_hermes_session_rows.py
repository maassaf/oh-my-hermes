"""A `state.db` `sessions` table in the shape Hermes writes, for compaction lineage tests.

Columns, types and values are the ones Hermes' own `SessionDB` leaves after a
rotating compaction (`publish_compression_child` in
`hermes_state_compression.py`, read at Hermes 8b9ac5773e and at v0.22.2, and
produced by that code into a throwaway home): the closed segment keeps its row
with `ended_at` in epoch seconds and `end_reason` `compression`; the next
segment is a new row naming it as `parent_session_id`, open, whose
`model_config` is the agent's own, so a delegate child's continuation carries
the same `_delegate_from` as the segment it continues
(`tools/delegate_tool.py` stamps it once on the child agent). An empty
`model_config` is stored as NULL.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

COMPACTION = "compression"

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS sessions ("
    " id TEXT PRIMARY KEY, source TEXT NOT NULL, model_config TEXT, parent_session_id TEXT,"
    " started_at REAL NOT NULL, ended_at REAL, end_reason TEXT, last_activity_at REAL,"
    " archived INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0)"
)


def row(
    session_id: str,
    *,
    parent: str | None = None,
    source: str = "tui",
    delegate_from: str | None = None,
    config: dict[str, object] | str | None = None,
    started_at: float = 1000.0,
    ended_at: float | None = None,
    end_reason: str | None = None,
    last_activity_at: float | None = None,
) -> dict[str, object]:
    """One `sessions` row. `config` replaces the derived `model_config` (a str is stored raw)."""
    if config is None:
        config = {"_delegate_from": delegate_from} if delegate_from else {}
    return {
        "id": session_id, "parent": parent, "source": source, "config": config, "started_at": started_at,
        "ended_at": ended_at, "end_reason": end_reason, "last_activity_at": last_activity_at,
    }


def compacted(session_id: str, *, at: float, **fields: object) -> dict[str, object]:
    """A segment Hermes closed for compaction at `at`."""
    return row(session_id, ended_at=at, end_reason=COMPACTION, **fields)


def write_sessions(hermes_home: Path, *rows: dict[str, object]) -> Path:
    path = Path(hermes_home) / "state.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(_SCHEMA)
        for item in rows:
            config = item["config"]
            stored = config if isinstance(config, str) else (json.dumps(config) if config else None)
            connection.execute(
                "INSERT INTO sessions (id, source, model_config, parent_session_id, started_at, ended_at,"
                " end_reason, last_activity_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (item["id"], item["source"], stored, item["parent"], item["started_at"], item["ended_at"],
                 item["end_reason"], item["last_activity_at"]),
            )
        connection.commit()
    finally:
        connection.close()
    return path


def write_unreadable_state_db(hermes_home: Path) -> Path:
    """A `state.db` that exists and is not a database: the reader opens it and every query fails."""
    path = Path(hermes_home) / "state.db"
    path.write_bytes(b"this is not an sqlite database\n" * 64)
    return path


def within_seconds(limit: float, call):
    """``call()``'s result, or an AssertionError if it has not returned within ``limit`` seconds.

    For a reader that runs inside a Hermes hook: a loop that never ends must
    fail the test, not hang the suite. The call runs on a daemon thread, so a
    hung one is abandoned rather than joined.
    """
    import threading

    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["value"] = call()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the test's thread below
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(limit)
    if worker.is_alive():
        raise AssertionError(f"did not return within {limit} seconds")
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome["value"]
