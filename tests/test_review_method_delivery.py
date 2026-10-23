"""Review methods reach both distributions; shape checks do not prove semantics."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from omh.installer import install_skill_pack
from omh.paths import resolve_paths
from omh.skills.catalog import omh_skill_install_path
from omh.skills.packaging import builtin_skill_reference_templates, builtin_skill_templates
from omh.skills.render import agent_skill_reference_templates, agent_skill_templates
from omh.skills.review_methods import review_method_reference_templates
from omh.skills.skill_record import REFERENCE_PRODUCERS, skill_record


class ReviewMethodDeliveryTests(unittest.TestCase):
    def test_registered_once_and_delivered_with_exact_portable_paths(self):
        self.assertEqual(REFERENCE_PRODUCERS.count(review_method_reference_templates), 1)
        native = {(t.skill_name, t.relative_path): t.content for t in builtin_skill_reference_templates()}
        portable = {(t.skill_name, t.relative_path): t.content for t in agent_skill_reference_templates()}
        recorded = {t.relative_path: t.content for t in skill_record("code-review").references()}
        for template in review_method_reference_templates():
            key = ("code-review", template.relative_path)
            self.assertEqual(native[key], template.content)
            self.assertEqual(recorded[template.relative_path], template.content)
            self.assertEqual(portable[("omh-code-review", template.relative_path)], template.content)

    def test_both_bodies_expose_contract_and_on_demand_methods(self):
        native = {t.name: t.content for t in builtin_skill_templates()}["code-review"]
        portable = {t.name: t.content for t in agent_skill_templates()}["omh-code-review"]
        for body in (native, portable):
            for marker in ("finding-validation.md", "architecture-delta.md", "risk-selection.md",
                           "verification_gaps", "spec_axis", "could_not_assess"):
                self.assertIn(marker, body)
            self.assertIn("independent validation unavailable", body)
            self.assertIn("selected and omitted", body)

    def test_checklists_keep_findings_first_and_verification_gaps_unranked(self):
        root = Path(__file__).resolve().parents[1]
        bodies = {
            "native producer": {t.name: t.content for t in builtin_skill_templates()}["code-review"],
            "portable producer": {t.name: t.content for t in agent_skill_templates()}["omh-code-review"],
            "native file": (root / "skills/omh-code-review/SKILL.md").read_text(encoding="utf-8"),
            "portable file": (root / "agent-skills/omh-code-review/SKILL.md").read_text(encoding="utf-8"),
        }
        for target, body in bodies.items():
            with self.subTest(target=target):
                _, heading, after = body.partition("## Completion Checklist\n")
                self.assertTrue(heading, target)
                checklist = after.split("\n## ", 1)[0]
                first_item = next(line for line in checklist.splitlines() if line.startswith("- "))
                self.assertTrue(first_item.startswith("- Findings come first"), first_item)
                self.assertIn("rank ordinary findings by severity", first_item)
                self.assertIn("keep gaps unranked in `verification_gaps`", first_item)

    def test_lens_and_smell_link_methods_without_ranking_gaps(self):
        refs = {t.relative_path: t.content for t in builtin_skill_reference_templates() if t.skill_name == "code-review"}
        lenses = refs["references/review-lenses.md"]
        for marker in ("verification_gaps", "not in `findings`", "architecture-delta.md", "risk-selection.md"):
            self.assertIn(marker, lenses)
        self.assertIn("do not assign", lenses)
        self.assertIn("architecture-delta.md", refs["references/smell-baseline.md"])
        self.assertIn("extraction proof rubric", refs["references/smell-baseline.md"])

    def test_builtin_and_local_source_install_include_methods(self):
        expected = review_method_reference_templates()
        source_dir = Path(__file__).resolve().parents[1]
        for source in ("builtin", "local"):
            with self.subTest(source=source), TemporaryDirectory() as tmp:
                paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")
                kwargs = {"source": source, "profile": "full"}
                if source == "local":
                    kwargs["source_dir"] = source_dir
                install_skill_pack(paths, **kwargs)
                root = paths.skills_dir / omh_skill_install_path("code-review")
                for template in expected:
                    self.assertEqual((root / template.relative_path).read_text(encoding="utf-8"), template.content)


if __name__ == "__main__":
    unittest.main()
