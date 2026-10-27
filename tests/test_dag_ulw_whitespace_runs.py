"""`dag_ulw_cues` reads a long run of spaces in one pass.

`executable_routing_text` masks a fenced or quoted block to spaces, so a message that carries a log reaches the cue
regexes as one long run of them. A pattern that opens on `\\s*` and is searched (`split`, `finditer`, `sub`) re-read
that run from each of its positions: quadratic, and long enough to hold the GIL for minutes inside a `pre_llm_call`
hook, which stalls the gateway's event loop. These pin the same cuts as before and a fenced log that routes at once.
"""

from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh import dag_ulw_cues  # noqa: E402
from omh.plugin_bundle.omh.dag_ulw_cues import dispatches_dag_ulw  # noqa: E402
from omh.plugin_bundle.omh.reference_regions import executable_routing_text  # noqa: E402

# Before the fix each of these took minutes; after it, milliseconds.
FENCED_LOG = "이 로그 보고 고쳐줘\n```\n" + "build step ok\n" * 20_000 + "```\n"
SPACED_TAIL = "fix all the failing tests" + " " * 200_000 + ", ok?"


class WhitespaceRunTest(unittest.TestCase):
    def test_a_fenced_log_routes_without_rereading_its_masked_run(self) -> None:
        self.assertIsInstance(dispatches_dag_ulw(executable_routing_text(FENCED_LOG)), bool)

    def test_a_long_run_before_a_confirmation_tag_routes(self) -> None:
        self.assertIsInstance(dispatches_dag_ulw(SPACED_TAIL), bool)

    def test_clause_breaks_cut_where_they_did(self) -> None:
        cases = {
            "fix it , then test": ["fix it", "then test"],
            "fix it  ;  then test": ["fix it", "then test"],
            "a…  ; - ..b": ["a", "", "-", "b"],
            "a - b": ["a", "b"],
            "a  -  b": ["a", "b"],
            "a...b": ["a", "b"],
        }
        for text, cut in cases.items():
            with self.subTest(text=text):
                self.assertEqual(dag_ulw_cues._CLAUSE_BREAK.split(text), cut)

    def test_list_colons_cut_where_they_did(self) -> None:
        self.assertEqual([m.span() for m in dag_ulw_cues._LIST_COLON.finditer("a  :  b 10:30 c:")], [(1, 6), (15, 16)])

    def test_a_confirmation_tag_still_reads_off_with_its_spaces(self) -> None:
        self.assertEqual(dag_ulw_cues._FINAL_CONFIRMATION_TAG.sub("", "fix all the tests  ,  ok ?", count=1), "fix all the tests")
        self.assertEqual(dag_ulw_cues._FINAL_CONFIRMATION_TAG.sub("", "fix it, ok? then more", count=1), "fix it, ok? then more")


if __name__ == "__main__":
    unittest.main()
