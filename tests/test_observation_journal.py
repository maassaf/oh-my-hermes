from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.workflows.observation_journal import (  # noqa: E402
    CANONICAL_OBSERVATION_EVENTS,
    build_observation_event,
    merge_lifecycle_projection,
    project_run_lifecycle,
)


class ObservationJournalUnitStatusTests(unittest.TestCase):
    def test_unit_verification_event_folds_without_overloading_run_verification(self) -> None:
        event = build_observation_event(
            {
                "target_type": "run",
                "target_id": "run-core",
                "run_id": "run-core",
                "event": "unit_verification_observed",
                "status": "observed",
                "worker_ref": "core",
                "summary": "dispatcher observed unit verification",
            }
        )

        projection = project_run_lifecycle([event], run_id="run-core")

        self.assertIn("unit_verification_observed", CANONICAL_OBSERVATION_EVENTS)
        self.assertTrue(projection["unit_verification_observed"])
        self.assertFalse(projection["verification_observed"])

    def test_unit_result_failure_receipts_are_canonical_but_not_lifecycle_rungs(self) -> None:
        for event_name in ("unit_result_missing", "unit_result_invalid"):
            with self.subTest(event=event_name):
                event = build_observation_event(
                    {
                        "target_type": "run",
                        "target_id": "run-core",
                        "run_id": "run-core",
                        "event": event_name,
                        "status": "observed",
                        "worker_ref": "core",
                        "summary": f"sidecar receipt: {event_name}",
                    }
                )
                projection = project_run_lifecycle([event], run_id="run-core")

                self.assertIn(event_name, CANONICAL_OBSERVATION_EVENTS)
                self.assertFalse(projection["execution_observed"])
                self.assertFalse(projection["unit_verification_observed"])
                self.assertEqual(projection["observation_status"], "unknown")

    def test_merge_lifecycle_projection_preserves_legacy_run_verification(self) -> None:
        merged = merge_lifecycle_projection(
            {"verification_observed": True, "observation_status": "verification_observed"},
            project_run_lifecycle([], run_id="run-core"),
        )

        self.assertTrue(merged["verification_observed"])
        self.assertFalse(merged["unit_verification_observed"])
        self.assertEqual(merged["observation_status"], "verification_observed")


if __name__ == "__main__":
    unittest.main()
