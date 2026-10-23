from __future__ import annotations

import hashlib
import json
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _local_package import load_local_package
from _platform_support import requires_domain_intelligence_store

load_local_package()
from omh.paths import resolve_paths
from omh.workflows.domain_intelligence import (
    approve_domain_candidate,
    build_domain_review,
    build_domain_status,
    canonical_profile_digest,
    capture_domain_candidate,
    reject_domain_candidate,
    retire_domain_profile,
)
from omh.workflows import domain_intelligence_operations as approval_operations
from omh.workflows import domain_intelligence_operation_store as operation_store
from omh.workflows import domain_intelligence_rejection_operations as rejection_operations


LIFECYCLE = "omh.workflows.domain_intelligence_lifecycle"


def _snapshot(root: Path) -> dict[str, bytes]:
    store = root / ".omh" / "memory" / "domain-intelligence"
    return {
        str(path.relative_to(store)): path.read_bytes()
        for path in sorted(store.rglob("*.json"))
    }


def _operation_digest(operation: dict[str, object]) -> str:
    payload = {
        key: value for key, value in operation.items() if key != "operation_digest"
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@requires_domain_intelligence_store
class DomainIntelligenceTransactionTests(unittest.TestCase):
    def _replacement(self, root: Path):
        paths = resolve_paths(root / ".omh", root / ".hermes")
        first = capture_domain_candidate(
            paths,
            scope_kind="project",
            scope_ref="transaction-repo",
            domain_id="sales",
            mappings=[("pipeline", "pipeline")],
        )["candidate"]
        approve_domain_candidate(paths, str(first["candidate_id"]))
        replacement = capture_domain_candidate(
            paths,
            scope_kind="project",
            scope_ref="transaction-repo",
            domain_id="sales",
            mappings=[("forecast", "forecast")],
        )["candidate"]
        return paths, replacement

    def _active_profile(self, root: Path):
        paths = resolve_paths(root / ".omh", root / ".hermes")
        candidate = capture_domain_candidate(
            paths,
            scope_kind="organization",
            scope_ref="retirement-org",
            domain_id="payments",
            mappings=[("capture", "capture")],
        )["candidate"]
        approved = approve_domain_candidate(paths, str(candidate["candidate_id"]))
        return paths, approved["profile"]

    def test_approval_recovers_idempotently_after_every_write_boundary(self) -> None:
        boundaries = (
            "write_approval_operation",
            "write_archive_idempotent",
            "write_review_idempotent",
            "write_profile_resumable",
            "write_candidate_resumable",
            "delete_approval_operation",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths, candidate = self._replacement(root)
                with patch(
                    f"{LIFECYCLE}.{boundary}", side_effect=OSError(f"fault:{boundary}")
                ):
                    with self.assertRaisesRegex(OSError, f"fault:{boundary}"):
                        approve_domain_candidate(
                            paths,
                            str(candidate["candidate_id"]),
                            approved_by="operator",
                        )

                recovered = approve_domain_candidate(
                    paths, str(candidate["candidate_id"]), approved_by="operator"
                )
                self.assertEqual(recovered["candidate"]["status"], "approved")
                self.assertEqual(recovered["profile"]["revision"], 2)
                operations = (
                    root / ".omh" / "memory" / "domain-intelligence" / "operations"
                )
                self.assertEqual(list(operations.glob("*.json")), [])
                self.assertEqual(
                    len(list((operations.parent / "history").glob("*.json"))), 1
                )

    def test_legacy_profile_and_review_partial_state_finalizes_pending_candidate(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, candidate = self._replacement(root)
            with patch(
                f"{LIFECYCLE}.write_candidate_resumable",
                side_effect=OSError("candidate fault"),
            ):
                with self.assertRaisesRegex(OSError, "candidate fault"):
                    approve_domain_candidate(paths, str(candidate["candidate_id"]))

            operation = next(
                (root / ".omh" / "memory" / "domain-intelligence" / "operations").glob(
                    "*.json"
                )
            )
            operation.unlink()
            recovered = approve_domain_candidate(paths, str(candidate["candidate_id"]))
            self.assertEqual(recovered["candidate"]["status"], "approved")
            self.assertEqual(
                recovered["profile"]["candidate_id"], candidate["candidate_id"]
            )

    def test_legacy_reconciliation_rejects_coordinated_profile_review_tamper(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, candidate = self._replacement(root)
            with patch(
                f"{LIFECYCLE}.write_candidate_resumable",
                side_effect=OSError("candidate fault"),
            ):
                with self.assertRaisesRegex(OSError, "candidate fault"):
                    approve_domain_candidate(paths, str(candidate["candidate_id"]))
            store = root / ".omh" / "memory" / "domain-intelligence"
            next((store / "operations").glob("*.json")).unlink()
            profile_path = next((store / "profiles").glob("*.json"))
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["workflow_hints"] = ["deep-interview"]
            profile["payload_digest"] = canonical_profile_digest(profile)
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            review_path = (
                store
                / "reviews"
                / f"direview_{profile['profile_id']}_r{profile['revision']}.json"
            )
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["payload_digest"] = profile["payload_digest"]
            review_path.write_text(json.dumps(review), encoding="utf-8")
            before = _snapshot(root)

            with self.assertRaisesRegex(
                ValueError,
                "approval_operation_lineage_mismatch|candidate_already_approved_conflict",
            ):
                approve_domain_candidate(paths, str(candidate["candidate_id"]))
            self.assertEqual(_snapshot(root), before)

    def test_rejection_refuses_candidate_with_approval_commit_or_operation(
        self,
    ) -> None:
        for boundary in ("write_archive_idempotent", "write_candidate_resumable"):
            with self.subTest(boundary=boundary), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths, candidate = self._replacement(root)
                with patch(
                    f"{LIFECYCLE}.{boundary}",
                    side_effect=OSError("approval interrupted"),
                ):
                    with self.assertRaisesRegex(OSError, "approval interrupted"):
                        approve_domain_candidate(paths, str(candidate["candidate_id"]))
                with self.assertRaisesRegex(
                    ValueError, "approval_in_progress|candidate_already_approved"
                ):
                    reject_domain_candidate(paths, str(candidate["candidate_id"]))

    def test_interrupted_rejection_fences_same_profile_approval_until_retry(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidates = [
                capture_domain_candidate(
                    paths,
                    scope_kind="project",
                    scope_ref="fenced-rejection",
                    domain_id="sales",
                    mappings=[(term, term)],
                )["candidate"]
                for term in ("pipeline", "forecast")
            ]
            rejected, approved = candidates
            with patch(
                f"{LIFECYCLE}.delete_rejection_operation",
                side_effect=OSError("rejection interrupted"),
            ):
                with self.assertRaisesRegex(OSError, "rejection interrupted"):
                    reject_domain_candidate(
                        paths,
                        str(rejected["candidate_id"]),
                        reason="insufficient_evidence",
                    )

            before = _snapshot(root)
            with self.assertRaisesRegex(ValueError, "domain_transition_in_progress"):
                approve_domain_candidate(paths, str(approved["candidate_id"]))
            self.assertEqual(_snapshot(root), before)

            recovered = reject_domain_candidate(
                paths,
                str(rejected["candidate_id"]),
                reason="insufficient_evidence",
            )
            self.assertEqual(recovered["decision"], "rejected")
            decided = approve_domain_candidate(paths, str(approved["candidate_id"]))
            self.assertEqual(decided["decision"], "approved")

    def test_completed_approval_journal_fences_retirement_until_retry(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, candidate = self._replacement(root)
            with patch(
                f"{LIFECYCLE}.delete_approval_operation",
                side_effect=OSError("approval journal stranded"),
            ):
                with self.assertRaisesRegex(OSError, "approval journal stranded"):
                    approve_domain_candidate(paths, str(candidate["candidate_id"]))

            before = _snapshot(root)
            with self.assertRaisesRegex(ValueError, "domain_transition_in_progress"):
                retire_domain_profile(
                    paths,
                    scope_kind="project",
                    scope_ref="transaction-repo",
                    domain_id="sales",
                    reason="superseded",
                )
            self.assertEqual(_snapshot(root), before)

            recovered = approve_domain_candidate(paths, str(candidate["candidate_id"]))
            self.assertEqual(recovered["profile"]["revision"], 2)
            retired = retire_domain_profile(
                paths,
                scope_kind="project",
                scope_ref="transaction-repo",
                domain_id="sales",
                reason="superseded",
            )
            self.assertEqual(retired["profile"]["revision"], 3)

    def test_interrupted_retirement_fences_replacement_approval_until_retry(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, _profile = self._active_profile(root)
            replacement = capture_domain_candidate(
                paths,
                scope_kind="organization",
                scope_ref="retirement-org",
                domain_id="payments",
                mappings=[("refund", "refund")],
            )["candidate"]
            retirement = {
                "scope_kind": "organization",
                "scope_ref": "retirement-org",
                "domain_id": "payments",
                "reason": "superseded",
            }
            with patch(
                f"{LIFECYCLE}.delete_retirement_operation",
                side_effect=OSError("retirement interrupted"),
            ):
                with self.assertRaisesRegex(OSError, "retirement interrupted"):
                    retire_domain_profile(paths, **retirement)

            before = _snapshot(root)
            with self.assertRaisesRegex(ValueError, "domain_transition_in_progress"):
                approve_domain_candidate(paths, str(replacement["candidate_id"]))
            self.assertEqual(_snapshot(root), before)

            recovered = retire_domain_profile(paths, **retirement)
            self.assertEqual(recovered["decision"], "retired")
            fresh = capture_domain_candidate(
                paths,
                scope_kind="organization",
                scope_ref="retirement-org",
                domain_id="payments",
                mappings=[("replacement", "replacement")],
            )["candidate"]
            approved = approve_domain_candidate(paths, str(fresh["candidate_id"]))
            self.assertEqual(approved["profile"]["revision"], 3)

    def test_approval_and_rejection_journals_mutually_fence_and_resume(self) -> None:
        cases = (
            (
                "approval",
                "write_review_idempotent",
                approve_domain_candidate,
                reject_domain_candidate,
                "approved",
            ),
            (
                "rejection",
                "delete_rejection_operation",
                reject_domain_candidate,
                approve_domain_candidate,
                "rejected",
            ),
        )
        for name, boundary, original, conflicting, decision in cases:
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = resolve_paths(root / ".omh", root / ".hermes")
                candidate = capture_domain_candidate(
                    paths,
                    scope_kind="user",
                    scope_ref=f"mutual-{name}",
                    domain_id="sales",
                    mappings=[("qbr", "qbr")],
                )["candidate"]
                with patch(
                    f"{LIFECYCLE}.{boundary}", side_effect=OSError("decision interrupted")
                ):
                    with self.assertRaisesRegex(OSError, "decision interrupted"):
                        original(paths, str(candidate["candidate_id"]))
                before = _snapshot(root)
                with self.assertRaisesRegex(
                    ValueError, "domain_transition_in_progress|approval_in_progress"
                ):
                    conflicting(paths, str(candidate["candidate_id"]))
                self.assertEqual(_snapshot(root), before)
                recovered = original(paths, str(candidate["candidate_id"]))
                self.assertEqual(recovered["decision"], decision)

    def test_recovery_conflict_preserves_store_byte_for_byte(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, candidate = self._replacement(root)
            with patch(
                f"{LIFECYCLE}.write_review_idempotent",
                side_effect=OSError("review fault"),
            ):
                with self.assertRaisesRegex(OSError, "review fault"):
                    approve_domain_candidate(paths, str(candidate["candidate_id"]))

            profile_path = next(
                (root / ".omh" / "memory" / "domain-intelligence" / "profiles").glob(
                    "*.json"
                )
            )
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["payload_digest"] = "0" * 64
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            before = _snapshot(root)
            with self.assertRaisesRegex(ValueError, "approval_profile_state_conflict"):
                approve_domain_candidate(paths, str(candidate["candidate_id"]))
            self.assertEqual(_snapshot(root), before)

    def test_immutable_history_and_review_conflicts_fail_closed(self) -> None:
        for artifact in ("history", "review"):
            with self.subTest(artifact=artifact), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths, candidate = self._replacement(root)
                with patch(
                    f"{LIFECYCLE}.write_archive_idempotent",
                    side_effect=OSError("archive fault"),
                ):
                    with self.assertRaisesRegex(OSError, "archive fault"):
                        approve_domain_candidate(paths, str(candidate["candidate_id"]))
                store = root / ".omh" / "memory" / "domain-intelligence"
                operation = json.loads(
                    next((store / "operations").glob("*.json")).read_text(
                        encoding="utf-8"
                    )
                )
                if artifact == "history":
                    target = (
                        store
                        / "history"
                        / f"{operation['profile_id']}_r{operation['base_profile_revision']}.json"
                    )
                else:
                    target = (
                        store
                        / "reviews"
                        / f"{operation['target_review']['review_id']}.json"
                    )
                target.write_text('{"conflict": true}\n', encoding="utf-8")
                before = _snapshot(root)
                with self.assertRaisesRegex(
                    ValueError, f"approval_{artifact}_state_conflict"
                ):
                    approve_domain_candidate(paths, str(candidate["candidate_id"]))
                self.assertEqual(_snapshot(root), before)

    def test_operation_record_requires_exact_schema_and_digest(self) -> None:
        for violation, expected in (
            ("schema", "approval_operation_schema_mismatch"),
            ("digest", "approval_operation_digest_mismatch"),
        ):
            with self.subTest(violation=violation), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths, candidate = self._replacement(root)
                with patch(
                    f"{LIFECYCLE}.write_archive_idempotent",
                    side_effect=OSError("archive fault"),
                ):
                    with self.assertRaisesRegex(OSError, "archive fault"):
                        approve_domain_candidate(paths, str(candidate["candidate_id"]))
                operation_path = next(
                    (
                        root / ".omh" / "memory" / "domain-intelligence" / "operations"
                    ).glob("*.json")
                )
                operation = json.loads(operation_path.read_text(encoding="utf-8"))
                if violation == "schema":
                    operation["unexpected"] = "metadata"
                else:
                    operation["target_revision"] = int(operation["target_revision"]) + 1
                operation_path.write_text(json.dumps(operation), encoding="utf-8")
                before = _snapshot(root)
                with self.assertRaisesRegex(ValueError, expected):
                    approve_domain_candidate(paths, str(candidate["candidate_id"]))
                self.assertEqual(_snapshot(root), before)

    def test_approval_operation_validates_full_targets_before_resume(self) -> None:
        for violation, expected in (
            ("timestamp", "invalid_profile_approved_at"),
            ("transition", "approval_operation_transition_mismatch"),
        ):
            with self.subTest(violation=violation), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths, candidate = self._replacement(root)
                with patch(
                    f"{LIFECYCLE}.write_archive_idempotent",
                    side_effect=OSError("archive fault"),
                ):
                    with self.assertRaisesRegex(OSError, "archive fault"):
                        approve_domain_candidate(paths, str(candidate["candidate_id"]))
                operation_path = next(
                    (
                        root / ".omh" / "memory" / "domain-intelligence" / "operations"
                    ).glob("*.json")
                )
                operation = json.loads(operation_path.read_text(encoding="utf-8"))
                if violation == "timestamp":
                    operation["target_profile"]["approved_at"] = "not-a-time"
                else:
                    operation["target_candidate"]["reviewed_at"] = (
                        "2099-01-01T00:00:00Z"
                    )
                    operation["target_candidate"]["updated_at"] = "2099-01-01T00:00:00Z"
                operation["operation_digest"] = _operation_digest(operation)
                operation_path.write_text(json.dumps(operation), encoding="utf-8")
                before = _snapshot(root)
                with self.assertRaisesRegex(ValueError, expected):
                    approve_domain_candidate(paths, str(candidate["candidate_id"]))
                self.assertEqual(_snapshot(root), before)

    def test_rejection_recovers_after_every_write_boundary(self) -> None:
        boundaries = (
            "write_rejection_operation",
            "write_rejection_review_idempotent",
            "write_rejection_candidate_resumable",
            "delete_rejection_operation",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = resolve_paths(root / ".omh", root / ".hermes")
                candidate = capture_domain_candidate(
                    paths,
                    scope_kind="user",
                    scope_ref="reject-user",
                    domain_id="sales",
                    mappings=[("qbr", "qbr")],
                )["candidate"]
                with patch(
                    f"{LIFECYCLE}.{boundary}", side_effect=OSError(f"fault:{boundary}")
                ):
                    with self.assertRaisesRegex(OSError, f"fault:{boundary}"):
                        reject_domain_candidate(
                            paths,
                            str(candidate["candidate_id"]),
                            reason="insufficient_evidence",
                        )
                operations = (
                    root / ".omh" / "memory" / "domain-intelligence" / "operations"
                )
                records = list(operations.glob("reject_*.json"))
                operation_was_not_written = boundary == "write_rejection_operation"
                self.assertEqual(len(records), 0 if operation_was_not_written else 1)
                targets = (
                    None
                    if operation_was_not_written
                    else json.loads(records[0].read_text(encoding="utf-8"))
                )
                if targets is not None:
                    self.assertEqual(targets["profile_id"], candidate["profile_id"])
                recovered = reject_domain_candidate(
                    paths,
                    str(candidate["candidate_id"]),
                    reason="insufficient_evidence",
                )
                self.assertEqual(recovered["decision"], "rejected")
                if targets is not None:
                    self.assertEqual(
                        recovered["candidate"], targets["target_candidate"]
                    )
                    self.assertEqual(recovered["review"], targets["target_review"])
                self.assertEqual(list(operations.glob("reject_*.json")), [])

    def test_rejection_operation_profile_claim_is_validated_before_retry(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate = capture_domain_candidate(
                paths,
                scope_kind="user",
                scope_ref="rejection-claim",
                domain_id="sales",
                mappings=[("qbr", "qbr")],
            )["candidate"]
            with patch(
                f"{LIFECYCLE}.write_rejection_review_idempotent",
                side_effect=OSError("rejection interrupted"),
            ):
                with self.assertRaisesRegex(OSError, "rejection interrupted"):
                    reject_domain_candidate(paths, str(candidate["candidate_id"]))
            operation_path = next(
                (
                    root / ".omh" / "memory" / "domain-intelligence" / "operations"
                ).glob("reject_*.json")
            )
            operation = json.loads(operation_path.read_text(encoding="utf-8"))
            operation["profile_id"] = "diprofile_tampered"
            operation["operation_digest"] = _operation_digest(operation)
            operation_path.write_text(json.dumps(operation), encoding="utf-8")
            before = _snapshot(root)
            with self.assertRaisesRegex(
                ValueError, "rejection_operation_profile_identity"
            ):
                reject_domain_candidate(paths, str(candidate["candidate_id"]))
            self.assertEqual(_snapshot(root), before)

    def test_transition_scan_fails_closed_on_malformed_or_duplicate_records(self) -> None:
        for violation in ("malformed", "duplicate"):
            with self.subTest(violation=violation), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = resolve_paths(root / ".omh", root / ".hermes")
                candidates = [
                    capture_domain_candidate(
                        paths,
                        scope_kind="project",
                        scope_ref=f"scan-{violation}",
                        domain_id="sales",
                        mappings=[(term, term)],
                    )["candidate"]
                    for term in ("pipeline", "forecast")
                ]
                operations = (
                    root / ".omh" / "memory" / "domain-intelligence" / "operations"
                )
                if violation == "malformed":
                    operations.mkdir(mode=0o700, parents=True, exist_ok=True)
                    (operations / "broken.json").write_text("{}", encoding="utf-8")
                else:
                    with patch(
                        f"{LIFECYCLE}.write_rejection_review_idempotent",
                        side_effect=OSError("rejection interrupted"),
                    ):
                        with self.assertRaisesRegex(OSError, "rejection interrupted"):
                            reject_domain_candidate(
                                paths, str(candidates[0]["candidate_id"])
                            )
                    original = next(operations.glob("reject_*.json"))
                    (operations / "duplicate.json").write_bytes(original.read_bytes())
                before = _snapshot(root)
                with self.assertRaisesRegex(
                    ValueError,
                    "domain_transition_operation_invalid|domain_transition_operation_identity_mismatch",
                ):
                    approve_domain_candidate(paths, str(candidates[1]["candidate_id"]))
                self.assertEqual(_snapshot(root), before)

    def test_operation_capacity_fails_before_writes_and_keeps_retry_resumable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidates = [
                capture_domain_candidate(
                    paths,
                    scope_kind="project",
                    scope_ref=f"operation-capacity-{index}",
                    domain_id="sales",
                    mappings=[("pipeline", "pipeline")],
                )["candidate"]
                for index in range(2)
            ]
            with patch(
                f"{LIFECYCLE}.write_rejection_review_idempotent",
                side_effect=OSError("rejection interrupted"),
            ):
                with self.assertRaisesRegex(OSError, "rejection interrupted"):
                    reject_domain_candidate(paths, str(candidates[0]["candidate_id"]))
            before = _snapshot(root)
            with (
                patch(
                    "omh.workflows.domain_intelligence_store_security.MAX_DOMAIN_ARTIFACT_FILES",
                    1,
                ),
                self.assertRaisesRegex(
                    ValueError, "decision_operation_capacity_exceeded"
                ),
            ):
                approve_domain_candidate(paths, str(candidates[1]["candidate_id"]))
            self.assertEqual(_snapshot(root), before)
            recovered = reject_domain_candidate(paths, str(candidates[0]["candidate_id"]))
            self.assertEqual(recovered["decision"], "rejected")

    def test_decision_target_capacity_fails_before_journal_creation(self) -> None:
        cases = (
            "approval-profile",
            "approval-review",
            "approval-history",
            "rejection-review",
            "retirement-review",
            "retirement-history",
        )
        for name in cases:
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                store = root / ".omh" / "memory" / "domain-intelligence"
                if name == "approval-profile":
                    paths = resolve_paths(root / ".omh", root / ".hermes")
                    candidate = capture_domain_candidate(
                        paths,
                        scope_kind="user",
                        scope_ref="approval-profile-capacity",
                        domain_id="sales",
                        mappings=[("qbr", "qbr")],
                    )["candidate"]
                    action = partial(
                        approve_domain_candidate, paths, str(candidate["candidate_id"])
                    )
                elif name.startswith("approval"):
                    paths, candidate = self._replacement(root)
                    action = partial(
                        approve_domain_candidate, paths, str(candidate["candidate_id"])
                    )
                elif name == "rejection-review":
                    paths = resolve_paths(root / ".omh", root / ".hermes")
                    candidate = capture_domain_candidate(
                        paths,
                        scope_kind="user",
                        scope_ref="rejection-capacity",
                        domain_id="sales",
                        mappings=[("qbr", "qbr")],
                    )["candidate"]
                    action = partial(
                        reject_domain_candidate, paths, str(candidate["candidate_id"])
                    )
                else:
                    paths, _profile = self._active_profile(root)
                    action = partial(
                        retire_domain_profile,
                        paths,
                        scope_kind="organization",
                        scope_ref="retirement-org",
                        domain_id="payments",
                    )

                target_kind = (
                    "history"
                    if name.endswith("history")
                    else "profiles"
                    if name.endswith("profile")
                    else "reviews"
                )
                target_directory = store / target_kind
                target_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                limit = 2 if name.endswith("history") else 1
                while len(list(target_directory.glob("*.json"))) < limit:
                    index = len(list(target_directory.glob("*.json")))
                    (target_directory / f"capacity-{index}.json").write_text(
                        "{}", encoding="utf-8"
                    )
                before = _snapshot(root)
                with (
                    patch.object(
                        operation_store.security,
                        "MAX_DOMAIN_ARTIFACT_FILES",
                        limit,
                    ),
                    self.assertRaisesRegex(ValueError, "artifact_capacity_exceeded"),
                ):
                    action()
                self.assertEqual(_snapshot(root), before)
                self.assertEqual(list((store / "operations").glob("*.json")), [])

    def test_journaled_decision_rechecks_target_capacity_before_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate = capture_domain_candidate(
                paths,
                scope_kind="user",
                scope_ref="journaled-capacity",
                domain_id="sales",
                mappings=[("qbr", "qbr")],
            )["candidate"]
            store = root / ".omh" / "memory" / "domain-intelligence"
            reviews = store / "reviews"
            real_write = rejection_operations.write_rejection_review_idempotent

            def fill_capacity_then_write(paths, operation):
                (reviews / "capacity.json").write_text("{}", encoding="utf-8")
                real_write(paths, operation)

            with (
                patch.object(
                    operation_store.security, "MAX_DOMAIN_ARTIFACT_FILES", 1
                ),
                patch(
                    f"{LIFECYCLE}.write_rejection_review_idempotent",
                    side_effect=fill_capacity_then_write,
                ),
                self.assertRaisesRegex(ValueError, "artifact_capacity_exceeded"),
            ):
                reject_domain_candidate(paths, str(candidate["candidate_id"]))

            self.assertEqual(len(list((store / "operations").glob("*.json"))), 1)
            self.assertFalse(
                (reviews / f"direview_{candidate['candidate_id']}.json").exists()
            )
            (reviews / "capacity.json").unlink()
            recovered = reject_domain_candidate(paths, str(candidate["candidate_id"]))
            self.assertEqual(recovered["decision"], "rejected")

    def test_journaled_approval_rechecks_profile_capacity_before_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate = capture_domain_candidate(
                paths,
                scope_kind="user",
                scope_ref="journaled-profile-capacity",
                domain_id="sales",
                mappings=[("qbr", "qbr")],
            )["candidate"]
            store = root / ".omh" / "memory" / "domain-intelligence"
            profiles = store / "profiles"
            real_write = approval_operations.write_profile_resumable

            def fill_capacity_then_write(paths, operation):
                (profiles / "capacity.json").write_text("{}", encoding="utf-8")
                real_write(paths, operation)

            with (
                patch.object(
                    operation_store.security, "MAX_DOMAIN_ARTIFACT_FILES", 1
                ),
                patch(
                    f"{LIFECYCLE}.write_profile_resumable",
                    side_effect=fill_capacity_then_write,
                ),
                self.assertRaisesRegex(ValueError, "artifact_capacity_exceeded"),
            ):
                approve_domain_candidate(paths, str(candidate["candidate_id"]))

            self.assertEqual(len(list((store / "operations").glob("*.json"))), 1)
            self.assertEqual(list(profiles.glob("dprof_*.json")), [])
            (profiles / "capacity.json").unlink()
            recovered = approve_domain_candidate(paths, str(candidate["candidate_id"]))
            self.assertEqual(recovered["candidate"]["status"], "approved")

    def test_decision_cleanup_uses_anchored_operations_directory(self) -> None:
        cases = ("approval", "rejection", "retirement")
        for name in cases:
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                if name == "approval":
                    paths, candidate = self._replacement(root)
                    action = partial(
                        approve_domain_candidate, paths, str(candidate["candidate_id"])
                    )
                elif name == "rejection":
                    paths = resolve_paths(root / ".omh", root / ".hermes")
                    candidate = capture_domain_candidate(
                        paths,
                        scope_kind="user",
                        scope_ref="cleanup-rejection",
                        domain_id="sales",
                        mappings=[("qbr", "qbr")],
                    )["candidate"]
                    action = partial(
                        reject_domain_candidate, paths, str(candidate["candidate_id"])
                    )
                else:
                    paths, _profile = self._active_profile(root)
                    action = partial(
                        retire_domain_profile,
                        paths,
                        scope_kind="organization",
                        scope_ref="retirement-org",
                        domain_id="payments",
                    )

                boundary = f"delete_{name}_operation"
                with patch(f"{LIFECYCLE}.{boundary}", side_effect=OSError("stranded")):
                    with self.assertRaisesRegex(OSError, "stranded"):
                        action()

                operations = (
                    root / ".omh" / "memory" / "domain-intelligence" / "operations"
                )
                operation_path = next(operations.glob("*.json"))
                operation_name = operation_path.name
                anchored = operations.with_name("operations-anchored")
                outside = root / "outside"
                outside.mkdir(mode=0o755)
                victim = outside / operation_name
                victim.write_text("external", encoding="utf-8")
                real_unlink = operation_store.security.os.unlink

                def swap_then_unlink(filename, *, dir_fd=None):
                    self.assertIsNotNone(dir_fd)
                    operations.rename(anchored)
                    operations.symlink_to(outside, target_is_directory=True)
                    return real_unlink(filename, dir_fd=dir_fd)

                with patch.object(
                    operation_store.security.os, "unlink", side_effect=swap_then_unlink
                ):
                    action()

                self.assertEqual(victim.read_text(encoding="utf-8"), "external")
                self.assertFalse((anchored / operation_name).exists())

    def test_retirement_recovers_after_every_write_boundary(self) -> None:
        boundaries = (
            "write_retirement_operation",
            "write_retirement_archive_idempotent",
            "write_retirement_review_idempotent",
            "write_retirement_profile_resumable",
            "delete_retirement_operation",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths, _ = self._active_profile(root)
                kwargs = {
                    "scope_kind": "organization",
                    "scope_ref": "retirement-org",
                    "domain_id": "payments",
                    "reason": "superseded",
                }
                with patch(
                    f"{LIFECYCLE}.{boundary}", side_effect=OSError(f"fault:{boundary}")
                ):
                    with self.assertRaisesRegex(OSError, f"fault:{boundary}"):
                        retire_domain_profile(paths, **kwargs)
                operations = (
                    root / ".omh" / "memory" / "domain-intelligence" / "operations"
                )
                records = list(operations.glob("retire_*.json"))
                operation_was_not_written = boundary == "write_retirement_operation"
                self.assertEqual(len(records), 0 if operation_was_not_written else 1)
                targets = (
                    None
                    if operation_was_not_written
                    else json.loads(records[0].read_text(encoding="utf-8"))
                )
                recovered = retire_domain_profile(paths, **kwargs)
                self.assertEqual(recovered["decision"], "retired")
                if targets is not None:
                    self.assertEqual(recovered["profile"], targets["target_profile"])
                    self.assertEqual(recovered["review"], targets["target_review"])
                self.assertEqual(list(operations.glob("retire_*.json")), [])

    def test_rejection_and_retirement_conflicts_preserve_store(self) -> None:
        cases = (
            (
                "rejection",
                "write_rejection_review_idempotent",
                "reviews",
                "target_review",
                reject_domain_candidate,
            ),
            (
                "retirement-review",
                "write_retirement_review_idempotent",
                "reviews",
                "target_review",
                retire_domain_profile,
            ),
            (
                "retirement-history",
                "write_retirement_archive_idempotent",
                "history",
                "prior_profile",
                retire_domain_profile,
            ),
        )
        for name, boundary, dirname, target_key, action in cases:
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                if name == "rejection":
                    paths = resolve_paths(root / ".omh", root / ".hermes")
                    candidate = capture_domain_candidate(
                        paths,
                        scope_kind="user",
                        scope_ref="conflict-user",
                        domain_id="sales",
                        mappings=[("qbr", "qbr")],
                    )["candidate"]
                    args = (paths, str(candidate["candidate_id"]))
                    kwargs = {}
                else:
                    paths, _ = self._active_profile(root)
                    args = (paths,)
                    kwargs = {
                        "scope_kind": "organization",
                        "scope_ref": "retirement-org",
                        "domain_id": "payments",
                    }
                with patch(
                    f"{LIFECYCLE}.{boundary}", side_effect=OSError("decision fault")
                ):
                    with self.assertRaisesRegex(OSError, "decision fault"):
                        action(*args, **kwargs)
                store = root / ".omh" / "memory" / "domain-intelligence"
                operation = json.loads(
                    next((store / "operations").glob("*.json")).read_text(
                        encoding="utf-8"
                    )
                )
                target = operation[target_key]
                if dirname == "reviews":
                    path = store / dirname / f"{target['review_id']}.json"
                else:
                    path = (
                        store
                        / dirname
                        / f"{target['profile_id']}_r{target['revision']}.json"
                    )
                path.write_text('{"conflict": true}\n', encoding="utf-8")
                before = _snapshot(root)
                with self.assertRaisesRegex(ValueError, "state_conflict"):
                    action(*args, **kwargs)
                self.assertEqual(_snapshot(root), before)

    def test_preexisting_decision_conflicts_do_not_create_operation_records(
        self,
    ) -> None:
        for name in ("rejection", "retirement-review", "retirement-history"):
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                store = root / ".omh" / "memory" / "domain-intelligence"
                if name == "rejection":
                    paths = resolve_paths(root / ".omh", root / ".hermes")
                    candidate = capture_domain_candidate(
                        paths,
                        scope_kind="user",
                        scope_ref="existing-conflict",
                        domain_id="sales",
                        mappings=[("qbr", "qbr")],
                    )["candidate"]
                    conflict = (
                        store / "reviews" / f"direview_{candidate['candidate_id']}.json"
                    )
                    action = partial(
                        reject_domain_candidate, paths, str(candidate["candidate_id"])
                    )
                else:
                    paths, profile = self._active_profile(root)
                    if name == "retirement-review":
                        conflict = (
                            store
                            / "reviews"
                            / f"direview_{profile['profile_id']}_r{profile['revision'] + 1}.json"
                        )
                    else:
                        conflict = (
                            store
                            / "history"
                            / f"{profile['profile_id']}_r{profile['revision']}.json"
                        )
                    action = partial(
                        retire_domain_profile,
                        paths,
                        scope_kind="organization",
                        scope_ref="retirement-org",
                        domain_id="payments",
                    )
                conflict.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                conflict.write_text('{"conflict": true}\n', encoding="utf-8")
                before = _snapshot(root)
                with self.assertRaisesRegex(ValueError, "state_conflict"):
                    action()
                self.assertEqual(_snapshot(root), before)
                self.assertEqual(list((store / "operations").glob("*.json")), [])

    def test_candidate_capacity_rejects_257th_without_hiding_first_256(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            for index in range(256):
                capture_domain_candidate(
                    paths,
                    scope_kind="user",
                    scope_ref=f"capacity-{index}",
                    domain_id="sales",
                    mappings=[("qbr", "qbr")],
                )
            with self.assertRaisesRegex(ValueError, "candidate_capacity_exceeded"):
                capture_domain_candidate(
                    paths,
                    scope_kind="user",
                    scope_ref="capacity-overflow",
                    domain_id="sales",
                    mappings=[("qbr", "qbr")],
                )
            self.assertEqual(len(build_domain_review(paths, limit=256)["cards"]), 256)
            self.assertEqual(
                build_domain_status(paths)["counts"]["pending_review"], 256
            )

    def test_external_overflow_does_not_hide_valid_pending_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_paths = resolve_paths(
                root / "source" / ".omh", root / "source" / ".hermes"
            )
            candidate = capture_domain_candidate(
                source_paths,
                scope_kind="user",
                scope_ref="overflow-visible",
                domain_id="sales",
                mappings=[("qbr", "qbr")],
            )["candidate"]
            paths = resolve_paths(root / "target" / ".omh", root / "target" / ".hermes")
            candidate_dir = (
                root
                / "target"
                / ".omh"
                / "memory"
                / "domain-intelligence"
                / "candidates"
            )
            candidate_dir.mkdir(mode=0o700, parents=True)
            candidate_id = str(candidate["candidate_id"])
            for index in range(256):
                external_id = f"dicand_{index:016x}"
                if external_id == candidate_id:
                    external_id = "dicand_ffffffffffffffff"
                (candidate_dir / f"{external_id}.json").write_text(
                    json.dumps({"candidate_id": external_id}),
                    encoding="utf-8",
                )
            (candidate_dir / f"{candidate_id}.json").write_text(
                json.dumps(candidate),
                encoding="utf-8",
            )

            review = build_domain_review(paths, limit=256)
            status = build_domain_status(paths)

            self.assertEqual(
                [card["candidate_id"] for card in review["cards"]],
                [candidate_id],
            )
            self.assertEqual(review["counts"]["pending_review"], 1)
            self.assertEqual(status["counts"]["pending_review"], 1)
            self.assertIn(
                "artifact_file_count_exceeded",
                {item["reason"] for item in review["diagnostics"]},
            )

    def test_operation_symlink_is_rejected_without_mutating_domain_artifacts(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, candidate = self._replacement(root)
            operations = root / ".omh" / "memory" / "domain-intelligence" / "operations"
            operations.mkdir(mode=0o700, exist_ok=True)
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            (operations / f"approve_{candidate['candidate_id']}.json").symlink_to(
                outside
            )
            before = _snapshot(root)
            with self.assertRaisesRegex(ValueError, "symlink"):
                approve_domain_candidate(paths, str(candidate["candidate_id"]))
            self.assertEqual(_snapshot(root), before)


if __name__ == "__main__":
    unittest.main()
