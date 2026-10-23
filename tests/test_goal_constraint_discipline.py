from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()
from omh.goal_loop import (
    LOOP_CONSTRAINT_CLASSES,
    LOOP_CONSTRAINT_NEXT_ACTION_RELATIONSHIP,
)
from omh.skills.packaging import builtin_skill_reference_templates, builtin_skill_templates


class GoalConstraintDisciplineDoctrineTests(unittest.TestCase):
    def _reference(self):
        return next(
            template
            for template in builtin_skill_reference_templates()
            if template.relative_path == "references/goal-constraint-discipline.md"
        )

    def _skills(self):
        return {skill.name: skill for skill in builtin_skill_templates()}

    def test_reference_exists_and_stays_compact(self) -> None:
        reference = self._reference()
        self.assertEqual(reference.skill_name, "loop")
        # Progressive-disclosure budget: the doctrine must stay a compact,
        # on-demand load, well under the generic 24,500 per-reference ceiling.
        self.assertLess(len(reference.content.encode("utf-8")), 8_000)

    def test_reference_carries_omh_vocabulary(self) -> None:
        reference = self._reference()
        for anchor in (
            "prepared_not_observed",
            "wait_reason",
            "verification_gap",
            "permission envelope",
            "loop_constraint_assessment/v1",
        ):
            self.assertIn(anchor, reference.content)

    def test_reference_carries_no_url(self) -> None:
        reference = self._reference()
        skills = self._skills()
        self.assertNotIn("http", reference.content)
        # The always-loaded body needs its own gate: it is where a
        # well-meaning later edit is most likely to add a "see also" link.
        self.assertNotIn("http", skills["loop"].content)

    def test_reference_names_every_constraint_class_in_the_tuple(self) -> None:
        reference = self._reference()
        for name in LOOP_CONSTRAINT_CLASSES:
            self.assertIn(name, reference.content)

    def test_reference_carries_all_five_focusing_steps(self) -> None:
        reference = self._reference()
        for step in ("**Identify**", "**Exploit**", "**Subordinate**", "**Elevate**", "**Repeat**"):
            self.assertIn(step, reference.content)

    def test_reference_carries_the_translation_table_and_every_anti_pattern(self) -> None:
        reference = self._reference()
        self.assertIn("operating expense", reference.content)
        for anti_pattern in (
            "Robot-line fallacy",
            "Inventory blindness",
            "Balanced-line fallacy",
            "Premature elevation",
            "Constraint inertia",
        ):
            self.assertIn(anti_pattern, reference.content)

    def test_reference_attributes_the_source(self) -> None:
        reference = self._reference()
        self.assertIn("Goldratt", reference.content)
        self.assertIn("Theory of Constraints", reference.content)

    def test_pointers_are_targeted(self) -> None:
        skills = self._skills()
        self.assertIn("## Constraint Discipline", skills["loop"].content)
        self.assertIn("references/goal-constraint-discipline.md", skills["loop"].content)
        self.assertIn("ulw-loop/references/goal-constraint-discipline.md", skills["ultrawork"].content)
        # A named third skill proves the splice is targeted, not universal.
        self.assertNotIn("## Constraint Discipline", skills["ralplan"].content)
        self.assertNotIn("goal-constraint-discipline.md", skills["ralplan"].content)

    def test_loop_body_keeps_toc_nouns_out(self) -> None:
        # Scope: the loop body ONLY. Never assert this on ultrawork, whose
        # live "high throughput" routing trigger legitimately renders into
        # its body twice.
        loop_body = self._skills()["loop"].content
        self.assertNotIn("throughput", loop_body)
        self.assertNotIn("drum", loop_body)

    def test_the_body_states_the_next_action_precedence(self) -> None:
        loop_body = self._skills()["loop"].content
        self.assertIn(LOOP_CONSTRAINT_NEXT_ACTION_RELATIONSHIP, loop_body)
        self.assertIn("names the recorded step", loop_body)


if __name__ == "__main__":
    unittest.main()
