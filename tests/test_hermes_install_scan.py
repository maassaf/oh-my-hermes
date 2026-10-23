r"""Every shipped skill passes the Hermes install scanner across the supported range.

`hermes skills install <tap>/skills/<name>` runs `tools/skills_guard.py` over
the quarantined skill directory. For a community source a `dangerous` verdict
is refused outright (`--force` does not override it) and a `caution` verdict
is blocked too (`INSTALL_POLICY`), so one high finding refuses the install
unless `--force` is given. Twice a plain sentence in a generated skill body
tripped a critical rule: a cryptocurrency name in the shortlist sidecar
(#2000), then `setgid`, a `> .claude/settings` shape, "do not tell the user",
a trigger spelling that cryptocurrency name and a literal `sudo` across skill
bodies and reference files (#2014). The drift gates cannot see this -- a
producer and its generated file move together -- so this runs the scanner
itself, vendored verbatim in `tests/_vendor`, over `skills/` and
`agent-skills/` exactly as Hermes runs it.

The scanner changes between Hermes releases and OMH installs on every release
from the `requires_hermes` floor up, so every scanner a release tag in that
range ships runs here, one vendored copy each. `RELEASE_SCANNER` maps each
release to its tag's file digest and its copy, and is tied to the floor in
`plugin.yaml` with no release skipped after it. Canary builds between
releases carried other scanner builds and are not gated.

- v2 (`hermes_skills_guard_v2`): Hermes 0.21.1 through 0.21.3, the floor. Its
  `context_exfil` rule is `(include|output|print|send|share)\s+(?:\w+\s+)*`
  followed by `context` or `conversation`, with no word boundary on either
  end, so it spans a whole line of plain words and fires inside a word:
  "A blueprint is prepared design context" reads as `print ... context`. Four
  lines in ulw-loop, omh-wiki and omh-routing scanned `caution` under v2 alone
  until they were reworded.
- v6 (`hermes_skills_guard_v6`): Hermes 0.21.4 and 0.21.5. Measured on
  2026-10-13 by importing each copy, every v6 rule id is also a v2, v8 or v10
  rule id, but a diff of `THREAT_PATTERNS` found five v6 patterns that match
  none of the three byte for byte. `dns_exfil` is narrower than v2's and
  `bash_reverse_shell` is equivalent to v8's. In `curl_pipe_shell`,
  `wget_pipe_shell` and `echo_pipe_exec` neither v2 nor v6 has a `\b` after
  the shell name (v8 added it), and v6 adds `zsh`, `ksh` and `dash` to v2's
  `sh` and `bash`. So a pipe into a word that starts with `sh` or `bash`, such
  as `| shellcheck`, is flagged by v2 and v6, and one into a word that starts
  with `zsh`, `ksh` or `dash`, such as `| dashboard`, by v6 alone;
  `V6_ONLY_PIPES` pins the second. Only `THREAT_PATTERNS` were diffed, and
  `scan_file` differs too (v2 has no link-destination masking or inert-path
  demotion, v6 decodes files as `utf-8` where v8 and v10 use `utf-8-sig`, v10
  demotes Markdown notes), so no version's verdict is inferred from another's.
- v8 (`hermes_skills_guard_v8`): Hermes 0.21.6.
- v10 (`hermes_skills_guard_v10`): Hermes 0.21.7 and upstream main. Upstream
  87a281e0c1 made a bare `setgid` and a `sudo` in Markdown informational
  (medium), so the pinned severities below differ per version.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

from _local_package import load_local_package
from _vendor import hermes_skills_guard_v2, hermes_skills_guard_v6, hermes_skills_guard_v8, hermes_skills_guard_v10

load_local_package()
from omh.plugin_bundle.omh.host_compat import declared_range, parse_range, version_satisfies

REPO = Path(__file__).resolve().parents[1]
SHIPPED_SKILL_ROOTS = (REPO / "skills", REPO / "agent-skills")
PLUGIN_MANIFEST = REPO / "src" / "plugin_bundle" / "omh" / "plugin.yaml"
SCANNERS = {
    "v2": hermes_skills_guard_v2,
    "v6": hermes_skills_guard_v6,
    "v8": hermes_skills_guard_v8,
    "v10": hermes_skills_guard_v10,
}

# Each Hermes release from the floor up: its tag, the sha256 of
# tools/skills_guard.py at that tag (read with `git show <tag>:tools/skills_guard.py`
# on 2026-10-13), and the vendored copy that stands for it. Each release ships its
# copy's exact bytes except those in NARROWER_THAN_ITS_COPY.
RELEASE_SCANNER = {
    "0.21.1": ("v2026.9.7", "007846383d775bbda8ac6ed9f6d9fedef4f4cebf052ad059e0cfcb15c9d3fb27", "v2"),
    "0.21.2": ("v2026.9.11", "007846383d775bbda8ac6ed9f6d9fedef4f4cebf052ad059e0cfcb15c9d3fb27", "v2"),
    "0.21.3": ("v2026.9.14", "0c193bdefd6dead5e3b8f5d03c51a4e77dfa3cc4a5afc6498f0625600295dabb", "v2"),
    "0.21.4": ("v2026.9.21", "ad9d3616c67fa2f0707dd8c6007d652a0cb44ad6ae51eb5d888ed57be2230bd1", "v6"),
    "0.21.5": ("v2026.9.24", "ad9d3616c67fa2f0707dd8c6007d652a0cb44ad6ae51eb5d888ed57be2230bd1", "v6"),
    "0.21.6": ("v0.21.6", "0c33d15095cff5f980d02e4f1bf056bf43f39789600604c885c673fb68d9cbb3", "v8"),
    "0.21.7": ("v0.21.7", "2b39ae73585709c51af62cbf8c3f23c5330ca1e297ac01453e9328996e281562", "v10"),
}

# A release whose bytes differ from the copy that stands for it, and why the copy
# still covers it: 0.21.3 (tag v2026.9.14) ships a second v2 build that only
# narrowed `dns_exfil`, so the 0.21.1 copy is the stricter of the two.
NARROWER_THAN_ITS_COPY = {
    "0.21.3": "v2 with a narrower dns_exfil; every other byte equals 0.21.1's",
}

# sha256 of each vendored copy below its OMH header (line endings read as LF),
# equal to tools/skills_guard.py at the tag the header names.
UPSTREAM_SHA256 = {
    "v2": "007846383d775bbda8ac6ed9f6d9fedef4f4cebf052ad059e0cfcb15c9d3fb27",
    "v6": "ad9d3616c67fa2f0707dd8c6007d652a0cb44ad6ae51eb5d888ed57be2230bd1",
    "v8": "0c33d15095cff5f980d02e4f1bf056bf43f39789600604c885c673fb68d9cbb3",
    "v10": "2b39ae73585709c51af62cbf8c3f23c5330ca1e297ac01453e9328996e281562",
}

# One sentence per rule that blocked, or under v2 would have blocked, an install.
BLOCKED_SENTENCES = {
    "setuid_setgid": "but on many Linux distributions `sg` is util-linux's setgid tool.",
    "deception_hide": "Do not tell the user an edit format will make an executor faster.",
    "other_agent_config_mod_shell": "project scope is `<dispatch cwd>/.claude/settings.local.json` with rules",
    "crypto_mining": "Strong routing signals: `monero gateway`, `connector readiness`",
    "sudo_usage": "Refused anywhere in argv: a shell or `env`, a forge CLI, `sudo`, network tools.",
    "context_exfil": "A blueprint is prepared design context. It is not evidence that a store was created.",
}

# The severity each version gives those sentences in a community SKILL.md. A
# rule absent from a row must not fire at all in that version.
EXPECTED_SEVERITY = {
    "v2": {
        "setuid_setgid": "critical",
        "deception_hide": "high",
        "other_agent_config_mod_shell": "critical",
        "crypto_mining": "critical",
        "sudo_usage": "high",
        "context_exfil": "high",
    },
    "v6": {
        "setuid_setgid": "critical",
        "deception_hide": "high",
        "other_agent_config_mod_shell": "critical",
        "crypto_mining": "critical",
        "sudo_usage": "high",
    },
    "v8": {
        "setuid_setgid": "critical",
        "deception_hide": "high",
        "other_agent_config_mod_shell": "critical",
        "crypto_mining": "critical",
        "sudo_usage": "high",
    },
    "v10": {
        "setuid_setgid": "medium",
        "deception_hide": "high",
        "other_agent_config_mod_shell": "critical",
        "crypto_mining": "critical",
        "sudo_usage": "medium",
    },
}

# A pipe into a word that starts with `dash`, `ksh` or `zsh`. v6 scores each
# line critical and no other version finds anything in them, so without the
# v6 copy a Hermes 0.21.4 or 0.21.5 install could be refused while this gate
# passed. (A word that starts with `sh` or `bash` would also fire under v2.)
V6_ONLY_PIPES = {
    "curl_pipe_shell": "Refresh the panel with `curl -s localhost:8080/status | dashboard-render`.",
    "wget_pipe_shell": "Fetch the profile with `wget -q localhost:8080/profile -O - | kshrc-tool`.",
    "echo_pipe_exec": "Check a snippet with `echo 'alias ll=ls' | zshrc-lint`.",
}


# `agent/skill_preprocessing.py` `_INLINE_SHELL_RE`, the same pattern at the
# floor tag v2026.9.7 and at upstream 63d0b94120. With `skills.inline_shell`
# on, `skill_view` replaces each match in a SKILL.md body with that command's
# output, so a body carrying the shape would run a command on load. A linked
# reference is served raw at both revisions (`_serve_skill_file` in
# `tools/skills_tool_plugin.py`), which is why the pin reads bodies only.
INLINE_SHELL = re.compile(r"!`([^`\n]+)`")


def _skill_dirs() -> list[Path]:
    return [path for root in SHIPPED_SKILL_ROOTS for path in sorted(root.iterdir()) if path.is_dir()]


def _blocking(result) -> list[str]:
    return [
        f"{finding.severity} {finding.pattern_id} {finding.file}:{finding.line}: {finding.match}"
        for finding in result.findings
        if finding.severity in ("critical", "high")
    ]


def _scan_probe(lines) -> dict:
    with TemporaryDirectory() as tmp:
        skill = Path(tmp) / "probe"
        skill.mkdir()
        (skill / "SKILL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {version: guard.scan_skill(skill, "community") for version, guard in SCANNERS.items()}


class HermesInstallScanTests(unittest.TestCase):
    def test_every_shipped_skill_scans_safe_as_a_community_install(self) -> None:
        problems: dict[str, list[str]] = {}
        for guard in SCANNERS.values():
            for skill_dir in _skill_dirs():
                result = guard.scan_skill(skill_dir, "community")
                blocking = _blocking(result)
                if result.verdict != "safe" or blocking:
                    key = f"{guard.SCANNER_VERSION} {skill_dir.relative_to(REPO)}"
                    problems[key] = [f"verdict={result.verdict}", *blocking]
        # assertEqual on dicts goes through assertDictEqual, whose repr and diff
        # truncation would hide the file:line findings this message exists for.
        report = "\n".join(f"{skill}: " + "; ".join(rows) for skill, rows in problems.items())
        self.assertFalse(
            problems,
            "a skill body trips a Hermes install-scanner rule; reword the producer in "
            "src/skills (never the generated file), regenerate, and re-derive the digests:\n" + report,
        )

    def test_no_shipped_skill_body_carries_an_inline_shell_command(self) -> None:
        # No OMH skill means to run anything when it loads, so the count is zero
        # across every shipped SKILL.md, the Agent Skills projection included.
        shipped = sorted(path / "SKILL.md" for path in _skill_dirs())
        self.assertTrue(shipped)
        hits = [
            f"{path.relative_to(REPO)}: {match.group(0)}"
            for path in shipped
            for match in INLINE_SHELL.finditer(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(hits, [], "a shipped skill body carries Hermes' inline-shell shape")
        self.assertIsNotNone(INLINE_SHELL.search("Today is !`date`."), "the pattern no longer matches its own shape")

    def test_every_release_from_the_floor_up_has_its_scanner_vendored(self) -> None:
        requirement = declared_range(PLUGIN_MANIFEST)
        floors = [bound for op, bound in parse_range(requirement) if op == ">="]
        self.assertEqual(len(floors), 1, requirement)
        major, minor, first_patch = floors[0]
        releases = sorted(tuple(int(part) for part in release.split(".")) for release in RELEASE_SCANNER)
        last_patch = releases[-1][2]
        # The patch series from the floor to the newest listed release, none skipped.
        self.assertEqual(releases, [(major, minor, patch) for patch in range(first_patch, last_patch + 1)])
        self.assertEqual([release for release in RELEASE_SCANNER if not version_satisfies(release, requirement)], [])
        for release, (tag, digest, copy) in RELEASE_SCANNER.items():
            with self.subTest(release=release, tag=tag):
                if release in NARROWER_THAN_ITS_COPY:
                    self.assertNotEqual(digest, UPSTREAM_SHA256[copy])
                else:
                    self.assertEqual(digest, UPSTREAM_SHA256[copy])
        self.assertLessEqual(set(NARROWER_THAN_ITS_COPY), set(RELEASE_SCANNER))
        self.assertEqual({copy for _, _, copy in RELEASE_SCANNER.values()}, set(SCANNERS))
        self.assertEqual(set(EXPECTED_SEVERITY), set(SCANNERS))
        self.assertEqual(set(UPSTREAM_SHA256), set(SCANNERS))

    def test_each_vendored_copy_is_the_upstream_file_byte_for_byte(self) -> None:
        for version, guard in SCANNERS.items():
            with self.subTest(version=version):
                data = Path(guard.__file__).read_bytes().replace(b"\r\n", b"\n")
                header, upstream = data.split(b"\n\n", 1)
                self.assertTrue(header.startswith(b"# ruff: noqa\n# Vendored verbatim"))
                self.assertEqual(hashlib.sha256(upstream).hexdigest(), UPSTREAM_SHA256[version])

    def test_a_caution_verdict_blocks_a_community_install_in_every_version(self) -> None:
        # Why the scan above refuses `caution`, not only `dangerous`.
        for version, guard in SCANNERS.items():
            with self.subTest(version=version):
                self.assertEqual(guard.INSTALL_POLICY["community"][guard.VERDICT_INDEX["caution"]], "block")

    def test_each_vendored_scanner_still_scores_the_sentences_that_blocked_installs(self) -> None:
        # Pins every vendored copy to the findings this gate exists for, so a
        # refresh that weakens or drops a rule cannot leave the scan above
        # passing vacuously.
        results = _scan_probe(BLOCKED_SENTENCES.values())
        for version, expected in EXPECTED_SEVERITY.items():
            with self.subTest(version=version):
                result = results[version]
                self.assertEqual(result.verdict, "dangerous")
                fired: dict[str, set[str]] = {}
                for finding in result.findings:
                    if finding.pattern_id in BLOCKED_SENTENCES:
                        fired.setdefault(finding.pattern_id, set()).add(finding.severity)
                self.assertEqual(fired, {rule: {severity} for rule, severity in expected.items()})

    def test_only_the_v6_slot_catches_a_pipe_into_a_word_that_starts_with_zsh_ksh_or_dash(self) -> None:
        results = _scan_probe(V6_ONLY_PIPES.values())
        for version, result in results.items():
            with self.subTest(version=version):
                expected = {(rule, "critical") for rule in V6_ONLY_PIPES} if version == "v6" else set()
                self.assertEqual({(f.pattern_id, f.severity) for f in result.findings}, expected)


if __name__ == "__main__":
    unittest.main()
