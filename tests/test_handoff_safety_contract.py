"""The handoff safety contract and its anti-decoration rule (#818).

Acceptance criteria under test:

* the contract is producible offline from allowlisted local metadata,
* nothing hides a dispatch, an automatic merge, arbitrary authority, or a raw
  prompt behind it, and
* no start or success is claimed without the matching evidence.

The load-bearing test in this file is not any single boundary; it is that every
boundary declares whether it is *enforced* and, if so, names a symbol that a
reader can open. A contract listing nine boundaries while guarding four reads as
coverage that does not exist, which is worse than shipping no contract, so the
tests here fail in both directions: adding a boundary without an enforcer fails,
and deleting an enforcer while leaving the declaration standing fails too.
"""

from __future__ import annotations

import builtins
import importlib
import inspect
import json
import socket
import subprocess
import unittest
from unittest import mock

from _local_package import load_local_package

load_local_package()

from omh.coding.action_gate import (  # noqa: E402
    ENFORCEMENT_LEVELS,
    EVIDENCE_REQUIREMENT_KEYS,
    HANDOFF_CONTRACT_INPUT_SOURCES,
    HANDOFF_EVIDENCE_STAGES,
    HANDOFF_SAFETY_CONTRACT_KEY,
    HANDOFF_SAFETY_CONTRACT_KEYS,
    HANDOFF_SAFETY_CONTRACT_SCHEMA_VERSION,
    SAFETY_BOUNDARY_KEYS,
    SAFETY_BOUNDARY_NAMES,
    build_task_authority_envelope,
    build_task_handoff_safety_contract,
    evaluate_action_gate,
    split_handoff_safety_contract,
    validate_action_gate_verdict,
    validate_handoff_safety_contract,
)
from omh.coding.coding_delegation import build_coding_delegation_payload  # noqa: E402
from omh.commands import setup as setup_command  # noqa: E402
from omh.quality.safety_preflight import data_boundary_enforcement_facts  # noqa: E402
from omh.runtime.claims import (  # noqa: E402
    RUNTIME_CLAIM_LADDER,
    Claim,
    allowed_runtime_claims,
    blocked_runtime_claims,
)
from omh.runtime.records import (  # noqa: E402
    CODING_DELEGATION_RECORD_KEYS,
    build_coding_delegation_record,
    validate_coding_delegation_record,
)
from omh.workflows.external_effect_receipts import external_effect_id  # noqa: E402


_MESSAGE = "fix the login bug in src/auth.py and add a regression test"
# The one executed remote call in the tree, outside the delegation lane. The
# network boundary names it, so the name has to keep matching the code.
_STAR_ENDPOINT = "/user/starred/rlaope/oh-my-hermes"
_ALLOW_VERDICT = {
    "schema_version": "omh_safety_preflight_verdict/v1",
    "outcome": "allow",
    "rule_id": "",
    "field": "",
    "correction": "",
    "safety_profile_revision": "rev-frozen",
}

# The boundaries that name a real enforcing symbol today. Hardcoded on purpose:
# it has to be edited by hand, in the same commit, whenever a boundary gains or
# loses an enforcer, so neither direction of drift can land quietly.
#
# `confirmation_answered` is deliberately *not* here. The gate really does
# refuse a risky action with no live approval, but only for a caller that holds
# a run an approval could bind to, and the shipped delegation lane has none —
# see `action_gate.RISK_CONSENT_BLOCKER`. A boundary that refuses nothing on the
# path users travel is a declaration, so it is declared.
_ENFORCED_BOUNDARIES = frozenset(
    {
        "confirmation_arming",
        "credential",
        "data_declared_destination",
        "data_prohibited_data_class",
        "data_workspace_root_claim",
        "dispatch",
        "evidence_separation",
        "file",
        "merge",
        "network",
        "profile_revision",
        "prohibited_actions",
        "review_receipt",
        "storage_content",
        "untrusted_input",
    }
)
# The boundaries the contract declares but does not guard, each with the issue
# that must land before it can move. A blocker is not always an issue number:
# `account_authorization` names the reason instead, because nothing that could
# be filed would close it — OMH cannot observe a consent flow owned by someone
# else's website. `workspace` joined it when #820 landed: that issue shipped the
# pre-dispatch half (`workspace_binding_guard/v1` reserves a workspace and a
# branch for one handoff), and the half that remains is confining a process that
# is already running, which no change on this side of the wall can do.
_DECLARED_NOT_ENFORCED = {
    "account_authorization": "host_owned_consent_flow_is_not_observable_by_omh",
    "confirmation_answered": "no_confirmation_answer_intake_mints_a_run_bound_approval",
    # #821 shipped `recovery_anchor/v1`, so the blocker stopped being that
    # issue. It is a reason string now for the same cause as the two above: the
    # delegation lane observes no baseline revision, so it has nothing to build
    # an anchor from, and no ticket against this repository changes that.
    "recovery": "no_delegation_surface_holds_an_observed_baseline_to_attach_an_anchor_from",
    # #826 shipped `run_lineage_checkpoint/v1`, so this blocker stopped being
    # that issue too. A lineage chain now refuses to reach dispatch without an
    # observed start, which orders a chain; nothing makes a chain required of a
    # run, so the claim ladder is unchanged and the row stays declared.
    "start_evidence": "lineage_orders_the_start_but_no_claim_rung_requires_a_lineage_chain",
    # #835 shipped `generated_artifact/v1` and its dry-run cleanup preview, so
    # this blocker stopped being that issue too. Lifecycle is readable now;
    # removal is not implemented and is not queued behind a ticket, because that
    # issue puts cleanup-without-explicit-confirmation out of scope and no
    # surface here can take a confirmation.
    "storage_retention": "the_preview_lists_what_could_be_removed_and_no_surface_removes_anything",
    "workspace": "no_omh_side_constraint_can_bind_a_running_executor_process",
}
# The three data-boundary rows whose blocker is a *host* fact rather than a
# repository fact: a host with an OS confinement backend is blocked on no lane
# placing an executor under the sandbox, and a host without one is blocked on
# not having a backend at all. Pinning either string here would make this test
# pass on macOS and fail on Windows, so the expected value is read from the same
# facts the contract read.
_HOST_DEPENDENT_BOUNDARIES = frozenset(
    {
        "data_executor_honours_declared_targets",
        "data_runtime_filesystem_confinement",
        "data_runtime_network_confinement",
    }
)


def _host_dependent_blockers() -> dict[str, str]:
    facts = data_boundary_enforcement_facts()
    return {
        f"data_{limit['limit']}": str(limit["blocked_by"])
        for limit in facts["limits"]  # type: ignore[index]
        if f"data_{limit['limit']}" in _HOST_DEPENDENT_BOUNDARIES
    }


def _envelope(**overrides: object) -> dict[str, object]:
    envelope = build_task_authority_envelope(
        denied=False,
        delegation_action="delegate",
        intent="coding",
        review_required=False,
        work_owner_mode="external_executor",
        selected_executor_profile="codex",
        dispatchable=True,
        choice_required=False,
        isolation_plan={"strategy": "worktree_recommended"},
        message=_MESSAGE,
        safety_profile_revision="rev-frozen",
    )
    envelope.update(overrides)
    return envelope


def _contract() -> dict[str, object]:
    return build_task_handoff_safety_contract(_envelope())


def _gate(**overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "message": _MESSAGE,
        "delegation_action": "delegate",
        "intent": "coding",
        "review_required": False,
        "work_owner_mode": "external_executor",
        "selected_executor_profile": "codex",
        "dispatch_policy": "ask_before_dispatch",
        "dispatchable": True,
        "choice_required": False,
        "executor_selection_status": "handoff_prepared",
        "isolation_plan": {"strategy": "worktree_recommended"},
        "safety_preflight": _ALLOW_VERDICT,
    }
    arguments.update(overrides)
    return evaluate_action_gate(**arguments)  # type: ignore[arg-type]


def _boundary(contract: dict[str, object], name: str) -> dict[str, object]:
    for entry in contract["boundaries"]:  # type: ignore[index]
        if entry["boundary"] == name:
            return entry
    raise AssertionError(f"no boundary named {name}")


def _stage(contract: dict[str, object], name: str) -> dict[str, object]:
    for entry in contract["evidence_requirements"]:  # type: ignore[index]
        if entry["stage"] == name:
            return entry
    raise AssertionError(f"no evidence stage named {name}")


def _receipt(kind: str, run_id: str, *, result: str = "succeeded") -> dict[str, object]:
    action, acting_surface = {
        "review": ("review_submitted", "runtime_review_record"),
        "ci": ("ci_run", "runtime_ci_record"),
        "merge": ("merge", "runtime_merge_record"),
    }[kind]
    return {
        "receipt_id": f"receipt-{kind}",
        "effect_id": external_effect_id(kind, run_id),
        "action": action,
        "acting_surface": acting_surface,
        "observed_result": result,
    }


class HandoffSafetyContractShapeTests(unittest.TestCase):
    def test_the_contract_is_a_field_group_on_the_delegation_not_a_record_family(self) -> None:
        # Joined records go stale against a delegation rebuilt under a different
        # revision (#811); the contract therefore ships as one key on the
        # delegation, beside `action_gate` and never inside it.
        self.assertIn("handoff_safety_contract", CODING_DELEGATION_RECORD_KEYS)
        payload = build_coding_delegation_payload(_MESSAGE, executor_target="codex")
        self.assertIn("handoff_safety_contract", payload)
        self.assertNotIn(HANDOFF_SAFETY_CONTRACT_KEY, payload["action_gate"])

    def test_contract_keys_and_schema_are_fixed(self) -> None:
        contract = _contract()
        self.assertEqual(set(contract), set(HANDOFF_SAFETY_CONTRACT_KEYS))
        self.assertEqual(contract["schema_version"], HANDOFF_SAFETY_CONTRACT_SCHEMA_VERSION)
        self.assertEqual(contract["status"], "prepared_not_observed")
        self.assertEqual(validate_handoff_safety_contract(contract), [])

    def test_the_contract_travels_with_the_envelope_values_it_describes(self) -> None:
        envelope = _envelope()
        contract = build_task_handoff_safety_contract(envelope)
        self.assertEqual(contract["prohibited_actions"], envelope["blocked_actions"])
        self.assertEqual(contract["allowed_executors"], envelope["allowed_executors"])
        self.assertEqual(contract["allowed_targets"], envelope["allowed_targets"])
        self.assertEqual(contract["merge_authority"], "disabled")
        self.assertEqual(contract["external_action_authority"], "prepare_only")
        self.assertEqual(contract["safety_profile_revision"], "rev-frozen")
        self.assertEqual(validate_handoff_safety_contract(contract, envelope=envelope), [])

    def test_the_gate_produces_exactly_one_contract_and_the_split_stores_it_once(self) -> None:
        verdict = _gate()
        gate, contract = split_handoff_safety_contract(verdict)
        self.assertNotIn(HANDOFF_SAFETY_CONTRACT_KEY, gate)
        self.assertEqual(validate_action_gate_verdict(gate), [])
        self.assertEqual(validate_handoff_safety_contract(contract, envelope=gate["authority_envelope"]), [])

    def test_a_denied_verdict_still_carries_a_contract(self) -> None:
        verdict = _gate(
            safety_preflight={**_ALLOW_VERDICT, "outcome": "deny", "rule_id": "target_paths_bounded"},
        )
        _, contract = split_handoff_safety_contract(verdict)
        self.assertEqual(contract["merge_authority"], "disabled")
        # A denial withdraws authority; it never withdraws the statement of what
        # the boundary was, which is what the user is owed on a refusal.
        self.assertEqual(sorted(SAFETY_BOUNDARY_NAMES), sorted(entry["boundary"] for entry in contract["boundaries"]))


class HandoffSafetyContractOfflineTests(unittest.TestCase):
    """AC1: producible offline from allowlisted local metadata."""

    def test_input_sources_are_the_declared_local_metadata_surfaces(self) -> None:
        self.assertEqual(_contract()["input_sources"], list(HANDOFF_CONTRACT_INPUT_SOURCES))
        self.assertTrue(_contract()["produced_offline"])

    def test_repeated_builds_are_byte_identical(self) -> None:
        first = json.dumps(_contract(), sort_keys=True)
        second = json.dumps(_contract(), sort_keys=True)
        self.assertEqual(first, second)
        gate_first = json.dumps(split_handoff_safety_contract(_gate())[1], sort_keys=True)
        gate_second = json.dumps(split_handoff_safety_contract(_gate())[1], sort_keys=True)
        self.assertEqual(gate_first, gate_second)
        self.assertEqual(first, gate_first)

    def test_the_contract_builds_with_every_io_primitive_disarmed(self) -> None:
        """Offline by construction, not by convention.

        The imports are warmed first because the lazy goal-loop import inside
        the envelope builder would otherwise fail on the patched `open` and
        prove nothing about the contract itself.
        """
        _gate()

        def _refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError("the safety contract must not perform I/O")

        with (
            mock.patch.object(builtins, "open", _refuse),
            mock.patch.object(socket, "socket", _refuse),
            mock.patch.object(socket, "create_connection", _refuse),
            mock.patch.object(subprocess, "run", _refuse),
            mock.patch.object(subprocess, "Popen", _refuse),
        ):
            offline = build_task_handoff_safety_contract(_envelope())
            _, from_gate = split_handoff_safety_contract(_gate())
        self.assertEqual(validate_handoff_safety_contract(offline), [])
        self.assertEqual(json.dumps(offline, sort_keys=True), json.dumps(from_gate, sort_keys=True))


class AntiDecorationTests(unittest.TestCase):
    """Every declared boundary states whether anything actually guards it."""

    def test_every_boundary_declares_enforcement_and_names_an_enforcer_or_a_blocker(self) -> None:
        for entry in _contract()["boundaries"]:  # type: ignore[index]
            with self.subTest(boundary=entry["boundary"]):
                self.assertEqual(set(entry), set(SAFETY_BOUNDARY_KEYS))
                self.assertIn(entry["enforcement"], ENFORCEMENT_LEVELS)
                self.assertTrue(entry["statement"].strip())
                if entry["enforcement"] == "enforced":
                    self.assertTrue(entry["enforced_by"])
                    self.assertEqual(entry["blocked_by"], "")
                else:
                    self.assertEqual(entry["enforced_by"], [])
                    self.assertTrue(entry["blocked_by"].strip())

    def test_the_enforced_boundary_set_is_exactly_the_hardcoded_set(self) -> None:
        contract = _contract()
        enforced = {entry["boundary"] for entry in contract["boundaries"] if entry["enforcement"] == "enforced"}
        # Fails when a boundary is declared without an enforcer, and equally
        # when an enforcer is deleted but the declaration is left behind.
        self.assertEqual(enforced, set(_ENFORCED_BOUNDARIES))
        declared = {
            entry["boundary"]: entry["blocked_by"]
            for entry in contract["boundaries"]
            if entry["enforcement"] == "declared_not_enforced"
        }
        self.assertEqual(declared, {**_DECLARED_NOT_ENFORCED, **_host_dependent_blockers()})
        self.assertEqual(set(SAFETY_BOUNDARY_NAMES), enforced | set(declared))

    def test_every_cited_enforcer_resolves_to_a_real_symbol(self) -> None:
        """The check that stops the contract drifting into fiction."""
        contract = _contract()
        cited = {
            symbol
            for group in ("boundaries", "evidence_requirements")
            for entry in contract[group]
            for symbol in entry["enforced_by"]
        }
        self.assertTrue(cited)
        for symbol in sorted(cited):
            with self.subTest(symbol=symbol):
                module_name, _, attribute = symbol.rpartition(".")
                self.assertTrue(module_name.startswith("omh."), symbol)
                module = importlib.import_module(module_name)
                self.assertTrue(hasattr(module, attribute), f"{symbol} does not exist")

    def test_no_boundary_is_justified_by_the_absence_of_a_capability(self) -> None:
        """An absence claim cannot be enforced, and this tree contradicts one.

        `omh setup --star` really does shell out to `gh`, so a boundary saying
        "no such capability exists anywhere in src/" would be exactly the
        decoration this contract exists to prevent. Statements are scoped to the
        surface they govern instead.
        """
        for entry in _contract()["boundaries"]:
            with self.subTest(boundary=entry["boundary"]):
                statement = entry["statement"].lower()
                for absence_claim in ("no network client exists in src/", "opens no network connection"):
                    self.assertNotIn(absence_claim, statement)

    def test_the_network_boundary_scopes_itself_and_names_the_real_exception(self) -> None:
        network = _boundary(_contract(), "network")
        statement = network["statement"]
        self.assertIn("The coding delegation lane holds no network client", statement)
        self.assertIn("prepare_only", statement)
        # The exception is named exactly, not rounded away. The path is read
        # from the tree so the sentence cannot outlive the call it describes.
        self.assertIn("omh setup --star", statement)
        self.assertIn(_STAR_ENDPOINT, statement)
        source = inspect.getsource(setup_command._try_star_github_repo)
        self.assertIn(_STAR_ENDPOINT, source)
        self.assertIn("gh", source)
        # Enforcement is the lane check, never the call site that performs it.
        self.assertEqual(network["enforced_by"], ["omh.coding.action_gate.validate_task_authority_envelope"])

    def test_the_star_call_is_gated_behind_an_explicit_flag(self) -> None:
        # The statement calls it operator-invoked; that has to be true.
        self.assertIn('getattr(args, "star", False)', inspect.getsource(setup_command.cmd_setup))

    def test_unenforced_boundaries_state_the_gap_in_words(self) -> None:
        contract = _contract()
        workspace = _boundary(contract, "workspace")["statement"]
        self.assertIn("nothing binds a running executor", workspace)
        # And it says which half #820 did close, so a reader does not read the
        # unenforced verdict as "nothing about workspaces is guarded".
        self.assertIn("workspace_binding_guard/v1", workspace)
        self.assertIn("not a state OMH can observe", _boundary(contract, "account_authorization")["statement"])
        # Same shape as the workspace and start_evidence rows: #835 closed the
        # readable half, so the sentence has to name what now exists *and* keep
        # stating the gap. Without the second clause a reader takes a preview
        # that lists removable artifacts for something that removes them.
        storage_retention = _boundary(contract, "storage_retention")["statement"]
        self.assertIn("generated_artifact_cleanup_preview/v1", storage_retention)
        self.assertIn("still does not happen is deletion", storage_retention)
        self.assertIn("dry run with no removal path", storage_retention)
        self.assertIn("persist until the operator deletes them", storage_retention)
        # Same shape as the workspace row after #820: #826 closed a half, so the
        # sentence has to name what now exists *and* keep stating the gap, or a
        # reader takes the ordered chain for a required one.
        start_evidence = _boundary(contract, "start_evidence")["statement"]
        self.assertIn("no observed start", start_evidence)
        self.assertIn("run_lineage_checkpoint/v1", start_evidence)
        self.assertIn("does not do is make the start required", start_evidence)
        # The recovery row is the one that names a contract that *does* exist,
        # so the sentence has to keep saying which half is missing. Without this
        # the "recovery_anchor/v1 contract exists" clause could stand alone and
        # read as enforcement.
        recovery = _boundary(contract, "recovery")["statement"]
        self.assertIn("recovery_anchor/v1 contract exists", recovery)
        self.assertIn("still does not happen is attachment", recovery)
        self.assertIn("records no baseline to undo against", recovery)


class HandoffSafetyContractValidatorTests(unittest.TestCase):
    def test_a_boundary_claiming_enforcement_with_no_enforcer_is_rejected(self) -> None:
        contract = _contract()
        _boundary(contract, "workspace").update({"enforcement": "enforced", "blocked_by": ""})
        errors = validate_handoff_safety_contract(contract)
        self.assertTrue(any("names no enforcing symbol" in error for error in errors), errors)

    def test_a_boundary_that_is_not_enforced_but_names_an_enforcer_is_rejected(self) -> None:
        contract = _contract()
        _boundary(contract, "workspace")["enforced_by"] = ["omh.coding.action_gate.evaluate_action_gate"]
        errors = validate_handoff_safety_contract(contract)
        self.assertTrue(any("must not name an enforcing symbol" in error for error in errors), errors)

    def test_a_boundary_that_is_not_enforced_and_names_no_blocker_is_rejected(self) -> None:
        contract = _contract()
        _boundary(contract, "recovery")["blocked_by"] = ""
        errors = validate_handoff_safety_contract(contract)
        self.assertTrue(any("must name the blocker" in error for error in errors), errors)

    def test_an_enforced_boundary_that_also_names_a_blocker_is_rejected(self) -> None:
        contract = _contract()
        _boundary(contract, "merge")["blocked_by"] = "#999"
        errors = validate_handoff_safety_contract(contract)
        self.assertTrue(any("must not name a blocker" in error for error in errors), errors)

    def test_an_enforcer_that_is_not_a_dotted_symbol_reference_is_rejected(self) -> None:
        contract = _contract()
        _boundary(contract, "merge")["enforced_by"] = ["the merge gate"]
        errors = validate_handoff_safety_contract(contract)
        self.assertTrue(any("dotted omh symbol reference" in error for error in errors), errors)

    def test_an_unknown_boundary_name_is_rejected(self) -> None:
        contract = _contract()
        _boundary(contract, "merge")["boundary"] = "merge_but_friendlier"
        errors = validate_handoff_safety_contract(contract)
        self.assertTrue(any("boundaries must be exactly" in error for error in errors), errors)

    def test_a_dropped_boundary_is_rejected(self) -> None:
        contract = _contract()
        contract["boundaries"] = [entry for entry in contract["boundaries"] if entry["boundary"] != "workspace"]
        errors = validate_handoff_safety_contract(contract)
        self.assertTrue(any("boundaries must be exactly" in error for error in errors), errors)

    def test_an_unsupported_enforcement_level_is_rejected(self) -> None:
        contract = _contract()
        _boundary(contract, "merge")["enforcement"] = "mostly"
        self.assertTrue(any("enforcement is unsupported" in error for error in validate_handoff_safety_contract(contract)))

    def test_an_extra_field_on_a_boundary_is_rejected(self) -> None:
        # The entry shape is closed, so a boundary cannot smuggle in a second,
        # unvalidated claim beside the one the validator reads.
        contract = _contract()
        _boundary(contract, "merge")["mechanism"] = "vibes"
        self.assertTrue(any("keys are invalid" in error for error in validate_handoff_safety_contract(contract)))

    def test_a_contract_that_disagrees_with_its_envelope_is_rejected(self) -> None:
        envelope = _envelope()
        contract = build_task_handoff_safety_contract(envelope)
        contract["prohibited_actions"] = [action for action in contract["prohibited_actions"] if action != "merge"]
        errors = validate_handoff_safety_contract(contract, envelope=envelope)
        self.assertTrue(any("prohibited_actions must match" in error for error in errors), errors)

    def test_a_contract_that_claims_it_was_not_produced_offline_is_rejected(self) -> None:
        contract = _contract()
        contract["produced_offline"] = False
        errors = validate_handoff_safety_contract(contract)
        self.assertTrue(any("produced_offline" in error for error in errors), errors)

    def test_a_contract_that_drops_the_claim_boundary_is_rejected(self) -> None:
        contract = _contract()
        contract["claim_boundary"] = "everything is fine"
        errors = validate_handoff_safety_contract(contract)
        self.assertTrue(any("claim_boundary" in error for error in errors), errors)

    def test_a_non_object_contract_is_rejected(self) -> None:
        self.assertEqual(validate_handoff_safety_contract("contract"), ["handoff_safety_contract must be an object"])


class HandoffSafetyContractRecordTests(unittest.TestCase):
    """The contract survives compaction and cannot be silently dropped."""

    def _delegation(self) -> dict[str, object]:
        payload = build_coding_delegation_payload(_MESSAGE, executor_target="codex")
        return {**payload, "message": _MESSAGE}

    def test_the_contract_survives_the_delegation_compactor(self) -> None:
        delegation = self._delegation()
        record = build_coding_delegation_record(delegation)
        self.assertEqual(record["handoff_safety_contract"], delegation["handoff_safety_contract"])
        self.assertEqual(validate_coding_delegation_record(record), [])
        # Deep-copied, so a later mutation of the payload cannot reach the record.
        delegation["handoff_safety_contract"]["boundaries"] = []
        self.assertEqual(len(record["handoff_safety_contract"]["boundaries"]), len(SAFETY_BOUNDARY_NAMES))

    def test_the_record_survives_a_json_round_trip(self) -> None:
        record = build_coding_delegation_record(self._delegation())
        restored = json.loads(json.dumps(record))
        self.assertEqual(validate_coding_delegation_record(restored), [])
        self.assertEqual(restored["handoff_safety_contract"], record["handoff_safety_contract"])

    def test_a_raw_gate_verdict_is_split_by_the_compactor(self) -> None:
        # A caller that hands over the verdict as `evaluate_action_gate`
        # returned it still stores exactly one copy, beside the gate.
        delegation = self._delegation()
        delegation["action_gate"] = {**delegation["action_gate"], HANDOFF_SAFETY_CONTRACT_KEY: delegation.pop("handoff_safety_contract")}
        record = build_coding_delegation_record(delegation)
        self.assertNotIn(HANDOFF_SAFETY_CONTRACT_KEY, record["action_gate"])
        self.assertEqual(validate_coding_delegation_record(record), [])

    def test_a_gate_without_its_contract_is_rejected(self) -> None:
        record = build_coding_delegation_record(self._delegation())
        del record["handoff_safety_contract"]
        errors = validate_coding_delegation_record(record)
        self.assertTrue(any("requires the handoff_safety_contract" in error for error in errors), errors)

    def test_a_contract_without_its_gate_is_rejected(self) -> None:
        record = build_coding_delegation_record(self._delegation())
        del record["action_gate"]
        errors = validate_coding_delegation_record(record)
        self.assertTrue(any("requires the action_gate that produced it" in error for error in errors), errors)

    def test_a_second_copy_inside_the_gate_is_rejected(self) -> None:
        record = build_coding_delegation_record(self._delegation())
        record["action_gate"][HANDOFF_SAFETY_CONTRACT_KEY] = record["handoff_safety_contract"]
        errors = validate_coding_delegation_record(record)
        self.assertTrue(any("must not carry handoff_safety_contract" in error for error in errors), errors)

    def test_a_stored_contract_that_disagrees_with_the_stored_envelope_is_rejected(self) -> None:
        record = build_coding_delegation_record(self._delegation())
        record["handoff_safety_contract"]["merge_authority"] = "granted"
        errors = validate_coding_delegation_record(record)
        self.assertTrue(any("merge_authority must match" in error for error in errors), errors)


class ProhibitedActionTests(unittest.TestCase):
    """AC2: no hidden dispatch, no automatic merge, no arbitrary authority."""

    def test_merge_and_external_posting_are_prohibited_on_every_coding_handoff(self) -> None:
        for target in ("codex", "claude-code", "generic"):
            with self.subTest(target=target):
                payload = build_coding_delegation_payload(_MESSAGE, executor_target=target)
                contract = payload["handoff_safety_contract"]
                for never in ("merge", "external_posting", "pr_creation", "pr_revision"):
                    self.assertIn(never, contract["prohibited_actions"])
                self.assertEqual(contract["merge_authority"], "disabled")
                self.assertEqual(contract["external_action_authority"], "prepare_only")

    def test_prohibited_actions_are_the_exact_complement_of_the_allowed_set(self) -> None:
        payload = build_coding_delegation_payload(_MESSAGE, executor_target="codex")
        envelope = payload["action_gate"]["authority_envelope"]
        contract = payload["handoff_safety_contract"]
        self.assertEqual(contract["prohibited_actions"], envelope["blocked_actions"])
        self.assertEqual(set(contract["prohibited_actions"]) & set(envelope["allowed_actions"]), set())

    def test_a_non_dispatchable_owner_is_named_by_the_dispatch_boundary(self) -> None:
        dispatch = _boundary(_contract(), "dispatch")
        self.assertIn("omh.coding.fanout_dispatch.DISPATCH_COMMAND_TEMPLATES", dispatch["enforced_by"])
        self.assertIn("never spawned", dispatch["statement"])

    def test_the_storage_boundary_states_that_no_raw_prompt_is_persisted(self) -> None:
        storage = _boundary(_contract(), "storage_content")
        self.assertIn("sha256", storage["statement"])
        record = build_coding_delegation_record(
            {**build_coding_delegation_payload(_MESSAGE, executor_target="codex"), "message": _MESSAGE}
        )
        self.assertNotIn(_MESSAGE, json.dumps(record))
        self.assertEqual(record["message_length"], len(_MESSAGE))


class EvidenceRequirementTests(unittest.TestCase):
    """AC3: no start or success claim without the matching evidence."""

    def test_the_contract_covers_exactly_the_six_stages_818_names(self) -> None:
        contract = _contract()
        self.assertEqual(
            [entry["stage"] for entry in contract["evidence_requirements"]],
            ["start", "execution", "verification", "review", "ci", "merge"],
        )
        self.assertEqual(tuple(HANDOFF_EVIDENCE_STAGES), ("start", "execution", "verification", "review", "ci", "merge"))
        for entry in contract["evidence_requirements"]:
            with self.subTest(stage=entry["stage"]):
                self.assertEqual(set(entry), set(EVIDENCE_REQUIREMENT_KEYS))
                self.assertTrue(entry["observation_event"].strip())

    def test_start_is_declared_unenforced_because_no_rung_reads_it(self) -> None:
        # Decided rather than papered over: `runtime_start` is recordable but is
        # neither a claim rung nor a journal prerequisite, so the contract
        # refuses to imply a gate that does not exist.
        start = _stage(_contract(), "start")
        self.assertEqual(start["enforcement"], "declared_not_enforced")
        self.assertEqual(start["blocked_by"], "#826")
        self.assertEqual(start["claim"], "")
        self.assertNotIn("start", {claim.value for claim in RUNTIME_CLAIM_LADDER})

    def test_every_enforced_stage_names_the_claim_rung_it_gates(self) -> None:
        rungs = {claim.value for claim in RUNTIME_CLAIM_LADDER}
        for entry in _contract()["evidence_requirements"]:
            if entry["enforcement"] != "enforced":
                continue
            with self.subTest(stage=entry["stage"]):
                self.assertIn(entry["claim"], rungs)

    def test_no_success_is_claimable_without_the_matching_observation(self) -> None:
        run_id = "run-818"
        status: dict[str, object] = {"external_effects": {"run_id": run_id}}
        self.assertEqual(allowed_runtime_claims(status, validation_failed=False), [Claim.METADATA_AVAILABLE])

        # Each step adds exactly one observation and unlocks exactly one rung.
        steps: list[tuple[str, dict[str, object], Claim]] = [
            ("prepared", {"available": True}, Claim.HANDOFF_PREPARED),
            ("wrapper", {"prompt_dispatched": True}, Claim.EXECUTOR_DISPATCHED),
            ("execution", {"observed": True}, Claim.EXECUTION_OBSERVED),
            ("verification", {"observed": True}, Claim.VERIFICATION_OBSERVED),
            (
                "review",
                {"observed": True, "status": "passed", "receipt": _receipt("review", run_id)},
                Claim.REVIEW_OBSERVED,
            ),
            ("ci", {"observed": True, "status": "passed", "receipt": _receipt("ci", run_id)}, Claim.CI_OBSERVED),
            ("merge_readiness", {"observed": True, "status": "ready"}, Claim.MERGE_READY),
            ("merge", {"observed": True, "status": "merged", "receipt": _receipt("merge", run_id)}, Claim.MERGED),
        ]
        for section, value, unlocked in steps:
            with self.subTest(section=section):
                blocked = {entry["claim"] for entry in blocked_runtime_claims(
                    allowed_runtime_claims(status, validation_failed=False), validation_failed=False
                )}
                self.assertIn(unlocked.value, blocked)
                status[section] = value
                self.assertIn(unlocked, allowed_runtime_claims(status, validation_failed=False))

    def test_a_local_record_alone_cannot_claim_ci_or_merge(self) -> None:
        run_id = "run-818"
        status: dict[str, object] = {
            "external_effects": {"run_id": run_id},
            "prepared": {"available": True},
            "wrapper": {"prompt_dispatched": True},
            "execution": {"observed": True},
            "verification": {"observed": True},
            "review": {"observed": True, "status": "passed", "receipt": _receipt("review", run_id)},
            "ci": {"observed": True, "status": "passed"},
        }
        self.assertNotIn(Claim.CI_OBSERVED, allowed_runtime_claims(status, validation_failed=False))
        for result in ("attempted", "failed", "unknown"):
            with self.subTest(result=result):
                status["ci"] = {"observed": True, "status": "passed", "receipt": _receipt("ci", run_id, result=result)}
                self.assertNotIn(Claim.CI_OBSERVED, allowed_runtime_claims(status, validation_failed=False))
        status["ci"] = {"observed": True, "status": "passed", "receipt": _receipt("ci", run_id)}
        status["merge_readiness"] = {"observed": True, "status": "ready"}
        status["merge"] = {"observed": True, "status": "merged"}
        self.assertNotIn(Claim.MERGED, allowed_runtime_claims(status, validation_failed=False))

    def test_review_is_receipt_gated_like_ci_and_merge_and_the_contract_says_so(self) -> None:
        # #844 closed the last asymmetry in the ladder. Review was the one stage
        # where OMH would report "review passed" with nothing naming the surface
        # that reviewed it.
        run_id = "run-818"
        status: dict[str, object] = {
            "external_effects": {"run_id": run_id},
            "prepared": {"available": True},
            "wrapper": {"prompt_dispatched": True},
            "execution": {"observed": True},
            "verification": {"observed": True},
            "review": {"observed": True, "status": "passed"},
        }
        self.assertNotIn(Claim.REVIEW_OBSERVED, allowed_runtime_claims(status, validation_failed=False))
        # The refusal reads as a receipt gap, not as a missing review record.
        blocked = {entry["claim"]: entry["reason"] for entry in blocked_runtime_claims(
            allowed_runtime_claims(status, validation_failed=False), validation_failed=False
        )}
        self.assertIn("external effect receipt", blocked["review_observed"])
        self.assertIn("runtime_review_record", blocked["review_observed"])
        self.assertIn("before external effect receipts existed", blocked["review_observed"])

        # Only a succeeded receipt for this run's review effect lifts it.
        for result in ("attempted", "failed", "unknown"):
            with self.subTest(result=result):
                status["review"] = {
                    "observed": True,
                    "status": "passed",
                    "receipt": _receipt("review", run_id, result=result),
                }
                self.assertNotIn(Claim.REVIEW_OBSERVED, allowed_runtime_claims(status, validation_failed=False))
        status["review"] = {"observed": True, "status": "passed", "receipt": _receipt("review", run_id)}
        self.assertIn(Claim.REVIEW_OBSERVED, allowed_runtime_claims(status, validation_failed=False))

        review_receipt = _boundary(_contract(), "review_receipt")
        self.assertEqual(review_receipt["enforcement"], "enforced")
        self.assertEqual(review_receipt["blocked_by"], "")
        self.assertIn("omh.runtime.claims._receipt_cited", review_receipt["enforced_by"])

    def test_a_failed_validation_collapses_every_claim_to_metadata(self) -> None:
        status: dict[str, object] = {
            "prepared": {"available": True},
            "wrapper": {"prompt_dispatched": True},
            "execution": {"observed": True},
        }
        self.assertEqual(allowed_runtime_claims(status, validation_failed=True), [Claim.METADATA_AVAILABLE])


if __name__ == "__main__":
    unittest.main()
