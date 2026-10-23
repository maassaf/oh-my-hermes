from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _local_package import load_local_package


load_local_package()
from omh.paths import resolve_paths
from omh.workflows.domain_intelligence_contracts import (
    CLAIM_BOUNDARY,
    DOMAIN_CANDIDATE_SCHEMA_VERSION,
    DOMAIN_PROFILE_SCHEMA_VERSION,
    DOMAIN_REVIEW_RECORD_SCHEMA_VERSION,
    REDUCTION_POLICY,
    canonical_profile_digest,
    normalize_scope,
    stable_profile_id,
)
from omh.workflows.domain_intelligence_lineage import ProfileValidationContext
from omh.workflows.domain_intelligence_validation import validate_profile_artifact


_NOW = "2026-07-31T00:00:00Z"


def _validation_chain(length: int) -> tuple[
    dict[str, object], ProfileValidationContext
]:
    scope = normalize_scope("project", "repo-lineage-depth")
    profile_id = stable_profile_id(scope, "payments")
    content = {
        "scope": scope,
        "domain_id": "payments",
        "vocabulary_mappings": [{"phrase": "capture", "canonical": "capture"}],
        "workflow_hints": [],
        "confidence": {
            "estimate": 1.0,
            "evidence_strength": "bounded_operator_review",
            "observation_count": 1,
            "routing_authority": "none",
        },
        "provenance": {
            "source_class": "operator_supplied",
            "source_ref": "lineage-depth",
            "observation_count": 1,
            "raw_persisted": False,
        },
    }
    candidates: dict[str, dict[str, object]] = {}
    reviews: dict[str, dict[str, object]] = {}
    profiles: list[dict[str, object]] = []
    active: dict[str, object] | None = None
    for revision in range(1, length + 1):
        if revision % 2:
            candidate_id = f"dicand_{revision:016x}"
            candidate = {
                "schema_version": DOMAIN_CANDIDATE_SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "status": "approved",
                "profile_id": profile_id,
                **deepcopy(content),
                "base_profile_revision": revision - 1,
                "created_at": _NOW,
                "updated_at": _NOW,
                "redaction_policy": REDUCTION_POLICY,
                "claim_boundary": CLAIM_BOUNDARY,
                "reviewed_at": _NOW,
                "reviewed_by": "operator",
                "review_id": f"direview_{profile_id}_r{revision}",
                "revision": revision,
            }
            active = {
                "schema_version": DOMAIN_PROFILE_SCHEMA_VERSION,
                "profile_id": profile_id,
                "revision": revision,
                "status": "active",
                **deepcopy(content),
                "base_profile_revision": revision - 1,
                "candidate_id": candidate_id,
                "approved_by": "operator",
                "approved_at": _NOW,
                "created_at": _NOW,
                "updated_at": _NOW,
                "redaction_policy": REDUCTION_POLICY,
                "claim_boundary": CLAIM_BOUNDARY,
            }
            profile = active
            candidates[candidate_id] = candidate
            decision = "approved"
            review_candidate_id = candidate_id
            reason = "operator_request"
        else:
            if active is None:
                raise AssertionError("retirement requires an active predecessor")
            profile = {
                **deepcopy(active),
                "revision": revision,
                "status": "retired",
                "base_profile_revision": revision - 1,
                "updated_at": _NOW,
                "retired_at": _NOW,
                "retired_by": "operator",
                "retirement_reason_code": "superseded",
            }
            decision = "retired"
            review_candidate_id = ""
            reason = "superseded"
        profile["payload_digest"] = canonical_profile_digest(profile)
        review_id = f"direview_{profile_id}_r{revision}"
        reviews[review_id] = {
            "schema_version": DOMAIN_REVIEW_RECORD_SCHEMA_VERSION,
            "review_id": review_id,
            "candidate_id": review_candidate_id,
            "profile_id": profile_id,
            "revision": revision,
            "decision": decision,
            "reviewer_claim": "operator",
            "payload_digest": profile["payload_digest"],
            "reviewed_at": _NOW,
            "reason_code": reason,
            "claim_boundary": CLAIM_BOUNDARY,
        }
        profiles.append(profile)
    return profiles[-1], ProfileValidationContext(
        history={
            (profile_id, int(profile["revision"])): profile
            for profile in profiles[:-1]
        },
        candidates=candidates,
        reviews=reviews,
    )


class DomainIntelligenceLineageDepthTests(unittest.TestCase):
    def test_valid_511_revision_chain_validates_without_python_recursion(self) -> None:
        current, context = _validation_chain(511)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")

            validate_profile_artifact(paths, current, context=context)

        self.assertEqual(len(context.validated), 511)
        self.assertEqual(context.validating, set())

    def test_deep_chain_tamper_still_fails_closed(self) -> None:
        current, context = _validation_chain(511)
        tampered = context.history[(str(current["profile_id"]), 256)]
        tampered["base_profile_revision"] = 0
        tampered["payload_digest"] = canonical_profile_digest(tampered)
        review = context.reviews[f"direview_{current['profile_id']}_r256"]
        review["payload_digest"] = tampered["payload_digest"]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")

            with self.assertRaisesRegex(
                ValueError, "approved_candidate_lineage_required"
            ):
                validate_profile_artifact(paths, current, context=context)

        self.assertEqual(context.validating, set())


if __name__ == "__main__":
    unittest.main()
