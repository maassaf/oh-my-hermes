"""Contracts for `approval_receipt/v1` (issue #807).

Grouped by acceptance criterion:

- AC1: an expired or revoked approval cannot satisfy another run.
- AC2: the exact added path, endpoint, or tool is named.
- AC3: an approval never proves the host applied it.

Plus the store invariants the family rests on: five distinct refusals for the
five dimensions an approval binds, supersession and revocation that link instead
of overwriting, idempotent minting, a mint that never raises into the
confirmation flow, and an append-only store that survives concurrency and a torn
tail.

Every clock is injected. `now` is passed to every mint and every read, so no
test sleeps and no assertion depends on wall-clock timing -- Windows CI is an
enforcing target and a timing-sensitive receipt store is exactly the shape that
loses there first.
"""

from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.coding.action_gate import CONFIRMATION_LADDERS as GATE_CONFIRMATION_LADDERS
from omh.coding.action_gate import MUTATING_ACTIONS as GATE_MUTATING_ACTIONS
from omh.paths import resolve_paths
from omh.runtime.records import (
    APPROVAL_RECEIPT_RECORD_KEYS,
    OPTIONAL_RUNTIME_STORE_VALIDATORS,
)
from omh.workflows.approval_receipts import (
    APPROVABLE_ACTIONS,
    APPROVAL_DECISION_SCHEMA_VERSION,
    APPROVAL_DECISIONS,
    APPROVAL_MINT_RESULT_SCHEMA_VERSION,
    APPROVAL_RECEIPT_CONSTANT_KEYS,
    APPROVAL_RECEIPT_KEYS,
    APPROVAL_RECEIPT_SCHEMA_VERSION,
    APPROVAL_TTL_SECONDS,
    CLAIM_BOUNDARY,
    CONFIRMATION_LADDERS,
    REFUSAL_ABSENT,
    REFUSAL_ACTION,
    REFUSAL_CODES,
    REFUSAL_DENIED,
    REFUSAL_EXPIRED,
    REFUSAL_OWNER,
    REFUSAL_REASONS,
    REFUSAL_REVISION,
    REFUSAL_REVOKED,
    REFUSAL_RUN,
    REFUSAL_SCOPE,
    REFUSAL_SUPERSEDED,
    SCOPE_CLASSES,
    SCOPE_REFUSAL_CODES,
    UNKNOWN_AGE_SECONDS,
    ApprovalReceiptError,
    append_approval_receipt,
    approval_age_seconds,
    approval_id,
    approval_satisfies_request,
    approval_satisfies_request_in,
    build_approval_receipt,
    compact_approval_receipt,
    mint_approval_receipt,
    read_approval_mint_failures,
    read_approval_receipts,
    record_approval_receipt,
    validate_approval_receipt,
    validate_approval_receipt_store,
)
from omh.runtime.artifacts import validate_runtime
from omh.workflows.goal_loop import LOOP_ACTIONS


REPO_ROOT = Path(__file__).resolve().parents[1]

T0 = "2026-08-06T12:00:00Z"
T_PLUS_10S = "2026-08-06T12:00:10Z"
T_PLUS_2H = "2026-08-06T14:00:00Z"

GRANT: dict[str, str] = {
    "approved_action": "repo_edit",
    "scope_class": "filesystem_path",
    "scope_ref": "src/omh/paths.py",
    "owner": "codex",
    "run_id": "run-1",
    "safety_profile_revision": "rev-1",
    "confirmation_ladder": "operator_confirmation",
}
# The same five dimensions, as a satisfaction request (no ladder: which ladder
# asked the question is a property of the answer, not of the request).
REQUEST: dict[str, str] = {key: value for key, value in GRANT.items() if key != "confirmation_ladder"}


def _paths(tmp: str):
    return resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")


def _valid_receipt(**overrides: Any) -> dict[str, Any]:
    return build_approval_receipt(**{**GRANT, "decided_at": T0, **overrides})


class ApprovalReceiptShapeTests(unittest.TestCase):
    def test_receipt_record_keys_are_exact(self) -> None:
        record = _valid_receipt()
        self.assertEqual(tuple(sorted(record)), APPROVAL_RECEIPT_KEYS)
        self.assertEqual(record["schema_version"], APPROVAL_RECEIPT_SCHEMA_VERSION)
        self.assertEqual(record["claim_boundary"], CLAIM_BOUNDARY)
        self.assertEqual(record["privacy"], "metadata_only")
        self.assertEqual(validate_approval_receipt(record), [])

    def test_the_compactor_renders_every_key_that_is_not_a_fixed_constant(self) -> None:
        # A key in the tuple and missing from the compactor is a field that is
        # stored and never seen. That drift has cost this repo twice.
        record = _valid_receipt()
        rendered = compact_approval_receipt(record, now=T_PLUS_10S)
        expected = set(APPROVAL_RECEIPT_KEYS) - set(APPROVAL_RECEIPT_CONSTANT_KEYS)
        self.assertTrue(expected <= set(rendered), sorted(expected - set(rendered)))
        self.assertEqual(rendered["age_seconds"], 10)
        self.assertFalse(rendered["expired"])

    def test_closed_vocabularies_are_enforced_at_build_and_at_render(self) -> None:
        for field, value in (
            ("approved_action", "research"),
            ("scope_class", "anything"),
            ("confirmation_ladder", "someone_asked_nicely"),
            ("decision", "applied"),
        ):
            with self.subTest(field=field), self.assertRaises(ApprovalReceiptError):
                _valid_receipt(**{field: value})
        tampered = {**_valid_receipt(), "decision": "applied"}
        self.assertEqual(compact_approval_receipt(tampered, now=T0)["decision"], "")

    def test_the_approvable_action_vocabulary_stays_inside_the_gate_vocabulary(self) -> None:
        # Held locally rather than imported: the action gate will call into this
        # module, so importing it here would be a cycle. The relationship is
        # pinned instead.
        self.assertTrue(set(APPROVABLE_ACTIONS) <= set(LOOP_ACTIONS))
        self.assertTrue(set(GATE_MUTATING_ACTIONS) <= set(APPROVABLE_ACTIONS))
        self.assertEqual(set(CONFIRMATION_LADDERS), set(GATE_CONFIRMATION_LADDERS))

    def test_the_scope_vocabulary_covers_what_an_escalation_can_add(self) -> None:
        # AC3 of #807 names three: the added path, endpoint, or tool. The other
        # two are the escalations the confirmation ladders themselves ask about.
        self.assertEqual(
            SCOPE_CLASSES,
            ("executor_profile", "filesystem_path", "network_endpoint", "permission_profile", "tool"),
        )

    def test_a_scope_ref_must_name_something_inside_the_workspace(self) -> None:
        for scope_ref in ("../outside.py", "src/../../outside.py", "C:/Windows/system32", "https://evil.test/x"):
            with self.subTest(scope_ref=scope_ref), self.assertRaises(ApprovalReceiptError):
                _valid_receipt(scope_ref=scope_ref)

    def test_the_receipt_store_is_registered_beside_the_other_runtime_records(self) -> None:
        self.assertEqual(APPROVAL_RECEIPT_RECORD_KEYS, APPROVAL_RECEIPT_KEYS)
        entries = {entry.store_name: entry for entry in OPTIONAL_RUNTIME_STORE_VALIDATORS}
        self.assertIn("approval_receipts.jsonl", entries)
        entry = entries["approval_receipts.jsonl"]
        self.assertEqual(entry.record_id_key, "receipt_id")
        self.assertEqual(entry.validator(_valid_receipt()), [])
        self.assertTrue(entry.validator({"schema_version": "wrong"}))
        # #846 joined this store to the one registry. It used to need a sibling
        # tuple because the consumer applied every entry to the single store it
        # read, and this validator rejects anything that is not an approval
        # receipt -- which is why the consumer must dispatch per store. That
        # dispatch is proved in tests/test_runtime_artifacts.py.
        self.assertTrue(entry.validator({"schema_version": "external_effect_receipt/v1"}))


class ApprovalNeverAssertsExecutionTests(unittest.TestCase):
    """AC3: an approval never proves the host applied it."""

    def test_the_claim_boundary_denies_execution_on_every_record(self) -> None:
        record = _valid_receipt()
        self.assertIn("proves consent was given", record["claim_boundary"])
        self.assertIn("not dispatch, execution", record["claim_boundary"])

    def test_a_record_cannot_be_constructed_in_a_shape_that_claims_execution(self) -> None:
        for key in ("applied", "executed", "execution_result", "exit_code", "host_applied", "observed_result", "result"):
            with self.subTest(key=key):
                errors = validate_approval_receipt({**_valid_receipt(), key: "yes"})
                self.assertTrue(
                    any("must not carry execution-claim keys" in error for error in errors),
                    errors,
                )

    def test_raw_and_hidden_keys_are_refused_by_name(self) -> None:
        errors = validate_approval_receipt({**_valid_receipt(), "prompt": "do the thing"})
        self.assertTrue(any("must not carry raw or hidden keys" in error for error in errors), errors)

    def test_the_decision_vocabulary_has_no_execution_state(self) -> None:
        self.assertEqual(APPROVAL_DECISIONS, ("granted", "denied", "revoked"))
        errors = validate_approval_receipt({**_valid_receipt(), "decision": "executed"})
        self.assertTrue(any("decision is unsupported" in error for error in errors), errors)


class ApprovalScopeRefusalTests(unittest.TestCase):
    """The five dimensions an approval binds, each with its own refusal code."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.paths = _paths(self._tmp.name)
        mint_approval_receipt(self.paths, now=T0, **GRANT)

    def _refusal(self, **overrides: str) -> dict[str, Any]:
        return approval_satisfies_request(self.paths, now=T_PLUS_10S, **{**REQUEST, **overrides})

    def test_the_exact_request_is_satisfied(self) -> None:
        decision = self._refusal()
        self.assertTrue(decision["satisfied"])
        self.assertEqual(decision["reason_code"], "")
        self.assertEqual(decision["schema_version"], APPROVAL_DECISION_SCHEMA_VERSION)
        self.assertEqual(decision["expires_after_seconds"], APPROVAL_TTL_SECONDS)
        self.assertTrue(decision["receipt_id"])

    def test_five_dimensions_refuse_with_five_distinct_codes(self) -> None:
        cases = (
            ("sibling action", {"approved_action": "pr_creation"}, REFUSAL_ACTION),
            ("broader scope", {"scope_ref": "src"}, REFUSAL_SCOPE),
            ("another owner", {"owner": "claude-code"}, REFUSAL_OWNER),
            ("another run", {"run_id": "run-2"}, REFUSAL_RUN),
            ("stale safety revision", {"safety_profile_revision": "rev-2"}, REFUSAL_REVISION),
        )
        seen = []
        for label, override, expected in cases:
            with self.subTest(case=label):
                decision = self._refusal(**override)
                self.assertFalse(decision["satisfied"])
                self.assertEqual(decision["reason_code"], expected)
                self.assertEqual(decision["reason"], REFUSAL_REASONS[expected])
                seen.append(decision["reason_code"])
        self.assertEqual(sorted(seen), sorted(set(seen)))
        self.assertEqual(set(seen), set(SCOPE_REFUSAL_CODES))

    def test_widening_is_structurally_impossible_in_both_directions(self) -> None:
        # An approval for a narrower scope satisfies that same narrower scope
        # only: the parent directory is refused, and so is a sibling under it.
        for scope_ref in ("src", "src/omh", "src/omh/paths.py.bak", "src/omh/local_store.py"):
            with self.subTest(scope_ref=scope_ref):
                self.assertEqual(self._refusal(scope_ref=scope_ref)["reason_code"], REFUSAL_SCOPE)

    def test_another_scope_class_over_the_same_ref_is_still_another_scope(self) -> None:
        self.assertEqual(self._refusal(scope_class="tool")["reason_code"], REFUSAL_SCOPE)

    def test_an_empty_store_refuses_with_the_absent_code(self) -> None:
        with TemporaryDirectory() as tmp:
            decision = approval_satisfies_request(_paths(tmp), now=T0, **REQUEST)
            self.assertEqual(decision["reason_code"], REFUSAL_ABSENT)
            self.assertEqual(decision["receipt_id"], "")
            self.assertEqual(decision["age_seconds"], UNKNOWN_AGE_SECONDS)

    def test_every_refusal_code_has_a_constant_reason_line(self) -> None:
        self.assertEqual(sorted(REFUSAL_REASONS), sorted(REFUSAL_CODES))
        for code in REFUSAL_CODES:
            self.assertTrue(REFUSAL_REASONS[code].strip())


class ApprovalLifetimeTests(unittest.TestCase):
    """AC1: an expired or revoked approval cannot satisfy another run."""

    def test_an_expired_approval_refuses_at_read_time(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            mint_approval_receipt(paths, now=T0, **GRANT)
            self.assertTrue(approval_satisfies_request(paths, now=T_PLUS_10S, **REQUEST)["satisfied"])
            expired = approval_satisfies_request(paths, now=T_PLUS_2H, **REQUEST)
            self.assertFalse(expired["satisfied"])
            self.assertEqual(expired["reason_code"], REFUSAL_EXPIRED)
            # Nothing on disk changed: expiry is recomputed, never written.
            stored = read_approval_receipts(paths)
            self.assertEqual(len(stored), 1)
            self.assertEqual(sorted(stored[0]), list(APPROVAL_RECEIPT_KEYS))
            self.assertNotIn("expires_at", stored[0])

    def test_an_expired_approval_cannot_satisfy_another_run(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            mint_approval_receipt(paths, now=T0, **GRANT)
            decision = approval_satisfies_request(paths, now=T_PLUS_2H, **{**REQUEST, "run_id": "run-2"})
            self.assertFalse(decision["satisfied"])
            self.assertEqual(decision["reason_code"], REFUSAL_RUN)

    def test_a_revoked_approval_cannot_satisfy_its_own_run_or_another(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            mint_approval_receipt(paths, now=T0, **GRANT)
            mint_approval_receipt(paths, now="2026-08-06T12:00:05Z", **{**GRANT, "decision": "revoked"})
            same_run = approval_satisfies_request(paths, now=T_PLUS_10S, **REQUEST)
            other_run = approval_satisfies_request(paths, now=T_PLUS_10S, **{**REQUEST, "run_id": "run-2"})
            self.assertEqual(same_run["reason_code"], REFUSAL_REVOKED)
            self.assertFalse(other_run["satisfied"])
            self.assertEqual(other_run["reason_code"], REFUSAL_ABSENT)

    def test_a_denied_confirmation_is_recorded_and_never_satisfies(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            mint_approval_receipt(paths, now=T0, **{**GRANT, "decision": "denied"})
            decision = approval_satisfies_request(paths, now=T_PLUS_10S, **REQUEST)
            self.assertEqual(decision["reason_code"], REFUSAL_DENIED)

    def test_a_decision_stamped_in_the_future_cannot_be_shown_to_be_fresh(self) -> None:
        # Clamping a future stamp to age zero would make "edit the timestamp
        # forward" a way to widen the window from disk.
        self.assertEqual(approval_age_seconds("2026-08-06T13:00:00Z", T0), UNKNOWN_AGE_SECONDS)
        self.assertEqual(approval_age_seconds("not-a-timestamp", T0), UNKNOWN_AGE_SECONDS)
        forged = _valid_receipt(decided_at="2026-08-06T13:00:00Z")
        decision = approval_satisfies_request_in([forged], now=T0, **REQUEST)
        self.assertEqual(decision["reason_code"], REFUSAL_EXPIRED)

    def test_the_window_lives_in_code_and_is_bounded(self) -> None:
        # Shorter than the six-hour horizon an advisory limit signal gets, and
        # far below the seven-day cap a stale-read override may carry.
        self.assertEqual(APPROVAL_TTL_SECONDS, 60 * 60)
        self.assertLess(APPROVAL_TTL_SECONDS, 6 * 60 * 60)


class ApprovalSupersessionTests(unittest.TestCase):
    def test_a_superseded_approval_refuses_and_its_predecessor_stays_byte_identical(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            mint_approval_receipt(paths, now=T0, **GRANT)
            first_line = paths.runtime_approval_receipts_path.read_bytes()
            # The safety profile moved, so the operator answers the same
            # question again under the new revision.
            mint_approval_receipt(
                paths,
                now="2026-08-06T12:00:05Z",
                **{**GRANT, "safety_profile_revision": "rev-2"},
            )

            stale = approval_satisfies_request(paths, now=T_PLUS_10S, **REQUEST)
            fresh = approval_satisfies_request(
                paths,
                now=T_PLUS_10S,
                **{**REQUEST, "safety_profile_revision": "rev-2"},
            )

            self.assertFalse(stale["satisfied"])
            self.assertEqual(stale["reason_code"], REFUSAL_SUPERSEDED)
            self.assertTrue(fresh["satisfied"])
            # History is linked, never rewritten: the predecessor's line is the
            # same bytes it was before the successor landed.
            lines = paths.runtime_approval_receipts_path.read_bytes().splitlines(keepends=True)
            self.assertEqual(lines[0], first_line)
            receipts = read_approval_receipts(paths)
            self.assertEqual(receipts[1]["supersedes_receipt_ref"], receipts[0]["receipt_id"])

    def test_answers_to_one_confirmation_share_one_chain(self) -> None:
        identity = approval_id(
            run_id=GRANT["run_id"],
            owner=GRANT["owner"],
            approved_action=GRANT["approved_action"],
            scope_class=GRANT["scope_class"],
            scope_ref=GRANT["scope_ref"],
        )
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            mint_approval_receipt(paths, now=T0, **GRANT)
            mint_approval_receipt(paths, now="2026-08-06T12:00:05Z", **{**GRANT, "decision": "revoked"})
            self.assertEqual(
                [receipt["approval_id"] for receipt in read_approval_receipts(paths)],
                [identity, identity],
            )
            self.assertEqual(len(read_approval_receipts(paths, approval_id=identity)), 2)


class ApprovalStoreValidatorTests(unittest.TestCase):
    def test_the_store_validator_rejects_a_self_cycle(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            record = _valid_receipt()
            forged = {**record, "supersedes_receipt_ref": record["receipt_id"]}
            path = paths.runtime_approval_receipts_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(forged, sort_keys=True) + "\n", encoding="utf-8")
            result = validate_approval_receipt_store(path)
            self.assertFalse(result["ok"])
            self.assertTrue(any("must not name itself" in error for error in result["errors"]), result["errors"])

    def test_the_store_validator_rejects_a_fork(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            first = record_approval_receipt(paths, now=T0, **GRANT)
            # Two answers that both believe they replaced the first one: the
            # store can no longer say which is current.
            for revision in ("rev-2", "rev-3"):
                append_approval_receipt(
                    paths,
                    build_approval_receipt(
                        **{**GRANT, "safety_profile_revision": revision},
                        decided_at="2026-08-06T12:00:05Z",
                        supersedes_receipt_ref=first["receipt_id"],
                    ),
                )
            result = validate_approval_receipt_store(paths.runtime_approval_receipts_path)
            self.assertFalse(result["ok"])
            self.assertTrue(any("forks the chain" in error for error in result["errors"]), result["errors"])

    def test_the_store_validator_rejects_a_link_to_a_receipt_that_does_not_exist(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            append_approval_receipt(
                paths,
                build_approval_receipt(**GRANT, decided_at=T0, supersedes_receipt_ref="approval-receipt-nope"),
            )
            result = validate_approval_receipt_store(paths.runtime_approval_receipts_path)
            self.assertFalse(result["ok"])
            self.assertTrue(
                any("does not name an earlier receipt" in error for error in result["errors"]),
                result["errors"],
            )

    def test_runtime_validate_really_opens_the_approval_store(self) -> None:
        """The registry had no consumer, so the chain validator was unreachable.

        A forked chain is the fault that matters: two answers that both believe
        they replaced the same predecessor make "the current answer" ambiguous,
        and an ambiguous approval is the one thing a consent record must never
        be. Before this wiring `omh runtime validate` reported `ok` over it.
        """
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            first = record_approval_receipt(paths, now=T0, **GRANT)
            for revision in ("rev-2", "rev-3"):
                append_approval_receipt(
                    paths,
                    build_approval_receipt(
                        **{**GRANT, "safety_profile_revision": revision},
                        decided_at=T_PLUS_10S,
                        supersedes_receipt_ref=first["receipt_id"],
                    ),
                )
            result = validate_runtime(paths)
            self.assertIn("approval_receipts", result)
            self.assertFalse(result["approval_receipts"]["ok"])
            self.assertFalse(result["ok"])
            self.assertTrue(
                any("forks the chain" in error for error in result["approval_receipts"]["errors"]),
                result["approval_receipts"]["errors"],
            )

    def test_runtime_validate_stays_green_on_a_clean_or_absent_store(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            self.assertTrue(validate_runtime(paths)["approval_receipts"]["ok"])
            record_approval_receipt(paths, now=T0, **GRANT)
            self.assertTrue(validate_runtime(paths)["approval_receipts"]["ok"])

    def test_a_hand_moved_approval_id_is_refused(self) -> None:
        # Without this an edited store could move one grant onto another
        # question's chain and have it supersede an approval it never answered.
        forged = {**_valid_receipt(), "approval_id": "approval-0000000000000000"}
        errors = validate_approval_receipt(forged)
        self.assertTrue(any("does not identify its own" in error for error in errors), errors)


class ApprovalMintTests(unittest.TestCase):
    def test_minting_is_idempotent_while_the_answer_is_live(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            first = mint_approval_receipt(paths, now=T0, **GRANT)
            again = mint_approval_receipt(paths, now="2026-08-06T12:00:05Z", **GRANT)
            third = mint_approval_receipt(paths, now=T_PLUS_10S, **GRANT)

            self.assertEqual(first["schema_version"], APPROVAL_MINT_RESULT_SCHEMA_VERSION)
            self.assertEqual(first["outcome"], "recorded")
            self.assertTrue(first["minted"])
            for repeat in (again, third):
                self.assertEqual(repeat["outcome"], "already_recorded")
                self.assertFalse(repeat["minted"])
                self.assertEqual(repeat["receipt_id"], first["receipt_id"])
            self.assertEqual(len(read_approval_receipts(paths)), 1)

    def test_consent_re_given_after_expiry_is_new_consent(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            first = mint_approval_receipt(paths, now=T0, **GRANT)
            renewed = mint_approval_receipt(paths, now=T_PLUS_2H, **GRANT)
            self.assertEqual(renewed["outcome"], "recorded")
            self.assertNotEqual(renewed["receipt_id"], first["receipt_id"])
            self.assertEqual(len(read_approval_receipts(paths)), 2)
            self.assertTrue(approval_satisfies_request(paths, now=T_PLUS_2H, **REQUEST)["satisfied"])

    def test_a_mint_failure_neither_raises_nor_loses_the_receipt(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            # The store path is unwritable: a directory stands where the file
            # belongs, which is what an OS-level failure looks like from here.
            store_path = paths.runtime_approval_receipts_path
            store_path.parent.mkdir(parents=True, exist_ok=True)
            store_path.mkdir()

            failed = mint_approval_receipt(paths, now=T0, **GRANT)

            self.assertEqual(failed["schema_version"], APPROVAL_MINT_RESULT_SCHEMA_VERSION)
            self.assertFalse(failed["minted"])
            self.assertEqual(failed["outcome"], "not_written")
            self.assertTrue(failed["error"])
            self.assertEqual(failed["receipt_id"], "")
            failures = read_approval_mint_failures(store_path)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["outcome"], "not_written")
            self.assertEqual(failures[0]["approved_action"], GRANT["approved_action"])
            self.assertEqual(failures[0]["decision"], "granted")

    def test_a_refused_mint_is_returned_not_raised(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            refused = mint_approval_receipt(paths, now=T0, **{**GRANT, "approved_action": "research"})
            self.assertEqual(refused["outcome"], "refused")
            self.assertFalse(refused["minted"])
            self.assertEqual(read_approval_receipts(paths), [])
            self.assertEqual(read_approval_mint_failures(paths.runtime_approval_receipts_path)[0]["outcome"], "refused")

    def test_the_strict_entry_point_still_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ApprovalReceiptError):
                record_approval_receipt(_paths(tmp), now=T0, **{**GRANT, "approved_action": "research"})


class ApprovalStoreDurabilityTests(unittest.TestCase):
    def test_concurrent_appends_do_not_interleave(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            writers = 12
            barrier = threading.Barrier(writers)
            failures: list[Exception] = []

            def append(index: int) -> None:
                record = build_approval_receipt(
                    **{**GRANT, "run_id": f"run-{index}", "scope_ref": f"src/omh/mod_{index}.py"},
                    decided_at=T0,
                )
                barrier.wait()
                try:
                    append_approval_receipt(paths, record)
                except Exception as exc:  # reported on the main thread, never silently dropped
                    failures.append(exc)

            threads = [threading.Thread(target=append, args=(index,)) for index in range(writers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(failures, [])
            lines = paths.runtime_approval_receipts_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), writers)
            for line in lines:
                self.assertEqual(validate_approval_receipt(json.loads(line)), [])
            self.assertEqual(
                sorted(json.loads(line)["run_id"] for line in lines),
                sorted(f"run-{index}" for index in range(writers)),
            )

    def test_concurrent_answers_to_one_confirmation_keep_an_unbroken_chain(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            writers = 8
            barrier = threading.Barrier(writers)
            failures: list[Exception] = []

            def answer(index: int) -> None:
                barrier.wait()
                try:
                    record_approval_receipt(
                        paths,
                        now=T0,
                        **{**GRANT, "safety_profile_revision": f"rev-{index}"},
                    )
                except Exception as exc:  # reported on the main thread, never silently dropped
                    failures.append(exc)

            threads = [threading.Thread(target=answer, args=(index,)) for index in range(writers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(failures, [])
            result = validate_approval_receipt_store(paths.runtime_approval_receipts_path)
            receipts = read_approval_receipts(paths)
            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(len(receipts), writers)
            links = [receipt["supersedes_receipt_ref"] for receipt in receipts[1:]]
            self.assertEqual(links, [receipt["receipt_id"] for receipt in receipts[:-1]])

    def test_a_torn_last_line_cannot_swallow_the_next_append(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            record_approval_receipt(paths, now=T0, **GRANT)
            path = paths.runtime_approval_receipts_path
            # A short write: the process died mid-line, so the tail has no
            # newline terminating it.
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"schema_version": "approval_rec')

            record_approval_receipt(paths, now=T0, **{**GRANT, "run_id": "run-2"})

            lines = path.read_text(encoding="utf-8").splitlines()
            store = validate_approval_receipt_store(path)

            self.assertEqual(len(lines), 3)
            self.assertEqual(json.loads(lines[0])["run_id"], "run-1")
            self.assertEqual(lines[1], '{"schema_version": "approval_rec')
            self.assertEqual(json.loads(lines[2])["run_id"], "run-2")
            # The torn line is the only casualty, and the store says so once.
            self.assertEqual(len(store["errors"]), 1, store["errors"])
            self.assertIn(":2:", store["errors"][0])
            self.assertEqual([receipt["run_id"] for receipt in read_approval_receipts(paths)], ["run-1", "run-2"])


class ApprovalCliTests(unittest.TestCase):
    def test_the_approvals_view_reads_the_store_in_plain_text_and_json(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            base = ["--omh-home", str(paths.omh_home), "--hermes-home", str(paths.hermes_home)]
            mint_approval_receipt(paths, now=T0, **GRANT)

            status, text, stderr = run_cli(base + ["runtime", "approvals"], output_json=False)
            self.assertEqual(status, 0, stderr)
            self.assertIn("Approval receipts (1 shown)", text)
            self.assertIn(GRANT["scope_ref"], text)
            self.assertIn("granted", text)
            self.assertIn("proves consent was given", text)

            status, stdout, stderr = run_cli(base + ["runtime", "approvals", "--json"])
            self.assertEqual(status, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["schema_version"], "runtime_approval_receipts_view/v1")
            self.assertEqual(payload["receipt_count"], 1)
            self.assertTrue(payload["store_ok"])
            self.assertEqual(payload["expires_after_seconds"], APPROVAL_TTL_SECONDS)
            self.assertEqual(payload["receipts"][0]["scope_ref"], GRANT["scope_ref"])
            self.assertEqual(payload["claim_boundary"], CLAIM_BOUNDARY)

    def test_the_approvals_view_scopes_to_one_run(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            base = ["--omh-home", str(paths.omh_home), "--hermes-home", str(paths.hermes_home)]
            mint_approval_receipt(paths, now=T0, **GRANT)
            mint_approval_receipt(paths, now=T0, **{**GRANT, "run_id": "run-2"})
            status, stdout, stderr = run_cli(base + ["runtime", "approvals", "--run", "run-2", "--json"])
            self.assertEqual(status, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual([row["run_id"] for row in payload["receipts"]], ["run-2"])

    def test_no_cli_path_mints_an_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(tmp)
            base = ["--omh-home", str(paths.omh_home), "--hermes-home", str(paths.hermes_home)]
            for attempt in (
                ["runtime", "approvals", "--grant"],
                ["runtime", "approvals", "--decision", "granted"],
                ["runtime", "approvals", "--record", "--action", "repo_edit"],
                ["runtime", "approvals", "--approve", "src/omh/paths.py"],
            ):
                with self.subTest(attempt=attempt), self.assertRaises(SystemExit):
                    run_cli(base + attempt, output_json=False)
            self.assertEqual(read_approval_receipts(paths), [])

    def test_no_command_module_can_reach_a_mint(self) -> None:
        """The structural half: an approval comes from the confirmation flow.

        The flag test above proves today's parser refuses today's guesses. This
        proves no command module holds a writer at all, so a future flag cannot
        quietly acquire one.
        """
        writers = (
            "mint_approval_receipt",
            "mint_approval_receipt_at",
            "record_approval_receipt",
            "append_approval_receipt",
            "build_approval_receipt",
        )
        offenders = []
        for path in sorted((REPO_ROOT / "src" / "commands").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            offenders.extend(
                f"{path.name}:{writer}" for writer in writers if writer in text
            )
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
