from __future__ import annotations

import json
from importlib.resources import files
import unittest

from omh.catalogs.awesome_hermes_agent import (
    CATALOG_SCHEMA_VERSION,
    AwesomeHermesCatalogError,
    UPSTREAM_README_SHA256,
    UPSTREAM_SOURCE_COMMIT,
    _parse_catalog,
    awesome_hermes_catalog,
    awesome_hermes_items,
)


_SOURCE_FIELDS = (
    "repo",
    "url",
    "default_branch",
    "commit",
    "readme_sha256",
    "retrieved_at",
    "claim_boundary",
)


class AwesomeHermesAgentCatalogCoherenceTests(unittest.TestCase):
    def _raw_catalog(self) -> dict[str, object]:
        resource = files("omh.catalogs").joinpath("awesome_hermes_agent_catalog.json")
        return json.loads(resource.read_text(encoding="utf-8"))

    def test_catalog_loads_clean_via_module_loader(self) -> None:
        catalog = awesome_hermes_catalog()

        self.assertEqual(catalog.to_dict()["schema_version"], CATALOG_SCHEMA_VERSION)
        self.assertTrue(catalog.items)

    def test_declared_item_count_matches_parsed_items(self) -> None:
        raw = self._raw_catalog()
        items = awesome_hermes_items()

        self.assertEqual(raw["item_count"], len(raw["items"]))
        self.assertEqual(raw["item_count"], len(items))

    def test_source_block_fields_are_present_and_non_empty(self) -> None:
        source = self._raw_catalog()["source"]

        self.assertIsInstance(source, dict)
        for field in _SOURCE_FIELDS:
            with self.subTest(field=field):
                value = source.get(field)
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip())

    def test_source_provenance_is_pinned_to_the_reviewed_upstream_snapshot(self) -> None:
        source = self._raw_catalog()["source"]

        self.assertIsInstance(source, dict)
        self.assertEqual(source["repo"], "0xNyk/awesome-hermes-agent")
        self.assertEqual(source["commit"], UPSTREAM_SOURCE_COMMIT)
        self.assertEqual(source["readme_sha256"], UPSTREAM_README_SHA256)

    def test_loader_rejects_catalogs_that_deviate_from_the_pinned_snapshot(self) -> None:
        raw = self._raw_catalog()
        source = raw["source"]
        self.assertIsInstance(source, dict)
        source["commit"] = "0" * 40

        with self.assertRaisesRegex(AwesomeHermesCatalogError, "pinned upstream source"):
            _parse_catalog(raw)

    def test_item_ids_are_unique(self) -> None:
        ids = [item.id for item in awesome_hermes_items()]

        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
