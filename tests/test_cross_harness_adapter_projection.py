from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from _cli_harness import run_cli
from src.quality.cross_harness_adapter_evidence import (
    adapter_evidence_payload,
    adapter_request_payload,
    adapter_result_payload,
    artifact_content_digest,
    corpus_fixture_binding_digest,
    parse_adapter_evidence,
    project_adapter_evidence,
)
from src.quality.cross_harness_adapter_model import (
    AdapterContractError,
    canonical_digest,
    parse_adapter_request,
    parse_adapter_result,
)
from src.quality.cross_harness_benchmark import (
    evaluate_submission,
    parse_corpus,
    score_submission,
)
from src.quality.cross_harness_benchmark_values import JsonValue
from tests.test_cross_harness_adapter_contract import _request, _result


ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "benchmarks" / "cross-harness" / "v1" / "example-passing-submission.json"


def _passing_bundle_raw() -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    benchmark = _json_object(_load_json(EXAMPLE))
    corpus_raw = _json_object(benchmark["corpus"])
    corpus = parse_corpus(corpus_raw)
    passing = _json_object(benchmark["submission"])
    cases: list[JsonValue] = []
    for raw_result in _json_array(passing["results"]):
        submission_result = _json_object(raw_result)
        result = _result()
        for field in (
            "fixture_id",
            "adapter_id",
            "capability_id",
            "evidence_class",
            "actual_machine",
            "facts",
            "child_results",
        ):
            result[field] = submission_result[field]
        result["observation_state"] = submission_result["runtime_observation"]
        request = _request()
        request["corpus_digest"] = corpus.digest
        fixture = next(item for item in corpus.fixtures if item.id == result["fixture_id"])
        request["fixture_binding_digest"] = corpus_fixture_binding_digest(fixture)
        request["fixture_id"] = result["fixture_id"]
        request["adapter_id"] = result["adapter_id"]
        request["capability_id"] = result["capability_id"]
        request_digest = canonical_digest(request)
        result["request_digest"] = request_digest
        result["artifact_hash"] = artifact_content_digest(result)
        cases.append(
            {
                "fixture_id": result["fixture_id"],
                "request": request,
                "request_digest": request_digest,
                "result": result,
                "result_digest": canonical_digest(result),
                "source_binding": submission_result["source_binding"],
                "command_evidence": submission_result["command_evidence"],
            }
        )
    bundle: dict[str, JsonValue] = {
        "schema_version": "cross_harness_adapter_evidence/v1",
        "corpus_digest": corpus.digest,
        "harness_id": passing["harness_id"],
        "cases": cases,
    }
    bundle["bundle_digest"] = canonical_digest(bundle)
    return corpus_raw, bundle


class ExistingBenchmarkCharacterizationTests(unittest.TestCase):
    def test_validate_score_and_report_outputs_are_unchanged(self) -> None:
        expected = {
            "validate": "a90a12a955003b74748a25121b40a12fd74e9074732ea424b1c25842daddebd3",
            "score": "5fbcffbe8208b50973670b0fcd563def5ad8ea61ed38c5f4ed49f084d9cb14af",
            "report": "1d2d1c87ab0bcb2ed54df66846149c4df5b56030e1b6ae3bf9cb672bbf01f8cf",
        }
        for command, digest in expected.items():
            with self.subTest(command=command):
                status, stdout, stderr = run_cli(
                    ["benchmark", command, "--input", str(EXAMPLE)]
                )
                self.assertEqual(status, 0, stderr)
                self.assertEqual(hashlib.sha256(stdout.encode()).hexdigest(), digest)


class AdapterBoundaryFailingFirstTests(unittest.TestCase):
    def test_canonical_payloads_round_trip_without_shape_drift(self) -> None:
        request = parse_adapter_request(_request())
        result = parse_adapter_result(_result())
        self.assertEqual(parse_adapter_request(adapter_request_payload(request)), request)
        self.assertEqual(parse_adapter_result(adapter_result_payload(result)), result)

        _, bundle_raw = _passing_bundle_raw()
        bundle = parse_adapter_evidence(bundle_raw)
        self.assertEqual(parse_adapter_evidence(adapter_evidence_payload(bundle)), bundle)

    def test_bundle_projection_requires_digest_bound_exact_fixture_evidence(self) -> None:
        corpus_raw, bundle_raw = _passing_bundle_raw()
        corpus = parse_corpus(corpus_raw)
        passing = _json_object(_json_object(_load_json(EXAMPLE))["submission"])
        projected = project_adapter_evidence(corpus, parse_adapter_evidence(bundle_raw))

        report = evaluate_submission(projected, corpus)
        score = score_submission(projected, corpus)
        self.assertEqual(projected, passing)
        self.assertEqual(sum(outcome.status == "pass" for outcome in report.outcomes), 15)
        self.assertEqual((score.total, score.level), (100, 5))

    def test_digest_tamper_missing_duplicate_and_attacker_rehash_fail_closed(self) -> None:
        corpus_raw, bundle = _passing_bundle_raw()
        cases = _json_array(bundle["cases"])
        first = _json_object(cases[0])
        first["result_digest"] = "f" * 64
        bundle["bundle_digest"] = canonical_digest({key: value for key, value in bundle.items() if key != "bundle_digest"})
        with self.assertRaisesRegex(AdapterContractError, "result_digest_mismatch"):
            parse_adapter_evidence(bundle)

        corpus = parse_corpus(corpus_raw)
        _, missing = _passing_bundle_raw()
        _json_array(missing["cases"]).pop()
        missing["bundle_digest"] = canonical_digest({key: value for key, value in missing.items() if key != "bundle_digest"})
        with self.assertRaisesRegex(AdapterContractError, "missing_fixture_evidence"):
            project_adapter_evidence(corpus, parse_adapter_evidence(missing))

        _, duplicate = _passing_bundle_raw()
        duplicate_cases = _json_array(duplicate["cases"])
        duplicate_cases.append(dict(_json_object(duplicate_cases[0])))
        duplicate["bundle_digest"] = canonical_digest({key: value for key, value in duplicate.items() if key != "bundle_digest"})
        with self.assertRaisesRegex(AdapterContractError, "duplicate_fixture_evidence"):
            parse_adapter_evidence(duplicate)

        _, attacker = _passing_bundle_raw()
        attacker_case = _json_object(_json_array(attacker["cases"])[0])
        attacker_request = _json_object(attacker_case["request"])
        attacker_request["schema_version"] = "cross_harness_adapter_request/v999"
        attacker_case["request_digest"] = canonical_digest(attacker_request)
        attacker_result = _json_object(attacker_case["result"])
        attacker_result["request_digest"] = attacker_case["request_digest"]
        attacker_result["artifact_hash"] = artifact_content_digest(attacker_result)
        attacker_case["result_digest"] = canonical_digest(attacker_result)
        attacker["bundle_digest"] = canonical_digest({key: value for key, value in attacker.items() if key != "bundle_digest"})
        with self.assertRaisesRegex(AdapterContractError, "stale_adapter_version"):
            parse_adapter_evidence(attacker)

    def test_outer_fixture_identity_and_copied_fixture_content_are_bound(self) -> None:
        _, outer_mismatch = _passing_bundle_raw()
        outer_case = _json_object(_json_array(outer_mismatch["cases"])[0])
        outer_case["fixture_id"] = "different-fixture"
        outer_mismatch["bundle_digest"] = canonical_digest({key: value for key, value in outer_mismatch.items() if key != "bundle_digest"})
        with self.assertRaisesRegex(AdapterContractError, "fixture_binding_mismatch"):
            parse_adapter_evidence(outer_mismatch)

    def test_fully_rehashed_cross_case_retaining_source_and_command_is_rejected(self) -> None:
        corpus_raw, bundle = _passing_bundle_raw()
        corpus = parse_corpus(corpus_raw)
        cases = _json_array(bundle["cases"])
        copied = deepcopy(_json_object(cases[0]))
        target = _json_object(cases[1])
        target_fixture = corpus.fixtures[1]
        request = _json_object(copied["request"])
        result = _json_object(copied["result"])
        copied["fixture_id"] = target["fixture_id"]
        request["fixture_id"] = target_fixture.id
        request["adapter_id"] = target_fixture.adapter_id
        request["capability_id"] = target_fixture.capability_id
        request["fixture_binding_digest"] = corpus_fixture_binding_digest(target_fixture)
        copied["request_digest"] = canonical_digest(request)
        result["request_digest"] = copied["request_digest"]
        result["fixture_id"] = target_fixture.id
        result["adapter_id"] = target_fixture.adapter_id
        result["capability_id"] = target_fixture.capability_id
        result["artifact_hash"] = artifact_content_digest(result)
        copied["result_digest"] = canonical_digest(result)
        cases[1] = copied
        bundle["bundle_digest"] = canonical_digest({key: value for key, value in bundle.items() if key != "bundle_digest"})

        with self.assertRaisesRegex(AdapterContractError, "fixture_binding_mismatch"):
            project_adapter_evidence(corpus, parse_adapter_evidence(bundle))

    def test_source_and_command_bindings_match_the_full_trusted_corpus_values(self) -> None:
        mutations: tuple[tuple[str, str, JsonValue], ...] = (
            ("source_binding", "source_id", "different-source"),
            ("source_binding", "commit", "f" * 40),
            ("source_binding", "license", "different-license"),
            ("source_binding", "path_metadata", "different/metadata"),
            ("source_binding", "source_digest", "f" * 64),
            ("command_evidence", "command_id", "different-command"),
            ("command_evidence", "harness", "different-harness"),
            ("command_evidence", "argv", ["different-command"]),
            ("command_evidence", "cwd_class", "different-cwd"),
            ("command_evidence", "source_id", "different-source"),
            ("command_evidence", "source_commit", "f" * 40),
            ("command_evidence", "expected_exit", 1),
            ("command_evidence", "expected_semantic_result", "different-result"),
            ("command_evidence", "binding_digest", "f" * 64),
        )
        for section, field, value in mutations:
            with self.subTest(section=section, field=field):
                corpus_raw, bundle = _passing_bundle_raw()
                case = _json_object(_json_array(bundle["cases"])[0])
                _json_object(case[section])[field] = value
                bundle["bundle_digest"] = canonical_digest({key: item for key, item in bundle.items() if key != "bundle_digest"})
                with self.assertRaisesRegex(AdapterContractError, "fixture_binding_mismatch"):
                    project_adapter_evidence(parse_corpus(corpus_raw), parse_adapter_evidence(bundle))

    def test_artifact_hash_is_verified_independently_of_envelope_digests(self) -> None:
        corpus_raw, bundle = _passing_bundle_raw()
        case = _json_object(_json_array(bundle["cases"])[0])
        result = _json_object(case["result"])
        result["artifact_hash"] = "f" * 64
        case["result_digest"] = canonical_digest(result)
        bundle["bundle_digest"] = canonical_digest({key: value for key, value in bundle.items() if key != "bundle_digest"})

        with self.assertRaisesRegex(AdapterContractError, "artifact_hash_mismatch"):
            project_adapter_evidence(parse_corpus(corpus_raw), parse_adapter_evidence(bundle))

    def test_failed_child_survives_projection_and_blocks_certification(self) -> None:
        corpus_raw, bundle = _passing_bundle_raw()
        first_case = _json_object(_json_array(bundle["cases"])[0])
        result = _json_object(first_case["result"])
        result["child_results"] = [{"id": "child-a", "result": "fail"}]
        result["artifact_hash"] = artifact_content_digest(result)
        first_case["result_digest"] = canonical_digest(result)
        bundle["bundle_digest"] = canonical_digest({key: value for key, value in bundle.items() if key != "bundle_digest"})
        corpus = parse_corpus(corpus_raw)
        projected = project_adapter_evidence(corpus, parse_adapter_evidence(bundle))

        outcome = evaluate_submission(projected, corpus).outcomes[0]
        score = score_submission(projected, corpus)
        self.assertEqual((outcome.status, outcome.reason_codes), ("fail", ("child_failed",)))
        self.assertFalse(score.contract_certified)


def _load_json(path: Path) -> JsonValue:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _json_array(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


if __name__ == "__main__":
    unittest.main()
