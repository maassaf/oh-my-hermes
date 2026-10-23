from __future__ import annotations

import errno
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from _local_package import load_local_package
from _platform_support import requires_secure_dir_io

load_local_package()
from omh.system.local_store import FileLockTimeout
from omh.workflows import domain_intelligence_bound_store as bound_store
from omh.workflows import domain_intelligence_profile_snapshot as profile_snapshot


def _attempt_shared_fifo_lock(domain_root: str, result: object) -> None:
    descriptor = os.open(domain_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            with bound_store.shared_domain_store_lock_at(
                descriptor,
                timeout_seconds=0.02,
                poll_interval=0.001,
            ):
                pass
        except Exception as exc:
            result.send((type(exc).__name__, str(exc)))
        else:
            result.send(("acquired", ""))
    finally:
        os.close(descriptor)
        result.close()


class _EndlessEntries:
    def __init__(self, suffix: str) -> None:
        self.suffix = suffix
        self.count = 0

    def __enter__(self) -> _EndlessEntries:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def __iter__(self) -> _EndlessEntries:
        return self

    def __next__(self) -> SimpleNamespace:
        self.count += 1
        return SimpleNamespace(name=f"entry-{self.count:08d}{self.suffix}")


@unittest.skipUnless(
    hasattr(os, "mkfifo") and bound_store.fcntl is not None,
    "requires POSIX FIFOs and flock",
)
class BoundStoreLockSecurityTests(unittest.TestCase):
    def test_fifo_lock_is_rejected_without_bypassing_the_deadline(self) -> None:
        with TemporaryDirectory() as tmp:
            domain_root = Path(tmp)
            os.mkfifo(domain_root / ".store.lock", mode=0o600)
            context = multiprocessing.get_context("fork")
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(
                target=_attempt_shared_fifo_lock,
                args=(str(domain_root), sender),
            )
            process.start()
            sender.close()
            process.join(timeout=0.75)
            blocked = process.is_alive()
            if blocked:
                process.terminate()
                process.join(timeout=0.75)

            self.assertFalse(
                blocked,
                "opening a FIFO .store.lock blocked before the lock deadline",
            )
            self.assertTrue(receiver.poll(), "lock attempt returned no result")
            exception_name, message = receiver.recv()
            self.assertEqual(exception_name, "ValueError")
            self.assertIn("regular file", message)
            receiver.close()

    def test_lock_open_does_not_follow_a_symlink_to_a_device(self) -> None:
        with TemporaryDirectory() as tmp:
            domain_root = Path(tmp)
            (domain_root / ".store.lock").symlink_to("/dev/null")
            descriptor = os.open(domain_root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaisesRegex(ValueError, "must not be a symlink"):
                    with bound_store.shared_domain_store_lock_at(descriptor):
                        pass
            finally:
                os.close(descriptor)

    def test_lock_timeout_uses_the_requested_poll_interval_and_hard_cap(self) -> None:
        with TemporaryDirectory() as tmp:
            domain_root = Path(tmp)
            (domain_root / ".store.lock").write_text("", encoding="utf-8")
            descriptor = os.open(domain_root, os.O_RDONLY | os.O_DIRECTORY)
            lock_busy = BlockingIOError(errno.EAGAIN, "busy")
            try:
                with (
                    patch.object(
                        bound_store.fcntl,
                        "flock",
                        side_effect=[lock_busy, lock_busy],
                    ) as flock,
                    patch.object(
                        bound_store.time,
                        "monotonic",
                        side_effect=[10.0, 10.0, 10.25],
                    ),
                    patch.object(bound_store.time, "sleep") as sleep,
                ):
                    with self.assertRaisesRegex(FileLockTimeout, "within 1.0s"):
                        with bound_store.shared_domain_store_lock_at(
                            descriptor,
                            timeout_seconds=1.0,
                            poll_interval=0.037,
                        ):
                            pass
                self.assertEqual(flock.call_count, 2)
                sleep.assert_called_once_with(0.037)
            finally:
                os.close(descriptor)


@requires_secure_dir_io
class DomainProfileSnapshotEnumerationTests(unittest.TestCase):
    def test_json_enumeration_stops_immediately_after_the_artifact_limit(self) -> None:
        entries = _EndlessEntries(".json")
        with TemporaryDirectory() as tmp:
            descriptor = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with (
                    patch.object(
                        profile_snapshot.os,
                        "listdir",
                        side_effect=AssertionError("unbounded listdir used"),
                    ),
                    patch.object(
                        profile_snapshot.os,
                        "scandir",
                        return_value=entries,
                    ),
                ):
                    with self.assertRaisesRegex(
                        ValueError, "artifact_file_count_exceeded"
                    ):
                        profile_snapshot._snapshot_directory(descriptor)
            finally:
                os.close(descriptor)

        self.assertEqual(
            entries.count,
            profile_snapshot.MAX_DOMAIN_ARTIFACT_FILES + 1,
        )

    def test_non_json_enumeration_has_a_total_entry_bound(self) -> None:
        entries = _EndlessEntries(".tmp")
        with TemporaryDirectory() as tmp:
            descriptor = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with patch.object(
                    profile_snapshot.os,
                    "scandir",
                    return_value=entries,
                ):
                    with self.assertRaisesRegex(
                        ValueError, "artifact_file_count_exceeded"
                    ):
                        profile_snapshot._snapshot_directory(descriptor)
            finally:
                os.close(descriptor)

        self.assertLessEqual(
            entries.count,
            profile_snapshot.MAX_DOMAIN_ARTIFACT_FILES * 2 + 2,
        )


if __name__ == "__main__":
    unittest.main()
