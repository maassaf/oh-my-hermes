"""`research_briefing/v1` -- the reader-facing half of the research engine.

The engine's other outputs answer to a machine, so nothing in the repo used to
decide whether a document was written well. These tests pin the half of the
briefing format a function can decide: a title that is a sentence, a role label
outside the closed vocabulary, a repeated title, an intensifier, a measured
figure with no date, and a source with no class are all rejected at build time
rather than noticed by a reader.

The page renderer is pinned on the two properties that make it safe to hand to
someone: it fetches nothing when opened, and it carries its own print rules so
the reader's print dialog is what produces a PDF. OMH never claims it produced
one.
"""

from __future__ import annotations

import unittest

from omh.skills.catalog import builtin_definitions
from omh.skills.packaging import builtin_skill_reference_templates
from omh.workflows.research_briefing import (
    BRIEFING_CLAIM_BOUNDARY,
    BRIEFING_ROLE_LABELS,
    RESEARCH_BRIEFING_SCHEMA_VERSION,
    build_research_briefing,
    render_research_briefing_markdown,
    render_research_briefing_page,
    research_briefing_errors,
)


def _sections() -> tuple[dict[str, object], ...]:
    return (
        {
            "role": "problem",
            "title": "Load growth under rising input",
            "paragraphs": (
                "Context length rose from 8k to 128k across one year, so the per-request "
                "key-value cache now dominates serving memory.",
            ),
            "figure": "req 8k   |####\nreq 128k |################################",
        },
        {
            "role": "cost",
            "title": "Retrieval precision weakens in proportion to the KV cache removed",
            "paragraphs": (
                "Grouped-query attention shares key and value heads, so it removes cache in "
                "proportion to the group size and weakens exact-match retrieval by the same factor.",
            ),
        },
    )


def _briefing(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "audience": "human_briefing",
        "language": "en",
        "question": "Which attention cache layout should the serving stack adopt?",
        "as_of": "2026-09-01",
        "output_formats": ("markdown", "page"),
        "title": "KV cache layout choice for the serving stack",
        "learning_objectives": ("Choose between MHA, GQA, and MLA for a fixed memory budget.",),
        "assumed_knowledge": ("Transformer attention at the level of query, key, and value tensors.",),
        "out_of_scope": ("Training-time memory behaviour.",),
        "sections": _sections(),
        "figures": (
            {
                "label": "KV cache per request at 128k",
                "value": "40 GB",
                "basis": "derived",
                "as_of": "2026-09-01",
            },
        ),
        "glossary": (
            {"term": "GQA", "definition": "Grouped-query attention: query heads share one key/value head per group."},
        ),
        "traps": ("Treating a cache reduction as a quality-neutral change.",),
        "sources": (
            {
                "title": "GQA paper",
                "url": "https://arxiv.org/abs/2305.13245",
                "source_class": "upstream_official",
                "retrieved_on": "2026-09-01",
            },
        ),
    }
    kwargs.update(overrides)
    return build_research_briefing(**kwargs)  # type: ignore[arg-type]


class ResearchBriefingContractTests(unittest.TestCase):
    def test_build_returns_a_validated_payload_with_prepared_exports(self) -> None:
        payload = _briefing()
        self.assertEqual(payload["schema_version"], RESEARCH_BRIEFING_SCHEMA_VERSION)
        self.assertEqual(research_briefing_errors(payload), [])
        self.assertEqual(payload["claim_boundary"], BRIEFING_CLAIM_BOUNDARY)
        # Preparing a document is never rendering one.
        self.assertEqual(payload["export"], {"markdown": "prepared", "page": "prepared"})

    def test_builder_refuses_omitted_language(self) -> None:
        with self.assertRaisesRegex((TypeError, ValueError), "language"):
            build_research_briefing(
                audience="human_briefing", question="q", as_of="2026-10-22",
                output_formats=("markdown",), sections=_sections(),
            )
        payload = _briefing()
        del payload["language"]
        for render in (render_research_briefing_markdown, render_research_briefing_page):
            with self.assertRaisesRegex(ValueError, "language"):
                render(payload)

    def test_audience_and_format_are_declared_not_inferred(self) -> None:
        with self.assertRaises(ValueError):
            _briefing(audience="whatever")
        with self.assertRaises(ValueError):
            _briefing(language="")
        # The format question belongs to the human branch alone.
        with self.assertRaises(ValueError):
            _briefing(audience="coding_agent_handoff")
        agent = _briefing(audience="coding_agent_handoff", output_formats=())
        self.assertEqual(agent["output_formats"], [])

    def test_title_rules_a_function_can_decide_are_enforced(self) -> None:
        cases = (
            ("Load rises as input grows.", "sentence"),
            ("Is the cache the problem?", "question"),
            ("3 options", "numeric scaffolding"),
        )
        for title, _why in cases:
            with self.subTest(title=title):
                with self.assertRaises(ValueError):
                    _briefing(sections=({"role": "problem", "title": title, "paragraphs": ("Body.",)},))

    def test_role_label_comes_from_the_closed_vocabulary(self) -> None:
        with self.assertRaises(ValueError):
            _briefing(sections=({"role": "summary", "title": "A real title", "paragraphs": ("Body.",)},))
        for role in BRIEFING_ROLE_LABELS:
            with self.subTest(role=role):
                payload = _briefing(
                    sections=({"role": role, "title": f"Title for {role}", "paragraphs": ("Body.",)},)
                )
                self.assertEqual(research_briefing_errors(payload), [])

    def test_repeated_titles_and_banned_prose_are_rejected(self) -> None:
        repeated = (
            {"role": "problem", "title": "Remaining problems", "paragraphs": ("One.",)},
            {"role": "limit", "title": "Remaining problems", "paragraphs": ("Two.",)},
        )
        with self.assertRaises(ValueError):
            _briefing(sections=repeated)
        with self.assertRaises(ValueError):
            _briefing(
                sections=(
                    {"role": "solution", "title": "A real title", "paragraphs": ("This is dramatically better.",)},
                )
            )
        with self.assertRaises(ValueError):
            _briefing(
                sections=({"role": "solution", "title": "A real title", "paragraphs": ("So what happens?",)},)
            )

    def test_unsafe_or_malformed_source_urls_are_refused_at_every_entry(self) -> None:
        urls = (
            "javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>x</script>",
            "file:///etc/passwd", "vbscript:msgbox(1)", "ftp://example.org/p", "//example.org/p",
            "/relative", "https:///missing-host", "https://", "https://[invalid", "https://host:bad/p",
            "https://example.org/\nx", "https://example.org/\tx", " https://example.org/p",
            "https://example.org/\\evil", "https://user:password@example.org/p", 42,
        )
        for url in urls:
            with self.subTest(url=url):
                payload = _briefing()
                source = dict(payload["sources"][0], url=url)
                with self.assertRaisesRegex(ValueError, "url"):
                    _briefing(sources=(source,))
                payload["sources"] = [source]
                self.assertTrue(any("url" in issue for issue in research_briefing_errors(payload)))
                for render in (render_research_briefing_markdown, render_research_briefing_page):
                    with self.assertRaisesRegex(ValueError, "url"):
                        render(payload)

    def test_invalid_citation_ids_are_refused_at_every_entry(self) -> None:
        # Index labels avoid stringifying an integer beyond Python's digit limit.
        invalid_ids = (True, False, 1.0, [], {}, (), b"paper", 0, -1,
                       "", " \t\n", 2**63, 10**5000)
        for index, identifier in enumerate(invalid_ids):
            with self.subTest(case=index):
                payload = _briefing()
                source = dict(payload["sources"][0], id=identifier)
                with self.assertRaisesRegex(ValueError, "source 0: id"):
                    _briefing(sources=(source,))
                payload["sources"] = [source]
                self.assertTrue(any("source 0: id" in issue
                                    for issue in research_briefing_errors(payload)))
                for render in (render_research_briefing_markdown, render_research_briefing_page):
                    with self.assertRaisesRegex(ValueError, "source 0: id"):
                        render(payload)

    def test_figures_and_sources_carry_their_basis_and_class(self) -> None:
        with self.assertRaises(ValueError):
            _briefing(figures=({"label": "x", "value": "1", "basis": "guessed"},))
        # A measured figure without a date cannot be checked later.
        with self.assertRaises(ValueError):
            _briefing(figures=({"label": "x", "value": "1", "basis": "measured"},))
        with self.assertRaises(ValueError):
            _briefing(sources=({"title": "x", "source_class": "blog", "retrieved_on": "2026-09-01"},))
        with self.assertRaises(ValueError):
            _briefing(sources=({"title": "x", "source_class": "practitioner"},))


class ResearchBriefingRenderTests(unittest.TestCase):
    def test_positive_integer_citation_ids_survive_as_visible_metadata(self) -> None:
        from html.parser import HTMLParser

        class VisibleText(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.text: list[str] = []

            def handle_data(self, data: str) -> None:
                self.text.append(data)

        for identifier in (1, 42, 2**63 - 1):
            with self.subTest(identifier=identifier):
                source = dict(_briefing()["sources"][0], id=identifier)
                payload = _briefing(sources=(source,))
                self.assertEqual(payload["sources"][0]["id"], identifier)
                self.assertEqual(research_briefing_errors(payload), [])
                markdown = render_research_briefing_markdown(payload)
                page = render_research_briefing_page(payload)
                self.assertIn(f"- [{identifier}] GQA paper", markdown)
                parser = VisibleText()
                parser.feed(page)
                self.assertIn(f"[{identifier}] GQA paper", "".join(parser.text))
                self.assertIn(f"<li>[{identifier}] GQA paper", page)
                self.assertIn("@media print", page)

    def test_markdown_follows_the_required_structure(self) -> None:
        markdown = render_research_briefing_markdown(_briefing())
        self.assertTrue(markdown.startswith("# KV cache layout choice for the serving stack"))
        for heading in (
            "## What you can do after reading",
            "## Assumed knowledge",
            "## Out of scope",
            "## Problem - Load growth under rising input",
            "## Cost - Retrieval precision weakens in proportion to the KV cache removed",
            "## Appendix A: glossary",
            "## Appendix B: misconceptions and traps",
            "## Appendix C: sources",
        ):
            self.assertIn(heading, markdown)
        # The figure is drawn, not described.
        self.assertIn("```", markdown)
        self.assertIn(BRIEFING_CLAIM_BOUNDARY, markdown)
        # Appendices come after the body.
        self.assertLess(markdown.index("## Problem -"), markdown.index("## Appendix A"))

    def test_source_urls_and_optional_ids_survive_both_renderers_as_visible_text(self) -> None:
        from html import escape, unescape
        from html.parser import HTMLParser

        class PageText(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.text: list[str] = []
                self.links: list[str] = []

            def handle_data(self, data: str) -> None:
                self.text.append(data)

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                if tag == "a":
                    self.links.append(dict(attrs)["href"])

        for scheme in ("http", "https"):
            for identifier in (None, "paper-1", "  paper-01  ", "001", "A_[B]*\\C`",
                               '<img src=x onerror="alert(1)">'):
                with self.subTest(scheme=scheme, identifier=identifier):
                    source = dict(_briefing()["sources"][0])
                    source.update(title='<script>alert(1)</script> [paper]',
                                  url=f'{scheme}://example.org/p?q="<paper>"&x=1')
                    source["id"] = identifier
                    payload = _briefing(sources=(source,))
                    markdown = render_research_briefing_markdown(payload)
                    page = render_research_briefing_page(payload)
                    parser = PageText()
                    parser.feed(page)
                    visible = "".join(parser.text)
                    self.assertIn(source["url"], visible)  # Visible on paper, not only in href.
                    self.assertIn(source["url"], parser.links)
                    self.assertIn(escape(source["url"], quote=False), markdown)
                    self.assertNotIn("<script>", page)
                    self.assertNotIn("<script>", markdown)
                    self.assertNotIn("<img", page)
                    self.assertNotIn("<img", markdown)
                    self.assertIn("@media print", page)
                    if identifier is not None:
                        self.assertIn(f"[{identifier}] ", visible)
                        self.assertIn(f"[{identifier}] ", unescape(markdown))
                    else:
                        self.assertNotIn("[None]", visible)
                        self.assertNotIn("[None]", markdown)
                    self.assertEqual(payload["sources"][0], source)

    def test_english_temporal_receipt_and_capture_clock_are_preserved(self) -> None:
        import json
        from omh.workflows.temporal_source_receipts import as_of_date, build_temporal_source_receipt

        receipt = build_temporal_source_receipt(
            as_of=as_of_date("2026-06-01"), source_url="https://example.org/p",
            source_class="upstream_official", evidence_kind="historical_capture",
            retrieved_at="2026-09-08T10:00:00Z", capture_provider="web_archive",
            capture_provider_class="archive_service", captured_at="2026-05-20T08:15:00Z",
        )
        before = json.dumps(receipt, sort_keys=True)
        source = {"id": "paper_1", "title": "Official paper", "url": receipt["source_url"],
                  "source_class": "upstream_official", "retrieved_on": "2026-09-08",
                  "temporal_source_receipt": receipt}
        payload = _briefing(sources=(source,))
        for render in (render_research_briefing_markdown, render_research_briefing_page):
            output = render(payload)
            self.assertIn("retrieved 2026-09-08, historical capture 2026-05-20T08:15:00Z via web_archive", output)
            self.assertIn("paper_1", output)
            self.assertIn("https://example.org/p", output)
        self.assertEqual(json.dumps(payload["sources"][0]["temporal_source_receipt"], sort_keys=True), before)
        for changed in ({**source, "source_class": "practitioner"},
                        {**source, "url": "https://other.example/p"},
                        {**source, "temporal_source_receipt": {**receipt, "cutoff_relation": "after"}}):
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    _briefing(sources=(changed,))

    def test_page_is_self_contained_and_print_ready(self) -> None:
        page = render_research_briefing_page(_briefing())
        self.assertIn("<!DOCTYPE html>", page)
        self.assertIn("@page", page)
        self.assertIn("@media print", page)
        # Nothing may fetch when the page is opened.
        for forbidden in ("<link", "<script", "@import", "src=", "//fonts."):
            self.assertNotIn(forbidden, page, forbidden)
        self.assertIn(BRIEFING_CLAIM_BOUNDARY, page)

    def test_page_escapes_supplied_text_and_renders_deterministically(self) -> None:
        payload = _briefing(
            sections=(
                {
                    "role": "pitfall",
                    "title": "Markup in a supplied title",
                    "paragraphs": ("A body mentioning <script>alert(1)</script> and A & B.",),
                },
            )
        )
        page = render_research_briefing_page(payload)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("A &amp; B", page)
        self.assertEqual(page, render_research_briefing_page(payload))


_KO_CAPTIONS = {
    "question": "질문", "as_of": "기준 시점", "learning_objectives": "학습 목표",
    "assumed_knowledge": "사전 지식", "out_of_scope": "제외 범위", "figures": "수치",
    "figure_value": "값", "figure_basis": "산출 근거", "figure_as_of": "측정 시점",
    "glossary": "개념 사전", "traps": "주의 사항", "sources": "출처",
    "basis_measured": "측정", "basis_assumed": "가정", "basis_derived": "계산",
    "source_class_upstream_official": "공식 자료", "source_class_practitioner": "실무 자료",
    "source_class_unattributed": "미확인 자료", "retrieved_on": "조회",
    "live_page": "현재 페이지", "historical_capture": "과거 캡처",
    "capture_time_unknown": "캡처 시각 미상", "capture_provider": "제공자",
    "claim_boundary": "조사 브리핑은 준비된 의사결정 참고 자료이며 실행·검토·통합·병합 증거가 아닙니다. "
                      "관찰된 파일 증거 없이 페이지는 PDF가 아닙니다.",
}


class ResearchBriefingLanguageTests(unittest.TestCase):
    def test_non_english_scaffolding_cannot_fall_back_to_english(self) -> None:
        for language in ("ko", "fr", "ja"):
            with self.subTest(language=language):
                with self.assertRaisesRegex(ValueError, "required.*non-English"):
                    _briefing(language=language)
        with self.assertRaisesRegex(ValueError, "required.*non-English"):
            _briefing(language="ko", role_labels={"problem": "문제", "cost": "비용"},
                      captions={"question": "질문", "as_of": "기준 시점", "glossary": "개념 사전"})

    def test_every_rendered_caption_and_role_is_required(self) -> None:
        payload = _briefing(language="ko", role_labels={"problem": "문제", "cost": "비용"},
                            captions=_KO_CAPTIONS)
        required = (
            "question", "as_of", "learning_objectives", "assumed_knowledge", "out_of_scope",
            "figures", "figure_value", "figure_basis", "figure_as_of", "basis_derived",
            "glossary", "traps", "sources", "source_class_upstream_official", "retrieved_on", "claim_boundary",
        )
        for key in required:
            with self.subTest(caption=key):
                changed = dict(payload, captions={k: v for k, v in _KO_CAPTIONS.items() if k != key})
                self.assertTrue(any(f"captions: {key!r} is required" in issue
                                    for issue in research_briefing_errors(changed)))
                for render in (render_research_briefing_markdown, render_research_briefing_page):
                    with self.assertRaisesRegex(ValueError, "required.*non-English"):
                        render(changed)
        for role in ("problem", "cost"):
            changed = dict(payload, role_labels={k: v for k, v in payload["role_labels"].items() if k != role})
            self.assertTrue(any(f"role_labels: {role!r} is required" in issue
                                for issue in research_briefing_errors(changed)))

    def test_only_used_scaffolding_needs_translations(self) -> None:
        payload = _briefing(
            language="fr", sections=({"role": "problem", "title": "Charge", "paragraphs": ("Texte.",)},),
            learning_objectives=(), assumed_knowledge=(), out_of_scope=(), figures=(), glossary=(), traps=(), sources=(),
            role_labels={"problem": "Problème"},
            captions={"question": "Question", "as_of": "Date", "claim_boundary": "Contexte préparé, pas une exécution."},
        )
        self.assertEqual(research_briefing_errors(payload), [])
        for render in (render_research_briefing_markdown, render_research_briefing_page):
            self.assertIn("Problème - Charge", render(payload))

    def test_full_korean_scaffolding_includes_temporal_notes_and_boundary(self) -> None:
        from omh.workflows.temporal_source_receipts import as_of_date, build_temporal_source_receipt

        for kind, captured_at in (("live_page", ""), ("historical_capture", ""),
                                  ("historical_capture", "2026-05-20T08:15:00Z")):
            with self.subTest(kind=kind, captured_at=captured_at):
                receipt = build_temporal_source_receipt(
                    as_of=as_of_date("2026-06-01"), source_url="https://example.org/p", source_class="upstream_official",
                    evidence_kind=kind, retrieved_at="2026-09-08T10:00:00Z",
                    capture_provider="archive" if kind == "historical_capture" else "",
                    capture_provider_class="archive_service" if kind == "historical_capture" else "none",
                    captured_at=captured_at,
                )
                source = {"id": "문서-1", "title": "공식 문서", "url": "https://example.org/p",
                          "source_class": "upstream_official", "retrieved_on": "2026-09-08",
                          "temporal_source_receipt": receipt}
                payload = _briefing(
                    language="ko", title="서빙 스택의 캐시 선택", question="캐시 레이아웃 선택 기준",
                    sections=({"role": "problem", "title": "입력 증가로 인한 부하 증가", "paragraphs": ("메모리 부하가 늘어납니다.",)},),
                    learning_objectives=("구조 선택",), assumed_knowledge=("어텐션",), out_of_scope=("학습",),
                    figures=({"label": "메모리", "value": "40 GB", "basis": "derived", "as_of": "2026-09-01"},),
                    glossary=({"term": "캐시", "definition": "임시 저장소"},), traps=("품질 저하",),
                    sources=(source,), role_labels={"problem": "문제"}, captions=_KO_CAPTIONS,
                )
                self.assertEqual(payload["sources"][0]["temporal_source_receipt"], receipt)
                self.assertEqual(payload["claim_boundary"], BRIEFING_CLAIM_BOUNDARY)
                for render in (render_research_briefing_markdown, render_research_briefing_page):
                    output = render(payload)
                    self.assertIn("문제 - 입력 증가로 인한 부하 증가", output)
                    for label in ("학습 목표", "사전 지식", "제외 범위", "수치", "계산", "공식 자료", "조회", "출처"):
                        self.assertIn(label, output)
                    self.assertIn(_KO_CAPTIONS["claim_boundary"], output)
                    for english in ("retrieved ", "historical capture", "capture time unknown", "live page",
                                    " via ", "derived", "upstream_official", "Appendix", BRIEFING_CLAIM_BOUNDARY):
                        self.assertNotIn(english, output)
                    if kind == "live_page":
                        self.assertIn("현재 페이지", output)
                    else:
                        self.assertIn("과거 캡처", output)
                        self.assertIn("제공자 archive", output)
                        self.assertIn(captured_at or "캡처 시각 미상", output)

    def test_scaffolding_labels_travel_with_the_payload(self) -> None:
        payload = _briefing(
            language="ko",
            title="서빙 스택의 KV 캐시 레이아웃 선택",
            sections=(
                {
                    "role": "problem",
                    "title": "입력 증가로 인한 부하 증가",
                    "paragraphs": ("컨텍스트 길이가 8k에서 128k로 늘면서 요청당 KV 캐시가 서빙 메모리를 지배하게 됐다.",),
                },
            ),
            figures=(),
            role_labels={"problem": "문제"},
            captions=_KO_CAPTIONS,
        )
        markdown = render_research_briefing_markdown(payload)
        self.assertIn("## 문제 - 입력 증가로 인한 부하 증가", markdown)
        self.assertIn("질문:", markdown)
        self.assertIn("기준 시점:", markdown)
        self.assertIn("## 개념 사전", markdown)
        self.assertIn("## 출처", markdown)
        self.assertNotIn("Appendix", markdown)
        page = render_research_briefing_page(payload)
        self.assertIn('lang="ko"', page)
        self.assertIn("문제 - 입력 증가로 인한 부하 증가", page)

    def test_translated_enum_labels_follow_the_validators_whitespace_policy(self) -> None:
        payload = _briefing(
            language="ko", role_labels={"problem": "문제"}, captions=_KO_CAPTIONS,
            sections=({"role": " problem ", "title": "부하", "paragraphs": ("본문.",)},),
            figures=({"label": "수치", "value": "1", "basis": " derived "},),
            sources=({"title": "문서", "source_class": " upstream_official ", "retrieved_on": "2026-10-22"},),
        )
        for render in (render_research_briefing_markdown, render_research_briefing_page):
            output = render(payload)
            self.assertIn("문제 - 부하", output)
            self.assertIn("계산", output)
            self.assertIn("공식 자료", output)

    def test_unknown_label_keys_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _briefing(role_labels={"summary": "요약"})
        with self.assertRaises(ValueError):
            _briefing(captions={"epilogue": "맺음말"})
        with self.assertRaises(ValueError):
            _briefing(captions={"glossary": "   "})


class ResearchSkillBranchTests(unittest.TestCase):
    def test_the_research_skill_asks_the_audience_before_retrieval(self) -> None:
        definition = next(item for item in builtin_definitions() if item.name == "research")
        inputs = " ".join(definition.required_inputs)
        self.assertIn("output audience", inputs)
        self.assertIn("output format", inputs)
        self.assertIn("output language", inputs)
        bar = " ".join(definition.quality_bar)
        self.assertIn("Ask who the output is for before retrieval", bar)
        self.assertIn("references/briefing-format.md", bar)
        self.assertIn(RESEARCH_BRIEFING_SCHEMA_VERSION, " ".join(definition.expected_outputs))
        self.assertIn(RESEARCH_BRIEFING_SCHEMA_VERSION, " ".join(definition.safety_rules))

    def test_the_briefing_format_reference_ships_with_the_skill(self) -> None:
        templates = {
            template.relative_path: template
            for template in builtin_skill_reference_templates()
            if template.skill_name == "research"
        }
        self.assertIn("references/briefing-format.md", templates)
        content = templates["references/briefing-format.md"].content
        # The closed role vocabulary must be discoverable from the reference.
        for role in BRIEFING_ROLE_LABELS:
            self.assertIn(role.capitalize(), content, role)
        self.assertIn("never inferred", content)


if __name__ == "__main__":
    unittest.main()
