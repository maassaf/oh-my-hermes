from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.paths import resolve_paths
from omh.runtime.artifacts import DEFAULT_RUN_HISTORY_LIMIT
from omh.runtime.context_budget import (
    payload_fingerprint,
    record_context_emission,
    run_context_budget,
    run_show_surface,
)


DEFAULT_SHOW_SURFACE = run_show_surface(full=False, history_limit=DEFAULT_RUN_HISTORY_LIMIT)
FULL_SHOW_SURFACE = run_show_surface(full=True)


class UnchangedRunSuppressionTests(unittest.TestCase):
    """Stop handing a polling agent the same projection to re-narrate.

    The observed 300-message session re-printed an unchanged failure list on
    every cycle. A second identical projection carries no information the first
    did not, so the surface returns a marker instead of the material.
    """

    def _base(self, root: Path) -> list[str]:
        return ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

    def _recorded_run(self, base: list[str]) -> str:
        status, stdout, stderr = run_cli(
            base + ["runtime", "record", "--skill", "oh-my-hermes", "--harness", "coding-handling"]
        )
        self.assertEqual(stderr, "")
        self.assertEqual(status, 0)
        return json.loads(stdout)["run"]["run_id"]

    def _show(self, base: list[str], run_id: str, *extra: str) -> dict:
        status, stdout, stderr = run_cli(base + ["runtime", "show", run_id, *extra])
        self.assertEqual(stderr, "")
        self.assertEqual(status, 0)
        return json.loads(stdout)

    def test_second_identical_show_returns_an_unchanged_marker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            run_id = self._recorded_run(base)

            first = self._show(base, run_id)
            self.assertNotIn("unchanged_since_last_emission", first)
            self.assertIn("run", first)

            second = self._show(base, run_id)
            self.assertTrue(second["unchanged_since_last_emission"])
            self.assertEqual(second["delta"], {})
            self.assertEqual(second["run"]["run_id"], run_id)
            self.assertIn("--full", second["full_output_command"])

    def test_the_unchanged_payload_is_smaller_than_the_full_projection(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            run_id = self._recorded_run(base)

            first = self._show(base, run_id)
            second = self._show(base, run_id)

            self.assertLess(
                len(json.dumps(second, sort_keys=True)),
                len(json.dumps(first, sort_keys=True)),
                "suppression must actually reduce what reaches agent context",
            )

    def test_full_is_never_suppressed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            run_id = self._recorded_run(base)

            self._show(base, run_id)
            for _ in range(3):
                full = self._show(base, run_id, "--full")
                self.assertNotIn("unchanged_since_last_emission", full)
                self.assertIn("events", full)
                self.assertIsNone(full["history"]["limit"])

    def test_a_changed_projection_reopens_the_full_output(self) -> None:
        """Suppression must be driven by the comparison, not by the call count.

        Recording a different fingerprint stands in for the run moving on. If
        this did not reopen, a run that changed after one quiet poll would stay
        invisible.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            run_id = self._recorded_run(base)

            self._show(base, run_id)
            self.assertTrue(self._show(base, run_id)["unchanged_since_last_emission"])

            record_context_emission(
                paths,
                run_id,
                surface=DEFAULT_SHOW_SURFACE,
                byte_count=0,
                payload_fingerprint_value="the-run-moved-on",
            )

            reopened = self._show(base, run_id)
            self.assertNotIn("unchanged_since_last_emission", reopened)
            self.assertIn("run", reopened)
            self.assertIn("events", reopened)

    def test_the_emitted_budget_hides_the_internal_fingerprint(self) -> None:
        """`--full` must look exactly as it did before this check existed.

        The fingerprint is a hash of the projection the caller already has. It
        belongs in the ledger, not in the payload.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            run_id = self._recorded_run(base)

            full = self._show(base, run_id, "--full")
            self.assertNotIn("last_payload_fingerprint", full["context_budget"])
            self.assertTrue(
                run_context_budget(paths, run_id, surface=FULL_SHOW_SURFACE)["last_payload_fingerprint"],
                "the ledger still needs the fingerprint it just stopped emitting",
            )

    def test_each_history_limit_keeps_its_own_comparison(self) -> None:
        """A different limit is a different projection, so it needs its own key.

        Sharing one key across limits meant an agent alternating `--full` and
        the default view never matched its own previous emission, so suppression
        silently never fired for either.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            run_id = self._recorded_run(base)

            self._show(base, run_id, "--full")
            self._show(base, run_id)
            self.assertTrue(
                self._show(base, run_id)["unchanged_since_last_emission"],
                "an interleaved --full call must not reset the default view's comparison",
            )

            self.assertNotIn("unchanged_since_last_emission", self._show(base, run_id, "--limit", "5"))
            self.assertTrue(self._show(base, run_id, "--limit", "5")["unchanged_since_last_emission"])

    def test_the_fingerprint_ignores_the_budget_envelope(self) -> None:
        """The budget changes on every call; fingerprinting it would defeat the check."""
        shown = {"run": {"run_id": "run-1", "status": "recorded"}}
        first = payload_fingerprint(shown)
        second = payload_fingerprint(dict(shown))
        self.assertEqual(first, second)
        self.assertNotEqual(first, payload_fingerprint({**shown, "run": {"run_id": "run-1", "status": "done"}}))

    def test_the_ledger_stores_the_fingerprint_per_surface(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            run_id = self._recorded_run(base)

            self.assertEqual(run_context_budget(paths, run_id, surface=DEFAULT_SHOW_SURFACE)["last_payload_fingerprint"], "")
            self._show(base, run_id)
            recorded = run_context_budget(paths, run_id, surface=DEFAULT_SHOW_SURFACE)["last_payload_fingerprint"]
            self.assertTrue(recorded)
            self.assertEqual(run_context_budget(paths, run_id, surface="fanout_show")["last_payload_fingerprint"], "")


if __name__ == "__main__":
    unittest.main()
