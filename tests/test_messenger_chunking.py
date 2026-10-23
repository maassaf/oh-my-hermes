"""Messenger rendering needs REAL per-platform character ceilings.

Before this contract, `_messenger_chunking_hint()` took no arguments and
returned one global advisory number (`max_recommended_chars: 1800`) for
every platform. Discord, Slack, and Telegram each enforce a different hard
per-message character cap, so a single global number either under-warns on
generous platforms or over-warns on tight ones, and an adapter never had an
enforceable stop -- only advice.

This also covers `density_policy`, a *declared* (not enforced) markdown
saturation policy added alongside the existing `prefix_policy`/`table_policy`
advisories. There was previously no markdown-density or saturation control
on the chat rendering path at all: `context_budget` protects the supervising
agent's context window, not a human reader's chat bubble.
"""

from __future__ import annotations

import unittest

from omh.wrapper.contract import messenger_rendering_contract
from omh.wrapper.route_hints import build_chat_route_hint_payload

# Mirrors the ceilings declared in `omh.wrapper.contract`. Kept here as
# expected values, not re-derived, so the test actually pins the numbers
# rather than re-implementing the lookup.
_CEILINGS = {
    "discord": {"max_recommended_chars": 1700, "hard_limit_chars": 1900},
    "slack": {"max_recommended_chars": 2700, "hard_limit_chars": 2900},
    "telegram": {"max_recommended_chars": 3700, "hard_limit_chars": 3900},
}
_GENERIC_CEILING = {"max_recommended_chars": 1600, "hard_limit_chars": 1800}


def _contract(**overrides: object) -> dict:
    kwargs: dict[str, object] = dict(
        visible_prefix="[omh] board",
        first_line="Status update",
        body="body text",
        claim_boundary="metadata only",
    )
    kwargs.update(overrides)
    return messenger_rendering_contract(**kwargs)


class PerPlatformChunkingCeilingTests(unittest.TestCase):
    def test_each_known_platform_gets_its_own_ceiling(self) -> None:
        for source, expected in _CEILINGS.items():
            with self.subTest(source=source):
                chunking = _contract(source=source)["chunking"]
                self.assertEqual(chunking["max_recommended_chars"], expected["max_recommended_chars"])
                self.assertEqual(chunking["hard_limit_chars"], expected["hard_limit_chars"])

    def test_hermes_and_generic_share_the_generic_ceiling(self) -> None:
        for source in ("hermes", "generic"):
            with self.subTest(source=source):
                chunking = _contract(source=source)["chunking"]
                self.assertEqual(chunking["max_recommended_chars"], _GENERIC_CEILING["max_recommended_chars"])
                self.assertEqual(chunking["hard_limit_chars"], _GENERIC_CEILING["hard_limit_chars"])

    def test_absent_source_falls_back_to_the_generic_ceiling(self) -> None:
        # `source` is optional so no existing caller breaks; the default must
        # resolve to the same generic ceiling as an explicitly unknown one.
        chunking = _contract()["chunking"]
        self.assertEqual(chunking["max_recommended_chars"], _GENERIC_CEILING["max_recommended_chars"])
        self.assertEqual(chunking["hard_limit_chars"], _GENERIC_CEILING["hard_limit_chars"])

    def test_unknown_source_falls_back_to_the_generic_ceiling(self) -> None:
        for source in ("whatsapp", "signal", ""):
            with self.subTest(source=source):
                chunking = _contract(source=source)["chunking"]
                self.assertEqual(chunking["max_recommended_chars"], _GENERIC_CEILING["max_recommended_chars"])
                self.assertEqual(chunking["hard_limit_chars"], _GENERIC_CEILING["hard_limit_chars"])

    def test_recommended_is_always_strictly_below_the_hard_limit(self) -> None:
        for source in ("discord", "slack", "telegram", "hermes", "generic", "unknown", ""):
            with self.subTest(source=source):
                chunking = _contract(source=source)["chunking"]
                self.assertLess(chunking["max_recommended_chars"], chunking["hard_limit_chars"])

    def test_fresh_dict_per_call_is_preserved(self) -> None:
        # A caller embedding the chunking hint in a payload must not be able
        # to mutate it for every other caller.
        first = _contract(source="discord")["chunking"]
        first["hard_limit_chars"] = -1
        second = _contract(source="discord")["chunking"]
        self.assertEqual(second["hard_limit_chars"], 1900)


class DensityPolicyTests(unittest.TestCase):
    def test_density_policy_is_present_on_both_render_profiles(self) -> None:
        for profile in ("limited_markdown", "rich_markdown"):
            with self.subTest(profile=profile):
                density_policy = _contract(render_profile=profile)["density_policy"]
                self.assertEqual(density_policy["max_heading_levels"], 2)
                self.assertEqual(density_policy["max_bullets"], 12)
                self.assertIn("nested_bullets", density_policy["avoid"])
                self.assertIn("bold_inside_bullets", density_policy["avoid"])
                self.assertIn("tables_on_limited_profiles", density_policy["avoid"])


class RouteHintChunkingSurvivesTheKeyWhitelistTests(unittest.TestCase):
    """`build_chat_route_hint_payload` builds its `messenger_rendering` block
    as a hand-rolled literal, not via `messenger_rendering_contract`. A key
    added upstream to `_messenger_chunking_hint` is only visible in the
    emitted hint if the literal actually forwards `source` into that call --
    checking the source text is not enough, only the emitted payload proves
    the key survives.
    """

    def test_hard_limit_chars_reaches_the_emitted_route_hint_for_every_source(self) -> None:
        for source, expected in _CEILINGS.items():
            with self.subTest(source=source):
                payload = build_chat_route_hint_payload("please review my pull request diff", source=source)
                chunking = payload["chat_response"]["messenger_rendering"]["chunking"]
                self.assertIn("hard_limit_chars", chunking)
                self.assertEqual(chunking["max_recommended_chars"], expected["max_recommended_chars"])
                self.assertEqual(chunking["hard_limit_chars"], expected["hard_limit_chars"])

    def test_generic_source_falls_back_in_the_route_hint_too(self) -> None:
        payload = build_chat_route_hint_payload("please review my pull request diff", source="generic")
        chunking = payload["chat_response"]["messenger_rendering"]["chunking"]
        self.assertEqual(chunking["max_recommended_chars"], _GENERIC_CEILING["max_recommended_chars"])
        self.assertEqual(chunking["hard_limit_chars"], _GENERIC_CEILING["hard_limit_chars"])


def _fences_balanced(chunk: str) -> bool:
    open_marker = ""
    for line in chunk.splitlines():
        stripped = line.strip()
        if open_marker:
            character = open_marker[0]
            if len(stripped) >= len(open_marker) and stripped == character * len(stripped):
                open_marker = ""
            continue
        for character in ("`", "~"):
            if stripped.startswith(character * 3):
                length = len(stripped) - len(stripped.lstrip(character))
                open_marker = character * length
                break
    return not open_marker


class DeterministicChunkListTests(unittest.TestCase):
    """`chunked_body_texts` gives adapters ready-made chunks for the resolved
    platform instead of a ceiling number and a guess."""

    def test_a_body_that_fits_is_a_single_chunk(self) -> None:
        contract = _contract(source="discord")
        self.assertEqual(contract["chunked_body_texts"], [contract["body_text"]])

    def test_a_long_body_splits_at_paragraph_boundaries_under_the_soft_ceiling(self) -> None:
        paragraphs = [f"Paragraph {index}: " + ("evidence sentence. " * 20).strip() for index in range(12)]
        body = "\n\n".join(paragraphs)
        contract = _contract(source="discord", body=body)
        chunks = contract["chunked_body_texts"]
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), _CEILINGS["discord"]["max_recommended_chars"])
            self.assertTrue(chunk.startswith("Paragraph "))
        self.assertEqual("\n\n".join(chunks), body)

    def test_a_fence_split_across_chunks_is_closed_and_reopened(self) -> None:
        rows = "\n".join(f"row-{index:04d}  value-{index:04d}" for index in range(120))
        body = f"```text\n{rows}\n```"
        contract = _contract(source="discord", body=body)
        chunks = contract["chunked_body_texts"]
        self.assertGreater(len(chunks), 1)
        for index, chunk in enumerate(chunks):
            self.assertLessEqual(len(chunk), _CEILINGS["discord"]["max_recommended_chars"])
            self.assertTrue(_fences_balanced(chunk), chunk[:80])
            self.assertTrue(chunk.rstrip().endswith("```"))
            if index > 0:
                # Reopened fences never repeat the language tag.
                self.assertTrue(chunk.startswith("```\n"), chunk[:20])

    def test_every_platform_ceiling_bounds_its_own_chunks(self) -> None:
        body = "\n\n".join(("Paragraph body sentence. " * 15).strip() for _ in range(20))
        for source, expected in _CEILINGS.items():
            with self.subTest(source=source):
                chunks = _contract(source=source, body=body)["chunked_body_texts"]
                for chunk in chunks:
                    self.assertLessEqual(len(chunk), expected["max_recommended_chars"])

    def test_an_unbroken_line_is_hard_split_as_a_last_resort(self) -> None:
        body = "x" * 5000
        chunks = _contract(source="discord", body=body)["chunked_body_texts"]
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), _CEILINGS["discord"]["max_recommended_chars"])
        self.assertEqual("".join(chunks), body)

    def test_the_fence_split_reserves_room_for_the_closing_fence_line(self) -> None:
        # Kills the close-cost mutant (`len(candidate) <= limit` instead of
        # `len(candidate) + close_cost <= limit`), which is byte-identical on
        # every other fixture in this file. Rows are exactly 120 chars, so
        # after the opening ``` plus n rows the running chunk is 3 + n*121
        # chars. n=14 gives 1697, which fits Discord's 1700 ceiling raw but
        # NOT once the closing "\n```" (4 chars) is appended (1701). The
        # split must land one row earlier: 13 rows in the first chunk, and
        # every emitted chunk (closing and reopened fence lines included)
        # stays within the ceiling.
        rows = [f"row-{index:03d}-" + "v" * 112 for index in range(20)]
        self.assertTrue(all(len(row) == 120 for row in rows))
        body = "```\n" + "\n".join(rows) + "\n```"
        contract = _contract(source="discord", body=body)
        # The arithmetic above is grounded in the chunked text being the
        # body byte-identically (bare fence, nothing for transforms to do).
        self.assertEqual(contract["body_text"], body)
        chunks = contract["chunked_body_texts"]
        self.assertEqual(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), _CEILINGS["discord"]["max_recommended_chars"])
            self.assertTrue(_fences_balanced(chunk), chunk[:80])
        self.assertEqual(chunks[0].count("row-"), 13)
        self.assertTrue(chunks[0].rstrip().endswith("```"))
        self.assertTrue(chunks[1].startswith("```\nrow-013-"), chunks[1][:20])


class SlackDialectBodyTests(unittest.TestCase):
    """The resolved-slack body is mrkdwn: no '#' headings, no '**', links as
    <url|text>, conversions applied outside fences only."""

    _BODY = "\n".join(
        (
            "## Result summary",
            "",
            "This change is **ready** for review, see [the PR](https://example.test/pr/7).",
            "",
            "```python",
            "# not a heading, **not bold**",
            "value = '[x](y)'",
            "```",
        )
    )

    def _slack_contract(self) -> dict:
        return _contract(source="slack", body=self._BODY)

    def test_headings_become_bold_lines(self) -> None:
        body_text = self._slack_contract()["body_text"]
        self.assertIn("*Result summary*", body_text)
        self.assertNotIn("## ", body_text)

    def test_double_star_bold_becomes_single_star_outside_fences(self) -> None:
        body_text = self._slack_contract()["body_text"]
        self.assertIn("is *ready* for review", body_text)
        self.assertNotIn("**ready**", body_text)

    def test_links_become_slack_angle_form(self) -> None:
        self.assertIn("<https://example.test/pr/7|the PR>", self._slack_contract()["body_text"])

    def test_a_link_whose_url_contains_balanced_parens_survives(self) -> None:
        # A URL group stopping at the first ")" would emit a broken
        # "<...Rust_(programming_language|the spec>) ..." with a stray paren.
        body = "See [the spec](https://en.wikipedia.org/wiki/Rust_(programming_language)) for details."
        body_text = _contract(source="slack", body=body)["body_text"]
        self.assertEqual(
            body_text,
            "See <https://en.wikipedia.org/wiki/Rust_(programming_language)|the spec> for details.",
        )

    def test_an_unparseable_link_line_is_left_unchanged(self) -> None:
        body = "See [broken](not a real url) for details."
        self.assertEqual(_contract(source="slack", body=body)["body_text"], body)

    def test_inline_code_spans_keep_their_bytes(self) -> None:
        # `**kwargs` inside backticks is code, not bold markup; halving it to
        # `*kwargs` corrupts the code the reader is meant to copy.
        body = "Pass `**kwargs` and `**extra` to the call."
        contract = _contract(source="slack", body=body)
        self.assertEqual(contract["body_text"], body)
        self.assertNotIn("slack_dialect_markdown", contract["transforms_applied"])

    def test_a_link_shape_inside_inline_code_is_untouched(self) -> None:
        body = "`[x](y)` in code"
        self.assertEqual(_contract(source="slack", body=body)["body_text"], body)

    def test_mixed_prose_and_inline_code_converts_only_the_prose(self) -> None:
        body = "use **bold** and `**kwargs**`"
        self.assertEqual(
            _contract(source="slack", body=body)["body_text"],
            "use *bold* and `**kwargs**`",
        )

    def test_fenced_content_is_left_byte_identical(self) -> None:
        body_text = self._slack_contract()["body_text"]
        self.assertIn("# not a heading, **not bold**", body_text)
        self.assertIn("value = '[x](y)'", body_text)

    def test_fence_language_tags_are_stripped_on_every_limited_profile(self) -> None:
        for source in ("slack", "discord", "telegram"):
            with self.subTest(source=source):
                contract = _contract(source=source, body=self._BODY)
                self.assertNotIn("```python", contract["body_text"])
                code_blocks = [block for block in contract["body_blocks"] if block["type"] == "code_block"]
                self.assertEqual(code_blocks[0]["language"], "python")

    def test_transforms_record_the_dialect_and_strip(self) -> None:
        transforms = self._slack_contract()["transforms_applied"]
        self.assertIn("slack_dialect_markdown", transforms)
        self.assertIn("fence_language_tags_stripped", transforms)

    def test_non_slack_sources_do_not_get_the_dialect(self) -> None:
        contract = _contract(source="discord", body=self._BODY)
        self.assertIn("## Result summary", contract["body_text"])
        self.assertNotIn("slack_dialect_markdown", contract["transforms_applied"])

    def test_rich_profile_bodies_are_untouched(self) -> None:
        contract = _contract(source="hermes", render_profile="rich_markdown", body=self._BODY)
        self.assertEqual(contract["body_text"], self._BODY)
        self.assertEqual(contract["transforms_applied"], [])


if __name__ == "__main__":
    unittest.main()
