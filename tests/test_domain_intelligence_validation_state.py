from __future__ import annotations

import unittest

from omh.workflows.domain_intelligence_validation_state import profile_key


class DomainIntelligenceValidationStateTests(unittest.TestCase):
    def test_profile_key_returns_only_prevalidated_identity_types(self) -> None:
        profile_id = "dprof_0123456789abcdef01234567"

        self.assertEqual(
            profile_key({"profile_id": profile_id, "revision": 3}),
            (profile_id, 3),
        )

        for revision in (True, False, "1", None, [], {}):
            with self.subTest(revision=revision):
                with self.assertRaisesRegex(ValueError, "^invalid_revision$"):
                    profile_key({"profile_id": profile_id, "revision": revision})

        for invalid_profile_id in (None, 7, "profile-1"):
            with self.subTest(profile_id=invalid_profile_id):
                with self.assertRaisesRegex(ValueError, "^unsafe_profile_id$"):
                    profile_key(
                        {"profile_id": invalid_profile_id, "revision": 1}
                    )


if __name__ == "__main__":
    unittest.main()
