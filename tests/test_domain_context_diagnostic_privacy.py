from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _platform_support import requires_domain_intelligence_store
from omh.coding_lifecycle import start_codex_delegation_lifecycle
from omh.paths import resolve_paths
from omh.workflows.domain_routing_context import (
    resolve_domain_routing_context,
    resolve_domain_routing_context_result,
)
from omh.wrapper_contract import (
    build_chat_interaction_payload,
    build_status_card_from_status,
)
from omh.wrapper_sessions import create_or_resume_wrapper_session

from domain_context_diagnostics_support import (
    _ensure_store,
    _find_resolution_results,
    _profile,
)
from test_domain_routing_context import _approve_profile, _binding, _repository


@requires_domain_intelligence_store
class DomainContextDiagnosticPrivacyTests(unittest.TestCase):
    def test_public_compatibility_projection_contains_no_diagnostics(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "public-projection")
            _ensure_store(root)
            profiles = (_profile(root),)
            with patch(
                "omh.workflows.domain_intelligence_profile_snapshot."
                "read_validated_domain_profiles_at",
                return_value=profiles,
            ), _binding(root) as binding:
                internal = resolve_domain_routing_context_result(
                    binding,
                    "pipeline review",
                    locale="en",
                )
                public = resolve_domain_routing_context(
                    binding,
                    "pipeline review",
                    locale="en",
                )

        self.assertEqual(public, internal.context)
        serialized = json.dumps(public, sort_keys=True)
        self.assertNotIn("domain_resolution_status", serialized)
        self.assertNotIn("domain_resolution_reason", serialized)
        self.assertNotIn(internal.reason, serialized)

    def test_diagnostics_never_enter_wrapper_session_runtime_or_status_payloads(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "payload-privacy")
            _approve_profile(root, domain_id="sales")
            paths = resolve_paths(root / ".omh", root / ".hermes")
            message = "Please do a pipeline review"
            metadata = {
                "source_event_id": "diagnostic-event",
                "channel_ref": "diagnostic-channel",
            }
            interaction = build_chat_interaction_payload(
                message,
                source="discord",
                source_metadata=metadata,
                paths=paths,
                _host_project_binding_factory=lambda: _binding(root),
            )
            session = create_or_resume_wrapper_session(
                paths,
                message,
                source="discord",
                source_metadata=metadata,
                _host_project_binding_factory=lambda: _binding(root),
            )
            lifecycle = start_codex_delegation_lifecycle(
                paths,
                "Implement focused diagnostics",
                source="discord",
                source_metadata={
                    "source_event_id": "coding-event",
                    "channel_ref": "coding-channel",
                },
            )
            status_card = build_status_card_from_status(lifecycle["status"])

        payloads = {
            "wrapper": interaction,
            "session": session,
            "runtime": lifecycle["run"],
            "coding": lifecycle["coding_delegation"],
            "status": status_card,
        }
        self.assertEqual(_find_resolution_results(payloads), [])
        serialized = json.dumps(payloads, sort_keys=True)
        for reason in (
            "profile_store_unhealthy",
            "profile_store_lock_failed",
            "profile_store_snapshot_changed",
            "match_overflow",
            "empty_workflow_hints",
            "unknown_workflow_hint",
            "compatibility_only_workflow_hint",
            "conflicting_workflow_hints",
            "canonical_conflict",
            "missing_question_spec",
        ):
            with self.subTest(reason=reason):
                self.assertNotIn(reason, serialized)


if __name__ == "__main__":
    unittest.main()
