"""A readiness decision expires, and a changed prerequisite invalidates it.

The defect (#837): `_cached_profile` returned any entry carrying
`observed_once`, with no age check. `updated_at` was written and never read
back, and `force=True` was the only thing that re-probed. So the first readiness
observation on a machine was the last one: a person who uninstalled `codex`,
lost a permission, or moved to another checkout still got `ready`, and the
handoff was dispatched into a gap nobody had been told about.

What these pin:

* AC1 -- prepared-only capability evidence and a stale observation can never
  produce `ready`.
* AC2 -- one test per invalidating axis (profile, tool, permission, workspace);
  each flips a decision that WAS usable to not-usable, naming the axis.
* AC3 -- the repair card has one closed key set for every owner. Codex, Claude
  Code, and the Hermes runtime differ only in owner-specific values.

Guards: a stale decision is never silently re-probed, and `force=True` stays
the only way to replace a stored observation.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()
from omh.coding.executor_capability_snapshots import (  # noqa: E402
    build_executor_capability_snapshot,
    executor_capability_snapshot_path,
    write_executor_capability_snapshot,
)
from omh.coding.executor_readiness import (  # noqa: E402
    _repair_command,
    executor_choice_context,
    live_readiness_binding,
    probe_executor_readiness,
)
from omh.coding.pre_handoff_readiness import (  # noqa: E402
    CAPABILITY_EVIDENCE_STALE_AFTER_SECONDS,
    PRE_HANDOFF_REPAIR_CARD_KEYS,
    PRE_HANDOFF_REPAIR_STEP_IDS,
    PRE_HANDOFF_REPAIR_STEP_KEYS,
    READINESS_BINDING_AXES,
    READINESS_STALE_AFTER_SECONDS,
    PreHandoffRepairCardError,
    build_pre_handoff_repair_card,
    evaluate_pre_handoff_readiness,
    readiness_binding,
    validate_pre_handoff_repair_card,
)
from omh.system.local_store import atomic_write_json  # noqa: E402
from omh.system.paths import OmhPaths  # noqa: E402


OBSERVED_AT = "2026-01-01T00:00:00Z"
WITHIN_WINDOW = "2026-01-01T01:00:00Z"
PAST_WINDOW = "2026-01-01T07:00:00Z"
_UNMATCHED_DIGEST = "0" * 64


def _binding_inputs(**overrides: Any) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "profile": "codex",
        "profile_revision": "Codex CLI\x1esend_to_executor\x1elocal_command",
        "tool_paths": ("codex", "--version", "/usr/local/bin/codex"),
        "permission_revision": "permission-revision-a",
        "workspace_ref": "/repos/alpha",
    }
    inputs.update(overrides)
    return inputs


def _cached_entry(*, updated_at: str, binding: dict[str, Any], status: str = "ready") -> dict[str, Any]:
    return {
        "schema_version": "executor_readiness/v1",
        "profile": "codex",
        "status": status,
        "available": status == "ready",
        "observed_once": True,
        "updated_at": updated_at,
        "readiness_binding": binding,
    }


def _seed_cache(paths: OmhPaths, profile: str, entry: dict[str, Any]) -> None:
    atomic_write_json(
        paths.executor_readiness_path,
        {"schema_version": "executor_readiness_cache/v1", "profiles": {profile: entry}},
        private=True,
    )


def _paths(root: Path) -> OmhPaths:
    return OmhPaths(omh_home=root / ".omh", hermes_home=root / ".hermes")


def _snapshot(capabilities: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "executor_capability_snapshot/v1",
        "executor": "codex",
        "recorded_at": OBSERVED_AT,
        "capabilities": capabilities,
    }


class BindingAxisTests(unittest.TestCase):
    """The digest actually moves when its input moves -- and only that axis."""

    def test_identical_inputs_produce_an_identical_binding(self) -> None:
        first = readiness_binding(**_binding_inputs())
        second = readiness_binding(**_binding_inputs())
        self.assertEqual(first, second)

    def test_the_binding_carries_no_timestamp(self) -> None:
        # A wall clock inside a payload that is compared for equality turns the
        # comparison into a race, which is why expiry is derived at read time
        # from `updated_at` instead of stored in the binding.
        text = json.dumps(readiness_binding(**_binding_inputs()), sort_keys=True)
        self.assertNotIn("2026", text)
        self.assertNotIn("Z\"", text)

    def test_each_input_moves_exactly_its_own_axis(self) -> None:
        base = readiness_binding(**_binding_inputs())
        moved = {
            "profile": _binding_inputs(profile_revision="Codex CLI\x1eshow_prompt_handoff\x1elocal_command"),
            "tool": _binding_inputs(tool_paths=("codex", "--version", "/opt/homebrew/bin/codex")),
            "permission": _binding_inputs(permission_revision="permission-revision-b"),
            "workspace": _binding_inputs(workspace_ref="/repos/beta"),
        }
        self.assertEqual(sorted(moved), sorted(READINESS_BINDING_AXES))
        for axis, inputs in moved.items():
            with self.subTest(axis=axis):
                changed = readiness_binding(**inputs)
                self.assertNotEqual(changed["digest"], base["digest"])
                differing = [name for name in READINESS_BINDING_AXES if changed["axes"][name] != base["axes"][name]]
                self.assertEqual(differing, [axis])


class PreparedAndStaleEvidenceTests(unittest.TestCase):
    """AC1: prepared-only or stale evidence can never produce a ready result."""

    def _fresh_verdict_inputs(self) -> dict[str, Any]:
        binding = readiness_binding(**_binding_inputs())
        return {
            "profile": "codex",
            "cached": _cached_entry(updated_at=OBSERVED_AT, binding=binding),
            "binding": binding,
            "now": WITHIN_WINDOW,
        }

    def test_the_control_case_is_usable(self) -> None:
        # Without this the AC1 cases below could pass for the wrong reason.
        verdict = evaluate_pre_handoff_readiness(**self._fresh_verdict_inputs())
        self.assertTrue(verdict["usable"])
        self.assertEqual(verdict["state"], "fresh")

    def test_prepared_only_capability_evidence_is_never_usable(self) -> None:
        verdict = evaluate_pre_handoff_readiness(
            **self._fresh_verdict_inputs(),
            capability_snapshot=_snapshot(
                {"worktree_isolation": {"status": "prepared"}, "parallel_agents": {"status": "unknown"}}
            ),
        )
        self.assertFalse(verdict["usable"])
        self.assertEqual(verdict["state"], "evidence_prepared_only")
        self.assertEqual(verdict["reason_code"], "capability_evidence_prepared_only")
        self.assertIn("prepared evidence only", verdict["reason"])

    def test_expired_capability_evidence_is_never_usable(self) -> None:
        verdict = evaluate_pre_handoff_readiness(
            **self._fresh_verdict_inputs(),
            capability_snapshot=_snapshot(
                {
                    "worktree_isolation": {
                        "status": "host_observed",
                        "scope": {"mode": "worktree"},
                        "evidence_ref": "local-observation-1",
                        # Older than the 24h window the snapshot validator
                        # already enforces at record time.
                        "observed_at": "2025-12-30T00:00:00Z",
                    }
                }
            ),
        )
        self.assertFalse(verdict["usable"])
        self.assertEqual(verdict["state"], "evidence_expired")
        self.assertGreater(verdict["age_seconds"], CAPABILITY_EVIDENCE_STALE_AFTER_SECONDS)

    def test_host_observed_capability_evidence_inside_the_window_stays_usable(self) -> None:
        verdict = evaluate_pre_handoff_readiness(
            **self._fresh_verdict_inputs(),
            capability_snapshot=_snapshot(
                {
                    "worktree_isolation": {
                        "status": "host_observed",
                        "scope": {"mode": "worktree"},
                        "evidence_ref": "local-observation-1",
                        "observed_at": OBSERVED_AT,
                    }
                }
            ),
        )
        self.assertTrue(verdict["usable"])

    def test_a_stale_observation_never_reads_ready_through_the_probe(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _seed_cache(
                paths,
                "codex",
                _cached_entry(updated_at=OBSERVED_AT, binding=live_readiness_binding(paths, "codex")),
            )
            result = probe_executor_readiness(paths, "codex", now=PAST_WINDOW)
        self.assertNotEqual(result["status"], "ready")
        self.assertEqual(result["status"], "stale")
        self.assertFalse(result["available"])
        self.assertEqual(result["cache_status"], "invalidated")
        self.assertEqual(result["pre_handoff_readiness"]["state"], "expired")
        self.assertGreater(result["pre_handoff_readiness"]["age_seconds"], READINESS_STALE_AFTER_SECONDS)
        self.assertEqual(result["repair_card"]["reason_code"], "readiness_expired")

    def test_a_fresh_bound_observation_still_reads_ready_through_the_probe(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _seed_cache(
                paths,
                "codex",
                _cached_entry(updated_at=OBSERVED_AT, binding=live_readiness_binding(paths, "codex")),
            )
            result = probe_executor_readiness(paths, "codex", now=WITHIN_WINDOW)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["cache_status"], "cached")
        self.assertNotIn("repair_card", result)

    def test_a_prepared_only_snapshot_on_disk_invalidates_the_probe(self) -> None:
        # The record-time window in `executor_capability_snapshots` is applied
        # again at dispatch time: a snapshot that claims capabilities nobody
        # observed cannot carry a ready decision to a handoff.
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _seed_cache(
                paths,
                "codex",
                _cached_entry(updated_at=OBSERVED_AT, binding=live_readiness_binding(paths, "codex")),
            )
            write_executor_capability_snapshot(
                executor_capability_snapshot_path(paths.executor_capability_snapshots_dir, "codex"),
                build_executor_capability_snapshot(
                    executor="codex",
                    capabilities={"worktree_isolation": {"status": "prepared"}},
                    recorded_at=OBSERVED_AT,
                ),
            )
            result = probe_executor_readiness(paths, "codex", now=WITHIN_WINDOW)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["pre_handoff_readiness"]["reason_code"], "capability_evidence_prepared_only")
        self.assertEqual(result["repair_card"]["state"], "evidence_prepared_only")

    def test_a_stale_decision_never_ranks_ready_in_the_choose_executor_card(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _seed_cache(
                paths,
                "codex",
                _cached_entry(updated_at=OBSERVED_AT, binding=live_readiness_binding(paths, "codex")),
            )
            fresh = executor_choice_context(paths, now=WITHIN_WINDOW)
            stale = executor_choice_context(paths, now=PAST_WINDOW)

        by_profile = {entry["profile"]: entry for entry in fresh["candidates"]}
        self.assertEqual(by_profile["codex"]["readiness_status"], "ready")
        by_profile = {entry["profile"]: entry for entry in stale["candidates"]}
        self.assertEqual(by_profile["codex"]["readiness_status"], "stale")
        self.assertEqual(by_profile["codex"]["readiness_freshness"], "readiness_expired")
        # An unprobed candidate is not stale, it is unobserved -- the two read
        # differently so a wrapper does not offer a repair for a first probe.
        self.assertEqual(by_profile["claude-code"]["readiness_status"], "not_observed")
        self.assertEqual(by_profile["claude-code"]["readiness_freshness"], "readiness_never_observed")

    def test_a_legacy_unbound_entry_is_invalidated_rather_than_trusted(self) -> None:
        # Entries written before #837 carry no binding, so no changed
        # prerequisite can be ruled out for them. One forced re-probe rebinds.
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            atomic_write_json(
                paths.executor_readiness_path,
                {
                    "schema_version": "executor_readiness_cache/v1",
                    "profiles": {
                        "codex": {
                            "schema_version": "executor_readiness/v1",
                            "profile": "codex",
                            "status": "ready",
                            "observed_once": True,
                            "updated_at": OBSERVED_AT,
                        }
                    },
                },
                private=True,
            )
            result = probe_executor_readiness(paths, "codex", now=WITHIN_WINDOW)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["pre_handoff_readiness"]["reason_code"], "readiness_unbound")


class ChangedPrerequisiteTests(unittest.TestCase):
    """AC2: one axis per test, each flipping a usable decision to not-usable."""

    def _flip(self, axis: str) -> dict[str, Any]:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            live = live_readiness_binding(paths, "codex")
            _seed_cache(paths, "codex", _cached_entry(updated_at=OBSERVED_AT, binding=live))
            before = probe_executor_readiness(paths, "codex", now=WITHIN_WINDOW)
            self.assertEqual(before["status"], "ready", "the decision must be ready before the axis moves")

            stale_binding = {**live, "axes": {**live["axes"], axis: _UNMATCHED_DIGEST}}
            _seed_cache(paths, "codex", _cached_entry(updated_at=OBSERVED_AT, binding=stale_binding))
            return probe_executor_readiness(paths, "codex", now=WITHIN_WINDOW)

    def _assert_named_invalidation(self, result: dict[str, Any], axis: str) -> None:
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["cache_status"], "invalidated")
        verdict = result["pre_handoff_readiness"]
        self.assertEqual(verdict["state"], "changed")
        self.assertEqual(verdict["reason_code"], "readiness_inputs_changed")
        self.assertEqual(verdict["changed_axes"], [axis])
        self.assertIn(axis, verdict["reason"])
        self.assertEqual(result["repair_card"]["changed_axes"], [axis])
        self.assertEqual(len(result["repair_card"]["missing_prerequisites"]), 1)

    def test_a_changed_profile_identity_invalidates_the_decision(self) -> None:
        self._assert_named_invalidation(self._flip("profile"), "profile")

    def test_a_changed_tool_set_invalidates_the_decision(self) -> None:
        self._assert_named_invalidation(self._flip("tool"), "tool")

    def test_a_changed_permission_profile_invalidates_the_decision(self) -> None:
        self._assert_named_invalidation(self._flip("permission"), "permission")

    def test_a_changed_workspace_invalidates_the_decision(self) -> None:
        self._assert_named_invalidation(self._flip("workspace"), "workspace")

    def test_a_workspace_move_changes_the_live_binding(self) -> None:
        # The end-to-end cases above substitute a digest; this one proves a real
        # workspace change produces a different one, so they are not tautologies.
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            here = live_readiness_binding(paths, "codex", workspace_ref="/repos/alpha")
            elsewhere = live_readiness_binding(paths, "codex", workspace_ref="/repos/beta")
        self.assertNotEqual(here["axes"]["workspace"], elsewhere["axes"]["workspace"])
        self.assertEqual(here["axes"]["tool"], elsewhere["axes"]["tool"])


class RepairCardContractTests(unittest.TestCase):
    """AC3: executor-specific values inside an executor-neutral key set."""

    _OWNER_SPECIFIC_FIELDS = frozenset({"profile", "label", "reason", "repair_steps", "verify_command"})

    def _card(self, profile: str) -> dict[str, Any]:
        binding = readiness_binding(**_binding_inputs(profile=profile))
        verdict = evaluate_pre_handoff_readiness(
            profile=profile,
            cached=_cached_entry(updated_at=OBSERVED_AT, binding=binding),
            binding=binding,
            now=PAST_WINDOW,
        )
        self.assertEqual(verdict["state"], "expired")
        return build_pre_handoff_repair_card(verdict, repair_command=_repair_command(profile))

    def test_every_owner_gets_the_same_closed_key_set(self) -> None:
        for profile in ("codex", "claude-code", "hermes", "generic", "omo-runtime"):
            with self.subTest(profile=profile):
                card = self._card(profile)
                self.assertEqual(sorted(card), sorted(PRE_HANDOFF_REPAIR_CARD_KEYS))
                self.assertEqual([step["id"] for step in card["repair_steps"]], list(PRE_HANDOFF_REPAIR_STEP_IDS))
                for step in card["repair_steps"]:
                    self.assertEqual(sorted(step), sorted(PRE_HANDOFF_REPAIR_STEP_KEYS))
                self.assertEqual(validate_pre_handoff_repair_card(card), [])

    def test_two_owners_differ_only_in_owner_specific_values(self) -> None:
        codex = self._card("codex")
        claude = self._card("claude-code")
        differing = {key for key in codex if codex[key] != claude[key]}
        self.assertEqual(differing, self._OWNER_SPECIFIC_FIELDS)
        # The shared half is the contract: reason code, blocked action, state,
        # and the named gap read identically for both owners.
        self.assertEqual(codex["reason_code"], claude["reason_code"])
        self.assertEqual(codex["missing_prerequisites"], claude["missing_prerequisites"])
        self.assertEqual(codex["blocked_action"], claude["blocked_action"])

    def test_the_hermes_runtime_is_a_first_class_owner(self) -> None:
        # A wrapper-observed profile has no CLI to confirm, so its owner-specific
        # command is the local check that covers it -- same field, same shape.
        hermes = self._card("hermes")
        codex = self._card("codex")
        self.assertEqual(sorted(hermes), sorted(codex))
        self.assertEqual(hermes["repair_steps"][0]["command"], "omh doctor")
        self.assertEqual(codex["repair_steps"][0]["command"], "codex --version")

    def test_the_card_never_claims_the_repair_ran(self) -> None:
        card = self._card("codex")
        self.assertEqual(card["status"], "prepared_not_observed")
        self.assertEqual(card["blocked_action"], "coding_handoff_dispatch")
        self.assertIn("not a repair", card["claim_boundary"])

    def test_a_fresh_verdict_has_no_card_to_build(self) -> None:
        binding = readiness_binding(**_binding_inputs())
        verdict = evaluate_pre_handoff_readiness(
            profile="codex",
            cached=_cached_entry(updated_at=OBSERVED_AT, binding=binding),
            binding=binding,
            now=WITHIN_WINDOW,
        )
        with self.assertRaises(PreHandoffRepairCardError):
            build_pre_handoff_repair_card(verdict, repair_command="codex --version")


class RepairCardValidatorTests(unittest.TestCase):
    """The validator checks both directions: nothing missing, nothing extra."""

    def _valid(self) -> dict[str, Any]:
        binding = readiness_binding(**_binding_inputs())
        verdict = evaluate_pre_handoff_readiness(
            profile="codex",
            cached=_cached_entry(updated_at=OBSERVED_AT, binding=binding),
            binding=binding,
            now=PAST_WINDOW,
        )
        return build_pre_handoff_repair_card(verdict, repair_command="codex --version")

    def test_a_built_card_validates(self) -> None:
        self.assertEqual(validate_pre_handoff_repair_card(self._valid()), [])

    def test_a_missing_key_is_reported(self) -> None:
        card = self._valid()
        del card["verify_command"]
        errors = validate_pre_handoff_repair_card(card)
        self.assertTrue(any("missing required keys: verify_command" in error for error in errors))

    def test_an_extra_key_is_reported(self) -> None:
        card = self._valid()
        card["executor_note"] = "codex only"
        errors = validate_pre_handoff_repair_card(card)
        self.assertTrue(any("unsupported keys: executor_note" in error for error in errors))

    def test_a_claimed_repair_is_reported(self) -> None:
        card = self._valid()
        card["status"] = "observed"
        errors = validate_pre_handoff_repair_card(card)
        self.assertTrue(any("never claims a repair ran" in error for error in errors))

    def test_an_unsupported_axis_is_reported(self) -> None:
        card = self._valid()
        card["changed_axes"] = ["network"]
        errors = validate_pre_handoff_repair_card(card)
        self.assertTrue(any("unsupported axes: network" in error for error in errors))

    def test_a_reason_code_that_contradicts_the_state_is_reported(self) -> None:
        card = self._valid()
        card["reason_code"] = "readiness_fresh"
        errors = validate_pre_handoff_repair_card(card)
        self.assertTrue(any("reason_code must match the state" in error for error in errors))

    def test_a_dropped_repair_step_is_reported(self) -> None:
        card = self._valid()
        card["repair_steps"] = card["repair_steps"][:1]
        errors = validate_pre_handoff_repair_card(card)
        self.assertTrue(any("repair_steps must be exactly" in error for error in errors))


class ReProbeGuardTests(unittest.TestCase):
    """`force=True` stays the only thing that replaces a stored observation."""

    def test_a_stale_decision_is_not_silently_refreshed(self) -> None:
        def _forbidden(_contract: dict[str, object]) -> dict[str, object]:
            raise AssertionError("a stale decision must not re-probe without force")

        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _seed_cache(
                paths,
                "codex",
                _cached_entry(updated_at=OBSERVED_AT, binding=live_readiness_binding(paths, "codex")),
            )
            before = paths.executor_readiness_path.read_bytes()
            with patch("omh.coding.executor_readiness._run_probe", _forbidden):
                result = probe_executor_readiness(paths, "codex", now=PAST_WINDOW)
            after = paths.executor_readiness_path.read_bytes()

        self.assertEqual(result["status"], "stale")
        self.assertEqual(before, after, "an invalidated read must not rewrite the stored decision")

    def test_force_is_what_replaces_a_stale_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _seed_cache(
                paths,
                "codex",
                _cached_entry(updated_at=OBSERVED_AT, binding=live_readiness_binding(paths, "codex")),
            )
            # omx-runtime resolves no local binary in the test environment, so
            # the forced probe stays a PATH lookup with no subprocess.
            forced = probe_executor_readiness(paths, "omx-runtime", force=True)
            stored = json.loads(paths.executor_readiness_path.read_text(encoding="utf-8"))

        self.assertNotIn("repair_card", forced)
        entry = stored["profiles"]["omx-runtime"]
        self.assertTrue(entry["observed_once"])
        self.assertTrue(entry["updated_at"])
        self.assertEqual(sorted(entry["readiness_binding"]["axes"]), sorted(READINESS_BINDING_AXES))

    def test_a_forced_probe_rebinds_a_legacy_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            probe_executor_readiness(paths, "omx-runtime", force=True)
            rebound = probe_executor_readiness(paths, "omx-runtime")
        self.assertEqual(rebound["cache_status"], "cached")
        self.assertEqual(rebound["pre_handoff_readiness"]["state"], "fresh")


if __name__ == "__main__":
    unittest.main()
