"""Route hints on a delegated child's first turn (#2049)."""

from __future__ import annotations

import re
from tempfile import TemporaryDirectory
import unittest

from _local_package import load_local_package

load_local_package()
from omh.plugin_bundle.omh import awareness as awareness_module
from omh.plugin_bundle.omh.hooks.llm_hooks import pre_llm_call
from omh.plugin_bundle.omh.hooks.nudge_budget import reset_nudge_budget
from omh.plugin_bundle.omh.hooks.session_hooks import subagent_start
from omh.skills.catalog_types import ULW_ENGINE_SKILL_NAMES


class DelegatedChildRouteHintTests(unittest.TestCase):
    """A delegated child's brief is not a request to start an engine (#2049).

    The gate reads an in-process registry that `subagent_start` fills, so it
    cuts the engine hint only when the child's own `pre_llm_call` runs in the
    process that saw `subagent_start`. The same brief is routed twice: once on
    a registered child session, once on a session nobody registered. The
    second case is also what a child gets when its `subagent_start` was seen
    by another process: the registry is process memory, so that child is
    unregistered here and keeps the full hint.
    """

    def setUp(self) -> None:
        reset_nudge_budget()
        self.addCleanup(reset_nudge_budget)

    def _context(self, brief: str, *, delegated: bool) -> str:
        with TemporaryDirectory() as omh_home, TemporaryDirectory() as hermes_home:
            if delegated:
                subagent_start(
                    parent_session_id="parent-session", child_session_id="child-session",
                    omh_home=omh_home, hermes_home=hermes_home,
                )
            result = pre_llm_call(
                user_message=brief, is_first_turn=True, session_id="child-session",
                platform="subagent", omh_home=omh_home, hermes_home=hermes_home,
            )
        assert result is not None
        return str(result["context"])

    def test_registered_child_gets_no_engine_route_hint_or_context_card(self) -> None:
        for brief in ("$ulw-work fix the build", "ultrawork this refactor until the tests pass"):
            with self.subTest(brief=brief):
                context = self._context(brief, delegated=True)
                self.assertNotIn("selected=ulw-work", context)
                self.assertNotIn("lane=coding_handoff", context)
                self.assertNotIn("next_action=prepare_parallel_delivery", context)

    def test_unregistered_session_keeps_the_engine_route_hint_and_context_card(self) -> None:
        for brief in ("$ulw-work fix the build", "ultrawork this refactor until the tests pass"):
            with self.subTest(brief=brief):
                context = self._context(brief, delegated=False)
                self.assertIn("- selected=ulw-work; lane=coding_handoff", context)
                self.assertIn("next_action=prepare_parallel_delivery", context)

    def _hinted_workflows(self, brief: str, *, delegated: bool) -> set[str]:
        names = awareness_module._canonical_workflow_by_display_name()
        return {
            names.get(display, display)
            for display in re.findall(r"^- selected=([\w-]+);", self._context(brief, delegated=delegated), re.MULTILINE)
        }

    def test_registered_child_keeps_every_hint_but_the_orchestrating_engines(self) -> None:
        # The brief draws an orchestrating-engine hint (`ultrawork`) and a
        # task-engine hint that is right for it (`research`); the child loses
        # exactly the first.
        brief = "ultrawork the upload fix and research the retry library options"
        plain = self._hinted_workflows(brief, delegated=False)
        child = self._hinted_workflows(brief, delegated=True)
        self.assertTrue({"ultrawork", "research"} <= plain, plain)
        self.assertEqual(child, plain - awareness_module._ULW_ORCHESTRATING_ENGINES)
        self.assertIn("research", child)

    def test_every_engine_is_classified_orchestrating_or_task(self) -> None:
        # A new engine fails here until someone decides whether a delegated
        # child may still be pointed at it.
        self.assertLessEqual(awareness_module._ULW_ORCHESTRATING_ENGINES, set(ULW_ENGINE_SKILL_NAMES))
        self.assertEqual(
            set(ULW_ENGINE_SKILL_NAMES) - awareness_module._ULW_ORCHESTRATING_ENGINES,
            {"context", "deep-interview", "research", "ultraperf", "ultraqa"},
        )


if __name__ == "__main__":
    unittest.main()
