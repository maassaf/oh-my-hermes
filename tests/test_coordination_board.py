from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()

from omh.coding.context_safety import coding_progress_policy_enforcement  # noqa: E402
from omh.system.paths import OmhPaths  # noqa: E402
from omh.workflows.coordination_board import (  # noqa: E402
    COORDINATION_BOARD_CLAIM_BOUNDARY,
    COORDINATION_BOARD_EVIDENCE_KINDS,
    COORDINATION_BOARD_LANES,
    COORDINATION_BOARD_SCHEMA_VERSION,
    build_coordination_board,
    claims_completion,
    render_coordination_board_text,
    validate_coordination_board,
)
from omh.workflows.goal_ledger import (  # noqa: E402
    cancel_goal_ledger,
    create_goal_ledger,
    record_goal_blocker,
    record_goal_checkpoint,
)

_NOW = "2026-08-09T12:00:00Z"
_LATER = "2026-08-09T18:30:00Z"
# Must satisfy omh.coding.fanout_contracts.FANOUT_ID_PATTERN, so the fixture
# uses a real-shaped id even though the board never writes one.
_FANOUT_ID = "fanout-0123456789ab"


def _paths(root: Path) -> OmhPaths:
    return OmhPaths(omh_home=root / "omh", hermes_home=root / "hermes")


def _write_fanout(
    paths: OmhPaths,
    *,
    fanout_id: str = _FANOUT_ID,
    units: list[dict],
    dispatched: list[dict] | None = None,
) -> None:
    fanout_dir = paths.fanout_contracts_dir / fanout_id
    fanout_dir.mkdir(parents=True, exist_ok=True)
    (fanout_dir / "fanout_contract.json").write_text(
        json.dumps({"fanout_id": fanout_id, "units": units}), encoding="utf-8"
    )
    if dispatched is not None:
        (fanout_dir / "dispatch_summary.json").write_text(
            json.dumps({"fanout_id": fanout_id, "units": dispatched}), encoding="utf-8"
        )


def _write_journal(paths: OmhPaths, events: list[dict]) -> None:
    paths.runtime_journal_dir.mkdir(parents=True, exist_ok=True)
    paths.runtime_journal_events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )


def _observed(run_id: str, event: str, *, runtime_profile: str = "") -> dict:
    record = {"run_id": run_id, "event": event, "status": "observed", "observed_at": _NOW}
    if runtime_profile:
        record["runtime_profile"] = runtime_profile
    return record


def _full_evidence_ladder(run_id: str, *, runtime_profile: str = "") -> list[dict]:
    return [
        _observed(run_id, "executor_result_observed", runtime_profile=runtime_profile),
        _observed(run_id, "verification_result_observed"),
        _observed(run_id, "review_result_observed"),
        _observed(run_id, "ci_result_observed"),
        _observed(run_id, "merge_observed"),
    ]


def _item_by_id(payload: dict, item_id: str) -> dict:
    for item in payload["items"]:
        if item["item_id"] == item_id:
            return item
    raise AssertionError(f"{item_id} is not on the board: {[item['item_id'] for item in payload['items']]}")


def _four_lane_store(paths: OmhPaths) -> None:
    """One store that produces blocked, dependency-gated, active, and next-ready work."""
    create_goal_ledger(paths, "ship the gateway", ["AC1 gateway ships"], goal_id="g-blocked")
    record_goal_checkpoint(
        paths, "g-blocked", "wire the gateway", status="pending", mutation_id="cp-wire"
    )
    record_goal_blocker(
        paths, "g-blocked", "staging credentials are missing", mark_goal_blocked=True, mutation_id="bl-creds"
    )
    create_goal_ledger(paths, "migrate the store", ["AC1 store migrates"], goal_id="g-active")
    record_goal_checkpoint(
        paths, "g-active", "port the writer", status="in_progress", mutation_id="cp-port"
    )
    _write_fanout(
        paths,
        units=[
            {"unit_id": "alpha", "title": "extract the parser", "owner": "codex"},
            {"unit_id": "beta", "title": "rewire callers", "owner": "claude", "depends_on": ["alpha"]},
        ],
    )


class CoordinationBoardDeterminismTests(unittest.TestCase):
    """Acceptance criterion 1: identical artifacts, identical ordered board and digest."""

    def test_identical_artifacts_produce_the_same_ordered_board_and_digest(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _four_lane_store(paths)
            first = build_coordination_board(paths, now=_NOW)
            second = build_coordination_board(paths, now=_LATER)

        self.assertEqual(first["items"], second["items"])
        self.assertEqual(first["board_digest"], second["board_digest"])
        self.assertEqual(first["lane_counts"], second["lane_counts"])
        # The one field that legitimately differs is the one kept outside the
        # digest, which is exactly why the digest survived the clock change.
        self.assertEqual(first["observed_at"], _NOW)
        self.assertEqual(second["observed_at"], _LATER)
        self.assertNotEqual(first["observed_at"], second["observed_at"])

    def test_digest_changes_when_a_recorded_artifact_changes(self) -> None:
        """A digest that never moves proves nothing, so pin that it does."""
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _four_lane_store(paths)
            before = build_coordination_board(paths, now=_NOW)
            record_goal_checkpoint(
                paths, "g-active", "port the reader", status="pending", mutation_id="cp-read"
            )
            after = build_coordination_board(paths, now=_NOW)

        self.assertNotEqual(before["board_digest"], after["board_digest"])
        self.assertEqual(after["item_count"], before["item_count"] + 1)

    def test_board_order_follows_the_declared_lane_order(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _four_lane_store(paths)
            payload = build_coordination_board(paths, now=_NOW)

        lanes = [item["lane"] for item in payload["items"]]
        indexes = [COORDINATION_BOARD_LANES.index(lane) for lane in lanes]
        self.assertEqual(indexes, sorted(indexes))
        self.assertEqual(payload["lane_order"], list(COORDINATION_BOARD_LANES))

    def test_limit_truncates_the_view_without_changing_count_or_digest(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _four_lane_store(paths)
            full = build_coordination_board(paths, now=_NOW)
            capped = build_coordination_board(paths, limit=2, now=_NOW)

        self.assertEqual(full["item_count"], 4)
        self.assertEqual(len(capped["items"]), 2)
        self.assertEqual(capped["item_count"], 4)
        self.assertEqual(capped["board_digest"], full["board_digest"])
        self.assertIn("Showing 2 of 4 items", render_coordination_board_text(capped))


class CoordinationBoardEvidenceTests(unittest.TestCase):
    """Acceptance criterion 2: a generic done never satisfies the evidence ladder."""

    def test_generic_done_cannot_satisfy_verification_review_ci_or_merge(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            create_goal_ledger(paths, "close the loop", ["AC1 loop closes"], goal_id="g-claimed")
            record_goal_checkpoint(
                paths,
                "g-claimed",
                "everything is done",
                criteria_refs=["AC001"],
                status="done",
                evidence_refs=["local note"],
                mutation_id="cp-done",
            )
            _write_fanout(
                paths,
                units=[{"unit_id": "alpha", "title": "extract the parser", "owner": "codex"}],
                dispatched=[
                    {"unit_id": "alpha", "owner": "codex", "status": "completed", "integration_ready": True}
                ],
            )
            payload = build_coordination_board(paths, now=_NOW)

        checkpoint = _item_by_id(payload, "g-claimed/cp-done")
        unit = _item_by_id(payload, f"{_FANOUT_ID}/alpha")
        for item in (checkpoint, unit):
            with self.subTest(item=item["item_id"]):
                self.assertTrue(item["claimed_complete"])
                self.assertEqual(item["evidence_observed"], [])
                self.assertEqual(item["missing_evidence"], list(COORDINATION_BOARD_EVIDENCE_KINDS))
                for kind in ("verification", "review", "ci", "merge"):
                    self.assertIn(kind, item["missing_evidence"])
                self.assertNotEqual(item["lane"], "evidence_complete")
                self.assertEqual(item["lane"], "active")
        self.assertEqual(payload["lane_counts"]["evidence_complete"], 0)
        self.assertEqual(validate_coordination_board(payload), [])

    def test_observed_ladder_is_the_only_route_to_evidence_complete(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _write_fanout(
                paths,
                units=[{"unit_id": "alpha", "title": "extract the parser", "run_ref": "run-alpha"}],
                dispatched=[
                    {"unit_id": "alpha", "owner": "choose", "status": "completed", "run_ref": "run-alpha"}
                ],
            )
            _write_journal(paths, _full_evidence_ladder("run-alpha", runtime_profile="codex"))
            payload = build_coordination_board(paths, now=_NOW)

        unit = _item_by_id(payload, f"{_FANOUT_ID}/alpha")
        self.assertEqual(unit["lane"], "evidence_complete")
        self.assertEqual(unit["evidence_observed"], list(COORDINATION_BOARD_EVIDENCE_KINDS))
        self.assertEqual(unit["missing_evidence"], [])
        # The unit named no owner and the dispatch entry said `choose`, so the
        # only honest owner left is the runtime the journal observed.
        self.assertEqual(unit["owner"], "codex")
        self.assertEqual(payload["sources_used"], ["fanout_contracts", "runtime_observations"])

    def test_partial_ladder_stays_active_and_names_what_is_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _write_fanout(
                paths,
                units=[{"unit_id": "alpha", "title": "extract the parser", "run_ref": "run-alpha"}],
                dispatched=[{"unit_id": "alpha", "status": "completed", "run_ref": "run-alpha"}],
            )
            _write_journal(
                paths,
                [
                    _observed("run-alpha", "executor_result_observed"),
                    _observed("run-alpha", "verification_result_observed"),
                ],
            )
            payload = build_coordination_board(paths, now=_NOW)

        unit = _item_by_id(payload, f"{_FANOUT_ID}/alpha")
        self.assertEqual(unit["lane"], "active")
        self.assertEqual(unit["evidence_observed"], ["execution", "verification"])
        self.assertEqual(unit["missing_evidence"], ["review", "ci", "merge"])

    def test_a_journal_event_that_was_not_observed_is_not_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _write_fanout(
                paths,
                units=[{"unit_id": "alpha", "title": "extract the parser", "run_ref": "run-alpha"}],
                dispatched=[{"unit_id": "alpha", "status": "completed", "run_ref": "run-alpha"}],
            )
            _write_journal(
                paths,
                [
                    {
                        "run_id": "run-alpha",
                        "event": "review_result_observed",
                        "status": "blocked",
                        "observed_at": _NOW,
                    }
                ],
            )
            payload = build_coordination_board(paths, now=_NOW)

        self.assertEqual(_item_by_id(payload, f"{_FANOUT_ID}/alpha")["evidence_observed"], [])

    def test_evidence_belonging_to_another_run_never_leaks_across_items(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _write_fanout(
                paths,
                units=[
                    {"unit_id": "alpha", "title": "extract the parser", "run_ref": "run-alpha"},
                    {"unit_id": "gamma", "title": "document it", "run_ref": "run-gamma"},
                ],
                dispatched=[
                    {"unit_id": "alpha", "status": "completed", "run_ref": "run-alpha"},
                    {"unit_id": "gamma", "status": "completed", "run_ref": "run-gamma"},
                ],
            )
            _write_journal(paths, _full_evidence_ladder("run-alpha"))
            payload = build_coordination_board(paths, now=_NOW)

        self.assertEqual(_item_by_id(payload, f"{_FANOUT_ID}/alpha")["lane"], "evidence_complete")
        gamma = _item_by_id(payload, f"{_FANOUT_ID}/gamma")
        self.assertEqual(gamma["evidence_observed"], [])
        self.assertEqual(gamma["lane"], "active")

    def test_claims_completion_accepts_the_ledger_vocabulary_and_nothing_else(self) -> None:
        for word in ("done", "completed", "already_completed", "merge_ready", "PASSED"):
            with self.subTest(word=word):
                self.assertTrue(claims_completion(word))
        for word in ("", "pending", "in_progress", "running", "blocked", "failed", "not_selected", None):
            with self.subTest(word=word):
                self.assertFalse(claims_completion(word))


class CoordinationBoardLaneTests(unittest.TestCase):
    """Acceptance criterion 3: the four question lanes render from one projection."""

    def test_board_renders_active_blocked_dependency_gated_and_next_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _four_lane_store(paths)
            payload = build_coordination_board(paths, now=_NOW)

        self.assertEqual(payload["schema_version"], COORDINATION_BOARD_SCHEMA_VERSION)
        self.assertEqual(validate_coordination_board(payload), [])
        self.assertEqual(
            {lane: count for lane, count in payload["lane_counts"].items() if count},
            {"blocked": 1, "dependency_gated": 1, "active": 1, "next_ready": 1},
        )

        blocked = _item_by_id(payload, "g-blocked/cp-wire")
        self.assertEqual(blocked["lane"], "blocked")
        self.assertIn("goal status blocked", blocked["blocked_by"])
        self.assertIn("staging credentials are missing", blocked["blocked_by"])

        gated = _item_by_id(payload, f"{_FANOUT_ID}/beta")
        self.assertEqual(gated["lane"], "dependency_gated")
        self.assertEqual(gated["depends_on"], [f"{_FANOUT_ID}/alpha"])
        self.assertEqual(gated["unmet_dependencies"], [f"{_FANOUT_ID}/alpha"])
        self.assertEqual(gated["owner"], "claude")

        self.assertEqual(_item_by_id(payload, "g-active/cp-port")["lane"], "active")
        self.assertEqual(_item_by_id(payload, f"{_FANOUT_ID}/alpha")["lane"], "next_ready")

        text = render_coordination_board_text(payload)
        for heading in ("BLOCKED (1)", "DEPENDENCY GATED (1)", "ACTIVE (1)", "NEXT READY (1)"):
            self.assertIn(heading, text)
        self.assertIn("waiting on fanout-0123456789ab/alpha", text)
        self.assertIn("missing evidence: execution, verification, review, ci, merge", text)
        self.assertIn(COORDINATION_BOARD_CLAIM_BOUNDARY, text)
        self.assertEqual(
            payload["summary"],
            "4 coordination items: 1 blocked, 1 dependency-gated, 1 active, 1 next-ready.",
        )

    def test_a_satisfied_dependency_opens_the_gate_without_becoming_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _write_fanout(
                paths,
                units=[
                    {"unit_id": "alpha", "title": "extract the parser", "owner": "codex"},
                    {"unit_id": "beta", "title": "rewire callers", "owner": "codex", "depends_on": ["alpha"]},
                ],
                dispatched=[{"unit_id": "alpha", "owner": "codex", "status": "completed"}],
            )
            payload = build_coordination_board(paths, now=_NOW)

        beta = _item_by_id(payload, f"{_FANOUT_ID}/beta")
        self.assertEqual(beta["unmet_dependencies"], [])
        self.assertEqual(beta["lane"], "next_ready")
        # The gate opened; the ladder did not.
        self.assertEqual(beta["missing_evidence"], list(COORDINATION_BOARD_EVIDENCE_KINDS))
        self.assertEqual(
            _item_by_id(payload, f"{_FANOUT_ID}/alpha")["missing_evidence"],
            list(COORDINATION_BOARD_EVIDENCE_KINDS),
        )

    def test_dry_run_planned_dependency_does_not_open_the_gate(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _write_fanout(
                paths,
                units=[
                    {"unit_id": "alpha", "title": "extract the parser"},
                    {"unit_id": "beta", "title": "rewire callers", "depends_on": ["alpha"]},
                ],
                dispatched=[{"unit_id": "alpha", "status": "dry_run_planned"}],
            )
            payload = build_coordination_board(paths, now=_NOW)

        self.assertEqual(_item_by_id(payload, f"{_FANOUT_ID}/beta")["lane"], "dependency_gated")

    def test_dependency_on_an_unknown_unit_stays_unmet(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _write_fanout(
                paths,
                units=[{"unit_id": "beta", "title": "rewire callers", "depends_on": ["ghost"]}],
                dispatched=[{"unit_id": "beta", "status": "prepared"}],
            )
            payload = build_coordination_board(paths, now=_NOW)

        beta = _item_by_id(payload, f"{_FANOUT_ID}/beta")
        self.assertEqual(beta["lane"], "dependency_gated")
        self.assertEqual(beta["unmet_dependencies"], [f"{_FANOUT_ID}/ghost"])

    def test_recorded_dependency_gate_without_a_contract_edge_still_names_a_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _write_fanout(
                paths,
                units=[{"unit_id": "beta", "title": "rewire callers"}],
                dispatched=[
                    {"unit_id": "beta", "status": "blocked_by_dependency", "blocked_on": ["alpha"]}
                ],
            )
            payload = build_coordination_board(paths, now=_NOW)

        beta = _item_by_id(payload, f"{_FANOUT_ID}/beta")
        self.assertEqual(beta["lane"], "dependency_gated")
        self.assertEqual(beta["unmet_dependencies"], [f"{_FANOUT_ID}/alpha"])
        self.assertEqual(validate_coordination_board(payload), [])

    def test_failed_dispatch_is_blocked_and_not_selected_is_only_prepared(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _write_fanout(
                paths,
                units=[
                    {"unit_id": "alpha", "title": "extract the parser"},
                    {"unit_id": "gamma", "title": "document it"},
                ],
                dispatched=[
                    {"unit_id": "alpha", "status": "worktree_failed"},
                    {"unit_id": "gamma", "status": "not_selected"},
                ],
            )
            payload = build_coordination_board(paths, now=_NOW)

        alpha = _item_by_id(payload, f"{_FANOUT_ID}/alpha")
        self.assertEqual(alpha["lane"], "blocked")
        self.assertEqual(alpha["blocked_by"], ["dispatch status worktree_failed"])
        self.assertEqual(_item_by_id(payload, f"{_FANOUT_ID}/gamma")["lane"], "prepared")

    def test_invalid_capability_snapshot_is_a_blocking_dispatch_status(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _write_fanout(
                paths,
                units=[{"unit_id": "alpha", "title": "extract the parser"}],
                dispatched=[{"unit_id": "alpha", "status": "capability_snapshot_invalid"}],
            )
            payload = build_coordination_board(paths, now=_NOW)

        alpha = _item_by_id(payload, f"{_FANOUT_ID}/alpha")
        self.assertEqual(alpha["lane"], "blocked")
        self.assertEqual(
            alpha["blocked_by"],
            ["dispatch status capability_snapshot_invalid"],
        )

    def test_a_goal_without_checkpoints_still_shows_as_startable_work(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            create_goal_ledger(paths, "ship the gateway", ["AC1 gateway ships"], goal_id="g-fresh")
            payload = build_coordination_board(paths, now=_NOW)

        objective = _item_by_id(payload, "g-fresh/objective")
        self.assertEqual(objective["source"], "goal_objective")
        self.assertEqual(objective["lane"], "next_ready")
        self.assertEqual(objective["owner"], "unassigned")
        self.assertEqual(payload["sources_used"], ["goal_ledgers"])

    def test_a_cancelled_goal_is_recorded_not_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            create_goal_ledger(paths, "abandon the port", ["AC1 port lands"], goal_id="g-cancelled")
            record_goal_checkpoint(
                paths, "g-cancelled", "start the port", status="pending", mutation_id="cp-start"
            )
            cancel_goal_ledger(paths, "g-cancelled", reason="scope dropped")
            payload = build_coordination_board(paths, now=_NOW)

        self.assertEqual(_item_by_id(payload, "g-cancelled/cp-start")["lane"], "prepared")


class CoordinationBoardResilienceTests(unittest.TestCase):
    def test_empty_store_projects_a_valid_empty_board(self) -> None:
        with TemporaryDirectory() as tmp:
            payload = build_coordination_board(_paths(Path(tmp)), now=_NOW)

        self.assertEqual(validate_coordination_board(payload), [])
        self.assertEqual(payload["item_count"], 0)
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["sources_used"], [])
        self.assertEqual(payload["summary"], "No coordinated work recorded.")
        self.assertEqual(payload["claim_boundary"], COORDINATION_BOARD_CLAIM_BOUNDARY)
        text = render_coordination_board_text(payload)
        self.assertIn("No coordinated work recorded.", text)
        self.assertIn("Coordination board (0 items)", text)

    def test_unreadable_artifacts_are_skipped_instead_of_failing_the_board(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            create_goal_ledger(paths, "ship the gateway", ["AC1 gateway ships"], goal_id="g-good")
            broken_goal = paths.goals_dir / "g-broken"
            broken_goal.mkdir(parents=True, exist_ok=True)
            (broken_goal / "goal.json").write_text("{ not json", encoding="utf-8")
            _write_fanout(paths, units=[{"unit_id": "alpha", "title": "extract the parser"}])
            broken_fanout = paths.fanout_contracts_dir / "fanout-ffffffffffff"
            broken_fanout.mkdir(parents=True, exist_ok=True)
            (broken_fanout / "fanout_contract.json").write_text("[]", encoding="utf-8")
            payload = build_coordination_board(paths, now=_NOW)

        self.assertEqual(validate_coordination_board(payload), [])
        self.assertEqual(
            sorted(item["item_id"] for item in payload["items"]),
            ["fanout-0123456789ab/alpha", "g-good/objective"],
        )

    def test_units_and_checkpoints_without_an_id_are_dropped(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _write_fanout(
                paths,
                units=[{"title": "nameless"}, {"unit_id": "alpha", "title": "extract the parser"}],
            )
            # Hand-written rather than recorded through the ledger API, which
            # refuses an id-less checkpoint. The board reads whatever is on
            # disk, so the shape it must survive is the one it never wrote.
            goal_dir = paths.goals_dir / "g-partial"
            goal_dir.mkdir(parents=True, exist_ok=True)
            (goal_dir / "goal.json").write_text(
                json.dumps(
                    {
                        "goal_id": "g-partial",
                        "status": "active",
                        "objective_summary": "ship the gateway",
                        "checkpoints": [
                            {"status": "pending", "summary": "nameless"},
                            {"checkpoint_id": "cp-wire", "status": "pending", "summary": "wire it"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            payload = build_coordination_board(paths, now=_NOW)

        self.assertEqual(
            sorted(item["item_id"] for item in payload["items"]),
            [f"{_FANOUT_ID}/alpha", "g-partial/cp-wire"],
        )
        self.assertEqual(validate_coordination_board(payload), [])


class CoordinationBoardValidationTests(unittest.TestCase):
    def _valid_payload(self) -> dict:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _four_lane_store(paths)
            return build_coordination_board(paths, now=_NOW)

    def test_a_projected_board_validates_clean(self) -> None:
        self.assertEqual(validate_coordination_board(self._valid_payload()), [])

    def test_wrong_schema_version_is_refused(self) -> None:
        payload = {**self._valid_payload(), "schema_version": "coordination_board/v2"}
        self.assertIn("schema_version must be coordination_board/v1", validate_coordination_board(payload))

    def test_missing_digest_and_claim_boundary_are_refused(self) -> None:
        payload = {**self._valid_payload(), "board_digest": "", "claim_boundary": " "}
        errors = validate_coordination_board(payload)
        self.assertIn("board_digest is required", errors)
        self.assertIn("claim_boundary is required", errors)

    def test_an_unknown_lane_is_refused(self) -> None:
        payload = self._valid_payload()
        payload["items"][0] = {**payload["items"][0], "lane": "shipped"}
        self.assertIn("items[0]: lane is unsupported", validate_coordination_board(payload))

    def test_evidence_complete_with_missing_evidence_is_refused(self) -> None:
        """The claim boundary, as a payload rule rather than a sentence."""
        payload = self._valid_payload()
        payload["items"][0] = {**payload["items"][0], "lane": "evidence_complete"}
        errors = validate_coordination_board(payload)
        self.assertIn("items[0]: evidence_complete requires no missing evidence", errors)

    def test_an_evidence_partition_that_loses_a_kind_is_refused(self) -> None:
        payload = self._valid_payload()
        payload["items"][0] = {
            **payload["items"][0],
            "evidence_observed": [],
            "missing_evidence": ["review"],
        }
        self.assertIn(
            "items[0]: evidence_observed and missing_evidence must partition every evidence kind",
            validate_coordination_board(payload),
        )

    def test_an_unsupported_evidence_kind_is_refused(self) -> None:
        payload = self._valid_payload()
        payload["items"][0] = {**payload["items"][0], "evidence_observed": ["vibes"]}
        self.assertIn(
            "items[0]: evidence_observed carries an unsupported evidence kind",
            validate_coordination_board(payload),
        )

    def test_a_reasonless_blocked_or_gated_item_is_refused(self) -> None:
        payload = self._valid_payload()
        payload["items"] = [
            {**_item_by_id(payload, "g-blocked/cp-wire"), "blocked_by": []},
            {**_item_by_id(payload, f"{_FANOUT_ID}/beta"), "unmet_dependencies": []},
        ]
        errors = validate_coordination_board(payload)
        self.assertIn("items[0]: blocked requires at least one blocked_by reason", errors)
        self.assertIn("items[1]: dependency_gated requires at least one unmet dependency", errors)

    def test_structural_type_errors_are_refused(self) -> None:
        errors = validate_coordination_board(
            {
                "schema_version": COORDINATION_BOARD_SCHEMA_VERSION,
                "board_digest": "abc",
                "claim_boundary": COORDINATION_BOARD_CLAIM_BOUNDARY,
                "item_count": "4",
                "lane_order": ["blocked"],
                "lane_counts": [],
                "items": "none",
                "sources_used": ["telepathy"],
            }
        )
        self.assertIn("item_count must be an integer", errors)
        self.assertIn("lane_order must list every board lane in board order", errors)
        self.assertIn("lane_counts must be an object", errors)
        self.assertIn("items must be a list", errors)
        self.assertIn("sources_used carries an unsupported source", errors)

    def test_a_non_object_item_is_refused(self) -> None:
        payload = {**self._valid_payload(), "items": ["not an item"]}
        self.assertIn("items[0]: item must be an object", validate_coordination_board(payload))


class CoordinationBoardCliTests(unittest.TestCase):
    def test_goal_board_defaults_to_text_and_opts_into_json(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _four_lane_store(paths)
            home = ["--omh-home", str(paths.omh_home), "--hermes-home", str(paths.hermes_home)]
            text_status, text_out, _ = run_cli([*home, "goal", "board"], output_json=False)
            json_status, json_out, _ = run_cli([*home, "goal", "board", "--json"], output_json=False)

        self.assertEqual(text_status, 0)
        self.assertIn("Coordination board (4 items)", text_out)
        self.assertIn("BLOCKED (1)", text_out)
        self.assertIn("NEXT READY (1)", text_out)
        self.assertNotIn('"schema_version"', text_out)

        self.assertEqual(json_status, 0)
        payload = json.loads(json_out)
        self.assertEqual(payload["schema_version"], COORDINATION_BOARD_SCHEMA_VERSION)
        self.assertEqual(payload["item_count"], 4)
        self.assertEqual(validate_coordination_board(payload), [])

    def test_goal_board_refuses_a_non_positive_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            home = ["--omh-home", str(paths.omh_home), "--hermes-home", str(paths.hermes_home)]
            status, _stdout, stderr = run_cli([*home, "goal", "board", "--limit", "0"], output_json=False)

        self.assertNotEqual(status, 0)
        self.assertIn("--limit must be at least 1", stderr)

    def test_goal_board_is_declared_a_bounded_polled_surface(self) -> None:
        self.assertIn("omh goal board", coding_progress_policy_enforcement()["bounded_surfaces"])


if __name__ == "__main__":
    unittest.main()
