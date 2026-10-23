"""Contract tests for `handoff_input_manifest/v1` (issue #823).

Every file these tests hash is written through `atomic_write_text`, never
`Path.write_text`: the latter translates "\\n" to "\\r\\n" on Windows, and a
manifest hashes file bytes, so the same fixture would produce two different
digests on two platforms and the determinism assertions would be measuring the
line ending rather than the contract.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()
from omh.coding import handoff_input_manifest as manifest_module  # noqa: E402
from omh.coding.handoff_input_manifest import (  # noqa: E402
    HANDOFF_INPUT_MANIFEST_PIN_SCHEMA_VERSION,
    HANDOFF_INPUT_MANIFEST_SCHEMA_VERSION,
    MANIFEST_EXCLUSION_REASONS,
    MANIFEST_ITEM_KINDS,
    MANIFEST_REQUIRED_ITEM_FIELDS,
    ManifestSelection,
    build_handoff_input_manifest,
    input_manifest_digest,
    input_manifest_pin,
    input_manifest_pin_matches,
    input_manifest_summary,
    pinned_input_manifest,
    validate_handoff_input_manifest,
    validate_handoff_input_manifest_pin,
)
from omh.coding_delegation import build_coding_delegation_payload  # noqa: E402
from omh.local_store import atomic_write_text  # noqa: E402
from omh.system import binary_io  # noqa: E402
from omh.runtime.records import validate_coding_delegation_record  # noqa: E402
from _platform_support import requires_symlinks  # noqa: E402


PLAN_TEXT = "\n".join(
    [
        "# Plan",
        "",
        "## Scope",
        "Only the retry path.",
        "",
        "## Verification",
        "Run the targeted tests.",
        "",
        "## Risks",
        "None known.",
        "",
    ]
)


def _context_pack(
    *,
    included: list[dict[str, object]] | None = None,
    blocked: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "handoff_context_pack/v1",
        "executor_target": "codex",
        "session_id": "session-1",
        "scope": {"kind": "project", "ref": "demo"},
        "source_refs": [],
        "included_context": included
        if included is not None
        else [
            {
                "item_id": "memory-1",
                "key": "default_executor",
                "summary": "The reviewed default coding owner is codex.",
                "source": "omh_memory",
                "truth_level": "approved_context",
                "scope": {"kind": "project", "ref": "demo"},
            }
        ],
        "excluded_context": [],
        "blocked_by_conflicts": blocked or [],
        "redaction_policy": "metadata_only",
        "claim_boundary": "Prepared context only.",
    }


class ManifestWorkspace:
    """A workspace whose files are byte-stable on every platform."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        atomic_write_text(path, text)
        return path


class _WorkspaceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = ManifestWorkspace(Path(self._tmp.name).resolve())
        self.workspace.write("src/client.py", "def retry():\n    return True\n")
        self.workspace.write("src/helper.py", "def helper():\n    return 2\n")
        self.workspace.write("docs/plan.md", PLAN_TEXT)

    def build(self, **kwargs: object) -> dict[str, object]:
        params: dict[str, object] = {
            "executor_target": "codex",
            "session_id": "session-1",
            "scope": {"kind": "project", "ref": "demo"},
            "workspace_root": self.workspace.root,
        }
        params.update(kwargs)
        return build_handoff_input_manifest(**params)  # type: ignore[arg-type]

    def only_exclusion(self, manifest: dict[str, object], reason: str) -> dict[str, object]:
        excluded = [row for row in manifest["excluded_items"] if row["reason"] == reason]  # type: ignore[index]
        self.assertEqual(len(excluded), 1, manifest["excluded_items"])
        return excluded[0]


class AcceptanceCriterionOneTests(_WorkspaceCase):
    """AC1: every item carries provenance, selector, hash, byte cost, reason, safety."""

    def test_every_item_of_every_kind_populates_all_six_fields(self) -> None:
        manifest = self.build(
            selections=[
                ManifestSelection("file", "path_glob", "src/*.py"),
                ManifestSelection("plan_section", "plan_heading", "docs/plan.md### Verification"),
                ManifestSelection("diff", "revision_range", "HEAD~1..HEAD", content="diff --git a b\n+retry\n"),
            ],
            context_pack=_context_pack(),
        )
        self.assertEqual(validate_handoff_input_manifest(manifest), [])
        kinds = {str(item["item_kind"]) for item in manifest["items"]}  # type: ignore[index]
        self.assertEqual(kinds, set(MANIFEST_ITEM_KINDS))
        for item in manifest["items"]:  # type: ignore[index]
            for field in MANIFEST_REQUIRED_ITEM_FIELDS:
                self.assertIn(field, item)
                self.assertTrue(item[field] != "" and item[field] is not None, f"{item['item_id']}.{field}")
            self.assertEqual(item["provenance"].keys(), {"source", "local_ref", "truth_level"})
            self.assertEqual(item["selector"].keys(), {"kind", "expression"})
            self.assertTrue(str(item["hash"]).startswith("sha256:"))
            self.assertGreater(int(item["byte_cost"]), 0)
            self.assertEqual(item["safety_result"]["status"], "safe")

    def test_item_shape_is_uniform_across_kinds(self) -> None:
        manifest = self.build(
            selections=[
                ManifestSelection("file", "path", "src/client.py"),
                ManifestSelection("plan_section", "plan_heading", "docs/plan.md### Scope"),
                ManifestSelection("diff", "revision_range", "main..HEAD", content="diff --git a b\n+x\n"),
            ],
            context_pack=_context_pack(),
        )
        shapes = {tuple(sorted(item.keys())) for item in manifest["items"]}  # type: ignore[index]
        self.assertEqual(len(shapes), 1, "kind-specific data must ride in named fields, not a second shape")

    def test_payload_missing_any_required_item_field_fails_validation(self) -> None:
        manifest = self.build(selections=[ManifestSelection("file", "path", "src/client.py")])
        for field in MANIFEST_REQUIRED_ITEM_FIELDS:
            damaged = deepcopy(manifest)
            del damaged["items"][0][field]  # type: ignore[index]
            damaged["digest"] = input_manifest_digest(damaged)
            errors = validate_handoff_input_manifest(damaged)
            self.assertTrue(errors, f"a manifest missing {field} must not validate")
            self.assertTrue(any(field in error for error in errors), errors)

    def test_selector_is_reproducible_against_the_same_inputs(self) -> None:
        first = self.build(selections=[ManifestSelection("file", "path_glob", "src/*.py")])
        second = self.build(selections=[ManifestSelection("file", "path_glob", "src/*.py")])
        self.assertEqual(
            [item["item_id"] for item in first["items"]],  # type: ignore[index]
            [item["item_id"] for item in second["items"]],  # type: ignore[index]
        )
        self.assertEqual(
            [item["hash"] for item in first["items"]],  # type: ignore[index]
            [item["hash"] for item in second["items"]],  # type: ignore[index]
        )
        # Local refs are POSIX on every platform, so a manifest built on Windows
        # names the same item as one built on Linux. Both spellings are asserted
        # as literals rather than composed through os.sep.
        refs = [item["provenance"]["local_ref"] for item in first["items"]]  # type: ignore[index]
        self.assertEqual(refs, ["src/client.py", "src/helper.py"])
        for ref in refs:
            self.assertNotIn("\\", ref)


class AcceptanceCriterionTwoTests(_WorkspaceCase):
    """AC2: one test per exclusion reason the implementation can produce."""

    def test_reason_over_budget_reports_the_numbers(self) -> None:
        manifest = self.build(
            selections=[
                ManifestSelection("file", "path", "src/client.py"),
                ManifestSelection("file", "path", "src/helper.py"),
            ],
            budget_bytes=30,
        )
        row = self.only_exclusion(manifest, "over_budget")
        self.assertEqual(row["item_id"], "file:src/helper.py")
        self.assertIn("30", str(row["detail"]))
        self.assertEqual(row["byte_cost"], 27)
        budget = manifest["budget"]
        self.assertTrue(budget["over_budget"])  # type: ignore[index]
        self.assertEqual(budget["budget_bytes"], 30)  # type: ignore[index]
        self.assertEqual(budget["used_bytes"], 29)  # type: ignore[index]
        self.assertEqual(budget["requested_bytes"], 56)  # type: ignore[index]
        self.assertEqual(budget["over_budget_bytes"], 26)  # type: ignore[index]

    def test_reason_over_budget_also_covers_the_item_limit(self) -> None:
        manifest = self.build(
            selections=[ManifestSelection("file", "path_glob", "src/*.py")],
            item_limit=1,
        )
        row = self.only_exclusion(manifest, "over_budget")
        self.assertIn("limit of 1 item", str(row["detail"]))
        self.assertEqual(manifest["budget"]["item_count"], 1)  # type: ignore[index]

    def test_reason_unsafe_content(self) -> None:
        self.workspace.write("src/leak.py", 'api_key = "value"\n')
        manifest = self.build(selections=[ManifestSelection("file", "path", "src/leak.py")])
        row = self.only_exclusion(manifest, "unsafe_content")
        self.assertEqual(row["item_id"], "file:src/leak.py")
        self.assertIn("blocked", str(row["detail"]))
        self.assertEqual(manifest["items"], [])

    def test_reason_outside_workspace(self) -> None:
        manifest = self.build(selections=[ManifestSelection("file", "path", "../escape.py")])
        row = self.only_exclusion(manifest, "outside_workspace")
        self.assertEqual(manifest["items"], [])
        self.assertIn("workspace", str(row["detail"]))

    def test_reason_unreadable_source(self) -> None:
        manifest = self.build(
            selections=[
                ManifestSelection("file", "path", "src/absent.py"),
                ManifestSelection("diff", "revision_range", "HEAD~1..HEAD"),
            ]
        )
        reasons = sorted({str(row["reason"]) for row in manifest["excluded_items"]})  # type: ignore[index]
        self.assertEqual(reasons, ["unreadable_source"])
        self.assertEqual(len(manifest["excluded_items"]), 2)  # type: ignore[arg-type]

    def test_reason_duplicate_item(self) -> None:
        manifest = self.build(
            selections=[
                ManifestSelection("file", "path", "src/client.py"),
                ManifestSelection("file", "path_glob", "src/client.py"),
            ]
        )
        row = self.only_exclusion(manifest, "duplicate_item")
        self.assertEqual(row["item_id"], "file:src/client.py")
        self.assertEqual(len(manifest["items"]), 1)  # type: ignore[arg-type]

    def test_reason_blocked_by_unresolved_conflict(self) -> None:
        pack = _context_pack(
            blocked=[
                {
                    "item_id": "memory-1",
                    "key": "default_executor",
                    "severity": "blocker",
                    "reason": "Two sources disagree.",
                    "claim_boundary": "Review before reuse.",
                }
            ]
        )
        manifest = self.build(context_pack=pack)
        row = self.only_exclusion(manifest, "blocked_by_unresolved_conflict")
        self.assertEqual(row["item_id"], "reviewed_memory:memory-1")
        self.assertEqual(manifest["items"], [])

    def test_every_declared_reason_has_a_test(self) -> None:
        """The reason vocabulary and this class stay in step.

        A reason added to the contract without a case here would ship
        unexercised, which is the shape AC2 exists to prevent.
        """
        covered = {
            name.replace("test_reason_", "")
            for name in dir(self)
            if name.startswith("test_reason_")
        }
        for reason in MANIFEST_EXCLUSION_REASONS:
            self.assertTrue(
                any(name.startswith(reason) for name in covered),
                f"no exclusion test names {reason}",
            )


class AcceptanceCriterionThreeTests(_WorkspaceCase):
    """AC3: the handoff pins the exact manifest revision and digest."""

    def test_handoff_pins_revision_and_digest(self) -> None:
        payload = build_coding_delegation_payload(
            "implement the retry fix in src/client.py and add tests",
            executor_target="codex",
            context_pack=_context_pack(),
            force_coding_handoff=True,
        )
        manifest = payload["executor_handoff"]["input_manifest"]  # type: ignore[index]
        self.assertEqual(manifest["schema_version"], HANDOFF_INPUT_MANIFEST_SCHEMA_VERSION)
        self.assertEqual(manifest["revision"], 1)
        self.assertEqual(manifest["digest"], input_manifest_digest(manifest))
        self.assertEqual(validate_handoff_input_manifest(manifest), [])

    def test_editing_the_manifest_changes_the_digest(self) -> None:
        manifest = self.build(selections=[ManifestSelection("file", "path", "src/client.py")])
        revised = deepcopy(manifest)
        revised["revision"] = 2
        revised["items"] = []
        revised["digest"] = input_manifest_digest(revised)
        self.assertNotEqual(revised["digest"], manifest["digest"])

    def test_pinned_handoff_still_names_the_old_revision_after_a_later_edit(self) -> None:
        manifest = self.build(selections=[ManifestSelection("file", "path", "src/client.py")])
        handoff: dict[str, object] = {}
        handoff["input_manifest"] = pinned_input_manifest(manifest)
        pin = input_manifest_pin(handoff["input_manifest"])  # type: ignore[arg-type]
        self.assertEqual(validate_handoff_input_manifest_pin(pin), [])
        self.assertEqual(pin["schema_version"], HANDOFF_INPUT_MANIFEST_PIN_SCHEMA_VERSION)

        # The caller keeps revising its own manifest after the handoff was built.
        manifest["revision"] = 2
        manifest["items"] = []
        manifest["digest"] = input_manifest_digest(manifest)

        self.assertEqual(handoff["input_manifest"]["revision"], 1)  # type: ignore[index]
        self.assertNotEqual(handoff["input_manifest"]["digest"], manifest["digest"])  # type: ignore[index]
        self.assertFalse(input_manifest_pin_matches(pin, manifest))
        self.assertTrue(input_manifest_pin_matches(pin, handoff["input_manifest"]))

    def test_pin_recomputes_rather_than_trusting_the_stored_digest(self) -> None:
        manifest = self.build(selections=[ManifestSelection("file", "path", "src/client.py")])
        pin = input_manifest_pin(manifest)
        manifest["items"] = []  # edited without refreshing the stored digest
        self.assertFalse(input_manifest_pin_matches(pin, manifest))

    def test_a_revision_bump_alone_changes_the_digest(self) -> None:
        first = self.build(selections=[ManifestSelection("file", "path", "src/client.py")], revision=1)
        second = self.build(selections=[ManifestSelection("file", "path", "src/client.py")], revision=2)
        self.assertNotEqual(first["digest"], second["digest"])


class DeterminismTests(_WorkspaceCase):
    def test_same_inputs_and_selectors_produce_the_same_digest(self) -> None:
        selections = [
            ManifestSelection("file", "path_glob", "src/*.py"),
            ManifestSelection("plan_section", "plan_heading", "docs/plan.md### Verification"),
            ManifestSelection("diff", "revision_range", "HEAD~1..HEAD", content="diff --git a b\n+retry\n"),
        ]
        first = self.build(selections=selections, context_pack=_context_pack())
        second = self.build(selections=selections, context_pack=_context_pack())
        self.assertEqual(first, second)
        self.assertEqual(first["digest"], second["digest"])

    def test_changing_a_source_byte_changes_the_digest(self) -> None:
        before = self.build(selections=[ManifestSelection("file", "path", "src/client.py")])
        self.workspace.write("src/client.py", "def retry():\n    return False\n")
        after = self.build(selections=[ManifestSelection("file", "path", "src/client.py")])
        self.assertNotEqual(before["digest"], after["digest"])

    def test_manifest_carries_no_timestamp_field(self) -> None:
        """No clock means no clock can reach the digest seed."""
        manifest = self.build(selections=[ManifestSelection("file", "path", "src/client.py")])
        for key in manifest:
            self.assertNotIn("_at", key)
            self.assertNotIn("time", key)


class SafetyGuardTests(_WorkspaceCase):
    def test_an_unsafe_item_is_never_included(self) -> None:
        self.workspace.write("src/creds.py", 'password = "hunter2"\n')
        manifest = self.build(
            selections=[
                ManifestSelection("file", "path", "src/creds.py"),
                ManifestSelection("file", "path", "src/client.py"),
            ]
        )
        included = [str(item["item_id"]) for item in manifest["items"]]  # type: ignore[index]
        self.assertEqual(included, ["file:src/client.py"])
        self.assertEqual(self.only_exclusion(manifest, "unsafe_content")["item_id"], "file:src/creds.py")

    def test_safety_result_names_a_classifier_the_action_gate_already_declares(self) -> None:
        from omh.coding.action_gate import AUTHORITY_CLASSIFIERS

        manifest = self.build(selections=[ManifestSelection("file", "path", "src/client.py")])
        for item in manifest["items"]:  # type: ignore[index]
            self.assertIn(item["safety_result"]["classifier"], AUTHORITY_CLASSIFIERS)

    def test_a_manifest_claiming_safe_over_an_unsafe_item_fails_validation(self) -> None:
        manifest = self.build(selections=[ManifestSelection("file", "path", "src/client.py")])
        forged = deepcopy(manifest)
        forged["items"][0]["safety_result"]["status"] = "needs_review"  # type: ignore[index]
        forged["digest"] = input_manifest_digest(forged)
        self.assertTrue(validate_handoff_input_manifest(forged))

    def test_a_manifest_whose_refs_fail_the_screen_cannot_be_attached(self) -> None:
        manifest = self.build(selections=[ManifestSelection("file", "path", "src/client.py")])
        forged = deepcopy(manifest)
        forged["items"][0]["provenance"]["local_ref"] = "password = hunter2"  # type: ignore[index]
        forged["digest"] = input_manifest_digest(forged)
        with self.assertRaises(ValueError):
            pinned_input_manifest(forged)


class BudgetReportingTests(_WorkspaceCase):
    def test_an_oversized_package_is_reported_with_numbers_not_truncated(self) -> None:
        manifest = self.build(
            selections=[ManifestSelection("file", "path_glob", "src/*.py")],
            budget_bytes=10,
        )
        self.assertEqual(manifest["items"], [])
        self.assertEqual(len(manifest["excluded_items"]), 2)  # type: ignore[arg-type]
        for row in manifest["excluded_items"]:  # type: ignore[index]
            self.assertEqual(row["reason"], "over_budget")
            self.assertGreater(int(row["byte_cost"]), 0)
            self.assertIn("10", str(row["detail"]))
        # Both sources were refused before being read, and the numbers still
        # arrive: 29 + 27 requested against a 10-byte budget.
        self.assertEqual(manifest["budget"]["requested_bytes"], 56)  # type: ignore[index]
        self.assertEqual(manifest["budget"]["over_budget_bytes"], 46)  # type: ignore[index]

    def test_a_source_larger_than_the_whole_budget_reports_both_numbers(self) -> None:
        self.workspace.write("src/big.py", "# padding\n" * 200)
        manifest = self.build(
            selections=[ManifestSelection("file", "path", "src/big.py")],
            budget_bytes=100,
        )
        row = self.only_exclusion(manifest, "over_budget")
        self.assertEqual(row["byte_cost"], 2000)
        self.assertIn("2000", str(row["detail"]))
        self.assertIn("100", str(row["detail"]))

    def test_summary_reports_counts_without_carrying_items(self) -> None:
        manifest = self.build(
            selections=[ManifestSelection("file", "path_glob", "src/*.py")],
            item_limit=1,
        )
        summary = input_manifest_summary(manifest)
        self.assertEqual(summary["item_count"], 1)
        self.assertEqual(summary["excluded_count"], 1)
        self.assertEqual(summary["digest"], manifest["digest"])
        self.assertNotIn("items", summary)


class WorkspaceFileSelectionSecurityTests(_WorkspaceCase):
    def test_windows_cross_api_snapshot_ignores_only_incompatible_ctime(self) -> None:
        before = SimpleNamespace(
            st_dev=1,
            st_ino=2,
            st_mode=stat.S_IFREG | 0o600,
            st_size=3,
            st_mtime_ns=4,
            st_ctime_ns=5,
        )
        ctime_only = SimpleNamespace(**{**vars(before), "st_ctime_ns": 6})
        mtime_changed = SimpleNamespace(**{**vars(before), "st_mtime_ns": 7})
        with patch.object(manifest_module, "_WINDOWS_PATH_CTIME_IS_BIRTHTIME", True):
            self.assertEqual(
                manifest_module._cross_api_file_snapshot(before),
                manifest_module._cross_api_file_snapshot(ctime_only),
            )
            self.assertNotEqual(
                manifest_module._cross_api_file_snapshot(before),
                manifest_module._cross_api_file_snapshot(mtime_changed),
            )
        self.assertNotEqual(
            manifest_module._file_snapshot(before),
            manifest_module._file_snapshot(ctime_only),
        )

    def test_fallback_reads_windows_text_files_as_exact_binary_bytes(self) -> None:
        source = b"first line\r\nsecond line\r\n"
        candidate = self.workspace.root / "src" / "windows-lines.txt"
        candidate.write_bytes(source)
        (self.workspace.root / "src" / "unsafe.py").write_bytes(b'api_key = "value"\r\n')
        (self.workspace.root / "src" / "large.py").write_bytes(b"x" * 257)
        real_open = os.open
        real_dup = os.dup
        real_read = os.read
        binary_flag = 1 << 29
        descriptor_generations: dict[int, int] = {}
        opened_descriptors: set[tuple[int, int]] = set()
        binary_descriptors: set[tuple[int, int]] = set()
        native_setmode = binary_io._msvcrt.setmode if binary_io._msvcrt is not None else None
        native_binary_flag = getattr(os, "O_BINARY", 0)

        class FakeMsvcrt:
            @staticmethod
            def setmode(descriptor: int, mode: int) -> int:
                self.assertEqual(mode, binary_flag)
                prior = native_setmode(descriptor, native_binary_flag) if native_setmode else 0
                binary_descriptors.add((descriptor, descriptor_generations[descriptor]))
                return prior

        def windows_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
            requested_binary = bool(flags & binary_flag)
            descriptor = real_open(path, flags & ~binary_flag, *args, **kwargs)  # type: ignore[arg-type]
            generation = descriptor_generations.get(descriptor, 0) + 1
            descriptor_generations[descriptor] = generation
            opened_descriptors.add((descriptor, generation))
            if requested_binary:
                binary_descriptors.add((descriptor, generation))
            return descriptor

        def windows_read(descriptor: int, size: int) -> bytes:
            data = real_read(descriptor, size)
            token = (descriptor, descriptor_generations[descriptor])
            return data if token in binary_descriptors else data.replace(b"\r\n", b"\n")

        def windows_dup(descriptor: int) -> int:
            duplicate = real_dup(descriptor)
            generation = descriptor_generations.get(duplicate, 0) + 1
            descriptor_generations[duplicate] = generation
            opened_descriptors.add((duplicate, generation))
            return duplicate

        selection = [
            ManifestSelection("file", "path", "src/windows-lines.txt"),
            ManifestSelection("file", "path", "src/unsafe.py"),
            ManifestSelection("file", "path", "src/large.py"),
        ]
        with (
            patch.object(manifest_module, "_NOFOLLOW_FLAG", 0),
            patch.object(binary_io, "_BINARY_FLAG", binary_flag),
            patch.object(binary_io, "_msvcrt", FakeMsvcrt()),
            patch.object(os, "open", side_effect=windows_open),
            patch.object(os, "dup", side_effect=windows_dup),
            patch.object(os, "read", side_effect=windows_read),
        ):
            first = self.build(selections=selection, budget_bytes=256)
            second = self.build(selections=selection, budget_bytes=256)

        self.assertTrue(opened_descriptors)
        self.assertEqual(opened_descriptors, binary_descriptors)
        self.assertEqual(first, second)
        item = first["items"][0]  # type: ignore[index]
        self.assertEqual(item["byte_cost"], len(source))
        self.assertEqual(item["hash"], "sha256:" + hashlib.sha256(source).hexdigest())
        self.assertEqual(
            [row["reason"] for row in first["excluded_items"]],  # type: ignore[index]
            ["over_budget", "unsafe_content"],
        )
        pin = input_manifest_pin(first)
        self.assertTrue(input_manifest_pin_matches(pin, first))
        changed = deepcopy(first)
        changed["items"][0]["hash"] = "sha256:" + "0" * 64  # type: ignore[index]
        self.assertFalse(input_manifest_pin_matches(pin, changed))

    def test_fallback_preserves_multiple_selection_safety_and_budget_contracts(self) -> None:
        self.workspace.write("src/unsafe.py", 'api_key = "value"\n')
        self.workspace.write("src/large.py", "x" * 80)
        selections = [
            ManifestSelection("file", "path", "src/client.py"),
            ManifestSelection("file", "path", "src/helper.py"),
            ManifestSelection("file", "path", "src/unsafe.py"),
            ManifestSelection("file", "path", "src/large.py"),
        ]

        with patch.object(manifest_module, "_NOFOLLOW_FLAG", 0):
            first = self.build(selections=selections, budget_bytes=64)
            second = self.build(selections=selections, budget_bytes=64)

        self.assertEqual(first, second)
        self.assertEqual(
            [item["item_id"] for item in first["items"]],  # type: ignore[index]
            ["file:src/client.py", "file:src/helper.py"],
        )
        self.assertEqual(
            [row["reason"] for row in first["excluded_items"]],  # type: ignore[index]
            ["over_budget", "unsafe_content"],
        )
        self.assertEqual(validate_handoff_input_manifest(first), [])
        pin = input_manifest_pin(first)
        self.assertEqual(validate_handoff_input_manifest_pin(pin), [])
        self.assertTrue(input_manifest_pin_matches(pin, pinned_input_manifest(first)))

    @requires_symlinks
    def test_symlinked_workspace_root_is_refused_without_including_target_bytes(self) -> None:
        real_root = self.workspace.root / "real-workspace"
        real_root.mkdir()
        secret = "def from_symlinked_root():\n    return True\n"
        atomic_write_text(real_root / "secret.py", secret)
        linked_root = self.workspace.root / "linked-workspace"
        linked_root.symlink_to(real_root, target_is_directory=True)
        selection = [ManifestSelection("file", "path", "secret.py")]

        first = self.build(workspace_root=linked_root, selections=selection)
        with patch.object(manifest_module, "_NOFOLLOW_FLAG", 0):
            second = self.build(workspace_root=linked_root, selections=selection)

        self.assertEqual(first, second)
        self.assertEqual(first["items"], [])
        self.assertEqual(first["budget"]["used_bytes"], 0)  # type: ignore[index]
        row = self.only_exclusion(first, "unreadable_source")
        self.assertEqual(row["item_id"], "file:secret.py")
        self.assertEqual(row["byte_cost"], 0)
        self.assertIn("workspace root", str(row["detail"]).lower())
        self.assertIn("symlink", str(row["detail"]).lower())
        self.assertNotIn(hashlib.sha256(secret.encode("utf-8")).hexdigest(), str(first))

    @requires_symlinks
    def test_final_symlink_is_refused_without_including_target_bytes(self) -> None:
        target = self.workspace.write("src/symlink-target.py", "def target():\n    return True\n")
        (self.workspace.root / "src/linked.py").symlink_to(target)

        selection = [ManifestSelection("file", "path", "src/linked.py")]
        first = self.build(selections=selection)
        with patch.object(manifest_module, "_NOFOLLOW_FLAG", 0):
            second = self.build(selections=selection)

        self.assertEqual(first, second)
        self.assertEqual(first["items"], [])
        row = self.only_exclusion(first, "unreadable_source")
        self.assertEqual(row["item_id"], "file:src/linked.py")
        self.assertIn("symlink", str(row["detail"]).lower())

    @requires_symlinks
    def test_intermediate_symlink_is_refused_without_including_target_bytes(self) -> None:
        real = self.workspace.root / "real-source"
        real.mkdir()
        self.workspace.write("real-source/nested.py", "def nested():\n    return True\n")
        (self.workspace.root / "linked-source").symlink_to(real, target_is_directory=True)

        selection = [ManifestSelection("file", "path", "linked-source/nested.py")]
        first = self.build(selections=selection)
        with patch.object(manifest_module, "_NOFOLLOW_FLAG", 0):
            manifest = self.build(selections=selection)

        self.assertEqual(first, manifest)
        self.assertEqual(manifest["items"], [])
        row = self.only_exclusion(manifest, "unreadable_source")
        self.assertEqual(row["item_id"], "file:linked-source/nested.py")
        self.assertIn("symlink", str(row["detail"]).lower())

    def test_fallback_replacement_between_validation_and_open_is_refused(self) -> None:
        candidate = self.workspace.write("src/race.py", "def original():\n    return True\n")
        original = self.workspace.root / "src/original-race.py"
        replacement = b"def replacement():\n    return False\n"
        real_lstat = Path.lstat
        swapped = False

        def swap_after_lstat(path: Path) -> os.stat_result:
            nonlocal swapped
            result = real_lstat(path)
            if not swapped and path == candidate:
                swapped = True
                os.replace(candidate, original)
                candidate.write_bytes(replacement)
            return result

        with (
            patch.object(manifest_module, "_NOFOLLOW_FLAG", 0),
            patch.object(Path, "lstat", autospec=True, side_effect=swap_after_lstat),
        ):
            manifest = self.build(
                selections=[ManifestSelection("file", "path", "src/race.py")]
            )

        self.assertTrue(swapped, "the deterministic race seam must replace the validated name")
        self.assertEqual(manifest["items"], [])
        row = self.only_exclusion(manifest, "unreadable_source")
        self.assertEqual(row["item_id"], "file:src/race.py")
        self.assertIn("changed", str(row["detail"]).lower())
        self.assertNotIn(hashlib.sha256(replacement).hexdigest(), str(manifest))

    def test_descriptor_nofollow_replacement_between_validation_and_open_is_refused(self) -> None:
        if not manifest_module._descriptor_relative_reads_supported():
            self.skipTest("descriptor-relative no-follow is unavailable")
        candidate = self.workspace.write("src/descriptor-race.py", "def original():\n    return True\n")
        original = self.workspace.root / "src/original-descriptor-race.py"
        replacement = b"def replacement():\n    return False\n"
        real_stat_at = manifest_module._stat_at
        swapped = False

        def swap_after_stat(name: str, directory_fd: int) -> os.stat_result:
            nonlocal swapped
            result = real_stat_at(name, directory_fd)
            if not swapped and name == "descriptor-race.py":
                swapped = True
                os.replace(candidate, original)
                candidate.write_bytes(replacement)
            return result

        with patch.object(manifest_module, "_stat_at", side_effect=swap_after_stat):
            manifest = self.build(
                selections=[ManifestSelection("file", "path", "src/descriptor-race.py")]
            )

        self.assertTrue(swapped)
        self.assertEqual(manifest["items"], [])
        row = self.only_exclusion(manifest, "unreadable_source")
        self.assertIn("changed", str(row["detail"]).lower())
        self.assertNotIn(hashlib.sha256(replacement).hexdigest(), str(manifest))

    def test_fallback_refuses_workspace_root_replacement_during_selection(self) -> None:
        selected_root = self.workspace.root / "selected-root"
        selected_root.mkdir()
        candidate = selected_root / "source.py"
        atomic_write_text(candidate, "def original():\n    return True\n")
        moved_root = self.workspace.root / "moved-root"
        replacement = b"def replacement():\n    return False\n"
        real_lstat = Path.lstat
        root_stats = 0

        def replace_after_root_validation(path: Path) -> os.stat_result:
            nonlocal root_stats
            result = real_lstat(path)
            if path == selected_root:
                root_stats += 1
                if root_stats == 2:
                    selected_root.rename(moved_root)
                    selected_root.mkdir()
                    (selected_root / "source.py").write_bytes(replacement)
            return result

        with (
            patch.object(manifest_module, "_NOFOLLOW_FLAG", 0),
            patch.object(Path, "lstat", autospec=True, side_effect=replace_after_root_validation),
        ):
            manifest = self.build(
                workspace_root=selected_root,
                selections=[ManifestSelection("file", "path", "source.py")],
            )

        self.assertEqual(manifest["items"], [])
        row = self.only_exclusion(manifest, "unreadable_source")
        self.assertIn("workspace root changed", str(row["detail"]).lower())
        self.assertNotIn(hashlib.sha256(replacement).hexdigest(), str(manifest))

    def test_fallback_refuses_intermediate_replacement_during_selection(self) -> None:
        directory = self.workspace.root / "race-directory"
        directory.mkdir()
        candidate = directory / "source.py"
        atomic_write_text(candidate, "def original():\n    return True\n")
        moved_directory = self.workspace.root / "moved-directory"
        replacement = b"def replacement():\n    return False\n"
        real_lstat = Path.lstat
        swapped = False

        def replace_after_component_validation(path: Path) -> os.stat_result:
            nonlocal swapped
            result = real_lstat(path)
            if not swapped and path == directory:
                swapped = True
                directory.rename(moved_directory)
                directory.mkdir()
                (directory / "source.py").write_bytes(replacement)
            return result

        with (
            patch.object(manifest_module, "_NOFOLLOW_FLAG", 0),
            patch.object(Path, "lstat", autospec=True, side_effect=replace_after_component_validation),
        ):
            manifest = self.build(
                selections=[ManifestSelection("file", "path", "race-directory/source.py")]
            )

        self.assertTrue(swapped)
        self.assertEqual(manifest["items"], [])
        row = self.only_exclusion(manifest, "unreadable_source")
        self.assertIn("changed", str(row["detail"]).lower())
        self.assertNotIn(hashlib.sha256(replacement).hexdigest(), str(manifest))


class ProjectTermsExplicitSelectionTests(_WorkspaceCase):
    def test_project_terms_source_uses_only_the_explicit_bounded_file_path(self) -> None:
        source = "# Project Terms\n\n- dispatch packet means handoff\n"
        self.workspace.write("PROJECT_TERMS.md", source)

        first = self.build(
            selections=[ManifestSelection("file", "path", "PROJECT_TERMS.md")]
        )
        second = self.build(
            selections=[ManifestSelection("file", "path", "PROJECT_TERMS.md")]
        )

        self.assertEqual(first, second)
        self.assertEqual(first["excluded_items"], [])
        self.assertEqual(len(first["items"]), 1)
        item = first["items"][0]
        self.assertEqual(item["item_kind"], "file")
        self.assertEqual(item["selector"], {"kind": "path", "expression": "PROJECT_TERMS.md"})
        self.assertEqual(item["provenance"]["local_ref"], "PROJECT_TERMS.md")
        self.assertEqual(item["byte_cost"], len(source.encode("utf-8")))
        self.assertEqual(item["inclusion_reason"], "explicit_selection")
        self.assertEqual(item["safety_result"]["status"], "safe")
        self.assertEqual(item["hash"], "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest())
        self.assertNotIn("content", item, "the manifest records exact-byte identity, not a second source copy")

    def test_project_terms_source_is_refused_whole_when_over_budget_or_unsafe(self) -> None:
        cases = (
            ("over_budget", "x" * 257, 256),
            ("unsafe_content", 'api_key = "value"\n', 256),
        )
        for reason, source, budget in cases:
            with self.subTest(reason=reason):
                self.workspace.write("PROJECT_TERMS.md", source)
                manifest = self.build(
                    selections=[ManifestSelection("file", "path", "PROJECT_TERMS.md")],
                    budget_bytes=budget,
                )
                self.assertEqual(manifest["items"], [])
                exclusion = self.only_exclusion(manifest, reason)
                self.assertEqual(exclusion["item_id"], "file:PROJECT_TERMS.md")
                self.assertEqual(exclusion["byte_cost"], len(source.encode("utf-8")))
                self.assertNotIn("truncated", str(exclusion).lower())


class PackSupersetTests(_WorkspaceCase):
    def test_manifest_references_the_pack_rather_than_competing_with_it(self) -> None:
        pack = _context_pack()
        manifest = self.build(context_pack=pack)
        self.assertEqual(
            manifest["derived_from"],
            {
                "schema_version": "handoff_context_pack/v1",
                "executor_target": "codex",
                "session_id": "session-1",
                "included_context_count": 1,
                "excluded_context_count": 0,
                "blocked_by_conflicts_count": 0,
            },
        )
        item = manifest["items"][0]  # type: ignore[index]
        self.assertEqual(item["item_kind"], "reviewed_memory")
        self.assertEqual(item["inclusion_reason"], "reviewed_memory_projection")
        # Provenance is carried from the pack, not re-derived, so the two
        # surfaces can never grade the same record differently.
        self.assertEqual(item["provenance"]["source"], "omh_memory")
        self.assertEqual(item["provenance"]["truth_level"], "approved_context")
        self.assertEqual(item["selector"], {"kind": "memory_record_id", "expression": "memory-1"})

    def test_the_pack_still_validates_unchanged_beside_the_manifest(self) -> None:
        from omh.memory import validate_handoff_context_pack

        payload = build_coding_delegation_payload(
            "implement the retry fix in src/client.py and add tests",
            executor_target="codex",
            context_pack=_context_pack(),
            force_coding_handoff=True,
        )
        handoff = payload["executor_handoff"]
        self.assertEqual(
            validate_handoff_context_pack(handoff["context_pack"], require_conflict_free=True),  # type: ignore[index]
            [],
        )
        self.assertEqual(validate_handoff_input_manifest(handoff["input_manifest"]), [])  # type: ignore[index]

    def test_an_accepted_plan_pack_survives_a_machine_specific_artifact_path(self) -> None:
        """A pack's absolute `artifact_ref` must not reach the manifest.

        `build_plan_handoff_context_pack` always sets one. Carrying it here
        would put the operator's home directory into the digest — so two
        checkouts of the same package would disagree — and a home directory
        named like an email address is exactly what the ref screen refuses, so
        the plan would drop out as unsafe on one machine and travel on the next.
        """
        from omh.workflows.hermes_planning import build_plan_handoff_context_pack

        for home in ("/Users/someone@example.com/work/repo", "/home/plain/work/repo"):
            pack = build_plan_handoff_context_pack(
                {
                    "path": f"{home}/.omh/plans/plan.md",
                    "status": "accepted",
                    "schema_version": "hermes_plan/v1",
                    "sha256": "a" * 64,
                    "text": "plan body",
                }
            )
            manifest = self.build(context_pack=pack)
            self.assertEqual(manifest["excluded_items"], [], home)
            item = manifest["items"][0]  # type: ignore[index]
            self.assertEqual(item["provenance"]["local_ref"], "plan_artifact")
            self.assertNotIn(home, str(manifest))

    def test_no_pack_and_no_selection_means_no_invented_manifest(self) -> None:
        payload = build_coding_delegation_payload(
            "implement the retry fix in src/client.py and add tests",
            executor_target="codex",
            force_coding_handoff=True,
        )
        self.assertNotIn("input_manifest", payload["executor_handoff"])  # type: ignore[operator]


class DelegationRecordTests(_WorkspaceCase):
    def test_the_recorded_delegation_keeps_the_manifest_and_validates(self) -> None:
        from omh.coding_delegation import coding_delegation_record_payload

        message = "implement the retry fix in src/client.py and add tests"
        payload = build_coding_delegation_payload(
            message,
            executor_target="codex",
            context_pack=_context_pack(),
            force_coding_handoff=True,
        )
        record = coding_delegation_record_payload(payload, message)
        record["updated_at"] = "2026-07-15T00:00:00Z"
        self.assertEqual(validate_coding_delegation_record(record), [])
        recorded = record["executor_handoff"]["input_manifest"]  # type: ignore[index]
        self.assertEqual(recorded, payload["executor_handoff"]["input_manifest"])  # type: ignore[index]
        # Recorded whole, so its digest still describes what it carries.
        self.assertEqual(recorded["digest"], input_manifest_digest(recorded))

    def test_an_invalid_manifest_is_refused_at_attach_time(self) -> None:
        manifest = self.build(selections=[ManifestSelection("file", "path", "src/client.py")])
        broken = deepcopy(manifest)
        broken["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(ValueError):
            build_coding_delegation_payload(
                "implement the retry fix in src/client.py and add tests",
                executor_target="codex",
                input_manifest=broken,
                force_coding_handoff=True,
            )


class SelectorContractTests(_WorkspaceCase):
    def test_a_selector_kind_that_cannot_replay_its_item_kind_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.build(selections=[ManifestSelection("file", "revision_range", "HEAD~1..HEAD")])

    def test_an_unknown_item_kind_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.build(selections=[ManifestSelection("screenshot", "path", "a.png")])

    def test_a_workspace_backed_kind_without_a_root_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build_handoff_input_manifest(selections=[ManifestSelection("file", "path", "src/client.py")])

    def test_a_plan_heading_selector_takes_only_its_own_section(self) -> None:
        manifest = self.build(
            selections=[ManifestSelection("plan_section", "plan_heading", "docs/plan.md### Verification")]
        )
        item = manifest["items"][0]  # type: ignore[index]
        self.assertEqual(item["provenance"]["local_ref"], "docs/plan.md### Verification")
        self.assertEqual(item["byte_cost"], len("## Verification\nRun the targeted tests.\n".encode("utf-8")))

    def test_a_windows_rooted_selector_is_refused_as_outside_workspace(self) -> None:
        # A single leading backslash is root-anchored on Windows. Both spellings
        # are asserted as literals; neither is composed through os.sep.
        for expression in ("\\src\\client.py", "/src/client.py"):
            manifest = self.build(selections=[ManifestSelection("file", "path", expression)])
            self.assertEqual(manifest["items"], [], expression)
            self.assertEqual(self.only_exclusion(manifest, "outside_workspace")["item_kind"], "file")


if __name__ == "__main__":
    unittest.main()
