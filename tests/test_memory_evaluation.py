from __future__ import annotations

import ast
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.workflows.memory_evaluation import (
    build_evaluation_corpus,
    corpus_digest,
    derive_same_host_ratios,
    run_memory_evaluation,
)


class MemoryEvaluationTests(unittest.TestCase):
    frozen_at = datetime(2031, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def test_profile_is_deterministic_for_seed_and_frozen_time(self) -> None:
        first = run_memory_evaluation("small", repetitions=2, seed=73, frozen_at=self.frozen_at)
        second = run_memory_evaluation("small", repetitions=2, seed=73, frozen_at=self.frozen_at)

        self.assertEqual(first["corpus_digest"], second["corpus_digest"])
        self.assertEqual(first["logical_result"], second["logical_result"])
        self.assertTrue(first["logical_result"]["correctness_gates"]["passed"])

    def test_corpus_digest_detects_a_logical_corpus_change(self) -> None:
        corpus = build_evaluation_corpus("small", seed=73, frozen_at=self.frozen_at)
        changed = copy.deepcopy(corpus)
        changed["artifacts"][0]["summary"] = "changed deterministic fixture"

        self.assertNotEqual(corpus_digest(corpus), corpus_digest(changed))

    def test_host_metadata_has_portable_shape(self) -> None:
        report = run_memory_evaluation("small", repetitions=1, frozen_at=self.frozen_at)

        host = report["host"]
        self.assertEqual(set(host), {"python", "platform", "machine", "cpu_count", "filesystem_type"})
        self.assertIsInstance(host["python"], dict)
        self.assertIsInstance(host["platform"], dict)
        self.assertIsInstance(host["machine"], str)
        self.assertTrue(host["cpu_count"] is None or isinstance(host["cpu_count"], int))
        self.assertTrue(host["filesystem_type"] is None or isinstance(host["filesystem_type"], str))

    def test_raw_repetitions_are_preserved_and_summarized_without_a_gate(self) -> None:
        report = run_memory_evaluation("small", repetitions=3, frozen_at=self.frozen_at)

        samples = report["raw_elapsed_ns"]
        self.assertEqual(samples, [row["elapsed_ns"] for row in report["repetitions"]])
        self.assertEqual(len(samples), 3)
        self.assertEqual(report["elapsed_summary_ns"]["max"], max(samples))
        self.assertEqual(report["elapsed_summary_ns"]["sample_count"], 3)
        self.assertNotIn("elapsed", report["logical_result"]["correctness_gates"])

    def test_same_host_ratios_are_pairwise_and_preserve_missing_baselines(self) -> None:
        self.assertEqual(derive_same_host_ratios([20, 50, 9], [10, 0, 3]), [2.0, None, 3.0])

        report = run_memory_evaluation("small", repetitions=2, frozen_at=self.frozen_at)
        self.assertEqual(
            report["same_host_normalization"]["target_to_baseline_sample_ratios"],
            derive_same_host_ratios(report["raw_elapsed_ns"], report["baseline"]["raw_elapsed_ns"]),
        )

    def test_runner_has_no_network_or_model_imports_and_no_universal_timing_assertion(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "src" / "workflows" / "memory_evaluation.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {"http", "httpx", "requests", "socket", "urllib", "model", "openai", "anthropic"}
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertFalse(imported & forbidden)
        self.assertNotIn("latency_threshold", source)
        self.assertNotIn("universal timing assertion", source.lower())

    def test_cli_writes_an_evaluation_artifact_with_no_vps_claim(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "result.json"
            status, stdout, stderr = run_cli(
                [
                    "--omh-home",
                    str(root / ".omh"),
                    "--hermes-home",
                    str(root / ".hermes"),
                    "memory",
                    "evaluate",
                    "--profile",
                    "small",
                    "--repetitions",
                    "2",
                    "--seed",
                    "73",
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual((status, stderr), (0, ""))
            payload = json.loads(stdout)
            self.assertEqual(payload["schema_version"], "omh_memory_evaluation/v1")
            self.assertEqual(payload, json.loads(output.read_text(encoding="utf-8")))
            self.assertFalse(payload["vps_artifact"]["recorded"])
            self.assertIn("No VPS result", payload["claim_boundary"])


if __name__ == "__main__":
    unittest.main()
