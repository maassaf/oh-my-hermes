"""A fake Hermes `tui_gateway` that replays a recorded session over stdio.

Tests drive the real `gateway_spawn` + `GatewayClient` against it. `materialize`
writes a `tui_gateway` package into a temp dir whose `entry` module is a copy of
this file, next to the scenario it plays. With `HERMES_PYTHON=sys.executable`
and `HERMES_PYTHON_SRC_ROOT=<that dir>` (see `replay_env`), the argv
`gateway_spawn` builds -- `python -m tui_gateway.entry` -- runs `main()` below.

Playback: recorded gateway frames (`dir: in`) are written to stdout in order.
When the next recorded frame is the client's (`dir: out`), the fake reads one
line from stdin and checks it. A call must name the recorded method; its id is
remembered and the recorded answer is re-addressed to it, so a test's ids need
not match the recording's. An answer to a server request must carry the
recorded `srq-*` id and exactly the recorded result or error. A mismatch is
written to stderr and the fake exits with MISMATCH_EXIT, which the test sees as
a `GatewayExited` whose reason is in `stderr_tail()`.

Answer table (`Scenario.answers`): a call whose method the table names is
answered from the table, re-addressed to its id, whenever it arrives -- in
place of a recorded client frame, or after the recorded frames -- and does not
advance the recording. A method listed more than once is answered with its
entries in order, the last one repeating. It lets a client whose startup calls differ from the
recording's (say, `config.get` and `commands.catalog` before `session.create`)
replay the recorded turns unchanged; `recorded_answer` builds an entry from a
recorded answer. Every client line the fake reads is appended, as received, to
`client_frames.jsonl` beside the scenario, which `client_frames` reads back.

Fixtures: `tests/fixtures/tui_gateway/*.jsonl`, sanitized from real Hermes
6c61c3e27d recordings. The first line is a header naming the Hermes commit, the
sanitizing and trimming steps, and the named segments `spans` resolves.

Only the standard library is imported: the copy runs in a child interpreter
whose import path holds nothing but the temp dir.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "tui_gateway"
SCENARIO_FILE = "scenario.json"
CLIENT_LOG_FILE = "client_frames.jsonl"
MISMATCH_EXIT = 3

# What the fake does once its frames run out.
WAIT_EOF = "wait_eof"  # read stdin until EOF, then exit with `exit_code`; any further client frame is a mismatch
EXIT = "exit"  # exit with `exit_code` at once
HANG = "hang"  # keep running past stdin EOF (bounded, so a killed test run never orphans it for long)
HANG_LIMIT_SECONDS = 30.0


@dataclass(frozen=True)
class Scenario:
    fixture: str
    spans: tuple[tuple[int, int], ...] = ()
    stderr: tuple[str, ...] = ()  # written to stderr before anything else
    inject: tuple[tuple[int, str], ...] = ()  # raw stdout lines written before the played frame at that position
    after: str = WAIT_EOF
    ignore_sigterm: bool = False
    exit_code: int = 0
    answers: tuple[tuple[str, dict[str, Any]], ...] = ()  # (method, {"result": ...} or {"error": ...})


def load_fixture(name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The fixture's header and its rows (`{"t", "dir", "frame"}`)."""
    lines = (FIXTURE_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(lines[0])["fixture"], [json.loads(line) for line in lines[1:]]


def spans(fixture: str, *segments: str) -> tuple[tuple[int, int], ...]:
    """The [start, stop) row ranges of the named segments, in the order given."""
    header, _rows = load_fixture(fixture)
    return tuple(tuple(header["segments"][name]) for name in segments)


def recorded_answer(fixture: str, segment: str) -> dict[str, Any]:
    """The segment's recorded answer to the client's call, as an answer-table entry (`result` or `error`)."""
    (start, stop), = spans(fixture, segment)
    rows = load_fixture(fixture)[1][start:stop]
    frame = next(row["frame"] for row in rows if row["dir"] == "in" and "method" not in row["frame"])
    return {key: frame[key] for key in ("result", "error") if key in frame}


def client_frames(root: Path) -> list[dict[str, Any]]:
    """Every client frame the fake materialized in `root` has read so far, in order."""
    path = root / "tui_gateway" / CLIENT_LOG_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def played_rows(scenario: Scenario) -> list[dict[str, Any]]:
    _header, rows = load_fixture(scenario.fixture)
    return [row for start, stop in scenario.spans for row in rows[start:stop]]


def _answer_table(answers: tuple[tuple[str, dict[str, Any]], ...]) -> dict[str, list[dict[str, Any]]]:
    table: dict[str, list[dict[str, Any]]] = {}
    for method, frame in answers:
        table.setdefault(method, []).append(frame)
    return table


def materialize(root: Path, scenario: Scenario) -> None:
    """Write a fake `tui_gateway` package into `root` that plays `scenario`."""
    package = root / "tui_gateway"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    shutil.copyfile(Path(__file__), package / "entry.py")
    plan = {
        "rows": played_rows(scenario),
        "stderr": list(scenario.stderr),
        "inject": [list(pair) for pair in scenario.inject],
        "after": scenario.after,
        "ignore_sigterm": scenario.ignore_sigterm,
        "exit_code": scenario.exit_code,
        "answers": _answer_table(scenario.answers),
    }
    (package / SCENARIO_FILE).write_text(json.dumps(plan), encoding="utf-8")


def replay_env(root: Path) -> dict[str, str]:
    """This process's environment without ambient Hermes or frontend names, pointed at the fake."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("HERMES_", "OMH_TUI_")) and name != "PYTHONPATH"
    }
    env["HERMES_PYTHON"] = sys.executable
    env["HERMES_PYTHON_SRC_ROOT"] = str(root)
    return env


# ---------------------------------------------------------------------------
# Child side: runs as `python -m tui_gateway.entry` inside the temp dir.
# ---------------------------------------------------------------------------


def _emit_line(text: str) -> None:
    sys.stdout.buffer.write(text.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _complain(problem: str) -> int:
    sys.stderr.write(f"replay: {problem}\n")
    sys.stderr.flush()
    return MISMATCH_EXIT


def _describe(frame: object) -> str:
    return json.dumps(frame)[:200]


def _readdressed(frame: dict[str, Any], ids: dict[int, int]) -> dict[str, Any]:
    if "method" not in frame and frame.get("id") in ids:
        return {**frame, "id": ids[frame["id"]]}
    return frame


def _log_client_line(line: bytes) -> None:
    with Path(__file__).with_name(CLIENT_LOG_FILE).open("ab") as log:
        log.write(line if line.endswith(b"\n") else line + b"\n")


def _answered_from_table(frame: dict[str, Any], answers: dict[str, Any]) -> bool:
    method = frame.get("method")
    if not isinstance(method, str) or method not in answers or type(frame.get("id")) is not int:
        return False
    entries = answers[method]
    answer = entries.pop(0) if len(entries) > 1 else entries[0]
    _emit_line(json.dumps({"jsonrpc": "2.0", "id": frame["id"], **answer}))
    return True


def _read_client_frame(answers: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """The client's next frame the answer table does not cover; `(None, None)` at end of input."""
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None, None
        _log_client_line(line)
        try:
            frame = json.loads(line)
        except ValueError:
            return None, f"the client sent a line that is not JSON: {line[:120]!r}"
        if not isinstance(frame, dict):
            return None, f"the client sent a non-object frame: {_describe(frame)}"
        if not _answered_from_table(frame, answers):
            return frame, None


def _check_client_frame(recorded: dict[str, Any], ids: dict[int, int], answers: dict[str, Any]) -> str | None:
    """None when the client's next frame matches `recorded`, else what went wrong."""
    frame, problem = _read_client_frame(answers)
    if problem is not None:
        return problem
    if frame is None:
        return f"stdin closed while waiting for {_describe(recorded)}"
    if "method" in recorded:
        if frame.get("method") != recorded["method"] or type(frame.get("id")) is not int:
            return f"expected a {recorded['method']} call, got {_describe(frame)}"
        ids[recorded["id"]] = frame["id"]
        return None
    expected = {key: recorded[key] for key in ("id", "result", "error") if key in recorded}
    answered = {key: frame[key] for key in ("id", "result", "error") if key in frame}
    if "method" in frame or answered != expected:
        return f"expected the answer {_describe(expected)}, got {_describe(frame)}"
    return None


def main() -> int:
    plan = json.loads(Path(__file__).with_name(SCENARIO_FILE).read_text(encoding="utf-8"))
    if plan["ignore_sigterm"]:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    for line in plan["stderr"]:
        sys.stderr.write(line + "\n")
    sys.stderr.flush()
    injected: dict[int, list[str]] = {}
    for position, text in plan["inject"]:
        injected.setdefault(position, []).append(text)
    rows = plan["rows"]
    answers = plan.get("answers", {})
    ids: dict[int, int] = {}
    for position, row in enumerate(rows):
        for text in injected.get(position, ()):
            _emit_line(text)
        if row["dir"] == "in":
            _emit_line(json.dumps(_readdressed(row["frame"], ids)))
            continue
        problem = _check_client_frame(row["frame"], ids, answers)
        if problem is not None:
            return _complain(problem)
    for text in injected.get(len(rows), ()):
        _emit_line(text)
    if plan["after"] == HANG:
        give_up = time.monotonic() + HANG_LIMIT_SECONDS
        while time.monotonic() < give_up:
            time.sleep(0.05)
    elif plan["after"] == WAIT_EOF:
        extra, problem = _read_client_frame(answers)
        if problem is not None:
            return _complain(problem)
        if extra is not None:
            return _complain(f"a client frame after the recorded ones: {_describe(extra)}")
    return plan["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
