#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# uv run tools/package_manager/metadata.py --wheel dist/*.whl --json

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from email.parser import BytesParser
from email.policy import compat32
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tomllib
from zipfile import BadZipFile, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
VERSION_SOURCE_PATTERN = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)
EXPECTED_NAME = "oh-my-hermes"
EXPECTED_REQUIRES_PYTHON = ">=3.11"


@dataclass(frozen=True, slots=True)
class DistributionError(ValueError):
    """A bounded package-identity refusal."""

    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class WheelIdentity:
    """Verified identity of one OMH wheel."""

    name: str
    version: str
    requires_python: str
    wheel: str
    sha256: str


def prepare_distribution_output(output: Path) -> Path:
    """Create a lexical output parent without following a direct link."""

    output = Path(os.path.abspath(output))
    current = output
    missing: list[Path] = []
    while True:
        try:
            status = current.lstat()
        except FileNotFoundError:
            if current == output:
                current = current.parent
                continue
            missing.append(current)
            current = current.parent
            continue
        except OSError as exc:
            raise DistributionError("could not inspect distribution output") from exc
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        is_reparse = bool(
            getattr(status, "st_file_attributes", 0) & reparse_attribute
        )
        if stat.S_ISLNK(status.st_mode) or is_reparse:
            raise DistributionError(
                "distribution output path must not contain a symbolic link"
            )
        if current == output:
            break
        if not stat.S_ISDIR(status.st_mode):
            raise DistributionError(
                "distribution output parent must be a directory"
            )
        for directory in reversed(missing):
            try:
                directory.mkdir()
            except OSError as exc:
                raise DistributionError(
                    "could not create distribution output parent"
                ) from exc
        break
    return output


def canonical_version(project_root: Path = PROJECT_ROOT) -> str:
    """Return the synchronized project version."""

    pyproject = project_root / "pyproject.toml"
    version_source = project_root / "src" / "omh" / "version.py"
    try:
        project_data = tomllib.loads(pyproject.read_text())
        pyproject_version = project_data["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise DistributionError("pyproject.toml has no valid project version") from exc
    if not isinstance(pyproject_version, str) or not VERSION_PATTERN.fullmatch(
        pyproject_version
    ):
        raise DistributionError("project version must use X.Y.Z")

    try:
        source_match = VERSION_SOURCE_PATTERN.search(version_source.read_text())
    except OSError as exc:
        raise DistributionError("could not read src/omh/version.py") from exc
    if source_match is None:
        raise DistributionError("src/omh/version.py has no literal __version__")
    if source_match.group(1) != pyproject_version:
        raise DistributionError(
            "pyproject.toml and src/omh/version.py versions do not match"
        )
    return pyproject_version


def version_order(existing: str, candidate: str) -> int:
    """Compare two canonical release versions."""

    if not VERSION_PATTERN.fullmatch(existing) or not VERSION_PATTERN.fullmatch(
        candidate
    ):
        raise DistributionError("release versions must use X.Y.Z")
    existing_parts = tuple(map(int, existing.split(".")))
    candidate_parts = tuple(map(int, candidate.split(".")))
    return (existing_parts > candidate_parts) - (
        existing_parts < candidate_parts
    )


def sha256_path(path: Path) -> str:
    """Hash a file without loading the entire artifact into memory."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DistributionError(f"could not read wheel: {path.name}") from exc
    return digest.hexdigest()


def inspect_wheel(
    wheel: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> WheelIdentity:
    """Parse and verify the exact pure-Python OMH wheel."""

    version = canonical_version(project_root)
    expected_filename = f"oh_my_hermes-{version}-py3-none-any.whl"
    if wheel.name != expected_filename:
        raise DistributionError(
            f"wheel filename must be {expected_filename}, got {wheel.name}"
        )

    try:
        with ZipFile(wheel) as archive:
            metadata_names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                raise DistributionError("wheel must contain one METADATA file")
            message = BytesParser(policy=compat32).parsebytes(
                archive.read(metadata_names[0])
            )
    except (BadZipFile, KeyError, OSError) as exc:
        raise DistributionError("wheel is not a readable OMH artifact") from exc

    name = str(message.get("Name", ""))
    wheel_version = str(message.get("Version", ""))
    requires_python = str(message.get("Requires-Python", ""))
    if name != EXPECTED_NAME:
        raise DistributionError(f"wheel project name must be {EXPECTED_NAME}")
    if wheel_version != version:
        raise DistributionError("wheel version does not match the project version")
    if requires_python != EXPECTED_REQUIRES_PYTHON:
        raise DistributionError(
            f"wheel Requires-Python must be {EXPECTED_REQUIRES_PYTHON}"
        )
    return WheelIdentity(
        name=name,
        version=version,
        requires_python=requires_python,
        wheel=wheel.name,
        sha256=sha256_path(wheel),
    )


def npm_owner_names(raw: str) -> tuple[str, ...]:
    """Parse the JSON and plain-text formats emitted by ``npm owner ls``."""

    text = raw.strip()
    if not text:
        raise DistributionError("npm owner output is empty")

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        names = [
            line.split(maxsplit=1)[0]
            for line in text.splitlines()
            if line.strip()
        ]
    else:
        if isinstance(value, dict):
            names = [name for name in value if isinstance(name, str)]
        elif isinstance(value, list):
            names = []
            for item in value:
                if isinstance(item, str):
                    names.append(item)
                elif (
                    isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                ):
                    names.append(item["name"])
                else:
                    raise DistributionError(
                        "npm owner JSON contains an unsupported entry"
                    )
        else:
            raise DistributionError("npm owner output has an unsupported shape")

    normalized = tuple(dict.fromkeys(name.strip() for name in names if name.strip()))
    if not normalized:
        raise DistributionError("npm owner output contains no owners")
    return normalized


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify OMH distribution identity.")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--version", action="store_true")
    parser.add_argument(
        "--compare-versions",
        nargs=2,
        metavar=("EXISTING", "CANDIDATE"),
    )
    parser.add_argument("--require-npm-owner")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the metadata verifier CLI."""

    args = _parser().parse_args(argv)
    try:
        if args.version:
            print(canonical_version())
            return 0
        if args.compare_versions is not None:
            print(version_order(*args.compare_versions))
            return 0
        if args.require_npm_owner is not None:
            owners = npm_owner_names(sys.stdin.read())
            if args.require_npm_owner not in owners:
                raise DistributionError(
                    f"required npm owner is missing: {args.require_npm_owner}"
                )
            return 0
        if args.wheel is None:
            raise DistributionError(
                "--wheel is required unless a version operation is used"
            )
        identity = inspect_wheel(args.wheel)
    except DistributionError as exc:
        print(f"omh distribution: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(asdict(identity), sort_keys=True))
    else:
        print(identity.wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
