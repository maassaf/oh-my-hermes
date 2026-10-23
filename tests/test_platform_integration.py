"""P0 integration of the omh_platform_envelope/v1 model through OMH entry points.

An adapter hands ``platform_context`` (who the platform is, plus opaque
conversation/thread/user refs) to the chat interaction payload builder, the
wrapper session store, the plugin tool, and the CLI. These tests pin the
integration contract:

- the envelope is built and validated before persistence and rides the
  interaction payload as top-level ``platform``;
- thread keys derive from platform identity
  (``source:pf-{platform_id}:{conversation_ref}[:{thread_ref}][:{target_scope}]``)
  with no event id or message hash, so two events in one conversation resume
  one session, while the same refs on two platforms never collide;
- a context without ``conversation_ref`` falls back to the exact legacy
  derivation, and legacy calls (``platform_context=None``) stay
  byte-identical, including the pinned ``hermes:c1:m1`` key;
- session records stay ``wrapper_session/v1`` with an optional compact
  ``platform`` carrying only platform_id/transport_source/session_scope --
  never refs, capabilities, or limits -- and old records still validate;
- unsafe input fails closed before any session directory exists: unknown
  sources, raw phone/email/secret refs, and contradictory contexts;
- the plugin reports bounded ``omh_interact_result/v1`` errors
  (``unsupported_source`` / ``invalid_platform_context``) with the supported
  source list and a ``source=generic`` + ``platform_context.platform`` hint,
  and never silently reports ``hermes``.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()

from omh.paths import resolve_paths
from omh.runtime_records import validate_wrapper_session_record
from omh.system.platform_envelope import (
    PlatformContextError,
    platform_thread_key_scope,
)
from omh.wrapper.contract import build_chat_interaction_payload
from omh.wrapper.sessions import (
    create_or_resume_wrapper_session,
    platform_session_id_for_thread_key,
    session_id_for_thread_key,
)

_WHATSAPP_CONTEXT = {
    "platform": "whatsapp",
    "conversation_ref": "conv-1234-abcd",
    "thread_ref": "thread-5678-efgh",
    "user_ref": "user-9012-ijkl",
}

_SIGNAL_CONTEXT = {
    "platform": "signal",
    "conversation_ref": "conv-1234-abcd",
    "thread_ref": "thread-5678-efgh",
    "user_ref": "user-9012-ijkl",
}

_MESSAGE = "summarize the current OMH status"


def _whatsapp_key(*, source: str = "generic", thread: bool = True) -> str:
    parts = f"{source}:pf-whatsapp:conv-1234-abcd"
    if thread:
        parts += ":thread-5678-efgh"
    return parts


class DirectApiPlatformContextTests(unittest.TestCase):
    def test_generic_source_with_whatsapp_context_attaches_envelope(self) -> None:
        payload = build_chat_interaction_payload(
            _MESSAGE,
            source="generic",
            platform_context=_WHATSAPP_CONTEXT,
        )

        envelope = payload["platform"]
        self.assertEqual(envelope["schema_version"], "omh_platform_envelope/v1")
        self.assertEqual(envelope["platform_id"], "whatsapp")
        self.assertEqual(envelope["transport_source"], "generic")
        self.assertEqual(envelope["session_scope"], "conversation")
        self.assertEqual(
            envelope["identity"],
            {
                "conversation_ref": "conv-1234-abcd",
                "thread_ref": "thread-5678-efgh",
                "user_ref": "user-9012-ijkl",
            },
        )
        self.assertEqual(payload["thread_key"], _whatsapp_key())
        self.assertEqual(payload["source"], "generic")

    def test_thread_key_has_no_event_id_or_message_hash(self) -> None:
        first = build_chat_interaction_payload(
            "first message",
            source="generic",
            platform_context=_WHATSAPP_CONTEXT,
            source_metadata={"source_event_id": "event-a"},
        )
        second = build_chat_interaction_payload(
            "a different second message",
            source="generic",
            platform_context=_WHATSAPP_CONTEXT,
            source_metadata={"source_event_id": "event-b"},
        )

        self.assertEqual(first["thread_key"], _whatsapp_key())
        self.assertEqual(first["thread_key"], second["thread_key"])
        self.assertNotIn("event-a", first["thread_key"])
        self.assertNotIn(first["message_sha256"][:12], first["thread_key"])

    def test_thread_key_omits_thread_ref_when_not_declared(self) -> None:
        context = {key: value for key, value in _WHATSAPP_CONTEXT.items() if key != "thread_ref"}
        payload = build_chat_interaction_payload(
            _MESSAGE, source="generic", platform_context=context
        )
        self.assertEqual(payload["thread_key"], _whatsapp_key(thread=False))

    def test_thread_key_appends_target_scope_when_target_metadata_present(self) -> None:
        payload = build_chat_interaction_payload(
            _MESSAGE,
            source="generic",
            platform_context=_WHATSAPP_CONTEXT,
            source_metadata={"agent_ref": "agent-a"},
        )
        self.assertTrue(payload["thread_key"].startswith(_whatsapp_key() + ":target-"))

    def test_same_refs_on_whatsapp_and_signal_produce_different_keys(self) -> None:
        whatsapp = build_chat_interaction_payload(
            _MESSAGE, source="generic", platform_context=_WHATSAPP_CONTEXT
        )
        signal = build_chat_interaction_payload(
            _MESSAGE, source="generic", platform_context=_SIGNAL_CONTEXT
        )
        self.assertNotEqual(whatsapp["thread_key"], signal["thread_key"])
        self.assertNotEqual(
            platform_session_id_for_thread_key(whatsapp["thread_key"]),
            platform_session_id_for_thread_key(signal["thread_key"]),
        )

    def test_missing_conversation_ref_uses_legacy_event_fallback(self) -> None:
        context = {"platform": "whatsapp", "user_ref": "user-9012-ijkl"}
        payload = build_chat_interaction_payload(
            _MESSAGE,
            source="generic",
            platform_context=context,
            source_metadata={"source_event_id": "m1", "channel_ref": "c1"},
        )

        self.assertEqual(payload["thread_key"], "generic:c1:m1")
        self.assertEqual(payload["platform"]["session_scope"], "event_fallback")

    def test_event_fallback_without_event_id_matches_legacy_message_hash_key(self) -> None:
        # Plain string message, no metadata: the legacy derivation falls back
        # to the message hash, and the platform path must reuse that exact
        # already-computed legacy key rather than re-deriving one from an
        # empty message.
        context = {"platform": "whatsapp"}
        legacy = build_chat_interaction_payload(_MESSAGE, source="generic")
        with_context = build_chat_interaction_payload(
            _MESSAGE, source="generic", platform_context=context
        )

        expected = (
            "generic:channel:"
            + hashlib.sha256(_MESSAGE.encode("utf-8")).hexdigest()[:12]
        )
        self.assertEqual(legacy["thread_key"], expected)
        self.assertEqual(with_context["thread_key"], expected)
        self.assertEqual(with_context["platform"]["session_scope"], "event_fallback")

    def test_legacy_call_without_context_is_byte_identical(self) -> None:
        legacy = build_chat_interaction_payload(
            _MESSAGE,
            source="hermes",
            source_metadata={"source_event_id": "m1", "channel_ref": "c1"},
        )
        explicit_none = build_chat_interaction_payload(
            _MESSAGE,
            source="hermes",
            source_metadata={"source_event_id": "m1", "channel_ref": "c1"},
            platform_context=None,
        )

        self.assertNotIn("platform", legacy)
        self.assertEqual(
            json.dumps(legacy, sort_keys=True),
            json.dumps(explicit_none, sort_keys=True),
        )
        self.assertEqual(legacy["thread_key"], "hermes:c1:m1")

    def test_unknown_source_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            build_chat_interaction_payload(
                _MESSAGE, source="whatsapp", platform_context=_WHATSAPP_CONTEXT
            )

    def test_context_for_wrong_transport_fails_closed(self) -> None:
        with self.assertRaises(PlatformContextError):
            build_chat_interaction_payload(
                _MESSAGE, source="discord", platform_context=_WHATSAPP_CONTEXT
            )

    def test_raw_phone_ref_fails_closed(self) -> None:
        with self.assertRaises(PlatformContextError):
            build_chat_interaction_payload(
                _MESSAGE,
                source="generic",
                platform_context={**_WHATSAPP_CONTEXT, "user_ref": "+1 (555) 010-2030"},
            )

    def test_raw_email_ref_fails_closed(self) -> None:
        with self.assertRaises(PlatformContextError):
            build_chat_interaction_payload(
                _MESSAGE,
                source="generic",
                platform_context={**_WHATSAPP_CONTEXT, "user_ref": "person@example.com"},
            )

    def test_secret_shaped_ref_fails_closed(self) -> None:
        with self.assertRaises(PlatformContextError):
            build_chat_interaction_payload(
                _MESSAGE,
                source="generic",
                platform_context={**_WHATSAPP_CONTEXT, "user_ref": "ghp_0123456789abcdefXYZ"},
            )

    def test_colon_in_conversation_ref_cannot_collide_with_thread_ref_segment(self) -> None:
        # D1 structural collision: before segment encoding, conversation_ref
        # 'conv:th_9' with no thread and conversation_ref 'conv' + thread_ref
        # 'th_9' produced the identical scope 'whatsapp:conv:th_9'.
        merged = build_chat_interaction_payload(
            _MESSAGE,
            source="generic",
            platform_context={"platform": "whatsapp", "conversation_ref": "conv:th_9"},
        )
        split = build_chat_interaction_payload(
            _MESSAGE,
            source="generic",
            platform_context={
                "platform": "whatsapp",
                "conversation_ref": "conv",
                "thread_ref": "th_9",
            },
        )

        self.assertEqual(merged["thread_key"], "generic:pf-whatsapp:conv%3Ath_9")
        self.assertEqual(split["thread_key"], "generic:pf-whatsapp:conv:th_9")
        self.assertNotEqual(merged["thread_key"], split["thread_key"])

    def test_literal_percent_in_ref_round_trips_distinctly(self) -> None:
        # A literal '%' encodes as %25, so a ref that already looks encoded
        # can never alias the encoding of another ref.
        scope_plain = platform_thread_key_scope(
            {
                "schema_version": "omh_platform_envelope/v1",
                "platform_id": "whatsapp",
                "identity": {"conversation_ref": "conv:th_9"},
            }
        )
        scope_percent = platform_thread_key_scope(
            {
                "schema_version": "omh_platform_envelope/v1",
                "platform_id": "whatsapp",
                "identity": {"conversation_ref": "conv%3Ath_9"},
            }
        )

        self.assertEqual(scope_plain, "whatsapp:conv%3Ath_9")
        self.assertEqual(scope_percent, "whatsapp:conv%253Ath_9")
        self.assertNotEqual(scope_plain, scope_percent)

    def test_refs_without_reserved_chars_keep_visible_keys(self) -> None:
        payload = build_chat_interaction_payload(
            _MESSAGE, source="generic", platform_context=_WHATSAPP_CONTEXT
        )
        self.assertEqual(payload["thread_key"], _whatsapp_key())

    def test_raw_discord_snowflake_ref_rejected_but_prefixed_snowflake_accepted(self) -> None:
        # Numeric PII rejection is not relaxed: a raw native numeric ID is
        # indistinguishable from a hand-typed phone number, so the adapter
        # must prefix or hash it before the core accepts the envelope.
        snowflake = "1234567890123456789"
        with self.assertRaises(PlatformContextError):
            build_chat_interaction_payload(
                _MESSAGE,
                source="discord",
                platform_context={"platform": "discord", "conversation_ref": snowflake},
            )

        payload = build_chat_interaction_payload(
            _MESSAGE,
            source="discord",
            platform_context={
                "platform": "discord",
                "conversation_ref": f"discord-channel-{snowflake}",
            },
        )
        self.assertEqual(
            payload["platform"]["identity"]["conversation_ref"],
            f"discord-channel-{snowflake}",
        )
        self.assertEqual(
            payload["thread_key"],
            f"discord:pf-discord:discord-channel-{snowflake}",
        )


class WrapperSessionPlatformContextTests(unittest.TestCase):
    def test_two_event_ids_in_one_conversation_resume_one_session(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")

            first = create_or_resume_wrapper_session(
                paths,
                "first event",
                source="generic",
                platform_context=_WHATSAPP_CONTEXT,
                source_metadata={"source_event_id": "event-a"},
            )
            second = create_or_resume_wrapper_session(
                paths,
                "second event",
                source="generic",
                platform_context=_WHATSAPP_CONTEXT,
                source_metadata={"source_event_id": "event-b"},
            )

            self.assertFalse(first["resumed"])
            self.assertTrue(second["resumed"])
            self.assertEqual(first["session"]["session_id"], second["session"]["session_id"])
            self.assertEqual(first["session"]["thread_key"], _whatsapp_key())
            self.assertEqual(
                first["session"]["session_id"],
                platform_session_id_for_thread_key(_whatsapp_key()),
            )

    def test_crafted_legacy_key_cannot_collide_with_platform_session(self) -> None:
        # D2 namespace collision: a crafted legacy channel_ref/source_event_id
        # reproduces the exact human thread_key bytes of a platform session.
        # The legacy session keeps the legacy hash namespace; the platform
        # session hashes under a separate typed namespace, so the sessions
        # land in different directories even with equal human keys.
        crafted_metadata = {
            "channel_ref": "pf-whatsapp:conv-x",
            "source_event_id": "th-y",
        }
        platform_context = {
            "platform": "whatsapp",
            "conversation_ref": "conv-x",
            "thread_ref": "th-y",
        }
        human_key = "generic:pf-whatsapp:conv-x:th-y"

        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")

            legacy = create_or_resume_wrapper_session(
                paths, _MESSAGE, source="generic", source_metadata=crafted_metadata
            )
            platform = create_or_resume_wrapper_session(
                paths, _MESSAGE, source="generic", platform_context=platform_context
            )

            # Equal pre-domain human key bytes, stored unaltered on both.
            self.assertEqual(legacy["session"]["thread_key"], human_key)
            self.assertEqual(platform["session"]["thread_key"], human_key)

            # Distinct session ids and directories.
            legacy_id = legacy["session"]["session_id"]
            platform_id = platform["session"]["session_id"]
            self.assertNotEqual(legacy_id, platform_id)
            self.assertEqual(legacy_id, session_id_for_thread_key(human_key))
            self.assertEqual(platform_id, platform_session_id_for_thread_key(human_key))
            self.assertTrue((paths.runtime_wrapper_sessions_dir / legacy_id).is_dir())
            self.assertTrue((paths.runtime_wrapper_sessions_dir / platform_id).is_dir())
            self.assertEqual(validate_wrapper_session_record(legacy["session"]), [])
            self.assertEqual(validate_wrapper_session_record(platform["session"]), [])
            self.assertNotIn("platform", legacy["session"])

            # Both resume under their own namespaces.
            legacy_again = create_or_resume_wrapper_session(
                paths, _MESSAGE, source="generic", source_metadata=crafted_metadata
            )
            platform_again = create_or_resume_wrapper_session(
                paths, _MESSAGE, source="generic", platform_context=platform_context
            )
            self.assertTrue(legacy_again["resumed"])
            self.assertTrue(platform_again["resumed"])
            self.assertEqual(legacy_again["session"]["session_id"], legacy_id)
            self.assertEqual(platform_again["session"]["session_id"], platform_id)

    def test_legacy_session_id_derivation_is_byte_identical(self) -> None:
        self.assertEqual(
            session_id_for_thread_key("hermes:c1:m1"),
            "ws-" + hashlib.sha256(b"hermes:c1:m1").hexdigest()[:24],
        )
        self.assertNotEqual(
            platform_session_id_for_thread_key("hermes:c1:m1"),
            session_id_for_thread_key("hermes:c1:m1"),
        )

    def test_same_refs_on_two_platforms_open_different_sessions(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")

            whatsapp = create_or_resume_wrapper_session(
                paths, _MESSAGE, source="generic", platform_context=_WHATSAPP_CONTEXT
            )
            signal = create_or_resume_wrapper_session(
                paths, _MESSAGE, source="generic", platform_context=_SIGNAL_CONTEXT
            )

            self.assertNotEqual(
                whatsapp["session"]["session_id"], signal["session"]["session_id"]
            )
            self.assertNotEqual(
                whatsapp["session"]["thread_key"], signal["session"]["thread_key"]
            )

    def test_session_record_carries_compact_platform_only(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")

            result = create_or_resume_wrapper_session(
                paths, _MESSAGE, source="generic", platform_context=_WHATSAPP_CONTEXT
            )
            session = result["session"]

            self.assertEqual(session["schema_version"], "wrapper_session/v1")
            self.assertEqual(
                session["platform"],
                {
                    "platform_id": "whatsapp",
                    "transport_source": "generic",
                    "session_scope": "conversation",
                },
            )
            # The compact platform block carries identity and scope only:
            # refs, capabilities, and limits never persist inside it. (The
            # session thread_key embeds conversation/thread refs by design --
            # that is the specified key format.)
            persisted_platform = json.dumps(session["platform"], sort_keys=True)
            self.assertNotIn("conv-1234-abcd", persisted_platform)
            self.assertNotIn("thread-5678-efgh", persisted_platform)
            self.assertNotIn("user-9012-ijkl", persisted_platform)
            self.assertNotIn("capabilities", session["platform"])
            self.assertNotIn("limits", session["platform"])
            self.assertNotIn("user_ref", json.dumps(session, sort_keys=True))
            self.assertEqual(validate_wrapper_session_record(session), [])

            # The envelope on the interaction keeps the full detail; only the
            # persisted record is compacted.
            self.assertEqual(
                result["interaction"]["platform"]["identity"]["conversation_ref"],
                "conv-1234-abcd",
            )

    def test_session_record_rejects_platform_transport_mismatch_with_session_source(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")

            result = create_or_resume_wrapper_session(
                paths, _MESSAGE, source="generic", platform_context=_WHATSAPP_CONTEXT
            )
            record = dict(result["session"])
            self.assertEqual(validate_wrapper_session_record(record), [])

            # A hand-crafted record whose session source disagrees with the
            # compact platform's transport_source must not validate: the
            # session claims one transport while the platform block speaks
            # for another.
            forged = dict(record)
            forged["source"] = "discord"
            errors = validate_wrapper_session_record(forged)
            self.assertTrue(errors)
            self.assertTrue(
                any("transport_source" in error for error in errors),
                errors,
            )

    def test_legacy_session_record_has_no_platform_and_still_validates(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")

            result = create_or_resume_wrapper_session(
                paths,
                _MESSAGE,
                source="hermes",
                source_metadata={"source_event_id": "m1", "channel_ref": "c1"},
            )
            session = result["session"]

            self.assertNotIn("platform", session)
            self.assertEqual(session["thread_key"], "hermes:c1:m1")
            self.assertEqual(
                session["session_id"], session_id_for_thread_key("hermes:c1:m1")
            )
            self.assertEqual(validate_wrapper_session_record(session), [])

    def test_missing_conversation_ref_falls_back_to_legacy_thread_key(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = resolve_paths(Path(tmp) / ".omh", Path(tmp) / ".hermes")

            result = create_or_resume_wrapper_session(
                paths,
                _MESSAGE,
                source="generic",
                platform_context={"platform": "whatsapp"},
                source_metadata={"source_event_id": "m1", "channel_ref": "c1"},
            )

            self.assertEqual(result["session"]["thread_key"], "generic:c1:m1")
            self.assertEqual(
                result["session"]["platform"],
                {
                    "platform_id": "whatsapp",
                    "transport_source": "generic",
                    "session_scope": "event_fallback",
                },
            )
            self.assertEqual(validate_wrapper_session_record(result["session"]), [])

    def test_unsafe_ref_fails_before_session_directory_creation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")

            with self.assertRaises(PlatformContextError):
                create_or_resume_wrapper_session(
                    paths,
                    _MESSAGE,
                    source="generic",
                    platform_context={**_WHATSAPP_CONTEXT, "user_ref": "person@example.com"},
                )

            self.assertFalse(paths.runtime_wrapper_sessions_dir.exists())

    def test_unknown_source_fails_before_session_directory_creation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = resolve_paths(root / ".omh", root / ".hermes")

            with self.assertRaises(ValueError):
                create_or_resume_wrapper_session(
                    paths, _MESSAGE, source="whatsapp", platform_context=_WHATSAPP_CONTEXT
                )

            self.assertFalse(paths.runtime_wrapper_sessions_dir.exists())


class PluginPlatformContextTests(unittest.TestCase):
    def _handler(self):
        from omh.plugin_bundle.omh.tools.chat_tool import omh_interact_handler

        return omh_interact_handler

    def test_plugin_accepts_platform_context_on_session_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            handler = self._handler()

            payload = json.loads(
                handler(
                    {
                        "message": _MESSAGE,
                        "source": "generic",
                        "platform_context": _WHATSAPP_CONTEXT,
                        "omh_home": str(root / ".omh"),
                        "hermes_home": str(root / ".hermes"),
                    }
                )
            )

            self.assertEqual(payload["schema_version"], "chat_interaction/v1")
            self.assertEqual(payload["platform"]["platform_id"], "whatsapp")
            # The plugin injects hermes_home into source_metadata, so the key
            # carries the specified trailing target_scope segment.
            self.assertTrue(payload["thread_key"].startswith(_whatsapp_key() + ":target-"))
            self.assertEqual(
                payload["wrapper_session"]["thread_key"], payload["thread_key"]
            )
            self.assertTrue(payload["wrapper_session"]["recorded"])

    def test_plugin_accepts_platform_context_without_session(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            handler = self._handler()

            payload = json.loads(
                handler(
                    {
                        "message": _MESSAGE,
                        "source": "generic",
                        "platform_context": _WHATSAPP_CONTEXT,
                        "record_session": False,
                        "omh_home": str(root / ".omh"),
                        "hermes_home": str(root / ".hermes"),
                    }
                )
            )

            self.assertEqual(payload["platform"]["platform_id"], "whatsapp")
            self.assertTrue(payload["thread_key"].startswith(_whatsapp_key() + ":target-"))
            self.assertFalse(payload["wrapper_session"]["recorded"])

    def test_plugin_unknown_source_returns_bounded_unsupported_source_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            handler = self._handler()

            payload = json.loads(
                handler(
                    {
                        "message": _MESSAGE,
                        "source": "whatsapp",
                        "platform_context": _WHATSAPP_CONTEXT,
                        "omh_home": str(root / ".omh"),
                        "hermes_home": str(root / ".hermes"),
                    }
                )
            )

            self.assertEqual(payload["schema_version"], "omh_interact_result/v1")
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error"], "unsupported_source")
            self.assertEqual(
                payload["supported_sources"],
                ["generic", "discord", "slack", "telegram", "hermes"],
            )
            self.assertIn("generic", payload["hint"])
            self.assertIn("platform_context", payload["hint"])
            self.assertNotEqual(payload.get("source"), "hermes")
            self.assertNotIn("thread_key", payload)
            self.assertFalse(paths_sessions_created(root))

    def test_plugin_unknown_source_without_context_never_reports_hermes(self) -> None:
        handler = self._handler()

        payload = json.loads(handler({"message": _MESSAGE, "source": "whatsapp"}))

        self.assertEqual(payload["schema_version"], "omh_interact_result/v1")
        self.assertEqual(payload["error"], "unsupported_source")
        self.assertNotEqual(payload.get("source"), "hermes")

    def test_plugin_invalid_platform_context_returns_bounded_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            handler = self._handler()

            payload = json.loads(
                handler(
                    {
                        "message": _MESSAGE,
                        "source": "generic",
                        "platform_context": {"platform": "not-a-platform"},
                        "omh_home": str(root / ".omh"),
                        "hermes_home": str(root / ".hermes"),
                    }
                )
            )

            self.assertEqual(payload["schema_version"], "omh_interact_result/v1")
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error"], "invalid_platform_context")
            self.assertEqual(
                payload["supported_sources"],
                ["generic", "discord", "slack", "telegram", "hermes"],
            )
            self.assertIn("generic", payload["hint"])
            self.assertFalse(paths_sessions_created(root))

    def test_plugin_raw_pii_ref_returns_invalid_platform_context(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            handler = self._handler()

            payload = json.loads(
                handler(
                    {
                        "message": _MESSAGE,
                        "source": "generic",
                        "platform_context": {
                            **_WHATSAPP_CONTEXT,
                            "user_ref": "+1 (555) 010-2030",
                        },
                        "omh_home": str(root / ".omh"),
                        "hermes_home": str(root / ".hermes"),
                    }
                )
            )

            self.assertEqual(payload["error"], "invalid_platform_context")
            self.assertFalse(paths_sessions_created(root))

    def test_plugin_unsupported_source_error_never_echoes_source_text(self) -> None:
        handler = self._handler()
        sentinel = "ghp_0123456789abcdefSENTINEL"

        payload = json.loads(
            handler({"message": _MESSAGE, "source": f"whatsapp-{sentinel}"})
        )

        serialized = json.dumps(payload, sort_keys=True)
        self.assertEqual(payload["schema_version"], "omh_interact_result/v1")
        self.assertEqual(payload["error"], "unsupported_source")
        self.assertEqual(
            payload["supported_sources"],
            ["generic", "discord", "slack", "telegram", "hermes"],
        )
        self.assertIn("generic", payload["hint"])
        self.assertNotIn(sentinel, serialized)

    def test_plugin_invalid_platform_context_never_echoes_platform_text(self) -> None:
        handler = self._handler()
        sentinel = "+1 (555) 010-2030"

        payload = json.loads(
            handler(
                {
                    "message": _MESSAGE,
                    "source": "generic",
                    "platform_context": {"platform": sentinel},
                }
            )
        )

        serialized = json.dumps(payload, sort_keys=True)
        self.assertEqual(payload["schema_version"], "omh_interact_result/v1")
        self.assertEqual(payload["error"], "invalid_platform_context")
        self.assertEqual(
            payload["supported_sources"],
            ["generic", "discord", "slack", "telegram", "hermes"],
        )
        self.assertIn("generic", payload["hint"])
        self.assertNotIn(sentinel, serialized)

    def test_plugin_legacy_call_without_context_is_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            handler = self._handler()

            payload = json.loads(
                handler(
                    {
                        "message": _MESSAGE,
                        "source": "hermes",
                        "source_metadata": {"source_event_id": "m1", "channel_ref": "c1"},
                        "omh_home": str(root / ".omh"),
                        "hermes_home": str(root / ".hermes"),
                    }
                )
            )

            self.assertEqual(payload["schema_version"], "chat_interaction/v1")
            self.assertNotIn("platform", payload)
            # Legacy plugin thread keys keep the exact legacy derivation; the
            # plugin injects hermes_home into source_metadata, which the
            # legacy target-scope rule has always folded in.
            self.assertTrue(payload["thread_key"].startswith("hermes:c1:"))
            self.assertTrue(payload["thread_key"].endswith(":m1"))
            self.assertNotIn("pf-", payload["thread_key"])


def paths_sessions_created(root: Path) -> bool:
    return (root / ".omh").exists() and any(
        (root / ".omh").rglob("session.json")
    )


class CliPlatformContextTests(unittest.TestCase):
    def test_cli_interact_accepts_platform_and_context_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            status, stdout, stderr = run_cli(
                base
                + [
                    "chat",
                    "interact",
                    "--source",
                    "generic",
                    "--platform",
                    "whatsapp",
                    "--platform-context-json",
                    json.dumps(
                        {
                            "conversation_ref": "conv-1234-abcd",
                            "thread_ref": "thread-5678-efgh",
                        }
                    ),
                    "--json",
                    _MESSAGE,
                ],
                output_json=False,
            )

            self.assertEqual(status, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["platform"]["platform_id"], "whatsapp")
            self.assertEqual(payload["thread_key"], _whatsapp_key())

    def test_cli_interact_context_json_may_carry_platform_key(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            status, stdout, stderr = run_cli(
                base
                + [
                    "chat",
                    "interact",
                    "--source",
                    "generic",
                    "--platform-context-json",
                    json.dumps(_WHATSAPP_CONTEXT),
                    "--json",
                    _MESSAGE,
                ],
                output_json=False,
            )

            self.assertEqual(status, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["platform"]["platform_id"], "whatsapp")
            self.assertEqual(payload["thread_key"], _whatsapp_key())

    def test_cli_session_start_persists_compact_platform(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            status, stdout, stderr = run_cli(
                base
                + [
                    "chat",
                    "session",
                    "start",
                    "--source",
                    "generic",
                    "--platform-context-json",
                    json.dumps(_WHATSAPP_CONTEXT),
                    _MESSAGE,
                ],
                output_json=False,
            )

            self.assertEqual(status, 0, stderr)
            payload = json.loads(stdout)
            self.assertEqual(payload["session"]["thread_key"], _whatsapp_key())
            self.assertEqual(
                payload["session"]["platform"],
                {
                    "platform_id": "whatsapp",
                    "transport_source": "generic",
                    "session_scope": "conversation",
                },
            )
            self.assertEqual(validate_wrapper_session_record(payload["session"]), [])

    def test_cli_interact_rejects_whatsapp_source(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            # argparse --source choices stay the five CHAT_SOURCES, so an
            # unsupported source fails closed at parse time with exit code 2.
            with self.assertRaises(SystemExit) as raised:
                run_cli(
                    base
                    + [
                        "chat",
                        "interact",
                        "--source",
                        "whatsapp",
                        "--platform-context-json",
                        json.dumps(_WHATSAPP_CONTEXT),
                        "--json",
                        _MESSAGE,
                    ],
                    output_json=False,
                )

            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(paths_sessions_created(root))

    def test_cli_interact_rejects_conflicting_platform_flags(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            status, _, stderr = run_cli(
                base
                + [
                    "chat",
                    "interact",
                    "--source",
                    "generic",
                    "--platform",
                    "signal",
                    "--platform-context-json",
                    json.dumps(_WHATSAPP_CONTEXT),
                    "--json",
                    _MESSAGE,
                ],
                output_json=False,
            )

            self.assertNotEqual(status, 0)
            self.assertFalse(paths_sessions_created(root))

    def test_cli_interact_rejects_non_object_context_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            status, _, stderr = run_cli(
                base
                + [
                    "chat",
                    "interact",
                    "--source",
                    "generic",
                    "--platform-context-json",
                    '["whatsapp"]',
                    "--json",
                    _MESSAGE,
                ],
                output_json=False,
            )

            self.assertNotEqual(status, 0)

    def test_cli_interact_raw_phone_ref_fails_before_any_session_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            status, _, stderr = run_cli(
                base
                + [
                    "chat",
                    "interact",
                    "--source",
                    "generic",
                    "--platform-context-json",
                    json.dumps({**_WHATSAPP_CONTEXT, "user_ref": "+1 (555) 010-2030"}),
                    "--json",
                    _MESSAGE,
                ],
                output_json=False,
            )

            self.assertNotEqual(status, 0)
            self.assertFalse(paths_sessions_created(root))

    def test_cli_interact_without_platform_flags_is_byte_identical(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes")]
            argv = base + [
                "chat",
                "interact",
                "--source",
                "hermes",
                "--source-event-id",
                "m1",
                "--channel-ref",
                "c1",
                "--json",
                _MESSAGE,
            ]
            first_status, first_stdout, first_stderr = run_cli(argv, output_json=False)
            second_status, second_stdout, second_stderr = run_cli(argv, output_json=False)

            self.assertEqual(first_status, 0, first_stderr)
            self.assertEqual(second_status, 0, second_stderr)
            self.assertEqual(json.loads(first_stdout)["thread_key"], "hermes:c1:m1")
            self.assertNotIn("platform", json.loads(first_stdout))


if __name__ == "__main__":
    unittest.main()
