from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

from _local_package import load_local_package
load_local_package()

ROOT = Path(__file__).resolve().parents[1]


class RenderSkillPackTests(unittest.TestCase):
    def test_all_target_parent_and_leaf_types_are_checked_before_any_write(self):
        from unittest.mock import patch

        tool = self.tool()
        for conflict in ("root", "parent", "nested_parent", "leaf"):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as td:
                root = Path(td) / "skills"
                if conflict == "root":
                    root.write_bytes(b"BLOCKING ROOT")
                else:
                    (root / "a").mkdir(parents=True)
                    (root / "a/SKILL.md").write_bytes(b"ORIGINAL BODY")
                    block = root / ("z/nested" if conflict == "nested_parent" else "z/SKILL.md" if conflict == "leaf" else "z")
                    block.parent.mkdir(parents=True, exist_ok=True)
                    if conflict == "leaf":
                        block.mkdir()
                    else:
                        block.write_bytes(b"BLOCKING USER FILE")
                def snapshot():
                    return {p.relative_to(Path(td)).as_posix(): None if p.is_dir() else p.read_bytes() for p in Path(td).rglob("*")}
                before = snapshot()
                expected = {"a/SKILL.md": "replacement body", "z/nested/SKILL.md" if conflict == "nested_parent" else "z/SKILL.md": "new body"}
                for write in (False, True):
                    with self.subTest(write=write), patch.object(tool, "tap_files", return_value=expected):
                        with self.assertRaises((OSError, ValueError)) as refused:
                            tool.materialize(root, write=write)
                        self.assertEqual(snapshot(), before)
                        self.assertIsInstance(refused.exception, ValueError)

    def tool(self):
        path = ROOT / "tools/render_skill_pack.py"
        self.assertTrue(path.is_file(), "missing checked canonical pack producer")
        spec = importlib.util.spec_from_file_location("render_skill_pack_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_write_check_missing_stale_extra_and_foreign_preservation(self):
        tool = self.tool()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skills"
            result = tool.materialize(root, write=True)
            self.assertTrue(result["ok"])
            self.assertTrue(tool.materialize(root, write=False)["ok"])
            support = root / "omh-show-reel/toolkit/scripts/timeline.py"
            support.write_text("edited")
            self.assertIn("omh-show-reel/toolkit/scripts/timeline.py", tool.materialize(root, write=False)["stale"])
            foreign = root / "omh-show-reel/toolkit/foreign.py"
            foreign.write_text("foreign")
            result = tool.materialize(root, write=False)
            self.assertIn("omh-show-reel/toolkit/foreign.py", result["extra"])
            with self.assertRaises(ValueError):
                tool.materialize(root, write=True)
            self.assertEqual(foreign.read_text(), "foreign")
            support.unlink()
            self.assertIn("omh-show-reel/toolkit/scripts/timeline.py", tool.materialize(root, write=False)["missing"])


if __name__ == "__main__":
    unittest.main()
