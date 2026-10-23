# visual-engineering-design-skill-harness - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** Existing visual-engineering and web-design work will automatically receive the smallest relevant set of OMH-managed design guidance before normal coding, with integrity checks, repair guidance, executor-neutral carriage, and source-fresh visual evidence. Users will not need a new command or skill name.

**Why this approach:** It reuses OMH’s current generated design skills instead of importing a second ecosystem, and it passes the exact verified guidance bytes transiently so mutable files cannot change between verification and use. Security/readiness metadata stays separate from execution, observed reads, and visual quality claims.

**What it will NOT do:** It will not vendor the upstream corpus or CLI, add network/update behavior, introduce a new user-facing skill or command, broaden unrelated routing, or treat a prepared prompt as proof that guidance was read or applied.

**Effort:** XL
**Risk:** High - the change crosses routing, managed-file integrity, every coding-owner handoff, explicit fanout dispatch, generated guidance, and visual evidence freshness while preserving frozen contracts.
**Decisions to sanity-check:** Structured routing only; verified content delivered transiently rather than reopened from disk; fanout prompt transport moves from argv to stdin without changing its frozen work contract; core installs remain small and unresolved guidance blocks live action with repair instructions.

Your next move: Run the high-accuracy plan review; after it approves, explicitly start execution in a separate implementation run. Full execution detail follows below.

---

> TL;DR (machine): XL/high-risk cross-cutting implementation; add structured required-read contracts, no-follow digest-bound transient bundles, metadata-only readiness/receipts, owner-neutral ordinary and stdin fanout carriage, generated guidance adaptation, revision-bound visual QA, and full adversarial verification without a new public surface or fanout-v2 change.

## Scope
### Must have
- Keep the user surface unchanged: `ulw-work` and the existing structured `visual-engineering` route activate a phase-minimal set of current OMH-managed design skills without a new public command, skill, role, awareness lane, or model category.
- Derive activation only from frozen structured routing data (`model_role=design_visual`, selected category `visual-engineering`, and product-family metadata used only for subtype selection); never hard-gate from raw task prose.
- Define closed, owner-neutral `required_skill_reads/v1`, `required_skill_readiness/v1`, and managed-read receipt contracts with canonical IDs, manifest-relative paths, policy revision, rendered SHA-256, phase, resolution state, repair action, and conservative claim boundaries.
- Resolve files only from the canonical OMH managed-skill root and install manifest; require regular non-symlink files, anchor path traversal beneath the root, read bytes once with no-follow semantics, compare them to freshly rendered expected bytes, and use those exact bytes for the transient prompt bundle.
- Deliver the verified bundle before normal work for Codex, Claude Code, Hermes, OMO/OMX/OMC runtime owners, and generic prompt handoffs. Persist metadata only; classify carriage as `prepared/content_provided`, not reported or observed reading.
- Preserve `fanout_contract/v2` byte-for-byte and shape-for-shape. Derive a sibling readiness artifact at dispatch, change spawnable fanout adapters to send complete prompts through stdin rather than argv, and fail before worktree creation or process spawn when readiness is unresolved.
- Keep executor reports, authenticated read telemetry, guidance application, accessibility evidence, checks, and source-revision-bound visual PASS as distinct states.
- Adapt only independently authored upstream techniques into existing generated design/frontend references: search before build, master plus route/page overrides, variance/motion/density controls, compact anti-patterns, and freshness metadata.
- Bind web visual-QA packages and PASS-eligible captures to an explicit source revision so previous captures become stale after source changes.
- Ship source, generated projections, tests, operator/agent documentation, and evidence in one goal-complete PR with executor-neutral behavior across all supported owners.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do not add a public skill, command, role, awareness/capability family, hidden executor, network service, dependency, Hermes core patch, upstream CLI, CSV/gallery corpus, font asset, installer, or runtime auto-update path.
- Do not modify `FANOUT_CONTRACT_SCHEMA_VERSION`, `FANOUT_CONTRACT_KEYS`, unit fields, canonical payload bytes, or existing `fanout_contract/v2` fixtures.
- Do not activate from generic words such as “design” or “publish”; package/release publishing, design documents, architecture design, docs, copy, API/backend work, and incidental prose are negative controls.
- Do not enlarge the core skill profile merely to satisfy readiness. Core-profile absence yields unresolved readiness plus deterministic `omh setup`/`omh update`/`omh doctor` repair; full installs resolve the selected IDs.
- Do not accept project-local, executor-local, third-party, absolute, symlinked, out-of-root, stale, locally modified, or wrong-digest files as managed hard-gate inputs.
- Do not persist verified skill bytes or complete fanout prompts under `.omh`, journals, summaries, sidecars, or readiness artifacts; never place the prompt or bundle in subprocess argv.
- Do not let an empty advisory `skill_sequence`, missing executor-local discovery, or owner retarget suppress or semantically change required reads.
- Do not claim prepared/content-provided or executor-reported rows prove tool-observed reading, application, accessibility, testing, review, source freshness, or visual quality.
- Do not hand-edit generated `skills/*`, `skills/*/references/*`, or `docs/WORKFLOWS.md`; one task owns catalog source and all regenerated projections.
- Do not copy upstream prose or data records substantially; independent authorship and the pinned research citation are the provenance boundary.
- Do not implement product code while executing this plan document itself; product implementation begins only when a worker is explicitly started on this reviewed plan.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after with Python `unittest`; every behavioral task includes its production change and focused positive/negative tests, generated prose is directly reviewed rather than sentence-pinned.
- Contract checks assert exact JSON keys, schema versions, ordering, digest equality, claim states, return codes, and unchanged frozen fanout fixtures.
- Integrity checks exercise missing, stale, modified, symlinked, out-of-root, wrong-digest, and concurrent-replacement inputs without sleeps; the test replaces the file from an explicit hook between resolution and bundle construction.
- Owner-matrix checks compare semantic IDs, phase, policy revision, and digests rather than owner-specific prompt syntax.
- Browser QA is agent-executed at fixed desktop and narrow viewports, imports real captures, and proves matching-revision PASS plus changed-revision HOLD.
- Evidence: <attemptDir>/task-<N>-visual-engineering-design-skill-harness.<ext> (attemptDir = currentAttemptDir from 'omo-agent-toolkit ulw-loop status --json', .omo/evidence/ulw/<session>/<goalId>/a<attempt>; outside ulw-loop use .omo/evidence/)

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.
- **Wave 1 — independent foundations:** Todos 1-3.
- **Wave 2 — contracts and secure transport primitives:** Todos 4-8 after their listed Wave-1 dependencies.
- **Wave 3 — handoff/dispatch integration and generated behavior:** Todos 9-12.
- **Wave 4 — cross-surface, adversarial, and real-browser closure:** Todos 13-15.
- **Final verification wave:** F1-F4 run in parallel only after Todos 1-15 are green. Any rejection returns to the owning todo; no final task self-approves.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | 4, 5, 6, 7, 9, 10, 13, 14 | 2, 3 |
| 2 | — | 4, 11 | 1, 3 |
| 3 | — | 12, 15 | 1, 2 |
| 4 | 1, 2 | 9, 10, 13, 14 | 5, 6, 7, 8 |
| 5 | 1 | 9, 10, 13, 14 | 4, 6, 7, 8 |
| 6 | 1 | 9, 13 | 4, 5, 7, 8 |
| 7 | 1 | 9, 10, 13, 14 | 4, 5, 6, 8 |
| 8 | 1 | 10, 14 | 4, 5, 6, 7 |
| 9 | 4, 5, 6, 7 | 13 | 10, 11, 12 |
| 10 | 4, 5, 7, 8 | 14 | 9, 11, 12 |
| 11 | 2, 4 | 13 | 9, 10, 12 |
| 12 | 3 | 15 | 9, 10, 11 |
| 13 | 9, 11 | F1-F4 | 14, 15 |
| 14 | 10 | F1-F4 | 13, 15 |
| 15 | 12 | F1-F4 | 13, 14 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [ ] 1. Define the structured required-skill policy and closed contract
  What to do / Must NOT do: Add `src/coding/required_skill_reads.py` as the single source for `required_skill_reads/v1`, deterministic activation, phase ordering, semantic digesting, and strict validation. Activate only when normalized structured routing has `model_role=design_visual` or selected category `visual-engineering`; use product family only to select an existing specialist. Map direction to `design-orchestration` plus the selected specialist, UI implementation to `frontend` plus `design-quality-gate`, and rendered acceptance to `accessibility-audit` plus `visual-qa`. Encode rows as canonical skill ID, managed-manifest-relative `SKILL.md` path, phase (`direction`, `implementation`, `acceptance`), source=`managed_skill`, resolution, and rendered SHA-256; top-level fields are `schema_version`, `policy_revision`, `activation_basis`, `status`, ordered `reads`, `repair`, and `claim_boundary`. Reject unknown/duplicate IDs, absolute paths, phase/order drift, extra keys, and `api`/nonvisual activation. Add schema constants to `src/coding/coding_contracts.py`. Must NOT inspect raw task prose or change existing model routing.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 4, 5, 6, 7, 9, 10, 13, 14
  References: `.omo/research/visual-engineering-design-skill-harness/RESEARCH-DOSSIER.md:117-129`; `.omo/research/visual-engineering-design-skill-harness/SYNTHESIS.md:3-7`; `src/coding/model_routing.py:191-203,251-251`; `src/coding/product_family_templates.py:5-36,39-73`; `src/coding/coding_contracts.py:1-45`; `tests/test_model_routing.py`; `tests/test_product_family_templates.py`
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_required_skill_reads.py tests/test_model_routing.py tests/test_product_family_templates.py -v` exits 0; exact JSON-key tests reject extras and assert stable ordering/digests; structured `design_visual`/`visual-engineering` cases return the phase-minimal mapping while API/backend/docs/copy/design-document/architecture-design/package-publishing cases return no required reads and preserve sibling route ownership.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_required_skill_reads.RequiredSkillReadsTests.test_visual_engineering_web_mapping -v`; assert exit 0 and write the validated contract JSON to `<attemptDir>/task-1-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_required_skill_reads.RequiredSkillReadsTests.test_nonvisual_and_prose_only_cases_do_not_activate -v`; assert exit 0 and that all negative controls return an empty required-read contract without changing the selected category.
  Commit: Y | `feat(coding): define required managed skill read policy`
  Recommended task executor category: `unspecified-high`

- [ ] 2. Adapt upstream design techniques into existing generated guidance
  What to do / Must NOT do: Independently author compact, progressive guidance in the existing `design-orchestration`, `design-quality-gate`, `frontend`, `accessibility-audit`, and `visual-qa` `SkillDefinition` bodies in `src/skills/catalog_definitions.py`; align their existing `HarnessDefinition` acceptance/contracts in `src/skills/catalog_harnesses.py` only where the new guidance changes a machine-consumed gate. Cover search-before-build, a master system with explicit page/route overrides, restrained variance/motion/density controls, accessibility/responsive anti-patterns, and factual freshness/source-review metadata. Regenerate every affected `skills/*/SKILL.md`, `skills/*/references/*.md`, and `docs/WORKFLOWS.md` from source in this same task. Must NOT edit `src/skills/catalog_feature_surfaces.py` for these skills, vendor upstream prose, CSV/gallery rows, CLI/runtime code, assets, fonts, installer/update behavior, dependencies, or a new skill definition.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 4, 11, 13
  References: `.omo/research/visual-engineering-design-skill-harness/RESEARCH-DOSSIER.md:33-45,65-89`; `.omo/research/visual-engineering-design-skill-harness/research-manifest.json`; `src/skills/catalog_definitions.py:2560-3125`; `src/skills/catalog_harnesses.py:555-900,2332-2400`; `src/skills/render.py`; `AGENTS.md:137-138,169-181`; upstream pin `a38d04c3d5c298c851dbe5e6ee1965ee3de42cb5`
  Acceptance criteria: `uv run python -m omh.cli docs workflows` regenerates source projections; `uv run python -m omh.cli docs workflows --check`, `uv run python -m omh.cli docs skill-context-cost`, and `PYTHONPATH=tests uv run python -m unittest tests/test_skill_context_cost.py tests/test_skill_governance.py tests/test_skill_pattern_risk_review.py -v` all exit 0; direct review confirms independent wording, progressive disclosure, no new skill ID, no upstream corpus/asset, and no network/update instruction.
  QA happy: Run `uv run python -m omh.cli docs workflows --check` and `uv run python -m omh.cli docs skill-context-cost`; capture exit codes, changed generated paths, and context-cost totals in `<attemptDir>/task-2-visual-engineering-design-skill-harness.json`.
  QA failure: Add and run a source-level regression in `tests/test_skill_governance.py` that feeds a forbidden upstream runtime/auto-update or raw-corpus marker into the design feature-surface policy and asserts rejection; run `PYTHONPATH=tests uv run python -m unittest tests.test_skill_governance -v` with exit 0.
  Commit: Y | `feat(skills): enrich generated design guidance`
  Recommended task executor category: `writing`

- [ ] 3. Bind web visual-QA contracts to source revision
  What to do / Must NOT do: Extend package, capture, and result contracts in `src/workflows/web_visual_qa_contracts.py` with one normalized source identity/revision and update `src/workflows/web_visual_qa.py` so PASS eligibility requires every capture and verdict to match the current package revision. Rebinding or changing the revision marks prior captures stale and produces HOLD until fresh evidence is imported. Carry the field through existing CLI/channel/message-card projections without coupling it to managed-skill readiness. Must NOT infer revisions from prose, file mtimes, or screenshot names, and must not let a missing revision reach PASS.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 12, 15
  References: `.omo/research/visual-engineering-design-skill-harness/RESEARCH-DOSSIER.md:142-165`; `src/workflows/web_visual_qa.py:35-91,184-272`; `src/workflows/web_visual_qa_contracts.py:14-37,110-128,194-259`; `tests/test_web_visual_qa.py`; `tests/test_web_visual_qa_capture_file_safety.py`; `tests/test_web_visual_qa_channel_delivery.py`
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_web_visual_qa.py tests/test_web_visual_qa_capture_file_safety.py tests/test_web_visual_qa_channel_delivery.py tests/test_web_visual_qa_message_card.py -v` exits 0; exact-schema tests reject missing/extra/mismatched revision fields; a completed `rev-a` package can PASS, then rebinding to `rev-b` makes all `rev-a` captures stale and prevents PASS.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_web_visual_qa.WebVisualQaTests.test_matching_source_revision_can_pass -v`; assert exit 0 and save the resulting package JSON to `<attemptDir>/task-3-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_web_visual_qa.WebVisualQaTests.test_changed_source_revision_invalidates_prior_captures -v`; assert exit 0, stale capture state, HOLD verdict, and no PASS field.
  Commit: Y | `feat(visual-qa): bind captures to source revision`
  Recommended task executor category: `visual-engineering`

- [ ] 4. Build the no-follow verified transient skill bundle
  What to do / Must NOT do: Add `src/coding/managed_skill_bundle.py` to resolve only install-manifest-owned canonical skills beneath the managed root. Anchor traversal with a directory descriptor; open every relative component with no-follow semantics, require a regular file, read bytes exactly once, freshly render expected bytes from catalog source, compare SHA-256, and build the ordered phase-minimal in-memory prompt bundle from those same verified bytes. Return metadata separately from transient bytes. Expose deterministic errors and repair actions for missing, stale, modified, symlinked, out-of-root, wrong-digest, and concurrent-replacement states. Must NOT reopen a mutable path after hashing, accept executor/project-local skills, persist bundle bytes, or add a fallback that weakens the gate.
  Parallelization: Wave 2 | Blocked by: 1, 2 | Blocks: 9, 10, 13, 14
  References: `src/install/installer.py:355-394,776-841`; `src/install/guidance_projection.py:3-19,96-173`; `src/skills/render.py`; `src/coding/required_skill_reads.py` from Todo 1; `tests/test_guidance_projection.py`; `tests/test_skill_install_layout.py`
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_managed_skill_bundle.py tests/test_guidance_projection.py tests/test_skill_install_layout.py -v` exits 0; bundle metadata contains only IDs, manifest-relative paths, phases, policy revision, and digests; test hooks replace a source between resolution and read without sleeps and prove the emitted bytes either match the verified digest or construction fails closed.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_managed_skill_bundle.ManagedSkillBundleTests.test_full_profile_builds_ordered_verified_bundle -v`; assert exit 0, byte/digest equality, and write only metadata to `<attemptDir>/task-4-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_managed_skill_bundle.ManagedSkillBundleTests.test_integrity_matrix_fails_closed_before_bundle -v`; assert exit 0 for missing, stale, modified, symlink, escape, wrong-digest, and replacement cases, with no bundle bytes returned.
  Commit: Y | `feat(coding): verify managed skill prompt bundles`
  Recommended task executor category: `deep`

- [ ] 5. Define metadata-only readiness artifacts and action gates
  What to do / Must NOT do: Add `required_skill_readiness/v1` validation/persistence in `src/coding/required_skill_readiness.py` and a sibling managed readiness root in `src/system/paths.py`. Key records by owner, policy revision, required-read semantic digest, and—when fanout applies—fanout/unit IDs. Persist only canonical IDs, manifest-relative paths, phases, digests, resolution states, deterministic repair commands, dispatchability, and claim boundary. Allow ordinary preparation to return unresolved readiness with repair guidance, but require every live action gate to reject unresolved rows. Must NOT store prompt/bundle bytes, absolute host paths, or convert repair preparation into execution evidence.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 9, 10, 13, 14
  References: `.omo/research/visual-engineering-design-skill-harness/RESEARCH-DOSSIER.md:92-116,131-140`; `src/system/paths.py:54-70,248-255`; `src/coding/fanout_artifacts.py:62-107,176-268`; `src/coding/coding_contracts.py:1-45`; `src/coding/required_skill_reads.py` from Todo 1
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_required_skill_readiness.py -v` exits 0; exact-key validation rejects raw bytes/absolute paths/extras; resolved rows are dispatchable, unresolved rows remain preparable but non-dispatchable, and serialized artifacts round-trip with stable semantic digests.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_required_skill_readiness.RequiredSkillReadinessTests.test_resolved_metadata_round_trip -v`; assert exit 0 and save the metadata artifact to `<attemptDir>/task-5-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_required_skill_readiness.RequiredSkillReadinessTests.test_unresolved_readiness_blocks_action_and_keeps_repair -v`; assert exit 0, `dispatchable=false`, one deterministic repair action, and no execution/observed claim.
  Commit: Y | `feat(coding): persist managed skill readiness metadata`
  Recommended task executor category: `unspecified-high`

- [ ] 6. Preserve required-read semantics across owner retargeting
  What to do / Must NOT do: Extend the owner-neutral `coding_task_contract/v1` and retarget validation so the required-read schema version, policy revision, canonical ordered IDs/phases, and semantic digest are preserved. A new owner may re-resolve its managed root, but only when canonical IDs and freshly rendered digests remain identical; any policy/order/phase/digest change is `replan_required`. Carry the contract for Codex, Claude Code, Hermes, runtime, and generic owners without adding owner syntax to semantic fields. Must NOT treat owner-local path changes as permission to alter skills or let retargeting downgrade unresolved readiness.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 9, 13
  References: `src/coding/owner_retarget.py:75-94,151-218`; `src/coding/coding_contracts.py:1-45`; `src/coding/coding_delegation.py:748-818`; `tests/test_coding_retarget.py`
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_coding_retarget.py -v` exits 0; each supported owner preserves the same required-read semantic digest; changed IDs/order/phase/policy/digest and resolved-to-unresolved downgrade require replanning while owner-specific resolved paths never enter the preserved task contract.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_coding_retarget.CodingRetargetTests.test_required_skill_semantics_survive_owner_change -v`; assert exit 0 and store before/after semantic JSON in `<attemptDir>/task-6-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_coding_retarget.CodingRetargetTests.test_required_skill_drift_requires_replan -v`; assert exit 0 and exact `replan_required` reasons for ID, order, phase, policy, and digest drift.
  Commit: Y | `feat(coding): preserve skill reads through retargeting`
  Recommended task executor category: `unspecified-high`

- [ ] 7. Add conservative managed-read receipt contracts
  What to do / Must NOT do: Add a shared receipt validator in `src/coding/managed_skill_read_receipts.py` and attach optional `managed_skill_reads` rows to existing owner completion/capability surfaces and `fanout_unit_result/v1`. Key rows by canonical skill ID and expected digest; allow only `missing`, `content_provided`, `executor_reported_read`, and `tool_observed_read` with explicit provenance. Promote to observed only from authenticated matching file-read telemetry. Partial, malformed, unknown-ID, wrong-digest, or conflicting receipts keep readiness unresolved but do not rewrite process exit/check evidence. Must NOT add `applied`, accessibility, review, or visual-PASS implications.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 9, 10, 13, 14
  References: `src/coding/fanout_unit_results.py:1-22,67-91,173-207`; `src/coding/executor_capabilities.py:41-113,162-246`; `src/coding/coding_contracts.py:1-45`; `tests/test_fanout_unit_results.py`; `tests/test_executor_skill_discovery.py`
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_managed_skill_read_receipts.py tests/test_fanout_unit_results.py tests/test_executor_skill_discovery.py -v` exits 0; exact provenance tests distinguish all four states; only authenticated matching telemetry yields `tool_observed_read`; no receipt changes process/check exit evidence or produces an applied/quality claim.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_managed_skill_read_receipts.ManagedSkillReadReceiptTests.test_matching_report_and_telemetry_have_distinct_states -v`; assert exit 0 and write classified rows to `<attemptDir>/task-7-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_managed_skill_read_receipts.ManagedSkillReadReceiptTests.test_invalid_receipt_matrix_never_promotes -v`; assert exit 0 for missing, malformed, partial, unknown, wrong-digest, and unauthenticated cases.
  Commit: Y | `feat(coding): classify managed skill read receipts`
  Recommended task executor category: `deep`

- [ ] 8. Move fanout prompt delivery from argv to stdin
  What to do / Must NOT do: Refactor every spawnable adapter in `src/coding/fanout_dispatch.py` so command templates contain only fixed command options plus model/effort metadata, and deliver the complete unit prompt through subprocess stdin. Add an explicit stdin payload to dry-run/launch records without persisting its bytes. Keep shell-free argv lists and existing local-only explicit dispatch semantics. Must NOT place `{prompt}` or any managed bundle sentinel in argv, command previews, journals, dispatch summaries, sidecars, or `.omh`; do not alter `fanout_contract/v2`.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 10, 14
  References: `src/coding/fanout_dispatch.py:63-92,177-259,855-1143`; `src/coding/fanout_contracts.py:6-35`; `tests/test_fanout_dispatch.py`; `AGENTS.md:110-118`
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_fanout_dispatch.py tests/test_fanout_contract.py -v` exits 0; adapter tests find a sentinel in captured stdin and nowhere in argv or persisted artifacts; existing fanout contract fixtures and canonical JSON remain byte-identical.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_fanout_dispatch.FanoutDispatchTests.test_all_spawnable_adapters_send_prompt_via_stdin -v`; assert exit 0 and save redacted argv/stdin-presence metadata to `<attemptDir>/task-8-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_fanout_dispatch.FanoutDispatchTests.test_prompt_and_bundle_never_persist_or_enter_argv -v`; assert exit 0 after scanning command previews, journals, summaries, sidecars, and readiness JSON for the sentinel.
  Commit: Y | `fix(fanout): deliver dispatch prompts through stdin`
  Recommended task executor category: `unspecified-high`

- [ ] 9. Carry verified required reads through ordinary handoffs
  What to do / Must NOT do: Integrate policy resolution, secure bundle construction, readiness, and receipt expectations into `src/coding/coding_delegation.py` for executor, runtime, and prompt-handoff branches. Attach the owner-neutral contract next to existing role/product-family context, render the transient verified content before normal work in `src/coding/prompting.py`, and make run-backed live actions reject unresolved readiness. Prompt-only preparation may still emit a repairable non-dispatchable handoff. Ensure mandatory reads render independently of advisory executor-local `skill_sequence`. Must NOT describe preparation as execution, vary semantic IDs by owner, or suppress required reads when discovery is empty/unsupported.
  Parallelization: Wave 3 | Blocked by: 4, 5, 6, 7 | Blocks: 13
  References: `src/coding/coding_delegation.py:430-560,748-818,900-1035`; `src/workflows/role_context_packs.py:1-210`; `src/coding/prompting.py:199-231`; `src/coding/owner_retarget.py:151-218`; `tests/test_coding_delegation.py`; `tests/test_prepared_runtime_run_executor_matrix.py`; `tests/test_executor_skill_discovery.py`
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_coding_delegation.py tests/test_coding_retarget.py tests/test_prepared_runtime_run_executor_matrix.py tests/test_executor_skill_discovery.py -v` exits 0; Codex run-backed, Claude Code prompt-only, Hermes runtime, OMO/OMX/OMC runtime, and generic prompt handoffs receive equivalent IDs/phases/digests/status; required content precedes normal work and survives empty advisory discovery; unresolved run-backed actions fail before launch.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_prepared_runtime_run_executor_matrix.PreparedRuntimeRunExecutorMatrixTests.test_visual_required_reads_are_semantically_equivalent -v`; assert exit 0 and store redacted owner-matrix metadata in `<attemptDir>/task-9-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_coding_delegation.CodingDelegationTests.test_unresolved_required_read_blocks_live_action_but_preserves_repair_handoff -v`; assert exit 0, no launch evidence, `dispatchable=false`, and prepared-only repair.
  Commit: Y | `feat(coding): carry verified design guidance in handoffs`
  Recommended task executor category: `deep`

- [ ] 10. Gate fanout dispatch with sibling readiness and verified bundles
  What to do / Must NOT do: In `src/coding/fanout_dispatch.py`, derive required reads only from frozen unit role/category/product-family metadata, build the verified transient bundle, and write a separate metadata-only readiness artifact under the new sibling root. Insert required content before unit protocol and advisory skills, send it through Todo 8 stdin transport, and validate managed-read receipts on completion. Run preflight before worktree preparation, prompt dispatch, spawn, or success-summary persistence; unresolved or wrong-digest readiness returns a deterministic repair error. Must NOT mutate contract units, canonical payloads, schema keys/version, or report a failed preflight as dispatched.
  Parallelization: Wave 3 | Blocked by: 4, 5, 7, 8 | Blocks: 14
  References: `src/coding/fanout_contracts.py:6-35,124-205`; `src/coding/fanout_dispatch.py:220-259,600-860,855-1143`; `src/coding/fanout_artifacts.py:62-107,176-268`; `src/coding/fanout_unit_results.py:67-91,173-207`; `tests/test_fanout_contract.py`; `tests/test_fanout_dispatch.py`; `tests/test_fanout_unit_results.py`
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_fanout_contract.py tests/test_fanout_dispatch.py tests/test_fanout_unit_results.py -v` exits 0; frozen `fanout_contract/v2` equality/shape fixtures are unchanged; visual dry-run creates a separate readiness artifact with fanout/unit/owner/policy/IDs/digests and no bytes; missing/wrong-digest readiness prevents worktree creation and spawn.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_fanout_dispatch.FanoutDispatchTests.test_visual_unit_emits_separate_ready_artifact_and_stdin_bundle -v`; assert exit 0 and save readiness metadata plus unchanged contract digest to `<attemptDir>/task-10-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_fanout_dispatch.FanoutDispatchTests.test_integrity_failure_precedes_worktree_and_spawn -v`; assert exit 0, zero worktree/spawn calls, deterministic repair, and no dispatched/success row.
  Commit: Y | `feat(fanout): gate visual units on managed readiness`
  Recommended task executor category: `deep`

- [ ] 11. Enforce generated projection freshness and install-profile behavior
  What to do / Must NOT do: Integrate the verified bundle authority with existing installer/guidance-projection state so freshly rendered catalog bytes are the expected digest and the install manifest is the ownership/path authority. Extend tests for current, missing, stale-render, locally modified, and unregistered states. Preserve the intentionally small core profile: missing design guidance in core installs produces unresolved readiness and explicit setup/update/full-profile repair, while a full install resolves all selected existing IDs. Re-run and commit every generated projection from Todo 2 with its source. Must NOT make executor-local discovery authoritative, expand `CORE_SKILLS`/`CORE_PROFILE_SKILLS`, or hide local modification behind automatic repair.
  Parallelization: Wave 3 | Blocked by: 2, 4 | Blocks: 13
  References: `src/install/installer.py:355-394,776-841`; `src/install/guidance_projection.py:3-19,96-173`; `src/skills/catalog.py:1139-1164`; `src/skills/render.py`; `tests/test_guidance_projection.py`; `tests/test_installer_skill_profile.py`; `tests/test_skill_install_layout.py`
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_guidance_projection.py tests/test_installer_skill_profile.py tests/test_skill_install_layout.py -v` exits 0; core-profile tests return unresolved readiness with deterministic repair and no profile mutation; full-profile tests resolve all mapped design IDs to current renderer digests; `uv run python -m omh.cli docs workflows --check` exits 0.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_installer_skill_profile.InstallerSkillProfileTests.test_full_profile_resolves_visual_required_reads -v`; assert exit 0 and record IDs/digests in `<attemptDir>/task-11-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_installer_skill_profile.InstallerSkillProfileTests.test_core_profile_reports_repair_without_expansion -v`; assert exit 0, unchanged core profile, prepared-only repair, and non-dispatchable readiness.
  Commit: Y | `test(installer): lock managed design readiness profiles`
  Recommended task executor category: `unspecified-high`

- [ ] 12. Carry source revision through visual-QA workflow surfaces
  What to do / Must NOT do: Wire Todo 3’s source revision through web visual-QA package creation, capture import, criteria completion, message cards, channel delivery, and serialized status. Require explicit revision on machine-consumed entry points; present stale evidence and HOLD reasons conservatively. Preserve existing capture-file safety and generated evidence boundaries. Must NOT derive source identity from mutable file metadata, conflate required-skill readiness with visual freshness, or add owner interaction.
  Parallelization: Wave 3 | Blocked by: 3 | Blocks: 15
  References: `src/workflows/web_visual_qa.py:35-91,184-272`; `src/workflows/web_visual_qa_contracts.py:110-128,194-259`; `src/commands/web_qa.py`; `tests/test_web_visual_qa.py`; `tests/test_web_visual_qa_capture_file_safety.py`; `tests/test_web_visual_qa_channel_delivery.py`; `tests/test_web_visual_qa_message_card.py`
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_web_visual_qa.py tests/test_web_visual_qa_capture_file_safety.py tests/test_web_visual_qa_channel_delivery.py tests/test_web_visual_qa_message_card.py -v` exits 0; JSON and message-card projections preserve exact revision and stale/HOLD state; file-safety regressions remain green.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_web_visual_qa_channel_delivery.WebVisualQaChannelDeliveryTests.test_matching_revision_delivery_reports_current_pass -v`; assert exit 0 and store the rendered machine card in `<attemptDir>/task-12-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_web_visual_qa_channel_delivery.WebVisualQaChannelDeliveryTests.test_stale_revision_delivery_never_reports_pass -v`; assert exit 0, explicit stale reason, and HOLD status.
  Commit: Y | `feat(visual-qa): carry revision freshness across surfaces`
  Recommended task executor category: `visual-engineering`

- [ ] 13. Prove owner neutrality, routing precision, and profile compatibility
  What to do / Must NOT do: Add a cross-surface integration matrix covering Codex run-backed, Claude Code prompt-only, Hermes runtime, OMO/OMX/OMC runtime, and generic prompt owners plus web/mobile/desktop subtypes. Assert equivalent semantic IDs/phases/digests/readiness/claim boundaries, owner-specific carriage only, stable retargeting, and no suppression by empty advisory discovery. Add positive structured activation and negative precision cases to existing routing corpora and test that package publishing, design documents, architecture design, docs/copy, API/backend, and incidental wording never steal routing. Repair only defects found in Todos 1, 4, 9, and 11; do not invent new abstractions.
  Parallelization: Wave 4 | Blocked by: 9, 11 | Blocks: F1-F4
  References: `src/coding/coding_delegation.py:748-818,969-982`; `src/coding/model_routing.py:191-203,251-251`; `src/quality/routing_precision.py:38-518,2141-2144`; `tests/test_prepared_runtime_run_executor_matrix.py`; `tests/test_router_content.py`; `tests/test_model_routing.py`; `tests/test_executor_skill_discovery.py`; `AGENTS.md:129-132,153-156`
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_prepared_runtime_run_executor_matrix.py tests/test_router_content.py tests/test_model_routing.py tests/test_executor_skill_discovery.py -v` exits 0; every owner has the same semantic contract and conservative evidence state; all negative controls retain their prior route/owner; core/full profile expectations remain explicit.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_prepared_runtime_run_executor_matrix.PreparedRuntimeRunExecutorMatrixTests.test_visual_required_read_owner_matrix -v`; assert exit 0 and save the semantic comparison table to `<attemptDir>/task-13-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_router_content.RouterContentTests.test_visual_required_read_negative_controls -v`; assert exit 0 and unchanged sibling-route ownership for every negative corpus case.
  Commit: Y | `test(coding): prove visual guidance owner neutrality`
  Recommended task executor category: `unspecified-high`

- [ ] 14. Close fanout security, persistence, and receipt scenarios end to end
  What to do / Must NOT do: Add an end-to-end fanout test matrix for structured visual units, frozen contract equality, metadata-only readiness, no-follow integrity, stdin bundle carriage, argv/persistence absence, pre-worktree failure ordering, and receipt classification. Include missing, stale, local modification, symlink, path escape, wrong digest, concurrent replacement, malformed/partial/wrong-digest reports, and authenticated observed telemetry. Verify no failed preflight creates a worktree, process, dispatched row, or success summary, and no receipt rewrites process/check exit evidence. Must NOT weaken existing frozen fixtures or use timing sleeps/polling.
  Parallelization: Wave 4 | Blocked by: 10 | Blocks: F1-F4
  References: `src/coding/fanout_contracts.py:6-35`; `src/coding/fanout_dispatch.py:63-92,177-259,600-860,855-1143`; `src/coding/fanout_unit_results.py:67-91,173-207`; `tests/test_fanout_contract.py`; `tests/test_fanout_dispatch.py`; `tests/test_fanout_unit_results.py`; `AGENTS.md:110-118`
  Acceptance criteria: `PYTHONPATH=tests uv run python -m unittest tests/test_fanout_contract.py tests/test_fanout_dispatch.py tests/test_fanout_unit_results.py -v` exits 0 once; exact existing contract payload fixture/digest is unchanged; sentinel scans prove prompt/bundle absence from argv and all persisted artifacts; integrity failures occur before worktree/spawn; only authenticated matching telemetry is observed.
  QA happy: Run `PYTHONPATH=tests uv run python -m unittest tests.test_fanout_dispatch.FanoutDispatchTests.test_visual_fanout_end_to_end_readiness_and_receipt -v`; assert exit 0 and save redacted contract/readiness/result metadata to `<attemptDir>/task-14-visual-engineering-design-skill-harness.json`.
  QA failure: Run `PYTHONPATH=tests uv run python -m unittest tests.test_fanout_dispatch.FanoutDispatchTests.test_visual_fanout_security_failure_matrix -v`; assert exit 0, zero side effects, deterministic repair, no raw content persistence, and no false observed/applied/PASS state.
  Commit: Y | `test(fanout): close managed guidance security matrix`
  Recommended task executor category: `deep`

- [ ] 15. Exercise revision-bound visual QA through the real browser surface
  What to do / Must NOT do: Use agent-executed browser automation against a deterministic local design-direction preview at fixed desktop and narrow viewports. Capture the real page for source revision `rev-a`, import captures into the web visual-QA workflow, complete machine-consumed criteria, and observe PASS. Rebind the package to `rev-b` without new captures and observe stale/HOLD; then capture `rev-b` and prove PASS can recover. Save screenshots and package JSON under the task evidence path and tear down browser/server resources. Must NOT ask the user to click/approve, reuse stale captures, use sleeps, or substitute source inspection for real rendering.
  Parallelization: Wave 4 | Blocked by: 12 | Blocks: F1-F4
  References: `src/workflows/web_visual_qa.py:35-91,184-272`; `src/workflows/web_visual_qa_contracts.py:110-128,194-259`; `tests/test_web_visual_qa.py`; `tests/test_web_visual_qa_capture_file_safety.py`; `.omo/research/visual-engineering-design-skill-harness/qa_research_dossier.py`
  Acceptance criteria: The agent driver exits 0; desktop and narrow screenshots exist for `rev-a` and `rev-b`; package JSON records matching source revision for each PASS; the intermediate `rev-b` state with `rev-a` captures is stale/HOLD and cannot PASS; browser, context, local server, and port are confirmed closed.
  QA happy: Run the task’s bounded Playwright driver with `rev-a`, then fresh `rev-b`; assert exit 0 and store screenshots plus final package JSON under `<attemptDir>/task-15-visual-engineering-design-skill-harness/`.
  QA failure: Run the same driver’s `--reuse-stale-captures` scenario after rebinding `rev-a` to `rev-b`; assert exit 0 for the test, exact HOLD/stale state, rejected PASS, and a cleanup receipt.
  Commit: Y | `test(visual-qa): exercise source revision in browser`
  Recommended task executor category: `visual-engineering`

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
  What to do / Must NOT do: Independently map every Must have, Must NOT have, Todo 1-15 acceptance criterion, evidence path, and exact schema decision to the implemented diff and observed command output. Reject placeholders, skipped/only tests, unimplemented branches, missing evidence, or claims supported only by prepared/reported state. Must NOT accept green unrelated tests as coverage.
  Parallelization: Final wave | Blocked by: 13, 14, 15 | Blocks: completion
  References: this plan; `.omo/research/visual-engineering-design-skill-harness/RESEARCH-DOSSIER.md`; `.omo/research/visual-engineering-design-skill-harness/SYNTHESIS.md`; `AGENTS.md`; all task evidence under `<attemptDir>/`
  Acceptance criteria: Independent reviewer returns APPROVE with a requirement-to-file/test/evidence table; every planned contract and negative control has direct evidence; no unresolved P0/P1 finding remains.
  QA happy: Run the final plan-compliance reviewer against the complete diff and evidence ledger; store the APPROVE report at `<attemptDir>/task-F1-visual-engineering-design-skill-harness.md`.
  QA failure: Seed the review input with one missing task evidence reference and verify the reviewer returns REJECT rather than inferring completion; remove the seed before final APPROVE.
  Commit: N | review-only final gate
  Recommended task executor category: `deep`

- [ ] F2. Code quality and security review
  What to do / Must NOT do: Review strict schema parsing, path anchoring/no-follow behavior, byte/digest authority, transient data lifetime, argv/stdin handling, artifact persistence, fail-closed ordering, retarget invariants, receipt provenance, and source-revision state transitions. Inspect changed symbols with LSP references and run diagnostics before build. Must NOT approve broad abstractions, fallback weakening, hidden network behavior, raw content persistence, or unrelated cleanup.
  Parallelization: Final wave | Blocked by: 13, 14, 15 | Blocks: completion
  References: changed `src/coding/*`, `src/install/*`, `src/skills/*`, `src/workflows/web_visual_qa*`, `src/system/paths.py`, and corresponding tests; `AGENTS.md:110-138`
  Acceptance criteria: Independent reviewer returns APPROVE with zero blocking correctness/security findings; `uv run --group lint ruff check src tests`, `uv run python -m compileall -q src tests`, and `git diff --check` each exit 0.
  QA happy: Run LSP diagnostics over every changed Python module, then lint/compile/diff checks; save commands, exit codes, and APPROVE report at `<attemptDir>/task-F2-visual-engineering-design-skill-harness.md`.
  QA failure: Run the reviewer against an isolated synthetic fixture containing one absolute-path readiness row and verify it is rejected; do not alter product files for this control.
  Commit: N | review-only final gate
  Recommended task executor category: `deep`

- [ ] F3. Real manual QA and full regression gate
  What to do / Must NOT do: Re-run the matching real surfaces: ordinary owner matrix, fanout dry-run and blocked live preflight, stdin sentinel capture, full-profile readiness, core-profile repair, and revision-bound browser PASS/HOLD/PASS. Then run the full repository unittest suite once. Must NOT use fixed sleeps, reuse stale browser evidence, or omit cleanup receipts.
  Parallelization: Final wave | Blocked by: 13, 14, 15 | Blocks: completion
  References: Todos 13-15 evidence; `tests/test_prepared_runtime_run_executor_matrix.py`; `tests/test_fanout_dispatch.py`; `tests/test_web_visual_qa.py`; repository Verification commands in `AGENTS.md`
  Acceptance criteria: Every real-surface scenario matches the plan; `PYTHONPATH=tests uv run python -m unittest discover -s tests -v` exits 0 in one run; browser/context/server/port are closed; no child/background process remains.
  QA happy: Execute ordinary handoff, fanout dry-run, and browser revision scenarios plus the full suite; save redacted outputs/screenshots/cleanup receipts at `<attemptDir>/task-F3-visual-engineering-design-skill-harness/`.
  QA failure: Execute blocked fanout integrity and stale visual-revision cases; assert zero spawn/worktree side effects and HOLD/no-PASS respectively, with exit 0 for the QA driver.
  Commit: N | verification-only final gate
  Recommended task executor category: `unspecified-high`

- [ ] F4. Scope fidelity and generated-artifact audit
  What to do / Must NOT do: Compare the final diff against the research adopt/adapt/reject table and plan guardrails. Confirm one goal-complete PR, no new public skill/command/role/capability/awareness lane/dependency/network path, no fanout-v2 drift, no copied upstream corpus, no core-profile inflation, and no hand-edited generated projection. Review public/user wording for executor neutrality and backend-command audience. Must NOT approve hidden follow-up capability gaps.
  Parallelization: Final wave | Blocked by: 13, 14, 15 | Blocks: completion
  References: `.omo/research/visual-engineering-design-skill-harness/RESEARCH-DOSSIER.md:63-89`; `.omo/research/visual-engineering-design-skill-harness/SYNTHESIS.md:9-34`; `AGENTS.md:17-70,124-138,169-181`; final `git diff --stat` and `git diff`
  Acceptance criteria: Independent reviewer returns APPROVE; generated checks (`uv run python -m omh.cli docs workflows --check`, `uv run python -m omh.cli docs roles --check`, `uv run python -m omh.cli docs capability-families --check`, `uv run python -m omh.cli docs ulw-inventory --check`, `uv run python -m omh.cli docs ulw-site --check`) all exit 0; frozen fanout contract fixture/digest is unchanged.
  QA happy: Run generated checks and scope reviewer over the exact final diff; store APPROVE report and path/category inventory at `<attemptDir>/task-F4-visual-engineering-design-skill-harness.md`.
  QA failure: Compare the diff against explicit forbidden path/category patterns and prove a synthetic new-skill/dependency/fanout-schema change would trigger REJECT; remove synthetic input before APPROVE.
  Commit: N | review-only final gate
  Recommended task executor category: `deep`

## Commit strategy
- Deliver one PR for the full user goal; do not split research, harness, guidance, visual freshness, or review fixes into follow-up PRs.
- Use focused, green, DCO-signed commits in dependency order: policy/contracts; generated guidance; visual-QA schema; integrity/readiness; retarget/receipts; stdin transport; ordinary/fanout integration; profile/generated checks; matrix/security/browser QA; review fixes.
- Every implementation commit includes its focused tests and is independently revertible. Generated source and all byte projections remain in the same commit. Delegated fanout work never commits unless the execution owner explicitly assigned that commit.
- Follow the repository trailer contract: `Constraint`, `Rejected`, `Confidence`, `Scope-risk`, `Directive`, `Tested`, `Not-tested`, and `Signed-off-by`.
- Do not commit planning artifacts or product code during this planning run; these commit instructions apply only when a worker explicitly executes the reviewed plan.

## Success criteria
- Existing `ulw-work`/structured `visual-engineering` requests receive the phase-minimal current OMH-managed guidance without learning a new command or skill.
- All supported owners carry identical canonical read IDs/phases/digests and conservative claim states; owner retargeting preserves the semantic digest.
- Missing, stale, modified, symlinked, out-of-root, wrong-digest, and concurrent-replacement inputs fail closed before live action while ordinary preparation returns deterministic repair only.
- Verified bytes are transient and delivered before normal work; fanout sends prompts through stdin, persists metadata only, and keeps `fanout_contract/v2` canonical bytes/shape unchanged.
- Reported, observed, application, accessibility/check, source freshness, and visual PASS evidence remain separate and cannot promote one another.
- Existing generated design/frontend guidance contains the independently authored adopted techniques, has no upstream runtime/corpus/dependency, and all generated/context-cost/profile checks are green.
- Matching source-revision browser evidence can PASS; changed/missing revision makes old captures stale and forces HOLD until fresh captures exist.
- Focused suites, routing precision corpus, owner matrix, fanout security matrix, generated checks, lint, compile, `git diff --check`, and the full unittest suite all exit 0.
- F1-F4 independently APPROVE with complete evidence and cleanup receipts; no unrelated changes or live child/runtime resources remain.
