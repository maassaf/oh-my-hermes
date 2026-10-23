"""Structured contract tests for bounded ulw-context frontier interviews."""

from __future__ import annotations

import unittest

from omh.plugin_bundle.omh.native_tools import native_tool_presence_clause
from omh.skills import catalog as skill_catalog
from omh.wrapper.contract import build_chat_interaction_payload


class ContextFrontierHarnessTests(unittest.TestCase):
    def test_context_uses_a_dedicated_frontier_harness(self) -> None:
        self.assertEqual(
            skill_catalog.primary_harness_for_skill("context"),
            "decision-frontier",
        )

        harness = skill_catalog.harness_definition("decision-frontier")
        self.assertIn("round_budget_respected", harness.evidence_ladder)
        self.assertIn("shared_understanding_confirmed", harness.evidence_ladder)
        self.assertTrue(
            any(
                str(skill_catalog.DEEP_INTERVIEW_MAX_ROUNDS) in condition
                for condition in harness.stop_conditions
            ),
            harness.stop_conditions,
        )

    def test_deep_interview_keeps_its_original_harness(self) -> None:
        self.assertEqual(
            skill_catalog.primary_harness_for_skill("deep-interview"),
            "deep-interview",
        )


class ContextFrontierPolicyTests(unittest.TestCase):
    def _policy(self) -> dict[str, object]:
        factory = getattr(skill_catalog, "decision_frontier_policy", None)
        self.assertIsNotNone(
            factory,
            "catalog must expose decision_frontier_policy()",
        )
        assert factory is not None
        return factory()

    def test_policy_exposes_bounded_batch_semantics(self) -> None:
        policy = self._policy()

        self.assertEqual(policy["schema_version"], "decision_frontier_policy/v1")
        self.assertEqual(policy["harness"], "decision-frontier")
        self.assertEqual(
            policy["max_rounds"],
            skill_catalog.DEEP_INTERVIEW_MAX_ROUNDS,
        )
        self.assertEqual(
            policy["soft_check_round"],
            skill_catalog.DEEP_INTERVIEW_SOFT_CHECK_ROUND,
        )
        self.assertEqual(policy["budget_scope"], "clarification_episode")
        self.assertEqual(policy["round_unit"], "dependency_ready_batch")
        self.assertEqual(policy["decision_id_prefix"], "D")
        self.assertEqual(
            policy["decision_states"],
            ["open", "resolved", "deferred", "blocked"],
        )

    def test_policy_exposes_stop_partial_answer_and_recovery_rules(self) -> None:
        policy = self._policy()

        self.assertEqual(
            policy["stop_rule_order"],
            ["frontier_terminal", "user_stop", "round_ceiling"],
        )
        self.assertEqual(policy["partial_answer_policy"], "addressed_only")
        self.assertEqual(policy["omitted_answer_transition"], "none")
        self.assertEqual(
            policy["recommendation_policy"],
            "explicit_acceptance_only",
        )
        self.assertEqual(policy["user_stop_scope"], "questioning_only")
        self.assertEqual(
            policy["compaction_failure_action"],
            "close_with_recovery_blocker",
        )
        self.assertEqual(
            policy["consent_gates"],
            ["frontier_entry", "summary_confirmation", "next_path"],
        )

    def test_policy_returns_fresh_nested_lists(self) -> None:
        first = self._policy()
        second = self._policy()

        states = first["decision_states"]
        self.assertIsInstance(states, list)
        assert isinstance(states, list)
        states.append("corrupted")

        self.assertEqual(
            second["decision_states"],
            ["open", "resolved", "deferred", "blocked"],
        )

    def test_policy_is_static_and_contains_no_live_round_state(self) -> None:
        policy = self._policy()

        self.assertNotIn("round_number", policy)
        self.assertNotIn("current_round", policy)
        self.assertNotIn("decisions", policy)
        self.assertLess(
            skill_catalog.DEEP_INTERVIEW_SOFT_CHECK_ROUND,
            skill_catalog.DEEP_INTERVIEW_MAX_ROUNDS,
        )


class ContextFrontierWrapperTests(unittest.TestCase):
    def test_context_card_exposes_bounded_frontier_policy(self) -> None:
        payload = build_chat_interaction_payload(
            "use ulw-context to align the terms this project uses",
            source="discord",
        )
        state = payload["chat_response"]["state"]

        policy = state.get("frontier_policy")
        self.assertIsInstance(policy, dict)
        assert isinstance(policy, dict)
        self.assertEqual(
            policy["schema_version"],
            "decision_frontier_policy/v1",
        )
        self.assertEqual(policy["harness"], "decision-frontier")
        self.assertEqual(
            policy["max_rounds"],
            skill_catalog.DEEP_INTERVIEW_MAX_ROUNDS,
        )
        self.assertEqual(policy["round_unit"], "dependency_ready_batch")

    def test_context_card_uses_batch_appropriate_actions_and_flow(self) -> None:
        payload = build_chat_interaction_payload(
            "use ulw-context to align the terms this project uses",
            source="discord",
        )
        response = payload["chat_response"]
        state = response["state"]
        labels = [str(action["label"]) for action in response["actions"]]
        flow = [str(item) for item in state["recommended_flow"]]

        self.assertNotIn("Ask one question", labels)
        self.assertIn("run_bounded_dependency_ready_frontier", flow)
        self.assertNotIn("exhaust_dependency_ready_frontier", flow)


class ContextFrontierClarifyTests(unittest.TestCase):
    """A frontier round goes through Hermes' `clarify` tool when it is present.

    The tool takes options only in `choices` (recommended first) and its picker
    adds the typed-answer row itself; a numbered batch written into the message
    leaves the options as text the user cannot pick. Presence is the tool list
    or tool search, never a Hermes version.
    """

    def _protocol(self) -> str:
        from omh.skills.render import context_skill

        body = context_skill().content
        return " ".join(body[body.index("## Workflow Protocol"):body.index("## Runtime Evidence")].split())

    def test_the_round_goes_through_clarify_choices_when_present(self) -> None:
        protocol = self._protocol()
        self.assertIn(f"When {native_tool_presence_clause('clarify', code=True)}, the round header", protocol)
        # The tool keeps only the first four choices, and the question's own text
        # is the one field every Hermes version echoes back, so the identifier lives there.
        self.assertIn("one `questions` entry per decision whose question text opens with its identifier", protocol)
        self.assertIn("at most four `choices` with the recommendation first beside the real alternatives", protocol)
        self.assertIn("no free-input row of your own, and no options in the question text", protocol)
        self.assertIn("the round header and each decision's evidence and tradeoff stay in your message", protocol)

    def test_only_independent_decisions_share_a_call(self) -> None:
        protocol = self._protocol()
        self.assertIn("Decisions in one frontier have no prerequisite between them, so they share the call", protocol)
        self.assertIn("a dependent never joins its prerequisite", protocol)
        self.assertIn("an unanswered entry leaves its decision open", protocol)
        self.assertIn("continues in a second call in the same round", protocol)

    def test_the_numbered_batch_is_the_fallback(self) -> None:
        protocol = self._protocol()
        self.assertIn("Without `clarify`, the numbered batch in your message is the fallback.", protocol)
        self.assertLess(protocol.index("one numbered batch per round"), protocol.index("`clarify`"))

    def test_the_shared_reference_stays_host_neutral(self) -> None:
        # `references/decision-frontier.md` also ships to the Agent Skills
        # projection, which has no Hermes `clarify`; the rule lives in the
        # Hermes body only.
        from omh.skills.render import context_reference_templates

        reference = next(t for t in context_reference_templates() if t.relative_path.endswith("decision-frontier.md"))
        self.assertNotIn("clarify", reference.content)


if __name__ == "__main__":
    unittest.main()
