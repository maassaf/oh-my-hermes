from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from _local_package import load_local_package

load_local_package()

from omh.paths import resolve_paths
from omh.wrapper_sessions import create_or_resume_wrapper_session
from omh.workflows.domain_project_context import bind_plugin_project

from domain_context_lifecycle_support import (
    EN_SALES_QUESTION,
    _assert_applied_context,
    _block_external_connections,
    _capture_and_approve,
    _chat,
    _repository,
    _run_omh,
    _unresolved_message,
)
from test_plugin_distribution import FakeHermesContext, load_installed_plugin


class DomainContextStoreIsolationMixin:
    def test_same_named_repositories_use_only_the_bound_project_store(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            first = _repository(base / "one" / "same-project")
            second = _repository(base / "two" / "same-project")
            phrase = "expert-marker-200-en"
            _capture_and_approve(first, domain="isolation", phrase=phrase)

            applied = _chat(first, _unresolved_message(phrase))
            isolated = _chat(second, _unresolved_message(phrase))

            self.assertIn("domain_routing_context", applied)
            self.assertNotIn("domain_routing_context", isolated)

    def test_user_and_organization_profiles_are_not_consumed(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "scope-project")
            phrase = "expert-marker-300-en"
            _capture_and_approve(
                root,
                scope_kind="user",
                scope_ref="user-lifecycle",
                domain="user-domain",
                phrase=phrase,
            )
            _capture_and_approve(
                root,
                scope_kind="organization",
                scope_ref="org-lifecycle",
                domain="organization-domain",
                phrase=phrase,
            )

            interaction = _chat(root, _unresolved_message(phrase))

            self.assertNotIn("domain_routing_context", interaction)

    def test_packaged_plugin_rejects_absent_and_hostile_redirects(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            trusted = _repository(base / "trusted-project")
            hostile = _repository(base / "hostile-project")
            phrase = "expert-marker-400-en"
            _capture_and_approve(trusted, domain="trusted", phrase=phrase)
            _capture_and_approve(hostile, domain="hostile", phrase=phrase)
            _run_omh(
                trusted, "--scope", "project", "setup", "--with-plugin", "--json"
            )
            plugin = load_installed_plugin(trusted / ".hermes" / "plugins" / "omh")
            context = FakeHermesContext()
            plugin.register(context)
            handler = context.tools["omh_interact"]["args"][2]
            args = {
                "message": _unresolved_message(phrase),
                "source": "discord",
                "record_session": False,
                "project_root": str(hostile),
                "omh_home": str(hostile / ".omh"),
                "source_metadata": {"project_ref": str(hostile)},
            }

            with mock.patch.dict(
                os.environ,
                {
                    "PROJECT_ROOT": str(hostile),
                    "OMH_HOME": str(hostile / ".omh"),
                },
            ), _block_external_connections():
                absent = json.loads(handler(args))
                applied = json.loads(handler(args, project_root=str(trusted)))

            self.assertNotIn("domain_routing_context", absent)
            _assert_applied_context(
                self,
                applied,
                workflow="sales-development",
                locale="en",
                question=EN_SALES_QUESTION,
            )

    def test_cross_repository_session_reuse_uses_each_turns_fresh_binding(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            first = _repository(base / "first" / "same-project")
            second = _repository(base / "second" / "same-project")
            phrase = "expert-marker-500-en"
            _capture_and_approve(first, domain="first", phrase=phrase)
            _capture_and_approve(
                second,
                domain="second",
                phrase=phrase,
                workflow_hints=("finance-analysis",),
            )
            shared_paths = resolve_paths(base / "shared-omh", base / "shared-hermes")
            metadata = {"source_event_id": "same-event", "channel_ref": "same-channel"}

            with _block_external_connections():
                first_turn = create_or_resume_wrapper_session(
                    shared_paths,
                    _unresolved_message(phrase),
                    source="discord",
                    source_metadata=metadata,
                    _host_project_binding_factory=lambda: bind_plugin_project(
                        {"project_root": str(first)}
                    ),
                )
            with _block_external_connections():
                second_turn = create_or_resume_wrapper_session(
                    shared_paths,
                    _unresolved_message(phrase),
                    source="discord",
                    source_metadata=metadata,
                    _host_project_binding_factory=lambda: bind_plugin_project(
                        {"project_root": str(second)}
                    ),
                )

            self.assertTrue(second_turn["resumed"])
            self.assertEqual(
                first_turn["session"]["session_id"], second_turn["session"]["session_id"]
            )
            self.assertEqual(
                first_turn["interaction"]["domain_routing_context"]["workflow_hint"],
                "sales-development",
            )
            self.assertEqual(
                second_turn["interaction"]["domain_routing_context"]["workflow_hint"],
                "finance-analysis",
            )
