#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# uv run tools/package_manager/stage_npm.py --wheel dist/*.whl --output dist/npm/oh-my-hermes

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
from zipfile import BadZipFile, ZipFile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.package_manager.metadata import (  # noqa: E402
    DistributionError,
    WheelIdentity,
    inspect_wheel,
    prepare_distribution_output,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SOURCE = PROJECT_ROOT / "packaging" / "npm"
TOKEN_FIELDS = {
    "__OMH_VERSION__": "version",
    "__OMH_WHEEL__": "wheel",
    "__OMH_SHA256__": "sha256",
}


def _is_reparse_point(status: stat.stat_result) -> bool:
    attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(status, "st_file_attributes", 0) & attribute)


def _regular_wheel(path: Path) -> stat.stat_result:
    try:
        status = path.lstat()
    except OSError as exc:
        raise DistributionError("could not inspect the npm wheel input") from exc
    if (
        stat.S_ISLNK(status.st_mode)
        or _is_reparse_point(status)
        or not stat.S_ISREG(status.st_mode)
    ):
        raise DistributionError("npm wheel input must be a regular file")
    return status


def _cache_tree_sha256(wheel: Path) -> str:
    digest = hashlib.sha256()
    try:
        with ZipFile(wheel) as archive:
            members = sorted(archive.infolist(), key=lambda item: item.filename)
            for member in members:
                if member.is_dir():
                    continue
                path = PurePosixPath(member.filename)
                mode = member.external_attr >> 16
                if (
                    not member.filename
                    or "\\" in member.filename
                    or path.is_absolute()
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or stat.S_ISLNK(mode)
                ):
                    raise DistributionError(
                        f"unsafe wheel member: {member.filename[:120]}"
                    )
                digest.update(path.as_posix().encode())
                digest.update(b"\0")
                with archive.open(member) as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                digest.update(b"\0")
    except (BadZipFile, OSError) as exc:
        raise DistributionError("could not hash the npm cache tree") from exc
    return digest.hexdigest()


def _render_manifest(
    identity: WheelIdentity,
    cache_tree_sha256: str,
) -> str:
    try:
        template = (PACKAGE_SOURCE / "package.template.json").read_text()
    except OSError as exc:
        raise DistributionError("could not read npm package template") from exc
    replacements = {
        token: getattr(identity, field)
        for token, field in TOKEN_FIELDS.items()
    }
    replacements["__OMH_CACHE_TREE_SHA256__"] = cache_tree_sha256
    for token, value in replacements.items():
        template = template.replace(token, value)
    if "__OMH_" in template:
        raise DistributionError("npm package template has an unresolved token")
    try:
        payload = json.loads(template)
    except json.JSONDecodeError as exc:
        raise DistributionError("npm package template is not valid JSON") from exc
    return f"{json.dumps(payload, indent=2, sort_keys=False)}\n"


def _copy_package_sources(stage: Path) -> None:
    for directory in ("bin", "lib"):
        source = PACKAGE_SOURCE / directory
        if not source.is_dir():
            raise DistributionError(f"npm package source is missing {directory}/")
        shutil.copytree(
            source,
            stage / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for source, target in (
        (PACKAGE_SOURCE / "README.md", stage / "README.md"),
        (PROJECT_ROOT / "LICENSE", stage / "LICENSE"),
    ):
        if not source.is_file():
            raise DistributionError(f"npm package source is missing {source.name}")
        shutil.copy2(source, target)


def stage_npm_package(wheel: Path, output: Path) -> Path:
    """Create one complete npm package tree from a verified wheel."""

    _regular_wheel(wheel)
    identity = inspect_wheel(wheel)
    output = prepare_distribution_output(output)
    if output.exists():
        raise DistributionError("npm package output already exists")
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.stage-",
            dir=output.parent,
        )
    )
    try:
        _copy_package_sources(stage)
        vendor = stage / "vendor"
        vendor.mkdir()
        vendored_wheel = vendor / wheel.name
        shutil.copy2(wheel, vendored_wheel)
        _regular_wheel(vendored_wheel)
        with vendored_wheel.open("rb") as artifact:
            copied_sha256 = hashlib.file_digest(artifact, "sha256").hexdigest()
        if copied_sha256 != identity.sha256:
            raise DistributionError("vendored npm wheel digest changed during staging")
        (stage / "package.json").write_text(
            _render_manifest(identity, _cache_tree_sha256(vendored_wheel))
        )
        launcher = stage / "bin" / "omh.js"
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        stage.replace(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage the OMH npm package.")
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run npm package staging."""

    args = _parser().parse_args(argv)
    try:
        output = stage_npm_package(args.wheel, args.output)
    except (DistributionError, OSError, shutil.Error) as exc:
        print(f"omh distribution: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
