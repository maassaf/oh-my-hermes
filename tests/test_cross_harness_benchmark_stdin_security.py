from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[1]


def _run_cli_bytes(stdin_bytes: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "omh.cli",
            "benchmark",
            "score",
            "--stdin",
        ],
        cwd=ROOT,
        input=stdin_bytes,
        capture_output=True,
        check=False,
        env={**os.environ, "OMH_OUTPUT": "json"},
    )


class CrossHarnessBenchmarkStdinSecurityTests(unittest.TestCase):
    def test_cli_helper_runs_without_uv_on_path(self) -> None:
        # Given: PATH excludes uv and every other executable.
        benchmark_input = (
            ROOT
            / "benchmarks"
            / "cross-harness"
            / "v1"
            / "example-passing-submission.json"
        ).read_bytes()

        # When: the focused subprocess helper validates the trusted envelope.
        with patch.dict(os.environ, {"PATH": ""}):
            completed = _run_cli_bytes(benchmark_input)

        # Then: the current interpreter reaches the CLI without uv.
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema_version"], "cross_harness_benchmark_score/v1")
        self.assertTrue(payload["contract_certified"])

    def test_oversize_json_integer_is_a_structured_input_error(self) -> None:
        secret = b"7" * 5_000
        completed = _run_cli_bytes(b'{"value":' + secret + b"}")

        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["reason_codes"], ["invalid_json"])
        self.assertNotIn(secret, completed.stdout)
        self.assertNotIn(secret, completed.stderr)

    def test_invalid_utf8_stdin_is_a_structured_input_error(self) -> None:
        completed = _run_cli_bytes(b'\xff{"secret":"do-not-echo"}')

        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["reason_codes"], ["invalid_utf8"])
        self.assertNotIn(b"do-not-echo", completed.stdout)
        self.assertNotIn(b"do-not-echo", completed.stderr)

    def test_multibyte_stdin_limit_is_enforced_in_bytes(self) -> None:
        completed = _run_cli_bytes(('"' + "\u00e9" * 500_001 + '"').encode())

        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout)["reason_codes"], ["input_too_large"]
        )


if __name__ == "__main__":
    _ = unittest.main()
