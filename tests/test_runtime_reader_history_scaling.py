from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from tempfile import TemporaryDirectory
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh import runtime_reader


def _write_runs(home: Path, run_ids: list[str]) -> None:
    for run_id in run_ids:
        run_dir = home / "runtime" / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(
            json.dumps({"run_id": run_id, "phase": "planned"}),
            encoding="utf-8",
        )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{json.dumps(row, separators=(',', ':'))}\n" for row in rows), encoding="utf-8")


class RuntimeReaderHistoryScalingTests(unittest.TestCase):
    def _read_with_counters(
        self,
        home: Path,
        *,
        marker: str,
    ) -> tuple[dict[str, object], int, list[Any], list[Any], list[Any]]:
        parse_count = 0
        real_loads = json.loads

        def counting_loads(value: str, *args: object, **kwargs: object) -> object:
            nonlocal parse_count
            if f'"scale":"{marker}"' in value:
                parse_count += 1
            return real_loads(value, *args, **kwargs)

        with (
            patch.object(runtime_reader.json, "loads", side_effect=counting_loads),
            patch.object(runtime_reader, "_read_jsonl", wraps=runtime_reader._read_jsonl) as read_jsonl,
            patch.object(
                runtime_reader,
                "_group_rows_by_run_id",
                wraps=runtime_reader._group_rows_by_run_id,
            ) as group_rows,
            patch.object(runtime_reader, "_summarize_run", wraps=runtime_reader._summarize_run) as summarize,
        ):
            status = runtime_reader.read_omh_status(home, limit=5)

        return (
            status,
            parse_count,
            list(summarize.call_args_list),
            list(read_jsonl.call_args_list),
            list(group_rows.call_args_list),
        )

    def test_five_thousand_lifecycle_rows_are_parsed_once_and_grouped_before_summarizing(self) -> None:
        selected = [f"run-{index:03d}" for index in range(5)]
        rows = [
            {
                "scale": "journal",
                "schema_version": runtime_reader.OBSERVATION_EVENT_SCHEMA_VERSION,
                "run_id": selected[index],
                "event_id": f"selected-{index}",
                "event": "runtime_start",
                "status": "observed",
            }
            for index in range(len(selected))
        ]
        rows.extend(
            {
                "scale": "journal",
                "schema_version": runtime_reader.OBSERVATION_EVENT_SCHEMA_VERSION,
                "run_id": f"historical-{index:05d}",
                "event_id": f"historical-{index:05d}",
                "event": "runtime_start",
                "status": "observed",
            }
            for index in range(5, 5_000)
        )

        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omh"
            _write_runs(home, selected)
            journal_path = (home / "runtime" / "journal" / "events.jsonl").resolve()
            _write_jsonl(journal_path, rows)

            status, parse_count, summarize_calls, jsonl_calls, group_calls = self._read_with_counters(
                home,
                marker="journal",
            )

        receipt_path = journal_path.with_name(runtime_reader.EXTERNAL_EFFECT_RECEIPT_STORE_NAME)
        self.assertEqual(parse_count, 5_000)
        self.assertEqual(sum(call.args[0] == journal_path for call in jsonl_calls), 1)
        self.assertEqual(sum(call.args[0] == receipt_path for call in jsonl_calls), 1)
        self.assertEqual(sorted(len(call.args[0]) for call in group_calls), [0, 5_000])
        self.assertEqual(sum(len(call.kwargs["journal_events"]) for call in summarize_calls), len(selected))
        self.assertEqual([run["journal_event_count"] for run in status["runs"]], [1] * len(selected))

    def test_fifty_thousand_receipt_rows_are_parsed_once_and_only_matching_rows_are_summarized(self) -> None:
        selected = [f"run-{index:03d}" for index in range(5)]
        rows = [
            {
                "scale": "receipt",
                "schema_version": runtime_reader.EXTERNAL_EFFECT_RECEIPT_SCHEMA_VERSION,
                "run_id": selected[index],
                "effect_id": f"ci:{selected[index]}",
                "receipt_id": f"selected-{index}",
                "acting_surface": "github-actions",
                "observed_result": "succeeded",
            }
            for index in range(len(selected))
        ]
        rows.extend(
            {
                "scale": "receipt",
                "schema_version": runtime_reader.EXTERNAL_EFFECT_RECEIPT_SCHEMA_VERSION,
                "run_id": f"historical-{index:05d}",
                "effect_id": f"ci:historical-{index:05d}",
                "receipt_id": f"historical-{index:05d}",
                "acting_surface": "github-actions",
                "observed_result": "succeeded",
            }
            for index in range(5, 50_000)
        )

        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omh"
            _write_runs(home, selected)
            for run_id in selected:
                (home / "runtime" / "runs" / run_id / "ci.json").write_text(
                    json.dumps({"observed": True, "status": "passed"}),
                    encoding="utf-8",
                )
            receipt_path = (
                home / "runtime" / "journal" / runtime_reader.EXTERNAL_EFFECT_RECEIPT_STORE_NAME
            ).resolve()
            _write_jsonl(receipt_path, rows)

            status, parse_count, summarize_calls, jsonl_calls, group_calls = self._read_with_counters(
                home,
                marker="receipt",
            )

        journal_path = receipt_path.with_name("events.jsonl")
        self.assertEqual(parse_count, 50_000)
        self.assertEqual(sum(call.args[0] == journal_path for call in jsonl_calls), 1)
        self.assertEqual(sum(call.args[0] == receipt_path for call in jsonl_calls), 1)
        self.assertEqual(sorted(len(call.args[0]) for call in group_calls), [0, 50_000])
        self.assertEqual(sum(len(call.kwargs["external_effect_receipts"]) for call in summarize_calls), len(selected))
        self.assertTrue(all(run["ci_observed"] for run in status["runs"]))

    def test_grouped_history_matches_full_scan_for_mixed_and_malformed_fixture(self) -> None:
        selected = ["run-a", "run-b"]
        events = [
            {
                "schema_version": runtime_reader.OBSERVATION_EVENT_SCHEMA_VERSION,
                "run_id": "run-a",
                "event_id": "event-a1",
                "event": "runtime_start",
                "status": "observed",
                "summary": "started",
            },
            {"schema_version": "other/v1", "run_id": "run-a", "event": "merge"},
            {"schema_version": runtime_reader.OBSERVATION_EVENT_SCHEMA_VERSION, "run_id": ""},
            {
                "schema_version": runtime_reader.OBSERVATION_EVENT_SCHEMA_VERSION,
                "run_id": "run-b",
                "event_id": "event-b1",
                "event": "verification",
                "status": "failed",
                "summary": "failed verification",
            },
            {
                "schema_version": runtime_reader.OBSERVATION_EVENT_SCHEMA_VERSION,
                "run_id": "unselected",
                "event_id": "historical",
                "event": "merge",
                "status": "observed",
            },
        ]
        receipts = [
            {
                "schema_version": runtime_reader.EXTERNAL_EFFECT_RECEIPT_SCHEMA_VERSION,
                "run_id": "run-a",
                "effect_id": "review:run-a",
                "receipt_id": "receipt-a",
                "acting_surface": "review-tool",
                "observed_result": "succeeded",
            },
            {
                "schema_version": runtime_reader.EXTERNAL_EFFECT_RECEIPT_SCHEMA_VERSION,
                "run_id": "run-b",
                "effect_id": "ci:run-b",
                "receipt_id": "receipt-b",
                "acting_surface": "github-actions",
                "observed_result": "failed",
            },
            {"schema_version": "other/v1", "run_id": "run-a", "effect_id": "merge:run-a"},
            {"schema_version": runtime_reader.EXTERNAL_EFFECT_RECEIPT_SCHEMA_VERSION, "run_id": ""},
        ]

        with TemporaryDirectory() as tmp:
            home = Path(tmp) / ".omh"
            _write_runs(home, selected)
            journal_dir = home / "runtime" / "journal"
            events_path = journal_dir / "events.jsonl"
            receipts_path = journal_dir / runtime_reader.EXTERNAL_EFFECT_RECEIPT_STORE_NAME
            _write_jsonl(events_path, events)
            _write_jsonl(receipts_path, receipts)
            with events_path.open("a", encoding="utf-8") as stream:
                stream.write("{malformed lifecycle row\n[]\n")
            with receipts_path.open("a", encoding="utf-8") as stream:
                stream.write("{malformed receipt row\nnull\n")

            optimized = runtime_reader.read_omh_status(home, limit=5)["runs"]
            parsed_events = runtime_reader._read_jsonl(events_path)
            parsed_receipts = runtime_reader._read_jsonl(receipts_path)
            full_scan = []
            for run_json in sorted((home / "runtime" / "runs").glob("*/run.json"), reverse=True):
                full_scan.append(
                    runtime_reader._summarize_run(
                        run_json.parent,
                        run=runtime_reader._read_json(run_json),
                        journal_events=parsed_events,
                        external_effect_receipts=parsed_receipts,
                    )
                )

        self.assertEqual(optimized, full_scan)


if __name__ == "__main__":
    unittest.main()
