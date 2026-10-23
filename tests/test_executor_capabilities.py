"""Compatibility contract for the deprecated `executor_capability/v1` API."""

from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.coding.executor_capabilities import (  # noqa: E402
    CAPABILITY_STATES,
    EDIT_FORMAT_KEYS,
    EXECUTOR_CAPABILITY_SCHEMA_VERSION,
    KNOWN_CAPABILITY_PROFILES,
    capability_for_profile,
    capability_for_profile_or_none,
)


class ExecutorCapabilityCompatibilityTests(unittest.TestCase):
    def test_known_profiles_keep_the_legacy_shape(self) -> None:
        for profile in KNOWN_CAPABILITY_PROFILES:
            with self.subTest(profile=profile):
                row = capability_for_profile(profile)
                self.assertEqual(row["schema_version"], EXECUTOR_CAPABILITY_SCHEMA_VERSION)
                self.assertEqual(row["profile"], profile)
                self.assertEqual(set(row["edit_format_support"]), set(EDIT_FORMAT_KEYS))
                self.assertIn(row["persistent_eval"], CAPABILITY_STATES)
                self.assertIn(row["tool_reentry"], CAPABILITY_STATES)
                self.assertIn(row["code_mode_batching"], CAPABILITY_STATES)
                self.assertEqual(
                    row["provenance"],
                    {"source": "", "observed_at": None, "executor_version": None},
                )

    def test_unknown_profile_raises_and_optional_reader_returns_none(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown dispatch profile"):
            capability_for_profile("hermes")
        self.assertIsNone(capability_for_profile_or_none("hermes"))

    def test_callers_cannot_mutate_later_compatibility_projections(self) -> None:
        first = capability_for_profile("codex")
        first["edit_format_support"]["patch"] = "supported"

        second = capability_for_profile("codex")

        self.assertEqual(second["edit_format_support"]["patch"], "unknown")


if __name__ == "__main__":
    unittest.main()
