"""Contract tests for the local safety-rule attestation lane (issue #805 AC3).

Two axes, and the whole point is that they stay two. `signature_state` answers
"did the local tag verify" and the org source `status` answers "is this a rule
document OMH can read". Every test below either pins one axis while the other
moves, or pins what a failure on the attestation axis may NOT do: activate a new
revision, discard the last one that verified, or leave a half-applied rule set
behind.

Honesty is also under test. The tag is HMAC-SHA256 with a key the operator holds
locally, which is symmetric: it shows the bytes were not altered by anyone
without the key, it does not show who wrote them, and a reader of the key can
mint a tag that verifies. `test_the_module_says_plainly_what_the_tag_does_not_prove`
and `test_a_key_holder_can_mint_a_tag_that_verifies` keep that limitation stated
rather than implied.

Every file these tests hash goes through `atomic_write_text`, never
`Path.write_text`: the tag covers file bytes, and text-mode newline translation
would produce a different tag on Windows than on POSIX for the same source.
"""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from _local_package import load_local_package

load_local_package()

from omh.coding.project_governance import (  # noqa: E402
    ORG_SAFETY_RULE_SOURCE_REASON_CODES,
    ORG_SAFETY_RULE_SOURCE_SCHEMA_VERSION,
    org_safety_rule_source_unavailable,
    read_org_safety_rule_source,
)
from omh.coding.safety_rule_attestation import (  # noqa: E402
    ATTESTATION_KEY_FIELD,
    ATTESTATION_TAG_FIELD,
    ATTESTATION_TAG_SUFFIX,
    ORG_SAFETY_RULE_ACTIVATION_SCHEMA_VERSION,
    ORG_SAFETY_RULE_ACTIVATION_STATUSES,
    SAFETY_RULE_ATTESTATION_ALGORITHM,
    SAFETY_RULE_ATTESTATION_CLAIM_BOUNDARY,
    SAFETY_RULE_ATTESTATION_REASON_CODES,
    SAFETY_RULE_TRUST_STATE_SCHEMA_VERSION,
    SIGNATURE_STATES,
    activate_org_safety_rules,
    default_attestation_tag_path,
    read_safety_rule_trust_state,
    safety_rule_attestation_tag,
    verify_safety_rule_attestation,
    write_safety_rule_trust_state,
)
from omh.local_store import atomic_write_text  # noqa: E402
from omh.paths import resolve_paths  # noqa: E402
from omh.quality.safety_preflight import evaluate_safety_preflight  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
ATTESTATION_SOURCE = REPO_ROOT / "src" / "coding" / "safety_rule_attestation.py"

ATTESTED_AT = "2026-08-09T00:00:00Z"


def org_source_text(revision: str = "org-1", **overrides: object) -> str:
    document: dict[str, object] = {
        "schema_version": ORG_SAFETY_RULE_SOURCE_SCHEMA_VERSION,
        "revision": revision,
    }
    document.update(overrides)
    return json.dumps(document, sort_keys=True)


def preflight_request(**overrides: object) -> dict[str, object]:
    """A minimal allowed request, the same shape `test_safety_preflight` uses."""
    base: dict[str, object] = {
        "owner": "codex",
        "approved_scope": "issue-805",
        "message_context_mode": "bounded",
        "raw_content_included": False,
        "data_classes": ["workspace_metadata"],
        "workspace_roots": [],
        "target_paths": ["src/coding/safety_rule_attestation.py"],
        "remote_targets": [],
        "approved_destinations": [],
        "access_intents": ["read"],
        "persisted_content_refs": [],
        "evidence_claims": ["prepared_not_observed"],
        "observed_record_refs": [],
    }
    base.update(overrides)
    return base


class AttestationFixture(unittest.TestCase):
    """One org rule source, one operator key, one trust file, all local."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "org-rules.json"
        self.key = self.root / "attestation.key"
        self.trust = self.root / "omh-home" / "runtime" / "safety_rule_trust.json"
        atomic_write_text(self.key, "a-local-operator-secret\n")

    @property
    def tag(self) -> Path:
        path = default_attestation_tag_path(self.source)
        assert path is not None
        return path

    def write_source(self, revision: str = "org-1", **overrides: object) -> None:
        atomic_write_text(self.source, org_source_text(revision, **overrides))

    def write_valid_tag(self, *, key: Path | None = None, suffix: str = "\n") -> str:
        digest = safety_rule_attestation_tag(self.source.read_bytes(), key_path=key or self.key)
        atomic_write_text(self.tag, digest + suffix)
        return digest

    def activate(self, **overrides: object) -> dict[str, object]:
        options: dict[str, object] = {
            "attestation_key_path": self.key,
            "trust_state_path": self.trust,
            "attested_at": ATTESTED_AT,
        }
        options.update(overrides)
        return activate_org_safety_rules(self.source, **options)  # type: ignore[arg-type]


class UnconfiguredInstallsAreUntouched(AttestationFixture):
    """The opt-in half: with no key, #805 is not in the picture at all."""

    def test_no_configured_key_is_not_required_and_activates_as_before(self) -> None:
        self.write_source(revision="org-1", denied_remote_target_kinds=["public_internet"])
        activation = activate_org_safety_rules(self.source, trust_state_path=self.trust)
        self.assertEqual(activation["signature_state"], "not_required")
        self.assertEqual(activation["signature_reason_code"], "attestation_not_required")
        self.assertEqual(activation["signature_field"], "")
        self.assertEqual(activation["status"], "activated")
        self.assertEqual(activation["effective_revision"], "org-1")
        self.assertEqual(activation["rejected_revision"], "")
        # The result handed to the evaluator is exactly what the pre-#805 reader
        # returns for the same file. Not equivalent -- equal.
        self.assertEqual(activation["org_rule_source"], read_org_safety_rule_source(self.source))

    def test_an_unconfigured_install_never_creates_a_trust_file(self) -> None:
        self.write_source()
        activate_org_safety_rules(self.source, trust_state_path=self.trust)
        self.assertFalse(self.trust.exists())
        self.assertIsNone(read_safety_rule_trust_state(self.trust))

    def test_a_tag_lying_around_is_ignored_when_no_key_is_configured(self) -> None:
        self.write_source()
        atomic_write_text(self.tag, "0" * 64 + "\n")
        activation = activate_org_safety_rules(self.source, trust_state_path=self.trust)
        self.assertEqual(activation["signature_state"], "not_required")
        self.assertEqual(activation["status"], "activated")
        self.assertEqual(activation["org_rule_source"], read_org_safety_rule_source(self.source))

    def test_the_verdict_is_unchanged_when_nothing_is_configured(self) -> None:
        self.write_source(denied_remote_target_kinds=["public_internet"])
        activation = activate_org_safety_rules(self.source, trust_state_path=self.trust)
        with_activation = evaluate_safety_preflight(
            preflight_request(), org_rule_source=activation["org_rule_source"]
        )
        without = evaluate_safety_preflight(
            preflight_request(), org_rule_source=read_org_safety_rule_source(self.source)
        )
        self.assertEqual(with_activation, without)
        self.assertEqual(with_activation["status"], "allow")


class AValidTagActivates(AttestationFixture):
    def test_a_valid_tag_activates_the_revision_and_records_it(self) -> None:
        self.write_source(revision="org-1", max_target_paths=4)
        self.write_valid_tag()
        activation = self.activate()
        self.assertEqual(activation["signature_state"], "valid")
        self.assertEqual(activation["signature_reason_code"], "attestation_valid")
        self.assertEqual(activation["status"], "activated")
        self.assertEqual(activation["effective_revision"], "org-1")
        self.assertEqual(activation["rejected_revision"], "")
        source = activation["org_rule_source"]
        assert isinstance(source, dict)
        self.assertEqual(source["status"], "available")
        self.assertEqual(source["revision"], "org-1")
        self.assertEqual(source, read_org_safety_rule_source(self.source))

    def test_a_verified_revision_becomes_the_trust_state(self) -> None:
        self.write_source(revision="org-1", denied_remote_target_kinds=["public_internet"], max_target_paths=4)
        self.write_valid_tag()
        self.activate()
        trust = read_safety_rule_trust_state(self.trust)
        assert trust is not None
        self.assertEqual(trust["schema_version"], SAFETY_RULE_TRUST_STATE_SCHEMA_VERSION)
        self.assertEqual(trust["revision"], "org-1")
        self.assertEqual(trust["denied_remote_target_kinds"], ["public_internet"])
        self.assertEqual(trust["max_target_paths"], 4)
        self.assertEqual(trust["attested_at"], ATTESTED_AT)
        self.assertEqual(len(str(trust["content_sha256"])), 64)

    def test_activation_without_a_trust_path_records_nothing(self) -> None:
        self.write_source()
        self.write_valid_tag()
        activation = self.activate(trust_state_path=None)
        self.assertEqual(activation["status"], "activated")
        self.assertFalse(self.trust.exists())

    def test_the_tag_covers_the_exact_source_bytes(self) -> None:
        self.write_source(revision="org-1")
        self.write_valid_tag()
        self.assertEqual(self.activate()["signature_state"], "valid")
        # One changed byte in the source and the same tag no longer verifies.
        self.write_source(revision="org-2")
        self.assertEqual(self.activate()["signature_state"], "invalid")

    def test_a_trailing_newline_or_crlf_around_the_tag_still_verifies(self) -> None:
        # `atomic_write_text` writes with newline="", so these are the literal
        # bytes an editor on either platform would leave behind.
        self.write_source()
        for suffix in ("", "\n", "\r\n", "\n\n"):
            with self.subTest(suffix=repr(suffix)):
                self.write_valid_tag(suffix=suffix)
                self.assertEqual(self.activate()["signature_state"], "valid")

    def test_an_uppercase_tag_still_verifies(self) -> None:
        self.write_source()
        digest = safety_rule_attestation_tag(self.source.read_bytes(), key_path=self.key)
        atomic_write_text(self.tag, digest.upper() + "\n")
        self.assertEqual(self.activate()["signature_state"], "valid")


class AFailedAttestationDoesNotActivate(AttestationFixture):
    def attest_first_revision(self) -> None:
        self.write_source(revision="org-1", denied_remote_target_kinds=["public_internet"])
        self.write_valid_tag()
        self.assertEqual(self.activate()["status"], "activated")

    def test_an_invalid_tag_retains_the_last_valid_set_and_names_the_failure(self) -> None:
        self.attest_first_revision()
        # A new revision whose bytes the existing tag no longer covers.
        self.write_source(revision="org-2", max_target_paths=1)
        activation = self.activate()
        self.assertEqual(activation["signature_state"], "invalid")
        self.assertEqual(activation["signature_reason_code"], "attestation_tag_invalid")
        self.assertEqual(activation["signature_field"], ATTESTATION_TAG_FIELD)
        self.assertEqual(activation["status"], "retained")
        self.assertEqual(activation["effective_revision"], "org-1")
        self.assertEqual(activation["rejected_revision"], "org-2")

    def test_the_retained_rule_set_carries_nothing_from_the_rejected_revision(self) -> None:
        self.attest_first_revision()
        self.write_source(revision="org-2", denied_remote_target_kinds=["git_remote"], max_target_paths=1)
        source = self.activate()["org_rule_source"]
        assert isinstance(source, dict)
        self.assertEqual(source["status"], "available")
        self.assertEqual(source["revision"], "org-1")
        self.assertEqual(
            source["rules"], {"denied_remote_target_kinds": ["public_internet"], "max_target_paths": None}
        )
        # No partial application: not the rejected revision label, not its cap,
        # and not its digest anywhere in the payload that goes on to be applied.
        rendered = json.dumps(source, sort_keys=True)
        self.assertNotIn("org-2", rendered)
        self.assertNotIn("git_remote", rendered)

    def test_a_missing_tag_refuses_with_its_own_reason(self) -> None:
        self.write_source(revision="org-1")
        activation = self.activate()
        self.assertEqual(activation["signature_state"], "missing")
        self.assertEqual(activation["signature_reason_code"], "attestation_tag_missing")
        self.assertEqual(activation["signature_field"], ATTESTATION_TAG_FIELD)
        self.assertEqual(activation["status"], "unavailable")
        self.assertEqual(activation["effective_revision"], "")
        self.assertEqual(activation["rejected_revision"], "org-1")
        source = activation["org_rule_source"]
        assert isinstance(source, dict)
        self.assertEqual(source["status"], "unavailable")
        self.assertEqual(source["reason_code"], "org_source_attestation_missing")

    def test_a_missing_tag_retains_a_previously_valid_set(self) -> None:
        self.attest_first_revision()
        self.tag.unlink()
        self.write_source(revision="org-2")
        activation = self.activate()
        self.assertEqual(activation["signature_state"], "missing")
        self.assertEqual(activation["status"], "retained")
        self.assertEqual(activation["effective_revision"], "org-1")
        self.assertEqual(activation["rejected_revision"], "org-2")

    def test_an_unreadable_key_is_unverifiable_and_activates_nothing(self) -> None:
        self.write_source(revision="org-1")
        self.write_valid_tag()
        for label, key_path in (
            ("absent key file", self.root / "absent.key"),
            ("directory as a key", self.root),
            ("blank-looking key file", self.write_blank_key()),
        ):
            with self.subTest(label=label):
                activation = self.activate(attestation_key_path=key_path)
                self.assertEqual(activation["signature_state"], "unverifiable")
                self.assertEqual(activation["signature_reason_code"], "attestation_key_unreadable")
                self.assertEqual(activation["signature_field"], ATTESTATION_KEY_FIELD)
                self.assertNotEqual(activation["status"], "activated")
                self.assertEqual(activation["effective_revision"], "")
                source = activation["org_rule_source"]
                assert isinstance(source, dict)
                self.assertEqual(source["reason_code"], "org_source_attestation_key_unreadable")

    def test_unverifiable_retains_rather_than_activating_when_a_valid_set_exists(self) -> None:
        self.attest_first_revision()
        self.write_source(revision="org-2")
        activation = self.activate(attestation_key_path=self.root / "absent.key")
        self.assertEqual(activation["signature_state"], "unverifiable")
        self.assertEqual(activation["status"], "retained")
        self.assertEqual(activation["effective_revision"], "org-1")
        self.assertEqual(activation["rejected_revision"], "org-2")

    def test_a_failed_verification_leaves_the_trust_file_byte_identical(self) -> None:
        self.attest_first_revision()
        before = self.trust.read_bytes()
        self.write_source(revision="org-2", max_target_paths=1)
        for _ in range(3):
            self.assertEqual(self.activate()["status"], "retained")
        self.assertEqual(self.trust.read_bytes(), before)

    def test_the_retained_set_is_the_last_valid_one_not_merely_the_previous_one(self) -> None:
        self.attest_first_revision()
        # Two rejected revisions in a row. Neither may become "the previous
        # one" that a later failure falls back to.
        for revision in ("org-2", "org-3"):
            self.write_source(revision=revision, max_target_paths=1)
            activation = self.activate()
            self.assertEqual(activation["status"], "retained")
            self.assertEqual(activation["effective_revision"], "org-1")
            self.assertEqual(activation["rejected_revision"], revision)
        trust = read_safety_rule_trust_state(self.trust)
        assert trust is not None
        self.assertEqual(trust["revision"], "org-1")

    def test_a_retained_set_earned_by_another_source_path_is_not_inherited(self) -> None:
        self.attest_first_revision()
        other = self.root / "other-rules.json"
        atomic_write_text(other, org_source_text("org-other"))
        activation = activate_org_safety_rules(
            other,
            attestation_key_path=self.key,
            trust_state_path=self.trust,
            attested_at=ATTESTED_AT,
        )
        self.assertEqual(activation["signature_state"], "missing")
        self.assertEqual(activation["status"], "unavailable")
        self.assertEqual(activation["effective_revision"], "")

    def write_blank_key(self) -> Path:
        path = self.root / "blank.key"
        atomic_write_text(path, "   \n")
        return path


class TheTwoAxesStaySeparate(AttestationFixture):
    def test_a_valid_tag_over_a_malformed_source_is_still_unavailable(self) -> None:
        atomic_write_text(self.source, "{ not json at all")
        self.write_valid_tag()
        activation = self.activate()
        # Attestation axis: the bytes are the attested bytes.
        self.assertEqual(activation["signature_state"], "valid")
        self.assertEqual(activation["signature_reason_code"], "attestation_valid")
        # Safety axis: unchanged, and for the reader's own reason.
        self.assertEqual(activation["status"], "unavailable")
        source = activation["org_rule_source"]
        assert isinstance(source, dict)
        self.assertEqual(source["status"], "unavailable")
        self.assertEqual(source["reason_code"], "org_source_malformed")
        self.assertEqual(source["rules"], {})

    def test_a_valid_tag_over_a_malformed_source_records_no_trust_state(self) -> None:
        atomic_write_text(self.source, "{ not json at all")
        self.write_valid_tag()
        self.activate()
        self.assertIsNone(read_safety_rule_trust_state(self.trust))

    def test_an_invalid_tag_over_a_well_formed_source_refuses_for_a_different_reason(self) -> None:
        atomic_write_text(self.source, "{ not json at all")
        self.write_valid_tag()
        malformed = self.activate()["org_rule_source"]
        self.write_source(revision="org-1")
        unattested = self.activate()["org_rule_source"]
        assert isinstance(malformed, dict) and isinstance(unattested, dict)
        self.assertEqual(malformed["status"], unattested["status"])
        self.assertNotEqual(malformed["reason_code"], unattested["reason_code"])
        self.assertEqual(unattested["reason_code"], "org_source_attestation_invalid")

    def test_signature_state_and_safety_status_are_independent_fields(self) -> None:
        """Both values of one axis occur against both values of the other."""
        observed: set[tuple[str, str]] = set()

        # invalid + unavailable: a bad tag with nothing on record to fall back to.
        self.write_source(revision="org-1")
        atomic_write_text(self.tag, "0" * 64)
        observed.add(self._axes(self.activate()))

        # valid + available: the ordinary activation.
        self.write_valid_tag()
        observed.add(self._axes(self.activate()))

        # valid + unavailable: the attested bytes are not a rule document.
        atomic_write_text(self.source, "[]")
        self.write_valid_tag()
        observed.add(self._axes(self.activate()))

        # invalid + available: the tag failed, and the retained set is in force.
        self.write_source(revision="org-2")
        observed.add(self._axes(self.activate()))

        # not_required + available: nothing configured, nothing checked.
        observed.add(self._axes(activate_org_safety_rules(self.source, trust_state_path=self.trust)))

        self.assertEqual(
            observed,
            {
                ("invalid", "unavailable"),
                ("valid", "available"),
                ("valid", "unavailable"),
                ("invalid", "available"),
                ("not_required", "available"),
            },
        )

    def _axes(self, activation: dict[str, object]) -> tuple[str, str]:
        source = activation["org_rule_source"]
        assert isinstance(source, dict)
        return str(activation["signature_state"]), str(source["status"])


class RefusalsReachTheEvaluator(AttestationFixture):
    """A refusal is only real if the preflight turns it into a denial."""

    def test_every_attestation_refusal_denies_with_a_correction(self) -> None:
        self.write_source(revision="org-1")
        expected = {
            "missing tag": ("org_source_attestation_missing", {}),
            "invalid tag": ("org_source_attestation_invalid", {"tag": "0" * 64}),
            "unreadable key": ("org_source_attestation_key_unreadable", {"key": self.root / "absent.key"}),
        }
        for label, (reason_code, setup) in expected.items():
            with self.subTest(label=label):
                if "tag" in setup:
                    atomic_write_text(self.tag, str(setup["tag"]))
                activation = self.activate(**({"attestation_key_path": setup["key"]} if "key" in setup else {}))
                verdict = evaluate_safety_preflight(
                    preflight_request(), org_rule_source=activation["org_rule_source"]
                )
                self.assertEqual(verdict["status"], "deny")
                self.assertEqual(verdict["reason_code"], reason_code)
                self.assertEqual(verdict["level"], "org")
                self.assertTrue(verdict["field"])
                self.assertTrue(verdict["correction"])

    def test_a_retained_rule_set_still_narrows_the_evaluator(self) -> None:
        self.write_source(revision="org-1", denied_remote_target_kinds=["public_internet"])
        self.write_valid_tag()
        self.activate()
        self.write_source(revision="org-2", denied_remote_target_kinds=[])
        activation = self.activate()
        self.assertEqual(activation["status"], "retained")
        destination = {"kind": "public_internet", "ref": "example-host"}
        verdict = evaluate_safety_preflight(
            preflight_request(
                remote_targets=[dict(destination)],
                approved_destinations=[dict(destination)],
                access_intents=["read", "share"],
            ),
            org_rule_source=activation["org_rule_source"],
        )
        # The rejected revision dropped the denial. The retained one still
        # applies it, which is what "retained and in force" has to mean.
        self.assertEqual(verdict["status"], "deny")
        self.assertEqual(verdict["reason_code"], "org_rule_denied")

    def test_the_new_refusal_codes_joined_the_existing_vocabulary(self) -> None:
        for reason_code in (
            "org_source_attestation_missing",
            "org_source_attestation_invalid",
            "org_source_attestation_key_unreadable",
        ):
            with self.subTest(reason_code=reason_code):
                self.assertIn(reason_code, ORG_SAFETY_RULE_SOURCE_REASON_CODES)

    def test_a_reason_code_outside_the_vocabulary_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            org_safety_rule_source_unavailable("0" * 64, "org_source_made_up", "org_rule_source.document")


class TrustStateIsDefensive(AttestationFixture):
    def test_a_record_that_cannot_be_fully_believed_is_no_record_at_all(self) -> None:
        base = write_safety_rule_trust_state(
            self.trust,
            source_identity_sha256="a" * 64,
            content_sha256="b" * 64,
            revision="org-1",
            rules={"denied_remote_target_kinds": ["public_internet"], "max_target_paths": 4},
            attested_at=ATTESTED_AT,
        )
        self.assertIsNotNone(read_safety_rule_trust_state(self.trust))
        broken = {
            "extra field": {**base, "extra": "smuggled"},
            "missing field": {key: value for key, value in base.items() if key != "revision"},
            "wrong schema": {**base, "schema_version": "other/v9"},
            "bad identity": {**base, "source_identity_sha256": "nope"},
            "bad content digest": {**base, "content_sha256": ""},
            "blank revision": {**base, "revision": "   "},
            "unbounded kinds": {**base, "denied_remote_target_kinds": "public_internet"},
            "negative cap": {**base, "max_target_paths": -1},
            "boolean cap": {**base, "max_target_paths": True},
            "non-string timestamp": {**base, "attested_at": 0},
        }
        for label, record in broken.items():
            with self.subTest(label=label):
                atomic_write_text(self.trust, json.dumps(record, sort_keys=True) + "\n")
                self.assertIsNone(read_safety_rule_trust_state(self.trust))

    def test_unreadable_or_absent_trust_state_is_no_retained_set(self) -> None:
        self.assertIsNone(read_safety_rule_trust_state(self.trust))
        atomic_write_text(self.trust, "{ not json")
        self.assertIsNone(read_safety_rule_trust_state(self.trust))
        atomic_write_text(self.trust, "[]")
        self.assertIsNone(read_safety_rule_trust_state(self.trust))

    def test_the_trust_state_lives_inside_the_omh_home(self) -> None:
        paths = resolve_paths(omh_home=self.root / "omh-home", hermes_home=self.root / "hermes-home")
        trust_path = paths.safety_rule_trust_state_path
        self.assertEqual(trust_path.name, "safety_rule_trust.json")
        self.assertEqual(trust_path.parent, paths.runtime_dir)
        self.assertEqual(paths.runtime_dir.parent, paths.omh_home)

    def test_the_trust_record_holds_metadata_only(self) -> None:
        record = write_safety_rule_trust_state(
            self.trust,
            source_identity_sha256="a" * 64,
            content_sha256="b" * 64,
            revision="org-1",
            rules={"denied_remote_target_kinds": ["public_internet"], "max_target_paths": None},
            attested_at=ATTESTED_AT,
        )
        for key, value in record.items():
            with self.subTest(key=key):
                if isinstance(value, list):
                    self.assertTrue(all(isinstance(item, str) for item in value))
                else:
                    self.assertIsInstance(value, (str, int, type(None)))


class TheTagPathAndVocabularyAreStable(AttestationFixture):
    def test_the_tag_sits_beside_the_source_it_covers(self) -> None:
        tag_path = default_attestation_tag_path(self.source)
        assert tag_path is not None
        # Asserted through path components, never through a separator literal.
        self.assertEqual(tag_path.name, f"org-rules.json{ATTESTATION_TAG_SUFFIX}")
        self.assertEqual(tag_path.parent, self.source.parent)
        self.assertIsNone(default_attestation_tag_path("   "))

    def test_an_explicit_tag_path_overrides_the_sidecar(self) -> None:
        self.write_source()
        elsewhere = self.root / "detached.tag"
        digest = safety_rule_attestation_tag(self.source.read_bytes(), key_path=self.key)
        atomic_write_text(elsewhere, digest)
        self.assertEqual(self.activate(attestation_tag_path=elsewhere)["signature_state"], "valid")
        self.assertEqual(self.activate(attestation_tag_path=self.root / "absent.tag")["signature_state"], "missing")

    def test_the_activation_payload_shape_is_closed(self) -> None:
        self.write_source()
        self.write_valid_tag()
        activation = self.activate()
        self.assertEqual(
            set(activation),
            {
                "schema_version",
                "status",
                "signature_state",
                "signature_reason_code",
                "signature_field",
                "algorithm",
                "effective_revision",
                "rejected_revision",
                "org_rule_source",
                "claim_boundary",
            },
        )
        self.assertEqual(activation["schema_version"], ORG_SAFETY_RULE_ACTIVATION_SCHEMA_VERSION)
        self.assertIn(activation["status"], ORG_SAFETY_RULE_ACTIVATION_STATUSES)
        self.assertIn(activation["signature_state"], SIGNATURE_STATES)
        self.assertIn(activation["signature_reason_code"], SAFETY_RULE_ATTESTATION_REASON_CODES)
        self.assertEqual(activation["algorithm"], SAFETY_RULE_ATTESTATION_ALGORITHM)

    def test_a_malformed_tag_file_is_invalid_rather_than_missing(self) -> None:
        self.write_source()
        for content in ("not-a-digest", "0" * 63, "0" * 65, "z" * 64):
            with self.subTest(content=content[:12]):
                atomic_write_text(self.tag, content)
                self.assertEqual(self.activate()["signature_state"], "invalid")

    def test_the_same_inputs_give_the_same_answer_every_time(self) -> None:
        self.write_source(revision="org-1")
        self.write_valid_tag()
        first = json.dumps(self.activate(), sort_keys=True)
        for _ in range(5):
            self.assertEqual(json.dumps(self.activate(), sort_keys=True), first)


class HonestAboutWhatASharedKeyProves(AttestationFixture):
    def test_a_key_holder_can_mint_a_tag_that_verifies(self) -> None:
        """The symmetric limitation, asserted rather than only documented.

        Anyone able to read the key can write a tag over any bytes they like and
        have it verify. That is not a defect in the implementation; it is what
        HMAC is, and it is the reason this lane claims integrity against a local
        key and never provenance.
        """
        atomic_write_text(self.source, org_source_text("attacker-authored"))
        forged = safety_rule_attestation_tag(self.source.read_bytes(), key_path=self.key)
        atomic_write_text(self.tag, forged)
        activation = self.activate()
        self.assertEqual(activation["signature_state"], "valid")
        self.assertEqual(activation["effective_revision"], "attacker-authored")

    def test_a_tag_made_with_a_different_key_does_not_verify(self) -> None:
        self.write_source()
        other_key = self.root / "other.key"
        atomic_write_text(other_key, "a-different-operator-secret\n")
        self.write_valid_tag(key=other_key)
        self.assertEqual(self.activate()["signature_state"], "invalid")

    def test_the_claim_boundary_names_the_limitation(self) -> None:
        text = SAFETY_RULE_ATTESTATION_CLAIM_BOUNDARY.lower()
        for phrase in ("shared key", "not who wrote them", "read the key"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        for phrase in ("not compliance", "execution", "review", "ci", "merge"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_the_module_says_plainly_what_the_tag_does_not_prove(self) -> None:
        docstring = ast.get_docstring(ast.parse(ATTESTATION_SOURCE.read_text(encoding="utf-8"))) or ""
        lowered = docstring.lower()
        for phrase in ("not public-key signing", "symmetric", "no third-party provenance"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, lowered)

    def test_the_algorithm_is_named_as_a_keyed_digest(self) -> None:
        self.assertEqual(SAFETY_RULE_ATTESTATION_ALGORITHM, "hmac-sha256")


class NoModelNoNetworkNoDependency(unittest.TestCase):
    """Asserted by construction, the way the sibling preflight tests do it."""

    ALLOWED_IMPORTS = frozenset({"__future__", "collections", "hashlib", "hmac", "pathlib", "re", "time"})
    FORBIDDEN_IMPORTS = frozenset(
        {"socket", "ssl", "urllib", "http", "requests", "httpx", "aiohttp", "subprocess", "asyncio"}
    )

    def test_the_attestation_runs_on_the_standard_library_alone(self) -> None:
        roots: set[str] = set()
        for node in ast.walk(ast.parse(ATTESTATION_SOURCE.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        self.assertLessEqual(roots, self.ALLOWED_IMPORTS)
        self.assertEqual(roots & self.FORBIDDEN_IMPORTS, set())

    def test_the_verifier_reads_bytes_and_nothing_else(self) -> None:
        result = verify_safety_rule_attestation(b"", key_path="", tag_path="")
        self.assertEqual(result["signature_state"], "not_required")
        self.assertEqual(result["reason_code"], "attestation_not_required")
        self.assertEqual(result["algorithm"], SAFETY_RULE_ATTESTATION_ALGORITHM)


if __name__ == "__main__":
    unittest.main()
