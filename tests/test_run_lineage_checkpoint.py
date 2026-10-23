"""Contracts for `run_lineage_checkpoint/v1` (issue #826).

The acceptance criteria this holds, and where each one is asserted:

AC1  OMH reconstructs and validates the chain offline
     -> `AFullRunsChainIsReconstructedFromTheStoreAlone`
AC2  changing history or referenced digests fails validation
     -> `ChangingHistoryBreaksTheChain`,
        `ChangingAReferencedDigestBreaksTheChain`
AC3  prepared checkpoints never imply later evidence classes
     -> `APreparedCheckpointImpliesNoObservedClass`

Plus the two things this family is worthless without: that the store is wired
into the registry and the validator its siblings use
(`TheStoreIsRegisteredBesideItsSiblings`), and that an observed start really is
a prerequisite of a dispatch checkpoint (`TheChainOrdersTheObservedStart`) --
which is the fact the `start_evidence` safety boundary's new statement rests on,
so it has to be true.

Store mechanics -- concurrent appends, torn tails, chain forks -- are inherited
from `system/append_only_store.py` and proved there. What is asserted here is
this family's own contribution: the seal over the record, the walk that reports
where a history stopped being trustworthy, and the derivation that makes an
overclaiming checkpoint unconstructible rather than merely unusual.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _local_package import load_local_package

load_local_package()
from omh.coding.action_gate import (  # noqa: E402
    START_EVIDENCE_BLOCKER,
    build_task_authority_envelope,
    build_task_handoff_safety_contract,
)
from omh.local_store import atomic_write_text  # noqa: E402
from omh.runtime.artifacts import validate_runtime  # noqa: E402
from omh.runtime.records import (  # noqa: E402
    OPTIONAL_RUNTIME_STORE_VALIDATORS,
    RUN_LINEAGE_CHECKPOINT_RECORD_KEYS,
)
from omh.system.append_only_store import RAW_OR_HIDDEN_KEYS  # noqa: E402
from omh.system.paths import OmhPaths  # noqa: E402
from omh.workflows.observation_journal import PROJECTION_ORDER  # noqa: E402
from omh.workflows.run_lineage import (  # noqa: E402
    ARTIFACT_REF_LIMIT,
    BREAK_CODES,
    BREAK_DIGEST_MISMATCH,
    BREAK_INVALID_RECORD,
    BREAK_PARENT_MISMATCH,
    BREAK_PREREQUISITE,
    BREAK_REASONS,
    BREAK_SEQUENCE,
    CHECKPOINT_DIGEST_KEYS,
    CLAIM_BOUNDARY,
    EXECUTION_CLAIM_KEYS,
    LINEAGE_EVIDENCE_CLASSES,
    LINEAGE_STATES,
    LINEAGE_TRANSITIONS,
    RUN_LINEAGE_CHECKPOINT_KEYS,
    RUN_LINEAGE_CHECKPOINT_SCHEMA_VERSION,
    TRANSITION_EVIDENCE_CLASS,
    UNSEALED_CHECKPOINT_KEYS,
    RunLineageError,
    append_run_lineage_checkpoint,
    build_run_lineage_checkpoint,
    checkpoint_digest,
    checkpoint_implies_evidence_class,
    compact_run_lineage_checkpoint,
    lineage_id,
    read_run_lineage,
    read_run_lineage_checkpoints,
    reconstruct_run_lineage,
    run_lineage_head,
    validate_run_lineage_checkpoint,
    validate_run_lineage_checkpoint_store,
)
from omh.workflows.workspace_bindings import (  # noqa: E402
    EXECUTION_CLAIM_KEYS as BINDING_EXECUTION_CLAIM_KEYS,
)


_RUN_ID = "run-20260809-alpha"
_OWNER = "claude-code"
_PROFILE = "rev-frozen"
_PLAN = "plan-7c1f2a"
_HANDOFF = "handoff-9b3e11"
_MESSAGE = "fix the login bug in src/auth.py and add a regression test"

# The two transitions whose evidence class is `prepared_not_observed`. Derived
# rather than listed, so a transition that later becomes prepared joins the AC3
# assertions without anyone remembering to add it here.
_PREPARED_TRANSITIONS = tuple(
    transition
    for transition, evidence_class in TRANSITION_EVIDENCE_CLASS.items()
    if evidence_class == "prepared_not_observed"
)


def _paths(root: Path) -> OmhPaths:
    return OmhPaths(omh_home=root, hermes_home=root / "hermes")


def _append(paths: OmhPaths, transition: str, **overrides: object) -> dict[str, object]:
    """One checkpoint appended onto whatever head the chain already has."""
    head = run_lineage_head(read_run_lineage_checkpoints(paths), run_id=_RUN_ID)
    arguments: dict[str, object] = {
        "run_id": _RUN_ID,
        "transition": transition,
        "owner": _OWNER,
        "safety_profile_revision": _PROFILE,
        "plan_digest": _PLAN,
        "handoff_digest": _HANDOFF if transition != "plan_accepted" else "",
        "continue_from": str(head.get("digest", "")),
    }
    arguments.update(overrides)
    return append_run_lineage_checkpoint(paths, **arguments)  # type: ignore[arg-type]


def _full_chain(paths: OmhPaths) -> list[dict[str, object]]:
    """A whole run, from accepted plan to observed merge."""
    return [_append(paths, transition) for transition in LINEAGE_TRANSITIONS]


def _checkpoint(**overrides: object) -> dict[str, object]:
    """One well-formed genesis checkpoint, built without a store."""
    arguments: dict[str, object] = {
        "run_id": _RUN_ID,
        "transition": "plan_accepted",
        "owner": _OWNER,
        "safety_profile_revision": _PROFILE,
        "sequence": 0,
        "plan_digest": _PLAN,
        "recorded_at": "2026-08-09T00:00:00Z",
    }
    arguments.update(overrides)
    return build_run_lineage_checkpoint(**arguments)  # type: ignore[arg-type]


def _write_store(path: Path, records: list[dict[str, object]]) -> None:
    """Rewrite the store from records, byte-stable on every platform.

    `atomic_write_text` and not `Path.write_text`: these bytes are read back and
    hashed, and Windows text mode would rewrite every newline.
    """
    atomic_write_text(path, "".join(json.dumps(record, sort_keys=True) + "\n" for record in records))


def _contract_boundary(name: str) -> dict[str, object]:
    contract = build_task_handoff_safety_contract(
        build_task_authority_envelope(
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
            safety_profile_revision=_PROFILE,
        )
    )
    for entry in contract["boundaries"]:  # type: ignore[index]
        if entry["boundary"] == name:
            return entry  # type: ignore[return-value]
    raise AssertionError(f"no boundary named {name}")


class AFullRunsChainIsReconstructedFromTheStoreAlone(unittest.TestCase):
    """AC1: the whole history, rebuilt offline and verified link by link."""

    def test_a_full_run_reconstructs_in_order_and_validates(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            appended = _full_chain(paths)

            # Nothing from the append call is reused: the reconstruction reads
            # the store back from disk, which is what "offline from the store
            # alone" has to mean.
            report = read_run_lineage(paths, run_id=_RUN_ID)

            self.assertTrue(report["ok"], report["break_reason"])
            self.assertEqual(report["checkpoint_count"], len(LINEAGE_TRANSITIONS))
            self.assertEqual(report["intact_checkpoint_count"], len(LINEAGE_TRANSITIONS))
            self.assertEqual(report["transitions"], list(LINEAGE_TRANSITIONS))
            self.assertEqual(report["head_transition"], "merge_observed")
            self.assertEqual(report["head_evidence_class"], "merge_observed")
            self.assertEqual(report["head_digest"], appended[-1]["digest"])
            self.assertEqual(report["break_code"], "")
            self.assertEqual(report["break_sequence"], -1)

    def test_the_reconstruction_answers_what_the_run_continued_from(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _full_chain(paths)

            report = read_run_lineage(paths, run_id=_RUN_ID)

            # Accepted plan -> handoff -> dispatch -> verification -> current
            # evidence, which is the question #826 opens with.
            self.assertEqual(report["plan_digest"], _PLAN)
            self.assertEqual(report["handoff_digest"], _HANDOFF)
            self.assertEqual(report["owner"], _OWNER)
            self.assertEqual(report["safety_profile_revision"], _PROFILE)
            self.assertEqual(report["evidence_classes"][0], "prepared_not_observed")
            self.assertIn("dispatch_observed", report["evidence_classes"])
            self.assertIn("verification_observed", report["evidence_classes"])
            self.assertEqual(report["evidence_classes"][-1], "merge_observed")

    def test_every_link_names_its_predecessors_own_digest(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            chain = _full_chain(paths)

            self.assertEqual(chain[0]["parent_digest"], "")
            for index, record in enumerate(chain):
                with self.subTest(sequence=index):
                    self.assertEqual(record["sequence"], index)
                    self.assertEqual(record["digest"], checkpoint_digest(record))
                    if index:
                        self.assertEqual(record["parent_digest"], chain[index - 1]["digest"])

    def test_the_store_validates_clean_and_names_one_lineage(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _full_chain(paths)

            report = validate_run_lineage_checkpoint_store(paths.runtime_run_lineage_checkpoints_path)

            self.assertTrue(report["ok"], report["errors"])
            self.assertEqual(report["checkpoint_count"], len(LINEAGE_TRANSITIONS))
            self.assertEqual(report["lineage_count"], 1)
            self.assertEqual(report["broken_lineage_count"], 0)

    def test_two_runs_keep_separate_chains(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _append(paths, "plan_accepted")
            other = append_run_lineage_checkpoint(
                paths,
                run_id="run-20260809-beta",
                transition="plan_accepted",
                owner="codex",
                safety_profile_revision=_PROFILE,
                plan_digest="plan-other",
            )

            self.assertEqual(other["sequence"], 0)
            self.assertEqual(other["parent_digest"], "")
            self.assertNotEqual(other["lineage_id"], lineage_id(_RUN_ID))
            self.assertTrue(read_run_lineage(paths, run_id="run-20260809-beta")["ok"])
            self.assertTrue(read_run_lineage(paths, run_id=_RUN_ID)["ok"])

    def test_the_digest_is_deterministic_and_never_seals_the_clock(self) -> None:
        early = _checkpoint(recorded_at="2026-08-09T00:00:00Z")
        late = _checkpoint(recorded_at="2026-08-09T23:59:59Z")

        # Same history, two clocks, one digest. A wall clock inside the seal
        # would make an equality check a race, which is why it is outside it.
        self.assertEqual(early["digest"], late["digest"])
        self.assertEqual(sorted(UNSEALED_CHECKPOINT_KEYS), ["digest", "record_id", "recorded_at"])
        self.assertEqual(
            sorted(CHECKPOINT_DIGEST_KEYS),
            sorted(set(RUN_LINEAGE_CHECKPOINT_KEYS) - set(UNSEALED_CHECKPOINT_KEYS)),
        )

    def test_a_continuation_must_name_the_lineage_head(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            genesis = _append(paths, "plan_accepted")

            with self.assertRaises(RunLineageError):
                _append(paths, "handoff_prepared", continue_from="")
            with self.assertRaises(RunLineageError):
                _append(paths, "handoff_prepared", continue_from="a" * 64)

            second = _append(paths, "handoff_prepared", continue_from=str(genesis["digest"]))
            self.assertEqual(second["parent_digest"], genesis["digest"])


class ChangingHistoryBreaksTheChain(unittest.TestCase):
    """AC2, first half: editing an earlier record's field fails validation."""

    def test_editing_an_earlier_checkpoints_field_is_reported_at_that_checkpoint(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            chain = _full_chain(paths)
            store = paths.runtime_run_lineage_checkpoints_path
            tampered = [dict(record) for record in chain]
            # The owner of the second checkpoint, rewritten long after the fact.
            # Nothing else on disk changes, and every later record still reads
            # exactly as it did.
            tampered[1]["owner"] = "codex"
            _write_store(store, tampered)

            report = read_run_lineage(paths, run_id=_RUN_ID)

            self.assertFalse(report["ok"])
            self.assertEqual(report["break_code"], BREAK_DIGEST_MISMATCH)
            self.assertEqual(report["break_sequence"], 1)
            self.assertEqual(report["break_record_id"], chain[1]["record_id"])
            self.assertEqual(report["break_transition"], chain[1]["transition"])
            self.assertEqual(report["intact_checkpoint_count"], 1)
            self.assertIn("was changed after it was written", report["break_reason"])

    def test_editing_an_earlier_checkpoint_fails_omh_runtime_validate(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            chain = _full_chain(paths)
            store = paths.runtime_run_lineage_checkpoints_path
            tampered = [dict(record) for record in chain]
            tampered[2]["plan_digest"] = "plan-substituted"
            _write_store(store, tampered)

            store_report = validate_run_lineage_checkpoint_store(store)
            runtime_report = validate_runtime(paths)

            self.assertFalse(store_report["ok"])
            self.assertEqual(store_report["broken_lineage_count"], 1)
            self.assertTrue(any("chain break at sequence 2" in error for error in store_report["errors"]))
            self.assertFalse(runtime_report["ok"])
            self.assertFalse(runtime_report["run_lineage_checkpoints"]["ok"])

    def test_deleting_a_checkpoint_from_the_middle_is_reported(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            chain = _full_chain(paths)
            _write_store(paths.runtime_run_lineage_checkpoints_path, [*chain[:3], *chain[4:]])

            report = read_run_lineage(paths, run_id=_RUN_ID)

            self.assertFalse(report["ok"])
            # The record that moved into position 3 says it is at position 4.
            self.assertEqual(report["break_code"], BREAK_SEQUENCE)
            self.assertEqual(report["break_sequence"], 3)
            self.assertEqual(report["break_record_id"], chain[4]["record_id"])

    def test_a_checkpoint_grafted_from_another_run_is_reported(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            store = paths.runtime_run_lineage_checkpoints_path
            chain = _full_chain(paths)
            tampered = [dict(record) for record in chain]
            # Same chain, one record relabelled onto another run's lineage and
            # resealed, so the seal itself is consistent. Only the derived
            # lineage id catches it; without that, one run's history could be
            # grafted onto another run's chain.
            tampered[2]["lineage_id"] = lineage_id("run-20260809-beta")
            tampered[2]["digest"] = checkpoint_digest(tampered[2])
            _write_store(store, tampered)

            report = read_run_lineage(paths, run_id=_RUN_ID)

            self.assertFalse(report["ok"])
            self.assertEqual(report["break_code"], BREAK_INVALID_RECORD)
            self.assertEqual(report["break_sequence"], 2)
            self.assertTrue(
                any("lineage_id does not identify its own run" in error
                    for error in validate_run_lineage_checkpoint_store(store)["errors"])
            )

    def test_every_break_code_carries_a_written_reason(self) -> None:
        self.assertEqual(sorted(BREAK_REASONS), sorted(BREAK_CODES))
        for code in BREAK_CODES:
            with self.subTest(code=code):
                self.assertTrue(BREAK_REASONS[code].strip())


class ChangingAReferencedDigestBreaksTheChain(unittest.TestCase):
    """AC2, second half: repointing a link fails validation.

    Separate from editing a field on purpose. A field edit is caught because the
    record no longer matches its own seal; a repointed `parent_digest` is a
    record that matches its own seal perfectly and still does not continue the
    history it claims to. Only the walk catches the second one, so only a
    separate test proves the walk is there.
    """

    def test_repointing_a_parent_digest_is_reported_at_that_checkpoint(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            chain = _full_chain(paths)
            tampered = [dict(record) for record in chain]
            # Point checkpoint 3 back at checkpoint 0 and reseal it, so the
            # record is internally consistent and only its position in the
            # history is a lie.
            tampered[3]["parent_digest"] = chain[0]["digest"]
            tampered[3]["digest"] = checkpoint_digest(tampered[3])
            _write_store(paths.runtime_run_lineage_checkpoints_path, tampered)

            self.assertEqual(validate_run_lineage_checkpoint(tampered[3]), [])

            report = read_run_lineage(paths, run_id=_RUN_ID)

            self.assertFalse(report["ok"])
            self.assertEqual(report["break_code"], BREAK_PARENT_MISMATCH)
            self.assertEqual(report["break_sequence"], 3)
            self.assertEqual(report["break_record_id"], chain[3]["record_id"])
            self.assertIn("does not continue from the history it claims", report["break_reason"])

    def test_a_repointed_parent_digest_fails_omh_runtime_validate(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            chain = _full_chain(paths)
            tampered = [dict(record) for record in chain]
            tampered[4]["parent_digest"] = chain[1]["digest"]
            tampered[4]["digest"] = checkpoint_digest(tampered[4])
            _write_store(paths.runtime_run_lineage_checkpoints_path, tampered)

            report = validate_runtime(paths)

            self.assertFalse(report["ok"])
            self.assertFalse(report["run_lineage_checkpoints"]["ok"])
            self.assertTrue(
                any("chain break at sequence 4" in error for error in report["run_lineage_checkpoints"]["errors"])
            )

    def test_a_parent_digest_naming_no_checkpoint_at_all_is_reported(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            chain = _full_chain(paths)
            tampered = [dict(record) for record in chain]
            tampered[2]["parent_digest"] = "b" * 64
            tampered[2]["digest"] = checkpoint_digest(tampered[2])
            _write_store(paths.runtime_run_lineage_checkpoints_path, tampered)

            store_report = validate_run_lineage_checkpoint_store(
                paths.runtime_run_lineage_checkpoints_path
            )

            self.assertFalse(store_report["ok"])
            # The shared walk sees a link into a record that never existed; the
            # reconstruction sees a link to the wrong predecessor. Both are the
            # same tamper and both have to be reported.
            self.assertTrue(
                any("does not name an earlier checkpoint" in error for error in store_report["errors"]),
                store_report["errors"],
            )
            self.assertEqual(read_run_lineage(paths, run_id=_RUN_ID)["break_code"], BREAK_PARENT_MISMATCH)

    def test_a_second_checkpoint_claiming_one_predecessor_forks_the_chain(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            chain = _full_chain(paths)
            fork = dict(chain[2])
            fork["record_id"] = "run-lineage-forked-000000"
            fork["owner"] = "codex"
            fork["digest"] = checkpoint_digest(fork)
            _write_store(paths.runtime_run_lineage_checkpoints_path, [*chain, fork])

            store_report = validate_run_lineage_checkpoint_store(
                paths.runtime_run_lineage_checkpoints_path
            )

            self.assertFalse(store_report["ok"])
            self.assertTrue(
                any("forks the chain" in error for error in store_report["errors"]), store_report["errors"]
            )

    def test_a_digest_that_is_not_a_digest_is_refused_by_shape(self) -> None:
        record = _checkpoint()
        for value in ("", "not-a-digest", "A" * 64, "a" * 63):
            with self.subTest(digest=value):
                tampered = {**record, "digest": value}
                self.assertTrue(
                    any("digest must be a sha256 hex digest" in error
                        for error in validate_run_lineage_checkpoint(tampered))
                )

    def test_only_the_genesis_checkpoint_has_no_parent(self) -> None:
        record = _checkpoint()
        orphaned = {**record, "sequence": 3}
        orphaned["digest"] = checkpoint_digest(orphaned)
        self.assertTrue(
            any("only the checkpoint at sequence 0 has no parent_digest" in error
                for error in validate_run_lineage_checkpoint(orphaned))
        )

        parented = {**record, "parent_digest": "c" * 64}
        parented["digest"] = checkpoint_digest(parented)
        self.assertTrue(
            any("only the checkpoint at sequence 0 has no parent_digest" in error
                for error in validate_run_lineage_checkpoint(parented))
        )


class APreparedCheckpointImpliesNoObservedClass(unittest.TestCase):
    """AC3: preparing a run says only that it was prepared."""

    def test_a_prepared_checkpoint_implies_nothing_across_the_class_vocabulary(self) -> None:
        for transition in _PREPARED_TRANSITIONS:
            record = _checkpoint(transition=transition, handoff_digest=_HANDOFF)
            for evidence_class in LINEAGE_EVIDENCE_CLASSES:
                with self.subTest(transition=transition, evidence_class=evidence_class):
                    implied = checkpoint_implies_evidence_class(record, evidence_class)
                    self.assertEqual(implied, evidence_class == "prepared_not_observed")

    def test_no_checkpoint_implies_a_class_beyond_its_own_transition(self) -> None:
        # The wider statement AC3 is a case of: a checkpoint states one class,
        # the one its own transition records, and never the next rung.
        for transition in LINEAGE_TRANSITIONS:
            record = _checkpoint(transition=transition, handoff_digest=_HANDOFF)
            for evidence_class in LINEAGE_EVIDENCE_CLASSES:
                with self.subTest(transition=transition, evidence_class=evidence_class):
                    self.assertEqual(
                        checkpoint_implies_evidence_class(record, evidence_class),
                        evidence_class == TRANSITION_EVIDENCE_CLASS[transition],
                    )

    def test_a_class_outside_the_vocabulary_is_never_implied(self) -> None:
        record = _checkpoint(transition="merge_observed", handoff_digest=_HANDOFF)
        for value in ("", "merged", "anything", "MERGE_OBSERVED"):
            with self.subTest(value=value):
                self.assertFalse(checkpoint_implies_evidence_class(record, value))

    def test_a_prepared_checkpoint_cannot_be_built_carrying_an_observed_class(self) -> None:
        # There is no argument for it. The class is derived from the transition,
        # which is the structural half of AC3: an overclaiming checkpoint is
        # unconstructible rather than merely discouraged.
        for transition in _PREPARED_TRANSITIONS:
            with self.subTest(transition=transition):
                record = _checkpoint(transition=transition, handoff_digest=_HANDOFF)
                self.assertEqual(record["evidence_class"], "prepared_not_observed")
                self.assertEqual(record["state"], "prepared")
                self.assertNotIn("evidence_class", build_run_lineage_checkpoint.__kwdefaults__ or {})

    def test_a_hand_edited_prepared_checkpoint_claiming_a_later_class_is_refused(self) -> None:
        record = _checkpoint()
        for evidence_class in LINEAGE_EVIDENCE_CLASSES:
            if evidence_class == "prepared_not_observed":
                continue
            with self.subTest(evidence_class=evidence_class):
                tampered = {**record, "evidence_class": evidence_class}
                tampered["digest"] = checkpoint_digest(tampered)
                errors = validate_run_lineage_checkpoint(tampered)
                self.assertTrue(
                    any("never states a class its own transition did not reach" in error for error in errors),
                    errors,
                )

    def test_a_prepared_state_and_an_observed_class_cannot_coexist(self) -> None:
        observed = _checkpoint(transition="merge_observed", handoff_digest=_HANDOFF)
        tampered = {**observed, "state": "prepared"}
        tampered["digest"] = checkpoint_digest(tampered)
        self.assertTrue(
            any("a prepared checkpoint must carry evidence_class" in error
                for error in validate_run_lineage_checkpoint(tampered))
        )

        prepared = {**_checkpoint(), "state": "observed"}
        prepared["digest"] = checkpoint_digest(prepared)
        self.assertTrue(
            any("an observed checkpoint must not carry evidence_class" in error
                for error in validate_run_lineage_checkpoint(prepared))
        )

    def test_the_claim_boundary_denies_every_later_evidence_class_on_every_record(self) -> None:
        record = _checkpoint()
        self.assertEqual(record["claim_boundary"], CLAIM_BOUNDARY)
        self.assertIn("It is not that evidence", CLAIM_BOUNDARY)
        self.assertIn(
            "never dispatch, execution, verification, review, CI, merge-readiness, or merge evidence",
            CLAIM_BOUNDARY,
        )
        self.assertIn("a prepared checkpoint states no observed class at all", CLAIM_BOUNDARY)

    def test_a_record_cannot_be_constructed_in_a_shape_that_claims_execution(self) -> None:
        record = _checkpoint()
        for key in sorted(EXECUTION_CLAIM_KEYS):
            with self.subTest(key=key):
                errors = validate_run_lineage_checkpoint({**record, key: "yes"})
                self.assertTrue(
                    any("must not carry execution-claim keys" in error for error in errors), errors
                )

    def test_the_execution_claim_vocabulary_matches_its_sibling(self) -> None:
        # Three families now refuse the same shape by name. Pinned pairwise so
        # a key added to one and missed on another is a failing test.
        self.assertEqual(EXECUTION_CLAIM_KEYS, BINDING_EXECUTION_CLAIM_KEYS)

    def test_a_record_cannot_carry_raw_or_hidden_content(self) -> None:
        record = _checkpoint()
        for key in sorted(RAW_OR_HIDDEN_KEYS):
            with self.subTest(key=key):
                errors = validate_run_lineage_checkpoint({**record, key: "x"})
                self.assertTrue(any("must not carry raw or hidden keys" in error for error in errors), errors)


class TheChainOrdersTheObservedStart(unittest.TestCase):
    """The fact the `start_evidence` boundary's new statement rests on."""

    def test_a_dispatch_checkpoint_requires_an_observed_start_in_the_chain(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _append(paths, "plan_accepted")
            _append(paths, "handoff_prepared")

            with self.assertRaises(RunLineageError) as raised:
                _append(paths, "executor_dispatch_observed")

            self.assertIn("runtime_start_observed", str(raised.exception))
            self.assertEqual(len(read_run_lineage_checkpoints(paths, run_id=_RUN_ID)), 2)

    def test_a_hand_written_chain_that_skips_the_start_is_reported(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            plan = _checkpoint(sequence=0)
            handoff = _checkpoint(
                transition="handoff_prepared",
                handoff_digest=_HANDOFF,
                sequence=1,
                parent_digest=str(plan["digest"]),
            )
            dispatch = _checkpoint(
                transition="executor_dispatch_observed",
                handoff_digest=_HANDOFF,
                sequence=2,
                parent_digest=str(handoff["digest"]),
            )
            _write_store(paths.runtime_run_lineage_checkpoints_path, [plan, handoff, dispatch])

            report = read_run_lineage(paths, run_id=_RUN_ID)

            self.assertFalse(report["ok"])
            self.assertEqual(report["break_code"], BREAK_PREREQUISITE)
            self.assertEqual(report["break_sequence"], 2)
            self.assertFalse(
                validate_run_lineage_checkpoint_store(paths.runtime_run_lineage_checkpoints_path)["ok"]
            )

    def test_the_start_evidence_boundary_stays_declared_and_says_why(self) -> None:
        # Honest bookkeeping. Ordering a chain is not requiring one, so the
        # boundary does not move; what changes is the blocker, which stops being
        # a ticket and becomes the reason nothing further can close it here.
        boundary = _contract_boundary("start_evidence")

        self.assertEqual(boundary["enforcement"], "declared_not_enforced")
        self.assertEqual(boundary["enforced_by"], [])
        self.assertEqual(boundary["blocked_by"], START_EVIDENCE_BLOCKER)
        self.assertNotEqual(boundary["blocked_by"], "#826")
        self.assertIn("run_lineage_checkpoint/v1", boundary["statement"])
        self.assertIn("no observed start", boundary["statement"])

    def test_every_other_prerequisite_mirrors_the_journals_lifecycle_order(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            _append(paths, "plan_accepted")
            _append(paths, "handoff_prepared")
            _append(paths, "runtime_start_observed")
            _append(paths, "executor_dispatch_observed")

            for transition in ("verification_result_observed", "review_result_observed", "merge_observed"):
                with self.subTest(transition=transition), self.assertRaises(RunLineageError):
                    _append(paths, transition)


class TheStoreIsRegisteredBesideItsSiblings(unittest.TestCase):
    """The wiring: without it nothing in production ever runs these validators."""

    def test_the_family_registers_its_validator_under_its_own_store(self) -> None:
        entries = {entry.store_name: entry for entry in OPTIONAL_RUNTIME_STORE_VALIDATORS}
        self.assertIn("run_lineage_checkpoints.jsonl", entries)
        entry = entries["run_lineage_checkpoints.jsonl"]
        # `record_id`, not the `receipt_id` two of its siblings carry, so the
        # label a fault is reported under is part of the registration.
        self.assertEqual(entry.record_id_key, "record_id")
        self.assertEqual(entry.validator(_checkpoint()), [])
        self.assertTrue(entry.validator({"schema_version": "approval_receipt/v1"}))
        self.assertTrue(entry.validator({"schema_version": "workspace_binding_guard/v1"}))
        self.assertEqual(RUN_LINEAGE_CHECKPOINT_RECORD_KEYS, RUN_LINEAGE_CHECKPOINT_KEYS)

    def test_the_store_lives_beside_its_siblings_and_not_inside_a_run(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            self.assertEqual(
                paths.runtime_run_lineage_checkpoints_path.parent,
                paths.runtime_workspace_bindings_path.parent,
            )
            self.assertNotIn("runs", paths.runtime_run_lineage_checkpoints_path.parts)
            self.assertEqual(
                paths.runtime_run_lineage_checkpoints_path.name, "run_lineage_checkpoints.jsonl"
            )

    def test_the_store_report_reaches_omh_runtime_validate(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            store = paths.runtime_run_lineage_checkpoints_path
            store.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(store, json.dumps({"schema_version": "nonsense"}) + "\n")

            report = validate_runtime(paths)

            self.assertIn("run_lineage_checkpoints", report)
            self.assertFalse(report["run_lineage_checkpoints"]["ok"])
            self.assertFalse(report["ok"])

    def test_an_absent_store_is_not_a_fault(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = _paths(Path(tmp))
            store = paths.runtime_run_lineage_checkpoints_path
            self.assertFalse(store.exists())

            report = validate_run_lineage_checkpoint_store(store)
            scoped = validate_run_lineage_checkpoint_store(store, run_id=_RUN_ID)

            self.assertTrue(report["ok"], report["errors"])
            self.assertEqual(report["checkpoint_count"], 0)
            self.assertEqual(report["lineage_count"], 0)
            # A run with no checkpoints has no chain, scoped or not.
            self.assertTrue(scoped["ok"], scoped["errors"])
            self.assertEqual(scoped["lineage_count"], 0)

    def test_a_hand_edited_artifact_list_renders_as_nothing(self) -> None:
        record = _checkpoint(artifact_refs=["a-one"])
        self.assertEqual(compact_run_lineage_checkpoint(record)["artifact_refs"], ["a-one"])
        # A string where the list should be would otherwise render one entry per
        # character.
        self.assertEqual(
            compact_run_lineage_checkpoint({**record, "artifact_refs": "a-one"})["artifact_refs"], []
        )


class TheRecordShapeIsClosed(unittest.TestCase):
    """Vocabularies, key sets, and the things a metadata-only record refuses."""

    def test_the_evidence_class_vocabulary_is_the_journals_projection_order(self) -> None:
        # Restated rather than imported, so this is where the two are pinned.
        self.assertEqual(LINEAGE_EVIDENCE_CLASSES, PROJECTION_ORDER)

    def test_every_transition_maps_onto_a_class_and_every_class_has_a_transition(self) -> None:
        self.assertEqual(sorted(TRANSITION_EVIDENCE_CLASS), sorted(LINEAGE_TRANSITIONS))
        self.assertEqual(set(TRANSITION_EVIDENCE_CLASS.values()), set(LINEAGE_EVIDENCE_CLASSES))

    def test_the_record_keys_and_schema_are_fixed(self) -> None:
        record = _checkpoint()
        self.assertEqual(set(record), set(RUN_LINEAGE_CHECKPOINT_KEYS))
        self.assertEqual(record["schema_version"], RUN_LINEAGE_CHECKPOINT_SCHEMA_VERSION)
        self.assertEqual(record["privacy"], "metadata_only")
        self.assertEqual(validate_run_lineage_checkpoint(record), [])

    def test_an_unsupported_transition_or_state_is_refused(self) -> None:
        with self.assertRaises(RunLineageError):
            _checkpoint(transition="merged")
        with self.assertRaises(RunLineageError):
            _checkpoint(state="in_progress")
        self.assertEqual(LINEAGE_STATES, ("prepared", "observed", "blocked", "cancelled", "failed"))

    def test_a_terminal_state_is_accepted_on_any_transition(self) -> None:
        for state in ("blocked", "cancelled", "failed"):
            with self.subTest(state=state):
                record = _checkpoint(state=state)
                self.assertEqual(validate_run_lineage_checkpoint(record), [])

    def test_a_url_or_a_path_cannot_enter_a_reference_field(self) -> None:
        for value in ("https://evil.test/plan", "/Users/someone/plan.md", "plan?id=1"):
            with self.subTest(value=value), self.assertRaises(RunLineageError):
                _checkpoint(plan_digest=value)

    def test_a_plan_accepted_checkpoint_must_name_its_plan(self) -> None:
        with self.assertRaises(RunLineageError) as raised:
            _checkpoint(plan_digest="")
        self.assertIn("plan_digest is required", str(raised.exception))

    def test_everything_from_the_handoff_onward_must_name_its_handoff(self) -> None:
        for transition in LINEAGE_TRANSITIONS[1:]:
            with self.subTest(transition=transition), self.assertRaises(RunLineageError) as raised:
                _checkpoint(transition=transition, handoff_digest="")
            self.assertIn("handoff_digest is required", str(raised.exception))

    def test_artifact_refs_are_bounded_sorted_and_deduplicated(self) -> None:
        record = _checkpoint(artifact_refs=["b-two", "a-one", "b-two"])
        self.assertEqual(record["artifact_refs"], ["a-one", "b-two"])
        with self.assertRaises(RunLineageError):
            _checkpoint(artifact_refs=[f"artifact-{index}" for index in range(ARTIFACT_REF_LIMIT + 1)])
        unsorted = {**record, "artifact_refs": ["b-two", "a-one"]}
        unsorted["digest"] = checkpoint_digest(unsorted)
        self.assertTrue(
            any("must be sorted and free of duplicates" in error
                for error in validate_run_lineage_checkpoint(unsorted))
        )

    def test_a_sequence_that_is_not_a_whole_number_is_refused(self) -> None:
        for value in (-1, True, "0", 1.5):
            with self.subTest(sequence=value), self.assertRaises(RunLineageError):
                _checkpoint(sequence=value)

    def test_rendering_refuses_anything_outside_its_vocabulary(self) -> None:
        record = _checkpoint()
        compact = compact_run_lineage_checkpoint(record)
        self.assertEqual(compact["transition"], "plan_accepted")
        self.assertEqual(compact["digest"], record["digest"])
        self.assertEqual(compact["parent_digest"], "")

        hand_edited = {**record, "transition": "invented", "digest": "/etc/passwd", "state": "whatever"}
        rendered = compact_run_lineage_checkpoint(hand_edited)
        self.assertEqual(rendered["transition"], "")
        self.assertEqual(rendered["state"], "")
        self.assertEqual(rendered["digest"], "")

    def test_a_reconstruction_of_a_run_with_no_chain_is_empty_and_whole(self) -> None:
        report = reconstruct_run_lineage([], run_id=_RUN_ID)
        self.assertTrue(report["ok"])
        self.assertEqual(report["checkpoint_count"], 0)
        self.assertEqual(report["transitions"], [])
        self.assertEqual(report["head_digest"], "")
        self.assertEqual(report["claim_boundary"], CLAIM_BOUNDARY)


if __name__ == "__main__":
    unittest.main()
