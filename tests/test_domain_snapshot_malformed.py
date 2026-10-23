from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _platform_support import requires_domain_intelligence_store
from omh.paths import resolve_paths
from omh.workflows.domain_intelligence_profile_snapshot import (
    read_validated_domain_profiles_at,
)
from omh.wrapper_contract import build_chat_interaction_payload

from domain_routing_context_support import (
    _approve_profile,
    _binding,
    _repository,
    _resolver,
    _store,
)


@requires_domain_intelligence_store
class DomainSnapshotMalformedTests(unittest.TestCase):
    def test_malformed_profile_revisions_fail_closed_at_each_public_boundary(
        self,
    ) -> None:
        revisions = (
            ("object", {}),
            ("array", []),
            ("null", None),
            ("bool-true", True),
            ("bool-false", False),
            ("string", "1"),
        )
        for label, revision in revisions:
            with self.subTest(revision=label), TemporaryDirectory() as temporary:
                root = _repository(Path(temporary) / "malformed-revision")
                _approve_profile(root, domain_id="sales", phrase="feels off")
                profile_path = next((_store(root) / "profiles").glob("*.json"))
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                profile["revision"] = revision
                profile_path.write_text(json.dumps(profile), encoding="utf-8")

                with _binding(root) as binding:
                    with self.assertRaisesRegex(
                        ValueError, "^artifact_identity_mismatch$"
                    ):
                        read_validated_domain_profiles_at(binding)
                    self.assertIsNone(
                        _resolver()(binding, "something feels off", locale="en")
                    )

                payload = build_chat_interaction_payload(
                    "something feels off",
                    source="discord",
                    source_metadata={},
                    paths=resolve_paths(root / ".omh", root / ".hermes"),
                    _host_project_binding_factory=lambda: _binding(root),
                )
                self.assertNotIn("domain_routing_context", payload)


if __name__ == "__main__":
    unittest.main()
