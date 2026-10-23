from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from omh.paths import OmhPaths
from omh.plugin_bundle.omh.buzz_diagnostics import probe_buzz


class BuzzDiagnosticsTests(unittest.TestCase):
    def test_missing_cli_is_truthful_read_only_and_secret_safe(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(root / ".omh", root / ".hermes")
            paths.hermes_home.mkdir()
            paths.hermes_config_path.write_text(
                "gateway:\n  platforms:\n    buzz:\n      enabled: true\n      relay_url: https://relay.example\n",
                encoding="utf-8",
            )
            before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
            with patch.dict(
                os.environ,
                {
                    "BUZZ_PRIVATE_KEY": "nsec-secret-do-not-print",
                    "OPENAI_API_KEY": "model-secret-do-not-print",
                    "BUZZ_CLI_PATH": str(root / "missing-buzz"),
                },
                clear=False,
            ):
                payload = probe_buzz(paths)
            after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))

            self.assertEqual(payload["schema_version"], "omh_buzz_probe/v1")
            self.assertEqual(payload["status"], "missing")
            self.assertEqual(payload["reason_code"], "buzz_cli_missing")
            self.assertTrue(payload["configured"])
            self.assertTrue(payload["credential_present"])
            self.assertTrue(payload["read_only"])
            self.assertEqual(before, after)
            rendered = json.dumps(payload, sort_keys=True)
            self.assertNotIn("nsec-secret-do-not-print", rendered)
            self.assertNotIn("model-secret-do-not-print", rendered)
            self.assertNotIn("https://relay.example", rendered)

    def test_executable_metadata_is_reported_without_executing_cli(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(root / ".omh", root / ".hermes")
            paths.hermes_home.mkdir()
            marker = root / "buzz-executed"
            executable = root / "buzz"
            executable.write_text(f"#!/bin/sh\n: > '{marker}'\n", encoding="utf-8")
            executable.chmod(0o755)

            with patch.dict(
                os.environ,
                {
                    "BUZZ_CLI_PATH": str(executable),
                    "BUZZ_PRIVATE_KEY": "nsec-secret-do-not-print",
                    "OPENAI_API_KEY": "model-secret-do-not-print",
                },
                clear=False,
            ):
                payload = probe_buzz(paths)

            self.assertFalse(marker.exists())
            self.assertEqual(payload["status"], "unverified")
            self.assertEqual(payload["reason_code"], "buzz_cli_present_not_executed")
            self.assertTrue(payload["installed"])
            self.assertEqual(payload["executable"], str(executable.resolve()))
            self.assertIsNone(payload["version"])
            self.assertFalse(payload["observed"])
            self.assertNotIn("nsec-secret-do-not-print", json.dumps(payload))

    def test_probe_never_discovers_buzz_from_ambient_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(root / ".omh", root / ".hermes")
            paths.hermes_home.mkdir()
            hostile_bin = root / "hostile-bin"
            hostile_bin.mkdir()
            executable = hostile_bin / "buzz"
            executable.write_text("#!/bin/sh\nprintf 'owned\\n'\n", encoding="utf-8")
            executable.chmod(0o755)

            payload = probe_buzz(paths, environ={"HOME": str(root), "PATH": str(hostile_bin)})

            self.assertEqual(payload["status"], "missing")
            self.assertEqual(payload["reason_code"], "buzz_cli_missing")
            self.assertIsNone(payload["executable"])

    def test_probe_accepts_only_direct_buzz_config_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OmhPaths(root / ".omh", root / ".hermes")
            paths.hermes_home.mkdir()
            executable = root / "buzz"
            executable.write_text("", encoding="utf-8")
            executable.chmod(0o755)
            paths.hermes_config_path.write_text(
                "gateway:\n"
                "  platforms:\n"
                "    buzz:\n"
                '      enabled: "true"\n'
                "      extra:\n"
                "        nested:\n"
                f"          cli_path: {executable}\n",
                encoding="utf-8",
            )
            (paths.hermes_home / ".env").write_text(
                "export BUZZ_PRIVATE_KEY=nsec-secret-do-not-print\n",
                encoding="utf-8",
            )

            payload = probe_buzz(paths, environ={"HOME": str(root), "PATH": ""})

            self.assertTrue(payload["configured"])
            self.assertTrue(payload["credential_present"])
            self.assertEqual(payload["status"], "missing")
            self.assertIsNone(payload["executable"])


if __name__ == "__main__":
    unittest.main()
