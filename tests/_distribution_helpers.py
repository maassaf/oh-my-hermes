from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from tools.package_manager.metadata import canonical_version


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_VERSION = canonical_version()
NPM_PACKAGE_SOURCE = PROJECT_ROOT / "packaging" / "npm"
STAGE_NPM = PROJECT_ROOT / "tools" / "package_manager" / "stage_npm.py"
RENDER_HOMEBREW = (
    PROJECT_ROOT / "tools" / "package_manager" / "render_homebrew.py"
)


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd or PROJECT_ROOT,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def build_wheel(
    output_dir: Path,
    *,
    env: dict[str, str] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run(
        [
            require_tool("uv"),
            "build",
            "--wheel",
            "--out-dir",
            str(output_dir),
        ],
        cwd=PROJECT_ROOT,
        env=env,
    )
    wheels = list(output_dir.glob("oh_my_hermes-*.whl"))
    if len(wheels) != 1:
        raise AssertionError(
            f"expected one OMH wheel, found {wheels}: {result.stdout}{result.stderr}"
        )
    return wheels[0]


def stage_npm_package(wheel: Path, output_dir: Path) -> Path:
    if not STAGE_NPM.is_file():
        raise AssertionError(f"npm staging entry point is missing: {STAGE_NPM}")
    run(
        [
            sys.executable,
            str(STAGE_NPM),
            "--wheel",
            str(wheel),
            "--output",
            str(output_dir),
        ]
    )
    return output_dir


def pack_npm_package(
    package_dir: Path,
    output_dir: Path,
    *,
    env: dict[str, str] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run(
        command_for(
            Path(require_tool("npm")),
            "pack",
            "--json",
            "--pack-destination",
            str(output_dir),
        ),
        cwd=package_dir,
        env=env,
    )
    payload = json.loads(result.stdout)
    if isinstance(payload, list):
        packed = payload[0]
    else:
        packed = payload["oh-my-hermes"]
    return output_dir / packed["filename"]


def locate_global_omh(prefix: Path, *, platform: str = os.name) -> Path:
    names = (
        ("omh.cmd", "omh.exe", "omh")
        if platform == "nt"
        else ("omh", "omh.exe", "omh.cmd")
    )
    candidates = [
        directory / name
        for directory in (prefix / "bin", prefix)
        for name in names
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise AssertionError(f"global installation did not expose omh under {prefix}")


def command_for(
    binary: Path,
    *arguments: str,
    platform: str = os.name,
) -> list[str]:
    if platform == "nt" and binary.suffix.casefold() == ".cmd":
        return ["cmd", "/d", "/c", str(binary), *arguments]
    return [str(binary), *arguments]


def require_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise AssertionError(f"required distribution test tool is missing: {name}")
    return executable
