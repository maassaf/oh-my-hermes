"""The research-department skill names the lanes its own plan emits.

The skill body tells the model which skills carry Scout, Analyst and Briefer;
`research_department_plan/v1` is the record OMH prepares with those lanes in
`roles`. The two drifted when three subset skills retired: the plan's Scout
became `research` + `web-research` while the prose said `source-finder`. The
plan is what consumers read, so the prose follows it.
"""

from __future__ import annotations

import unittest

from omh.research_department import build_research_department_plan
from omh.skills.catalog import builtin_definitions, omh_skill_display_name
from omh.skills.render import builtin_skill_templates


class ResearchDepartmentLaneTests(unittest.TestCase):
    def test_handoff_policy_maps_scout_and_analyst_to_the_plan_lanes(self) -> None:
        plan = build_research_department_plan("weekly AI agent news research")
        lanes = {role["role"]: role["primary_skills"] for role in plan["roles"]}
        policy = next(d for d in builtin_definitions() if d.name == "research-department").handoff_policy
        body = next(t.content for t in builtin_skill_templates() if t.name == "research-department")
        for role, label in (("scout", "Scout"), ("analyst", "Analyst")):
            with self.subTest(role=role):
                # The catalog names the lanes canonically; the body the model
                # loads names them by the labels `skill_view` loads.
                canonical = "/".join(f"`{skill}`" for skill in lanes[role])
                loadable = "/".join(f"`{omh_skill_display_name(skill)}`" for skill in lanes[role])
                self.assertIn(f"{label} to {canonical}", policy)
                self.assertIn(f"{label} to {loadable}", body)
                self.assertNotIn(f"{label} to {canonical}", body)


if __name__ == "__main__":
    unittest.main()
