"""#2030: concurrent HUD readers share one fanout scan, and only that.

Every open TUI spawns its own reader every few seconds, and the fanout record
does not depend on the reading session, so the readers share it through a
short-lived entry under the OMH home. These tests pin what makes that sharing
safe: an entry is reused only under a key that still describes the files the
scan reads, it never outlives its TTL, it never crosses homes, any cache fault
costs a scan instead of the HUD, and an error from the scan itself is raised
once rather than retried.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from omh.coding.executor_progress import (  # noqa: E402
    append_progress_event,
    build_progress_binding,
    build_progress_event,
    write_progress_binding,
)
from omh.coding.fanout import build_fanout_contract  # noqa: E402
from omh.coding.fanout_artifacts import (  # noqa: E402
    fanout_dispatch_summary_path,
    write_fanout_contract,
)
from omh.plugin_bundle.omh import runtime_reader  # noqa: E402
from omh.system.paths import OmhPaths  # noqa: E402


def _record(paths: OmhPaths) -> dict[str, object]:
    contract = build_fanout_contract(
        "Shared fanout record",
        [
            {"unit_id": "api", "file_scope": ["src/api/"], "depends_on": []},
            {"unit_id": "web", "file_scope": ["src/web/"], "depends_on": ["api"]},
        ],
    )
    recorded = write_fanout_contract(paths, contract)
    _write_summary(paths, recorded, "prepared_not_observed")
    return recorded


def _write_summary(paths: OmhPaths, recorded: dict[str, object], status: str) -> None:
    units = recorded["units"]
    assert isinstance(units, list)
    fanout_dispatch_summary_path(paths, str(recorded["fanout_id"])).write_text(
        json.dumps(
            {
                "schema_version": "fanout_dispatch_summary/v1",
                "fanout_id": recorded["fanout_id"],
                "units": [
                    {"unit_id": str(unit["unit_id"]), "status": status}
                    for unit in units
                    if isinstance(unit, dict)
                ],
            }
        ),
        encoding="utf-8",
    )


def _statuses(record: object) -> list[str]:
    assert isinstance(record, tuple), record
    return [str(unit["status"]) for unit in record[2]["units"]]


class HudFanoutCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
        self.recorded = _record(self.paths)
        self.home = self.paths.omh_home.resolve()
        self.cache_path = self.home / "runtime" / runtime_reader.HUD_FANOUT_CACHE_FILE

    def _counted(self):
        return patch.object(
            runtime_reader,
            "_compute_hud_local_fanout_record",
            wraps=runtime_reader._compute_hud_local_fanout_record,
        )

    def test_a_second_reader_reuses_the_stored_record(self) -> None:
        direct = runtime_reader._compute_hud_local_fanout_record(self.home)
        with self._counted() as compute:
            first = runtime_reader._hud_local_fanout_record(self.home)
            second = runtime_reader._hud_local_fanout_record(self.home)

        self.assertEqual(compute.call_count, 1)
        self.assertEqual(first, direct)
        # The stored entry round-trips to the very value the scan returns,
        # tuple shape included, so a reader that hits it sees no difference.
        self.assertEqual(second, direct)
        self.assertIsInstance(second, tuple)

    def test_a_status_change_is_read_on_the_next_poll_not_after_the_ttl(self) -> None:
        with self._counted() as compute:
            before = runtime_reader._hud_local_fanout_record(self.home)
            _write_summary(self.paths, self.recorded, "running")
            after = runtime_reader._hud_local_fanout_record(self.home)

        self.assertEqual(compute.call_count, 2)
        self.assertEqual(_statuses(before), ["prepared_not_observed"] * 2)
        self.assertEqual(_statuses(after), ["running"] * 2)

    def test_an_entry_older_than_the_ttl_is_recomputed(self) -> None:
        runtime_reader._hud_local_fanout_record(self.home)
        entry = json.loads(self.cache_path.read_text(encoding="utf-8"))
        entry["stored_at"] -= runtime_reader.HUD_FANOUT_CACHE_TTL_SECONDS + 1
        self.cache_path.write_text(json.dumps(entry), encoding="utf-8")

        with self._counted() as compute:
            runtime_reader._hud_local_fanout_record(self.home)

        self.assertEqual(compute.call_count, 1)

    def test_an_entry_from_another_home_is_never_read(self) -> None:
        runtime_reader._hud_local_fanout_record(self.home)
        other = Path(self._tmp.name) / "other-omh"
        shutil.copytree(self.home, other)

        with self._counted() as compute:
            record = runtime_reader._hud_local_fanout_record(other.resolve())

        self.assertEqual(compute.call_count, 1)
        self.assertEqual(record, runtime_reader._compute_hud_local_fanout_record(self.home))

    def test_a_corrupt_entry_falls_back_to_the_scan(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        for corrupt in ("{not json", "[]", json.dumps({"schema_version": "omh_hud_fanout_cache/v1"})):
            with self.subTest(corrupt=corrupt):
                self.cache_path.write_text(corrupt, encoding="utf-8")
                with self._counted() as compute:
                    record = runtime_reader._hud_local_fanout_record(self.home)
                self.assertEqual(compute.call_count, 1)
                self.assertEqual(_statuses(record), ["prepared_not_observed"] * 2)

    def test_a_lock_that_does_not_come_free_scans_directly(self) -> None:
        @contextmanager
        def held(path: Path, *, timeout_seconds: float):
            raise TimeoutError(f"timed out after {timeout_seconds:g}s waiting for lock: {path}")
            yield "fcntl"

        with patch.object(runtime_reader, "_awareness_delivery_lock", held), self._counted() as compute:
            record = runtime_reader._hud_local_fanout_record(self.home)

        self.assertEqual(compute.call_count, 1)
        self.assertEqual(_statuses(record), ["prepared_not_observed"] * 2)

    def test_an_unwritable_cache_still_returns_the_record(self) -> None:
        with patch.object(runtime_reader, "write_text_atomic", side_effect=PermissionError("read-only")):
            record = runtime_reader._hud_local_fanout_record(self.home)

        self.assertEqual(_statuses(record), ["prepared_not_observed"] * 2)
        self.assertFalse(self.cache_path.exists())

    def test_a_scan_error_reaches_the_caller_exactly_once(self) -> None:
        with patch.object(
            runtime_reader,
            "_compute_hud_local_fanout_record",
            side_effect=RuntimeError("scan failed"),
        ) as compute:
            with self.assertRaises(RuntimeError):
                runtime_reader._hud_local_fanout_record(self.home)

        self.assertEqual(compute.call_count, 1)
        self.assertFalse(self.cache_path.exists())

    def test_a_home_without_fanout_records_writes_nothing(self) -> None:
        bare = Path(self._tmp.name) / "bare-omh"
        bare.mkdir()

        self.assertIsNone(runtime_reader._hud_local_fanout_record(bare.resolve()))
        self.assertEqual(list(bare.iterdir()), [])


class ExecutorProgressLogReadTests(unittest.TestCase):
    def test_logs_are_read_only_for_the_bindings_that_survive_the_cut(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            now = datetime.now(timezone.utc)
            for index in range(5):
                stamp = (now - timedelta(seconds=index)).isoformat().replace("+00:00", "Z")
                binding = build_progress_binding(
                    target_type="run",
                    target_id=f"run-{index}",
                    executor_profile="codex",
                    now=stamp,
                )
                write_progress_binding(paths, binding)
                append_progress_event(
                    paths,
                    binding,
                    build_progress_event(binding, event_type="executor_dispatched", observed_at=stamp),
                )
            runtime_dir = paths.omh_home.resolve() / "runtime"

            with patch.object(
                runtime_reader,
                "_read_hud_jsonl",
                wraps=runtime_reader._read_hud_jsonl,
            ) as read_log:
                bindings = runtime_reader._progress_bindings(runtime_dir, limit=2, hud_safe=True)

            # Two survivors, each with an event log and a report log.
            self.assertEqual(read_log.call_count, 4)
            self.assertEqual(
                [item["binding"]["target_id"] for item in bindings],
                ["run-0", "run-1"],
            )
            self.assertTrue(all(item["latest_event"] for item in bindings))


class HudScanTests(unittest.TestCase):
    """A HUD read lists every run and binding file, but opens only what it can show or has not seen."""

    def setUp(self) -> None:
        runtime_reader._hud_binding_orders.clear()
        self.addCleanup(runtime_reader._hud_binding_orders.clear)

    def test_the_run_list_checks_only_the_newest_runs_it_shows(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            runs = home / "runtime" / "runs"
            for index in range(50):
                (runs / f"run-{index:03d}").mkdir(parents=True)
                (runs / f"run-{index:03d}" / "run.json").write_text(json.dumps({"run_id": f"run-{index:03d}"}), encoding="utf-8")
            (runs / "run-999").mkdir()  # the newest name, with no run.json: passed over
            with patch.object(runtime_reader, "_contains_symlink", wraps=runtime_reader._contains_symlink) as checked:
                status = runtime_reader._read_omh_hud_status(home, limit=5)
            self.assertEqual([run["run_id"] for run in status["runs"]], [f"run-{index:03d}" for index in range(49, 44, -1)])
            # The run list's own checks (the binding scan, and an open where no dir_fd exists, check theirs).
            scanned = [
                call.args[0] for call in checked.call_args_list
                if call.args[0].name == "run.json" and call.kwargs.get("root") == runs
            ]
            self.assertEqual(len(scanned), 6)  # run-999 and the five shown, not all 51

    def test_the_newest_children_are_the_ones_a_sort_of_every_child_keeps(self) -> None:
        with TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            for name in ("a", "a-b", "a.b", "a b", "b", "C", "b0"):
                (runs / name).mkdir(parents=True)
                (runs / name / "run.json").write_text("{}", encoding="utf-8")
            (runs / "z").mkdir()  # the newest name, over the size cap
            (runs / "z" / "run.json").write_text(" " * (runtime_reader.MAX_HUD_METADATA_BYTES + 1), encoding="utf-8")
            (runs / "y").mkdir()  # the next newest, its run.json a symlink
            try:
                (runs / "y" / "run.json").symlink_to(runs / "a" / "run.json")
            except OSError:  # no symlink privilege (Windows): the size case still runs
                pass
            for limit in range(1, 11):
                with self.subTest(limit=limit):
                    self.assertEqual(
                        runtime_reader._newest_hud_child_files(runs, "run.json", limit=limit),
                        sorted(runtime_reader._hud_child_files(runs, "run.json"), reverse=True)[:limit],
                    )

    def test_a_second_read_opens_only_binding_files_written_since_and_the_survivors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
            now = datetime.now(timezone.utc)

            def write(index: int, age: int) -> None:
                stamp = (now - timedelta(seconds=age)).isoformat().replace("+00:00", "Z")
                write_progress_binding(
                    paths, build_progress_binding(target_type="run", target_id=f"run-{index}", executor_profile="codex", now=stamp)
                )

            for index in range(20):
                write(index, age=10 + index)
            runtime_dir = paths.omh_home.resolve() / "runtime"

            def read() -> tuple[list[str], int]:
                with patch.object(runtime_reader, "_read_hud_json", wraps=runtime_reader._read_hud_json) as reader:
                    bindings = runtime_reader._progress_bindings(runtime_dir, limit=2, hud_safe=True)
                opened = [call.args[0] for call in reader.call_args_list if call.args[0].name == "binding.json"]
                return [item["binding"]["target_id"] for item in bindings], len(opened)

            self.assertEqual(read(), (["run-0", "run-1"], 20))
            self.assertEqual(read(), (["run-0", "run-1"], 2))  # only the two survivors, for their content
            write(15, age=0)  # rewritten: now the newest
            self.assertEqual(read(), (["run-15", "run-0"], 2))  # the rewritten file, then run-0
            runtime_reader._hud_binding_orders.clear()
            self.assertEqual(read(), (["run-15", "run-0"], 20))  # the same cut as a read that remembers nothing

    def test_each_read_remembers_only_the_binding_files_it_saw(self) -> None:
        with TemporaryDirectory() as tmp:
            runtime_dir = self._bindings(Path(tmp), 3)
            files = sorted(runtime_dir.rglob("binding.json"))
            runtime_reader._progress_bindings(runtime_dir, limit=2, hud_safe=True)
            self.assertEqual(sorted(runtime_reader._hud_binding_orders), files)
            files[0].unlink()
            runtime_reader._progress_bindings(runtime_dir, limit=2, hud_safe=True)
            self.assertEqual(sorted(runtime_reader._hud_binding_orders), files[1:])

    def test_a_survivor_whose_file_changed_after_its_stat_is_left_out(self) -> None:
        with TemporaryDirectory() as tmp:
            runtime_dir = self._bindings(Path(tmp), 3)
            runtime_reader._progress_bindings(runtime_dir, limit=2, hud_safe=True)  # every order remembered
            newest = next(
                path for path in runtime_dir.rglob("binding.json")
                if json.loads(path.read_text(encoding="utf-8"))["target_id"] == "run-0"
            )
            real = runtime_reader._read_hud_json

            def rewritten_after_its_stat(path: Path, **kwargs: object) -> dict[str, object]:
                return {} if path == newest else real(path, **kwargs)

            with patch.object(runtime_reader, "_read_hud_json", side_effect=rewritten_after_its_stat):
                bindings = runtime_reader._progress_bindings(runtime_dir, limit=2, hud_safe=True)
            self.assertEqual([item["binding"]["target_id"] for item in bindings], ["run-1"])

    @staticmethod
    def _bindings(root: Path, count: int) -> Path:
        """`count` run bindings, run-0 the newest; the runtime directory that holds them."""
        paths = OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")
        now = datetime.now(timezone.utc)
        for index in range(count):
            stamp = (now - timedelta(seconds=10 + index)).isoformat().replace("+00:00", "Z")
            write_progress_binding(
                paths, build_progress_binding(target_type="run", target_id=f"run-{index}", executor_profile="codex", now=stamp)
            )
        return paths.omh_home.resolve() / "runtime"


if __name__ == "__main__":
    unittest.main()
