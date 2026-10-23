"""OMH memory on every surface Hermes starts: the provider's surface classes.

Captures made on the Hermes TUI and Desktop were never recalled there: the
provider treated any `platform` other than `cli` as a shared surface, and a
shared surface denies every identity-unbound record. Hermes passes `tui` and
`desktop` for its operator consoles and never passes a principal context, so
prefetch served 0 of 2 model captures on `tui` and 2 of 2 on `cli`. The owner
decision of 2026-10-13 classifies each surface in provider `initialize`, from
Hermes' own kwargs, failing closed for anything unknown:

- `operator_console`: `cli`, `tui`, `desktop` -- reads and writes like the CLI;
- `shared`: every gateway, `api_server`, `webhook`, `relay`, `acp`, an empty
  platform and anything unknown -- principal-bound reads only, no capture or
  demote, and no blocks, reminder or brief in the pack;
- `background`: a non-primary `agent_context` (cron) -- shared reads, no writes.

Everything here runs through the provider's real `initialize` and the tool's
real handler; nothing patches the classifier.
"""

from __future__ import annotations

from argparse import Namespace
from contextlib import chdir
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.paths import resolve_paths  # noqa: E402
from omh.plugin_bundle.omh.memory_blocks import approve_memory_block, build_memory_block, write_memory_block  # noqa: E402
from omh.plugin_bundle.omh.memory_dreaming import consolidation_path, dreaming_state_path, read_latest_consolidation  # noqa: E402
from omh.commands.coding import _delegate_memory_surface  # noqa: E402
from omh.plugin_bundle.omh import memory_provider  # noqa: E402
from omh.plugin_bundle.omh.memory_provider import OmhMemoryProvider  # noqa: E402
from omh.plugin_bundle.omh.memory_session_lens import (  # noqa: E402
    SURFACE_BACKGROUND,
    SURFACE_OPERATOR_CONSOLE,
    SURFACE_SHARED,
    note_session_records,
    publish_session_lens,
    read_session_lens,
    session_lens_path,
)
from omh.plugin_bundle.omh.tools import memory_tool  # noqa: E402
from omh.plugin_bundle.omh.tools.memory_tool import omh_memory_handler  # noqa: E402
from omh.coding.coding_delegation import build_coding_delegation_payload  # noqa: E402
from omh.workflows import memory as memory_workflow  # noqa: E402
from omh.workflows.memory import (  # noqa: E402
    HANDOFF_CONTEXT_PACK_SCHEMA_VERSION,
    build_project_memory_status,
    memory_recall_pack_for_handoff,
    memory_surface_for_source,
    validate_project_memory_recall_pack,
)
from omh.workflows.role_context_packs import build_role_context_pack, role_context_pack_hash, validate_role_context_pack  # noqa: E402
from omh.wrapper_sessions import (  # noqa: E402
    create_or_resume_wrapper_session,
    prepare_wrapper_session_handoff,
    record_plan_decision,
    select_wrapper_session_executor,
)
from project_identity_fixture import memory_paths, pin_review_first, seed_project_identity  # noqa: E402
from test_memory_demote_tool import FACT, entry, sha, write_memory  # noqa: E402
from test_memory_open_state import _open_record  # noqa: E402
from test_memory_principal import PRINCIPAL_A, _context  # noqa: E402
from test_memory_prefetch_canonical import rendered_ids  # noqa: E402
from test_memory_provider_hermes_order import hermes_turn  # noqa: E402
from test_memory_session_digest import ago, set_session_marks, stamp, write_record  # noqa: E402

# (platform, agent_context, shared_surface kwarg or None, expected class)
SURFACE_CASES = (
    ("cli", "primary", None, SURFACE_OPERATOR_CONSOLE),
    ("tui", "primary", None, SURFACE_OPERATOR_CONSOLE),
    ("desktop", "primary", None, SURFACE_OPERATOR_CONSOLE),
    ("telegram", "primary", None, SURFACE_SHARED),
    ("discord", "primary", None, SURFACE_SHARED),
    ("slack", "primary", None, SURFACE_SHARED),
    ("whatsapp", "primary", None, SURFACE_SHARED),
    ("api_server", "primary", None, SURFACE_SHARED),
    ("webhook", "primary", None, SURFACE_SHARED),
    ("relay", "primary", None, SURFACE_SHARED),
    ("acp", "primary", None, SURFACE_SHARED),
    ("", "primary", None, SURFACE_SHARED),
    ("bogus", "primary", None, SURFACE_SHARED),
    ("TUI", "primary", None, SURFACE_SHARED),
    ("cron", "cron", None, SURFACE_BACKGROUND),
    ("subagent", "subagent", None, SURFACE_BACKGROUND),
    ("cli", "cron", None, SURFACE_BACKGROUND),
    ("cli", "", None, SURFACE_OPERATOR_CONSOLE),
    # The kwarg tightens, never loosens.
    ("cli", "primary", True, SURFACE_SHARED),
    ("tui", "primary", True, SURFACE_SHARED),
    ("cron", "cron", True, SURFACE_BACKGROUND),
    ("telegram", "primary", False, SURFACE_SHARED),
    ("cron", "cron", False, SURFACE_BACKGROUND),
)
FLAGS = {SURFACE_OPERATOR_CONSOLE: (False, True), SURFACE_SHARED: (True, True), SURFACE_BACKGROUND: (True, False)}
GATEWAYS = ("telegram", "discord", "slack", "api_server")


class _Homes(unittest.TestCase):
    """A user OMH home, a Hermes home, a repository and a directory outside any repository."""

    def setUp(self) -> None:
        base = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.home = base / "omh-home"
        self.hermes = base / "hermes-home"
        (self.hermes / "memories").mkdir(parents=True)
        self.repo = base / "repo"
        self.identity = seed_project_identity(self.repo)
        self.outside = base / "outside"
        self.outside.mkdir()
        self.enterContext(patch.dict(os.environ, {"OMH_HOME": str(self.home), "HERMES_HOME": str(self.hermes)}))

    def start(self, session_id: str, *, platform: str, cwd: Path, agent_context: str | None = None, **extra: object) -> OmhMemoryProvider:
        """`initialize` with the kwargs `agent/agent_init.py::_memory_provider_init_kwargs` builds."""
        live = OmhMemoryProvider(self.home)
        kwargs: dict[str, object] = {
            "session_id": session_id,
            "platform": platform,
            "hermes_home": str(self.hermes),
            "agent_context": agent_context if agent_context is not None else (platform if platform in ("cron", "subagent") else "primary"),
            "cwd": str(cwd),
            **extra,
        }
        live.initialize(**kwargs)
        return live

    def tool(self, action: str, session_id: str, cwd: Path, **args: object) -> dict:
        return json.loads(omh_memory_handler({"action": action, **args}, cwd=str(cwd), session_id=session_id))

    def store_bytes(self) -> dict[str, bytes]:
        memory = self.home / "memory"
        return {str(path.relative_to(memory)): path.read_bytes() for path in sorted(memory.rglob("*")) if path.is_file()} if memory.exists() else {}


class SurfaceClassTests(_Homes):
    def test_each_platform_and_agent_context_gets_its_class_from_initialize(self) -> None:
        for index, (platform, agent_context, shared_kwarg, expected) in enumerate(SURFACE_CASES):
            with self.subTest(platform=platform, agent_context=agent_context, shared_surface=shared_kwarg):
                session_id = f"surface-{index}"
                extra = {} if shared_kwarg is None else {"shared_surface": shared_kwarg}
                self.start(session_id, platform=platform, cwd=self.repo, agent_context=agent_context, **extra)
                lens = read_session_lens(self.home, session_id)
                assert lens is not None
                self.assertEqual(lens["surface_class"], expected)
                self.assertEqual((lens["shared_surface"], lens["writes_enabled"]), FLAGS[expected])
                read_back = memory_tool._session_lens({"session_id": session_id})
                self.assertEqual((read_back.bound, read_back.surface_class), (True, expected))

    def test_a_lens_an_earlier_provider_wrote_is_read_by_its_flags(self) -> None:
        for session_id, shared, writes, expected in (
            ("old-cli", False, True, SURFACE_OPERATOR_CONSOLE),
            ("old-shared", True, True, SURFACE_SHARED),
            ("old-cron", True, False, SURFACE_BACKGROUND),
            # No provider ever wrote this pair; its class decides both flags.
            ("old-nonshared-cron", False, False, SURFACE_BACKGROUND),
        ):
            with self.subTest(session_id=session_id):
                publish_session_lens(
                    self.home,
                    session_id,
                    {"shared_surface": shared, "writes_enabled": writes, "principal_context": None, "homes": [str(self.home)], "project_identity": "", "executor_target": "hermes"},
                    reset=True,
                )
                lens = memory_tool._session_lens({"session_id": session_id})
                self.assertEqual(lens.surface_class, expected)
                self.assertEqual((lens.shared_surface, lens.writes_enabled), FLAGS[expected])

    def test_a_class_its_own_flags_contradict_is_not_a_lens(self) -> None:
        self.start("forged", platform="discord", cwd=self.repo)
        path = session_lens_path(self.home, "forged")
        lens = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**lens, "surface_class": SURFACE_OPERATOR_CONSOLE}), encoding="utf-8")
        self.assertIsNone(read_session_lens(self.home, "forged"))
        refused = self.tool("capture", "forged", self.repo, summary="The deploy freeze ends on 2026-11-02.")
        self.assertEqual((refused["status"], refused["reason"]), ("refused", "session_lens_unavailable"))


class KanbanWorkerTests(_Homes):
    """A Kanban worker reads like the console it is, and asks nobody anything.

    `hermes -p <profile> --cli ... chat -q "work kanban task <id>"`
    (`hermes_cli/kanban_db_dispatch.py`, `_worker_argv`) builds its agent in
    `hermes_cli/cli_agent_setup_mixin.py` with `platform="cli"`, and
    `_memory_provider_init_kwargs` makes that `agent_context="primary"` with
    the CLI-only callbacks; the spawn sets `HERMES_KANBAN_TASK` and
    `HERMES_SESSION_SOURCE=kanban` in the worker's environment. #2088's
    reporter relies on records in Kanban turns, so the worker keeps them. It
    runs unattended, so it gets no reminder, brief or digest, raises no brief,
    moves no counter and records no session close. Its default workspace is a
    scratch directory outside any repository, where a console now reads
    user-global records and never a project's.
    """

    WORKER_ENVIRONMENTS = (
        ("both markers", {"HERMES_KANBAN_TASK": "t_7f3a", "HERMES_SESSION_SOURCE": "kanban"}),
        ("task marker", {"HERMES_KANBAN_TASK": "t_7f3a"}),
        ("source marker", {"HERMES_SESSION_SOURCE": "kanban"}),
    )

    def setUp(self) -> None:
        super().setUp()
        self.scratch = self.repo.parent / "kanban-workspace"
        self.scratch.mkdir()
        # Hermes memory nearly full, so the attended seed session raises a brief.
        write_memory(self.hermes, "MEMORY.md", *(entry(index) for index in range(10)))
        seed = self.start("seed", platform="cli", cwd=self.repo)
        self.in_repo = self.tool("capture", "seed", self.repo, summary="Kanban workers run the release suite before moving a card.")
        self.personal = self.tool("capture", "seed", self.outside, summary="The operator wants kanban card titles in sentence case.")
        self.assertEqual((self.in_repo["status"], self.personal["status"]), ("remembered", "remembered"))
        seed.shutdown()
        # Something to report since the last close, so a digest would be served.
        set_session_marks(self.home, last_session_closed_at=ago(hours=3))
        write_record(self.home, "rec-digest", created_at=stamp(ago(hours=2)), source="hermes_model", scope={"kind": "user-global", "ref": "default"})
        paths = resolve_paths(self.home, self.hermes)
        pin_review_first(paths)
        self.open_record = _open_record(
            paths, "kanban question: which board owns the release cards", days_open=34, scope_kind="user-global", scope_ref="default"
        )["record_id"]
        self.assertTrue(read_latest_consolidation(self.home, self.hermes)["due"])

    def worker_state(self) -> dict[str, bytes | None]:
        memory = self.home / "memory"
        files = {name: memory / name for name in ("consolidation.jsonl", "session_close.json", "open_reminders.json")}
        files["dreaming.json"] = dreaming_state_path(self.home, self.hermes)
        files["consolidation.json"] = consolidation_path(self.home, self.hermes)
        return {name: path.read_bytes() if path.is_file() else None for name, path in files.items()}

    def test_a_worker_in_its_scratch_workspace_reads_records_and_asks_nothing(self) -> None:
        for label, environment in self.WORKER_ENVIRONMENTS:
            with self.subTest(env=label), patch.dict(os.environ, environment):
                before = self.worker_state()
                session_id = f"kanban-{label.replace(' ', '-')}"
                worker = self.start(
                    session_id,
                    platform="cli",
                    cwd=self.scratch,
                    warning_callback=lambda message: None,
                    status_callback=lambda line: None,
                    agent_identity="worker",
                    agent_workspace="hermes",
                )
                self.assertEqual(read_session_lens(self.home, session_id)["surface_class"], SURFACE_OPERATOR_CONSOLE)
                pack = hermes_turn(worker, 1, "kanban card titles sentence case release cards board")
                served = rendered_ids(pack)
                self.assertIn(self.personal["record_id"], served)
                self.assertIn(self.open_record, served)
                self.assertNotIn(self.in_repo["record_id"], served, "a scratch workspace has no project")
                for marker in ("omh reminder:", "<memory_consolidation", "<demote ", "<memory_digest"):
                    self.assertNotIn(marker, pack)
                self.assertIsNone(worker.latest_open_reminder())
                for turn in range(2, 9):
                    worker.on_turn_start(turn, "keep working the card")
                worker.on_memory_write("replace", "memory", "Fact 1: rewritten by the worker.")
                worker.on_pre_compress([{"role": "user"}] * 40)
                worker.on_session_end([])
                worker.shutdown()
                self.assertEqual(self.worker_state(), before)

    def test_the_same_scratch_session_without_the_markers_is_asked(self) -> None:
        attended = self.start("attended-scratch", platform="cli", cwd=self.scratch)
        pack = hermes_turn(attended, 1, "kanban card titles sentence case release cards board")
        self.assertIn("omh reminder:", pack)
        self.assertIn("<memory_consolidation", pack)
        self.assertIn("<memory_digest", pack)


class ConsoleRecallTests(_Homes):
    """The P0: a capture made on the CLI is recalled on the TUI and on Desktop, inside a repository and outside one."""

    def test_tui_and_desktop_recall_a_cli_capture_inside_a_repository(self) -> None:
        self.start("cli-repo", platform="cli", cwd=self.repo)
        captured = self.tool("capture", "cli-repo", self.repo, summary="The release branch is cut every Thursday at noon.")
        self.assertEqual(captured["status"], "remembered", captured)
        stored = json.loads((self.home / "memory" / "records" / f"{captured['record_id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["scope"], {"kind": "project", "ref": self.identity})
        for platform in ("tui", "desktop", "cli"):
            with self.subTest(platform=platform):
                live = self.start(f"{platform}-repo-recall", platform=platform, cwd=self.repo)
                self.assertEqual(rendered_ids(hermes_turn(live, 1, "when is the release branch cut")), [captured["record_id"]])
                recalled = self.tool("recall", f"{platform}-repo-recall", self.repo, query="release branch cut")
                self.assertEqual([row["record_id"] for row in recalled["records"]], [captured["record_id"]])
                live.shutdown()
        for platform in GATEWAYS:
            with self.subTest(platform=platform):
                live = self.start(f"{platform}-repo-recall", platform=platform, cwd=self.repo)
                self.assertEqual(rendered_ids(hermes_turn(live, 1, "when is the release branch cut")), [])

    def test_tui_and_desktop_recall_a_cli_capture_outside_a_repository(self) -> None:
        self.start("cli-outside", platform="cli", cwd=self.outside)
        captured = self.tool("capture", "cli-outside", self.outside, summary="The operator prefers replies without emoji.")
        self.assertEqual(captured["status"], "remembered", captured)
        stored = json.loads((self.home / "memory" / "records" / f"{captured['record_id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["scope"], {"kind": "user-global", "ref": "default"})
        for platform in ("tui", "desktop", "cli"):
            for cwd in (self.outside, self.repo):
                with self.subTest(platform=platform, cwd=cwd.name):
                    session_id = f"{platform}-{cwd.name}-recall"
                    live = self.start(session_id, platform=platform, cwd=cwd)
                    self.assertEqual(rendered_ids(hermes_turn(live, 1, "does the operator want emoji in replies")), [captured["record_id"]])
                    recalled = self.tool("recall", session_id, cwd, query="replies emoji")
                    self.assertEqual([row["record_id"] for row in recalled["records"]], [captured["record_id"]])
                    live.shutdown()


class OutsideRepositoryScopeTests(_Homes):
    """F4: with no project resolved, a console reads user-global and its own thread, and no project record at all."""

    def test_outside_a_repository_no_project_or_foreign_thread_record_leaks_in(self) -> None:
        self.start("cli-repo", platform="cli", cwd=self.repo)
        project = self.tool("capture", "cli-repo", self.repo, summary="The staging cluster drains nodes before each upgrade.")
        self.assertEqual(project["status"], "remembered", project)
        self.start("cli-outside", platform="cli", cwd=self.outside)
        personal = self.tool("capture", "cli-outside", self.outside, summary="The staging cluster notes are kept in the operator's paper notebook.")
        self.assertEqual(personal["status"], "remembered", personal)

        outside = self.start("tui-outside", platform="tui", cwd=self.outside)
        self.assertEqual(rendered_ids(hermes_turn(outside, 1, "staging cluster upgrade drains")), [personal["record_id"]])
        receipt = outside.latest_prefetch_receipt()
        assert receipt is not None
        self.assertEqual(
            receipt["lens"]["scope_allowlist"],
            [{"kind": "thread", "ref": "tui-outside"}, {"kind": "user-global", "ref": "default"}],
        )
        self.assertEqual(receipt["lens"]["project_identity_state"], "unresolved")
        refused = self.tool("confirm", "tui-outside", self.outside, record_id=project["record_id"])
        self.assertEqual((refused["status"], refused["reason"]), ("refused", "record_not_in_scope"))

        # Inside a repository the project label is required again: another
        # repository's record stays out, the personal one still comes in.
        other = self.repo.parent / "other-repo"
        seed_project_identity(other, "prj:" + "c" * 64)
        elsewhere = self.start("tui-other-repo", platform="tui", cwd=other)
        self.assertEqual(rendered_ids(hermes_turn(elsewhere, 1, "staging cluster upgrade drains")), [personal["record_id"]])

        # A shared surface outside a repository still reads nothing.
        shared = self.start("discord-outside", platform="discord", cwd=self.outside)
        self.assertEqual(rendered_ids(hermes_turn(shared, 1, "staging cluster upgrade drains")), [])
        self.assertEqual(shared.latest_prefetch_receipt()["lens"]["scope_allowlist"], [])


class OutsideRepositoryHintTests(_Homes):
    """Capture hints (#2091) judge recall through the same lens a turn outside a repository uses."""

    def test_a_similar_capture_outside_a_repository_reports_both_records_recalled(self) -> None:
        self.start("cli-outside", platform="cli", cwd=self.outside)
        held = self.tool("capture", "cli-outside", self.outside, summary="The staging database runs postgres 16")
        result = self.tool("capture", "cli-outside", self.outside, summary="The staging database runs postgres 17")
        self.assertEqual(result["similar_record_id"], held["record_id"], result)
        self.assertEqual(sorted(result["recalls_now"]), sorted([held["record_id"], result["record_id"]]))


class WriteRefusalTests(_Homes):
    """`capture` and `demote` write on an operator console only, and a refusal writes nothing."""

    def setUp(self) -> None:
        super().setUp()
        self.memory_file = write_memory(self.hermes, "MEMORY.md", FACT, entry(1))

    def assert_refused_without_writing(self, session_id: str, reason: str) -> None:
        before, hermes_before = self.store_bytes(), self.memory_file.read_bytes()
        captured = self.tool("capture", session_id, self.repo, summary="The deploy freeze ends on 2026-11-02.")
        self.assertEqual((captured["status"], captured["reason"]), ("refused", reason), captured)
        self.assertTrue(captured["next_action"].startswith("Nothing was saved."), captured)
        self.assertIsNone(captured["record_id"])
        self.assertNotIn("display_line", captured)
        demoted = self.tool("demote", session_id, self.repo, entry_sha256=sha(FACT)[:12])
        self.assertEqual((demoted["status"], demoted["reason"]), ("refused", reason), demoted)
        self.assertTrue(demoted["next_action"].startswith("Keep the Hermes entry"), demoted)
        self.assertNotIn("remove_from_hermes", demoted)
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(self.memory_file.read_bytes(), hermes_before)

    def test_a_gateway_refuses_capture_and_demote(self) -> None:
        for platform in GATEWAYS:
            with self.subTest(platform=platform):
                self.start(f"{platform}-writes", platform=platform, cwd=self.repo)
                self.assert_refused_without_writing(f"{platform}-writes", "shared_surface_writes_refused")

    def test_a_console_asked_to_be_shared_refuses_them_too(self) -> None:
        self.start("cli-shared", platform="cli", cwd=self.repo, shared_surface=True)
        self.assert_refused_without_writing("cli-shared", "shared_surface_writes_refused")

    def test_a_background_run_refuses_every_write(self) -> None:
        self.start("cron-writes", platform="cron", cwd=self.repo)
        self.assert_refused_without_writing("cron-writes", "writes_disabled")

    def test_an_unbound_session_refuses_every_write(self) -> None:
        self.start("live", platform="cli", cwd=self.repo)
        for session_id in ("never-initialized", ""):
            with self.subTest(session_id=session_id):
                self.assert_refused_without_writing(session_id, "session_lens_unavailable")

    def test_a_console_writes(self) -> None:
        for platform in ("cli", "tui", "desktop"):
            with self.subTest(platform=platform):
                self.start(f"{platform}-writes", platform=platform, cwd=self.repo)
                captured = self.tool("capture", f"{platform}-writes", self.repo, summary=f"The {platform} operator keeps release notes in the wiki.")
                self.assertEqual(captured["status"], "remembered", captured)
        demoted = self.tool("demote", "desktop-writes", self.repo, entry_sha256=sha(FACT)[:12])
        self.assertEqual(demoted["status"], "demoted", demoted)


class PackGateTests(_Homes):
    """Blocks, the reminder and the brief reach an operator console only."""

    def setUp(self) -> None:
        super().setUp()
        # The operator's own store doubles as the repository's, so the open
        # record is in the project lens; Hermes memory nearly full raises a
        # brief whose demote rows quote it.
        self.home = self.repo / ".omh"
        self.enterContext(patch.dict(os.environ, {"OMH_HOME": str(self.home)}))
        paths = memory_paths(self.home, self.hermes)
        self.open_record = _open_record(paths, "cache question: redis or memcached", days_open=34)["record_id"]
        write_memory_block(self.home, approve_memory_block(build_memory_block("operator", "The operator's always-on note.")))
        write_memory_block(self.home, approve_memory_block(build_memory_block("runbook", "value", description="How the operator deploys.", tier="reference")))
        self.entries = [entry(index) for index in range(10)]
        write_memory(self.hermes, "MEMORY.md", *self.entries)

    def test_shared_and_background_packs_carry_no_blocks_reminder_or_brief(self) -> None:
        for platform in (*GATEWAYS, "cron", "subagent"):
            with self.subTest(platform=platform):
                pack = hermes_turn(self.start(f"{platform}-pack", platform=platform, cwd=self.repo), 1, "cache question")
                for marker in ("<memory_blocks", "<memory_block_index", "omh reminder:", "<memory_consolidation", "<demote ", "always-on note"):
                    self.assertNotIn(marker, pack)
                for text in self.entries:
                    self.assertNotIn(text[:40], pack)

    def test_a_shared_surface_is_never_asked_even_about_a_record_its_principal_reads(self) -> None:
        # The lens does admit a principal-bound record here; the reminder is
        # still the operator's question, not the group chat's.
        paths = memory_paths(self.home, self.hermes)
        bound = _open_record(paths, "principal question: which cache tier", days_open=34, scope_kind="user", principal_context=_context(PRINCIPAL_A))
        live = self.start("shared_fixture", platform="discord", cwd=self.repo, principal_context=_context(PRINCIPAL_A))
        live.on_turn_start(1, "which cache tier", principal_context=_context(PRINCIPAL_A))
        pack = live.prefetch("which cache tier", session_id="shared_fixture")
        self.assertEqual(rendered_ids(pack), [bound["record_id"]])
        self.assertNotIn("omh reminder:", pack)
        self.assertIsNone(live.latest_open_reminder())

    def test_a_console_pack_carries_the_brief_with_its_demote_rows(self) -> None:
        for platform in ("tui", "desktop", "cli"):
            with self.subTest(platform=platform):
                live = self.start(f"{platform}-pack", platform=platform, cwd=self.repo)
                pack = hermes_turn(live, 1, "cache question")
                self.assertIn("<memory_blocks>", pack)
                self.assertIn("always-on note", pack)
                self.assertIn("<memory_block_index>", pack)
                self.assertIn("<memory_consolidation", pack)
                self.assertEqual(pack.count("<demote "), 3)
                self.assertIn(self.open_record, rendered_ids(pack))
                # Asked on the first console that served it, then on its cadence.
                self.assertEqual("omh reminder:" in pack, platform == "tui")
                live.shutdown()



class BlockReadGateTests(_Homes):
    """`omh_memory` lists and reads blocks on an operator console only."""

    def setUp(self) -> None:
        super().setUp()
        write_memory_block(self.home, approve_memory_block(build_memory_block("operator", "OPERATOR_BLOCK_VALUE", description="OPERATOR_BLOCK_DESCRIPTION")))
        write_memory_block(self.home, approve_memory_block(build_memory_block("runbook", "RUNBOOK_VALUE", description="RUNBOOK_DESCRIPTION", tier="reference")))

    def test_shared_and_background_sessions_neither_list_nor_read_a_block(self) -> None:
        for platform in ("telegram", "discord", "cron", "subagent"):
            with self.subTest(platform=platform):
                self.start(f"{platform}-blocks", platform=platform, cwd=self.repo)
                for action, extra in (("blocks", {}), ("read", {"label": "operator"}), ("read", {"label": "runbook"})):
                    result = self.tool(action, f"{platform}-blocks", self.repo, **extra)
                    self.assertEqual((result["status"], result["reason"]), ("refused", "shared_surface_reads_refused"), result)
                    text = json.dumps(result)
                    for secret in ("OPERATOR_BLOCK_VALUE", "OPERATOR_BLOCK_DESCRIPTION", "RUNBOOK_VALUE", "RUNBOOK_DESCRIPTION", '"operator"', '"runbook"'):
                        self.assertNotIn(secret, text)

    def test_an_unbound_session_neither_lists_nor_reads(self) -> None:
        for session_id in ("never-initialized", ""):
            with self.subTest(session_id=session_id):
                for action, extra in (("blocks", {}), ("read", {"label": "operator"})):
                    result = self.tool(action, session_id, self.repo, **extra)
                    self.assertEqual((result["status"], result["reason"]), ("refused", "session_lens_unavailable"))
                    self.assertNotIn("OPERATOR_BLOCK_VALUE", json.dumps(result))

    def test_a_console_lists_and_reads(self) -> None:
        for platform in ("cli", "tui", "desktop"):
            with self.subTest(platform=platform):
                self.start(f"{platform}-blocks", platform=platform, cwd=self.repo)
                listing = self.tool("blocks", f"{platform}-blocks", self.repo)
                self.assertEqual(sorted(row["label"] for row in listing["blocks"]), ["operator", "runbook"])
                read = self.tool("read", f"{platform}-blocks", self.repo, label="operator")
                self.assertEqual(read["block"]["value"], "OPERATOR_BLOCK_VALUE")


class LensOwnershipTests(_Homes):
    """Two providers under one session id: the lens only tightens, and only its writer withdraws it.

    Hermes' session-hygiene agent (`gateway/run_turn.py`) and `/compress`
    agent (`gateway/slash_commands_session.py`) load the provider, with
    `compression.checkpoint_required` on, as `platform="cli"` under the live
    gateway session's id.
    """

    def setUp(self) -> None:
        super().setUp()
        write_memory_block(self.home, approve_memory_block(build_memory_block("operator", "OPERATOR_BLOCK_VALUE")))

    def owner(self, session_id: str) -> str | None:
        lens = read_session_lens(self.home, session_id)
        return None if lens is None else lens["instance"]

    def test_a_cli_guest_under_a_gateway_session_adopts_the_shared_lens(self) -> None:
        gateway = self.start("gw-1", platform="discord", cwd=self.repo)
        owner = self.owner("gw-1")
        guest = self.start("gw-1", platform="cli", cwd=self.repo)
        lens = read_session_lens(self.home, "gw-1")
        self.assertEqual((lens["instance"], lens["surface_class"]), (owner, SURFACE_SHARED))
        # Its summary is a group chat's: no operator block goes into it.
        self.assertEqual(guest.on_pre_compress([{"role": "user"}] * 5), "")
        self.assertNotIn("OPERATOR_BLOCK_VALUE", hermes_turn(guest, 1, "hello"))
        refused = self.tool("capture", "gw-1", self.repo, summary="The deploy freeze ends on 2026-11-02.")
        self.assertEqual(refused["reason"], "shared_surface_writes_refused")
        guest.shutdown()
        self.assertEqual(self.owner("gw-1"), owner, "the guest withdraws nothing it did not write")
        gateway.on_turn_start(1, "hello")
        self.assertEqual(self.owner("gw-1"), owner)
        gateway.shutdown()
        self.assertIsNone(read_session_lens(self.home, "gw-1"))

    def test_a_tighter_provider_takes_the_lens_and_the_looser_one_follows(self) -> None:
        console = self.start("mixed", platform="cli", cwd=self.repo)
        first = self.owner("mixed")
        gateway = self.start("mixed", platform="discord", cwd=self.repo)
        taken = read_session_lens(self.home, "mixed")
        self.assertEqual(taken["surface_class"], SURFACE_SHARED)
        self.assertNotEqual(taken["instance"], first)
        console.on_turn_start(1, "hello")
        self.assertEqual(self.owner("mixed"), taken["instance"], "the looser provider never takes it back")
        self.assertNotIn("OPERATOR_BLOCK_VALUE", console.prefetch("hello"))
        console.shutdown()
        self.assertEqual(self.owner("mixed"), taken["instance"])
        gateway.shutdown()
        self.assertIsNone(read_session_lens(self.home, "mixed"))

    def test_an_equal_successor_owns_the_lens_with_its_lists(self) -> None:
        first = self.start("rebuilt", platform="discord", cwd=self.repo)
        note_session_records(self.home, "rebuilt", "seen", ["mem_0123456789abcdef"])
        self.start("rebuilt", platform="discord", cwd=self.repo)
        lens = read_session_lens(self.home, "rebuilt")
        self.assertEqual(lens["seen"], ["mem_0123456789abcdef"], "the same conversation keeps what it saw")
        first.shutdown()
        self.assertEqual(self.owner("rebuilt"), lens["instance"])

    def test_a_tightening_belongs_to_its_session_id(self) -> None:
        # A live CLI is overtaken by a Telegram provider on the same id; after
        # Telegram leaves, the CLI's next id starts from the class it declared.
        console = self.start("s2", platform="cli", cwd=self.repo)
        telegram = self.start("s2", platform="telegram", cwd=self.repo)
        console.on_turn_start(1, "hello")
        self.assertEqual(read_session_lens(self.home, "s2")["surface_class"], SURFACE_SHARED)
        telegram.shutdown()
        console.on_turn_start(2, "hello")
        self.assertEqual(read_session_lens(self.home, "s2")["surface_class"], SURFACE_SHARED, "the old id stays tightened")
        console.on_session_switch("s2-compressed", parent_session_id="s2")
        lens = read_session_lens(self.home, "s2-compressed")
        self.assertEqual(lens["surface_class"], SURFACE_OPERATOR_CONSOLE)
        self.assertIn("OPERATOR_BLOCK_VALUE", hermes_turn(console, 3, "hello", session_id="s2-compressed"))
        console.on_session_switch("s2-new", reset=True)
        self.assertEqual(read_session_lens(self.home, "s2-new")["surface_class"], SURFACE_OPERATOR_CONSOLE)

    def test_a_render_and_a_pre_compress_reread_the_lens_first(self) -> None:
        # No turn start in between: the hand-off and the render still see it,
        # each on its own.
        compressing = self.start("loose-first-compress", platform="cli", cwd=self.repo)
        self.start("loose-first-compress", platform="discord", cwd=self.repo)
        self.assertEqual(compressing.on_pre_compress([{"role": "user"}] * 3), "")
        rendering = self.start("loose-first-render", platform="cli", cwd=self.repo)
        self.start("loose-first-render", platform="discord", cwd=self.repo)
        rendering.queue_prefetch("hello")
        self.assertNotIn("OPERATOR_BLOCK_VALUE", rendering.prefetch("hello"))

    def test_a_swallowed_lens_write_never_loosens_the_session(self) -> None:
        self.start("swallowed", platform="discord", cwd=self.repo)
        guest = self.start("swallowed", platform="cli", cwd=self.repo)
        session_lens_path(self.home, "swallowed").unlink()
        with patch.object(memory_provider, "publish_session_lens", side_effect=PermissionError("read-only home")):
            guest.on_turn_start(1, "hello")
            self.assertNotIn("OPERATOR_BLOCK_VALUE", guest.prefetch("hello"))
        self.assertEqual(guest.on_pre_compress([{"role": "user"}]), "")

    def test_an_abandoned_tighter_lens_is_replaced_and_a_fresh_one_adopted(self) -> None:
        def plant(session_id: str, published_at: datetime) -> None:
            publish_session_lens(
                self.home,
                session_id,
                {
                    "surface_class": SURFACE_SHARED, "shared_surface": True, "writes_enabled": True, "principal_context": None,
                    "homes": [str(self.home)], "project_identity": "", "executor_target": "hermes",
                    "published_at": published_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                },
                reset=True,
                instance="crashed-gateway",
            )

        now = datetime.now(timezone.utc)
        plant("abandoned", now - timedelta(hours=13))
        self.start("abandoned", platform="tui", cwd=self.repo)
        lens = read_session_lens(self.home, "abandoned")
        self.assertEqual(lens["surface_class"], SURFACE_OPERATOR_CONSOLE)
        self.assertNotEqual(lens["instance"], "crashed-gateway")
        plant("fresh", now - timedelta(hours=1))
        fresh = self.start("fresh", platform="tui", cwd=self.repo)
        self.assertEqual(read_session_lens(self.home, "fresh")["instance"], "crashed-gateway")
        self.assertNotIn("OPERATOR_BLOCK_VALUE", hermes_turn(fresh, 1, "hello", session_id="fresh"))

    def test_a_live_owner_keeps_its_lens_fresh_and_takes_it_back(self) -> None:
        gateway = self.start("owned", platform="discord", cwd=self.repo)
        path = session_lens_path(self.home, "owned")
        lens = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**lens, "published_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
        gateway.on_turn_start(1, "hello")
        self.assertNotEqual(read_session_lens(self.home, "owned")["published_at"], "2026-01-01T00:00:00Z")
        # A console that replaced an abandoned-looking lens loses it at the owner's next turn start.
        path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "published_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
        console = self.start("owned", platform="tui", cwd=self.repo)
        self.assertEqual(read_session_lens(self.home, "owned")["surface_class"], SURFACE_OPERATOR_CONSOLE)
        gateway.on_turn_start(2, "hello")
        self.assertEqual(read_session_lens(self.home, "owned")["surface_class"], SURFACE_SHARED)
        console.queue_prefetch("hello")
        self.assertNotIn("OPERATOR_BLOCK_VALUE", console.prefetch("hello"))

    def test_a_failed_reclaim_leaves_no_looser_lens_behind(self) -> None:
        gateway = self.start("reclaim", platform="discord", cwd=self.repo)
        path = session_lens_path(self.home, "reclaim")
        path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "published_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
        self.start("reclaim", platform="tui", cwd=self.repo)
        self.assertEqual(read_session_lens(self.home, "reclaim")["surface_class"], SURFACE_OPERATOR_CONSOLE)
        with patch.object(memory_provider, "publish_session_lens", side_effect=PermissionError("read-only home")):
            gateway.on_turn_start(1, "hello")
        self.assertIsNone(read_session_lens(self.home, "reclaim"), "the looser lens does not keep answering the tool")
        refused = self.tool("capture", "reclaim", self.repo, summary="The deploy freeze ends on 2026-11-02.")
        self.assertEqual(refused["reason"], "session_lens_unavailable")
        recalled = self.tool("recall", "reclaim", self.repo, query="deploy freeze")
        self.assertEqual(recalled["reason"], "session_lens_unavailable")

    def test_a_downgrade_shows_in_the_receipt_and_the_status(self) -> None:
        self.start("visible", platform="discord", cwd=self.repo)
        guest = self.start("visible", platform="cli", cwd=self.repo)
        hermes_turn(guest, 1, "hello", session_id="visible")
        receipt = guest.latest_prefetch_receipt()
        self.assertEqual((receipt["surface_class"], receipt["tightened_by_foreign_lens"]), (SURFACE_SHARED, True))
        last = build_project_memory_status(resolve_paths(self.home, self.hermes))["last_prefetch"]
        self.assertEqual((last["surface_class"], last["tightened_by_foreign_lens"]), (SURFACE_SHARED, True))
        own = self.start("own", platform="tui", cwd=self.repo)
        hermes_turn(own, 1, "hello", session_id="own")
        self.assertEqual((own.latest_prefetch_receipt()["surface_class"], own.latest_prefetch_receipt()["tightened_by_foreign_lens"]), (SURFACE_OPERATOR_CONSOLE, False))

    def test_a_lens_an_earlier_release_wrote_is_foreign_and_only_a_console_withdraws_it(self) -> None:
        def plant_unowned(session_id: str, surface_class: str) -> None:
            shared, writes = FLAGS[surface_class]
            path = session_lens_path(self.home, session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "schema_version": "omh_memory_session_lens/v1", "session_id": session_id, "surface_class": surface_class,
                "shared_surface": shared, "writes_enabled": writes, "principal_context": None, "homes": [str(self.home)],
                "project_identity": "", "executor_target": "hermes", "seen": [], "confirmed": [],
                "published_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }), encoding="utf-8")

        plant_unowned("upgrade-console", SURFACE_SHARED)
        console = self.start("upgrade-console", platform="cli", cwd=self.repo)
        self.assertNotIn("instance", read_session_lens(self.home, "upgrade-console"), "a tighter unowned lens is not widened")
        console.shutdown()
        self.assertIsNone(read_session_lens(self.home, "upgrade-console"), "the session's console provider withdraws it")
        plant_unowned("upgrade-gateway", SURFACE_BACKGROUND)
        gateway = self.start("upgrade-gateway", platform="discord", cwd=self.repo)
        gateway.shutdown()
        self.assertIsNotNone(read_session_lens(self.home, "upgrade-gateway"), "only a console provider withdraws an unowned lens")

    def test_a_missing_lens_is_republished_by_its_provider(self) -> None:
        live = self.start("gone", platform="tui", cwd=self.repo)
        owner = self.owner("gone")
        session_lens_path(self.home, "gone").unlink()
        live.on_turn_start(1, "hello")
        lens = read_session_lens(self.home, "gone")
        self.assertEqual((lens["instance"], lens["surface_class"]), (owner, SURFACE_OPERATOR_CONSOLE))


class ConsoleHandoffRolePackTests(_Homes):
    """A console handoff spanning user-global, a project and a thread still becomes a coding delegation.

    The role context pack required every record to share the pack's scope.
    On main a repository store holding a project record and a user-global
    record already broke delegation that way; the console outside-repository
    rule made it reachable outside a repository too. The handoff now declares
    the allowlist its records were selected under, and the role pack accepts
    exactly those scopes; a record outside them still raises.
    """

    def seeded(self, store_root: Path, scopes: list[tuple[str, str]]) -> object:
        paths = resolve_paths(store_root, self.hermes)
        for index, (kind, ref) in enumerate(scopes):
            captured = memory_workflow.capture_project_memory_candidate(paths, f"Release checklist fixture {index} lives on paper", scope_kind=kind, scope_ref=ref)
            if not captured.get("auto_approved"):
                memory_workflow.approve_project_memory_candidate(paths, captured["candidate"]["candidate_id"])
        return paths

    def delegate(self, pack: dict) -> dict:
        payload = build_coding_delegation_payload("risky refactor of the release checklist", source="generic", executor_target="claude-code", memory_recall_pack=pack)
        handoff = next(payload[key] for key in ("executor_handoff", "runtime_handoff", "prompt_handoff") if isinstance(payload.get(key), dict))
        self.assertEqual(validate_role_context_pack(handoff["role_context_pack"]), [])
        return handoff["role_context_pack"]

    def console_pack(self, paths: object) -> dict:
        pack = memory_recall_pack_for_handoff(paths, "release checklist fixture paper", session_id="session-a", surface_class=SURFACE_OPERATOR_CONSOLE)
        assert pack is not None
        self.assertEqual(validate_project_memory_recall_pack(pack), [])
        return pack

    def test_outside_a_repository_a_user_global_record_delegates(self) -> None:
        pack = self.console_pack(self.seeded(self.home, [("user-global", "default")]))
        self.assertEqual([item["scope"]["kind"] for item in pack["included_records"]], ["user-global"])
        role = self.delegate(pack)
        self.assertEqual(role["scope"], {"kind": "unresolved", "ref": "scope_unresolved"})
        self.assertEqual(role["scope_allowlist"], pack["scope_allowlist"])
        self.assertEqual(role["record_count"], 1)

    def test_outside_a_repository_user_global_and_thread_records_delegate(self) -> None:
        pack = self.console_pack(self.seeded(self.home, [("user-global", "default"), ("thread", "session-a")]))
        self.assertEqual(sorted(item["scope"]["kind"] for item in pack["included_records"]), ["thread", "user-global"])
        self.assertEqual(self.delegate(pack)["record_count"], 2)

    def test_inside_a_repository_project_and_user_global_records_delegate(self) -> None:
        pack = self.console_pack(self.seeded(self.repo / ".omh", [("project", self.identity), ("user-global", "default")]))
        self.assertEqual(sorted(item["scope"]["kind"] for item in pack["included_records"]), ["project", "user-global"])
        role = self.delegate(pack)
        self.assertEqual(role["scope"], {"kind": "project", "ref": self.identity})
        self.assertEqual(role["record_count"], 2)

    def test_a_single_scope_pack_declares_nothing_new(self) -> None:
        # A console's pack: since messenger memory (#2088) a handoff with no
        # stated surface is shared and carries no identity-unbound record.
        pack = self.console_pack(self.seeded(self.repo / ".omh", [("project", self.identity)]))
        self.assertEqual({row["scope"]["kind"] for row in pack["included_records"]}, {"project"})
        self.assertNotIn("scope_allowlist", pack)
        self.assertNotIn("scope_allowlist", self.delegate(pack))

    def test_a_record_outside_the_declared_allowlist_still_raises(self) -> None:
        pack = self.console_pack(self.seeded(self.repo / ".omh", [("project", self.identity), ("user-global", "default")]))
        foreign = {"kind": "project", "ref": "prj:" + "c" * 64}
        mismatched = json.loads(json.dumps(pack))
        next(item for item in mismatched["included_records"] if item["scope"]["kind"] == "user-global")["scope"] = foreign
        self.assertTrue(any("inside scope_allowlist" in error for error in validate_project_memory_recall_pack(mismatched)))
        with self.assertRaises(ValueError):
            build_role_context_pack(memory_recall_pack=mismatched)
        # Mixed records with no declared allowlist are the old mismatch.
        undeclared = {key: value for key, value in pack.items() if key != "scope_allowlist"}
        with self.assertRaises(ValueError):
            build_role_context_pack(memory_recall_pack=undeclared)
        # A stored role pack whose record leaves its allowlist does not validate.
        role = build_role_context_pack(memory_recall_pack=pack)
        stored = json.loads(json.dumps(role))
        next(record for record in stored["records"] if record["scope"]["kind"] == "user-global")["scope"] = foreign
        self.assertTrue(any("scope_allowlist" in error for error in validate_role_context_pack(stored)))
        two_projects = {**role, "scope_allowlist": [*role["scope_allowlist"], foreign]}
        self.assertTrue(any("at most one project" in error for error in validate_role_context_pack(two_projects)))

    def assert_forged_allowlist_refused(self, base: dict, edit: object, moved: dict | None, message: str) -> None:
        """A hand-built allowlist fails the recall validator, the builder and, with a recomputed hash, the role validator.

        `edit` rewrites the declared allowlist; `moved`, when given, moves the
        user-global record into the forged scope so the records stay inside
        it and only the allowlist rule under test can refuse the pack.
        """
        forged = json.loads(json.dumps(base))
        forged["scope_allowlist"] = edit(forged["scope_allowlist"])
        if moved is not None:
            next(item for item in forged["included_records"] if item["scope"]["kind"] == "user-global")["scope"] = moved
        recall_errors = validate_project_memory_recall_pack(forged)
        self.assertTrue(any(message in error for error in recall_errors), recall_errors)
        with self.assertRaises(ValueError):
            build_role_context_pack(memory_recall_pack=forged)
        role = json.loads(json.dumps(build_role_context_pack(memory_recall_pack=base)))
        role["scope_allowlist"] = forged["scope_allowlist"]
        if moved is not None:
            next(record for record in role["records"] if record["scope"]["kind"] == "user-global")["scope"] = moved
        role["pack_hash"] = role_context_pack_hash(role)
        role_errors = validate_role_context_pack(role)
        self.assertTrue(any(message in error for error in role_errors), role_errors)

    def test_an_allowlist_no_selector_builds_is_refused(self) -> None:
        in_repo = self.console_pack(self.seeded(self.repo / ".omh", [("project", self.identity), ("user-global", "default")]))
        project_a = {"kind": "project", "ref": self.identity}
        project_b = {"kind": "project", "ref": "prj:" + "b" * 64}
        bob = {"kind": "user", "ref": "principal-bob"}
        other_thread = {"kind": "thread", "ref": "session-b"}
        another_user = {"kind": "user-global", "ref": "another-user"}
        run = {"kind": "run", "ref": "run-1"}
        cases = [
            ("a second principal", lambda scopes: [*scopes, {"kind": "user", "ref": "principal-alice"}, bob], bob, "at most one user scope"),
            ("an extra thread", lambda scopes: [*scopes, other_thread], other_thread, "at most one thread scope"),
            ("a repeated scope", lambda scopes: [*scopes, next(item for item in scopes if item["kind"] == "thread")], None, "at most one thread scope"),
            ("a foreign user-global", lambda scopes: [another_user if item["kind"] == "user-global" else item for item in scopes], another_user, "user-global only as user-global:default"),
            ("project B in a project A pack", lambda scopes: [*scopes, project_b], project_b, "no project but the pack's own scope"),
            ("the pack's own scope missing", lambda scopes: [item for item in scopes if item != project_a], None, "must contain the pack's scope"),
            ("a kind no selector reads", lambda scopes: [*scopes, run], run, "must name only"),
        ]
        for name, edit, moved, message in cases:
            with self.subTest(name):
                self.assert_forged_allowlist_refused(in_repo, edit, moved, message)

    def test_an_unresolved_pack_declares_no_project(self) -> None:
        outside = self.console_pack(self.seeded(self.home, [("user-global", "default"), ("thread", "session-a")]))
        project_b = {"kind": "project", "ref": "prj:" + "b" * 64}
        self.assert_forged_allowlist_refused(outside, lambda scopes: [*scopes, project_b], project_b, "no project but the pack's own scope")

    def test_only_a_recall_record_may_sit_in_the_allowlist_off_the_pack_scope(self) -> None:
        pack = self.console_pack(self.seeded(self.repo / ".omh", [("project", self.identity), ("user-global", "default")]))
        role = json.loads(json.dumps(build_role_context_pack(memory_recall_pack=pack)))
        record = next(record for record in role["records"] if record["scope"]["kind"] == "user-global")
        record["origin"] = HANDOFF_CONTEXT_PACK_SCHEMA_VERSION
        role["pack_hash"] = role_context_pack_hash(role)
        errors = validate_role_context_pack(role)
        self.assertTrue(any("for a recall record, its scope_allowlist" in error for error in errors), errors)
        # The builder refuses a context-pack memory item off the pack scope even when the allowlist names its scope.
        context_pack = {
            "included_context": [
                {
                    "item_id": "ctx-user-global",
                    "key": "release checklist",
                    "summary": "Release checklist fixture",
                    "source": "omh_memory",
                    "truth_level": "approved_context",
                    "scope": {"kind": "user-global", "ref": "default"},
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "scope must match across packs and included records"):
            build_role_context_pack(context_pack=context_pack, memory_recall_pack=pack)


class OutsideRepositoryControlPlaneTests(_Homes):
    """The operator recall, the coding handoff and the recall incident use the live turn's outside-repository rule."""

    def test_operator_recall_and_the_handoff_read_user_global_outside_a_repository(self) -> None:
        self.start("cli-repo", platform="cli", cwd=self.repo)
        project = self.tool("capture", "cli-repo", self.repo, summary="The staging cluster drains nodes before each upgrade.")
        self.start("cli-outside", platform="cli", cwd=self.outside)
        personal = self.tool("capture", "cli-outside", self.outside, summary="The staging cluster notes are kept in the operator's paper notebook.")
        prefix = ["--omh-home", str(self.home), "--hermes-home", str(self.hermes)]
        with chdir(self.outside):
            status, stdout, stderr = run_cli(prefix + ["memory", "recall", "staging", "cluster"])
        self.assertEqual(status, 0, stderr)
        self.assertEqual([row["record_id"] for row in json.loads(stdout)["included_records"]], [personal["record_id"]])
        # The user store's parent is no repository either. An operator
        # console's handoff reads user-global there.
        paths = resolve_paths(self.home, self.hermes)
        pack = memory_recall_pack_for_handoff(paths, "staging cluster upgrade", surface_class=SURFACE_OPERATOR_CONSOLE)
        assert pack is not None
        included = [row["record_id"] for row in pack["included_records"]]
        self.assertEqual(included, [personal["record_id"]])
        self.assertNotIn(project["record_id"], included)
        self.assertEqual(pack["scope"]["kind"], "unresolved")

    def test_a_chat_sourced_handoff_carries_no_user_global_record(self) -> None:
        # A wrapper builds coding handoffs for chat-sourced sessions, and the
        # pack can travel back to that chat.
        self.start("cli-outside", platform="cli", cwd=self.outside)
        personal = self.tool("capture", "cli-outside", self.outside, summary="The operator keeps release passwords in a hardware key.")
        paths = resolve_paths(self.home, self.hermes)
        for source in ("discord", "slack", "telegram", "github", "generic", "hermes", "bogus"):
            with self.subTest(source=source):
                self.assertEqual(memory_surface_for_source(source), SURFACE_SHARED)
                pack = memory_recall_pack_for_handoff(paths, "release passwords", session_id="discord-chat-42", surface_class=memory_surface_for_source(source))
                self.assertNotIn(personal["record_id"], json.dumps(pack))
        self.assertNotIn(personal["record_id"], json.dumps(memory_recall_pack_for_handoff(paths, "release passwords")), "an unstated surface is shared")
        for source in ("cli", "tui", "desktop"):
            self.assertEqual(memory_surface_for_source(source), SURFACE_OPERATOR_CONSOLE)
        console = memory_recall_pack_for_handoff(paths, "release passwords", surface_class=memory_surface_for_source("tui"))
        self.assertIn(personal["record_id"], [row["record_id"] for row in console["included_records"]])
        # The wrapper's own path for a Discord-sourced session; the same
        # message on a console would carry the record.
        message = "risky refactor of the release passwords rotation"
        self.assertIn(personal["record_id"], json.dumps(memory_recall_pack_for_handoff(paths, message, surface_class=SURFACE_OPERATOR_CONSOLE)))
        started = create_or_resume_wrapper_session(paths, message, source="discord")
        session_id = str(started["session"]["session_id"])
        record_plan_decision(paths, session_id, "accept")
        select_wrapper_session_executor(paths, session_id, "claude-code")
        prepared = prepare_wrapper_session_handoff(paths, session_id, message)
        self.assertNotIn(personal["record_id"], json.dumps(prepared))

    def test_coding_delegate_is_a_console_only_from_a_terminal(self) -> None:
        def args(**overrides: object) -> Namespace:
            return Namespace(**{"event_json": None, "stdin": False, "source": "generic", **overrides})

        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), patch.dict(os.environ):
            os.environ.pop("HERMES_SESSION_PLATFORM", None)
            self.assertEqual(_delegate_memory_surface(args()), SURFACE_OPERATOR_CONSOLE)
            self.assertEqual(_delegate_memory_surface(args(source="discord")), SURFACE_SHARED)
            self.assertEqual(_delegate_memory_surface(args(event_json="event.json")), SURFACE_SHARED)
            self.assertEqual(_delegate_memory_surface(args(stdin=True)), SURFACE_SHARED)
            # A child of a gateway session carries the session's platform
            # (`_inject_session_context_env`), pseudo-terminal or not.
            for platform, expected in (("discord", SURFACE_SHARED), ("telegram", SURFACE_SHARED), ("tui", SURFACE_OPERATOR_CONSOLE), ("cli", SURFACE_OPERATOR_CONSOLE)):
                with self.subTest(session_platform=platform):
                    os.environ["HERMES_SESSION_PLATFORM"] = platform
                    self.assertEqual(_delegate_memory_surface(args()), expected)
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=False):
            self.assertEqual(_delegate_memory_surface(args()), SURFACE_SHARED, "Hermes pipes the command's output")

    def test_a_console_receipt_made_outside_a_repository_is_authoritative(self) -> None:
        self.start("cli-outside", platform="cli", cwd=self.outside)
        personal = self.tool("capture", "cli-outside", self.outside, summary="The operator prefers replies without emoji.")
        live = self.start("session-a", platform="cli", cwd=self.outside)
        hermes_turn(live, 1, "emoji in replies")
        receipt = live.latest_prefetch_receipt()
        assert receipt is not None
        self.assertEqual([row["record_id"] for row in receipt["rendering"]["rendered_records"]], [personal["record_id"]])
        prefix = ["--omh-home", str(self.home), "--hermes-home", str(self.hermes)]
        with chdir(self.outside):
            status, stdout, stderr = run_cli(
                prefix + ["memory", "recall-incident", "--record-id", personal["record_id"], "--session-id", "session-a", "--scope-kind", "user-global", "--scope-ref", "default"]
            )
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            json.loads(stdout)["evidence_surfaces"]["live_prefetch_receipt"], {"status": "observed", "basis": "canonical_1452_receipt_bound"}
        )


if __name__ == "__main__":
    unittest.main()
