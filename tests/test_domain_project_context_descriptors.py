from __future__ import annotations

import importlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _local_package import load_local_package
from _platform_support import requires_domain_intelligence_store

load_local_package()

from domain_project_context_helpers import (
    domain_store as _domain_store,
    read_marker as _read_marker,
    repo as _repo,
)


class DomainProjectContextDescriptorTests(unittest.TestCase):
    def _module(self):
        return importlib.import_module("omh.workflows.domain_project_context")

    def _close(self, binding) -> None:
        if binding is not None:
            binding.close()

    @requires_domain_intelligence_store
    def test_same_named_repositories_keep_distinct_bound_stores(self) -> None:
        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            first = _repo(base / "one" / "same-name")
            second = _repo(base / "two" / "same-name")
            _domain_store(first, marker="first")
            _domain_store(second, marker="second")

            first_binding = domain_context.bind_cli_project(first)
            second_binding = domain_context.bind_cli_project(second)
            self.addCleanup(self._close, first_binding)
            self.addCleanup(self._close, second_binding)

            with first_binding.open_directory("profiles") as directory_fd:
                first_value = _read_marker(directory_fd)
            with second_binding.open_directory("profiles") as directory_fd:
                second_value = _read_marker(directory_fd)
            self.assertEqual(first_value, {"marker": "first"})
            self.assertEqual(second_value, {"marker": "second"})

    @requires_domain_intelligence_store
    def test_bound_descriptor_survives_path_swap(self) -> None:
        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp).resolve() / "swap-project")
            original_store = _domain_store(root, marker="opened-before-swap")
            original_identity = (original_store.stat().st_dev, original_store.stat().st_ino)
            binding = domain_context.bind_cli_project(root)
            self.addCleanup(self._close, binding)
            self.assertIsNotNone(binding)

            opened_home = root / ".omh-opened"
            (root / ".omh").rename(opened_home)
            _domain_store(root, marker="replacement-path")

            with binding.shared_store_lock():
                with binding.open_directory("profiles") as directory_fd:
                    value = _read_marker(directory_fd)

            self.assertEqual(value, {"marker": "opened-before-swap"})
            opened_store = opened_home / "memory" / "domain-intelligence"
            self.assertEqual(
                (opened_store.stat().st_dev, opened_store.stat().st_ino),
                original_identity,
            )

    @requires_domain_intelligence_store
    def test_session_binding_duplicates_trusted_descriptor_after_path_swap(self) -> None:
        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp).resolve() / "session-swap-project")
            original_store = _domain_store(root, marker="host-bound")
            original_identity = (
                original_store.stat().st_dev,
                original_store.stat().st_ino,
            )
            host_binding = domain_context.bind_cli_project(root)
            self.addCleanup(self._close, host_binding)
            self.assertIsNotNone(host_binding)

            (root / ".omh").rename(root / ".omh-host-bound")
            replacement_store = _domain_store(root, marker="replacement-path")
            replacement_identity = (
                replacement_store.stat().st_dev,
                replacement_store.stat().st_ino,
            )

            with patch.object(
                domain_context,
                "open_domain_directory",
                side_effect=AssertionError("session binding reopened the store path"),
            ):
                session_binding = domain_context.bind_session_project(host_binding)
                next_session_binding = domain_context.bind_session_project(host_binding)
            self.addCleanup(self._close, session_binding)
            self.addCleanup(self._close, next_session_binding)
            self.assertIsNotNone(session_binding)
            self.assertIsNotNone(next_session_binding)
            self.assertEqual(session_binding.project_root, host_binding.project_root)
            self.assertEqual(session_binding.project_paths, host_binding.project_paths)
            self.assertEqual(next_session_binding.project_paths, host_binding.project_paths)
            self.assertEqual(
                len(
                    {
                        host_binding.domain_store_fd,
                        session_binding.domain_store_fd,
                        next_session_binding.domain_store_fd,
                    }
                ),
                3,
            )
            self.assertFalse(os.get_inheritable(session_binding.domain_store_fd))
            self.assertFalse(os.get_inheritable(next_session_binding.domain_store_fd))
            session_stat = os.fstat(session_binding.domain_store_fd)
            self.assertEqual(
                (session_stat.st_dev, session_stat.st_ino),
                original_identity,
            )
            self.assertNotEqual(original_identity, replacement_identity)
            host_binding.close()
            with session_binding.open_directory("profiles") as directory_fd:
                self.assertEqual(_read_marker(directory_fd), {"marker": "host-bound"})
            session_binding.close()
            with next_session_binding.open_directory("profiles") as directory_fd:
                self.assertEqual(_read_marker(directory_fd), {"marker": "host-bound"})

    @requires_domain_intelligence_store
    def test_session_binding_rejects_closed_or_changed_host_descriptor(self) -> None:
        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = _repo(base / "trusted-project")
            other_root = _repo(base / "other-project")
            _domain_store(root)
            other_store = _domain_store(other_root)

            closed_binding = domain_context.bind_cli_project(root)
            self.assertIsNotNone(closed_binding)
            closed_binding.close()
            self.assertIsNone(domain_context.bind_session_project(closed_binding))

            changed_binding = domain_context.bind_cli_project(root)
            self.addCleanup(self._close, changed_binding)
            self.assertIsNotNone(changed_binding)
            os.close(changed_binding.domain_store_fd)
            changed_binding.domain_store_fd = os.open(
                other_store,
                os.O_RDONLY | os.O_DIRECTORY,
            )
            self.assertIsNone(domain_context.bind_session_project(changed_binding))

    @requires_domain_intelligence_store
    def test_session_constructor_failure_closes_duplicated_descriptor(self) -> None:
        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp).resolve() / "session-constructor-failure")
            _domain_store(root)
            host_binding = domain_context.bind_cli_project(root)
            self.addCleanup(self._close, host_binding)
            self.assertIsNotNone(host_binding)
            duplicate = os.dup(host_binding.domain_store_fd)

            with (
                patch.object(domain_context.os, "dup", return_value=duplicate),
                patch.object(
                    domain_context.HostProjectBinding,
                    "__init__",
                    side_effect=RuntimeError("construction failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "construction failed"),
            ):
                domain_context.bind_session_project(host_binding)

            with self.assertRaises(OSError):
                os.fstat(duplicate)

    def test_symlinked_store_components_fail_closed(self) -> None:
        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = _repo(base / "symlink-project")
            outside = base / "outside"
            (outside / "memory" / "domain-intelligence").mkdir(parents=True)
            (root / ".omh").symlink_to(outside, target_is_directory=True)

            self.assertIsNone(domain_context.bind_cli_project(root))

    @requires_domain_intelligence_store
    def test_binding_constructor_failure_closes_owned_descriptor(self) -> None:
        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp).resolve() / "constructor-failure")
            store = _domain_store(root)
            descriptor = os.open(store, os.O_RDONLY | os.O_DIRECTORY)
            with (
                patch.object(
                    domain_context, "open_domain_directory", return_value=descriptor
                ),
                patch.object(
                    domain_context,
                    "HostProjectBinding",
                    side_effect=RuntimeError("construction failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "construction failed"),
            ):
                domain_context.bind_cli_project(root)

            with self.assertRaises(OSError):
                os.fstat(descriptor)


if __name__ == "__main__":
    unittest.main()
