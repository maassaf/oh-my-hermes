"""Core admission and replay eligibility tests for memory governance.

Tests the basic eligibility checks: manual vs auto-safe admission modes,
multi-schema support, stale override validation, and revisions validation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from _local_package import load_local_package

load_local_package()
from omh.plugin_bundle.omh import memory_governance as governance
from omh.plugin_bundle.omh.hermes_memory import classify_record_expiry


NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
SAFE_SUMMARY = "Run deterministic release checks before deployment."


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _approved_artifact(
    *,
    schema_version: str = governance.PROJECT_MEMORY_RECORD_SCHEMA_VERSION,
    retention_class: str = "standard",
    record_type: str = "procedure",
    summary: str = SAFE_SUMMARY,
    admission_state: str = "approved_manual",
    revalidation: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    id_key = {
        governance.PROJECT_MEMORY_RECORD_SCHEMA_VERSION: "record_id",
        governance.MEMORY_SCOPE_SCHEMA_VERSION: "item_id",
        governance.MEMORY_BLOCK_SCHEMA_VERSION: "block_id",
    }[schema_version]
    artifact: dict[str, object] = {
        "schema_version": schema_version,
        id_key: "mem_fixture",
        "revision": 1,
        "record_type": record_type,
        "summary": summary,
        "scope": {"kind": "project", "ref": "default"},
        "source_class": "omh_local",
        "retention": governance.build_retention(
            retention_class,
            record_type=record_type,
            admitted_at=NOW,
        ),
    }
    if schema_version == governance.MEMORY_SCOPE_SCHEMA_VERSION:
        artifact["value"] = "release-check-policy"
    if schema_version == governance.MEMORY_BLOCK_SCHEMA_VERSION:
        artifact["value"] = "release-check-policy"
        artifact["label"] = "release-policy"
    if revalidation is not None:
        artifact["revalidation"] = revalidation
    identity = governance.stable_artifact_identity(artifact)
    payload_digest = governance.canonical_payload_digest(artifact)
    review = {
        "schema_version": governance.PROJECT_MEMORY_REVIEW_RECORD_SCHEMA_VERSION,
        "review_id": "review_fixture",
        "artifact_identity": identity,
        "decision": admission_state,
        "payload_digest": payload_digest,
        "policy_version": governance.MEMORY_GOVERNANCE_POLICY_VERSION,
        "classifier_version": governance.MEMORY_CLASSIFIER_VERSION,
    }
    artifact["admission"] = {
        "state": admission_state,
        "admitted_at": _iso(NOW),
        "review_id": "review_fixture",
        "artifact_identity": identity,
        "payload_digest": payload_digest,
        "policy_version": governance.MEMORY_GOVERNANCE_POLICY_VERSION,
        "classifier_version": governance.MEMORY_CLASSIFIER_VERSION,
    }
    return artifact, review


def _evaluate(
    artifact: dict[str, object],
    review: dict[str, object],
    *,
    now: datetime = NOW,
    **kwargs: object,
) -> dict[str, object]:
    return governance.evaluate_memory_replay(
        artifact,
        now=now,
        requested_scope={"kind": "project", "ref": "default"},
        review_resolver={"review_fixture": review},
        **kwargs,
    )


class AdmissionAndReplayTests(unittest.TestCase):
    def test_manual_and_auto_safe_admission_remain_distinct(self) -> None:
        manual, manual_review = _approved_artifact(admission_state="approved_manual")
        automatic, automatic_review = _approved_artifact(admission_state="approved_auto_safe")

        self.assertEqual(_evaluate(manual, manual_review)["admission_mode"], "approved_manual")
        self.assertEqual(_evaluate(automatic, automatic_review)["admission_mode"], "approved_auto_safe")

    def test_one_evaluator_handles_records_scope_items_and_blocks(self) -> None:
        for schema_version in (
            governance.PROJECT_MEMORY_RECORD_SCHEMA_VERSION,
            governance.MEMORY_SCOPE_SCHEMA_VERSION,
            governance.MEMORY_BLOCK_SCHEMA_VERSION,
        ):
            with self.subTest(schema_version=schema_version):
                artifact, review = _approved_artifact(schema_version=schema_version)
                result = _evaluate(artifact, review)
                self.assertTrue(result["eligible"])
                self.assertEqual(result["reason_code"], "eligible")

    def test_stale_override_requires_exact_identity_run_deadline_and_is_bounded(self) -> None:
        deadline = _iso(NOW - timedelta(minutes=1))
        artifact, review = _approved_artifact(revalidation={"deadline": deadline})
        identity = governance.stable_artifact_identity(artifact)
        override = {
            "artifact_identity": identity,
            "run_id": "run-1",
            "revalidation_deadline": deadline,
            "confirmed_at": _iso(NOW),
            "expires_at": _iso(NOW + timedelta(hours=1)),
            "reviewer_claim": "operator",
        }

        self.assertEqual(_evaluate(artifact, review, run_id="run-1", stale_override=override)["reason_code"], "eligible")
        self.assertEqual(
            _evaluate(artifact, review, run_id="run-1", stale_override=override, now=NOW + timedelta(hours=1))["reason_code"],
            "stale_override_expired",
        )
        invalid = {**override, "run_id": "run-2"}
        self.assertEqual(_evaluate(artifact, review, run_id="run-1", stale_override=invalid)["reason_code"], "stale_override_invalid")
        unbounded = {**override, "expires_at": _iso(NOW + timedelta(days=8))}
        self.assertEqual(_evaluate(artifact, review, run_id="run-1", stale_override=unbounded)["reason_code"], "stale_override_invalid")

    def test_b2_stale_override_requires_confirmed_at(self) -> None:
        """B2: Stale override must have confirmed_at field."""
        deadline = _iso(NOW - timedelta(minutes=1))
        artifact, review = _approved_artifact(revalidation={"deadline": deadline})
        identity = governance.stable_artifact_identity(artifact)
        override_missing_confirmed = {
            "artifact_identity": identity,
            "run_id": "run-1",
            "revalidation_deadline": deadline,
            "expires_at": _iso(NOW + timedelta(hours=1)),
        }
        result = _evaluate(artifact, review, run_id="run-1", stale_override=override_missing_confirmed)
        self.assertEqual(result["reason_code"], "stale_override_invalid")

    def test_b2_stale_override_requires_expires_at(self) -> None:
        """B2: Stale override must have expires_at field."""
        deadline = _iso(NOW - timedelta(minutes=1))
        artifact, review = _approved_artifact(revalidation={"deadline": deadline})
        identity = governance.stable_artifact_identity(artifact)
        override_missing_expires = {
            "artifact_identity": identity,
            "run_id": "run-1",
            "revalidation_deadline": deadline,
            "confirmed_at": _iso(NOW),
        }
        result = _evaluate(artifact, review, run_id="run-1", stale_override=override_missing_expires)
        self.assertEqual(result["reason_code"], "stale_override_invalid")

    def test_b2_stale_override_requires_revalidation_deadline(self) -> None:
        """B2: Stale override must have revalidation_deadline field."""
        deadline = _iso(NOW - timedelta(minutes=1))
        artifact, review = _approved_artifact(revalidation={"deadline": deadline})
        identity = governance.stable_artifact_identity(artifact)
        override_missing_deadline = {
            "artifact_identity": identity,
            "run_id": "run-1",
            "confirmed_at": _iso(NOW),
            "expires_at": _iso(NOW + timedelta(hours=1)),
        }
        result = _evaluate(artifact, review, run_id="run-1", stale_override=override_missing_deadline)
        self.assertEqual(result["reason_code"], "stale_override_invalid")

    def test_b3_standard_non_episode_has_no_default_ttl(self) -> None:
        """B3: Standard non-episode records must have explicit TTL or no expiry."""
        retention = governance.build_retention("standard", record_type="fact", admitted_at=NOW)
        self.assertNotIn("ttl_days", retention)
        self.assertNotIn("expires_at", retention)

        retention_episode = governance.build_retention("standard", record_type="episode", admitted_at=NOW)
        self.assertEqual(retention_episode["ttl_days"], 30)
        self.assertEqual(retention_episode["expires_at"], _iso(NOW + timedelta(days=30)))

        retention_explicit = governance.build_retention("standard", record_type="fact", admitted_at=NOW, ttl_days=14)
        self.assertEqual(retention_explicit["ttl_days"], 14)
        self.assertEqual(retention_explicit["expires_at"], _iso(NOW + timedelta(days=14)))

    def test_r1_source_class_is_set_from_artifact_in_eligible_result(self) -> None:
        """R1: Replay evaluation result must include source_class from artifact."""
        artifact, review = _approved_artifact()
        artifact["source_class"] = "omh_local"
        result = _evaluate(artifact, review)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["source_class"], "omh_local")
        
        artifact2, review2 = _approved_artifact()
        artifact2["source_class"] = "provider"
        result2 = _evaluate(artifact2, review2)
        self.assertEqual(result2["source_class"], "provider")
        
        artifact3, review3 = _approved_artifact()
        if "source_class" in artifact3:
            del artifact3["source_class"]
        result3 = _evaluate(artifact3, review3)
        self.assertIsNone(result3["source_class"])

    def test_r2_bool_revision_is_rejected_as_invalid(self) -> None:
        """R2: Boolean revisions must be rejected as invalid_revision."""
        artifact, review = _approved_artifact()
        artifact["revision"] = True
        result = _evaluate(artifact, review)
        self.assertEqual(result["reason_code"], "invalid_revision")
        self.assertFalse(result["eligible"])
        
        artifact2, review2 = _approved_artifact()
        artifact2["revision"] = False
        result2 = _evaluate(artifact2, review2)
        self.assertEqual(result2["reason_code"], "invalid_revision")
        self.assertFalse(result2["eligible"])
        
        artifact3, review3 = _approved_artifact()
        artifact3["revision"] = 1
        result3 = _evaluate(artifact3, review3)
        self.assertTrue(result3["eligible"])

    def test_approved_artifact_fails_closed_without_review_resolver(self) -> None:
        """Approved artifacts asserting review linkage fail closed when no resolver can verify it."""
        artifact, review = _approved_artifact()

        without_resolver = governance.evaluate_memory_replay(
            artifact,
            now=NOW,
            requested_scope={"kind": "project", "ref": "default"},
            review_resolver=None,
        )
        self.assertFalse(without_resolver["eligible"])
        self.assertEqual(without_resolver["reason_code"], "review_not_found")

        empty_resolver = governance.evaluate_memory_replay(
            artifact,
            now=NOW,
            requested_scope={"kind": "project", "ref": "default"},
            review_resolver={},
        )
        self.assertFalse(empty_resolver["eligible"])
        self.assertEqual(empty_resolver["reason_code"], "review_not_found")

        with_matching_resolver = _evaluate(artifact, review)
        self.assertTrue(with_matching_resolver["eligible"])
        self.assertEqual(with_matching_resolver["reason_code"], "eligible")

    def test_r4_naive_timestamp_treated_as_utc_at_boundary(self) -> None:
        """R4: Naive timestamps without tzinfo are treated as UTC."""
        record = {"ttl": {"expires_at": "2026-07-30T12:00:00"}}
        now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(classify_record_expiry(record, now=now), "expired")
        
        record2 = {"ttl": {"expires_at": "2026-07-30T12:00:01"}}
        self.assertEqual(classify_record_expiry(record2, now=now), "expiring")
        
        record3 = {"ttl": {"expires_at": "2026-08-10T12:00:00"}}
        self.assertEqual(classify_record_expiry(record3, now=now), "fresh")


if __name__ == "__main__":
    unittest.main()
