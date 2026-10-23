#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# python -I python_bridge.py run --site <cache>/site -- --help

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shutil
import stat
import sys
from zipfile import BadZipFile, ZipFile, ZipInfo


READY_SCHEMA = "omh_npm_cache/v2"
MAX_MEMBERS = 10_000
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
VERSION_SOURCE_PATTERN = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)


@dataclass(frozen=True, slots=True)
class BridgeError(ValueError):
    """A bounded launcher refusal."""

    reason: str

    def __str__(self) -> str:
        return self.reason


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BridgeError("could not read the bundled OMH wheel") from exc
    return digest.hexdigest()


def _is_reparse_point(status: os.stat_result) -> bool:
    attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(status, "st_file_attributes", 0) & attribute)


def _private_status(path: Path, *, directory: bool) -> os.stat_result:
    try:
        status = path.lstat()
    except OSError as exc:
        raise BridgeError("npm cache path is missing or unreadable") from exc
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        stat.S_ISLNK(status.st_mode)
        or _is_reparse_point(status)
        or not expected_type(status.st_mode)
    ):
        raise BridgeError("npm cache contains a non-regular path")
    if hasattr(os, "getuid") and status.st_uid != os.getuid():
        raise BridgeError("npm cache is not owned by the current user")
    if os.name != "nt" and stat.S_IMODE(status.st_mode) & 0o077:
        raise BridgeError("npm cache permissions are not private")
    return status


def _safe_parts(member: ZipInfo) -> tuple[str, ...]:
    name = member.filename
    path = PurePosixPath(name)
    mode = member.external_attr >> 16
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or stat.S_ISLNK(mode)
    ):
        raise BridgeError(f"unsafe wheel member: {name[:120]}")
    return path.parts


def _validated_members(
    archive: ZipFile,
) -> list[tuple[ZipInfo, tuple[str, ...]]]:
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise BridgeError("bundled wheel contains too many files")
    if sum(member.file_size for member in members) > MAX_UNPACKED_BYTES:
        raise BridgeError("bundled wheel is too large to extract")

    identities: dict[str, str] = {}
    validated: list[tuple[ZipInfo, tuple[str, ...]]] = []
    for member in members:
        parts = _safe_parts(member)
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise BridgeError(f"unsafe wheel member: {member.filename[:120]}")
        kind = "directory" if member.is_dir() else "file"
        identity = "/".join(parts).casefold()
        if identity in identities:
            raise BridgeError(f"duplicate wheel member: {member.filename[:120]}")
        for length in range(1, len(parts)):
            ancestor = "/".join(parts[:length]).casefold()
            if identities.get(ancestor) == "file":
                raise BridgeError(
                    f"wheel member collision: {member.filename[:120]}"
                )
        if kind == "file" and any(
            existing.startswith(f"{identity}/") for existing in identities
        ):
            raise BridgeError(f"wheel member collision: {member.filename[:120]}")
        identities[identity] = kind
        validated.append((member, parts))
    return validated


def _extract_wheel(wheel: Path, site: Path) -> None:
    try:
        with ZipFile(wheel) as archive:
            members = _validated_members(archive)
            site.mkdir(parents=True, mode=0o700, exist_ok=False)
            for member, parts in members:
                destination = site.joinpath(*parts)
                if member.is_dir():
                    destination.mkdir(parents=True, mode=0o700, exist_ok=True)
                    destination.chmod(0o700)
                    continue
                destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                destination.parent.chmod(0o700)
                with archive.open(member) as source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target)
                destination.chmod(0o600)
            for extracted in site.rglob("*"):
                status = extracted.lstat()
                if stat.S_ISLNK(status.st_mode):
                    raise BridgeError("npm cache contains a symbolic link")
                if extracted.is_dir():
                    extracted.chmod(0o700)
                elif extracted.is_file():
                    extracted.chmod(0o600)
                else:
                    raise BridgeError("npm cache contains a non-regular file")
    except BridgeError:
        raise
    except BadZipFile as exc:
        raise BridgeError("bundled OMH wheel is not a valid zip archive") from exc
    except (OSError, RuntimeError, NotImplementedError) as exc:
        raise BridgeError("could not extract the bundled OMH wheel") from exc


def _cache_tree_sha256(site: Path) -> str:
    digest = hashlib.sha256()
    try:
        _private_status(site, directory=True)
        members = sorted(
            site.rglob("*"),
            key=lambda path: path.relative_to(site).as_posix(),
        )
        for member in members:
            status = member.lstat()
            if stat.S_ISLNK(status.st_mode) or _is_reparse_point(status):
                raise BridgeError("npm cache contains a symbolic link")
            if hasattr(os, "getuid") and status.st_uid != os.getuid():
                raise BridgeError("npm cache is not owned by the current user")
            if os.name != "nt" and stat.S_IMODE(status.st_mode) & 0o077:
                relative = member.relative_to(site).as_posix()
                mode = stat.S_IMODE(status.st_mode)
                raise BridgeError(
                    f"npm cache permissions are not private: {relative} ({mode:o})"
                )
            if member.is_dir():
                continue
            if not member.is_file() or status.st_nlink != 1:
                raise BridgeError("npm cache contains a non-regular file")
            digest.update(member.relative_to(site).as_posix().encode())
            digest.update(b"\0")
            with member.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    except OSError as exc:
        raise BridgeError("could not verify npm cache contents") from exc
    return digest.hexdigest()


def _verify_extracted_site(site: Path, version: str) -> None:
    cli = site / "omh" / "cli" / "__init__.py"
    version_source = site / "omh" / "version.py"
    if not cli.is_file() or not version_source.is_file():
        raise BridgeError("bundled wheel does not contain the OMH CLI")
    try:
        version_match = VERSION_SOURCE_PATTERN.search(version_source.read_text())
    except OSError as exc:
        raise BridgeError("could not verify the extracted OMH version") from exc
    if version_match is None or version_match.group(1) != version:
        raise BridgeError("extracted OMH version does not match the npm package")


def bootstrap(
    wheel: Path,
    site: Path,
    version: str,
    expected_sha256: str,
    expected_tree_sha256: str,
) -> None:
    """Extract one verified wheel and write readiness last."""

    if not VERSION_PATTERN.fullmatch(version):
        raise BridgeError("npm package version is malformed")
    if not SHA256_PATTERN.fullmatch(expected_sha256):
        raise BridgeError("npm package wheel digest is malformed")
    if not SHA256_PATTERN.fullmatch(expected_tree_sha256):
        raise BridgeError("npm package cache digest is malformed")
    if _sha256(wheel) != expected_sha256:
        raise BridgeError("bundled OMH wheel digest does not match package metadata")
    _extract_wheel(wheel, site)
    _verify_extracted_site(site, version)
    if _cache_tree_sha256(site) != expected_tree_sha256:
        raise BridgeError("extracted OMH cache digest does not match the package")
    ready = {
        "schema_version": READY_SCHEMA,
        "version": version,
        "wheel_sha256": expected_sha256,
        "cache_tree_sha256": expected_tree_sha256,
    }
    try:
        ready_path = site.parent / "ready.json"
        with ready_path.open("x") as output:
            output.write(f"{json.dumps(ready, sort_keys=True)}\n")
        ready_path.chmod(0o600)
    except OSError as exc:
        raise BridgeError("could not write npm cache readiness") from exc


def run_cli(
    site: Path,
    version: str,
    expected_tree_sha256: str,
    arguments: list[str],
) -> int:
    """Execute the cached OMH module in an isolated interpreter."""

    sys.dont_write_bytecode = True
    ready_version, ready_tree_sha256 = _read_ready(site.parent / "ready.json")
    if ready_version != version or ready_tree_sha256 != expected_tree_sha256:
        raise BridgeError("npm cache readiness does not match the package")
    _verify_extracted_site(site, version)
    if _cache_tree_sha256(site) != expected_tree_sha256:
        raise BridgeError("npm cache contents do not match the package")
    script_dir = str(Path(__file__).resolve().parent)
    sys.path[:] = [str(site), *(entry for entry in sys.path if entry != script_dir)]
    sys.argv = ["omh", *arguments]
    runpy.run_module("omh.cli", run_name="__main__", alter_sys=True)
    return 0


def _read_ready(ready_path: Path) -> tuple[str, str]:
    try:
        _private_status(ready_path, directory=False)
        payload = json.loads(ready_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError("npm cache readiness is missing or malformed") from exc
    version = payload.get("version")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise BridgeError("npm cache readiness has no valid version")
    cache_tree_sha256 = payload.get("cache_tree_sha256")
    if (
        not isinstance(cache_tree_sha256, str)
        or not SHA256_PATTERN.fullmatch(cache_tree_sha256)
    ):
        raise BridgeError("npm cache readiness has no valid digest")
    return version, cache_tree_sha256


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the bundled OMH wheel.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = subparsers.add_parser("bootstrap")
    bootstrap_parser.add_argument("--wheel", required=True, type=Path)
    bootstrap_parser.add_argument("--site", required=True, type=Path)
    bootstrap_parser.add_argument("--version", required=True)
    bootstrap_parser.add_argument("--sha256", required=True)
    bootstrap_parser.add_argument("--tree-sha256", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--site", required=True, type=Path)
    run_parser.add_argument("--version", required=True)
    run_parser.add_argument("--tree-sha256", required=True)
    run_parser.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the bridge command boundary."""

    args = _parser().parse_args(argv)
    try:
        if args.command == "bootstrap":
            bootstrap(
                args.wheel,
                args.site,
                args.version,
                args.sha256,
                args.tree_sha256,
            )
            return 0
        arguments = args.arguments
        if arguments[:1] == ["--"]:
            arguments = arguments[1:]
        return run_cli(
            args.site,
            args.version,
            args.tree_sha256,
            arguments,
        )
    except BridgeError as exc:
        print(f"omh npm launcher: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
