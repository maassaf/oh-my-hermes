from __future__ import annotations

from copy import deepcopy
import json
import unittest

from _local_package import load_local_package


load_local_package()
from omh.coding.executor_capability_snapshots import build_executor_capability_snapshot
from omh.coding.executor_guidance_compatibility import (
    EXECUTOR_GUIDANCE_COMPATIBILITY_SCHEMA_VERSION,
    GUIDANCE_COMPATIBILITY_AXES,
    GUIDANCE_COMPATIBILITY_STATES,
    HOST_SPECIFIC_VOCABULARY,
    OBSERVED_AVAILABILITY_STATES,
    SUPPORTED_GUIDANCE_OWNERS,
    ExecutorGuidanceCompatibilityError,
    build_executor_guidance_compatibility,
    guidance_leakage_findings,
    reads_as_observed_availability,
    validate_executor_guidance_compatibility,
)
from omh.coding.executors import public_executor_options
from omh.coding.prompting import build_executor_prompting_contract, render_executor_prompt_sections


GUIDANCE_REF = "executor_prompting_contract/v1"

NEUTRAL_GUIDANCE = (
    "Goal\n"
    "- Deliver the requested bounded change.\n\n"
    "Test\n"
    "- Run the smallest relevant checks after editing.\n\n"
    "Task:\n"
    "{message}\n"
)

LEAKY_GUIDANCE = (
    "Goal\n"
    "- Deliver the requested bounded change.\n\n"
    "Test\n"
    "- Track every step with TodoWrite before editing.\n"
    "- Read ~/.claude/skills/reviewer/SKILL.md for the review lane.\n"
    "- Send edits through apply_patch.\n"
    "- Keep .cursor/rules/main.mdc in sync.\n\n"
    "Task:\n"
    "{message}\n"
)

DECLARED_SECTIONS = ("Goal", "Test", "Task")


def _rendered_executor_guidance() -> str:
    """The real OMH-owned executor prompt body, not a hand-written stand-in."""
    contract = build_executor_prompting_contract(
        "generic",
        intent="implement",
        message="add a bounded flag to the exporter",
        has_plan_artifact=False,
    )
    return render_executor_prompt_sections(
        contract,
        recommended_workflow="ultrawork",
        recommended_harness="python-unittest",
        acceptance_criteria=("the flag is accepted and defaulted",),
        verification=("PYTHONPATH=tests python -m unittest tests/test_cli.py",),
        review_required=True,
    )


def _claude_code_snapshot() -> dict[str, object]:
    return build_executor_capability_snapshot(
        executor="claude-code",
        capabilities={
            "parallel_agents": {
                "status": "host_observed",
                "scope": {"mode": "subagents"},
                "evidence_ref": "wrapper/session/abc123",
                "observed_at": "2026-01-01T00:00:00+00:00",
            },
            "background_work": {"status": "unknown"},
        },
        recorded_at="2026-01-01T01:00:00+00:00",
    )


def _row(payload: dict[str, object], owner: str) -> dict[str, object]:
    rows = [entry for entry in payload["owners"] if entry["owner"] == owner]
    if not rows:
        raise AssertionError(f"owner row missing: {owner}")
    return rows[0]


class HostSpecificLeakageTests(unittest.TestCase):
    """AC1: host-specific leakage is detected before handoff."""

    def test_leaked_host_mechanics_are_named_with_the_host_they_belong_to(self) -> None:
        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=LEAKY_GUIDANCE,
            required_sections=DECLARED_SECTIONS,
        )

        self.assertEqual(payload["leakage"]["status"], "detected")
        findings = {finding["token"]: finding for finding in payload["leakage"]["findings"]}
        self.assertEqual(set(findings), {"TodoWrite", ".claude/skills", "apply_patch", ".cursor/rules"})
        self.assertEqual(findings["TodoWrite"]["host"], "claude-code")
        self.assertEqual(findings[".claude/skills"]["host"], "claude-code")
        self.assertEqual(findings["apply_patch"]["host"], "codex")
        self.assertEqual(findings[".cursor/rules"]["host"], "cursor")
        self.assertEqual(findings["apply_patch"]["mechanic"], "Codex patch envelope")
        self.assertEqual(payload["leakage"]["finding_count"], 4)
        self.assertEqual(payload["summary"]["leakage_finding_count"], 4)

        # Every owner, including the hosts that own some of the tokens, is told
        # the guidance is not executor-neutral. `cursor` is not a supported
        # owner, so its token is foreign to all seven.
        for owner in SUPPORTED_GUIDANCE_OWNERS:
            with self.subTest(owner=owner):
                axis = _row(payload, owner)["axes"]["semantic_equivalence"]
                self.assertEqual(axis["state"], "unsupported")
                self.assertIn(".cursor/rules", axis["unmet"])

    def test_leakage_flags_the_owning_host_even_when_that_host_is_the_audited_owner(self) -> None:
        claude_only = "Goal\n- Track every step with TodoWrite.\n\nTest\n- none\n\nTask:\n{message}\n"

        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=claude_only,
            required_sections=DECLARED_SECTIONS,
        )

        claude_axis = _row(payload, "claude-code")["axes"]["semantic_equivalence"]
        codex_axis = _row(payload, "codex")["axes"]["semantic_equivalence"]
        # Claude Code resolves its own tool name, so the instruction still means
        # what OMH meant there -- but the guidance is no longer neutral.
        self.assertEqual(claude_axis["state"], "partial")
        self.assertEqual(claude_axis["checked"], ["TodoWrite"])
        self.assertEqual(claude_axis["unmet"], [])
        self.assertEqual(codex_axis["state"], "unsupported")
        self.assertEqual(codex_axis["unmet"], ["TodoWrite"])

    def test_executor_neutral_guidance_is_not_flagged(self) -> None:
        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=_rendered_executor_guidance(),
        )

        self.assertEqual(payload["leakage"]["status"], "clean")
        self.assertEqual(payload["leakage"]["findings"], [])
        self.assertEqual(payload["summary"]["leakage_finding_count"], 0)
        for owner in SUPPORTED_GUIDANCE_OWNERS:
            with self.subTest(owner=owner):
                row = _row(payload, owner)
                self.assertEqual(row["axes"]["semantic_equivalence"]["state"], "available")
                self.assertEqual(row["axes"]["syntax"]["state"], "available")
                self.assertEqual(row["state"], "available")
        self.assertEqual(payload["summary"]["fallback_required_count"], 0)

    def test_near_miss_wording_does_not_count_as_leakage(self) -> None:
        # Detection is exact token membership against the declared vocabulary,
        # so prose that merely resembles a host mechanic stays clean.
        near_miss = (
            "Goal\n"
            "- Keep a todo list and apply the patch through the project's own tooling.\n"
            "- Move the cursor to the failing line and read the claude notes.\n\n"
            "Test\n"
            "- none\n\n"
            "Task:\n"
            "{message}\n"
        )

        self.assertEqual(guidance_leakage_findings(near_miss), ())
        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=near_miss,
            required_sections=DECLARED_SECTIONS,
        )
        self.assertEqual(payload["leakage"]["status"], "clean")

    def test_declared_vocabulary_is_the_only_detector(self) -> None:
        for entry in HOST_SPECIFIC_VOCABULARY:
            with self.subTest(token=entry.token):
                text = f"Goal\n- Use {entry.token} here.\n\nTest\n- none\n\nTask:\n{{message}}\n"
                self.assertEqual(
                    [finding.token for finding in guidance_leakage_findings(text)], [entry.token]
                )
        self.assertEqual(
            len({entry.token for entry in HOST_SPECIFIC_VOCABULARY}), len(HOST_SPECIFIC_VOCABULARY)
        )


class OwnerCoverageTests(unittest.TestCase):
    """AC2: every supported owner receives a compatibility state and a fallback."""

    def test_supported_owners_are_the_published_coding_owners(self) -> None:
        published = sorted(str(option["profile"]) for option in public_executor_options())
        self.assertEqual(list(SUPPORTED_GUIDANCE_OWNERS), published)

    def test_every_supported_owner_receives_a_state_and_a_fallback(self) -> None:
        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=LEAKY_GUIDANCE,
            required_sections=DECLARED_SECTIONS,
            required_capabilities=("parallel_agents",),
            required_observations=("worker_dispatch", "review"),
            capability_snapshots={"claude-code": _claude_code_snapshot()},
        )

        self.assertEqual(
            set(payload),
            {
                "schema_version",
                "guidance_ref",
                "axes",
                "leakage",
                "owners",
                "summary",
                "not_observed",
                "claim_boundary",
            },
        )
        self.assertEqual(payload["schema_version"], EXECUTOR_GUIDANCE_COMPATIBILITY_SCHEMA_VERSION)
        self.assertEqual(payload["axes"], list(GUIDANCE_COMPATIBILITY_AXES))
        self.assertEqual([row["owner"] for row in payload["owners"]], list(SUPPORTED_GUIDANCE_OWNERS))
        self.assertEqual(payload["summary"]["owner_count"], len(SUPPORTED_GUIDANCE_OWNERS))

        for owner in SUPPORTED_GUIDANCE_OWNERS:
            with self.subTest(owner=owner):
                row = _row(payload, owner)
                self.assertIn(row["state"], GUIDANCE_COMPATIBILITY_STATES)
                self.assertEqual(set(row["axes"]), set(GUIDANCE_COMPATIBILITY_AXES))
                fallback = row["fallback"]
                self.assertTrue(fallback["required"])
                self.assertEqual(fallback["kind"], "portable_prompt_handoff")
                self.assertEqual(fallback["schema_version"], "coding_prompt_handoff/v1")
                self.assertEqual(fallback["portable_profile"], "generic")
                self.assertIn("{message}", fallback["portable_form"])
                self.assertTrue(fallback["reason"].strip())
        self.assertEqual(validate_executor_guidance_compatibility(payload), [])

    def test_a_record_missing_an_owner_fails_validation(self) -> None:
        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=NEUTRAL_GUIDANCE,
            required_sections=DECLARED_SECTIONS,
        )

        truncated = deepcopy(payload)
        truncated["owners"] = [row for row in truncated["owners"] if row["owner"] != "hermes"]
        errors = validate_executor_guidance_compatibility(truncated)
        self.assertTrue(
            any("missing: hermes" in error for error in errors),
            errors,
        )

        widened = deepcopy(payload)
        extra = deepcopy(_row(payload, "generic"))
        extra["owner"] = "not-a-coding-owner"
        widened["owners"] = sorted([*widened["owners"], extra], key=lambda row: row["owner"])
        self.assertTrue(
            any("unsupported coding owners: not-a-coding-owner" in error for error in validate_executor_guidance_compatibility(widened))
        )

    def test_a_row_without_a_fallback_fails_validation(self) -> None:
        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=NEUTRAL_GUIDANCE,
            required_sections=DECLARED_SECTIONS,
        )

        stripped = deepcopy(payload)
        del _row(stripped, "codex")["fallback"]
        errors = validate_executor_guidance_compatibility(stripped)
        self.assertTrue(any("owners.codex is missing required fields: fallback" in error for error in errors), errors)


class UnknownCapabilityTests(unittest.TestCase):
    """AC3: unknown capability cannot be presented as observed availability."""

    def test_exactly_one_state_in_the_vocabulary_reads_as_observed_availability(self) -> None:
        observed = {state for state in GUIDANCE_COMPATIBILITY_STATES if reads_as_observed_availability(state)}
        self.assertEqual(observed, {"available"})
        self.assertEqual(OBSERVED_AVAILABILITY_STATES, frozenset({"available"}))
        self.assertIn("unknown", GUIDANCE_COMPATIBILITY_STATES)
        for state in GUIDANCE_COMPATIBILITY_STATES:
            if state != "available":
                with self.subTest(state=state):
                    self.assertFalse(reads_as_observed_availability(state))

    def test_unknown_capability_never_yields_an_available_state(self) -> None:
        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=NEUTRAL_GUIDANCE,
            required_sections=DECLARED_SECTIONS,
            required_capabilities=("parallel_agents", "background_work"),
            capability_snapshots={"claude-code": _claude_code_snapshot()},
        )

        # `parallel_agents` is host_observed and `background_work` is unknown,
        # so the axis -- and therefore the row -- cannot read as available.
        claude = _row(payload, "claude-code")
        self.assertEqual(claude["axes"]["capability"]["state"], "unknown")
        self.assertEqual(claude["axes"]["capability"]["unmet"], ["background_work"])
        self.assertFalse(reads_as_observed_availability(claude["state"]))
        self.assertTrue(claude["fallback"]["required"])

        # An owner with no snapshot at all is unknown, never unsupported and
        # never available: absent evidence is not a negative observation.
        codex = _row(payload, "codex")
        self.assertEqual(codex["axes"]["capability"]["state"], "unknown")
        self.assertEqual(
            codex["axes"]["capability"]["unmet"], ["background_work", "parallel_agents"]
        )
        self.assertFalse(reads_as_observed_availability(codex["state"]))
        self.assertEqual(payload["summary"]["state_counts"]["available"], 0)

    def test_a_record_claiming_availability_over_an_unknown_axis_fails_validation(self) -> None:
        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=NEUTRAL_GUIDANCE,
            required_sections=DECLARED_SECTIONS,
            required_capabilities=("background_work",),
            capability_snapshots={"claude-code": _claude_code_snapshot()},
        )
        self.assertEqual(_row(payload, "claude-code")["axes"]["capability"]["state"], "unknown")

        forged = deepcopy(payload)
        row = _row(forged, "claude-code")
        row["state"] = "available"
        row["fallback"]["required"] = False
        forged["summary"]["state_counts"]["available"] = 1
        forged["summary"]["state_counts"]["unknown"] -= 1
        forged["summary"]["fallback_required_count"] -= 1

        errors = validate_executor_guidance_compatibility(forged)
        self.assertTrue(
            any(
                "owners.claude-code.state must not read as observed availability while an axis does not" in error
                for error in errors
            ),
            errors,
        )

    def test_unobservable_lifecycle_evidence_is_unknown_not_supported(self) -> None:
        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=NEUTRAL_GUIDANCE,
            required_sections=DECLARED_SECTIONS,
            required_observations=("worker_dispatch",),
        )

        # A prompt-only owner has no channel OMH can observe, so the audit says
        # unknown -- it does not claim the owner cannot report, and it does not
        # claim the evidence will appear.
        prompt_only = _row(payload, "generic")
        self.assertEqual(prompt_only["observation_channel"], "operator_reported")
        self.assertEqual(prompt_only["axes"]["observation_support"]["state"], "unknown")
        self.assertFalse(reads_as_observed_availability(prompt_only["state"]))

        runtime = _row(payload, "hermes")
        self.assertEqual(runtime["observation_channel"], "runtime_observation_ledger")
        self.assertEqual(runtime["axes"]["observation_support"]["state"], "available")


class OwnerDifferentialTests(unittest.TestCase):
    def test_the_same_guidance_differs_only_in_owner_specific_fields(self) -> None:
        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text="Goal\n- Track every step with TodoWrite.\n\nTest\n- none\n\nTask:\n{message}\n",
            required_sections=DECLARED_SECTIONS,
            required_capabilities=("parallel_agents",),
            required_observations=("worker_dispatch",),
            capability_snapshots={"claude-code": _claude_code_snapshot()},
        )

        # Both owners take a pasted prompt and report through the operator, so
        # every difference between their rows must be owner-specific.
        claude = _row(payload, "claude-code")
        generic = _row(payload, "generic")
        self.assertEqual(set(claude), set(generic))
        self.assertEqual(claude["guidance_form"], generic["guidance_form"])
        self.assertEqual(claude["observation_channel"], generic["observation_channel"])

        differing = {key for key in claude if claude[key] != generic[key]}
        self.assertEqual(differing, {"owner", "label", "state", "axes", "fallback"})

        differing_axes = {
            name for name in GUIDANCE_COMPATIBILITY_AXES if claude["axes"][name] != generic["axes"][name]
        }
        self.assertEqual(differing_axes, {"capability", "semantic_equivalence"})
        self.assertEqual(claude["axes"]["syntax"], generic["axes"]["syntax"])
        self.assertEqual(claude["axes"]["observation_support"], generic["axes"]["observation_support"])
        self.assertEqual(claude["axes"]["capability"]["state"], "available")
        self.assertEqual(generic["axes"]["capability"]["state"], "unknown")
        self.assertEqual(claude["axes"]["semantic_equivalence"]["state"], "partial")
        self.assertEqual(generic["axes"]["semantic_equivalence"]["state"], "unsupported")


class RecordContractTests(unittest.TestCase):
    def test_the_key_set_is_closed_in_both_directions(self) -> None:
        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=NEUTRAL_GUIDANCE,
            required_sections=DECLARED_SECTIONS,
        )
        self.assertEqual(validate_executor_guidance_compatibility(payload), [])

        widened = deepcopy(payload)
        widened["extra_field"] = "no"
        self.assertTrue(
            any("audit contains unsupported fields: extra_field" in error for error in validate_executor_guidance_compatibility(widened))
        )

        narrowed = deepcopy(payload)
        del narrowed["claim_boundary"]
        self.assertTrue(
            any("audit is missing required fields: claim_boundary" in error for error in validate_executor_guidance_compatibility(narrowed))
        )

        axis_widened = deepcopy(payload)
        _row(axis_widened, "codex")["axes"]["syntax"]["note"] = "no"
        self.assertTrue(
            any("owners.codex.axes.syntax contains unsupported fields: note" in error for error in validate_executor_guidance_compatibility(axis_widened))
        )

    def test_the_payload_is_deterministic_and_carries_no_guidance_body(self) -> None:
        first = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=LEAKY_GUIDANCE,
            required_sections=DECLARED_SECTIONS,
        )
        second = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text=LEAKY_GUIDANCE,
            required_sections=DECLARED_SECTIONS,
        )
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

        serialized = json.dumps(first, sort_keys=True)
        self.assertNotIn("Deliver the requested bounded change", serialized)
        self.assertNotIn("reviewer/SKILL.md", serialized)
        for forbidden in ("observed_at", "recorded_at", "generated_at", "guidance_text"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(f'"{forbidden}"', serialized)
        self.assertEqual(
            {name: entry["status"] for name, entry in first["not_observed"].items()},
            {
                "owner_capability_probe": "not_observed",
                "guidance_delivery": "not_observed",
                "owner_dispatch": "not_observed",
                "owner_execution": "not_observed",
                "evidence_report": "not_observed",
            },
        )

    def test_a_missing_declared_section_is_a_syntax_failure_for_every_owner(self) -> None:
        payload = build_executor_guidance_compatibility(
            guidance_ref=GUIDANCE_REF,
            guidance_text="Goal\n- Deliver the change.\n\nTask:\n{message}\n",
            required_sections=DECLARED_SECTIONS,
        )

        for owner in SUPPORTED_GUIDANCE_OWNERS:
            with self.subTest(owner=owner):
                axis = _row(payload, owner)["axes"]["syntax"]
                self.assertEqual(axis["state"], "unsupported")
                self.assertEqual(axis["unmet"], ["Test"])
        self.assertEqual(payload["summary"]["fallback_required_count"], len(SUPPORTED_GUIDANCE_OWNERS))

    def test_unsafe_or_unbounded_inputs_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "safe opaque metadata reference"):
            build_executor_guidance_compatibility(guidance_ref="api_key/v1", guidance_text=NEUTRAL_GUIDANCE)
        with self.assertRaisesRegex(ValueError, "nonempty string"):
            build_executor_guidance_compatibility(guidance_ref=GUIDANCE_REF, guidance_text="   ")
        with self.assertRaisesRegex(ValueError, "exceeds 131072 bytes"):
            build_executor_guidance_compatibility(guidance_ref=GUIDANCE_REF, guidance_text="x" * 131_073)
        with self.assertRaisesRegex(ValueError, "unsupported required capability name"):
            build_executor_guidance_compatibility(
                guidance_ref=GUIDANCE_REF,
                guidance_text=NEUTRAL_GUIDANCE,
                required_capabilities=("teleportation",),
            )
        with self.assertRaisesRegex(ValueError, "unsupported required observation"):
            build_executor_guidance_compatibility(
                guidance_ref=GUIDANCE_REF,
                guidance_text=NEUTRAL_GUIDANCE,
                required_observations=("vibes",),
            )
        with self.assertRaisesRegex(ExecutorGuidanceCompatibilityError, "unsupported capability snapshot owner"):
            build_executor_guidance_compatibility(
                guidance_ref=GUIDANCE_REF,
                guidance_text=NEUTRAL_GUIDANCE,
                capability_snapshots={"cursor": _claude_code_snapshot()},
            )


if __name__ == "__main__":
    unittest.main()
