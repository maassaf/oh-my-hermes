"""Pinned OSS study metadata never proves actual reading or execution."""

from copy import deepcopy
import unittest

from _local_package import load_local_package

load_local_package()
from omh.workflows.research_references import reference_studies_errors


REVISION = "Ab" * 20
REPO = "https://example.invalid/Owner/Project.git"


def reference_record():
    citation = {"kind": "code", "path": "src/Core.py", "line_range": [10, 12], "symbol": "Core.run"}
    return {
        "schema_version": "reference_study/v1", "id": "OSS:Exact",
        "repo": REPO, "revision": REVISION,
        "license": {"identifier": "MIT", "file": "LICENSE",
                    "provenance": {"repo": REPO, "revision": REVISION}},
        "read_scope": "core_modules", "core_analysis_status": "complete",
        "read_files": [{"path": "src/Core.py", "role": "core",
                        "line_ranges": [[5, 20]], "symbols": ["Core.run"]}],
        "findings": {key: [{"summary": key + " analysis", "evidence": [deepcopy(citation)]}]
                     for key in ("mechanism", "tradeoffs", "limits")},
    }


class ReferenceStudiesTests(unittest.TestCase):
    def test_pinned_core_record_is_accepted_without_mutation(self):
        value = reference_record()
        original = deepcopy(value)
        self.assertEqual(reference_studies_errors([value]), [])
        self.assertEqual(value, original)

    def test_accepts_list_single_record_or_envelope_and_rejects_malformed_shapes(self):
        record = reference_record()
        self.assertEqual(reference_studies_errors(record), [])
        self.assertEqual(reference_studies_errors({"reference_studies": [record]}), [])
        self.assertEqual(reference_studies_errors([]), [])
        for value in (None, True, "records", {}, [None], [True],
                      {"reference_studies": None}, {"reference_studies": {}},
                      dict(record, schema_version="reference_study/v2"),
                      dict(record, id=True), dict(record, repo=" "), [record, record]):
            with self.subTest(value=value):
                self.assertTrue(reference_studies_errors(value))
        for key in record:
            value = reference_record()
            del value[key]
            with self.subTest(missing=key):
                self.assertTrue(reference_studies_errors([value]))

    def test_revision_requires_full_literal_git_object_id_not_branch_tag_or_repaired_token(self):
        for revision in ("main", "v1.2.3", "refs/tags/v1", "HEAD", "abc1234", "a" * 39,
                         "a" * 41, "g" * 40, " " + REVISION, REVISION + "\n", True, None, [],
                         "a" * 40 + "^{commit}"):
            with self.subTest(revision=revision):
                self.assertTrue(reference_studies_errors(dict(reference_record(), revision=revision)))
        value = reference_record()
        value["revision"] = "B" * 64
        value["license"]["provenance"]["revision"] = "B" * 64
        self.assertEqual(reference_studies_errors(value), [])

    def test_license_file_and_provenance_are_required_and_bound_to_literal_pin(self):
        for license_record in (None, [], {}, {"identifier": "MIT"},
                               {"identifier": True, "file": "LICENSE", "provenance": {"repo": REPO, "revision": REVISION}},
                               {"identifier": "MIT", "file": " ", "provenance": {"repo": REPO, "revision": REVISION}},
                               dict(reference_record()["license"], provenance=None),
                               dict(reference_record()["license"], provenance={"repo": REPO, "revision": "main"}),
                               dict(reference_record()["license"], provenance={"repo": REPO.lower(), "revision": REVISION}),
                               dict(reference_record()["license"], provenance={"repo": REPO, "revision": REVISION.lower()})):
            with self.subTest(license=license_record):
                self.assertTrue(reference_studies_errors(dict(reference_record(), license=license_record)))

    def test_readme_only_is_not_completed_core_analysis(self):
        value = reference_record()
        value["read_scope"] = "readme_only"
        self.assertTrue(reference_studies_errors(value))
        value["core_analysis_status"] = "not_performed"
        value["read_files"] = [{"path": "README.md", "role": "readme",
                                "line_ranges": [[1, 10]], "symbols": []}]
        value["findings"] = {key: [] for key in ("mechanism", "tradeoffs", "limits")}
        self.assertEqual(reference_studies_errors(value), [])
        for scope, status in (([], "complete"), ("unknown", "complete"),
                              ("core_modules", True), ("readme_only", "partial")):
            with self.subTest(scope=scope, status=status):
                self.assertTrue(reference_studies_errors(dict(reference_record(), read_scope=scope,
                                                             core_analysis_status=status)))
        value = reference_record()
        value["read_files"][0]["role"] = "readme"
        self.assertTrue(reference_studies_errors(value))

    def test_read_inventory_requires_exact_unique_paths_ranges_and_symbols(self):
        item = reference_record()["read_files"][0]
        bad_files = (None, [], {}, [None], [item, item],
                     [dict(item, path=True)], [dict(item, role="unknown")],
                     [dict(item, line_ranges=None)], [dict(item, line_ranges=[])],
                     [dict(item, symbols=None)], [dict(item, symbols=[True])],
                     [dict(item, symbols=["Core.run", "Core.run"])])
        for files in bad_files:
            with self.subTest(files=files):
                self.assertTrue(reference_studies_errors(dict(reference_record(), read_files=files)))
        for bounds in ([0, 10], [-1, 10], [12, 10], [True, 10], [1, False],
                       [1.5, 10], [1, float("inf")], [float("nan"), 10], [1], "1:10"):
            with self.subTest(bounds=bounds):
                self.assertTrue(reference_studies_errors(dict(reference_record(),
                                                              read_files=[dict(item, line_ranges=[bounds])])))
        value = reference_record()
        value["read_scope"] = "readme_only"
        value["core_analysis_status"] = "not_performed"
        self.assertTrue(reference_studies_errors(value))

    def test_complete_core_findings_require_mechanism_tradeoff_and_limit_links(self):
        for key in ("mechanism", "tradeoffs", "limits"):
            for finding in (None, [], "analysis", [None], [{}],
                            [{"summary": "claim", "evidence": []}],
                            [{"summary": True, "evidence": [reference_record()["findings"][key][0]["evidence"][0]]}]):
                value = reference_record()
                value["findings"][key] = finding
                with self.subTest(key=key, finding=finding):
                    self.assertTrue(reference_studies_errors(value))
            value = reference_record()
            del value["findings"][key]
            with self.subTest(missing=key):
                self.assertTrue(reference_studies_errors(value))
        value = reference_record()
        value["core_analysis_status"] = "partial"
        value["findings"] = {key: [] for key in ("mechanism", "tradeoffs", "limits")}
        self.assertEqual(reference_studies_errors(value), [])

    def test_code_evidence_points_to_declared_literal_file_ranges_and_symbols(self):
        valid = reference_record()["findings"]["mechanism"][0]["evidence"][0]
        for citation in (None, True, {}, dict(valid, kind="unknown"),
                         dict(valid, path="src/core.py"), dict(valid, symbol="core.run"),
                         dict(valid, line_range=[4, 12]), dict(valid, line_range=[10, 21]),
                         dict(valid, line_range=[True, 12]), dict(valid, line_range=[12, 10]),
                         {"kind": "code", "path": "src/Core.py"},
                         dict(valid, line_range=None), dict(valid, symbol=None)):
            value = reference_record()
            value["findings"]["mechanism"][0]["evidence"] = [citation]
            with self.subTest(citation=citation):
                self.assertTrue(reference_studies_errors(value))
        for field in ("line_range", "symbol"):
            value = reference_record()
            del value["findings"]["mechanism"][0]["evidence"][0][field]
            with self.subTest(omitted=field):
                self.assertEqual(reference_studies_errors(value), [])
        value = reference_record()
        value["read_files"][0]["role"] = "readme"
        value["read_scope"] = "readme_only"
        value["core_analysis_status"] = "not_performed"
        self.assertTrue(reference_studies_errors(value))
        for findings in value["findings"].values():
            findings[0]["evidence"][0]["kind"] = "documentation"
        self.assertEqual(reference_studies_errors(value), [])

    def test_completed_core_analysis_requires_core_code_links_in_each_finding_axis(self):
        for key in ("mechanism", "tradeoffs", "limits"):
            value = reference_record()
            value["findings"][key][0]["evidence"][0]["kind"] = "documentation"
            with self.subTest(key=key):
                self.assertTrue(reference_studies_errors(value))
                value["core_analysis_status"] = "partial"
                self.assertEqual(reference_studies_errors(value), [])


if __name__ == "__main__":
    unittest.main()
