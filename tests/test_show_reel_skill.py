"""Public show-reel contract; checks data, not a renderer execution claim."""
from __future__ import annotations

import unittest
import tomllib
from pathlib import Path

from _local_package import load_local_package

load_local_package()
from omh.skills.catalog import builtin_definitions, omh_skill_display_name


class ShowReelSkillTests(unittest.TestCase):
    def test_reusable_toolkit_and_independent_artistic_evidence_are_required(self):
        from omh.skills.skill_record import project, skill_record
        from specialist_procedure_contract_support import DOMAIN_REVIEW_CONTRACTS
        record = skill_record("show-reel")
        fields = {c.check_id: set(c.required_result_fields) for c in record.definition.procedure_checks}
        required = {"reel_adapter_check": {"toolkit_provenance", "source_bound_run"}, "reel_preview_check": {"strongest_scene", "anti_slide_review", "before_after_and_boundaries"}, "reel_qa_check": {"technical_verdict", "artistic_verdict", "playback_verdict", "listening_verdict"}}
        for check, additions in required.items():
            self.assertLessEqual(additions, fields[check])
            self.assertEqual(fields[check], DOMAIN_REVIEW_CONTRACTS["show-reel"]["checks"][check])
        for target in ("hermes", "agent-skills"):
            files = project(record, target)
            body = files["omh-show-reel/SKILL.md"]
            self.assertIn("toolkit/README.md", body)
            recipe = files["omh-show-reel/references/renderer-adapter.md"]
            for relative in ("toolkit/scripts/new_project.py", "toolkit/scripts/render.mjs", "toolkit/scripts/synth_music.py", "toolkit/scripts/mix.py", "toolkit/scripts/qa_check.py", "toolkit/LICENSE.upstream"):
                self.assertIn("omh-show-reel/"+relative, files)
                self.assertIn(relative, recipe)
            self.assertIn("headline/card/rail", files["omh-show-reel/references/art-direction.md"])
            self.assertIn("not_observed", files["omh-show-reel/references/render-qa-delivery.md"])

    def test_public_docs_distinguish_optional_derived_code_from_core_execution(self):
        root = Path(__file__).resolve().parents[1]
        guide = root / "docs/SHOW-REEL-TOOLKIT.md"
        self.assertTrue(guide.is_file(), "missing maintained toolkit guide")
        text = guide.read_text(encoding="utf-8")
        for marker in ("toolkit/README.md", "LICENSE.upstream", "macOS", "HOLD", "not_observed", "built wheel"):
            self.assertIn(marker, text)
        self.assertIn("SHOW-REEL-TOOLKIT.md", (root / "README.md").read_text(encoding="utf-8"))
        self.assertIn("modified MIT", (root / "docs/SKILL-SOURCES.md").read_text(encoding="utf-8"))

    def test_original_end_to_end_method_has_observed_delivery_gates(self):
        definitions = {d.name: d for d in builtin_definitions()}
        self.assertIn("show-reel", definitions)
        d = definitions["show-reel"]
        self.assertEqual(omh_skill_display_name(d.name), "omh-show-reel")
        self.assertTrue(d.progressive_disclosure)
        self.assertEqual(d.hermes_role, "operator")
        self.assertEqual(len(d.procedure_steps), 10)
        self.assertEqual(d.expected_outputs[-1], "show_reel_delivery_manifest/v1")
        method = " ".join((*d.opening_steps, *d.quality_bar, *d.safety_rules, *d.final_checklist, *(s.instruction for s in d.procedure_steps)))
        for marker in ("at least 3", "keep/fix/rebuild", "weakest", "delivery-size", "codec", "color", "filesize", "concept", "source package", "HOLD"):
            self.assertIn(marker, method)
        project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["dependencies"], [])


if __name__ == "__main__":
    unittest.main()
