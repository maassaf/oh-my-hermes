"""Every shipped SKILL.md body stays under the per-skill byte ceiling.

The structure-lint gate (`SKILL_CONTEXT_BUDGET`) measures the generic
`workflow_skill_from_definition()` render. Several skills ship a bespoke
render instead -- `ultrawork_skill()` splices in the tests-first section,
`deep_interview_skill()` and `loop_skill()` add their own -- and that is the
body Hermes loads every turn. `ultrawork`'s shipped body sat 483 bytes over
the ceiling while the gate stayed green, because the gate never saw it. So
this measures what `builtin_skill_templates()` ships, every skill, against
the same ceiling.
"""

from __future__ import annotations

import unittest

from omh.skills.packaging import builtin_skill_templates
from omh.skills.validation import STRUCTURE_LINT_SKILL_BODY_BYTE_CEILING


class ShippedSkillBodyCeilingTests(unittest.TestCase):
    def test_every_shipped_skill_body_stays_under_the_byte_ceiling(self) -> None:
        templates = builtin_skill_templates()
        self.assertTrue(templates, "builtin_skill_templates() shipped nothing")
        for template in templates:
            size = len(template.content.encode("utf-8"))
            with self.subTest(skill=template.name):
                self.assertLessEqual(
                    size,
                    STRUCTURE_LINT_SKILL_BODY_BYTE_CEILING,
                    f"{template.name} ships {size} bytes; move detail into a reference "
                    "before raising the ceiling",
                )


if __name__ == "__main__":
    unittest.main()
