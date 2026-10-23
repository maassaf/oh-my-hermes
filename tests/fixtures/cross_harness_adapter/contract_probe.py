# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# ─── How to run ───
# PYTHONPATH=tests uv run python tests/fixtures/cross_harness_adapter/contract_probe.py

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "src"))

from omh.quality.cross_harness_adapter_evidence import (
    artifact_content_digest,
    corpus_fixture_binding_digest,
    parse_adapter_evidence,
    project_adapter_evidence,
)
from omh.quality.cross_harness_adapter_model import (
    AdapterContractError,
    canonical_digest,
)
from omh.quality.cross_harness_benchmark import parse_corpus
from omh.quality.cross_harness_benchmark_values import JsonValue

EXAMPLE = ROOT / "benchmarks" / "cross-harness" / "v1" / "example-passing-submission.json"


def mapping(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def array(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def build_bundle() -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    envelope: JsonValue = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    benchmark = mapping(envelope)
    corpus_raw = mapping(benchmark["corpus"])
    corpus = parse_corpus(corpus_raw)
    submission = mapping(benchmark["submission"])
    cases: list[JsonValue] = []
    for value in array(submission["results"]):
        source = mapping(value)
        fixture = next(item for item in corpus.fixtures if item.id == source["fixture_id"])
        request: dict[str, JsonValue] = {
            "schema_version": "cross_harness_adapter_request/v1",
            "protocol_version": "cross_harness_adapter_protocol/v1",
            "corpus_digest": corpus.digest,
            "fixture_binding_digest": corpus_fixture_binding_digest(fixture),
            "fixture_id": source["fixture_id"],
            "adapter_id": source["adapter_id"],
            "capability_id": source["capability_id"],
            "profile": "codex",
            "executable": "codex",
            "executable_version": "1.2.3",
            "model": "gpt-5.6-sol",
            "effort": "high",
            "capabilities": ["tool-events", "child-events"],
            "argv_digest": "1" * 64,
            "repetition": 1,
            "timeout_seconds": 30,
        }
        request_digest = canonical_digest(request)
        result: dict[str, JsonValue] = {
            "schema_version": "cross_harness_adapter_result/v1",
            "request_digest": request_digest,
            "fixture_id": source["fixture_id"],
            "adapter_id": source["adapter_id"],
            "capability_id": source["capability_id"],
            "evidence_class": source["evidence_class"],
            "observation_state": source["runtime_observation"],
            "actual_machine": source["actual_machine"],
            "facts": source["facts"],
            "skill_events": [],
            "tool_events": [],
            "child_results": source["child_results"],
            "artifact_type": "cross_harness_adapter_fixture_result/v1",
            "artifact_hash": "2" * 64,
            "process_status": "exit",
            "exit_code": 0,
            "side_effects": [{"path": "output/result.json", "change": "created"}],
        }
        result["artifact_hash"] = artifact_content_digest(result)
        cases.append({
            "fixture_id": source["fixture_id"],
            "request": request,
            "request_digest": request_digest,
            "result": result,
            "result_digest": canonical_digest(result),
            "source_binding": source["source_binding"],
            "command_evidence": source["command_evidence"],
        })
    bundle: dict[str, JsonValue] = {
        "schema_version": "cross_harness_adapter_evidence/v1",
        "corpus_digest": corpus.digest,
        "harness_id": submission["harness_id"],
        "cases": cases,
    }
    refresh_bundle(bundle)
    return corpus_raw, bundle


def refresh_bundle(bundle: dict[str, JsonValue]) -> None:
    unsigned = {key: value for key, value in bundle.items() if key != "bundle_digest"}
    bundle["bundle_digest"] = canonical_digest(unsigned)


def refresh_first_case(bundle: dict[str, JsonValue]) -> None:
    case = mapping(array(bundle["cases"])[0])
    request = mapping(case["request"])
    result = mapping(case["result"])
    case["request_digest"] = canonical_digest(request)
    result["request_digest"] = case["request_digest"]
    result["artifact_hash"] = artifact_content_digest(result)
    case["result_digest"] = canonical_digest(result)
    refresh_bundle(bundle)


def rejected_reason(bundle: dict[str, JsonValue]) -> str:
    try:
        parse_adapter_evidence(bundle)
    except AdapterContractError as error:
        return error.reason_code
    raise AssertionError("mutation unexpectedly accepted")


def main() -> int:
    corpus_raw, valid = build_bundle()
    corpus = parse_corpus(corpus_raw)
    projected = project_adapter_evidence(corpus, parse_adapter_evidence(valid))
    assert len(array(projected["results"])) == 15
    rejected: dict[str, str] = {}

    stale = deepcopy(valid)
    mapping(mapping(array(stale["cases"])[0])["request"])["schema_version"] = "cross_harness_adapter_request/v0"
    refresh_first_case(stale)
    rejected["stale_version"] = rejected_reason(stale)

    wrong_artifact = deepcopy(valid)
    mapping(mapping(array(wrong_artifact["cases"])[0])["result"])["artifact_type"] = "text/plain"
    refresh_first_case(wrong_artifact)
    rejected["wrong_artifact"] = rejected_reason(wrong_artifact)

    secret = deepcopy(valid)
    mapping(mapping(array(secret["cases"])[0])["request"])["model"] = "sk-redacted-example"
    refresh_first_case(secret)
    rejected["secret_shaped"] = rejected_reason(secret)

    tampered = deepcopy(valid)
    mapping(array(tampered["cases"])[0])["result_digest"] = "f" * 64
    refresh_bundle(tampered)
    rejected["digest_tamper"] = rejected_reason(tampered)

    missing = deepcopy(valid)
    array(missing["cases"]).pop()
    refresh_bundle(missing)
    try:
        project_adapter_evidence(corpus, parse_adapter_evidence(missing))
    except AdapterContractError as error:
        rejected["missing_fixture"] = error.reason_code

    duplicate = deepcopy(valid)
    duplicate_cases = array(duplicate["cases"])
    duplicate_cases.append(deepcopy(duplicate_cases[0]))
    refresh_bundle(duplicate)
    rejected["duplicate_fixture"] = rejected_reason(duplicate)

    print(json.dumps({"schema_version": "cross_harness_adapter_contract_probe/v1", "valid_cases": 1, "rejected": rejected}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
