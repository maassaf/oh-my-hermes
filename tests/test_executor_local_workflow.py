from __future__ import annotations

from copy import deepcopy
import unittest

from omh.coding.executor_local_workflow import (
    EXECUTOR_LOCAL_WORKFLOW_CLAIM_BOUNDARY,
    EXECUTOR_LOCAL_WORKFLOW_FALLBACK,
    WorkflowRecord,
    build_executor_local_workflow,
    validate_executor_local_workflow,
)


class ExecutorLocalWorkflowTests(unittest.TestCase):
    def test_profile_mapping_is_single_and_executor_scoped(self) -> None:
        expected = {
            "codex": ("codex_skill", "command_template", "$ultrawork {message}"),
            "hermes": ("hermes_installed_skill", "display_only", "/ulw-work {message}"),
            "omx-runtime": ("omx_skill", "display_only", "$ultrawork {message}"),
            "omo-runtime": ("omo_skill_reference", "skill_reference", ""),
            "omc-runtime": ("omc_skill_descriptor", "descriptor_only", ""),
        }

        for profile, candidate_shape in expected.items():
            with self.subTest(profile=profile):
                binding = build_executor_local_workflow(
                    profile=profile,
                    routed_workflow="ultrawork",
                    parent_handoff_dispatchable=profile == "codex",
                )
                self.assertIsNotNone(binding)
                assert binding is not None
                candidate = binding["candidate"]
                invocation = candidate["invocation"]
                self.assertEqual(
                    (candidate["kind"], invocation["mode"], invocation["template"]),
                    candidate_shape,
                )
                self.assertEqual(candidate["skill_id"], "ultrawork")
                self.assertEqual(binding["routed_workflow"], "ultrawork")
                self.assertEqual(validate_executor_local_workflow(binding), [])

    def test_unmapped_profiles_omit_binding(self) -> None:
        for profile in ("claude-code", "generic", "choose", "pi", "", "future-runtime"):
            with self.subTest(profile=profile):
                self.assertIsNone(
                    build_executor_local_workflow(
                        profile=profile,
                        routed_workflow="ultrawork",
                        parent_handoff_dispatchable=False,
                    )
                )

    def test_noncatalog_workflow_omits_binding(self) -> None:
        self.assertIsNone(
            build_executor_local_workflow(
                profile="codex",
                routed_workflow="rm-rf",
                parent_handoff_dispatchable=True,
                availability_evidence=self._evidence("codex", "rm-rf", "host_observed"),
            )
        )

    def test_hermes_uses_installed_display_name_and_reference_profiles_are_empty(self) -> None:
        hermes = self._binding("hermes", "ultrawork")
        self.assertEqual(hermes["candidate"]["invocation"]["template"], "/ulw-work {message}")
        for profile in ("omo-runtime", "omc-runtime"):
            with self.subTest(profile=profile):
                binding = self._binding(profile, "ultrawork")
                invocation = binding["candidate"]["invocation"]
                self.assertEqual(invocation["template"], "")
                self.assertEqual(invocation["message_placeholder"], "")

    def test_observed_codex_is_the_only_invocable_candidate(self) -> None:
        evidence = self._evidence("codex", "ultrawork", "host_observed")
        binding = build_executor_local_workflow(
            profile="codex",
            routed_workflow="ultrawork",
            parent_handoff_dispatchable=True,
            availability_evidence=evidence,
        )
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding["status"], "observed_available")
        self.assertEqual(
            binding["dispatchability"],
            {
                "handoff_dispatchable": True,
                "candidate_invocation_dispatchable": True,
                "reason": "observed_available_ask_before_dispatch",
            },
        )
        self.assertEqual(validate_executor_local_workflow(binding), [])

        for profile in ("hermes", "omx-runtime", "omo-runtime", "omc-runtime"):
            with self.subTest(profile=profile):
                runtime = build_executor_local_workflow(
                    profile=profile,
                    routed_workflow="ultrawork",
                    parent_handoff_dispatchable=False,
                    availability_evidence=self._evidence(profile, "ultrawork", "host_observed"),
                )
                self.assertIsNotNone(runtime)
                assert runtime is not None
                self.assertFalse(runtime["dispatchability"]["candidate_invocation_dispatchable"])
                self.assertEqual(validate_executor_local_workflow(runtime), [])

    def test_unknown_and_unavailable_never_dispatch(self) -> None:
        cases = (
            (None, "unknown", "availability_not_observed"),
            (self._evidence("codex", "ultrawork", "unavailable"), "observed_unavailable", "candidate_observed_unavailable"),
            (self._evidence("hermes", "ultrawork", "host_observed"), "unknown", "availability_not_observed"),
            (self._evidence("codex", "ultraqa", "host_observed"), "unknown", "availability_not_observed"),
            ({"status": "prepared"}, "unknown", "availability_not_observed"),
        )
        for evidence, status, reason in cases:
            with self.subTest(status=status, reason=reason):
                binding = build_executor_local_workflow(
                    profile="codex",
                    routed_workflow="ultrawork",
                    parent_handoff_dispatchable=True,
                    availability_evidence=evidence,
                )
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(binding["status"], status)
                self.assertFalse(binding["dispatchability"]["candidate_invocation_dispatchable"])
                self.assertEqual(binding["dispatchability"]["reason"], reason)
                self.assertEqual(validate_executor_local_workflow(binding), [])

    def test_local_path_evidence_cannot_enter_projected_availability(self) -> None:
        evidence = self._evidence("codex", "ultrawork", "host_observed")
        evidence["evidence_ref"] = "/Users/alice/private-worktree/evidence.json"

        binding = build_executor_local_workflow(
            profile="codex",
            routed_workflow="ultrawork",
            parent_handoff_dispatchable=True,
            availability_evidence=evidence,
        )

        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding["status"], "unknown")
        self.assertEqual(binding["availability"]["evidence_ref"], "")
        self.assertFalse(binding["dispatchability"]["candidate_invocation_dispatchable"])

    def test_strict_validator_rejects_truthy_non_boole_and_forged_observation(self) -> None:
        valid = self._binding("codex", "ultrawork")
        mutations = {
            "extra root key": lambda item: item.update({"extra": "forged"}),
            "truthy handoff boolean": lambda item: item["dispatchability"].update({"handoff_dispatchable": 1}),
            "truthy candidate boolean": lambda item: item["dispatchability"].update(
                {"candidate_invocation_dispatchable": "true"}
            ),
            "forged root observation": lambda item: item.update({"status": "observed_available"}),
            "profile mismatch": lambda item: item["availability"].update({"profile": "hermes"}),
            "skill mismatch": lambda item: item["candidate"].update({"skill_id": "ultraqa"}),
            "duplicate placeholder": lambda item: item["candidate"]["invocation"].update(
                {"template": "$ultrawork {message} {message}"}
            ),
            "absent placeholder": lambda item: item["candidate"]["invocation"].update(
                {"template": "$ultrawork"}
            ),
            "wrong selection basis": lambda item: item["candidate"].update({"selection_basis": "router_guess"}),
            "wrong fallback": lambda item: item.update({"fallback": "Run it."}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                forged = deepcopy(valid)
                mutate(forged)
                self.assertTrue(validate_executor_local_workflow(forged))

    def test_validator_rejects_runtime_invocability_and_arbitrary_invocations(self) -> None:
        runtime = build_executor_local_workflow(
            profile="omx-runtime",
            routed_workflow="ultrawork",
            parent_handoff_dispatchable=False,
            availability_evidence=self._evidence("omx-runtime", "ultrawork", "host_observed"),
        )
        self.assertIsNotNone(runtime)
        assert runtime is not None
        forged = deepcopy(runtime)
        forged["dispatchability"].update(
            {
                "handoff_dispatchable": True,
                "candidate_invocation_dispatchable": True,
                "reason": "observed_available_ask_before_dispatch",
            }
        )
        self.assertTrue(validate_executor_local_workflow(forged))

        arbitrary = deepcopy(runtime)
        arbitrary["candidate"]["invocation"].update({"template": "rm -rf {message}"})
        self.assertTrue(validate_executor_local_workflow(arbitrary))

    def test_exact_contract_keys_and_boundaries(self) -> None:
        binding = self._binding("codex", "ultrawork")
        self.assertEqual(
            set(binding),
            {
                "schema_version",
                "profile",
                "status",
                "routed_workflow",
                "candidate",
                "availability",
                "dispatchability",
                "fallback",
                "claim_boundary",
            },
        )
        self.assertEqual(set(binding["candidate"]), {"kind", "skill_id", "invocation", "rationale", "selection_basis"})
        self.assertEqual(set(binding["candidate"]["invocation"]), {"mode", "syntax", "template", "message_placeholder"})
        self.assertEqual(
            set(binding["availability"]),
            {
                "status",
                "basis",
                "profile",
                "skill_id",
                "scope",
                "recorded_at",
                "observed_at",
                "evidence_ref",
            },
        )
        self.assertEqual(
            set(binding["dispatchability"]),
            {"handoff_dispatchable", "candidate_invocation_dispatchable", "reason"},
        )
        self.assertEqual(binding["fallback"], EXECUTOR_LOCAL_WORKFLOW_FALLBACK)
        self.assertEqual(binding["claim_boundary"], EXECUTOR_LOCAL_WORKFLOW_CLAIM_BOUNDARY)

    def _binding(self, profile: str, workflow: str) -> WorkflowRecord:
        binding = build_executor_local_workflow(
            profile=profile,
            routed_workflow=workflow,
            parent_handoff_dispatchable=profile == "codex",
        )
        self.assertIsNotNone(binding)
        assert binding is not None
        return binding

    @staticmethod
    def _evidence(profile: str, skill_id: str, status: str) -> WorkflowRecord:
        return {
            "status": status,
            "scope": {"profile": profile, "skill_id": skill_id, "environment": "local"},
            "recorded_at": "2026-08-02T12:00:01+09:00",
            "observed_at": "2026-08-02T12:00:00+09:00",
            "evidence_ref": "operator:local-workflow-check",
        }


if __name__ == "__main__":
    unittest.main()
