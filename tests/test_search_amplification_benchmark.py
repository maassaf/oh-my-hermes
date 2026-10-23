"""Pure offline benchmark tests; no product bootstrap or provider imports."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/search_amplification"
SCRIPT = ROOT / "tools/benchmarks/search_amplification.py"


def benchmark():
    if not SCRIPT.exists():
        raise AssertionError("benchmark CLI implementation is missing")
    spec = importlib.util.spec_from_file_location("search_benchmark", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CorpusTests(unittest.TestCase):
    def test_search_benchmark_split_is_disjoint(self):
        b = benchmark()
        corpus = json.loads((FIXTURES / "corpus.json").read_text(encoding="utf-8"))
        summary = b.validate_corpus(corpus)
        self.assertEqual(summary["total"], 60)
        self.assertEqual(summary["splits"], {"dev": 30, "holdout": 30})
        self.assertEqual(len(summary["categories"]), 6)
        self.assertTrue(all(row == {"dev": 5, "holdout": 5}
                            for row in summary["categories"].values()))
        for field in ("case_id", "topic_id", "prompt"):
            broken = copy.deepcopy(corpus)
            broken["cases"][5][field] = broken["cases"][0][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                b.validate_corpus(broken)
        for field, value in (("synthetic", False), ("split", "test"),
                             ("category", "unknown"), ("unexpected", True)):
            broken = copy.deepcopy(corpus)
            broken["cases"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                b.validate_corpus(broken)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.b = benchmark()
        self.corpus = json.loads((FIXTURES / "corpus.json").read_text(encoding="utf-8"))
        self.receipts = json.loads((FIXTURES / "algorithm_receipts.json").read_text(encoding="utf-8"))

    def report(self, receipts=None):
        return self.b.reconcile(self.corpus, self.receipts if receipts is None else receipts,
                                lane="synthetic_algorithm", split="dev")

    def test_unavailable_tools_are_not_zero_results(self):
        report = self.report()
        for arm in ("baseline", "amplified"):
            empty = report["metrics"][arm]["empty_results"]["rate"]
            self.assertIsNone(empty["percent"])
            self.assertEqual(empty["basis"], "no_observations")
            self.assertEqual(empty["denominator"], 0)
            self.assertEqual(report["metrics"][arm]["empty_results"]["exclusion_counts"]["status:unavailable"], 1)
            for metric in report["metrics"][arm].values():
                self.assertEqual(metric["arm"], arm)
                self.assertEqual(metric["corpus"], self.corpus["corpus_id"])
                self.assertIn("selection", metric)
        self.assertEqual(report["paired_cases"], 1)
        self.assertFalse(report["live_improvement_claimed"])

    def test_missing_and_unpriced_measurements_stay_null(self):
        envelope = copy.deepcopy(self.receipts)
        for row in envelope["receipts"]:
            row["status"] = "completed"
            row["retrieved_evidence_ids"] = []
        report = self.report(envelope)
        for arm in ("baseline", "amplified"):
            metrics = report["metrics"][arm]
            self.assertEqual(metrics["empty_results"]["rate"]["percent"], 100)
            for metric in ("recall_at_5", "correctness", "grounding", "manual_grounding"):
                self.assertIsNone(metrics[metric]["rate"]["percent"])
            for metric in ("latency_ms", "cost_usd"):
                self.assertIsNone(metrics[metric]["value"])
                self.assertEqual(metrics[metric]["basis"], "no_observations")

    def test_receipts_are_paired_closed_and_bound_to_inputs(self):
        for field, value in (("input_digest", "0" * 64),
                             ("corpus_digest", "0" * 64),
                             ("case_id", "unknown"), ("arm", "other"),
                             ("latency_ms", True), ("cost_usd", -1),
                             ("status", "zero"), ("unexpected", "value"),
                             ("started_at", "not-a-time")):
            envelope = copy.deepcopy(self.receipts)
            envelope["receipts"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.report(envelope)
        envelope = copy.deepcopy(self.receipts)
        envelope["receipts"].append(copy.deepcopy(envelope["receipts"][0]))
        with self.assertRaises(ValueError):
            self.report(envelope)
        envelope = copy.deepcopy(self.receipts)
        envelope["receipts"][0]["conditions"]["model_id"] = "different-model"
        with self.assertRaises(ValueError):
            self.report(envelope)
        envelope = copy.deepcopy(self.receipts)
        envelope["receipts"].pop()
        report = self.report(envelope)
        self.assertEqual(report["paired_cases"], 0)
        self.assertEqual(report["unpaired_receipt_ids"], [envelope["receipts"][0]["receipt_id"]])
        with self.assertRaises(ValueError):
            self.b.reconcile(self.corpus, self.receipts, lane="real", split="dev")


    def completed_fixture(self):
        envelope = copy.deepcopy(self.receipts)
        for row in envelope["receipts"]:
            row.update(status="completed", answer_ref="SYNTHETIC:answer",
                       retrieved_evidence_ids=["gold-a", "noise-1", "noise-2", "noise-3", "noise-4", "gold-b"],
                       gold_evidence_ids=["gold-a", "gold-b"], correct=True,
                       grounding={"supported_claims": 2, "total_claims": 3,
                                  "citation_check_ref": "SYNTHETIC:citation-check"},
                       manual_grounding=False,
                       review={"review_ref": "SYNTHETIC:blind-review", "protocol_ref": "SYNTHETIC:protocol",
                               "blinded": True}, latency_ms=100,
                       cost_usd=0.01, pricing_ref="SYNTHETIC:price")
        return envelope

    def test_top_five_and_paired_metrics_use_independent_denominators(self):
        envelope = self.completed_fixture()
        report = self.report(envelope)
        for arm in ("baseline", "amplified"):
            metrics = report["metrics"][arm]
            self.assertEqual(metrics["recall_at_5"]["rate"]["numerator"], 1)
            self.assertEqual(metrics["recall_at_5"]["rate"]["denominator"], 2)
            self.assertEqual(metrics["recall_at_5"]["rate"]["percent"], 50)
            self.assertEqual(metrics["correctness"]["rate"]["percent"], 100)
            self.assertEqual(metrics["grounding"]["rate"]["numerator"], 2)
            self.assertEqual(metrics["grounding"]["rate"]["denominator"], 3)
            self.assertEqual(metrics["manual_grounding"]["rate"]["percent"], 0)
            self.assertEqual(metrics["latency_ms"]["value"], 100)
            self.assertEqual(metrics["cost_usd"]["value"], 0.01)
            checker = __import__("sys").modules["_search_benchmark_reported_rate"]
            for metric in metrics.values():
                if "rate" in metric:
                    self.assertEqual(checker.reported_rate_shape_errors(metric["rate"]), ())
        envelope["receipts"][1]["cost_usd"] = None
        envelope["receipts"][1]["pricing_ref"] = None
        report = self.report(envelope)
        self.assertEqual(report["metrics"]["baseline"]["cost_usd"]["exclusion_counts"]["other_arm:unpriced"], 1)
        self.assertIsNone(report["metrics"]["baseline"]["cost_usd"]["value"])

    def test_empty_or_synthetic_runs_cannot_pass_live_goals(self):
        report = self.b.reconcile(self.corpus, {"schema_version": "search_benchmark_receipts/v1", "receipts": []})
        self.assertEqual(report["paired_cases"], 0)
        self.assertTrue(all(gate["status"] == "unmeasured" for gate in report["quality_gates"].values()))
        report = self.report(self.completed_fixture())
        self.assertTrue(all(gate["status"] == "unmeasured" for gate in report["quality_gates"].values()))
        self.assertFalse(report["live_improvement_claimed"])
        self.assertEqual(report["rule_version"], "search_quality_decision/v1")

    def test_mixed_success_and_unavailable_pair_retains_both_statuses(self):
        envelope = self.completed_fixture()
        envelope["receipts"][1] = copy.deepcopy(self.receipts["receipts"][1])
        report = self.report(envelope)
        self.assertEqual(report["paired_cases"], 1)
        self.assertIsNone(report["metrics"]["baseline"]["recall_at_5"]["rate"]["percent"])
        self.assertEqual(report["metrics"]["baseline"]["recall_at_5"]["exclusion_counts"]["other_arm:status:unavailable"], 1)
        self.assertIsNone(report["metrics"]["amplified"]["empty_results"]["rate"]["percent"])

    def test_frozen_thresholds_on_synthetic_summary_numbers(self):
        # Numeric algorithm fixtures only, not REAL receipts or baseline answers.
        def group(n):
            return {arm: {metric: {"eligible_pairs": n, "rate": {"numerator": n, "denominator": n}, "value": 1}
                          for metric in ("correctness", "recall_at_5", "manual_grounding", "grounding", "latency_ms", "cost_usd")}
                    for arm in ("baseline", "amplified")}
        metrics, simple, complex_group = group(30), group(5), group(10)
        complex_group["baseline"]["recall_at_5"]["rate"] = {"numerator": 8, "denominator": 10}
        complex_group["amplified"]["recall_at_5"]["rate"] = {"numerator": 9, "denominator": 10}
        simple["amplified"]["latency_ms"]["value"] = 1.2
        metrics["amplified"]["cost_usd"]["value"] = 1.5
        selections = {"simple": simple, "complex": complex_group}
        gates = self.b.quality_gates(metrics, selections, lane="real", split="holdout")
        for key in ("complex_recall", "simple_correctness", "simple_latency", "median_cost", "citation_linkage", "manual_grounding"):
            self.assertEqual(gates[key]["status"], "pass", key)
        for key in ("installed_usability", "safety", "failure_semantics", "installed_e2e"):
            self.assertEqual(gates[key]["status"], "unmeasured", key)
        complex_group["baseline"]["recall_at_5"]["rate"]["numerator"] = 9
        gates = self.b.quality_gates(metrics, selections, lane="real", split="holdout")
        self.assertEqual(gates["complex_recall"]["status"], "fail")
        complex_group["baseline"]["manual_grounding"]["rate"]["numerator"] = 9
        gates = self.b.quality_gates(metrics, selections, lane="real", split="holdout")
        self.assertEqual(gates["complex_recall"]["status"], "pass")
        simple["amplified"]["latency_ms"]["value"] = 1.201
        metrics["baseline"]["cost_usd"]["value"] = 0
        gates = self.b.quality_gates(metrics, selections, lane="real", split="holdout")
        self.assertEqual(gates["simple_latency"]["status"], "fail")
        self.assertEqual(gates["median_cost"]["status"], "unmeasured")
        gates = self.b.quality_gates(metrics, selections, lane="real", split="dev")
        self.assertTrue(all(g["status"] == "unmeasured" for g in gates.values()))

    def test_invalid_judgments_and_statuses_are_rejected(self):
        for field, value in (("retrieved_evidence_ids", ["a", "a"]),
                             ("gold_evidence_ids", ["g", "g"]),
                             ("correct", 1), ("latency_ms", float("nan")),
                             ("cost_usd", float("inf")), ("pricing_ref", None),
                             ("review", {"review_ref": "SYNTHETIC:r", "protocol_ref": "SYNTHETIC:p", "blinded": False}),
                             ("grounding", {"supported_claims": 4, "total_claims": 3, "citation_check_ref": "SYNTHETIC:c"}),
                             ("ended_at", "2026-10-22T00:00:00Z")):
            envelope = self.completed_fixture()
            envelope["receipts"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.report(envelope)
        for status in ("unavailable", "denied", "failed", "cancelled", "unsearched", "budget_exhausted"):
            envelope = copy.deepcopy(self.receipts)
            for row in envelope["receipts"]:
                row["status"] = status
            report = self.report(envelope)
            self.assertIsNone(report["metrics"]["baseline"]["empty_results"]["rate"]["percent"])


class CliTests(unittest.TestCase):
    def test_cli_has_no_network_subprocess_or_write_effects(self):
        import contextlib
        import io
        import sys
        b = benchmark()
        enabled = [True]
        def pure_guard(event, args):
            if not enabled[0]:
                return
            if event.startswith("socket.") or event in ("subprocess.Popen", "os.system", "os.posix_spawn"):
                raise AssertionError("pure benchmark attempted network/process effect")
            if event == "open":
                mode, flags = args[1:3]
                if (type(mode) is str and any(c in mode for c in "wax+")) or (type(flags) is int and flags & 3):
                    raise AssertionError("pure benchmark attempted file write")
        sys.addaudithook(pure_guard)
        try:
            with self.assertRaisesRegex(AssertionError, "file write"):
                with (Path.home() / "benchmark-denial-probe").open("w", encoding="utf-8"):
                    pass
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(b.main(["validate"]), 0)
                self.assertEqual(b.main(["reconcile", "--observations", str(FIXTURES / "empty_observations.json")]), 0)
                self.assertEqual(b.main(["reconcile", "--lane", "synthetic_algorithm", "--split", "dev",
                                         "--observations", str(FIXTURES / "algorithm_receipts.json")]), 0)
        finally:
            enabled[0] = False

    def test_cli_validates_reconciles_and_returns_controlled_errors(self):
        import contextlib
        import io
        b = benchmark()
        for args, expected in ((["validate"], 0),
                               (["reconcile", "--observations", str(FIXTURES / "empty_observations.json")], 0),
                               (["reconcile", "--observations", str(FIXTURES / "algorithm_receipts.json")], 2),
                               (["validate", "--corpus", str(FIXTURES / "missing.json")], 2)):
            output, errors = io.StringIO(), io.StringIO()
            with self.subTest(args=args), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(b.main(args), expected)
            if expected == 0:
                parsed = json.loads(output.getvalue())
                if args[0] == "reconcile":
                    self.assertTrue(all(g["status"] == "unmeasured" for g in parsed["quality_gates"].values()))
            else:
                self.assertIn("error", json.loads(errors.getvalue()))
                self.assertNotIn("Traceback", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
