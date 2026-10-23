"""Contracts for `native_capability_request/v1` (issue #789).

Grouped by acceptance criterion:

- AC1: a feature reference produces a request carrying citations and a
  current-coverage answer per named OMH capability. Missing either fails.
- AC2: the resolution is an OMH planning or coding action, and naming an
  installable package or extension as the resolution is a validation error.
  Both directions: the same need expressed natively is accepted.
- AC3: an accepted request populates an executor-neutral brief whose shape is
  identical across every first-class coding owner, and acceptance never reads
  as implementation.

Plus the two guards the family exists for: the observed reference behavior, the
desired outcome, and the missing native behavior are three distinguishable
fields, and a request citing no snapshot is refused.

The coverage id space and the surface vocabulary are re-derived from this
repository rather than restated here, so a test cannot pass against a
vocabulary that has drifted from the catalog it claims to come from.

Nothing in this file writes a file. Every payload is compared by value, and
`Path.write_text` rewrites "\\n" as CRLF on Windows, so a fixture written that
way would compare equal on macOS and unequal on the Windows job. There is no
fixture to get wrong.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any

from _local_package import load_local_package

load_local_package()
from omh.capabilities.families import capability_family_projection  # noqa: E402
from omh.catalogs.awesome_hermes_agent_outcomes import awesome_hermes_plugin_outcomes  # noqa: E402
from omh.coding.executors import EXECUTOR_PROFILES  # noqa: E402
from omh.quality.capability_inspiration_snapshot import (  # noqa: E402
    build_capability_inspiration_snapshot,
    build_capability_inspiration_source,
    capability_inspiration_citation,
)
from omh.skills.catalog import installable_skill_names  # noqa: E402
from omh.workflows.native_capability_blueprint import (  # noqa: E402
    IMPLEMENTATION_CLAIM_KEYS,
    NATIVE_CAPABILITY_SURFACES,
    REQUIRED_NATIVE_CAPABILITY_SURFACES,
    SOURCE_HOST_MECHANICS,
)
from omh.workflows.native_capability_request import (  # noqa: E402
    BRIEF_CLAIM_BOUNDARY,
    BRIEF_CLAIM_STATUS,
    BRIEF_DIGEST_KEYS,
    BRIEF_NON_GOALS,
    COVERAGE_GAP_STATES,
    COVERAGE_STATES,
    INSTALLATION_RESOLUTIONS,
    NATIVE_CAPABILITY_BRIEF_OWNERS,
    NATIVE_CAPABILITY_COVERAGE_KEYS,
    NATIVE_CAPABILITY_REQUEST_BRIEF_KEYS,
    NATIVE_CAPABILITY_REQUEST_BRIEF_SCHEMA_VERSION,
    NATIVE_CAPABILITY_REQUEST_KEYS,
    NATIVE_CAPABILITY_REQUEST_NOT_OBSERVED,
    NATIVE_CAPABILITY_REQUEST_SCHEMA_VERSION,
    NATIVE_CAPABILITY_RESOLUTIONS,
    OMH_CODING_RESOLUTIONS,
    OMH_PLANNING_RESOLUTIONS,
    REQUEST_CLAIM_BOUNDARY,
    REQUEST_DIGEST_KEYS,
    REQUEST_PRIVACY,
    REVIEW_STATES,
    NativeCapabilityRequestError,
    brief_digest_of,
    build_native_capability_request,
    build_native_capability_request_brief,
    native_capability_coverage_vocabulary,
    native_capability_request_blueprint_gap,
    native_capability_request_offered_actions,
    request_digest_of,
    resolution_lane,
    unknown_coverage_capabilities,
    validate_native_capability_request,
    validate_native_capability_request_brief,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "src" / "workflows" / "native_capability_request.py"

CAPABILITY_ID = "memory-browser"

# The feature URL a person pastes into chat. It is recorded here, in the
# snapshot the caller froze, and never on the request; OMH does not open it.
FEATURE_URL = "https://docs.openclaw.ai/plugins/community"


def citation(*, capability_id: str = CAPABILITY_ID, **overrides: Any) -> dict[str, Any]:
    """A `capability_inspiration_citation/v1` over one supplied feature page."""
    source = build_capability_inspiration_source(
        uri=overrides.get("uri", FEATURE_URL),
        revision="a" * 40,
        content="A community plugin that lists a workspace's saved notes and opens one.",
        license_note="MIT, as read on the page",
        observation_provenance="user",
    )
    snapshot = build_capability_inspiration_snapshot(
        capability_id=capability_id,
        observed_sources=[source],
        observer="user",
        observed_at="2026-08-09T00:00:00Z",
        findings=["The plugin lists saved notes and opens one in place."],
        derived_requirements=["Hermes should list reviewed OMH memory and open one entry."],
    )
    return capability_inspiration_citation(snapshot)


def request_kwargs(**overrides: Any) -> dict[str, Any]:
    """A complete, valid request's arguments, minimally overridable."""
    base: dict[str, Any] = {
        "capability_id": CAPABILITY_ID,
        "observed_reference_behavior": (
            "The referenced plugin lists a workspace's saved notes and opens one in place."
        ),
        "desired_user_outcome": (
            "A person asks Hermes what it remembers about a project and gets the entries back in chat."
        ),
        "missing_native_behavior": (
            "OMH has no way to list reviewed local memory entries back to a chat user."
        ),
        "example_requests": (
            "Make Hermes able to show me what it remembers about this project.",
            "I saw a notes browser in another agent; give Hermes the useful part.",
        ),
        "current_coverage": (
            {
                "capability_id": "memory-new",
                "coverage": "partially_covered",
                "note": "Captures one new record and never lists the existing ones.",
            },
            {
                "capability_id": "decision-recall",
                "coverage": "covered",
                "note": "Rejected-decision recall already answers from reviewed memory.",
            },
            {
                "capability_id": "retain_knowledge",
                "coverage": "partially_covered",
                "note": "The family has capture and cleanup, and no browse action.",
            },
        ),
        "inspiration_citation": citation(),
        "resolution_action": "prepare_native_capability_blueprint",
        "resolution_summary": (
            "Design a native OMH memory browsing capability that answers from reviewed local memory."
        ),
        "safety_constraints": (
            "Reviewed OMH-local memory only; never claim Hermes internal memory was read.",
        ),
        "affected_surfaces": ("skill_catalog", "routing_triggers", "memory_policy"),
        "observed_source_mechanics": ("host_plugin_manifest", "host_tool_definition"),
        "review_state": "prepared",
        "prepared_at": "2026-08-09T00:00:00Z",
    }
    base.update(overrides)
    return base


def request(**overrides: Any) -> dict[str, Any]:
    return build_native_capability_request(**request_kwargs(**overrides))


def accepted(**overrides: Any) -> dict[str, Any]:
    return request(review_state="accepted", **overrides)


def resealed(**overrides: Any) -> dict[str, Any]:
    """A payload edited after minting, with the digest and id re-derived.

    Lets a test fail on the rule under test rather than on the tamper check.
    """
    payload = request()
    payload.update(overrides)
    payload["request_digest"] = request_digest_of(payload)
    payload["request_id"] = f"native-capability-request-{payload['request_digest'][:16]}"
    return payload


def matrix_native_capability_ids() -> set[str]:
    """Every `native_capability_ids` entry in the plugin outcome matrix."""
    outcomes = awesome_hermes_plugin_outcomes()["outcomes"]
    return {
        str(capability_id)
        for outcome in outcomes  # type: ignore[union-attr]
        for capability_id in outcome["native_capability_ids"]
    }


class CoverageVocabularyTests(unittest.TestCase):
    """The ids a coverage answer may cite are this repository's, not prose."""

    def test_the_vocabulary_is_the_shipped_skills_and_the_family_ids(self) -> None:
        families = {str(family["id"]) for family in capability_family_projection()["families"]}  # type: ignore[union-attr,index]

        self.assertEqual(
            set(native_capability_coverage_vocabulary()),
            set(installable_skill_names()) | families,
        )

    def test_the_vocabulary_is_sorted_and_distinct(self) -> None:
        vocabulary = native_capability_coverage_vocabulary()

        self.assertEqual(list(vocabulary), sorted(set(vocabulary)))
        self.assertGreater(len(vocabulary), 50)

    def test_it_shares_the_id_space_the_plugin_outcome_matrix_already_uses(self) -> None:
        # `awesome_hermes_plugin_outcome_matrix/v1` maps an external plugin
        # outcome onto `native_capability_ids`. A coverage answer cites the same
        # space, so every id that matrix names and this repository ships is
        # usable as a coverage answer, and the ones it does not ship are not.
        matrix_ids = matrix_native_capability_ids()
        shipped = matrix_ids & set(native_capability_coverage_vocabulary())

        self.assertTrue(shipped, "the outcome matrix must name at least one shipped capability")
        self.assertEqual(unknown_coverage_capabilities(shipped), ())
        self.assertEqual(set(unknown_coverage_capabilities(matrix_ids)), matrix_ids - shipped)

    def test_a_capability_omh_does_not_ship_is_not_in_the_vocabulary(self) -> None:
        self.assertEqual(
            unknown_coverage_capabilities(["memory-new", "notes-browser-plugin"]),
            ("notes-browser-plugin",),
        )


class CitationAndCoverageTests(unittest.TestCase):
    """AC1: a feature reference produces citations plus current coverage."""

    def test_a_supplied_feature_reference_produces_a_valid_request(self) -> None:
        payload = request()

        self.assertEqual(validate_native_capability_request(payload), [])
        self.assertEqual(payload["schema_version"], NATIVE_CAPABILITY_REQUEST_SCHEMA_VERSION)
        self.assertEqual(payload["privacy"], REQUEST_PRIVACY)
        self.assertEqual(payload["claim_boundary"], REQUEST_CLAIM_BOUNDARY)
        self.assertEqual(payload["not_observed"], list(NATIVE_CAPABILITY_REQUEST_NOT_OBSERVED))
        self.assertTrue(payload["request_id"].startswith("native-capability-request-"))

    def test_the_citation_is_the_snapshot_family_and_not_a_second_evidence_record(self) -> None:
        payload = request()
        cited = payload["inspiration_citation"]

        self.assertEqual(cited["schema_version"], "capability_inspiration_citation/v1")
        self.assertEqual(cited["capability_id"], CAPABILITY_ID)
        self.assertEqual(cited["source_availability"], "not_observed")
        self.assertEqual(cited["cited_source_count"], 1)

    def test_the_feature_url_lives_in_the_cited_snapshot_and_never_on_the_request(self) -> None:
        payload = request()

        self.assertNotIn(FEATURE_URL, repr(payload))
        self.assertNotIn("uri", payload["inspiration_citation"])

    def test_a_request_citing_no_snapshot_is_refused(self) -> None:
        for empty in ({}, None):
            with self.subTest(empty=empty):
                with self.assertRaises(NativeCapabilityRequestError) as raised:
                    request(inspiration_citation=empty)
                self.assertIn("must be a capability_inspiration_citation/v1", str(raised.exception))

    def test_a_missing_citation_key_is_refused(self) -> None:
        payload = resealed()
        del payload["inspiration_citation"]
        payload["request_digest"] = request_digest_of(payload)
        payload["request_id"] = f"native-capability-request-{payload['request_digest'][:16]}"

        errors = validate_native_capability_request(payload)

        self.assertIn(
            "native_capability_request is missing keys: ['inspiration_citation']",
            errors,
        )

    def test_a_malformed_citation_is_refused_by_the_snapshot_validator(self) -> None:
        broken = citation()
        broken["cited_source_count"] = 0

        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(inspiration_citation=broken)

        self.assertIn("is not a valid citation", str(raised.exception))
        self.assertIn("cited_source_count", str(raised.exception))

    def test_a_citation_frozen_for_another_capability_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(inspiration_citation=citation(capability_id="something-else"))

        message = str(raised.exception)
        self.assertIn("must be frozen for the capability being requested", message)
        self.assertIn("something-else", message)

    def test_a_request_with_no_coverage_answer_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(current_coverage=())

        self.assertIn("current_coverage must answer for at least 1 OMH capability", str(raised.exception))

    def test_a_named_capability_without_a_verdict_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(current_coverage=({"capability_id": "memory-new", "note": "no verdict given"},))

        message = str(raised.exception)
        self.assertIn("needs a coverage answer for 'memory-new'", message)
        for state in COVERAGE_STATES:
            self.assertIn(state, message)

    def test_a_coverage_verdict_outside_the_vocabulary_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(
                current_coverage=(
                    {"capability_id": "memory-new", "coverage": "sort_of", "note": "hand-wave"},
                )
            )

        self.assertIn("needs a coverage answer for 'memory-new'", str(raised.exception))

    def test_coverage_naming_a_capability_omh_does_not_ship_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(
                current_coverage=(
                    {
                        "capability_id": "notes-browser-plugin",
                        "coverage": "not_covered",
                        "note": "invented capability",
                    },
                )
            )

        message = str(raised.exception)
        self.assertIn("names capabilities OMH does not ship", message)
        self.assertIn("notes-browser-plugin", message)
        self.assertIn("never prose", message)

    def test_a_capability_family_id_is_a_valid_coverage_answer(self) -> None:
        payload = request(
            current_coverage=(
                {
                    "capability_id": "retain_knowledge",
                    "coverage": "partially_covered",
                    "note": "The family has capture and cleanup, and no browse action.",
                },
            )
        )

        self.assertEqual(validate_native_capability_request(payload), [])

    def test_coverage_that_says_everything_is_covered_contradicts_the_stated_gap(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(
                current_coverage=(
                    {
                        "capability_id": "memory-new",
                        "coverage": "covered",
                        "note": "already answers",
                    },
                    {
                        "capability_id": "decision-recall",
                        "coverage": "covered",
                        "note": "already answers",
                    },
                )
            )

        self.assertIn("contradicts missing_native_behavior", str(raised.exception))

    def test_coverage_answers_once_per_capability(self) -> None:
        payload = resealed(
            current_coverage=[
                {"capability_id": "memory-new", "coverage": "not_covered", "note": "first"},
                {"capability_id": "memory-new", "coverage": "covered", "note": "second"},
            ]
        )

        self.assertIn(
            "native_capability_request current_coverage must answer once per capability",
            validate_native_capability_request(payload),
        )

    def test_a_coverage_entry_uses_an_exact_key_set(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(
                current_coverage=(
                    {
                        "capability_id": "memory-new",
                        "coverage": "not_covered",
                        "note": "fine",
                        "confidence": "high",
                    },
                )
            )

        self.assertIn("current_coverage entry has unsupported keys: ['confidence']", str(raised.exception))
        self.assertEqual(NATIVE_CAPABILITY_COVERAGE_KEYS, ("capability_id", "coverage", "note"))

    def test_coverage_order_does_not_change_the_request(self) -> None:
        forward = request_kwargs()["current_coverage"]
        backward = tuple(reversed(forward))

        self.assertEqual(request(current_coverage=forward), request(current_coverage=backward))

    def test_the_gap_states_are_the_verdicts_that_leave_work(self) -> None:
        self.assertEqual(COVERAGE_GAP_STATES, ("partially_covered", "not_covered"))
        self.assertEqual(set(COVERAGE_GAP_STATES) - set(COVERAGE_STATES), set())


class ResolutionBoundaryTests(unittest.TestCase):
    """AC2: OMH planning or coding, and never installing somebody's package."""

    def test_every_omh_resolution_is_accepted(self) -> None:
        for action in NATIVE_CAPABILITY_RESOLUTIONS:
            with self.subTest(action=action):
                payload = request(resolution_action=action)
                self.assertEqual(validate_native_capability_request(payload), [])
                self.assertIn(resolution_lane(action), ("omh_planning", "omh_coding"))

    def test_the_resolution_vocabulary_is_planning_plus_coding_and_nothing_else(self) -> None:
        self.assertEqual(
            NATIVE_CAPABILITY_RESOLUTIONS, (*OMH_PLANNING_RESOLUTIONS, *OMH_CODING_RESOLUTIONS)
        )
        self.assertEqual(set(OMH_PLANNING_RESOLUTIONS) & set(OMH_CODING_RESOLUTIONS), set())
        self.assertEqual(set(NATIVE_CAPABILITY_RESOLUTIONS) & set(INSTALLATION_RESOLUTIONS), set())
        self.assertEqual(resolution_lane("install_host_extension"), "")

    def test_naming_an_installable_resolution_is_refused_by_name(self) -> None:
        for action in INSTALLATION_RESOLUTIONS:
            with self.subTest(action=action):
                with self.assertRaises(NativeCapabilityRequestError) as raised:
                    request(resolution_action=action)
                message = str(raised.exception)
                self.assertIn("must not resolve the request by adopting a package", message)
                self.assertIn(action, message)
                self.assertIn("is never the resolution", message)

    def test_an_installation_directive_in_the_resolution_text_is_refused(self) -> None:
        summaries = (
            "Tell the user to install the OpenClaw notes plugin.",
            "Ask them to enable the plugin in their host settings.",
            "Run npm install openclaw-notes in the workspace.",
            "Add the MCP server that exposes the notes browser.",
            "Vendor the package into the repository and call it native.",
        )
        for summary in summaries:
            with self.subTest(summary=summary):
                with self.assertRaises(NativeCapabilityRequestError) as raised:
                    request(resolution_summary=summary)
                self.assertIn(
                    "must not name an installable package or extension as the resolution",
                    str(raised.exception),
                )

    def test_a_package_specifier_in_the_resolution_text_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(resolution_summary="Adopt @openclaw/notes-browser and expose it through Hermes.")

        self.assertIn("@openclaw/notes-browser", str(raised.exception))

    def test_the_same_need_as_an_omh_planning_action_is_accepted(self) -> None:
        payload = request(
            resolution_action="prepare_product_proposal",
            resolution_summary=(
                "Propose a native OMH capability that answers the same question from reviewed local memory."
            ),
        )

        self.assertEqual(validate_native_capability_request(payload), [])
        self.assertEqual(resolution_lane(payload["resolution_action"]), "omh_planning")

    def test_the_same_need_as_an_omh_coding_action_is_accepted(self) -> None:
        payload = request(
            resolution_action="prepare_coding_handoff",
            resolution_summary=(
                "Hand a scoped OMH coding task to the selected owner: list reviewed local memory in chat."
            ),
        )

        self.assertEqual(validate_native_capability_request(payload), [])
        self.assertEqual(resolution_lane(payload["resolution_action"]), "omh_coding")

    def test_the_scan_is_scoped_to_the_resolution_and_not_to_the_boundary_prose(self) -> None:
        # The same words are exactly right in a safety constraint, where they
        # state the boundary rather than propose crossing it.
        payload = request(
            safety_constraints=(
                "Never install the referenced extension or require it at runtime.",
                "Reviewed OMH-local memory only; never claim Hermes internal memory was read.",
            )
        )

        self.assertEqual(validate_native_capability_request(payload), [])

    def test_a_referenced_tool_may_still_be_mentioned_in_the_resolution(self) -> None:
        payload = request(
            resolution_summary=(
                "Design the native OMH equivalent of the behavior seen in the referenced notes browser."
            )
        )

        self.assertEqual(validate_native_capability_request(payload), [])

    def test_the_offered_actions_are_all_omh_actions_with_the_chosen_one_first(self) -> None:
        for action in NATIVE_CAPABILITY_RESOLUTIONS:
            with self.subTest(action=action):
                offered = native_capability_request_offered_actions(request(resolution_action=action))
                self.assertEqual(offered[0], action)
                self.assertEqual(sorted(offered), sorted(NATIVE_CAPABILITY_RESOLUTIONS))
                self.assertEqual(set(offered) & set(INSTALLATION_RESOLUTIONS), set())
                self.assertTrue(all(resolution_lane(item) in ("omh_planning", "omh_coding") for item in offered))

    def test_the_offered_actions_accessor_refuses_an_invalid_request(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError):
            native_capability_request_offered_actions(resealed(current_coverage=[]))


class SeparationGuardTests(unittest.TestCase):
    """The three things the issue exists to keep apart stay apart."""

    def test_the_three_fields_are_three_distinct_required_keys(self) -> None:
        fields = ("observed_reference_behavior", "desired_user_outcome", "missing_native_behavior")

        for field in fields:
            with self.subTest(field=field):
                self.assertIn(field, NATIVE_CAPABILITY_REQUEST_KEYS)
                self.assertIn(field, REQUEST_DIGEST_KEYS)
                with self.assertRaises(NativeCapabilityRequestError) as raised:
                    request(**{field: "  "})
                self.assertIn(f"{field} must be a non-empty string", str(raised.exception))

    def test_each_field_moves_the_digest_on_its_own(self) -> None:
        base = request()
        rewritten = {
            "observed_reference_behavior": "The referenced plugin also pins one note to the top.",
            "desired_user_outcome": "A person gets a short list back and can ask for one entry in full.",
            "missing_native_behavior": "OMH cannot render a reviewed memory entry into a chat answer.",
        }
        for field, text in rewritten.items():
            with self.subTest(field=field):
                changed = request(**{field: text})
                self.assertNotEqual(changed["request_digest"], base["request_digest"])
                self.assertEqual(
                    {key: changed[key] for key in rewritten if key != field},
                    {key: base[key] for key in rewritten if key != field},
                )

    def test_collapsing_any_two_of_them_into_one_sentence_is_refused(self) -> None:
        shared = "Hermes should list the notes it has saved for this project."
        pairs = (
            ("observed_reference_behavior", "desired_user_outcome"),
            ("observed_reference_behavior", "missing_native_behavior"),
            ("desired_user_outcome", "missing_native_behavior"),
        )
        for first, second in pairs:
            with self.subTest(pair=(first, second)):
                with self.assertRaises(NativeCapabilityRequestError) as raised:
                    request(**{first: shared, second: shared})
                message = str(raised.exception)
                self.assertIn("keeps the observed reference behavior", message)
                self.assertIn(first, message)
                self.assertIn(second, message)

    def test_the_reference_implementation_mechanics_have_their_own_field(self) -> None:
        payload = request()

        self.assertEqual(payload["observed_source_mechanics"], ["host_plugin_manifest", "host_tool_definition"])
        self.assertTrue(set(payload["observed_source_mechanics"]) <= set(SOURCE_HOST_MECHANICS))

    def test_an_invented_source_mechanic_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(observed_source_mechanics=("host_plugin_manifest", "host_telepathy"))

        self.assertIn("observed_source_mechanics has unsupported entries: ['host_telepathy']", str(raised.exception))

    def test_example_asks_are_sentences_rather_than_commands(self) -> None:
        for command in ("omh chat route 'browse memory'", "/memory-browser", "--capability memory-browser"):
            with self.subTest(command=command):
                with self.assertRaises(NativeCapabilityRequestError) as raised:
                    request(
                        example_requests=(
                            "Make Hermes able to show me what it remembers about this project.",
                            command,
                        )
                    )
                self.assertIn("natural-language asks, not commands", str(raised.exception))

    def test_a_request_names_at_least_one_safety_constraint(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(safety_constraints=())

        self.assertIn("safety_constraints must name at least 1", str(raised.exception))


class ExecutorNeutralBriefTests(unittest.TestCase):
    """AC3: an accepted request populates an executor-neutral brief."""

    def test_every_first_class_owner_is_a_valid_brief_owner(self) -> None:
        self.assertEqual(NATIVE_CAPABILITY_BRIEF_OWNERS, EXECUTOR_PROFILES)
        for owner in ("codex", "claude-code", "hermes", "generic"):
            self.assertIn(owner, NATIVE_CAPABILITY_BRIEF_OWNERS)

    def test_the_brief_shape_is_identical_across_every_owner(self) -> None:
        payload = accepted()
        briefs = {
            owner: build_native_capability_request_brief(
                payload, owner=owner, prepared_at="2026-08-09T00:00:00Z"
            )
            for owner in NATIVE_CAPABILITY_BRIEF_OWNERS
        }

        self.assertGreaterEqual(len(briefs), 3)
        shapes = {owner: {key: value for key, value in brief.items() if key != "owner"} for owner, brief in briefs.items()}
        reference = shapes["codex"]
        for owner, shape in shapes.items():
            with self.subTest(owner=owner):
                self.assertEqual(sorted(briefs[owner]), sorted(NATIVE_CAPABILITY_REQUEST_BRIEF_KEYS))
                self.assertEqual(briefs[owner]["owner"], owner)
                self.assertEqual(shape, reference)
                self.assertEqual(validate_native_capability_request_brief(briefs[owner]), [])

    def test_the_brief_digest_covers_the_work_and_not_the_owner(self) -> None:
        payload = accepted()
        digests = {
            build_native_capability_request_brief(payload, owner=owner)["brief_digest"]
            for owner in NATIVE_CAPABILITY_BRIEF_OWNERS
        }

        self.assertEqual(len(digests), 1)
        self.assertNotIn("owner", BRIEF_DIGEST_KEYS)
        self.assertNotIn("prepared_at", BRIEF_DIGEST_KEYS)

    def test_the_brief_digest_ignores_the_clock_and_follows_the_request(self) -> None:
        early = build_native_capability_request_brief(
            accepted(), owner="codex", prepared_at="2026-01-01T00:00:00Z"
        )
        late = build_native_capability_request_brief(
            accepted(), owner="codex", prepared_at="2030-12-31T23:59:59Z"
        )
        changed = build_native_capability_request_brief(
            accepted(
                desired_user_outcome="A person gets one remembered entry rendered in full when they ask."
            ),
            owner="codex",
        )

        self.assertEqual(early["brief_digest"], brief_digest_of(early))
        self.assertEqual(early["brief_digest"], late["brief_digest"])
        self.assertNotEqual(early["brief_digest"], changed["brief_digest"])

    def test_the_brief_carries_the_evidence_and_the_coverage_forward(self) -> None:
        payload = accepted()
        brief = build_native_capability_request_brief(payload, owner="claude-code")

        self.assertEqual(brief["inspiration_citation"], payload["inspiration_citation"])
        self.assertEqual(brief["current_coverage"], list(payload["current_coverage"]))
        self.assertEqual(brief["request_id"], payload["request_id"])
        self.assertEqual(brief["request_digest"], payload["request_digest"])

    def test_the_acceptance_criteria_are_derived_from_the_request(self) -> None:
        payload = accepted()
        brief = build_native_capability_request_brief(payload, owner="hermes")
        criteria = brief["acceptance_criteria"]

        self.assertIn(payload["desired_user_outcome"], criteria[0])
        self.assertIn(payload["missing_native_behavior"], criteria[1])
        gaps = [
            entry["capability_id"]
            for entry in payload["current_coverage"]
            if entry["coverage"] in COVERAGE_GAP_STATES
        ]
        for capability_id in gaps:
            self.assertTrue(any(capability_id in line for line in criteria))
        self.assertIn("No source-host mechanic becomes an OMH runtime requirement", criteria[-1])

    def test_only_an_accepted_request_populates_a_brief(self) -> None:
        for state in REVIEW_STATES:
            with self.subTest(state=state):
                payload = request(review_state=state)
                if state == "accepted":
                    self.assertEqual(
                        build_native_capability_request_brief(payload, owner="codex")["owner"], "codex"
                    )
                    continue
                with self.assertRaises(NativeCapabilityRequestError) as raised:
                    build_native_capability_request_brief(payload, owner="codex")
                message = str(raised.exception)
                self.assertIn("needs an accepted request", message)
                self.assertIn(state, message)

    def test_an_unsupported_owner_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            build_native_capability_request_brief(accepted(), owner="some-other-agent")

        self.assertIn("owner is unsupported", str(raised.exception))

    def test_an_invalid_request_never_populates_a_brief(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError):
            build_native_capability_request_brief(
                resealed(review_state="accepted", current_coverage=[]), owner="generic"
            )

    def test_acceptance_never_reads_as_implemented(self) -> None:
        brief = build_native_capability_request_brief(accepted(), owner="omx-runtime")

        self.assertEqual(brief["claim_status"], BRIEF_CLAIM_STATUS)
        self.assertEqual(brief["claim_status"], "prepared_not_observed")
        self.assertEqual(brief["claim_boundary"], BRIEF_CLAIM_BOUNDARY)
        self.assertEqual(brief["not_observed"], list(NATIVE_CAPABILITY_REQUEST_NOT_OBSERVED))
        self.assertEqual(set(brief) & IMPLEMENTATION_CLAIM_KEYS, set())
        for unobserved in ("native_capability_implementation", "verification_execution", "review", "ci", "merge"):
            self.assertIn(unobserved, brief["not_observed"])
        self.assertEqual(brief["non_goals"], list(BRIEF_NON_GOALS))
        self.assertIn("a reviewed decision rather than a built capability", brief["claim_boundary"])
        self.assertIn("none of it means the capability exists", brief["claim_boundary"])
        self.assertIn(
            "Claiming the capability exists, is available, or has been implemented.", brief["non_goals"]
        )

    def test_a_brief_shaped_to_claim_implementation_is_refused(self) -> None:
        brief = build_native_capability_request_brief(accepted(), owner="generic")
        brief["implemented"] = True

        errors = validate_native_capability_request_brief(brief)

        self.assertTrue(errors)
        self.assertIn("must not carry implementation-claim keys: ['implemented']", errors[0])

    def test_an_edited_brief_fails_its_digest(self) -> None:
        brief = build_native_capability_request_brief(accepted(), owner="generic")
        brief["desired_user_outcome"] = "Something nobody asked for."

        self.assertIn(
            "native_capability_request_brief brief_digest does not match the work it seals; the payload was "
            "edited after it was built",
            validate_native_capability_request_brief(brief),
        )

    def test_the_brief_schema_version_and_key_set_are_closed(self) -> None:
        brief = build_native_capability_request_brief(accepted(), owner="generic")

        self.assertEqual(brief["schema_version"], NATIVE_CAPABILITY_REQUEST_BRIEF_SCHEMA_VERSION)
        brief["extra"] = "no"
        self.assertIn(
            "native_capability_request_brief has unsupported keys: ['extra']",
            validate_native_capability_request_brief(brief),
        )


class BlueprintSeamTests(unittest.TestCase):
    """The request references #791's blueprint rather than re-inventing one."""

    def test_affected_surfaces_use_the_blueprint_vocabulary(self) -> None:
        payload = request(affected_surfaces=NATIVE_CAPABILITY_SURFACES)

        self.assertEqual(validate_native_capability_request(payload), [])
        self.assertEqual(payload["affected_surfaces"], list(NATIVE_CAPABILITY_SURFACES))

    def test_an_invented_surface_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(affected_surfaces=("skill_catalog", "vscode_agent_plugin"))

        self.assertIn("do not exist in this repository", str(raised.exception))
        self.assertIn("vscode_agent_plugin", str(raised.exception))

    def test_the_blueprint_gap_reports_what_a_blueprint_would_still_need(self) -> None:
        payload = request(affected_surfaces=("skill_catalog", "routing_triggers"))

        gap = native_capability_request_blueprint_gap(payload)

        self.assertNotIn("skill_catalog", gap)
        self.assertNotIn("routing_triggers", gap)
        self.assertEqual(
            list(gap),
            [
                surface
                for surface in REQUIRED_NATIVE_CAPABILITY_SURFACES
                if surface not in {"skill_catalog", "routing_triggers"}
            ],
        )

    def test_a_request_naming_every_required_surface_has_no_gap(self) -> None:
        self.assertEqual(
            native_capability_request_blueprint_gap(
                request(affected_surfaces=REQUIRED_NATIVE_CAPABILITY_SURFACES)
            ),
            (),
        )

    def test_naming_no_surface_is_allowed_and_reports_the_whole_required_set(self) -> None:
        payload = request(affected_surfaces=())

        self.assertEqual(validate_native_capability_request(payload), [])
        self.assertEqual(
            native_capability_request_blueprint_gap(payload), REQUIRED_NATIVE_CAPABILITY_SURFACES
        )

    def test_a_blueprint_is_referenced_by_its_digest(self) -> None:
        digest = "b" * 64

        payload = request(blueprint_ref=digest)

        self.assertEqual(validate_native_capability_request(payload), [])
        self.assertEqual(payload["blueprint_ref"], digest)

    def test_a_blueprint_reference_that_is_not_a_digest_is_refused(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(blueprint_ref="the memory browser blueprint")

        self.assertIn("must be a native_capability_blueprint/v1 blueprint_digest", str(raised.exception))

    def test_the_gap_accessor_refuses_an_invalid_request(self) -> None:
        with self.assertRaises(NativeCapabilityRequestError):
            native_capability_request_blueprint_gap(resealed(affected_surfaces=["not_a_surface"]))


class EnvelopeAndDeterminismTests(unittest.TestCase):
    """Closed keys, no clock, and a module that cannot reach the network."""

    def test_the_key_set_is_closed_in_both_directions(self) -> None:
        payload = request()

        self.assertEqual(sorted(payload), sorted(NATIVE_CAPABILITY_REQUEST_KEYS))

        payload["extra"] = "no"
        self.assertIn(
            "native_capability_request has unsupported keys: ['extra']",
            validate_native_capability_request(payload),
        )

        del payload["extra"]
        del payload["capability_id"]
        self.assertIn(
            "native_capability_request is missing keys: ['capability_id']",
            validate_native_capability_request(payload),
        )

    def test_a_feature_url_cannot_be_parked_on_the_request(self) -> None:
        payload = request()
        payload["url"] = FEATURE_URL

        errors = validate_native_capability_request(payload)

        self.assertIn("must not carry raw or hidden keys: ['url']", errors[0])
        self.assertIn("cited through", errors[0])

    def test_a_request_shaped_to_claim_implementation_is_refused(self) -> None:
        payload = request()
        payload["installed"] = True

        errors = validate_native_capability_request(payload)

        self.assertIn("must not carry implementation-claim keys: ['installed']", errors[0])

    def test_the_review_states_never_mean_available(self) -> None:
        self.assertEqual(REVIEW_STATES, ("prepared", "reviewed", "accepted", "rejected"))
        for state in REVIEW_STATES:
            with self.subTest(state=state):
                self.assertEqual(validate_native_capability_request(request(review_state=state)), [])
        with self.assertRaises(NativeCapabilityRequestError) as raised:
            request(review_state="available")
        self.assertIn("none of which means the capability is available", str(raised.exception))

    def test_the_digest_ignores_the_clock_so_a_request_reproduces(self) -> None:
        early = request(prepared_at="2026-01-01T00:00:00Z")
        late = request(prepared_at="2030-12-31T23:59:59Z")

        self.assertEqual(early["request_digest"], late["request_digest"])
        self.assertEqual(early["request_id"], late["request_id"])
        self.assertNotIn("prepared_at", REQUEST_DIGEST_KEYS)

    def test_a_request_keeps_one_id_from_prepared_through_accepted(self) -> None:
        prepared = request(review_state="prepared")
        agreed = request(review_state="accepted")

        self.assertEqual(prepared["request_id"], agreed["request_id"])
        self.assertNotIn("review_state", REQUEST_DIGEST_KEYS)

    def test_an_edited_request_fails_its_digest(self) -> None:
        payload = request()
        payload["desired_user_outcome"] = "Something nobody asked for."

        self.assertIn(
            "native_capability_request request_digest does not match the ask it seals; the payload was "
            "edited after it was minted",
            validate_native_capability_request(payload),
        )

    def test_the_request_id_must_follow_its_digest(self) -> None:
        payload = request()
        payload["request_id"] = "native-capability-request-0000000000000000"

        self.assertIn(
            "native_capability_request request_id does not match its request_digest",
            validate_native_capability_request(payload),
        )

    def test_a_caller_mutating_its_citation_cannot_move_a_minted_request(self) -> None:
        supplied = citation()
        payload = build_native_capability_request(**request_kwargs(inspiration_citation=supplied))

        supplied["observer"] = "somebody else"

        self.assertEqual(validate_native_capability_request(payload), [])
        self.assertEqual(payload["inspiration_citation"]["observer"], "user")

    def test_the_module_cannot_fetch_anything(self) -> None:
        # #789's boundary in its enforcing form: a feature URL is recorded, and
        # this module has no way to open one. Derived from the source rather
        # than asserted in prose.
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        absolute: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                absolute.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                absolute.add(str(node.module).split(".")[0])

        self.assertEqual(absolute, {"__future__", "collections", "functools", "hashlib", "json", "re", "typing"})


if __name__ == "__main__":
    unittest.main()
