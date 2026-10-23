from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Final

from src.quality.cross_harness_benchmark import (
    EvaluationReport,
    JsonValue,
    ScoreReport,
    evaluate_submission,
    parse_corpus,
    score_submission,
)


_ROOT = Path(__file__).resolve().parents[1]
_BENCHMARK_DIR = _ROOT / "benchmarks" / "cross-harness" / "v1"
_FIXTURE_IDS: Final[tuple[str, ...]] = (
    "model-explicit-selection",
    "model-neutral-fallback",
    "routing-machine-decision",
    "routing-unsupported-script",
    "ralplan-consensus-artifact",
    "ultragoal-stop-contract",
    "ultrawork-observed-runtime",
    "ultrawork-child-propagation",
    "installed-skill-parity",
    "safety-prepared-boundary",
    "safety-no-secret-material",
    "evidence-runtime-observation",
    "evidence-command-binding",
    "reproducibility-source-pin",
    "reporting-coverage-separation",
)
_DYNAMIC_FIXTURE_IDS: Final[frozenset[str]] = frozenset(
    {
        "ultrawork-observed-runtime",
        "ultrawork-child-propagation",
        "evidence-runtime-observation",
        "evidence-command-binding",
    }
)


def _evaluate(
    name: str,
) -> tuple[EvaluationReport, ScoreReport, dict[str, JsonValue]]:
    with (_BENCHMARK_DIR / name).open(encoding="utf-8") as source:
        value: JsonValue = json.load(source)
    if not isinstance(value, dict):
        raise AssertionError(f"{name} must contain a JSON object")
    corpus = value["corpus"]
    submission = value["submission"]
    if not isinstance(corpus, dict) or not isinstance(submission, dict):
        raise AssertionError(f"{name} must contain corpus and submission objects")
    parsed_corpus = parse_corpus(corpus)
    report = evaluate_submission(submission, parsed_corpus)
    return report, score_submission(submission, parsed_corpus), submission


def _outcome_map(
    report: EvaluationReport,
) -> dict[str, tuple[str, tuple[str, ...], bool]]:
    return {
        outcome.fixture_id: (
            outcome.status,
            outcome.reason_codes,
            outcome.submission_claims_runtime_observed,
        )
        for outcome in report.outcomes
    }


def _single_result_outcome_map(
    target: str,
    target_outcome: tuple[str, tuple[str, ...], bool],
) -> dict[str, tuple[str, tuple[str, ...], bool]]:
    return {
        fixture_id: target_outcome
        if fixture_id == target
        else ("unsupported", ("missing_result",), False)
        for fixture_id in _FIXTURE_IDS
    }


class CrossHarnessBenchmarkDocumentationExamplesTests(unittest.TestCase):
    def test_passing_example_maps_every_fixture_to_observed_pass(self) -> None:
        report, score, _ = _evaluate("example-passing-submission.json")

        self.assertEqual(
            _outcome_map(report),
            {fixture_id: ("pass", (), True) for fixture_id in _FIXTURE_IDS},
        )
        self.assertEqual(
            (score.total, score.level, score.contract_certified), (100, 5, True)
        )
        self.assertEqual(
            (score.coverage_supported, score.coverage_total, score.reason_codes),
            (15, 15, ()),
        )

    def test_level_four_example_maps_dynamic_fixtures_to_unobserved_passes(
        self,
    ) -> None:
        report, score, _ = _evaluate("example-level-four-submission.json")

        self.assertEqual(
            _outcome_map(report),
            {
                fixture_id: ("pass", (), fixture_id not in _DYNAMIC_FIXTURE_IDS)
                for fixture_id in _FIXTURE_IDS
            },
        )
        self.assertEqual(
            (score.total, score.level, score.contract_certified), (100, 4, True)
        )
        self.assertEqual(
            (score.coverage_supported, score.coverage_total, score.reason_codes),
            (15, 15, ()),
        )

    def test_child_failure_example_maps_p0_child_to_failure(self) -> None:
        report, score, submission = _evaluate("example-failing-child-submission.json")
        target = "ultrawork-child-propagation"
        results = submission["results"]
        assert isinstance(results, list)
        first_result = results[0]
        assert isinstance(first_result, dict)
        actual_machine = first_result["actual_machine"]
        assert isinstance(actual_machine, dict)

        self.assertEqual(actual_machine, {"parent_exit": 0})
        self.assertEqual(
            _outcome_map(report),
            _single_result_outcome_map(target, ("fail", ("child_failed",), True)),
        )
        self.assertEqual(
            (score.total, score.level, score.contract_certified), (0, 0, False)
        )
        self.assertEqual(
            score.reason_codes,
            ("p0_failure", "fixture_not_passed", "below_dimension_minimum"),
        )

    def test_unsupported_example_maps_target_to_adapter_unavailable(self) -> None:
        report, score, _ = _evaluate("example-unsupported-submission.json")
        target = "model-explicit-selection"

        self.assertEqual(
            _outcome_map(report),
            _single_result_outcome_map(
                target,
                ("unsupported", ("adapter_unavailable",), True),
            ),
        )
        self.assertEqual(
            (score.total, score.level, score.contract_certified), (0, 0, False)
        )
        self.assertEqual(
            score.reason_codes, ("fixture_not_passed", "below_dimension_minimum")
        )

    def test_partial_example_maps_target_to_insufficient_evidence(self) -> None:
        report, score, _ = _evaluate("example-partial-submission.json")
        target = "model-explicit-selection"

        self.assertEqual(
            _outcome_map(report),
            _single_result_outcome_map(
                target,
                ("partial", ("insufficient_evidence_class",), True),
            ),
        )
        self.assertEqual(
            (score.total, score.level, score.contract_certified), (0, 0, False)
        )
        self.assertEqual(
            score.reason_codes, ("fixture_not_passed", "below_dimension_minimum")
        )


if __name__ == "__main__":
    _ = unittest.main()
