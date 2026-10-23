from __future__ import annotations

from pathlib import Path


_DOMAIN_STORE_RELATIVE = Path(".omh/memory/domain-intelligence")
_LOCK_NAME = ".store.lock"


def domain_store_snapshot(root: Path) -> dict[str, bytes]:
    """Return persisted domain data, excluding the OS synchronization sidecar."""
    store = root / _DOMAIN_STORE_RELATIVE
    if not store.exists():
        return {}
    lock_path = store / _LOCK_NAME
    return {
        path.relative_to(store).as_posix(): path.read_bytes()
        for path in sorted(store.rglob("*"))
        if path.is_file() and not path.is_symlink() and path != lock_path
    }


def repository_tree_snapshot(root: Path) -> dict[str, bytes]:
    """Return repository bytes without treating the domain lock as user data."""
    lock_path = root / _DOMAIN_STORE_RELATIVE / _LOCK_NAME
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink() and path != lock_path
    }
