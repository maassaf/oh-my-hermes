"""Procedure delivery contracts, not output-quality or live-agent compliance evidence."""
from __future__ import annotations

import unittest
from pathlib import Path

from _local_package import load_local_package

load_local_package()


class AdaptationQualityTests(unittest.TestCase):
    def test_shipped_reference_and_authoring_docs_preserve_the_shared_procedure(self):
        from omh.skills.render import agent_skill_reference_templates

        root = Path(__file__).resolve().parents[1]
        expected = next(t.content for t in agent_skill_reference_templates()
                        if t.relative_path == "references/adaptation-quality.md")
        for tree in ("skills", "agent-skills"):
            path = root / tree / "omh-verification-gate/references/adaptation-quality.md"
            with self.subTest(tree=tree):
                self.assertTrue(path.is_file(), str(path))
                self.assertEqual(path.read_text(encoding="utf-8"), expected)
        for name in ("ulw-plan", "ulw-work", "omh-verification-gate"):
            body = (root / "agent-skills" / name / "SKILL.md").read_text(encoding="utf-8")
            sibling = "../omh-verification-gate/references/adaptation-quality.md"
            self.assertIn(sibling, body)
            self.assertTrue((root / "agent-skills" / name / sibling).is_file())
        for doc in ("docs/ADDING-A-SKILL.md", "docs/AGENT-SKILLS.md"):
            with self.subTest(doc=doc):
                text = (root / doc).read_text(encoding="utf-8")
                self.assertIn("adaptation-quality.md", text)
                self.assertIn("methods-only", text)
                self.assertIn("output-quality", text)

    def test_dag_define_guidance_saves_complete_goal_separate_from_node_verify(self):
        from omh.skills.render import builtin_skill_templates

        body = next(t.content for t in builtin_skill_templates() if t.name == "dag-ulw")
        self.assertIn("with the complete run `goal` and each node's", body)
        self.assertIn("The saved goal reaches every native task; local VERIFY does not replace goal acceptance.", body)

    def test_planning_building_dag_and_verification_load_the_same_procedure(self):
        from omh.skills.render import agent_skill_templates, builtin_skill_templates

        native = {t.name: t.content for t in builtin_skill_templates()}
        portable = {t.name: t.content for t in agent_skill_templates()}
        reference = "references/adaptation-quality.md"
        for name in ("ralplan", "ultrawork", "dag-ulw", "verification-gate"):
            with self.subTest(target="native", name=name):
                self.assertIn("When adapting an existing capability", native[name])
                self.assertIn(f"skill_view(name='omh-verification-gate', file_path='{reference}')", native[name])
                self.assertIn("carry its criteria and comparisons into tasks and evidence", native[name])
                self.assertEqual(native[name].count(reference), 1)
        for name in ("ulw-plan", "ulw-work", "omh-verification-gate"):
            with self.subTest(target="portable", name=name):
                self.assertIn(f"../omh-verification-gate/{reference}", portable[name])
                self.assertIn("carry its criteria and comparisons into tasks and evidence", portable[name])
                self.assertNotIn("skill_view(", portable[name])
                self.assertEqual(portable[name].count(reference), 1)
        self.assertNotIn("omh-dag-ulw", portable)  # No runtime port invented here.

    def test_one_shared_on_demand_procedure_preserves_executable_and_quality_outcomes(self):
        from omh.skills.packaging import builtin_skill_reference_templates
        from omh.skills.render import agent_skill_reference_templates

        native = [t for t in builtin_skill_reference_templates()
                  if t.relative_path == "references/adaptation-quality.md"]
        portable = [t for t in agent_skill_reference_templates()
                    if t.relative_path == "references/adaptation-quality.md"]
        self.assertEqual([t.skill_name for t in native], ["verification-gate"])
        self.assertEqual([t.skill_name for t in portable], ["omh-verification-gate"])
        self.assertEqual(native[0].content, portable[0].content)
        text = " ".join(native[0].content.split())
        # The phases are explicit so a final smoke render cannot replace the early comparison.
        for heading in ("## Pin the requested outcome", "## Reuse before rewriting",
                        "## Compare early", "## Compare the shipped result", "## Report two verdicts"):
            self.assertIn(heading, text)
        for contract in ("baseline revision", "target revision", "operative code", "assets", "references", "defaults",
                         "reuse / adapt / omit", "user-visible loss", "user-agreed exclusions",
                         "methods-only", "license", "dependency", "security", "permissions", "budgets",
                         "same representative task", "early end-to-end", "final same-task comparison",
                         "installed/distributed", "technical verdict", "output-quality verdict",
                         "unobserved", "subjective review", "missing credential", "not permission",
                         "FR-###", "SC-###", "TASK", "VERIFY", "SCOPE"):
            with self.subTest(contract=contract):
                self.assertIn(contract, text)
        self.assertIn("Do not silently replace a requested executable adaptation with a recipe or methods-only outcome.", text)
        self.assertIn("A passing render can prove the reduced substitute works without proving retained capability or craft.", text)
        self.assertIn("Structural native_capability_quality_gate checks are not output-quality evidence.", text)
        self.assertIn("Unrelated tasks do not require upstream execution.", text)
