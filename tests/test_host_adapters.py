"""Real clone installers and catalog projection parity; no host execution claims."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from _cli_harness import run_cli

ROOT = Path(__file__).resolve().parents[1]
HOSTS = ("cursor", "claude", "codex", "opencode", "openclaw", "pi")


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


FOREIGN = "my-skill/SKILL.md"
# Source tampering both installers must refuse before any write. The layout
# cases are JSON a reader of the copied receipt accepts, so a row the installer
# skipped or invented would change which files that receipt claims to own.
TAMPERING = (
    "modified", "missing", "extra", "inventory_missing", "inventory_row_dropped",
    "row_six_space", "row_no_space", "row_top_level_four_space", "row_outside_files",
    "row_parent_escape", "row_dot_alias", "row_extra", "row_duplicate", "row_case_variant",
)


def tamper(case, clone, mutation):
    path = clone / "agent-skills/ulw-work/SKILL.md"
    inventory = clone / "agent-skills/.omh-agent-skills-manifest.json"
    text = inventory.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    first = next(index for index, line in enumerate(lines) if line.startswith('    "'))
    digest = "a" * 64

    def row(name):
        return next(line for line in lines if line.startswith(f'    "{name}": '))

    if mutation == "modified":
        path.write_bytes(path.read_bytes() + b"tampered")
        return
    if mutation == "missing":
        path.unlink()
        return
    if mutation == "extra":
        (path.parent / "extra.txt").write_bytes(b"extra")
        return
    if mutation == "inventory_missing":
        inventory.unlink()
        return
    if mutation == "inventory_row_dropped":
        text = text.replace(row("ulw-work/SKILL.md"), "")
    elif mutation == "row_six_space":
        text = text.replace(lines[first], lines[first] + f'      "{FOREIGN}": "{digest}",\n')
    elif mutation == "row_no_space":
        text = text.replace(lines[first], lines[first] + f'    "{FOREIGN}":"{digest}",\n')
    elif mutation == "row_top_level_four_space":
        moved = row("ulw-work/SKILL.md")
        text = text.replace(moved, "").replace('  "files": {\n', moved + '  "files": {\n')
    elif mutation == "row_outside_files":
        text = text.replace('  "schema_version"', f'  "{FOREIGN}": "{digest}",\n  "schema_version"')
    elif mutation == "row_parent_escape":
        readme = clone / "README.md"
        readme.write_bytes(b"outside the pack\n")
        target = row("omh-ask/SKILL.md")
        (clone / "agent-skills/omh-ask/SKILL.md").unlink()
        text = text.replace(target, f'    "../README.md": "{hashlib.sha256(readme.read_bytes()).hexdigest()}",\n')
    elif mutation == "row_dot_alias":
        text = text.replace('    "omh-ask/SKILL.md": ', '    "omh-ask/./SKILL.md": ')
    elif mutation == "row_extra":
        text = text.replace(lines[first], lines[first] + f'    "{FOREIGN}": "{digest}",\n')
    elif mutation == "row_duplicate":
        text = text.replace(lines[first], lines[first] * 2)
    elif mutation == "row_case_variant":
        variant = row("omh-ask/SKILL.md")
        text = text.replace(variant, variant.replace('"omh-ask/', '"OMH-ASK/') + variant)
    case.assertNotEqual(text, inventory.read_text(encoding="utf-8"), mutation)
    if mutation.startswith("row_"):
        json.loads(text)  # Still JSON: the receipt would be read, not rejected.
    inventory.write_bytes(text.encode("utf-8"))


class HostAdapterTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("sh"), "POSIX shell unavailable")
    def test_destination_types_are_preflighted_before_any_write(self):
        self._destination_type_conflicts("sh")

    @unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "PowerShell unavailable")
    def test_powershell_destination_types_are_preflighted_before_any_write(self):
        self._destination_type_conflicts(shutil.which("pwsh") or shutil.which("powershell"))

    def _destination_type_conflicts(self, shell):
        for host in HOSTS:
            for conflict in ("ancestor", "root", "nested_parent", "leaf", "receipt"):
                with self.subTest(host=host, conflict=conflict), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp).resolve()
                    target = root / (".claude/skills" if host == "claude" else ".agents/skills")
                    if conflict in ("ancestor", "root"):
                        block = target.parent if conflict == "ancestor" else target
                        block.parent.mkdir(parents=True, exist_ok=True)
                        block.write_bytes(b"USER PARENT FILE")
                    else:
                        for relative in ("omh-ask/SKILL.md", "omh-show-reel/SKILL.md", ".omh-agent-skills-manifest.json"):
                            path = target / relative
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(b"ORIGINAL MUST SURVIVE REFUSAL")
                        relative = {"nested_parent": "omh-show-reel/toolkit/scripts", "leaf": "omh-show-reel/toolkit/scripts/timeline.py", "receipt": ".omh-agent-skills-manifest.json"}[conflict]
                        block = target / relative
                        block.parent.mkdir(parents=True, exist_ok=True)
                        if conflict == "nested_parent":
                            block.write_bytes(b"USER PARENT FILE")
                        else:
                            if block.exists():
                                block.unlink()
                            block.mkdir()
                    (root / "foreign.txt").write_bytes(b"FOREIGN")
                    def snapshot():
                        return {p.relative_to(root).as_posix(): None if p.is_dir() else p.read_bytes() for p in root.rglob("*")}
                    before = snapshot()
                    command = [shell, str(ROOT / f".{host}/install.sh")] if shell == "sh" else [shell, "-NoProfile", "-NonInteractive", "-File", str(ROOT / f".{host}/install.ps1")]
                    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertEqual(snapshot(), before, result.stderr)

    def test_powershell_producer_checks_parent_types_before_copying(self):
        from omh.install.agent_skills_projection import agent_skill_files
        from omh.skills.host_adapter_render import host_adapter_files

        generated = host_adapter_files(agent_skill_files())
        for host in HOSTS:
            text = generated[f".{host}/install.ps1"]
            with self.subTest(host=host):
                self.assertIn("function Assert-DirectoryParents", text)
                self.assertIn("-PathType Container", text)
                self.assertLess(text.index("Assert-DirectoryParents $path $base"), text.index("[IO.Directory]::CreateDirectory"))
                self.assertEqual((ROOT / f".{host}/install.ps1").read_text(), text)

    def test_committed_adapter_byte_gate(self):
        from contextlib import chdir
        with chdir(ROOT):
            code, output, error = run_cli(["docs", "agent-skills", "--check"])
        self.assertEqual(code, 0, error + output)
        self.assertEqual(json.loads(output).get("adapter_file_count"), 18)
        for host in HOSTS:
            for name in ("install.sh", "install.ps1", "manifest.json"):
                self.assertTrue((ROOT / f".{host}" / name).is_file())

    @unittest.skipUnless(shutil.which("sh"), "POSIX shell unavailable")
    def test_install_equality(self):
        self._install_equality("sh")

    @unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "PowerShell unavailable")
    def test_powershell_install_equality(self):
        self._install_equality(shutil.which("pwsh") or shutil.which("powershell"))

    def _install_equality(self, shell):
        from omh.install.agent_skills_projection import MANIFEST_NAME, agent_skill_files, agent_skills_manifest
        expected = tree(ROOT / "agent-skills")
        skills = {p.split("/")[0] for p in expected if p != MANIFEST_NAME}
        expected[MANIFEST_NAME] = (json.dumps(agent_skills_manifest(agent_skill_files()), indent=2, sort_keys=True) + "\n").encode()
        for host in HOSTS:
            for user in (False, True):
                with self.subTest(host=host, user=user), tempfile.TemporaryDirectory(prefix="omh adapter ") as tmp:
                    root = Path(tmp).resolve()
                    home, repo = root / "home", root / "project"
                    home.mkdir()
                    repo.mkdir()
                    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
                    if shell == "sh":
                        command = [shell, str(ROOT / f".{host}/install.sh"), *(["--user"] if user else [])]
                    else:
                        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                   "-File", str(ROOT / f".{host}/install.ps1"), *(["-User"] if user else [])]
                    target = (home if user else repo) / (".claude/skills" if host == "claude" else ".agents/skills")
                    for _ in range(2):
                        result = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True, timeout=60)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(tree(target), expected)
                        self.assertEqual(set(result.stdout.splitlines()[1:]), skills)
                    self.assertFalse(((repo if user else home) / target.relative_to(home if user else repo)).exists())

    @unittest.skipUnless(shutil.which("sh"), "POSIX shell unavailable")
    def test_cli_script_tree_equality(self):
        from contextlib import chdir
        from unittest.mock import patch
        for host in HOSTS:
            for scope in ("repo", "user"):
                with self.subTest(host=host, scope=scope), tempfile.TemporaryDirectory(prefix="omh parity ") as tmp:
                    root = Path(tmp).resolve()
                    cli_repo, script_repo = root / "cli-project", root / "script-project"
                    cli_home, script_home = root / "cli-home", root / "script-home"
                    for path in (cli_repo, script_repo, cli_home, script_home):
                        path.mkdir()
                    relative = ".claude/skills" if host == "claude" else ".agents/skills"
                    cli_target = (cli_home if scope == "user" else cli_repo) / relative
                    script_target = (script_home if scope == "user" else script_repo) / relative
                    with chdir(cli_repo), patch.dict(os.environ, {"HOME": str(cli_home), "USERPROFILE": str(cli_home)}):
                        code, output, error = run_cli(["install", "--target", "agents", "--host", host,
                                                       "--scope", scope, "--json"])
                    self.assertEqual(code, 0, error + output)
                    result = subprocess.run(
                        ["sh", str(ROOT / f".{host}/install.sh"), *(["--user"] if scope == "user" else [])],
                        cwd=script_repo, env={**os.environ, "HOME": str(script_home), "USERPROFILE": str(script_home)},
                        capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    # Compare every installed byte, including the managed receipt.
                    before = tree(script_target)
                    self.assertEqual(before, tree(cli_target))
                    with chdir(script_repo), patch.dict(os.environ, {"HOME": str(script_home), "USERPROFILE": str(script_home)}):
                        code, output, error = run_cli(["install", "--target", "agents", "--host", host,
                                                       "--scope", scope, "--status", "--json"])
                    self.assertEqual(code, 0, error + output)
                    status = json.loads(output)
                    self.assertEqual(status["projection"], "fresh")
                    self.assertEqual(status["drift"], "clean")
                    self.assertEqual(status["target_dirs"], [str(script_target)])
                    self.assertEqual(tree(script_target), before)

    def test_legacy_absolute_receipt_refreshes_to_portable_receipt(self):
        from omh.install.agent_skills_projection import MANIFEST_NAME, agent_skills_status, install_agent_skills
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve() / ".agents/skills"
            install_agent_skills(target)
            receipt = target / MANIFEST_NAME
            expected = receipt.read_bytes()
            manifest = json.loads(expected)
            self.assertEqual(manifest["target_dirs"], ["."])
            manifest["target_dirs"] = [str(target)]
            receipt.write_text(json.dumps(manifest), encoding="utf-8")
            status = agent_skills_status(target)
            self.assertEqual(status["projection"], "stale")
            self.assertEqual(status["drift"], "clean")
            self.assertEqual(install_agent_skills(target)["projection"], "fresh")
            self.assertEqual(receipt.read_bytes(), expected)

    def test_inventory_digest_and_cli_lockstep(self):
        from contextlib import chdir
        import hashlib
        from unittest.mock import patch
        from omh.install.agent_skills_projection import MANIFEST_NAME
        from omh.skills.host_adapters import HOST_ADAPTERS, checksum_inventory, source_digest
        self.assertEqual(tuple(adapter.host for adapter in HOST_ADAPTERS), HOSTS)
        expected = tree(ROOT / "agent-skills")
        receipt = expected.pop(MANIFEST_NAME)
        hashes = {path: hashlib.sha256(content).hexdigest() for path, content in expected.items()}
        # The committed inventory lists exactly the pack's bytes, and its digest
        # keeps the pre-inventory definition so stored discovery receipts compare.
        self.assertEqual(json.loads(receipt)["files"], hashes)
        self.assertEqual(source_digest(hashes), hashlib.sha256(checksum_inventory(hashes).encode()).hexdigest())
        for host in HOSTS:
            manifest = json.loads((ROOT / f".{host}/manifest.json").read_bytes())
            # Nothing per-host moves when a skill body changes.
            self.assertEqual(manifest["inventory"], MANIFEST_NAME)
            self.assertFalse({"files", "source_digest", "digest_format"} & manifest.keys())
            for name in ("install.sh", "install.ps1", "manifest.json"):
                self.assertIsNone(re.search(rb"[0-9a-f]{64}", (ROOT / f".{host}" / name).read_bytes()), name)
            self.assertEqual(manifest["transform"], "copy-only")
            self.assertEqual(manifest["evidence"], "docs/AGENT-SKILLS.md#host-support-matrix")
            for scope in ("repo", "user"):
                with self.subTest(host=host, scope=scope), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp).resolve()
                    home, repo = root / "home", root / "project"
                    home.mkdir()
                    repo.mkdir()
                    with chdir(repo), patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
                        command = ["install", "--target", "agents", "--host", host, "--json"]
                        if scope == "user":
                            command += ["--scope", scope]
                        target = (home if scope == "user" else repo) / manifest["targets"][scope]
                        for flag in ("--status", "--dry-run"):
                            code, output, error = run_cli(command + [flag])
                            self.assertEqual(code, 0, error)
                            self.assertEqual(json.loads(output)["projection"], "missing")
                            self.assertFalse(target.exists())
                        for _ in range(2):
                            code, output, error = run_cli(command)
                            self.assertEqual(code, 0, error)
                            payload = json.loads(output)
                            self.assertEqual(payload["target_dirs"], [str(target)])
                            self.assertEqual(payload["host_adapter"], manifest)
                            self.assertEqual(payload["projection"], "fresh")
                            installed = tree(target)
                            self.assertEqual(installed.pop(MANIFEST_NAME), receipt)
                            self.assertEqual(installed, expected)
                        self.assertFalse((home / ".hermes").exists())
                        self.assertFalse((home / ".omh").exists())
        self.assertNotEqual(run_cli(["install", "--host", "cursor"])[0], 0)
        with self.assertRaises(SystemExit) as rejected:
            run_cli(["install", "--target", "agents", "--host", "unknown"])
        self.assertEqual(rejected.exception.code, 2)

    def test_gate_catches_every_adapter_missing_stale_and_crlf_preserves_claude(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            custom = root / ".claude/skills/triage-sweep/SKILL.md"
            custom.parent.mkdir(parents=True)
            custom.write_bytes(b"custom skill\n")
            command = ["docs", "agent-skills", "--output", str(root / "agent-skills")]
            self.assertEqual(run_cli(command)[0], 0)
            self.assertEqual(custom.read_bytes(), b"custom skill\n")
            self.assertEqual(run_cli(command + ["--check"])[0], 0)
            for host in HOSTS:
                for name in ("install.sh", "install.ps1", "manifest.json"):
                    relative = f".{host}/{name}"
                    path = root / relative
                    original = path.read_bytes()
                    with self.subTest(path=relative):
                        for content in (None, b"stale", original.replace(b"\n", b"\r\n")):
                            if content is None:
                                path.unlink()
                            else:
                                path.write_bytes(content)
                            code, output, error = run_cli(command + ["--check"])
                            self.assertEqual(code, 1, error)
                            key = "adapter_missing" if content is None else "adapter_stale"
                            self.assertEqual(json.loads(output)[key], [relative])
                        path.write_bytes(original)
            inventory = root / "agent-skills/.omh-agent-skills-manifest.json"
            original = inventory.read_bytes()
            for content in (None, original.replace(b"\n", b"\r\n")):
                with self.subTest(path="inventory", content=content is None):
                    if content is None:
                        inventory.unlink()
                    else:
                        inventory.write_bytes(content)
                    code, output, error = run_cli(command + ["--check"])
                    self.assertEqual(code, 1, error)
                    key = "missing" if content is None else "stale"
                    self.assertEqual(json.loads(output)[key], [inventory.name])
            self.assertEqual(run_cli(command)[0], 0)
            self.assertEqual(run_cli(command + ["--check"])[0], 0)
            self.assertEqual(custom.read_bytes(), b"custom skill\n")

    def test_git_attributes_pin_all_generated_bytes(self):
        paths = [f".{host}/{name}" for host in HOSTS for name in ("install.sh", "install.ps1", "manifest.json")]
        paths.append("agent-skills/.omh-agent-skills-manifest.json")
        result = subprocess.run(["git", "-c", "core.autocrlf=true", "check-attr", "eol", "--", *paths],
                                cwd=ROOT, text=True, capture_output=True, check=True, timeout=10)
        self.assertEqual(result.stdout.splitlines(), [f"{path}: eol: lf" for path in paths])

    @unittest.skipUnless(shutil.which("sh"), "POSIX shell unavailable")
    def test_source_tampering_fails_before_writes(self):
        self._tampering_fails_before_writes(HOSTS, lambda clone, host: ["sh", str(clone / f".{host}/install.sh")])

    @unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "PowerShell unavailable")
    def test_powershell_source_tampering_fails_before_writes(self):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        self._tampering_fails_before_writes(("claude", "cursor"), lambda clone, host: [
            shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(clone / f".{host}/install.ps1")])

    def _tampering_fails_before_writes(self, hosts, command):
        for host in hosts:
            for mutation in TAMPERING:
                with self.subTest(host=host, mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp).resolve()
                    clone, repo = root / "clone", root / "project"
                    repo.mkdir()
                    shutil.copytree(ROOT / "agent-skills", clone / "agent-skills")
                    shutil.copytree(ROOT / f".{host}", clone / f".{host}", ignore=shutil.ignore_patterns("skills"))
                    tamper(self, clone, mutation)
                    result = subprocess.run(command(clone, host), cwd=repo,
                                            capture_output=True, text=True, timeout=60)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertTrue(result.stderr)
                    self.assertEqual(list(repo.iterdir()), [])

    @unittest.skipUnless(os.name != "nt" and shutil.which("sh"), "POSIX symlink/hash fallback test")
    def test_no_python_or_git_hash_fallback_and_preservation(self):
        from omh.install.agent_skills_projection import MANIFEST_NAME, agent_skill_files, agent_skills_manifest
        expected = tree(ROOT / "agent-skills")
        expected[MANIFEST_NAME] = (json.dumps(agent_skills_manifest(agent_skill_files()), indent=2, sort_keys=True) + "\n").encode()
        for hasher in ("sha256sum", "shasum"):
            if not shutil.which(hasher):
                continue  # Only the installed platform checksum utilities can be exercised.
            with self.subTest(hasher=hasher), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                binaries, repo = root / "bin", root / "project"
                binaries.mkdir()
                repo.mkdir()
                for name in (hasher, "dirname", "find", "sort", "mkdir", "cp", "sed"):
                    (binaries / name).symlink_to(shutil.which(name))
                custom = repo / ".claude/skills/triage-sweep/SKILL.md"
                custom.parent.mkdir(parents=True)
                custom.write_bytes(b"custom")
                env = {**os.environ, "PATH": str(binaries)}
                result = subprocess.run([shutil.which("sh"), str(ROOT / ".claude/install.sh")], cwd=repo,
                                        env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(custom.read_bytes(), b"custom")
                installed = tree(custom.parents[1])
                installed.pop("triage-sweep/SKILL.md")
                self.assertEqual(installed, expected)

    @unittest.skipUnless(os.name != "nt" and shutil.which("sh"), "POSIX symlink test")
    def test_symlink_destinations_and_invalid_flags_fail_before_writes(self):
        for relative in (".agents", ".agents/skills/ulw-work", ".agents/skills/ulw-work/SKILL.md",
                         ".agents/skills/.omh-agent-skills-manifest.json"):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                repo, outside = root / "project", root / "outside"
                repo.mkdir()
                outside.mkdir()
                link = repo / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(outside)
                result = subprocess.run(["sh", str(ROOT / ".cursor/install.sh")], cwd=repo,
                                        capture_output=True, text=True, timeout=60)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(list(outside.iterdir()), [])
                self.assertEqual(list(repo.rglob("SKILL.md")), [link] if relative.endswith("SKILL.md") else [])
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(["sh", str(ROOT / ".cursor/install.sh"), "--invalid"], cwd=tmp,
                                    capture_output=True, text=True, timeout=60)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
