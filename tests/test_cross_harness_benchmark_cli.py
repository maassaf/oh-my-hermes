from __future__ import annotations

from contextlib import chdir
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _cli_harness import run_cli
from omh.commands.cross_harness_benchmark import cmd_benchmark_validate
from omh.commands.main import build_parser, main
from omh.quality.cross_harness_benchmark import JsonValue


ROOT = Path(__file__).parents[1]
BENCHMARK_DIR = ROOT / "benchmarks" / "cross-harness" / "v1"


def _benchmark_input(
    filename: str = "example-passing-submission.json",
) -> dict[str, JsonValue]:
    value = json.loads((BENCHMARK_DIR / filename).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


class CrossHarnessBenchmarkCliTests(unittest.TestCase):
    def test_validate_reads_one_explicit_file_without_creating_runtime_state(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.json"
            input_path.write_text(json.dumps(_benchmark_input()), encoding="utf-8")
            with chdir(root):
                status, stdout, stderr = run_cli(
                    ["benchmark", "validate", "--input", str(input_path)]
                )
            self.assertEqual(status, 0, stderr)
            payload = json.loads(stdout)
            self.assertTrue(payload["valid"])
            self.assertFalse((root / ".omh").exists())

    def test_copied_passing_file_is_contract_certified_without_verified_execution(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "copied-passing-input.json"
            input_path.write_text(json.dumps(_benchmark_input()), encoding="utf-8")

            self._assert_unverified_level_five(
                ["--input", str(input_path)], stdin_text=""
            )

    def test_fabricated_runtime_claims_remain_explicitly_unverified(self) -> None:
        benchmark_input = _benchmark_input("example-level-four-submission.json")
        submission = benchmark_input["submission"]
        assert isinstance(submission, dict)
        results = submission["results"]
        assert isinstance(results, list)
        for result in results:
            assert isinstance(result, dict)
            if result["runtime_observation"] == "prepared_not_observed":
                result["evidence_class"] = "runtime"
                result["runtime_observation"] = "observed"

        self._assert_unverified_level_five(
            ["--stdin"], stdin_text=json.dumps(benchmark_input)
        )

    def test_validate_accepts_semantic_failure_but_score_and_report_fail_contract_certification(
        self,
    ) -> None:
        benchmark_input = _benchmark_input()
        submission = benchmark_input["submission"]
        assert isinstance(submission, dict)
        results = submission["results"]
        assert isinstance(results, list)
        result = next(
            result
            for result in results
            if isinstance(result, dict)
            and result.get("fixture_id") == "ultrawork-child-propagation"
        )
        result["child_results"] = [{"id": "primary", "result": "fail"}]
        encoded = json.dumps(benchmark_input)
        validate_status, validate_stdout, validate_stderr = run_cli(
            ["benchmark", "validate", "--stdin"], stdin_text=encoded
        )
        self.assertEqual(validate_status, 0, validate_stderr)
        failed_outcome = next(
            outcome
            for outcome in json.loads(validate_stdout)["outcomes"]
            if outcome["fixture_id"] == "ultrawork-child-propagation"
        )
        self.assertEqual(failed_outcome["status"], "fail")
        for command in ("score", "report"):
            with self.subTest(command=command):
                status, stdout, stderr = run_cli(
                    ["benchmark", command, "--stdin"], stdin_text=encoded
                )
                self.assertEqual(status, 1, stderr)
                payload = json.loads(stdout)
                score = payload if command == "score" else payload["score"]
                self.assertFalse(score["contract_certified"])
                self.assertEqual(
                    score["evidence_authenticity"], "unverified_submission"
                )
                self.assertFalse(score["execution_verified"])
                self.assertNotIn("certified", score)
                self.assertIn(
                    "p0_failure",
                    score["reason_codes"],
                )

    def test_invalid_input_variants_return_deterministic_structured_errors(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing_path = root / "missing.json"
            malformed_path = root / "malformed.json"
            malformed_path.write_text("{", encoding="utf-8")
            cases = (
                (["benchmark", "validate"], "missing_input"),
                (
                    ["benchmark", "validate", "--input", str(missing_path)],
                    "input_file_unavailable",
                ),
                (
                    ["benchmark", "validate", "--input", str(malformed_path)],
                    "invalid_json",
                ),
                (["benchmark", "validate", "--stdin"], "input_must_be_object"),
                (
                    [
                        "benchmark",
                        "validate",
                        "--input",
                        str(malformed_path),
                        "--stdin",
                    ],
                    "conflicting_input",
                ),
            )
            for args, reason in cases:
                with self.subTest(reason=reason):
                    stdin = "[]" if reason == "input_must_be_object" else ""
                    status, stdout, _ = run_cli(args, stdin_text=stdin)
                    self.assertEqual(status, 2)
                    self.assertEqual(json.loads(stdout)["reason_codes"], [reason])

    def test_report_exposes_coverage_unknowns_claim_boundary_and_repeated_calls_leave_no_state(
        self,
    ) -> None:
        benchmark_input = _benchmark_input()
        corpus = benchmark_input["corpus"]
        submission = benchmark_input["submission"]
        assert isinstance(corpus, dict)
        assert isinstance(submission, dict)
        results = submission["results"]
        assert isinstance(results, list)
        first = results[0]
        assert isinstance(first, dict)
        first["capability_id"] = "unavailable"
        encoded = json.dumps(benchmark_input)
        first_status, first_stdout, first_stderr = run_cli(
            ["benchmark", "report", "--stdin"], stdin_text=encoded
        )
        second_status, second_stdout, second_stderr = run_cli(
            ["benchmark", "report", "--stdin"], stdin_text=encoded
        )
        self.assertEqual(first_status, 1, first_stderr)
        self.assertEqual(second_status, 1, second_stderr)
        self.assertEqual(first_stdout, second_stdout)
        payload = json.loads(first_stdout)
        self.assertEqual(payload["schema_version"], "cross_harness_benchmark_report/v1")
        self.assertEqual(payload["claim_boundary"], corpus["claim_boundary"])
        self.assertEqual(payload["coverage"]["unsupported"], 1)
        self.assertEqual(payload["unsupported"], ["model-explicit-selection"])
        self.assertEqual(payload["unknowns"], ["model-explicit-selection"])

    def test_parser_registers_validate_callback_and_explicit_input_options(
        self,
    ) -> None:
        parser = build_parser()
        stdin_args = parser.parse_args(["benchmark", "validate", "--stdin"])
        file_args = parser.parse_args(
            ["benchmark", "validate", "--input", "input.json"]
        )
        self.assertEqual(
            (stdin_args.command, stdin_args.benchmark_command),
            ("benchmark", "validate"),
        )
        self.assertIs(stdin_args.func, cmd_benchmark_validate)
        self.assertIsNone(stdin_args.input_file)
        self.assertTrue(stdin_args.stdin)
        self.assertEqual(file_args.input_file, "input.json")
        self.assertFalse(file_args.stdin)

    def test_interrupted_stdin_leaves_no_state_and_next_file_invocation_succeeds(
        self,
    ) -> None:
        class InterruptingStdin:
            def read(self, _size: int = -1) -> str:
                raise KeyboardInterrupt

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.json"
            input_path.write_text(json.dumps(_benchmark_input()), encoding="utf-8")
            with (
                chdir(root),
                patch(
                    "omh.commands.cross_harness_benchmark.sys.stdin",
                    InterruptingStdin(),
                ),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    main(["benchmark", "validate", "--stdin"])
            self.assertFalse((root / ".omh").exists())
            status, stdout, stderr = run_cli(
                ["benchmark", "validate", "--input", str(input_path)]
            )
            self.assertEqual(status, 0, stderr)
            self.assertTrue(json.loads(stdout)["valid"])

    def _assert_unverified_level_five(
        self, input_args: list[str], *, stdin_text: str
    ) -> None:
        score_status, score_stdout, score_stderr = run_cli(
            ["benchmark", "score", *input_args], stdin_text=stdin_text
        )
        report_status, report_stdout, report_stderr = run_cli(
            ["benchmark", "report", *input_args], stdin_text=stdin_text
        )
        self.assertEqual(score_status, 0, score_stderr)
        self.assertEqual(report_status, 0, report_stderr)
        score_payload = json.loads(score_stdout)
        report_payload = json.loads(report_stdout)
        for score in (score_payload, report_payload["score"]):
            self.assertEqual(
                (
                    score["contract_certified"],
                    score["level"],
                    score["evidence_authenticity"],
                    score["execution_verified"],
                ),
                (True, 5, "unverified_submission", False),
            )
            self.assertNotIn("certified", score)
        outcomes = report_payload["evaluation"]["outcomes"]
        self.assertEqual(
            {
                outcome["submission_claims_runtime_observed"]
                for outcome in outcomes
            },
            {True},
        )
        self.assertTrue(all("runtime_observed" not in outcome for outcome in outcomes))
