from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from src.quality.cross_harness_benchmark import JsonValue, corpus_digest


_ROOT = Path(__file__).resolve().parents[1]
_BENCHMARK_DIR = _ROOT / "benchmarks" / "cross-harness" / "v1"


def _load_json_object(path: Path) -> dict[str, JsonValue]:
    with path.open(encoding="utf-8") as source:
        value: JsonValue = json.load(source)
    if not isinstance(value, dict):
        raise AssertionError(f"{path.name} must contain a JSON object")
    return value


class CrossHarnessBenchmarkManifestTests(unittest.TestCase):
    def test_manifest_command_is_an_executable_cli_binding(self) -> None:
        manifest = _load_json_object(_BENCHMARK_DIR / "manifest.json")
        command_bindings = manifest["command_bindings"]
        assert isinstance(command_bindings, list)
        command = command_bindings[0]
        assert isinstance(command, dict)
        argv = command["argv"]
        assert isinstance(argv, list)
        self.assertEqual(
            argv,
            ["python3", "-m", "omh.cli", "harness", "validate"],
        )
        command_argv: list[str] = []
        for argument in argv:
            assert isinstance(argument, str)
            command_argv.append(argument)

        completed = subprocess.run(
            command_argv,
            cwd=_ROOT,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "OMH_OUTPUT": "json"},
        )

        self.assertEqual(
            completed.returncode, command["expected_exit"], completed.stderr
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(command["expected_semantic_result"], "validated")
        self.assertEqual(payload["schema_version"], "catalog_validation/v1")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])

    def test_real_cli_rejects_false_for_integer_zero_predicate(self) -> None:
        # Given: a complete envelope supplies false where the corpus expects integer zero.
        benchmark_input = _load_json_object(
            _BENCHMARK_DIR / "example-passing-submission.json"
        )
        submission = benchmark_input["submission"]
        assert isinstance(submission, dict)
        results = submission["results"]
        assert isinstance(results, list)
        target = next(
            result
            for result in results
            if isinstance(result, dict)
            and result.get("fixture_id") == "ultrawork-child-propagation"
        )
        actual_machine = target["actual_machine"]
        assert isinstance(actual_machine, dict)
        actual_machine["parent_exit"] = False

        # When: the source-bound CLI validates the envelope from the repository root.
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "omh.cli",
                "benchmark",
                "validate",
                "--stdin",
            ],
            cwd=_ROOT,
            input=json.dumps(benchmark_input),
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "OMH_OUTPUT": "json"},
        )

        # Then: the real CLI derives a predicate mismatch instead of a pass.
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        outcome = next(
            item
            for item in payload["outcomes"]
            if item["fixture_id"] == "ultrawork-child-propagation"
        )
        self.assertEqual(
            (outcome["status"], outcome["reason_codes"]),
            ("fail", ["predicate_mismatch"]),
        )

    def test_real_cli_rejects_recomputed_corpus_for_null_predicate(self) -> None:
        # Given: a caller changes a frozen predicate and recomputes its digest.
        benchmark_input = _load_json_object(
            _BENCHMARK_DIR / "example-passing-submission.json"
        )
        corpus = benchmark_input["corpus"]
        submission = benchmark_input["submission"]
        assert isinstance(corpus, dict)
        assert isinstance(submission, dict)
        fixtures = corpus["fixtures"]
        results = submission["results"]
        assert isinstance(fixtures, list)
        assert isinstance(results, list)
        fixture = fixtures[0]
        result = results[0]
        assert isinstance(fixture, dict)
        assert isinstance(result, dict)
        predicates = fixture["expected_machine"]
        actual_machine = result["actual_machine"]
        assert isinstance(predicates, list)
        assert isinstance(actual_machine, dict)
        predicate = predicates[0]
        assert isinstance(predicate, dict)
        predicate["value"] = None
        del actual_machine["selected_owner"]
        corpus_payload = {
            key: value for key, value in corpus.items() if key != "corpus_digest"
        }
        corpus["corpus_digest"] = corpus_digest(corpus_payload)
        submission["corpus_digest"] = corpus["corpus_digest"]

        # When: the source-bound CLI validates the envelope from the repository root.
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "omh.cli",
                "benchmark",
                "validate",
                "--stdin",
            ],
            cwd=_ROOT,
            input=json.dumps(benchmark_input),
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "OMH_OUTPUT": "json"},
        )

        # Then: the real CLI rejects the caller-owned corpus before evaluation.
        self.assertEqual(completed.returncode, 2, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["reason_codes"], ["untrusted_corpus_digest"])

    def test_manifest_source_path_is_available_locally_and_at_the_pinned_commit(
        self,
    ) -> None:
        manifest = _load_json_object(_BENCHMARK_DIR / "manifest.json")
        sources = manifest["sources"]
        assert isinstance(sources, list)
        source = sources[0]
        assert isinstance(source, dict)
        path_metadata = source["path_metadata"]
        commit = source["commit"]
        assert isinstance(path_metadata, str)
        assert isinstance(commit, str)
        self.assertTrue((_ROOT / path_metadata).is_file())

        git = shutil.which("git")
        if git is None:
            self.skipTest("git is unavailable for pinned-source verification")
        commit_available = subprocess.run(
            [git, "cat-file", "-e", f"{commit}^{{commit}}"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if commit_available.returncode != 0:
            self.skipTest("the pinned source commit is unavailable locally")
        pinned_path = subprocess.run(
            [git, "cat-file", "-e", f"{commit}:{path_metadata}"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(pinned_path.returncode, 0, pinned_path.stderr)

    def test_every_example_embeds_the_exact_manifest_corpus(self) -> None:
        manifest = _load_json_object(_BENCHMARK_DIR / "manifest.json")

        for example_path in sorted(_BENCHMARK_DIR.glob("example-*.json")):
            with self.subTest(example=example_path.name):
                example = _load_json_object(example_path)
                self.assertEqual(example["corpus"], manifest)


if __name__ == "__main__":
    _ = unittest.main()
