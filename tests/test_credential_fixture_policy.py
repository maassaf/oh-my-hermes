"""Gate keeping AWS-key-shaped literals out of the tree.

A secret-scanning alert on a fabricated test value costs the same triage as a
real one: rotate, revoke, check the logs, close. The only way to not pay it
again is for the literal to not exist, which is what `tests/_credential_fixtures.py`
arranges -- the sample values are joined from a prefix and a body at import
time, so the shape is real at runtime and absent from disk.

That arrangement only holds while nobody writes the literal back. This module
re-derives the answer from the tree on every run: it walks the repository with
the same pattern the scanner uses, and fails with the offending path when a
match appears. Adding a fixture is then the obvious move rather than the
disciplined one.

The pattern is `_AWS_ACCESS_KEY` from `src/system/metadata_safety.py`, quoted
here rather than imported, so a change that narrows the production detector
cannot silently narrow this gate too.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from _credential_fixtures import AWS_ACCESS_KEY_ID, AWS_TEMPORARY_ACCESS_KEY_ID
from _local_package import load_local_package


load_local_package()
from omh.system.metadata_safety import is_secret_value_shaped

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_MODULE = "tests/_credential_fixtures.py"

# `\b(?:AKIA|ASIA)[A-Z0-9]{16}\b` -- the AWS access key id shape, long-term and
# temporary. Deliberately a copy of the production detector, not an import.
AWS_ACCESS_KEY_LITERAL = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")

# Text the scanner would read. Binary assets cannot carry a literal a reviewer
# would paste back, so they are not walked.
SCANNED_SUFFIXES = frozenset(
    {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".sh", ".ps1", ".html", ".css", ".js"}
)

# Untracked build output and local runtime state. Everything else under the
# repository root is walked, so a new tracked directory is covered by default.
IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".playwright-mcp",
        ".hermes",
        ".omh",
        ".omc",
        ".omo",
        ".loop",
    }
)


def _is_ignored(path: Path) -> bool:
    return any(
        part in IGNORED_DIRECTORY_NAMES or part.endswith(".egg-info") for part in path.relative_to(REPO_ROOT).parts
    )


def _scanned_files() -> list[Path]:
    return sorted(
        path
        for path in REPO_ROOT.rglob("*")
        if path.is_file() and path.suffix in SCANNED_SUFFIXES and not _is_ignored(path)
    )


class CredentialFixturePolicyTests(unittest.TestCase):
    def test_no_aws_access_key_literal_is_stored_in_the_tree(self) -> None:
        offenders = []
        for path in _scanned_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            if AWS_ACCESS_KEY_LITERAL.search(text):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())

        self.assertEqual(
            offenders,
            [],
            "AWS-key-shaped literal stored in the tree; secret scanning will alert on it. "
            f"Import the sample value from `{FIXTURE_MODULE}` instead of writing it out.",
        )

    def test_the_scan_reaches_the_files_a_regression_would_land_in(self) -> None:
        """An empty offender list means nothing if the walk found nothing."""
        scanned = {path.relative_to(REPO_ROOT).as_posix() for path in _scanned_files()}
        for expected in (FIXTURE_MODULE, "tests/test_risky_action_confirmation.py", "src/system/metadata_safety.py"):
            self.assertIn(expected, scanned)

    def test_the_gate_pattern_still_matches_the_shape_it_guards(self) -> None:
        for value in (AWS_ACCESS_KEY_ID, AWS_TEMPORARY_ACCESS_KEY_ID):
            with self.subTest(value=value):
                self.assertTrue(AWS_ACCESS_KEY_LITERAL.fullmatch(value))

    def test_the_fixtures_keep_the_shape_the_production_detector_screens(self) -> None:
        for value in (AWS_ACCESS_KEY_ID, AWS_TEMPORARY_ACCESS_KEY_ID):
            with self.subTest(value=value):
                self.assertEqual(len(value), 20)
                self.assertTrue(is_secret_value_shaped(value))


if __name__ == "__main__":
    unittest.main()
