"""Contracts for `capability_gap_scout/v1`.

Three properties are load-bearing and each has its own section below.

Goal scoping. The report is an answer to one goal, so the same opportunities and
the same catalog asked about a different outcome must come back in a different
order. A ranking that ignored the goal would be the coverage report this repo
already has twice.

The product boundary. An opportunity's own prose may not name a distributable
artifact as the answer, and that is enforced in both directions: the phrasing
"install X" is refused, the same capability phrased as user value is accepted,
and a finding is free to name the project it was read from. The prohibition is
on the answer, never on the evidence.

Evidence states. Fresh, stale, and denied are three separate answers with three
separate consequences, and only fresh supports an opportunity. The staleness
verdict is derived from the cited snapshot at read time, so the same frozen
snapshot reads fresh under one `now` and stale under another while staying
byte-identical -- there is no expiry written anywhere to go out of date.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from _local_package import load_local_package

load_local_package()
from omh.capabilities.families import capability_family_projection
from omh.quality.capability_gap_scout import (
    AGE_NOT_ESTABLISHED,
    CAPABILITY_GAP_EVIDENCE_KEYS,
    CAPABILITY_GAP_OPPORTUNITY_KEYS,
    CAPABILITY_GAP_SCOUT_CLAIM_BOUNDARY,
    CAPABILITY_GAP_SCOUT_KEYS,
    CAPABILITY_GAP_SCOUT_NOT_OBSERVED,
    CAPABILITY_GAP_SCOUT_SCHEMA_VERSION,
    CAPABILITY_GAP_SOURCE_POLICY_KEYS,
    CAPABILITY_GAP_SUMMARY_KEYS,
    COVERAGE_STATES,
    DEFAULT_FRESHNESS_HORIZON_DAYS,
    EVIDENCE_STATES,
    MAX_APPROVED_SOURCES,
    MAX_EVIDENCE_PER_OPPORTUNITY,
    MAX_FRESHNESS_HORIZON_DAYS,
    MAX_LINE_LENGTH,
    MAX_OPPORTUNITIES,
    OPPORTUNITY_CONFIDENCE_LEVELS,
    SCOUT_NEXT_ACTIONS,
    WITHHELD_REASONS,
    CapabilityGapScoutError,
    build_capability_gap_scout,
    package_recommendation_refusals,
    validate_capability_gap_scout,
)
from omh.quality.capability_inspiration_snapshot import (
    build_capability_inspiration_snapshot,
    build_capability_inspiration_source,
)


_APPROVED_DOCS = "https://docs.openclaw.ai/plugins/community"
_APPROVED_REGISTRY = "https://registry.modelcontextprotocol.io/docs"
_UNAPPROVED = "https://example.invalid/somebodys-blog-post"

_NOW = "2026-08-09T00:00:00Z"
_RECENT = "2026-07-01T00:00:00Z"
_LONG_AGO = "2024-01-01T00:00:00Z"

_REVIEW_GOAL = "catch risky changes before review"
_CITATION_GOAL = "explain which research source backs a claim"

# Words that would make a ranked opportunity read as a survey OMH just ran. None
# of them appears in this family's field names or closed vocabularies, which is
# the property the "OMH does not search" boundary asks for.
_ASSERTED_SEARCH = (
    "reachable",
    "online",
    "verified",
    "fetched",
    "downloaded",
    "crawled",
    "searched",
    "up to date",
)

# Every module that could turn this into a searcher. The scout ranks evidence a
# caller supplied; a network import here would make that claim unenforceable.
_NETWORK_MODULES = (
    "http",
    "httpx",
    "requests",
    "socket",
    "ssl",
    "subprocess",
    "urllib",
)


def _snapshot(
    uri: str = _APPROVED_DOCS,
    *,
    observed_at: str = _RECENT,
    content: str = "community plugin index, hooks section",
    extra_uri: str = "",
) -> dict[str, object]:
    sources = [
        build_capability_inspiration_source(
            uri=uri, revision="a" * 40, content=content, observation_provenance="user"
        )
    ]
    if extra_uri:
        sources.append(
            build_capability_inspiration_source(
                uri=extra_uri,
                revision="b" * 40,
                content=f"{content} second reading",
                observation_provenance="user",
            )
        )
    return build_capability_inspiration_snapshot(
        capability_id="capability-gap-scout",
        observed_sources=sources,
        observer="maintainer reading the published docs",
        observed_at=observed_at,
        findings=("Hook manifests are declared per lane.",),
    )


def _evidence(
    finding: str = "Risk lanes are ordered before a reviewer opens anything.",
    **snapshot_kwargs: object,
) -> dict[str, object]:
    return {"snapshot": _snapshot(**snapshot_kwargs), "finding": finding}  # type: ignore[arg-type]


def _review_opportunity(**overrides: object) -> dict[str, object]:
    opportunity: dict[str, object] = {
        "outcome_statement": "Hermes flags a risky change before a reviewer opens the diff",
        "user_value": "A reviewer sees which change is risky without reading everything first.",
        "coverage_family_id": "delegate_coding_and_ship",
        "coverage_state": "partial",
        "coverage_note": "Code review is covered; ordering by risk is not.",
        "evidence": [_evidence()],
    }
    opportunity.update(overrides)
    return opportunity


def _citation_opportunity(**overrides: object) -> dict[str, object]:
    opportunity: dict[str, object] = {
        "outcome_statement": "Hermes names the research source behind every claim it makes",
        "user_value": "A reader can check a claim against the research source it came from.",
        "coverage_family_id": "learn_and_gather",
        "coverage_state": "covered",
        "coverage_note": "Source finding and research already retrieve material.",
        "evidence": [_evidence(finding="Each claim carries the id of the source it came from.")],
    }
    opportunity.update(overrides)
    return opportunity


def _scout(
    *,
    goal: str = _REVIEW_GOAL,
    now: str = _NOW,
    opportunities: list[dict[str, object]] | None = None,
    approved_sources: tuple[str, ...] = (_APPROVED_DOCS, _APPROVED_REGISTRY),
    freshness_horizon_days: int = DEFAULT_FRESHNESS_HORIZON_DAYS,
) -> dict[str, object]:
    return build_capability_gap_scout(
        goal=goal,
        now=now,
        opportunities=opportunities if opportunities is not None else [_review_opportunity()],
        approved_sources=approved_sources,
        freshness_horizon_days=freshness_horizon_days,
    )


def _first(report: dict[str, object]) -> dict[str, object]:
    return report["ranked_opportunities"][0]  # type: ignore[index,return-value]


def _only_withheld(report: dict[str, object]) -> dict[str, object]:
    return report["withheld_opportunities"][0]  # type: ignore[index,return-value]


class GoalScopedOpportunitiesTests(unittest.TestCase):
    """AC1: distinct opportunities tied to coverage, reordered by the goal."""

    def test_a_goal_returns_distinct_opportunities_tied_to_current_coverage(self) -> None:
        report = _scout(opportunities=[_review_opportunity(), _citation_opportunity()])
        ranked = report["ranked_opportunities"]

        self.assertEqual(validate_capability_gap_scout(report), [])
        self.assertEqual(report["schema_version"], CAPABILITY_GAP_SCOUT_SCHEMA_VERSION)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(len({row["opportunity_id"] for row in ranked}), 2)
        self.assertEqual([row["rank"] for row in ranked], [1, 2])
        self.assertEqual(report["summary"]["distinct_coverage_family_count"], 2)
        for row in ranked:
            with self.subTest(opportunity=row["opportunity_id"]):
                self.assertIn(row["coverage_state"], COVERAGE_STATES)
                self.assertTrue(row["coverage_label"])
                self.assertEqual(row["withheld_reason"], "")

    def test_coverage_is_read_from_the_live_capability_families(self) -> None:
        families = {
            str(family["id"]): str(family["label"])
            for family in capability_family_projection()["families"]
        }
        report = _scout(opportunities=[_review_opportunity(), _citation_opportunity()])

        for row in report["ranked_opportunities"]:
            with self.subTest(family=row["coverage_family_id"]):
                self.assertIn(row["coverage_family_id"], families)
                self.assertEqual(row["coverage_label"], families[str(row["coverage_family_id"])])

    def test_a_different_goal_reorders_the_same_catalog(self) -> None:
        opportunities = [_review_opportunity(), _citation_opportunity()]

        for_review = _scout(goal=_REVIEW_GOAL, opportunities=opportunities)
        for_citations = _scout(goal=_CITATION_GOAL, opportunities=opportunities)

        self.assertEqual(validate_capability_gap_scout(for_citations), [])
        self.assertEqual(_first(for_review)["coverage_family_id"], "delegate_coding_and_ship")
        self.assertEqual(_first(for_citations)["coverage_family_id"], "learn_and_gather")
        self.assertNotEqual(
            [row["opportunity_id"] for row in for_review["ranked_opportunities"]],
            [row["opportunity_id"] for row in for_citations["ranked_opportunities"]],
        )

    def test_the_goal_outranks_the_size_of_the_gap(self) -> None:
        # The citation opportunity is `covered` and the review one is `partial`,
        # so coverage weight alone would rank review first under either goal.
        # Under the citation goal it does not, which is what "goal-scoped" buys.
        for_citations = _scout(
            goal=_CITATION_GOAL, opportunities=[_review_opportunity(), _citation_opportunity()]
        )
        winner, runner_up = for_citations["ranked_opportunities"]

        self.assertEqual(winner["coverage_state"], "covered")
        self.assertEqual(runner_up["coverage_state"], "partial")
        self.assertGreater(winner["goal_alignment"], runner_up["goal_alignment"])
        self.assertGreater(winner["priority_score"], runner_up["priority_score"])

    def test_a_coverage_family_outside_the_projection_is_refused(self) -> None:
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(opportunities=[_review_opportunity(coverage_family_id="ship_it_faster")])

        self.assertIn("coverage_family_id must be one of", str(caught.exception))

    def test_the_same_request_reproduces_the_same_report(self) -> None:
        opportunities = [_review_opportunity(), _citation_opportunity()]

        first = _scout(opportunities=opportunities)
        second = _scout(opportunities=opportunities)

        self.assertEqual(first, second)

    def test_the_report_id_ignores_the_moment_it_was_evaluated(self) -> None:
        early = _scout(now="2026-08-09T00:00:00Z")
        later = _scout(now="2026-08-19T00:00:00Z")

        # The clock really did move -- the recorded age says so -- and the
        # identity of the answer did not follow it.
        self.assertNotEqual(early["evaluated_at"], later["evaluated_at"])
        self.assertEqual(
            _first(later)["evidence"][0]["observed_age_days"]
            - _first(early)["evidence"][0]["observed_age_days"],
            10,
        )
        self.assertEqual(early["report_id"], later["report_id"])

    def test_a_moved_verdict_moves_the_report_id(self) -> None:
        fresh = _scout(now=_NOW)
        aged = _scout(now="2027-08-09T00:00:00Z")

        self.assertNotEqual(fresh["report_id"], aged["report_id"])

    def test_a_goal_the_ranker_cannot_score_is_refused(self) -> None:
        for goal in ("", "   ", "the and or of to"):
            with self.subTest(goal=goal):
                with self.assertRaises(CapabilityGapScoutError) as caught:
                    _scout(goal=goal)
                self.assertIn("goal", str(caught.exception))


class UserValueNotAShoppingListTests(unittest.TestCase):
    """AC2: the answer explains value; naming an installable package is refused."""

    def test_an_opportunity_phrased_as_an_installation_is_refused(self) -> None:
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(
                opportunities=[
                    _review_opportunity(
                        outcome_statement="Install the openclaw diff-risk plugin so Hermes flags changes"
                    )
                ]
            )

        message = str(caught.exception)
        self.assertIn("recommends an installable package instead of user value", message)
        self.assertIn("'install'", message)

    def test_the_same_capability_phrased_as_user_value_is_accepted(self) -> None:
        report = _scout(
            opportunities=[
                _review_opportunity(
                    outcome_statement="Hermes flags a risky change before a reviewer opens the diff"
                )
            ]
        )

        self.assertEqual(validate_capability_gap_scout(report), [])
        self.assertEqual(_first(report)["rank"], 1)
        self.assertTrue(_first(report)["user_value"])

    def test_adopting_a_named_artifact_is_refused_without_the_word_install(self) -> None:
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(
                opportunities=[
                    _review_opportunity(
                        outcome_statement="Adopt the community review plugin for risky changes"
                    )
                ]
            )

        self.assertIn("recommends adopting a distributable artifact", str(caught.exception))

    def test_every_prose_field_of_an_opportunity_is_held_to_the_boundary(self) -> None:
        shopping_list = "Enable the diff-risk extension for reviewers"
        for field in ("outcome_statement", "user_value", "coverage_note"):
            with self.subTest(field=field):
                with self.assertRaises(CapabilityGapScoutError) as caught:
                    _scout(opportunities=[_review_opportunity(**{field: shopping_list})])
                self.assertIn(field, str(caught.exception))

    def test_a_finding_may_name_the_project_it_was_read_from(self) -> None:
        observed = "The openclaw community plugin page documents a per-hook risk manifest."
        report = _scout(opportunities=[_review_opportunity(evidence=[_evidence(finding=observed)])])

        self.assertEqual(validate_capability_gap_scout(report), [])
        self.assertEqual(_first(report)["evidence"][0]["finding"], observed)

    def test_the_answer_may_not_recommend_what_a_finding_is_allowed_to_name(self) -> None:
        # The sharp edge of AC2: the very same sentence is admissible evidence
        # and an inadmissible answer. The prohibition is on what OMH proposes,
        # never on what an observer read.
        recommendation = "Use the openclaw community plugin to get a per-hook risk manifest"

        as_evidence = _scout(
            opportunities=[_review_opportunity(evidence=[_evidence(finding=recommendation)])]
        )

        self.assertEqual(validate_capability_gap_scout(as_evidence), [])
        self.assertEqual(_first(as_evidence)["evidence"][0]["finding"], recommendation)
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(opportunities=[_review_opportunity(outcome_statement=recommendation)])
        self.assertIn("recommends adopting a distributable artifact", str(caught.exception))

    def test_the_boundary_language_this_repo_already_uses_is_not_refused(self) -> None:
        # "must not require the source plugin at runtime" is the inspiration
        # boundary every issue in this batch states. A blanket word ban would
        # refuse OMH's own contract language, so the artifact vocabulary only
        # refuses alongside an adoption cue.
        self.assertEqual(
            package_recommendation_refusals(
                "Hermes answers this natively and must not require the source plugin at runtime"
            ),
            [],
        )

    def test_an_omh_package_workflow_is_not_read_as_a_distributable(self) -> None:
        # `materials-package`, `report-package`, and `deliverable-package` are
        # OMH capabilities. A rule that refuses its own product vocabulary is a
        # broken rule, so bare "package" is deliberately not a cue.
        self.assertEqual(
            package_recommendation_refusals(
                "Hermes prepares a materials package a reviewer can read in one pass"
            ),
            [],
        )

    def test_a_hand_written_report_cannot_smuggle_a_recommendation_past_the_validator(self) -> None:
        report = _scout()
        _first(report)["user_value"] = "Install the diff-risk plugin and the reviewer is done"

        errors = validate_capability_gap_scout(report)

        self.assertTrue(
            any("recommends an installable package instead of user value" in error for error in errors),
            errors,
        )

    def test_user_value_is_required(self) -> None:
        for field in ("outcome_statement", "user_value"):
            with self.subTest(field=field):
                with self.assertRaises(CapabilityGapScoutError) as caught:
                    _scout(opportunities=[_review_opportunity(**{field: "   "})])
                self.assertIn(f"{field} is required", str(caught.exception))


class ThreeEvidenceStatesTests(unittest.TestCase):
    """AC3: fresh, stale, and denied stay three answers with three consequences."""

    def test_the_three_states_are_distinguishable_in_one_report(self) -> None:
        report = _scout(
            opportunities=[
                _review_opportunity(
                    evidence=[
                        _evidence(finding="A recent reading of the approved docs."),
                        _evidence(finding="An old reading of the approved docs.", observed_at=_LONG_AGO),
                        _evidence(finding="A reading of a source nobody approved.", uri=_UNAPPROVED),
                    ]
                )
            ]
        )
        row = _first(report)
        states = {str(item["evidence_state"]) for item in row["evidence"]}

        self.assertEqual(validate_capability_gap_scout(report), [])
        self.assertEqual(states, set(EVIDENCE_STATES))
        self.assertEqual(
            (row["fresh_evidence_count"], row["stale_evidence_count"], row["denied_evidence_count"]),
            (1, 1, 1),
        )

    def test_a_ranked_opportunity_still_shows_what_did_not_support_it(self) -> None:
        report = _scout(
            opportunities=[
                _review_opportunity(
                    evidence=[
                        _evidence(finding="A recent reading of the approved docs."),
                        _evidence(finding="An old reading of the approved docs.", observed_at=_LONG_AGO),
                    ]
                )
            ]
        )
        row = _first(report)

        self.assertEqual(row["rank"], 1)
        self.assertEqual(row["fresh_evidence_count"], 1)
        self.assertEqual(row["stale_evidence_count"], 1)
        self.assertEqual(report["summary"]["stale_evidence_count"], 1)

    def test_confidence_counts_distinct_fresh_snapshots(self) -> None:
        cases = {
            "low": [_evidence(finding="One reading.")],
            "medium": [
                _evidence(finding="One reading.", content="first page"),
                _evidence(finding="Another reading.", content="second page"),
            ],
            "high": [
                _evidence(finding=f"Reading {index}.", content=f"page {index}")
                for index in range(3)
            ],
        }
        for expected, evidence in cases.items():
            with self.subTest(confidence=expected):
                report = _scout(opportunities=[_review_opportunity(evidence=evidence)])
                self.assertEqual(_first(report)["confidence"], expected)
                self.assertIn(expected, OPPORTUNITY_CONFIDENCE_LEVELS)

    def test_paraphrasing_one_reading_does_not_raise_confidence(self) -> None:
        # Two findings drawn from the same frozen snapshot are one piece of
        # evidence. A count over findings would reward saying it twice.
        snapshot = _snapshot()
        report = _scout(
            opportunities=[
                _review_opportunity(
                    evidence=[
                        {"snapshot": snapshot, "finding": "Risk lanes are ordered."},
                        {"snapshot": snapshot, "finding": "Ordering happens before review."},
                    ]
                )
            ]
        )

        self.assertEqual(_first(report)["fresh_evidence_count"], 2)
        self.assertEqual(_first(report)["confidence"], "low")

    def test_evidence_that_cannot_support_leaves_confidence_unsupported(self) -> None:
        for evidence in (
            [_evidence(observed_at=_LONG_AGO)],
            [_evidence(uri=_UNAPPROVED)],
        ):
            with self.subTest(state=evidence[0]["snapshot"]["observed_at"]):
                report = _scout(opportunities=[_review_opportunity(evidence=evidence)])
                self.assertEqual(_only_withheld(report)["confidence"], "unsupported")
                self.assertEqual(OPPORTUNITY_CONFIDENCE_LEVELS[0], "unsupported")

    def test_a_confidence_that_outruns_its_evidence_is_refused(self) -> None:
        report = _scout()
        _first(report)["confidence"] = "high"

        self.assertIn(
            "capability_gap_scout ranked_opportunities[0] confidence does not match its distinct "
            "fresh snapshots",
            validate_capability_gap_scout(report),
        )

    def test_stale_evidence_cannot_support_an_opportunity(self) -> None:
        report = _scout(
            opportunities=[
                _review_opportunity(evidence=[_evidence(observed_at=_LONG_AGO)]),
            ]
        )
        withheld = _only_withheld(report)

        self.assertEqual(validate_capability_gap_scout(report), [])
        self.assertEqual(report["ranked_opportunities"], [])
        self.assertEqual(withheld["withheld_reason"], "evidence_stale")
        self.assertEqual(withheld["fresh_evidence_count"], 0)
        self.assertEqual(withheld["rank"], 0)
        self.assertEqual(report["next_action"], "refresh_stale_evidence")

    def test_denied_evidence_cannot_support_an_opportunity(self) -> None:
        report = _scout(
            opportunities=[_review_opportunity(evidence=[_evidence(uri=_UNAPPROVED)])],
        )
        withheld = _only_withheld(report)

        self.assertEqual(validate_capability_gap_scout(report), [])
        self.assertEqual(report["ranked_opportunities"], [])
        self.assertEqual(withheld["withheld_reason"], "evidence_denied")
        self.assertEqual(withheld["evidence"][0]["denied_sources"], [_UNAPPROVED])
        self.assertEqual(report["next_action"], "widen_source_policy")

    def test_a_denial_is_never_reported_as_staleness(self) -> None:
        # An unapproved source read years ago is `denied`, not `stale`: the
        # policy verdict comes first and its age was never even considered.
        report = _scout(
            opportunities=[_review_opportunity(evidence=[_evidence(uri=_UNAPPROVED, observed_at=_LONG_AGO)])],
        )
        item = _only_withheld(report)["evidence"][0]

        self.assertEqual(item["evidence_state"], "denied")
        self.assertEqual(_only_withheld(report)["withheld_reason"], "evidence_denied")
        self.assertEqual(report["summary"]["stale_evidence_count"], 0)

    def test_stale_and_denied_together_keep_both_reasons(self) -> None:
        report = _scout(
            opportunities=[
                _review_opportunity(
                    evidence=[
                        _evidence(finding="An old reading of the approved docs.", observed_at=_LONG_AGO),
                        _evidence(finding="A reading of a source nobody approved.", uri=_UNAPPROVED),
                    ]
                )
            ]
        )

        self.assertEqual(validate_capability_gap_scout(report), [])
        self.assertEqual(_only_withheld(report)["withheld_reason"], "evidence_stale_and_denied")
        self.assertEqual(sorted(WITHHELD_REASONS)[-1], "evidence_stale_and_denied")

    def test_one_denied_source_denies_a_snapshot_that_also_cites_an_approved_one(self) -> None:
        # A finding cannot say which of its sources it came from, so a snapshot
        # is only as admissible as its least admissible source.
        report = _scout(
            opportunities=[_review_opportunity(evidence=[_evidence(extra_uri=_UNAPPROVED)])],
        )
        item = _only_withheld(report)["evidence"][0]

        self.assertEqual(item["cited_source_count"], 2)
        self.assertEqual(item["denied_sources"], [_UNAPPROVED])
        self.assertEqual(item["evidence_state"], "denied")

    def test_an_empty_policy_admits_nothing(self) -> None:
        report = _scout(opportunities=[_review_opportunity()], approved_sources=())

        self.assertEqual(validate_capability_gap_scout(report), [])
        self.assertEqual(report["source_policy"]["approved_source_count"], 0)
        self.assertEqual(report["next_action"], "widen_source_policy")

    def test_staleness_is_derived_at_read_time_and_never_written_down(self) -> None:
        snapshot = _snapshot(observed_at=_RECENT)
        frozen = json.dumps(snapshot, sort_keys=True)
        opportunities = [_review_opportunity(evidence=[{"snapshot": snapshot, "finding": "One reading."}])]

        inside = _scout(now="2026-08-09T00:00:00Z", opportunities=opportunities)
        outside = _scout(now="2027-08-09T00:00:00Z", opportunities=opportunities)

        self.assertEqual(_first(inside)["evidence"][0]["evidence_state"], "fresh")
        self.assertEqual(_only_withheld(outside)["evidence"][0]["evidence_state"], "stale")
        self.assertEqual(json.dumps(snapshot, sort_keys=True), frozen)
        self.assertNotIn("expires", frozen)
        self.assertNotIn("expiry", frozen)

    def test_the_horizon_is_the_callers_policy_not_the_snapshots(self) -> None:
        opportunities = [_review_opportunity(evidence=[_evidence(observed_at=_RECENT)])]

        generous = _scout(opportunities=opportunities, freshness_horizon_days=90)
        strict = _scout(opportunities=opportunities, freshness_horizon_days=7)

        self.assertEqual(_first(generous)["evidence"][0]["evidence_state"], "fresh")
        self.assertEqual(_only_withheld(strict)["evidence"][0]["evidence_state"], "stale")
        self.assertEqual(
            _first(generous)["evidence"][0]["observed_age_days"],
            _only_withheld(strict)["evidence"][0]["observed_age_days"],
        )

    def test_an_observation_time_that_cannot_be_read_is_never_fresh(self) -> None:
        report = _scout(
            opportunities=[_review_opportunity(evidence=[_evidence(observed_at="sometime last spring")])],
        )
        item = _only_withheld(report)["evidence"][0]

        self.assertEqual(validate_capability_gap_scout(report), [])
        self.assertEqual(item["observed_age_days"], AGE_NOT_ESTABLISHED)
        self.assertEqual(item["evidence_state"], "stale")

    def test_a_now_that_cannot_be_read_is_refused(self) -> None:
        for now in ("", "yesterday"):
            with self.subTest(now=now):
                with self.assertRaises(CapabilityGapScoutError) as caught:
                    _scout(now=now)
                self.assertIn("now must be an ISO 8601 timestamp", str(caught.exception))

    def test_an_observation_dated_ahead_of_now_is_clamped_rather_than_refused(self) -> None:
        report = _scout(
            opportunities=[_review_opportunity(evidence=[_evidence(observed_at="2026-08-09T00:00:01Z")])],
            now="2026-08-09T00:00:00Z",
        )
        item = _first(report)["evidence"][0]

        self.assertEqual(item["observed_age_days"], 0)
        self.assertEqual(item["evidence_state"], "fresh")

    def test_a_naive_observation_time_is_read_as_utc(self) -> None:
        report = _scout(opportunities=[_review_opportunity(evidence=[_evidence(observed_at="2026-07-01")])])

        self.assertEqual(_first(report)["evidence"][0]["observed_age_days"], 39)


class CollapsingAndRefusalTests(unittest.TestCase):
    """The guards that keep a ranking from double-counting or floating free."""

    def test_duplicate_opportunities_are_collapsed_into_one(self) -> None:
        report = _scout(
            opportunities=[
                _review_opportunity(evidence=[_evidence(finding="First reading of the docs.")]),
                _review_opportunity(evidence=[_evidence(finding="Second reading of the docs.")]),
            ]
        )
        row = _first(report)

        self.assertEqual(validate_capability_gap_scout(report), [])
        self.assertEqual(len(report["ranked_opportunities"]), 1)
        self.assertEqual(report["summary"]["opportunity_count"], 1)
        self.assertEqual(len(row["evidence"]), 2)
        self.assertEqual(row["fresh_evidence_count"], 2)

    def test_a_repeated_wording_is_the_same_opportunity(self) -> None:
        spaced = _review_opportunity(
            outcome_statement="  Hermes flags a RISKY change before a reviewer   opens the diff ",
            user_value="A different author said the same thing in different words.",
        )
        report = _scout(opportunities=[_review_opportunity(), spaced])

        self.assertEqual(len(report["ranked_opportunities"]), 1)
        self.assertEqual(
            _first(report)["user_value"],
            "A reviewer sees which change is risky without reading everything first.",
        )

    def test_a_repeated_citation_is_counted_once(self) -> None:
        report = _scout(
            opportunities=[
                _review_opportunity(evidence=[_evidence(finding="One reading."), _evidence(finding="One reading.")])
            ]
        )

        self.assertEqual(len(_first(report)["evidence"]), 1)
        self.assertEqual(_first(report)["fresh_evidence_count"], 1)

    def test_an_opportunity_citing_no_evidence_is_refused(self) -> None:
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(opportunities=[_review_opportunity(evidence=[])])

        self.assertIn("requires at least one cited snapshot", str(caught.exception))

    def test_an_invalid_snapshot_citation_is_refused(self) -> None:
        broken = _snapshot()
        broken["evidence_digest"] = "0" * 64

        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(
                opportunities=[
                    _review_opportunity(evidence=[{"snapshot": broken, "finding": "A reading."}])
                ]
            )

        self.assertIn("cites an invalid snapshot", str(caught.exception))

    def test_a_citation_that_is_not_a_snapshot_is_refused(self) -> None:
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(
                opportunities=[
                    _review_opportunity(evidence=[{"snapshot": "the openclaw docs", "finding": "A reading."}])
                ]
            )

        self.assertIn("must be a capability_inspiration_snapshot/v1 object", str(caught.exception))

    def test_a_finding_is_required(self) -> None:
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(opportunities=[_review_opportunity(evidence=[{"snapshot": _snapshot(), "finding": " "}])])

        self.assertIn("evidence finding is required", str(caught.exception))

    def test_unsupported_input_keys_are_refused(self) -> None:
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(opportunities=[_review_opportunity(**{"priority_score": 999})])
        self.assertIn("opportunity has unsupported keys: ['priority_score']", str(caught.exception))

        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(
                opportunities=[
                    _review_opportunity(
                        evidence=[{"snapshot": _snapshot(), "finding": "A reading.", "state": "fresh"}]
                    )
                ]
            )
        self.assertIn("evidence has unsupported keys: ['state']", str(caught.exception))

    def test_opportunities_stay_bounded(self) -> None:
        crowd = [
            _review_opportunity(
                outcome_statement=f"Hermes narrows the review lane by rule number {index}",
                evidence=[_evidence(finding=f"Reading number {index}.")],
            )
            for index in range(MAX_OPPORTUNITIES + 1)
        ]

        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(opportunities=crowd)

        self.assertIn(f"exceeds {MAX_OPPORTUNITIES} distinct entries", str(caught.exception))

    def test_evidence_stays_bounded_on_the_way_in(self) -> None:
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(
                opportunities=[
                    _review_opportunity(
                        evidence=[
                            _evidence(finding=f"Reading number {index}.")
                            for index in range(MAX_EVIDENCE_PER_OPPORTUNITY + 1)
                        ]
                    )
                ]
            )

        self.assertIn(f"evidence exceeds {MAX_EVIDENCE_PER_OPPORTUNITY} entries", str(caught.exception))

    def test_evidence_stays_bounded_after_collapsing(self) -> None:
        half = MAX_EVIDENCE_PER_OPPORTUNITY // 2 + 1
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(
                opportunities=[
                    _review_opportunity(
                        evidence=[_evidence(finding=f"Left reading {index}.") for index in range(half)]
                    ),
                    _review_opportunity(
                        evidence=[_evidence(finding=f"Right reading {index}.") for index in range(half)]
                    ),
                ]
            )

        self.assertIn("after collapsing duplicates", str(caught.exception))

    def test_an_overlong_line_is_refused(self) -> None:
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(opportunities=[_review_opportunity(outcome_statement="x" * (MAX_LINE_LENGTH + 1))])

        self.assertIn(f"exceeds {MAX_LINE_LENGTH} characters", str(caught.exception))

    def test_the_source_policy_stays_bounded(self) -> None:
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(
                approved_sources=tuple(
                    f"https://example.test/source-{index}" for index in range(MAX_APPROVED_SOURCES + 1)
                )
            )

        self.assertIn(f"approved_sources exceeds {MAX_APPROVED_SOURCES} entries", str(caught.exception))

    def test_a_policy_that_is_not_a_list_of_uris_is_refused(self) -> None:
        with self.assertRaises(CapabilityGapScoutError) as caught:
            _scout(approved_sources=_APPROVED_DOCS)  # type: ignore[arg-type]

        self.assertIn("approved_sources must be a sequence of source uris", str(caught.exception))

    def test_an_unusable_horizon_is_refused(self) -> None:
        for horizon in (0, -1, MAX_FRESHNESS_HORIZON_DAYS + 1):
            with self.subTest(horizon=horizon):
                with self.assertRaises(CapabilityGapScoutError) as caught:
                    _scout(freshness_horizon_days=horizon)
                self.assertIn("freshness_horizon_days must be between", str(caught.exception))

    def test_a_report_with_nothing_to_rank_asks_for_evidence(self) -> None:
        report = _scout(opportunities=[])

        self.assertEqual(validate_capability_gap_scout(report), [])
        self.assertEqual(report["ranked_opportunities"], [])
        self.assertEqual(report["next_action"], "supply_observed_evidence")
        self.assertIn(report["next_action"], SCOUT_NEXT_ACTIONS)


class ValidatorTests(unittest.TestCase):
    """The key sets are closed both ways and every derived value is re-derived."""

    def test_the_built_report_carries_exactly_the_declared_keys(self) -> None:
        report = _scout(opportunities=[_review_opportunity()])

        self.assertEqual(sorted(report), sorted(CAPABILITY_GAP_SCOUT_KEYS))
        self.assertEqual(sorted(report["source_policy"]), sorted(CAPABILITY_GAP_SOURCE_POLICY_KEYS))
        self.assertEqual(sorted(report["summary"]), sorted(CAPABILITY_GAP_SUMMARY_KEYS))
        self.assertEqual(sorted(_first(report)), sorted(CAPABILITY_GAP_OPPORTUNITY_KEYS))
        self.assertEqual(sorted(_first(report)["evidence"][0]), sorted(CAPABILITY_GAP_EVIDENCE_KEYS))

    def test_an_extra_key_is_refused(self) -> None:
        report = _scout()
        report["source_freshness_verified"] = True

        self.assertIn(
            "capability_gap_scout has unsupported keys: ['source_freshness_verified']",
            validate_capability_gap_scout(report),
        )

    def test_a_missing_key_is_refused(self) -> None:
        report = _scout()
        del report["summary"]

        self.assertIn(
            "capability_gap_scout is missing keys: ['summary']",
            validate_capability_gap_scout(report),
        )

    def test_an_extra_key_on_a_nested_shape_is_refused(self) -> None:
        report = _scout()
        _first(report)["evidence"][0]["source_reachable"] = True

        self.assertIn(
            "capability_gap_scout ranked_opportunities[0] evidence[0] has unsupported keys: "
            "['source_reachable']",
            validate_capability_gap_scout(report),
        )

    def test_a_priority_that_does_not_follow_from_its_signals_is_refused(self) -> None:
        report = _scout(opportunities=[_review_opportunity(), _citation_opportunity()])
        _first(report)["priority_score"] = 9999

        self.assertIn(
            "capability_gap_scout ranked_opportunities[0] priority_score does not follow from its own signals",
            validate_capability_gap_scout(report),
        )

    def test_a_reordered_ranking_is_refused(self) -> None:
        report = _scout(opportunities=[_review_opportunity(), _citation_opportunity()])
        report["ranked_opportunities"] = list(reversed(report["ranked_opportunities"]))

        errors = validate_capability_gap_scout(report)

        self.assertIn(
            "capability_gap_scout ranked_opportunities must be sorted by descending priority "
            "then opportunity_id",
            errors,
        )

    def test_a_rank_out_of_sequence_is_refused(self) -> None:
        report = _scout()
        _first(report)["rank"] = 7

        self.assertIn(
            "capability_gap_scout ranked_opportunities[0] rank must be 1",
            validate_capability_gap_scout(report),
        )

    def test_a_stale_finding_promoted_to_fresh_is_refused(self) -> None:
        report = _scout(opportunities=[_review_opportunity(evidence=[_evidence(observed_at=_LONG_AGO)])])
        _only_withheld(report)["evidence"][0]["evidence_state"] = "fresh"

        errors = validate_capability_gap_scout(report)

        self.assertIn(
            "capability_gap_scout withheld_opportunities[0] evidence[0] evidence_state does not "
            "follow from its denied sources, age, and the report horizon",
            errors,
        )

    def test_a_denied_finding_promoted_to_fresh_is_refused(self) -> None:
        report = _scout(opportunities=[_review_opportunity(evidence=[_evidence(uri=_UNAPPROVED)])])
        _only_withheld(report)["evidence"][0]["evidence_state"] = "fresh"

        self.assertTrue(
            any("evidence_state does not follow" in error for error in validate_capability_gap_scout(report))
        )

    def test_a_withheld_opportunity_moved_into_the_ranking_is_refused(self) -> None:
        report = _scout(opportunities=[_review_opportunity(evidence=[_evidence(observed_at=_LONG_AGO)])])
        report["ranked_opportunities"] = report["withheld_opportunities"]
        report["withheld_opportunities"] = []

        errors = validate_capability_gap_scout(report)

        self.assertIn(
            "capability_gap_scout ranked_opportunities[0] a ranked opportunity must carry no withheld_reason",
            errors,
        )
        self.assertIn(
            "capability_gap_scout ranked_opportunities[0] a ranked opportunity must cite at least "
            "one fresh finding",
            errors,
        )

    def test_a_ranked_opportunity_moved_into_the_withheld_list_is_refused(self) -> None:
        report = _scout()
        report["withheld_opportunities"] = report["ranked_opportunities"]
        report["ranked_opportunities"] = []

        errors = validate_capability_gap_scout(report)

        self.assertIn(
            "capability_gap_scout withheld_opportunities[0] withheld_reason must be one of "
            f"{list(WITHHELD_REASONS)}",
            errors,
        )
        self.assertIn(
            "capability_gap_scout withheld_opportunities[0] a withheld opportunity must cite no fresh finding",
            errors,
        )

    def test_an_opportunity_repeated_across_both_lists_is_refused(self) -> None:
        report = _scout()
        report["withheld_opportunities"] = [dict(_first(report))]

        self.assertIn(
            "capability_gap_scout an opportunity_id must not appear twice",
            validate_capability_gap_scout(report),
        )

    def test_a_summary_that_does_not_total_the_lists_is_refused(self) -> None:
        report = _scout()
        report["summary"]["fresh_evidence_count"] = 42

        self.assertIn(
            "capability_gap_scout summary fresh_evidence_count does not match the reported opportunities",
            validate_capability_gap_scout(report),
        )

    def test_a_next_action_that_does_not_follow_is_refused(self) -> None:
        report = _scout()
        report["next_action"] = "widen_source_policy"

        self.assertIn(
            "capability_gap_scout next_action does not follow from its opportunities and evidence",
            validate_capability_gap_scout(report),
        )

    def test_a_report_id_that_does_not_match_its_material_is_refused(self) -> None:
        report = _scout()
        report["report_id"] = "capability-gap-scout-0000000000000000"

        self.assertIn(
            "capability_gap_scout report_id does not match its ranked material",
            validate_capability_gap_scout(report),
        )

    def test_a_rewritten_source_policy_is_refused(self) -> None:
        report = _scout()
        report["source_policy"]["approved_sources"] = [_UNAPPROVED]

        errors = validate_capability_gap_scout(report)

        self.assertIn(
            "capability_gap_scout source_policy approved_source_count does not match approved_sources",
            errors,
        )
        self.assertIn(
            "capability_gap_scout source_policy policy_id does not match its approved sources", errors
        )

    def test_a_weakened_claim_boundary_is_refused(self) -> None:
        report = _scout()
        report["claim_boundary"] = "OMH searched the ecosystem for you."

        self.assertIn(
            "capability_gap_scout claim_boundary must state that OMH searched nothing itself",
            validate_capability_gap_scout(report),
        )

    def test_a_dropped_boundary_declaration_is_refused(self) -> None:
        report = _scout()
        report["not_evidence_until_observed"] = ["source_availability"]

        self.assertIn(
            "capability_gap_scout not_evidence_until_observed must be "
            f"{list(CAPABILITY_GAP_SCOUT_NOT_OBSERVED)}",
            validate_capability_gap_scout(report),
        )

    def test_a_relabelled_coverage_family_is_refused(self) -> None:
        report = _scout()
        _first(report)["coverage_label"] = "Ship whatever is popular"

        self.assertIn(
            "capability_gap_scout ranked_opportunities[0] coverage_label does not match the current "
            "capability family",
            validate_capability_gap_scout(report),
        )

    def test_a_non_object_is_refused(self) -> None:
        self.assertEqual(validate_capability_gap_scout("not an object"), ["capability_gap_scout must be an object"])


class NoSearchHappenedTests(unittest.TestCase):
    """The report is a ranking over supplied evidence, and says so."""

    def test_the_module_cannot_search(self) -> None:
        # Parsed rather than string-matched: this module's docstring explains
        # what it does not import, and a prefix scan would read those sentences
        # as imports.
        module_path = (
            Path(__file__).resolve().parents[1] / "src" / "quality" / "capability_gap_scout.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])

        for module in _NETWORK_MODULES:
            with self.subTest(module=module):
                self.assertNotIn(module, imported)

    def test_nothing_in_the_report_reads_as_a_survey_omh_just_ran(self) -> None:
        report = _scout(opportunities=[_review_opportunity(), _citation_opportunity()])
        scanned = dict(report)
        scanned.pop("claim_boundary")
        rendered = json.dumps(scanned, sort_keys=True).lower()

        for phrase in _ASSERTED_SEARCH:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, rendered)

    def test_the_boundary_states_the_negations_it_owes(self) -> None:
        report = _scout()

        self.assertEqual(report["claim_boundary"], CAPABILITY_GAP_SCOUT_CLAIM_BOUNDARY)
        self.assertIn("no search, fetch, download, or network call", report["claim_boundary"])
        self.assertIn("never an instruction to install", report["claim_boundary"])
        self.assertEqual(
            report["not_evidence_until_observed"], list(CAPABILITY_GAP_SCOUT_NOT_OBSERVED)
        )


if __name__ == "__main__":
    unittest.main()
