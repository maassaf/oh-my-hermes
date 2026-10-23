from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _local_package import load_local_package
from _platform_support import requires_domain_intelligence_store

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows; the suite below is skipped there
    fcntl = None  # type: ignore[assignment]

load_local_package()

from omh.workflows import domain_intelligence_profile_snapshot as profile_snapshot
from omh.workflows.domain_intelligence_snapshot_budget import (
    DomainSnapshotBudget,
    MAX_DOMAIN_SNAPSHOT_BYTES,
    MAX_DOMAIN_SNAPSHOT_JSON_NODES,
)
from omh.workflows.domain_intelligence_store_security import (
    MAX_DOMAIN_ARTIFACT_BYTES,
)


def _repository(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    store = root / ".omh" / "memory" / "domain-intelligence"
    for name in ("profiles", "reviews", "history"):
        (store / name).mkdir(parents=True)
    (store / ".store.lock").write_text("", encoding="utf-8")
    return root, store


def _binding(root: Path):
    from omh.workflows.domain_project_context import bind_cli_project

    binding = bind_cli_project(root)
    if binding is None:
        raise AssertionError("fixture failed to bind project")
    return binding


def _profile_id(index: int) -> str:
    return f"dprof_{index:024x}"


def _write_near_limit_artifacts(
    directory: Path, count: int, *, start: int = 0
) -> None:
    for index in range(start, start + count):
        profile_id = _profile_id(index)
        prefix = json.dumps(
            {"profile_id": profile_id, "padding": ""},
            separators=(",", ":"),
        ).encode("utf-8")
        padding_bytes = MAX_DOMAIN_ARTIFACT_BYTES - len(prefix) - 32
        payload = json.dumps(
            {"profile_id": profile_id, "padding": "x" * padding_bytes},
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) >= MAX_DOMAIN_ARTIFACT_BYTES:
            raise AssertionError("fixture must remain below the per-file byte cap")
        (directory / f"{profile_id}.json").write_bytes(payload)


def _write_max_node_artifacts(
    directory: Path, count: int, *, start: int = 0
) -> None:
    # root + profile_id + padding list + 4,093 scalars = 4,096 nodes.
    for index in range(start, start + count):
        profile_id = _profile_id(index)
        payload = {"profile_id": profile_id, "padding": [0] * 4093}
        (directory / f"{profile_id}.json").write_text(
            json.dumps(payload, separators=(",", ":")),
            encoding="utf-8",
        )


@requires_domain_intelligence_store
class DomainSnapshotBudgetRegressionTests(unittest.TestCase):
    def test_aggregate_bytes_reject_96_near_limit_files_before_any_decode(self) -> None:
        with TemporaryDirectory() as tmp:
            root, store = _repository(Path(tmp).resolve() / "byte-amplification")
            for offset, dirname in enumerate(("profiles", "reviews", "history")):
                _write_near_limit_artifacts(
                    store / dirname,
                    32,
                    start=offset * 32,
                )
            with _binding(root) as binding:
                real_read = profile_snapshot.read_stable_json_at
                with patch.object(
                    profile_snapshot,
                    "read_stable_json_at",
                    wraps=real_read,
                ) as reader:
                    with self.assertRaises(ValueError) as raised:
                        profile_snapshot.read_validated_domain_profiles_at(binding)

            self.assertEqual(reader.call_count, 0)
            self.assertEqual(str(raised.exception), "domain_snapshot_bytes_exceeded")
            contender = os.open(store / ".store.lock", os.O_RDWR)
            try:
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(contender)

    def test_aggregate_nodes_reject_128_max_node_files_after_bounded_reads(self) -> None:
        with TemporaryDirectory() as tmp:
            root, store = _repository(Path(tmp).resolve() / "node-amplification")
            for dirname, count, start in (
                ("profiles", 43, 0),
                ("reviews", 43, 43),
                ("history", 42, 86),
            ):
                _write_max_node_artifacts(store / dirname, count, start=start)
            with _binding(root) as binding:
                real_read = profile_snapshot.read_stable_json_at
                with patch.object(
                    profile_snapshot,
                    "read_stable_json_at",
                    wraps=real_read,
                ) as reader:
                    with self.assertRaises(ValueError) as raised:
                        profile_snapshot.read_validated_domain_profiles_at(binding)

            self.assertEqual(
                reader.call_count,
                MAX_DOMAIN_SNAPSHOT_JSON_NODES // 4096 + 1,
            )
            self.assertEqual(
                str(raised.exception), "domain_snapshot_json_nodes_exceeded"
            )

    def test_byte_and_node_budgets_accept_cap_and_reject_cap_plus_one(self) -> None:
        budget = DomainSnapshotBudget()
        budget.require_total_bytes(MAX_DOMAIN_SNAPSHOT_BYTES)
        with self.assertRaisesRegex(ValueError, "domain_snapshot_bytes_exceeded"):
            budget.require_total_bytes(MAX_DOMAIN_SNAPSHOT_BYTES + 1)

        budget.consume_nodes(MAX_DOMAIN_SNAPSHOT_JSON_NODES)
        self.assertEqual(budget.decoded_nodes, MAX_DOMAIN_SNAPSHOT_JSON_NODES)
        with self.assertRaisesRegex(
            ValueError, "domain_snapshot_json_nodes_exceeded"
        ):
            budget.consume_nodes(1)

    def test_each_health_directory_allows_1024_tiny_json_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root, store = _repository(Path(tmp).resolve() / "tiny-artifacts")
            for dirname in ("profiles", "reviews", "history"):
                for index in range(1024):
                    (store / dirname / f"tiny-{index:04d}.json").write_text(
                        "{}", encoding="utf-8"
                    )

            with _binding(root) as binding:
                with self.assertRaises(ValueError) as raised:
                    profile_snapshot.read_validated_domain_profiles_at(binding)

            self.assertEqual(str(raised.exception), "artifact_identity_mismatch")


if __name__ == "__main__":
    unittest.main()
