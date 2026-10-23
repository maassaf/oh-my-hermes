"""Fenced code must survive the trip to a messenger.

Before this contract existed, a fenced block collapsed into a single
`paragraph` with its newlines flattened to spaces, on BOTH render profiles.
Any adapter that consumes `body_blocks` therefore lost column alignment --
which is the whole point of putting a status table in a fence.
"""

from __future__ import annotations

import unittest

from omh.wrapper.contract import messenger_rendering_contract

_ALIGNED = "\n".join(
    (
        "unit              runtime      model          status",
        "research-sweep    claude-code  opus xhigh     running",
        "api-ratelimit     codex        gpt-5.6-sol    running",
    )
)
_BODY = f"Running work\n\n```text\n{_ALIGNED}\n```\n\nBoundary: metadata only"

PROFILES = ("limited_markdown", "rich_markdown")


def _contract(body: str, profile: str) -> dict:
    return messenger_rendering_contract(
        visible_prefix="[omh] board",
        first_line="Running work",
        body=body,
        claim_boundary="metadata only",
        render_profile=profile,
    )


def _blocks_of_type(contract: dict, block_type: str) -> list[dict]:
    return [block for block in contract["body_blocks"] if block["type"] == block_type]


class FencedCodeSurvivesTests(unittest.TestCase):
    def test_a_fence_becomes_one_code_block_on_every_profile(self) -> None:
        for profile in PROFILES:
            with self.subTest(profile=profile):
                code_blocks = _blocks_of_type(_contract(_BODY, profile), "code_block")
                self.assertEqual(len(code_blocks), 1)

    def test_newlines_and_alignment_are_preserved_verbatim(self) -> None:
        for profile in PROFILES:
            with self.subTest(profile=profile):
                block = _blocks_of_type(_contract(_BODY, profile), "code_block")[0]
                self.assertEqual(block["text"], _ALIGNED)
                self.assertIn("\n", block["text"])

    def test_the_fence_language_is_carried(self) -> None:
        for profile in PROFILES:
            with self.subTest(profile=profile):
                block = _blocks_of_type(_contract(_BODY, profile), "code_block")[0]
                self.assertEqual(block["language"], "text")

    def test_a_fence_without_a_language_reports_an_empty_one(self) -> None:
        body = "Heading\n\n```\nalpha  beta\n```"
        for profile in PROFILES:
            with self.subTest(profile=profile):
                block = _blocks_of_type(_contract(body, profile), "code_block")[0]
                self.assertEqual(block["language"], "")
                self.assertEqual(block["text"], "alpha  beta")

    def test_leading_whitespace_inside_a_fence_is_kept(self) -> None:
        body = "Tree\n\n```\nroot\n    child\n        leaf\n```"
        for profile in PROFILES:
            with self.subTest(profile=profile):
                block = _blocks_of_type(_contract(body, profile), "code_block")[0]
                self.assertEqual(block["text"], "root\n    child\n        leaf")

    def test_a_blank_line_inside_a_fence_does_not_split_the_block(self) -> None:
        body = "Log\n\n```\nfirst\n\nsecond\n```"
        for profile in PROFILES:
            with self.subTest(profile=profile):
                blocks = _blocks_of_type(_contract(body, profile), "code_block")
                self.assertEqual(len(blocks), 1)
                self.assertEqual(blocks[0]["text"], "first\n\nsecond")

    def test_an_unterminated_fence_still_keeps_its_shape(self) -> None:
        # Falling back to prose would reflow exactly the content the fence was
        # protecting, which is the failure this whole contract exists to stop.
        body = "Truncated\n\n```\nalpha  beta\ngamma  delta"
        for profile in PROFILES:
            with self.subTest(profile=profile):
                blocks = _blocks_of_type(_contract(body, profile), "code_block")
                self.assertEqual(len(blocks), 1)
                self.assertEqual(blocks[0]["text"], "alpha  beta\ngamma  delta")

    def test_prose_around_a_fence_still_becomes_ordinary_blocks(self) -> None:
        for profile in PROFILES:
            with self.subTest(profile=profile):
                types = [block["type"] for block in _contract(_BODY, profile)["body_blocks"]]
                self.assertEqual(types, ["paragraph", "code_block", "paragraph"])

    def test_bullets_and_numbers_are_unaffected_by_the_fence_handling(self) -> None:
        body = "Intro\n\n- first\n- second\n\n1. one\n2. two"
        for profile in PROFILES:
            with self.subTest(profile=profile):
                contract = _contract(body, profile)
                self.assertEqual(len(_blocks_of_type(contract, "bullet")), 2)
                self.assertEqual(len(_blocks_of_type(contract, "numbered")), 2)
                self.assertEqual(_blocks_of_type(contract, "code_block"), [])

    def test_a_body_with_no_fence_emits_no_code_block(self) -> None:
        for profile in PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(_blocks_of_type(_contract("Just prose here.", profile), "code_block"), [])

    def test_only_code_blocks_carry_a_language_key(self) -> None:
        for profile in PROFILES:
            with self.subTest(profile=profile):
                for block in _contract(_BODY, profile)["body_blocks"]:
                    if block["type"] != "code_block":
                        self.assertNotIn("language", block)


class CodeBlockIsPreferredEverywhereTests(unittest.TestCase):
    def test_both_profiles_prefer_code_blocks(self) -> None:
        # Discord, Slack, and Telegram all render triple-backtick fences, so the
        # limited profile has no reason to avoid them the way it avoids tables.
        for profile in PROFILES:
            with self.subTest(profile=profile):
                contract = _contract(_BODY, profile)
                self.assertIn("code_block", contract["preferred_blocks"])
                self.assertNotIn("code_block", contract["avoid_blocks"])

    def test_the_limited_profile_still_avoids_tables(self) -> None:
        contract = _contract(_BODY, "limited_markdown")
        self.assertIn("markdown_table", contract["avoid_blocks"])
        self.assertNotIn("markdown_table", contract["preferred_blocks"])

    def test_the_fallback_blocks_keep_the_fence_too(self) -> None:
        # `fallback_body_blocks` is what an adapter uses when it cannot render
        # the primary format; it must not be the one that loses alignment.
        for profile in PROFILES:
            with self.subTest(profile=profile):
                fallback = _contract(_BODY, profile)["fallback_body_blocks"]
                code_blocks = [block for block in fallback if block["type"] == "code_block"]
                self.assertEqual(len(code_blocks), 1)
                self.assertEqual(code_blocks[0]["text"], _ALIGNED)


class FencePairingTests(unittest.TestCase):
    """CommonMark-style pairing: only a closing fence of the same marker with
    at least the opening run length (and no info string) closes a block, so a
    fenced block that QUOTES another fence survives as one code block."""

    def test_a_fence_containing_an_inner_backtick_fence_stays_one_block(self) -> None:
        inner = "```python\nprint('hi')\n```"
        body = f"Quoting a fence\n\n````markdown\n{inner}\n````\n\nAfter"
        for profile in PROFILES:
            with self.subTest(profile=profile):
                blocks = _blocks_of_type(_contract(body, profile), "code_block")
                self.assertEqual(len(blocks), 1)
                self.assertEqual(blocks[0]["text"], inner)
                self.assertEqual(blocks[0]["language"], "markdown")

    def test_a_tilde_fence_contains_backtick_fence_lines_as_content(self) -> None:
        inner = "```text\naligned  columns\n```"
        body = f"Tilde\n\n~~~\n{inner}\n~~~"
        for profile in PROFILES:
            with self.subTest(profile=profile):
                blocks = _blocks_of_type(_contract(body, profile), "code_block")
                self.assertEqual(len(blocks), 1)
                self.assertEqual(blocks[0]["text"], inner)

    def test_a_shorter_run_does_not_close_a_longer_opening(self) -> None:
        body = "Head\n\n````\n```\nstill inside\n````"
        for profile in PROFILES:
            with self.subTest(profile=profile):
                blocks = _blocks_of_type(_contract(body, profile), "code_block")
                self.assertEqual(len(blocks), 1)
                self.assertEqual(blocks[0]["text"], "```\nstill inside")

    def test_an_unterminated_outer_fence_keeps_inner_fence_lines(self) -> None:
        body = "Truncated\n\n````python\n```\ninner  content"
        for profile in PROFILES:
            with self.subTest(profile=profile):
                blocks = _blocks_of_type(_contract(body, profile), "code_block")
                self.assertEqual(len(blocks), 1)
                self.assertEqual(blocks[0]["text"], "```\ninner  content")
                self.assertEqual(blocks[0]["language"], "python")

    def test_a_backtick_fence_line_with_backtick_info_is_content(self) -> None:
        # CommonMark: a backtick fence cannot carry a backtick in its info
        # string, so such a line cannot open a fence and stays prose.
        body = "Para\n``` a ` b\nmore prose"
        for profile in PROFILES:
            with self.subTest(profile=profile):
                blocks = _blocks_of_type(_contract(body, profile), "code_block")
                self.assertEqual(blocks, [])

    def test_an_interior_fence_line_with_info_does_not_close_the_block(self) -> None:
        body = "Head\n\n```\n``` a ` b\n```"
        for profile in PROFILES:
            with self.subTest(profile=profile):
                blocks = _blocks_of_type(_contract(body, profile), "code_block")
                self.assertEqual(len(blocks), 1)
                self.assertEqual(blocks[0]["text"], "``` a ` b")


class StatusBoardOnAMessengerScreenTests(unittest.TestCase):
    """What Discord/Slack/Telegram actually receive for a running-work board.

    OMH never posts: it produces the contract and an adapter sends it. These
    assertions therefore pin the emitted bytes and the per-platform fit, which
    is the whole of what this package controls.
    """

    def _board_contract(self, source: str, profile: str) -> dict:
        from omh.coding.status_board import (
            CODING_STATUS_BOARD_CLAIM_BOUNDARY,
            CODING_STATUS_BOARD_SHORT_BOUNDARY,
            status_board_messenger_body,
        )

        payload = {
            "schema_version": "omh_coding_status_board/v1",
            "observed_at": "2026-08-03T04:35:00Z",
            "unit_count": 2,
            "running_count": 2,
            "claim_boundary": CODING_STATUS_BOARD_CLAIM_BOUNDARY,
            "units": [
                {
                    "label": "api-ratelimit",
                    "runtime": "codex",
                    "model_label": "gpt-5.6-sol xhigh",
                    "status": "running",
                    "elapsed_text": "35m",
                    "tokens_text": "128,400",
                    "session_ref": "019a7b3e",
                    "summary": "",
                },
                {
                    "label": "research-sweep",
                    "runtime": "claude-code",
                    "model_label": "opus xhigh",
                    "status": "running",
                    "elapsed_text": "4m",
                    "tokens_text": "unknown",
                    "session_ref": "unknown",
                    "summary": "",
                },
            ],
        }
        body = status_board_messenger_body(payload, render_profile=profile)
        self.short_boundary = CODING_STATUS_BOARD_SHORT_BOUNDARY
        self.full_boundary = CODING_STATUS_BOARD_CLAIM_BOUNDARY
        return messenger_rendering_contract(
            visible_prefix="[omh] running-work-board",
            first_line="Running work",
            body=body,
            claim_boundary=CODING_STATUS_BOARD_CLAIM_BOUNDARY,
            render_profile=profile,
            source=source,
        )

    def test_the_board_fits_one_message_on_every_messenger(self) -> None:
        for source, profile in (
            ("discord", "limited_markdown"),
            ("slack", "limited_markdown"),
            ("telegram", "limited_markdown"),
            ("hermes", "rich_markdown"),
        ):
            with self.subTest(source=source):
                contract = self._board_contract(source, profile)
                posted = "\n".join(
                    [contract["visible_prefix"], contract["first_line"], contract["body_text"]]
                )
                self.assertLessEqual(len(posted), contract["chunking"]["hard_limit_chars"])

    def test_the_limited_profile_emits_bullets_and_never_a_table(self) -> None:
        contract = self._board_contract("discord", "limited_markdown")
        types = [block["type"] for block in contract["body_blocks"]]
        self.assertIn("bullet", types)
        self.assertNotIn("markdown_table", types)

    def test_the_rich_profile_keeps_the_table_monospaced_and_the_caveat_prose(self) -> None:
        # A boundary paragraph inside the fence gets monospace-wrapped at the
        # table's width, which reads as broken rendering rather than a caveat.
        contract = self._board_contract("hermes", "rich_markdown")
        types = [block["type"] for block in contract["body_blocks"]]
        self.assertEqual(types, ["code_block", "paragraph"])
        code = next(b for b in contract["body_blocks"] if b["type"] == "code_block")
        self.assertNotIn(self.short_boundary, code["text"])

    def test_the_messenger_body_carries_the_short_boundary_not_the_full_one(self) -> None:
        # The full text was 53% of a two-row board -- boilerplate a reader
        # scrolls past to reach the data. It stays in the payload and --json.
        for profile in ("limited_markdown", "rich_markdown"):
            with self.subTest(profile=profile):
                contract = self._board_contract("discord", profile)
                self.assertIn(self.short_boundary, contract["body_text"])
                self.assertNotIn(self.full_boundary, contract["body_text"])

    def test_runtime_and_model_read_as_one_field(self) -> None:
        contract = self._board_contract("discord", "limited_markdown")
        self.assertIn("codex (gpt-5.6-sol xhigh)", contract["body_text"])
        self.assertNotIn("codex — (", contract["body_text"])

    def test_the_board_uses_no_markup_that_needs_per_dialect_escaping(self) -> None:
        # No bold, italics, links, or headings means no Slack mrkdwn or
        # Telegram MarkdownV2 conversion is required for this surface.
        contract = self._board_contract("slack", "limited_markdown")
        for marker in ("**", "__", "###", "](", "~~"):
            self.assertNotIn(marker, contract["body_text"], marker)


if __name__ == "__main__":
    unittest.main()
