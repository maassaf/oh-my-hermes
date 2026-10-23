from __future__ import annotations

from domain_routing_context_support import _contract_module


class DomainPhraseMatcherMixin:
    def test_required_unicode_boundary_vectors(self) -> None:
        matches = _contract_module().matches_reviewed_phrase
        vectors = (
            ("ＰＩＰＥＬＩＮＥ REVIEW", "pipeline review", True),
            ("(고객 세그먼트) 검토", "고객 세그먼트", True),
            ("pipeline reviewer", "pipeline review", False),
            ("고객 세그먼트화", "고객 세그먼트", False),
            ("pipeline-review", "pipeline review", False),
            ("sales-development", "pipeline review", False),
        )
        for message, phrase, expected in vectors:
            with self.subTest(message=message, phrase=phrase):
                self.assertEqual(matches(message, phrase), expected)

    def test_normalization_collapses_unicode_whitespace_and_casefolds(self) -> None:
        matches = _contract_module().matches_reviewed_phrase
        self.assertTrue(matches("  STRASSE\n\t review  ", "Straße review"))
        self.assertTrue(matches("pipeline\u3000review", "pipeline review"))

    def test_word_like_categories_and_underscore_enforce_boundaries(self) -> None:
        matches = _contract_module().matches_reviewed_phrase
        for message in ("xpipeline", "1pipeline", "_pipeline", "\u0301pipeline"):
            with self.subTest(message=message):
                self.assertFalse(matches(message, "pipeline"))
        for message in ("pipelinex", "pipeline1", "pipeline_", "pipeline\u0301"):
            with self.subTest(message=message):
                self.assertFalse(matches(message, "pipeline"))
        self.assertTrue(matches("(pipeline).", "pipeline"))

    def test_punctuation_edge_phrase_needs_only_literal_equality(self) -> None:
        matches = _contract_module().matches_reviewed_phrase
        self.assertTrue(matches("x(renewal)!", "(renewal)"))
        self.assertFalse(matches("x renewal !", "renewal!"))

    def test_all_occurrences_are_searched_and_empty_inputs_do_not_match(self) -> None:
        matches = _contract_module().matches_reviewed_phrase
        self.assertTrue(
            matches("pipeline reviewer; pipeline review.", "pipeline review")
        )
        self.assertFalse(matches("pipeline review", ""))
        self.assertFalse(matches("", "pipeline review"))
        self.assertFalse(matches(None, "pipeline review"))
