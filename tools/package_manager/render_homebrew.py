#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# uv run tools/package_manager/render_homebrew.py --version X.Y.Z --wheel dist/*.whl --output dist/homebrew/omh.rb

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.package_manager.metadata import (  # noqa: E402
    DistributionError,
    VERSION_PATTERN,
    inspect_wheel,
    prepare_distribution_output,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = PROJECT_ROOT / "packaging" / "homebrew" / "omh.rb.tmpl"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _production_identity(version: str, url: str, sha256: str) -> tuple[str, str]:
    expected_url = (
        "https://github.com/rlaope/oh-my-hermes/releases/download/"
        f"v{version}/oh_my_hermes-{version}-py3-none-any.whl"
    )
    parsed = urlsplit(url)
    if parsed.query or parsed.fragment or url != expected_url:
        raise DistributionError(
            "Homebrew URL must be the exact versioned GitHub release wheel"
        )
    if not SHA256_PATTERN.fullmatch(sha256):
        raise DistributionError("Homebrew SHA-256 must be 64 lowercase hex digits")
    return url, sha256


def _local_identity(version: str, wheel: Path) -> tuple[str, str]:
    identity = inspect_wheel(wheel)
    if identity.version != version:
        raise DistributionError("Homebrew version does not match the local wheel")
    try:
        url = wheel.resolve(strict=True).as_uri()
    except (OSError, ValueError) as exc:
        raise DistributionError("could not resolve the local Homebrew wheel") from exc
    return url, identity.sha256


def render_formula(
    *,
    version: str,
    output: Path,
    wheel: Path | None,
    url: str | None,
    sha256: str | None,
) -> Path:
    """Render one validated Homebrew formula."""

    if not VERSION_PATTERN.fullmatch(version):
        raise DistributionError("Homebrew version must use X.Y.Z")
    if wheel is not None:
        if url is not None or sha256 is not None:
            raise DistributionError("local wheel mode cannot include URL or SHA-256")
        artifact_url, artifact_sha256 = _local_identity(version, wheel)
    else:
        if url is None or sha256 is None:
            raise DistributionError("production mode requires URL and SHA-256")
        artifact_url, artifact_sha256 = _production_identity(version, url, sha256)

    try:
        rendered = TEMPLATE.read_text()
    except OSError as exc:
        raise DistributionError("could not read the Homebrew formula template") from exc
    replacements = {
        "__OMH_URL__": artifact_url,
        "__OMH_VERSION__": version,
        "__OMH_SHA256__": artifact_sha256,
    }
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    if "__OMH_" in rendered:
        raise DistributionError("Homebrew formula has an unresolved token")

    output = prepare_distribution_output(output)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=output.parent,
            prefix=f".{output.name}.",
            delete=False,
        ) as temporary:
            temporary.write(rendered)
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o644)
        temporary_path.replace(output)
    except OSError as exc:
        raise DistributionError("could not write the Homebrew formula") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render the OMH Homebrew formula.")
    parser.add_argument("--version", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--wheel", type=Path)
    source.add_argument("--url")
    parser.add_argument("--sha256")
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Homebrew renderer boundary."""

    args = _parser().parse_args(argv)
    try:
        output = render_formula(
            version=args.version,
            output=args.output,
            wheel=args.wheel,
            url=args.url,
            sha256=args.sha256,
        )
    except DistributionError as exc:
        print(f"omh distribution: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
