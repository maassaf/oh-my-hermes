"""Hermes edits to OMH-managed skills: redirected at the hook, kept by the installer, reported by doctor.

Hermes' background self-improvement review may patch, edit, or write files into
any skill it loaded, `skills.external_dirs` included; only delete and archive
are guarded upstream (`tools/skill_manager_guards.py`), and every write lands
in `<hermes_home>/skills/.curator_ledger.jsonl` (`tools/skill_ledger.py`).
Before this, the next `omh update` of a standalone install refused with "local
modifications detected", and a staged update left the edit in a generation
Hermes stopped loading.

Three layers are pinned here, each against the shape upstream really sends:

- `HookRedirectTests`: `pre_tool_call` blocks `skill_manage` edit, patch,
  write_file, remove_file and delete on a skill the install manifest records,
  and the block names where the lesson goes. A skill OMH did not install
  passes untouched, whatever its name looks like, and so does one a higher
  tier (the home's own skills, `skills.create_dir`) also holds.
- `InstallerKeepsHermesEditTests`: the installer keeps a skill only while the
  ledger accounts for what is on disk (a changed SKILL.md through the newest
  write to it, and only the files a row changed), carries it into a new
  generation the profile includes, and `--force` replaces the whole skill,
  saying whether the ledger holds a copy. A hand edit with no ledger row
  still refuses, and a removed skill is rendered again.
- `DoctorLedgerTests`: `omh doctor` reads the ledger without writing it.

Live firing in the background-review fork is NOT observed here: it needs a
Hermes host that ships that review, and the owner machine does not run one.
What is established is the code path on upstream origin/main 63d0b94120:
`agent/background_review.py::_run_review_fork` sets a thread tool whitelist
that admits the skills toolset, `agent/tool_executor.py::_pre_tool_block`
dispatches every call through `hermes_cli/plugins.py::_dispatch_pre_tool_call_hooks`,
and `_get_pre_tool_call_directive_details` checks that whitelist and then
calls `invoke_hook("pre_tool_call", tool_name=..., args=...)` for every tool
it admits -- `skill_manage` among them.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from _local_package import load_local_package

load_local_package()

from omh.commands import setup as setup_commands
from omh.commands.language import MESSAGES
from omh.core.errors import OmhError
from omh.hashutil import sha256_file
from omh.install import installer
from omh.install import manifest as manifest_module
from omh.install.config_adapter import memory_provider_selection, skill_create_dir_selection
from omh.install import curator_ledger as curator_ledger_module
from omh.install.curator_ledger import CURATOR_LEDGER_RELATIVE_PATH, curator_ledger_paths, read_ledger_writes
from omh.install.skill_registration import hermes_profile_dirs
from omh.install.installer import (
    HERMES_EDIT_RECORD_KEY,
    LEDGER_COPY_PARTIAL_KEY,
    PRUNE_SKIP_REASONS,
    RENDER_MARK_KEY,
    RENDER_MARKS_FLAG,
    install_skill_pack,
    reconcile_skill_profile,
    skill_install_relative_dir,
)
from omh.maintenance import doctor as doctor_module
from omh.maintenance.doctor import run_doctor
from omh.manifest import read_manifest, same_directory
from omh.paths import OmhPaths
from omh.plugin_bundle.omh import skill_ownership
from omh.plugin_bundle.omh.hooks import nudge_budget, session_attendance
from omh.plugin_bundle.omh.hooks.tool_hooks import pre_tool_call
from omh.skill_pack import builtin_skill_reference_templates, builtin_skill_templates
from omh.skills.catalog import canonical_skill_for_label, omh_skill_display_name
from omh.skills.catalog_types import OMH_MANAGED_SKILL_CLAUSE, OMH_SKILL_NAME_PREFIX, ULW_SKILL_NAME_PREFIX
from omh.skills.render import agent_skill_reference_templates, agent_skill_templates

SESSION = "20261013_101010_abc123"
# A core skill that ships reference files, so a supporting-file write has a
# real target.
SKILL = "oh-my-hermes"


def _ledger_row(path: Path, *, actor: str = "curator", action: str = "patch", before: str = "", ts: str = "") -> str:
    """One row the way `tools/skill_ledger.py::append_entry` writes it after `_delta`."""
    real = os.path.realpath(path)
    after = [{"path": real, "sha256": sha256_file(path)}] if path.exists() else []
    before_items = [{"path": real, "sha256": before}] if before else []
    return json.dumps(
        {
            "id": "a1b2c3d4e5f6",
            "ts": ts or "2026-10-13T01:02:03.000000+00:00",
            "actor": actor,
            "action": action,
            "skill": path.parent.name,
            "evidence": {},
            "before": before_items,
            "after": after,
        }
    )


def _change_row(path: Path, *, before_sha: str = "", after_sha: str = "", action: str, ts: str) -> str:
    """A row that changed one file from `before_sha` to `after_sha` ("" on either side: absent)."""
    real = os.path.realpath(path)
    return json.dumps(
        {
            "id": "c0ffee", "ts": ts, "actor": "curator", "action": action, "skill": path.parent.name, "evidence": {},
            "before": [{"path": real, "sha256": before_sha}] if before_sha else [],
            "after": [{"path": real, "sha256": after_sha}] if after_sha else [],
        }
    )


def _append_ledger(hermes_home: Path, *rows: str) -> Path:
    ledger = hermes_home / CURATOR_LEDGER_RELATIVE_PATH
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(row + "\n")
    return ledger


class _Home(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.paths = OmhPaths(omh_home=self.root / "omh", hermes_home=self.root / "hermes")
        self.paths.hermes_home.mkdir(parents=True)

    def skill_file(self, paths: OmhPaths | None = None, name: str = SKILL) -> Path:
        return (paths or self.paths).skills_dir / skill_install_relative_dir(name) / "SKILL.md"

    def hermes_patch(self, skill_file: Path, text: str = "\nlearned: check the lockfile first\n", **row) -> None:
        """Edit a managed file in place and ledger it, as a skill_manage patch does."""
        before = sha256_file(skill_file) if skill_file.exists() else ""
        with skill_file.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        _append_ledger(self.paths.hermes_home, _ledger_row(skill_file, before=before, **row))

    def edit_manifest(self, edit=None, *, legacy: bool = False) -> dict:
        """Rewrite the manifest; `legacy` makes it one written before render marks existed."""
        manifest = read_manifest(self.paths.manifest_path)
        if edit is not None:
            edit(manifest)
        if legacy:
            manifest.pop(RENDER_MARKS_FLAG, None)
            for record in manifest["skills"]:
                record.pop(RENDER_MARK_KEY, None)
        self.paths.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def own_skill(self, relative: str, *, content: str = "---\nname: mine\n---\nmy own skill\n") -> Path:
        directory = self.paths.skills_dir / relative
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(content, encoding="utf-8")
        (directory / "notes.md").write_text("my notes\n", encoding="utf-8")
        return directory

    def store_blob(self, path: Path) -> None:
        """Keep a file's content where Hermes keeps it: `.curator_backups/blobs/<sha256>`."""
        blob = self.paths.hermes_home / ".curator_backups" / "blobs" / sha256_file(path)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(path.read_bytes())


class HookRedirectTests(_Home):
    def setUp(self) -> None:
        super().setUp()
        session_attendance.reset_session_attendance()
        nudge_budget.reset_nudge_budget()
        self.addCleanup(session_attendance.reset_session_attendance)
        self.addCleanup(nudge_budget.reset_nudge_budget)
        install_skill_pack(self.paths, profile="core")
        self.label = self.skill_file().parent.name

    def call(self, args: dict, *, session: str = SESSION) -> dict | None:
        # `args`, not `tool_input`: the keyword Hermes passes.
        return pre_tool_call(
            tool_name="skill_manage",
            args=args,
            session_id=session,
            omh_home=str(self.paths.omh_home),
            hermes_home=str(self.paths.hermes_home),
        )

    def test_every_guarded_action_on_a_managed_skill_is_blocked(self) -> None:
        self.assertEqual(skill_ownership.GUARDED_ACTIONS, {"edit", "patch", "write_file", "remove_file", "delete"})
        for index, action in enumerate(sorted(skill_ownership.GUARDED_ACTIONS)):
            with self.subTest(action=action):
                directive = self.call(
                    {"operations": [{"action": action, "name": self.label, "file_path": "references/x.md"}]},
                    session=f"{SESSION}-{index}",
                )
                self.assertIsNotNone(directive)
                self.assertEqual(directive["action"], "block")
                self.assertIn(f"Do not retry this {action} on '{self.label}'", directive["message"])

    def test_the_block_names_where_the_lesson_goes(self) -> None:
        directive = self.call({"operations": [{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}]})
        message = directive["message"]
        # A differently named companion the fork can create with the one tool it has...
        self.assertIn("skill_manage action=create with a different name", message)
        # ...under a name without OMH's label prefix (`omh-routing` here).
        self.assertEqual(self.label, "omh-routing")
        self.assertIn("such as 'routing-notes', holding a short companion that names 'omh-routing'", message)
        self.assertNotIn(f"'{self.label}-notes'", message)
        self.assertIn("rather than a copy of it", message)
        # ...or the workflow-learning lane. A core install does not carry that
        # skill, so the lane is named without a skill_view call that would fail.
        self.assertIn("OMH's workflow-learning lane", message)
        self.assertNotIn("skill_view name=", message)

    def test_the_lane_pointer_names_the_installed_workflow_learning_skill(self) -> None:
        install_skill_pack(self.paths, profile="full")
        directive = self.call({"operations": [{"action": "edit", "name": self.label, "content": "x"}]})
        self.assertIn("(skill_view name='omh-workflow-learning')", directive["message"])

    def test_the_legacy_flat_shape_and_a_mixed_batch_are_read(self) -> None:
        self.assertEqual(self.call({"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"})["action"], "block")
        mixed = {
            "operations": [
                {"action": "create", "name": "my-notes", "content": "---\nname: my-notes\n---\n"},
                {"action": "write_file", "file_path": "references/a.md", "file_content": "x"},
            ],
            "name": self.label,
        }
        self.assertEqual(self.call(mixed, session=f"{SESSION}-mixed")["action"], "block")

    def test_a_skill_omh_did_not_install_passes_untouched(self) -> None:
        # A user's own skill, one wearing OMH's prefix, and OMH's canonical
        # name (Hermes resolves `oh-my-hermes` apart from the installed label).
        for index, name in enumerate(("my-deploy-notes", "omh-notes", SKILL)):
            with self.subTest(name=name):
                self.assertIsNone(
                    self.call(
                        {"operations": [{"action": "patch", "name": name, "old_string": "a", "new_string": "b"}]},
                        session=f"{SESSION}-pass-{index}",
                    )
                )

    def test_create_and_reads_are_not_this_guards_business(self) -> None:
        # `create` refuses a name that already resolves, so it cannot reach an OMH skill.
        self.assertIsNone(self.call({"operations": [{"action": "create", "name": self.label}]}, session=f"{SESSION}-own"))
        self.assertIsNone(
            pre_tool_call(
                tool_name="skill_view",
                args={"name": self.label},
                session_id=SESSION,
                omh_home=str(self.paths.omh_home),
                hermes_home=str(self.paths.hermes_home),
            )
        )

    def test_a_same_name_skill_in_the_hermes_home_takes_the_edit_and_passes(self) -> None:
        # `_find_skill` serves the profile's own skills directory first, so the
        # edit lands on that copy, which OMH does not own.
        local = self.paths.hermes_home / "skills" / "workflow" / self.label
        local.mkdir(parents=True)
        (local / "SKILL.md").write_text(f"---\nname: {self.label}\n---\n", encoding="utf-8")
        self.assertIsNone(self.call({"operations": [{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}]}))

    def write_config(self, text: str | bytes) -> None:
        config = self.paths.hermes_home / "config.yaml"
        if isinstance(text, bytes):
            config.write_bytes(text)
        else:
            config.write_text(text, encoding="utf-8")

    def patch_call(self, session: str) -> dict | None:
        return self.call(
            {"operations": [{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}]},
            session=session,
        )

    def test_a_same_name_skill_in_skills_create_dir_takes_the_edit_and_passes(self) -> None:
        # `_find_skill` searches create_dir after the profile's own directory
        # and before every external one, relative paths anchored at the home.
        self.write_config("model: x\nskills:\n  external_dirs:\n    - /elsewhere\n  create_dir: fleet-skills  # shared\n")
        copy = self.paths.hermes_home / "fleet-skills" / self.label
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_text(f"---\nname: {self.label}\n---\n", encoding="utf-8")
        self.assertIsNone(self.patch_call(f"{SESSION}-create-dir"))

    def test_a_create_dir_without_that_name_still_blocks(self) -> None:
        create_dir = self.root / "fleet-skills"
        (create_dir / "someone-elses-skill").mkdir(parents=True)
        (create_dir / "someone-elses-skill" / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
        # `as_posix()`: in YAML double quotes a Windows path's backslashes are
        # escapes, which Hermes' loader rejects or mangles and this reader refuses.
        self.write_config(f'skills:\n  create_dir: "{create_dir.as_posix()}"\n')
        self.assertEqual(self.patch_call(f"{SESSION}-create-dir-other")["action"], "block")
        # A deeper `create_dir` key is some other setting, not the tier.
        copy = self.root / "nested" / self.label
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_text(f"---\nname: {self.label}\n---\n", encoding="utf-8")
        self.write_config(f"skills:\n  hub:\n    create_dir: {self.root / 'nested'}\n")
        self.assertEqual(self.patch_call(f"{SESSION}-create-dir-deep")["action"], "block")

    def test_an_empty_skills_block_still_blocks(self) -> None:
        for index, text in enumerate(("skills: {}\n", "skills:\n  create_dir: ~\n", "skills:\n  create_dir: ''\n")):
            with self.subTest(text=text):
                self.write_config(text)
                self.assertEqual(self.patch_call(f"{SESSION}-empty-{index}")["action"], "block")

    def test_a_config_this_scan_cannot_read_lets_the_call_through(self) -> None:
        # Fail open: create_dir might hold the name, and a refusal aimed at a
        # copy OMH does not own is the worse error.
        for index, text in enumerate((b"skills:\n  create_dir: \xff\xfe\n", "skills: {create_dir: fleet-skills}\n")):
            with self.subTest(text=text):
                self.write_config(text)
                self.assertIsNone(self.patch_call(f"{SESSION}-unread-{index}"))

    def test_a_foreground_delete_is_refused_with_its_own_consequence(self) -> None:
        # Upstream guards a delete only inside the background-review fork.
        directive = self.call({"operations": [{"action": "delete", "name": self.label}]}, session=f"{SESSION}-del")
        self.assertEqual(directive["action"], "block")
        self.assertIn(
            "deleting it here does not last: the next update renders it again or reports it missing",
            directive["message"],
        )

    def test_operations_sent_as_a_json_string_are_read(self) -> None:
        # The hook sees the arguments before Hermes coerces them to the schema.
        encoded = json.dumps([{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}])
        self.assertEqual(self.call({"operations": encoded}, session=f"{SESSION}-json")["action"], "block")
        self.assertIsNone(self.call({"operations": "[not json"}, session=f"{SESSION}-badjson"))

    def test_the_block_says_where_a_created_skill_lands(self) -> None:
        self.assertIn("(it lands in this profile's own skills directory)", self.patch_call(f"{SESSION}-lands")["message"])
        create_dir = self.root / "fleet-skills"
        self.write_config(f"skills:\n  create_dir: {create_dir}\n")
        self.assertIn(f"(it lands in {create_dir})", self.patch_call(f"{SESSION}-lands-fleet")["message"])

    def test_a_create_dir_this_reader_cannot_decode_fails_open(self) -> None:
        # `\n` inside double quotes is an escape this reader does not decode;
        # guessing would resolve a directory Hermes does not use.
        self.write_config('skills:\n  create_dir: "C:\\new\\skills"\n')
        self.assertIsNone(self.patch_call(f"{SESSION}-escape"))

    def test_a_quoted_create_dir_with_a_comment_and_a_bom_still_resolves(self) -> None:
        copy = self.root / "fleet-skills" / self.label
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_text(f"---\nname: {self.label}\n---\n", encoding="utf-8")
        text = f'skills:\n  create_dir: "{(self.root / "fleet-skills").as_posix()}"  # shared\n'
        self.write_config(b"\xef\xbb\xbf" + text.encode("utf-8"))
        self.assertIsNone(self.patch_call(f"{SESSION}-quoted"))

    def test_a_stat_that_raises_fails_open_instead_of_escaping_the_hook(self) -> None:
        with mock.patch.object(Path, "is_file", side_effect=PermissionError(13, "Permission denied")):
            self.assertIsNone(
                skill_ownership.managed_skill_edit_directive(
                    tool_name="skill_manage",
                    tool_input={"operations": [{"action": "patch", "name": self.label}]},
                    omh_home=str(self.paths.omh_home),
                    hermes_home=str(self.paths.hermes_home),
                )
            )

    def test_no_readable_manifest_lets_the_call_through(self) -> None:
        manifest = self.paths.omh_home / "manifest.json"
        manifest.write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.call({"operations": [{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}]}))
        manifest.unlink()
        self.assertIsNone(
            self.call(
                {"operations": [{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}]},
                session=f"{SESSION}-gone",
            )
        )

    def test_a_manifest_record_whose_skill_is_gone_is_not_a_target(self) -> None:
        self.skill_file().unlink()
        self.assertIsNone(self.call({"operations": [{"action": "patch", "name": self.label, "old_string": "a", "new_string": "b"}]}))


class InstallerKeepsHermesEditTests(_Home):
    def test_a_hermes_edit_is_kept_and_reported_instead_of_refusing_the_update(self) -> None:
        install_skill_pack(self.paths, profile="core")
        skill_file = self.skill_file()
        self.hermes_patch(skill_file)
        edited = skill_file.read_bytes()

        result = install_skill_pack(self.paths, profile="core")

        self.assertEqual(skill_file.read_bytes(), edited)
        [item] = result["hermes_edited_skills"]
        self.assertEqual(item["name"], SKILL)
        self.assertEqual(item["disposition"], "kept")
        self.assertEqual((item["actor"], item["action"]), ("curator", "patch"))
        self.assertEqual(item["ts"], "2026-10-13T01:02:03.000000+00:00")
        record = next(record for record in read_manifest(self.paths.manifest_path)["skills"] if record["name"] == SKILL)
        self.assertEqual(record["sha256"], sha256_file(skill_file))
        self.assertEqual(record[HERMES_EDIT_RECORD_KEY]["actor"], "curator")

    def test_a_kept_skill_stays_kept_after_the_ledger_row_is_gone(self) -> None:
        install_skill_pack(self.paths, profile="core")
        skill_file = self.skill_file()
        self.hermes_patch(skill_file)
        install_skill_pack(self.paths, profile="core")
        (self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH).unlink()
        edited = skill_file.read_bytes()

        result = install_skill_pack(self.paths, profile="core")

        self.assertEqual(skill_file.read_bytes(), edited)
        self.assertEqual([item["name"] for item in result["hermes_edited_skills"]], [SKILL])

    def test_a_hand_edit_with_no_ledger_row_still_refuses(self) -> None:
        install_skill_pack(self.paths, profile="core")
        skill_file = self.skill_file()
        with skill_file.open("a", encoding="utf-8") as handle:
            handle.write("\nlocal edit\n")

        with self.assertRaisesRegex(OmhError, "local modifications detected"):
            install_skill_pack(self.paths, profile="core")

    def test_a_ledger_row_whose_content_is_no_longer_on_disk_is_history(self) -> None:
        install_skill_pack(self.paths, profile="core")
        skill_file = self.skill_file()
        rendered = skill_file.read_bytes()
        self.hermes_patch(skill_file)
        skill_file.write_bytes(rendered)
        # A rollback row restoring OMH's render is not an edit either.
        _append_ledger(self.paths.hermes_home, _ledger_row(skill_file, actor="user", action="rollback"))

        result = install_skill_pack(self.paths, profile="core")

        self.assertNotIn("hermes_edited_skills", result)

    def test_force_replaces_the_edit_and_says_so(self) -> None:
        install_skill_pack(self.paths, profile="core")
        skill_file = self.skill_file()
        rendered = skill_file.read_bytes()
        self.hermes_patch(skill_file)

        result = install_skill_pack(self.paths, profile="core", force=True)

        self.assertEqual(skill_file.read_bytes(), rendered)
        self.assertEqual([item["disposition"] for item in result["hermes_edited_skills"]], ["replaced"])
        # No blob stored here, so the report claims no copy.
        self.assertEqual(result["hermes_edited_skills"][0]["ledger_copy"], [])
        self.assertTrue(all(HERMES_EDIT_RECORD_KEY not in record for record in read_manifest(self.paths.manifest_path)["skills"]))
        # The old row now describes content that is gone, so nothing is kept next time.
        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))

    def test_a_row_listing_unchanged_files_does_not_count_them(self) -> None:
        # Hermes before 0.21.4 listed every file of the skill in a row, OMH's
        # own unchanged files included, with the same hash on both sides.
        install_skill_pack(self.paths, profile="core")
        skill_dir = self.skill_file().parent
        files = sorted(path for path in skill_dir.rglob("*") if path.is_file())
        before = [{"path": os.path.realpath(path), "sha256": sha256_file(path)} for path in files]
        with self.skill_file().open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\nlearned at the floor\n")
        after = [{"path": os.path.realpath(path), "sha256": sha256_file(path)} for path in files]
        row = {"id": "f", "ts": "2026-09-08T00:00:00+00:00", "actor": "curator", "action": "patch",
               "skill": skill_dir.name, "evidence": {}, "before": before, "after": after}
        _append_ledger(self.paths.hermes_home, json.dumps(row))

        self.assertEqual([item["disposition"] for item in install_skill_pack(self.paths, profile="core")["hermes_edited_skills"]], ["kept"])
        install_skill_pack(self.paths, profile="core", force=True)
        # OMH's re-rendered supporting files still match the row's after list,
        # and they were never changed by it, so they are not an edit.
        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))

    def test_a_hand_edit_to_skill_md_beside_a_ledgered_support_write_still_refuses(self) -> None:
        install_skill_pack(self.paths, profile="core")
        reference = next(template for template in builtin_skill_reference_templates() if template.skill_name == SKILL)
        self.hermes_patch(self.skill_file().parent / reference.relative_path)
        with self.skill_file().open("a", encoding="utf-8") as handle:
            handle.write("\nmy own edit\n")

        with self.assertRaisesRegex(OmhError, "local modifications detected"):
            install_skill_pack(self.paths, profile="core")

    def test_a_file_hermes_added_does_not_survive_force(self) -> None:
        install_skill_pack(self.paths, profile="core")
        added = self.skill_file().parent / "references" / "lessons.md"
        added.parent.mkdir(exist_ok=True)
        added.write_text("lesson\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(added, action="write_file"))
        self.assertEqual([item["name"] for item in install_skill_pack(self.paths, profile="core")["hermes_edited_skills"]], [SKILL])

        install_skill_pack(self.paths, profile="core", force=True)

        self.assertFalse(added.exists())
        self.assertTrue(self.skill_file().is_file())
        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))

    def test_the_replaced_report_claims_a_ledger_copy_only_when_one_is_stored(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        self.store_blob(self.skill_file())

        stored = install_skill_pack(self.paths, profile="core", dry_run=True, force=True)["hermes_edited_skills"][0]
        self.assertEqual(stored["ledger_copy"], ["SKILL.md"])
        self.assertIn("still holds a copy of SKILL.md", self._summary({"hermes_edited_skills": [stored]}))

        # Kept through the manifest record after the row and its blob are gone:
        # nothing in the ledger can bring the edit back.
        install_skill_pack(self.paths, profile="core")
        (self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH).unlink()
        for blob in (self.paths.hermes_home / ".curator_backups" / "blobs").iterdir():
            blob.unlink()
        trimmed = install_skill_pack(self.paths, profile="core", force=True)["hermes_edited_skills"][0]
        self.assertEqual(trimmed["ledger_copy"], [])
        summary = self._summary({"hermes_edited_skills": [trimmed]})
        self.assertIn("holds no copy of it, so the edit cannot be recovered from there", summary)
        self.assertNotIn("still holds", summary)

    def _summary(self, payload: dict) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            setup_commands._print_install_summary({"skills": [], **payload}, command="install")
        return out.getvalue()

    def test_a_deleted_managed_skill_is_rendered_again_and_reported(self) -> None:
        install_skill_pack(self.paths, profile="core")
        skill_dir = self.skill_file().parent
        for path in sorted(skill_dir.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        skill_dir.rmdir()

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(self.skill_file().is_file())
        self.assertEqual(
            result["restored_skills"],
            [{"name": SKILL, "path": f"{skill_install_relative_dir(SKILL).as_posix()}/SKILL.md", "disposition": "restored"}],
        )
        self.assertIn(f"Restored {SKILL} at", self._summary(result))

    def test_an_install_whose_own_catalog_lacks_a_kept_skill_leaves_it(self) -> None:
        """A kept skill is withheld from the pruners even when this install's catalog lacks it.

        That is the shape of an explicit `--from-skills-dir` source missing a
        skill the catalog definitions still render; the template list is
        patched because building such a source is beside the point. It does
        NOT model a skill a release retired: there the name no longer resolves
        to its render directory at all, which `RetiredSkillEditTests` covers.
        """
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        install_skill_pack(self.paths, profile="core")
        kept = self.skill_file().read_bytes()
        templates = [template for template in builtin_skill_templates() if template.name != SKILL]
        references = [template for template in builtin_skill_reference_templates() if template.skill_name != SKILL]

        with (
            mock.patch.object(installer, "builtin_skill_templates", return_value=templates),
            mock.patch.object(installer, "builtin_skill_reference_templates", return_value=references),
        ):
            result = install_skill_pack(self.paths, profile="core")

        self.assertEqual(self.skill_file().read_bytes(), kept)
        self.assertNotIn(SKILL, result["pruned_skills"])

    def test_a_kept_full_only_skill_is_not_carried_into_a_core_generation(self) -> None:
        full_only = "workflow-learning"
        first = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-1" / "skills")
        second = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-2" / "skills")
        install_skill_pack(first, profile="full")
        self.hermes_patch(self.skill_file(first, full_only))
        self.hermes_patch(self.skill_file(first))

        result = install_skill_pack(second, profile="core")

        self.assertFalse(self.skill_file(second, full_only).exists())
        self.assertEqual([item["name"] for item in result["hermes_edited_skills"]], [SKILL])

    def test_a_file_added_then_removed_is_not_an_edit(self) -> None:
        # Probe P1: a write_file undone by a remove_file leaves OMH's render.
        install_skill_pack(self.paths, profile="core")
        added = self.skill_file().parent / "references" / "lessons.md"
        added.write_text("lesson\n", encoding="utf-8")
        sha = sha256_file(added)
        _append_ledger(self.paths.hermes_home, _change_row(added, after_sha=sha, action="write_file", ts="2026-10-13T01:00:00+00:00"))
        added.unlink()
        _append_ledger(self.paths.hermes_home, _change_row(added, before_sha=sha, action="remove_file", ts="2026-10-13T02:00:00+00:00"))

        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))
        install_skill_pack(self.paths, profile="core", force=True)
        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))

    def test_removing_a_file_omh_renders_is_an_edit_that_is_kept(self) -> None:
        install_skill_pack(self.paths, profile="core")
        reference = next(template for template in builtin_skill_reference_templates() if template.skill_name == SKILL)
        target = self.skill_file().parent / reference.relative_path
        sha = sha256_file(target)
        target.unlink()
        _append_ledger(self.paths.hermes_home, _change_row(target, before_sha=sha, action="remove_file", ts="2026-10-13T02:00:00+00:00"))

        result = install_skill_pack(self.paths, profile="core")

        self.assertEqual([(item["name"], item["action"]) for item in result["hermes_edited_skills"]], [(SKILL, "remove_file")])
        self.assertFalse(target.exists())

    def test_force_replaces_a_hand_edited_skill_holding_a_hermes_file(self) -> None:
        # Probe P2: the hand edit refuses, and --force must clear the file
        # Hermes added, or the next plain install keeps the skill for it.
        install_skill_pack(self.paths, profile="core")
        added = self.skill_file().parent / "references" / "lessons.md"
        added.write_text("lesson\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(added, action="write_file"))
        with self.skill_file().open("a", encoding="utf-8") as handle:
            handle.write("\nmy own hand edit\n")
        with self.assertRaisesRegex(OmhError, "local modifications detected"):
            install_skill_pack(self.paths, profile="core")

        forced = install_skill_pack(self.paths, profile="core", force=True)

        self.assertEqual([(item["name"], item["disposition"]) for item in forced["hermes_edited_skills"]], [(SKILL, "replaced")])
        self.assertFalse(added.exists())
        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))

    def test_force_never_clears_a_copy_of_a_managed_skill_elsewhere_in_the_pack(self) -> None:
        # Probe P3: the manifest records a person's copy under the skill's name.
        install_skill_pack(self.paths, profile="core")
        backup = self.paths.skills_dir / "my-backups" / self.skill_file().parent.name
        backup.mkdir(parents=True)
        (backup / "SKILL.md").write_bytes(self.skill_file().read_bytes())
        (backup / "personal.md").write_text("mine\n", encoding="utf-8")
        install_skill_pack(self.paths, profile="core")
        # A copy nobody edited through Hermes is not an edit to report.
        self.assertNotIn("hermes_edited_skills", install_skill_pack(self.paths, profile="core"))
        _append_ledger(self.paths.hermes_home, _ledger_row(backup / "personal.md", action="write_file"))
        rendered = self.skill_file().read_bytes()

        plain = install_skill_pack(self.paths, profile="core")
        forced = install_skill_pack(self.paths, profile="core", force=True)

        # Reported, and nothing else: the copy is not kept in the skill's
        # place (the skill itself still renders) and --force leaves it alone.
        for result in (plain, forced):
            self.assertEqual(
                [(item["disposition"], item["path"]) for item in result["hermes_edited_skills"]],
                [("left_behind", f"my-backups/{backup.name}/SKILL.md")],
            )
        self.assertTrue((backup / "personal.md").is_file())
        self.assertEqual(self.skill_file().read_bytes(), rendered)
        self.assertIn("neither kept nor carried", self._summary(plain))

    def test_a_record_path_that_leaves_the_pack_is_never_followed(self) -> None:
        # Probe P4: a record climbing out of the pack, with kept evidence.
        install_skill_pack(self.paths, profile="core")
        outside = self.root / "outside" / "victim"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text("victim\n", encoding="utf-8")
        manifest = read_manifest(self.paths.manifest_path)
        manifest["skills"].append(
            {
                "name": SKILL,
                "path": os.path.relpath(outside / "SKILL.md", self.paths.skills_dir).replace(os.sep, "/"),
                "sha256": sha256_file(outside / "SKILL.md"),
                "source": "builtin",
                HERMES_EDIT_RECORD_KEY: {"actor": "curator", "ts": "t", "action": "patch", "ledger": "l"},
            }
        )
        self.paths.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = install_skill_pack(self.paths, profile="core", force=True)

        self.assertTrue((outside / "SKILL.md").is_file())
        self.assertNotIn("hermes_edited_skills", result)

    def test_force_on_a_staged_update_leaves_the_older_generation_alone(self) -> None:
        first = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-1" / "skills")
        second = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-2" / "skills")
        install_skill_pack(first, profile="core")
        self.hermes_patch(self.skill_file(first))
        edited = self.skill_file(first).read_bytes()

        result = install_skill_pack(second, profile="core", force=True)

        # The rollback target keeps exactly what it held.
        self.assertEqual(self.skill_file(first).read_bytes(), edited)
        self.assertEqual([item["disposition"] for item in result["hermes_edited_skills"]], ["replaced"])
        self.assertNotEqual(self.skill_file(second).read_bytes(), edited)

    def test_force_finds_a_copy_recorded_under_an_earlier_generation(self) -> None:
        # The row names generation 1, where Hermes made the edit; generation 2
        # holds the kept copy, so the ledger is cut to generation 2 and the row
        # sits outside that cut. What --force loses is judged by content
        # against every row, so the copy is still found.
        first = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-1" / "skills")
        second = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-2" / "skills")
        install_skill_pack(first, profile="core")
        self.hermes_patch(self.skill_file(first))
        self.store_blob(self.skill_file(first))
        install_skill_pack(second, profile="core")
        self.assertEqual(read_ledger_writes(self.paths.hermes_home, within=[second.skills_dir]), ([], []))

        result = install_skill_pack(second, profile="core", force=True)

        [item] = result["hermes_edited_skills"]
        self.assertEqual((item["disposition"], item["ledger_copy"]), ("replaced", ["SKILL.md"]))

    def test_a_replaced_edit_names_the_ledger_that_could_not_be_read(self) -> None:
        # #2111 follow-up: the copies named for a replaced edit come from the
        # ledgers that could be read. A bot's ledger that could not be may hold
        # more, so the report names it beside the list, dry run or not.
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        self.store_blob(self.skill_file())
        bot_ledger = self.paths.hermes_home / "profiles" / "bot" / CURATOR_LEDGER_RELATIVE_PATH
        bot_ledger.parent.mkdir(parents=True)
        bot_ledger.write_bytes(b"\xff\xfe not utf-8\n")

        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                result = install_skill_pack(self.paths, profile="core", force=True, dry_run=dry_run)

                [item] = result["hermes_edited_skills"]
                self.assertEqual((item["disposition"], item["ledger_copy"]), ("replaced", ["SKILL.md"]))
                self.assertIs(item[LEDGER_COPY_PARTIAL_KEY], True)
                [unread] = result["unreadable_ledgers"]
                self.assertTrue(unread.startswith(f"{bot_ledger}: "), unread)
                [line] = [line.strip() for line in self._summary(result).splitlines() if "could not be read in full" in line]
                self.assertEqual(
                    line,
                    f"Hermes' curator ledger could not be read in full ({unread}), so what OMH says above about "
                    f"copies of {SKILL} counts only what it could read: a file it says has no copy may still have one there.",
                )
        # The marker is persisted with the entry; the error text is not.
        [recorded] = read_manifest(self.paths.manifest_path)["hermes_edited_skills"]
        self.assertIs(recorded[LEDGER_COPY_PARTIAL_KEY], True)
        self.assertNotIn(unread.encode(), self.paths.manifest_path.read_bytes())
        complete = {key: value for key, value in item.items() if key != LEDGER_COPY_PARTIAL_KEY}
        self.assertNotIn("could not be read in full", self._summary({**result, "hermes_edited_skills": [complete]}))

    def test_force_clears_only_skills_this_install_renders_again(self) -> None:
        # Clearing is for a skill the render below writes back; a skill no
        # template renders is the pruner's to judge, never the force clear's.
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        item = installer.hermes_edited_skills(read_manifest(self.paths.manifest_path), self.paths, overwritten=True)[0]
        writes = read_ledger_writes(self.paths.hermes_home)[0]
        rendered = installer.rendered_skill_files()

        _replaced, without = installer._force_replacements([item], self.paths, {"doctor"}, writes, rendered, writes)
        _replaced, with_skill = installer._force_replacements([item], self.paths, {"doctor", SKILL}, writes, rendered, writes)

        self.assertEqual(without, [])
        self.assertEqual(with_skill, [self.skill_file().parent])

    def test_a_skill_removed_from_the_active_generation_is_reported_restored(self) -> None:
        # Probe R1: judged in the pack the manifest describes, not the new one.
        first = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-1" / "skills")
        second = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-2" / "skills")
        install_skill_pack(first, profile="core")
        for path in sorted(self.skill_file(first).parent.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.skill_file(first).parent.rmdir()

        result = install_skill_pack(second, profile="core")

        self.assertTrue(self.skill_file(second).is_file())
        self.assertEqual([(item["name"], item["disposition"]) for item in result["restored_skills"]], [(SKILL, "restored")])

    def test_a_dry_run_reports_a_restore_without_writing(self) -> None:
        install_skill_pack(self.paths, profile="core")
        for path in sorted(self.skill_file().parent.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.skill_file().parent.rmdir()

        result = install_skill_pack(self.paths, profile="core", dry_run=True)

        self.assertEqual([item["disposition"] for item in result["restored_skills"]], ["restored"])
        self.assertFalse(self.skill_file().exists())

    def test_a_renamed_copy_in_a_folder_of_ones_own_is_reported_not_pruned(self) -> None:
        # A copy under a name the catalog does not ship is recorded like an
        # orphaned skill, but OMH never rendered anything under `my-backups/`.
        install_skill_pack(self.paths, profile="core")
        copy = self.paths.skills_dir / "my-backups" / "omh-routing-old"
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_bytes(self.skill_file().read_bytes())
        (copy / "personal.md").write_text("mine\n", encoding="utf-8")
        recorded = install_skill_pack(self.paths, profile="core")

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue((copy / "personal.md").is_file())
        self.assertEqual(result["pruned_skills"], [])
        expected = {"name": "omh-routing-old", "path": "my-backups/omh-routing-old/SKILL.md", "reason": "not_rendered_by_omh"}
        # Named by the install that first records it, listed by every later one.
        self.assertEqual(recorded["prune_skipped_skills"], [{**expected, "newly_seen": True}])
        self.assertEqual(result["prune_skipped_skills"], [{**expected, "newly_seen": False}])
        self.assertIn(
            "Left omh-routing-old at my-backups/omh-routing-old/SKILL.md in place: OMH did not render it",
            self._summary(recorded),
        )
        # A manifest written before render marks existed still spares it, by
        # where it sits.
        self.edit_manifest(legacy=True)
        legacy = install_skill_pack(self.paths, profile="core")
        self.assertTrue((copy / "personal.md").is_file())
        self.assertEqual([item["reason"] for item in legacy["prune_skipped_skills"]], ["not_an_omh_layout"])

    def test_a_plain_install_never_prunes_through_a_record_path_that_leaves_the_pack(self) -> None:
        install_skill_pack(self.paths, profile="core")
        outside = self.root / "outside" / "victim"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text("victim\n", encoding="utf-8")
        (outside / "precious.txt").write_text("precious\n", encoding="utf-8")
        record = {
            "name": "victim",
            "path": os.path.relpath(outside / "SKILL.md", self.paths.skills_dir).replace(os.sep, "/"),
            "sha256": sha256_file(outside / "SKILL.md"),
            "source": "builtin",
            # Even a record that claims OMH rendered it.
            RENDER_MARK_KEY: sha256_file(outside / "SKILL.md"),
        }
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.edit_manifest(lambda manifest: manifest["skills"].append(dict(record)), legacy=legacy)

                result = install_skill_pack(self.paths, profile="core")

                self.assertTrue((outside / "precious.txt").is_file())
                self.assertEqual(result["pruned_skills"], [])
                self.assertEqual(
                    [(item["name"], item["reason"]) for item in result["prune_skipped_skills"]],
                    [("victim", "not_an_omh_layout")],
                )

    def test_the_pruner_never_follows_a_category_link_out_of_the_pack(self) -> None:
        install_skill_pack(self.paths, profile="core")
        category = self.paths.skills_dir / skill_install_relative_dir(SKILL).parts[0]
        outside = self.root / "outside" / "shared-category"
        shutil.copytree(category, outside)
        shutil.rmtree(category)
        category.symlink_to(outside, target_is_directory=True)
        orphan = outside / "legacy-orphan-skill"
        orphan.mkdir()
        (orphan / "SKILL.md").write_text("# someone else's\n", encoding="utf-8")
        self.edit_manifest(
            lambda manifest: manifest["skills"].append(
                {
                    "name": "legacy-orphan-skill",
                    "path": f"{category.name}/legacy-orphan-skill/SKILL.md",
                    "sha256": sha256_file(orphan / "SKILL.md"),
                    "source": "builtin",
                    RENDER_MARK_KEY: sha256_file(orphan / "SKILL.md"),
                }
            )
        )

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue((orphan / "SKILL.md").is_file())
        self.assertIn(
            {"name": "legacy-orphan-skill", "path": f"{category.name}/legacy-orphan-skill/SKILL.md", "reason": "outside_the_skills_directory"},
            result["prune_skipped_skills"],
        )

    def test_force_never_clears_through_a_category_link_out_of_the_pack(self) -> None:
        # Probe N2: the category directory is a link, and the ledger row was
        # written through it.
        install_skill_pack(self.paths, profile="core")
        category = self.paths.skills_dir / skill_install_relative_dir(SKILL).parts[0]
        outside = self.root / "outside" / "shared-category"
        shutil.copytree(category, outside)
        personal = outside / self.skill_file().parent.name / "personal.md"
        personal.write_text("mine\n", encoding="utf-8")
        shutil.rmtree(category)
        category.symlink_to(outside, target_is_directory=True)
        through_link = os.path.normpath(self.skill_file().parent / "personal.md")
        _append_ledger(
            self.paths.hermes_home,
            json.dumps(
                {
                    "id": "n2", "ts": "2026-10-13T04:00:00+00:00", "actor": "curator", "action": "write_file",
                    "skill": self.skill_file().parent.name, "evidence": {}, "before": [],
                    "after": [{"path": through_link, "sha256": sha256_file(personal)}],
                }
            ),
        )

        install_skill_pack(self.paths, profile="core", force=True)

        self.assertTrue(personal.is_file())

    def test_a_kept_skill_is_not_carried_through_a_category_link_out_of_the_new_pack(self) -> None:
        first = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-1" / "skills")
        second = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-2" / "skills")
        install_skill_pack(first, profile="core")
        self.hermes_patch(self.skill_file(first))
        outside = self.root / "outside" / "shared-category"
        theirs = outside / self.skill_file(first).parent.name / "personal.md"
        theirs.parent.mkdir(parents=True)
        theirs.write_text("mine\n", encoding="utf-8")
        second.skills_dir.mkdir(parents=True)
        (second.skills_dir / skill_install_relative_dir(SKILL).parts[0]).symlink_to(outside, target_is_directory=True)

        install_skill_pack(second, profile="core")

        self.assertTrue(theirs.is_file())

    def test_an_edit_where_a_release_moved_the_skill_is_reported_left_behind(self) -> None:
        # A staged update over a generation whose record sits in the skill's
        # former category directory, with kept evidence on the record.
        first = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-1" / "skills")
        second = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-2" / "skills")
        install_skill_pack(first, profile="core")
        old_render = self.skill_file(first).parent
        moved = first.skills_dir / "former-category" / old_render.name
        moved.parent.mkdir(parents=True)
        old_render.rename(moved)
        with (moved / "SKILL.md").open("a", encoding="utf-8") as handle:
            handle.write("\nlearned: kept edit\n")
        manifest = read_manifest(self.paths.manifest_path)
        current = f"{skill_install_relative_dir(SKILL).as_posix()}/SKILL.md"
        for record in manifest["skills"]:
            if record["path"] == current:
                record["path"] = f"former-category/{old_render.name}/SKILL.md"
                record["sha256"] = sha256_file(moved / "SKILL.md")
                record[HERMES_EDIT_RECORD_KEY] = {"actor": "curator", "ts": "2026-10-13T01:02:03+00:00", "action": "patch", "ledger": "l"}
        self.paths.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        template = next(template for template in builtin_skill_templates() if template.name == SKILL)

        result = install_skill_pack(second, profile="core")

        self.assertEqual(
            [(item["name"], item["disposition"], item["path"]) for item in result["hermes_edited_skills"]],
            [(SKILL, "left_behind", f"former-category/{old_render.name}/SKILL.md")],
        )
        # Not carried and not cleared: the new generation holds OMH's render,
        # the old one still holds the edit.
        self.assertEqual(self.skill_file(second).read_text(encoding="utf-8"), template.content)
        self.assertIn("kept edit", (moved / "SKILL.md").read_text(encoding="utf-8"))
        self.assertFalse((second.skills_dir / "former-category").exists())

    def flat_position(self) -> Path:
        """Move the skill to the top-level position an earlier release rendered it at, record included."""
        install_skill_pack(self.paths, profile="core")
        render = self.skill_file().parent
        flat = self.paths.skills_dir / render.name
        render.rename(flat)
        current = f"{skill_install_relative_dir(SKILL).as_posix()}/SKILL.md"

        def move_record(manifest: dict) -> None:
            for record in manifest["skills"]:
                if record["path"] == current:
                    record["path"] = f"{flat.name}/SKILL.md"

        self.edit_manifest(move_record)
        return flat

    def test_a_left_behind_edit_at_the_top_level_really_stays(self) -> None:
        # R4-2 / probe Q6a: reported as staying, then deleted by the
        # flat-layout pruner in the same install.
        flat = self.flat_position()
        added = flat / "references" / "lessons.md"
        added.write_text("lesson\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(added, action="write_file"))

        for force in (False, True):
            with self.subTest(force=force):
                result = install_skill_pack(self.paths, profile="core", force=force)
                self.assertEqual(
                    [(item["disposition"], item["path"]) for item in result["hermes_edited_skills"]],
                    [("left_behind", f"{flat.name}/SKILL.md")],
                )
                self.assertTrue(added.is_file())
                self.assertEqual(result["flat_layout_skills_removed"], [])
                # The skill itself renders where this release renders it.
                self.assertTrue(self.skill_file().is_file())
                summary = self._summary(result)
                self.assertIn(f"stays at {flat.name}/SKILL.md", summary)
                # It stays for good, so the line says how it goes: by hand.
                self.assertIn(
                    f"While the catalog ships {SKILL}, no install removes it, with or without --force; "
                    "delete that directory yourself",
                    summary,
                )
                # Reported as left behind, and not listed a second time as
                # something the pruners left in place.
                self.assertNotIn("prune_skipped_skills", result)
                self.assertNotIn(b"prune_skipped_skills", self.paths.manifest_path.read_bytes())

    def test_a_left_behind_edit_to_skill_md_does_not_refuse_the_install(self) -> None:
        # Probe Q6b: the edited SKILL.md differs from its record, and the
        # ledger explains it, so it is reported rather than refused.
        flat = self.flat_position()
        self.hermes_patch(flat / "SKILL.md")
        edited = (flat / "SKILL.md").read_bytes()

        result = install_skill_pack(self.paths, profile="core")

        self.assertEqual([item["disposition"] for item in result["hermes_edited_skills"]], ["left_behind"])
        self.assertEqual((flat / "SKILL.md").read_bytes(), edited)

    def test_force_names_the_files_it_removes_for_good(self) -> None:
        # Probe N9: a person's own file beside a Hermes-added one.
        install_skill_pack(self.paths, profile="core")
        render = self.skill_file().parent
        (render / "my-notes.md").write_text("never ledgered\n", encoding="utf-8")
        added = render / "references" / "lessons.md"
        added.write_text("lesson\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(added, action="write_file"))
        self.store_blob(added)

        result = install_skill_pack(self.paths, profile="core", force=True)

        [item] = result["hermes_edited_skills"]
        self.assertEqual(item["ledger_copy"], ["references/lessons.md"])
        self.assertEqual(item["discarded"], ["my-notes.md"])
        self.assertIn("holds no copy of: my-notes.md", self._summary(result))

    def test_a_supporting_file_patch_keeps_the_whole_skill(self) -> None:
        install_skill_pack(self.paths, profile="core")
        reference = next(template for template in builtin_skill_reference_templates() if template.skill_name == SKILL)
        target = self.skill_file().parent / reference.relative_path
        self.hermes_patch(target, action="patch")
        patched = target.read_bytes()

        result = install_skill_pack(self.paths, profile="core")

        # Reference files are not in the manifest, so without the ledger this
        # overwrite was silent.
        self.assertEqual(target.read_bytes(), patched)
        self.assertEqual([item["name"] for item in result["hermes_edited_skills"]], [SKILL])

    def test_a_staged_update_carries_the_edit_into_the_new_generation(self) -> None:
        first = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-1" / "skills")
        second = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-2" / "skills")
        third = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-3" / "skills")
        install_skill_pack(first, profile="core")
        old_file = self.skill_file(first)
        self.hermes_patch(old_file, action="edit")
        edited = old_file.read_bytes()
        reference = next(template for template in builtin_skill_reference_templates() if template.skill_name == SKILL)

        result = install_skill_pack(second, profile="core")

        self.assertEqual(self.skill_file(second).read_bytes(), edited)
        # Carried whole, supporting files included.
        self.assertTrue((self.skill_file(second).parent / reference.relative_path).is_file())
        self.assertEqual([item["disposition"] for item in result["hermes_edited_skills"]], ["kept"])
        # The next generation carries it again from the manifest's own record:
        # the ledger row named the first generation, and now it is gone too.
        (self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH).unlink()
        carried = install_skill_pack(third, profile="core")
        self.assertEqual(self.skill_file(third).read_bytes(), edited)
        self.assertEqual([item["name"] for item in carried["hermes_edited_skills"]], [SKILL])
        # Every other skill is OMH's current render.
        other = next(template for template in builtin_skill_templates() if template.name == "doctor")
        self.assertEqual(self.skill_file(third, "doctor").read_text(encoding="utf-8"), other.content)

    def test_a_dry_run_reports_without_writing(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        before = self.paths.manifest_path.read_bytes()

        result = install_skill_pack(self.paths, profile="core", dry_run=True)

        self.assertEqual([item["disposition"] for item in result["hermes_edited_skills"]], ["kept"])
        self.assertEqual(self.paths.manifest_path.read_bytes(), before)


class RenderMarkPruneTests(_Home):
    """The pruners delete only what an install itself rendered and nobody changed since.

    The manifest records every SKILL.md under the pack, so a record never
    proved OMH rendered a directory. An install now marks the records it
    wrote with the hash it wrote (`rendered_sha256`) and flags the manifest
    (`render_marks`); a pruner deletes only a marked record whose SKILL.md
    still has that hash. A manifest without the flag was written before marks
    existed and is pruned by the rule it was written under, once.
    """

    def records(self) -> dict[str, dict]:
        return {record["path"]: record for record in read_manifest(self.paths.manifest_path)["skills"]}

    def drop_from_catalog(self, name: str):
        templates = [template for template in builtin_skill_templates() if template.name != name]
        references = [template for template in builtin_skill_reference_templates() if template.skill_name != name]
        return (
            mock.patch.object(installer, "builtin_skill_templates", return_value=templates),
            mock.patch.object(installer, "builtin_skill_reference_templates", return_value=references),
        )

    def test_an_install_marks_what_it_rendered_and_nothing_else(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.own_skill("guide/my-own-skill")
        install_skill_pack(self.paths, profile="core")

        manifest = read_manifest(self.paths.manifest_path)
        self.assertIs(manifest[RENDER_MARKS_FLAG], True)
        records = self.records()
        own = records.pop("guide/my-own-skill/SKILL.md")
        self.assertNotIn(RENDER_MARK_KEY, own)
        self.assertTrue(records)
        for path, record in records.items():
            with self.subTest(path=path):
                self.assertEqual(record[RENDER_MARK_KEY], record["sha256"])

    def test_a_kept_skill_keeps_the_mark_of_what_omh_wrote(self) -> None:
        install_skill_pack(self.paths, profile="core")
        rendered_sha = sha256_file(self.skill_file())
        self.hermes_patch(self.skill_file())
        install_skill_pack(self.paths, profile="core")

        record = self.records()[f"{skill_install_relative_dir(SKILL).as_posix()}/SKILL.md"]
        self.assertEqual(record[RENDER_MARK_KEY], rendered_sha)
        self.assertNotEqual(record["sha256"], rendered_sha)

    def test_a_persons_own_skill_at_an_omh_position_is_never_pruned(self) -> None:
        # Probes Q1-Q3: in a category directory, at the top level, and a
        # renamed copy beside the skill it was copied from.
        install_skill_pack(self.paths, profile="core")
        render = self.skill_file().parent
        own = [self.own_skill("guide/my-own-skill"), self.own_skill("my-own-top-level-skill")]
        sibling = render.parent / "omh-routing-old"
        shutil.copytree(render, sibling)
        (sibling / "personal.md").write_text("mine\n", encoding="utf-8")
        install_skill_pack(self.paths, profile="core")

        for force in (False, False, True):
            with self.subTest(force=force):
                result = install_skill_pack(self.paths, profile="core", force=force)
                self.assertEqual(result["pruned_skills"], [])
                self.assertEqual(result["flat_layout_skills_removed"], [])
                self.assertTrue(all((directory / "notes.md").is_file() for directory in own))
                self.assertTrue((sibling / "personal.md").is_file())
                self.assertEqual(
                    sorted((item["name"], item["reason"]) for item in result["prune_skipped_skills"]),
                    [
                        ("my-own-skill", "not_rendered_by_omh"),
                        ("my-own-top-level-skill", "not_rendered_by_omh"),
                        ("omh-routing-old", "not_rendered_by_omh"),
                    ],
                )

    def test_a_retired_skill_omh_rendered_is_pruned_while_unchanged(self) -> None:
        install_skill_pack(self.paths, profile="core")
        retired = self.skill_file(name="doctor").parent
        first, second = self.drop_from_catalog("doctor")

        with first, second:
            result = install_skill_pack(self.paths, profile="core")

        self.assertEqual(result["pruned_skills"], ["doctor"])
        self.assertFalse(retired.exists())

    def changed_retired_skill(self) -> Path:
        """A skill OMH rendered, edited since, whose record already holds the edited hash.

        The record holding the new hash is what a reconcile or an earlier
        `--force` leaves; with the old hash a plain install refuses outright,
        as it does for any changed managed file.
        """
        install_skill_pack(self.paths, profile="core")
        retired = self.skill_file(name="doctor")
        with retired.open("a", encoding="utf-8") as handle:
            handle.write("\nsomeone kept working on it\n")
        (retired.parent / "my-notes.md").write_text("mine\n", encoding="utf-8")

        def rehash(manifest: dict) -> None:
            for record in manifest["skills"]:
                if record["name"] == "doctor":
                    record["sha256"] = sha256_file(retired)

        self.edit_manifest(rehash)
        return retired

    def test_a_changed_retired_skill_is_left_by_a_plain_install(self) -> None:
        retired = self.changed_retired_skill()
        first, second = self.drop_from_catalog("doctor")

        with first, second:
            result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(retired.is_file())
        self.assertEqual(result["pruned_skills"], [])
        self.assertNotIn("force_pruned_skills", result)
        self.assertEqual(
            [(item["name"], item["reason"]) for item in result["prune_skipped_skills"]],
            [("doctor", "changed_since_rendered")],
        )
        self.assertIn("A run with --force removes it, except under OMH_SECURITY=strict.", self.summary(result))

    def test_force_removes_a_changed_retired_skill_and_says_what_that_lost(self) -> None:
        # The mark proves OMH rendered the path; --force is consent to lose
        # the changes, as it is for a live skill.
        retired = self.changed_retired_skill()
        _append_ledger(self.paths.hermes_home, _ledger_row(retired, action="patch"))
        self.store_blob(retired)
        relative = f"{skill_install_relative_dir('doctor').as_posix()}/SKILL.md"
        first, second = self.drop_from_catalog("doctor")

        with first, second:
            result = install_skill_pack(self.paths, profile="core", force=True)

        self.assertFalse(retired.parent.exists())
        self.assertEqual(result["pruned_skills"], ["doctor"])
        [item] = result["force_pruned_skills"]
        self.assertEqual((item["name"], item["path"]), ("doctor", relative))
        self.assertEqual(item["ledger_copy"], ["SKILL.md"])
        self.assertIn("my-notes.md", item["discarded"])
        self.assertNotIn("SKILL.md", item["discarded"])
        summary = self.summary(result)
        self.assertIn(f"Removed doctor at {relative} because --force was given", summary)
        self.assertIn("still holds a copy of SKILL.md", summary)
        self.assertIn("Removing doctor deleted files Hermes' curator ledger holds no copy of:", summary)

    def test_force_never_removes_what_omh_did_not_render(self) -> None:
        # The same state without the mark: a directory an install merely found.
        retired = self.changed_retired_skill()
        self.edit_manifest(
            lambda manifest: [record.pop(RENDER_MARK_KEY, None) for record in manifest["skills"] if record["name"] == "doctor"]
        )
        first, second = self.drop_from_catalog("doctor")

        with first, second:
            result = install_skill_pack(self.paths, profile="core", force=True)

        self.assertTrue(retired.is_file())
        self.assertEqual(result["pruned_skills"], [])
        self.assertNotIn("force_pruned_skills", result)
        self.assertEqual([item["reason"] for item in result["prune_skipped_skills"]], ["not_rendered_by_omh"])

    def summary(self, payload: dict) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            setup_commands._print_install_summary({"skills": [], **payload}, command="install")
        return out.getvalue()

    def test_the_flat_layout_pruner_follows_the_same_rule(self) -> None:
        # A top-level copy OMH rendered (its record carries the mark), edited
        # since, beside the categorized skill that replaced it.
        install_skill_pack(self.paths, profile="core")
        render = self.skill_file().parent
        flat = self.paths.skills_dir / render.name
        shutil.copytree(render, flat)
        rendered_sha = sha256_file(flat / "SKILL.md")
        with (flat / "SKILL.md").open("a", encoding="utf-8") as handle:
            handle.write("\nedited after OMH wrote it\n")
        install_skill_pack(self.paths, profile="core")

        def claim_rendered(manifest: dict) -> None:
            for record in manifest["skills"]:
                if record["path"] == f"{flat.name}/SKILL.md":
                    record[RENDER_MARK_KEY] = rendered_sha

        self.edit_manifest(claim_rendered)
        plain = install_skill_pack(self.paths, profile="core")
        self.assertTrue((flat / "SKILL.md").is_file())
        self.assertIn(
            (SKILL, "changed_since_rendered"),
            [(item["name"], item["reason"]) for item in plain["prune_skipped_skills"]],
        )

        self.edit_manifest(claim_rendered)
        forced = install_skill_pack(self.paths, profile="core", force=True)

        self.assertFalse(flat.exists())
        self.assertEqual(forced["flat_layout_skills_removed"], [SKILL])
        self.assertEqual([(item["name"], item["path"]) for item in forced["force_pruned_skills"]], [(SKILL, f"{flat.name}/SKILL.md")])
        self.assertTrue(self.skill_file().is_file())

    def test_a_directory_omh_did_not_render_is_named_once_and_listed_every_time(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.own_skill("guide/my-own-skill")

        first = install_skill_pack(self.paths, profile="core")
        second = install_skill_pack(self.paths, profile="core")
        reconcile_skill_profile(self.paths)
        third = install_skill_pack(self.paths, profile="core")

        expected = {"name": "my-own-skill", "path": "guide/my-own-skill/SKILL.md", "reason": "not_rendered_by_omh"}
        self.assertEqual(first["prune_skipped_skills"], [{**expected, "newly_seen": True}])
        self.assertIn("Left my-own-skill at guide/my-own-skill/SKILL.md in place", self.summary(first))
        # Still in the result on every later install, a reconcile in between
        # included, and no longer in what a person reads.
        for later in (second, third):
            self.assertEqual(later["prune_skipped_skills"], [{**expected, "newly_seen": False}])
            self.assertNotIn("my-own-skill", self.summary(later))

    def test_a_reason_a_person_can_act_on_is_printed_every_time(self) -> None:
        retired = self.changed_retired_skill()
        first, second = self.drop_from_catalog("doctor")

        with first, second:
            install_skill_pack(self.paths, profile="core")
            again = install_skill_pack(self.paths, profile="core")

        self.assertTrue(retired.is_file())
        self.assertIn(
            "its SKILL.md changed since OMH rendered it there, or a Hermes write is recorded under it (OMH cannot "
            "tell whether before or after it last rendered there)",
            self.summary(again),
        )

    def test_an_emptied_category_with_a_stray_skill_file_is_not_removed(self) -> None:
        # No other skill below it, so "holds another SKILL.md" is false; it is
        # still the category directory, and what else sits in it is not OMH's.
        category = self.paths.skills_dir / skill_install_relative_dir(SKILL).parts[0]
        category.mkdir(parents=True)
        (category / "SKILL.md").write_text("stray\n", encoding="utf-8")
        (category / "notes.txt").write_text("kept beside the skills\n", encoding="utf-8")
        manifest = manifest_module.new_manifest(
            "builtin", self.paths.skills_dir, manifest_module.skill_records(self.paths.skills_dir, "builtin")
        )
        catalog = {template.name for template in builtin_skill_templates()}

        removed, skipped, _forced = installer._prune_orphaned_skills(
            self.paths.skills_dir, manifest, catalog, force=True
        )

        self.assertEqual(removed, [])
        self.assertTrue((category / "notes.txt").is_file())
        self.assertEqual(
            skipped, [{"name": category.name, "path": f"{category.name}/SKILL.md", "reason": "category_directory"}]
        )

    def test_a_manifest_from_before_marks_is_pruned_by_its_own_rule_once(self) -> None:
        # An unmarked record of a skill the catalog does not ship, unchanged,
        # at an OMH position: the cleanup a retired skill has always had.
        install_skill_pack(self.paths, profile="core")
        orphan = self.own_skill("legacy-orphan-skill", content="# legacy orphan skill\n")
        install_skill_pack(self.paths, profile="core")
        self.edit_manifest(legacy=True)

        result = install_skill_pack(self.paths, profile="core")

        self.assertEqual(result["pruned_skills"], ["legacy-orphan-skill"])
        self.assertFalse(orphan.exists())
        # The manifest that install wrote carries marks: from here on, only
        # what OMH rendered.
        self.assertIs(read_manifest(self.paths.manifest_path)[RENDER_MARKS_FLAG], True)
        again = self.own_skill("legacy-orphan-skill", content="# legacy orphan skill\n")
        install_skill_pack(self.paths, profile="core")
        install_skill_pack(self.paths, profile="core")
        self.assertTrue(again.exists())

    def test_a_stray_skill_file_in_a_category_never_takes_the_category(self) -> None:
        # R4-1 / probe Q4: `guide/SKILL.md` reads as a one-part skill path.
        install_skill_pack(self.paths, profile="core")
        category = self.skill_file().parent.parent
        (category / "SKILL.md").write_text("stray\n", encoding="utf-8")
        install_skill_pack(self.paths, profile="core")
        members = sorted(child.name for child in category.iterdir())

        def claim_rendered(manifest: dict) -> None:
            for record in manifest["skills"]:
                if record["path"] == f"{category.name}/SKILL.md":
                    record[RENDER_MARK_KEY] = record["sha256"]

        for legacy, force in ((True, False), (False, True)):
            with self.subTest(legacy=legacy, force=force):
                # Under the old rule, and under the mark rule with a record
                # that claims the mark: the guard is what stands either way.
                self.edit_manifest(claim_rendered, legacy=legacy)
                result = install_skill_pack(self.paths, profile="core", force=force)
                self.assertEqual(sorted(child.name for child in category.iterdir()), members)
                self.assertEqual(result["pruned_skills"], [])
                self.assertIn(
                    {"name": category.name, "path": f"{category.name}/SKILL.md", "reason": "category_directory"},
                    result["prune_skipped_skills"],
                )

    def test_a_top_level_copy_named_like_a_skill_is_not_a_flat_leftover(self) -> None:
        # Probe Q6c without a Hermes write: the flat-layout pruner took any
        # recorded top-level directory named after a catalog skill for a
        # leftover of the pre-category layout.
        install_skill_pack(self.paths, profile="core")
        render = self.skill_file().parent
        copy = self.paths.skills_dir / render.name
        shutil.copytree(render, copy)
        (copy / "personal.md").write_text("mine\n", encoding="utf-8")
        install_skill_pack(self.paths, profile="core")

        for force in (False, True):
            with self.subTest(force=force):
                result = install_skill_pack(self.paths, profile="core", force=force)
                self.assertTrue((copy / "personal.md").is_file())
                self.assertEqual(result["flat_layout_skills_removed"], [])
                self.assertIn(
                    (SKILL, f"{copy.name}/SKILL.md", "not_rendered_by_omh"),
                    [(item["name"], item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
                )

    def test_a_directory_holding_other_skills_is_never_pruned(self) -> None:
        # Not a category of this catalog, and still not a leaf: under the
        # pre-mark rule an unchanged `oldcat/SKILL.md` would take `oldcat/kept/`.
        install_skill_pack(self.paths, profile="core")
        self.own_skill("oldcat", content="stale\n")
        inner = self.own_skill("oldcat/kept")
        install_skill_pack(self.paths, profile="core")
        self.edit_manifest(legacy=True)

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue((inner / "notes.md").is_file())
        self.assertIn(
            {"name": "oldcat", "path": "oldcat/SKILL.md", "reason": "category_directory"},
            result["prune_skipped_skills"],
        )

    def test_a_category_is_never_pruned_when_the_catalog_lacks_its_namesake(self) -> None:
        # `ultrawork` is a skill name and the category the ULW skills render
        # under; a catalog without the skill must not remove the category.
        install_skill_pack(self.paths, profile="full")
        category = self.paths.skills_dir / "ultrawork"
        (category / "SKILL.md").write_text("stale flat render\n", encoding="utf-8")
        members = sorted(child.name for child in category.iterdir() if child.is_dir())
        self.assertGreater(len(members), 1)
        manifest = manifest_module.new_manifest(
            "builtin", self.paths.skills_dir, manifest_module.skill_records(self.paths.skills_dir, "builtin")
        )
        catalog = {template.name for template in builtin_skill_templates()} - {"ultrawork"}

        _removed, skipped, _forced = installer._prune_orphaned_skills(
            self.paths.skills_dir, manifest, catalog, force=True
        )

        # The retired skill's own render directory goes, as it always did; the
        # category and every other skill in it stay.
        retired = installer.skill_directory_name("ultrawork")
        self.assertEqual(
            sorted(child.name for child in category.iterdir() if child.is_dir()),
            [member for member in members if member != retired],
        )
        self.assertEqual(
            skipped, [{"name": "ultrawork", "path": "ultrawork/SKILL.md", "reason": "category_directory"}]
        )

    def test_reconcile_carries_marks_and_kept_evidence(self) -> None:
        install_skill_pack(self.paths, profile="full")
        self.hermes_patch(self.skill_file())
        install_skill_pack(self.paths, profile="core")
        before = self.records()[f"{skill_install_relative_dir(SKILL).as_posix()}/SKILL.md"]

        reconcile_skill_profile(self.paths)

        manifest = read_manifest(self.paths.manifest_path)
        self.assertIs(manifest[RENDER_MARKS_FLAG], True)
        after = self.records()[f"{skill_install_relative_dir(SKILL).as_posix()}/SKILL.md"]
        self.assertEqual(after[RENDER_MARK_KEY], before[RENDER_MARK_KEY])
        self.assertEqual(after[HERMES_EDIT_RECORD_KEY], before[HERMES_EDIT_RECORD_KEY])
        self.assertTrue(all(RENDER_MARK_KEY in record for record in manifest["skills"]))

    def test_every_skip_reason_has_its_own_line_in_every_language(self) -> None:
        # R4-3: one line for every reason printed two false clauses for the
        # case where the directory IS the one OMH renders into.
        english = {}
        for reason in PRUNE_SKIP_REASONS:
            key = f"prune_skipped_{reason}_line"
            for language, messages in MESSAGES.items():
                with self.subTest(reason=reason, language=language):
                    self.assertIn(key, messages)
                    self.assertIn("{name}", messages[key])
                    self.assertIn("{path}", messages[key])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                setup_commands._print_install_summary(
                    {"skills": [], "prune_skipped_skills": [{"name": "n", "path": "p/SKILL.md", "reason": reason}]},
                    command="install",
                )
            english[reason] = next(line.strip() for line in out.getvalue().splitlines() if "Left n at" in line)
        self.assertEqual(len(set(english.values())), len(PRUNE_SKIP_REASONS))
        same = english["same_directory_as_a_rendered_skill"]
        self.assertIn("it is the directory OMH renders a skill into", same)
        self.assertNotIn("does not ship", same)
        self.assertNotIn("not where OMH renders", same)
        self.assertIn("OMH did not render it", english["not_rendered_by_omh"])
        self.assertIn("holds other skills", english["category_directory"])


class RetiredSkillEditTests(_Home):
    """A skill a release really retired, with a Hermes edit left in it.

    The catalog no longer knows the name, so it falls back to a render path it
    never had (`workflow/omh-old-skill`) and nothing keeps the directory. The
    pruners judged "changed since OMH rendered it" by the SKILL.md hash alone,
    so a Hermes write to a supporting file went with the directory on a plain
    install, and nothing said so.
    """

    NAME = "old-skill"
    REL = "tracker/omh-old-skill/SKILL.md"

    def retired_skill(self, *, kept: bool) -> tuple[Path, Path]:
        install_skill_pack(self.paths, profile="core")
        directory = self.paths.skills_dir / "tracker" / "omh-old-skill"
        (directory / "references").mkdir(parents=True)
        (directory / "SKILL.md").write_text("---\nname: omh-old-skill\n---\nretired\n", encoding="utf-8")
        lessons = directory / "references" / "lessons.md"
        lessons.write_text("learned: check the lockfile first\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(lessons, action="write_file"))
        self.store_blob(lessons)
        sha = sha256_file(directory / "SKILL.md")
        record = {"name": self.NAME, "path": self.REL, "sha256": sha, "source": "builtin", RENDER_MARK_KEY: sha}
        if kept:
            record[HERMES_EDIT_RECORD_KEY] = {"actor": "curator", "ts": "2026-10-13T01:02:03+00:00", "action": "write_file", "ledger": "l"}
        self.edit_manifest(lambda manifest: manifest["skills"].append(record))
        return directory, lessons

    def summary(self, payload: dict, language: str = "en") -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            setup_commands._print_install_summary({"skills": [], **payload}, command="install", language=language)
        return out.getvalue()

    def removal_lines(self, payload: dict, language: str = "en", needle: str = "Removed ") -> list[str]:
        return [line.strip() for line in self.summary(payload, language).splitlines() if needle in line]

    def assert_left_then_forced(self, directory: Path, lessons: Path) -> None:
        plain = install_skill_pack(self.paths, profile="core")
        self.assertTrue(lessons.is_file())
        self.assertEqual(plain["pruned_skills"], [])
        self.assertIn(
            (self.NAME, self.REL, "changed_since_rendered"),
            [(item["name"], item["path"], item["reason"]) for item in plain["prune_skipped_skills"]],
        )
        self.assertIn("A run with --force removes it, except under OMH_SECURITY=strict.", self.summary(plain))
        # Still left on the next plain install.
        self.assertTrue(install_skill_pack(self.paths, profile="core") and lessons.is_file())

        forced = install_skill_pack(self.paths, profile="core", force=True)

        self.assertFalse(directory.exists())
        [item] = forced["force_pruned_skills"]
        self.assertEqual(item["ledger_copy"], ["references/lessons.md"])
        self.assertIn("still holds a copy of references/lessons.md", self.summary(forced))

    def test_a_kept_supporting_file_edit_survives_retirement(self) -> None:
        self.assert_left_then_forced(*self.retired_skill(kept=True))

    def test_kept_evidence_alone_keeps_it_after_the_ledger_row_is_gone(self) -> None:
        # The ledger can be trimmed; the record's evidence is what remains,
        # and it has to survive the manifest each install rewrites.
        directory, lessons = self.retired_skill(kept=True)
        (self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH).unlink()

        for _ in range(2):
            result = install_skill_pack(self.paths, profile="core")
            self.assertTrue(lessons.is_file())
            # By path: once the catalog no longer knows the name, the next
            # manifest records the directory under its own name.
            self.assertIn(
                (self.REL, "changed_since_rendered"),
                [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
            )

    def test_a_ledgered_write_not_yet_kept_survives_retirement(self) -> None:
        self.assert_left_then_forced(*self.retired_skill(kept=False))

    def test_a_flat_leftover_holding_a_hermes_file_is_left(self) -> None:
        # The pre-relabel position, named by the canonical name: not the
        # skill's label, so it is not reported as left behind, and only the
        # flat-layout pruner's own rule stands between it and removal.
        install_skill_pack(self.paths, profile="core")
        render = self.skill_file().parent
        flat = self.paths.skills_dir / SKILL
        shutil.copytree(render, flat)
        lessons = flat / "references" / "lessons.md"
        lessons.write_text("lesson\n", encoding="utf-8")
        install_skill_pack(self.paths, profile="core")
        _append_ledger(self.paths.hermes_home, _ledger_row(lessons, action="write_file"))

        def claim_rendered(manifest: dict) -> None:
            for record in manifest["skills"]:
                if record["path"] == f"{flat.name}/SKILL.md":
                    record[RENDER_MARK_KEY] = record["sha256"]

        self.edit_manifest(claim_rendered)
        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(lessons.is_file())
        self.assertEqual(result["flat_layout_skills_removed"], [])
        self.assertIn(
            (SKILL, "changed_since_rendered"),
            [(item["name"], item["reason"]) for item in result["prune_skipped_skills"]],
        )

    def test_an_unchanged_flat_leftover_is_removed_and_the_summary_says_so(self) -> None:
        install_skill_pack(self.paths, profile="core")
        flat = self.paths.skills_dir / SKILL
        shutil.copytree(self.skill_file().parent, flat)
        install_skill_pack(self.paths, profile="core")

        def claim_rendered(manifest: dict) -> None:
            for record in manifest["skills"]:
                if record["path"] == f"{SKILL}/SKILL.md":
                    record[RENDER_MARK_KEY] = record["sha256"]

        self.edit_manifest(claim_rendered)
        result = install_skill_pack(self.paths, profile="core")

        self.assertFalse(flat.exists())
        self.assertEqual(result["flat_layout_skills_removed"], [SKILL])
        self.assertEqual(
            [(item["path"], item["rule"], item["kind"]) for item in result["removed_skill_directories"]],
            [(f"{SKILL}/SKILL.md", "render_mark", "flat_copy")],
        )
        self.assertEqual(
            self.removal_lines(result),
            [
                f"Removed {SKILL}/, a top-level directory named for {SKILL}, which now renders in its category "
                "directory, with every file in it. OMH rendered it there, its SKILL.md is unchanged, and no "
                "Hermes write is recorded under it."
            ],
        )

    def test_a_retired_skill_with_no_edit_is_removed_and_the_summary_says_so(self) -> None:
        # Probe G5: a person's files beside an unchanged SKILL.md, no ledger
        # row. The mark proves OMH rendered the directory and the whole of it
        # goes; the line says what was checked and that every file went,
        # never that nothing in the directory changed.
        install_skill_pack(self.paths, profile="core")
        directory = self.paths.skills_dir / "tracker" / "omh-old-skill"
        (directory / "references").mkdir(parents=True)
        (directory / "SKILL.md").write_text("---\nname: omh-old-skill\n---\nretired\n", encoding="utf-8")
        (directory / "references" / "rail.md").write_text("I rewrote this\n", encoding="utf-8")
        (directory / "work.md").write_text("weeks of work\n", encoding="utf-8")
        sha = sha256_file(directory / "SKILL.md")
        self.edit_manifest(
            lambda manifest: manifest["skills"].append(
                {"name": self.NAME, "path": self.REL, "sha256": sha, "source": "builtin", RENDER_MARK_KEY: sha}
            )
        )

        result = install_skill_pack(self.paths, profile="core")

        self.assertFalse(directory.exists())
        self.assertEqual(result["pruned_skills"], [self.NAME])
        self.assertEqual(
            self.removal_lines(result),
            [
                f"Removed tracker/omh-old-skill/ ({self.NAME}), a skill the catalog does not ship, with every file "
                "in it. OMH rendered it there, its SKILL.md is unchanged, and no Hermes write is recorded under it."
            ],
        )
        for language in ("ko", "ja", "zh"):
            with self.subTest(language=language):
                [line] = self.removal_lines(result, language, "tracker/omh-old-skill/")
                self.assertIn(MESSAGES[language]["removal_render_mark_note"], line)
                self.assertNotIn(MESSAGES[language]["removal_earlier_manifest_note"], line)

    def test_a_removal_under_a_manifest_from_before_marks_does_not_claim_omh_rendered_it(self) -> None:
        # Probe case 1, a real upgrade: an earlier manifest recorded a
        # person's top-level skill, and the earlier rule removes it. Nothing
        # tells OMH whether it rendered that directory, so the line says so.
        install_skill_pack(self.paths, profile="core")
        own = self.own_skill("my-own-top")
        install_skill_pack(self.paths, profile="core")
        self.edit_manifest(legacy=True)

        result = install_skill_pack(self.paths, profile="core")

        self.assertFalse(own.exists())
        self.assertEqual(
            [(item["path"], item["rule"], item["kind"]) for item in result["removed_skill_directories"]],
            [("my-own-top/SKILL.md", "earlier_manifest", "retired_skill")],
        )
        self.assertEqual(
            self.removal_lines(result),
            [
                "Removed my-own-top/ (my-own-top), a skill the catalog does not ship, with every file in it. The install "
                "manifest is from an earlier release, so OMH cannot tell whether it rendered it."
            ],
        )
        for language in ("ko", "ja", "zh"):
            with self.subTest(language=language):
                [line] = self.removal_lines(result, language, "my-own-top/")
                self.assertIn(MESSAGES[language]["removal_earlier_manifest_note"], line)
                self.assertNotIn(MESSAGES[language]["removal_render_mark_note"], line)

    def test_a_flat_copy_at_the_top_of_a_category_names_only_the_files_that_went(self) -> None:
        # `ultrawork` is a skill name and a category: only the SKILL.md and
        # references/ at the top of the category go, so the line may not say
        # every file in the directory did.
        install_skill_pack(self.paths, profile="full")
        category = self.paths.skills_dir / skill_install_relative_dir("ultrawork").parts[0]
        outside = self.root / "kept-references"
        outside.mkdir()
        # Without references/, with a real one, and with a link the removal keeps.
        for references, removed in ((None, ["SKILL.md"]), ("dir", ["SKILL.md", "references/"]), ("link", ["SKILL.md"])):
            with self.subTest(references=references):
                (category / "SKILL.md").write_text("---\nname: ultrawork\n---\nstale pre-relabel render\n", encoding="utf-8")
                if references == "dir":
                    (category / "references").mkdir()
                    (category / "references" / "old.md").write_text("old\n", encoding="utf-8")
                elif references == "link":
                    try:
                        (category / "references").symlink_to(outside, target_is_directory=True)
                    except OSError:
                        self.skipTest("symlinks unavailable")
                install_skill_pack(self.paths, profile="full")
                self.edit_manifest(legacy=True)

                result = install_skill_pack(self.paths, profile="full")

                self.assertFalse((category / "SKILL.md").exists())
                self.assertEqual((category / "references").is_symlink(), references == "link")
                self.assertEqual(
                    [
                        (item["path"], item["rule"], item["kind"], item["removed_entries"])
                        for item in result["removed_skill_directories"]
                    ],
                    [(f"{category.name}/SKILL.md", "earlier_manifest", "flat_copy_in_category", removed)],
                )
                [line] = self.removal_lines(result)
                named = ", ".join(f"{category.name}/{entry}" for entry in removed)
                self.assertTrue(line.startswith(f"Removed {named}, named for ultrawork,"), line)
                self.assertEqual(f"{category.name}/references/" in line, references == "dir")
                self.assertIn(f"the rest of {category.name}/ stays", line)
                self.assertNotIn("every file", line)
                if references == "link":
                    (category / "references").unlink()

    def test_a_recorded_hermes_write_keeps_it_after_the_file_changed_again(self) -> None:
        # Probe case 3: one recorded write_file row, then the person added to
        # that file. No write is still on disk, but one is recorded under the
        # directory, which is what "changed since OMH rendered it" counts.
        install_skill_pack(self.paths, profile="core")
        directory = self.paths.skills_dir / "tracker" / "omh-old-skill"
        (directory / "references").mkdir(parents=True)
        (directory / "SKILL.md").write_text("---\nname: omh-old-skill\n---\nretired\n", encoding="utf-8")
        lessons = directory / "references" / "lessons.md"
        lessons.write_text("lesson Hermes wrote\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(lessons, action="write_file"))
        with lessons.open("a", encoding="utf-8") as handle:
            handle.write("plus what I added by hand\n")
        sha = sha256_file(directory / "SKILL.md")
        self.edit_manifest(
            lambda manifest: manifest["skills"].append(
                {"name": self.NAME, "path": self.REL, "sha256": sha, "source": "builtin", RENDER_MARK_KEY: sha}
            )
        )

        for _ in range(2):
            result = install_skill_pack(self.paths, profile="core")
            self.assertTrue(lessons.is_file())
            self.assertEqual(result["pruned_skills"], [])
            self.assertNotIn("removed_skill_directories", result)
            self.assertIn(
                (self.REL, "changed_since_rendered"),
                [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
            )

    def test_force_names_each_removed_directory_once_by_path(self) -> None:
        # Probe G6: one retired name at two directories, one changed and one
        # not. Both go under --force; the changed one has the --force line and
        # the unchanged one its own, so two directories read as two removals.
        install_skill_pack(self.paths, profile="core")
        changed = self.paths.skills_dir / "tracker" / "omh-old-skill"
        unchanged = self.paths.skills_dir / "omh-old-skill"
        records = []
        for directory in (changed, unchanged):
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text("---\nname: omh-old-skill\n---\nretired\n", encoding="utf-8")
            sha = sha256_file(directory / "SKILL.md")
            rel = f"{directory.relative_to(self.paths.skills_dir).as_posix()}/SKILL.md"
            records.append({"name": self.NAME, "path": rel, "sha256": sha, "source": "builtin", RENDER_MARK_KEY: sha})
        with (changed / "SKILL.md").open("a", encoding="utf-8") as handle:
            handle.write("\nchanged by hand\n")
        records[0]["sha256"] = sha256_file(changed / "SKILL.md")
        self.edit_manifest(lambda manifest: manifest["skills"].extend(records))

        result = install_skill_pack(self.paths, profile="core", force=True)

        self.assertFalse(changed.exists())
        self.assertFalse(unchanged.exists())
        self.assertEqual([item["path"] for item in result["force_pruned_skills"]], [self.REL])
        lines = self.removal_lines(result)
        self.assertEqual(len(lines), 2, lines)
        [forced] = [line for line in lines if "because --force was given" in line]
        [plain] = [line for line in lines if line != forced]
        self.assertTrue(forced.startswith(f"Removed {self.NAME} at {self.REL} because --force was given"), lines)
        self.assertTrue(plain.startswith(f"Removed omh-old-skill/ ({self.NAME}), a skill the catalog"), lines)
        self.assertIn("OMH rendered it there, its SKILL.md is unchanged", plain)

    def test_force_reads_the_ledger_once_for_every_loss_list(self) -> None:
        # One read per install: --force used to read the whole ledger again
        # for a replaced edit, again for a skill it clears, and again for every
        # directory it removed.
        directory, _lessons = self.retired_skill(kept=False)
        self.hermes_patch(self.skill_file())
        self.store_blob(self.skill_file())
        doctor_dir = self.skill_file(name="doctor").parent
        with (doctor_dir / "SKILL.md").open("a", encoding="utf-8") as handle:
            handle.write("\nmy hand edit\n")
        added = doctor_dir / "references" / "hermes-added.md"
        added.parent.mkdir(exist_ok=True)
        added.write_text("a file Hermes added\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(added, action="write_file"))
        self.store_blob(added)
        ledger = self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH
        rows = ledger.read_text(encoding="utf-8").splitlines()
        reads: list[Path] = []
        parsed: list[str] = []
        read_bytes = Path.read_bytes
        row_writes = curator_ledger_module._row_writes

        def counting(path: Path) -> bytes:
            if path.name == CURATOR_LEDGER_RELATIVE_PATH.name:
                reads.append(path)
            return read_bytes(path)

        def counting_rows(line: str, source: str):
            parsed.append(line)
            return row_writes(line, source)

        with (
            mock.patch.object(Path, "read_bytes", counting),
            mock.patch.object(curator_ledger_module, "_row_writes", side_effect=counting_rows),
        ):
            result = install_skill_pack(self.paths, profile="core", force=True)

        self.assertEqual(reads, [ledger])
        # Each row parsed once, though the loss lists need rows outside the packs too.
        self.assertEqual(sorted(parsed), sorted(rows))
        self.assertEqual(len(rows), 3)
        self.assertFalse(directory.exists())
        self.assertEqual(
            sorted((item["name"], item["disposition"], tuple(item["ledger_copy"])) for item in result["hermes_edited_skills"]),
            [("doctor", "replaced", ("references/hermes-added.md",)), (SKILL, "replaced", ("SKILL.md",))],
        )
        [forced] = result["force_pruned_skills"]
        self.assertEqual((forced["name"], forced["ledger_copy"]), (self.NAME, ["references/lessons.md"]))
        # Every ledger was read, so no loss list is marked or said to be partial.
        self.assertNotIn("unreadable_ledgers", result)
        self.assertFalse(any(LEDGER_COPY_PARTIAL_KEY in item for item in [*result["hermes_edited_skills"], forced]))
        self.assertNotIn("could not be read in full", self.summary(result))

    def test_a_force_loss_list_names_the_ledger_that_could_not_be_read(self) -> None:
        # #2111 follow-up: a --force removal of a changed directory lists what
        # the readable ledgers hold a copy of. A bot's ledger that could not be
        # read may hold more, so the report names it beside the list.
        directory, _lessons = self.retired_skill(kept=False)
        bot_ledger = self.paths.hermes_home / "profiles" / "bot" / CURATOR_LEDGER_RELATIVE_PATH
        bot_ledger.parent.mkdir(parents=True)
        bot_ledger.write_bytes(b"\xff\xfe not utf-8\n")

        forced = install_skill_pack(self.paths, profile="core", force=True)

        self.assertFalse(directory.exists())
        [item] = forced["force_pruned_skills"]
        self.assertEqual(
            (item["reason"], item["ledger_copy"], item["discarded"]),
            ("changed_since_rendered", ["references/lessons.md"], ["SKILL.md"]),
        )
        self.assertIs(item[LEDGER_COPY_PARTIAL_KEY], True)
        [unread] = forced["unreadable_ledgers"]
        self.assertTrue(unread.startswith(f"{bot_ledger}: "), unread)
        lines = [line.strip() for line in self.summary(forced).splitlines()]
        discarded = lines.index(f"Removing {self.NAME} deleted files Hermes' curator ledger holds no copy of: SKILL.md.")
        self.assertEqual(
            lines[discarded + 1],
            f"Hermes' curator ledger could not be read in full ({unread}), so what OMH says above about "
            f"copies of {self.NAME} counts only what it could read: a file it says has no copy may still have one there.",
        )
        for language in ("ko", "ja", "zh"):
            with self.subTest(language=language):
                [line] = [line for line in self.summary(forced, language).splitlines() if unread in line]
                self.assertIn(self.NAME, line)
        # The error text stays in the result, like every other unread name.
        self.assertNotIn(unread.encode(), self.paths.manifest_path.read_bytes())
        complete = {key: value for key, value in item.items() if key != LEDGER_COPY_PARTIAL_KEY}
        self.assertNotIn("could not be read in full", self.summary({**forced, "force_pruned_skills": [complete]}))

    def test_many_torn_lines_name_their_ledger_once_after_every_loss_list(self) -> None:
        # Fail-closed reads used to add one unread entry per torn line, and the
        # summary printed the whole list beside every loss list: 40 torn lines
        # made a line of over 9,000 characters, once per replaced or removed skill.
        directory, _lessons = self.retired_skill(kept=False)
        self.hermes_patch(self.skill_file())
        self.store_blob(self.skill_file())
        bot_ledger = self.paths.hermes_home / "profiles" / "bot" / CURATOR_LEDGER_RELATIVE_PATH
        bot_ledger.parent.mkdir(parents=True)
        # A row cut short after naming a file under the pack.
        torn = json.dumps({"path": os.path.join(os.path.realpath(self.paths.skills_dir), "x", "SKILL.md")})[:-3]
        bot_ledger.write_text("".join(f"{torn}\n" for _ in range(40)), encoding="utf-8")

        forced = install_skill_pack(self.paths, profile="core", force=True)

        self.assertFalse(directory.exists())
        self.assertEqual(forced["unreadable_ledgers"], [f"{bot_ledger}: 40 lines are not ledger rows (the first is line 1)"])
        summary = self.summary(forced)
        [line] = [line.strip() for line in summary.splitlines() if "could not be read in full" in line]
        self.assertEqual(
            line,
            f"Hermes' curator ledger could not be read in full ({forced['unreadable_ledgers'][0]}), so what OMH says "
            f"above about copies of {SKILL}, {self.NAME} counts only what it could read: a file it says has no copy "
            "may still have one there.",
        )
        self.assertEqual(summary.count(str(bot_ledger)), 1)

    def test_strict_security_keeps_a_retired_skill_with_a_hermes_edit_under_force(self) -> None:
        # OMH_SECURITY=strict takes away --force's override of a local
        # change; a retired skill holding a Hermes write is one.
        directory, lessons = self.retired_skill(kept=False)

        with mock.patch.dict(os.environ, {"OMH_SECURITY": "strict"}):
            result = install_skill_pack(self.paths, profile="core", force=True)

        self.assertTrue(lessons.is_file())
        self.assertEqual(result["pruned_skills"], [])
        self.assertNotIn("force_pruned_skills", result)
        self.assertIn(
            (self.REL, "changed_since_rendered"),
            [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
        )

    def marked_retired_skill(self) -> Path:
        """A retired skill OMH rendered, its SKILL.md unchanged and nothing else in it."""
        install_skill_pack(self.paths, profile="core")
        directory = self.paths.skills_dir / "tracker" / "omh-old-skill"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text("---\nname: omh-old-skill\n---\nretired\n", encoding="utf-8")
        sha = sha256_file(directory / "SKILL.md")
        self.edit_manifest(
            lambda manifest: manifest["skills"].append(
                {"name": self.NAME, "path": self.REL, "sha256": sha, "source": "builtin", RENDER_MARK_KEY: sha}
            )
        )
        return directory

    def corrupt_ledger(self) -> Path:
        """A ledger that exists and cannot be decoded: OMH cannot tell what Hermes wrote."""
        ledger = self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_bytes(b"\xff\xfe not a ledger row\n")
        return ledger

    def test_a_missing_ledger_means_no_writes_and_the_removal_proceeds(self) -> None:
        directory = self.marked_retired_skill()
        self.assertFalse((self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH).exists())

        result = install_skill_pack(self.paths, profile="core")

        self.assertFalse(directory.exists())
        self.assertEqual(result["pruned_skills"], [self.NAME])
        self.assertNotIn("unreadable_ledgers", result)

    def test_an_unreadable_ledger_keeps_a_directory_omh_would_remove(self) -> None:
        # Fail closed: an unreadable ledger may record a Hermes write under
        # the directory, and a removal is final.
        directory = self.marked_retired_skill()
        ledger = self.corrupt_ledger()

        for _ in range(2):
            result = install_skill_pack(self.paths, profile="core")
            self.assertTrue(directory.is_dir())
            self.assertEqual(result["pruned_skills"], [])
            self.assertNotIn("removed_skill_directories", result)
            self.assertIn(
                (self.REL, "ledger_unreadable"),
                [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
            )
            [unread] = result["unreadable_ledgers"]
            self.assertTrue(unread.startswith(f"{ledger}: "), unread)
            [line] = self.removal_lines(result, needle=self.REL)
            self.assertIn(f"Hermes' curator ledger could not be read ({unread})", line)
            self.assertIn("A run with --force removes it, except under OMH_SECURITY=strict.", line)
        # Persisted as the standing verdict, without the error text.
        standing = read_manifest(self.paths.manifest_path)["prune_skipped_skills"]
        self.assertIn({"name": "omh-old-skill", "path": self.REL, "reason": "ledger_unreadable"}, standing)
        self.assertNotIn(b"unreadable_ledgers", self.paths.manifest_path.read_bytes())
        for language in ("ko", "ja", "zh"):
            with self.subTest(language=language):
                [line] = self.removal_lines(result, language, self.REL)
                self.assertIn(unread, line)
                self.assertIn("OMH_SECURITY=strict", line)

        with mock.patch.dict(os.environ, {"OMH_SECURITY": "strict"}):
            strict = install_skill_pack(self.paths, profile="core", force=True)
        self.assertTrue(directory.is_dir())
        self.assertNotIn("force_pruned_skills", strict)

        forced = install_skill_pack(self.paths, profile="core", force=True)

        self.assertFalse(directory.exists())
        # Nothing is claimed about what the ledger holds: it could not be read.
        self.assertEqual(forced["force_pruned_skills"], [{"name": "omh-old-skill", "path": self.REL, "reason": "ledger_unreadable"}])
        [line] = self.removal_lines(forced)
        self.assertEqual(
            line,
            f"Removed omh-old-skill at {self.REL} because --force was given: Hermes' curator ledger could not be "
            f"read ({unread}), so OMH could not tell whether Hermes wrote under it or whether the ledger holds a copy of it.",
        )
        for language in ("ko", "ja", "zh"):
            with self.subTest(language=language):
                [line] = self.removal_lines(forced, language, self.REL)
                self.assertIn(unread, line)

    def test_a_ledger_line_under_the_pack_that_is_not_a_row_keeps_it_too(self) -> None:
        # A torn row that names a file in the directory may be the write that
        # changed it. A torn line naming no path in the pack says nothing about it.
        directory = self.marked_retired_skill()
        elsewhere = json.dumps(os.path.realpath(self.root / "elsewhere" / "SKILL.md"))
        ledger = _append_ledger(self.paths.hermes_home, '{"id": "x", "after": [{"path": ' + elsewhere)

        result = install_skill_pack(self.paths, profile="core")

        self.assertFalse(directory.exists())
        self.assertNotIn("unreadable_ledgers", result)

        directory = self.marked_retired_skill()
        named = json.dumps(os.path.realpath(directory / "references" / "lessons.md"))
        _append_ledger(self.paths.hermes_home, '{"id": "y", "after": [{"path": ' + named)

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(directory.is_dir())
        self.assertEqual(result["unreadable_ledgers"], [f"{ledger}: line 2 is not a ledger row"])
        self.assertIn(
            (self.REL, "ledger_unreadable"),
            [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
        )
        # The reader every other caller uses is unchanged: the line is skipped, not reported.
        self.assertEqual(read_ledger_writes(self.paths.hermes_home, within=[self.paths.skills_dir]), ([], []))

    def test_a_recorded_write_outranks_an_unreadable_line(self) -> None:
        # What is known is said: the ledger records a write under the
        # directory, so --force can still name the copies it holds.
        directory, lessons = self.retired_skill(kept=False)
        named = json.dumps(os.path.realpath(directory / "references" / "torn.md"))
        _append_ledger(self.paths.hermes_home, '{"id": "z", "after": [{"path": ' + named)

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(lessons.is_file())
        self.assertIn("unreadable_ledgers", result)
        self.assertIn(
            (self.REL, "changed_since_rendered"),
            [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
        )

    @unittest.skipIf(os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0, "POSIX permissions, not as root")
    def test_a_profiles_directory_that_cannot_be_listed_keeps_it(self) -> None:
        # Probe L5: every bot-profile ledger sits behind the listing; a bot's
        # write under the directory is unknown, not absent.
        directory = self.marked_retired_skill()
        (directory / "references").mkdir()
        lessons = directory / "references" / "lessons.md"
        lessons.write_text("lesson the bot's Hermes wrote\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home / "profiles" / "bot", _ledger_row(lessons, action="write_file"))
        profiles = self.paths.hermes_home / "profiles"
        os.chmod(profiles, 0)
        self.addCleanup(os.chmod, profiles, stat.S_IRWXU)

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(lessons.is_file())
        self.assertIn(
            (self.REL, "ledger_unreadable"),
            [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
        )
        [unread] = result["unreadable_ledgers"]
        self.assertTrue(unread.startswith(f"{profiles}: "), unread)
        # Every other reader of the profile list behaves as before.
        self.assertEqual(hermes_profile_dirs(self.paths.hermes_home), [])
        self.assertEqual(curator_ledger_paths(self.paths.hermes_home), [self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH])
        self.assertEqual(read_ledger_writes(self.paths.hermes_home, within=[self.paths.skills_dir]), ([], []))

    @unittest.skipIf(os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0, "POSIX permissions, not as root")
    def test_a_profile_entry_that_cannot_be_read_does_not_hide_the_others(self) -> None:
        # Probe P1: one entry whose stat fails used to empty the whole list,
        # so a readable bot ledger was never read.
        directory = self.marked_retired_skill()
        profiles = self.paths.hermes_home / "profiles"
        (profiles / "bot").mkdir(parents=True)
        locked = self.root / "locked"
        (locked / "inner").mkdir(parents=True)
        (profiles / "zz-link").symlink_to(locked / "inner", target_is_directory=True)
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, stat.S_IRWXU)

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(directory.is_dir())
        self.assertIn(
            (self.REL, "ledger_unreadable"),
            [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
        )
        [unread] = result["unreadable_ledgers"]
        self.assertTrue(unread.startswith(f"{profiles / 'zz-link'}: "), unread)

        (directory / "references").mkdir()
        lessons = directory / "references" / "lessons.md"
        lessons.write_text("lesson the bot's Hermes wrote\n", encoding="utf-8")
        _append_ledger(profiles / "bot", _ledger_row(lessons, action="write_file"))

        result = install_skill_pack(self.paths, profile="core")

        # The readable bot ledger is read: its write is known, and outranks the unknown.
        self.assertIn(
            (self.REL, "changed_since_rendered"),
            [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
        )

    def test_a_write_recorded_before_the_last_render_keeps_it_and_says_so(self) -> None:
        # Probe H1: Hermes wrote, a --force render put OMH's files back, then
        # the skill retired. The row still counts; the line may not say the
        # directory changed since OMH rendered it.
        directory = self.marked_retired_skill()
        gone = directory / "references" / "lessons.md"
        _append_ledger(self.paths.hermes_home, _change_row(gone, after_sha="c" * 64, action="write_file", ts="2026-09-01T00:00:00+00:00"))

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(directory.is_dir())
        [line] = self.removal_lines(result, needle=self.REL)
        self.assertIn(
            "its SKILL.md changed since OMH rendered it there, or a Hermes write is recorded under it (OMH cannot "
            "tell whether before or after it last rendered there)",
            line,
        )
        self.assertNotIn("it changed since", line)

        forced = install_skill_pack(self.paths, profile="core", force=True)

        [line] = [line for line in self.removal_lines(forced) if "because --force was given" in line]
        self.assertIn("its SKILL.md changed since OMH rendered it there, or a Hermes write is recorded under it", line)
        self.assertNotIn("it had changed", line)

    def test_an_unterminated_last_line_keeps_it(self) -> None:
        # Probe L3b: an append in progress, cut before any path, names nothing
        # in the pack, and may be the write that changed the directory.
        directory = self.marked_retired_skill()
        ledger = _append_ledger(self.paths.hermes_home, _ledger_row(self.root / "elsewhere.md", action="write_file"))
        torn = '{"id": "t", "ts": "2026-10-13T02:00:00+00:00", "actor": "curator", "action": "write_file", "skill": "omh-old-skill", "before": [], '
        with ledger.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(torn)

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(directory.is_dir())
        self.assertEqual(result["unreadable_ledgers"], [f"{ledger}: line 2 is unterminated, so an append may be in progress"])
        self.assertIn(
            (self.REL, "ledger_unreadable"),
            [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
        )
        # Every other reader skips it as before.
        self.assertEqual(read_ledger_writes(self.paths.hermes_home, within=[self.paths.skills_dir]), ([], []))

        with ledger.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write('"after": []}\n')

        result = install_skill_pack(self.paths, profile="core")

        self.assertFalse(directory.exists())
        self.assertNotIn("unreadable_ledgers", result)

    def test_a_row_holding_a_raw_line_separator_is_one_row(self) -> None:
        # Hermes writes rows with ensure_ascii=False and splits them on "\n"
        # alone; str.splitlines would cut a row at U+2028, U+2029 or U+0085.
        directory = self.marked_retired_skill()
        (directory / "references").mkdir()
        odd = directory / "references" / "note\u2028\u2029\u0085.md"
        odd.write_text("lesson\n", encoding="utf-8")
        real = os.path.realpath(odd)
        row = json.dumps(
            {
                "id": "u", "ts": "2026-10-13T03:00:00+00:00", "actor": "curator", "action": "write_file",
                "skill": "omh-old-skill", "evidence": {}, "before": [], "after": [{"path": real, "sha256": sha256_file(odd)}],
            },
            ensure_ascii=False,
        )
        self.assertIn("\u2028", row)
        _append_ledger(self.paths.hermes_home, row)

        writes, unread = read_ledger_writes(self.paths.hermes_home, within=[self.paths.skills_dir])
        self.assertEqual(([write.path for write in writes], unread), ([real], []))

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(odd.is_file())
        self.assertNotIn("unreadable_ledgers", result)
        self.assertIn(
            (self.REL, "changed_since_rendered"),
            [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
        )

    def test_an_unreadable_ledger_keeps_a_flat_leftover_too(self) -> None:
        install_skill_pack(self.paths, profile="core")
        flat = self.paths.skills_dir / SKILL
        shutil.copytree(self.skill_file().parent, flat)
        install_skill_pack(self.paths, profile="core")

        def claim_rendered(manifest: dict) -> None:
            for record in manifest["skills"]:
                if record["path"] == f"{SKILL}/SKILL.md":
                    record[RENDER_MARK_KEY] = record["sha256"]

        self.edit_manifest(claim_rendered)
        self.corrupt_ledger()

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(flat.is_dir())
        self.assertEqual(result["flat_layout_skills_removed"], [])
        self.assertIn(
            (f"{SKILL}/SKILL.md", "ledger_unreadable"),
            [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]],
        )

    def test_an_unreadable_ledger_changes_nothing_else(self) -> None:
        # The live skills update as before, and a manifest from before marks
        # keeps its own rule, which never read the ledger.
        install_skill_pack(self.paths, profile="core")
        own = self.own_skill("my-own-top")
        install_skill_pack(self.paths, profile="core")
        skill_file = self.skill_file()
        template = next(template for template in builtin_skill_templates() if template.name == SKILL)
        skill_file.write_text(template.content + "\nolder render\n", encoding="utf-8")
        current = f"{skill_install_relative_dir(SKILL).as_posix()}/SKILL.md"

        def older_render(manifest: dict) -> None:
            for record in manifest["skills"]:
                if record["path"] == current:
                    record["sha256"] = sha256_file(skill_file)

        self.edit_manifest(older_render, legacy=True)
        self.corrupt_ledger()

        result = install_skill_pack(self.paths, profile="core")

        self.assertEqual(skill_file.read_text(encoding="utf-8"), template.content)
        self.assertNotIn("hermes_edited_skills", result)
        self.assertFalse(own.exists())
        self.assertEqual(
            [(item["path"], item["rule"]) for item in result["removed_skill_directories"]],
            [("my-own-top/SKILL.md", "earlier_manifest")],
        )

    def test_strict_security_keeps_a_changed_flat_leftover_under_force(self) -> None:
        install_skill_pack(self.paths, profile="core")
        flat = self.paths.skills_dir / SKILL
        shutil.copytree(self.skill_file().parent, flat)
        install_skill_pack(self.paths, profile="core")
        with (flat / "SKILL.md").open("a", encoding="utf-8") as handle:
            handle.write("\nchanged by hand\n")

        def claim_rendered(manifest: dict) -> None:
            for record in manifest["skills"]:
                if record["path"] == f"{SKILL}/SKILL.md":
                    record[RENDER_MARK_KEY] = record["sha256"]
                    record["sha256"] = sha256_file(flat / "SKILL.md")

        self.edit_manifest(claim_rendered)

        with mock.patch.dict(os.environ, {"OMH_SECURITY": "strict"}):
            result = install_skill_pack(self.paths, profile="core", force=True)

        self.assertTrue(flat.is_dir())
        self.assertEqual(result["flat_layout_skills_removed"], [])
        self.assertNotIn("force_pruned_skills", result)


class ManifestStabilityTests(_Home):
    """An install over an unchanged disk writes the same manifest bytes.

    `omh update` reads the manifest hash to say whether the workflow content
    changed, so a manifest rewritten on an unchanged disk reports a change
    that did not happen.
    """

    def test_identical_installs_after_a_directory_appears_write_identical_manifests(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.own_skill("guide/my-own-skill")

        results, snapshots = [], []
        for _ in range(3):
            results.append(install_skill_pack(self.paths, profile="core"))
            snapshots.append(self.paths.manifest_path.read_bytes())

        self.assertEqual(snapshots[1], snapshots[0])
        self.assertEqual(snapshots[2], snapshots[1])
        self.assertNotIn(b"newly_seen", snapshots[0])
        listed = [
            [(item["path"], item["reason"]) for item in result["prune_skipped_skills"]] for result in results
        ]
        self.assertEqual(listed, [[("guide/my-own-skill/SKILL.md", "not_rendered_by_omh")]] * 3)
        out = []
        for result in results:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                setup_commands._print_install_summary({"skills": [], **result}, command="install")
            out.append(buffer.getvalue())
        # Named by the install that first records it, then only in the JSON.
        self.assertIn("Left my-own-skill at guide/my-own-skill/SKILL.md in place", out[0])
        self.assertNotIn("my-own-skill", out[1])
        self.assertNotIn("my-own-skill", out[2])


class CaseVariantRenderDirectoryTests(_Home):
    """A render directory renamed to another letter case, where that is still the same directory.

    macOS's default filesystem is case-insensitive: `guide/OMH-ROUTING` IS
    `guide/omh-routing`. The manifest used to record it by its on-disk
    spelling, which is a name the catalog does not ship, so the second plain
    install rendered into the directory and then pruned it as an orphan
    (measured on origin/main d3f5d610d: SKILL.md gone, `pruned_skills`
    `['OMH-ROUTING']`, back on the third install). Skipped where the
    filesystem tells the two spellings apart.
    """

    def rename_render_dir(self) -> Path:
        install_skill_pack(self.paths, profile="core")
        render = self.skill_file().parent
        variant = render.parent / render.name.upper()
        render.rename(variant)
        if not render.exists():
            self.skipTest("case-sensitive filesystem: the renamed directory is a different directory")
        return variant

    def skill_record(self) -> list[tuple[str, str]]:
        return [
            (record["name"], record["path"])
            for record in read_manifest(self.paths.manifest_path)["skills"]
            if "routing" in record["path"].lower()
        ]

    def test_the_skill_survives_plain_installs_and_force_and_stays_healthy(self) -> None:
        self.rename_render_dir()
        render_path = f"{skill_install_relative_dir(SKILL).as_posix()}/SKILL.md"
        template = next(template for template in builtin_skill_templates() if template.name == SKILL)

        for force in (False, False, True, False):
            with self.subTest(force=force):
                result = install_skill_pack(self.paths, profile="core", force=force)
                self.assertEqual(self.skill_file().read_text(encoding="utf-8"), template.content)
                self.assertEqual(result["pruned_skills"], [])
                # Recorded as the catalog skill at the path an install renders,
                # so nothing is reported, this time or any later one.
                self.assertNotIn("prune_skipped_skills", result)
                self.assertEqual(self.skill_record(), [(SKILL, render_path)])

        checks = {check.name: check for check in run_doctor(self.paths)}
        self.assertTrue(checks["local_modifications"].ok, checks["local_modifications"].message)
        self.assertTrue(checks[f"skill:{SKILL}"].ok)
        self.assertNotIn("missing_managed_skills", checks)

    def test_a_manifest_that_recorded_the_other_spelling_does_not_prune_the_skill(self) -> None:
        # What an install before this guard wrote after the rename.
        variant = self.rename_render_dir()
        manifest = read_manifest(self.paths.manifest_path)
        for record in manifest["skills"]:
            if record["name"] == SKILL:
                record["name"] = variant.name
                record["path"] = f"{variant.parent.name}/{variant.name}/SKILL.md"
        self.paths.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = install_skill_pack(self.paths, profile="core")

        self.assertTrue(self.skill_file().is_file())
        self.assertEqual(result["pruned_skills"], [])
        self.assertEqual(
            result["prune_skipped_skills"],
            [
                {
                    "name": variant.name,
                    "path": f"{variant.parent.name}/{variant.name}/SKILL.md",
                    "reason": "same_directory_as_a_rendered_skill",
                }
            ],
        )
        # Reported once: this install recorded the skill properly.
        self.assertNotIn("prune_skipped_skills", install_skill_pack(self.paths, profile="core"))

    def test_a_hermes_edit_spelled_by_the_on_disk_case_is_recognised(self) -> None:
        # SF3 / probe Q7a: the ledger row spells the directory as it is on
        # disk, the record spells it as the install renders it.
        variant = self.rename_render_dir()
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(variant / "SKILL.md")
        self.store_blob(variant / "SKILL.md")
        edited = (variant / "SKILL.md").read_bytes()

        kept = install_skill_pack(self.paths, profile="core")

        self.assertEqual([(item["name"], item["disposition"]) for item in kept["hermes_edited_skills"]], [(SKILL, "kept")])
        self.assertEqual(self.skill_file().read_bytes(), edited)

        forced = install_skill_pack(self.paths, profile="core", force=True)

        self.assertEqual(
            [(item["disposition"], item["ledger_copy"]) for item in forced["hermes_edited_skills"]],
            [("replaced", ["SKILL.md"])],
        )
        self.assertNotEqual(self.skill_file().read_bytes(), edited)

    def test_a_folded_name_match_alone_does_not_rename_a_record(self) -> None:
        # Where the filesystem tells the spellings apart, the other-case
        # directory is another directory and keeps its own record. Asked of
        # the filesystem (`same_directory`), stubbed here so this runs on
        # either kind.
        relative = skill_install_relative_dir(SKILL)
        other = self.paths.skills_dir / relative.parent / relative.name.upper()
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("someone else's\n", encoding="utf-8")

        with mock.patch.object(manifest_module, "same_directory", return_value=False):
            records = manifest_module.skill_records(self.paths.skills_dir, "builtin")

        self.assertEqual(
            [(record.name, record.path) for record in records],
            [(other.name, f"{relative.parent.as_posix()}/{other.name}/SKILL.md")],
        )

    def test_two_paths_that_cannot_be_read_are_not_the_same_directory(self) -> None:
        self.assertFalse(same_directory(self.root / "absent-a", self.root / "absent-b"))
        self.assertTrue(same_directory(self.root, self.root / "."))


class CompanionNameTests(unittest.TestCase):
    """The refusal suggests a companion name nothing would read as OMH's."""

    def test_the_suggestion_drops_the_label_prefix(self) -> None:
        message = skill_ownership.managed_skill_edit_message("omh-plan", "patch", "omh-workflow-learning")
        self.assertIn(
            "skill_manage action=create with a different name, such as 'plan-notes', holding a short companion "
            "that names 'omh-plan' and the case it adds rather than a copy of it",
            message,
        )
        self.assertIn(
            "such as 'work-notes', holding a short companion that names 'ulw-work'",
            skill_ownership.managed_skill_edit_message("ulw-work", "edit"),
        )

    def test_no_installed_label_gets_a_suggestion_that_reads_as_omh_s(self) -> None:
        prefixes = (OMH_SKILL_NAME_PREFIX, ULW_SKILL_NAME_PREFIX)
        labels = [omh_skill_display_name(template.name) for template in builtin_skill_templates()]
        # `dag-ulw` is the one label that is its own canonical name (people type `/dag-ulw`); it carries no prefix.
        self.assertEqual({label.split("-", 1)[0] + "-" for label in labels if label != "dag-ulw"}, set(prefixes))
        for label in labels:
            message = skill_ownership.managed_skill_edit_message(label, "patch")
            companion = message.split("such as '", 1)[1].split("'", 1)[0]
            with self.subTest(label=label, companion=companion):
                self.assertTrue(companion.endswith("-notes"))
                self.assertFalse(companion.startswith(prefixes))
                self.assertEqual(canonical_skill_for_label(companion), "")


class RefusalArticleTests(unittest.TestCase):
    def test_each_refused_action_takes_its_article(self) -> None:
        expected = {
            "edit": "so an edit here",
            "patch": "so a patch here",
            "write_file": "so a write_file here",
            "remove_file": "so a remove_file here",
        }
        self.assertEqual(set(expected) | {"delete"}, set(skill_ownership.GUARDED_ACTIONS))
        for action, phrase in expected.items():
            with self.subTest(action=action):
                self.assertIn(phrase, skill_ownership.managed_skill_edit_message("omh-plan", action))


class OwnershipWordingTests(unittest.TestCase):
    """The skills that set OMH's own skills apart tell them by the installed label.

    Held two ways: each of those skills states the label clause once, and no
    body or reference, Hermes or portable, writes the `omh-*` or `ulw-*`
    wildcard the old prefix rule used. A prefix rule worded any other way is
    not caught here.
    """

    OWNERS = ("skill", "rules-distill", "skill-health")

    def test_the_clause_reads_as_written(self) -> None:
        self.assertEqual(
            OMH_MANAGED_SKILL_CLAUSE,
            "An OMH-managed skill is one OMH installed, under a label OMH's catalog renders such as `omh-plan` or "
            "`ulw-work`; a companion skill the person or an agent created is theirs, whatever its name.",
        )

    def test_each_skill_that_treats_omh_skills_apart_states_the_clause(self) -> None:
        hermes = {template.name: template.content for template in builtin_skill_templates()}
        portable = {template.name: template.content for template in agent_skill_templates()}
        for name in self.OWNERS:
            bodies = {"hermes": hermes[name]}
            if omh_skill_display_name(name) in portable:
                bodies["portable"] = portable[omh_skill_display_name(name)]
            for projection, body in bodies.items():
                with self.subTest(skill=name, projection=projection):
                    self.assertEqual(body.count(OMH_MANAGED_SKILL_CLAUSE), 1)
        # `skill` is Hermes-only; the other two are projected too.
        self.assertEqual(
            sorted(name for name in self.OWNERS if omh_skill_display_name(name) in portable),
            ["rules-distill", "skill-health"],
        )

    def test_no_body_writes_an_omh_or_ulw_wildcard(self) -> None:
        """No body or reference, Hermes or portable, writes `omh-*` or `ulw-*`, the old prefix rule's shape."""
        prefix_rule = re.compile(r"\b(?:omh|ulw)-\*")
        bodies = [
            *((f"hermes:{template.name}", template.content) for template in builtin_skill_templates()),
            *((f"portable:{template.name}", template.content) for template in agent_skill_templates()),
            *((f"reference:{t.skill_name}/{t.relative_path}", t.content) for t in builtin_skill_reference_templates()),
            *(
                (f"portable-reference:{t.skill_name}/{t.relative_path}", t.content)
                for t in agent_skill_reference_templates()
            ),
        ]
        self.assertEqual(sorted(name for name, content in bodies if prefix_rule.search(content)), [])


class ConfigScalarTests(unittest.TestCase):
    """A quoted value ends at its closing quote, in the core reader and the plugin's alike."""

    def test_a_trailing_comment_after_a_quoted_value_is_not_part_of_it(self) -> None:
        self.assertEqual(skill_create_dir_selection('skills:\n  create_dir: "/srv/skills"  # shared\n'), "/srv/skills")
        self.assertEqual(memory_provider_selection("memory:\n  provider: 'omh'  # mine\n"), "omh")
        self.assertEqual(skill_ownership.yaml_scalar(" 'it''s' # c"), "it's")
        # Unchanged: an unquoted value still stops at `#`, an unclosed quote is kept as written.
        self.assertEqual(memory_provider_selection("memory:\n  provider: omh # mine\n"), "omh")
        self.assertEqual(memory_provider_selection('memory:\n  provider: "omh\n'), '"omh')

    def test_an_escape_this_reader_does_not_decode_is_not_guessed(self) -> None:
        self.assertIsNone(skill_ownership.yaml_scalar(' "a\\nb"'))
        self.assertIsNone(skill_ownership.yaml_scalar(' "\\u00e9"'))
        self.assertEqual(skill_ownership.yaml_scalar(' "C:\\\\x\\"y"'), 'C:\\x"y')
        # Only a comment may follow a closing quote.
        self.assertIsNone(skill_ownership.yaml_scalar(' "a"b'))
        self.assertEqual(skill_ownership.yaml_scalar(' "a" # note'), "a")
        # Core keeps its answer from before the shared parser for what it cannot read.
        self.assertEqual(memory_provider_selection('memory:\n  provider: "a\\nb"\n'), "a\\nb")

    def test_a_quoted_tilde_is_a_string_not_a_null(self) -> None:
        self.assertEqual(skill_create_dir_selection('skills:\n  create_dir: "~"\n'), "~")
        self.assertFalse(skill_ownership.yaml_null(' "~"'))
        self.assertTrue(skill_ownership.yaml_null(" ~  # unset"))

    def test_a_null_create_dir_is_unset(self) -> None:
        for text in ("skills:\n  create_dir: ~\n", "skills:\n  create_dir: null\n", "skills:\n  create_dir:\n"):
            with self.subTest(text=text):
                self.assertEqual(skill_create_dir_selection(text), "")


class LedgerReaderTests(_Home):
    """`read_ledger_writes` on its own: what it keeps, and what it names unread."""

    def test_a_generator_of_directories_cuts_the_same_as_a_list(self) -> None:
        # `within` was consumed twice, for the line cut and then the write
        # filter, so a generator came back with no writes at all.
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        as_list = read_ledger_writes(self.paths.hermes_home, within=[self.paths.skills_dir])
        self.assertEqual(len(as_list[0]), 1)

        as_generator = read_ledger_writes(self.paths.hermes_home, within=(root for root in [self.paths.skills_dir]))

        self.assertEqual(as_generator, as_list)

    def test_torn_lines_name_their_ledger_once_with_a_count(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        # A row cut short after naming a file under the pack.
        torn = json.dumps({"path": os.path.join(os.path.realpath(self.paths.skills_dir), "x", "SKILL.md")})[:-3]
        ledger = _append_ledger(self.paths.hermes_home, *([torn] * 40))
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(torn)

        writes, unread = read_ledger_writes(self.paths.hermes_home, within=[self.paths.skills_dir], fail_closed=True)

        self.assertEqual(len(writes), 1)
        self.assertEqual(
            unread,
            [
                f"{ledger}: 40 lines are not ledger rows (the first is line 2), and line 42 is unterminated, "
                "so an append may be in progress"
            ],
        )


class DoctorLedgerTests(_Home):
    def _check(self, name: str):
        return next(check for check in run_doctor(self.paths) if check.name == name)

    def test_a_clean_home_reports_no_write(self) -> None:
        install_skill_pack(self.paths, profile="core")
        check = self._check("hermes_skill_edits")
        self.assertTrue(check.ok)
        self.assertEqual(check.severity, "ok")
        self.assertIn("records no skill write under OMH-managed skills", check.message)

    def test_a_hermes_edit_is_a_warning_not_a_local_modification(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        ledger = self.paths.hermes_home / CURATOR_LEDGER_RELATIVE_PATH
        ledger_bytes = ledger.read_bytes()

        checks = {check.name: check for check in run_doctor(self.paths)}

        edits = checks["hermes_skill_edits"]
        self.assertTrue(edits.ok)
        self.assertEqual(edits.severity, "warning")
        self.assertIn(f"{SKILL} at {skill_install_relative_dir(SKILL).as_posix()}/SKILL.md (curator patch", edits.message)
        self.assertIn("records 1 skill write(s) under OMH-managed skills", edits.message)
        self.assertIn("separately named skill", edits.next_action)
        # The installer keeps it, so it no longer blocks doctor.
        self.assertTrue(checks["local_modifications"].ok)
        # Read only.
        self.assertEqual(ledger.read_bytes(), ledger_bytes)

    def test_a_kept_skill_is_not_reported_stale(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        install_skill_pack(self.paths, profile="core")

        checks = {check.name: check for check in run_doctor(self.paths)}

        self.assertTrue(checks["skill_freshness"].ok, checks["skill_freshness"].message)
        self.assertEqual(checks["hermes_skill_edits"].severity, "warning")

    def test_a_write_outside_omh_skills_is_not_counted(self) -> None:
        install_skill_pack(self.paths, profile="core")
        own = self.paths.hermes_home / "skills" / "my-notes" / "SKILL.md"
        own.parent.mkdir(parents=True)
        own.write_text("---\nname: my-notes\n---\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(own, action="create"))

        self.assertEqual(len(read_ledger_writes(self.paths.hermes_home)[0]), 1)
        check = self._check("hermes_skill_edits")
        self.assertEqual(check.severity, "ok")
        self.assertIn("records no skill write under OMH-managed skills", check.message)

    def remove_skill(self, name: str = SKILL) -> str:
        skill_dir = self.skill_file(name=name).parent
        for path in sorted(skill_dir.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        skill_dir.rmdir()
        return f"{skill_install_relative_dir(name).as_posix()}/SKILL.md"

    def test_a_missing_skill_is_judged_in_the_pack_the_manifest_describes(self) -> None:
        # The installer judges it there (`manifest_pack`), so doctor does too.
        elsewhere = OmhPaths(omh_home=self.paths.omh_home, hermes_home=self.paths.hermes_home, managed_skills_dir=self.root / "gen-1" / "skills")
        install_skill_pack(elsewhere, profile="core")
        for path in sorted(self.skill_file(elsewhere).parent.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.skill_file(elsewhere).parent.rmdir()

        check = next(check for check in run_doctor(self.paths) if check.name == "missing_managed_skills")

        self.assertIn("1 managed skill(s) are missing their SKILL.md", check.message)
        self.assertIn(f"{SKILL} at ", check.message)

    def test_a_left_behind_edit_is_named_apart_from_kept_ones(self) -> None:
        install_skill_pack(self.paths, profile="core")
        backup = self.paths.skills_dir / "my-backups" / self.skill_file().parent.name
        backup.mkdir(parents=True)
        (backup / "SKILL.md").write_bytes(self.skill_file().read_bytes())
        (backup / "personal.md").write_text("mine\n", encoding="utf-8")
        install_skill_pack(self.paths, profile="core")
        _append_ledger(self.paths.hermes_home, _ledger_row(backup / "personal.md", action="write_file"))

        check = next(check for check in run_doctor(self.paths) if check.name == "hermes_skill_edits")

        self.assertEqual(check.severity, "warning")
        self.assertIn("1 Hermes edit(s) sit in a directory this release does not render the skill into", check.message)
        self.assertNotIn("that installs keep", check.message)

    def test_a_missing_skill_the_next_install_restores_is_a_warning_without_force(self) -> None:
        install_skill_pack(self.paths, profile="core")
        rel = self.remove_skill()

        checks = {check.name: check for check in run_doctor(self.paths)}

        # Not a hand edit: no blocking local modification, no --force advice.
        self.assertTrue(checks["local_modifications"].ok, checks["local_modifications"].message)
        missing = checks["missing_managed_skills"]
        self.assertTrue(missing.ok)
        self.assertEqual(missing.severity, "warning")
        self.assertIn(f"{SKILL} at {rel}: the next install renders it again", missing.message)
        self.assertNotIn("omh install --force", missing.next_action)
        # And the installer does what doctor said.
        self.assertEqual(
            [item["disposition"] for item in install_skill_pack(self.paths, profile="core")["restored_skills"]],
            ["restored"],
        )

    def test_a_missing_skill_outside_the_recorded_profile_reads_not_restored(self) -> None:
        full_only = "workflow-learning"
        install_skill_pack(self.paths, profile="full")
        # A core run keeps installed full-only skills and records `core`.
        install_skill_pack(self.paths, profile="core")
        rel = self.remove_skill(full_only)

        check = next(check for check in run_doctor(self.paths) if check.name == "missing_managed_skills")

        self.assertIn(f"{full_only} at {rel}: the recorded core profile does not include it", check.message)
        self.assertIn("reports it not_restored and leaves it uninstalled", check.message)
        self.assertEqual(
            [item["disposition"] for item in install_skill_pack(self.paths, profile="core")["restored_skills"]],
            ["not_restored"],
        )

    def test_a_hand_edit_keeps_its_force_advice(self) -> None:
        install_skill_pack(self.paths, profile="core")
        with self.skill_file().open("a", encoding="utf-8") as handle:
            handle.write("\nmy own edit\n")

        checks = {check.name: check for check in run_doctor(self.paths)}

        self.assertFalse(checks["local_modifications"].ok)
        self.assertEqual(checks["local_modifications"].severity, "blocking")
        self.assertIn("omh install --force", checks["local_modifications"].next_action)
        self.assertNotIn("missing_managed_skills", checks)

    def test_doctor_reads_the_ledger_once_and_only_rows_under_omh_paths(self) -> None:
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        own = self.paths.hermes_home / "skills" / "my-notes" / "SKILL.md"
        own.parent.mkdir(parents=True)
        own.write_text("x\n", encoding="utf-8")
        _append_ledger(self.paths.hermes_home, _ledger_row(own, action="create"), "{not json")

        within = read_ledger_writes(self.paths.hermes_home, within=[self.paths.skills_dir])[0]
        self.assertEqual([Path(write.path).name for write in within], ["SKILL.md"])
        self.assertTrue(all("my-notes" not in write.path for write in within))
        calls: list[object] = []
        real = doctor_module.read_ledger_writes

        def counting(*args, **kwargs):
            calls.append(kwargs.get("within"))
            return real(*args, **kwargs)

        with (
            mock.patch.object(doctor_module, "read_ledger_writes", side_effect=counting),
            mock.patch.object(installer, "read_ledger_writes", side_effect=counting),
        ):
            check = next(check for check in run_doctor(self.paths) if check.name == "hermes_skill_edits")
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(calls[0])
        self.assertEqual(check.severity, "warning")

    def test_doctor_judges_hermes_edits_once(self) -> None:
        # Judging hashes every managed SKILL.md; two checks read the verdict.
        install_skill_pack(self.paths, profile="core")
        self.hermes_patch(self.skill_file())
        calls: list[object] = []
        real = doctor_module.hermes_edited_skills

        def counting(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        with mock.patch.object(doctor_module, "hermes_edited_skills", side_effect=counting):
            checks = {check.name: check for check in run_doctor(self.paths)}

        self.assertEqual(len(calls), 1)
        self.assertTrue(checks["local_modifications"].ok)
        self.assertIn(f"{SKILL} at ", checks["hermes_skill_edits"].message)

    def test_an_unreadable_ledger_is_named_not_read_as_empty(self) -> None:
        install_skill_pack(self.paths, profile="core")
        ledger = _append_ledger(self.paths.hermes_home)
        ledger.write_bytes(b"\xff\xfe not utf-8")

        check = self._check("hermes_skill_edits")

        self.assertEqual(check.severity, "warning")
        self.assertIn("ledger unreadable:", check.message)


if __name__ == "__main__":
    unittest.main()
