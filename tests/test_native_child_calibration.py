"""Calibration delivery must survive plugin registration and native rendering (#2214)."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh import register  # noqa: E402
from omh.coding import unit_prompt_protocol as protocol  # noqa: E402
from test_delegation_protocol_section import _SectionCtx  # noqa: E402
from test_delegation_protocol_section import (  # noqa: E402
    _HOST_SECTIONS_CONTAINER,
    _ManagedSectionCtx,
    _Manager,
    _host_framed,
)
from _standalone_bundle import run_in_no_package_host  # noqa: E402

SECTION_ID = "omh.child-calibration"


class NativeChildCalibrationTests(unittest.TestCase):
    def section(self):
        ctx = _SectionCtx()
        register(ctx)
        sections = [section for section in ctx.sections if section["id"] == SECTION_ID]
        self.assertEqual(len(sections), 1, "native children must register their own calibration section")
        return sections[0]

    def test_registered_section_renders_the_childs_exact_model_once(self):
        section = self.section()
        text = section["content"]({"platform": "subagent", "model": "openai/gpt-6.1-sol"})
        expected = protocol.MODEL_HIGH_EFFORT_CALIBRATIONS["gpt-6.1-sol"]
        self.assertEqual(text.count(expected), 1)
        self.assertNotIn(protocol.HIGH_EFFORT_CALIBRATIONS["gpt"], text)
        self.assertEqual(section["position"], "after_memory")
        self.assertLessEqual(len(text), section["max_chars"])

    def test_every_effort_and_missing_effort_render_the_same_child_calibration(self):
        callback = self.section()["content"]
        base = {"platform": "subagent", "model": "anthropic/claude-opus-5-5"}
        expected = protocol.HIGH_EFFORT_CALIBRATIONS["claude"]
        self.assertEqual(callback(base), expected)
        for effort in (None, "", "none", "low", "medium", "high", "xhigh", "max", "unknown"):
            with self.subTest(effort=effort):
                self.assertEqual(callback({**base, "reasoning_effort": effort,
                                           "selected_reasoning_effort": effort}), expected)

    def test_child_model_not_provider_or_parent_identity_selects_the_block(self):
        from omh.plugin_bundle.omh import host_context

        callback = self.section()["content"]
        cases = (("claude-opus-5-5", "gpt-6.1-sol", "claude"),
                 ("gpt-6-terra", "claude-opus-5-5", "gpt"))
        # A recorded main-agent identity is available but must never be queried.
        with patch.object(host_context, "active_main_agent_model", side_effect=AssertionError("parent read")):
            for child, parent, family in cases:
                self.assertEqual(callback({"platform": "subagent", "model": child,
                                           "parent_model": parent, "provider": parent}),
                                 protocol.HIGH_EFFORT_CALIBRATIONS[family])

    def test_exact_family_alias_snapshot_and_generic_selection(self):
        callback = self.section()["content"]
        cases = {
            "openai/gpt-6-astra": "gpt-6-astra",
            "gateway/openai/GPT-6.1-SOL": "gpt-6.1-sol",
            "openai/gpt-6.1-sol-pro": "gpt-6.1-sol",
            "gpt-6.1-sol-2026-10-01": "gpt-6.1-sol",
            "gpt-6-astra-pro-2026-10-01": "gpt-6-astra",
            "gpt-6.1-sol-2026-13-01": "gpt",
            "gpt-6.1-sol-2026-10-01-2026-10-02": "gpt",
            "gpt-6.1-sol-unlisted": "gpt",
            "gpt-5.6-sol": "gpt",
            "gpt-6-terra": "gpt",
            "deepseek/deepseek-v4.1-flash": "deepseek-v4.1-flash",
            "deepseek/deepseek-flash": "deepseek-v4.1-flash",
            "deepseek-flash-2026-10-01": "deepseek-v4.1-flash",
            "anthropic/claude-opus-5-5": "claude",
            "bedrock/us.anthropic.claude-opus-5-5": "claude",
            "anthropic-claude-sonnet-5.5": "claude",
            "fable": "claude",
            "qwen/qwen3.6-coder-plus": "qwen",
            "openai-gpt-6-terra": "gpt",
            "openai-gpt-image-1": "generic",
            "qwen/qwen3": "generic",
            "new-vendor/new-model-2026-10-01": "generic",
            "": "generic",
        }
        for model, key in cases.items():
            with self.subTest(model=model):
                table = (protocol.MODEL_HIGH_EFFORT_CALIBRATIONS if key in protocol.MODEL_HIGH_EFFORT_CALIBRATIONS
                         else protocol.HIGH_EFFORT_CALIBRATIONS)
                self.assertEqual(callback({"platform": "subagent", "model": model}), table[key])
        for info in ({"platform": "subagent"}, {"platform": "subagent", "model": None}):
            self.assertEqual(callback(info), protocol.HIGH_EFFORT_CALIBRATIONS["generic"])

    def test_nonchild_and_malformed_sessions_are_strictly_empty(self):
        callback = self.section()["content"]
        for platform in ("cli", "tui", "discord", "telegram", "cron", "desktop", "", None,
                         "SUBAGENT", " subagent", "subagent "):
            with self.subTest(platform=platform):
                self.assertEqual(callback({"platform": platform, "model": "gpt-6.1-sol"}), "")
        for info in (None, [], "subagent", {"model": "gpt-6.1-sol"}):
            self.assertEqual(callback(info), "")

    def test_every_registered_block_fits_the_host_framed_prompt_budget(self):
        ctx = _SectionCtx()
        register(ctx)
        section = next(s for s in ctx.sections if s["id"] == SECTION_ID)
        models = [*protocol.MODEL_HIGH_EFFORT_CALIBRATIONS]
        models += [f"{key}-test" for key in protocol.HIGH_EFFORT_CALIBRATIONS]
        self.assertLessEqual(section["max_chars"], 4000)
        for model in models:
            with self.subTest(model=model):
                info = {"session_id": "budget-child", "platform": "subagent", "model": model}
                text = section["content"](info)
                self.assertTrue(text)
                self.assertLessEqual(len(text.strip()), section["max_chars"])
                self.assertNotIn("<!-- hermes-plugin-sections:", text)
                blocks = [_host_framed(s["id"], s["content"](info))
                          for s in sorted(ctx.sections, key=lambda s: s["id"])]
                total = len(_HOST_SECTIONS_CONTAINER) + len("\n\n".join(blocks))
                self.assertLess(total, 4000)  # OMH's tighter share of the host's 8000.

    def test_duplicate_loader_pass_skips_but_another_manager_registers(self):
        manager = _Manager()
        first = _ManagedSectionCtx(manager)
        register(first)
        second = _ManagedSectionCtx(manager)
        register(second)
        self.assertEqual(first.attempts.count(SECTION_ID), 1)
        self.assertNotIn(SECTION_ID, second.attempts)
        other = _ManagedSectionCtx(_Manager())
        register(other)
        self.assertEqual(other.attempts.count(SECTION_ID), 1)

    def test_standalone_plugin_registers_and_renders_without_core(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_in_no_package_host('''
class Context:
    def __init__(self): self.sections = {}
    def register_tool(self, *args, **kwargs): pass
    def register_hook(self, *args, **kwargs): pass
    def register_system_prompt_section(self, id, content, **kwargs): self.sections[id] = content
ctx = Context()
module.register(ctx)
callback = ctx.sections["omh.child-calibration"]
from hermes_plugin_omh.calibration import MODEL_HIGH_EFFORT_CALIBRATIONS
text = callback({"platform": "subagent", "model": "openai/gpt-6.1-sol"})
print(json.dumps({"core_available": omh_spec, "exact": text == MODEL_HIGH_EFFORT_CALIBRATIONS["gpt-6.1-sol"],
                  "parent": callback({"platform": "cli", "model": "gpt-6.1-sol"})}))
''', cwd=Path(directory))
        self.assertEqual(result, {"core_available": False, "exact": True, "parent": ""})


class SharedCalibrationTests(unittest.TestCase):
    def test_control_plane_reexports_the_shared_definitions_and_resolver(self):
        from omh.plugin_bundle.omh import calibration

        for name in ("HIGH_EFFORT_CALIBRATIONS", "MODEL_HIGH_EFFORT_CALIBRATIONS",
                     "MAIN_AGENT_COMPOSITION_CALIBRATIONS", "MODEL_COMPOSITION_CALIBRATIONS",
                     "calibration_entry_for_model"):
            self.assertIs(getattr(protocol, name), getattr(calibration, name))

    def test_mutating_a_shared_definition_changes_both_real_consumers(self):
        from omh.plugin_bundle.omh import calibration

        callback = NativeChildCalibrationTests().section()["content"]
        for table, key, model, family in (
            (calibration.MODEL_HIGH_EFFORT_CALIBRATIONS, "gpt-6.1-sol", "openai/gpt-6.1-sol", "gpt"),
            (calibration.HIGH_EFFORT_CALIBRATIONS, "claude", "anthropic/claude-opus-5-5", "claude"),
        ):
            with self.subTest(key=key), patch.dict(table, {key: "shared mutation sentinel"}):
                self.assertEqual(callback({"platform": "subagent", "model": model}), "shared mutation sentinel")
                self.assertEqual(protocol.calibration_for_route({"selected_model": model, "model_family": family,
                                                                "selected_reasoning_effort": "high"}),
                                 "shared mutation sentinel")

    def test_native_resolution_matches_the_existing_contract_and_family_roster(self):
        from omh.coding.model_contracts import MODEL_CONTRACTS, DECLARED_MODEL_CONTRACT_PROJECTIONS
        from omh.coding.model_routing import model_family
        from omh.plugin_bundle.omh.calibration import calibration_entry_for_model

        models = [*MODEL_CONTRACTS, *DECLARED_MODEL_CONTRACT_PROJECTIONS,
                  "fable", "mythos", "qwen3.6-coder-plus", "new-model", "", "openai-gpt-image-1"]
        for model in models:
            for qualified in (model, f"provider/{model}", f"{model}-2026-10-01"):
                with self.subTest(model=qualified):
                    route = {"selected_model": qualified, "model_family": model_family(qualified),
                             "selected_reasoning_effort": "high"}
                    self.assertEqual(calibration_entry_for_model(qualified), protocol.calibration_entry_for_route(route))

    def test_fanout_gate_recorded_family_and_family_only_are_unchanged(self):
        route = {"selected_model": "openai/gpt-6.1-sol", "model_family": "claude"}
        for effort in ("", None, "none", "low", "medium", "unknown"):
            for family_only in (False, True):
                with self.subTest(effort=effort, family_only=family_only):
                    self.assertIsNone(protocol.calibration_entry_for_route(
                        {**route, "selected_reasoning_effort": effort}, family_only=family_only))
        for effort in ("high", "xhigh", "max", "HIGH"):
            routed = {**route, "selected_reasoning_effort": effort}
            self.assertEqual(protocol.calibration_entry_for_route(routed)[0], "gpt-6.1-sol")
            self.assertEqual(protocol.calibration_entry_for_route(routed, family_only=True),
                             ("claude", protocol.HIGH_EFFORT_CALIBRATIONS["claude"]))
        for model, family, expected in (("gpt-6-terra", "claude", "claude"),
                                         ("new-model", "new-family", "generic"),
                                         ("gpt-6-terra", "", "generic")):
            self.assertEqual(protocol.calibration_entry_for_route({"selected_model": model, "model_family": family,
                                                                   "selected_reasoning_effort": "high"})[0], expected)
        for route in (None, [], "high"):
            self.assertIsNone(protocol.calibration_entry_for_route(route))

    def test_family_only_does_not_resolve_the_selected_model(self):
        route = {"selected_model": "openai/gpt-6.1-sol", "model_family": "claude",
                 "selected_reasoning_effort": "high"}
        with patch.object(protocol, "contract_model_id", side_effect=AssertionError("model resolved")):
            self.assertEqual(protocol.calibration_entry_for_route(route, family_only=True),
                             ("claude", protocol.HIGH_EFFORT_CALIBRATIONS["claude"]))


if __name__ == "__main__":
    unittest.main()
