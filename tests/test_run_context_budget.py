from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.paths import resolve_paths
from omh.runtime.context_budget import (
    RUN_CONTEXT_BUDGET_BYTES,
    context_budget_ledger_path,
    degrade_run_payload,
    record_context_emission,
    run_context_budget,
)


class RunContextBudgetTests(unittest.TestCase):
    def test_concurrent_emissions_preserve_exact_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")

            def record(index: int) -> None:
                record_context_emission(
                    paths,
                    "run-1",
                    surface=f"concurrent-{index}",
                    byte_count=10,
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(record, range(8)))

            report = run_context_budget(paths, "run-1")
            self.assertEqual(report["observe_call_count"], 8)
            self.assertEqual(report["emitted_bytes"], 80)
    def test_fresh_run_has_full_budget_and_is_not_exhausted(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")

            budget = run_context_budget(paths, "run-1", surface="runtime_show")

            self.assertFalse(budget["exhausted"])
            self.assertEqual(budget["emitted_bytes"], 0)
            self.assertEqual(budget["remaining_bytes"], RUN_CONTEXT_BUDGET_BYTES)
            self.assertEqual(budget["observe_call_count"], 0)

    def test_emissions_accumulate_per_run_and_per_surface(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")

            record_context_emission(paths, "run-1", surface="runtime_show", byte_count=1000)
            record_context_emission(paths, "run-1", surface="fanout_show", byte_count=500)
            record_context_emission(paths, "run-2", surface="runtime_show", byte_count=7)
            budget = run_context_budget(paths, "run-1")

            self.assertEqual(budget["emitted_bytes"], 1500)
            self.assertEqual(budget["observe_call_count"], 2)
            self.assertEqual(budget["surfaces"], {"runtime_show": 1, "fanout_show": 1})
            self.assertEqual(run_context_budget(paths, "run-2")["emitted_bytes"], 7)
            self.assertTrue(context_budget_ledger_path(paths).exists())

    def test_budget_exhausts_only_past_the_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")

            record_context_emission(paths, "run-1", surface="runtime_show", byte_count=RUN_CONTEXT_BUDGET_BYTES - 1)
            self.assertFalse(run_context_budget(paths, "run-1")["exhausted"])

            record_context_emission(paths, "run-1", surface="runtime_show", byte_count=1)
            self.assertTrue(run_context_budget(paths, "run-1")["exhausted"])

    def test_degraded_payload_keeps_lifecycle_and_points_at_artifacts(self) -> None:
        shown = {
            "run": {"run_id": "run-1", "skill": "plan", "harness": "coding-handling", "status": "started"},
            "lifecycle": {"observation_status": "prepared_not_observed"},
            "history": {"journal_events": {"total": 900, "shown": 20, "omitted": 880}},
            "journal_events": [{"event": "runtime_start_observed", "status": "observed", "observed_at": "now"}],
            "events": [{"event": "noise"} for _ in range(500)],
        }

        degraded = degrade_run_payload(shown, {"run_id": "run-1", "exhausted": True})

        self.assertTrue(degraded["degraded"])
        self.assertNotIn("events", degraded)
        self.assertEqual(degraded["lifecycle"], shown["lifecycle"])
        self.assertEqual(degraded["latest_journal_event"]["event"], "runtime_start_observed")
        self.assertEqual(degraded["history"]["journal_events"]["total"], 900)
        self.assertEqual(degraded["full_history_command"], "omh runtime show run-1 --full")
        self.assertIn("not execution, review, CI", degraded["claim_boundary"])


class RunContextBudgetCliTests(unittest.TestCase):
    def _recorded_run(self, base: list[str]) -> str:
        status, stdout, stderr = run_cli(base + ["runtime", "record", "--skill", "oh-my-hermes", "--harness", "coding-handling"])
        self.assertEqual(stderr, "")
        self.assertEqual(status, 0)
        return json.loads(stdout)["run"]["run_id"]

    def test_runtime_show_records_emission_and_degrades_past_budget(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            paths = resolve_paths(root / ".omh", root / ".hermes")
            run_id = self._recorded_run(base)

            status, stdout, stderr = run_cli(base + ["runtime", "show", run_id])
            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            first = json.loads(stdout)
            self.assertFalse(first["context_budget"]["exhausted"])
            self.assertNotIn("degraded", first)
            self.assertGreater(run_context_budget(paths, run_id)["emitted_bytes"], 0)

            record_context_emission(paths, run_id, surface="runtime_show", byte_count=RUN_CONTEXT_BUDGET_BYTES)

            status, stdout, stderr = run_cli(base + ["runtime", "show", run_id])
            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            degraded = json.loads(stdout)
            self.assertTrue(degraded["degraded"])
            self.assertEqual(degraded["degraded_reason"], "run_context_budget_exhausted")
            self.assertNotIn("events", degraded)
            self.assertIn("lifecycle", degraded)

            status, stdout, stderr = run_cli(base + ["runtime", "show", run_id, "--full"])
            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            full = json.loads(stdout)
            self.assertNotIn("degraded", full)
            self.assertIn("events", full)
            self.assertIsNone(full["history"]["limit"])

    def test_runtime_show_limit_is_bounded_and_validated(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            run_id = self._recorded_run(base)

            status, stdout, stderr = run_cli(base + ["runtime", "show", run_id, "--limit", "1"])
            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stdout)["history"]["limit"], 1)

            status, _, stderr = run_cli(base + ["runtime", "show", run_id, "--limit", "0"])
            self.assertEqual(status, 2)
            self.assertIn("--limit must be at least 1", stderr)


if __name__ == "__main__":
    unittest.main()
