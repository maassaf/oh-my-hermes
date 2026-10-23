from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.paths import resolve_paths
from omh.runtime.context_budget import PROGRESS_STATUS_LEDGER_KEY, run_context_budget


class ProgressStatusSuppressionTests(unittest.TestCase):
    """"What is running?" is the most repeatable question, and the biggest answer.

    A ten-workflow CLI simulation measured `progress-status` at roughly 8KB per
    call -- one full binding record per executor -- with no budget of its own,
    while the two other observe surfaces already had one. Repeating that
    unchanged is pure cost.
    """

    def _base(self, root: Path) -> list[str]:
        return ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

    def _bound_run(self, base: list[str], ref: str) -> str:
        status, stdout, stderr = run_cli(
            base + ["runtime", "record", "--skill", "oh-my-hermes", "--harness", "coding-handling"]
        )
        self.assertEqual(stderr, "")
        self.assertEqual(status, 0)
        run_id = json.loads(stdout)["run"]["run_id"]
        status, _stdout, stderr = run_cli(
            base
            + [
                "runtime", "progress-bind", "--run", run_id,
                "--executor-profile", "claude_code", "--claude-session-ref", ref,
            ]
        )
        self.assertEqual(stderr, "")
        self.assertEqual(status, 0)
        return run_id

    def _observe(self, base: list[str], run_id: str, latest: str, summary: str) -> None:
        status, _stdout, stderr = run_cli(
            base
            + [
                "runtime", "progress-observe", "--run", run_id,
                "--process-status", "running",
                "--profile-status", "running",
                "--profile-latest-event", latest,
                "--profile-summary", summary,
            ]
        )
        self.assertEqual(stderr, "")
        self.assertEqual(status, 0)

    def _status(self, base: list[str], *extra: str) -> dict:
        status, stdout, stderr = run_cli(base + ["runtime", "progress-status", *extra])
        self.assertEqual(stderr, "")
        self.assertEqual(status, 0)
        return json.loads(stdout)

    def test_a_repeated_status_call_returns_counts_instead_of_every_binding(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            run_id = self._bound_run(base, "sess-1")
            self._observe(base, run_id, "repo_exploration", "inspecting")

            first = self._status(base)
            self.assertNotIn("unchanged_since_last_emission", first)
            self.assertIn("active_executors", first)

            second = self._status(base)
            self.assertTrue(second["unchanged_since_last_emission"])
            self.assertEqual(second["active_executor_count"], len(first["active_executors"]))
            self.assertNotIn("active_executors", second)
            self.assertLess(
                len(json.dumps(second, sort_keys=True)),
                len(json.dumps(first, sort_keys=True)),
                "suppression must actually reduce what reaches agent context",
            )

    def test_full_is_never_suppressed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            run_id = self._bound_run(base, "sess-1")
            self._observe(base, run_id, "repo_exploration", "inspecting")

            self._status(base)
            for _ in range(3):
                full = self._status(base, "--full")
                self.assertNotIn("unchanged_since_last_emission", full)
                self.assertIn("active_executors", full)

    def test_new_executor_activity_reopens_the_full_projection(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            run_id = self._bound_run(base, "sess-1")
            self._observe(base, run_id, "repo_exploration", "inspecting")

            self._status(base)
            self.assertTrue(self._status(base)["unchanged_since_last_emission"])

            self._observe(base, run_id, "diff_started", "changed files")
            reopened = self._status(base)
            self.assertNotIn("unchanged_since_last_emission", reopened)
            self.assertIn("active_executors", reopened)

    def test_a_second_executor_reopens_the_full_projection(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            first_run = self._bound_run(base, "sess-1")
            self._observe(base, first_run, "repo_exploration", "inspecting")
            self._status(base)
            self.assertTrue(self._status(base)["unchanged_since_last_emission"])

            second_run = self._bound_run(base, "sess-2")
            self._observe(base, second_run, "repo_exploration", "inspecting")
            reopened = self._status(base)
            self.assertNotIn("unchanged_since_last_emission", reopened)
            self.assertEqual(len(reopened["active_executors"]), 2)

    def test_each_limit_keeps_its_own_comparison(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            run_id = self._bound_run(base, "sess-1")
            self._observe(base, run_id, "repo_exploration", "inspecting")

            self._status(base)
            self.assertTrue(self._status(base)["unchanged_since_last_emission"])
            self.assertNotIn("unchanged_since_last_emission", self._status(base, "--limit", "5"))
            self.assertTrue(self._status(base, "--limit", "5")["unchanged_since_last_emission"])

    def test_the_ledger_bucket_is_not_a_run_id(self) -> None:
        """The projection spans every binding, so it cannot be charged to one run."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            run_id = self._bound_run(base, "sess-1")
            self._observe(base, run_id, "repo_exploration", "inspecting")
            self._status(base)

            self.assertNotEqual(PROGRESS_STATUS_LEDGER_KEY, run_id)
            recorded = run_context_budget(paths, PROGRESS_STATUS_LEDGER_KEY, surface="progress_status:50")
            self.assertTrue(recorded["last_payload_fingerprint"])
            self.assertGreater(recorded["emitted_bytes"], 0)
            self.assertEqual(
                run_context_budget(paths, run_id, surface="progress_status:50")["emitted_bytes"],
                0,
                "progress-status must not spend an individual run's budget",
            )


if __name__ == "__main__":
    unittest.main()
