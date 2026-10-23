from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from _cli_harness import run_cli
from _credential_fixtures import AWS_ACCESS_KEY_ID
from _local_package import load_local_package


load_local_package()
from omh.local_store import atomic_write_json
from omh.paths import resolve_paths
from omh.runtime.run_efficiency import build_run_efficiency_report, parse_run_efficiency_input, write_run_efficiency_report


def _input_payload() -> dict[str, object]:
    return {
        "schema_version": "run_efficiency_input/v1",
        "run_id": "run-123",
        "context_budget": {
            "schema_version": "omh_run_context_budget/v1",
            "run_id": "run-123",
            "budget_bytes": 1000,
            "emitted_bytes": 250,
            "remaining_bytes": 750,
            "surfaces": {"runtime_show": 3, "progress_status": 1},
        },
        "observations": [
            {"metric": "wall_clock_duration_ms", "value": 24, "source_ref": "local-run-timer"},
            {"metric": "context_bytes", "value": 250, "source_ref": "context-budget"},
        ],
    }


class RunEfficiencyReportTests(unittest.TestCase):
    def test_report_projects_only_supplied_local_metrics_and_fixed_unobserved_domains(self) -> None:
        report = build_run_efficiency_report(parse_run_efficiency_input(_input_payload()))

        self.assertEqual(report["schema_version"], "run_efficiency_report/v1")
        self.assertEqual(report["run_id"], "run-123")
        self.assertEqual(report["context_utilization_ratio"], "0.250000")
        self.assertEqual(report["surface_counts"], {"progress_status": 1, "runtime_show": 3})
        self.assertEqual(len(report["observations"]), 2)
        self.assertEqual(
            report["not_observed"],
            {
                "provider": {"status": "not_observed"},
                "billing": {"status": "not_observed"},
                "cron": {"status": "not_observed"},
                "host": {"status": "not_observed"},
            },
        )
        self.assertEqual(report["host_usage"], {"status": "not_observed", "reason": "no Hermes session named"})
        self.assertIn(
            "Neither is provider billing, cron, or host-load evidence, and nothing is intercepted or estimated.",
            report["claim_boundary"],
        )

    def test_parser_rejects_inconsistent_budget_and_unknown_metric(self) -> None:
        inconsistent = _input_payload()
        budget = inconsistent["context_budget"]
        self.assertIsInstance(budget, dict)
        budget["remaining_bytes"] = 749
        with self.assertRaisesRegex(ValueError, "remaining_bytes"):
            parse_run_efficiency_input(inconsistent)

        unknown_metric = _input_payload()
        observations = unknown_metric["observations"]
        self.assertIsInstance(observations, list)
        observations[0]["metric"] = "provider_cost"
        with self.assertRaisesRegex(ValueError, "metric"):
            parse_run_efficiency_input(unknown_metric)

        for secret_source_value in (
            AWS_ACCESS_KEY_ID,
            "AIzaSyDUMMYABCDEFGHIJKLMNOPQRSTUVWX123",
            "npm_12345678901234567890",
            "gho_12345678901234567890",
            "whsec_12345678901234567890",
        ):
            with self.subTest(source_ref=secret_source_value):
                secret_source = _input_payload()
                observations = secret_source["observations"]
                self.assertIsInstance(observations, list)
                observations[0]["source_ref"] = secret_source_value
                with self.assertRaisesRegex(ValueError, "safe opaque metadata reference"):
                    parse_run_efficiency_input(secret_source)

        secret_run_id = _input_payload()
        secret_run_id["run_id"] = "npm_12345678901234567890"
        budget = secret_run_id["context_budget"]
        self.assertIsInstance(budget, dict)
        budget["run_id"] = secret_run_id["run_id"]
        with self.assertRaisesRegex(ValueError, "safe opaque metadata reference"):
            parse_run_efficiency_input(secret_run_id)

        too_many_surfaces = _input_payload()
        budget = too_many_surfaces["context_budget"]
        self.assertIsInstance(budget, dict)
        budget["surfaces"] = {f"surface-{index}": index for index in range(33)}
        with self.assertRaisesRegex(ValueError, "at most 32"):
            parse_run_efficiency_input(too_many_surfaces)

    def test_write_uses_a_deterministic_hash_id_under_the_runtime_efficiency_store(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")
            report = build_run_efficiency_report(parse_run_efficiency_input(_input_payload()))

            written = write_run_efficiency_report(paths, report)

            self.assertTrue(written["written"])
            self.assertTrue(written["path"].startswith(str(paths.runtime_efficiency_reports_dir)))
            self.assertTrue(Path(written["path"]).exists())
            self.assertTrue(written["report_id"].startswith("efficiency_"))

    def test_write_rejects_storage_resolving_outside_omh_home(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            paths.omh_home.mkdir()
            outside = root / "outside"
            outside.mkdir()
            paths.runtime_dir.symlink_to(outside, target_is_directory=True)
            report = build_run_efficiency_report(parse_run_efficiency_input(_input_payload()))

            with self.assertRaisesRegex(ValueError, "resolve under OMH home"):
                write_run_efficiency_report(paths, report)

    def test_cli_writes_only_when_requested(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            omh_home = root / ".omh"
            input_path = root / "input.json"
            atomic_write_json(input_path, _input_payload())

            status, stdout, stderr = run_cli(
                [
                    "--omh-home",
                    str(omh_home),
                    "runtime",
                    "efficiency-report",
                    "--input",
                    str(input_path),
                    "--write",
                ]
            )

            self.assertEqual(status, 0, stderr)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["schema_version"], "run_efficiency_report/v1")
            self.assertTrue(payload["artifact"]["written"])
            self.assertTrue(Path(payload["artifact"]["path"]).exists())


MAIN = "20261013_100000_main01"
QUIET = "20261013_110000_quiet1"


def _build_state_db(hermes_home: Path) -> None:
    """The `sessions` / `session_model_usage` columns the cost receipt reads (Hermes' state.db shape)."""
    hermes_home.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(hermes_home / "state.db")
    connection.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, model TEXT, model_config TEXT,
            parent_session_id TEXT, started_at REAL NOT NULL, ended_at REAL, end_reason TEXT,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT, cost_source TEXT,
            api_call_count INTEGER DEFAULT 0
        );
        CREATE TABLE session_model_usage (
            session_id TEXT NOT NULL, model TEXT NOT NULL, billing_provider TEXT NOT NULL DEFAULT '',
            api_call_count INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd REAL NOT NULL DEFAULT 0, actual_cost_usd REAL NOT NULL DEFAULT 0,
            cost_status TEXT, cost_source TEXT, first_seen REAL, last_seen REAL
        );
        """
    )
    for session_id in (MAIN, QUIET):
        connection.execute(
            "INSERT INTO sessions (id, source, model, model_config, started_at) VALUES (?, 'tui', 'gpt-6-sol', '{}', 1000.0)",
            (session_id,),
        )
    connection.execute(
        "INSERT INTO session_model_usage (session_id, model, input_tokens, output_tokens, estimated_cost_usd, "
        "actual_cost_usd, cost_status, cost_source, api_call_count) VALUES (?, 'gpt-6-sol', 1200, 300, 0.05, 0, "
        "'estimated', 'official_docs_snapshot', 2)",
        (MAIN,),
    )
    connection.commit()
    connection.close()


class RunEfficiencyHostUsageTests(unittest.TestCase):
    """`host_usage` is the named session's cost receipt: observed only from recorded tokens."""

    def _report(self, session: str) -> dict[str, object]:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_state_db(root / ".hermes")
            input_path = root / "input.json"
            atomic_write_json(input_path, _input_payload())
            status, stdout, stderr = run_cli(
                [
                    "--omh-home",
                    str(root / ".omh"),
                    "--hermes-home",
                    str(root / ".hermes"),
                    "runtime",
                    "efficiency-report",
                    "--input",
                    str(input_path),
                    "--hermes-session",
                    session,
                ]
            )
        self.assertEqual(status, 0, stderr)
        return json.loads(stdout)

    def test_a_session_with_recorded_tokens_reports_host_usage_as_observed_and_keeps_billing_apart(self) -> None:
        report = self._report(MAIN)

        host_usage = report["host_usage"]
        self.assertEqual(host_usage["status"], "observed")
        self.assertEqual(host_usage["source"], "omh_cost_receipt/v1")
        self.assertEqual(host_usage["session_id"], MAIN)
        self.assertEqual(host_usage["priced_tokens"], 1500)
        self.assertEqual(host_usage["unpriced_tokens"], 0)
        self.assertEqual(host_usage["observed_cost_usd"], 0.05)
        # The basis travels with the amount: this figure is Hermes' estimate,
        # not a billed charge.
        self.assertEqual(
            host_usage["sources"]["session"],
            {
                "count": 1,
                "cost_usd": 0.05,
                "priced_tokens": 1500,
                "unpriced_tokens": 0,
                "cost_bases": ["Hermes estimated"],
                "unpriced_models": [],
            },
        )
        self.assertFalse(host_usage["truncated"])
        self.assertIn(
            "fanout units an executor reported are left out. Tokens with no recorded price are not in the cost, and "
            "none of it is a provider invoice.",
            host_usage["claim_boundary"],
        )
        self.assertEqual(
            report["not_observed"],
            {
                "provider": {"status": "not_observed"},
                "billing": {"status": "not_observed"},
                "cron": {"status": "not_observed"},
            },
        )

    def test_a_session_that_recorded_no_tokens_stays_not_observed_never_zero(self) -> None:
        report = self._report(QUIET)

        self.assertEqual(
            report["host_usage"],
            {
                "status": "not_observed",
                "session_id": QUIET,
                "reason": "Hermes recorded no token usage for this session",
                "missing": [],
            },
        )
        self.assertEqual(report["not_observed"]["host"], {"status": "not_observed"})

    def test_a_hermes_home_without_state_db_says_so(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.json"
            atomic_write_json(input_path, _input_payload())
            status, stdout, stderr = run_cli(
                [
                    "--omh-home",
                    str(root / ".omh"),
                    "--hermes-home",
                    str(root / ".hermes"),
                    "runtime",
                    "efficiency-report",
                    "--input",
                    str(input_path),
                    "--hermes-session",
                    MAIN,
                ]
            )
        self.assertEqual(status, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(
            report["host_usage"],
            {"status": "not_observed", "session_id": MAIN, "reason": "Hermes state.db not found"},
        )
        self.assertEqual(report["not_observed"]["host"], {"status": "not_observed"})

    def test_a_session_hermes_has_no_row_for_is_not_observed(self) -> None:
        report = self._report("20261013_120000_absent")

        self.assertEqual(
            report["host_usage"],
            {
                "status": "not_observed",
                "session_id": "20261013_120000_absent",
                "reason": "no Hermes session row for this session id",
            },
        )
        self.assertEqual(report["not_observed"]["host"], {"status": "not_observed"})

    def test_an_observed_receipt_with_zero_tokens_and_a_recorded_price_is_still_not_observed(self) -> None:
        receipt = {
            "schema_version": "omh_cost_receipt/v1",
            "status": "observed",
            "session_id": MAIN,
            "sources": {"session": {"count": 1, "cost_usd": 0.0, "priced_tokens": 0, "unpriced_tokens": 0}},
            "priced_tokens": 0,
            "unpriced_tokens": 0,
            "observed_cost_usd": 0.0,
            "missing": [],
        }

        report = build_run_efficiency_report(parse_run_efficiency_input(_input_payload()), cost_receipt=receipt)

        self.assertEqual(report["host_usage"]["status"], "not_observed")
        self.assertNotIn("observed_cost_usd", report["host_usage"])
        self.assertEqual(report["not_observed"]["host"], {"status": "not_observed"})

    def test_fanout_units_an_executor_reported_are_not_host_usage(self) -> None:
        # The receipt also sums fanout units from their dispatch summaries;
        # only the buckets Hermes accounts for in state.db are host usage.
        receipt = {
            "schema_version": "omh_cost_receipt/v1",
            "status": "observed",
            "session_id": MAIN,
            "sources": {
                "session": {"count": 1, "cost_usd": 0.05, "priced_tokens": 1500, "unpriced_tokens": 0},
                "delegated_children": {"count": 1, "cost_usd": None, "priced_tokens": 0, "unpriced_tokens": 200},
                "fanout_units": {"count": 2, "cost_usd": 3.0, "priced_tokens": 90000, "unpriced_tokens": 7},
                "kanban_workers": {"count": 0, "cost_usd": None, "priced_tokens": 0, "unpriced_tokens": 0},
            },
            "priced_tokens": 91500,
            "unpriced_tokens": 207,
            "observed_cost_usd": 3.05,
            "missing": [
                {"source": "fanout_units", "id": "fanout-1/u1", "reason": "ran, reported no usage"},
                {"source": "delegated_children", "id": "child", "reason": "no usage recorded"},
            ],
        }

        host_usage = build_run_efficiency_report(
            parse_run_efficiency_input(_input_payload()), cost_receipt=receipt
        )["host_usage"]

        self.assertEqual(host_usage["status"], "observed")
        self.assertEqual(host_usage["priced_tokens"], 1500)
        self.assertEqual(host_usage["unpriced_tokens"], 200)
        self.assertEqual(host_usage["observed_cost_usd"], 0.05)
        self.assertEqual(set(host_usage["sources"]), {"session", "delegated_children", "kanban_workers"})
        self.assertEqual(
            host_usage["missing"], [{"source": "delegated_children", "id": "child", "reason": "no usage recorded"}]
        )

    def test_truncation_counts_only_when_an_included_source_hit_its_bound(self) -> None:
        base = {
            "schema_version": "omh_cost_receipt/v1",
            "status": "observed",
            "session_id": MAIN,
            "sources": {"session": {"count": 1, "cost_usd": None, "priced_tokens": 0, "unpriced_tokens": 900}},
            "missing": [],
            "truncated": True,
        }
        for truncated_sources, expected in ((["fanout_units"], False), (["kanban_workers"], True), ([], False)):
            with self.subTest(truncated_sources=truncated_sources):
                receipt = {**base, "truncated_sources": truncated_sources}
                report = build_run_efficiency_report(parse_run_efficiency_input(_input_payload()), cost_receipt=receipt)
                self.assertEqual(report["host_usage"]["truncated"], expected)

    def test_a_receipt_whose_only_tokens_are_fanout_units_is_not_host_usage(self) -> None:
        receipt = {
            "schema_version": "omh_cost_receipt/v1",
            "status": "observed",
            "session_id": MAIN,
            "sources": {"fanout_units": {"count": 1, "cost_usd": 1.0, "priced_tokens": 5000, "unpriced_tokens": 0}},
            "priced_tokens": 5000,
            "unpriced_tokens": 0,
            "observed_cost_usd": 1.0,
            "missing": [],
        }

        report = build_run_efficiency_report(parse_run_efficiency_input(_input_payload()), cost_receipt=receipt)

        self.assertEqual(report["host_usage"]["status"], "not_observed")
        self.assertEqual(report["not_observed"]["host"], {"status": "not_observed"})


if __name__ == "__main__":
    unittest.main()
