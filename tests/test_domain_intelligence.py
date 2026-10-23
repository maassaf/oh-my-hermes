from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _cli_harness import run_cli
from _local_package import load_local_package
from _platform_support import requires_domain_intelligence_store

load_local_package()
from omh.paths import OmhPaths, resolve_paths
from omh.workflows.domain_intelligence import (
    approve_domain_candidate,
    build_domain_review,
    build_domain_status,
    capture_domain_candidate,
    canonical_profile_digest,
    list_domain_profiles,
    reject_domain_candidate,
    retire_domain_profile,
    stable_profile_id,
)


FORBIDDEN_RAW_KEYS = {"prompt", "raw", "body", "content", "transcript", "hidden_reasoning"}


def _json_files(root: Path, dirname: str) -> list[Path]:
    return sorted((root / ".omh" / "memory" / "domain-intelligence" / dirname).glob("*.json"))


def _store_snapshot(root: Path) -> dict[str, bytes]:
    store = root / ".omh" / "memory" / "domain-intelligence"
    return {str(path.relative_to(store)): path.read_bytes() for path in sorted(store.rglob("*.json"))}


def _capture_project_candidate(paths: OmhPaths, scope_ref: str, phrase: str) -> dict[str, object]:
    return capture_domain_candidate(
        paths,
        scope_kind="project",
        scope_ref=scope_ref,
        domain_id="payments",
        mappings=[(phrase, phrase)],
    )["candidate"]


def _assert_no_raw_prompt_fields(testcase: unittest.TestCase, value: object) -> None:
    if isinstance(value, dict):
        testcase.assertTrue(FORBIDDEN_RAW_KEYS.isdisjoint({str(key).lower() for key in value}))
        for nested in value.values():
            _assert_no_raw_prompt_fields(testcase, nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_raw_prompt_fields(testcase, nested)


class DomainIntelligenceTests(unittest.TestCase):
    @requires_domain_intelligence_store
    def test_capture_review_approve_list_and_retire_lifecycle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")

            captured = capture_domain_candidate(
                paths,
                scope_kind="organization",
                scope_ref="org-acme",
                domain_id="Sales",
                mappings=[("QBR", "quarterly_business_review"), ("deal desk", "deal_desk")],
                workflow_hints=["deep-interview"],
                source_class="operator_supplied",
                source_ref="ticket-123",
                observation_count=3,
                confidence=0.75,
            )

            candidate = captured["candidate"]
            candidate_id = candidate["candidate_id"]
            self.assertEqual(candidate["schema_version"], "domain_intelligence_candidate/v1")
            self.assertEqual(candidate["status"], "pending_review")
            self.assertEqual(candidate["scope"]["kind"], "organization")
            self.assertEqual(candidate["scope"]["ref_authority"], "operator_or_wrapper_supplied")
            self.assertEqual(candidate["scope"]["identity_claim"], "not_authenticated_identity_evidence")
            self.assertEqual(candidate["domain_id"], "sales")
            self.assertEqual(candidate["base_profile_revision"], 0)
            self.assertEqual(candidate["provenance"]["raw_persisted"], False)
            _assert_no_raw_prompt_fields(self, candidate)

            review = build_domain_review(paths)
            self.assertEqual(review["schema_version"], "domain_intelligence_review_queue/v1")
            self.assertEqual(review["cards"][0]["candidate_id"], candidate_id)
            self.assertIn("routing_prior_not_override", review["claim_boundary"])

            approved = approve_domain_candidate(paths, str(candidate_id), approved_by="domain-curator")
            profile = approved["profile"]
            review_record = approved["review"]
            self.assertEqual(approved["decision"], "approved")
            self.assertEqual(profile["schema_version"], "domain_intelligence_profile/v1")
            self.assertEqual(profile["revision"], 1)
            self.assertEqual(profile["status"], "active")
            self.assertEqual(profile["payload_digest"], review_record["payload_digest"])
            self.assertEqual(profile["payload_digest"], canonical_profile_digest(profile))

            listing = list_domain_profiles(paths, scope_kind="organization", scope_ref="org-acme", domain_id="sales")
            self.assertEqual(listing["counts"]["profiles"], 1)
            self.assertEqual(listing["profiles"][0]["profile_id"], profile["profile_id"])

            retired = retire_domain_profile(
                paths,
                scope_kind="organization",
                scope_ref="org-acme",
                domain_id="sales",
                retired_by="domain-curator",
                reason="superseded",
            )
            self.assertEqual(retired["decision"], "retired")
            self.assertEqual(retired["profile"]["revision"], 2)
            self.assertEqual(retired["profile"]["status"], "retired")
            self.assertEqual(list_domain_profiles(paths)["counts"]["profiles"], 0)
            self.assertEqual(len(_json_files(root, "history")), 1)

    @requires_domain_intelligence_store
    def test_retired_profile_can_be_reactivated_by_a_reviewed_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            first = _capture_project_candidate(paths, "repo-reactivation", "capture")
            approve_domain_candidate(paths, str(first["candidate_id"]))
            retire_domain_profile(
                paths,
                scope_kind="project",
                scope_ref="repo-reactivation",
                domain_id="payments",
                reason="superseded",
            )
            replacement = _capture_project_candidate(paths, "repo-reactivation", "refund")

            reactivated = approve_domain_candidate(paths, str(replacement["candidate_id"]))

            self.assertEqual((reactivated["profile"]["revision"], reactivated["profile"]["status"]), (3, "active"))
            self.assertEqual(list_domain_profiles(paths)["counts"]["profiles"], 1)
            status = build_domain_status(paths)
            self.assertEqual(status["counts"]["active_profiles"], 1)
            self.assertEqual(status["counts"]["malformed_artifacts"], 0)

    @requires_domain_intelligence_store
    def test_reactivated_profile_rejects_tampered_retired_predecessor(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            first = _capture_project_candidate(paths, "repo-retired-chain", "capture")
            approve_domain_candidate(paths, str(first["candidate_id"]))
            retire_domain_profile(
                paths,
                scope_kind="project",
                scope_ref="repo-retired-chain",
                domain_id="payments",
                reason="superseded",
            )
            replacement = _capture_project_candidate(paths, "repo-retired-chain", "refund")
            approve_domain_candidate(paths, str(replacement["candidate_id"]))
            retired_path = _json_files(root, "history")[1]
            retired = json.loads(retired_path.read_text(encoding="utf-8"))
            retired["vocabulary_mappings"] = [{"phrase": "forged", "canonical": "forged"}]
            retired["payload_digest"] = canonical_profile_digest(retired)
            retired_path.write_text(json.dumps(retired), encoding="utf-8")
            review_path = next(
                path for path in _json_files(root, "reviews") if path.stem.endswith("_r2")
            )
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["payload_digest"] = retired["payload_digest"]
            review_path.write_text(json.dumps(review), encoding="utf-8")

            self.assertEqual(list_domain_profiles(paths)["profiles"], [])
            self.assertEqual(build_domain_status(paths)["counts"]["active_profiles"], 0)

    @requires_domain_intelligence_store
    def test_profile_queries_read_each_artifact_at_most_once(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            for revision in range(1, 9):
                candidate = _capture_project_candidate(
                    paths, "repo-query-complexity", f"term{revision}"
                )
                approve_domain_candidate(paths, str(candidate["candidate_id"]))
            artifact_count = len(_store_snapshot(root))
            from omh.workflows import domain_intelligence_store as store
            from omh.workflows import domain_intelligence_store_resolution as resolution

            for query in (list_domain_profiles, build_domain_status):
                with self.subTest(query=query.__name__), patch.object(
                    resolution,
                    "read_bounded_json",
                    wraps=resolution.read_bounded_json,
                ) as bounded_read, patch.object(
                    store,
                    "read_history_artifacts",
                    wraps=store.read_history_artifacts,
                ) as history_read:
                    query(paths)
                self.assertEqual(history_read.call_count, 1)
                self.assertLessEqual(bounded_read.call_count, artifact_count)

    def test_validation_blocks_bad_inputs_before_writing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            invalid_cases = [
                {"scope_kind": "team", "scope_ref": "ok", "domain_id": "sales", "mappings": [("x", "y")]},
                {"scope_kind": "user", "scope_ref": "../bad", "domain_id": "sales", "mappings": [("x", "y")]},
                {"scope_kind": "user", "scope_ref": "u1", "domain_id": "sales", "mappings": []},
                {"scope_kind": "user", "scope_ref": "u1", "domain_id": "sales", "mappings": [("x", "y"), ("X", "z")]},
                {"scope_kind": "user", "scope_ref": "u1", "domain_id": "sales", "mappings": [("x", "bad canonical")]},
                {"scope_kind": "user", "scope_ref": "u1", "domain_id": "sales", "mappings": [("prompt", "term")]},
                {"scope_kind": "user", "scope_ref": "u1", "domain_id": "sales", "mappings": [("x", "y")], "confidence": 1.1},
                {"scope_kind": "user", "scope_ref": "u1", "domain_id": "sales", "mappings": [("x", "y")], "observation_count": 0},
            ]
            for kwargs in invalid_cases:
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(ValueError):
                        capture_domain_candidate(paths, **kwargs)
            self.assertFalse((root / ".omh" / "memory" / "domain-intelligence" / "candidates").exists())

    @requires_domain_intelligence_store
    def test_stale_candidates_and_replacement_history(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            first = capture_domain_candidate(
                paths,
                scope_kind="project",
                scope_ref="repo",
                domain_id="payments",
                mappings=[("capture", "payment_capture")],
            )["candidate"]
            second = capture_domain_candidate(
                paths,
                scope_kind="project",
                scope_ref="repo",
                domain_id="payments",
                mappings=[("refund", "refund")],
            )["candidate"]
            approved = approve_domain_candidate(paths, str(first["candidate_id"]))
            self.assertEqual(approved["profile"]["revision"], 1)
            with self.assertRaisesRegex(ValueError, "stale_candidate"):
                approve_domain_candidate(paths, str(second["candidate_id"]))

            replacement = capture_domain_candidate(
                paths,
                scope_kind="project",
                scope_ref="repo",
                domain_id="payments",
                mappings=[("refund", "refund")],
            )["candidate"]
            self.assertEqual(replacement["base_profile_revision"], 1)
            replaced = approve_domain_candidate(paths, str(replacement["candidate_id"]))
            self.assertEqual(replaced["profile"]["revision"], 2)
            self.assertEqual(len(_json_files(root, "profiles")), 1)
            self.assertEqual(len(_json_files(root, "history")), 1)

    @requires_domain_intelligence_store
    def test_invalid_current_profile_blocks_capture_and_approve_without_archiving(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            first = capture_domain_candidate(
                paths,
                scope_kind="project",
                scope_ref="repo-av1",
                domain_id="sales",
                mappings=[("pipeline", "pipeline")],
            )["candidate"]
            approve_domain_candidate(paths, str(first["candidate_id"]))
            second = capture_domain_candidate(
                paths,
                scope_kind="project",
                scope_ref="repo-av1",
                domain_id="sales",
                mappings=[("forecast", "forecast")],
            )["candidate"]
            profile_path = _json_files(root, "profiles")[0]
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["payload_digest"] = "0" * 64
            profile_path.write_text(json.dumps(profile), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "payload_digest_mismatch|matching_review_required"):
                capture_domain_candidate(
                    paths,
                    scope_kind="project",
                    scope_ref="repo-av1",
                    domain_id="sales",
                    mappings=[("renewal", "renewal")],
                )
            with self.assertRaisesRegex(ValueError, "payload_digest_mismatch|matching_review_required"):
                approve_domain_candidate(paths, str(second["candidate_id"]))
            self.assertEqual(_json_files(root, "history"), [])

    @requires_domain_intelligence_store
    def test_tampered_current_review_metadata_blocks_writes_and_archiving(self) -> None:
        for field, value in (("reviewer_claim", "operator-2"), ("reason_code", "duplicate")):
            with self.subTest(field=field), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = resolve_paths(root / ".omh", root / ".hermes")
                first = capture_domain_candidate(
                    paths,
                    scope_kind="project",
                    scope_ref=f"repo-review-{field}",
                    domain_id="sales",
                    mappings=[("pipeline", "pipeline")],
                )["candidate"]
                approved = approve_domain_candidate(paths, str(first["candidate_id"]), approved_by="operator-1")
                self.assertEqual(approved["review"]["reason_code"], "operator_request")
                second = capture_domain_candidate(
                    paths,
                    scope_kind="project",
                    scope_ref=f"repo-review-{field}",
                    domain_id="sales",
                    mappings=[("forecast", "forecast")],
                )["candidate"]
                review_path = _json_files(root, "reviews")[0]
                review = json.loads(review_path.read_text(encoding="utf-8"))
                review[field] = value
                review_path.write_text(json.dumps(review), encoding="utf-8")
                before = _store_snapshot(root)

                with self.assertRaisesRegex(ValueError, "matching_review_required"):
                    capture_domain_candidate(
                        paths,
                        scope_kind="project",
                        scope_ref=f"repo-review-{field}",
                        domain_id="sales",
                        mappings=[("renewal", "renewal")],
                    )
                self.assertEqual(_store_snapshot(root), before)
                with self.assertRaisesRegex(ValueError, "matching_review_required"):
                    approve_domain_candidate(paths, str(second["candidate_id"]))
                self.assertEqual(_store_snapshot(root), before)
                self.assertEqual(_json_files(root, "history"), [])

    @requires_domain_intelligence_store
    def test_boolean_candidate_base_revision_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate = capture_domain_candidate(
                paths,
                scope_kind="user",
                scope_ref="user-bool-revision",
                domain_id="sales",
                mappings=[("qbr", "qbr")],
            )["candidate"]
            candidate_path = _json_files(root, "candidates")[0]
            candidate_path.write_text(json.dumps({**candidate, "base_profile_revision": True}), encoding="utf-8")

            review = build_domain_review(paths)
            self.assertEqual(review["cards"], [])
            self.assertEqual(review["diagnostics"][0]["reason"], "invalid_base_profile_revision")
            with self.assertRaisesRegex(ValueError, "invalid_base_profile_revision"):
                approve_domain_candidate(paths, str(candidate["candidate_id"]))

    @requires_domain_intelligence_store
    def test_none_profile_base_revision_is_bounded_reader_diagnostic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate = capture_domain_candidate(
                paths,
                scope_kind="organization",
                scope_ref="org-none-revision",
                domain_id="payments",
                mappings=[("capture", "capture")],
            )["candidate"]
            approved = approve_domain_candidate(paths, str(candidate["candidate_id"]))
            profile_path = _json_files(root, "profiles")[0]
            profile_path.write_text(
                json.dumps({**approved["profile"], "base_profile_revision": None}),
                encoding="utf-8",
            )

            listing = list_domain_profiles(paths)
            self.assertEqual(listing["profiles"], [])
            self.assertEqual(listing["diagnostics"][0]["reason"], "invalid_base_profile_revision")
            status = build_domain_status(paths)
            self.assertEqual(status["counts"]["active_profiles"], 0)
            self.assertEqual(status["diagnostics"][0]["reason"], "invalid_base_profile_revision")

    @requires_domain_intelligence_store
    def test_malformed_nested_metadata_reports_diagnostics_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate = capture_domain_candidate(
                paths,
                scope_kind="user",
                scope_ref="user-av2",
                domain_id="support",
                mappings=[("sla", "sla")],
            )["candidate"]
            candidate_path = _json_files(root, "candidates")[0]
            malformed = dict(candidate)
            malformed["confidence"] = {**candidate["confidence"], "observation_count": None}
            candidate_path.write_text(json.dumps(malformed), encoding="utf-8")

            review = build_domain_review(paths)
            self.assertEqual(review["cards"], [])
            self.assertEqual(review["diagnostics"][0]["reason"], "invalid_confidence_observation_count")

            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            approved = approve_domain_candidate(paths, str(candidate["candidate_id"]))
            profile_path = _json_files(root, "profiles")[0]
            bad_profile = dict(approved["profile"])
            bad_profile["provenance"] = {**approved["profile"]["provenance"], "source_ref": None}
            profile_path.write_text(json.dumps(bad_profile), encoding="utf-8")
            status = build_domain_status(paths)
            self.assertEqual(status["counts"]["active_profiles"], 0)
            self.assertEqual(status["diagnostics"][0]["reason"], "invalid_source_ref")

    def test_nan_and_infinite_confidence_are_rejected_without_writes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(value=value):
                    with self.assertRaisesRegex(ValueError, "confidence_out_of_range"):
                        capture_domain_candidate(
                            paths,
                            scope_kind="user",
                            scope_ref="user-av3",
                            domain_id="sales",
                            mappings=[("qbr", "qbr")],
                            confidence=value,
                        )
            self.assertFalse((root / ".omh" / "memory" / "domain-intelligence" / "candidates").exists())

            status, _stdout, stderr = run_cli(
                [
                    "--omh-home",
                    str(root / ".omh"),
                    "--hermes-home",
                    str(root / ".hermes"),
                    "memory",
                    "domain-capture",
                    "--scope-kind",
                    "user",
                    "--scope-ref",
                    "user-av3",
                    "--domain",
                    "sales",
                    "--mapping",
                    "QBR=qbr",
                    "--confidence",
                    "nan",
                ]
            )
            self.assertNotEqual(status, 0)
            self.assertIn("confidence_out_of_range", stderr)
            self.assertFalse((root / ".omh" / "memory" / "domain-intelligence" / "candidates").exists())

    @requires_domain_intelligence_store
    def test_fail_closed_for_malformed_and_review_mismatched_profiles(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate = capture_domain_candidate(
                paths,
                scope_kind="user",
                scope_ref="user-1",
                domain_id="marketing",
                mappings=[("utm", "utm")],
            )["candidate"]
            approved = approve_domain_candidate(paths, str(candidate["candidate_id"]))
            profile_id = str(approved["profile"]["profile_id"])
            profile_path = _json_files(root, "profiles")[0]
            review_path = _json_files(root, "reviews")[0]

            review_path.unlink()
            listing = list_domain_profiles(paths)
            self.assertEqual(listing["counts"]["profiles"], 0)
            self.assertEqual(listing["diagnostics"][0]["reason"], "matching_review_required")

            profile = dict(approved["profile"])
            profile["claim_boundary"] = "changed prose only"
            self.assertEqual(canonical_profile_digest(profile), approved["profile"]["payload_digest"])
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            review_path.write_text(json.dumps(approved["review"]), encoding="utf-8")
            changed_boundary = list_domain_profiles(paths)
            self.assertEqual(changed_boundary["counts"]["profiles"], 0)
            self.assertEqual(changed_boundary["diagnostics"][0]["reason"], "invalid_claim_boundary")

            profile["claim_boundary"] = approved["profile"]["claim_boundary"]
            profile["domain_id"] = "ads"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            mismatched = list_domain_profiles(paths)
            self.assertEqual(mismatched["counts"]["profiles"], 0)
            self.assertIn("mismatch", mismatched["diagnostics"][0]["reason"])
            self.assertEqual(stable_profile_id(approved["profile"]["scope"], "marketing"), profile_id)

            (root / ".omh" / "memory" / "domain-intelligence" / "profiles" / "bad.json").write_text("{", encoding="utf-8")
            status = build_domain_status(paths)
            self.assertGreaterEqual(status["counts"]["malformed_artifacts"], 1)

    @requires_domain_intelligence_store
    def test_review_decision_must_match_profile_status(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate = capture_domain_candidate(
                paths,
                scope_kind="organization",
                scope_ref="org-review",
                domain_id="sales",
                mappings=[("forecast", "forecast")],
            )["candidate"]
            approved = approve_domain_candidate(paths, str(candidate["candidate_id"]))
            review_path = _json_files(root, "reviews")[0]
            review = dict(approved["review"])
            review["decision"] = "retired"
            review_path.write_text(json.dumps(review), encoding="utf-8")

            listing = list_domain_profiles(paths)
            self.assertEqual(listing["profiles"], [])
            self.assertEqual(listing["diagnostics"][0]["reason"], "matching_review_required")

    @requires_domain_intelligence_store
    def test_status_counts_only_valid_review_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate = capture_domain_candidate(
                paths,
                scope_kind="project",
                scope_ref="repo",
                domain_id="ads",
                mappings=[("roas", "return_on_ad_spend")],
            )["candidate"]
            approve_domain_candidate(paths, str(candidate["candidate_id"]))
            reviews_dir = root / ".omh" / "memory" / "domain-intelligence" / "reviews"
            (reviews_dir / "bad_schema.json").write_text(
                json.dumps(
                    {
                        "schema_version": "other/v1",
                        "review_id": "direview_dprof_fake_r1",
                        "profile_id": "dprof_fake",
                        "revision": 1,
                        "decision": "approved",
                        "payload_digest": "0" * 64,
                    }
                ),
                encoding="utf-8",
            )
            (reviews_dir / "bad_digest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "domain_intelligence_review_record/v1",
                        "review_id": "direview_dprof_fake_r1",
                        "profile_id": "dprof_fake",
                        "revision": 1,
                        "decision": "approved",
                        "payload_digest": "not-a-digest",
                    }
                ),
                encoding="utf-8",
            )

            status = build_domain_status(paths)
            self.assertEqual(status["counts"]["reviews"], 1)
            reasons = {item["path_name"]: item["reason"] for item in status["diagnostics"]}
            self.assertEqual(reasons["bad_schema.json"], "unsupported_review_schema")
            self.assertEqual(reasons["bad_digest.json"], "invalid_review_digest")

    @requires_domain_intelligence_store
    def test_noncanonical_confidence_and_provenance_tampering_fail_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate = capture_domain_candidate(
                paths,
                scope_kind="user",
                scope_ref="user-meta",
                domain_id="support",
                mappings=[("sla", "sla")],
                source_class="operator_supplied",
                source_ref="source-1",
            )["candidate"]
            candidate_path = _json_files(root, "candidates")[0]
            tampered_candidate = dict(candidate)
            tampered_candidate["confidence"] = {**candidate["confidence"], "evidence_strength": "forged"}
            candidate_path.write_text(json.dumps(tampered_candidate), encoding="utf-8")
            review = build_domain_review(paths)
            self.assertEqual(review["cards"], [])
            self.assertEqual(review["diagnostics"][0]["reason"], "confidence_not_canonical")

            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            approved = approve_domain_candidate(paths, str(candidate["candidate_id"]))
            profile_path = _json_files(root, "profiles")[0]
            tampered_profile = dict(approved["profile"])
            tampered_profile["provenance"] = {**approved["profile"]["provenance"], "raw_persisted": True}
            tampered_profile["payload_digest"] = canonical_profile_digest(tampered_profile)
            profile_path.write_text(json.dumps(tampered_profile), encoding="utf-8")
            listing = list_domain_profiles(paths)
            self.assertEqual(listing["profiles"], [])
            self.assertEqual(listing["diagnostics"][0]["reason"], "provenance_not_canonical")

    @requires_domain_intelligence_store
    def test_tampered_candidate_profile_identity_cannot_approve(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate = capture_domain_candidate(
                paths,
                scope_kind="project",
                scope_ref="repo",
                domain_id="sales",
                mappings=[("pipeline", "pipeline")],
            )["candidate"]
            candidate_path = _json_files(root, "candidates")[0]
            tampered = dict(candidate)
            tampered["profile_id"] = stable_profile_id(candidate["scope"], "ads")
            candidate_path.write_text(json.dumps(tampered), encoding="utf-8")

            review = build_domain_review(paths)
            self.assertEqual(review["cards"], [])
            self.assertEqual(review["diagnostics"][0]["reason"], "candidate_profile_identity_mismatch")
            with self.assertRaisesRegex(ValueError, "candidate_profile_identity_mismatch"):
                approve_domain_candidate(paths, str(candidate["candidate_id"]))

    @requires_domain_intelligence_store
    def test_rejection_and_already_decided_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")
            candidate = capture_domain_candidate(
                paths,
                scope_kind="user",
                scope_ref="u1",
                domain_id="ads",
                mappings=[("roas", "return_on_ad_spend")],
            )["candidate"]
            rejected = reject_domain_candidate(paths, str(candidate["candidate_id"]), reason="insufficient_evidence")
            self.assertEqual(rejected["decision"], "rejected")
            self.assertEqual(list_domain_profiles(paths)["counts"]["profiles"], 0)
            with self.assertRaisesRegex(ValueError, "candidate_not_pending_review"):
                approve_domain_candidate(paths, str(candidate["candidate_id"]))

    @requires_domain_intelligence_store
    def test_reviewer_reason_and_source_refs_are_strict_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            with self.assertRaisesRegex(ValueError, "unsafe_source_ref"):
                capture_domain_candidate(
                    paths,
                    scope_kind="organization",
                    scope_ref="org-av4",
                    domain_id="sales",
                    mappings=[("qbr", "qbr")],
                    source_ref="raw sentence source",
                )

            candidate = capture_domain_candidate(
                paths,
                scope_kind="organization",
                scope_ref="org-av4",
                domain_id="sales",
                mappings=[("qbr", "qbr")],
                source_ref="source-1",
            )["candidate"]
            with self.assertRaisesRegex(ValueError, "unsafe_reviewer_claim"):
                approve_domain_candidate(paths, str(candidate["candidate_id"]), approved_by="raw reviewer")
            with self.assertRaisesRegex(ValueError, "invalid_review_reason_code"):
                reject_domain_candidate(paths, str(candidate["candidate_id"]), reason="not enough evidence")
            rejected = reject_domain_candidate(
                paths,
                str(candidate["candidate_id"]),
                rejected_by="operator-1",
                reason="insufficient_evidence",
            )
            self.assertEqual(rejected["review"]["reason_code"], "insufficient_evidence")

            candidate2 = capture_domain_candidate(
                paths,
                scope_kind="organization",
                scope_ref="org-av4",
                domain_id="sales",
                mappings=[("renewal", "renewal")],
                source_ref="source-2",
            )["candidate"]
            approved = approve_domain_candidate(paths, str(candidate2["candidate_id"]), approved_by="operator-1")
            with self.assertRaisesRegex(ValueError, "invalid_review_reason_code"):
                retire_domain_profile(
                    paths,
                    scope_kind="organization",
                    scope_ref="org-av4",
                    domain_id="sales",
                    retired_by="operator-1",
                    reason="because this is obsolete",
                )
            retired = retire_domain_profile(
                paths,
                scope_kind="organization",
                scope_ref="org-av4",
                domain_id="sales",
                retired_by="operator-1",
                reason="superseded",
            )
            self.assertEqual(approved["review"]["reviewer_claim"], "operator-1")
            self.assertEqual(retired["review"]["reason_code"], "superseded")

            base = ["--omh-home", str(root / ".omh-cli"), "--hermes-home", str(root / ".hermes-cli")]
            status, _stdout, stderr = run_cli(
                base
                + [
                    "memory",
                    "domain-capture",
                    "--scope-kind",
                    "organization",
                    "--scope-ref",
                    "org-cli-av4",
                    "--domain",
                    "sales",
                    "--mapping",
                    "QBR=qbr",
                    "--source-ref",
                    "raw sentence source",
                ]
            )
            self.assertNotEqual(status, 0)
            self.assertIn("unsafe_source_ref", stderr)

            status, stdout, stderr = run_cli(
                base
                + [
                    "memory",
                    "domain-capture",
                    "--scope-kind",
                    "organization",
                    "--scope-ref",
                    "org-cli-av4",
                    "--domain",
                    "sales",
                    "--mapping",
                    "QBR=qbr",
                    "--source-ref",
                    "source-1",
                ]
            )
            self.assertEqual((status, stderr), (0, ""))
            cli_candidate_id = json.loads(stdout)["candidate"]["candidate_id"]

            status, _stdout, stderr = run_cli(base + ["memory", "domain-approve", cli_candidate_id, "--approved-by", "raw reviewer"])
            self.assertNotEqual(status, 0)
            self.assertIn("unsafe_reviewer_claim", stderr)
            status, _stdout, stderr = run_cli(base + ["memory", "domain-reject", cli_candidate_id, "--reason", "raw sentence reason"])
            self.assertNotEqual(status, 0)
            self.assertIn("invalid_review_reason_code", stderr)
            status, stdout, stderr = run_cli(
                base + ["memory", "domain-reject", cli_candidate_id, "--rejected-by", "operator-1", "--reason", "duplicate"]
            )
            self.assertEqual((status, stderr), (0, ""))
            self.assertEqual(json.loads(stdout)["review"]["reason_code"], "duplicate")

    @requires_domain_intelligence_store
    def test_cli_capture_review_approve_list_retire_and_reject(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            status, stdout, stderr = run_cli(
                base
                + [
                    "memory",
                    "domain-capture",
                    "--scope-kind",
                    "organization",
                    "--scope-ref",
                    "org-cli",
                    "--domain",
                    "sales",
                    "--mapping",
                    "QBR=quarterly_business_review",
                    "--mapping",
                    "deal desk=deal_desk",
                    "--workflow-hint",
                    "deep-interview",
                    "--source-ref",
                    "cli-1",
                    "--observation-count",
                    "2",
                    "--confidence",
                    "0.8",
                ]
            )
            self.assertEqual((status, stderr), (0, ""))
            captured = json.loads(stdout)
            candidate_id = captured["candidate"]["candidate_id"]

            status, stdout, _stderr = run_cli(base + ["memory", "domain-review", "--candidate", candidate_id])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stdout)["cards"][0]["candidate_id"], candidate_id)

            status, stdout, _stderr = run_cli(base + ["memory", "domain-approve", candidate_id, "--approved-by", "operator"])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stdout)["decision"], "approved")

            status, stdout, _stderr = run_cli(
                base
                + [
                    "memory",
                    "domain-list",
                    "--scope-kind",
                    "organization",
                    "--scope-ref",
                    "org-cli",
                    "--domain",
                    "sales",
                ]
            )
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stdout)["counts"]["profiles"], 1)

            status, stdout, _stderr = run_cli(
                base
                + [
                    "memory",
                    "domain-retire",
                    "--scope-kind",
                    "organization",
                    "--scope-ref",
                    "org-cli",
                    "--domain",
                    "sales",
                ]
            )
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stdout)["decision"], "retired")

            status, stdout, _stderr = run_cli(
                base
                + [
                    "memory",
                    "domain-capture",
                    "--scope-kind",
                    "user",
                    "--scope-ref",
                    "user-cli",
                    "--domain",
                    "ads",
                    "--mapping",
                    "ROAS=return_on_ad_spend",
                ]
            )
            self.assertEqual(status, 0)
            reject_id = json.loads(stdout)["candidate"]["candidate_id"]
            status, stdout, _stderr = run_cli(base + ["memory", "domain-reject", reject_id, "--reason", "duplicate"])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(stdout)["decision"], "rejected")

            lock_file = root / ".omh" / "memory" / "domain-intelligence" / ".store.lock"
            self.assertTrue(lock_file.exists())
            for path in (root / ".omh" / "memory" / "domain-intelligence").rglob("*.json"):
                _assert_no_raw_prompt_fields(self, json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
