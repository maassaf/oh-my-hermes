from __future__ import annotations

import unittest
from unittest.mock import patch

from _local_package import load_local_package


load_local_package()

from omh.catalogs import awesome_hermes_agent as awesome_catalog
from omh.skills import context_cost


class AwesomeHermesAgentEfficiencyTests(unittest.TestCase):
    def test_coverage_preparation_is_shared_by_repeated_and_composite_reads(self) -> None:
        awesome_catalog._awesome_hermes_coverage_cached.cache_clear()
        self.addCleanup(awesome_catalog._awesome_hermes_coverage_cached.cache_clear)
        with patch.object(
            awesome_catalog,
            "_coverage_for_item",
            wraps=awesome_catalog._coverage_for_item,
        ) as coverage_for_item:
            first = awesome_catalog.awesome_hermes_coverage()
            second = awesome_catalog.awesome_hermes_coverage()
            payload = awesome_catalog.awesome_hermes_coverage_payload()

        cache_info = awesome_catalog._awesome_hermes_coverage_cached.cache_info()
        self.assertEqual(first, second)
        self.assertEqual(payload["item_count"], len(first))
        self.assertEqual(coverage_for_item.call_count, len(first))
        self.assertEqual(cache_info.misses, 1)
        self.assertGreaterEqual(cache_info.hits, 3)

    def test_coverage_cache_refreshes_after_a_rule_set_version_change(self) -> None:
        awesome_catalog._awesome_hermes_coverage_cached.cache_clear()
        self.addCleanup(awesome_catalog._awesome_hermes_coverage_cached.cache_clear)
        original = awesome_catalog.awesome_hermes_coverage()

        with patch("omh.catalogs.awesome_hermes_agent_rules.RULE_SET_VERSION", "test-rule-set/v2"):
            refreshed = awesome_catalog.awesome_hermes_coverage()

        self.assertNotEqual(refreshed[0].rule_set_version, original[0].rule_set_version)
        self.assertEqual(refreshed[0].rule_set_version, "test-rule-set/v2")

    def test_context_cost_reuses_one_rendered_template_snapshot(self) -> None:
        with patch.object(
            context_cost,
            "builtin_skill_templates",
            wraps=context_cost.builtin_skill_templates,
        ) as templates, patch.object(
            context_cost,
            "builtin_skill_reference_templates",
            wraps=context_cost.builtin_skill_reference_templates,
        ) as reference_templates:
            payload = context_cost.skill_context_cost_payload()

        self.assertEqual({profile["profile"] for profile in payload["profiles"]}, {"core", "full"})
        self.assertEqual(templates.call_count, 1)
        self.assertEqual(reference_templates.call_count, 1)


if __name__ == "__main__":
    unittest.main()
