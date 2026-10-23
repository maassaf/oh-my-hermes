"""The delegation protocol reaches every Hermes `delegate_task` child (#2174).

The `coding-handling` harness requires the protocol in every delegated or
reviewer prompt, runtime-native spawns included. OMH's own unit prompts carried
it; a Hermes-native child got it only when the parent model pasted it in. The
plugin now registers the `omh.delegation-protocol` system prompt section,
which renders for a child (`platform == "subagent"`) and is empty for every
other session, so the parent's own prompt does not change.

Everything except the single-definition check reaches the section through
`register()`, so on a tree without it these tests fail on what the plugin
registers, not on a missing import.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _local_package import load_local_package

load_local_package()

from _cli_harness import run_cli  # noqa: E402
from omh.coding import unit_prompt_protocol  # noqa: E402
from omh.commands import setup as setup_commands  # noqa: E402
from omh.maintenance.doctor import _delegation_protocol_platform_check, run_doctor  # noqa: E402
from omh.paths import OmhPaths  # noqa: E402
from omh.system.hermes_install import MAX_BOUNDED_READ_BYTES  # noqa: E402
from omh.plugin_bundle.omh import register  # noqa: E402
from omh.plugin_bundle.omh.hooks import tool_hooks  # noqa: E402
from omh.skills.catalog import harness_definition  # noqa: E402

SECTION_ID = "omh.delegation-protocol"
# Hermes' own limits (`hermes_cli/plugins_dispatch.py`), copied because the
# host is not importable here.
_HOST_MAX_SECTION_CHARS = 4000
_HOST_MAX_SECTIONS_TOTAL_CHARS = 8000
_HOST_SECTIONS_CONTAINER = "<!-- hermes-plugin-sections:start -->\n" + "\n<!-- hermes-plugin-sections:end -->"
# The line that builds a `delegate_task` child, verbatim from Hermes'
# `tools/delegate_tool.py` (:277 at v0.22.2, d923a5eb48; :237 at v2026.9.7).
_HERMES_CHILD_BUILD_LINE = (
    '                ephemeral_system_prompt=child_prompt, log_prefix=f"[subagent-{task_index}]", platform="subagent",\n'
)
# The composition-guide entries a delegated agent acts on verbatim. `goal_echo`
# reaches a child with its criteria made conditional (`_child_goal_echo`);
# `prompt_cache` and `applies_to` tell the composing parent how to write a
# delegate prompt.
_CHILD_PROTOCOL_KEYS = ("verification_stop", "review_cap")


def _session_info(platform: str, session_id: str = "s-1") -> dict[str, str]:
    return {
        "session_id": session_id,
        "model": "gpt-6-astra",
        "provider": "openai-codex",
        "platform": platform,
        "profile_name": "default",
        "cwd": "/tmp/project-a",
    }


def _host_framed(section_id: str, text: str) -> str:
    return f"## Plugin Context: {section_id}\n<!-- hermes-plugin-section-chars:{len(text)} -->\n\n{text}"


def _child_goal_echo(goal_echo: str) -> str:
    """The guide's goal echo-back as a child reads it: the numbered criteria only if the goal has them."""
    conditional = goal_echo.replace("the numbered criteria. ", "the numbered criteria, if the goal has them. ", 1)
    if conditional == goal_echo:
        raise AssertionError(f"the goal echo-back no longer names the numbered criteria: {goal_echo!r}")
    return conditional


def _composition_guide_protocol() -> dict[str, str]:
    status, stdout, _stderr = run_cli(["coding", "composition-guide", "--json"])
    if status != 0:
        raise AssertionError(f"composition-guide exited {status}")
    return json.loads(stdout)["delegation_protocol"]


class _SectionCtx:
    def __init__(self) -> None:
        self.sections: list[dict[str, object]] = []

    def register_tool(self, name, *args, **kwargs) -> None:
        pass

    def register_hook(self, name, callback) -> None:
        pass

    def register_system_prompt_section(self, id, content, *, position="after_memory", max_chars=4000):
        self.sections.append({"id": id, "content": content, "position": position, "max_chars": max_chars})


class _Manager:
    def __init__(self) -> None:
        self._system_prompt_sections: dict[str, object] = {}


class _ManagedSectionCtx(_SectionCtx):
    """A plugin-loader context that refuses a second registration, as Hermes does.

    Hermes logs that refusal on every session init, so an attempt is recorded
    even when it is refused.
    """

    def __init__(self, manager: _Manager) -> None:
        super().__init__()
        self._manager = manager
        self.attempts: list[str] = []

    def register_system_prompt_section(self, id, content, **kwargs):
        self.attempts.append(id)
        if id in self._manager._system_prompt_sections:
            raise ValueError(f"system prompt section {id!r} is already registered by plugin 'omh'")
        self._manager._system_prompt_sections[id] = content
        super().register_system_prompt_section(id, content, **kwargs)


def _registered_section(ctx: _SectionCtx) -> dict[str, object]:
    found = [section for section in ctx.sections if section["id"] == SECTION_ID]
    if len(found) != 1:
        raise AssertionError(f"expected one {SECTION_ID} registration, got {len(found)}")
    return found[0]


def _render(platform: object, session_id: str = "s-1") -> str:
    """What the registered section renders for a session on `platform`."""
    ctx = _SectionCtx()
    register(ctx)
    content = _registered_section(ctx)["content"]
    info = _session_info(platform, session_id) if isinstance(platform, str) else platform
    return content(info)


class ProducerParityTests(unittest.TestCase):
    def test_a_child_section_carries_every_composition_guide_protocol_line(self) -> None:
        protocol = _composition_guide_protocol()
        text = _render("subagent")
        self.assertIn(_child_goal_echo(protocol["goal_echo"]), text)
        for key in _CHILD_PROTOCOL_KEYS:
            with self.subTest(key=key):
                self.assertIn(protocol[key], text)

    def test_the_control_plane_reads_the_bundle_definitions(self) -> None:
        from omh.plugin_bundle.omh import delegation_protocol

        for name in ("GOAL_ECHO_PROTOCOL", "VERIFICATION_STOP_PROTOCOL", "REVIEW_ROLE_PROTOCOL"):
            with self.subTest(name=name):
                self.assertIs(getattr(unit_prompt_protocol, name), getattr(delegation_protocol, name))


class FramingTests(unittest.TestCase):
    """The section also reaches children no OMH-guided parent composed, such as
    Hermes' own `/review` reviewer, so only the goal echo-back is unconditional,
    and even it asks for numbered criteria only when the goal has them."""

    def test_only_the_goal_echo_applies_to_every_child(self) -> None:
        protocol = _composition_guide_protocol()
        lines = _render("subagent").splitlines()
        self.assertIn(f"- {_child_goal_echo(protocol['goal_echo'])}", lines)
        self.assertIn(f"- When your task changes files or other state: {protocol['verification_stop']}", lines)
        self.assertIn(
            f"- When you review work against stated success criteria: {protocol['review_cap']}", lines
        )

    def test_the_shared_goal_echo_keeps_its_criteria_unconditional(self) -> None:
        # Fanout unit prompts and the composition guide read the shared
        # constant, and an OMH-composed prompt always carries numbered
        # criteria, so only the child section's line is conditional.
        protocol = _composition_guide_protocol()
        self.assertNotIn("if the goal has them", protocol["goal_echo"])
        self.assertNotIn(f"- {protocol['goal_echo']}", _render("subagent").splitlines())


class RegistrationTests(unittest.TestCase):
    def test_the_plugin_registers_the_section_and_it_renders_for_a_child(self) -> None:
        ctx = _SectionCtx()
        register(ctx)
        section = _registered_section(ctx)
        from omh.plugin_bundle.omh import delegation_protocol

        self.assertEqual(section["position"], "after_memory")
        text = section["content"](_session_info("subagent"))
        self.assertEqual(text, delegation_protocol.DELEGATION_PROTOCOL_SECTION)
        # The host skips a section longer than its `max_chars`.
        self.assertLessEqual(len(text.strip()), int(section["max_chars"]))
        self.assertLessEqual(int(section["max_chars"]), _HOST_MAX_SECTION_CHARS)

    def test_both_omh_sections_together_leave_most_of_the_shared_budget(self) -> None:
        ctx = _SectionCtx()
        register(ctx)
        _registered_section(ctx)
        info = _session_info("subagent")
        blocks = [_host_framed(str(s["id"]), s["content"](info)) for s in sorted(ctx.sections, key=lambda s: s["id"])]
        rendered = len(_HOST_SECTIONS_CONTAINER) + len("\n\n".join(blocks))
        self.assertLess(rendered, _HOST_MAX_SECTIONS_TOTAL_CHARS // 2)

    def test_a_second_loader_pass_does_not_register_it_again(self) -> None:
        manager = _Manager()
        first = _ManagedSectionCtx(manager)
        register(first)
        second = _ManagedSectionCtx(manager)
        register(second)
        self.assertEqual([s["id"] for s in first.sections].count(SECTION_ID), 1)
        self.assertNotIn(SECTION_ID, second.attempts)


class RenderTests(unittest.TestCase):
    def test_each_child_section_carries_the_protocol_once(self) -> None:
        protocol = _composition_guide_protocol()
        texts = [_render("subagent", f"child-{index}") for index in range(3)]
        self.assertEqual(len(set(texts)), 1)
        for text in texts:
            self.assertEqual(text.count(_child_goal_echo(protocol["goal_echo"])), 1)
            for key in _CHILD_PROTOCOL_KEYS:
                with self.subTest(key=key):
                    self.assertEqual(text.count(protocol[key]), 1)

    def test_the_parent_and_every_other_session_render_nothing(self) -> None:
        self.assertNotEqual(_render("subagent"), "")
        for platform in ("cli", "tui", "telegram", "discord", "cron", "desktop", "", "SUBAGENT", " subagent"):
            with self.subTest(platform=platform):
                self.assertEqual(_render(platform), "")
        self.assertEqual(_render({"session_id": "s-1"}), "")
        self.assertEqual(_render(None), "")


class DelegateToolCallTests(unittest.TestCase):
    """The section is the delivery; a `delegate_task` call's own arguments stay the model's."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.omh_home = Path(tmp.name) / "omh"
        self.hermes_home = Path(tmp.name) / "hermes"
        self.omh_home.mkdir()
        self.hermes_home.mkdir()

    def pre_tool_call(self, tool_name: str, args: dict[str, object]) -> object:
        return tool_hooks.pre_tool_call(
            tool_name=tool_name,
            args=args,
            session_id="parent-1",
            task_id="parent-1",
            omh_home=str(self.omh_home),
            hermes_home=str(self.hermes_home),
        )

    def test_no_tool_call_has_its_arguments_rewritten(self) -> None:
        calls = (
            ("delegate_task", {"goal": "Print the word hello."}),
            ("delegate_task", {"tasks": [{"goal": "a"}, {"goal": "b"}, {"goal": "c"}]}),
            ("terminal", {"command": "ls"}),
            ("read_file", {"path": "README.md"}),
        )
        for tool_name, args in calls:
            with self.subTest(tool_name=tool_name, args=args):
                result = self.pre_tool_call(tool_name, args)
                self.assertFalse(isinstance(result, dict) and result.get("action") == "modify", result)


class HarnessGuidanceTests(unittest.TestCase):
    """The parent stops pasting the protocol where the plugin already carries it."""

    def test_coding_handling_leaves_hermes_children_to_the_plugin(self) -> None:
        lines = [line for line in harness_definition("coding-handling").quality_bar if "composition-guide" in line]
        self.assertEqual(len(lines), 1, lines)
        line = lines[0]
        self.assertIn("On Hermes `delegate_task` the OMH plugin puts the protocol in each child's system prompt", line)
        self.assertIn("do not paste it into the goal", line)
        self.assertIn("still write the numbered done criteria there", line)
        self.assertIn("For Codex, Claude Code, and other runtime-native spawns, embed it in the prompt yourself", line)

    def test_the_harness_line_the_guide_and_the_section_state_the_same_conditions(self) -> None:
        # The verification pass binds a task that changes files or other
        # state, and the review cap a task that reviews against stated
        # criteria, wherever the parent or the child reads the protocol.
        lines = [line for line in harness_definition("coding-handling").quality_bar if "composition-guide" in line]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn(
            "exactly one mandatory verification pass with a two fix-and-verify cycle cap when the task changes "
            "files or other state",
            lines[0],
        )
        applies_to = _composition_guide_protocol()["applies_to"]
        self.assertIn("verification_stop binds a task that changes files or other state", applies_to)
        self.assertIn("review_cap one that reviews work against stated success criteria", applies_to)
        status, stdout, _stderr = run_cli(["coding", "composition-guide", "--model", "gpt-6-astra"], output_json=False)
        self.assertEqual(status, 0)
        section_lines = _render("subagent").splitlines()
        for condition in ("When your task changes files or other state: ", "When you review work against stated success criteria: "):
            with self.subTest(condition=condition):
                guide_lines = [line for line in stdout.splitlines() if line.startswith(f"- {condition}")]
                self.assertEqual(len(guide_lines), 1, stdout)
                self.assertIn(guide_lines[0], section_lines)

    def test_composition_guide_gives_the_parent_the_same_instruction(self) -> None:
        # The harness line names composition-guide as the protocol's source, so
        # the two must not disagree about pasting.
        applies_to = _composition_guide_protocol()["applies_to"]
        self.assertIn("On Hermes `delegate_task` the OMH plugin puts goal_echo, verification_stop", applies_to)
        self.assertIn("do not paste them into the goal", applies_to)
        self.assertIn("its goal_echo asking for the numbered criteria only if the goal has them", applies_to)
        self.assertIn("For Codex, Claude Code, and other runtime-native spawns, embed them", applies_to)
        status, stdout, _stderr = run_cli(["coding", "composition-guide", "--model", "gpt-6-astra"], output_json=False)
        self.assertEqual(status, 0)
        header = next(line for line in stdout.splitlines() if line.startswith("Delegation protocol ("))
        self.assertIn("each Hermes `delegate_task` child, so do not paste them there", header)
        self.assertIn("its goal echo asks for the numbered criteria only if the goal has them", header)
        self.assertIn("embed them for Codex, Claude Code", header)


class DoctorPlatformCheckTests(unittest.TestCase):
    """`omh doctor` reads the installed Hermes for the platform the section keys on,
    so a rename shows as a warning instead of every child quietly losing the protocol."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.paths = OmhPaths(omh_home=self.root / "omh", hermes_home=self.root / "hermes")

    def install(self, source: str | None) -> Path:
        tools = self.paths.hermes_home / "hermes-agent" / "tools"
        tools.mkdir(parents=True)
        if source is not None:
            (tools / "delegate_tool.py").write_text(source, encoding="utf-8")
        return tools / "delegate_tool.py"

    def test_hermes_building_the_child_with_the_section_platform_reads_ok(self) -> None:
        source = self.install(_HERMES_CHILD_BUILD_LINE)
        check = _delegation_protocol_platform_check(self.paths)
        self.assertEqual((check.ok, check.severity, check.observed), (True, "ok", True))
        self.assertIn('platform="subagent"', check.message)
        self.assertIn(str(source), check.message)
        # The literal the doctor looks for is the platform the section renders for.
        self.assertNotEqual(_render("subagent"), "")

    def test_a_renamed_platform_warns_and_says_what_to_do(self) -> None:
        self.install(_HERMES_CHILD_BUILD_LINE.replace('platform="subagent"', 'platform="delegate"'))
        check = _delegation_protocol_platform_check(self.paths)
        self.assertEqual((check.ok, check.severity, check.observed), (True, "warning", True))
        self.assertIn('no longer builds a delegate_task child with platform="subagent"', check.message)
        # Bare `composition-guide` prints no protocol line; `--model` does.
        self.assertIn("omh coding composition-guide --model <your model>", check.next_action)

    def test_an_install_without_the_delegate_tool_warns_unobserved(self) -> None:
        self.install(None)
        check = _delegation_protocol_platform_check(self.paths)
        self.assertEqual((check.ok, check.severity, check.observed), (True, "warning", False))
        self.assertIn("is not established", check.message)

    def test_a_delegate_tool_over_the_read_cap_is_not_read(self) -> None:
        self.install(_HERMES_CHILD_BUILD_LINE + "#" * MAX_BOUNDED_READ_BYTES)
        check = _delegation_protocol_platform_check(self.paths)
        self.assertEqual((check.ok, check.severity, check.observed), (True, "warning", False))
        self.assertIn("is not established", check.message)

    def test_no_hermes_install_is_not_observed(self) -> None:
        check = _delegation_protocol_platform_check(self.paths)
        self.assertEqual((check.ok, check.severity, check.observed), (True, "ok", False))
        self.assertIn("Hermes install not found", check.message)

    def test_doctor_runs_it_in_the_optional_surfaces_group_and_no_other(self) -> None:
        self.install(_HERMES_CHILD_BUILD_LINE)
        rows = [check for check in run_doctor(self.paths) if check.name == "plugin_delegation_protocol"]
        self.assertEqual(len(rows), 1)
        for group in setup_commands._doctor_operator_summary(rows)["groups"]:
            self.assertEqual(group["total"], 1 if group["name"] == "optional_surfaces" else 0, group["name"])


if __name__ == "__main__":
    unittest.main()
