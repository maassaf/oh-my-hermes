from __future__ import annotations

from copy import deepcopy
import json
import unittest

from omh.quality.skill_governance import build_skill_governance_policy
from omh.routing.chat import route_chat_message
from omh.routing.domain_context_eligibility import (
    DISPATCH_ROUTE,
    ELIGIBLE_UNRESOLVED_ROUTE,
    PROTECTED_ROUTE,
    classify_domain_context_eligibility,
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class DomainContextEligibilityTests(unittest.TestCase):
    def test_only_genuinely_unresolved_routes_are_eligible(self) -> None:
        for message in (
            "something feels off",
            "ビルドについて相談したい",
        ):
            with self.subTest(message=message):
                route = route_chat_message(message)
                route_before = _canonical_bytes(route)
                candidate_before = _canonical_bytes(route.get("candidate_handoff"))

                result = classify_domain_context_eligibility(route, message)

                self.assertTrue(result.eligible)
                self.assertEqual(result.reason, ELIGIBLE_UNRESOLVED_ROUTE)
                self.assertEqual(_canonical_bytes(route), route_before)
                self.assertEqual(_canonical_bytes(route.get("candidate_handoff")), candidate_before)

    def test_protected_route_reason_matrix_and_serialization_are_stable(self) -> None:
        cases = (
            ("direct_answer", "what's 2+2?", PROTECTED_ROUTE),
            ("file_lookup", "README 파일 찾아줘", PROTECTED_ROUTE),
            ("help_catalog", "what OMH workflows are available?", DISPATCH_ROUTE),
            ("maintenance", "omh update", DISPATCH_ROUTE),
            (
                "task_card",
                "Reproduce this Hermes and Friren setup on another MacBook, backup the state "
                "to private GitHub, and transfer the Discord gateway without duplicate responders.",
                DISPATCH_ROUTE,
            ),
            ("explicit_invocation", "$deep-interview onboarding feels vague", DISPATCH_ROUTE),
            (
                "static_specialist",
                "Build a discovery plan and qualification questions for a mid-market prospect "
                "considering our support platform.",
                DISPATCH_ROUTE,
            ),
            ("operator", "GitHub issue 들어온 걸 PR 만들 수 있게 정리해줘", DISPATCH_ROUTE),
            ("workflow_learning", "learn from this workflow run", DISPATCH_ROUTE),
            ("status", "Codex 작업이 어디까지 진행됐는지 알려줘", DISPATCH_ROUTE),
            ("representative_dispatch", "why is the build failing on main?", DISPATCH_ROUTE),
        )
        for name, message, expected_reason in cases:
            with self.subTest(name=name):
                route = route_chat_message(message)
                route_before = _canonical_bytes(route)
                candidate_before = _canonical_bytes(route.get("candidate_handoff"))

                result = classify_domain_context_eligibility(route, message)

                self.assertFalse(result.eligible)
                self.assertEqual(result.reason, expected_reason)
                self.assertEqual(_canonical_bytes(route), route_before)
                self.assertEqual(_canonical_bytes(route.get("candidate_handoff")), candidate_before)

    def test_governance_blocked_route_is_protected(self) -> None:
        policy = build_skill_governance_policy(
            project=[{"skills": ["code-review"]}, {"skills": ["ralplan"]}],
        )
        message = "plan a product change"
        route = route_chat_message(message, skill_policy=policy)
        route_before = _canonical_bytes(route)
        candidate_before = _canonical_bytes(route.get("candidate_handoff"))

        result = classify_domain_context_eligibility(route, message)

        self.assertEqual(route["action"], "clarify")
        self.assertEqual(route["skill_governance"]["status"], "fail_closed")
        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, PROTECTED_ROUTE)
        self.assertEqual(_canonical_bytes(route), route_before)
        self.assertEqual(_canonical_bytes(route.get("candidate_handoff")), candidate_before)

        governance_reachable = deepcopy(route)
        governance_reachable["explicit"] = False
        governance_reachable["clarification"] = "Which workflow should own this?"
        reached = classify_domain_context_eligibility(governance_reachable, message)
        self.assertFalse(reached.eligible)
        self.assertEqual(reached.reason, PROTECTED_ROUTE)

    def test_each_guard_field_independently_protects_synthetic_routes(self) -> None:
        message = "the release checklist feels unresolved"

        def eligible_route() -> dict:
            return {
                "action": "clarify",
                "selected_skill": "oh-my-hermes",
                "explicit": False,
                "clarification": "Which workflow should own this?",
                "skill_governance": None,
                "route_next_action": "",
                "recommendations": [],
                "score": 1,
            }

        baseline = classify_domain_context_eligibility(eligible_route(), message)
        self.assertTrue(baseline.eligible)
        self.assertEqual(baseline.reason, ELIGIBLE_UNRESOLVED_ROUTE)

        dispatch = classify_domain_context_eligibility(
            {**eligible_route(), "action": "dispatch"}, message
        )
        self.assertFalse(dispatch.eligible)
        self.assertEqual(dispatch.reason, DISPATCH_ROUTE)

        guard_cases = (
            ("non_unresolved_action", {"action": "answer"}),
            ("missing_action", {"action": ""}),
            ("foreign_selected_skill", {"selected_skill": "sales-development"}),
            ("explicit_true", {"explicit": True}),
            ("explicit_missing", {"explicit": None}),
            ("empty_clarification", {"clarification": "   "}),
            ("governance_not_mapping", {"skill_governance": "fail_closed"}),
            ("governance_unresolved", {"skill_governance": {"status": "fail_closed"}}),
            ("task_card_present", {"task_card": {"unit": "reproduce-setup"}}),
            ("workflow_route_plan_present", {"workflow_route_plan": {"steps": []}}),
            ("learning_candidate_card_present", {"learning_candidate_card": {"id": "c1"}}),
            ("route_next_action_set", {"route_next_action": "dispatch"}),
            (
                "protected_recommendation_action",
                {
                    "recommendations": [
                        {"next_action": "answer_directly", "score": 0, "matched": []}
                    ]
                },
            ),
            (
                "protected_leading_match",
                {
                    "recommendations": [
                        {"next_action": "recommend", "score": 1, "matched": ["metadata:status"]}
                    ]
                },
            ),
        )
        for name, override in guard_cases:
            with self.subTest(name=name):
                result = classify_domain_context_eligibility(
                    {**eligible_route(), **override}, message
                )
                self.assertFalse(result.eligible)
                self.assertEqual(result.reason, PROTECTED_ROUTE)

        for prefixed in ("", "   ", "/plan release", "$plan release", "./plan release", "@plan release"):
            with self.subTest(message=prefixed or "<blank>"):
                result = classify_domain_context_eligibility(eligible_route(), prefixed)
                self.assertFalse(result.eligible)
                self.assertEqual(result.reason, PROTECTED_ROUTE)

    def test_low_confidence_or_candidate_presence_alone_is_not_eligibility(self) -> None:
        message = "something feels off"
        unresolved = route_chat_message(message)
        self.assertEqual(unresolved["confidence"], "low")
        self.assertIn("candidate_handoff", unresolved)

        direct_answer = route_chat_message("what's 2+2?")
        self.assertEqual(direct_answer["confidence"], "low")
        self.assertIn("candidate_handoff", direct_answer)

        self.assertTrue(classify_domain_context_eligibility(unresolved, message).eligible)
        self.assertFalse(
            classify_domain_context_eligibility(direct_answer, "what's 2+2?").eligible
        )

    def test_structured_protected_route_signals_are_not_domain_eligible(self) -> None:
        for message in (
            "help",
            "show status",
            "operator actions",
            "maintenance status",
        ):
            with self.subTest(message=message):
                route = route_chat_message(message)
                route_before = _canonical_bytes(route)
                candidate_before = _canonical_bytes(route.get("candidate_handoff"))

                self.assertEqual(route["action"], "clarify")
                result = classify_domain_context_eligibility(route, message)

                self.assertFalse(result.eligible)
                self.assertEqual(result.reason, PROTECTED_ROUTE)
                self.assertEqual(_canonical_bytes(route), route_before)
                self.assertEqual(_canonical_bytes(route.get("candidate_handoff")), candidate_before)

    def test_display_prose_does_not_control_protected_route_eligibility(self) -> None:
        for message in (
            "what's 2+2?",
            "README file lookup",
            "help",
            "show status",
            "operator actions",
            "maintenance status",
        ):
            with self.subTest(message=message):
                route = deepcopy(route_chat_message(message))
                route["reason"] = "Completely revised user-facing route explanation."
                route["clarification"] = "Completely revised user-facing clarification."
                for recommendation in route.get("recommendations", []):
                    recommendation["why"] = "Revised recommendation explanation."
                    recommendation["description"] = "Revised workflow description."

                result = classify_domain_context_eligibility(route, message)

                self.assertFalse(result.eligible)
                self.assertEqual(result.reason, PROTECTED_ROUTE)

    def test_domain_specific_unresolved_routes_remain_eligible(self) -> None:
        for message in (
            "pipeline status",
            "sales status",
            "sales pipeline feels unresolved",
            "help with the sales pipeline",
            "show pipeline status",
            "sales maintenance status",
        ):
            with self.subTest(message=message):
                route = route_chat_message(message)

                self.assertEqual(route["action"], "clarify")
                result = classify_domain_context_eligibility(route, message)

                self.assertTrue(result.eligible)
                self.assertEqual(result.reason, ELIGIBLE_UNRESOLVED_ROUTE)


if __name__ == "__main__":
    unittest.main()
