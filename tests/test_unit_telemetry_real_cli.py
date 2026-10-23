"""Telemetry parsed from output CAPTURED FROM THE REAL CLIs.

Every other telemetry test uses a stream this repo wrote. These fixtures were
captured by actually running the installed binaries on 2026-08-03:

    claude -p "Reply with exactly: ok" --output-format json
    codex exec --json --skip-git-repo-check "Reply with exactly: ok"

That distinction earned its keep immediately. The synthetic streams carried no
cache fields, so the parser reported `input_tokens: 2` for a claude run that
actually consumed roughly 29,700 input tokens -- wrong by four orders of
magnitude, and invisible until a real binary was run.

These fixtures are trimmed of the assistant's reply text and of fields the
parser never reads, but every key it DOES read is verbatim.
"""

from __future__ import annotations

import json
import unittest

from omh.coding.status_board import UNKNOWN, _reported_tokens
from omh.coding.unit_telemetry import parse_unit_telemetry

# Verbatim `usage`, `session_id`, and `type` from a real run.
REAL_CLAUDE_JSON = json.dumps(
    {
        "is_error": False,
        "num_turns": 1,
        "stop_reason": "end_turn",
        "session_id": "c5721579-07d5-4546-80b2-536c36121f7d",
        "total_cost_usd": 0.1521565,
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 14441,
            "cache_read_input_tokens": 15273,
            "output_tokens": 4,
            "service_tier": "standard",
        },
        "subtype": "success",
        "result": "ok",
        "type": "result",
        "duration_ms": 2968,
    }
)

# Verbatim JSONL lines from a real run, including the error event codex emitted
# mid-stream, which the parser must ignore without losing the usage that
# follows it.
REAL_CODEX_JSONL = "\n".join(
    [
        json.dumps({"type": "thread.started", "thread_id": "019fc601-f5f4-7b33-89b9-b508d6ad0ca9"}),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "error", "message": "Skill descriptions were shortened"},
            }
        ),
        json.dumps({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "ok"}}),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 27305,
                    "cached_input_tokens": 6912,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 0,
                },
            }
        ),
    ]
)


class RealClaudeOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = parse_unit_telemetry("claude-code", REAL_CLAUDE_JSON)

    def test_the_real_stream_parses(self) -> None:
        self.assertTrue(self.parsed["parsed"])
        self.assertEqual(self.parsed["source"], "claude_json")

    def test_the_top_level_session_id_is_read(self) -> None:
        self.assertEqual(self.parsed["session_ref"], "c5721579-07d5-4546-80b2-536c36121f7d")

    def test_cache_tokens_are_not_dropped(self) -> None:
        # The whole point: input_tokens alone understates this run 4 orders of
        # magnitude, and only a real binary revealed it.
        self.assertEqual(self.parsed["input_tokens"], 2)
        self.assertEqual(self.parsed["cache_read_tokens"], 15273)
        self.assertEqual(self.parsed["cache_write_tokens"], 14441)

    def test_billable_sums_the_components_the_cli_stated(self) -> None:
        self.assertEqual(self.parsed["tokens_billable"], 2 + 4 + 15273 + 14441)
        self.assertEqual(self.parsed["tokens_billable_source"], "summed_reported_components")

    def test_no_provider_total_is_invented(self) -> None:
        # claude reports no `total_tokens`; the key must stay absent rather
        # than being aliased from the sum.
        self.assertNotIn("tokens_total", self.parsed)


class RealCodexOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = parse_unit_telemetry("codex", REAL_CODEX_JSONL)

    def test_the_real_stream_parses(self) -> None:
        self.assertTrue(self.parsed["parsed"])
        self.assertEqual(self.parsed["source"], "codex_json")

    def test_thread_id_is_the_session_ref(self) -> None:
        # codex names it `thread_id`, not `session_id`.
        self.assertEqual(self.parsed["session_ref"], "019fc601-f5f4-7b33-89b9-b508d6ad0ca9")

    def test_codex_cache_vocabulary_is_normalized(self) -> None:
        # `cached_input_tokens` / `cache_write_input_tokens` are codex's names
        # for what claude calls cache_read / cache_creation.
        self.assertEqual(self.parsed["input_tokens"], 27305)
        self.assertEqual(self.parsed["cache_read_tokens"], 6912)
        self.assertEqual(self.parsed["cache_write_tokens"], 0)
        self.assertEqual(self.parsed["reasoning_tokens"], 0)

    def test_billable_sums_the_components_the_cli_stated(self) -> None:
        self.assertEqual(self.parsed["tokens_billable"], 27305 + 5 + 6912 + 0 + 0)

    def test_no_provider_total_is_invented(self) -> None:
        self.assertNotIn("tokens_total", self.parsed)

    def test_a_mid_stream_error_event_does_not_lose_the_usage(self) -> None:
        self.assertIn("tokens_billable", self.parsed)


class BoardRendersRealCountsTests(unittest.TestCase):
    """Neither CLI reports a total, so reading only `tokens_total` left the
    board's token column `unknown` on every real run."""

    def test_the_board_falls_back_to_the_summed_components(self) -> None:
        for owner, stream, expected in (
            ("claude-code", REAL_CLAUDE_JSON, 29720),
            ("codex", REAL_CODEX_JSONL, 34222),
        ):
            with self.subTest(owner=owner):
                entry = dict(parse_unit_telemetry(owner, stream))
                self.assertEqual(_reported_tokens(entry), expected)

    def test_a_provider_total_still_wins_when_one_exists(self) -> None:
        entry = {"tokens_total": 11, "tokens_billable": 99}
        self.assertEqual(_reported_tokens(entry), 11)

    def test_a_run_that_reported_nothing_stays_unknown(self) -> None:
        self.assertEqual(_reported_tokens({}), UNKNOWN)


if __name__ == "__main__":
    unittest.main()
