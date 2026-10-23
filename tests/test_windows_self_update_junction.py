"""Real Windows junctions; no symlink or filesystem emulation in this module."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from typing import Literal
import unittest

from _platform_support import requires_windows

from omh.core.errors import OmhError
from omh.install.self_update_platform import JUNCTION_TARGET_ENV, JUNCTION_TIMEOUT_SECONDS, SelfUpdatePlatform, remove_directory_link
from omh.install.self_update_state import pointer_target, switch_current
from omh.system.local_store import is_junction


@requires_windows
class WindowsSelfUpdateJunctionTests(unittest.TestCase):
    def _platform(self, executable: str, version_prefix: str) -> SelfUpdatePlatform:
        resolved = shutil.which(executable)
        self.assertIsNotNone(resolved, f"required Windows test executable is missing: {executable}")
        assert resolved is not None
        version = subprocess.run(
            [resolved, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"],
            shell=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=JUNCTION_TIMEOUT_SECONDS,
        )
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertTrue(version.stdout.strip().startswith(version_prefix), version.stdout)

        def runner(
            command: list[str],
            *,
            shell: Literal[False],
            cwd: str,
            env: dict[str, str],
            text: Literal[True],
            stdout: int,
            stderr: int,
            timeout: float,
        ) -> subprocess.CompletedProcess[str]:
            # Select the installed PowerShell, retaining the production program,
            # flags, environment transport and bounded subprocess semantics.
            return subprocess.run(
                [resolved, *command[1:]],
                shell=shell,
                cwd=cwd,
                env=env,
                text=text,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout,
            )

        return SelfUpdatePlatform.windows(runner)

    def _assert_pointer(self, root: Path, target: Path, platform: SelfUpdatePlatform) -> None:
        self.assertTrue(is_junction(root / "current"))
        pointed = pointer_target(root, platform=platform)
        self.assertIsNotNone(pointed)
        assert pointed is not None
        self.assertTrue(pointed.samefile(target))
        self.assertEqual((root / "current" / "sentinel").read_text(encoding="utf-8"), target.name)
        self.assertEqual(list(root.glob(".current.*")), [])

    def _exercise_lifecycle(self, executable: str, version_prefix: str) -> None:
        platform = self._platform(executable, version_prefix)
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "A&B%TEMP%^!()' $"
            root.mkdir()
            previous = root / "generations" / "previous"
            candidate = root / "generations" / "candidate"
            for target in (previous, candidate):
                target.mkdir(parents=True)
                (target / "sentinel").write_text(target.name, encoding="utf-8")
            direct = root / "direct junction"
            try:
                platform.create_directory_link(root, direct, previous)
                self.assertTrue(is_junction(direct))
                self.assertEqual((direct / "sentinel").read_text(encoding="utf-8"), "previous")
                remove_directory_link(direct)
                self.assertFalse(direct.exists())
                # First activation, replacement, and known-good recovery all
                # move actual junctions, not an injected filesystem substitute.
                for target in (previous, candidate, previous):
                    switch_current(root, target, platform=platform)
                    self._assert_pointer(root, target, platform)
            finally:
                for link in (direct, root / "current", *root.glob(".current.*")):
                    remove_directory_link(link)
            self.assertFalse((root / "current").exists())
            for target in (previous, candidate):
                self.assertEqual((target / "sentinel").read_text(encoding="utf-8"), target.name)

    def test_windows_powershell_51_creates_swaps_and_rolls_back_real_junctions(self) -> None:
        self._exercise_lifecycle("powershell.exe", "5.1.")

    def test_powershell_7_creates_swaps_and_rolls_back_real_junctions(self) -> None:
        self._exercise_lifecycle("pwsh.exe", "7.")

    def test_powershell_7_rejects_relative_target_and_preserves_known_good(self) -> None:
        native = self._platform("pwsh.exe", "7.")

        def relative_target_runner(
            command: list[str],
            *,
            shell: Literal[False],
            cwd: str,
            env: dict[str, str],
            text: Literal[True],
            stdout: int,
            stderr: int,
            timeout: float,
        ) -> subprocess.CompletedProcess[str]:
            # Negative control: send the pre-fix payload to actual PS7. Only
            # the data changes; creation, refusal and cleanup remain real.
            relative_environment = env.copy()
            relative_environment[JUNCTION_TARGET_ENV] = os.path.relpath(env[JUNCTION_TARGET_ENV], cwd)
            self.assertFalse(Path(relative_environment[JUNCTION_TARGET_ENV]).is_absolute())
            return native.runner(
                command,
                shell=shell,
                cwd=cwd,
                env=relative_environment,
                text=text,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout,
            )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = root / "generations" / "previous"
            candidate = root / "generations" / "candidate"
            for target in (previous, candidate):
                target.mkdir(parents=True)
                (target / "sentinel").write_text(target.name, encoding="utf-8")
            try:
                switch_current(root, previous, platform=native)
                with self.assertRaisesRegex(OmhError, "cannot create Windows directory junction"):
                    switch_current(root, candidate, platform=SelfUpdatePlatform.windows(relative_target_runner))
                self._assert_pointer(root, previous, native)
            finally:
                for link in (root / "current", *root.glob(".current.*")):
                    remove_directory_link(link)
            for target in (previous, candidate):
                self.assertEqual((target / "sentinel").read_text(encoding="utf-8"), target.name)


if __name__ == "__main__":
    unittest.main()
