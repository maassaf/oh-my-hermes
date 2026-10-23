"""Search registration parity, conservative families and schema-size boundaries."""
import json
import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh import register  # noqa: E402
from omh.plugin_bundle.omh.metadata import PROVIDED_TOOLS, TOOL_FILE_STEMS  # noqa: E402
from omh.plugin_bundle.omh.tools import BUILTIN_TOOL_NAMES, builtin_tool_schemas  # noqa: E402
from omh.capabilities.families import capability_family_projection  # noqa: E402
from test_search_tool import SearchContext  # noqa: E402


class PluginToolSurfaceTests(unittest.TestCase):
    def test_registered_builtin_metadata_and_manifest_tool_inventory_match(self):
        from pathlib import Path
        from omh.plugin_bundle import omh
        from omh.workflows.plugin_manifest_yaml import entry_values, read_plugin_manifest_entries

        ctx = SearchContext()
        register(ctx)
        self.assertEqual(set(ctx.tools), set(BUILTIN_TOOL_NAMES))
        self.assertEqual(set(ctx.tools), set(PROVIDED_TOOLS))
        self.assertEqual(set(ctx.tools), set(TOOL_FILE_STEMS))
        manifest = Path(omh.__file__).with_name("plugin.yaml").read_text(encoding="utf-8")
        self.assertEqual(set(ctx.tools), set(entry_values(read_plugin_manifest_entries(manifest), "provides_tools")[0].sequence))
        self.assertEqual([schema["name"] for schema in builtin_tool_schemas()], sorted(ctx.tools))
        for schema in builtin_tool_schemas():
            self.assertEqual(schema, ctx.tools[schema["name"]][0])

    def test_search_schema_is_compact_not_a_copy_of_all_nested_definitions(self):
        schema = next(schema for schema in builtin_tool_schemas() if schema["name"] == "omh_search")
        encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        self.assertLess(len(encoded), 2500)
        self.assertNotIn("$defs", encoded)
        self.assertEqual(schema["parameters"]["required"], ["action", "payload"])
        self.assertFalse(schema["parameters"]["additionalProperties"])

    def test_learn_and_gather_family_names_optional_helper_without_retrieval_claim(self):
        family = next(row for row in capability_family_projection()["families"] if row["id"] == "learn_and_gather")
        self.assertIn("omh_search", family["route_summary"])
        self.assertIn("optional", family["route_summary"])
        self.assertIn("not retrieval", family["route_summary"])


if __name__ == "__main__":
    unittest.main()
