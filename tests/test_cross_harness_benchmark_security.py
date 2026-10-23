from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _credential_fixtures import AWS_ACCESS_KEY_ID
from src.quality import cross_harness_benchmark as benchmark
from src.quality.cross_harness_benchmark import (
    BenchmarkValidationError,
    evaluate_submission,
    parse_corpus,
    score_submission,
)
from src.quality.cross_harness_benchmark_input import BenchmarkJsonInputError, decode_benchmark_json
from src.quality.cross_harness_benchmark_values import JsonValue, corpus_digest


ROOT = Path(__file__).parents[1]
CLI_COMMAND = (sys.executable, "-m", "omh.cli", "benchmark")


def _json_object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _json_array(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _benchmark_input(filename: str = "example-passing-submission.json") -> dict[str, JsonValue]:
    path = ROOT / "benchmarks" / "cross-harness" / "v1" / filename
    value = decode_benchmark_json(path.read_text(encoding="utf-8"))
    return _json_object(value)


def _run_cli(stdin_text: str, command: str = "score") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*CLI_COMMAND, command, "--stdin"],
        cwd=ROOT,
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "OMH_OUTPUT": "json"},
    )


def _forge_corpus_and_matching_submission(
    benchmark_input: dict[str, JsonValue], value: str
) -> None:
    corpus = _json_object(benchmark_input["corpus"])
    submission = _json_object(benchmark_input["submission"])
    results = {
        _json_object(item)["fixture_id"]: _json_object(item)
        for item in _json_array(submission["results"])
    }
    corpus["corpus_id"] = value
    for fixture_value in _json_array(corpus["fixtures"]):
        fixture = _json_object(fixture_value)
        result = results[fixture["id"]]
        for predicate_value in _json_array(fixture["expected_machine"]):
            predicate = _json_object(predicate_value)
            predicate["value"] = value
            scope = predicate["scope"]
            key = predicate["key"]
            assert isinstance(scope, str)
            assert isinstance(key, str)
            _json_object(result[scope])[key] = value
    payload = {key: item for key, item in corpus.items() if key != "corpus_digest"}
    corpus["corpus_digest"] = corpus_digest(payload)
    submission["corpus_digest"] = corpus["corpus_digest"]


class CrossHarnessBenchmarkSecurityTests(unittest.TestCase):
    def test_forged_evaluation_report_has_no_public_contract_certification_path(
        self,
    ) -> None:
        failing_input = _benchmark_input("example-failing-child-submission.json")
        corpus = parse_corpus(_json_object(failing_input["corpus"]))
        failing_submission = _json_object(failing_input["submission"])
        failed = evaluate_submission(failing_submission, corpus)
        passed = evaluate_submission(
            _json_object(_benchmark_input()["submission"]), corpus
        )
        forged = replace(failed, outcomes=passed.outcomes)

        self.assertNotIn("score_evaluation", benchmark.__dict__)
        self.assertRaises(TypeError, score_submission, forged, corpus)
        score = score_submission(failing_submission, corpus)
        self.assertEqual((score.total, score.level, score.contract_certified), (0, 0, False))
        self.assertIn("p0_failure", score.reason_codes)

    def test_decoder_rejects_duplicate_members_at_every_object_depth(self) -> None:
        for raw in (
            '{"schema_version":"attacker","schema_version":"canonical"}',
            '{"outer":{"secret":"attacker","secret":"canonical"}}',
        ):
            with self.subTest(raw=raw[:16]):
                with self.assertRaisesRegex(BenchmarkJsonInputError, "duplicate_key"):
                    _ = decode_benchmark_json(raw)

    def test_cli_rejects_duplicate_top_level_and_nested_members_without_echo(
        self,
    ) -> None:
        compact = json.dumps(_benchmark_input(), separators=(",", ":"))
        cases = (
            '{"schema_version":"attacker-secret",' + compact[1:],
            compact.replace(
                '"harness_id":',
                '"harness_id":"attacker-secret","harness_id":',
                1,
            ),
        )
        for raw in cases:
            with self.subTest(nested='"harness_id"' in raw):
                completed = _run_cli(raw)

                self._assert_cli_error(completed, "duplicate_key")
                self.assertNotIn("attacker-secret", completed.stdout)
                self.assertNotIn("attacker-secret", completed.stderr)

    def test_evaluation_and_scoring_reject_untrusted_corpus_objects(self) -> None:
        benchmark_input = _benchmark_input()
        corpus = parse_corpus(_json_object(benchmark_input["corpus"]))
        submission = _json_object(benchmark_input["submission"])
        untrusted = replace(corpus, digest="0" * 64)

        self._assert_corpus_rejected(submission, untrusted, "untrusted_corpus_digest")

    def test_evaluation_and_scoring_reject_forged_corpus_fields(self) -> None:
        benchmark_input = _benchmark_input()
        corpus = parse_corpus(_json_object(benchmark_input["corpus"]))
        submission = _json_object(benchmark_input["submission"])
        first_dimension = corpus.dimensions[0]
        first_fixture = corpus.fixtures[0]
        first_predicate = first_fixture.predicates[0]
        first_source = corpus.sources[0]
        first_command = corpus.commands[0]
        forged_corpora = {
            "dimension_weight": replace(
                corpus,
                dimensions=(
                    replace(first_dimension, weight=first_dimension.weight + 101),
                    *corpus.dimensions[1:],
                ),
            ),
            "predicate": replace(
                corpus,
                fixtures=(
                    replace(
                        first_fixture,
                        predicates=(
                            replace(first_predicate, value="forged"),
                            *first_fixture.predicates[1:],
                        ),
                    ),
                    *corpus.fixtures[1:],
                ),
            ),
            "source": replace(
                corpus,
                sources=(
                    replace(first_source, license="forged"),
                    *corpus.sources[1:],
                ),
            ),
            "command": replace(
                corpus,
                commands=(
                    replace(
                        first_command,
                        expected_exit=first_command.expected_exit + 1,
                    ),
                    *corpus.commands[1:],
                ),
            ),
        }

        for field, forged in forged_corpora.items():
            with self.subTest(field=field):
                self._assert_corpus_rejected(
                    submission, forged, "corpus_digest_mismatch"
                )

    def test_recomputed_attacker_corpus_is_rejected_before_contract_certification(
        self,
    ) -> None:
        benchmark_input = _benchmark_input()
        _forge_corpus_and_matching_submission(benchmark_input, "attacker-controlled")

        completed = _run_cli(json.dumps(benchmark_input))

        self._assert_cli_error(completed, "untrusted_corpus_digest")

    def test_untrusted_corpus_pii_shapes_are_rejected_without_echo(self) -> None:
        for secret in (
            "person@example.test",
            "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
            AWS_ACCESS_KEY_ID,
        ):
            with self.subTest(secret_shape=secret[:4]):
                benchmark_input = _benchmark_input()
                _forge_corpus_and_matching_submission(benchmark_input, secret)

                completed = _run_cli(json.dumps(benchmark_input), "report")

                self._assert_cli_error(completed, "untrusted_corpus_digest")
                self.assertNotIn(secret, completed.stdout)
                self.assertNotIn(secret, completed.stderr)

    def test_non_finite_json_numbers_are_rejected_at_decode_boundary(self) -> None:
        for non_finite in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(non_finite=repr(non_finite)):
                benchmark_input = _benchmark_input()
                submission = _json_object(benchmark_input["submission"])
                submission["harness_id"] = non_finite

                completed = _run_cli(json.dumps(benchmark_input))

                self._assert_cli_error(completed, "invalid_json")

    def test_excessive_json_depth_is_a_structured_input_error(self) -> None:
        benchmark_input = _benchmark_input()
        submission = _json_object(benchmark_input["submission"])
        nested: JsonValue = "leaf"
        for _ in range(100):
            nested = [nested]
        submission["harness_id"] = nested

        completed = _run_cli(json.dumps(benchmark_input))

        self._assert_cli_error(completed, "input_too_complex")

    def test_decoder_recursion_error_is_a_structured_input_error(self) -> None:
        with (
            patch(
                "src.quality.cross_harness_benchmark_input.json.loads",
                side_effect=RecursionError,
            ),
            self.assertRaisesRegex(BenchmarkJsonInputError, "input_too_complex"),
        ):
            _ = decode_benchmark_json("[]")

    def test_oversize_stdin_is_a_structured_input_error(self) -> None:
        benchmark_input = _benchmark_input()
        submission = _json_object(benchmark_input["submission"])
        submission["harness_id"] = "x" * 2_000_000

        completed = _run_cli(json.dumps(benchmark_input))

        self._assert_cli_error(completed, "input_too_large")

    def test_oversize_file_is_a_structured_input_error(self) -> None:
        with TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "oversize.json"
            _ = input_path.write_text("x" * 2_000_000, encoding="utf-8")

            completed = subprocess.run(
                [*CLI_COMMAND, "validate", "--input", str(input_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "OMH_OUTPUT": "json"},
            )

        self._assert_cli_error(completed, "input_too_large")

    def _assert_cli_error(
        self, completed: subprocess.CompletedProcess[str], reason_code: str
    ) -> None:
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["reason_codes"], [reason_code])

    def _assert_corpus_rejected(
        self,
        submission: dict[str, JsonValue],
        corpus: benchmark.Corpus,
        reason_code: str,
    ) -> None:
        for operation in (evaluate_submission, score_submission):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(BenchmarkValidationError, reason_code):
                    _ = operation(submission, corpus)


if __name__ == "__main__":
    _ = unittest.main()
