from __future__ import annotations

from pathlib import Path

from omh.workflows.domain_intelligence_store_security import (
    MAX_DOMAIN_ARTIFACT_BYTES,
    MAX_DOMAIN_JSON_DEPTH,
    MAX_DOMAIN_JSON_NODES,
)
from omh.workflows.domain_intelligence_store_writer import read_managed_json_at


def repo(root: Path, *, linked_worktree: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".git"
    if linked_worktree:
        marker.write_text("gitdir: /elsewhere/.git/worktrees/test\n", encoding="utf-8")
    else:
        marker.mkdir()
    return root


def domain_store(root: Path, *, marker: str = "bound") -> Path:
    store = root / ".omh" / "memory" / "domain-intelligence"
    for name in ("profiles", "reviews", "history"):
        (store / name).mkdir(parents=True, mode=0o700)
    (store / ".store.lock").write_text("", encoding="utf-8")
    (store / "profiles" / "marker.json").write_text(
        f'{{"marker":"{marker}"}}', encoding="utf-8"
    )
    return store


def read_marker(directory_fd: int) -> dict[str, object] | None:
    return read_managed_json_at(
        directory_fd,
        "marker.json",
        max_bytes=MAX_DOMAIN_ARTIFACT_BYTES,
        max_depth=MAX_DOMAIN_JSON_DEPTH,
        max_nodes=MAX_DOMAIN_JSON_NODES,
    )
