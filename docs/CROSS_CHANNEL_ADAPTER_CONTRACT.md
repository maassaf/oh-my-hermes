# Cross-Channel Adapter Contract

This contract is for Hermes adapter authors and operators integrating a hosted
chat platform with OMH. It describes the handoff boundary; it does not move
transport behavior into OMH.

## Ingress

Call the existing generic wrapper surface and supply platform identity
separately:

```python
build_chat_interaction_payload(
    message,
    source="generic",
    platform_context={
        "platform": "matrix",
        "conversation_ref": "conv:matrix:7d3a91",
        "thread_ref": "thread:matrix:2f6c84",
        "capabilities": {"reply": {"threads": True}},
    },
)
```

The validated platform envelope is returned at `payload["platform"]`. The
adapter-facing rendered contract is returned at
`payload["chat_response"]["messenger_rendering"]`; it includes
`omh_message_gate` when present and the `adapter_payload`. These are prepared
local contracts only: the adapter still owns transport and delivery, and their
presence is not posting or delivery evidence.

`source="generic"` is required for platforms without an existing core source.
Discord, Slack, and Telegram keep their existing source values. Buzz uses
`source="hermes"` because Hermes owns its transport while
`platform_context.platform="buzz"` preserves the platform identity. Do not add
a new source registry entry merely to identify a host.

The supported platform ids are exactly these 23, in registry order:

1. `telegram`
2. `discord`
3. `whatsapp`
4. `slack`
5. `signal`
6. `mattermost`
7. `matrix`
8. `home_assistant`
9. `email`
10. `twilio_sms`
11. `dingtalk`
12. `wecom_websocket`
13. `wecom_callback`
14. `wechat_ilink`
15. `feishu_lark`
16. `imessage_bluebubbles`
17. `qqbot`
18. `tencent_yuanbao`
19. `microsoft_teams`
20. `line`
21. `simplex`
22. `api_server`
23. `buzz`

## Opaque identity and session isolation

The adapter generates `conversation_ref`, `thread_ref`, and `user_ref`. Use a
stable platform-prefixed opaque id or a platform-prefixed hash, never a raw
phone number, email address, access token, display name, event body, or vendor
payload. For example, `conv:matrix:7d3a91` is suitable; a room member's email
address is not.

Supplying `conversation_ref` produces `session_scope: conversation`. OMH may
then isolate continuity by platform and conversation/thread refs. Without a
conversation ref, the envelope is `session_scope: event_fallback`: treat that
event as isolated and do not merge it into a conversation merely because a
thread or user ref happens to match. Adapters own the mapping from vendor ids
to these opaque refs.

When OMH derives a thread key from envelope identity, each identity segment
(`conversation_ref`, `thread_ref`) is percent-encoded before it becomes a
`:`-separated thread-key segment, so a `:` or `%` inside a ref can never
collide with the segment structure or with another ref's encoding. Refs made
of unreserved characters (letters, digits, `-._~`) keep their visible form in
the key. Platform session ids are hashed in a separate typed namespace
(`omh_platform_session_identity/v1` wrapping the full thread key), so no
legacy `channel_ref`/`source_event_id` combination can mint the same session
id as a platform-envelope session, even when the human-readable thread keys
match byte for byte.

## Profiles, formats, limits, and provenance

The shipped matrix is
[`examples/wrapper-golden/platform-capability-matrix.json`](../examples/wrapper-golden/platform-capability-matrix.json).
It records profile defaults, not live vendor truth.

`render_profile` controls OMH's generic rendering behavior. Mattermost, Matrix,
email, Microsoft Teams, API Server, and Buzz default to `rich_markdown`; all
other registered platforms default to `limited_markdown`. `format_family`
labels the adapter's intended native surface (`matrix/matrix_html`,
`microsoft_teams/adaptive_card`, `line/flex_message`, `buzz/markdown`, and so
on). A format label does not mean OMH serializes, posts, or validates that
vendor format.

Discord, Slack, and Telegram retain their verified core renderer limits. Every
other profile defaults to the conservative OMH pair of 1600 recommended and
1800 hard characters with `limit_provenance: conservative_default`. Do not
replace that pair with remembered or guessed vendor limits. If an adapter has
a validated deployment-specific limit, declare both limit fields in
`platform_context`; the resulting provenance is `adapter_declared`, not
`verified`. Chunk and post `messenger_rendering.chunked_body_texts` in order.

All registry capability fields are unknown and therefore resolve to `false`
with `unverified_default_false`. An adapter may declare booleans for the
capabilities its configured implementation supports. Such values remain
`adapter_declared`; they are examples of adapter configuration, not claims
about every account, server, plan, or vendor deployment.

## Rendering and adapter payload

Use `chat_response.messenger_rendering` as the adapter-facing projection:

- Render `body_text` for the resolved profile, or `fallback_body_text` when the
  actual surface is narrower.
- Post `chunked_body_texts` and then `follow_up_texts` in their supplied order.
- When `omh_message_gate` is present, consume the structured
  `omh_message_gate/v1` object. Preserve its ordered fields, warnings, prompt
  reference, and `prompt_block`; do not infer gate state from prose.
- Translate `adapter_payload.media.attachments` according to declared media
  support. Fetching, uploading, captioning, and vendor file identifiers remain
  adapter-owned.
- Apply `adapter_payload.reply.thread_ref` only through the host's reply or
  thread API. OMH does not resolve vendor thread ids.
- Translate `adapter_payload.reactions.items` and
  `adapter_payload.actions.response_actions` only when the matching declared
  support is true. The adapter owns callback validation and action routing.
- Treat `adapter_payload.delivery.state: prepared_not_delivered` and
  `observed: false` literally. Rendering is never proof of posting or receipt.

The seven shipped examples under
[`examples/wrapper-golden/platform-envelopes/`](../examples/wrapper-golden/platform-envelopes/)
show deterministic WhatsApp, Signal, Microsoft Teams, Matrix, Feishu/Lark,
LINE, and Mattermost envelopes. Their capability values are deliberately
adapter-declared examples and their message/file limits remain profile
defaults.

## Ownership boundary

OMH owns deterministic response preparation: selected profile, normalized
body, chunks, structured gate, actions, attachment metadata, provenance, and
claim boundaries. The adapter continues to own transport, authentication,
encryption, network calls, media transfer, vendor payload serialization,
posting, retries, reactions, callback handling, delivery observation, and
receipt reporting.

Consequently:

- `conservative_default` means no platform-specific limit was established.
- `unverified_default_false` means absence of evidence, not evidence that a
  vendor can never support the capability.
- `adapter_declared` means the current adapter supplied the value; OMH did not
  verify it against a vendor or network.
- `adapter_owns_transport_core_owns_response` is an ownership statement, not
  execution or delivery evidence.

Keep these boundaries in operator-facing status. Only adapter-observed host
responses may advance a prepared payload to posted or delivered state.
