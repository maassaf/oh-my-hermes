from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.runtime.context_budget import smaller_payload


class SmallerPayloadTests(unittest.TestCase):
    """Suppression must never be the reason output grew.

    A compact replacement carries fixed overhead -- schema version, claim
    boundary, next action, full-output hint -- so on a nearly-empty projection
    it is the larger of the two. Measured on a live fanout dispatch:
    `progress-status` with no active binding cost 280 bytes in full and 856
    once "compacted".
    """

    def test_the_compact_form_wins_when_it_is_smaller(self) -> None:
        original = {"rows": ["x" * 500], "detail": "y" * 500}
        compacted = {"count": 1, "unchanged_since_last_emission": True}
        self.assertIs(smaller_payload(original, compacted), compacted)

    def test_the_original_wins_when_the_compact_form_is_larger(self) -> None:
        original = {"rows": []}
        compacted = {
            "unchanged_since_last_emission": True,
            "claim_boundary": "a long contract sentence " * 10,
            "next_action": "wait_for_new_observed_evidence_instead_of_repeating_this_command",
        }
        self.assertIs(smaller_payload(original, compacted), original)

    def test_it_handles_values_json_cannot_serialize_directly(self) -> None:
        original = {"path": Path("/tmp/x"), "rows": ["x" * 200]}
        compacted = {"count": 0}
        self.assertIs(smaller_payload(original, compacted), compacted)


class SuppressionNeverGrowsOnRealSurfacesTests(unittest.TestCase):
    def _base(self, root: Path) -> list[str]:
        return ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

    def test_progress_status_with_no_bindings_never_grows_when_repeated(self) -> None:
        """The exact shape a live fanout dispatch produced: nothing bound."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)

            sizes = []
            for _ in range(4):
                status, stdout, stderr = run_cli(base + ["runtime", "progress-status"])
                self.assertEqual(stderr, "")
                self.assertEqual(status, 0)
                sizes.append(len(stdout.strip()))

            self.assertEqual(
                max(sizes),
                sizes[0],
                f"a repeated call must not be larger than the first: {sizes}",
            )

    def test_a_repeated_run_show_never_grows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            status, stdout, stderr = run_cli(
                base + ["runtime", "record", "--skill", "oh-my-hermes", "--harness", "coding-handling"]
            )
            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            run_id = json.loads(stdout)["run"]["run_id"]

            sizes = []
            for _ in range(3):
                status, stdout, stderr = run_cli(base + ["runtime", "show", run_id])
                self.assertEqual(stderr, "")
                self.assertEqual(status, 0)
                sizes.append(len(stdout.strip()))

            self.assertEqual(max(sizes), sizes[0], f"repeat calls must not grow: {sizes}")

    def test_a_suppressed_observation_never_grows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            status, stdout, stderr = run_cli(
                base + ["runtime", "record", "--skill", "oh-my-hermes", "--harness", "coding-handling"]
            )
            self.assertEqual(status, 0, stderr)
            run_id = json.loads(stdout)["run"]["run_id"]
            status, _stdout, stderr = run_cli(
                base
                + [
                    "runtime", "progress-bind", "--run", run_id,
                    "--executor-profile", "claude_code", "--claude-session-ref", "s1",
                ]
            )
            self.assertEqual(status, 0, stderr)

            observe = base + [
                "runtime", "progress-observe", "--run", run_id,
                "--process-status", "running",
                "--profile-status", "running",
                "--profile-latest-event", "repo_exploration",
                "--profile-summary", "inspecting",
            ]
            sizes = []
            for _ in range(3):
                status, stdout, stderr = run_cli(observe)
                self.assertEqual(stderr, "")
                self.assertEqual(status, 0)
                sizes.append(len(stdout.strip()))

            self.assertEqual(max(sizes), sizes[0], f"suppressed calls must not grow: {sizes}")


if __name__ == "__main__":
    unittest.main()
