# Search amplification evaluation — operator reference

## Evidence boundary

`corpus.json` contains **60 SYNTHETIC task specifications**, not search responses,
baseline answers, gold answers, or observed provider results. The six approved
categories each have ten distinct tasks: five `dev`, five `holdout`. Topic-family
IDs, case IDs, and whitespace/case-normalized prompt comparison keys must be
unique across the entire corpus. This checks declared family leakage and exact
prompt duplicates, not arbitrary semantic paraphrases or training contamination.
Opaque source strings in the stored tasks are never rewritten.

`algorithm_receipts.json` is a separate **SYNTHETIC** unavailable-capability
algorithm fixture. Tests also construct explicitly synthetic numeric/retrieval
fixtures. These verify accounting, pairing, ranking-cutoff and gate logic; none
is a live benchmark. `empty_observations.json` contains no observations, not
zero-result observations. There are no committed REAL receipts or answers.

The local/app task descriptions do not authorize access or create their fixture
targets. A later evaluation owner must materialize approved test fixtures/accounts
and pre-register their scope bindings. Public-web tasks likewise need independent
source assessment. Missing scope, tools, permission, answers, judgments, timing or
pricing must remain missing. Do not substitute generated results or estimates.

## CLI (maintainer/operator, not a new normal-user command)

From the repository root, with the repository's normal verified environment:

```sh
uv run python tools/benchmarks/search_amplification.py validate
uv run python tools/benchmarks/search_amplification.py reconcile --observations tests/fixtures/search_amplification/empty_observations.json
uv run python tools/benchmarks/search_amplification.py reconcile --lane synthetic_algorithm --split dev --observations tests/fixtures/search_amplification/algorithm_receipts.json
uv run python tools/benchmarks/search_amplification.py reconcile --split holdout --observations /path/to/approved-real-paired-receipts.json
```

`--corpus PATH` overrides the default corpus. `--lane real` (the default) accepts
only `observation_kind: REAL`; `--lane synthetic_algorithm` accepts only
`observation_kind: SYNTHETIC`. Mixed lanes fail. Reconciliation always requires an
explicit local observations file; there is no collector, provider client, hidden
fallback, external model, or automatic tool call. Output is JSON on stdout.
Exit 0 means corpus/receipt reconciliation succeeded, **not** that quality goals
passed. Invalid JSON, duplicate JSON fields, nonfinite numbers, invalid records
or unreadable files return 2 with a controlled JSON error on stderr.

For this constrained run, the evidence-root `benchmark/run_checks.py` runs these
commands using an allowlisted environment, invocation-isolated scratch homes,
`UV_NO_PROJECT=1`, `UV_OFFLINE=1`, `UV_PYTHON_DOWNLOADS=never`, no dotenv, no bytecode,
and already-installed Python/cached Ruff. It does not sync, install, or download.
This is an explicit no-project execution of the named commands, not project
installation/package verification. No broad test suite is authorized by it.

## Receipt schema (`search_benchmark_receipts/v1`)

The root is a closed object with exactly `schema_version` (the literal above)
and `receipts` (an array, allowed empty). Every receipt is a closed object with
**all** the fields below required; nullable fields must explicitly be `null`.

| Field | Type / meaning |
|---|---|
| `receipt_id` | Nonblank globally unique string; immutable external receipt identity. |
| `pair_id` | Nonblank string; exactly one `baseline` and one `amplified` receipt per complete pair. |
| `case_id` | Exact corpus case ID. One preselected pair per case; repeated trials require a separately frozen selection protocol, never cherry-picking inside this report. |
| `arm` | Exactly `baseline` or `amplified`. |
| `observation_kind` | Exactly `REAL` or `SYNTHETIC`, required to match the selected lane. This label alone does not authenticate execution. |
| `corpus_digest` | Exact SHA-256 canonical digest of the entire corpus. |
| `input_digest` | Exact SHA-256 canonical digest of `{prompt: case.prompt, conditions: case.conditions}`. |
| `conditions` | Closed object described below; identical across both arms. |
| `arm_revision` | Nonblank exact code/config revision of that arm; may differ between arms. |
| `started_at`, `ended_at` | ISO-8601 timestamps with explicit timezone; start must not exceed end. Original strings remain unchanged. |
| `trace_ref` | Nonblank opaque reference to the externally retained actual execution/failure ledger. A ref is not proof of access, truth, or execution. |
| `answer_ref` | Nonblank opaque external answer reference or null; required for any answer judgment. Never embed a generated baseline answer here. |
| `status` | `completed`, `unavailable`, `denied`, `failed`, `cancelled`, `unsearched`, or `budget_exhausted`. |
| `retrieved_evidence_ids` | Ordered unique nonblank evidence-ID strings, or null if retrieval was not observed. `[]` means an actually completed search returned no candidates, not missing capability. |
| `gold_evidence_ids` | Independently established unique nonblank evidence-ID strings or null. Empty gold is unscorable, not perfect recall. Both nonnull lists must agree across arms. Gold must be established by the review protocol, not generated from a preferred arm's retrieved set. |
| `correct` | Blinded answer correctness judgment, boolean or null. No automatic semantic correctness claim. |
| `grounding` | Null or closed object `{supported_claims, total_claims, citation_check_ref}`. Counts are nonnegative JSON integers (not booleans), supported <= total. The nonblank check ref must point to the external read-location/citation audit; total 0 is unmeasured. This is citation linkage, **not truth verification**. |
| `manual_grounding` | Separate arm-blinded human support judgment over the answer's material claims, boolean or null. Do not fill it from the automatic citation check. |
| `review` | Null or closed object `{review_ref, protocol_ref, blinded}`. Refs are nonblank strings and blinded must be true. Required for any supplied gold/answer judgment. If both arms carry reviews their protocol refs must match. |
| `latency_ms` | Finite nonnegative observed end-to-end elapsed milliseconds, or null. Do not derive a made-up value from a target or a synthetic expectation. |
| `cost_usd` | Finite nonnegative actually priced observation, or null for unpriced/missing usage. No estimated replacement for missing usage/pricing. |
| `pricing_ref` | Nonblank opaque pricing/usage receipt ref iff cost is nonnull; otherwise null. Explicit measured zero with a pricing ref is allowed; a zero baseline ratio remains undefined. |

`conditions` contains exactly six fields: nonblank `model_id` (provider-qualified),
`model_version`, `environment_id`, `scope_ref`, `clock`, and `tool_versions` (a
nonempty object mapping exact nonblank tool names to exact nonblank versions).
The scope ref and supplied assessment clock must equal the case conditions.
Provider/model/tool/settings/environment/scope must be controlled and recorded by
the evaluator; changing an arm's revision is the treatment, not a license to
change the evaluation conditions. Case `conditions.fixture_only=true` describes
the artificial task scope, not permission to relabel simulated execution REAL.
REAL means externally observed execution, even when the task itself is synthetic.

Canonical digest encoding is UTF-8 `json.dumps(value, sort_keys=True,
ensure_ascii=False, separators=(',', ':'), allow_nan=False)`. The CLI `validate`
prints the corpus digest; the module's pure `input_digest(case)` computes each
input binding. Digests identify supplied data; they are not signatures.

Noncompleted records must have null retrieved/gold/correct/grounding/manual fields.
They can retain their independently observed timing/cost fields, but v1 quality
summaries conservatively exclude all noncompleted statuses, explicitly naming
those exclusions. Mixed success/unavailable pairs are valid; the unavailable arm
is never scored as a zero-result search. A missing review/gold/value in either
arm excludes that pair for that metric, not every other observed metric.

## Pairing and metrics

Unknown or duplicate IDs, duplicate arms, changed task/model/tool/scope/clock,
conflicting nonnull gold, changed review protocol, malformed times, invalid
counts and unknown fields are rejected. Incomplete pairs are reported explicitly
and are not scored. Every metric names arm, corpus, lane, selected case IDs,
split, both-arms eligibility rule, eligible-pair count and exclusion counts.
Rate payloads under `rate` reuse `src/quality/reported_rate.py` unchanged and carry
its exact closed `omh_reported_rate/v1` shape: numerator, denominator,
numerator bucket, denominator meaning, excluded classes, basis and percent.
A zero denominator yields `percent:null, basis:no_observations`, never zero.

- `empty_results`: completed searches returning `[]` / completed paired searches
  with observed result lists. Missing capabilities are excluded by named status.
- `recall_at_5`: unique gold IDs in the first five returned IDs / independently
  judged nonempty gold IDs, micro-aggregated across metric-eligible pairs.
- `correctness`: blinded correct answers / eligible paired answer judgments.
- `grounding`: read-location citation matches / reviewed material claims.
- `manual_grounding`: independently blinded supported answers / eligible paired
  manual judgments. This cannot be inferred from citation syntax.
- `latency_ms`: nearest-rank p95 over completed, latency-observed paired cases.
- `cost_usd`: median over completed, actually priced paired cases.

Timing/cost summaries retain a null `value` and `basis:no_observations` when
empty. Known failed-call charges/times remain in the supplied source receipts;
this conservative v1 report is not an all-attempt billing ledger. It cannot pass
a full-cost goal on a successful-only subset because every selected holdout case
must have both eligible completed observations.

## Frozen candidate decision rules

`search_quality_decision/v1` is fixed here before any REAL holdout observations.
Develop on `dev`; do not tune on holdout results. A later rule change needs a new
version and new untouched holdout selection. No development or synthetic run can
pass a live gate. Subset observations remain unmeasured, even if their scores
look favorable. `selection_metrics.simple` covers five simple-web holdout cases;
`selection_metrics.complex` covers ten current-comparison/paper-repository
holdout cases. `metrics` covers all thirty selected cases.

- Complex micro Recall@5: at least +10 percentage points with all ten eligible
  pairs. If baseline >=90%, require noninferior recall **and** improved separate
  blinded manual grounding on all ten pairs instead.
- Simple correctness must not decrease; all five paired judgments required.
- Simple nearest-rank p95 must be <=1.2 times baseline; all five paired values
  and a positive baseline required.
- Overall median observed USD cost must be <=1.5 times baseline; all thirty
  paired values and a positive baseline required. Unpriced is not free.
- Citation linkage and independent manual support require complete thirty-pair
  reviews and all amplified material claims/answers supported, respectively.
- Installed usability, safety, installed failure semantics and installed E2E
  remain unmeasured in this offline receipt schema. They need separate authorized
  installed-system observations; this tool never promotes references to those
  claims. `live_improvement_claimed` is always false: the report is a bounded
  receipt reconciliation, not an authenticated superiority or launch claim.

Small-sample variability, real search nondeterminism, evaluator reliability,
access revocation and collector authenticity remain outside a pure reconciler.
No live improvement, installed behavior, semantic truth, broad-suite containment,
new provider capability, or general superiority is established by this corpus.
