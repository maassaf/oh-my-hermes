from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _local_package import load_local_package
from _platform_support import requires_domain_intelligence_store

load_local_package()

from omh.coding_lifecycle import start_codex_delegation_lifecycle
from omh.paths import resolve_paths
from omh.runtime_records import validate_coding_delegation_record
from omh.workflows.domain_project_context import bind_cli_project
from omh.wrapper_contract import build_chat_interaction_payload, build_status_card_from_status

from test_domain_routing_context import _approve_profile, _repository
from test_domain_context_persistence_privacy import DomainContextPersistencePrivacyMixin


AUTHORITATIVE_DIRECTORIES = ("candidates", "reviews", "profiles", "history")
PUBLIC_CONTEXT_KEYS = {
    "schema_version",
    "workflow_hint",
    "required_input",
    "question",
    "digest",
    "claim_boundary",
}
PROFILE_PRIVATE_KEYS = {
    "profile_id",
    "revision",
    "payload_digest",
    "mapping_digest",
    "mapping_ref",
    "mapping_index",
    "vocabulary_mappings",
    "phrase",
    "canonical",
    "project_scope_ref",
    "store_path",
    "root_path",
    "candidate_id",
    "review_id",
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _authoritative_snapshot(store: Path) -> dict[str, tuple[int, str]]:
    snapshot: dict[str, tuple[int, str]] = {}
    for dirname in AUTHORITATIVE_DIRECTORIES:
        directory = store / dirname
        directory.mkdir(parents=True, exist_ok=True)
        for path in sorted(directory.glob("*.json")):
            data = path.read_bytes()
            snapshot[str(path.relative_to(store))] = (
                len(data),
                hashlib.sha256(data).hexdigest(),
            )
    return snapshot


def _private_source_values(root: Path, profile: dict[str, object]) -> set[str]:
    store = root / ".omh" / "memory" / "domain-intelligence"
    mapping = profile["vocabulary_mappings"][0]
    values = {
        str(profile["profile_id"]),
        str(profile["candidate_id"]),
        str(profile["payload_digest"]),
        str(profile["scope"]["ref"]),
        str(profile["domain_id"]),
        str(mapping["phrase"]),
        str(mapping["canonical"]),
        str(root.resolve()),
        str(store.resolve()),
        _canonical_bytes(mapping).decode("utf-8"),
    }
    for path in sorted(store.rglob("*.json")):
        values.add(path.stem)
        value = json.loads(path.read_text(encoding="utf-8"))
        for key in ("candidate_id", "profile_id", "review_id", "payload_digest"):
            if value.get(key):
                values.add(str(value[key]))

    derivatives: set[str] = set()
    for value in values:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        derivatives.update({digest, digest[:16], digest[:24], digest[:32]})
    return {value for value in values | derivatives if len(value) >= 8}


def _private_key_paths(value: object, prefix: tuple[str, ...] = ()) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            path = (*prefix, key_text)
            if key_text in PROFILE_PRIVATE_KEYS:
                paths.append(".".join(path))
            paths.extend(_private_key_paths(nested, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            paths.extend(_private_key_paths(nested, (*prefix, f"[{index}]")))
    return paths


def _assert_no_private_material(
    testcase: unittest.TestCase,
    value: object,
    private_values: set[str],
    *,
    label: str,
) -> None:
    serialized = _canonical_bytes(value).decode("utf-8")
    leaked_values = sorted(item for item in private_values if item in serialized)
    testcase.assertEqual(leaked_values, [], f"{label} leaked private values")
    testcase.assertEqual(
        _private_key_paths(value),
        [],
        f"{label} retained profile-private fields",
    )


class DomainContextPrivacyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = _repository(Path(self.temporary.name).resolve() / "privacy-project")
        self.paths = resolve_paths(self.root / ".omh", self.root / ".hermes")
        self.phrase = "private routing marker"
        self.message = f"Something about this {self.phrase} still feels unresolved"
        self.profile = _approve_profile(
            self.root,
            domain_id="privacy-audit",
            phrase=self.phrase,
            canonical="private_routing_marker_canonical",
        )
        self.store = self.root / ".omh" / "memory" / "domain-intelligence"
        self.private_values = _private_source_values(self.root, self.profile)

    def _applied_interaction(self) -> dict[str, object]:
        return build_chat_interaction_payload(
            self.message,
            source="discord",
            source_metadata={"source_event_id": "event-001", "channel_ref": "channel-001"},
            paths=self.paths,
            _host_project_binding_factory=self._binding_factory,
        )

    def _binding_factory(self):
        binding = bind_cli_project(self.root)
        if binding is None:
            raise AssertionError("fixture failed to mint a project binding")
        return binding


@requires_domain_intelligence_store
class DomainContextPrivacyTests(DomainContextPersistencePrivacyMixin, DomainContextPrivacyTestCase):
    def test_public_context_has_only_contract_fields_and_public_digest_inputs(self) -> None:
        interaction = self._applied_interaction()

        self.assertIn("domain_routing_context", interaction)
        context = interaction["domain_routing_context"]
        self.assertEqual(set(context), PUBLIC_CONTEXT_KEYS)
        self.assertEqual(
            [path for path in _find_key_paths(interaction, "domain_routing_context")],
            ["domain_routing_context"],
        )
        preimage = {key: value for key, value in context.items() if key != "digest"}
        self.assertEqual(
            context["digest"],
            hashlib.sha256(_canonical_bytes(preimage)).hexdigest(),
        )
        _assert_no_private_material(
            self,
            preimage,
            self.private_values,
            label="public digest preimage",
        )

    def test_runtime_coding_and_status_records_keep_existing_schemas_private(self) -> None:
        interaction = self._applied_interaction()
        self.assertIn("domain_routing_context", interaction)
        lifecycle = start_codex_delegation_lifecycle(
            self.paths,
            "Implement the bounded validation and add focused tests",
            source="discord",
            source_metadata={"source_event_id": "event-003", "channel_ref": "channel-003"},
        )
        coding = lifecycle["coding_delegation"]
        status_card = build_status_card_from_status(lifecycle["status"])
        plain_root = _repository(Path(self.temporary.name).resolve() / "plain-project")
        plain_paths = resolve_paths(plain_root / ".omh", plain_root / ".hermes")
        baseline = start_codex_delegation_lifecycle(
            plain_paths,
            "Implement the bounded validation and add focused tests",
            source="discord",
            source_metadata={"source_event_id": "event-003", "channel_ref": "channel-003"},
        )
        baseline_status_card = build_status_card_from_status(baseline["status"])

        self.assertEqual(set(lifecycle["run"]), set(baseline["run"]))
        self.assertEqual(set(coding), set(baseline["coding_delegation"]))
        self.assertEqual(set(status_card), set(baseline_status_card))
        self.assertEqual(validate_coding_delegation_record(coding), [])
        _assert_no_private_material(
            self,
            {
                "run": lifecycle["run"],
                "coding": coding,
                "status_card": status_card,
            },
            self.private_values,
            label="runtime coding and status records",
        )

def _find_key_paths(
    value: object,
    target: str,
    prefix: tuple[str, ...] = (),
) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            path = (*prefix, key_text)
            if key_text == target:
                paths.append(".".join(path))
            paths.extend(_find_key_paths(nested, target, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            paths.extend(_find_key_paths(nested, target, (*prefix, f"[{index}]")))
    return paths
