"""Reference instruction contracts, not measured agent review performance."""

from __future__ import annotations

import importlib
from pathlib import PurePosixPath
import unittest

from omh.skills.render import SkillReferenceTemplate


class ReviewMethodTests(unittest.TestCase):
    def templates(self):
        try:
            module = importlib.import_module("omh.skills.review_methods")
        except ModuleNotFoundError as exc:
            if exc.name != "omh.skills.review_methods":
                raise
            self.fail("missing review_methods reference producer")
        return module.review_method_reference_templates()

    def test_stable_unique_reference_contract(self):
        templates = self.templates()
        expected = [
            "references/finding-validation.md",
            "references/architecture-delta.md",
            "references/risk-selection.md",
        ]
        self.assertIsInstance(templates, list)
        self.assertEqual([item.relative_path for item in templates], expected)
        self.assertEqual(len({item.relative_path for item in templates}), len(expected))
        self.assertEqual(templates, self.templates())
        self.assertIsNot(templates, self.templates())
        for item in templates:
            with self.subTest(path=item.relative_path):
                self.assertIsInstance(item, SkillReferenceTemplate)
                self.assertEqual(item.skill_name, "code-review")
                path = PurePosixPath(item.relative_path)
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertTrue(item.content.startswith("# "))
                self.assertTrue(item.content.endswith("\n"))
                for heading in ("## Input", "## Procedure", "## Evidence threshold", "## Stop and missing context", "## Boundary"):
                    self.assertIn(heading, item.content)
                self.assertRegex(item.content, r"(?m)^1\. ")
                self.assertRegex(item.content, r"(?m)^2\. ")

    def content(self, name):
        return " ".join(next(t.content for t in self.templates() if t.relative_path == f"references/{name}.md").split())

    def require(self, name, clauses):
        text = self.content(name)
        for clause in clauses:
            with self.subTest(method=name, invariant=clause):
                self.assertIn(clause, text)

    def test_independent_validation_preserves_decision_states(self):
        self.require("finding-validation", (
            "Deduplicate by root cause", "different reader", "introducedness",
            "reachability", "callers", "guards", "tests",
            "`confirmed`", "`rejected`", "`unresolved`",
            "Exclude rejected candidates from final defects",
            "retain unresolved security or data-loss risks",
            "not confirmed defects", "blocking decisions follow `REVIEW.md`",
            "independent validation unavailable", "same reader's second pass is not independent",
            "failure to reproduce is not disproof", "absence of rebuttal is not confirmation",
        ))

    def test_architecture_requires_observed_edges_and_failure_ownership(self):
        self.require("architecture-delta", (
            "existing owner", "before/after", "import and caller edges",
            "closed cycle path", "producer and consumer",
            "one realistic failure per new integration", "partial effects",
            "retry ownership", "user-visible result", "protecting assertion",
            "Stop at guessed hops", "file move alone is not a defect",
        ))

    def test_extraction_requires_contract_compatible_net_simplification(self):
        self.require("architecture-delta", (
            "first-party authored callers", "generated or vendored copies",
            "contracts, error handling, security, and deployment differences",
            "existing reuse", "removed concepts", "added options, coupling, and tests",
            "Retract the extraction recommendation", "only moving complexity",
            "incompatible contracts", "single caller", "LOC or file size",
            "never automatic gates", "references/smell-baseline.md",
        ))

    def test_risk_selection_is_selective_but_not_size_blind(self):
        self.require("risk-selection", (
            "correctness baseline", "selected and omitted", "budget", "actual coverage",
            "auth, input, and security", "boundary, API, and type changes",
            "migrations and rollback", "retry, timeouts, and jobs", "false-green CI gates",
            "even a one-line change", "not blanket all-lens or all-model fanout",
            "external egress authority", "references/review-lenses.md",
            "omh-failure-signal-audit", "omh-refactor-plan",
        ))

    def test_boundaries_do_not_claim_execution_or_performance(self):
        for template in self.templates():
            boundary = " ".join(template.content.split("## Boundary", 1)[1].split())
            with self.subTest(path=template.relative_path):
                for clause in ("read-only", "not execution", "not measured agent performance", "no permission"):
                    self.assertIn(clause, boundary)


if __name__ == "__main__":
    unittest.main()
