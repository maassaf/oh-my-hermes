from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _local_package import load_local_package
from _platform_support import requires_domain_intelligence_store

load_local_package()
from omh.paths import resolve_paths
from omh.workflows import domain_intelligence_store as store


@requires_domain_intelligence_store
class DomainIntelligenceStoreOverflowTests(unittest.TestCase):
    def test_incomplete_identity_scan_exposes_no_records(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            directory = store.profiles_dir(paths)
            (directory / "canonical.json").write_text(
                json.dumps({"profile_id": "canonical"}),
                encoding="utf-8",
            )
            (directory / "filler.json").write_text(
                json.dumps({"profile_id": "filler"}),
                encoding="utf-8",
            )
            (directory / "late-alias.json").write_text(
                json.dumps({"profile_id": "canonical"}),
                encoding="utf-8",
            )
            diagnostics: list[dict[str, str]] = []

            with patch.object(store, "MAX_DOMAIN_ARTIFACT_FILES", 1):
                records = store.read_profiles(paths, diagnostics)

            self.assertEqual(records, [])
            self.assertIn(
                "artifact_file_count_exceeded",
                {item["reason"] for item in diagnostics},
            )

    def test_candidate_capacity_uses_bounded_path_scan(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")
            directory = store.candidates_dir(paths)
            with patch.object(
                store,
                "bounded_json_paths",
                return_value=((directory / "one.json",), True),
            ) as bounded_scan:
                with self.assertRaisesRegex(
                    ValueError, "candidate_capacity_exceeded"
                ):
                    store.ensure_candidate_capacity(paths)

            bounded_scan.assert_called_once_with(
                directory,
                limit=store.MAX_DOMAIN_CANDIDATE_FILES - 1,
            )


if __name__ == "__main__":
    unittest.main()
