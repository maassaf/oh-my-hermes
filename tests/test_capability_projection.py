"""Contract tests for the task-scoped capability projection (issue #814).

Three acceptance criteria drive this file, and each one is locked at the level
it can actually fail at:

- AC1 is arithmetic. A projection either fits the declared budget or degrades
  through the same path the runtime observe surfaces use, and the degraded
  payload names every capability it dropped with the byte numbers that caused
  it.
- AC2 is structural, not behavioural. A projection is a VIEW; the test that
  matters is not "this refresh happened to keep the grant" but "there is no
  seam through which a refresh could change it" -- a frozen grant, one
  constructor, and a refresh entry point with no authority parameter at all.
- AC3 is about what the payload does NOT contain. Naming ninety irrelevant
  workflows to explain their absence is the inventory dump the feature exists
  to remove, so exclusions are itemized only for what the router considered and
  counted for everything else.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.capabilities import projection as projection_module
from omh.capabilities.projection import (
    CAPABILITY_AUTHORITY_SCHEMA_VERSION,
    CAPABILITY_EXPANSION_SCHEMA_VERSION,
    CAPABILITY_PROJECTION_SCHEMA_VERSION,
    CONSIDERED_LIMIT,
    EXCLUSION_REASON_CODES,
    CapabilityAuthority,
    CapabilityProjectionError,
    approve_capability_authority,
    authority_change_report,
    capability_exclusion,
    expand_capability,
    project_capabilities,
    refresh_capability_projection,
)
from omh.capabilities.registry import capability_summary
from omh.paths import resolve_paths
from omh.runtime.context_budget import (
    CAPABILITY_PROJECTION_SUMMARY_ONLY_SCHEMA_VERSION,
    CAPABILITY_PROJECTION_SURFACE,
    RUN_CONTEXT_BUDGET_SCHEMA_VERSION,
    context_budget_ledger_path,
    record_context_emission,
    run_context_budget,
)
from omh.skills.catalog import installable_skill_names

CODING_REQUEST = "prepare a coding handoff for this issue"
PROJECTION_SOURCE = Path(projection_module.__file__)


def _budget(remaining: int, *, total: int = 200_000, task_id: str = "task-1") -> dict[str, object]:
    """An `omh_run_context_budget/v1` payload with a chosen remaining allowance.

    Handed in as a parameter exactly like a clock reading would be, so these
    tests never depend on a filesystem ledger to reach a budget state.
    """
    emitted = max(0, total - remaining)
    return {
        "schema_version": RUN_CONTEXT_BUDGET_SCHEMA_VERSION,
        "run_id": task_id,
        "surface": CAPABILITY_PROJECTION_SURFACE,
        "budget_bytes": total,
        "emitted_bytes": emitted,
        "remaining_bytes": max(0, remaining),
        "observe_call_count": 0,
        "surfaces": {},
        "last_payload_fingerprint": "",
        "exhausted": remaining <= 0,
        "enforcement": "degrade_to_summary_only_with_artifact_pointers",
        "policy": "timed_polling_rejected; raw_log_dumping_rejected",
    }


def _full_authority(task_id: str = "task-1") -> CapabilityAuthority:
    return approve_capability_authority(
        task_id=task_id,
        granted_capabilities=sorted(installable_skill_names()),
    )


def _function_calls(source: Path, callee: str) -> set[str]:
    """Names of the module-level functions that call `callee`."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    callers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == callee:
                callers.add(node.name)
    return callers


class BudgetedProjectionTests(unittest.TestCase):
    """AC1: context and handoffs stay inside declared budgets."""

    def test_a_projection_stays_inside_the_declared_budget(self) -> None:
        projection = project_capabilities(CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000))

        payload = projection.to_dict()
        self.assertEqual(payload["schema_version"], CAPABILITY_PROJECTION_SCHEMA_VERSION)
        self.assertFalse(payload["degraded"])
        self.assertNotIn("budget_drop", payload)
        self.assertLessEqual(payload["projected_bytes"], payload["context_budget"]["remaining_bytes"])
        self.assertEqual(payload["context_budget"]["schema_version"], RUN_CONTEXT_BUDGET_SCHEMA_VERSION)
        # Internal bookkeeping stays internal, the same way `public_budget`
        # keeps it out of every other budgeted surface.
        self.assertNotIn("last_payload_fingerprint", payload["context_budget"])

    def test_a_task_projection_is_a_fraction_of_the_whole_catalog_summary(self) -> None:
        projection = project_capabilities(CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000))

        projected = len(json.dumps(projection.to_dict(), sort_keys=True, separators=(",", ":")))
        whole_catalog = len(json.dumps(capability_summary(), sort_keys=True, separators=(",", ":")))
        self.assertLess(projected * 4, whole_catalog)

    def test_a_trimmed_projection_names_every_capability_it_dropped(self) -> None:
        roomy = project_capabilities(CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000))
        tight = project_capabilities(CODING_REQUEST, authority=_full_authority(), budget=_budget(3_000))

        payload = tight.to_dict()
        self.assertFalse(payload["degraded"])
        self.assertLess(payload["included_count"], roomy.to_dict()["included_count"])
        self.assertLessEqual(payload["projected_bytes"], 3_000)

        drop = payload["budget_drop"]
        dropped = set(roomy.included_capabilities()) - set(tight.included_capabilities())
        self.assertEqual(set(drop["dropped_capabilities"]), dropped)
        self.assertEqual(drop["dropped_count"], len(dropped))
        self.assertEqual(drop["remaining_bytes"], 3_000)
        self.assertEqual(drop["budget_bytes"], 200_000)

        budget_exclusions = {
            entry["capability"]
            for entry in payload["exclusions"]
            if entry["reason_code"] == "beyond_context_budget"
        }
        self.assertEqual(budget_exclusions, dropped)

    def test_an_over_budget_projection_degrades_through_the_existing_path(self) -> None:
        roomy = project_capabilities(CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000))
        starved = project_capabilities(CODING_REQUEST, authority=_full_authority(), budget=_budget(10))

        payload = starved.to_dict()
        self.assertEqual(payload["schema_version"], CAPABILITY_PROJECTION_SUMMARY_ONLY_SCHEMA_VERSION)
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["degraded_reason"], "capability_projection_context_budget_exhausted")
        self.assertEqual(payload["included"], [])
        self.assertEqual(payload["included_count"], 0)

        drop = payload["budget_drop"]
        self.assertEqual(set(drop["dropped_capabilities"]), set(roomy.included_capabilities()))
        self.assertEqual(drop["dropped_count"], len(roomy.included_capabilities()))
        self.assertGreater(drop["projected_bytes"], drop["remaining_bytes"])
        self.assertEqual(drop["remaining_bytes"], 10)
        self.assertEqual(drop["budget_bytes"], 200_000)
        self.assertIn("nothing was silently withheld", payload["claim_boundary"])
        self.assertEqual(
            {entry["reason_code"] for entry in payload["exclusions"]},
            {"beyond_context_budget"},
        )

    def test_a_degraded_projection_still_reports_the_unchanged_authority(self) -> None:
        authority = _full_authority()
        starved = project_capabilities(CODING_REQUEST, authority=authority, budget=_budget(10))

        self.assertEqual(starved.to_dict()["authority"]["digest"], authority.digest)

    def test_the_cli_spends_the_task_budget_through_the_shared_ledger(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            status, stdout, stderr = run_cli([*base, "capabilities", "project", CODING_REQUEST, "--task-id", "task-a"])

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["schema_version"], CAPABILITY_PROJECTION_SCHEMA_VERSION)
            self.assertTrue(context_budget_ledger_path(paths).exists())

            spent = run_context_budget(paths, "task-a", surface=CAPABILITY_PROJECTION_SURFACE)
            self.assertGreater(spent["emitted_bytes"], 0)
            self.assertEqual(spent["surfaces"], {CAPABILITY_PROJECTION_SURFACE: 1})
            self.assertEqual(run_context_budget(paths, "task-b")["emitted_bytes"], 0)

    def test_an_exhausted_task_budget_degrades_the_cli_projection(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            record_context_emission(
                paths,
                "task-a",
                surface=CAPABILITY_PROJECTION_SURFACE,
                byte_count=run_context_budget(paths, "task-a")["budget_bytes"],
            )

            status, stdout, _ = run_cli([*base, "capabilities", "project", CODING_REQUEST, "--task-id", "task-a"])

            self.assertEqual(status, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["schema_version"], CAPABILITY_PROJECTION_SUMMARY_ONLY_SCHEMA_VERSION)
            self.assertTrue(payload["degraded"])
            self.assertGreater(payload["budget_drop"]["dropped_count"], 0)


class ApprovedAuthorityTests(unittest.TestCase):
    """AC2: a refresh cannot silently alter approved authority."""

    def _narrow_grant(self) -> tuple[CapabilityAuthority, list[str], list[str]]:
        offered = sorted(installable_skill_names())
        granted = offered[:20]
        return approve_capability_authority(task_id="task-1", granted_capabilities=granted), granted, offered

    def test_refreshing_with_a_larger_catalog_leaves_the_grant_untouched(self) -> None:
        authority, granted, offered = self._narrow_grant()
        original = project_capabilities(
            CODING_REQUEST, authority=authority, budget=_budget(200_000), offered_capabilities=granted
        )

        refreshed = refresh_capability_projection(
            original, budget=_budget(200_000), offered_capabilities=offered
        )

        self.assertIs(refreshed.authority, original.authority)
        self.assertEqual(refreshed.authority.digest, original.authority.digest)
        self.assertEqual(refreshed.authority.granted_capabilities(), tuple(sorted(granted)))
        self.assertTrue(set(refreshed.included_capabilities()) <= set(granted))

        payload = refreshed.to_dict()
        self.assertEqual(payload["offered_count"], len(offered))
        self.assertEqual(
            payload["exclusion_summary"]["not_granted_by_authority"],
            len(offered) - len(granted),
        )

    def test_a_capability_the_catalog_gained_is_reported_not_absorbed(self) -> None:
        authority, granted, offered = self._narrow_grant()
        original = project_capabilities(
            CODING_REQUEST, authority=authority, budget=_budget(200_000), offered_capabilities=granted
        )
        newcomer = next(name for name in offered if name not in granted and name in _shortlisted(CODING_REQUEST))

        refreshed = refresh_capability_projection(
            original, budget=_budget(200_000), offered_capabilities=offered
        )

        payload = refreshed.to_dict()
        self.assertNotIn(newcomer, refreshed.included_capabilities())
        entry = next(item for item in payload["exclusions"] if item["capability"] == newcomer)
        self.assertEqual(entry["reason_code"], "not_granted_by_authority")
        self.assertIn("approving it again", entry["explanation"])

    def test_the_refresh_entry_point_has_no_authority_seam(self) -> None:
        parameters = inspect.signature(refresh_capability_projection).parameters

        self.assertNotIn("authority", parameters)
        self.assertFalse([name for name in parameters if "authority" in name])

    def test_the_grant_record_cannot_be_edited_after_approval(self) -> None:
        authority = _full_authority()

        self.assertTrue(dataclasses.is_dataclass(authority))
        self.assertTrue(getattr(CapabilityAuthority, "__dataclass_params__").frozen)
        self.assertIsInstance(authority.granted, frozenset)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            authority.task_id = "other"  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            authority.granted.add("smuggled")  # type: ignore[attr-defined]

    def test_the_emitted_authority_block_is_a_copy_and_carries_no_inventory(self) -> None:
        authority = _full_authority()
        payload = project_capabilities(
            CODING_REQUEST, authority=authority, budget=_budget(200_000)
        ).to_dict()

        emitted = payload["authority"]
        emitted["granted_families"].append("smuggled_family")
        emitted["granted_capability_count"] = 9_999

        self.assertEqual(authority.to_dict()["granted_capability_count"], len(authority.granted))
        self.assertNotIn("smuggled_family", authority.granted_families)
        self.assertEqual(emitted["schema_version"], CAPABILITY_AUTHORITY_SCHEMA_VERSION)
        # The grant identity travels; the grant contents do not. Emitting the
        # granted list would restore the whole-catalog dump the projection
        # exists to remove.
        self.assertNotIn("granted", payload["authority"])
        self.assertNotIn("granted_capabilities", payload["authority"])

    def test_only_the_approval_constructor_builds_a_grant(self) -> None:
        callers = _function_calls(PROJECTION_SOURCE, "CapabilityAuthority")

        self.assertEqual(callers, {"approve_capability_authority"})

    def test_no_projection_code_path_mutates_a_frozen_grant(self) -> None:
        source = PROJECTION_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        attribute_writes = {
            target.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute)
        }

        self.assertEqual(attribute_writes, set())
        # The two documented escape hatches out of a frozen dataclass.
        self.assertNotIn("object.__setattr__", source)
        self.assertNotIn("dataclasses.replace", source)
        self.assertNotIn("__dict__", source)

    def test_no_other_module_constructs_a_grant(self) -> None:
        src_root = PROJECTION_SOURCE.parents[1]
        builders = sorted(
            path.relative_to(src_root).as_posix()
            for path in src_root.rglob("*.py")
            if "CapabilityAuthority(" in path.read_text(encoding="utf-8")
        )

        self.assertEqual(builders, ["capabilities/projection.py"])

    def test_a_changed_grant_is_refused_instead_of_silently_reprojected(self) -> None:
        authority = _full_authority()

        unchanged = authority_change_report(authority.digest, authority)
        changed = authority_change_report("stale-digest", authority)

        self.assertTrue(unchanged["unchanged"])
        self.assertEqual(unchanged["next_action"], "reuse_projection")
        self.assertFalse(changed["unchanged"])
        self.assertEqual(changed["next_action"], "re_approve_capability_authority")
        self.assertEqual(changed["current_digest"], authority.digest)
        # A digest is all the caller held, so the refusal points at the policy
        # surface instead of re-emitting the grant it is protecting.
        self.assertEqual(changed["compare_command"], "omh capability-policy status")
        self.assertNotIn("granted", json.dumps(changed))

    def test_the_cli_refuses_a_projection_whose_grant_no_longer_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            status, stdout, _ = run_cli(
                [*base, "capabilities", "project", CODING_REQUEST, "--authority-digest", "stale-digest"]
            )

            self.assertEqual(status, 2)
            payload = json.loads(stdout)
            self.assertEqual(payload["schema_version"], "omh_capability_authority_change/v1")
            self.assertFalse(payload["unchanged"])
            self.assertEqual(payload["approved_digest"], "stale-digest")

    def test_the_grant_digest_moves_only_when_the_grant_moves(self) -> None:
        offered = sorted(installable_skill_names())
        first = approve_capability_authority(task_id="task-1", granted_capabilities=offered)
        same = approve_capability_authority(task_id="task-1", granted_capabilities=reversed(offered))
        wider = approve_capability_authority(task_id="task-1", granted_capabilities=[*offered, "invented"])

        self.assertEqual(first.digest, same.digest)
        self.assertNotEqual(first.digest, wider.digest)

    def test_an_approved_envelope_pins_the_permission_profile_and_its_digest(self) -> None:
        envelope = {"schema_version": "task_authority_envelope/v1", "permission_profile": "handoff_only"}

        authority = approve_capability_authority(
            task_id="task-1",
            granted_capabilities=["plan"],
            authority_envelope=envelope,
        )

        self.assertEqual(authority.permission_profile, "handoff_only")
        self.assertNotEqual(authority.envelope_digest, "")
        with self.assertRaises(CapabilityProjectionError):
            approve_capability_authority(
                task_id="task-1",
                granted_capabilities=["plan"],
                permission_profile="full_loop",
                authority_envelope=envelope,
            )

    def test_an_unsupported_permission_profile_is_refused(self) -> None:
        with self.assertRaises(CapabilityProjectionError) as caught:
            approve_capability_authority(
                task_id="task-1", granted_capabilities=["plan"], permission_profile="do_anything"
            )

        self.assertIn("full_loop", str(caught.exception))


class ExplainedExclusionTests(unittest.TestCase):
    """AC3: relevant inclusion and exclusion, without a tool-list dump."""

    def test_every_offered_capability_is_accounted_for_by_the_closed_vocabulary(self) -> None:
        payload = project_capabilities(
            CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000)
        ).to_dict()

        summary = payload["exclusion_summary"]
        self.assertEqual(set(summary), set(EXCLUSION_REASON_CODES))
        self.assertEqual(payload["included_count"] + sum(summary.values()), payload["offered_count"])
        self.assertEqual(payload["excluded_count"], sum(summary.values()))
        for entry in payload["exclusions"]:
            self.assertIn(entry["reason_code"], EXCLUSION_REASON_CODES)
            self.assertTrue(entry["explanation"].strip())
        self.assertEqual(payload["exclusion_reason_vocabulary"], list(EXCLUSION_REASON_CODES))

    def test_an_unknown_exclusion_reason_is_refused(self) -> None:
        with self.assertRaises(CapabilityProjectionError) as caught:
            capability_exclusion("plan", "because_the_model_felt_like_it")

        message = str(caught.exception)
        self.assertIn("because_the_model_felt_like_it", message)
        for reason in EXCLUSION_REASON_CODES:
            self.assertIn(reason, message)

    def test_the_payload_carries_no_full_tool_inventory(self) -> None:
        payload = project_capabilities(
            CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000)
        ).to_dict()

        named = {entry["capability"] for entry in payload["included"]}
        named |= {entry["capability"] for entry in payload["exclusions"]}
        self.assertLessEqual(len(named), CONSIDERED_LIMIT)
        self.assertLess(len(named) * 4, payload["offered_count"])
        for section in ("skills", "hooks", "plugin_tools", "tool_requirements", "orchestration_patterns"):
            self.assertNotIn(section, payload)

    def test_an_irrelevant_capability_is_counted_but_never_named(self) -> None:
        payload = project_capabilities(
            CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000)
        ).to_dict()

        named = {entry["capability"] for entry in payload["included"]}
        named |= {entry["capability"] for entry in payload["exclusions"]}
        unnamed = set(installable_skill_names()) - named

        self.assertGreater(len(unnamed), 80)
        self.assertEqual(payload["exclusion_summary"]["not_relevant_to_request"], len(unnamed))

    def test_included_capabilities_explain_why_they_matched(self) -> None:
        payload = project_capabilities(
            CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000)
        ).to_dict()

        self.assertTrue(payload["included"])
        for entry in payload["included"]:
            self.assertEqual(
                set(entry),
                {"capability", "family", "summary", "next_action", "match_reason", "score"},
            )
            self.assertTrue(entry["match_reason"].strip())
            self.assertTrue(entry["family"].strip())

    def test_selection_reuses_the_router_shortlist_rather_than_a_second_ranking(self) -> None:
        projection = project_capabilities(
            CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000)
        )

        shortlist = _shortlisted(CODING_REQUEST)
        self.assertTrue(set(projection.included_capabilities()) <= set(shortlist))
        scores = [entry["score"] for entry in projection.to_dict()["included"]]
        self.assertEqual(scores, sorted(scores, reverse=True))


class ExpansionGuardTests(unittest.TestCase):
    def test_expansion_is_never_automatic(self) -> None:
        payload = project_capabilities(
            CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000)
        ).to_dict()

        self.assertFalse(payload["expansion"]["automatic"])
        self.assertEqual(payload["expansion"]["policy"], "explicit_request_only")
        self.assertEqual(sorted(payload["expansion"]["expandable"]), sorted(payload["expansion"]["expandable"]))
        for entry in payload["included"]:
            for detail_only in ("triggers", "quality_bar", "safety_rules", "expected_outputs"):
                self.assertNotIn(detail_only, entry)

    def test_no_projection_code_path_expands_a_capability(self) -> None:
        callers = _function_calls(PROJECTION_SOURCE, "expand_capability")

        self.assertEqual(callers, set())

    def test_an_explicit_expansion_returns_the_exact_detail(self) -> None:
        projection = project_capabilities(CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000))
        target = projection.included_capabilities()[0]

        expanded = expand_capability(projection, target)

        self.assertEqual(expanded["schema_version"], CAPABILITY_EXPANSION_SCHEMA_VERSION)
        self.assertEqual(expanded["capability"], target)
        self.assertTrue(expanded["requested"])
        self.assertFalse(expanded["automatic"])
        self.assertEqual(expanded["detail"]["id"], target)
        self.assertEqual(expanded["authority_digest"], projection.authority.digest)
        self.assertIn("triggers", expanded["detail"])

    def test_expanding_something_the_projection_did_not_include_is_refused(self) -> None:
        projection = project_capabilities(CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000))
        absent = next(
            name for name in sorted(installable_skill_names()) if name not in projection.included_capabilities()
        )

        with self.assertRaises(CapabilityProjectionError) as caught:
            expand_capability(projection, absent)

        self.assertIn("not in this projection", str(caught.exception))

    def test_expanding_outside_the_grant_is_refused(self) -> None:
        offered = sorted(installable_skill_names())
        authority = approve_capability_authority(task_id="task-1", granted_capabilities=offered[:20])
        projection = project_capabilities(
            CODING_REQUEST, authority=authority, budget=_budget(200_000), offered_capabilities=offered
        )
        ungranted = next(name for name in offered if name not in offered[:20])

        with self.assertRaises(CapabilityProjectionError) as caught:
            expand_capability(projection, ungranted)

        self.assertIn("outside the approved authority", str(caught.exception))

    def test_the_cli_expands_only_on_request(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            _status, stdout, _ = run_cli([*base, "capabilities", "project", CODING_REQUEST])
            target = json.loads(stdout)["included"][0]["capability"]

            status, expanded_out, stderr = run_cli(
                [*base, "capabilities", "project", CODING_REQUEST, "--expand", target]
            )

            self.assertEqual(stderr, "")
            self.assertEqual(status, 0)
            expanded = json.loads(expanded_out)
            self.assertEqual(expanded["schema_version"], CAPABILITY_EXPANSION_SCHEMA_VERSION)
            self.assertEqual(expanded["capability"], target)


class ProjectionSurfaceTests(unittest.TestCase):
    def test_the_whole_catalog_summary_is_unchanged_for_existing_callers(self) -> None:
        before = capability_summary()

        project_capabilities(CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000))
        project_capabilities("find papers about this", authority=_full_authority(), budget=_budget(200_000))

        after = capability_summary()
        self.assertEqual(before, after)
        self.assertEqual(after["schema_version"], "omh_capability_summary/v1")
        self.assertEqual(after["determinism"], "static_projection_no_runtime_clock")
        self.assertNotIn("projection", after)
        self.assertNotIn("included", after)

    def test_the_same_request_projects_byte_identically(self) -> None:
        first = project_capabilities(CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000))
        second = project_capabilities(CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000))

        self.assertEqual(first.to_dict(), second.to_dict())

    def test_the_projection_payload_carries_no_wall_clock_field(self) -> None:
        payload = project_capabilities(
            CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000)
        ).to_dict()

        serialized = json.dumps(payload, sort_keys=True)
        for clock_field in ("updated_at", "observed_at", "generated_at", "timestamp"):
            self.assertNotIn(clock_field, serialized)
        self.assertEqual(payload["determinism"], "static_projection_no_runtime_clock")

    def test_a_projection_limit_below_one_is_refused(self) -> None:
        with self.assertRaises(CapabilityProjectionError):
            project_capabilities(CODING_REQUEST, authority=_full_authority(), budget=_budget(200_000), limit=0)

    def test_the_cli_defaults_to_plain_text_and_opts_into_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]

            _status, text, _ = run_cli([*base, "capabilities", "project", CODING_REQUEST], output_json=False)
            _status, machine, _ = run_cli(
                [*base, "capabilities", "project", CODING_REQUEST, "--json"], output_json=False
            )

            self.assertTrue(text.startswith("OMH capability projection for task "))
            self.assertIn("Expansion is explicit", text)
            self.assertIn("Excluded", text)
            self.assertEqual(
                json.loads(machine)["schema_version"], CAPABILITY_PROJECTION_SCHEMA_VERSION
            )


def _shortlisted(request: str) -> list[str]:
    from omh.routing.recommend import recommend_skills

    return [str(entry["skill"]) for entry in recommend_skills(request, limit=CONSIDERED_LIMIT)]


if __name__ == "__main__":
    unittest.main()
