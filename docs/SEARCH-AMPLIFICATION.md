# Finding sources without a new search mode

Ask Hermes for what you need. OMH's existing web research, source finder and
deep research skills share a bounded search-and-read method; there is no new
user command or workflow to learn. No mandatory extra API key, vector database,
AutoRAG installation or search-mode setup is added. Actual retrieval still
requires a search/read tool already available to your host.

## What this helps with

These are illustrative requests, not observed search results:

- **“Find datasets for browser agent benchmarks.”** Find candidates against
  evaluation needs, license and maintenance requirements; read original pages
  before claiming those requirements are met. A link alone is not a verified
  download or license check.
- **“Find the report about our last authentication incident.”** Search only the
  project folders or connected account scopes authorized for this request;
  connect related reports/code with original locations, without exposing private
  material to public queries or a shared channel.
- **“Where is this file in this folder?”** Use the direct file lookup path. Do not
  turn a known-folder lookup into a global literature review.
- **“Which of these libraries fits our current requirements?”** Read current
  official docs/releases, retain version/date differences and show disagreements
  rather than fill gaps from old memory.

The method discovers usable sources, plans only the queries needed, runs actual
host search/read calls, merges candidates without losing versions or accounts,
checks each required condition, and searches once more only for eligible gaps.
Important claims need read-original locations; snippets remain unconfirmed.
“Not searched,” “access denied,” “tool failed” and “nothing found in the searched
scope” stay different. None means the material does not exist anywhere.

Simple lookups stay small: direct lookup allows up to 2 total queries and 4
external search/read attempts; general exploration 3 and 10; comparison 5 and 18.
Each allows at most one follow-up round, included in those totals. Failed and
cancelled attempts count too. A satisfied question stops early; these are caps,
not minimum work or evidence of better answers. A separate declared deep-research
budget does not silently reset a search plan's cumulative budget.

## What works where

| Surface | Available behavior | Boundary |
| --- | --- | --- |
| **Native Hermes** with enabled OMH plugin, package backend and standard host context | Optional `omh_search` plan/merge/assess local processing, plus existing research guidance | Hermes executes every real search/read, controls tool timeouts/cancellation and checks live authorization; the helper is not a search engine |
| **Native Hermes**, plugin off, helper disabled/missing, backend unavailable, missing host context or full state capacity | Same method as an explicitly reported skill-only fallback using available host tools | No claim that structured helper validation or anchoring ran; conversation accounting is not enforcement |
| **Portable** Agent Skills hosts | Shipped method, exact progressive field readout and complete synthetic payload examples, used with the host's available tools | Native Hermes hook/helper/context transport is not promised; no emulation of authentication or durable accounting |
| **Unsupported** source/connector, no search backend or no current authorization | Explain the missing capability, use supplied material if useful, or request the smallest necessary access | No zero-result fiction, automatic setup, background crawling or invented findings |

An already-connected **AutoRAG MCP** may be one optional search/read source when
its actual tool operations, identity and authorized scope fit the request. OMH
does not install it, require it, add its runtime dependencies, or imply that every
MCP connector is supported or tested. Connection presence is not authorization.
Existing web/file/app tools remain the retrieval owners. Requests to translate
“find this,” quoted requests, and requests to build search software keep their
own workflows; no new Korean trigger list is introduced.

## Privacy and limits

Only the explicitly authorized source/principal/scope belongs in a search. Live
permission must be checked before each tool call and disclosure, including after
revocation. A supplied grant, capability reference or model judgment is an
assertion, never authenticated access, verified execution or factual truth.
Document contents are data, not instructions to change scope or call tools.
No hidden embedding, reranker or Jev call is added; existing turn-specific Jev
consent rules remain intact. Source-trust, citation, historical-receipt and
blocked-source recovery rules still apply. A citation check is not fact checking.

The native helper receives `session_id` and `user_task` through standard host
dispatch, not model-visible arguments. Missing/malformed context fails closed.
Its anchoring is **process-local**, not durable budget or permission enforcement:
digests plus bounded non-content operation flags and a numeric UTC clock floor,
with at most 64 capability fingerprints/flags per plan. No raw identifiers,
tool/account names, capabilities, plans, queries, ledgers or bodies are retained.
Native merge rejects changed bindings/tools and snapshots older than the plan,
intersects planned/current operations, and admits no new capability authority.
Identical host request text is conservatively the same request
within that session/process; caller request IDs do not create a budget reset.
After a process restart, an old plan/assessment cannot resume its lost binding;
a new plan does not prove the previous budget survived. Capacity is bounded to
256 sessions and 32 requests per session, fails closed without eviction and
provides no model reset action. Do not change sessions or request text to bypass
these limits. When accounting is lost, stop or obtain explicit authority rather
than silently restart the budget. Skill prose cannot cancel an in-flight tool.

State advances only after result and complete response serialization pass the
8 MiB output cap. Size/serialization failure claims no state or retry reservation
and preserves the last delivered state, including null before first assessment;
an identical retry fails the same bound, not a hidden revision check. A smaller
valid cumulative payload can continue with the unchanged delivered-ledger prefix.
Never discard actual call observations to fit; report the bound if no valid
representation fits. A refused plan reserves no request slot. These are local
response guarantees, not a guarantee of transport delivery after return.

No default raw-query, private-body, search-log or global-memory retention is
introduced. Existing optional citation artifacts are run-private and task-scoped,
not cross-session memory. Proposed durable knowledge still needs the existing
review/approval path. Searching private material does not authorize publishing
it elsewhere. Host/provider logging is separate from OMH's helper behavior and
must be evaluated against that host/provider's own policy.

## Evidence status

Live quality, installed end-to-end behavior, connector revocation, latency and
cost improvement are **unmeasured**. Offline synthetic examples check contracts,
not real retrieval, model choices, authorization or universal superiority.
Candidate targets—not measured results—are:

- at least **10 percentage points** better complex-search Recall@5, or a frozen
  noninferiority-plus-grounding rule when the baseline is already at least 90%;
- no simple-lookup correctness regression and simple-lookup p95 latency within
  **1.2 times** baseline;
- total median cost within **1.5 times** baseline, with unpriced/missing
  observations kept unknown rather than estimated as measured;
- independently reviewed grounding plus separate citation linkage checks, and
  observed safety/failure/usability/installed tests before feature-complete claims.

The paired evaluation must use authorized real observations on a frozen holdout,
with matched model/tools/scope and explicit exclusions. Synthetic success cannot
pass live gates. More searches or more documents alone is not an improvement.

## Agent/integrator reference

Normal users need only the request examples above. Each of the three existing
skills ships `references/search-method.md`, `references/search-fields.md` and
`references/search-examples.md` in both managed and portable projections. They
resolve relative to the installed skill directory and do not require this source
tree or a machine-local discovery/evidence file. Field definitions are generated
from the maintained producer descriptors; examples are explicitly synthetic and
exercise the actual native handler and pure producers in tests. Load these
references on demand instead of adding all nested fields to the skill body.

Canonical producer APIs remain `build_search_plan(spec, capabilities)`,
`merge_search_candidates(candidate_sets, scope)` and
`assess_search_coverage(plan, observations)`. The tool accepts exactly `action`
and `payload`; merge uses a `search_merge_context/v1` envelope and assess uses
`search_observations/v1`, never a naked grant or ledger array. Pure APIs are
stateless; only the optional native handler adds the limited in-process binding.
