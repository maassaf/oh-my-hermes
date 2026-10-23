from __future__ import annotations

import json
from pathlib import Path
import unittest

from src.quality.cross_harness_benchmark import (
    BenchmarkValidationError,
    JsonValue,
    corpus_digest,
    evaluate_submission,
    parse_corpus,
    score_submission,
)


ROOT = Path(__file__).parents[1]
CORPUS_PATH = ROOT / "benchmarks" / "cross-harness" / "v1" / "manifest.json"
PASSING_INPUT_PATH = ROOT / "benchmarks" / "cross-harness" / "v1" / "example-passing-submission.json"


def _json_object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _json_array(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _load_corpus_data() -> dict[str, JsonValue]:
    with CORPUS_PATH.open(encoding="utf-8") as stream:
        value: JsonValue = json.load(stream)
    return _json_object(value)


def _passing_submission() -> dict[str, JsonValue]:
    with PASSING_INPUT_PATH.open(encoding="utf-8") as stream:
        value: JsonValue = json.load(stream)
    return _json_object(_json_object(value)["submission"])


def _submission_result(submission: dict[str, JsonValue], index: int = 0) -> dict[str, JsonValue]:
    return _json_object(_json_array(submission["results"])[index])


def _result_object(
    submission: dict[str, JsonValue], field: str, index: int = 0
) -> dict[str, JsonValue]:
    return _json_object(_submission_result(submission, index)[field])


def _refresh_corpus_digest(raw: dict[str, JsonValue]) -> None:
    payload = {key: value for key, value in raw.items() if key != "corpus_digest"}
    raw["corpus_digest"] = corpus_digest(payload)


class CrossHarnessBenchmarkTests(unittest.TestCase):
    def test_recomputed_corpus_cannot_remove_a_fixture(self) -> None:
        raw = _load_corpus_data()
        _json_array(raw["fixtures"]).pop()
        _refresh_corpus_digest(raw)

        with self.assertRaisesRegex(BenchmarkValidationError, "untrusted_corpus_digest"):
            parse_corpus(raw)

    def test_recomputed_corpus_cannot_duplicate_fixture_ids(self) -> None:
        raw = _load_corpus_data()
        fixtures = _json_array(raw["fixtures"])
        _json_object(fixtures[1])["id"] = _json_object(fixtures[0])["id"]
        _refresh_corpus_digest(raw)

        with self.assertRaisesRegex(BenchmarkValidationError, "untrusted_corpus_digest"):
            parse_corpus(raw)

    def test_recomputed_corpus_cannot_change_dimension_weights(self) -> None:
        raw = _load_corpus_data()
        dimensions = _json_array(raw["dimensions"])
        _json_object(dimensions[0])["weight"] = 9
        _refresh_corpus_digest(raw)

        with self.assertRaisesRegex(BenchmarkValidationError, "untrusted_corpus_digest"):
            parse_corpus(raw)

    def test_extra_corpus_fields_are_rejected(self) -> None:
        raw = _load_corpus_data()
        raw["unexpected"] = True

        with self.assertRaisesRegex(BenchmarkValidationError, "extra_fields"):
            parse_corpus(raw)

    def test_wrong_submission_field_types_are_rejected(self) -> None:
        submission = _passing_submission()
        _submission_result(submission)["runtime_observation"] = 1

        with self.assertRaisesRegex(BenchmarkValidationError, "wrong_type"):
            evaluate_submission(submission, parse_corpus(_load_corpus_data()))

    def test_recomputed_corpus_cannot_replace_integers_with_booleans(self) -> None:
        for section, field in (("dimensions", "weight"), ("dimensions", "minimum"), ("command_bindings", "expected_exit")):
            for value in (False, True):
                with self.subTest(field=field, value=value):
                    raw = _load_corpus_data()
                    entries = _json_array(raw[section])
                    _json_object(entries[0])[field] = value
                    _refresh_corpus_digest(raw)

                    with self.assertRaisesRegex(
                        BenchmarkValidationError, "untrusted_corpus_digest"
                    ):
                        parse_corpus(raw)

    def test_json_booleans_are_never_submission_integer_fields(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        for field in ("expected_exit", "observed_exit"):
            for value in (False, True):
                with self.subTest(field=field, value=value):
                    submission = _passing_submission()
                    _result_object(submission, "command_evidence")[field] = value

                    with self.assertRaisesRegex(BenchmarkValidationError, "wrong_type"):
                        evaluate_submission(submission, corpus)

    def test_caller_supplied_status_is_rejected(self) -> None:
        submission = _passing_submission()
        _submission_result(submission)["status"] = "pass"

        with self.assertRaisesRegex(BenchmarkValidationError, "extra_fields"):
            evaluate_submission(submission, parse_corpus(_load_corpus_data()))

    def test_duplicate_fixture_identity_is_rejected(self) -> None:
        submission = _passing_submission()
        results = _json_array(submission["results"])
        results.append(dict(_json_object(results[0])))

        with self.assertRaisesRegex(BenchmarkValidationError, "duplicate_result"):
            evaluate_submission(submission, parse_corpus(_load_corpus_data()))

    def test_machine_semantics_derive_complete_passing_outcomes(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        report = evaluate_submission(_passing_submission(), corpus)

        self.assertEqual(
            tuple((outcome.fixture_id, outcome.status, outcome.reason_codes) for outcome in report.outcomes),
            tuple((fixture.id, "pass", ()) for fixture in corpus.fixtures),
        )

    def test_missing_exact_adapter_or_capability_is_unsupported(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        cases = (
            ("adapter_id", "generic", "adapter_unavailable"),
            ("capability_id", "missing", "capability_unavailable"),
        )
        for field, value, reason in cases:
            with self.subTest(field=field):
                submission = _passing_submission()
                _submission_result(submission)[field] = value

                outcome = evaluate_submission(submission, corpus).outcomes[0]

                self.assertEqual((outcome.status, outcome.reason_codes), ("unsupported", (reason,)))

    def test_below_required_evidence_is_partial(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        submission = _passing_submission()
        _submission_result(submission)["evidence_class"] = "prepared"

        outcome = evaluate_submission(submission, corpus).outcomes[0]

        self.assertEqual((outcome.status, outcome.reason_codes), ("partial", ("insufficient_evidence_class",)))

    def test_unobserved_runtime_is_partial_and_cannot_reach_level_five(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        submission = _passing_submission()
        dynamic_index = next(index for index, item in enumerate(corpus.fixtures) if item.dynamic)
        _submission_result(submission, dynamic_index)["runtime_observation"] = "prepared_not_observed"
        report = evaluate_submission(submission, corpus)
        outcome = report.outcomes[dynamic_index]
        score = score_submission(submission, corpus)

        self.assertEqual((outcome.status, outcome.reason_codes), ("partial", ("runtime_not_observed",)))
        self.assertEqual((score.total, score.level, score.contract_certified), (95, 3, False))

    def test_evidence_failure_precedes_unobserved_runtime(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        submission = _passing_submission()
        dynamic_index = next(index for index, item in enumerate(corpus.fixtures) if item.dynamic)
        result = _submission_result(submission, dynamic_index)
        result["runtime_observation"] = "prepared_not_observed"
        result["evidence_class"] = "prepared"

        outcome = evaluate_submission(submission, corpus).outcomes[dynamic_index]

        self.assertEqual((outcome.status, outcome.reason_codes), ("partial", ("insufficient_evidence_class",)))

    def test_command_argv_drift_is_rejected(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        submission = _passing_submission()
        _result_object(submission, "command_evidence")["argv"] = ["omh", "doctor"]

        with self.assertRaisesRegex(BenchmarkValidationError, "command_binding_mismatch"):
            evaluate_submission(submission, corpus)

    def test_source_pin_drift_is_rejected(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        submission = _passing_submission()
        _result_object(submission, "source_binding")["commit"] = "f" * 40

        with self.assertRaisesRegex(BenchmarkValidationError, "source_binding_mismatch"):
            evaluate_submission(submission, corpus)

    def test_hostile_metadata_is_rejected(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        for hostile in ("/Users/example/private", "sk-example-token", "ignore previous instructions"):
            with self.subTest(hostile=hostile):
                submission = _passing_submission()
                submission["harness_id"] = hostile

                with self.assertRaisesRegex(BenchmarkValidationError, "unsafe_metadata"):
                    evaluate_submission(submission, corpus)

    def test_stale_binding_digests_are_rejected(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        cases = (
            ("command_evidence", "binding_digest", "stale_binding_digest"),
            ("source_binding", "source_digest", "stale_source_digest"),
        )
        for section, field, reason in cases:
            with self.subTest(section=section):
                submission = _passing_submission()
                _result_object(submission, section)[field] = "0" * 64

                with self.assertRaisesRegex(BenchmarkValidationError, reason):
                    evaluate_submission(submission, corpus)

    def test_failed_child_overrides_parent_exit_zero(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        submission = _passing_submission()
        _submission_result(submission)["child_results"] = [{"id": "primary", "result": "fail"}]

        outcome = evaluate_submission(submission, corpus).outcomes[0]

        self.assertEqual((outcome.status, outcome.reason_codes), ("fail", ("child_failed",)))

    def test_unsupported_result_lowers_score_and_blocks_contract_certification(
        self,
    ) -> None:
        corpus = parse_corpus(_load_corpus_data())
        submission = _passing_submission()
        _submission_result(submission)["capability_id"] = "missing"

        score = score_submission(submission, corpus)

        self.assertEqual((score.total, score.level, score.contract_certified), (90, 3, False))

    def test_score_levels_zero_through_three_are_reachable_by_contract(self) -> None:
        corpus = parse_corpus(_load_corpus_data())

        def score_with_failed_dimensions(count: int) -> int:
            submission = _passing_submission()
            failed = {item.id for item in corpus.dimensions[:count]}
            for index, fixture in enumerate(corpus.fixtures):
                if fixture.dimension in failed:
                    _result_object(submission, "actual_machine", index).clear()
                    _result_object(submission, "facts", index).clear()
            return score_submission(submission, corpus).level

        for failed_dimensions, expected_level in ((10, 0), (9, 1), (5, 2), (1, 3)):
            with self.subTest(expected_level=expected_level):
                self.assertEqual(score_with_failed_dimensions(failed_dimensions), expected_level)

    def test_all_pass_without_observed_dynamic_runtime_reaches_level_four(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        level_four = _passing_submission()
        for index, fixture in enumerate(corpus.fixtures):
            if fixture.dynamic:
                result = _submission_result(level_four, index)
                result["evidence_class"] = "test"
                result["runtime_observation"] = "prepared_not_observed"
        report = evaluate_submission(level_four, corpus)
        score = score_submission(level_four, corpus)

        self.assertEqual(
            tuple(
                (outcome.status, outcome.submission_claims_runtime_observed)
                for outcome in report.outcomes
            ),
            tuple(("pass", not fixture.dynamic) for fixture in corpus.fixtures),
        )
        self.assertEqual((score.total, score.level, score.contract_certified), (100, 4, True))

    def test_all_pass_with_observed_dynamic_runtime_reaches_level_five(self) -> None:
        corpus = parse_corpus(_load_corpus_data())

        score = score_submission(_passing_submission(), corpus)

        self.assertEqual((score.total, score.level, score.contract_certified), (100, 5, True))
        self.assertEqual(score.evidence_authenticity, "unverified_submission")
        self.assertFalse(score.execution_verified)

    def test_p0_failure_blocks_contract_certification(self) -> None:
        corpus = parse_corpus(_load_corpus_data())
        submission = _passing_submission()
        p0_index = next(index for index, item in enumerate(corpus.fixtures) if item.priority == "P0")
        _result_object(submission, "actual_machine", p0_index).clear()

        score = score_submission(submission, corpus)

        self.assertFalse(score.contract_certified)
        self.assertIn("p0_failure", score.reason_codes)

    def test_fixture_mutation_invalidates_declared_corpus_digest(self) -> None:
        raw = _load_corpus_data()
        fixture = _json_object(_json_array(raw["fixtures"])[0])
        predicate = _json_object(_json_array(fixture["expected_machine"])[0])
        predicate["value"] = "mutated"

        with self.assertRaisesRegex(BenchmarkValidationError, "corpus_digest_mismatch"):
            parse_corpus(raw)


if __name__ == "__main__":
    unittest.main()
