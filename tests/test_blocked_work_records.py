"""Contracts for the blocked-work record family and the decision history over it.

The acceptance criteria these hold, and where each one is asserted:

#808
  AC1  raw prompts, tokens, files, and payloads cannot serialize into a record
       -> `RawContentCannotSerialize`
  AC2  blocked, failed, cancelled, and completed are distinguished
       -> `OutcomesStayApart`, `StatusVocabulariesDistinguishTerminalStates`
  AC3  hashes are non-reversible
       -> `FingerprintHashesTheClassNotTheInput`

#806
  AC1  blocked actions are explainable offline after the original turn
       -> `HistoryExplainsBlocksAfterTheTurn`
  AC2  no raw secrets, prompts, or payloads in history
       -> `RawContentCannotSerialize`, `HistoryCarriesNoRequestContent`
  AC3  an allow is never rendered as attempted or completed
       -> `AnAllowIsNeverAttemptedOrCompleted`

Store mechanics -- concurrent appends, torn tails, supersession forks -- are
inherited from `system/append_only_store.py` and proved there. What is asserted
here is the *wiring*: that this family reaches the shared walk with its own key
names, and that its chain is judged at all.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _local_package import load_local_package

load_local_package()
from omh.coding.action_gate import (
    ACTION_GATE_KEYS,
    DENIAL_KEYS,
    EXCLUSION_REASON_CODES,
    evaluate_action_gate,
)
from omh.coding.hermes_harness import _harness_status
from omh.coding.work_reporting import (
    REPORT_STATUSES,
    _ko_polite_status_sentence,
    _ko_status_sentence,
    _runtime_completion_satisfied,
)
from omh.quality.safety_preflight import (
    ACCESS_INTENTS,
    DATA_CLASSES,
    EVIDENCE_CLAIMS,
    FIELD_CLASSES,
    REMOTE_TARGET_KINDS,
    correction_reason_codes,
)
from omh.quality import safety_preflight as safety_preflight_module
from omh.runtime.artifacts import (
    export_runtime,
    runtime_observation_not_applicable,
    summarize_runtime_observation_status,
    _delegated_runtime_observation_status,
)
from omh.runtime.records import (
    OPTIONAL_RUNTIME_STORE_VALIDATORS,
    RUNTIME_BLOCK_OBSERVATION_EVENTS,
    RUNTIME_NON_LADDER_OBSERVATION_EVENTS,
    RUNTIME_OBSERVABLE_EVENTS,
    RUNTIME_OBSERVATION_EVENTS,
    RUNTIME_OBSERVATION_STATUSES,
    RUNTIME_TERMINAL_OBSERVATION_EVENTS,
)
from omh.system.append_only_store import RAW_OR_HIDDEN_KEYS
from omh.system.paths import OmhPaths
from omh.workflows import blocked_work_records as blocked_work_records_module
from omh.workflows.blocked_work_records import (
    ACTION_GATE_DENIAL_KEYS_READ,
    ACTION_GATE_VERDICT_KEYS_READ,
    ALLOW_RECOVERY_ACTION,
    BLOCKED_WORK_DECISION_KEYS,
    BLOCKED_WORK_RECORD_CONSTANT_KEYS,
    BLOCKED_WORK_RECORD_KEYS,
    BLOCKED_WORK_RECORD_SCHEMA_VERSION,
    DECISION_OUTCOMES,
    DECISION_SOURCES,
    ENFORCEMENT_MODES,
    ENFORCING_SOURCES,
    LIFECYCLE_STATES,
    OBSERVING_SOURCES,
    OUTCOME_LIFECYCLE,
    OUTCOME_WORK_CLAIMS,
    MAX_CLASS_SHAPE_ENTRIES,
    REASON_DOMAINS,
    RECOVERY_ACTIONS,
    REQUEST_CLASS_LABELS,
    REQUEST_FIELDS_READ,
    SOURCE_ENFORCEMENT,
    SOURCE_ENFORCEMENT_BLOCKERS,
    SUCCESS_OUTCOMES,
    WORK_CLAIMS,
    BlockedWorkRecordError,
    build_blocked_work_record,
    compact_blocked_work_record,
    decision_from_action_gate,
    decision_history,
    decision_reason_text,
    fingerprint_for_request,
    mint_blocked_work_record,
    project_decision_history,
    reason_codes_for_domain,
    record_blocked_work,
    recovery_action_for,
    redacted_blocked_work_ref,
    request_class_shape,
    request_fingerprint,
    validate_blocked_work_record,
    validate_blocked_work_record_store,
)
from omh.quality.safety_preflight import REQUEST_FIELDS
from omh.workflows.observation_journal import (
    CANONICAL_OBSERVATION_EVENTS,
    OBSERVATION_STATUSES,
    project_run_lifecycle,
)
from omh.wrapper.contract import STATUS_CARD_STEP_STATES, build_chat_response_from_delegation
from omh.wrapper.localized_copy import (
    SUPPORTED_COPY_LOCALES,
    recovery_action_copy,
    recovery_action_copy_ids,
)


def _request(**overrides: object) -> dict[str, object]:
    """A well-formed preflight request; every field is the class, not the value."""
    request: dict[str, object] = {
        "owner": "hermes",
        "approved_scope": "workspace",
        "message_context_mode": "bounded",
        "raw_content_included": False,
        "data_classes": ["workspace_source"],
        "workspace_roots": ["src"],
        "target_paths": ["src/login.py"],
        "access_intents": ["read"],
        "evidence_claims": ["prepared_not_observed"],
    }
    request.update(overrides)
    return request


def _record(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "source": "safety_preflight",
        "outcome": "blocked",
        "reason_domain": "safety_preflight_correction",
        "reason_code": "secret_shaped_value",
        "owner": "hermes",
        "safety_profile_revision": "rev-1",
        "request": _request(),
    }
    kwargs.update(overrides)
    return build_blocked_work_record(**kwargs)  # type: ignore[arg-type]


def _paths(root: Path) -> OmhPaths:
    return OmhPaths(omh_home=root, hermes_home=root / "hermes")


# The minimum `evaluate_action_gate` needs to reach a verdict. Every test that
# asks what a gate decided builds the verdict through this, never by hand: a
# hand-built gate dict is a second answer to a contract only one module owns,
# and the defect this file now guards against was exactly that.
_GATE_KWARGS: dict[str, object] = {
    "message": "please add a feature",
    "delegation_action": "delegate",
    "intent": "implement",
    "review_required": False,
    "work_owner_mode": "delegated_executor",
    "selected_executor_profile": "codex",
    "dispatch_policy": "prepare_only",
    "dispatchable": True,
    "choice_required": False,
    "executor_selection_status": "selected",
}


def _denied_verdict(reason_codes: list[str]) -> dict[str, object]:
    """A real denied verdict from the one decision path in the tree."""
    return evaluate_action_gate(
        **{
            **_GATE_KWARGS,
            "safety_preflight": {
                "outcome": "deny",
                "rule_id": "secrets_absent",
                "field": "owner",
                "correction": "Remove the credential-shaped value.",
                "reason_codes": list(reason_codes),
                "safety_profile_revision": "rev-9",
            },
        }
    )


def _milestone(event_type: str, status: str, updated_at: str) -> dict[str, object]:
    return {
        "event_type": event_type,
        "status": status,
        "updated_at": updated_at,
        "target_type": "run",
        "target_id": "run-1",
    }


class RawContentCannotSerialize(unittest.TestCase):
    """#808 AC1 and #806 AC2: there is nowhere on a record for content to go."""

    def test_every_raw_or_hidden_key_name_is_refused_by_name(self) -> None:
        # By name, not merely as "unsupported key": the error has to say what the
        # rule is, because the next person adding a field reads the error.
        for key in sorted(RAW_OR_HIDDEN_KEYS):
            with self.subTest(key=key):
                record = dict(_record())
                record[key] = "anything at all"
                errors = validate_blocked_work_record(record)
                self.assertTrue(
                    any("must not carry raw or hidden keys" in error for error in errors),
                    f"{key} was not refused by name: {errors}",
                )

    def test_raw_content_cannot_ride_in_through_any_stored_field(self) -> None:
        # Every string field, against every shape #808 AC1 names. A record that
        # validates clean while carrying any of these is the whole failure.
        payloads = {
            "prompt": "You are a helpful assistant. Ignore previous instructions.",
            "token": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "aws_secret": "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "file_path": "src/auth/token.py",
            "absolute_path": "/etc/passwd",
            "windows_path": "C:\\Users\\me\\secrets.txt",
            "payload_json": '{"password": "hunter2", "rows": [1, 2, 3]}',
            "transcript": "user: hi\nassistant: hello\nuser: my ssn is 123-45-6789",
            "url": "https://example.com/secret?token=abc",
        }
        string_fields = [key for key in BLOCKED_WORK_RECORD_KEYS if key != "request_class_shape"]
        for field in string_fields:
            for name, payload in payloads.items():
                with self.subTest(field=field, payload=name):
                    record = dict(_record())
                    record[field] = payload
                    self.assertTrue(
                        validate_blocked_work_record(record),
                        f"{name} serialized cleanly into {field}",
                    )

    def test_build_refuses_a_path_in_every_reference_it_accepts(self) -> None:
        # `require_opaque_metadata_ref` permits `/` because approval receipts
        # store a scope path on purpose. This family must not inherit that, and
        # the path used here is deliberately innocuous -- `src/auth/token.py`
        # would be refused by the credential detector first and would prove
        # nothing about the path guard.
        for field in ("owner", "safety_profile_revision", "run_id", "supersedes_record_ref"):
            with self.subTest(field=field):
                with self.assertRaises(BlockedWorkRecordError) as caught:
                    _record(**{field: "src/views/home.py"})
                self.assertIn("must not name a path", str(caught.exception))

    def test_the_path_guard_is_what_refuses_a_plain_workspace_path(self) -> None:
        # The guard has to be the one doing the work: a path that no other
        # detector objects to must still be refused.
        errors = validate_blocked_work_record({**_record(), "owner": "src/views/home.py"})
        self.assertTrue(any("must not name a path" in error for error in errors), errors)

    def test_the_record_has_no_free_text_field_at_all(self) -> None:
        # The structural claim behind AC1: every stored string is an opaque ref
        # or a closed vocabulary member, so a sentence has nowhere to land.
        record = _record()
        free_text = {
            key
            for key, value in record.items()
            if isinstance(value, str)
            and key not in BLOCKED_WORK_RECORD_CONSTANT_KEYS
            and " " in value
        }
        self.assertEqual(free_text, set())

    def test_a_class_shape_label_outside_the_vocabulary_is_refused(self) -> None:
        with self.assertRaises(BlockedWorkRecordError):
            _record(class_shape=["field_class:path", "src/auth/token.py"])

    def test_the_stored_shape_names_no_file_from_the_request(self) -> None:
        record = _record(request=_request(target_paths=["src/auth/token.py", "src/billing/card.py"]))
        serialized = json.dumps(record)
        self.assertNotIn("token.py", serialized)
        self.assertNotIn("billing", serialized)
        self.assertIn("field_class:path", record["request_class_shape"])


class FingerprintHashesTheClassNotTheInput(unittest.TestCase):
    """#808 AC3: the digest has no caller-authored input, so nothing inverts it."""

    def test_same_class_shape_different_values_share_a_fingerprint(self) -> None:
        first = _request(owner="hermes", target_paths=["src/a.py"], approved_scope="alpha")
        second = _request(owner="someone-else", target_paths=["docs/b.md"], approved_scope="omega")
        self.assertEqual(fingerprint_for_request(first), fingerprint_for_request(second))

    def test_a_different_class_shape_changes_the_fingerprint(self) -> None:
        base = _request()
        for label, changed in (
            ("extra path", _request(target_paths=["src/a.py", "src/b.py"])),
            ("extra intent", _request(access_intents=["read", "write"])),
            ("different data class", _request(data_classes=["workspace_metadata"])),
            ("raw content included", _request(raw_content_included=True)),
            ("full context mode", _request(message_context_mode="full")),
            ("remote target present", _request(remote_targets=[{"kind": "git_remote", "ref": "origin"}])),
        ):
            with self.subTest(label):
                self.assertNotEqual(fingerprint_for_request(base), fingerprint_for_request(changed))

    def test_the_fingerprint_cannot_be_inverted_to_a_filename(self) -> None:
        # The concrete attack: hash every candidate path the way a truncated sha
        # of a filename would be hashed, and look for the record's fingerprint.
        # It cannot appear, because no path byte was ever an input.
        target = fingerprint_for_request(_request(target_paths=["src/auth/token.py"]))
        candidates = [
            "src/auth/token.py",
            "src/auth/token.py\n",
            "token.py",
            "/etc/passwd",
            "C:\\secrets.txt",
        ]
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assertNotEqual(request_fingerprint([candidate]), target)
                self.assertNotEqual(request_fingerprint([f"field_class:{candidate}"]), target)

    def test_the_fingerprint_ignores_labels_outside_the_class_vocabulary(self) -> None:
        # A hand-edited store cannot smuggle a value into the digest by adding a
        # label: unknown labels are dropped before hashing.
        self.assertEqual(
            request_fingerprint(["field_class:path"]),
            request_fingerprint(["field_class:path", "src/auth/token.py"]),
        )

    def test_every_class_shape_label_comes_from_a_preflight_vocabulary(self) -> None:
        # Derived, not restated: a data class or access intent added to the
        # preflight cannot become an unnamed label here.
        expected = set()
        expected.update(f"field_class:{name}" for name in set(FIELD_CLASSES.values()))
        expected.update(f"data_class:{value}" for value in DATA_CLASSES)
        expected.update(f"access_intent:{value}" for value in ACCESS_INTENTS)
        expected.update(f"remote_target_kind:{value}" for value in REMOTE_TARGET_KINDS)
        expected.update(f"evidence_claim:{value}" for value in EVIDENCE_CLAIMS)
        self.assertTrue(expected <= set(REQUEST_CLASS_LABELS))

    def test_the_shape_is_a_multiset_so_counts_survive(self) -> None:
        shape = request_class_shape(_request(target_paths=["a.py", "b.py", "c.py"]))
        self.assertEqual(shape.count("field_class:path"), 4)  # 3 target paths + 1 workspace root

    def test_the_fingerprint_does_not_move_with_the_clock(self) -> None:
        first = _record(decided_at="2026-01-01T00:00:00Z")
        second = _record(decided_at="2026-06-01T00:00:00Z")
        self.assertEqual(first["request_fingerprint"], second["request_fingerprint"])
        self.assertEqual(first["decision_id"], second["decision_id"])


class OutcomesStayApart(unittest.TestCase):
    """#808 AC2: four terminal states, four different claims."""

    def test_every_outcome_has_exactly_one_lifecycle_and_one_work_claim(self) -> None:
        self.assertEqual(set(OUTCOME_LIFECYCLE), set(DECISION_OUTCOMES))
        self.assertEqual(set(OUTCOME_WORK_CLAIMS), set(DECISION_OUTCOMES))
        self.assertTrue(set(OUTCOME_LIFECYCLE.values()) <= set(LIFECYCLE_STATES))
        self.assertTrue(set(OUTCOME_WORK_CLAIMS.values()) <= set(WORK_CLAIMS))

    def test_blocked_failed_cancelled_and_completed_render_distinctly(self) -> None:
        rendered = {}
        for outcome in ("blocked", "failed", "cancelled", "completed"):
            record = _record(
                source="executor_report" if outcome != "blocked" else "safety_preflight",
                outcome=outcome,
            )
            compact = compact_blocked_work_record(record)
            rendered[outcome] = (compact["outcome"], compact["lifecycle_state"], compact["work_claim"])
        self.assertEqual(len(set(rendered.values())), 4, rendered)

    def test_blocked_never_renders_as_a_skipped_success(self) -> None:
        # The nearest success-shaped state a gate reaches is `not_required`.
        # A block must never fold into it, and must never claim completion.
        compact = compact_blocked_work_record(_record(outcome="blocked"))
        self.assertEqual(compact["lifecycle_state"], "paused")
        self.assertNotIn(compact["lifecycle_state"], {"not_required", "complete"})
        self.assertNotEqual(compact["work_claim"], "completed")
        self.assertNotIn("blocked", SUCCESS_OUTCOMES)

    def test_only_completed_is_success_shaped(self) -> None:
        self.assertEqual(
            {outcome for outcome, claim in OUTCOME_WORK_CLAIMS.items() if claim == "completed"},
            set(SUCCESS_OUTCOMES),
        )

    def test_a_blocked_record_always_offers_a_recovery(self) -> None:
        for domain in REASON_DOMAINS:
            for code in reason_codes_for_domain(domain):
                with self.subTest(domain=domain, code=code):
                    self.assertIn(recovery_action_for(domain, code), RECOVERY_ACTIONS)

    def test_a_hand_edited_lifecycle_is_rejected(self) -> None:
        record = dict(_record(outcome="blocked"))
        record["lifecycle_state"] = "terminal"
        self.assertTrue(
            any("lifecycle_state must be paused" in error for error in validate_blocked_work_record(record))
        )


class AnAllowIsNeverAttemptedOrCompleted(unittest.TestCase):
    """#806 AC3, held as a table property rather than an absence of rows."""

    def test_an_allow_is_recorded_at_all(self) -> None:
        record = _record(outcome="allowed", reason_code="allowed")
        self.assertEqual(record["outcome"], "allowed")
        self.assertEqual(validate_blocked_work_record(record), [])

    def test_an_allow_claims_no_attempt_and_no_completion(self) -> None:
        self.assertEqual(OUTCOME_WORK_CLAIMS["allowed"], "not_attempted")
        self.assertEqual(OUTCOME_LIFECYCLE["allowed"], "open")
        compact = compact_blocked_work_record(_record(outcome="allowed", reason_code="allowed"))
        self.assertEqual(compact["work_claim"], "not_attempted")
        self.assertNotEqual(compact["lifecycle_state"], "terminal")

    def test_no_outcome_maps_an_allow_onto_a_success_shaped_state(self) -> None:
        for outcome, claim in OUTCOME_WORK_CLAIMS.items():
            if outcome == "allowed":
                self.assertNotEqual(claim, "completed")
                self.assertNotEqual(claim, "attempted_not_completed")

    def test_an_allow_promises_no_recovery(self) -> None:
        record = _record(outcome="allowed", reason_code="allowed")
        self.assertEqual(record["recovery_action"], ALLOW_RECOVERY_ACTION)

    def test_the_history_renders_an_allow_as_not_attempted(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            record_blocked_work(
                paths,
                source="safety_preflight",
                outcome="allowed",
                reason_domain="safety_preflight_correction",
                reason_code="allowed",
                owner="hermes",
                safety_profile_revision="rev-1",
                request=_request(),
            )
            history = decision_history(paths)
            self.assertEqual(len(history["entries"]), 1)
            entry = history["entries"][0]
            self.assertEqual(entry["outcome"], "allowed")
            self.assertEqual(entry["work_claim"], "not_attempted")
            self.assertEqual(history["summary"]["outcome_counts"]["allowed"], 1)
            self.assertEqual(history["summary"]["blocked_count"], 0)


class EnforcingVersusObserving(unittest.TestCase):
    """A hook cannot mint a record claiming OMH blocked anything."""

    def test_the_two_source_classes_do_not_overlap_and_cover_the_vocabulary(self) -> None:
        self.assertEqual(set(ENFORCING_SOURCES) & set(OBSERVING_SOURCES), set())
        self.assertEqual(set(ENFORCING_SOURCES) | set(OBSERVING_SOURCES), set(DECISION_SOURCES))
        self.assertEqual(set(SOURCE_ENFORCEMENT), set(DECISION_SOURCES))
        self.assertTrue(set(SOURCE_ENFORCEMENT.values()) <= set(ENFORCEMENT_MODES))

    def test_an_observing_source_is_pinned_to_declared_not_enforced(self) -> None:
        for source in OBSERVING_SOURCES:
            with self.subTest(source=source):
                record = _record(source=source, outcome="blocked")
                self.assertEqual(record["enforcement"], "declared_not_enforced")
                self.assertEqual(record["enforcement_blocker"], SOURCE_ENFORCEMENT_BLOCKERS[source])

    def test_the_plugin_hook_lane_states_the_contract_that_blocks_it(self) -> None:
        # `pre_tool_call` returns injected context or None and `pre_verify`'s
        # only action value is "continue"; neither can deny. The record says so.
        record = _record(source="plugin_hook_observation", outcome="blocked")
        self.assertEqual(record["enforcement_blocker"], "hermes_plugin_hooks_have_no_deny_channel")

    def test_an_observing_source_cannot_mint_an_allow(self) -> None:
        for source in OBSERVING_SOURCES:
            with self.subTest(source=source):
                with self.assertRaises(BlockedWorkRecordError) as caught:
                    _record(source=source, outcome="allowed", reason_code="allowed")
                self.assertIn("observes decisions", str(caught.exception))

    def test_a_hand_edited_store_cannot_claim_a_hook_enforced_anything(self) -> None:
        record = dict(_record(source="plugin_hook_observation", outcome="blocked"))
        record["enforcement"] = "enforced"
        record["enforcement_blocker"] = ""
        errors = validate_blocked_work_record(record)
        self.assertTrue(any("enforcement must be declared_not_enforced" in error for error in errors))

    def test_an_enforcing_source_carries_no_blocker(self) -> None:
        for source in ENFORCING_SOURCES:
            with self.subTest(source=source):
                self.assertEqual(_record(source=source)["enforcement_blocker"], "")


class ReasonCodesJoinExistingVocabularies(unittest.TestCase):
    """No fifth explanation vocabulary: the record cites a code and joins for prose."""

    def test_every_domain_resolves_to_a_vocabulary_that_already_exists(self) -> None:
        for domain in REASON_DOMAINS:
            with self.subTest(domain=domain):
                self.assertTrue(reason_codes_for_domain(domain))

    def test_the_action_gate_domain_stays_in_step_with_the_gate(self) -> None:
        self.assertEqual(
            set(reason_codes_for_domain("action_gate_exclusion")),
            set(EXCLUSION_REASON_CODES),
        )

    def test_the_preflight_domain_stays_in_step_with_the_corrections(self) -> None:
        self.assertEqual(
            set(reason_codes_for_domain("safety_preflight_correction")),
            set(correction_reason_codes()),
        )

    def test_every_blocked_reason_code_resolves_to_prose(self) -> None:
        for domain in REASON_DOMAINS:
            for code in reason_codes_for_domain(domain):
                if code == "allowed":
                    continue
                with self.subTest(domain=domain, code=code):
                    self.assertTrue(decision_reason_text(domain, code), f"{domain}/{code} has no prose")

    def test_a_code_from_the_wrong_domain_is_refused(self) -> None:
        with self.assertRaises(BlockedWorkRecordError):
            _record(reason_domain="runtime_claim_block", reason_code="secret_shaped_value")

    def test_an_unrecognised_code_renders_empty_rather_than_as_itself(self) -> None:
        self.assertEqual(decision_reason_text("safety_preflight_correction", "no_such_code"), "")


class HistoryExplainsBlocksAfterTheTurn(unittest.TestCase):
    """#806 AC1: the answer survives the turn that produced it."""

    def test_a_block_is_explainable_from_the_store_alone(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            record_blocked_work(
                paths,
                source="safety_preflight",
                outcome="blocked",
                reason_domain="safety_preflight_correction",
                reason_code="secret_shaped_value",
                owner="hermes",
                safety_profile_revision="rev-1",
                request=_request(),
            )
            # A later turn: nothing but the store on disk. No request, no
            # payload, no gate verdict in memory.
            raw = paths.runtime_blocked_work_records_path.read_text(encoding="utf-8")
            reloaded = [json.loads(line) for line in raw.splitlines() if line.strip()]
            history = project_decision_history(reloaded)
            entry = history["entries"][0]
            self.assertEqual(entry["outcome"], "blocked")
            self.assertEqual(entry["reason_code"], "secret_shaped_value")
            self.assertTrue(entry["reason"])
            self.assertIn(entry["recovery_action"], RECOVERY_ACTIONS)
            self.assertEqual(history["summary"]["explainable_blocked_count"], 1)
            self.assertEqual(history["summary"]["unexplainable_blocked_count"], 0)

    def test_a_record_with_no_run_is_still_in_the_history(self) -> None:
        # The case the family exists for: a preflight denial happens before a
        # run exists, so `run_id` is empty and per-run storage would lose it.
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            record = record_blocked_work(
                paths,
                source="safety_preflight",
                outcome="blocked",
                reason_domain="safety_preflight_correction",
                reason_code="owner_missing",
                owner="hermes",
                safety_profile_revision="rev-1",
                request=_request(),
            )
            self.assertEqual(record["run_id"], "")
            self.assertEqual(len(decision_history(paths)["entries"]), 1)

    def test_only_the_current_record_for_a_decision_is_rendered(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            common = {
                "source": "safety_preflight",
                "reason_domain": "safety_preflight_correction",
                "owner": "hermes",
                "safety_profile_revision": "rev-1",
                "request": _request(),
            }
            record_blocked_work(paths, outcome="blocked", reason_code="owner_missing", **common)
            record_blocked_work(paths, outcome="allowed", reason_code="allowed", **common)
            history = decision_history(paths)
            self.assertEqual(len(history["entries"]), 1)
            self.assertEqual(history["entries"][0]["outcome"], "allowed")
            self.assertEqual(history["summary"]["superseded_count"], 1)

    def test_a_repeat_block_is_recorded_and_the_head_reports_the_latest(self) -> None:
        # Two distinct blocks that share a class shape are two blocks. Folding
        # them onto one record dated at the first was not idempotent minting: a
        # class shape is deliberately shared by every request of that shape, so
        # the fold collapsed different requests on different turns and then
        # reported the wrong time for the one record it kept.
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            kwargs = {
                "source": "safety_preflight",
                "outcome": "blocked",
                "reason_domain": "safety_preflight_correction",
                "reason_code": "owner_missing",
                "owner": "hermes",
                "safety_profile_revision": "rev-1",
                "request": _request(),
            }
            first = mint_blocked_work_record(paths, now="2026-01-01T00:00:00Z", **kwargs)
            second = mint_blocked_work_record(paths, now="2026-06-01T00:00:00Z", **kwargs)
            self.assertEqual(first["outcome"], "recorded")
            self.assertEqual(second["outcome"], "repeat_recorded")
            self.assertTrue(second["minted"])
            self.assertNotEqual(first["record_id"], second["record_id"])
            stored = paths.runtime_blocked_work_records_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(stored), 2)
            history = decision_history(paths)
            # One decision, two records, and the head is the later occurrence.
            self.assertEqual(history["summary"]["decision_count"], 1)
            self.assertEqual(history["summary"]["record_count"], 2)
            self.assertEqual(history["summary"]["superseded_count"], 1)
            self.assertEqual(history["entries"][0]["decided_at"], "2026-06-01T00:00:00Z")

    def test_the_repeat_chain_still_validates_as_one_line(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            kwargs = {
                "source": "safety_preflight",
                "outcome": "blocked",
                "reason_domain": "safety_preflight_correction",
                "reason_code": "owner_missing",
                "owner": "hermes",
                "safety_profile_revision": "rev-1",
                "request": _request(),
            }
            for stamp in ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z"):
                mint_blocked_work_record(paths, now=stamp, **kwargs)
            report = validate_blocked_work_record_store(paths.runtime_blocked_work_records_path)
            self.assertTrue(report["ok"], report["errors"])
            self.assertEqual(report["record_count"], 3)

    def test_the_history_is_scopeable_to_one_run(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            common = {
                "source": "safety_preflight",
                "outcome": "blocked",
                "reason_domain": "safety_preflight_correction",
                "reason_code": "owner_missing",
                "owner": "hermes",
                "safety_profile_revision": "rev-1",
                "request": _request(),
            }
            record_blocked_work(paths, run_id="run-a", **common)
            record_blocked_work(paths, run_id="run-b", **common)
            self.assertEqual(len(decision_history(paths, run_id="run-a")["entries"]), 1)
            self.assertEqual(len(decision_history(paths)["entries"]), 2)


class HistoryCarriesNoRequestContent(unittest.TestCase):
    """#806 AC2, checked on the rendered projection rather than the stored line."""

    def test_the_projection_carries_no_path_no_secret_and_no_prompt(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            record_blocked_work(
                paths,
                source="safety_preflight",
                outcome="blocked",
                reason_domain="safety_preflight_correction",
                reason_code="secret_shaped_value",
                owner="hermes",
                safety_profile_revision="rev-1",
                request=_request(target_paths=["src/auth/token.py"], approved_scope="billing"),
            )
            serialized = json.dumps(decision_history(paths))
            for leak in ("token.py", "src/auth", "billing", "hunter2"):
                self.assertNotIn(leak, serialized)

    def test_the_projection_drops_a_hand_edited_label(self) -> None:
        record = dict(_record())
        record["request_class_shape"] = ["field_class:path", "src/auth/token.py"]
        compact = compact_blocked_work_record(record)
        self.assertEqual(compact["request_class_shape"], ["field_class:path"])

    def test_the_projection_folds_a_hand_edited_reference(self) -> None:
        record = dict(_record())
        record["owner"] = "https://example.com/leak?token=abc"
        self.assertTrue(compact_blocked_work_record(record)["owner"].startswith("ref-"))

    def test_an_unrecognised_vocabulary_value_renders_empty(self) -> None:
        record = dict(_record())
        record["outcome"] = "totally-made-up"
        self.assertEqual(compact_blocked_work_record(record)["outcome"], "")


class StoreWiring(unittest.TestCase):
    """The shared mechanics are reached, with this family's own key names."""

    def test_the_store_validates_clean_and_counts_its_records(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            record_blocked_work(
                paths,
                source="safety_preflight",
                outcome="blocked",
                reason_domain="safety_preflight_correction",
                reason_code="owner_missing",
                owner="hermes",
                safety_profile_revision="rev-1",
                request=_request(),
            )
            report = validate_blocked_work_record_store(paths.runtime_blocked_work_records_path)
            self.assertTrue(report["ok"], report["errors"])
            self.assertEqual(report["record_count"], 1)

    def test_the_supersede_walk_reports_this_family_key_names(self) -> None:
        # The wiring assertion: the shared walk is reached and told `record_id`
        # and `supersedes_record_ref`, not the receipt names it defaults to.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "blocked_work_records.jsonl"
            record = dict(_record())
            record["supersedes_record_ref"] = "blocked-work-does-not-exist"
            path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
            report = validate_blocked_work_record_store(path)
            self.assertFalse(report["ok"])
            joined = " ".join(report["errors"])
            self.assertIn("supersedes_record_ref", joined)
            self.assertIn("earlier record", joined)
            self.assertNotIn("receipt", joined)

    def test_an_unparseable_line_is_reported_by_the_shared_walk(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "blocked_work_records.jsonl"
            path.write_text("{not json\n", encoding="utf-8")
            self.assertFalse(validate_blocked_work_record_store(path)["ok"])

    def test_a_write_failure_returns_rather_than_raises_and_logs_a_sidecar(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            result = mint_blocked_work_record(
                paths,
                source="safety_preflight",
                outcome="blocked",
                reason_domain="safety_preflight_correction",
                reason_code="no_such_code",
                owner="hermes",
                safety_profile_revision="rev-1",
                request=_request(),
            )
            self.assertFalse(result["minted"])
            self.assertEqual(result["outcome"], "refused")
            self.assertTrue(result["error"])

    def test_the_family_registers_its_validator_under_its_own_store(self) -> None:
        # #846: one registry keyed by store, one consumer that dispatches per
        # store. This family's record id is `record_id`, not the `receipt_id`
        # its two siblings carry, so the label a fault is reported under is part
        # of the registration rather than hardcoded in the consumer.
        entries = {entry.store_name: entry for entry in OPTIONAL_RUNTIME_STORE_VALIDATORS}
        self.assertIn("blocked_work_records.jsonl", entries)
        entry = entries["blocked_work_records.jsonl"]
        self.assertEqual(entry.record_id_key, "record_id")
        self.assertEqual(entry.validator(_record()), [])
        self.assertTrue(entry.validator({"schema_version": "approval_receipt/v1"}))

    def test_the_store_lives_beside_its_siblings_and_not_inside_a_run(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            self.assertEqual(
                paths.runtime_blocked_work_records_path.parent,
                paths.runtime_approval_receipts_path.parent,
            )
            self.assertNotIn("runs", paths.runtime_blocked_work_records_path.parts)

    def test_the_schema_version_is_pinned(self) -> None:
        self.assertEqual(_record()["schema_version"], BLOCKED_WORK_RECORD_SCHEMA_VERSION)
        self.assertEqual(BLOCKED_WORK_RECORD_SCHEMA_VERSION, "blocked_work_record/v1")


class StatusVocabulariesDistinguishTerminalStates(unittest.TestCase):
    """#808 AC2 where the four states are actually read: the status vocabularies."""

    def test_cancelled_is_recordable_and_not_only_projectable(self) -> None:
        self.assertIn("cancelled", OBSERVATION_STATUSES)
        self.assertIn("cancelled", RUNTIME_OBSERVATION_STATUSES)

    def test_the_two_observation_status_tuples_stay_equal(self) -> None:
        # The CLI's `--status` choices come from one and every value it offers
        # is written through the other; a member in one only is a flag that
        # fails at the write.
        self.assertEqual(set(OBSERVATION_STATUSES), set(RUNTIME_OBSERVATION_STATUSES))

    def test_the_terminal_events_are_reachable_from_the_cli(self) -> None:
        self.assertTrue(set(RUNTIME_TERMINAL_OBSERVATION_EVENTS) <= set(RUNTIME_OBSERVABLE_EVENTS))
        self.assertTrue(set(RUNTIME_TERMINAL_OBSERVATION_EVENTS) <= set(CANONICAL_OBSERVATION_EVENTS))

    def test_the_terminal_events_stay_out_of_the_milestone_ladder(self) -> None:
        # Adding them to the ladder would make every run permanently missing
        # three milestones it was never meant to reach.
        self.assertEqual(set(RUNTIME_TERMINAL_OBSERVATION_EVENTS) & set(RUNTIME_OBSERVATION_EVENTS), set())

    def test_a_step_can_report_failed_and_cancelled_not_only_blocked(self) -> None:
        for state in ("blocked", "failed", "cancelled"):
            self.assertIn(state, STATUS_CARD_STEP_STATES)

    def test_a_blocked_step_state_is_not_the_success_shaped_one(self) -> None:
        self.assertIn("not_required", STATUS_CARD_STEP_STATES)
        self.assertNotEqual("blocked", "not_required")

    def test_a_work_report_can_say_cancelled(self) -> None:
        for status in ("blocked", "cancelled", "completed", "failed"):
            self.assertIn(status, REPORT_STATUSES)


class CrossModuleKeyReadsArePinned(unittest.TestCase):
    """Every key this module reads off another module's payload, re-derived from source.

    The defect these hold against is not a typo, it is a class of bug: this
    module reads dicts owned elsewhere, and a key that does not exist reads as
    the default rather than as an error, so the lane keeps working and reports
    something false. A hand-built fixture cannot catch it, because the fixture
    is written against the same wrong assumption as the code. So the reads are
    derived from the source text and checked against the owning module's own
    exported tuples, in the shape `tests/test_broad_exception_policy.py` uses.
    """

    @staticmethod
    def _gets_on(function_name: str, receiver_names: set[str]) -> set[str]:
        source = Path(inspect.getsourcefile(blocked_work_records_module)).read_text(encoding="utf-8")
        tree = ast.parse(source)
        found: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != function_name:
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call) or not isinstance(inner.func, ast.Attribute):
                    continue
                if inner.func.attr not in {"get", "pop"}:
                    continue
                receiver = inner.func.value
                if not isinstance(receiver, ast.Name) or receiver.id not in receiver_names:
                    continue
                if inner.args and isinstance(inner.args[0], ast.Constant) and isinstance(inner.args[0].value, str):
                    found.add(inner.args[0].value)
        return found

    def test_the_declared_verdict_reads_are_what_the_source_actually_reads(self) -> None:
        self.assertEqual(
            self._gets_on("decision_from_action_gate", {"gate"}),
            set(ACTION_GATE_VERDICT_KEYS_READ),
        )

    def test_the_declared_denial_reads_are_what_the_source_actually_reads(self) -> None:
        self.assertEqual(
            self._gets_on("decision_from_action_gate", {"denial_map"}),
            set(ACTION_GATE_DENIAL_KEYS_READ),
        )

    def test_every_verdict_key_read_is_a_key_the_action_gate_exports(self) -> None:
        self.assertTrue(
            set(ACTION_GATE_VERDICT_KEYS_READ) <= set(ACTION_GATE_KEYS),
            sorted(set(ACTION_GATE_VERDICT_KEYS_READ) - set(ACTION_GATE_KEYS)),
        )

    def test_every_denial_key_read_is_a_key_the_denial_contract_exports(self) -> None:
        # The one that was false: `reason_code` is not a member and never was.
        self.assertTrue(
            set(ACTION_GATE_DENIAL_KEYS_READ) <= set(DENIAL_KEYS),
            sorted(set(ACTION_GATE_DENIAL_KEYS_READ) - set(DENIAL_KEYS)),
        )
        self.assertNotIn("reason_code", DENIAL_KEYS)

    def test_a_real_denial_carries_the_plural_key_and_not_the_singular_one(self) -> None:
        denial = _denied_verdict(["secret_shaped_value"])["denial"]
        self.assertEqual(set(denial), set(DENIAL_KEYS))
        self.assertEqual(denial["reason_codes"], ["secret_shaped_value"])

    def test_every_request_field_read_by_name_is_declared_by_the_preflight(self) -> None:
        # The second read of the same kind: three request fields are read by
        # name rather than through `REQUEST_FIELDS`, so a rename there would
        # silently stop them contributing and move every fingerprint.
        self.assertTrue(
            set(REQUEST_FIELDS_READ) <= set(REQUEST_FIELDS),
            sorted(set(REQUEST_FIELDS_READ) - set(REQUEST_FIELDS)),
        )

    def test_the_destination_kind_key_is_the_preflight_own_nested_key(self) -> None:
        self.assertIn(
            blocked_work_records_module._DESTINATION_KIND_KEY,
            safety_preflight_module.DESTINATION_FIELD_CLASSES,
        )


class TheRenderPathAppliesThePathGuard(unittest.TestCase):
    """#808 AC1 at the third surface: write, validate, and render must agree."""

    def test_the_render_path_refuses_the_value_the_validator_refuses(self) -> None:
        # Deliberately the same value `RawContentCannotSerialize` uses. A
        # validator and a renderer reading one record and reaching opposite
        # answers about it is the defect, so the proof has to be the same bytes.
        record = {**_record(), "owner": "src/views/home.py"}
        self.assertTrue(any("must not name a path" in error for error in validate_blocked_work_record(record)))
        rendered = compact_blocked_work_record(record)["owner"]
        self.assertNotEqual(rendered, "src/views/home.py")
        self.assertTrue(rendered.startswith("ref-"), rendered)

    def test_the_history_projection_publishes_no_path_either(self) -> None:
        record = {**_record(), "owner": "src/views/home.py"}
        serialized = json.dumps(project_decision_history([record]))
        self.assertNotIn("src/views/home.py", serialized)
        self.assertNotIn("views", serialized)

    def test_every_path_shape_folds_at_render_not_only_the_absolute_ones(self) -> None:
        # The accurate scope of what leaked: a leading `/`, any `\`, and `../`
        # were already folded by the shared guard. A relative POSIX path
        # starting with an alphanumeric was not.
        for value in ("src/views/home.py", "docs/a.md", "a/b", "/etc/passwd", "..\\x", "../up.py"):
            with self.subTest(value=value):
                self.assertTrue(redacted_blocked_work_ref(value).startswith("ref-"), value)

    def test_a_plain_opaque_handle_still_renders_as_itself(self) -> None:
        # The guard must not fold ordinary identifiers, or every rendered record
        # becomes a wall of digests.
        for value in ("hermes", "rev-1", "shape-abc123", "run-42"):
            with self.subTest(value=value):
                self.assertEqual(redacted_blocked_work_ref(value), value)


class TheClassShapeBudgetKeepsEveryDistinctLabel(unittest.TestCase):
    """#808 AC3: a bounded field must not merge two shapes that differ."""

    def test_a_large_request_keeps_every_label_it_carries(self) -> None:
        big = _request(target_paths=[f"src/f{index}.py" for index in range(400)], raw_content_included=True)
        shape = request_class_shape(big)
        self.assertLessEqual(len(shape), MAX_CLASS_SHAPE_ENTRIES)
        small = _request(target_paths=["src/f0.py"], raw_content_included=True)
        self.assertTrue(
            set(request_class_shape(small)) <= set(shape),
            sorted(set(request_class_shape(small)) - set(shape)),
        )
        self.assertIn("raw_content:included", shape)
        self.assertIn("message_context_mode:bounded", shape)

    def test_raw_content_never_merges_with_a_request_that_excluded_it(self) -> None:
        # The concrete failure of alphabetical truncation: `raw_content:*` sorts
        # last, so on a large request it was the first label dropped and the two
        # shapes collapsed onto one fingerprint.
        paths = [f"src/f{index}.py" for index in range(400)]
        included = _request(target_paths=paths, raw_content_included=True)
        excluded = _request(target_paths=paths, raw_content_included=False)
        self.assertNotEqual(fingerprint_for_request(included), fingerprint_for_request(excluded))

    def test_a_large_request_still_distinguishes_every_late_sorting_label(self) -> None:
        paths = [f"src/f{index}.py" for index in range(400)]
        base = _request(target_paths=paths)
        for label, changed in (
            ("full context mode", _request(target_paths=paths, message_context_mode="full")),
            (
                "remote target present",
                _request(target_paths=paths, remote_targets=[{"kind": "git_remote", "ref": "origin"}]),
            ),
        ):
            with self.subTest(label):
                self.assertNotEqual(fingerprint_for_request(base), fingerprint_for_request(changed))

    def test_a_declared_empty_list_claims_no_occurrence(self) -> None:
        # A `max(1, ...)` floor made a request declaring no workspace paths ship
        # a record whose shape claimed one.
        self.assertEqual(request_class_shape(_request(target_paths=[])).count("field_class:path"), 1)
        empty = _request(target_paths=[], workspace_roots=[])
        self.assertEqual(request_class_shape(empty).count("field_class:path"), 0)

    def test_a_declared_empty_list_does_not_share_a_shape_with_a_named_file(self) -> None:
        self.assertNotEqual(
            fingerprint_for_request(_request(target_paths=[])),
            fingerprint_for_request(_request(target_paths=["src/a.py"])),
        )

    def test_the_shape_stays_inside_its_budget(self) -> None:
        shape = request_class_shape(_request(target_paths=[f"src/f{index}.py" for index in range(4000)]))
        self.assertLessEqual(len(shape), MAX_CLASS_SHAPE_ENTRIES)
        self.assertEqual(validate_blocked_work_record(_record(class_shape=shape)), [])


class CancelledSurvivesEveryProjection(unittest.TestCase):
    """#808 AC2 end to end: a vocabulary member the code does not back is a label."""

    def test_a_cancelled_status_projects_as_cancelled(self) -> None:
        # `_fold_event` ignored the status and fell through to its
        # `!= "observed"` return, so a run someone stopped projected as one that
        # had merely not got there yet.
        projection = project_run_lifecycle(
            [
                {
                    "run_id": "run-1",
                    "event": "executor_dispatch_observed",
                    "status": "cancelled",
                    "event_id": "e1",
                    "observed_at": "2026-01-01T00:00:00Z",
                }
            ],
            run_id="run-1",
        )
        self.assertTrue(projection["cancelled"])
        self.assertEqual(projection["observation_status"], "cancelled")
        self.assertFalse(projection["blocked"])
        self.assertFalse(projection["failed"])

    def test_the_four_statuses_project_to_four_different_answers(self) -> None:
        projected = {}
        for status in ("blocked", "cancelled", "failed", "observed"):
            projection = project_run_lifecycle(
                [
                    {
                        "run_id": "run-1",
                        "event": "prepared_handoff_created",
                        "status": status,
                        "event_id": f"e-{status}",
                        "observed_at": "2026-01-01T00:00:00Z",
                    }
                ],
                run_id="run-1",
            )
            projected[status] = (
                projection["observation_status"],
                projection["blocked"],
                projection["cancelled"],
                projection["failed"],
            )
        self.assertEqual(len(set(projected.values())), 4, projected)

    def test_a_cancelled_milestone_is_unsatisfied(self) -> None:
        summary = summarize_runtime_observation_status([_milestone("ci", "cancelled", "2026-01-01T00:00:00Z")])
        self.assertEqual(summary["cancelled_events"], ["ci"])
        self.assertIn("ci", summary["unsatisfied_events"])

    def test_a_cancelled_milestone_never_reaches_the_completion_claim(self) -> None:
        summary = summarize_runtime_observation_status(
            [_milestone(event, "observed", "2026-01-01T00:00:00Z") for event in RUNTIME_OBSERVATION_EVENTS]
            + [_milestone("ci", "cancelled", "2026-02-01T00:00:00Z")]
        )
        self.assertFalse(_runtime_completion_satisfied(summary))
        status = _harness_status(
            observed_events=set(summary["observed_events"]),
            blocked_events=set(summary["blocked_events"]),
            failed_events=set(summary["failed_events"]),
            cancelled_events=set(summary["cancelled_events"]),
            unsatisfied_events=set(summary["unsatisfied_events"]),
            projection_missing_evidence=set(),
        )
        self.assertEqual(status, "cancelled")

    def test_the_lifecycle_projection_status_says_cancelled_not_merely_missing(self) -> None:
        # The second producer of `runtime_observation_status/v1`: a cancelled
        # latest event landed in neither the blocked nor the failed list, so the
        # only signal left was `missing_events` and a stopped run rendered
        # exactly like one that had not got there yet.
        delegated = _delegated_runtime_observation_status(
            {},
            {
                "journal_event_count": 3,
                "latest_event": {"event": "executor_dispatch_observed", "status": "cancelled"},
            },
            next_action="wait_for_executor_evidence",
        )
        self.assertEqual(delegated["cancelled_events"], ["worker_dispatch"])
        self.assertIn("worker_dispatch", delegated["unsatisfied_events"])

    def test_every_runtime_observation_status_producer_publishes_the_same_keys(self) -> None:
        # One schema version, three producers. A reader that has to ask which
        # one built a payload before it knows which keys exist is reading three
        # schemas under one version.
        summary = summarize_runtime_observation_status([_milestone("ci", "observed", "2026-01-01T00:00:00Z")])
        delegated = _delegated_runtime_observation_status(
            {},
            {"journal_event_count": 1, "latest_event": {"event": "ci_result_observed", "status": "observed"}},
            next_action="record_merge_readiness",
        )
        not_applicable = runtime_observation_not_applicable("no runtime handoff")
        for other, label in ((delegated, "lifecycle_projection"), (not_applicable, "not_applicable")):
            with self.subTest(label):
                self.assertTrue(
                    set(summary) <= set(other),
                    sorted(set(summary) - set(other)),
                )

    def test_the_work_report_says_cancelled_in_korean_and_not_in_progress(self) -> None:
        self.assertNotEqual(_ko_status_sentence("작업", "cancelled"), _ko_status_sentence("작업", "in_progress"))
        self.assertNotEqual(
            _ko_polite_status_sentence("작업", "cancelled"),
            _ko_polite_status_sentence("작업", "in_progress"),
        )
        self.assertIn("취소", _ko_status_sentence("작업", "cancelled"))
        self.assertIn("취소", _ko_polite_status_sentence("작업", "cancelled"))

    def test_every_report_status_gets_its_own_korean_sentence(self) -> None:
        for renderer in (_ko_status_sentence, _ko_polite_status_sentence):
            rendered = {status: renderer("작업", status) for status in REPORT_STATUSES}
            with self.subTest(renderer=renderer.__name__):
                self.assertEqual(len(set(rendered.values())), len(REPORT_STATUSES), rendered)


class ABlockIsRecoverableAndNotTerminal(unittest.TestCase):
    """A block that cleared must stop being the answer to "what next"."""

    def test_blocked_is_not_classified_as_a_terminal_runtime_event(self) -> None:
        self.assertNotIn("blocked", RUNTIME_TERMINAL_OBSERVATION_EVENTS)
        self.assertIn("blocked", RUNTIME_BLOCK_OBSERVATION_EVENTS)
        self.assertEqual(
            set(RUNTIME_NON_LADDER_OBSERVATION_EVENTS),
            set(RUNTIME_BLOCK_OBSERVATION_EVENTS) | set(RUNTIME_TERMINAL_OBSERVATION_EVENTS),
        )

    def test_a_standing_block_still_pins_the_next_action(self) -> None:
        summary = summarize_runtime_observation_status(
            [
                _milestone("runtime_start", "observed", "2026-01-01T00:00:00Z"),
                _milestone("blocked", "blocked", "2026-02-01T00:00:00Z"),
            ]
        )
        self.assertEqual(summary["next_action"], "surface_runtime_blocker:blocked")
        self.assertEqual(summary["standing_block"], "blocked")

    def test_a_block_that_cleared_no_longer_pins_the_next_action(self) -> None:
        summary = summarize_runtime_observation_status(
            [
                _milestone("blocked", "blocked", "2026-01-01T00:00:00Z"),
                _milestone("runtime_start", "observed", "2026-02-01T00:00:00Z"),
                _milestone("merge", "observed", "2026-03-01T00:00:00Z"),
            ]
        )
        self.assertEqual(summary["standing_block"], "")
        self.assertNotIn("surface_runtime_block", summary["next_action"])
        self.assertTrue(summary["next_action"].startswith("record_runtime_observation:"), summary["next_action"])

    def test_a_terminal_event_still_pins_after_later_observations(self) -> None:
        # The asymmetry is the point: a cancellation is not undone by a later
        # milestone the way a block is.
        for event in RUNTIME_TERMINAL_OBSERVATION_EVENTS:
            with self.subTest(event=event):
                summary = summarize_runtime_observation_status(
                    [
                        _milestone(event, "observed", "2026-01-01T00:00:00Z"),
                        _milestone("merge", "observed", "2026-03-01T00:00:00Z"),
                    ]
                )
                self.assertEqual(summary["next_action"], f"surface_runtime_{event}:{event}")


class CoverageIsWhatTheDocsSay(unittest.TestCase):
    """The module and the docs claim what one call site provides, and say so."""

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[1]

    def _sources(self) -> dict[Path, str]:
        root = self._repo_root() / "src"
        return {path: path.read_text(encoding="utf-8") for path in root.rglob("*.py")}

    def test_only_the_delegation_lane_mints_records_today(self) -> None:
        # A pin on the coverage claim, not on the code. When a second producer
        # is wired, this fails -- and the fix is to update the "What actually
        # produces and reads these records today" section of the module
        # docstring and the matching paragraph in docs/ARCHITECTURE.md, then
        # widen this set.
        pattern = re.compile(r"\b(mint_blocked_work_record|record_blocked_work)\s*\(")
        producers = {
            path.relative_to(self._repo_root()).as_posix()
            for path, text in self._sources().items()
            if pattern.search(text) and path.name != "blocked_work_records.py"
        }
        self.assertEqual(producers, {"src/commands/coding.py"})

    def test_the_history_has_at_least_one_production_read_surface(self) -> None:
        readers = {
            path.relative_to(self._repo_root()).as_posix()
            for path, text in self._sources().items()
            if re.search(r"\bdecision_history\s*\(", text) and path.name != "blocked_work_records.py"
        }
        self.assertIn("src/runtime/artifacts.py", readers)

    def test_the_export_carries_the_decision_history(self) -> None:
        # #806 asks for a block to be explainable after the turn. Until the
        # export carried this, the only way to answer it was to open the JSONL.
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            record_blocked_work(
                paths,
                source="safety_preflight",
                outcome="blocked",
                reason_domain="safety_preflight_correction",
                reason_code="secret_shaped_value",
                owner="hermes",
                safety_profile_revision="rev-1",
                request=_request(target_paths=["src/auth/token.py"]),
            )
            exported = export_runtime(paths)
            history = exported["decision_history"]
            self.assertEqual(history["summary"]["blocked_count"], 1)
            self.assertEqual(history["entries"][0]["reason_code"], "secret_shaped_value")
            self.assertTrue(history["entries"][0]["reason"])
            self.assertNotIn("token.py", json.dumps(exported["decision_history"]))

    def test_the_module_docstring_states_the_sources_that_produce_nothing(self) -> None:
        # The docs must not assert coverage the wiring does not provide.
        doc = blocked_work_records_module.__doc__ or ""
        for source in ("approval_gate", "runtime_claim_gate"):
            self.assertIn(source, doc)
        self.assertIn("mint nothing yet", doc)

    def test_the_architecture_doc_states_the_same_gap(self) -> None:
        text = (self._repo_root() / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("mint nothing yet", text)


class RecoveryActionCopy(unittest.TestCase):
    """A closed id vocabulary with per-locale copy and an English fallback."""

    def test_every_recovery_action_has_copy(self) -> None:
        self.assertEqual(set(recovery_action_copy_ids()), set(RECOVERY_ACTIONS))

    def test_every_supported_locale_falls_back_to_english_at_worst(self) -> None:
        for action in RECOVERY_ACTIONS:
            for locale in SUPPORTED_COPY_LOCALES:
                with self.subTest(action=action, locale=locale):
                    self.assertTrue(recovery_action_copy(action, locale=locale))

    def test_an_unknown_id_renders_empty_rather_than_as_a_token(self) -> None:
        self.assertEqual(recovery_action_copy("no_such_recovery"), "")

    def test_the_copy_is_a_constant_and_interpolates_nothing(self) -> None:
        for action in RECOVERY_ACTIONS:
            for locale in SUPPORTED_COPY_LOCALES:
                line = recovery_action_copy(action, locale=locale)
                self.assertNotIn("{", line)
                self.assertNotIn("}", line)


class DeniedDelegationLeavesADurableRecord(unittest.TestCase):
    """Anchor A: the persistence gap this change exists to close."""

    def test_a_denied_gate_maps_to_a_blocked_decision(self) -> None:
        # Built by `evaluate_action_gate` and never by hand. The first version of
        # this test wrote its own denial dict carrying `reason_code` -- singular,
        # and not a member of `DENIAL_KEYS` at all -- so it agreed with a lane
        # that read a key the gate has never emitted, and every real denial
        # recorded the fallback code instead of the one that was raised.
        verdict = _denied_verdict(["secret_shaped_value"])
        self.assertNotIn("reason_code", verdict["denial"])
        decision = decision_from_action_gate(verdict, owner="hermes", safety_profile_revision="", request=_request())
        self.assertEqual(decision["outcome"], "blocked")
        self.assertEqual(decision["reason_code"], "secret_shaped_value")
        self.assertEqual(decision["reason_domain"], "safety_preflight_correction")
        self.assertEqual(decision["source"], "safety_preflight")
        self.assertEqual(decision["safety_profile_revision"], "rev-9")

    def test_an_allowed_gate_maps_to_an_allowed_decision(self) -> None:
        decision = decision_from_action_gate(
            {"outcome": "allow", "safety_profile_revision": "rev-9"},
            owner="hermes",
            safety_profile_revision="",
            request=_request(),
        )
        self.assertEqual(decision["outcome"], "allowed")
        self.assertEqual(decision["reason_code"], "allowed")
        self.assertEqual(decision["recovery_action"], ALLOW_RECOVERY_ACTION)

    def test_a_denial_with_an_unknown_code_is_still_recorded_as_blocked(self) -> None:
        # Recording it as an allow to keep the vocabulary tidy would be the one
        # unrecoverable mistake this family can make.
        decision = decision_from_action_gate(
            {"outcome": "deny", "denial": {"reason_codes": ["from_the_future"]}},
            owner="hermes",
            safety_profile_revision="rev-1",
            request=_request(),
        )
        self.assertEqual(decision["outcome"], "blocked")
        self.assertEqual(decision["reason_code"], "safety_preflight_denied")

    def test_a_revision_drift_denial_records_the_weaker_true_claim(self) -> None:
        # `_revision_drift_denial` carries `carried:`/`live:` codes that belong
        # to no explanation vocabulary. The record says the gate denied before
        # authority was granted -- true of every denial -- and does not invent a
        # code for the drift.
        verdict = evaluate_action_gate(
            **{
                **_GATE_KWARGS,
                "safety_preflight": {"outcome": "allow", "safety_profile_revision": "rev-9"},
                "live_safety_profile_revision": "rev-10",
            }
        )
        self.assertEqual(verdict["outcome"], "deny")
        self.assertEqual(verdict["denial"]["rule_id"], "safety_profile_revision_drift")
        decision = decision_from_action_gate(verdict, owner="hermes", safety_profile_revision="", request=_request())
        self.assertEqual(decision["outcome"], "blocked")
        self.assertEqual(decision["reason_code"], "safety_preflight_denied")
        self.assertEqual(decision["reason_domain"], "action_gate_exclusion")
        self.assertTrue(decision_reason_text(decision["reason_domain"], decision["reason_code"]))

    def test_an_action_gate_exclusion_code_keeps_its_own_domain(self) -> None:
        decision = decision_from_action_gate(
            {"outcome": "deny", "denial": {"reason_codes": ["withheld_pending_approval"]}},
            owner="hermes",
            safety_profile_revision="rev-1",
            request=_request(),
        )
        self.assertEqual(decision["reason_domain"], "action_gate_exclusion")
        self.assertEqual(decision["reason_code"], "withheld_pending_approval")
        self.assertEqual(decision["source"], "action_gate")
        self.assertEqual(decision["recovery_action"], "request_approval")

    def test_the_decision_block_carries_every_key_a_recording_surface_reads(self) -> None:
        # `wrapper.contract` reads `recovery_action` off this block. It did not
        # exist on it, so the read always missed and fell through to a
        # recomputation that happened to agree.
        decision = decision_from_action_gate(
            _denied_verdict(["secret_shaped_value"]),
            owner="hermes",
            safety_profile_revision="",
            request=_request(),
        )
        self.assertEqual(set(decision), set(BLOCKED_WORK_DECISION_KEYS))

    def test_the_decision_block_never_carries_the_request_itself(self) -> None:
        # It is embedded in the delegation payload and that payload is
        # serialized, so a returned request would put every path a caller named
        # into an artifact this family exists to keep them out of.
        decision = decision_from_action_gate(
            _denied_verdict(["secret_shaped_value"]),
            owner="hermes",
            safety_profile_revision="",
            request=_request(target_paths=["src/auth/token.py"]),
        )
        self.assertNotIn("request", decision)
        self.assertNotIn("token.py", json.dumps(decision))

    def test_the_denial_card_states_the_recovery_and_pauses_rather_than_skips(self) -> None:
        payload = {
            "delegation": {"action": "delegate", "intent": "implement", "recommended_workflow": "coding"},
            "action_gate": {
                "outcome": "deny",
                "denial": {
                    "rule_id": "secrets_absent",
                    "field": "owner",
                    "reason_code": "secret_shaped_value",
                    "correction": "Remove the credential-shaped value and pass an opaque reference instead.",
                },
            },
            "blocked_work_decision": {
                "outcome": "blocked",
                "reason_domain": "safety_preflight_correction",
                "reason_code": "secret_shaped_value",
                "recovery_action": "remove_prohibited_content",
            },
        }
        response = build_chat_response_from_delegation(payload)
        state = response["state"]
        self.assertEqual(state["work_lifecycle_state"], "paused")
        self.assertEqual(state["work_claim"], "not_attempted")
        self.assertEqual(state["recovery_action"], "remove_prohibited_content")
        self.assertTrue(state["recovery_action_copy"])
        self.assertIn(state["recovery_action_copy"], response["body"])
        self.assertFalse(state["dispatchable"])

    def test_the_denial_card_speaks_a_declared_locale_and_defaults_to_english(self) -> None:
        # Localization is opt-in here, never sniffed: a delegation payload
        # carries no request text, so a detected locale would be a guess.
        payload = {
            "delegation": {"action": "delegate", "intent": "implement", "recommended_workflow": "coding"},
            "action_gate": {"outcome": "deny", "denial": {"reason_code": "secret_shaped_value"}},
            "blocked_work_decision": {
                "outcome": "blocked",
                "reason_domain": "safety_preflight_correction",
                "reason_code": "secret_shaped_value",
                "recovery_action": "remove_prohibited_content",
            },
        }
        english = build_chat_response_from_delegation(payload)["state"]["recovery_action_copy"]
        korean = build_chat_response_from_delegation({**payload, "copy_locale": "ko"})["state"][
            "recovery_action_copy"
        ]
        self.assertEqual(english, recovery_action_copy("remove_prohibited_content", locale="en"))
        self.assertEqual(korean, recovery_action_copy("remove_prohibited_content", locale="ko"))
        self.assertNotEqual(english, korean)
        # An unsupported declaration falls back rather than failing.
        unknown = build_chat_response_from_delegation({**payload, "copy_locale": "xx"})["state"][
            "recovery_action_copy"
        ]
        self.assertEqual(unknown, english)


if __name__ == "__main__":
    unittest.main()
