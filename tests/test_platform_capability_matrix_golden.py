"""P2 shipped platform capability matrix contract."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from _local_package import load_local_package

load_local_package()

from _platform_golden import MATRIX_SCHEMA_VERSION, build_capability_matrix
from omh.system.platform_profiles import CAPABILITY_GROUPS, PLATFORM_IDS

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "examples" / "wrapper-golden" / "platform-capability-matrix.json"
CORE_LIMITS = {"telegram": (3700, 3900), "discord": (1700, 1900), "slack": (2700, 2900)}
RICH_PLATFORMS = {"mattermost", "matrix", "email", "microsoft_teams", "api_server", "buzz"}


class PlatformCapabilityMatrixGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.shipped = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))

    def test_shipped_matrix_exactly_matches_generated_data(self) -> None:
        self.assertEqual(self.shipped, build_capability_matrix())

    def test_schema_exact_ids_order_and_no_identity_refs(self) -> None:
        self.assertEqual(self.shipped["schema_version"], MATRIX_SCHEMA_VERSION)
        profiles = self.shipped["profiles"]
        ids = [profile["platform_id"] for profile in profiles]
        self.assertEqual(ids, list(PLATFORM_IDS))
        self.assertEqual(len(ids), 23)
        self.assertEqual(len(set(ids)), 23)
        self.assertNotIn("identity", json.dumps(self.shipped, sort_keys=True))

    def test_buzz_profile_reuses_hermes_transport_without_claiming_adapter_capabilities(self) -> None:
        profile = next(profile for profile in self.shipped["profiles"] if profile["platform_id"] == "buzz")
        self.assertEqual(profile["transport_source"], "hermes")
        self.assertEqual(profile["format_family"], "buzz/markdown")
        self.assertEqual(profile["limit_provenance"], "conservative_default")
        self.assertTrue(
            all(
                value is False
                for group in profile["capabilities"].values()
                for value in group.values()
            )
        )

    def test_limits_and_provenance_preserve_core_contract(self) -> None:
        for profile in self.shipped["profiles"]:
            platform_id = profile["platform_id"]
            limits = profile["limits"]
            pair = (limits["max_recommended_chars"], limits["hard_limit_chars"])
            if platform_id in CORE_LIMITS:
                self.assertEqual(pair, CORE_LIMITS[platform_id])
                self.assertEqual(profile["limit_provenance"], "verified")
            else:
                self.assertEqual(pair, (1600, 1800))
                self.assertEqual(profile["limit_provenance"], "conservative_default")

    def test_all_effective_capabilities_are_unknown_false(self) -> None:
        for profile in self.shipped["profiles"]:
            self.assertEqual(tuple(profile["capabilities"]), CAPABILITY_GROUPS)
            for group, capabilities in profile["capabilities"].items():
                self.assertTrue(all(value is False for value in capabilities.values()))
                provenance = profile["capability_provenance"][group]
                self.assertEqual(set(provenance), set(capabilities))
                self.assertTrue(all(value == "unverified_default_false" for value in provenance.values()))

    def test_render_profile_claim_and_field_provenance_are_bounded(self) -> None:
        for profile in self.shipped["profiles"]:
            expected = "rich_markdown" if profile["platform_id"] in RICH_PLATFORMS else "limited_markdown"
            self.assertEqual(profile["render_profile"], expected)
            self.assertEqual(profile["claim_boundary"], "adapter_owns_transport_core_owns_response")
            self.assertEqual(profile["field_provenance"], {
                "transport_source": "profile_registry",
                "render_profile": "profile_registry",
                "format_family": "approved_architecture_user_surface",
            })


if __name__ == "__main__":
    unittest.main()
