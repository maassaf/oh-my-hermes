"""Maintainer-only checked tap regeneration. Never executes optional companion data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from omh.hashutil import sha256_file
from omh.local_store import atomic_write_text
from omh.skills.catalog import omh_skill_display_name
from omh.skills.packaging import builtin_skill_templates, builtin_skill_reference_templates
from omh.skills.support_files import builtin_skill_support_templates, builtin_support_owners
from omh.skills.show_reel_support import provenance_manifest

ROOT = Path(__file__).resolve().parents[1]


def tap_files():
    bodies = {f"{omh_skill_display_name(t.name)}/SKILL.md": t.content for t in builtin_skill_templates()}
    bodies.update({f"{omh_skill_display_name(t.skill_name)}/{t.relative_path}": t.content for t in [*builtin_skill_reference_templates(), *builtin_skill_support_templates()]})
    return bodies


def checked(root, relative):
    if "\\" in relative or any(p in ("", ".", "..") for p in relative.split("/")) or Path(relative).is_absolute():
        raise ValueError("unsafe generated path")
    root = root.absolute()
    target = root / relative
    # The root's own parent and every component below it; above is the
    # maintainer's own layout (macOS reaches every temp dir through `/var`).
    for component in (root.parent, root, *(root / parent for parent in Path(relative).parents), target):
        if component.is_symlink():
            raise ValueError(f"generated output refuses symlink: {component}")
        if component.exists() and component != target and not component.is_dir():
            raise ValueError(f"generated parent is not a directory: {component}")
    if target.exists() and not target.is_file():
        raise ValueError(f"generated destination is not a file: {target}")
    return target


def materialize(root, *, write=False):
    expected = tap_files()
    patterns = ("*/SKILL.md", "*/references/*.md", *(f"{omh_skill_display_name(owner)}/toolkit/**/*" for owner in sorted(builtin_support_owners())))
    actual = {p.relative_to(root).as_posix() for pattern in patterns for p in root.glob(pattern) if p.is_file() or p.is_symlink()}
    missing = sorted(set(expected)-actual)
    extra = sorted(actual-set(expected))
    stale = sorted(p for p in actual & set(expected) if checked(root, p).read_bytes() != expected[p].encode("utf-8"))
    # Every path, including ones already correct, is preflighted before the first write.
    for relative in expected:
        checked(root, relative)
    retired = []
    previous = {}
    for owner in builtin_support_owners():
        prefix = omh_skill_display_name(owner) + "/toolkit/"
        provenance = checked(root, prefix + "upstream-manifest.json")
        document = json.loads(provenance.read_text(encoding="utf-8")) if provenance.is_file() else {}
        previous.update({prefix + path: entry for path, entry in document.get("files", {}).items()})
    for relative in extra:
        entry = previous.get(relative)
        target = checked(root, relative)
        if not isinstance(entry, dict) or sha256_file(target) != entry.get("local_sha256"):
            if write:
                raise ValueError(f"unowned/edited extra generated file preserved: {relative}")
        else:
            retired.append(target)
    if write:
        for relative, content in expected.items():
            atomic_write_text(checked(root, relative), content)
        for target in retired:
            target.unlink()
        return materialize(root, write=False)
    return {"ok": not missing and not extra and not stale, "expected": len(expected), "checked": len(actual), "missing": missing, "stale": stale, "extra": extra}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT / "skills")
    parser.add_argument("--refresh-provenance", action="store_true")
    args = parser.parse_args()
    if args.refresh_provenance:
        if not args.write:
            parser.error("--refresh-provenance requires --write after source audit")
        atomic_write_text(ROOT / "src/skills/show_reel_toolkit/upstream-manifest.json", json.dumps(provenance_manifest(), ensure_ascii=False, indent=2, sort_keys=True)+"\n")
        career_root = ROOT / "src/skills/ai_job_search_toolkit"
        career_manifest = career_root / "upstream-manifest.json"
        career = json.loads(career_manifest.read_text(encoding="utf-8"))
        for relative, entry in career["files"].items():
            entry["local_sha256"] = sha256_file(checked(career_root, relative))
        atomic_write_text(career_manifest, json.dumps(career, ensure_ascii=False, indent=2, sort_keys=True)+"\n")
    result = materialize(args.root, write=args.write)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
