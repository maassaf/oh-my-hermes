from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _platform_support import requires_domain_intelligence_store
from domain_context_lifecycle_support import (
    EN_SALES_QUESTION as EN_SALES_QUESTION,
    PUBLIC_CONTEXT_KEYS as PUBLIC_CONTEXT_KEYS,
    _approve_candidate as _approve_candidate,
    _assert_applied_context as _assert_applied_context,
    _block_external_connections as _block_external_connections,
    _canonical,
    _capture_and_approve,
    _capture_candidate as _capture_candidate,
    _chat,
    _repository,
    _run_omh as _run_omh,
    _unresolved_message,
)
from test_domain_context_lifecycle_journey import DomainContextLifecycleJourneyMixin
from test_domain_context_store_isolation import DomainContextStoreIsolationMixin


@requires_domain_intelligence_store
class DomainContextLifecycleTests(
    DomainContextLifecycleJourneyMixin,
    DomainContextStoreIsolationMixin,
    unittest.TestCase,
):
    def test_unrelated_malformed_health_artifact_suppresses_context(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "unhealthy-project")
            phrase = "expert-marker-600-en"
            _capture_and_approve(root, domain="healthy", phrase=phrase)
            reviews = root / ".omh" / "memory" / "domain-intelligence" / "reviews"
            (reviews / "unrelated-malformed.json").write_text("{", encoding="utf-8")

            interaction = _chat(root, _unresolved_message(phrase))

            self.assertNotIn("domain_routing_context", interaction)

    def test_valid_and_empty_hint_profiles_suppress_context(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "empty-hint-project")
            phrase = "expert-marker-700-en"
            _capture_and_approve(root, domain="valid", phrase=phrase)
            _capture_and_approve(
                root,
                domain="empty",
                phrase=phrase,
                workflow_hints=(),
            )

            interaction = _chat(root, _unresolved_message(phrase))

            self.assertNotIn("domain_routing_context", interaction)

    def test_unknown_hint_suppresses_context(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "unknown-hint-project")
            phrase = "expert-marker-800-en"
            _capture_and_approve(
                root,
                domain="unknown",
                phrase=phrase,
                workflow_hints=("unknown-workflow",),
            )

            interaction = _chat(root, _unresolved_message(phrase))

            self.assertNotIn("domain_routing_context", interaction)

    def test_protected_routes_keep_body_route_and_candidate_exactly_equal(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "protected-project")
            phrase = "protected route marker"
            protected_messages = ("what's 2+2?", "README 파일 찾아줘")
            baselines = {message: _chat(root, message) for message in protected_messages}
            _capture_and_approve(root, domain="protected", phrase=phrase)

            for message in protected_messages:
                with self.subTest(message=message):
                    after = _chat(root, message)
                    before = baselines[message]
                    self.assertNotIn("domain_routing_context", after)
                    self.assertEqual(after["chat_response"]["body"], before["chat_response"]["body"])
                    self.assertEqual(_canonical(after["route"]), _canonical(before["route"]))
                    self.assertEqual(
                        _canonical(after["route"].get("candidate_handoff")),
                        _canonical(before["route"].get("candidate_handoff")),
                    )


if __name__ == "__main__":
    unittest.main()
