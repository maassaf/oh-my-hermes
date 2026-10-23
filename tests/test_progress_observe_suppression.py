from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.executor_progress import compact_suppressed_observation


class SuppressedObservationEmissionTests(unittest.TestCase):
    """An observation that reports nothing should not cost a full binding record.

    A ten-workflow simulation measured 27 suppressed `progress-observe` calls at
    ~2,014 bytes each -- 54KB spent saying nothing happened -- of which the
    binding record was 1,728 of every 2,096. Hashes, instance ids, correlation
    aliases, and transition fingerprints do not help a caller who was just told
    there is nothing to report.
    """

    def _base(self, root: Path) -> list[str]:
        return ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

    def _bound_run(self, base: list[str]) -> str:
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
                "--executor-profile", "claude_code", "--claude-session-ref", "sess-1",
            ]
        )
        self.assertEqual(stderr, "")
        self.assertEqual(status, 0)
        return run_id

    def _observe(self, base: list[str], run_id: str, *extra: str) -> dict:
        status, stdout, stderr = run_cli(
            base
            + [
                "runtime", "progress-observe", "--run", run_id,
                "--process-status", "running",
                "--profile-status", "running",
                "--profile-latest-event", "repo_exploration",
                "--profile-summary", "inspecting",
                *extra,
            ]
        )
        self.assertEqual(stderr, "")
        self.assertEqual(status, 0)
        return json.loads(stdout)

    def test_a_suppressed_observation_returns_a_binding_reference(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            run_id = self._bound_run(base)

            first = self._observe(base, run_id)
            self.assertTrue(first["reported"])
            self.assertIn("binding", first)

            repeated = self._observe(base, run_id)
            self.assertFalse(repeated["reported"])
            self.assertEqual(repeated["suppressed_reason"], "duplicate_transition")
            self.assertNotIn("binding", repeated)
            self.assertEqual(repeated["binding_ref"]["state"], "active")
            # The reference must still carry what the caller needs to correlate
            # and to see where the binding stands.
            self.assertEqual(
                repeated["binding_ref"]["last_reported_event_type"],
                first["event"]["event_type"],
            )
            self.assertEqual(repeated["binding_ref"]["target_id"], run_id)
            self.assertEqual(repeated["binding_ref"]["executor_profile"], "claude_code")
            self.assertLess(
                len(json.dumps(repeated, sort_keys=True)),
                len(json.dumps(first, sort_keys=True)),
            )

    def test_the_reporting_contract_keys_survive_compaction(self) -> None:
        """Existing callers read these three; compaction must not disturb them."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            run_id = self._bound_run(base)
            self._observe(base, run_id)

            repeated = self._observe(base, run_id)
            self.assertFalse(repeated["reported"])
            self.assertEqual(repeated["reporting_action"], "suppress")
            self.assertEqual(repeated["suppressed_reason"], "duplicate_transition")
            self.assertEqual(repeated["event"], {})
            self.assertEqual(repeated["report"], {})
            self.assertIn("not result", repeated["claim_boundary"])

    def test_full_returns_the_complete_binding(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            run_id = self._bound_run(base)
            self._observe(base, run_id)

            repeated = self._observe(base, run_id, "--full")
            self.assertFalse(repeated["reported"])
            self.assertIn("binding", repeated)
            self.assertNotIn("binding_ref", repeated)
            self.assertIn("correlation_root", repeated["binding"])

    def test_a_reported_observation_is_never_compacted(self) -> None:
        """Only silence is cheap; an actual report keeps its full shape."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = self._base(root)
            run_id = self._bound_run(base)

            first = self._observe(base, run_id)
            self.assertTrue(first["reported"])
            self.assertIn("binding", first)
            self.assertNotIn("binding_ref", first)
            self.assertTrue(first["event"]["event_type"])

    def test_compaction_drops_only_the_binding_record(self) -> None:
        payload = {
            "schema_version": "omh_executor_progress_observation/v1",
            "binding": {
                "binding_id": "run:r1:codex",
                "target_type": "run",
                "target_id": "r1",
                "executor_profile": "codex",
                "state": "active",
                "report_count": 3,
                "last_reported_event_type": "repo_exploration",
                "correlation_aliases": {"codex_session_ref": "s1"},
                "last_transition_fingerprint": "deadbeef",
            },
            "event": {},
            "report": {},
            "reported": False,
            "suppressed_reason": "repeat_interval",
            "reporting_action": "suppress",
            "chat_report": "",
            "claim_boundary": "not result evidence",
        }
        compacted = compact_suppressed_observation(payload)
        self.assertNotIn("binding", compacted)
        self.assertEqual(compacted["binding_ref"]["report_count"], 3)
        self.assertEqual(compacted["binding_ref"]["executor_profile"], "codex")
        for key in ("reported", "suppressed_reason", "reporting_action", "event", "report", "claim_boundary"):
            with self.subTest(key=key):
                self.assertEqual(compacted[key], payload[key])
        rendered = json.dumps(compacted)
        self.assertNotIn("deadbeef", rendered, "internal fingerprints must not survive compaction")
        self.assertNotIn("correlation_aliases", rendered)


if __name__ == "__main__":
    unittest.main()
