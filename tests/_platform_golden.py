"""Deterministic builders for the shipped P2 platform golden artifacts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from omh.system.platform_envelope import build_platform_envelope
from omh.system.platform_profiles import PLATFORM_IDS, PLATFORM_PROFILES
from omh.wrapper.contract import messenger_rendering_contract
from omh.wrapper.message_gate import build_message_gate

MATRIX_SCHEMA_VERSION = "omh_platform_capability_matrix/v1"
FIXTURE_SCHEMA_VERSION = "omh_platform_envelope_golden/v1"
GOLDEN_PLATFORMS = (
    "whatsapp", "signal", "microsoft_teams", "matrix",
    "feishu_lark", "line", "mattermost",
)

_DECLARED_CAPABILITIES: dict[str, dict[str, dict[str, bool]]] = {
    "whatsapp": {"media": {"files": True, "images": True}, "reply": {"quotes": True}, "actions": {"buttons": True}},
    "signal": {"media": {"files": True, "voice": True}, "reply": {"quotes": True}, "reactions": {"native": True}},
    "microsoft_teams": {"media": {"files": True}, "reply": {"threads": True}, "actions": {"buttons": True, "forms": True}},
    "matrix": {"media": {"files": True, "images": True}, "reply": {"threads": True}, "reactions": {"native": True}},
    "feishu_lark": {"media": {"files": True}, "reply": {"threads": True}, "actions": {"buttons": True}},
    "line": {"media": {"files": True, "video": True}, "reply": {"quotes": True}, "actions": {"buttons": True}},
    "mattermost": {"media": {"files": True}, "reply": {"threads": True}, "reactions": {"native": True}, "actions": {"buttons": True}},
}

FIXED_BODY = "Review the prepared cross-channel handoff.\n\n- Confirm scope\n- Keep delivery adapter-owned."
FIXED_ACTIONS = [
    {"id": "approve", "label": "Approve prepared handoff"},
    {"id": "revise", "label": "Revise scope"},
]
FIXED_ATTACHMENTS = [
    {"kind": "document", "ref": "attachment:adapter-guide", "meta": {"alt": "Adapter guide"}}
]
FIXTURE_CLAIM_BOUNDARY = (
    "This golden records deterministic preparation only; it is not network, "
    "posting, delivery, or vendor-capability evidence."
)


def build_capability_matrix() -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    for platform_id in PLATFORM_IDS:
        profile = PLATFORM_PROFILES[platform_id]
        envelope = build_platform_envelope(
            {"platform": platform_id}, source=profile.transport_source
        )
        profiles.append({
            "platform_id": platform_id,
            "transport_source": profile.transport_source,
            "render_profile": profile.render_profile,
            "format_family": profile.format_family,
            "field_provenance": {
                "transport_source": "profile_registry",
                "render_profile": "profile_registry",
                "format_family": "approved_architecture_user_surface",
            },
            "limits": deepcopy(envelope["limits"]),
            "limit_provenance": envelope["limit_provenance"],
            "capabilities": deepcopy(envelope["capabilities"]),
            "capability_provenance": deepcopy(envelope["capability_provenance"]),
            "claim_boundary": envelope["claim_boundary"],
        })
    return {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "profiles": profiles,
        "claim_boundary": (
            "This matrix ships core profile defaults and provenance only; it is "
            "not live vendor, transport, authentication, posting, or delivery evidence."
        ),
    }


def build_platform_fixture(platform_id: str) -> dict[str, Any]:
    if platform_id not in GOLDEN_PLATFORMS:
        raise ValueError(f"unsupported golden platform: {platform_id}")
    context: dict[str, Any] = {
        "platform": platform_id,
        "conversation_ref": f"conv:{platform_id}:7d3a91",
        "thread_ref": f"thread:{platform_id}:2f6c84",
        "capabilities": deepcopy(_DECLARED_CAPABILITIES[platform_id]),
    }
    envelope = build_platform_envelope(context, source="generic")
    gate = build_message_gate(
        skill="ulw-work", executor="hermes", model="adapter-selected",
        status="prepared_not_observed", prompt_sha256="6f1d8b5a2c4e7d90",
        composed_prompt="Prepare the bounded adapter response; do not deliver it.",
    )
    rendering = messenger_rendering_contract(
        visible_prefix="[omh] adapter golden",
        first_line="Prepared cross-channel handoff",
        body=FIXED_BODY,
        claim_boundary=FIXTURE_CLAIM_BOUNDARY,
        source="generic",
        platform_envelope=envelope,
        message_gate=gate,
        follow_up_texts=(gate["prompt_block"],),
        response_actions=FIXED_ACTIONS,
        attachments=FIXED_ATTACHMENTS,
    )
    return {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "platform_id": platform_id,
        "source": "generic",
        "platform_context": context,
        "body": FIXED_BODY,
        "actions": deepcopy(FIXED_ACTIONS),
        "attachments": deepcopy(FIXED_ATTACHMENTS),
        "platform_envelope": envelope,
        "messenger_rendering": rendering,
        "claim_boundary": FIXTURE_CLAIM_BOUNDARY,
    }
