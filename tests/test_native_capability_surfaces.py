from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.awareness import awareness_primer_payload, workflow_context_card_for_workflow
from omh.routing import recommend as recommend_module
from omh.skill_pack import builtin_definitions, builtin_harnesses, builtin_skill_templates, installable_skill_definitions
from omh.skills.catalog import primary_harness_for_skill, skill_exposure_payload
from omh.wrapper.contract import VISIBLE_ACTIONS, build_chat_interaction_payload


class NativeCapabilitySurfaceTests(unittest.TestCase):
    _CONTRACTS = {
        "decision-recall": {
            "action": "show_rejected_decision_recall",
            "category": "memory",
            "phase": "decision-recall",
            "output": "rejected_decision_recall/v1",
            "prompt": "Show rejected decisions for this project.",
            "lane": "retained_knowledge",
        },
        "run-efficiency": {
            "action": "show_run_efficiency_report",
            "category": "observability",
            "phase": "run-efficiency",
            "output": "run_efficiency_report/v1",
            "prompt": "Show the local run efficiency report.",
            "lane": "automation_and_status",
        },
        "provider-profile-posture": {
            "action": "prepare_provider_profile_posture",
            "category": "operations",
            "phase": "provider-profile-posture",
            "output": "provider_profile_posture/v1",
            "prompt": "Prepare provider profile posture for this connector.",
            "lane": "automation_and_status",
        },
    }

    def test_native_capabilities_are_installable_harnessed_and_visible(self) -> None:
        definitions = {definition.name: definition for definition in builtin_definitions()}
        harnesses = {harness.name: harness for harness in builtin_harnesses()}
        templates = {template.name: template for template in builtin_skill_templates()}
        installable = {definition.name for definition in installable_skill_definitions()}

        for name, contract in self._CONTRACTS.items():
            with self.subTest(name=name):
                self.assertIn(name, definitions)
                self.assertIn(name, harnesses)
                self.assertIn(name, templates)
                self.assertIn(name, installable)
                self.assertEqual(primary_harness_for_skill(name), name)
                self.assertEqual(definitions[name].category, contract["category"])
                self.assertEqual(definitions[name].phase, contract["phase"])
                self.assertIn(contract["output"], definitions[name].expected_outputs)
                self.assertIn(contract["output"], harnesses[name].expected_outputs)
                self.assertIn(contract["action"], harnesses[name].wrapper_actions)
                self.assertIn(contract["action"], VISIBLE_ACTIONS)
                self.assertEqual(skill_exposure_payload(name)["exposure"], "workflow_skill")
                self.assertTrue(skill_exposure_payload(name)["install_visibility"])
                self.assertEqual(recommend_module._SKILL_POLICIES[name].next_action, contract["action"])

    def test_native_capabilities_route_to_their_visible_actions(self) -> None:
        for name, contract in self._CONTRACTS.items():
            with self.subTest(name=name):
                payload = build_chat_interaction_payload(contract["prompt"], source="discord")
                self.assertEqual(payload["route"]["selected_skill"], name)
                self.assertEqual(payload["next_action"], contract["action"])
                self.assertEqual(payload["chat_response"]["actions"][0]["id"], contract["action"])

    def test_native_capability_awareness_stays_lane_scoped(self) -> None:
        lanes = {
            str(lane["id"]): set(lane["skills"])
            for lane in awareness_primer_payload()["lanes"]
        }

        for name, contract in self._CONTRACTS.items():
            with self.subTest(name=name):
                self.assertIn(name, lanes[contract["lane"]])
                self.assertEqual(workflow_context_card_for_workflow(name)["id"], contract["lane"])
