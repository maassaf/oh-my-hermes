from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from _local_package import load_local_package
from _platform_support import requires_domain_intelligence_store
from domain_store_snapshot_support import (
    domain_store_snapshot,
    repository_tree_snapshot,
)

load_local_package()
from omh.paths import resolve_paths
from omh.system.local_store import file_lock
from omh.workflows import domain_intelligence_store as store
from omh.workflows import domain_intelligence_store_security as security
from omh.workflows import domain_intelligence_store_writer as store_writer


class FakeMsvcrt:
    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._held: dict[tuple[int, int], int] = {}
        self.calls: list[tuple[int, int, int]] = []
        self.materialized_lock_bytes = 0

    def locking(self, descriptor: int, mode: int, nbytes: int) -> None:
        metadata = os.fstat(descriptor)
        identity = (metadata.st_dev, metadata.st_ino)
        with self._guard:
            self.calls.append((descriptor, mode, nbytes))
            if mode == self.LK_NBLCK:
                if identity in self._held:
                    raise OSError(errno.EACCES, "region already locked")
                # Native msvcrt extends an empty file with a NUL when locking
                # its first byte. Reproduce that Windows-only filesystem side
                # effect so semantic snapshot tests execute on every host.
                if metadata.st_size == 0:
                    os.write(descriptor, b"\0")
                    self.materialized_lock_bytes += 1
                self._held[identity] = descriptor
            elif mode == self.LK_UNLCK:
                if self._held.get(identity) == descriptor:
                    del self._held[identity]
            else:
                raise ValueError(f"unsupported msvcrt mode: {mode}")


class WindowsDomainStoreLockTests(unittest.TestCase):
    def _windows_lock_patches(self, domain_root: Path, fake: FakeMsvcrt):
        real_open = os.open

        def windows_open(path, flags, mode=0o777, *, dir_fd=None):
            if dir_fd is not None:
                raise NotImplementedError("dir_fd is unavailable on Windows")
            candidate = Path(path)
            if candidate == domain_root or candidate.is_dir():
                raise PermissionError("the Windows CRT does not open directories")
            return real_open(path, flags, mode)

        return (
            patch.object(security, "fcntl", None),
            patch.object(security, "msvcrt", fake),
            patch.object(security, "_NOFOLLOW_FLAG", 0),
            patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
            patch.object(store_writer, "_DIRECTORY_FLAG", 0),
            patch.object(security.os, "open", side_effect=windows_open),
            patch.object(
                security.os,
                "fchmod",
                side_effect=AssertionError("os.fchmod is unavailable on Windows"),
                create=True,
            ),
        )

    def test_windows_path_creates_opens_and_reenters_with_msvcrt(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            domain_root = paths.memory_dir / "domain-intelligence"
            fake = FakeMsvcrt()

            patches = self._windows_lock_patches(domain_root, fake)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                with security.domain_store_lock(paths) as outer:
                    with security.domain_store_lock(paths) as inner:
                        self.assertTrue(outer["locked"])
                        self.assertTrue(inner["locked"])

                lock_path = domain_root / ".store.lock"
                self.assertTrue(lock_path.is_file())
                lock_path.write_bytes(b"existing")
                with security.domain_store_lock(paths) as reopened:
                    self.assertTrue(reopened["locked"])

            self.assertEqual(
                [mode for _, mode, size in fake.calls if size == 1],
                [fake.LK_NBLCK, fake.LK_UNLCK, fake.LK_NBLCK, fake.LK_UNLCK],
            )

    def test_windows_nul_lock_is_neutral_to_store_snapshots(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            domain_root = paths.memory_dir / "domain-intelligence"
            fake = FakeMsvcrt()
            before = domain_store_snapshot(root)
            tree_before = repository_tree_snapshot(root)

            patches = self._windows_lock_patches(domain_root, fake)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                with security.domain_store_lock(paths) as acquired:
                    self.assertTrue(acquired["locked"])

            lock_path = domain_root / ".store.lock"
            self.assertEqual(lock_path.read_bytes(), b"\0")
            self.assertEqual(fake.materialized_lock_bytes, 1)
            self.assertEqual(domain_store_snapshot(root), before)
            self.assertEqual(repository_tree_snapshot(root), tree_before)

            candidate = domain_root / "candidates" / "candidate.json"
            candidate.parent.mkdir()
            candidate.write_bytes(b'{"candidate_id":"changed"}\n')
            self.assertEqual(
                domain_store_snapshot(root),
                {"candidates/candidate.json": candidate.read_bytes()},
            )
            self.assertEqual(list(domain_root.rglob(".*.tmp")), [])

    def test_windows_two_contenders_timeout_then_release_and_reacquire(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            domain_root = paths.memory_dir / "domain-intelligence"
            fake = FakeMsvcrt()
            contender_done = threading.Event()
            contender_errors: list[BaseException] = []

            def contend() -> None:
                try:
                    with security.domain_store_lock(paths, timeout_seconds=0):
                        raise AssertionError("contender acquired an already-held lock")
                except BaseException as exc:
                    contender_errors.append(exc)
                finally:
                    contender_done.set()

            patches = self._windows_lock_patches(domain_root, fake)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                with security.domain_store_lock(paths) as acquired:
                    self.assertTrue(acquired["locked"])
                    contender = threading.Thread(target=contend)
                    contender.start()
                    self.assertTrue(contender_done.wait(timeout=2.0))
                    contender.join(timeout=2.0)
                    self.assertFalse(contender.is_alive())
                    self.assertEqual(len(contender_errors), 1)
                    self.assertIsInstance(contender_errors[0], security.FileLockTimeout)

                with security.domain_store_lock(paths, timeout_seconds=0) as reacquired:
                    self.assertTrue(reacquired["locked"])

    def test_windows_path_refuses_lock_symlink_without_mutating_target(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            domain_root = paths.memory_dir / "domain-intelligence"
            domain_root.mkdir(parents=True)
            victim = root / "victim"
            victim.write_bytes(b"unchanged")
            (domain_root / ".store.lock").symlink_to(victim)
            fake = FakeMsvcrt()

            patches = self._windows_lock_patches(domain_root, fake)
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patches[5],
                patches[6],
                self.assertRaisesRegex(ValueError, "symlink"),
            ):
                with security.domain_store_lock(paths):
                    pass

            self.assertEqual(victim.read_bytes(), b"unchanged")
            self.assertEqual(fake.calls, [])

    def test_windows_path_refuses_domain_root_identity_replacement(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            domain_root = paths.memory_dir / "domain-intelligence"
            fake = FakeMsvcrt()
            real_validate = security._validate_portable_directory_chain
            validation_count = 0

            def replace_root_identity(chain):
                nonlocal validation_count
                validation_count += 1
                if validation_count == 2:
                    root_path, identity = chain[-1]
                    changed_identity = (identity[0], identity[1] + 1, identity[2])
                    chain = (*chain[:-1], (root_path, changed_identity))
                return real_validate(chain)

            patches = self._windows_lock_patches(domain_root, fake)
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patches[5],
                patches[6],
                patch.object(
                    security,
                    "_validate_portable_directory_chain",
                    side_effect=replace_root_identity,
                ),
                self.assertRaisesRegex(ValueError, "changed while writing"),
            ):
                with security.domain_store_lock(paths):
                    pass

            lock_path = domain_root / ".store.lock"
            self.assertEqual(validation_count, 2)
            self.assertTrue(lock_path.is_file())
            self.assertEqual(
                [mode for _, mode, size in fake.calls if size == 1],
                [fake.LK_NBLCK, fake.LK_UNLCK],
            )
            lock_path.unlink()
            self.assertFalse(lock_path.exists())

    def test_windows_path_refuses_lock_identity_replacement(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            domain_root = paths.memory_dir / "domain-intelligence"
            domain_root.mkdir(parents=True)
            lock_path = domain_root / ".store.lock"
            lock_path.write_bytes(b"original")
            before = lock_path.stat()
            named_after = SimpleNamespace(
                st_dev=before.st_dev,
                st_ino=before.st_ino + 1,
                st_mode=before.st_mode,
            )
            fake = FakeMsvcrt()

            with (
                patch.object(security, "fcntl", None),
                patch.object(security, "msvcrt", fake),
                patch.object(security, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_DIRECTORY_FLAG", 0),
                patch.object(
                    security,
                    "_lock_path_stat",
                    side_effect=[before, named_after],
                ) as lock_stat,
                self.assertRaisesRegex(ValueError, "changed while opening"),
            ):
                with security.domain_store_lock(paths):
                    pass

            self.assertEqual(lock_stat.call_count, 2)
            self.assertEqual(lock_path.read_bytes(), b"original")
            self.assertEqual(fake.calls, [])
            lock_path.unlink()
            self.assertFalse(lock_path.exists())


class PortableManagedJsonWriteTests(unittest.TestCase):
    @staticmethod
    def _metadata(*, inode: int = 7, mode: int = stat.S_IFREG | 0o600):
        return SimpleNamespace(st_dev=3, st_ino=inode, st_mode=mode)

    def test_fallback_creates_a_missing_managed_json_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")

            with (
                patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_DIRECTORY_FLAG", 0),
            ):
                store_writer.atomic_write_managed_json(
                    paths,
                    "operations",
                    "portable.json",
                    {"state": "created"},
                )

            target = paths.memory_dir / "domain-intelligence" / "operations" / "portable.json"
            self.assertEqual(
                target.read_bytes(),
                b'{\n  "state": "created"\n}\n',
            )

    def test_fallback_atomically_replaces_an_existing_regular_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            directory = paths.memory_dir / "domain-intelligence" / "operations"
            directory.mkdir(parents=True)
            target = directory / "portable.json"
            target.write_text("old", encoding="utf-8")

            with (
                patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_DIRECTORY_FLAG", 0),
            ):
                store_writer.atomic_write_managed_json(
                    paths,
                    "operations",
                    target.name,
                    {"state": "replaced"},
                )

            self.assertEqual(
                target.read_bytes(),
                b'{\n  "state": "replaced"\n}\n',
            )

    def test_fallback_refuses_a_managed_target_symlink(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            linked = self._metadata(mode=stat.S_IFLNK | 0o777)

            with (
                patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_DIRECTORY_FLAG", 0),
                patch.object(store_writer, "_portable_directory_chain", return_value=()),
                patch.object(store_writer.os, "stat", return_value=linked),
                patch.object(store_writer.os, "open") as open_file,
                self.assertRaisesRegex(ValueError, "symlink"),
            ):
                store_writer.atomic_write_managed_json(
                    paths,
                    "operations",
                    "portable.json",
                    {"state": "unsafe"},
                )

            open_file.assert_not_called()

    def test_fallback_refuses_a_nonregular_managed_target(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            target = paths.memory_dir / "domain-intelligence" / "operations" / "portable.json"
            target.mkdir(parents=True)

            with (
                patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_DIRECTORY_FLAG", 0),
                self.assertRaisesRegex(ValueError, "regular file"),
            ):
                store_writer.atomic_write_managed_json(
                    paths,
                    "operations",
                    target.name,
                    {"state": "unsafe"},
                )

            self.assertTrue(target.is_dir())

    def test_fallback_refuses_temporary_content_mutated_before_replace(self) -> None:
        mutations = (
            ("same-size", lambda original: b"x" * len(original)),
            ("size", lambda original: original + b"attacker"),
        )
        for name, mutate in mutations:
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = resolve_paths(root / ".omh", root / ".hermes")
                directory = paths.memory_dir / "domain-intelligence" / "operations"
                directory.mkdir(parents=True)
                target = directory / "portable.json"
                target.write_bytes(b"trusted-existing")
                real_target_check = store_writer._portable_regular_target
                checks = 0

                def mutate_after_final_target_check(path):
                    nonlocal checks
                    result = real_target_check(path)
                    checks += 1
                    if checks == 2:
                        temporary = next(directory.glob(".*.tmp"))
                        temporary.write_bytes(mutate(temporary.read_bytes()))
                    return result

                with (
                    patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
                    patch.object(store_writer, "_DIRECTORY_FLAG", 0),
                    patch.object(
                        store_writer,
                        "_portable_regular_target",
                        side_effect=mutate_after_final_target_check,
                    ),
                    self.assertRaisesRegex(ValueError, "temporary content changed"),
                ):
                    store_writer.atomic_write_managed_json(
                        paths,
                        "operations",
                        target.name,
                        {"state": "trusted-new"},
                    )

                self.assertEqual(target.read_bytes(), b"trusted-existing")
                self.assertEqual(list(directory.glob(".*.tmp")), [])

    def test_fallback_refuses_a_target_replaced_while_writing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            directory = paths.memory_dir / "domain-intelligence" / "operations"
            directory.mkdir(parents=True)
            target = directory / "portable.json"
            target.write_text("original", encoding="utf-8")
            before = self._metadata(inode=7)
            replacement = self._metadata(inode=8)

            with (
                patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_DIRECTORY_FLAG", 0),
                patch.object(
                    store_writer,
                    "_portable_regular_target",
                    side_effect=[before, replacement],
                ),
                self.assertRaisesRegex(ValueError, "changed before replacement"),
            ):
                store_writer.atomic_write_managed_json(
                    paths,
                    "operations",
                    target.name,
                    {"state": "replacement"},
                )

            self.assertEqual(target.read_text(encoding="utf-8"), "original")
            self.assertEqual(list(directory.glob(".*.tmp")), [])


@requires_domain_intelligence_store
class PosixManagedJsonWriteTests(unittest.TestCase):
    def test_refuses_temporary_content_mutated_before_replace(self) -> None:
        mutations = (
            ("same-size", lambda original: b"x" * len(original)),
            ("size", lambda original: original + b"attacker"),
        )
        for name, mutate in mutations:
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = resolve_paths(root / ".omh", root / ".hermes")
                directory = paths.memory_dir / "domain-intelligence" / "operations"
                directory.mkdir(parents=True)
                target = directory / "posix.json"
                target.write_bytes(b"trusted-existing")
                real_target_check = store_writer._regular_target_at
                checks = 0

                def mutate_after_final_target_check(directory_fd, filename):
                    nonlocal checks
                    result = real_target_check(directory_fd, filename)
                    checks += 1
                    if checks == 2:
                        temporary = next(directory.glob(".*.tmp"))
                        temporary.write_bytes(mutate(temporary.read_bytes()))
                    return result

                with (
                    patch.object(
                        store_writer,
                        "_regular_target_at",
                        side_effect=mutate_after_final_target_check,
                    ),
                    self.assertRaisesRegex(ValueError, "temporary content changed"),
                ):
                    store_writer.atomic_write_managed_json(
                        paths,
                        "operations",
                        target.name,
                        {"state": "trusted-new"},
                    )

                self.assertEqual(target.read_bytes(), b"trusted-existing")
                self.assertEqual(list(directory.glob(".*.tmp")), [])


class PortableDomainStoreLockTests(unittest.TestCase):
    _FLAGS = os.O_RDWR | os.O_CREAT | os.O_APPEND

    def _assert_private_lock_mode(self, lock_path: Path) -> None:
        # The Windows CRT reports regular writable files as 0o666 regardless
        # of the creation mode; POSIX mode bits are not a Windows ACL signal.
        expected = 0o666 if os.name == "nt" else 0o600
        self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), expected)

    @staticmethod
    def _metadata(*, inode: int = 7, mode: int = stat.S_IFREG | 0o600):
        return SimpleNamespace(st_dev=3, st_ino=inode, st_mode=mode)

    def test_public_lock_fallback_creates_and_reenters_a_private_lock(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")

            with (
                patch.object(security, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_DIRECTORY_FLAG", 0),
            ):
                with security.domain_store_lock(paths) as outer:
                    with security.domain_store_lock(paths) as inner:
                        self.assertTrue(outer["locked"])
                        self.assertTrue(inner["locked"])

            lock_path = paths.memory_dir / "domain-intelligence" / ".store.lock"
            self.assertTrue(lock_path.is_file())
            self.assertFalse(lock_path.is_symlink())
            self._assert_private_lock_mode(lock_path)

    def test_public_lock_fallback_opens_an_existing_regular_lock(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            lock_path = paths.memory_dir / "domain-intelligence" / ".store.lock"
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text("existing", encoding="utf-8")

            with (
                patch.object(security, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_DIRECTORY_FLAG", 0),
            ):
                with security.domain_store_lock(paths) as state:
                    self.assertTrue(state["locked"])

            self.assertEqual(lock_path.read_text(encoding="utf-8"), "existing")
            self._assert_private_lock_mode(lock_path)

    def test_public_lock_fallback_refuses_a_lock_symlink(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            lock_path = paths.memory_dir / "domain-intelligence" / ".store.lock"
            lock_path.parent.mkdir(parents=True)
            victim = root / "victim"
            victim.write_text("unchanged", encoding="utf-8")
            lock_path.symlink_to(victim)

            with (
                patch.object(security, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_DIRECTORY_FLAG", 0),
                self.assertRaisesRegex(ValueError, "symlink"),
            ):
                with security.domain_store_lock(paths):
                    pass

            self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged")

    def test_public_lock_fallback_refuses_domain_root_replacement_while_opening(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            domain_root = paths.memory_dir / "domain-intelligence"
            domain_root.mkdir(parents=True)
            displaced = domain_root.with_name("domain-intelligence-opened")
            replacement = root / "replacement-root"
            replacement.mkdir()
            real_validate = store_writer._validate_portable_directory_chain
            swapped = False

            def swap_before_validation(chain):
                nonlocal swapped
                if not swapped:
                    swapped = True
                    domain_root.rename(displaced)
                    domain_root.symlink_to(replacement, target_is_directory=True)
                return real_validate(chain)

            with (
                patch.object(security, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_DIRECTORY_FLAG", 0),
                patch.object(
                    store_writer,
                    "_validate_portable_directory_chain",
                    side_effect=swap_before_validation,
                ),
                self.assertRaisesRegex(ValueError, "symlink|changed while opening"),
            ):
                with security.domain_store_lock(paths):
                    pass

            self.assertTrue(swapped)
            self.assertEqual(list(replacement.iterdir()), [])
            self.assertFalse((displaced / ".store.lock").exists())

    def test_public_lock_fallback_refuses_a_lock_replaced_while_opening(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            lock_path = paths.memory_dir / "domain-intelligence" / ".store.lock"
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text("original", encoding="utf-8")
            before = lock_path.stat()
            named_after = SimpleNamespace(
                st_dev=before.st_dev,
                st_ino=before.st_ino + 1,
                st_mode=before.st_mode,
            )

            with (
                patch.object(security, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_NOFOLLOW_FLAG", 0),
                patch.object(store_writer, "_DIRECTORY_FLAG", 0),
                patch.object(
                    security,
                    "_lock_path_stat",
                    side_effect=[before, named_after],
                ) as lock_stat,
                self.assertRaisesRegex(ValueError, "changed while opening"),
            ):
                with security.domain_store_lock(paths):
                    pass

            self.assertEqual(lock_stat.call_count, 2)
            self.assertEqual(lock_path.read_text(encoding="utf-8"), "original")
            lock_path.unlink()
            self.assertFalse(lock_path.exists())

    def test_fallback_safely_creates_a_missing_lock(self) -> None:
        opened = self._metadata()
        with (
            patch.object(security, "_NOFOLLOW_FLAG", 0),
            patch.object(
                security.os,
                "stat",
                side_effect=[FileNotFoundError, opened],
            ),
            patch.object(security, "open_binary", return_value=19) as open_lock,
            patch.object(security.os, "fstat", return_value=opened),
        ):
            self.assertEqual(security._open_store_lock_descriptor(11, self._FLAGS), 19)

        self.assertTrue(open_lock.call_args.args[1] & os.O_EXCL)

    def test_fallback_safely_opens_an_existing_regular_lock(self) -> None:
        existing = self._metadata()
        with (
            patch.object(security, "_NOFOLLOW_FLAG", 0),
            patch.object(security.os, "stat", side_effect=[existing, existing]),
            patch.object(security, "open_binary", return_value=23) as open_lock,
            patch.object(security.os, "fstat", return_value=existing),
        ):
            self.assertEqual(security._open_store_lock_descriptor(11, self._FLAGS), 23)

        self.assertFalse(open_lock.call_args.args[1] & os.O_CREAT)

    def test_fallback_refuses_a_lock_symlink_without_opening_it(self) -> None:
        linked = self._metadata(mode=stat.S_IFLNK | 0o777)
        with (
            patch.object(security, "_NOFOLLOW_FLAG", 0),
            patch.object(security.os, "stat", return_value=linked),
            patch.object(security.os, "open") as open_lock,
            self.assertRaisesRegex(ValueError, "must not be a symlink"),
        ):
            security._open_store_lock_descriptor(11, self._FLAGS)

        open_lock.assert_not_called()

    def test_fallback_refuses_a_lock_replaced_while_opening(self) -> None:
        before = self._metadata(inode=7)
        replacement = self._metadata(inode=8)
        with (
            patch.object(security, "_NOFOLLOW_FLAG", 0),
            patch.object(security.os, "stat", side_effect=[before, replacement]),
            patch.object(security, "open_binary", return_value=29),
            patch.object(security.os, "fstat", return_value=before),
            patch.object(security.os, "close") as close_lock,
            self.assertRaisesRegex(ValueError, "changed while opening"),
        ):
            security._open_store_lock_descriptor(11, self._FLAGS)

        close_lock.assert_called_once_with(29)


@requires_domain_intelligence_store
class DomainIntelligenceStoreSecurityTests(unittest.TestCase):
    def test_domain_root_creation_stays_on_opened_parent_after_swap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            paths.memory_dir.mkdir(mode=0o700, parents=True)
            outside = root / "outside-root-parent"
            outside.mkdir(mode=0o755)
            before_mode = outside.stat().st_mode & 0o777
            real_open = store_writer.os.open
            swapped = False

            def swap_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal swapped
                descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                if path == "memory" and dir_fd is not None and not swapped:
                    swapped = True
                    paths.memory_dir.rename(paths.memory_dir.with_name("memory-opened"))
                    paths.memory_dir.symlink_to(outside, target_is_directory=True)
                return descriptor

            with patch.object(store_writer.os, "open", side_effect=swap_open):
                security.secure_domain_root(paths, create=True)

            self.assertTrue(swapped)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(outside.stat().st_mode & 0o777, before_mode)
            self.assertTrue(
                (paths.memory_dir.with_name("memory-opened") / "domain-intelligence").is_dir()
            )

    def test_lock_stays_on_opened_domain_root_after_parent_swap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            domain_root = paths.memory_dir / "domain-intelligence"
            domain_root.mkdir(mode=0o700, parents=True)
            outside = root / "outside-lock-parent"
            outside.mkdir(mode=0o755)
            before_mode = outside.stat().st_mode & 0o777
            real_open = store_writer.os.open
            swapped = False

            def swap_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal swapped
                descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                if path == "domain-intelligence" and dir_fd is not None and not swapped:
                    swapped = True
                    domain_root.rename(domain_root.with_name("domain-intelligence-opened"))
                    domain_root.symlink_to(outside, target_is_directory=True)
                return descriptor

            with patch.object(store_writer.os, "open", side_effect=swap_open):
                with security.domain_store_lock(paths):
                    pass

            opened_root = domain_root.with_name("domain-intelligence-opened")
            self.assertTrue(swapped)
            self.assertTrue((opened_root / ".store.lock").is_file())
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(outside.stat().st_mode & 0o777, before_mode)

    def test_managed_writes_do_not_follow_swapped_open_directory(self) -> None:
        cases = (
            (
                "candidates",
                lambda paths: store.write_candidate(
                    paths, "candidate-swap", {"candidate_id": "candidate-swap"}
                ),
            ),
            (
                "profiles",
                lambda paths: store.write_profile(
                    paths, "profile-swap", {"profile_id": "profile-swap"}
                ),
            ),
            (
                "reviews",
                lambda paths: store.write_review(
                    paths, "review-swap", {"review_id": "review-swap"}
                ),
            ),
            (
                "history",
                lambda paths: store.archive_profile(
                    paths, {"profile_id": "profile-swap", "revision": 1}
                ),
            ),
            (
                "operations",
                lambda paths: store.atomic_write_managed_json(
                    paths,
                    "operations",
                    "operation-swap.json",
                    {"operation_id": "operation-swap"},
                ),
            ),
        )
        for managed_name, write in cases:
            with self.subTest(managed_name=managed_name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = resolve_paths(root / ".omh", root / ".hermes")
                managed = (
                    paths.memory_dir / "domain-intelligence" / managed_name
                )
                managed.mkdir(mode=0o700, parents=True)
                outside = root / f"outside-{managed_name}"
                outside.mkdir(mode=0o755)
                before_mode = outside.stat().st_mode & 0o777
                real_open = store_writer.os.open
                swapped = False

                def swap_open(path, flags, mode=0o777, *, dir_fd=None):
                    nonlocal swapped
                    descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                    if path == managed_name and dir_fd is not None and not swapped:
                        swapped = True
                        managed.rename(managed.with_name(f"{managed_name}-opened"))
                        managed.symlink_to(outside, target_is_directory=True)
                    return descriptor

                with patch.object(store_writer.os, "open", side_effect=swap_open):
                    if managed_name == "operations":
                        write(paths)
                    else:
                        with self.assertRaisesRegex(ValueError, "safely opened"):
                            write(paths)

                self.assertTrue(swapped)
                self.assertEqual(list(outside.iterdir()), [])
                self.assertEqual(outside.stat().st_mode & 0o777, before_mode)

    def test_bounded_read_stays_on_opened_parent_after_swap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            directory = store.candidates_dir(paths)
            target = directory / "candidate-read.json"
            target.write_text(json.dumps({"source": "managed"}), encoding="utf-8")
            outside = root / "outside-read-parent"
            outside.mkdir(mode=0o755)
            (outside / target.name).write_text(
                json.dumps({"source": "outside"}), encoding="utf-8"
            )
            real_open = store_writer.os.open
            swapped = False

            def swap_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal swapped
                descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                if path == "candidates" and dir_fd is not None and not swapped:
                    swapped = True
                    directory.rename(directory.with_name("candidates-opened"))
                    directory.symlink_to(outside, target_is_directory=True)
                return descriptor

            with patch.object(store_writer.os, "open", side_effect=swap_open):
                value = security.read_bounded_json(target)

            self.assertTrue(swapped)
            self.assertEqual(value, {"source": "managed"})
            self.assertEqual(
                json.loads((outside / target.name).read_text(encoding="utf-8")),
                {"source": "outside"},
            )

    def test_authoritative_reads_reject_all_target_identity_conflicts(self) -> None:
        cases = (
            (
                "candidate",
                "candidate_id",
                "dicand_authority",
                store.candidates_dir,
                lambda paths, artifact_id: store.read_candidate_or_raise(
                    paths, artifact_id
                ),
                "candidate_identity_conflict",
            ),
            (
                "profile",
                "profile_id",
                "dprof_authority",
                store.profiles_dir,
                lambda paths, artifact_id: store.read_profile(paths, artifact_id),
                "profile_identity_conflict",
            ),
            (
                "review",
                "review_id",
                "direview_authority",
                store.reviews_dir,
                lambda paths, artifact_id: store.read_review(paths, artifact_id),
                "review_identity_conflict",
            ),
        )
        for (
            name,
            identity_field,
            artifact_id,
            directory_fn,
            reader,
            expected_error,
        ) in cases:
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = resolve_paths(root / ".omh", root / ".hermes")
                directory = directory_fn(paths)
                artifact = {identity_field: artifact_id}
                (directory / f"{artifact_id}.json").write_text(
                    json.dumps(artifact), encoding="utf-8"
                )
                (directory / "alias.json").write_text(
                    json.dumps({**artifact, "messages": []}),
                    encoding="utf-8",
                )

                if name == "review":
                    value, error = reader(paths, artifact_id)
                    self.assertIsNone(value)
                    self.assertEqual(error, expected_error)
                else:
                    with self.assertRaisesRegex(ValueError, expected_error):
                        reader(paths, artifact_id)

    def test_authoritative_reads_ignore_unrelated_malformed_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate_id = "dicand_unique"
            profile_id = "dprof_unique"
            review_id = "direview_unique"
            cases = (
                (store.candidates_dir(paths), "candidate_id", candidate_id),
                (store.profiles_dir(paths), "profile_id", profile_id),
                (store.reviews_dir(paths), "review_id", review_id),
            )
            for directory, identity_field, artifact_id in cases:
                (directory / f"{artifact_id}.json").write_text(
                    json.dumps({identity_field: artifact_id}),
                    encoding="utf-8",
                )
                (directory / "unrelated-broken.json").write_text("{", encoding="utf-8")

            self.assertEqual(
                store.read_candidate_or_raise(paths, candidate_id)["candidate_id"],
                candidate_id,
            )
            self.assertEqual(
                store.read_profile(paths, profile_id)["profile_id"], profile_id
            )
            review, error = store.read_review(paths, review_id)
            self.assertIsNone(error)
            self.assertEqual(review["review_id"], review_id)

    def test_pairing_readers_close_candidate_and_review_alias_bypass(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate_id = "dicand_pairing"
            review_id = "direview_pairing"
            profile_id = "dprof_pairing"
            for directory, identity_field, artifact_id in (
                (store.candidates_dir(paths), "candidate_id", candidate_id),
                (store.reviews_dir(paths), "review_id", review_id),
            ):
                artifact = {identity_field: artifact_id}
                (directory / f"{artifact_id}.json").write_text(
                    json.dumps(artifact), encoding="utf-8"
                )
                (directory / "alias.json").write_text(
                    json.dumps(artifact), encoding="utf-8"
                )
            store.write_profile(paths, profile_id, {"profile_id": profile_id})
            candidate_diagnostics: list[dict[str, str]] = []
            review_diagnostics: list[dict[str, str]] = []

            self.assertEqual(store.read_candidates(paths, candidate_diagnostics), [])
            self.assertEqual(store.read_reviews(paths, review_diagnostics), [])
            with self.assertRaisesRegex(ValueError, "candidate_identity_conflict"):
                store.read_candidate_or_raise(paths, candidate_id)
            review, error = store.read_review(paths, review_id)
            self.assertIsNone(review)
            self.assertEqual(error, "review_identity_conflict")
            self.assertEqual(
                store.read_profile(paths, profile_id), {"profile_id": profile_id}
            )

    def test_candidates_directory_symlink_escape_is_rejected_without_external_mutation(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            outside = root / "outside-candidates"
            outside.mkdir(mode=0o755)
            domain_root = paths.memory_dir / "domain-intelligence"
            domain_root.mkdir(parents=True)
            (domain_root / "candidates").symlink_to(outside, target_is_directory=True)
            before_mode = outside.stat().st_mode & 0o777

            with self.assertRaisesRegex(ValueError, "symlink"):
                store.candidates_dir(paths)

            self.assertEqual(outside.stat().st_mode & 0o777, before_mode)
            self.assertEqual(list(outside.iterdir()), [])

    def test_domain_root_and_artifact_symlinks_are_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            outside = root / "outside-root"
            outside.mkdir()
            paths.memory_dir.mkdir(parents=True)
            (paths.memory_dir / "domain-intelligence").symlink_to(
                outside, target_is_directory=True
            )
            with self.assertRaisesRegex(ValueError, "symlink"):
                store.domain_root(paths)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            candidate_id = "dicand_linked"
            candidate_dir = store.candidates_dir(paths)
            victim = root / "victim.json"
            victim.write_text(
                json.dumps({"candidate_id": candidate_id}), encoding="utf-8"
            )
            (candidate_dir / f"{candidate_id}.json").symlink_to(victim)
            before = victim.read_bytes()

            with self.assertRaisesRegex(ValueError, "symlink"):
                store.read_candidate_or_raise(paths, candidate_id)

            self.assertEqual(victim.read_bytes(), before)

    def test_lock_symlink_does_not_chmod_or_mutate_victim(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            domain_root = paths.memory_dir / "domain-intelligence"
            domain_root.mkdir(parents=True)
            victim = root / "lock-victim"
            victim.write_text("unchanged", encoding="utf-8")
            victim.chmod(0o644)
            (domain_root / ".store.lock").symlink_to(victim)
            before = (victim.read_bytes(), victim.stat().st_mode & 0o777)

            with self.assertRaisesRegex(ValueError, "symlink"):
                with file_lock(store.store_lock_target(paths), private=True):
                    pass

            self.assertEqual(
                (victim.read_bytes(), victim.stat().st_mode & 0o777), before
            )

    def test_safe_domain_lock_api_creates_private_regular_lock(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            with store.domain_store_lock(paths) as state:
                self.assertTrue(state["locked"])
            lock_path = paths.memory_dir / "domain-intelligence" / ".store.lock"
            self.assertTrue(lock_path.is_file())
            self.assertFalse(lock_path.is_symlink())
            expected_mode = 0o666 if os.name == "nt" else 0o600
            self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), expected_mode)

    def test_bulk_readers_reject_filename_mismatch_and_duplicate_embedded_ids(
        self,
    ) -> None:
        cases = (
            ("candidates", "candidate_id", store.candidates_dir, store.read_candidates),
            ("profiles", "profile_id", store.profiles_dir, store.read_profiles),
            ("reviews", "review_id", store.reviews_dir, store.read_reviews),
        )
        for name, identity_field, directory_fn, reader in cases:
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = resolve_paths(root / ".omh", root / ".hermes")
                directory = directory_fn(paths)
                (directory / "expected.json").write_text(
                    json.dumps({identity_field: "other"}), encoding="utf-8"
                )
                diagnostics: list[dict[str, str]] = []
                self.assertEqual(reader(paths, diagnostics), [])
                self.assertEqual(diagnostics[0]["reason"], "artifact_identity_mismatch")

            with self.subTest(name=f"{name}-duplicate"), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = resolve_paths(root / ".omh", root / ".hermes")
                directory = directory_fn(paths)
                (directory / "duplicate.json").write_text(
                    json.dumps({identity_field: "duplicate"}), encoding="utf-8"
                )
                (directory / "alias.json").write_text(
                    json.dumps({identity_field: "duplicate"}), encoding="utf-8"
                )
                diagnostics = []
                self.assertEqual(reader(paths, diagnostics), [])
                self.assertEqual(
                    {item["reason"] for item in diagnostics}, {"duplicate_embedded_id"}
                )

    def test_reader_bounds_size_depth_nodes_and_file_count(self) -> None:
        required_constants = (
            "MAX_DOMAIN_ARTIFACT_BYTES",
            "MAX_DOMAIN_ARTIFACT_FILES",
            "MAX_DOMAIN_CANDIDATE_FILES",
            "MAX_DOMAIN_JSON_DEPTH",
            "MAX_DOMAIN_JSON_NODES",
        )
        for name in required_constants:
            self.assertIsInstance(getattr(store, name, None), int)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            directory = store.candidates_dir(paths)
            oversized = {
                "candidate_id": "oversized",
                "padding": "x" * store.MAX_DOMAIN_ARTIFACT_BYTES,
            }
            (directory / "oversized.json").write_text(
                json.dumps(oversized), encoding="utf-8"
            )
            diagnostics: list[dict[str, str]] = []
            self.assertEqual(store.read_candidates(paths, diagnostics), [])
            self.assertEqual(diagnostics[0]["reason"], "artifact_too_large")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            directory = store.candidates_dir(paths)
            deep = (
                '{"candidate_id":"deep","nested":' + "[" * 1200 + "0" + "]" * 1200 + "}"
            )
            (directory / "deep.json").write_text(deep, encoding="utf-8")
            diagnostics = []
            self.assertEqual(store.read_candidates(paths, diagnostics), [])
            self.assertEqual(diagnostics[0]["reason"], "artifact_json_depth_exceeded")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            directory = store.candidates_dir(paths)
            nodes = {
                "candidate_id": "nodes",
                "nodes": [0] * store.MAX_DOMAIN_JSON_NODES,
            }
            (directory / "nodes.json").write_text(json.dumps(nodes), encoding="utf-8")
            diagnostics = []
            self.assertEqual(store.read_candidates(paths, diagnostics), [])
            self.assertEqual(diagnostics[0]["reason"], "artifact_json_nodes_exceeded")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            directory = store.candidates_dir(paths)
            for index in range(store.MAX_DOMAIN_CANDIDATE_FILES):
                artifact_id = f"candidate-{index}"
                (directory / f"{artifact_id}.json").write_text(
                    json.dumps({"candidate_id": artifact_id}), encoding="utf-8"
                )
            (directory / "external-extra.json").write_text("{}", encoding="utf-8")
            diagnostics = []
            records = store.read_candidates(paths, diagnostics)
            self.assertEqual(len(records), store.MAX_DOMAIN_CANDIDATE_FILES)
            self.assertEqual(
                {item[0]["candidate_id"] for item in records},
                {f"candidate-{index}" for index in range(store.MAX_DOMAIN_CANDIDATE_FILES)},
            )
            self.assertIn(
                {"path_name": "candidates", "reason": "artifact_file_count_exceeded"},
                diagnostics,
            )

    def test_candidate_capacity_preserves_readable_maximum_and_rejects_new_write(
        self,
    ) -> None:
        self.assertEqual(store.MAX_DOMAIN_CANDIDATE_FILES, 256)
        self.assertGreaterEqual(store.MAX_DOMAIN_ARTIFACT_FILES, 1024)
        self.assertGreaterEqual(
            store.MAX_DOMAIN_ARTIFACT_FILES, store.MAX_DOMAIN_CANDIDATE_FILES * 4
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            directory = store.candidates_dir(paths)
            for index in range(store.MAX_DOMAIN_CANDIDATE_FILES):
                artifact_id = f"candidate-{index}"
                (directory / f"{artifact_id}.json").write_text(
                    json.dumps({"candidate_id": artifact_id}),
                    encoding="utf-8",
                )
            diagnostics: list[dict[str, str]] = []
            self.assertEqual(
                len(store.read_candidates(paths, diagnostics)),
                store.MAX_DOMAIN_CANDIDATE_FILES,
            )
            self.assertEqual(diagnostics, [])

            with self.assertRaisesRegex(ValueError, "candidate_capacity_exceeded"):
                store.ensure_candidate_capacity(paths)
            with self.assertRaisesRegex(ValueError, "candidate_capacity_exceeded"):
                store.write_candidate(
                    paths, "candidate-overflow", {"candidate_id": "candidate-overflow"}
                )
            self.assertFalse((directory / "candidate-overflow.json").exists())
            store.write_candidate(
                paths, "candidate-0", {"candidate_id": "candidate-0", "updated": True}
            )
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual((directory / "candidate-0.json").stat().st_mode & 0o777, 0o600)

    def test_general_artifact_writes_stop_at_reader_capacity(self) -> None:
        cases = (
            (
                "profile",
                store.profiles_dir,
                lambda paths: store.write_profile(
                    paths, "profile-new", {"profile_id": "profile-new"}
                ),
            ),
            (
                "review",
                store.reviews_dir,
                lambda paths: store.write_review(
                    paths, "review-new", {"review_id": "review-new"}
                ),
            ),
            (
                "history",
                store.history_dir,
                lambda paths: store.archive_profile(
                    paths, {"profile_id": "profile-new", "revision": 1}
                ),
            ),
        )
        for name, directory_fn, writer in cases:
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = resolve_paths(root / ".omh", root / ".hermes")
                directory = directory_fn(paths)
                for index in range(2):
                    (directory / f"occupied-{index}.json").write_text(
                        "{}", encoding="utf-8"
                    )
                with patch.object(store, "MAX_DOMAIN_ARTIFACT_FILES", 2):
                    with self.assertRaisesRegex(
                        ValueError, "artifact_capacity_exceeded"
                    ):
                        writer(paths)

    def test_managed_writes_reject_directory_swap_without_external_mutation(self) -> None:
        cases = (
            (
                "candidate",
                lambda paths: store.write_candidate(
                    paths, "candidate-race", {"candidate_id": "candidate-race"}
                ),
            ),
            (
                "profile",
                lambda paths: store.write_profile(
                    paths, "profile-race", {"profile_id": "profile-race"}
                ),
            ),
            (
                "review",
                lambda paths: store.write_review(
                    paths, "review-race", {"review_id": "review-race"}
                ),
            ),
            (
                "history",
                lambda paths: store.archive_profile(
                    paths, {"profile_id": "profile-race", "revision": 1}
                ),
            ),
        )
        real_capacity_check = store.ensure_new_artifact_capacity
        for managed_name, writer in cases:
            with self.subTest(managed_name=managed_name), TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = resolve_paths(root / ".omh", root / ".hermes")
                outside = root / "outside"
                outside.mkdir(mode=0o755)
                before_mode = outside.stat().st_mode & 0o777

                def swap_after_validation(directory, target, **kwargs):
                    real_capacity_check(directory, target, **kwargs)
                    directory.rename(directory.with_name(f"{directory.name}-validated"))
                    directory.symlink_to(outside, target_is_directory=True)

                with patch.object(
                    store,
                    "ensure_new_artifact_capacity",
                    side_effect=swap_after_validation,
                ):
                    with self.assertRaisesRegex(ValueError, "safely opened"):
                        writer(paths)

                self.assertEqual(list(outside.iterdir()), [])
                self.assertEqual(outside.stat().st_mode & 0o777, before_mode)

    def test_history_reader_uses_bounded_identity_checked_storage(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            directory = store.history_dir(paths)
            profile_id = "dprof_history"
            for revision in (1, 2):
                artifact = {"profile_id": profile_id, "revision": revision}
                (directory / f"{profile_id}_r{revision}.json").write_text(
                    json.dumps(artifact), encoding="utf-8"
                )
            (directory / "alias_r3.json").write_text(
                json.dumps({"profile_id": profile_id, "revision": 3}),
                encoding="utf-8",
            )
            victim = root / "history-victim.json"
            victim.write_text(
                json.dumps({"profile_id": "dprof_victim", "revision": 4}),
                encoding="utf-8",
            )
            (directory / "dprof_victim_r4.json").symlink_to(victim)
            before = victim.read_bytes()
            diagnostics: list[dict[str, str]] = []

            records = store.read_history_profiles(paths, diagnostics)

            self.assertEqual(
                [(item[0]["profile_id"], item[0]["revision"]) for item in records],
                [(profile_id, 1), (profile_id, 2)],
            )
            self.assertEqual(
                {item["reason"] for item in diagnostics},
                {
                    "artifact_identity_mismatch",
                    "domain-intelligence artifact path must not be a symlink",
                },
            )
            self.assertEqual(victim.read_bytes(), before)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            directory = store.history_dir(paths)
            profile_id = "dprof_duplicate_history"
            duplicate = {"profile_id": profile_id, "revision": 1}
            (directory / f"{profile_id}_r1.json").write_text(
                json.dumps(duplicate), encoding="utf-8"
            )
            (directory / "alias_r1.json").write_text(
                json.dumps(duplicate), encoding="utf-8"
            )
            revision_two = {"profile_id": profile_id, "revision": 2}
            (directory / f"{profile_id}_r2.json").write_text(
                json.dumps(revision_two), encoding="utf-8"
            )
            diagnostics = []

            records = store.read_history_profiles(paths, diagnostics)

            self.assertEqual(
                [(item[0]["profile_id"], item[0]["revision"]) for item in records],
                [(profile_id, 2)],
            )
            self.assertEqual(
                diagnostics,
                [
                    {"path_name": "alias_r1.json", "reason": "duplicate_embedded_id"},
                    {
                        "path_name": f"{profile_id}_r1.json",
                        "reason": "duplicate_embedded_id",
                    },
                ],
            )


if __name__ == "__main__":
    unittest.main()
