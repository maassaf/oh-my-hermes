from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from _local_package import load_local_package

load_local_package()

from _cli_harness import run_cli  # noqa: E402

from omh.coding.fanout import build_fanout_contract  # noqa: E402
from omh.coding.fanout_artifacts import (  # noqa: E402
    fanout_dispatch_summary_path,
    write_fanout_contract,
)
from omh.coding.inflight import write_inflight_marker  # noqa: E402
from omh.coding.fanout_dispatch import dispatch_fanout  # noqa: E402
from omh.coding import fanout_status as fanout_status_module  # noqa: E402
from omh.coding.fanout_status import (  # noqa: E402
    FANOUT_STATUS_SCHEMA_VERSION,
    WAIT_OUTCOME_ALL_UNITS_TERMINAL,
    WAIT_OUTCOME_STUCK_UNITS,
    WAIT_OUTCOME_TIMED_OUT,
    project_fanout_status,
    render_fanout_status_text,
    wait_for_fanout_status,
)
from omh.commands.coding import (  # noqa: E402
    FANOUT_STATUS_WAIT_CRASH_EXIT_CODE,
    FANOUT_STATUS_WAIT_JOURNAL_UNREADABLE_EXIT_CODE,
    FANOUT_STATUS_WAIT_STUCK_EXIT_CODE,
    FANOUT_STATUS_WAIT_TIMEOUT_EXIT_CODE,
    _fanout_status_wait_exit_code,
)
from omh.coding.unit_execution_state import UNIT_EXECUTION_STATES, UNIT_STUCK_STATES  # noqa: E402
from omh.core.errors import OmhError  # noqa: E402
from omh.system.paths import OmhPaths  # noqa: E402

_FANOUT_ID = "fanout-0123456789ab"


def _stamp(minutes_ago: int) -> str:
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(
    unit_id: str,
    event: str,
    *,
    minutes_ago: int,
    status: str = "observed",
    worktree: str = "",
    owner: str = "codex",
    evidence_refs: list[str] | None = None,
    legacy: bool = False,
) -> dict[str, object]:
    """One synthetic journal event for `{fanout}-{unit}`'s run.

    `legacy=True` drops the fields later journal versions added (schema_version,
    worker_ref, evidence_refs, runtime_profile) and uses the pre-canonical event
    alias, so the roster is exercised against both the old and current shapes.
    """
    run_id = f"{_FANOUT_ID}-{unit_id}"
    record: dict[str, object] = {
        "event_id": f"{unit_id}-{event}-{minutes_ago}",
        "target_type": "run",
        "target_id": run_id,
        "run_id": run_id,
        "event": event,
        "status": status,
        "observed_at": _stamp(minutes_ago),
        "summary": f"{unit_id} {event}",
    }
    if legacy:
        return record
    record.update(
        {
            "schema_version": "omh_observation_event/v1",
            "privacy": "metadata_only",
            "worker_ref": unit_id,
            "runtime_profile": owner,
            "evidence_refs": evidence_refs or [],
        }
    )
    if worktree:
        record["worktree_ref"] = worktree
    return record


def _seed_journal(paths: OmhPaths, events: list[dict[str, object]]) -> None:
    path = paths.runtime_journal_events_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(event, sort_keys=True)}\n" for event in events),
        encoding="utf-8",
    )


def _roster_fixture() -> list[dict[str, object]]:
    return [
        # `core` reached the per-unit verification receipt.
        _event("core", "worktree_creation_observed", minutes_ago=90, worktree="/tmp/wt/core"),
        _event("core", "executor_dispatch_observed", minutes_ago=80, worktree="/tmp/wt/core"),
        _event(
            "core",
            "executor_result_observed",
            minutes_ago=70,
            worktree="/tmp/wt/core",
            evidence_refs=["run:core:exit0"],
        ),
        _event(
            "core",
            "unit_verification_observed",
            minutes_ago=60,
            worktree="/tmp/wt/core",
            evidence_refs=["run:core:unittest"],
        ),
        # `docs` was dispatched to a different owner and only came back with a
        # process result, written in the legacy pre-canonical shape.
        _event("docs", "worker_dispatch", minutes_ago=50, legacy=True),
        _event("docs", "worker_result", minutes_ago=40, legacy=True),
        _event(
            "docs",
            "executor_result_observed",
            minutes_ago=39,
            owner="claude-code",
            worktree="/tmp/wt/docs",
            evidence_refs=["run:docs:exit0"],
        ),
        # `flaky` failed at the executor result.
        _event("flaky", "executor_dispatch_observed", minutes_ago=30, worktree="/tmp/wt/flaky"),
        _event(
            "flaky",
            "executor_result_observed",
            minutes_ago=20,
            status="failed",
            worktree="/tmp/wt/flaky",
        ),
        # A different fanout's unit must never appear in this roster.
        {
            "schema_version": "omh_observation_event/v1",
            "event_id": "other-1",
            "target_type": "run",
            "target_id": "fanout-ffffffffffff-other",
            "run_id": "fanout-ffffffffffff-other",
            "event": "executor_dispatch_observed",
            "status": "observed",
            "observed_at": _stamp(10),
            "summary": "other fanout",
            "privacy": "metadata_only",
            "worker_ref": "other",
            "runtime_profile": "codex",
            "evidence_refs": [],
        },
    ]


class FanoutStatusProjectionTests(unittest.TestCase):
    def test_roster_projects_units_from_journal_events(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())

            roster = project_fanout_status(paths, _FANOUT_ID)

            self.assertEqual(roster["schema_version"], FANOUT_STATUS_SCHEMA_VERSION)
            self.assertEqual(roster["fanout_id"], _FANOUT_ID)
            self.assertEqual([unit["unit_id"] for unit in roster["units"]], ["core", "docs", "flaky"])
            core, docs, flaky = roster["units"]
            self.assertEqual(core["lifecycle_state"], "unit_verification_observed")
            self.assertEqual(core["owner"], "codex")
            self.assertEqual(core["worktree_path"], "/tmp/wt/core")
            self.assertEqual(core["evidence_ref_count"], 2)
            self.assertGreaterEqual(core["last_event_age_seconds"], 3600)
            self.assertEqual(docs["lifecycle_state"], "process_succeeded")
            self.assertEqual(docs["owner"], "claude-code")
            self.assertEqual(flaky["lifecycle_state"], "dispatched_not_succeeded")
            self.assertEqual(roster["unit_count"], 3)

    def test_legacy_events_do_not_crash_or_invent_state(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            # Legacy-only unit: no schema_version, no worker_ref, aliased names.
            _seed_journal(
                paths,
                [
                    _event("legacy", "worker_dispatch", minutes_ago=20, legacy=True),
                    _event("legacy", "worker_result", minutes_ago=10, legacy=True),
                ],
            )

            roster = project_fanout_status(paths, _FANOUT_ID)

            self.assertEqual([unit["unit_id"] for unit in roster["units"]], ["legacy"])
            unit = roster["units"][0]
            self.assertEqual(unit["lifecycle_state"], "process_succeeded")
            self.assertEqual(unit["owner"], "unknown")
            self.assertEqual(unit["worktree_path"], "unknown")
            self.assertEqual(unit["evidence_ref_count"], 0)

    def test_integration_ready_requires_verification_and_merge_order_position(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(
                paths,
                [
                    _event("alpha", "executor_dispatch_observed", minutes_ago=40),
                    _event("alpha", "executor_result_observed", minutes_ago=35),
                    _event("beta", "executor_dispatch_observed", minutes_ago=30),
                    _event("beta", "executor_result_observed", minutes_ago=25),
                    _event("beta", "unit_verification_observed", minutes_ago=20),
                ],
            )

            roster = project_fanout_status(paths, _FANOUT_ID)

            by_unit = {unit["unit_id"]: unit for unit in roster["units"]}
            # `beta` has its own verification receipt, but `alpha` sits ahead of
            # it without one, so nothing is integration ready.
            self.assertEqual(by_unit["beta"]["lifecycle_state"], "unit_verification_observed")
            self.assertEqual(by_unit["alpha"]["lifecycle_state"], "process_succeeded")
            self.assertEqual(roster["integration_ready_units"], [])

    def test_integration_ready_needs_the_whole_observed_chain(self) -> None:
        """Verification alone is not integration eligibility; the sidecar rung counts."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(
                paths,
                [
                    _event("alpha", "executor_dispatch_observed", minutes_ago=40),
                    _event("alpha", "executor_result_observed", minutes_ago=35),
                    _event("alpha", "unit_result_validated", minutes_ago=32),
                    _event("alpha", "unit_verification_observed", minutes_ago=30),
                    _event("beta", "executor_dispatch_observed", minutes_ago=25),
                    _event("beta", "executor_result_observed", minutes_ago=20),
                    _event("beta", "unit_verification_observed", minutes_ago=15),
                ],
            )

            roster = project_fanout_status(paths, _FANOUT_ID)

            by_unit = {unit["unit_id"]: unit for unit in roster["units"]}
            self.assertEqual(by_unit["alpha"]["lifecycle_state"], "integration_ready")
            # `beta` never had a validated result sidecar observed.
            self.assertEqual(by_unit["beta"]["lifecycle_state"], "unit_verification_observed")
            self.assertEqual(roster["integration_ready_units"], ["alpha"])

    def test_summary_text_never_promotes_state_over_events(self) -> None:
        """A journal summary echoing success cannot lift the lifecycle state."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            misleading = _event("liar", "executor_dispatch_observed", minutes_ago=5)
            misleading["summary"] = "unit_verification_observed integration_ready all checks passed"
            _seed_journal(paths, [misleading])

            roster = project_fanout_status(paths, _FANOUT_ID)

            self.assertEqual(roster["units"][0]["lifecycle_state"], "dispatched_not_succeeded")
            self.assertEqual(roster["integration_ready_units"], [])

    def test_render_lists_one_line_per_unit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())

            text = render_fanout_status_text(project_fanout_status(paths, _FANOUT_ID))

            self.assertIn(_FANOUT_ID, text)
            self.assertIn("core", text)
            self.assertIn("unit_verification_observed", text)
            self.assertIn("codex", text)

    def test_empty_fanout_reports_no_units_observed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(
                paths,
                [
                    {
                        "schema_version": "omh_observation_event/v1",
                        "event_id": "fanout-level-1",
                        "target_type": "run",
                        "target_id": _FANOUT_ID,
                        "run_id": _FANOUT_ID,
                        "event": "prepared_handoff_created",
                        "status": "observed",
                        "observed_at": _stamp(5),
                        "summary": "contract frozen",
                        "privacy": "metadata_only",
                        "evidence_refs": [],
                    }
                ],
            )

            roster = project_fanout_status(paths, _FANOUT_ID)

            self.assertEqual(roster["units"], [])
            self.assertEqual(roster["unit_count"], 0)
            self.assertIn("no unit events", render_fanout_status_text(roster).lower())

    def test_unknown_fanout_id_raises_naming_the_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())

            with self.assertRaises(ValueError) as caught:
                project_fanout_status(paths, "fanout-aaaaaaaaaaaa")

            self.assertIn("fanout-aaaaaaaaaaaa", str(caught.exception))

    def test_malformed_fanout_id_raises_naming_the_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())

            with self.assertRaises(ValueError) as caught:
                project_fanout_status(paths, "nope")

            self.assertIn("nope", str(caught.exception))


class _FakeCompleted:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.stdout = "done"
        self.stderr = ""


def _real_git_runner(argv, **kwargs):
    """Run git for real; answer any agent-CLI spawn with a clean exit."""
    if argv[0] == "git":
        return subprocess.run(argv, **kwargs)
    return _FakeCompleted(0)


class FanoutStatusAgainstRealDispatchTests(unittest.TestCase):
    def test_roster_reads_events_a_real_dispatch_wrote(self) -> None:
        """The roster's unit discovery must match the dispatcher's run_ref convention."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True, text=True)
            (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"],
                cwd=repo, check=True, capture_output=True, text=True,
            )
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
            ).stdout.strip()
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            goal = "split the sample feature across agents"
            contract = write_fanout_contract(
                paths,
                build_fanout_contract(
                    goal,
                    [
                        {"unit_id": "core", "title": "Core", "owner": "codex", "file_scope": ["src/core/"]},
                        {"unit_id": "docs", "title": "Docs", "owner": "claude-code", "file_scope": ["docs/"]},
                    ],
                ),
            )
            dispatch_fanout(
                paths,
                contract,
                goal_text=goal,
                repo_root=repo,
                base_sha=sha,
                runner=_real_git_runner,
                readiness=lambda paths, profile, **kwargs: {"status": "ready", "profile": profile},
            )

            roster = project_fanout_status(paths, str(contract["fanout_id"]))

            by_unit = {unit["unit_id"]: unit for unit in roster["units"]}
            self.assertEqual(sorted(by_unit), ["core", "docs"])
            self.assertEqual(by_unit["core"]["lifecycle_state"], "process_succeeded")
            self.assertEqual(by_unit["core"]["owner"], "codex")
            self.assertEqual(by_unit["docs"]["owner"], "claude-code")
            self.assertTrue(by_unit["core"]["worktree_path"].endswith("repo-fanout-core"))
            # Nothing observed a verification receipt or a validated sidecar.
            self.assertEqual(roster["integration_ready_units"], [])


class FanoutStatusCliTests(unittest.TestCase):
    def test_cli_renders_roster_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            status, stdout, stderr = run_cli(
                base + ["coding", "fanout", "status", "--fanout-id", _FANOUT_ID],
                output_json=False,
            )

            self.assertEqual(status, 0, stderr)
            self.assertIn("core", stdout)
            self.assertIn("unit_verification_observed", stdout)
            self.assertIn("process_succeeded", stdout)

    def test_cli_json_output_carries_schema_version(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            status, stdout, stderr = run_cli(
                base + ["coding", "fanout", "status", "--fanout-id", _FANOUT_ID, "--json"]
            )

            self.assertEqual(status, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["schema_version"], FANOUT_STATUS_SCHEMA_VERSION)
            self.assertEqual(len(payload["units"]), 3)

    def test_cli_writes_no_state_and_no_events(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())
            before = sorted(
                (str(path.relative_to(root)), path.stat().st_mtime_ns)
                for path in (root / ".omh").rglob("*")
                if path.is_file()
            )
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            status, _stdout, stderr = run_cli(
                base + ["coding", "fanout", "status", "--fanout-id", _FANOUT_ID],
                output_json=False,
            )

            self.assertEqual(status, 0, stderr)
            after = sorted(
                (str(path.relative_to(root)), path.stat().st_mtime_ns)
                for path in (root / ".omh").rglob("*")
                if path.is_file()
            )
            self.assertEqual(before, after)

    def test_cli_unknown_fanout_id_exits_nonzero_naming_the_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            status, stdout, stderr = run_cli(
                base + ["coding", "fanout", "status", "--fanout-id", "nope"],
                output_json=False,
            )

            self.assertNotEqual(status, 0)
            self.assertIn("nope", stdout + stderr)


def _summary_written(paths: OmhPaths, core_row: dict[str, object], *, mtime_offset: float) -> Path:
    """A dispatch summary whose file time is `mtime_offset` seconds from now."""
    summary_path = fanout_dispatch_summary_path(paths, _FANOUT_ID)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps({
        "fanout_id": _FANOUT_ID,
        "units": [
            {"unit_id": "core", **core_row},
            {"unit_id": "docs", "unit_state": "verified"},
            {"unit_id": "flaky", "unit_state": "verified"},
        ],
    }), encoding="utf-8")
    moment = time.time() + mtime_offset
    os.utime(summary_path, (moment, moment))
    return summary_path

def _redispatched_core(paths: OmhPaths) -> None:
    """An earlier dispatch's summary, and a fresh marker for `core` with no reading yet.

    The recovery path: `core` hit its account limit an hour ago, every
    other unit verified, and `core` was just dispatched again. Until the
    new process's first output snapshot the marker carries no state.
    """
    _seed_journal(paths, _roster_fixture())
    _summary_written(
        paths, {"unit_state": "account_limit", "unit_state_reason": "session limit"}, mtime_offset=-3600,
    )
    write_inflight_marker(paths, _FANOUT_ID, "core", {
        "owner": "codex", "run_ref": f"{_FANOUT_ID}-core",
        "worktree": "/tmp/wt/core", "started_at": _stamp(0),
    })

def _leaked_reservation(paths: OmhPaths) -> None:
    """A capacity-reuse reservation marker this attempt never cleared, then its own summary.

    The worktree reuse writes a marker with owner, run ref and worktree
    only. Every return clears it, but the clear is best-effort: one that
    failed after a refusal (here the unfenced refusal, which records
    `permission_blocked`) leaves the marker, and the dispatch then writes
    its summary. So the marker is OLDER than the summary row it would
    otherwise hide.
    """
    _seed_journal(paths, _roster_fixture())
    write_inflight_marker(paths, _FANOUT_ID, "core", {
        "owner": "codex", "run_ref": f"{_FANOUT_ID}-core", "worktree": "/tmp/wt/core",
    })
    _summary_written(
        paths, {"unit_state": "permission_blocked", "reason": "no filesystem fence"}, mtime_offset=60,
    )


class FanoutStatusUnitProgressTests(unittest.TestCase):
    """The supervisor poll loop's inputs: is this unit's WORK moving, and is
    it over? Neither question is a ladder rung, and neither can move
    `lifecycle_state` -- see the module docstring's scoped exception."""

    def _stalled_marker(self, paths: OmhPaths, unit_id: str = "core") -> None:
        write_inflight_marker(paths, _FANOUT_ID, unit_id, {
            "owner": "codex",
            "run_ref": f"{_FANOUT_ID}-{unit_id}",
            "worktree": f"/tmp/wt/{unit_id}",
            "started_at": _stamp(40),
            "unit_state": "progress_stalled",
            "state_reason": "repeated_error:error: unable to read file, retrying (n=<n>)",
            "stalled_for_seconds": "1920",
        })

    def test_a_stalled_marker_reaches_the_json_roster_as_non_terminal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())
            self._stalled_marker(paths)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            status, stdout, stderr = run_cli(
                base + ["coding", "fanout", "status", "--fanout-id", _FANOUT_ID, "--json"]
            )

            self.assertEqual(status, 0, stderr)
            payload = json.loads(stdout)
            core = {unit["unit_id"]: unit for unit in payload["units"]}["core"]
            self.assertEqual(core["unit_state"], "progress_stalled")
            self.assertIs(core["terminal"], False)
            self.assertEqual(core["unit_state_source"], "inflight_marker")
            self.assertTrue(core["progress"]["reason"].startswith("repeated_error:"))
            self.assertEqual(core["progress"]["seconds_since_new_output"], 1920)
            # The roster-level stop condition the poll loop reads.
            self.assertIn("core", payload["stuck_units"])
            self.assertIs(payload["all_units_terminal"], False)
            # The ladder is untouched: the marker moved no rung.
            self.assertEqual(core["lifecycle_state"], "unit_verification_observed")

    def test_the_text_roster_names_the_stuck_unit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())
            self._stalled_marker(paths)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            status, stdout, stderr = run_cli(
                base + ["coding", "fanout", "status", "--fanout-id", _FANOUT_ID],
                output_json=False,
            )

            self.assertEqual(status, 0, stderr)
            self.assertIn("progress_stalled", stdout)
            self.assertIn("1920s since new output", stdout)
            self.assertIn("not terminal", stdout)
            self.assertIn("Stuck, needs intervention: core", stdout)

    def test_a_dispatch_summary_supplies_the_state_after_exit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())
            summary_path = fanout_dispatch_summary_path(paths, _FANOUT_ID)
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps({
                "fanout_id": _FANOUT_ID,
                "units": [
                    {"unit_id": "core", "unit_state": "verified", "unit_state_reason": "",
                     "progress": {"stalled_for_seconds": 0}},
                    {"unit_id": "docs", "unit_state": "failed", "unit_state_reason": "result_missing",
                     "progress": {"stalled_for_seconds": 12}},
                ],
            }), encoding="utf-8")

            roster = project_fanout_status(paths, _FANOUT_ID)
            by_unit = {unit["unit_id"]: unit for unit in roster["units"]}

            self.assertEqual(by_unit["core"]["unit_state"], "verified")
            self.assertIs(by_unit["core"]["terminal"], True)
            self.assertEqual(by_unit["core"]["unit_state_source"], "dispatch_summary")
            self.assertEqual(by_unit["docs"]["unit_state"], "failed")
            self.assertEqual(by_unit["docs"]["progress"]["reason"], "result_missing")
            self.assertIs(by_unit["docs"]["terminal"], True)
            self.assertEqual(roster["stuck_units"], [])
            # `flaky` has neither marker nor summary row, so the fanout is not
            # done -- absent evidence must never read as finished.
            self.assertEqual(by_unit["flaky"]["unit_state"], "unknown")
            self.assertIs(by_unit["flaky"]["terminal"], False)
            self.assertEqual(by_unit["flaky"]["unit_state_source"], "none")
            self.assertIsNone(by_unit["flaky"]["progress"]["seconds_since_new_output"])
            self.assertIs(roster["all_units_terminal"], False)

    def test_a_unit_refused_before_its_spawn_is_terminal_and_named(self) -> None:
        # The workspace preflight (#1489) refuses a unit before it spawns: the
        # summary row carries a stuck `unit_state` and the preflight's own
        # `reason`, with no marker and no progress evidence. Nothing exists to
        # move it, so reporting it non-terminal would hang a poll loop forever
        # on a unit that never started.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())
            summary_path = fanout_dispatch_summary_path(paths, _FANOUT_ID)
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps({
                "fanout_id": _FANOUT_ID,
                "units": [{
                    "unit_id": "core",
                    "status": "worktree_failed",
                    "reason_code": "workspace_preflight_blocked",
                    "failure_kind": "workspace_blocked",
                    "unit_state": "permission_blocked",
                    "reason": "worktree is not writable",
                }],
            }), encoding="utf-8")

            roster = project_fanout_status(paths, _FANOUT_ID)
            core = {unit["unit_id"]: unit for unit in roster["units"]}["core"]

            self.assertEqual(core["unit_state"], "permission_blocked")
            self.assertIs(core["terminal"], True)
            self.assertEqual(core["unit_state_source"], "dispatch_summary")
            # The preflight's own wording survives; a pre-spawn refusal never
            # reached the code that writes `unit_state_reason`.
            self.assertEqual(core["progress"]["reason"], "worktree is not writable")
            self.assertIsNone(core["progress"]["seconds_since_new_output"])
            # Terminal and still needing a human: both are true at once, and
            # the roster says so rather than picking one.
            self.assertIn("core", roster["stuck_units"])
            self.assertIn("needs intervention: core", render_fanout_status_text(roster))

    def test_a_blocked_unit_with_only_a_failure_kind_still_explains_itself(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())
            summary_path = fanout_dispatch_summary_path(paths, _FANOUT_ID)
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps({
                "fanout_id": _FANOUT_ID,
                "units": [{"unit_id": "core", "unit_state": "data_missing",
                           "failure_kind": "workspace_blocked"}],
            }), encoding="utf-8")

            core = {u["unit_id"]: u for u in project_fanout_status(paths, _FANOUT_ID)["units"]}["core"]

            self.assertEqual(core["progress"]["reason"], "workspace_blocked")
            self.assertIs(core["terminal"], True)

    def test_a_live_marker_outranks_a_stale_summary_row(self) -> None:
        # A marker means this unit is in flight NOW; a summary row from an
        # earlier dispatch of the same id must not report over it.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())
            summary_path = fanout_dispatch_summary_path(paths, _FANOUT_ID)
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps({
                "fanout_id": _FANOUT_ID,
                "units": [{"unit_id": "core", "unit_state": "verified"}],
            }), encoding="utf-8")
            self._stalled_marker(paths)

            roster = project_fanout_status(paths, _FANOUT_ID)
            core = {unit["unit_id"]: unit for unit in roster["units"]}["core"]

            self.assertEqual(core["unit_state"], "progress_stalled")
            self.assertEqual(core["unit_state_source"], "inflight_marker")
            self.assertIs(core["terminal"], False)

    def test_a_roster_with_no_progress_evidence_renders_as_it_always_did(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())

            roster = project_fanout_status(paths, _FANOUT_ID)
            text = render_fanout_status_text(roster)

            for unit in roster["units"]:
                self.assertEqual(unit["unit_state"], "unknown")
                self.assertIs(unit["terminal"], False)
            self.assertNotIn("  work:", text)
            self.assertNotIn("Stuck, needs intervention", text)
            self.assertIs(roster["all_units_terminal"], False)

    def test_a_marker_with_no_assessed_state_claims_nothing(self) -> None:
        # Written before the first stdout snapshot: it proves a spawn, not
        # that the work is moving. It is still the in-flight reading, so its
        # source is the marker and its state is `unknown`.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())
            write_inflight_marker(paths, _FANOUT_ID, "core", {
                "owner": "codex", "run_ref": f"{_FANOUT_ID}-core",
                "worktree": "/tmp/wt/core", "started_at": _stamp(1),
            })

            roster = project_fanout_status(paths, _FANOUT_ID)
            core = {unit["unit_id"]: unit for unit in roster["units"]}["core"]

            self.assertEqual(core["unit_state"], "unknown")
            self.assertEqual(core["unit_state_source"], "inflight_marker")
            self.assertIs(core["terminal"], False)

    def test_a_leaked_reservation_marker_never_hides_its_own_attempts_summary_row(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _leaked_reservation(paths)

            roster = project_fanout_status(paths, _FANOUT_ID)
            core = {unit["unit_id"]: unit for unit in roster["units"]}["core"]
            text = render_fanout_status_text(roster)

            self.assertEqual(core["unit_state"], "permission_blocked")
            self.assertEqual(core["unit_state_source"], "dispatch_summary")
            self.assertIs(core["terminal"], True)
            self.assertEqual(roster["stuck_units"], ["core"])
            self.assertIn("Stuck, needs intervention: core", text)
            self.assertNotIn("in flight", text)

    def test_the_rows_recorded_finish_decides_before_the_file_time(self) -> None:
        # A partial re-dispatch of another unit rewrites the file, so its time
        # can be later than the row it carries forward; the row's own
        # `finished_at` is the better answer whenever it is recorded.
        for finished_minutes_ago, mtime_offset, hidden in ((-60, -3600, False), (60, 3600, True)):
            with self.subTest(finished_minutes_ago=finished_minutes_ago), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
                _seed_journal(paths, _roster_fixture())
                write_inflight_marker(paths, _FANOUT_ID, "core", {
                    "owner": "codex", "run_ref": f"{_FANOUT_ID}-core", "worktree": "/tmp/wt/core",
                })
                _summary_written(
                    paths, {"unit_state": "account_limit", "finished_at": _stamp(finished_minutes_ago)},
                    mtime_offset=mtime_offset,
                )

                core = {u["unit_id"]: u for u in project_fanout_status(paths, _FANOUT_ID)["units"]}["core"]

                self.assertEqual(core["unit_state"], "unknown" if hidden else "account_limit")

    def test_a_marker_wins_only_when_strictly_later_than_the_row(self) -> None:
        recorded = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
        written_after = fanout_status_module._written_after
        self.assertTrue(written_after("2026-10-13T12:00:01Z", recorded))
        # Whole seconds: the same second is not later.
        self.assertFalse(written_after("2026-10-13T12:00:00Z", recorded))
        self.assertFalse(written_after("2026-10-13T11:59:59Z", recorded))
        self.assertFalse(written_after("2026-10-13T12:00:01Z", None))
        self.assertFalse(written_after("", recorded))
        self.assertFalse(written_after("not a time", recorded))

    def test_without_a_readable_time_on_either_side_the_summary_row_stands(self) -> None:
        with self.subTest(side="summary"), TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _redispatched_core(paths)

            with mock.patch.object(fanout_status_module, "_file_mtime", return_value=None):
                core = {u["unit_id"]: u for u in project_fanout_status(paths, _FANOUT_ID)["units"]}["core"]

            self.assertEqual(core["unit_state"], "account_limit")
        with self.subTest(side="marker"), TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _seed_journal(paths, _roster_fixture())
            _summary_written(paths, {"unit_state": "account_limit"}, mtime_offset=-3600)
            marker = write_inflight_marker(paths, _FANOUT_ID, "core", {
                "owner": "codex", "run_ref": f"{_FANOUT_ID}-core", "worktree": "/tmp/wt/core",
            })
            payload = json.loads(marker.read_text(encoding="utf-8"))
            payload["written_at"] = "not a time"
            marker.write_text(json.dumps(payload), encoding="utf-8")

            core = {u["unit_id"]: u for u in project_fanout_status(paths, _FANOUT_ID)["units"]}["core"]

            self.assertEqual(core["unit_state"], "account_limit")

    def test_a_fresh_marker_without_state_hides_the_earlier_dispatchs_summary_row(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _redispatched_core(paths)

            roster = project_fanout_status(paths, _FANOUT_ID)
            core = {unit["unit_id"]: unit for unit in roster["units"]}["core"]
            text = render_fanout_status_text(roster)

            self.assertEqual(core["unit_state"], "unknown")
            self.assertEqual(core["unit_state_source"], "inflight_marker")
            self.assertIs(core["terminal"], False)
            self.assertEqual(core["progress"]["reason"], "")
            self.assertEqual(roster["stuck_units"], [])
            self.assertIs(roster["all_units_terminal"], False)
            # A plain read says the unit is in flight and not yet assessed,
            # and nothing of the earlier attempt's limit reaches it.
            self.assertIn("  work: unknown (in flight, no output assessed yet), not terminal", text)
            self.assertNotIn("Stuck, needs intervention", text)
            self.assertNotIn("account_limit", text)
            self.assertNotIn("session limit", text)

    def test_a_watcher_over_a_redispatched_unit_waits_instead_of_reporting_the_old_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _redispatched_core(paths)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            clock = _FakeClock()

            with mock.patch.object(fanout_status_module, "time", clock):
                status, stdout, stderr = run_cli(
                    base + ["coding", "fanout", "status", "--fanout-id", _FANOUT_ID,
                            "--wait", "--timeout", "60", "--interval", "20", "--json"],
                )

            self.assertEqual(status, FANOUT_STATUS_WAIT_TIMEOUT_EXIT_CODE, stderr)
            self.assertEqual(clock.sleeps, [20.0, 20.0, 20.0])
            payload = json.loads(stdout)
            self.assertEqual(payload["wait"]["outcome"], WAIT_OUTCOME_TIMED_OUT)
            self.assertEqual(payload["stuck_units"], [])


class _FakeClock:
    """A monotonic clock that moves only when the wait sleeps, so no test sleeps for real."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _roster(*, stuck: list[str] | None = None, terminal: bool = False) -> dict[str, object]:
    return {"units": [{"unit_id": "core"}], "stuck_units": stuck or [], "all_units_terminal": terminal}


class FanoutStatusWaitLoopTests(unittest.TestCase):
    """`wait_for_fanout_status` stops on the roster's own answer, then on the clock."""

    def _wait(self, rosters: list[dict[str, object]], *, timeout: int = 600, interval: int = 30):
        clock = _FakeClock()
        reads = iter(rosters)
        result = wait_for_fanout_status(
            lambda: next(reads), timeout_seconds=timeout, interval_seconds=interval,
            clock=clock.monotonic, sleep=clock.sleep,
        )
        return result, clock

    def test_a_stuck_unit_stops_the_wait_at_the_read_that_shows_it(self) -> None:
        result, clock = self._wait([_roster(stuck=["core"])])

        self.assertEqual(result["wait"]["outcome"], WAIT_OUTCOME_STUCK_UNITS)
        self.assertEqual(result["wait"]["reads"], 1)
        self.assertEqual(clock.sleeps, [])

    def test_a_stuck_unit_outranks_a_finished_roster(self) -> None:
        # A summary row at `account_limit` is terminal and still needs a person.
        result, _clock = self._wait([_roster(stuck=["core"], terminal=True)])

        self.assertEqual(result["wait"]["outcome"], WAIT_OUTCOME_STUCK_UNITS)

    def test_the_wait_reads_until_every_unit_is_terminal(self) -> None:
        result, clock = self._wait([_roster(), _roster(), _roster(terminal=True)], interval=30)

        self.assertEqual(result["wait"]["outcome"], WAIT_OUTCOME_ALL_UNITS_TERMINAL)
        self.assertEqual(result["wait"]["reads"], 3)
        self.assertEqual(clock.sleeps, [30.0, 30.0])
        self.assertEqual(result["wait"]["waited_seconds"], 60)
        self.assertIs(result["all_units_terminal"], True)

    def test_the_deadline_ends_a_wait_that_never_concludes(self) -> None:
        clock = _FakeClock()
        reads: list[int] = []

        def read() -> dict[str, object]:
            reads.append(1)
            return _roster()

        result = wait_for_fanout_status(
            read, timeout_seconds=70, interval_seconds=30, clock=clock.monotonic, sleep=clock.sleep,
        )

        self.assertEqual(result["wait"]["outcome"], WAIT_OUTCOME_TIMED_OUT)
        # The last pause is cut to what is left, so the wait never overruns.
        self.assertEqual(clock.sleeps, [30.0, 30.0, 10.0])
        self.assertEqual(len(reads), 4)
        self.assertEqual(result["wait"]["waited_seconds"], 70)
        self.assertEqual(result["wait"]["timeout_seconds"], 70)

    def test_an_answer_read_at_the_deadline_still_wins_over_the_deadline(self) -> None:
        result, _clock = self._wait([_roster(), _roster(terminal=True)], timeout=30, interval=30)

        self.assertEqual(result["wait"]["outcome"], WAIT_OUTCOME_ALL_UNITS_TERMINAL)

    def test_a_read_error_is_raised_rather_than_waited_on(self) -> None:
        def read() -> dict[str, object]:
            raise ValueError("unknown fanout id: fanout-0123456789ab (no journal events name it)")

        # One clock for both seams, so a loop that swallowed the error would
        # reach its deadline and fail this test rather than spin forever.
        clock = _FakeClock()
        with self.assertRaises(ValueError):
            wait_for_fanout_status(read, timeout_seconds=600, interval_seconds=30,
                                   clock=clock.monotonic, sleep=clock.sleep)
        self.assertEqual(clock.sleeps, [])


class FanoutStatusWaitExitCodeTests(unittest.TestCase):
    """Every way the watcher stops maps to its own code, and only one of them is 0."""

    def _graded(self, outcome: str, units: list[dict[str, object]], stuck: list[str] | None = None) -> int:
        return _fanout_status_wait_exit_code(
            {"units": units, "stuck_units": stuck or [], "wait": {"outcome": outcome}}
        )

    def test_every_unit_verified_is_the_only_success(self) -> None:
        # Every outcome, every state beside a verified sibling, with and without
        # a torn journal line, a failure kind, or a run-level stop: 0 only for
        # a finished roster whose units all verified and nothing else is wrong.
        outcomes = (WAIT_OUTCOME_STUCK_UNITS, WAIT_OUTCOME_ALL_UNITS_TERMINAL, WAIT_OUTCOME_TIMED_OUT)
        damage = (None, "journal_errors", "failure_kind", "interrupted", "refused")
        successes = []
        for outcome in outcomes:
            for state in UNIT_EXECUTION_STATES:
                for flaw in damage:
                    units: list[dict[str, object]] = [
                        {"unit_id": "a", "unit_state": "verified"},
                        {"unit_id": "b", "unit_state": state},
                    ]
                    roster: dict[str, object] = {
                        "units": units,
                        "stuck_units": ["b"] if state in UNIT_STUCK_STATES else [],
                        "wait": {"outcome": outcome},
                    }
                    if flaw == "journal_errors":
                        roster["journal_errors"] = ["events.jsonl:8: torn"]
                    elif flaw == "failure_kind":
                        units[0]["failure_kind"] = "crash"
                    elif flaw is not None:
                        roster[flaw] = True
                    if _fanout_status_wait_exit_code(roster) == 0:
                        successes.append((outcome, state, flaw))
        self.assertEqual(successes, [(WAIT_OUTCOME_ALL_UNITS_TERMINAL, "verified", None)])

    def test_a_finished_unit_that_did_not_verify_is_a_failure(self) -> None:
        units = [{"unit_id": "a", "unit_state": "verified"}, {"unit_id": "b", "unit_state": "failed"}]
        self.assertEqual(self._graded(WAIT_OUTCOME_ALL_UNITS_TERMINAL, units), 1)

    def test_every_stuck_state_stops_with_the_stuck_code(self) -> None:
        for state in sorted(UNIT_STUCK_STATES):
            with self.subTest(state=state):
                units = [{"unit_id": "a", "unit_state": state}]
                self.assertEqual(
                    self._graded(WAIT_OUTCOME_STUCK_UNITS, units, stuck=["a"]),
                    FANOUT_STATUS_WAIT_STUCK_EXIT_CODE,
                )

    def test_a_timeout_is_its_own_code(self) -> None:
        units = [{"unit_id": "a", "unit_state": "running"}]
        self.assertEqual(self._graded(WAIT_OUTCOME_TIMED_OUT, units), FANOUT_STATUS_WAIT_TIMEOUT_EXIT_CODE)

    def test_an_unreadable_journal_line_keeps_a_clean_roster_from_reading_as_success(self) -> None:
        # The line could have named a unit that is now missing from the roster.
        roster = {
            "units": [{"unit_id": "a", "unit_state": "verified"}],
            "stuck_units": [],
            "journal_errors": ["events.jsonl:8: Unterminated string"],
            "wait": {"outcome": WAIT_OUTCOME_ALL_UNITS_TERMINAL},
        }
        self.assertEqual(_fanout_status_wait_exit_code(roster), FANOUT_STATUS_WAIT_JOURNAL_UNREADABLE_EXIT_CODE)

    def test_a_concrete_answer_outranks_an_unreadable_journal_line(self) -> None:
        errors = ["events.jsonl:8: Unterminated string"]
        for outcome, units, stuck, expected in (
            (WAIT_OUTCOME_STUCK_UNITS, [{"unit_id": "a", "unit_state": "awaiting_input"}], ["a"],
             FANOUT_STATUS_WAIT_STUCK_EXIT_CODE),
            (WAIT_OUTCOME_TIMED_OUT, [{"unit_id": "a", "unit_state": "running"}], [],
             FANOUT_STATUS_WAIT_TIMEOUT_EXIT_CODE),
            (WAIT_OUTCOME_ALL_UNITS_TERMINAL, [{"unit_id": "a", "unit_state": "failed"}], [], 1),
        ):
            with self.subTest(outcome=outcome, expected=expected):
                roster = {"units": units, "stuck_units": stuck, "journal_errors": errors, "wait": {"outcome": outcome}}
                self.assertEqual(_fanout_status_wait_exit_code(roster), expected)

    def test_the_codes_are_distinct_and_none_collides_with_success_an_error_or_a_signal(self) -> None:
        codes = {
            FANOUT_STATUS_WAIT_STUCK_EXIT_CODE,
            FANOUT_STATUS_WAIT_TIMEOUT_EXIT_CODE,
            FANOUT_STATUS_WAIT_JOURNAL_UNREADABLE_EXIT_CODE,
            FANOUT_STATUS_WAIT_CRASH_EXIT_CODE,
        }
        self.assertEqual(len(codes), 4)
        # 0 is success, 1 a finished failure (and Python's own uncaught
        # exception), 2 `main`'s OmhError; 130, 137 and 143 are SIGINT,
        # SIGKILL and SIGTERM as a shell reports them.
        self.assertFalse(codes & {0, 1, 2, 130, 137, 143})

    def test_the_documented_bounds_and_codes_are_the_commands_own(self) -> None:
        from omh.commands import coding as coding_commands

        docs = Path(__file__).resolve().parents[1] / "docs"
        # The two codes the mapper spells as literals, graded rather than typed.
        verified = self._graded(WAIT_OUTCOME_ALL_UNITS_TERMINAL, [{"unit_id": "a", "unit_state": "verified"}])
        failed = self._graded(WAIT_OUTCOME_ALL_UNITS_TERMINAL, [{"unit_id": "a", "unit_state": "failed"}])
        observability = " ".join((docs / "CODING-OBSERVABILITY.md").read_text(encoding="utf-8").split())
        # The FANOUT.md synopsis wraps its comment over `#` lines; read it as one sentence.
        synopsis = " ".join((docs / "FANOUT.md").read_text(encoding="utf-8").split()).replace(" # ", " ")
        for doc, phrase in (
            (observability,
             f"every `--interval` seconds (default {coding_commands.FANOUT_STATUS_WAIT_DEFAULT_INTERVAL_SECONDS}, "
             f"at least {coding_commands.FANOUT_STATUS_WAIT_MIN_INTERVAL_SECONDS})"),
            (observability, f"it exits {FANOUT_STATUS_WAIT_JOURNAL_UNREADABLE_EXIT_CODE} instead of {verified}"),
            (observability, f"exits {FANOUT_STATUS_WAIT_CRASH_EXIT_CODE} with its traceback on stderr"),
            (observability, f"`stuck_units` names a unit (exit {FANOUT_STATUS_WAIT_STUCK_EXIT_CODE})"),
            (observability, f"every unit is terminal (exit {verified} when each is `verified`, else {failed})"),
            (observability,
             f"`--timeout` (default {coding_commands.FANOUT_STATUS_WAIT_DEFAULT_TIMEOUT_SECONDS}, at most "
             f"{coding_commands.FANOUT_STATUS_WAIT_MAX_TIMEOUT_SECONDS}) passes first "
             f"(exit {FANOUT_STATUS_WAIT_TIMEOUT_EXIT_CODE})"),
            (synopsis,
             f"--wait [--timeout {coding_commands.FANOUT_STATUS_WAIT_DEFAULT_TIMEOUT_SECONDS}] "
             f"[--interval {coding_commands.FANOUT_STATUS_WAIT_DEFAULT_INTERVAL_SECONDS}]"),
            (synopsis,
             f"exits {FANOUT_STATUS_WAIT_STUCK_EXIT_CODE} on stuck_units, {verified} when every unit verified, "
             f"{failed} when all are terminal and one is not, {FANOUT_STATUS_WAIT_JOURNAL_UNREADABLE_EXIT_CODE} "
             f"when all verified but journal lines were unreadable, {FANOUT_STATUS_WAIT_TIMEOUT_EXIT_CODE} when "
             f"the timeout passes first, {FANOUT_STATUS_WAIT_CRASH_EXIT_CODE} on a crash"),
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, doc)


class FanoutStatusWaitCliTests(unittest.TestCase):
    """The watcher as a supervisor runs it: one command, one exit code, the last roster."""

    def _run(self, root: Path, *extra: str, output_json: bool = True):
        base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
        return run_cli(
            base + ["coding", "fanout", "status", "--fanout-id", _FANOUT_ID, *extra],
            output_json=output_json,
        )

    def _seed(self, root: Path) -> OmhPaths:
        paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
        _seed_journal(paths, _roster_fixture())
        return paths

    def _summary(self, paths: OmhPaths, states: dict[str, str]) -> None:
        summary_path = fanout_dispatch_summary_path(paths, _FANOUT_ID)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps({
            "fanout_id": _FANOUT_ID,
            "units": [{"unit_id": unit_id, "unit_state": state} for unit_id, state in states.items()],
        }), encoding="utf-8")

    def _no_sleep(self):
        def refuse(_seconds: float) -> None:
            raise AssertionError("the watcher slept on a roster that already answered")

        clock = _FakeClock()
        clock.sleep = refuse  # type: ignore[method-assign]
        return mock.patch.object(fanout_status_module, "time", clock)

    def test_a_unit_waiting_on_a_person_or_a_limit_ends_the_wait_at_once(self) -> None:
        # The two intervention points a per-poll supervisor had by name: a unit
        # asking a question and a unit refused by its provider. Both ride
        # `stuck_units`, so the first read that shows either one ends the wait.
        for state in ("awaiting_input", "account_limit"):
            with self.subTest(state=state), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = self._seed(root)
                write_inflight_marker(paths, _FANOUT_ID, "core", {
                    "owner": "codex", "run_ref": f"{_FANOUT_ID}-core", "worktree": "/tmp/wt/core",
                    "started_at": _stamp(40), "unit_state": state, "state_reason": "observed tail",
                })

                with self._no_sleep():
                    status, stdout, stderr = self._run(root, "--wait", "--json")

                self.assertEqual(status, FANOUT_STATUS_WAIT_STUCK_EXIT_CODE, stderr)
                payload = json.loads(stdout)
                self.assertEqual(payload["wait"]["outcome"], WAIT_OUTCOME_STUCK_UNITS)
                self.assertEqual(payload["wait"]["reads"], 1)
                self.assertEqual(payload["stuck_units"], ["core"])
                self.assertEqual(payload["schema_version"], FANOUT_STATUS_SCHEMA_VERSION)

    def test_every_unit_verified_exits_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._seed(root)
            self._summary(paths, {"core": "verified", "docs": "verified", "flaky": "verified"})

            with self._no_sleep():
                status, stdout, stderr = self._run(root, "--wait", "--json")

            self.assertEqual(status, 0, stderr)
            self.assertEqual(json.loads(stdout)["wait"]["outcome"], WAIT_OUTCOME_ALL_UNITS_TERMINAL)

    def test_a_finished_roster_with_a_failed_unit_exits_one(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._seed(root)
            self._summary(paths, {"core": "verified", "docs": "failed", "flaky": "verified"})

            with self._no_sleep():
                status, stdout, stderr = self._run(root, "--wait", "--json")

            self.assertEqual(status, 1, stderr)
            self.assertEqual(json.loads(stdout)["wait"]["outcome"], WAIT_OUTCOME_ALL_UNITS_TERMINAL)

    def test_a_wait_that_never_concludes_times_out_and_writes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            before = sorted(
                (str(path.relative_to(root)), path.stat().st_mtime_ns)
                for path in (root / ".omh").rglob("*") if path.is_file()
            )
            clock = _FakeClock()

            with mock.patch.object(fanout_status_module, "time", clock):
                status, stdout, stderr = self._run(
                    root, "--wait", "--timeout", "60", "--interval", "20", output_json=False,
                )

            self.assertEqual(status, FANOUT_STATUS_WAIT_TIMEOUT_EXIT_CODE, stderr)
            self.assertEqual(clock.sleeps, [20.0, 20.0, 20.0])
            # The reason the watcher stopped is the last line, where a host
            # relaying only the tail of the output still carries it.
            self.assertEqual(
                stdout.strip().splitlines()[-1],
                "Wait ended: timed_out after 60s (4 reads, timeout 60s).",
            )
            after = sorted(
                (str(path.relative_to(root)), path.stat().st_mtime_ns)
                for path in (root / ".omh").rglob("*") if path.is_file()
            )
            self.assertEqual(before, after)

    def test_an_unknown_fanout_fails_at_once_instead_of_at_the_deadline(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            with self._no_sleep():
                status, stdout, stderr = run_cli(
                    base + ["coding", "fanout", "status", "--fanout-id", "fanout-abcdefabcdef", "--wait"],
                    output_json=False,
                )

            self.assertEqual(status, 2)
            self.assertIn("fanout-abcdefabcdef", stdout + stderr)

    def test_the_bounds_refuse_an_unbounded_or_unwaited_value(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)
            for extra, needle in (
                (("--wait", "--timeout", "0"), "--timeout must be"),
                (("--wait", "--timeout", "999999"), "--timeout must be"),
                (("--wait", "--interval", "0"), "--interval must be"),
                (("--wait", "--interval", "4"), "--interval must be"),
                (("--timeout", "60"), "apply only with --wait"),
                (("--interval", "5"), "apply only with --wait"),
            ):
                with self.subTest(extra=extra), self._no_sleep():
                    status, stdout, stderr = self._run(root, *extra, output_json=False)
                    self.assertEqual(status, 2)
                    self.assertIn(needle, stdout + stderr)

    def test_a_torn_journal_line_keeps_a_clean_looking_roster_from_exiting_zero(self) -> None:
        # `flaky`'s lines are torn, so the roster cannot list it at all, and
        # the two units it can list both verified. The summary that recorded
        # `flaky` failing is never consulted for a unit the journal lost.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            journal = paths.runtime_journal_events_path
            journal.parent.mkdir(parents=True, exist_ok=True)
            lines = []
            for event in _roster_fixture():
                text = json.dumps(event, sort_keys=True)
                lines.append((text[:-3] if "flaky" in text else text) + "\n")
            journal.write_text("".join(lines), encoding="utf-8")
            self._summary(paths, {"core": "verified", "docs": "verified", "flaky": "failed"})

            with self._no_sleep():
                status, stdout, stderr = self._run(root, "--wait", "--json")

            self.assertEqual(status, FANOUT_STATUS_WAIT_JOURNAL_UNREADABLE_EXIT_CODE, stderr)
            payload = json.loads(stdout)
            self.assertEqual([unit["unit_id"] for unit in payload["units"]], ["core", "docs"])
            self.assertTrue(payload["journal_errors"])
            self.assertEqual(payload["wait"]["outcome"], WAIT_OUTCOME_ALL_UNITS_TERMINAL)

    def test_a_watcher_that_crashes_says_so_with_its_own_code(self) -> None:
        # Python's own exit for an uncaught exception is 1, the code that
        # means "every unit terminal and one not verified".
        def crash(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("roster reader broke")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)

            with mock.patch.object(fanout_status_module, "project_fanout_status", crash), self._no_sleep():
                status, stdout, stderr = self._run(root, "--wait", output_json=False)
                with self.assertRaises(RuntimeError):
                    # A single read grades nothing, so it keeps Python's own crash.
                    self._run(root, output_json=False)

            self.assertEqual(status, FANOUT_STATUS_WAIT_CRASH_EXIT_CODE)
            self.assertIn("RuntimeError: roster reader broke", stderr)
            self.assertEqual(stdout, "")

    def test_a_watcher_over_a_leaked_reservation_marker_stops_on_the_stuck_unit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            _leaked_reservation(paths)

            with self._no_sleep():
                status, stdout, stderr = self._run(root, "--wait", "--json")

            self.assertEqual(status, FANOUT_STATUS_WAIT_STUCK_EXIT_CODE, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["wait"]["reads"], 1)
            self.assertEqual(payload["stuck_units"], ["core"])

    def test_an_omh_error_inside_the_watcher_stays_the_commands_own_error(self) -> None:
        # OmhError is a RuntimeError, so the crash handler must not catch it:
        # a refusal the command words for its caller is exit 2, not a crash.
        def refuse(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise OmhError("journal root refused")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed(root)

            with mock.patch.object(fanout_status_module, "project_fanout_status", refuse), self._no_sleep():
                waited = self._run(root, "--wait", output_json=False)
                single = self._run(root, output_json=False)

            for label, (status, stdout, stderr) in (("wait", waited), ("single", single)):
                with self.subTest(read=label):
                    self.assertEqual(status, 2)
                    self.assertIn("omh: journal root refused", stderr)
                    self.assertNotIn("Traceback", stderr)

    def test_a_single_read_still_exits_zero_whatever_the_roster_says(self) -> None:
        # Without --wait the command reports; it does not grade. Between them
        # the rosters below draw every code the watcher grades a roster with.
        graded_codes = set()
        for label, states, torn in (
            ("verified", {"core": "verified", "docs": "verified", "flaky": "verified"}, False),
            ("failed", {"core": "failed", "docs": "verified", "flaky": "verified"}, False),
            ("stuck", {"core": "account_limit"}, False),
            ("unfinished", {}, False),
            ("verified over a torn line", {"core": "verified", "docs": "verified", "flaky": "verified"}, True),
        ):
            with self.subTest(roster=label), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = self._seed(root)
                if states:
                    self._summary(paths, states)
                if torn:
                    with paths.runtime_journal_events_path.open("a", encoding="utf-8") as stream:
                        stream.write('{"event": "torn\n')

                with mock.patch.object(fanout_status_module, "time", _FakeClock()):
                    graded, _stdout, _stderr = self._run(root, "--wait", "--timeout", "10", "--json")
                graded_codes.add(graded)
                status, stdout, stderr = self._run(root, "--json")

                self.assertEqual(status, 0, stderr)
                self.assertNotIn("wait", json.loads(stdout))
        self.assertEqual(graded_codes, {
            0, 1, FANOUT_STATUS_WAIT_STUCK_EXIT_CODE, FANOUT_STATUS_WAIT_TIMEOUT_EXIT_CODE,
            FANOUT_STATUS_WAIT_JOURNAL_UNREADABLE_EXIT_CODE,
        })


if __name__ == "__main__":
    unittest.main()
