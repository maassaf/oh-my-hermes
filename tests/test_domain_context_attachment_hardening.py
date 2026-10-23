from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, Mock, patch

from _platform_support import requires_domain_intelligence_store
from omh.routing.chat import route_chat_message
from omh.workflows.domain_project_context import HostProjectBinding
from omh.workflows.domain_routing_context import (
    MAX_DOMAIN_CONTEXT_INPUT_CODE_POINTS,
    DomainRoutingResolution,
    resolve_domain_routing_context,
    resolve_domain_routing_context_result,
)
from omh.wrapper.domain_context_attachment import resolve_attached_domain_context

from domain_context_diagnostics_support import _ensure_store
from test_domain_routing_context import _binding, _repository


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class DomainContextAttachmentHardeningTests(unittest.TestCase):
    def test_cap_proceeds_and_projects_only_applied_context(self) -> None:
        message = "x" * MAX_DOMAIN_CONTEXT_INPUT_CODE_POINTS
        route = route_chat_message(message)
        route_before = _canonical_bytes(route)
        context = {"domain_routing_context": {"schema_version": "test/v1"}}
        resolution = DomainRoutingResolution("applied", "applied", context)
        binding = MagicMock(spec=HostProjectBinding)
        resolver = Mock(return_value=resolution)

        attached = resolve_attached_domain_context(
            route,
            message,
            host_project_binding_factory=lambda: binding,
            resolver=resolver,
        )

        self.assertEqual(attached, context)
        self.assertIsNot(attached, resolution)
        resolver.assert_called_once_with(binding, message, locale="en")
        binding.close.assert_called_once_with()
        self.assertEqual(_canonical_bytes(route), route_before)
        serialized = _canonical_bytes(attached)
        self.assertNotIn(b"domain_resolution_status", serialized)
        self.assertNotIn(b"domain_resolution_reason", serialized)

    def test_cap_plus_one_skips_binding_and_resolver_without_changing_route(self) -> None:
        message = "x" * (MAX_DOMAIN_CONTEXT_INPUT_CODE_POINTS + 1)
        route = route_chat_message(message)
        route_before = _canonical_bytes(route)
        binding_factory = Mock()
        resolver = Mock()

        attached = resolve_attached_domain_context(
            route,
            message,
            host_project_binding_factory=binding_factory,
            resolver=resolver,
        )

        self.assertIsNone(attached)
        binding_factory.assert_not_called()
        resolver.assert_not_called()
        self.assertEqual(_canonical_bytes(route), route_before)

    def test_non_applied_resolution_consumes_private_status_and_reason(self) -> None:
        message = "something feels off"
        route = route_chat_message(message)
        binding = MagicMock(spec=HostProjectBinding)
        resolver = Mock(
            return_value=DomainRoutingResolution("excluded", "profile_store_unhealthy")
        )

        attached = resolve_attached_domain_context(
            route,
            message,
            host_project_binding_factory=lambda: binding,
            resolver=resolver,
        )

        self.assertIsNone(attached)
        resolver.assert_called_once_with(binding, message, locale="en")
        binding.close.assert_called_once_with()

    @requires_domain_intelligence_store
    def test_result_cap_plus_one_fails_before_snapshot_scan(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "bounded-resolution")
            _ensure_store(root)
            with _binding(root) as binding, patch(
                "omh.workflows.domain_intelligence_profile_snapshot."
                "read_validated_domain_profiles_at"
            ) as read_profiles:
                result = resolve_domain_routing_context_result(
                    binding,
                    "x" * (MAX_DOMAIN_CONTEXT_INPUT_CODE_POINTS + 1),
                    locale="en",
                )
                public = resolve_domain_routing_context(
                    binding,
                    "x" * (MAX_DOMAIN_CONTEXT_INPUT_CODE_POINTS + 1),
                    locale="en",
                )

        self.assertEqual(
            (result.status, result.reason),
            ("excluded", "input_too_large"),
        )
        self.assertIsNone(result.context)
        self.assertIsNone(public)
        self.assertNotIn("input_too_large", json.dumps(public))
        read_profiles.assert_not_called()


if __name__ == "__main__":
    unittest.main()
