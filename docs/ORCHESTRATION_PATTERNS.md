# OMH Orchestration Patterns

OMH orchestration patterns describe how Hermes should shape work before any
executor or runtime claims are made. They are metadata contracts, not hidden
automation.

This is a wrapper and maintainer reference. A normal user describes the work to
Hermes; Hermes or its wrapper selects and inspects the orchestration pattern.

Each pattern names:

- when to use it
- when not to use it
- owner role
- compatible skills
- required decisions
- prepared artifacts
- observed evidence required before status can advance
- wrapper actions that can render the UX

Agents and maintainers can inspect them locally:

```sh
omh capabilities export --section orchestration_patterns --json
omh capabilities inspect executor_session_handoff --json
```

## Included Patterns

| Pattern | Use |
| --- | --- |
| `single_lane` | Direct Hermes-retained work with one owner and one status lane. |
| `clarify_then_plan` | Fuzzy intent that needs a blocking question, plan, and accept/revise gate. |
| `plan_execute_verify` | Work that needs a plan, owner, execution evidence, and verification gate. |
| `fanout_synthesize` | Independent research or option gathering followed by synthesis. |
| `adversarial_review` | A verifier or reviewer challenges a plan, output, or release claim. |
| `team_staged_pipeline` | Multi-lane work where lead/member/verifier ownership is explicit. |
| `swarm_batch` | Independent high-throughput batches with clear ownership. |
| `worktree_isolated_workers` | Parallel implementation that should use isolated workspaces. |
| `loop_run_once` | A bounded loop tick without a daemon or hidden execution. |
| `executor_session_handoff` | Prepared handoff for Codex, Claude Code, Hermes coding skills, or oh-my runtimes. |
| `hermes_coding_team_path` | Optional Hermes-owned coding path with solo, durable-goal, team, and swarm start choices plus an observed runtime ladder. |
| `materials_generation_handoff` | Documents, decks, spreadsheets, PDFs, or other material packages needing generation/QA. |

## Evidence Rule

Prepared pattern metadata is not runtime evidence. For example,
`worktree_isolated_workers` can recommend a worktree policy, but OMH cannot say a
worktree exists until a wrapper or operator records a matching runtime
observation.

This keeps Hermes helpful in chat without pretending to be a hidden executor.

## Boundary-Policy Rules With Retry From Checkpoint

Some constraints only become checkable once an executor has already produced
something: a handoff that edits a forbidden path, a unit that reports a check it
was told not to run, a branch that moves off its frozen base. A boundary policy
is a dormant rule written before the handoff and evaluated only at observable
boundaries, meaning the executor events and artifacts OMH actually records.
Until a rule matches an observed fact it costs nothing and changes nothing.

When a rule does match, the pattern is cancel-or-reject, then retry-from-checkpoint:

- cancel the running handoff, or reject the finished one, with the violation
  named in plain text
- roll back to the latest durable checkpoint, which is a prepared handoff state
  OMH can point at, not a guess about the executor's internal progress
- build an explicit retry from that checkpoint, injecting only the matched
  policy text into the new prompt contract; unmatched rules stay dormant and
  unmentioned
- persist `rule_id`, `fired_at`, and `artifact_revision` with the retry so the
  decision stays auditable after the fact

Use it when a violation is cheap to detect from recorded evidence and expensive
to discover at review time. Don't use it for taste, style preferences, or
anything a reviewer should weigh in context; a boundary policy has to be a
mechanical predicate over observed fields.

Owner role is the lane owner who prepared the handoff. Compatible skills are the
coding-delegation and review surfaces. Required decisions are the rule set, the
checkpoint granularity, and the retry budget. Prepared artifacts are the policy
rules and the checkpoint reference. Observed evidence required before status can
advance is the recorded executor event or artifact revision the rule matched,
plus the retry's own observations.

This pattern deliberately differs from hidden stream interception. Some harnesses
watch an executor's output as it streams, abort it on a pattern match, splice a
system message into the context, and resume as if nothing happened. OMH rejects
that shape: it needs a hidden execution surface, it edits a transcript the user
never sees, and it leaves no artifact anyone can audit. The boundary version
keeps the useful idea, which is a rule that sleeps until it's relevant, and
drops the invisible part. Every firing is a visible cancel plus a visible retry,
and the injected text is limited to the rule that fired.

## Advisor-As-Reviewer Contract

An advisor is a reviewer lane that runs beside the main lane instead of after
it. It is fed deltas, meaning the new observations and artifacts since it last
spoke, not the whole transcript replayed. It runs asynchronously: the main lane
never blocks waiting for advice, and a late note is still useful because it
arrives attached to the delta it reviewed.

Advisor output is typed, and severity is a closed set:

| Severity | Meaning | Effect on the lane |
| --- | --- | --- |
| `aside` | Context worth having, no action implied. | None. Recorded, not surfaced as a decision. |
| `concern` | Something the owner should weigh before continuing. | Surfaced to the owner; the lane keeps moving. |
| `blocker` | The lane should not hand off in this state. | Gates the handoff until resolved or overridden explicitly. |

Each note carries evidence and a recommendation alongside its severity, so the
owner can act without re-deriving the finding. A `blocker` is the only severity
with teeth, and its only power is to hold the handoff gate; it never edits a
prompt, cancels a run, or writes to the workspace.

Two controls keep the lane from becoming noise. A cooldown suppresses repeat
notes for a bounded number of turns after the advisor speaks, and dedupe drops a
note whose finding matches one already recorded. Both are contract-level rules,
not heuristics the advisor gets to reinterpret.

The advisor is always explicit. Its notes are visible artifacts owned by a named
reviewer lane, its severity is machine-readable, and nothing it produces is
merged into the main lane's context without being shown. An advisor that
whispers into a prompt is not this pattern. Blocking definitions stay with
[REVIEW.md](../REVIEW.md); the advisor supplies findings and a severity, and the
review gate decides what ships.

## Multiple Agents, One Home

The patterns above describe a single OMH home coordinating one lane of
work at a time. When more than one agent, wrapper process, or coding
executor is actually running concurrently against the same `~/.omh` and
`~/.hermes` directories, read
[Multi-Agent Operations](MULTI_AGENT_OPERATIONS.md) for the shared-state
ownership model, the Hermes `config.yaml` capability boundary, why
`multi_agent_targets` topology is advisory narration rather than
enforcement, and which upstream-native primitives (Kanban, `delegate_task`,
profiles) OMH prefers to hand work off to instead of reimplementing.
