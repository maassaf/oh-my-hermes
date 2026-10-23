#!/usr/bin/env python3
"""Offline corpus validation and supplied-receipt reconciliation. Never calls providers."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import math
from datetime import datetime
import statistics
import sys
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = ROOT / "tests/fixtures/search_amplification/corpus.json"
CATEGORIES = (
    "simple_web_lookup", "current_comparison", "paper_repository_discovery",
    "local_file_lookup", "authorized_app_search", "ambiguity_followup_recovery",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def closed(value, fields, context):
    require(type(value) is dict and set(value) == set(fields.split()),
            f"{context}: invalid fields")


def text(value):
    return type(value) is str and bool(value.strip())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def input_digest(case):
    return digest({"prompt": case["prompt"], "conditions": case["conditions"]})


def validate_corpus(corpus):
    closed(corpus, "schema_version corpus_id label cases", "corpus")
    require(corpus["schema_version"] == "search_benchmark_corpus/v1"
            and text(corpus["corpus_id"]) and corpus["label"] == "SYNTHETIC", "corpus: version/label")
    require(type(corpus["cases"]) is list and len(corpus["cases"]) == 60, "corpus: exactly 60 cases required")
    seen = {name: set() for name in ("case_id", "topic_id", "prompt")}
    counts = {category: Counter() for category in CATEGORIES}
    for case in corpus["cases"]:
        closed(case, "case_id category split topic_id label synthetic prompt required_checks conditions", "case")
        require(case["category"] in CATEGORIES and case["split"] in ("dev", "holdout"), "case: category/split")
        require(case["synthetic"] is True and case["label"] == "SYNTHETIC", "case: synthetic label required")
        for field in seen:
            require(text(case[field]), f"case: {field} must be text")
            # Comparison key only: never mutate original opaque input tokens.
            key = re.sub(r"\s+", " ", case[field]).casefold().strip()
            require(key not in seen[field], f"corpus: duplicate/leaking {field}")
            seen[field].add(key)
        require(type(case["required_checks"]) is list and bool(case["required_checks"])
                and all(text(x) for x in case["required_checks"]), "case: required checks")
        closed(case["conditions"], "clock scope fixture_only", "conditions")
        require(text(case["conditions"]["clock"]) and text(case["conditions"]["scope"])
                and case["conditions"]["fixture_only"] is True, "conditions: synthetic scope")
        counts[case["category"]][case["split"]] += 1
    require(all(dict(count) == {"dev": 5, "holdout": 5} for count in counts.values()),
            "corpus: each category requires dev5/holdout5")
    return {"corpus_id": corpus["corpus_id"], "label": "SYNTHETIC", "total": 60,
            "splits": {"dev": 30, "holdout": 30}, "categories": counts,
            "corpus_digest": digest(corpus),
            "leakage_checks": ["unique_case_ids", "unique_topic_families", "unique_normalized_prompts"],
            "claim_boundary": "Synthetic tasks only; no API responses or measured baseline answers."}


RECEIPT_FIELDS = """receipt_id pair_id case_id arm observation_kind corpus_digest input_digest
conditions arm_revision started_at ended_at trace_ref answer_ref status retrieved_evidence_ids
 gold_evidence_ids correct grounding manual_grounding review latency_ms cost_usd pricing_ref"""
STATUSES = ("completed", "unavailable", "denied", "failed", "cancelled", "unsearched", "budget_exhausted")
ARMS = ("baseline", "amplified")


def timestamp(value):
    require(text(value), "receipt: timestamp must be text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("receipt: invalid timestamp") from None
    require(parsed.tzinfo is not None, "receipt: timestamp requires timezone")
    return parsed


def nonnegative(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def identifiers(value):
    return (type(value) is list and all(text(x) for x in value)
            and len(set(value)) == len(value))


def validate_receipt(row, cases, corpus_hash, kind):
    closed(row, RECEIPT_FIELDS, "receipt")
    for field in ("receipt_id", "pair_id", "case_id", "arm_revision", "trace_ref"):
        require(text(row[field]), f"receipt: {field} required")
    require(row["observation_kind"] == kind, "receipt: wrong evidence lane; never mix REAL and SYNTHETIC")
    require(row["arm"] in ARMS and row["case_id"] in cases, "receipt: unknown arm/case")
    case = cases[row["case_id"]]
    require(row["corpus_digest"] == corpus_hash and row["input_digest"] == input_digest(case),
            "receipt: corpus/input digest mismatch")
    condition = row["conditions"]
    closed(condition, "model_id model_version tool_versions environment_id scope_ref clock", "receipt conditions")
    require(all(text(condition[f]) for f in ("model_id", "model_version", "environment_id", "scope_ref", "clock")),
            "receipt: conditions must be named")
    require(type(condition["tool_versions"]) is dict and bool(condition["tool_versions"])
            and all(text(k) and text(v) for k, v in condition["tool_versions"].items()), "receipt: tool versions required")
    require(condition["scope_ref"] == case["conditions"]["scope"]
            and condition["clock"] == case["conditions"]["clock"], "receipt: task conditions changed")
    require(timestamp(row["started_at"]) <= timestamp(row["ended_at"]), "receipt: inverted interval")
    require(row["status"] in STATUSES, "receipt: invalid status")
    for field in ("retrieved_evidence_ids", "gold_evidence_ids"):
        require(row[field] is None or identifiers(row[field]), f"receipt: invalid {field}")
    for field in ("correct", "manual_grounding"):
        require(row[field] is None or type(row[field]) is bool, f"receipt: invalid {field}")
    for field in ("latency_ms", "cost_usd"):
        require(row[field] is None or nonnegative(row[field]), f"receipt: invalid {field}")
    require((row["cost_usd"] is None and row["pricing_ref"] is None)
            or (row["cost_usd"] is not None and text(row["pricing_ref"])), "receipt: price requires observed pricing ref")
    require(row["answer_ref"] is None or text(row["answer_ref"]), "receipt: invalid answer ref")
    if row["grounding"] is not None:
        closed(row["grounding"], "supported_claims total_claims citation_check_ref", "grounding")
        ground = row["grounding"]
        require(all(type(ground[f]) is int and ground[f] >= 0 for f in ("supported_claims", "total_claims"))
                and ground["supported_claims"] <= ground["total_claims"]
                and text(ground["citation_check_ref"]), "receipt: invalid grounding counts")
    has_judgment = any(row[f] is not None for f in ("gold_evidence_ids", "correct", "grounding", "manual_grounding"))
    if row["review"] is not None:
        closed(row["review"], "review_ref protocol_ref blinded", "review")
        require(text(row["review"]["review_ref"]) and text(row["review"]["protocol_ref"])
                and row["review"]["blinded"] is True, "receipt: review must be arm-blinded")
    require(not has_judgment or row["review"] is not None, "receipt: judgments require a blinded review receipt")
    require(not any(row[f] is not None for f in ("correct", "grounding", "manual_grounding"))
            or text(row["answer_ref"]), "receipt: answer judgment requires answer ref")
    if row["status"] != "completed":
        require(all(row[f] is None for f in ("retrieved_evidence_ids", "gold_evidence_ids", "correct", "grounding", "manual_grounding")),
                "receipt: uncompleted search is not zero results or assessed quality")


def rate_payload(numerator, denominator, numerator_of, denominator_of, excluded):
    # Import the existing pure rate owner by exact path, without OMH bootstrap.
    name = "_search_benchmark_reported_rate"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "src/quality/reported_rate.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].reported_rate(
        numerator=numerator, denominator=denominator, numerator_of=[numerator_of],
        denominator_of=denominator_of, excluded=excluded, digits=6).to_payload()


def measurement(row, metric):
    if row["status"] != "completed":
        return None, "status:" + row["status"]
    retrieved = row["retrieved_evidence_ids"]
    if metric == "empty_results":
        return (int(not retrieved), 1) if retrieved is not None else (None, "retrieval_unobserved")
    if metric == "recall_at_5":
        gold = row["gold_evidence_ids"]
        if retrieved is None or not gold:
            return None, "retrieval_or_nonempty_gold_unobserved"
        return len(set(retrieved[:5]) & set(gold)), len(gold)
    if metric in ("correctness", "manual_grounding"):
        field = "correct" if metric == "correctness" else "manual_grounding"
        return (int(row[field]), 1) if row[field] is not None else (None, "judgment_unobserved")
    if metric == "grounding":
        ground = row["grounding"]
        if ground is None or not ground["total_claims"]:
            return None, "claim_review_unobserved_or_empty"
        return ground["supported_claims"], ground["total_claims"]
    value = row[metric]
    return (value, 1) if value is not None else (None, "unpriced" if metric == "cost_usd" else "latency_unobserved")


def summarize_metric(corpus, selected, pairs, arm, metric, lane, split):
    counts = Counter()
    usable = []
    for case in selected:
        pair = pairs.get(case["case_id"])
        if pair is None:
            counts["unpaired_or_unobserved_case"] += 1
            continue
        value = measurement(pair[arm], metric)
        other = measurement(pair[ARMS[1 - ARMS.index(arm)]], metric)
        if value[0] is None:
            counts[value[1]] += 1
        elif other[0] is None:
            counts["other_arm:" + other[1]] += 1
        else:
            usable.append(value)
    excluded = [f"{key}:{count}" for key, count in sorted(counts.items())]
    result = {"arm": arm, "corpus": corpus["corpus_id"], "lane": lane,
              "selection": {"split": split, "rule": "both arms eligible for this metric; one pair per case",
                            "case_ids": [c["case_id"] for c in selected]},
              "eligible_pairs": len(usable), "exclusion_counts": dict(sorted(counts.items()))}
    if metric in ("latency_ms", "cost_usd"):
        values = sorted(x[0] for x in usable)
        result.update({"value": (values[math.ceil(.95 * len(values)) - 1] if metric == "latency_ms"
                                 else statistics.median(values)) if values else None,
                       "statistic": "nearest_rank_p95" if metric == "latency_ms" else "median",
                       "basis": "observed_cases" if values else "no_observations",
                       "denominator": len(values), "denominator_of": "paired cases with observed values in both arms",
                       "excluded": excluded})
    else:
        numerator = sum(x[0] for x in usable)
        denominator = sum(x[1] for x in usable)
        meaning = {"empty_results": ("completed_searches_with_empty_return", "completed paired searches with observed result lists"),
                   "recall_at_5": ("unique_gold_evidence_ids_in_first_five", "independently judged gold evidence IDs in eligible paired cases (micro recall)"),
                   "correctness": ("blinded_correct_answers", "eligible paired answers with blinded correctness judgments"),
                   "grounding": ("claims_with_read_location_citation_match", "reviewed material claims in eligible paired answers; citation match is not truth"),
                   "manual_grounding": ("blinded_manually_supported_answers", "eligible paired answers with separate blinded manual grounding review")}
        result["rate"] = rate_payload(numerator, denominator, *meaning[metric], excluded)
    return result


def reconcile(corpus, observations, *, lane="real", split="holdout"):
    summary = validate_corpus(corpus)
    require(lane in ("real", "synthetic_algorithm") and split in ("dev", "holdout"), "invalid lane/split")
    closed(observations, "schema_version receipts", "observations")
    require(observations["schema_version"] == "search_benchmark_receipts/v1"
            and type(observations["receipts"]) is list, "observations: invalid version/receipts")
    cases = {case["case_id"]: case for case in corpus["cases"]}
    receipts = observations["receipts"]
    grouped = {}
    seen = set()
    case_pairs = {}
    for row in receipts:
        validate_receipt(row, cases, summary["corpus_digest"], "REAL" if lane == "real" else "SYNTHETIC")
        require(row["receipt_id"] not in seen, "receipt: duplicate receipt ID")
        seen.add(row["receipt_id"])
        pair = grouped.setdefault(row["pair_id"], {})
        require(row["arm"] not in pair, "receipt: duplicate pair arm")
        pair[row["arm"]] = row
        previous = case_pairs.setdefault(row["case_id"], row["pair_id"])
        require(previous == row["pair_id"], "receipt: multiple pairs per case; preselect repeats outside this report")
    pairs = {}
    unpaired = []
    for pair in grouped.values():
        if len(pair) != 2:
            unpaired.extend(row["receipt_id"] for row in pair.values())
            continue
        baseline, amplified = pair["baseline"], pair["amplified"]
        require(all(baseline[f] == amplified[f] for f in ("case_id", "input_digest", "conditions")),
                "pair: task/model/tools/scope/clock mismatch")
        if baseline["gold_evidence_ids"] is not None and amplified["gold_evidence_ids"] is not None:
            require(baseline["gold_evidence_ids"] == amplified["gold_evidence_ids"], "pair: gold mismatch")
        # Missing judgments remain missing, including a successful/unavailable pair.
        if baseline["review"] is not None and amplified["review"] is not None:
            require(baseline["review"]["protocol_ref"] == amplified["review"]["protocol_ref"],
                    "pair: review protocol mismatch")
        pairs[baseline["case_id"]] = pair
    selected = [case for case in cases.values() if case["split"] == split]
    names = ("empty_results", "recall_at_5", "correctness", "grounding", "manual_grounding", "latency_ms", "cost_usd")
    metrics = {arm: {metric: summarize_metric(corpus, selected, pairs, arm, metric, lane, split)
                     for metric in names} for arm in ARMS}
    selections = {
        "simple": [case for case in selected if case["category"] == "simple_web_lookup"],
        "complex": [case for case in selected if case["category"] in
                    ("current_comparison", "paper_repository_discovery")],
    }
    selection_metrics = {
        selection: {arm: {metric: summarize_metric(corpus, subset, pairs, arm, metric, lane, split)
                          for metric in names} for arm in ARMS}
        for selection, subset in selections.items()
    }
    gates = quality_gates(metrics, selection_metrics, lane=lane, split=split)
    return {"schema_version": "search_benchmark_report/v1", "corpus": summary,
            "rule_version": "search_quality_decision/v1", "quality_gates": gates,
            "selection_metrics": selection_metrics,
            "lane": lane, "split": split, "receipt_count": len(receipts),
            "paired_cases": sum(c["case_id"] in pairs for c in selected),
            "unpaired_receipt_ids": sorted(unpaired), "metrics": metrics,
            "live_improvement_claimed": False,
            "claim_boundary": "Supplied receipts are assertions, not authenticated execution. Synthetic fixtures never measure live quality."}


def quality_gates(metrics, selections, *, lane, split):
    """Frozen v1 candidate rules, not installed/E2E or authenticated live claims."""
    rules = {
        "complex_recall": "+10pp micro Recall@5; if baseline >=90%, noninferior recall and improved blinded manual grounding",
        "simple_correctness": "noninferior blinded correctness on all 5 simple holdout pairs",
        "simple_latency": "nearest-rank p95 <=1.2x baseline on all 5 simple holdout pairs",
        "median_cost": "median observed USD cost <=1.5x baseline on all 30 holdout pairs",
        "citation_linkage": "all reviewed material claims have read-source location matches on all 30 pairs; not fact verification",
        "manual_grounding": "separate blinded manual grounding supports every amplified answer on all 30 pairs",
        "installed_usability": "installed natural-language use without new commands/modes/keys, or honest capability absence",
        "safety": "observed authorization, disclosure, provider-call and persistence boundaries",
        "failure_semantics": "observed installed failure, cancellation, unread, conflicting and unsearched handling",
        "installed_e2e": "installed holdout E2E and all routing/plugin/skill/generated/package gates observed",
    }
    gates = {key: {"status": "unmeasured", "rule": rule, "reason": "no_observations"}
             for key, rule in rules.items()}
    if lane != "real" or split != "holdout":
        reason = "synthetic_algorithm_not_live_evidence" if lane != "real" else "development_not_holdout"
        for gate in gates.values():
            gate["reason"] = reason
        return gates

    def complete(group, metric, expected):
        return all(group[arm][metric]["eligible_pairs"] == expected for arm in ARMS)

    def percent(group, arm, metric):
        rate = group[arm][metric]["rate"]
        return rate["numerator"] * 100 / rate["denominator"] if rate["denominator"] else None

    def decide(key, passed, refs):
        gates[key].update(status="pass" if passed else "fail", reason="supplied_paired_receipts_only", metric_refs=refs)

    simple, complex_group = selections["simple"], selections["complex"]
    if complete(complex_group, "recall_at_5", 10):
        baseline = percent(complex_group, "baseline", "recall_at_5")
        amplified = percent(complex_group, "amplified", "recall_at_5")
        if baseline < 90:
            decide("complex_recall", amplified - baseline >= 10 - 1e-12,
                   ["selection_metrics.complex.*.recall_at_5"])
        elif complete(complex_group, "manual_grounding", 10):
            decide("complex_recall", amplified >= baseline and
                   percent(complex_group, "amplified", "manual_grounding") >
                   percent(complex_group, "baseline", "manual_grounding"),
                   ["selection_metrics.complex.*.recall_at_5", "selection_metrics.complex.*.manual_grounding"])
    if complete(simple, "correctness", 5):
        decide("simple_correctness", percent(simple, "amplified", "correctness") >=
               percent(simple, "baseline", "correctness"), ["selection_metrics.simple.*.correctness"])
    for key, group, metric, expected, limit in (
        ("simple_latency", simple, "latency_ms", 5, 1.2),
        ("median_cost", metrics, "cost_usd", 30, 1.5),
    ):
        if complete(group, metric, expected):
            base = group["baseline"][metric]["value"]
            amp = group["amplified"][metric]["value"]
            # A zero baseline cannot define an observed ratio, even if amplified is zero.
            if base is not None and base > 0 and amp is not None:
                decide(key, amp / base <= limit, [f"{'metrics' if key == 'median_cost' else 'selection_metrics.simple'}.*.{metric}"])
                gates[key]["amplified_over_baseline"] = amp / base
            else:
                gates[key]["reason"] = "zero_or_missing_baseline_ratio_undefined"
    for key, metric in (("citation_linkage", "grounding"), ("manual_grounding", "manual_grounding")):
        if complete(metrics, metric, 30):
            decide(key, percent(metrics, "amplified", metric) == 100, [f"metrics.*.{metric}"])
    return gates


def load_json(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "JSON: duplicate field")
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError("JSON: non-finite number")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object,
                      parse_constant=reject_constant)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "reconcile"])
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--observations", type=Path, help="Supplied receipts only; no provider is called")
    parser.add_argument("--lane", choices=["real", "synthetic_algorithm"], default="real")
    parser.add_argument("--split", choices=["dev", "holdout"], default="holdout")
    args = parser.parse_args(argv)
    try:
        corpus = load_json(args.corpus)
        if args.command == "validate":
            output = validate_corpus(corpus)
        else:
            require(args.observations is not None, "reconcile requires --observations; collection is never automatic")
            output = reconcile(corpus, load_json(args.observations), lane=args.lane, split=args.split)
        print(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, TypeError, KeyError, OverflowError):
        print(json.dumps({"error": "invalid benchmark input or unreadable file; no provider called"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
