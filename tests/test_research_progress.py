"""Pure structural checks; supplied research is not independently observed."""

from copy import deepcopy
import unittest

from _local_package import load_local_package

load_local_package()
from omh.workflows.research_progress import research_progress_errors


def progress_record():
    return {
        "schema_version": "research_progress/v1",
        "budget": {"unit": "source_reads", "limit": 10, "spent": 4},
        "status": "complete",
        "stop_reason": "saturated",
        "axes": [{"id": "mechanism", "required_coverage": 1,
                  "covered_evidence_ids": ["E:Exact"], "unresolved_leads": []}],
        "saturation_rule": {"minimum_quiet_waves": 2},
        "waves": [{"id": "w1", "new_evidence_ids": ["E:Exact"]},
                  {"id": "w2", "new_evidence_ids": []},
                  {"id": "w3", "new_evidence_ids": []}],
    }


class ResearchProgressTests(unittest.TestCase):
    def test_complete_record_is_accepted_without_mutation(self):
        record = progress_record()
        original = deepcopy(record)
        self.assertEqual(research_progress_errors(record), [])
        self.assertEqual(record, original)

    def test_requires_mapping_schema_and_explicit_top_level_fields(self):
        for value in (None, [], "record", True, {},
                      dict(progress_record(), schema_version="research_progress/v9"),
                      dict(progress_record(), status=True),
                      dict(progress_record(), stop_reason=["saturated"])):
            with self.subTest(value=value):
                self.assertTrue(research_progress_errors(value))
        for key in progress_record():
            value = progress_record()
            del value[key]
            with self.subTest(missing=key):
                self.assertTrue(research_progress_errors(value))

    def test_budget_requires_explicit_finite_nonnegative_quantities_and_unit(self):
        bad = (None, True, "4", -1, float("nan"), float("inf"), -float("inf"), [], {})
        for field in ("limit", "spent"):
            for number in bad:
                value = progress_record()
                value["budget"][field] = number
                with self.subTest(field=field, number=number):
                    self.assertTrue(research_progress_errors(value))
        for budget in (None, [], {}, {"limit": 10, "spent": 4},
                       {"unit": " ", "limit": 10, "spent": 4},
                       {"unit": True, "limit": 10, "spent": 4}):
            with self.subTest(budget=budget):
                self.assertTrue(research_progress_errors(dict(progress_record(), budget=budget)))
        value = progress_record()
        value["budget"] = {"unit": "seconds", "limit": 0.5, "spent": 0.25}
        self.assertEqual(research_progress_errors(value), [])

    def test_stop_reason_never_turns_a_budget_or_interruption_into_completion(self):
        for reason in ("budget_exhausted", "cancelled", "blocked", "in_progress"):
            value = progress_record()
            value["stop_reason"] = reason
            if reason == "budget_exhausted":
                value["budget"]["spent"] = 10
            with self.subTest(reason=reason):
                self.assertTrue(research_progress_errors(value))
                value["status"] = "partial"
                self.assertEqual(research_progress_errors(value), [])
        for reason in ("completed", "saturated"):
            value = dict(progress_record(), stop_reason=reason, status="partial")
            with self.subTest(reason=reason):
                self.assertTrue(research_progress_errors(value))
        for reason, spent in (("budget_exhausted", 4), ("saturated", 10),
                              ("completed", 11), ("in_progress", 10)):
            value = progress_record()
            value["stop_reason"] = reason
            value["status"] = "complete" if reason in ("saturated", "completed") else "partial"
            value["budget"]["spent"] = spent
            with self.subTest(reason=reason, spent=spent):
                self.assertTrue(research_progress_errors(value))

    def test_axis_coverage_and_required_leads_gate_completion_only(self):
        for change in ({"required_coverage": 2},
                       {"unresolved_leads": [{"id": "L:1", "required": True}]}):
            value = progress_record()
            value["axes"][0].update(change)
            with self.subTest(change=change):
                self.assertTrue(research_progress_errors(value))
                value.update(status="partial", stop_reason="blocked")
                self.assertEqual(research_progress_errors(value), [])
        value = progress_record()
        value["axes"][0]["unresolved_leads"] = [{"id": "optional", "required": False}]
        self.assertEqual(research_progress_errors(value), [])
        for axes in ([], {}, [None], [{"id": "a"}],
                     [dict(progress_record()["axes"][0], required_coverage=True)],
                     [dict(progress_record()["axes"][0], required_coverage=-1)],
                     [dict(progress_record()["axes"][0], required_coverage=1.5)],
                     [dict(progress_record()["axes"][0], covered_evidence_ids="E:Exact")],
                     [dict(progress_record()["axes"][0], unresolved_leads=[{"id": "l", "required": 1}])],
                     [dict(progress_record()["axes"][0], unresolved_leads=[{"id": "l"}])],
                     progress_record()["axes"] * 2):
            with self.subTest(axes=axes):
                self.assertTrue(research_progress_errors(dict(progress_record(), axes=axes)))

    def test_saturation_requires_declared_trailing_quiet_waves_not_a_default(self):
        for rule in (None, {}, [], {"minimum_quiet_waves": True},
                     {"minimum_quiet_waves": 0}, {"minimum_quiet_waves": -1},
                     {"minimum_quiet_waves": 1.5}, {"minimum_quiet_waves": float("nan")},
                     {"minimum_quiet_waves": float("inf")}, {"minimum_quiet_waves": 3}):
            with self.subTest(rule=rule):
                self.assertTrue(research_progress_errors(dict(progress_record(), saturation_rule=rule)))
        for waves in ([], None, {}, [None], [{"id": "w1"}],
                      [{"id": "w1", "new_evidence_ids": "E:Exact"}]):
            with self.subTest(waves=waves):
                self.assertTrue(research_progress_errors(dict(progress_record(), waves=waves)))
        value = progress_record()
        value["waves"] = [value["waves"][1], value["waves"][2], value["waves"][0]]
        self.assertTrue(research_progress_errors(value))
        value.update(status="partial", stop_reason="blocked")
        self.assertEqual(research_progress_errors(value), [])
        value = progress_record()
        value["saturation_rule"]["minimum_quiet_waves"] = 1
        value["waves"].pop()
        self.assertEqual(research_progress_errors(value), [])

    def test_evidence_novelty_is_unique_across_waves_and_coverage_is_linked(self):
        mutations = (
            lambda v: v["waves"][1]["new_evidence_ids"].append("E:Exact"),
            lambda v: v["waves"][0]["new_evidence_ids"].append("E:Exact"),
            lambda v: v["waves"][1].update(id="w1"),
            lambda v: v["axes"][0]["covered_evidence_ids"].append("E:Exact"),
            lambda v: v["axes"][0].update(covered_evidence_ids=["e:exact"]),
            lambda v: v["axes"][0].update(unresolved_leads=[{"id": "L", "required": False}] * 2),
        )
        for mutate in mutations:
            value = progress_record()
            mutate(value)
            value.update(status="partial", stop_reason="blocked")
            with self.subTest(value=value):
                self.assertTrue(research_progress_errors(value))
        value = progress_record()
        value["waves"][0]["new_evidence_ids"].append("e:exact")
        value["axes"][0]["covered_evidence_ids"].append("e:exact")
        value["axes"][0]["required_coverage"] = 2
        self.assertEqual(research_progress_errors(value), [])


if __name__ == "__main__":
    unittest.main()
