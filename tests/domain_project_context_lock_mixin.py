from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

from _platform_support import requires_domain_intelligence_store

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows; tests below are skipped there
    fcntl = None  # type: ignore[assignment]


class DomainProjectContextLockMixin:
    @requires_domain_intelligence_store
    def test_shared_lock_is_descriptor_relative_nonblocking_and_fail_closed(
        self,
    ) -> None:
        from domain_project_context_helpers import (
            domain_store as _domain_store,
            repo as _repo,
        )

        domain_context = self._module()
        bound_store = importlib.import_module(
            "omh.workflows.domain_intelligence_bound_store"
        )
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp).resolve() / "lock-project")
            store = _domain_store(root)
            binding = domain_context.bind_cli_project(root)
            self.addCleanup(self._close, binding)
            self.assertIsNotNone(binding)

            signature = inspect.signature(bound_store.shared_domain_store_lock_at)
            self.assertEqual(signature.parameters["timeout_seconds"].default, 0.25)
            self.assertEqual(signature.parameters["poll_interval"].default, 0.01)

            with binding.shared_store_lock() as state:
                self.assertEqual(state, {"locked": True, "mode": "shared"})
                contender = os.open(store / ".store.lock", os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(contender)

            with patch.object(bound_store, "fcntl", None):
                with self.assertRaisesRegex(ValueError, "shared_lock_unavailable"):
                    with binding.shared_store_lock():
                        pass

            (store / ".store.lock").unlink()
            with self.assertRaises(FileNotFoundError):
                with binding.shared_store_lock():
                    pass

    @requires_domain_intelligence_store
    def test_shared_lock_times_out_without_creating_or_reopening_lock(self) -> None:
        from domain_project_context_helpers import (
            domain_store as _domain_store,
            repo as _repo,
        )

        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp).resolve() / "busy-lock-project")
            store = _domain_store(root)
            binding = domain_context.bind_cli_project(root)
            self.addCleanup(self._close, binding)
            contender = os.open(store / ".store.lock", os.O_RDWR)
            fcntl.flock(contender, fcntl.LOCK_EX)
            try:
                with self.assertRaisesRegex(Exception, "within 0.03s"):
                    with binding.shared_store_lock(
                        timeout_seconds=0.03, poll_interval=0.01
                    ):
                        pass
            finally:
                fcntl.flock(contender, fcntl.LOCK_UN)
                os.close(contender)

    @requires_domain_intelligence_store
    def test_shared_lock_honors_configured_poll_interval(self) -> None:
        from domain_project_context_helpers import (
            domain_store as _domain_store,
            repo as _repo,
        )

        domain_context = self._module()
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp).resolve() / "polling-lock-project")
            store = _domain_store(root)
            binding = domain_context.bind_cli_project(root)
            self.addCleanup(self._close, binding)
            contender = os.open(store / ".store.lock", os.O_RDWR)
            fcntl.flock(contender, fcntl.LOCK_EX)
            try:
                with patch("time.sleep", wraps=time.sleep) as sleep:
                    with self.assertRaisesRegex(Exception, "within 0.03s"):
                        with binding.shared_store_lock(
                            timeout_seconds=0.03,
                            poll_interval=0.02,
                        ):
                            pass
                self.assertGreaterEqual(sleep.call_count, 1)
                self.assertEqual(
                    {call.args for call in sleep.call_args_list}, {(0.02,)}
                )
            finally:
                fcntl.flock(contender, fcntl.LOCK_UN)
                os.close(contender)
