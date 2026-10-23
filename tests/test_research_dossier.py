"""The dossier validates supplied records, not facts or source authenticity."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from omh.workflows.research_dossier import research_dossier_errors, validate_research_dossier
from test_research_claims import record as claims_record
from test_research_progress import progress_record
from test_research_references import reference_record


def dossier():
    progress = progress_record()
    progress["axes"][0]["covered_evidence_ids"] = ["e1"]
    progress["waves"][0]["new_evidence_ids"] = ["e1"]
    return {
        "schema_version": "deep_research_dossier/v1",
        "question": "Which design should planning consider?",
        "scope": "Offline illustrative fixtures; no upstream observations.",
        "claim_records": claims_record(), "progress": progress,
        "reference_studies": [],
        "plan_feed": {"decision_drivers": ["Reliability"],
                      "viable_options": [{"summary": "Evaluate design A", "claim_ids": ["c1"],
                                          "evidence_ids": ["e1"], "reference_ids": []}],
                      "rejected_candidates": [], "risks": ["External validity unverified"],
                      "open_questions": ["Replication?"]},
        "citation_policy": {"ledger_scope": "run_private", "writer": "parent_only",
                            "reset_owner": "parent_only", "fan_in": "register_and_remap"},
    }


class ResearchDossierTests(unittest.TestCase):
    def test_requires_explicit_question_scope_and_closed_envelope(self):
        for value in (None, [], {}, {"schema_version": "deep_research_dossier/v1"}):
            with self.subTest(value=value):
                self.assertTrue(research_dossier_errors(value))


    def test_consumes_all_typed_record_validators_without_mutation(self):
        value = dossier()
        original = deepcopy(value)
        self.assertEqual(research_dossier_errors(value), [])
        self.assertEqual(value, original)
        for field in ("claim_records", "progress", "reference_studies"):
            candidate = dossier()
            candidate[field] = {} if field != "reference_studies" else [{}]
            with self.subTest(field=field):
                self.assertTrue(research_dossier_errors(candidate))
        candidate = dossier()
        candidate["reference_studies"] = [reference_record()]
        self.assertEqual(research_dossier_errors(candidate), [])


    def test_progress_evidence_ids_resolve_to_claim_or_reference_citations(self):
        for ident in ("invented", "E1", "s1", "c1", "OSS:Exact", "OSS:Exact:mechanism:0:9"):
            candidate = dossier()
            candidate["reference_studies"] = [reference_record()]
            candidate["progress"]["waves"][0]["new_evidence_ids"] = [ident]
            candidate["progress"]["axes"][0]["covered_evidence_ids"] = [ident]
            with self.subTest(ident=ident):
                self.assertTrue(any("unknown evidence" in error
                                    for error in research_dossier_errors(candidate)))
        candidate = dossier()
        study = reference_record()
        candidate["reference_studies"] = [study]
        ident = f'{study["id"]}:mechanism:0:0'
        candidate["progress"]["waves"][0]["new_evidence_ids"] = [ident]
        candidate["progress"]["axes"][0]["covered_evidence_ids"] = [ident]
        self.assertEqual(research_dossier_errors(candidate), [])
        candidate["claim_records"]["evidence"][0]["id"] = ident
        candidate["claim_records"]["claims"][0]["evidence_ids"][0] = ident
        self.assertTrue(any("ambiguous evidence" in error
                            for error in research_dossier_errors(candidate)))


    def test_plan_feed_is_decision_facing_and_resolves_typed_links(self):
        mutations = (
            lambda v: v.update(plan_feed={}),
            lambda v: v["plan_feed"].update(decision_drivers=[]),
            lambda v: v["plan_feed"].update(risks="unbounded notes"),
            lambda v: v["plan_feed"]["viable_options"][0].update(claim_ids=["missing"]),
            lambda v: v["plan_feed"]["viable_options"][0].update(evidence_ids=["invented"]),
            lambda v: v["plan_feed"]["viable_options"][0].update(reference_ids=["missing"]),
            lambda v: v["plan_feed"]["viable_options"][0].update(evidence_ids=[]),
            lambda v: v["plan_feed"].update(rejected_candidates=[{"summary": "B"}]),
            lambda v: v["claim_records"]["claims"][0].update(status="unresolved"),
        )
        for mutate in mutations:
            value = dossier()
            mutate(value)
            with self.subTest(mutate=mutate):
                self.assertTrue(any("plan_feed" in error for error in research_dossier_errors(value)))
        value = dossier()
        value["plan_feed"]["rejected_candidates"] = [
            dict(value["plan_feed"]["viable_options"][0], reason="Insufficient fit")]
        self.assertEqual(research_dossier_errors(value), [])
        value["claim_records"]["claims"][0]["status"] = "unresolved"
        value["plan_feed"]["viable_options"] = []
        self.assertEqual(research_dossier_errors(value), [])


    def test_plan_evidence_cannot_bypass_its_owning_claim_or_study(self):
        value = dossier()
        value["claim_records"]["claims"][0]["status"] = "unresolved"
        value["plan_feed"]["viable_options"][0]["claim_ids"] = []
        self.assertTrue(research_dossier_errors(value))
        value = dossier()
        study = reference_record()
        value["reference_studies"] = [study]
        option = value["plan_feed"]["viable_options"][0]
        option.update(evidence_ids=[f'{study["id"]}:mechanism:0:0'], claim_ids=[])
        self.assertTrue(research_dossier_errors(value))
        option["reference_ids"] = [study["id"]]
        self.assertEqual(research_dossier_errors(value), [])

    def test_rejects_shared_parallel_citation_writers(self):
        for change in ({"writer": "parallel_lanes"}, {"writer": "locked_parallel"},
                       {"ledger_scope": "profile_global"}, {"reset_owner": "each_lane"},
                       {"fan_in": "keep_lane_ids"}):
            value = dossier()
            value["citation_policy"].update(change)
            with self.subTest(change=change):
                self.assertTrue(any("citation_policy" in error
                                    for error in research_dossier_errors(value)))
        self.assertEqual(research_dossier_errors(dossier()), [])


    def test_partial_result_keeps_unresolved_and_refuted_separate(self):
        validate = validate_research_dossier
        value = dossier()
        value["progress"].update(status="partial", stop_reason="budget_exhausted")
        value["progress"]["budget"]["spent"] = 10
        value["claim_records"]["claims"][0]["status"] = "unresolved"
        value["claim_records"]["claims"].append({
            "id": "c2", "text": "Refuted alternative", "contested": True, "status": "refuted",
            "evidence_ids": ["e3"], "counter_search_ids": []})
        value["claim_records"]["evidence"].append(dict(
            value["claim_records"]["evidence"][0], id="e3", claim_id="c2", stance="refute"))
        value["plan_feed"]["viable_options"] = []
        original = deepcopy(value)
        result = validate(value)
        self.assertEqual(result.get("valid"), True)
        self.assertEqual(result["research_status"], "partial")
        self.assertEqual(result["claims_by_status"], {
            "corroborated": [], "unresolved": ["c1"], "refuted": ["c2"]})
        self.assertEqual(result["plan_feed"], value["plan_feed"])
        self.assertEqual(result["question"], value["question"])
        self.assertEqual(result["scope"], value["scope"])
        self.assertEqual(result["source_authenticity"], "not_verified")
        self.assertIn("structure only", result["claim_boundary"])
        self.assertEqual(validate(value), result)
        result["plan_feed"]["risks"].append("Mutate result only")
        self.assertEqual(value, original)
        self.assertFalse(validate({})["valid"])


    def test_module_cli_is_deterministic_local_and_refuses_invalid_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "dossier.json"
            source.write_text(json.dumps(dossier()), encoding="utf-8")
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"), HOME=str(root))
            command = [sys.executable, "-m", "omh.workflows.research_dossier"]
            def invoke(*args):
                return subprocess.run(command + list(args), cwd=root, env=env,
                                      text=True, capture_output=True, timeout=10)
            original = source.read_bytes()
            first = invoke(str(source))
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertTrue(first.stdout.strip(), "CLI must actually emit a JSON result")
            self.assertEqual(first.stderr, "")
            self.assertTrue(json.loads(first.stdout)["valid"])
            self.assertEqual(invoke(str(source)).stdout, first.stdout)
            source.write_text("{}", encoding="utf-8")
            invalid = invoke(str(source))
            self.assertEqual(invalid.returncode, 1)
            self.assertFalse(json.loads(invalid.stdout)["valid"])
            for data in (b"{", b"\xff", b'{"schema_version":NaN}', b'{"a":1,"a":2}'):
                source.write_bytes(data)
                bad = invoke(str(source))
                self.assertEqual(bad.returncode, 2, bad.stdout + bad.stderr)
                self.assertFalse(json.loads(bad.stdout)["valid"])
                self.assertNotIn("Traceback", bad.stdout + bad.stderr)
            for args in ([], [str(root / "missing")], [str(root)], [str(source), "extra"]):
                bad = invoke(*args)
                self.assertEqual(bad.returncode, 2, bad.stdout + bad.stderr)
                self.assertFalse(json.loads(bad.stdout)["valid"])
                self.assertNotIn("Traceback", bad.stdout + bad.stderr)
            source.write_bytes(original)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["dossier.json"])
            self.assertEqual(source.read_bytes(), original)


class ResearchDossierGuidanceTests(unittest.TestCase):
    def test_both_skill_projections_link_the_real_validation_contract(self):
        from omh.skills.packaging import builtin_skill_templates, builtin_skill_reference_templates
        from omh.skills.render import agent_skill_templates, agent_skill_reference_templates
        for bodies, refs, name in (
            (builtin_skill_templates(), builtin_skill_reference_templates(), "research"),
            (agent_skill_templates(), agent_skill_reference_templates(), "ulw-research"),
        ):
            body = next(t.content for t in bodies if t.name == name)
            self.assertIn("references/evidence-records.md", body)
            ref = next((t.content for t in refs if t.skill_name == name
                        and t.relative_path == "references/evidence-records.md"), "")
            for text in ("python -m omh.workflows.research_dossier <json-file>",
                         "research_claims/v1", "research_progress/v1", "reference_study/v1",
                         "parent-only", "register_and_remap", "structure only"):
                self.assertIn(text, ref)
            self.assertIn("parent-only", body)
            for token in ("python -m omh.workflows.research_dossier <json-file>",
                          "research_claims/v1", "research_progress/v1", "reference_study/v1"):
                self.assertIn(token, body)
            briefing = next(t.content for t in refs if t.skill_name == name
                            and t.relative_path == "references/briefing-format.md")
            self.assertIn("Explicit `en`", briefing)
            self.assertIn("language declaration is required", briefing)
            self.assertNotIn("anything left unnamed falls back to English", briefing)
            self.assertNotIn("When the declaration is absent, the document is English", briefing)


if __name__ == "__main__":
    unittest.main()
