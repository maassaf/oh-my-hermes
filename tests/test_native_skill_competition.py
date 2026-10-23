from __future__ import annotations

import json
import unittest

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.quality.native_skill_competition import (
    NATIVE_COMPETITION_CASES,
    build_native_skill_competition_report,
    native_skill_competition_errors,
)


class NativeSkillCompetitionTests(unittest.TestCase):
    def test_cases_cover_native_defaults_and_policy_overlay_exceptions(self) -> None:
        expected = {
            ("browser-operator", "native"),
            ("browser-operator", "omh"),
            ("workspace-file-operator", "native"),
            ("workspace-file-operator", "omh"),
            ("command-operator", "native"),
            ("command-operator", "omh"),
            ("live-info-operator", "native"),
            ("live-info-operator", "omh"),
            ("context", "omh"),
            ("context", "native"),
            ("jit-learn", "omh"),
        }
        self.assertEqual(
            {(case.omh_skill, case.expected_winner) for case in NATIVE_COMPETITION_CASES},
            expected,
        )
        context_case_ids = {case.case_id for case in NATIVE_COMPETITION_CASES if case.omh_skill == "context"}
        self.assertEqual(
            context_case_ids,
            {
                "context-explicit-label",
                "context-fuzzy-project-language",
                "context-multilingual-explicit",
                "context-memory-neighbor-negative",
            },
        )

    def test_frontmatter_lexical_gate_passes_every_case(self) -> None:
        report = build_native_skill_competition_report()

        self.assertEqual(report["schema_version"], "omh_native_skill_competition/v1")
        self.assertEqual(report["case_count"], 13)
        self.assertEqual(report["passed_count"], 13)
        self.assertEqual(report["failed_count"], 0)
        self.assertEqual(report["failures"], [])
        for result in report["results"]:
            with self.subTest(case=result["case_id"]):
                self.assertEqual(result["actual_winner"], result["expected_winner"])
                self.assertGreater(result["winner_score"], result["loser_score"])
                self.assertEqual(result["picker_surface"], "generated_frontmatter_name_description")
                self.assertEqual(result["evidence_kind"], "lexical_proxy")

    def test_empty_or_row_incoherent_evidence_fails_closed(self) -> None:
        empty = {
            "schema_version": "omh_native_skill_competition/v1",
            "case_count": 0,
            "passed_count": 0,
            "failed_count": 0,
            "all_passing": True,
            "failures": [],
            "results": [],
        }
        self.assertTrue(native_skill_competition_errors(empty))

        incoherent = build_native_skill_competition_report()
        incoherent["results"] = []
        self.assertTrue(native_skill_competition_errors(incoherent))

        fabricated = build_native_skill_competition_report()
        fabricated["results"][0]["actual_winner"] = "omh"
        self.assertTrue(native_skill_competition_errors(fabricated))

    def test_native_competition_cli_outputs_summary_and_json(self) -> None:
        status, stdout, stderr = run_cli(["demo", "native-competition", "--summary"], output_json=False)

        self.assertEqual(status, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertIn("cases: 13/13 passing", stdout)
        self.assertIn("failures: none", stdout)

        status, stdout, stderr = run_cli(["demo", "native-competition", "--json"], output_json=False)

        self.assertEqual(status, 0, stderr)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["schema_version"], "omh_native_skill_competition/v1")
        self.assertTrue(payload["all_passing"])


if __name__ == "__main__":
    unittest.main()
