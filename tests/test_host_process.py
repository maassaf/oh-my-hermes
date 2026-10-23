"""`host_process`: whether the Hermes process a helper ran in still runs, through Hermes' own probes only.

The probes are `gateway.status`'s, as the Hermes process provides them. Only an
answer they give that the recorded process ended reads as gone; anything they
cannot tell, and everything outside Hermes, reads as unknown.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from _local_package import load_local_package
from _module_patch import patch_modules

load_local_package()

from omh.plugin_bundle.omh import host_process  # noqa: E402

NO_HERMES = {"gateway": None, "gateway.status": None}


class _Broken:
    """A Hermes `gateway.status` that fails to load its names with something other than an ImportError."""

    def __getattr__(self, name: str) -> object:
        raise RuntimeError("half-initialized module")


BROKEN_HERMES = {"gateway": SimpleNamespace(status=_Broken()), "gateway.status": _Broken()}


def hermes(*, exists: bool = True, started: object = 777, raises: bool = False) -> dict[str, object]:
    def start_time(pid: int) -> object:
        if raises:
            raise RuntimeError("probe exploded")
        return started

    status = SimpleNamespace(_pid_exists=lambda pid: exists, get_process_start_time=start_time,
                             start_time_fingerprints_match=lambda recorded, current: abs(current - recorded) <= 200)
    return {"gateway": SimpleNamespace(status=status), "gateway.status": status}


class ThisProcess(unittest.TestCase):
    def test_outside_hermes_nothing_is_recorded(self) -> None:
        for probes in (NO_HERMES, BROKEN_HERMES):
            with self.subTest(broken=probes is BROKEN_HERMES), patch_modules(probes):
                self.assertIsNone(host_process.this_process())

    def test_the_pid_and_hermes_start_time_or_none_when_unread(self) -> None:
        for probes, started in ((hermes(started=777), 777), (hermes(started=None), None),
                                (hermes(started="777"), None), (hermes(raises=True), None)):
            with self.subTest(started=started), patch_modules(probes):
                self.assertEqual(host_process.this_process(),
                                 {"pid": os.getpid(), "started": started, "machine": host_process.machine()})


class Machine(unittest.TestCase):
    """Mutation: leave the pid namespace or the boot id out on Linux, or let a host name in elsewhere."""

    def linux(self, boot_id: str | None, namespace: str) -> str:
        with tempfile.TemporaryDirectory() as folder:
            boot = Path(folder) / "boot_id"
            if boot_id is not None:
                boot.write_text(boot_id + "\n", encoding="ascii")
            with mock.patch.object(host_process, "_PLATFORM", "linux"), \
                    mock.patch.object(host_process, "_BOOT_ID", boot), \
                    mock.patch.object(host_process.os, "readlink", return_value=namespace):
                return host_process.machine()

    def test_linux_names_the_boot_and_the_pid_namespace(self) -> None:
        host = self.linux("4f1c0d2e-boot", "pid:[4026531836]")
        self.assertEqual(host, "linux:4f1c0d2e-boot:pid:[4026531836]")
        self.assertNotEqual(self.linux("4f1c0d2e-boot", "pid:[4026532999]"), host, "a container on this host")
        # Another host, or this one after a reboot: unknown, never "gone", since neither can be told apart.
        self.assertNotEqual(self.linux("9a8b7c6d-boot", "pid:[4026531836]"), host)

    def test_linux_that_cannot_read_where_it_is_records_nothing(self) -> None:
        self.assertEqual(self.linux(None, "pid:[4026531836]"), "")
        with mock.patch.object(host_process, "machine", return_value=""), patch_modules(hermes()):
            self.assertIsNone(host_process.this_process())

    def on(self, platform_name: str, hardware: str, node: str) -> str:
        reader = "_darwin_uuid" if platform_name == "darwin" else "_windows_guid"
        with mock.patch.object(host_process, "_PLATFORM", platform_name), \
                mock.patch.object(host_process, reader, return_value=hardware), \
                mock.patch("platform.node", return_value=node):
            return host_process.machine()

    def test_off_linux_a_hardware_id_that_does_not_follow_the_network(self) -> None:
        """Two machines never read as one, and a renamed Mac (DHCP, reverse DNS, Bonjour "-2") stays itself.

        Mutation: use the platform alone, or the host name while a hardware id was read.
        """
        mac = self.on("darwin", "11111111-2222-3333-4444-555555555555", "MacBook-Pro.local")
        self.assertEqual(mac, "darwin:11111111-2222-3333-4444-555555555555")
        self.assertEqual(self.on("darwin", "11111111-2222-3333-4444-555555555555", "MacBook-Pro-2.local"), mac)
        self.assertNotEqual(self.on("darwin", "99999999-2222-3333-4444-555555555555", "MacBook-Pro.local"), mac)
        self.assertEqual(self.on("win32", "6d0e4c2a-guid", "DESKTOP-1"), "win32:6d0e4c2a-guid")
        self.assertNotEqual(self.on("win32", "7e1f5d3b-guid", "DESKTOP-1"), "win32:6d0e4c2a-guid")

    def test_without_a_hardware_id_nothing_is_recorded(self) -> None:
        """Two machines that share a name must never read as one, so a host name is never the identity.

        Mutation: fall back to the host name, or to the platform alone.
        """
        for platform_name, node in (("darwin", "MacBook-Pro.local"), ("win32", "DESKTOP-1"), ("freebsd14", "box")):
            with self.subTest(platform=platform_name):
                self.assertEqual(self.on(platform_name, "", node), "")
                with mock.patch.object(host_process, "_PLATFORM", platform_name), \
                        mock.patch.object(host_process, "_darwin_uuid", return_value=""), \
                        mock.patch.object(host_process, "_windows_guid", return_value=""), \
                        patch_modules(hermes()):
                    self.assertIsNone(host_process.this_process())

    def test_a_host_name_record_never_reads_as_here(self) -> None:
        """A `host:` record, which nothing writes, is unknown even under this machine's own name."""
        import platform

        status = hermes(exists=False)
        for here in ("darwin:11111111-2222-3333-4444-555555555555", ""):
            with self.subTest(here=here), mock.patch.object(host_process, "machine", return_value=here), \
                    patch_modules(status):
                self.assertIsNone(host_process.process_alive(
                    {"pid": 4242, "started": 777, "machine": f"host:{platform.node()}"}))

    def test_gethostuuid_is_read_through_libc_with_a_bounded_wait(self) -> None:
        """Mutation: accept a non-zero return, or pass a zero wait (gethostuuid(2): wait indefinitely)."""
        import ctypes

        calls: list[tuple[int, int]] = []

        class Libc:
            def __init__(self, result: int) -> None:
                self.result = result

            def gethostuuid(self, found: object, wait: object) -> int:
                ctypes.memmove(found, bytes(range(16)), 16)
                calls.append((wait._obj.tv_sec, wait._obj.tv_nsec))
                return self.result

        with mock.patch.object(ctypes, "CDLL", return_value=Libc(0)):
            self.assertEqual(host_process._darwin_uuid(), "00010203-0405-0607-0809-0a0b0c0d0e0f")
        for result in (-1, 1):
            with self.subTest(result=result), mock.patch.object(ctypes, "CDLL", return_value=Libc(result)):
                self.assertEqual(host_process._darwin_uuid(), "")
        self.assertTrue(all(seconds > 0 or nanoseconds > 0 for seconds, nanoseconds in calls))
        with mock.patch.object(ctypes, "CDLL", side_effect=OSError("no libc")):
            self.assertEqual(host_process._darwin_uuid(), "")
        # A libc without `gethostuuid`: the symbol lookup raises AttributeError.
        with mock.patch.object(ctypes, "CDLL", return_value=SimpleNamespace()):
            self.assertEqual(host_process._darwin_uuid(), "")

    @unittest.skipUnless(sys.platform == "darwin", "reads this Mac's own hardware UUID")
    def test_this_mac_reads_its_hardware_uuid(self) -> None:
        self.assertRegex(host_process._darwin_uuid(), r"\A[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")

    @unittest.skipUnless(sys.platform == "win32", "reads this Windows machine's MachineGuid")
    def test_this_windows_machine_reads_its_machine_guid(self) -> None:
        self.assertRegex(host_process._windows_guid(), r"\A\S+\Z")

    def test_no_registry_off_windows(self) -> None:
        if sys.platform != "win32":
            self.assertEqual(host_process._windows_guid(), "")


class ProcessAlive(unittest.TestCase):
    """Mutation: read a pid that exists as alive under another start time, or an unknown as gone."""

    def setUp(self) -> None:
        self.RECORDED = {"pid": 4242, "started": 777, "machine": host_process.machine()}

    def alive(self, probes: dict[str, object], recorded: object = None) -> bool | None:
        with patch_modules(probes):
            return host_process.process_alive(self.RECORDED if recorded is None else recorded)

    def test_gone_only_when_the_probes_say_the_process_ended(self) -> None:
        self.assertIs(self.alive(hermes(exists=False)), False)
        # No start time recorded: unknown while the pid exists, gone once it does not.
        self.assertIs(self.alive(hermes(exists=False), {**self.RECORDED, "started": None}), False)
        self.assertIs(self.alive(hermes(started=5000)), False)  # the pid now names another process
        self.assertIs(self.alive(hermes(started=777)), True)
        self.assertIs(self.alive(hermes(started=900)), True)  # inside Hermes' drift tolerance

    def test_a_record_names_where_its_pid_was_read(self) -> None:
        self.assertEqual(host_process.recorded_process(self.RECORDED), self.RECORDED)
        for where in (None, "", 7, "x" * 301):
            with self.subTest(machine=where):
                self.assertIsNone(host_process.recorded_process({**self.RECORDED, "machine": where}))

    def test_what_cannot_be_told_is_unknown(self) -> None:
        here = host_process.machine()
        cases = {
            "outside Hermes": (NO_HERMES, None),
            "a Hermes whose probes fail to load": (BROKEN_HERMES, None),
            "a pid read on another machine": (hermes(exists=False), {"pid": 4242, "started": 777,
                                                                   "machine": "host:another-host"}),
            "a pid read in another pid namespace": (hermes(exists=False), {
                "pid": 4242, "started": 777, "machine": "linux:4f1c0d2e-boot:pid:[4026532999]"}),
            "no machine recorded": (hermes(exists=False), {"pid": 4242, "started": 777}),
            "no start time recorded": (hermes(), {"pid": 4242, "started": None, "machine": here}),
            "no start time now": (hermes(started=None), None),
            "a probe raised": (hermes(raises=True), None),
            "nothing recorded": (hermes(exists=False), {}),
            "a pid that is not a number": (hermes(exists=False), {"pid": "4242", "started": 777, "machine": here}),
            "a pid that is a flag": (hermes(exists=False), {"pid": True, "started": 777, "machine": here}),
            "no pid at all": (hermes(exists=False), {"pid": 0, "started": 777, "machine": here}),
            "a start time that is not a number": (hermes(exists=False), {"pid": 4242, "started": "777",
                                                                         "machine": here}),
        }
        for name, (probes, recorded) in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(self.alive(probes, recorded))


if __name__ == "__main__":
    unittest.main()
