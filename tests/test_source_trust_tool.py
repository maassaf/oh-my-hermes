"""Contracts for the `omh_source_trust` plugin tool.

This is the surface where Hermes stops being *told* about the source-trust
ceiling and OMH actually runs it. Two things therefore have to hold, and they
pull in opposite directions:

- the ceiling must produce the same refusals through the tool as through the
  Python contract, so the tool cannot become a way around it;
- when the package backend is missing the tool must refuse to answer at all,
  rather than degrade to a permissive summary that reads like an enforced one.

The second is the reason this tool does not follow the standalone-fallback
pattern the rest of the bundle uses, so it is pinned hardest.
"""

from __future__ import annotations

import builtins
import json
import unittest

from _local_package import load_local_package

load_local_package()
from omh.plugin_bundle.omh.metadata import PROVIDED_TOOLS, TOOL_FILE_STEMS
from omh.plugin_bundle.omh.tools.source_trust_tool import (
    OMH_SOURCE_TRUST_SCHEMA,
    omh_source_trust_handler,
)
from omh.workflows.source_trust import SOURCE_TRUST_TIERS


STAMP = "2026-08-11T00:00:00Z"


def _call(**args: object) -> dict:
    return json.loads(omh_source_trust_handler(dict(args)))


def _claim(**overrides: object) -> dict:
    row = {
        "tier": "upstream_official",
        "claim_kind": "finding",
        "claim": "The runtime rejects a handoff without an owner.",
        "source_id": "source-aeb851a812b2",
        "recorded_at": STAMP,
    }
    row.update(overrides)
    return row


class RegistrationTests(unittest.TestCase):
    def test_the_tool_is_registered_under_its_file_stem(self) -> None:
        self.assertIn("omh_source_trust", PROVIDED_TOOLS)
        self.assertEqual(TOOL_FILE_STEMS["omh_source_trust"], "source_trust_tool")

    def test_the_schema_names_the_tool_and_bounds_the_claim(self) -> None:
        self.assertEqual(OMH_SOURCE_TRUST_SCHEMA["name"], "omh_source_trust")
        props = OMH_SOURCE_TRUST_SCHEMA["parameters"]["properties"]
        self.assertEqual(
            props["claims"]["items"]["properties"]["tier"]["enum"],
            list(SOURCE_TRUST_TIERS),
        )

    def test_the_description_refuses_to_read_as_verification(self) -> None:
        description = OMH_SOURCE_TRUST_SCHEMA["description"]
        self.assertIn("not observation", description)
        self.assertIn("never whether a claim is true", description.replace("reports source class, ", ""))


class CeilingThroughTheToolTests(unittest.TestCase):
    def test_accepted_claims_reach_a_summary(self) -> None:
        payload = _call(
            topic="handoff owner requirement",
            claims=[_claim(), _claim(tier="practitioner_heuristic", claim_kind="approach", claim="Set the owner first.")],
        )
        self.assertTrue(payload["ceiling_applied"])
        self.assertFalse(payload["degraded"])
        self.assertEqual(payload["accepted_count"], 2)
        self.assertEqual(payload["refused_count"], 0)
        self.assertEqual(payload["summary"]["strongest_claim_kind"], "finding")

    def test_an_overreaching_tier_is_refused_with_its_reason(self) -> None:
        payload = _call(
            topic="cache warming",
            claims=[_claim(tier="practitioner_heuristic", claim_kind="finding", claim="Warming halves p99.")],
        )
        self.assertEqual(payload["accepted_count"], 0)
        self.assertEqual(payload["refused_count"], 1)
        self.assertEqual(payload["refused_claims"][0]["index"], 0)
        self.assertIn("may not back a finding claim", payload["refused_claims"][0]["reason"])

    def test_completion_is_unreachable_through_the_tool(self) -> None:
        for tier in SOURCE_TRUST_TIERS:
            with self.subTest(tier=tier):
                payload = _call(
                    topic="migration",
                    claims=[_claim(tier=tier, claim_kind="completion", claim="It shipped and is green.")],
                )
                self.assertEqual(payload["accepted_count"], 0)
                self.assertNotEqual(payload["summary"]["strongest_claim_kind"], "completion")

    def test_a_url_source_id_is_refused(self) -> None:
        payload = _call(topic="spec", claims=[_claim(source_id="https://example.invalid/spec")])
        self.assertEqual(payload["refused_count"], 1)
        self.assertIn("opaque identifier", payload["refused_claims"][0]["reason"])

    def test_malformed_rows_are_refused_not_crashed(self) -> None:
        payload = _call(topic="spec", claims=["not a dict", {}, _claim()])
        self.assertEqual(payload["accepted_count"], 1)
        self.assertEqual(payload["refused_count"], 2)

    def test_claims_are_bounded_and_the_drop_is_reported(self) -> None:
        payload = _call(topic="spec", claims=[_claim() for _ in range(70)])
        self.assertEqual(payload["dropped_over_limit"], 6)
        self.assertEqual(payload["accepted_count"], 64)
        self.assertLessEqual(len(payload["refused_claims"]), 8)

    def test_no_claims_still_answers_and_supports_nothing(self) -> None:
        payload = _call(topic="spec")
        self.assertTrue(payload["ceiling_applied"])
        self.assertEqual(payload["summary"]["strongest_claim_kind"], "none")

    def test_the_boundary_travels_with_every_answer(self) -> None:
        payload = _call(topic="spec", claims=[_claim()])
        self.assertIn("not whether any claim is", payload["claim_boundary"])
        self.assertIn("never that OMH checked it", payload["claim_boundary"])


class FailClosedTests(unittest.TestCase):
    """Without the backend the tool must answer nothing, not answer permissively."""

    def _without_backend(self, **args: object) -> dict:
        real_import = builtins.__import__

        def fake_import(name, *rest, **kw):
            if name == "omh.workflows.source_trust":
                raise ModuleNotFoundError("No module named 'omh'", name="omh")
            return real_import(name, *rest, **kw)

        builtins.__import__ = fake_import
        try:
            return json.loads(omh_source_trust_handler(dict(args)))
        finally:
            builtins.__import__ = real_import

    def test_a_missing_backend_returns_no_summary(self) -> None:
        payload = self._without_backend(topic="spec", claims=[_claim()])
        self.assertTrue(payload["degraded"])
        self.assertFalse(payload["ceiling_applied"])
        self.assertNotIn("summary", payload)
        self.assertEqual(payload["source"], "standalone_plugin_bundle_fallback")

    def test_the_degraded_answer_says_the_ceiling_did_not_run(self) -> None:
        payload = self._without_backend(topic="spec", claims=[_claim()])
        self.assertIn("did not run", payload["reason"])
        self.assertIn("did not run", payload["claim_boundary"])

    def test_the_degraded_answer_never_reports_acceptance(self) -> None:
        """The negative case: a permissive fallback would be worse than no tool."""
        payload = self._without_backend(
            topic="spec",
            claims=[_claim(tier="practitioner_heuristic", claim_kind="completion")],
        )
        for field in ("accepted_count", "summary", "strongest_claim_kind"):
            self.assertNotIn(field, payload)


if __name__ == "__main__":
    unittest.main()
