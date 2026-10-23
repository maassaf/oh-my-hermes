"""Routing accuracy ratchet.

These assertions are floors, not equalities. Improving routing should make them
pass with room to spare; regressing it should fail them. That is the opposite
of the rest of the routing suite, which pins current behaviour and therefore
stayed green while ordinary requests misrouted.

Baseline measured 2026-07-27, after the skill-boundary, trigger-audit,
language-policy, and candidate-handoff work:

    en 5/6 resolved (100% covered)   ko 6/6 resolved (100% covered)
    ja 1/6            zh 1/6          es 0/6            hi 0/6
    overall 13/36 resolved, 14/36 covered, 0 high-confidence misroutes

Raise the floors when the numbers improve. Never lower them to make a change
pass.
"""

from __future__ import annotations

import unittest

from omh.quality.routing_accuracy import (
    ROUTING_ACCURACY_CASES,
    ROUTING_ACCURACY_SCHEMA_VERSION,
    build_routing_accuracy_demo,
    format_routing_accuracy_summary,
)


# Languages whose trigger tables actually carry entries. These must stay high.
TRIGGER_BACKED_RESOLVED_FLOOR = {"en": 5, "ko": 6}

# Languages with no meaningful trigger table. The floor is 0 on purpose: the fix
# for these is model selection, not more tokens, so demanding a deterministic
# score here would push work in the direction this repo has decided against.
# They are listed so nobody can quietly claim coverage that does not exist.
KNOWN_COVERAGE_GAP_LANGUAGES = ("es", "hi", "ja", "zh")

OVERALL_RESOLVED_FLOOR = 13
OVERALL_COVERED_FLOOR = 14
HIGH_CONFIDENCE_MISROUTE_CEILING = 0


class RoutingAccuracyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = build_routing_accuracy_demo(source="generic")
        self.summary = self.payload["summary"]
        self.languages = {bucket["language"]: bucket for bucket in self.payload["languages"]}

    def test_the_corpus_runs_one_intent_set_across_every_language(self) -> None:
        self.assertEqual(self.payload["schema_version"], ROUTING_ACCURACY_SCHEMA_VERSION)
        self.assertEqual(self.summary["case_count"], len(ROUTING_ACCURACY_CASES))

        intents_by_language: dict[str, set[str]] = {}
        for case in ROUTING_ACCURACY_CASES:
            intents_by_language.setdefault(case.language, set()).add(case.intent)
        self.assertEqual(len(set(map(frozenset, intents_by_language.values()))), 1)

    def test_high_confidence_misroutes_stay_at_zero(self) -> None:
        # The worst failure mode: dispatched confidently to the wrong workflow,
        # so neither a picker nor a candidate handoff rescues the user.
        self.assertLessEqual(
            self.summary["high_confidence_misroutes"],
            HIGH_CONFIDENCE_MISROUTE_CEILING,
        )

    def test_trigger_backed_languages_hold_their_floor(self) -> None:
        for language, floor in TRIGGER_BACKED_RESOLVED_FLOOR.items():
            with self.subTest(language=language):
                self.assertGreaterEqual(self.languages[language]["resolved"], floor)

    def test_overall_accuracy_holds_its_floor(self) -> None:
        self.assertGreaterEqual(self.summary["resolved"], OVERALL_RESOLVED_FLOOR)
        self.assertGreaterEqual(
            self.summary["resolved"] + self.summary["handed_off"],
            OVERALL_COVERED_FLOOR,
        )

    def test_the_known_coverage_gap_is_reported_not_hidden(self) -> None:
        for language in KNOWN_COVERAGE_GAP_LANGUAGES:
            with self.subTest(language=language):
                self.assertIn(language, self.languages)
                self.assertIn("resolved_percent", self.languages[language])

    def test_latin_script_is_not_the_same_as_english_coverage(self) -> None:
        # Spanish is Latin script, so input_language reports it as trigger-backed,
        # but the trigger tables are English. This case exists so that conflation
        # cannot be made silently.
        spanish = [row for row in self.payload["cases"] if row["language"] == "es"]

        self.assertTrue(spanish)
        for row in spanish:
            with self.subTest(case=row["id"]):
                self.assertEqual(row["script"], "latin")
        self.assertLess(self.languages["es"]["resolved"], self.languages["en"]["resolved"])

    def test_the_summary_reads_as_a_report(self) -> None:
        rendered = format_routing_accuracy_summary(self.payload)

        self.assertIn("OMH routing accuracy", rendered)
        self.assertIn("High-confidence misroutes:", rendered)
        self.assertIn("Per language:", rendered)
        self.assertIn("Boundary:", rendered)

    def test_a_handed_off_outcome_never_claims_a_decision(self) -> None:
        self.assertIn("never that Hermes picked it", str(self.payload["claim_boundary"]))


if __name__ == "__main__":
    unittest.main()
