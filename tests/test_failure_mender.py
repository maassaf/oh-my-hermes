from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.core.failure_mender import (  # noqa: E402
    build_escalation_request,
    decide_failure,
    validate_failure_decision,
)


class FailureMenderTests(unittest.TestCase):
    def test_transient_failures_retry_until_the_cap_then_escalate(self) -> None:
        retry = decide_failure(TimeoutError("provider timed out"), attempt=0, max_retries=2, source="mcp")
        capped = decide_failure(TimeoutError("provider timed out"), attempt=2, max_retries=2, source="mcp")
        self.assertEqual(retry["action"], "retry")
        self.assertTrue(retry["retry_allowed"])
        self.assertEqual(capped["action"], "escalate")
        self.assertFalse(capped["retry_allowed"])
        self.assertEqual(validate_failure_decision(retry), [])

    def test_persistent_and_external_failures_do_not_retry_blindly(self) -> None:
        self.assertEqual(decide_failure("stale revision conflict")["action"], "replan")
        self.assertEqual(decide_failure("consent required by provider")["action"], "escalate")
        self.assertEqual(decide_failure(PermissionError("permission denied"))["action"], "stop")

    def test_decision_is_metadata_only_and_validates_tampering(self) -> None:
        decision = decide_failure("private prompt content", source="  tool\n  call ")
        self.assertNotIn("private prompt content", str(decision))
        self.assertEqual(decision["source"], "tool call")
        self.assertEqual(validate_failure_decision(decision), [])
        self.assertTrue(validate_failure_decision({**decision, "action": "retry", "retry_allowed": False}))

    def test_escalation_request_is_safe_and_reviewable(self) -> None:
        decision = decide_failure("provider timed out", attempt=2, max_retries=2, source="mcp:shell")
        request = build_escalation_request(decision)
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request["seed_id"], "failure-" + decision["failure_sha256"][:12])
        self.assertNotIn("provider timed out", str(request))
        self.assertIn("sd create", request["create_command"])
        self.assertIsNone(build_escalation_request(decide_failure(TimeoutError("temporary"), attempt=0)))
