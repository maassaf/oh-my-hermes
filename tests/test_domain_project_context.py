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
    repo as _repo,
)
from domain_project_context_lock_mixin import DomainProjectContextLockMixin


class DomainProjectContextTests(DomainProjectContextLockMixin, unittest.TestCase):
    def _module(self):
        return importlib.import_module("omh.workflows.domain_project_context")

    def _close(self, binding) -> None:
        if binding is not None:
            binding.close()

    @requires_domain_intelligence_store
    def test_host_binding_is_surface_specific(self) -> None:
        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            cli_root = _repo(base / "cli-project")
            plugin_root = _repo(base / "plugin-project")
            _domain_store(cli_root)
            _domain_store(plugin_root)
            nested = cli_root / "src" / "nested"
            nested.mkdir(parents=True)

            cli_binding = domain_context.bind_cli_project(nested)
            self.addCleanup(self._close, cli_binding)
            self.assertIsNotNone(cli_binding)
            self.assertEqual(cli_binding.surface, "cli")
            self.assertEqual(cli_binding.project_root, cli_root)

            self.assertIsNone(domain_context.bind_plugin_project({}))
            with patch.dict(
                os.environ,
                {"OMH_HOME": str(cli_root / ".omh"), "PROJECT_ROOT": str(cli_root)},
            ), patch("pathlib.Path.cwd", return_value=cli_root):
                self.assertIsNone(
                    domain_context.bind_plugin_project(
                        {
                            "args": {"project_root": str(cli_root)},
                            "metadata": {"project_ref": cli_root.name},
                            "omh_home": str(cli_root / ".omh"),
                        }
                    )
                )

            plugin_binding = domain_context.bind_plugin_project(
                {"project_root": str(plugin_root)}
            )
            self.addCleanup(self._close, plugin_binding)
            self.assertIsNotNone(plugin_binding)
            self.assertEqual(plugin_binding.surface, "plugin")
            self.assertEqual(plugin_binding.project_root, plugin_root)

            session_binding = domain_context.bind_session_project(plugin_binding)
            self.addCleanup(self._close, session_binding)
            self.assertIsNotNone(session_binding)
            self.assertEqual(session_binding.surface, "session")
            self.assertEqual(session_binding.project_root, plugin_root)
            self.assertNotEqual(
                session_binding.domain_store_fd, plugin_binding.domain_store_fd
            )
            self.assertIsNone(domain_context.bind_session_project(None))
            self.assertIsNone(domain_context.bind_session_project(session_binding))

    @requires_domain_intelligence_store
    def test_cli_binding_canonicalizes_nested_and_symlinked_cwd(self) -> None:
        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = _repo(base / "canonical-project")
            _domain_store(root)
            nested = root / "src" / "deep"
            nested.mkdir(parents=True)
            cwd_link = base / "cwd-link"
            cwd_link.symlink_to(nested, target_is_directory=True)

            binding = domain_context.bind_cli_project(cwd_link)
            self.addCleanup(self._close, binding)
            self.assertIsNotNone(binding)
            self.assertEqual(binding.project_root, root)
            self.assertEqual(binding.project_paths.omh_home, root / ".omh")
            self.assertFalse(binding.project_paths.omh_home_named)

    @requires_domain_intelligence_store
    def test_linked_worktree_root_is_supported(self) -> None:
        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp).resolve() / "linked", linked_worktree=True)
            _domain_store(root)

            binding = domain_context.bind_cli_project(root)
            self.addCleanup(self._close, binding)
            self.assertIsNotNone(binding)
            self.assertEqual(binding.project_root, root)

    def test_plugin_root_must_be_the_canonical_repository_root(self) -> None:
        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = _repo(base / "strict-project")
            _domain_store(root)
            nested = root / "nested"
            nested.mkdir()
            root_link = base / "root-link"
            root_link.symlink_to(root, target_is_directory=True)

            self.assertIsNone(
                domain_context.bind_plugin_project({"project_root": str(nested)})
            )
            self.assertIsNone(
                domain_context.bind_plugin_project({"project_root": str(root_link)})
            )
            self.assertIsNone(
                domain_context.bind_plugin_project({"project_root": str(base / "missing")})
            )

if __name__ == "__main__":
    unittest.main()
