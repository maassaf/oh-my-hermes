"""Contracts for `source_trust_claim/v1` and `source_trust_summary/v1`.

The axis exists so knowledge OMH never observed can be carried without being
mistaken for something OMH did observe. These group by the guarantee each test
protects:

- The ceiling: a tier may back only the claim kinds the table gives it, and the
  refusal is a rejection rather than a silent downgrade.
- The floor under every tier: no source, however official, backs a completion
  claim -- that belongs to the observation axis alone.
- Attribution: a tier that names a source must have one, and the tier that means
  "nobody in particular" must not.
- The summary: it cannot be raised by malformed input and cannot report a
  completion.

Each positive case ships with the negative case that proves the guard is doing
the work, per the repo's guard-pattern rule.
"""

from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()
from omh.workflows.source_trust import (
    CLAIM_KIND_NONE,
    CLAIM_KINDS,
    SOURCE_TRUST_CLAIM_BOUNDARY,
    SOURCE_TRUST_CLAIM_SCHEMA_VERSION,
    SOURCE_TRUST_CLAIM_KEYS,
    SOURCE_TRUST_SUMMARY_SCHEMA_VERSION,
    SOURCE_TRUST_TIER_FALLBACK,
    SOURCE_TRUST_TIERS,
    TRUST_CLAIM_CEILING,
    SourceTrustError,
    build_source_trust_claim,
    claim_from_source_candidate,
    claim_kinds_for_tier,
    completion_is_never_source_backed,
    normalize_source_trust_tier,
    source_trust_claim_errors,
    summarize_source_trust,
    tier_may_claim,
    validate_source_trust_summary,
)


STAMP = "2026-08-11T00:00:00Z"


def _claim(**overrides: object) -> dict[str, object]:
    record = build_source_trust_claim(
        tier="upstream_official",
        claim_kind="finding",
        claim="The runtime rejects a handoff without an owner.",
        recorded_at=STAMP,
        source_ref="upstream-runtime-spec",
    )
    record.update(overrides)
    return record


class CeilingTests(unittest.TestCase):
    def test_practitioner_heuristic_backs_approach(self) -> None:
        record = build_source_trust_claim(
            tier="practitioner_heuristic",
            claim_kind="approach",
            claim="Warm the cache before the first batch or the first run skews the timing.",
            recorded_at=STAMP,
            source_ref="practitioner-note-cache-warm",
        )
        self.assertEqual(record["tier"], "practitioner_heuristic")
        self.assertEqual(record["claim_kind"], "approach")
        self.assertEqual(source_trust_claim_errors(record), [])

    def test_practitioner_heuristic_is_refused_as_a_finding(self) -> None:
        with self.assertRaises(SourceTrustError) as raised:
            build_source_trust_claim(
                tier="practitioner_heuristic",
                claim_kind="finding",
                claim="Warming the cache halves p99.",
                recorded_at=STAMP,
                source_ref="practitioner-note-cache-warm",
            )
        self.assertIn("may not back a finding claim", str(raised.exception))

    def test_refusal_is_not_a_silent_downgrade(self) -> None:
        """A rejected claim must not come back stored one rung lower."""
        with self.assertRaises(SourceTrustError):
            build_source_trust_claim(
                tier="practitioner_heuristic",
                claim_kind="finding",
                claim="Warming the cache halves p99.",
                recorded_at=STAMP,
                source_ref="practitioner-note-cache-warm",
            )

    def test_unattributed_backs_nothing(self) -> None:
        self.assertEqual(claim_kinds_for_tier("unattributed"), ())
        for kind in CLAIM_KINDS:
            with self.subTest(kind=kind):
                with self.assertRaises(SourceTrustError):
                    build_source_trust_claim(
                        tier="unattributed",
                        claim_kind=kind,
                        claim="Somebody said the scheduler retries twice.",
                        recorded_at=STAMP,
                    )

    def test_every_tier_has_an_explicit_ceiling(self) -> None:
        self.assertEqual(sorted(TRUST_CLAIM_CEILING), sorted(SOURCE_TRUST_TIERS))
        for tier, kinds in TRUST_CLAIM_CEILING.items():
            with self.subTest(tier=tier):
                self.assertTrue(set(kinds) <= set(CLAIM_KINDS), tier)


class CompletionFloorTests(unittest.TestCase):
    def test_no_tier_backs_a_completion_claim(self) -> None:
        self.assertTrue(completion_is_never_source_backed())
        for tier in SOURCE_TRUST_TIERS:
            with self.subTest(tier=tier):
                self.assertFalse(tier_may_claim(tier, "completion"))

    def test_upstream_official_is_also_refused_a_completion(self) -> None:
        with self.assertRaises(SourceTrustError) as raised:
            build_source_trust_claim(
                tier="upstream_official",
                claim_kind="completion",
                claim="The migration shipped and the suite is green.",
                recorded_at=STAMP,
                source_ref="upstream-release-notes",
            )
        self.assertIn("may not back a completion claim", str(raised.exception))


class AttributionTests(unittest.TestCase):
    def test_named_tier_requires_a_source(self) -> None:
        with self.assertRaises(SourceTrustError) as raised:
            build_source_trust_claim(
                tier="upstream_official",
                claim_kind="finding",
                claim="The runtime rejects a handoff without an owner.",
                recorded_at=STAMP,
            )
        self.assertIn("must name the source", str(raised.exception))

    def test_unattributed_must_not_name_a_source(self) -> None:
        """Only reachable on read: `build` refuses `unattributed` at the ceiling first.

        The ordering is deliberate -- a tier that backs nothing is refused for
        that reason before anything about its attribution is considered -- so the
        attribution rule is exercised against a hand-forged record.
        """
        errors = source_trust_claim_errors(_claim(tier="unattributed", source_ref="somewhere"))
        self.assertTrue(any("must not name a source" in error for error in errors), errors)

    def test_source_ref_must_not_be_navigable(self) -> None:
        with self.assertRaises(SourceTrustError):
            build_source_trust_claim(
                tier="upstream_official",
                claim_kind="finding",
                claim="The runtime rejects a handoff without an owner.",
                recorded_at=STAMP,
                source_ref="https://example.invalid/spec",
            )


class NormalizeTests(unittest.TestCase):
    def test_known_tiers_survive_normalization(self) -> None:
        for tier in SOURCE_TRUST_TIERS:
            with self.subTest(tier=tier):
                self.assertEqual(normalize_source_trust_tier(tier), tier)

    def test_unknown_tier_falls_closed(self) -> None:
        for value in ("", None, "trusted", "official-ish", "PRACTITIONER HEURISTICS"):
            with self.subTest(value=value):
                self.assertEqual(normalize_source_trust_tier(value), SOURCE_TRUST_TIER_FALLBACK)

    def test_fallback_tier_backs_nothing(self) -> None:
        """Falling closed is only safe because the fallback has no authority."""
        self.assertEqual(claim_kinds_for_tier(SOURCE_TRUST_TIER_FALLBACK), ())


class ClaimValidationTests(unittest.TestCase):
    def test_built_claim_validates(self) -> None:
        self.assertEqual(source_trust_claim_errors(_claim()), [])
        self.assertEqual(sorted(_claim()), sorted(SOURCE_TRUST_CLAIM_KEYS))

    def test_missing_and_unexpected_keys_are_both_reported(self) -> None:
        record = _claim()
        record.pop("recorded_at")
        record["notes"] = "extra"
        errors = source_trust_claim_errors(record)
        self.assertTrue(any("missing keys" in error for error in errors), errors)
        self.assertTrue(any("unsupported keys" in error for error in errors), errors)

    def test_schema_version_is_pinned(self) -> None:
        errors = source_trust_claim_errors(_claim(schema_version="source_trust_claim/v2"))
        self.assertTrue(any(SOURCE_TRUST_CLAIM_SCHEMA_VERSION in error for error in errors), errors)

    def test_hand_forged_ceiling_violation_is_caught_on_read(self) -> None:
        """The guard cannot be bypassed by writing the record by hand."""
        errors = source_trust_claim_errors(
            _claim(tier="practitioner_heuristic", source_ref="practitioner-note")
        )
        self.assertTrue(any("may not back a finding claim" in error for error in errors), errors)

    def test_non_mapping_is_rejected(self) -> None:
        self.assertEqual(source_trust_claim_errors(["not", "a", "record"]), ["source trust claim must be an object"])


class SummaryTests(unittest.TestCase):
    def test_summary_reports_the_strongest_accepted_claim(self) -> None:
        summary = summarize_source_trust(
            topic="handoff owner requirement",
            claims=[
                build_source_trust_claim(
                    tier="practitioner_heuristic",
                    claim_kind="approach",
                    claim="Set the owner before preparing the handoff.",
                    recorded_at=STAMP,
                    source_ref="practitioner-note-owner",
                ),
                _claim(),
            ],
        )
        self.assertEqual(summary["strongest_claim_kind"], "finding")
        self.assertEqual(summary["accepted_count"], 2)
        self.assertEqual(summary["rejected_count"], 0)
        self.assertEqual(summary["tiers_present"], ["upstream_official", "practitioner_heuristic"])
        self.assertEqual(summary["claim_boundary"], SOURCE_TRUST_CLAIM_BOUNDARY)
        self.assertEqual(validate_source_trust_summary(summary), [])

    def test_empty_summary_backs_nothing(self) -> None:
        summary = summarize_source_trust(topic="handoff owner requirement")
        self.assertEqual(summary["strongest_claim_kind"], CLAIM_KIND_NONE)
        self.assertEqual(summary["tiers_present"], [])
        self.assertEqual(validate_source_trust_summary(summary), [])

    def test_malformed_claims_cannot_raise_the_summary(self) -> None:
        summary = summarize_source_trust(
            topic="handoff owner requirement",
            claims=[
                {"tier": "upstream_official", "claim_kind": "finding"},
                _claim(tier="practitioner_heuristic", source_ref="practitioner-note"),
                build_source_trust_claim(
                    tier="practitioner_heuristic",
                    claim_kind="approach",
                    claim="Set the owner before preparing the handoff.",
                    recorded_at=STAMP,
                    source_ref="practitioner-note-owner",
                ),
            ],
        )
        self.assertEqual(summary["accepted_count"], 1)
        self.assertEqual(summary["rejected_count"], 2)
        self.assertEqual(summary["strongest_claim_kind"], "approach")
        self.assertEqual([row["index"] for row in summary["rejected_claims"]], [0, 1])
        self.assertEqual(validate_source_trust_summary(summary), [])

    def test_summary_schema_version_is_pinned(self) -> None:
        summary = summarize_source_trust(topic="handoff owner requirement")
        self.assertEqual(summary["schema_version"], SOURCE_TRUST_SUMMARY_SCHEMA_VERSION)

    def test_a_forged_completion_summary_is_rejected(self) -> None:
        summary = summarize_source_trust(topic="handoff owner requirement")
        summary["strongest_claim_kind"] = "completion"
        errors = validate_source_trust_summary(summary)
        self.assertTrue(any("never be completion" in error for error in errors), errors)

    def test_a_stripped_boundary_is_rejected(self) -> None:
        summary = summarize_source_trust(topic="handoff owner requirement")
        summary["claim_boundary"] = "checked and verified"
        errors = validate_source_trust_summary(summary)
        self.assertTrue(any("frozen boundary sentence" in error for error in errors), errors)


class ProducerTests(unittest.TestCase):
    """Binding a claim to a source candidate — the link that makes the ceiling reachable.

    Before this, `build_source_trust_claim` could only refuse claims someone else
    minted, and nothing in OMH minted one: `source_ref` refuses URL-shaped values
    and a URL is the only form a source arrives in. `source_finder` already mints
    the opaque id that fits, so these tests pin the binding and — most importantly
    — that the raw `uri` can never travel into a claim.
    """

    @staticmethod
    def _candidate(uri: str = "https://example.invalid/spec") -> dict[str, object]:
        from omh.workflows.source_finder import build_source_candidate

        return build_source_candidate(
            title="Runtime handoff spec",
            kind="docs_spec",
            uri=uri,
            summary="Upstream spec for handoff owners.",
        )

    def test_candidate_id_becomes_the_source_reference(self) -> None:
        candidate = self._candidate()
        record = claim_from_source_candidate(
            candidate=candidate,
            tier="upstream_official",
            claim_kind="finding",
            claim="The runtime rejects a handoff without an owner.",
            recorded_at=STAMP,
        )
        self.assertEqual(record["source_ref"], candidate["candidate_id"])
        self.assertEqual(source_trust_claim_errors(record), [])

    def test_the_raw_uri_never_reaches_the_claim(self) -> None:
        """The whole point: a candidate carries `uri` and `candidate_id` side by side."""
        candidate = self._candidate(uri="https://example.invalid/very-distinctive-path")
        self.assertEqual(candidate["uri"], "https://example.invalid/very-distinctive-path")
        record = claim_from_source_candidate(
            candidate=candidate,
            tier="practitioner_heuristic",
            claim_kind="approach",
            claim="Set the owner before preparing the handoff.",
            recorded_at=STAMP,
        )
        serialized = " ".join(str(value) for value in record.values())
        self.assertNotIn("example.invalid", serialized)
        self.assertNotIn("very-distinctive-path", serialized)
        self.assertNotIn("http", serialized)

    def test_the_ceiling_still_applies_through_the_producer(self) -> None:
        """The adapter must not become a way around the refusal."""
        with self.assertRaises(SourceTrustError) as raised:
            claim_from_source_candidate(
                candidate=self._candidate(),
                tier="practitioner_heuristic",
                claim_kind="finding",
                claim="Warming the cache halves p99.",
                recorded_at=STAMP,
            )
        self.assertIn("may not back a finding claim", str(raised.exception))

    def test_completion_is_still_unreachable_through_the_producer(self) -> None:
        for tier in SOURCE_TRUST_TIERS:
            with self.subTest(tier=tier):
                with self.assertRaises(SourceTrustError):
                    claim_from_source_candidate(
                        candidate=self._candidate(),
                        tier=tier,
                        claim_kind="completion",
                        claim="The migration shipped and the suite is green.",
                        recorded_at=STAMP,
                    )

    def test_unattributed_cannot_cite_a_candidate(self) -> None:
        with self.assertRaises(SourceTrustError) as raised:
            claim_from_source_candidate(
                candidate=self._candidate(),
                tier="unattributed",
                claim_kind="approach",
                claim="Somebody said the scheduler retries twice.",
                recorded_at=STAMP,
            )
        self.assertIn("the candidate is the attribution", str(raised.exception))

    def test_a_foreign_record_is_refused(self) -> None:
        for bad in ({"schema_version": "source_candidate/v2", "candidate_id": "source-abc"},
                    {"schema_version": "source_trust_claim/v1", "candidate_id": "source-abc"},
                    ["not", "a", "candidate"]):
            with self.subTest(bad=bad):
                with self.assertRaises(SourceTrustError):
                    claim_from_source_candidate(
                        candidate=bad,  # type: ignore[arg-type]
                        tier="upstream_official",
                        claim_kind="finding",
                        claim="The runtime rejects a handoff without an owner.",
                        recorded_at=STAMP,
                    )

    def test_a_candidate_without_an_id_is_refused(self) -> None:
        candidate = dict(self._candidate())
        candidate["candidate_id"] = ""
        with self.assertRaises(SourceTrustError) as raised:
            claim_from_source_candidate(
                candidate=candidate,
                tier="upstream_official",
                claim_kind="finding",
                claim="The runtime rejects a handoff without an owner.",
                recorded_at=STAMP,
            )
        self.assertIn("candidate_id", str(raised.exception))

    def test_produced_claims_flow_into_a_summary(self) -> None:
        """End to end: candidate -> claim -> summary, with the ceiling holding."""
        candidate = self._candidate()
        claims = [
            claim_from_source_candidate(
                candidate=candidate,
                tier="upstream_official",
                claim_kind="finding",
                claim="The runtime rejects a handoff without an owner.",
                recorded_at=STAMP,
            ),
            claim_from_source_candidate(
                candidate=candidate,
                tier="practitioner_heuristic",
                claim_kind="approach",
                claim="Set the owner before preparing the handoff.",
                recorded_at=STAMP,
            ),
        ]
        summary = summarize_source_trust(topic="handoff owner requirement", claims=claims)
        self.assertEqual(summary["accepted_count"], 2)
        self.assertEqual(summary["rejected_count"], 0)
        self.assertEqual(summary["strongest_claim_kind"], "finding")
        self.assertNotEqual(summary["strongest_claim_kind"], "completion")
        self.assertEqual(validate_source_trust_summary(summary), [])


class CatalogWiringTests(unittest.TestCase):
    """The research lane's prose must name the same tiers the module enforces.

    Without this the two drift: the catalog tells Hermes one vocabulary while
    `TRUST_CLAIM_CEILING` enforces another, and the guidance quietly stops
    describing the guard. Derived from `SOURCE_TRUST_TIERS` rather than
    hardcoded, so renaming a tier fails here instead of going unnoticed.
    """

    @staticmethod
    def _prose(definition: object) -> str:
        parts: list[str] = []
        for field in ("safety_rules", "quality_bar", "expected_outputs"):
            parts.extend(getattr(definition, field, ()) or ())
        return " ".join(parts).lower()

    def setUp(self) -> None:
        from omh.skills.catalog import builtin_definitions

        self.definitions = {definition.name: definition for definition in builtin_definitions()}

    def test_research_names_every_source_trust_tier(self) -> None:
        prose = self._prose(self.definitions["research"])
        for tier in SOURCE_TRUST_TIERS:
            with self.subTest(tier=tier):
                self.assertIn(tier.replace("_", " "), prose)

    def test_research_brief_claim_row_carries_the_source_class(self) -> None:
        prose = self._prose(self.definitions["research-brief"])
        self.assertIn("source class", prose)
        for tier in SOURCE_TRUST_TIERS:
            with self.subTest(tier=tier):
                self.assertIn(tier.replace("_", " "), prose)

    def test_best_practice_research_denies_itself_completion(self) -> None:
        """The strongest tier is the one most likely to be read as done."""
        prose = self._prose(self.definitions["best-practice-research"])
        self.assertIn("not completion evidence", prose)

    def test_no_research_lane_prose_promises_a_completion_claim(self) -> None:
        """The negative case: no lane may describe a source as settling completion."""
        for name in ("research", "research-brief", "best-practice-research"):
            with self.subTest(name=name):
                prose = self._prose(self.definitions[name])
                for tier in SOURCE_TRUST_TIERS:
                    readable = tier.replace("_", " ")
                    self.assertNotIn(f"{readable} confirms completion", prose)
                    self.assertNotIn(f"{readable} is completion evidence", prose)


if __name__ == "__main__":
    unittest.main()
