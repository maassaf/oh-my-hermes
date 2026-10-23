"""OMH offers no code review on the turn Hermes opens a kanban worker with.

Hermes' kanban dispatcher starts every worker, the review lane's included, with
one literal prompt, `work kanban task <id>`, and for a review-lane card it loads
its own bundled `sdlc-review` itself. That skill owns the card's transition. If OMH answered the same turn by offering `code-review`, the
worker would hold two review procedures and one card, and only one of them
knows how to move it.

`review` is a strong `code-review` trigger, so whether that happens was an open
question until it was measured. It does not: the prompt carries no review word.
These tests keep it that way on each deterministic surface that reads the turn,
the router, the message-specific route hint, the skill candidate line, and the
hook that assembles them. What they cannot see is a model choosing
`omh-code-review` from the skill index by itself; `code-review`'s own Do Not Use
line and its Hermes reference cover that case in words, and no test here claims
to have observed it.
"""

from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory

from _local_package import load_local_package

load_local_package()
from omh.plugin_bundle.omh.awareness import awareness_route_hint, awareness_route_hint_context_from_payload
from omh.plugin_bundle.omh.hooks.llm_hooks import pre_llm_call
from omh.plugin_bundle.omh.kanban_board_reader import _WORKER_PROMPT_TITLE_PREFIX
from omh.plugin_bundle.omh.skill_shortlist import skill_candidate_line, skill_candidates_for_turn
from omh.wrapper.contract import build_chat_interaction_payload

# Task ids are `t_` plus eight hex digits (`_new_task_id` in Hermes'
# `hermes_cli/kanban_db.py`). The prefix is the one the HUD reader already pins
# as the dispatcher's literal prompt, so the two cannot disagree about it.
WORKER_PROMPTS = tuple(f"{_WORKER_PROMPT_TITLE_PREFIX}{task_id}" for task_id in ("t_1a2b3c4d", "t_9f8e7d6c", "t_00000000"))
# A request that does surface code-review, so every probe below is shown able
# to see it. Without this the assertions would pass on a probe that sees nothing.
REVIEW_REQUEST = "review my pull request"


def _route(message: str) -> dict[str, object]:
    return build_chat_interaction_payload(message, source="discord")["route"]


def _named_candidates(route: dict[str, object]) -> set[str]:
    named = {str(route.get("candidate_skill") or "")}
    handoff = route.get("candidate_handoff")
    if isinstance(handoff, dict):
        named.update(str(candidate.get("skill") or "") for candidate in handoff.get("candidates") or [])
    return named


def _hook_context(message: str, session_id: str) -> str:
    with TemporaryDirectory() as omh_home, TemporaryDirectory() as hermes_home:
        result = pre_llm_call(
            user_message=message,
            is_first_turn=True,
            session_id=session_id,
            platform="cli",
            omh_home=omh_home,
            hermes_home=hermes_home,
        )
    return str((result or {}).get("context", ""))


class KanbanWorkerPromptRoutingTests(unittest.TestCase):
    def test_the_router_neither_dispatches_nor_offers_code_review(self) -> None:
        for prompt in WORKER_PROMPTS:
            with self.subTest(prompt=prompt):
                route = _route(prompt)
                self.assertNotEqual(route["action"], "dispatch")
                self.assertNotEqual(route.get("selected_skill"), "code-review")
                self.assertNotIn("code-review", _named_candidates(route))

    def test_the_route_hint_is_empty(self) -> None:
        for prompt in WORKER_PROMPTS:
            with self.subTest(prompt=prompt):
                payload = awareness_route_hint(prompt)
                self.assertEqual(payload.get("hints"), [])
                self.assertEqual(awareness_route_hint_context_from_payload(payload), "")

    def test_the_candidate_line_does_not_name_code_review(self) -> None:
        for prompt in WORKER_PROMPTS:
            with self.subTest(prompt=prompt):
                candidates = skill_candidates_for_turn(prompt, route_hint_payload=awareness_route_hint(prompt))
                self.assertNotIn("omh-code-review", skill_candidate_line(candidates))

    def test_the_hook_injects_no_route_hint_and_no_code_review(self) -> None:
        for index, prompt in enumerate(WORKER_PROMPTS):
            with self.subTest(prompt=prompt):
                context = _hook_context(prompt, f"kanban-worker-{index}")
                self.assertNotIn("[OMH Route Hint]", context)
                self.assertNotIn("code-review", context)

    def test_the_same_probes_do_see_code_review_on_a_review_request(self) -> None:
        candidates = skill_candidates_for_turn(REVIEW_REQUEST, route_hint_payload=awareness_route_hint(REVIEW_REQUEST))
        self.assertIn("omh-code-review", skill_candidate_line(candidates))
        self.assertIn("code-review", _named_candidates(_route(REVIEW_REQUEST)))
        self.assertIn("omh-code-review", _hook_context(REVIEW_REQUEST, "review-request"))


if __name__ == "__main__":
    unittest.main()
