from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _platform_support import requires_domain_intelligence_store
from omh.skills import catalog
from omh.system.local_store import FileLockTimeout
from omh.workflows.domain_routing_context import (
    resolve_domain_routing_context_result,
)

from domain_context_diagnostics_support import (
    DomainDiagnosticResolverMixin,
    _ensure_store,
    _profile,
)
from test_domain_routing_context import _binding, _repository


@requires_domain_intelligence_store
class DomainContextResolutionDiagnosticTests(
    DomainDiagnosticResolverMixin,
    unittest.TestCase,
):
    def test_absence_reasons_distinguish_missing_invalid_and_no_match(self) -> None:
        missing = resolve_domain_routing_context_result(
            None,
            "pipeline review",
            locale="en",
        )
        invalid = resolve_domain_routing_context_result(
            object(),
            "pipeline review",
            locale="en",
        )
        invalid_message = resolve_domain_routing_context_result(
            None,
            object(),
            locale="en",
        )
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "no-match")
            no_match = self._resolve(root, (_profile(root),), "unrelated request")

        self.assertEqual((missing.status, missing.reason), ("absent", "missing_binding"))
        self.assertEqual((invalid.status, invalid.reason), ("absent", "invalid_binding"))
        self.assertEqual(
            (invalid_message.status, invalid_message.reason),
            ("absent", "invalid_request"),
        )
        self.assertEqual((no_match.status, no_match.reason), ("absent", "no_match"))
        self.assertIsNone(no_match.context)

    def test_store_failures_have_sanitized_stable_reasons(self) -> None:
        cases = (
            (ValueError("malformed private artifact"), "profile_store_unhealthy"),
            (FileLockTimeout("private lock detail"), "profile_store_lock_failed"),
            (
                ValueError("domain_profile_snapshot_changed"),
                "profile_store_snapshot_changed",
            ),
            (
                FileNotFoundError(2, "private lock path", ".store.lock"),
                "profile_store_lock_failed",
            ),
        )
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "store-failures")
            _ensure_store(root)
            for error, reason in cases:
                with self.subTest(reason=reason), patch(
                    "omh.workflows.domain_intelligence_profile_snapshot."
                    "read_validated_domain_profiles_at",
                    side_effect=error,
                ), _binding(root) as binding:
                    result = resolve_domain_routing_context_result(
                        binding,
                        "pipeline review",
                        locale="en",
                    )
                self.assertEqual((result.status, result.reason), ("excluded", reason))
                self.assertIsNone(result.context)
                self.assertNotIn("private", result.reason)

    def test_match_and_hint_exclusions_report_exact_reasons(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "matching-reasons")
            cases = (
                (
                    tuple(_profile(root) for _ in range(65)),
                    "match_overflow",
                ),
                (
                    (_profile(root), _profile(root, hints=[])),
                    "empty_workflow_hints",
                ),
                (
                    (_profile(root, hints=["not-a-workflow"]),),
                    "unknown_workflow_hint",
                ),
                (
                    (
                        _profile(root),
                        _profile(root, hints=["finance-analysis"]),
                    ),
                    "conflicting_workflow_hints",
                ),
                (
                    (
                        _profile(root),
                        _profile(root, canonical="other_canonical"),
                    ),
                    "canonical_conflict",
                ),
            )
            for profiles, reason in cases:
                with self.subTest(reason=reason):
                    result = self._resolve(root, profiles)
                    self.assertEqual(
                        (result.status, result.reason),
                        ("excluded", reason),
                    )
                    self.assertIsNone(result.context)

    def test_missing_question_spec_and_applied_are_distinct(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "question-spec")
            profiles = (_profile(root),)
            applied = self._resolve(root, profiles)
            definitions = [
                replace(definition, expert_questions=())
                if definition.name == "sales-development"
                else definition
                for definition in catalog.routable_definitions()
            ]
            with patch.object(
                catalog,
                "routable_definitions",
                return_value=definitions,
            ):
                missing = self._resolve(root, profiles)

        self.assertEqual((applied.status, applied.reason), ("applied", "applied"))
        self.assertIsNotNone(applied.context)
        self.assertEqual(
            (missing.status, missing.reason),
            ("excluded", "missing_question_spec"),
        )
        self.assertIsNone(missing.context)

    def test_compatibility_only_hint_has_its_own_reason(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "compatibility-hint")
            routable = catalog.routable_definitions()
            compatibility_definition = replace(
                routable[0],
                name="legacy-domain-alias",
            )
            compatibility_exposure = replace(
                catalog.surface_exposure_for_skill(routable[0].name),
                compatibility_alias=True,
            )
            with (
                patch.object(
                    catalog,
                    "builtin_definitions",
                    return_value=[*routable, compatibility_definition],
                ),
                patch.object(
                    catalog,
                    "surface_exposure_for_skill",
                    return_value=compatibility_exposure,
                ),
            ):
                result = self._resolve(
                    root,
                    (_profile(root, hints=["legacy-domain-alias"]),),
                )

        self.assertEqual(
            (result.status, result.reason),
            ("excluded", "compatibility_only_workflow_hint"),
        )

    def test_retired_lifecycle_stage_keeps_the_compatibility_hint_reason(self) -> None:
        """`lifecycle_stage` is independent state, not a derivation source.

        A retired engine keeps `compatibility_alias=True` (plan #954 §9.1), so a
        stale workflow hint naming it must still resolve as a compatibility
        concern. Deriving `compatibility_alias` from the stage would flip this
        to `non_routable_workflow_hint`, which is why the field stayed plain.
        """
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "retired-hint")
            routable = catalog.routable_definitions()
            retired_definition = replace(
                routable[0],
                name="retired-domain-alias",
            )
            retired_exposure = replace(
                catalog.surface_exposure_for_skill(routable[0].name),
                compatibility_alias=True,
                lifecycle_stage="retired",
            )
            with (
                patch.object(
                    catalog,
                    "builtin_definitions",
                    return_value=[*routable, retired_definition],
                ),
                patch.object(
                    catalog,
                    "surface_exposure_for_skill",
                    return_value=retired_exposure,
                ),
            ):
                result = self._resolve(
                    root,
                    (_profile(root, hints=["retired-domain-alias"]),),
                )

        self.assertEqual(retired_exposure.lifecycle_stage, "retired")
        self.assertTrue(retired_exposure.compatibility_alias)
        self.assertEqual(
            (result.status, result.reason),
            ("excluded", "compatibility_only_workflow_hint"),
        )

    def test_unexpected_programmer_errors_are_not_silently_masked(self) -> None:
        with TemporaryDirectory() as temporary:
            root = _repository(Path(temporary) / "programmer-error")
            _ensure_store(root)
            with patch(
                "omh.workflows.domain_intelligence_profile_snapshot."
                "read_validated_domain_profiles_at",
                side_effect=TypeError("unexpected programmer error"),
            ), _binding(root) as binding:
                with self.assertRaisesRegex(TypeError, "unexpected programmer error"):
                    resolve_domain_routing_context_result(
                        binding,
                        "pipeline review",
                        locale="en",
                    )


if __name__ == "__main__":
    unittest.main()
