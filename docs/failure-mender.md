# Failure mender

The failure mender produces a bounded, metadata-only decision for an observed
error. It classifies failures as transient, persistent, permanent,
external-wait, or unknown and maps them to retry, replan, stop, or escalate.

Transient failures may retry only while `attempt < max_retries`. Persistent,
permanent, external-wait, and unknown failures do not authorize blind retry.
The original error text is never returned in the decision; a SHA-256 digest
provides a safe correlation key.

For `replan` and `escalate` outcomes, `build_escalation_request` prepares a
reviewable Seed proposal. It does not create a Seed or perform escalation.
Operators should inspect the proposal, reproduce the failure, and then invoke
the project task tracker explicitly.

## Rollout and rollback

The MCP bridge attaches the decision to tool-error results while preserving the
existing error status and payload. Consumers can ignore the additive field.
Rollback is therefore a caller-side compatibility change: stop reading
`failure_decision` or revert the bridge integration, without changing the
classifier contract or stored artifacts.
