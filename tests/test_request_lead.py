from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.data_share_cues import data_share_request_admitted
from omh.plugin_bundle.omh.hermes_session_commands import LOOP_TIMER_GOAL, LOOP_TIMER_NATIVE, loop_timer_verdict
from omh.plugin_bundle.omh.request_lead import COURTESY_OPENERS, after_courtesy, courtesy_opener_length
from omh.plugin_bundle.omh.spend_signals import SPEND_GATE_PHRASES, SPEND_REQUEST_MARKERS, spend_request_clause
from omh.routing.localization import normalized_phrase
from omh.routing.policy import _command_words


class CourtesyOpenerTests(unittest.TestCase):
    """One list of openers, read the same way by every reader of a request's opening."""

    def test_every_reader_looks_past_every_opener(self) -> None:
        # Before, each reader kept its own list: data-share-review admitted
        # "hey, redact pii ..." and refused "hi,", "pls" and "would you".
        for opener in COURTESY_OPENERS:
            lead = " ".join(opener) + ", "
            with self.subTest(opener=lead):
                self.assertTrue(
                    data_share_request_admitted(lead + "redact pii in the export before we send it to the vendor")
                )
                # An amount marks this spend whatever opened it.
                self.assertTrue(
                    spend_request_clause(normalized_phrase(lead + "transfer 40 dollars from my wallet to my landlord"), SPEND_GATE_PHRASES)
                )
                self.assertEqual(loop_timer_verdict(lead + "loop every 5 minutes and check ci"), LOOP_TIMER_NATIVE)
                self.assertEqual(_command_words(normalized_phrase(lead + "review this pr"))[:1], ["review"])

    def test_openers_repeat_and_take_their_punctuation(self) -> None:
        self.assertEqual(after_courtesy("hi hermes, can you please buy it"), "buy it")
        self.assertEqual(after_courtesy("ok. go ahead and pay"), "pay")
        self.assertEqual(after_courtesy("please,loop every 5m"), "loop every 5m")
        self.assertEqual(after_courtesy("please"), "")
        self.assertEqual(courtesy_opener_length(["go", "ahead", "and", "pay"]), 3)
        self.assertEqual(courtesy_opener_length(["i", "need", "you", "to", "pay"]), 4)
        self.assertEqual(courtesy_opener_length(["pay", "it"]), 0)

    def test_an_opener_counts_only_as_whole_words(self) -> None:
        # A word that runs on past the opener, and a sigil right after it, keep
        # the message's first word.
        for text in (
            "hermes's config is wrong",
            "hermes-agent crashed",
            "so-called experts",
            "okayish results",
            "history of the project",
            "ok/loop 5m check ci",
            "nowhere to be found",
        ):
            with self.subTest(text=text):
                self.assertEqual(after_courtesy(text), text)
        # Only the punctuation that ends a word goes with the opener, and a "."
        # right before a "/" does not: OMH's `./loop` keeps its sigil, spaced
        # or not, and stays OMH's loop.
        for text in ("ok ./loop every 5m check ci", "ok./loop every 5m check ci"):
            with self.subTest(text=text):
                self.assertEqual(after_courtesy(text), "./loop every 5m check ci")
                self.assertEqual(loop_timer_verdict(text), LOOP_TIMER_GOAL)
        self.assertEqual(after_courtesy("ok. please buy it"), "buy it")

    def test_every_spend_request_marker_is_an_opener(self) -> None:
        # A marker is read only among the openers stripped before the lead, so
        # a marker that is not an opener could never be found.
        self.assertEqual(set(SPEND_REQUEST_MARKERS) - set(COURTESY_OPENERS), set())


if __name__ == "__main__":
    unittest.main()
