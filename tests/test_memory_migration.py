from __future__ import annotations

import json
import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from _local_package import load_local_package

load_local_package()
from omh.local_store import atomic_write_json, read_json_object
from omh.paths import OmhPaths
from omh.plugin_bundle.omh.memory_governance import canonical_payload_digest
from omh.workflows.memory_migration import (
    build_memory_migration_inventory,
    reactivate_memory_artifact,
    validate_memory_copy_link_manifest,
    write_memory_migration_ledger,
)

NOW = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "memory_migration" / "store"


def _paths(root: str) -> OmhPaths:
    return OmhPaths(Path(root) / "omh", Path(root) / "hermes")


def _copy_store(paths: OmhPaths) -> None:
    shutil.copytree(FIXTURE, paths.memory_dir)


def _clear_operations(paths: OmhPaths) -> None:
    shutil.rmtree(paths.memory_operations_dir)


def _review(paths: OmhPaths, name: str = "approval") -> str:
    record = read_json_object(paths.memory_dir / "records" / "legacy-record.json")
    assert record is not None
    review_id = f"review-{name}"
    identity = {
        "schema_version": "project_memory_record/v1",
        "id": "legacy-record",
        "id_key": "record_id",
        "revision": 1,
        "scope": {"kind": "project", "ref": "default"},
    }
    atomic_write_json(
        paths.memory_dir / "reviews" / f"{review_id}.json",
        {
            "schema_version": "project_memory_review_record/v2",
            "review_id": review_id,
            "artifact_identity": identity,
            "decision": "approved_manual",
            "reviewer_claim": "operator",
            "payload_digest": canonical_payload_digest(record),
            "policy_version": "governance/v2",
            "reviewed_at": "2026-07-30T12:00:00Z",
        },
        private=True,
    )
    return review_id


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value)) if value else set()
    return set()


def _review_artifact(paths: OmhPaths, artifact: dict, *, review_id: str, identifier: str, id_key: str) -> str:
    identity = {
        "schema_version": artifact["schema_version"],
        "id": identifier,
        "id_key": id_key,
        "revision": artifact.get("revision", 1),
        "scope": artifact["scope"],
    }
    atomic_write_json(
        paths.memory_dir / "reviews" / f"{review_id}.json",
        {
            "schema_version": "project_memory_review_record/v2",
            "review_id": review_id,
            "artifact_identity": identity,
            "decision": "approved_manual",
            "reviewer_claim": "operator",
            "payload_digest": canonical_payload_digest(artifact),
            "policy_version": "governance/v2",
            "reviewed_at": "2026-07-30T12:00:00Z",
        },
        private=True,
    )
    return review_id


class MemoryMigrationTests(unittest.TestCase):
    def test_inventory_covers_surfaces_without_content_or_mutation(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _copy_store(paths)
            before = sorted(path.relative_to(paths.memory_dir) for path in paths.memory_dir.rglob("*"))
            inventory = build_memory_migration_inventory(paths)
            buckets = {item["source_bucket"] for item in inventory["artifacts"]}
            self.assertTrue({"active_records", "scope_items", "system_blocks", "reference_blocks", "archive_history", "archive_retirements_journal", "candidates", "reviews", "indexes", "block_links", "provider_write_journal", "provider_consolidation_journal", "migration_ledgers", "incomplete_operations"} <= buckets)
            self.assertTrue(inventory["dry_run"])
            self.assertIn("corrupt", inventory["counts"]["classification"])
            self.assertTrue(inventory["quarantine_proposals"])
            self.assertEqual(before, sorted(path.relative_to(paths.memory_dir) for path in paths.memory_dir.rglob("*")))
            self.assertFalse({"summary", "value", "content", "absolute_path", "payload_digest", "hash"} & _keys(inventory))

    def test_explicit_ledger_uses_real_operation_runner_and_is_bounded(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _copy_store(paths)
            _clear_operations(paths)
            inventory = build_memory_migration_inventory(paths)
            inventory["artifacts"].append({"source_bucket": "records", "summary": "must never persist"})
            ledger = write_memory_migration_ledger(paths, inventory, ledger_id="migration-ledger", now=NOW)
            stored = read_json_object(paths.memory_migrations_dir / "migration-ledger.json")
            operation = read_json_object(paths.memory_operations_dir / f"{ledger['operation_id']}.json")
            assert stored is not None and operation is not None
            self.assertEqual(stored["schema_version"], "memory_migration_inventory/v1")
            self.assertEqual(operation["state"], "completed")
            self.assertNotIn("summary", _keys(stored))
            self.assertLessEqual(len(stored["artifacts"]), 100)

    def test_reactivation_needs_apply_and_matching_immutable_review(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _copy_store(paths)
            _clear_operations(paths)
            self.assertEqual(
                reactivate_memory_artifact(paths, "legacy-record", review_id="legacy-review", now=NOW)["reason_code"],
                "matching_immutable_review_required",
            )
            review_id = _review(paths)
            self.assertEqual(
                reactivate_memory_artifact(paths, "legacy-record", review_id=review_id, now=NOW)["reason_code"],
                "apply_required",
            )
            result = reactivate_memory_artifact(paths, "legacy-record", review_id=review_id, apply=True, now=NOW)
            record = read_json_object(paths.memory_dir / "records" / "legacy-record.json")
            review = read_json_object(paths.memory_dir / "reviews" / f"{result['review_id']}.json")
            assert record is not None and review is not None
            self.assertTrue(result["applied"])
            self.assertEqual(record["schema_version"], "project_memory_record/v2")
            self.assertNotEqual(record["record_id"], "legacy-record")
            self.assertEqual(record["admission"]["review_id"], result["review_id"])
            self.assertEqual(review["prior_review_id"], review_id)
            self.assertFalse({"summary", "value", "payload_digest", "hash"} & _keys(result))

    def test_interrupted_reactivation_resumes_once_without_duplicate_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _copy_store(paths)
            _clear_operations(paths)
            review_id = _review(paths, "retry")
            writes = {"count": 0}

            def fail_second(_: str) -> None:
                writes["count"] += 1
                if writes["count"] == 2:
                    raise RuntimeError("injected write failure")

            with self.assertRaisesRegex(RuntimeError, "injected"):
                reactivate_memory_artifact(paths, "legacy-record", review_id=review_id, apply=True, now=NOW, write_hook=fail_second)
            retry = reactivate_memory_artifact(paths, "legacy-record", review_id=review_id, apply=True, now=NOW)
            record = read_json_object(paths.memory_dir / "records" / "legacy-record.json")
            assert record is not None
            operations = list(paths.memory_operations_dir.glob("*.json"))
            self.assertTrue(retry["applied"])
            self.assertEqual(len(operations), 1)
            self.assertEqual(record["admission"]["review_id"], retry["review_id"])

    def test_copy_link_manifest_is_field_limited(self) -> None:
        identity = {"schema_version": "project_memory_record/v2", "id": "migrated-record", "id_key": "record_id", "revision": 1, "scope": {"kind": "project", "ref": "default"}}
        manifest = {"schema_version": "memory_copy_link_manifest/v1", "artifact_identity": identity, "links": [{"relation": "copy", "artifact_identity": identity}]}
        self.assertEqual(validate_memory_copy_link_manifest(manifest), [])
        self.assertIn("manifest_has_unsupported_fields", validate_memory_copy_link_manifest({**manifest, "summary": "forbidden"}))

    def test_unsafe_or_malformed_artifacts_stay_legacy_without_an_operation(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _copy_store(paths)
            _clear_operations(paths)
            target = paths.memory_dir / "records" / "legacy-record.json"
            unsafe = read_json_object(target)
            assert unsafe is not None
            unsafe["summary"] = "api_token=never-reactivate"
            atomic_write_json(target, unsafe, private=True)
            review_id = _review(paths, "unsafe-content")
            result = reactivate_memory_artifact(paths, "legacy-record", review_id=review_id, apply=True, now=NOW)
            self.assertEqual(result["reason_code"], "safety_rescan_required")
            self.assertEqual(read_json_object(target)["schema_version"], "project_memory_record/v1")
            self.assertFalse(paths.memory_operations_dir.exists())

    def test_scope_item_and_block_reactivate_with_exact_immutable_reviews(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _copy_store(paths)
            _clear_operations(paths)
            scope_document = read_json_object(paths.memory_dir / "scopes" / "project.json")
            assert scope_document is not None
            scope_artifact = {
                **scope_document["items"]["legacy-scope"],
                "schema_version": scope_document["schema_version"],
                "scope": scope_document["scope"],
            }
            scope_review = _review_artifact(
                paths,
                scope_artifact,
                review_id="review-legacy-scope",
                identifier="legacy-scope",
                id_key="item_id",
            )
            scope_result = reactivate_memory_artifact(
                paths,
                "legacy-scope",
                review_id=scope_review,
                artifact_kind="scope_item",
                apply=True,
                now=NOW,
            )
            scope_after = read_json_object(paths.memory_dir / "scopes" / "project.json")
            assert scope_after is not None
            migrated_scope = next(iter(scope_after["items"].values()))
            self.assertTrue(scope_result["applied"])
            self.assertEqual(migrated_scope["schema_version"], "omh_memory_scope/v2")
            self.assertEqual(migrated_scope["admission"]["review_id"], scope_result["review_id"])

            block = read_json_object(paths.memory_dir / "blocks" / "system" / "legacy-system.json")
            assert block is not None
            block_review = _review_artifact(
                paths,
                block,
                review_id="review-legacy-block",
                identifier="legacy-system",
                id_key="block_id",
            )
            block_result = reactivate_memory_artifact(
                paths,
                "legacy-system",
                review_id=block_review,
                artifact_kind="block",
                apply=True,
                now=NOW,
            )
            block_after = read_json_object(paths.memory_dir / "blocks" / "system" / "legacy-system.json")
            assert block_after is not None
            self.assertTrue(block_result["applied"])
            self.assertEqual(block_after["schema_version"], "omh_memory_block/v2")
            self.assertEqual(block_after["admission"]["review_id"], block_result["review_id"])
            self.assertTrue((paths.memory_dir / "block_reviews" / f"{block_result['review_id']}.json").exists())

    def test_inventory_is_deterministic_capped_and_hostile_input_safe(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _copy_store(paths)
            records = paths.memory_dir / "records"
            for index in range(205):
                atomic_write_json(
                    records / f"extra-{index:03}.json",
                    {
                        "schema_version": "project_memory_record/v1",
                        "record_id": f"extra-{index:03}",
                        "revision": 1,
                        "record_type": "fact",
                        "summary": "bounded fixture",
                        "scope": {"kind": "project", "ref": "default"},
                    },
                    private=True,
                )
            outside = Path(tmp) / "outside.json"
            outside.write_text('{"summary":"hostile-secret"}', encoding="utf-8")
            (records / "000-hostile.json").symlink_to(outside)

            first = build_memory_migration_inventory(paths)
            second = build_memory_migration_inventory(paths)

            self.assertEqual(first, second)
            self.assertEqual(len(first["artifacts"]), 200)
            self.assertGreater(first["counts"]["total"], 200)
            self.assertEqual(first["omissions"], [{"reason_code": "inventory_item_limit", "omitted_count": first["counts"]["total"] - 200}])
            self.assertNotIn("hostile-secret", json.dumps(first))
            self.assertIn("symlink_or_path_escape", {row.get("reason_code") for row in first["quarantine_proposals"]})

    def test_ledger_validation_and_reactivation_gates_stay_fail_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _copy_store(paths)
            _clear_operations(paths)
            with self.assertRaisesRegex(ValueError, "unsupported_inventory_schema"):
                write_memory_migration_ledger(paths, {"schema_version": "wrong/v1"})
            with self.assertRaisesRegex(ValueError, "invalid_ledger_id"):
                write_memory_migration_ledger(paths, build_memory_migration_inventory(paths), ledger_id="../escape")

            for mutation, reason in (
                (lambda record: record.update({"retention": {"class": "durable", "ttl_days": 1}}), "retention_invalid"),
                (lambda record: record.update({"summary": 1}), "malformed_legacy_artifact"),
            ):
                shutil.rmtree(paths.memory_dir)
                _copy_store(paths)
                _clear_operations(paths)
                target = paths.memory_dir / "records" / "legacy-record.json"
                record = read_json_object(target)
                assert record is not None
                mutation(record)
                atomic_write_json(target, record, private=True)
                review_id = _review(paths, reason)
                result = reactivate_memory_artifact(paths, "legacy-record", review_id=review_id, apply=True, now=NOW)
                self.assertEqual(result["reason_code"], reason)
                self.assertEqual(read_json_object(target)["schema_version"], "project_memory_record/v1")
                self.assertFalse(paths.memory_operations_dir.exists())

    def test_mismatched_review_cannot_resume_an_interrupted_reactivation(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _copy_store(paths)
            _clear_operations(paths)
            review_id = _review(paths, "resume")

            def interrupt_second(name: str) -> None:
                if name == "write_immutable_review":
                    raise RuntimeError("interrupt")

            with self.assertRaisesRegex(RuntimeError, "interrupt"):
                reactivate_memory_artifact(paths, "legacy-record", review_id=review_id, apply=True, now=NOW, write_hook=interrupt_second)
            mismatched = reactivate_memory_artifact(paths, "legacy-record", review_id="review-other", apply=True, now=NOW)
            operation = next(paths.memory_operations_dir.glob("*.json"))
            self.assertEqual(mismatched["reason_code"], "matching_immutable_review_required")
            self.assertEqual(read_json_object(operation)["state"], "interrupted")
            self.assertTrue(reactivate_memory_artifact(paths, "legacy-record", review_id=review_id, apply=True, now=NOW)["applied"])

    def test_symlink_and_noncanonical_scope_stay_legacy_review_only(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            _copy_store(paths)
            _clear_operations(paths)
            review_id = _review(paths, "unsafe")
            target = paths.memory_dir / "records" / "legacy-record.json"
            outside = Path(tmp) / "outside.json"
            shutil.copyfile(target, outside)
            target.unlink()
            target.symlink_to(outside)
            blocked = reactivate_memory_artifact(paths, "legacy-record", review_id=review_id, apply=True, now=NOW)
            self.assertFalse(blocked["applied"])
            self.assertEqual(blocked["reason_code"], "symlink_or_path_escape")
            target.unlink()
            shutil.copyfile(outside, target)
            malformed = read_json_object(target)
            assert malformed is not None
            malformed["scope"] = {"kind": "outside", "ref": "default"}
            atomic_write_json(target, malformed, private=True)
            blocked = reactivate_memory_artifact(paths, "legacy-record", review_id=review_id, apply=True, now=NOW)
            self.assertEqual(blocked["reason_code"], "scope_invalid")
            self.assertEqual(read_json_object(target)["schema_version"], "project_memory_record/v1")


if __name__ == "__main__":
    unittest.main()
