"""Contract tests for the native safety preflight (#804) and the org rule source (#802).

The point of these tests is the boundary, not the happy path. A safety rule
that can be widened, that reads a body, that answers differently on the same
input, or that reads "unavailable" as "fine" is worse than no rule at all, so
each of those is locked here.
"""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _credential_fixtures import AWS_ACCESS_KEY_ID
from _local_package import load_local_package

load_local_package()

from omh.capabilities.toggles import (  # noqa: E402
    ORG_RULE_SOURCE_POLICY_SCHEMA_VERSION,
    build_capability_policy,
    build_org_rule_source_policy,
    org_rule_source_enabled,
    org_rule_source_path,
    read_capability_policy,
    read_org_rule_source_policy,
    write_capability_policy,
    write_org_rule_source_policy,
)
from omh.coding.project_governance import (  # noqa: E402
    ORG_SAFETY_RULE_SOURCE_MAX_BYTES,
    ORG_SAFETY_RULE_SOURCE_REASON_CODES,
    ORG_SAFETY_RULE_SOURCE_SCHEMA_VERSION,
    read_org_safety_rule_source,
)
from omh.commands.capability_policy import _policy_report  # noqa: E402
from omh.paths import resolve_paths  # noqa: E402
from omh.quality.safety_preflight import (  # noqa: E402
    EVIDENCE_CLAIMS,
    FIELD_CLASS_OPAQUE_REF,
    FIELD_CLASS_PATH,
    FIELD_CLASS_VOCABULARY,
    FIELD_CLASSES,
    MAX_ACCESS_INTENTS,
    MAX_APPROVED_DESTINATIONS,
    MAX_DATA_CLASSES,
    MAX_EVIDENCE_CLAIMS,
    MAX_PATH_CHARS,
    MAX_PERSISTED_CONTENT_REFS,
    MAX_REMOTE_TARGETS,
    MAX_TARGET_PATHS,
    MAX_WORKSPACE_ROOTS,
    REMOTE_TARGET_KINDS,
    REQUEST_FIELDS,
    RULE_ACCESS_INTENT_DECLARED,
    RULE_APPROVED_SCOPE_DECLARED,
    RULE_DATA_CLASSES_PERMITTED,
    RULE_DESTINATIONS_APPROVED,
    RULE_EVIDENCE_CLAIM_BOUNDED,
    RULE_INPUT_METADATA_BOUNDED,
    RULE_ORG_RULE_SOURCE,
    RULE_OWNER_DECLARED,
    RULE_PERSISTED_CONTENT_REFERENCED,
    RULE_RAW_CONTEXT_DECLARED,
    RULE_REMOTE_TARGETS_DECLARED,
    RULE_SECRETS_ABSENT,
    RULE_TARGET_PATHS_BOUNDED,
    RULE_WORKSPACE_ROOTS_DECLARED,
    SAFETY_PREFLIGHT_PRECEDENCE,
    SECRET_SHAPE_FIELD_CLASSES,
    evaluate_safety_preflight,
    recheck_safety_preflight_revision,
    safety_profile_digest,
    safety_profile_revision,
    safety_rule_profile,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_SOURCE = REPO_ROOT / "src" / "quality" / "safety_preflight.py"
GOVERNANCE_SOURCE = REPO_ROOT / "src" / "coding" / "project_governance.py"

# Every rule id, in denial precedence order. Stable strings, never positional:
# a prepared artifact and an org rule document both name rules by these ids.
EXPECTED_RULE_IDS = [
    "input_metadata_bounded",
    "secrets_absent",
    "data_classes_permitted",
    "owner_declared",
    "approved_scope_declared",
    "raw_context_declared",
    "workspace_roots_declared",
    "target_paths_bounded",
    "remote_targets_declared",
    "destinations_approved",
    "access_intent_declared",
    "persisted_content_referenced",
    "evidence_claim_bounded",
    "org_rule_source",
]


def request(**overrides: object) -> dict[str, object]:
    """A minimal allowed request; every field is pre-expansion metadata."""
    base: dict[str, object] = {
        "owner": "codex",
        "approved_scope": "issue-804",
        "message_context_mode": "bounded",
        "raw_content_included": False,
        "data_classes": ["workspace_metadata"],
        "workspace_roots": [],
        "target_paths": ["src/quality/safety_preflight.py"],
        "remote_targets": [],
        "approved_destinations": [],
        "access_intents": ["read"],
        "persisted_content_refs": [],
        "evidence_claims": ["prepared_not_observed"],
        "observed_record_refs": [],
    }
    base.update(overrides)
    return base


def sharing_request(kind: str = "git_remote", ref: str = "origin", **overrides: object) -> dict[str, object]:
    """A request that reaches one destination the boundary declared, with the intent that covers it.

    Naming a remote target is not enough on its own after #801: the boundary
    has to have approved that exact destination and the request has to declare
    the share intent, so every remote-target fixture carries all three.
    """
    destination = {"kind": kind, "ref": ref}
    base: dict[str, object] = {
        "remote_targets": [dict(destination)],
        "approved_destinations": [dict(destination)],
        "access_intents": ["read", "share"],
    }
    base.update(overrides)
    return request(**base)


def org_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": ORG_SAFETY_RULE_SOURCE_SCHEMA_VERSION,
        "revision": "org-2026-08-1",
    }
    document.update(overrides)
    return document


def write_org_source(root: Path, name: str = "org.json", **overrides: object) -> Path:
    path = root / name
    path.write_text(json.dumps(org_document(**overrides)), encoding="utf-8")
    return path


class SafetyProfileIdentityTests(unittest.TestCase):
    def test_the_rule_vocabulary_is_the_locked_stable_id_list(self) -> None:
        profile = safety_rule_profile()
        self.assertEqual([rule["id"] for rule in profile["rules"]], EXPECTED_RULE_IDS)
        self.assertTrue(all(isinstance(rule["id"], str) and rule["id"] for rule in profile["rules"]))

    def test_every_axis_the_issue_names_has_a_rule(self) -> None:
        axes = {rule["axis"] for rule in safety_rule_profile()["rules"]}
        self.assertLessEqual(
            {"owner", "paths", "secrets", "remote_targets", "persisted_content", "evidence_claims", "approved_scope"},
            axes,
        )

    def test_precedence_puts_the_builtin_floor_first_and_native_hermes_last(self) -> None:
        self.assertEqual(SAFETY_PREFLIGHT_PRECEDENCE, ("builtin_omh", "org", "native_hermes"))
        self.assertEqual(safety_rule_profile()["precedence"], list(SAFETY_PREFLIGHT_PRECEDENCE))

    def test_every_declared_reason_code_carries_a_correction(self) -> None:
        profile = safety_rule_profile()
        corrections = profile["corrections"]
        for rule in profile["rules"]:
            for code in rule["reason_codes"]:
                with self.subTest(code=code):
                    self.assertTrue(corrections.get(code))

    def test_the_revision_is_the_digest_of_the_profile_content(self) -> None:
        self.assertEqual(safety_profile_digest(safety_rule_profile()), safety_profile_revision())

    def test_the_revision_is_stable_across_calls_and_key_order(self) -> None:
        self.assertEqual(safety_profile_revision(), safety_profile_revision())
        reordered = dict(reversed(list(safety_rule_profile().items())))
        self.assertEqual(safety_profile_digest(reordered), safety_profile_revision())

    def test_the_revision_changes_when_and_only_when_the_profile_content_changes(self) -> None:
        unchanged = safety_rule_profile()
        self.assertEqual(safety_profile_digest(unchanged), safety_profile_revision())
        for mutate in (
            lambda profile: profile["bounds"].__setitem__("max_target_paths", MAX_TARGET_PATHS - 1),
            lambda profile: profile["corrections"].__setitem__("owner_missing", "different sentence"),
            lambda profile: profile["rules"].append({"id": "extra", "axis": "x", "level": "org", "reason_codes": []}),
            lambda profile: profile["vocabularies"]["remote_target_kinds"].append("smtp"),
            lambda profile: profile["vocabularies"]["data_classes"].append("telemetry"),
            lambda profile: profile["vocabularies"]["prohibited_data_classes"].clear(),
            lambda profile: profile["vocabularies"]["access_intents"].append("delete"),
            lambda profile: profile["bounds"].__setitem__("max_workspace_roots", MAX_WORKSPACE_ROOTS + 1),
            lambda profile: profile["data_boundary_limits"][0].__setitem__("enforcement_kind", "advisory"),
            lambda profile: profile["boundary_fields"].append("target_paths"),
        ):
            with self.subTest(mutate=mutate):
                mutated = safety_rule_profile()
                mutate(mutated)
                self.assertNotEqual(safety_profile_digest(mutated), safety_profile_revision())


class FieldClassTests(unittest.TestCase):
    """Each rule reads one field class. A rule pointed at the wrong class denies work."""

    def test_every_request_field_has_exactly_one_declared_class(self) -> None:
        self.assertEqual(sorted(FIELD_CLASSES), sorted(REQUEST_FIELDS))
        self.assertLessEqual(
            set(FIELD_CLASSES.values()),
            {FIELD_CLASS_OPAQUE_REF, FIELD_CLASS_PATH, FIELD_CLASS_VOCABULARY},
        )
        self.assertEqual(FIELD_CLASSES["target_paths"], FIELD_CLASS_PATH)

    def test_the_field_class_map_is_pinned_by_the_profile_revision(self) -> None:
        profile = safety_rule_profile()
        self.assertEqual(profile["field_classes"], dict(FIELD_CLASSES))
        self.assertEqual(profile["secret_shape_field_classes"], list(SECRET_SHAPE_FIELD_CLASSES))
        mutated = safety_rule_profile()
        mutated["field_classes"]["target_paths"] = FIELD_CLASS_OPAQUE_REF
        self.assertNotEqual(safety_profile_digest(mutated), safety_profile_revision())

    def test_credential_shape_is_read_on_opaque_refs_only(self) -> None:
        self.assertEqual(SECRET_SHAPE_FIELD_CLASSES, (FIELD_CLASS_OPAQUE_REF,))
        for field, value in (
            ("owner", "ghp_abcdefghijklmnopqrstuvwxyz"),
            ("approved_scope", "api_key-scope"),
            ("persisted_content_refs", [AWS_ACCESS_KEY_ID]),
            ("observed_record_refs", ["bearer-abc"]),
        ):
            with self.subTest(field=field):
                verdict = evaluate_safety_preflight(request(**{field: value}))
                self.assertEqual(verdict["status"], "deny")
                self.assertEqual(verdict["rule_id"], RULE_SECRETS_ABSENT)
                self.assertEqual(verdict["reason_code"], "secret_shaped_value")

    def test_a_source_path_is_not_read_as_a_credential(self) -> None:
        """A filename that contains a marker substring is still a filename.

        `token_store.py`, `test_authorization_headers.py`, `credentials_loader.py`,
        `tokenizer.py`, and `bearer_channel.py` are ordinary files. Reading the
        credential-shape rule over user-named paths denied every one of them.
        """
        for path in (
            "src/auth/token_store.py",
            "tests/test_authorization_headers.py",
            "src/api/credentials_loader.py",
            "lib/tokenizer.py",
            "bearer_channel.py",
            "src/secrets/password_reset.py",
            "src/private-key-rotation.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(evaluate_safety_preflight(request(target_paths=[path]))["status"], "allow")

    def test_the_path_rules_still_apply_to_every_one_of_those_paths(self) -> None:
        for path, reason_code in (
            ("/etc/token_store.py", "target_path_absolute"),
            ("~/token_store.py", "target_path_absolute"),
            ("C:\\keys\\token_store.py", "target_path_absolute"),
            ("../elsewhere/token_store.py", "target_path_escapes_project"),
            ("src/" + "a" * MAX_PATH_CHARS + "/token_store.py", "body_shaped_request_value"),
        ):
            with self.subTest(path=path):
                verdict = evaluate_safety_preflight(request(target_paths=[path]))
                self.assertEqual(verdict["status"], "deny")
                self.assertEqual(verdict["reason_code"], reason_code)
                self.assertEqual(verdict["field"], "target_paths[0]")

    def test_a_closed_vocabulary_denies_a_credential_by_membership(self) -> None:
        """Vocabulary fields are exempt from the secret rule and still fail closed."""
        mode = evaluate_safety_preflight(request(message_context_mode="ghp_abcdefghijklmnopqrstuvwxyz"))
        self.assertEqual(mode["status"], "deny")
        self.assertEqual(mode["reason_code"], "raw_context_mode_unknown")
        claim = evaluate_safety_preflight(request(evidence_claims=[AWS_ACCESS_KEY_ID]))
        self.assertEqual(claim["status"], "deny")
        self.assertEqual(claim["reason_code"], "evidence_claim_unknown")
        kind = evaluate_safety_preflight(request(remote_targets=[{"kind": "api_key", "ref": "origin"}]))
        self.assertEqual(kind["status"], "deny")
        self.assertEqual(kind["reason_code"], "remote_target_kind_unknown")

    def test_the_body_shape_bound_stays_universal(self) -> None:
        for field, value, limit_label in (
            ("owner", "line one\nline two", "field"),
            ("approved_scope", "```python```", "field"),
            ("target_paths", ["src/a.py\nsrc/b.py"], "path"),
            ("target_paths", ["src/" + "a" * MAX_PATH_CHARS + ".py"], "path"),
            ("persisted_content_refs", ["ref\twith\ttabs"], "field"),
        ):
            with self.subTest(field=field, limit=limit_label):
                verdict = evaluate_safety_preflight(request(**{field: value}))
                self.assertEqual(verdict["status"], "deny")
                self.assertEqual(verdict["reason_code"], "body_shaped_request_value")


class SafetyPreflightDeterminismTests(unittest.TestCase):
    def test_the_same_bounded_input_yields_the_same_decision_byte_for_byte(self) -> None:
        for candidate in (request(), request(owner=""), request(target_paths=["/etc/passwd"])):
            with self.subTest(candidate=candidate["owner"]):
                first = json.dumps(evaluate_safety_preflight(candidate), sort_keys=True)
                for _ in range(5):
                    self.assertEqual(json.dumps(evaluate_safety_preflight(candidate), sort_keys=True), first)
                self.assertEqual(
                    json.dumps(evaluate_safety_preflight(dict(candidate)), sort_keys=True),
                    first,
                )

    def test_an_allowed_request_is_quiet_and_pins_the_revision(self) -> None:
        verdict = evaluate_safety_preflight(request())
        self.assertEqual(verdict["status"], "allow")
        self.assertEqual((verdict["rule_id"], verdict["field"], verdict["correction"]), ("", "", ""))
        self.assertEqual(verdict["reason_code"], "allowed")
        self.assertEqual(verdict["safety_profile_revision"], safety_profile_revision())
        self.assertEqual(verdict["levels_applied"], ["builtin_omh"])
        self.assertEqual(verdict["org_reason_codes"], [])

    def test_the_verdict_states_that_passing_is_permission_and_not_execution_evidence(self) -> None:
        boundary = str(evaluate_safety_preflight(request())["claim_boundary"]).lower()
        self.assertIn("permission", boundary)
        for term in ("not compliance", "execution", "review", "ci", "merge"):
            self.assertIn(term, boundary)


class SafetyPreflightDenialTests(unittest.TestCase):
    def denials(self) -> list[tuple[str, object, str, str, str]]:
        long_paths = [f"src/p{index}.py" for index in range(MAX_TARGET_PATHS + 1)]
        many_remotes = [{"kind": "git_remote", "ref": f"remote-{index}"} for index in range(MAX_REMOTE_TARGETS + 1)]
        many_refs = [f"ref-{index}" for index in range(MAX_PERSISTED_CONTENT_REFS + 1)]
        many_claims = ["prepared_not_observed"] * (MAX_EVIDENCE_CLAIMS + 1)
        return [
            ("not an object", None, RULE_INPUT_METADATA_BOUNDED, "request", "request_not_an_object"),
            (
                "unknown field",
                request(raw_prompt="do the thing"),
                RULE_INPUT_METADATA_BOUNDED,
                "raw_prompt",
                "unknown_request_field",
            ),
            (
                "body shaped value",
                request(approved_scope="line one\nline two"),
                RULE_INPUT_METADATA_BOUNDED,
                "approved_scope",
                "body_shaped_request_value",
            ),
            (
                "credential shaped value",
                request(owner=AWS_ACCESS_KEY_ID),
                RULE_SECRETS_ABSENT,
                "owner",
                "secret_shaped_value",
            ),
            ("owner absent", request(owner=""), RULE_OWNER_DECLARED, "owner", "owner_missing"),
            (
                "owner not a ref",
                request(owner="two words"),
                RULE_OWNER_DECLARED,
                "owner",
                "owner_not_opaque_ref",
            ),
            (
                "scope absent",
                request(approved_scope="  "),
                RULE_APPROVED_SCOPE_DECLARED,
                "approved_scope",
                "approved_scope_missing",
            ),
            (
                "scope not a ref",
                request(approved_scope="scope with spaces"),
                RULE_APPROVED_SCOPE_DECLARED,
                "approved_scope",
                "approved_scope_not_opaque_ref",
            ),
            (
                "unknown context mode",
                request(message_context_mode="summary"),
                RULE_RAW_CONTEXT_DECLARED,
                "message_context_mode",
                "raw_context_mode_unknown",
            ),
            (
                "verbatim raw content under a bounded mode",
                request(message_context_mode="bounded", raw_content_included=True),
                RULE_RAW_CONTEXT_DECLARED,
                "raw_content_included",
                "raw_context_undeclared",
            ),
            (
                "raw content admission that is not a boolean",
                request(raw_content_included="yes"),
                RULE_RAW_CONTEXT_DECLARED,
                "raw_content_included",
                "raw_context_undeclared",
            ),
            (
                "paths not a list",
                request(target_paths="src/a.py"),
                RULE_TARGET_PATHS_BOUNDED,
                "target_paths",
                "target_path_not_bounded",
            ),
            (
                "too many paths",
                request(target_paths=long_paths),
                RULE_TARGET_PATHS_BOUNDED,
                "target_paths",
                "target_path_count_exceeded",
            ),
            (
                "absolute path",
                request(target_paths=["/etc/passwd"]),
                RULE_TARGET_PATHS_BOUNDED,
                "target_paths[0]",
                "target_path_absolute",
            ),
            (
                "windows absolute path",
                request(target_paths=["C:\\Windows\\system32"]),
                RULE_TARGET_PATHS_BOUNDED,
                "target_paths[0]",
                "target_path_absolute",
            ),
            (
                "escaping path",
                request(target_paths=["../other-project/a.py"]),
                RULE_TARGET_PATHS_BOUNDED,
                "target_paths[0]",
                "target_path_escapes_project",
            ),
            (
                "remote target without a ref key",
                request(remote_targets=[{"kind": "git_remote"}]),
                RULE_REMOTE_TARGETS_DECLARED,
                "remote_targets[0]",
                "remote_target_not_bounded",
            ),
            (
                "too many remote targets",
                request(remote_targets=many_remotes),
                RULE_REMOTE_TARGETS_DECLARED,
                "remote_targets",
                "remote_target_count_exceeded",
            ),
            (
                "unknown remote kind",
                request(remote_targets=[{"kind": "smtp", "ref": "mail"}]),
                RULE_REMOTE_TARGETS_DECLARED,
                "remote_targets[0].kind",
                "remote_target_kind_unknown",
            ),
            (
                "remote target without a ref",
                request(remote_targets=[{"kind": "git_remote", "ref": ""}]),
                RULE_REMOTE_TARGETS_DECLARED,
                "remote_targets[0].ref",
                "remote_target_ref_missing",
            ),
            (
                "inline persisted content",
                request(persisted_content_refs=["the whole file contents"]),
                RULE_PERSISTED_CONTENT_REFERENCED,
                "persisted_content_refs[0]",
                "persisted_content_inline",
            ),
            (
                "too many persisted refs",
                request(persisted_content_refs=many_refs),
                RULE_PERSISTED_CONTENT_REFERENCED,
                "persisted_content_refs",
                "persisted_content_count_exceeded",
            ),
            (
                "unknown evidence claim",
                request(evidence_claims=["merged"]),
                RULE_EVIDENCE_CLAIM_BOUNDED,
                "evidence_claims[0]",
                "evidence_claim_unknown",
            ),
            (
                "observed claim without an observed record",
                request(evidence_claims=["merge_observed"]),
                RULE_EVIDENCE_CLAIM_BOUNDED,
                "evidence_claims[0]",
                "evidence_claim_unobserved",
            ),
            (
                "too many evidence claims",
                request(evidence_claims=many_claims),
                RULE_EVIDENCE_CLAIM_BOUNDED,
                "evidence_claims",
                "evidence_claim_count_exceeded",
            ),
            (
                "observed record ref that is not a ref",
                request(observed_record_refs=["record with spaces"]),
                RULE_EVIDENCE_CLAIM_BOUNDED,
                "observed_record_refs[0]",
                "observed_record_ref_invalid",
            ),
            # #801 data boundary. Same contract as every rule above: the
            # verdict names the rule, the offending field, and the correction.
            (
                "data classes not a list",
                request(data_classes="workspace_source"),
                RULE_DATA_CLASSES_PERMITTED,
                "data_classes",
                "data_class_not_bounded",
            ),
            (
                "too many data classes",
                request(data_classes=["workspace_source"] * (MAX_DATA_CLASSES + 1)),
                RULE_DATA_CLASSES_PERMITTED,
                "data_classes",
                "data_class_count_exceeded",
            ),
            (
                "unknown data class",
                request(data_classes=["telemetry"]),
                RULE_DATA_CLASSES_PERMITTED,
                "data_classes[0]",
                "data_class_unknown",
            ),
            (
                "prohibited data class",
                request(data_classes=["workspace_source", "personal_data"]),
                RULE_DATA_CLASSES_PERMITTED,
                "data_classes[1]",
                "data_class_prohibited",
            ),
            (
                "prohibited class content in an opaque ref",
                request(persisted_content_refs=["ssn-123-45-6789"]),
                RULE_DATA_CLASSES_PERMITTED,
                "persisted_content_refs[0]",
                "prohibited_data_class_content",
            ),
            (
                "workspace roots not a list",
                request(workspace_roots="src"),
                RULE_WORKSPACE_ROOTS_DECLARED,
                "workspace_roots",
                "workspace_root_not_bounded",
            ),
            (
                "too many workspace roots",
                request(workspace_roots=[f"src/p{index}" for index in range(MAX_WORKSPACE_ROOTS + 1)]),
                RULE_WORKSPACE_ROOTS_DECLARED,
                "workspace_roots",
                "workspace_root_count_exceeded",
            ),
            (
                "absolute workspace root",
                request(workspace_roots=["/etc"]),
                RULE_WORKSPACE_ROOTS_DECLARED,
                "workspace_roots[0]",
                "workspace_root_absolute",
            ),
            (
                "escaping workspace root",
                request(workspace_roots=["../other-project"]),
                RULE_WORKSPACE_ROOTS_DECLARED,
                "workspace_roots[0]",
                "workspace_root_escapes_project",
            ),
            (
                "target outside the approved roots",
                request(workspace_roots=["docs"], target_paths=["src/quality/safety_preflight.py"]),
                RULE_TARGET_PATHS_BOUNDED,
                "target_paths[0]",
                "target_path_outside_workspace_roots",
            ),
            (
                "approved destination without a ref key",
                request(approved_destinations=[{"kind": "git_remote"}]),
                RULE_DESTINATIONS_APPROVED,
                "approved_destinations[0]",
                "approved_destination_not_bounded",
            ),
            (
                "too many approved destinations",
                request(
                    approved_destinations=[
                        {"kind": "git_remote", "ref": f"remote-{index}"}
                        for index in range(MAX_APPROVED_DESTINATIONS + 1)
                    ]
                ),
                RULE_DESTINATIONS_APPROVED,
                "approved_destinations",
                "approved_destination_count_exceeded",
            ),
            (
                "unknown approved destination kind",
                request(approved_destinations=[{"kind": "smtp", "ref": "mail"}]),
                RULE_DESTINATIONS_APPROVED,
                "approved_destinations[0].kind",
                "approved_destination_kind_unknown",
            ),
            (
                "approved destination without a ref",
                request(approved_destinations=[{"kind": "git_remote", "ref": ""}]),
                RULE_DESTINATIONS_APPROVED,
                "approved_destinations[0].ref",
                "approved_destination_ref_missing",
            ),
            (
                "destination the boundary never declared",
                request(remote_targets=[{"kind": "git_remote", "ref": "origin"}]),
                RULE_DESTINATIONS_APPROVED,
                "remote_targets[0]",
                "destination_not_declared",
            ),
            (
                "access intents not a list",
                request(access_intents="read"),
                RULE_ACCESS_INTENT_DECLARED,
                "access_intents",
                "access_intent_not_bounded",
            ),
            (
                "too many access intents",
                request(access_intents=["read"] * (MAX_ACCESS_INTENTS + 1)),
                RULE_ACCESS_INTENT_DECLARED,
                "access_intents",
                "access_intent_count_exceeded",
            ),
            (
                "unknown access intent",
                request(access_intents=["exfiltrate"]),
                RULE_ACCESS_INTENT_DECLARED,
                "access_intents[0]",
                "access_intent_unknown",
            ),
            (
                "declared destination reached without the share intent",
                sharing_request(access_intents=["read"]),
                RULE_ACCESS_INTENT_DECLARED,
                "access_intents",
                "access_intent_undeclared",
            ),
            (
                "share intent the boundary approved no destination for",
                request(access_intents=["read", "share"]),
                RULE_ACCESS_INTENT_DECLARED,
                "access_intents",
                "access_intent_unapproved",
            ),
        ]

    def test_every_denial_names_the_rule_the_field_and_a_correction(self) -> None:
        reason_codes_by_rule = {
            str(rule["id"]): set(rule["reason_codes"]) for rule in safety_rule_profile()["rules"]
        }
        for label, candidate, rule_id, field, reason_code in self.denials():
            with self.subTest(label=label):
                verdict = evaluate_safety_preflight(candidate)
                self.assertEqual(verdict["status"], "deny")
                self.assertEqual(verdict["rule_id"], rule_id)
                self.assertEqual(verdict["field"], field)
                self.assertEqual(verdict["reason_code"], reason_code)
                self.assertTrue(verdict["correction"])
                self.assertIn(reason_code, reason_codes_by_rule[rule_id])
                self.assertEqual(verdict["safety_profile_revision"], safety_profile_revision())

    def test_an_observed_claim_backed_by_an_observed_record_is_allowed(self) -> None:
        verdict = evaluate_safety_preflight(
            request(evidence_claims=["merge_observed"], observed_record_refs=["run/2026-08-06/1"])
        )
        self.assertEqual(verdict["status"], "allow")

    def test_the_evidence_claim_vocabulary_stays_closed(self) -> None:
        self.assertEqual(
            EVIDENCE_CLAIMS,
            (
                "prepared_not_observed",
                "dispatch_observed",
                "result_observed",
                "review_observed",
                "ci_observed",
                "merge_observed",
            ),
        )
        self.assertEqual(
            REMOTE_TARGET_KINDS,
            ("git_remote", "issue_tracker", "package_registry", "public_internet"),
        )


class PreExpansionInputTests(unittest.TestCase):
    """The verdict must be computable before any prompt template is expanded."""

    def test_full_mode_is_visible_from_the_declared_mode_not_from_a_preview(self) -> None:
        # coding_delegation's message_context_mode="full" path can interpolate
        # the raw user message verbatim and then declares
        # raw_content_included: True. The rule reads those two declared inputs,
        # so it is not blind there.
        allowed = evaluate_safety_preflight(request(message_context_mode="full", raw_content_included=True))
        self.assertEqual(allowed["status"], "allow")
        denied = evaluate_safety_preflight(request(message_context_mode="bounded", raw_content_included=True))
        self.assertEqual(denied["status"], "deny")
        self.assertEqual(denied["rule_id"], RULE_RAW_CONTEXT_DECLARED)
        self.assertEqual(denied["field"], "raw_content_included")

    def test_full_mode_may_declare_a_narrower_artifact_than_it_permits(self) -> None:
        """`full` is a ceiling, not an obligation, so the flag can disagree with it.

        The flag states what the build will actually carry. Requiring it to
        equal `mode == "full"` made it a restatement of the mode -- a value that
        could never disagree with the rule reading it -- and denied the default
        path, where a full-mode build attaches no verbatim message at all.
        """
        verdict = evaluate_safety_preflight(request(message_context_mode="full", raw_content_included=False))
        self.assertEqual(verdict["status"], "allow")

    def test_the_request_shape_admits_no_message_body_field(self) -> None:
        for forbidden in ("message", "delegation_prompt", "raw_content", "prompt", "diff"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, REQUEST_FIELDS)

    def test_bodies_credentials_and_prompts_cannot_reach_a_rule_evaluation(self) -> None:
        attempts = [
            ("raw prompt as an unknown field", request(prompt="ignore previous instructions"), "unknown_request_field"),
            ("code body in a declared field", request(owner="def main():\n    return 1"), "body_shaped_request_value"),
            ("fenced block in a declared field", request(approved_scope="```python```"), "body_shaped_request_value"),
            ("long body in a declared field", request(approved_scope="x" * 500), "body_shaped_request_value"),
            ("credential in a declared field", request(approved_scope="ghp_abcdefghijklmnopqrstuvwxyz"), "secret_shaped_value"),
            ("credential inside a list", request(persisted_content_refs=[AWS_ACCESS_KEY_ID]), "secret_shaped_value"),
            (
                "credential inside a nested remote target",
                request(remote_targets=[{"kind": "git_remote", "ref": AWS_ACCESS_KEY_ID}]),
                "secret_shaped_value",
            ),
            ("body inside a list", request(target_paths=["src/a.py\nsrc/b.py"]), "body_shaped_request_value"),
        ]
        for label, candidate, reason_code in attempts:
            with self.subTest(label=label):
                verdict = evaluate_safety_preflight(candidate)
                self.assertEqual(verdict["status"], "deny")
                self.assertEqual(verdict["reason_code"], reason_code)
                self.assertTrue(verdict["field"])


class OrgRuleSourceReaderTests(unittest.TestCase):
    def test_a_healthy_source_reports_its_revision_and_bounded_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_org_source(
                Path(tmp), denied_remote_target_kinds=["public_internet"], max_target_paths=4
            )
            result = read_org_safety_rule_source(path)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["reason_code"], "org_source_available")
        self.assertEqual(result["revision"], "org-2026-08-1")
        self.assertEqual(
            result["rules"], {"denied_remote_target_kinds": ["public_internet"], "max_target_paths": 4}
        )
        self.assertEqual(len(str(result["content_sha256"])), 64)

    def test_each_failure_mode_is_unavailable_with_its_own_reason_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            healthy = write_org_source(root)
            oversized = root / "big.json"
            oversized.write_text("x" * (ORG_SAFETY_RULE_SOURCE_MAX_BYTES + 1), encoding="utf-8")
            malformed = root / "bad.json"
            malformed.write_text("{ not json", encoding="utf-8")
            not_an_object = root / "list.json"
            not_an_object.write_text("[]", encoding="utf-8")
            wrong_version = root / "version.json"
            wrong_version.write_text(json.dumps({"schema_version": "other/v9", "revision": "r"}), encoding="utf-8")
            extra_field = root / "extra.json"
            extra_field.write_text(json.dumps(org_document(allow_everything=True)), encoding="utf-8")
            unsafe_value = root / "unsafe.json"
            unsafe_value.write_text(json.dumps(org_document(revision="api_key=abc123")), encoding="utf-8")

            observed = {
                "missing": read_org_safety_rule_source(root / "absent.json")["reason_code"],
                "blank path": read_org_safety_rule_source("")["reason_code"],
                "directory": read_org_safety_rule_source(root)["reason_code"],
                "oversized": read_org_safety_rule_source(oversized)["reason_code"],
                "slow": read_org_safety_rule_source(healthy, clock=iter([0.0, 9.0]).__next__)["reason_code"],
                "malformed": read_org_safety_rule_source(malformed)["reason_code"],
                "not an object": read_org_safety_rule_source(not_an_object)["reason_code"],
                "unknown version": read_org_safety_rule_source(wrong_version)["reason_code"],
                "unknown field": read_org_safety_rule_source(extra_field)["reason_code"],
                "unsafe metadata": read_org_safety_rule_source(unsafe_value)["reason_code"],
            }
            with mock.patch.object(Path, "open", side_effect=PermissionError("denied")):
                observed["unreadable"] = read_org_safety_rule_source(healthy)["reason_code"]

        self.assertEqual(
            observed,
            {
                "missing": "org_source_missing",
                "blank path": "org_source_missing",
                "directory": "org_source_unsafe",
                "oversized": "org_source_oversized",
                "slow": "org_source_timed_out",
                "malformed": "org_source_malformed",
                "not an object": "org_source_malformed",
                "unknown version": "org_source_unknown_version",
                "unknown field": "org_source_unknown_fields",
                "unsafe metadata": "org_source_unsafe_metadata",
                "unreadable": "org_source_unreadable",
            },
        )
        self.assertLessEqual(set(observed.values()), set(ORG_SAFETY_RULE_SOURCE_REASON_CODES))

    def test_a_symlinked_source_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = write_org_source(root)
            link = root / "link.json"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is not permitted on this host")
            self.assertEqual(read_org_safety_rule_source(link)["reason_code"], "org_source_unsafe")

    def test_the_reader_never_returns_content_for_an_unavailable_source(self) -> None:
        result = read_org_safety_rule_source("")
        self.assertEqual(result["rules"], {})
        self.assertEqual(result["revision"], "")
        self.assertEqual(result["content_sha256"], "")
        self.assertTrue(result["field"])


class OrgLevelTests(unittest.TestCase):
    def unavailable(self, reason_code: str) -> dict[str, object]:
        return {
            "schema_version": "omh_org_safety_rule_source/v1",
            "status": "unavailable",
            "reason_code": reason_code,
            "field": "org_rule_source.source_path",
            "source_identity_sha256": "0" * 64,
            "content_sha256": "",
            "revision": "",
            "rules": {},
            "claim_boundary": "not compliance, execution, review, ci, merge",
        }

    def available(self, **rules: object) -> dict[str, object]:
        return {
            "schema_version": "omh_org_safety_rule_source/v1",
            "status": "available",
            "reason_code": "org_source_available",
            "field": "",
            "source_identity_sha256": "0" * 64,
            "content_sha256": "1" * 64,
            "revision": "org-1",
            "rules": {"denied_remote_target_kinds": [], "max_target_paths": None, **rules},
            "claim_boundary": "not compliance, execution, review, ci, merge",
        }

    def test_every_unavailable_org_source_denies_rather_than_allowing(self) -> None:
        for reason_code in ORG_SAFETY_RULE_SOURCE_REASON_CODES:
            if reason_code == "org_source_available":
                continue
            with self.subTest(reason_code=reason_code):
                verdict = evaluate_safety_preflight(request(), org_rule_source=self.unavailable(reason_code))
                self.assertEqual(verdict["status"], "deny")
                self.assertEqual(verdict["rule_id"], RULE_ORG_RULE_SOURCE)
                self.assertEqual(verdict["reason_code"], reason_code)
                self.assertTrue(verdict["field"])
                self.assertTrue(verdict["correction"])
                self.assertIn(reason_code, verdict["org_reason_codes"])
                self.assertEqual(verdict["levels_applied"], ["builtin_omh", "org"])

    def test_a_malformed_org_result_object_denies(self) -> None:
        for candidate in ({}, {"status": "available"}, {"reason_code": "made_up"}, "available", []):
            with self.subTest(candidate=candidate):
                verdict = evaluate_safety_preflight(request(), org_rule_source=candidate)
                self.assertEqual(verdict["status"], "deny")
                self.assertEqual(verdict["reason_code"], "org_source_malformed")

    def test_an_org_rule_value_this_revision_cannot_honour_denies(self) -> None:
        for rules in ({"denied_remote_target_kinds": ["smtp"]}, {"max_target_paths": -1}, {"max_target_paths": "four"}):
            with self.subTest(rules=rules):
                verdict = evaluate_safety_preflight(request(), org_rule_source=self.available(**rules))
                self.assertEqual(verdict["status"], "deny")
                self.assertEqual(verdict["rule_id"], RULE_ORG_RULE_SOURCE)
                self.assertEqual(verdict["reason_code"], "org_rule_unknown_value")
                self.assertTrue(verdict["field"])

    def test_the_org_level_narrows_and_names_the_offending_field(self) -> None:
        verdict = evaluate_safety_preflight(
            sharing_request("public_internet", "example-host"),
            org_rule_source=self.available(denied_remote_target_kinds=["public_internet"]),
        )
        self.assertEqual(verdict["status"], "deny")
        self.assertEqual(verdict["rule_id"], RULE_ORG_RULE_SOURCE)
        self.assertEqual(verdict["field"], "remote_targets[0].kind")
        self.assertEqual(verdict["reason_code"], "org_rule_denied")
        self.assertIn("org_rule_denied", verdict["org_reason_codes"])

        capped = evaluate_safety_preflight(
            request(target_paths=["src/a.py", "src/b.py"]),
            org_rule_source=self.available(max_target_paths=1),
        )
        self.assertEqual(capped["status"], "deny")
        self.assertEqual(capped["field"], "target_paths")
        self.assertEqual(capped["reason_code"], "org_rule_denied")

    def test_the_org_level_cannot_widen_a_builtin_denial(self) -> None:
        too_many = [f"src/p{index}.py" for index in range(MAX_TARGET_PATHS + 1)]
        verdict = evaluate_safety_preflight(
            request(target_paths=too_many),
            org_rule_source=self.available(max_target_paths=MAX_TARGET_PATHS * 10),
        )
        self.assertEqual(verdict["status"], "deny")
        self.assertEqual(verdict["rule_id"], RULE_TARGET_PATHS_BOUNDED)
        self.assertEqual(verdict["reason_code"], "target_path_count_exceeded")
        self.assertIn("org_widening_ignored", verdict["org_reason_codes"])

    def test_a_builtin_denial_still_wins_when_the_org_source_is_also_broken(self) -> None:
        verdict = evaluate_safety_preflight(
            request(owner=""), org_rule_source=self.unavailable("org_source_missing")
        )
        self.assertEqual(verdict["status"], "deny")
        self.assertEqual(verdict["rule_id"], RULE_OWNER_DECLARED)
        self.assertIn("org_source_missing", verdict["org_reason_codes"])

    def test_a_healthy_org_level_stays_invisible_on_success(self) -> None:
        verdict = evaluate_safety_preflight(request(), org_rule_source=self.available())
        self.assertEqual(verdict["status"], "allow")
        self.assertEqual((verdict["rule_id"], verdict["field"], verdict["correction"]), ("", "", ""))
        self.assertEqual(verdict["org_reason_codes"], ["org_source_available"])

    def test_the_same_input_and_revision_yields_the_same_decision_with_the_org_level(self) -> None:
        source = self.available(denied_remote_target_kinds=["public_internet"], max_target_paths=2)
        candidate = sharing_request()
        first = json.dumps(evaluate_safety_preflight(candidate, org_rule_source=source), sort_keys=True)
        for _ in range(5):
            self.assertEqual(
                json.dumps(evaluate_safety_preflight(candidate, org_rule_source=source), sort_keys=True), first
            )


class RevisionRecheckTests(unittest.TestCase):
    def test_a_carried_verdict_or_revision_string_is_current(self) -> None:
        verdict = evaluate_safety_preflight(request())
        for carried in (verdict, verdict["safety_profile_revision"]):
            with self.subTest(carried=type(carried).__name__):
                recheck = recheck_safety_preflight_revision(carried)
                self.assertEqual(recheck["status"], "current")
                self.assertEqual(recheck["reason_code"], "revision_current")
                self.assertTrue(recheck["dispatchable"])

    def test_a_drifted_or_absent_revision_is_not_dispatchable(self) -> None:
        for carried, status, reason_code in (
            ("0" * 64, "drifted", "revision_drifted"),
            ("", "missing", "revision_missing"),
            (None, "missing", "revision_missing"),
            ({}, "missing", "revision_missing"),
        ):
            with self.subTest(status=status):
                recheck = recheck_safety_preflight_revision(carried)
                self.assertEqual(recheck["status"], status)
                self.assertEqual(recheck["reason_code"], reason_code)
                self.assertFalse(recheck["dispatchable"])
                self.assertEqual(recheck["live_revision"], safety_profile_revision())


class AlreadyMetAcceptanceCriterionPins(unittest.TestCase):
    """Issue #805 AC1 and AC2, already satisfied before the issue was worked.

    Nothing below is new behaviour and nothing below was reimplemented. Both
    criteria are pinned here because AC3 is built directly on top of them: the
    retention path decides which revision is in force, which is only meaningful
    while a verdict still reports its revision and a drifted revision still
    blocks. A silent regression in either would make AC3 answer a question
    nobody is asking.
    """

    def test_ac1_every_evaluated_artifact_reports_its_rule_revision(self) -> None:
        # Already met. `_verdict` pins `safety_profile_revision` on every
        # verdict it emits -- allow, builtin denial, org denial, and the
        # "not even an object" refusal alike -- and the durable records that
        # quote a verdict carry the same field onward
        # (`workflows/approval_receipts.py`, `workflows/blocked_work_records.py`,
        # each with their own tests). This is the pin, not a reimplementation.
        live = safety_profile_revision()
        cases: list[tuple[str, object, dict[str, object] | None]] = [
            ("allow", request(), None),
            ("builtin denial", request(owner=""), None),
            ("non-object request", "not a request", None),
            (
                "org denial",
                request(),
                {
                    "schema_version": "omh_org_safety_rule_source/v1",
                    "status": "unavailable",
                    "reason_code": "org_source_missing",
                    "field": "org_rule_source.source_path",
                    "source_identity_sha256": "0" * 64,
                    "content_sha256": "",
                    "revision": "",
                    "rules": {},
                    "claim_boundary": "not compliance, execution, review, ci, merge",
                },
            ),
        ]
        for label, candidate, org in cases:
            with self.subTest(label=label):
                verdict = evaluate_safety_preflight(candidate, org_rule_source=org)
                self.assertEqual(verdict["safety_profile_revision"], live)
                self.assertEqual(len(str(verdict["safety_profile_revision"])), 64)

    def test_ac2_changed_rules_block_continuation_until_re_evaluated(self) -> None:
        # Already met. `recheck_safety_preflight_revision` reports `drifted` for
        # a carried revision the live profile no longer matches and refuses to
        # call it dispatchable, and `coding/action_gate._revision_drift_denial`
        # turns exactly that into a denial *before* arbitration runs, which
        # `tests/test_risky_action_confirmation.py` already asserts. This is the
        # pin for the preflight half.
        allowed = evaluate_safety_preflight(request())
        self.assertEqual(allowed["status"], "allow")
        self.assertTrue(recheck_safety_preflight_revision(allowed)["dispatchable"])

        carried_under_an_older_profile = dict(allowed, safety_profile_revision="0" * 64)
        drifted = recheck_safety_preflight_revision(carried_under_an_older_profile)
        self.assertEqual((drifted["status"], drifted["reason_code"]), ("drifted", "revision_drifted"))
        self.assertFalse(drifted["dispatchable"])
        self.assertEqual(drifted["live_revision"], safety_profile_revision())

        # Re-evaluating under the live profile is the only thing that clears it.
        # The carried verdict's own status is untouched by the recheck, so a
        # stale allow cannot talk its way past the drift.
        self.assertEqual(carried_under_an_older_profile["status"], "allow")
        self.assertTrue(recheck_safety_preflight_revision(evaluate_safety_preflight(request()))["dispatchable"])


class NoModelNoNetworkNoDependencyTests(unittest.TestCase):
    """Asserted by construction: the imports are the whole story."""

    # `sys` joined the set for `data_boundary_enforcement_facts()`: naming the
    # platform is how the per-host confinement answer stays a probe instead of
    # a claim. Still stdlib, still no process, no socket, no model.
    ALLOWED_IMPORTS = frozenset(
        {"__future__", "ast", "collections", "hashlib", "json", "os", "pathlib", "re", "sys", "time", "typing"}
    )
    FORBIDDEN_IMPORTS = frozenset(
        {"socket", "ssl", "urllib", "http", "requests", "httpx", "aiohttp", "subprocess", "asyncio"}
    )

    def imported_roots(self, path: Path) -> set[str]:
        roots: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        return roots

    def test_the_rules_run_on_the_standard_library_alone(self) -> None:
        for path in (PREFLIGHT_SOURCE, GOVERNANCE_SOURCE):
            with self.subTest(path=path.name):
                roots = self.imported_roots(path)
                self.assertLessEqual(roots, self.ALLOWED_IMPORTS)
                self.assertEqual(roots & self.FORBIDDEN_IMPORTS, set())

    def test_the_pinned_dependency_set_is_still_empty(self) -> None:
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("dependencies = []", text)

    def test_the_module_never_borrows_the_routing_guard_vocabulary(self) -> None:
        # routing/policy.py's ~60 *_guard_applies helpers key off words in a
        # user message. A safety rule wired into that vocabulary would make a
        # safety decision depend on user text, so the word stays out of here.
        source = PREFLIGHT_SOURCE.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        body = code.split('"""', 2)[-1]
        self.assertNotIn("guard", body.lower())


class OrgRuleSourceOptInTests(unittest.TestCase):
    def paths(self, root: Path):
        paths = resolve_paths(root / ".omh", root / ".hermes")
        paths.setup_profile_path.parent.mkdir(parents=True, exist_ok=True)
        return paths

    def test_the_opt_in_defaults_to_off(self) -> None:
        policy = build_org_rule_source_policy()
        self.assertEqual(policy["schema_version"], ORG_RULE_SOURCE_POLICY_SCHEMA_VERSION)
        self.assertFalse(policy["enabled"])
        self.assertEqual(policy["source_path"], "")
        self.assertFalse(org_rule_source_enabled(policy))
        self.assertEqual(org_rule_source_path(policy), "")

    def test_every_persisted_value_is_a_scalar(self) -> None:
        for value in build_org_rule_source_policy(enabled=True, source_path="/org/rules.json").values():
            self.assertIsInstance(value, (str, bool, int))

    def test_an_absent_key_reads_as_off_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.paths(Path(tmp))
            self.assertFalse(org_rule_source_enabled(read_org_rule_source_policy(paths)))
            write_capability_policy(paths, ["retain_knowledge"])
            self.assertFalse(org_rule_source_enabled(read_org_rule_source_policy(paths)))
            written = write_org_rule_source_policy(paths, enabled=True, source_path="/org/rules.json")
            reread = read_org_rule_source_policy(paths)
            self.assertEqual(reread, written)
            self.assertTrue(org_rule_source_enabled(reread))
            self.assertEqual(org_rule_source_path(reread), "/org/rules.json")

    def test_writing_the_opt_in_preserves_every_other_profile_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.paths(Path(tmp))
            write_capability_policy(paths, ["retain_knowledge"])
            before = read_capability_policy(paths)
            write_org_rule_source_policy(paths, enabled=True, source_path="/org/rules.json")
            self.assertEqual(read_capability_policy(paths), before)
            self.assertNotEqual(before, build_capability_policy())

    def test_a_hand_edited_value_cannot_smuggle_extra_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.paths(Path(tmp))
            paths.setup_profile_path.write_text(
                json.dumps(
                    {
                        "org_rule_source_policy": {
                            "enabled": "yes",
                            "source_path": " /org/rules.json ",
                            "extra": {"nested": True},
                        }
                    }
                ),
                encoding="utf-8",
            )
            policy = read_org_rule_source_policy(paths)
            # `attestation_key_path` joined the closed key set in #805. The
            # bound did not move: a hand-edited `extra` is still dropped, and an
            # absent attestation key still reads as the blank default, which is
            # "no local attestation required".
            self.assertEqual(
                set(policy),
                {"schema_version", "enabled", "source_path", "attestation_key_path", "claim_boundary"},
            )
            self.assertTrue(policy["enabled"])
            self.assertEqual(policy["source_path"], "/org/rules.json")
            self.assertEqual(policy["attestation_key_path"], "")

    def test_a_non_object_value_reads_as_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.paths(Path(tmp))
            paths.setup_profile_path.write_text(
                json.dumps({"org_rule_source_policy": "enabled"}), encoding="utf-8"
            )
            self.assertFalse(org_rule_source_enabled(read_org_rule_source_policy(paths)))

    def test_the_capability_policy_report_shows_the_opt_in_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.paths(Path(tmp))
            report = _policy_report(paths)
            self.assertFalse(report["org_rule_source_enabled"])
            self.assertEqual(report["org_rule_source_path"], "")
            write_org_rule_source_policy(paths, enabled=True, source_path="/org/rules.json")
            report = _policy_report(paths)
            self.assertTrue(report["org_rule_source_enabled"])
            self.assertEqual(report["org_rule_source_path"], "/org/rules.json")
            self.assertEqual(
                report["org_rule_source_policy"]["schema_version"], ORG_RULE_SOURCE_POLICY_SCHEMA_VERSION
            )


class EndToEndOrgSourceTests(unittest.TestCase):
    def test_an_opted_in_source_on_disk_drives_the_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_org_source(root, denied_remote_target_kinds=["public_internet"])
            source = read_org_safety_rule_source(path)
            denied = evaluate_safety_preflight(
                sharing_request("public_internet", "example-host"),
                org_rule_source=source,
            )
            allowed = evaluate_safety_preflight(sharing_request(), org_rule_source=source)
        self.assertEqual(denied["status"], "deny")
        self.assertEqual(denied["reason_code"], "org_rule_denied")
        self.assertEqual(allowed["status"], "allow")

    def test_an_opted_in_but_absent_source_denies_instead_of_allowing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = read_org_safety_rule_source(Path(tmp) / "absent.json")
            verdict = evaluate_safety_preflight(request(), org_rule_source=source)
        self.assertEqual(verdict["status"], "deny")
        self.assertEqual(verdict["reason_code"], "org_source_missing")
        self.assertTrue(verdict["correction"])


if __name__ == "__main__":
    unittest.main()
