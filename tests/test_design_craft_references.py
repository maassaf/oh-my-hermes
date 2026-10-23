"""Design craft references: the named bar, the contract gate, the rubric.

Live design output through OMH read as generic because the design skills
stated abstract quality bars with no craft material behind them. These tests
pin the fix: the always-loaded skill bodies carry a NAMED bar and pointers,
and the on-demand references carry the material — the DESIGN.md contract
gate, the taste directions with their anti-slop checklist, the
reference-token extraction contract, and the critique rubric. The wording is
OMH's own; the concept lineage (oh-my-openagent's frontend architecture and
its permissively licensed design upstreams) is credited inside each
reference with a pinned commit and an explicit no-text-reproduced statement,
because the nearest upstream is Sustainable-Use-licensed and OMH is MIT.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest

from omh.skill_pack import builtin_skill_reference_templates, builtin_skill_templates
from omh.skills.catalog_types import omh_skill_display_name
from omh.skills.render import DESIGN_NAMED_BAR, design_reference_templates

REPO_ROOT = Path(__file__).resolve().parents[1]

FLAT_FAILS = "technically clean but flat"


def _unwrapped(content: str) -> str:
    """Soft line wraps collapsed, for phrase assertions that span lines."""
    return " ".join(content.split())


def _reference(skill: str, relative_path: str) -> str:
    for template in design_reference_templates():
        if template.skill_name == skill and template.relative_path == relative_path:
            return template.content
    raise AssertionError(f"missing design reference {skill}/{relative_path}")


def _body(skill: str) -> str:
    for template in builtin_skill_templates():
        if template.name == skill:
            return template.content
    raise AssertionError(f"missing skill {skill}")


class DesignReferenceRegistryTests(unittest.TestCase):
    def test_all_craft_references_are_registered_and_on_disk(self) -> None:
        expected = {
            ("frontend", "references/design-system-contract.md"),
            ("frontend", "references/taste-foundations.md"),
            ("frontend", "references/reference-token-extraction.md"),
            ("frontend", "references/tui-craft.md"),
            ("frontend", "references/screenshot-loop.md"),
            ("design-quality-gate", "references/design-critique-rubric.md"),
            ("visual-qa", "references/visual-verdict-contract.md"),
        }
        registered = {
            (template.skill_name, template.relative_path)
            for template in builtin_skill_reference_templates()
        }
        self.assertLessEqual(expected, registered)
        for skill, relative_path in expected:
            path = REPO_ROOT / "skills" / omh_skill_display_name(skill) / relative_path
            self.assertTrue(path.exists(), path)
            self.assertEqual(path.read_text(encoding="utf-8"), _reference(skill, relative_path))

    def test_every_reference_carries_the_honest_concept_lineage(self) -> None:
        # The nearest upstream architecture is Sustainable-Use-licensed and
        # OMH is MIT, so the attribution must (a) pin the upstream commit,
        # (b) state that no upstream text is reproduced, (c) credit the
        # permissive design upstreams the concepts actually come from, and
        # (d) carry the non-affiliation line for the named brands.
        for template in design_reference_templates():
            content = template.content
            self.assertIn("## Attribution", content, template.relative_path)
            self.assertRegex(content, r"oh-my-openagent@[0-9a-f]{7,}", template.relative_path)
            self.assertIn("Sustainable Use", content, template.relative_path)
            self.assertIn("No upstream text is reproduced", content, template.relative_path)
            for upstream in ("taste-skill", "ui-ux-pro-max-skill", "designpowers", "open-design"):
                self.assertIn(upstream, content, f"{template.relative_path}: {upstream}")
            self.assertIn("not affiliated with", content, template.relative_path)
            # The retired, incorrect claim must not resurface.
            self.assertNotIn("Apache-2.0, code-yeongyu", content, template.relative_path)


class DesignSystemContractTests(unittest.TestCase):
    def test_the_gate_is_stated_before_the_structure(self) -> None:
        content = _reference("frontend", "references/design-system-contract.md")
        self.assertIn("no component code before `DESIGN.md` exists", content)
        self.assertLess(
            content.index("no component code before `DESIGN.md` exists"),
            content.index("## Body sections"),
        )
        # Empty sections are decisions; existing UI without a contract stops
        # and asks; a code value outside the contract is drift.
        self.assertIn("silence is not a decision", _unwrapped(content))
        self.assertIn("Never decide silently", content)
        self.assertIn("drift", content)
        self.assertIn("observed-only", content)

    def test_new_contracts_use_the_format_sections_in_the_format_order(self) -> None:
        # Hermes bundles `design-md` for Google's DESIGN.md format under the
        # same file name. OMH's own numbered layout gave the model two
        # contradicting instructions for one file, so a new contract is the
        # format: token front matter, the format's eight sections in the
        # format's order, and OMH's sections beside them under names the
        # format does not use (the format rejects a repeated heading).
        content = _reference("frontend", "references/design-system-contract.md")
        body = content.split("## Body sections", 1)[1].split("## A contract in the earlier OMH layout", 1)[0]
        format_sections = (
            "**Overview**",
            "**Colors**",
            "**Typography**",
            "**Layout**",
            "**Elevation & Depth**",
            "**Shapes**",
            "**Components**",
            "**Do's and Don'ts**",
        )
        positions = [body.index(heading) for heading in format_sections]
        self.assertEqual(positions, sorted(positions))
        for extension in (
            "**Signature Element** *(OMH)*",
            "**Motion & Interaction** *(OMH)*",
            "**Accessibility Constraints & Accepted Debt** *(OMH)*",
            "**Research Log** *(OMH, greenfield builds)*",
        ):
            self.assertIn(extension, body, extension)
        front_matter = _unwrapped(content.split("## Token front matter", 1)[1].split("## Body sections", 1)[0])
        for token in ("`name:`", "`colors:`", "`primary`", "`omitted:`", "`{colors.primary}`", "`button-primary-hover`"):
            self.assertIn(token, front_matter, token)
        unwrapped = _unwrapped(body)
        # The format matches heading text exactly: a numbered or marked
        # heading is a section it does not know, and a repeated one is
        # rejected.
        self.assertIn(
            "Write each heading as the bare name spelled exactly as listed, with no number and no *OMH* marker",
            unwrapped,
        )
        self.assertIn("so `## 1. Overview` is a section it does not know", unwrapped)
        self.assertIn("Use each heading once: the format tells a consumer to reject a file that repeats one", unwrapped)
        # Only `surface` / `on-surface` are the format's recommended names;
        # `X` / `on-X` is OMH generalizing that pattern.
        self.assertIn(
            "The front matter spells the pair `X` and `on-X` (`primary` / `on-primary`), "
            "the pattern of the format's recommended `surface` / `on-surface` names",
            unwrapped,
        )
        # The linter exempts a token's whole family, not only its `on-`
        # partner, plus the Material baseline families.
        self.assertIn(
            "draws an `orphaned-tokens` warning unless it is in a referenced token's family or "
            "in the Material baseline families",
            unwrapped,
        )
        self.assertIn("for `ring` and the `chart-N` set that warning is expected, not a contract failure", unwrapped)

    def test_the_earlier_numbered_layout_stays_valid_until_the_user_asks(self) -> None:
        # Migration default: a file already written in the 0-8 layout is a
        # contract the gate accepts; conversion happens only on request and
        # moves decisions without making new ones.
        content = _reference("frontend", "references/design-system-contract.md")
        legacy = _unwrapped(content.split("## A contract in the earlier OMH layout", 1)[1].split("## Local reference data", 1)[0])
        for heading in (
            "0 Research Log",
            "1 Atmosphere & Identity",
            "2 Color",
            "3 Typography",
            "4 Spacing & Layout",
            "5 Components",
            "6 Motion & Interaction",
            "7 Depth & Surface",
            "8 Accessibility Constraints & Accepted Debt",
        ):
            self.assertIn(heading, legacy, heading)
        self.assertIn("still a valid contract and satisfies the gate", legacy)
        self.assertIn("Do not rewrite it into the format unasked", legacy)
        self.assertIn("Convert only when the user asks", legacy)
        self.assertIn("A conversion moves decisions and makes none", legacy)
        # Sibling references speak the format's terms; a legacy file takes
        # them through the conversion map read in reverse, without front
        # matter, or the legacy rule would contradict them.
        self.assertIn(
            "In a legacy file, write each into its counterpart by reading the conversion map below in reverse, "
            "as prose and without adding front matter",
            legacy,
        )
        for mapping in (
            "Overview and Signature Element into Atmosphere & Identity",
            "Colors, Shapes, `colors:`, and `rounded:` into Color",
            "Layout and `spacing:` into Spacing & Layout",
            "Elevation & Depth into Depth & Surface",
            "`typography:` and `components:` into Typography and Components",
        ):
            self.assertIn(mapping, legacy, mapping)
        # Do's and Don'ts is new in the forward map, so its reverse is an
        # added mapping, said as such rather than passed off as a reversal.
        self.assertIn(
            "Do's and Don'ts has no counterpart, since the format added it; as an added mapping, "
            "its rules go into Atmosphere & Identity",
            legacy,
        )
        # A file with no YAML gets one no-YAML warning and no other check.
        self.assertIn("the linter reports a single no-YAML warning and runs no other check", legacy)
        self.assertIn("That warning is not a contract failure", legacy)

    def test_lint_is_evidence_only_from_a_run_and_file_mechanics_defer_to_design_md(self) -> None:
        content = _unwrapped(_reference("frontend", "references/design-system-contract.md"))
        self.assertIn("For a file in the format, when Hermes' bundled `design-md` skill is available", content)
        self.assertIn("A file in the earlier OMH layout is not handed to it", content)
        self.assertIn("`npx -y @google/design.md lint DESIGN.md`", content)
        self.assertIn("the lint status is `not_observed`", content)
        self.assertIn("OMH itself runs no package and makes no network call", content)
        self.assertIn("never for the rendered UI", content)
        self.assertIn("google-labs-code/design.md@", content)
        self.assertIn("NousResearch/hermes-agent@", content)

    def test_the_overview_carries_the_taste_direction_the_rubric_judges_by(self) -> None:
        # The rubric judges inside the claimed direction, so the contract
        # must have a place to declare it — including borrowed elements.
        content = _reference("frontend", "references/design-system-contract.md")
        self.assertIn("the chosen taste direction (primary", content)
        self.assertIn("borrowed", content)

    def test_typography_covers_cjk_metrics_not_just_fallbacks(self) -> None:
        content = _reference("frontend", "references/design-system-contract.md")
        self.assertIn("CJK", content)
        self.assertIn("word-break", content)
        self.assertIn("truncation", content)

    def test_no_frontend_text_points_at_a_numbered_design_md_section(self) -> None:
        # Section numbers named the earlier 0-8 layout and mean something
        # else in the format, so every pointer names the section instead.
        # The subject is every packaged `frontend` text, so a new reference
        # joins the guard without being listed.
        # Both word orders: "`DESIGN.md` section 2" / "DESIGN.md's sections 2
        # and 3", and "section 2 of DESIGN.md" / "sections 2 and 3 in the
        # `DESIGN.md`". A section named in words carries no digit and passes.
        numbered = re.compile(
            r"DESIGN\.md`?(?:'s)?\s+sections?\s+\d"
            r"|\bsections?\s+\d+(?:(?:,\s*|,?\s+(?:and|or)\s+)\d+)*\s+(?:of|in)\s+(?:the\s+)?`?DESIGN\.md"
        )
        texts = [
            (template.relative_path, template.content)
            for template in builtin_skill_reference_templates()
            if template.skill_name == "frontend"
        ]
        texts.append(("SKILL.md", _body("frontend")))
        self.assertGreater(len(texts), 1)
        for name, content in texts:
            with self.subTest(text=name):
                self.assertIsNone(numbered.search(content))


class TasteFoundationsTests(unittest.TestCase):
    def test_the_bar_is_named_and_flatness_fails(self) -> None:
        content = _reference("frontend", "references/taste-foundations.md")
        self.assertIn(DESIGN_NAMED_BAR, content)
        self.assertIn("flatness is a defect", content)

    def test_primary_direction_with_declared_borrowing(self) -> None:
        # Exactly-one would make a hybrid brief unrepresentable; the rule is
        # one PRIMARY direction plus named borrowed elements.
        content = _reference("frontend", "references/taste-foundations.md")
        self.assertIn("Name one primary direction", content)
        self.assertIn("borrowed from another direction", content)
        for direction in ("Operational", "Minimalist", "Premium", "Bold"):
            self.assertIn(direction, content, direction)

    def test_anti_slop_checklist_names_the_failure_modes(self) -> None:
        content = _reference("frontend", "references/taste-foundations.md")
        for marker in (
            "Anti-slop checklist",
            "Template gravity",
            "One-note palette",
            "Weak hierarchy",
            "Missing states",
            "prefers-reduced-motion",
            "CJK as an afterthought",
        ):
            self.assertIn(marker, content, marker)

    def test_content_ordering_beats_visual_symmetry(self) -> None:
        content = _reference("frontend", "references/taste-foundations.md")
        self.assertIn("visual symmetry never outranks that sequence", _unwrapped(content))

    def test_the_model_default_prior_is_named_with_both_its_fits(self) -> None:
        # The prior arrives whether or not anyone chose it, so the reference
        # has to name it AND split the briefs it serves from the ones it
        # actively damages. Naming only the aesthetic would read as a
        # recommendation.
        content = _unwrapped(_reference("frontend", "references/taste-foundations.md"))
        self.assertIn("A coding model does not start neutral", content)
        for token in ("cream", "serif display", "terracotta"):
            self.assertIn(token, content, token)
        self.assertIn("It suits", content)
        self.assertIn("It is a failure mode", content)
        for suited in ("editorial", "portfolio", "hospitality"):
            self.assertIn(suited, content, suited)
        for failing in ("dashboards", "developer tools", "fintech", "data-dense"):
            self.assertIn(failing, content, failing)

    def test_a_negation_is_not_an_override_without_tokens(self) -> None:
        # "don't make it look AI" swaps one fixed default for the next one;
        # the override only becomes actionable as a hex palette plus a
        # typeface stack written into the contract.
        content = _unwrapped(_reference("frontend", "references/taste-foundations.md"))
        self.assertIn("Overriding the default takes tokens, not negations", content)
        self.assertIn("A negation names what to stop; it never names where to go", content)
        self.assertIn("a palette as hex", content)
        self.assertIn("a typeface stack", content)
        self.assertIn("Those land in `DESIGN.md` as `colors:`, `typography:`, `rounded:`, and `spacing:` tokens", content)

    def test_review_prompts_are_questions_not_a_second_ban_list(self) -> None:
        # These patterns are legitimate when something chose them, so they
        # ship as review questions a stated reason closes - separate from the
        # reject-on-sight checklist above them.
        content = _unwrapped(_reference("frontend", "references/taste-foundations.md"))
        self.assertIn("Review prompts — not bans", content)
        self.assertIn("They are the ones that show up when nothing chose them", content)
        for prompt in (
            "Framework blue",
            "`#3B82F6`",
            "Glass surfaces and cyan-to-purple gradients",
            "Inter everywhere",
            "Bounce easing",
            "Shadows on every surface",
            "Eyebrow, title, description",
            "The uniform grid",
            "CJK body under 14px",
        ):
            self.assertIn(prompt, content, prompt)
        self.assertIn("bento", content)
        self.assertIn("14px floor for Korean body text", content)


class TuiCraftTests(unittest.TestCase):
    def test_the_bar_is_named_and_defaults_are_scaffolding(self) -> None:
        content = _reference("frontend", "references/tui-craft.md")
        self.assertIn(DESIGN_NAMED_BAR, content)
        self.assertIn("scaffolding, not finished UI", _unwrapped(content))
        self.assertIn("a defect to fix, not a baseline to accept", _unwrapped(content))

    def test_borders_spacing_and_one_named_aesthetic(self) -> None:
        content = _reference("frontend", "references/tui-craft.md")
        self.assertIn("spend them sparingly", content)
        self.assertIn("muted-color ladder", content)
        self.assertIn("Name one terminal aesthetic", content)
        for aesthetic in ("Minimal utility", "Modern product", "Retro terminal", "Dense operational"):
            self.assertIn(aesthetic, content, aesthetic)

    def test_box_drawing_color_floor_and_keyboard_states(self) -> None:
        content = _reference("frontend", "references/tui-craft.md")
        self.assertIn("box-drawing family", content)
        self.assertIn("256-color fallback", content)
        self.assertIn("There is no pointer", _unwrapped(content))
        self.assertIn("Cursor-only focus", content)

    def test_verification_names_sizes_and_the_squeeze_defect_class(self) -> None:
        content = _reference("frontend", "references/tui-craft.md")
        self.assertIn("80x24 and 120x40 minimum", _unwrapped(content))
        self.assertIn("screenshot-equivalent", content)
        self.assertIn("Short-terminal squeeze", content)
        self.assertIn("prepared claim, not an observed one", _unwrapped(content))

    def test_tui_rejects_extend_the_anti_slop_checklist(self) -> None:
        content = _reference("frontend", "references/tui-craft.md")
        self.assertIn("extend the anti-slop checklist in `taste-foundations.md`", _unwrapped(content))
        for marker in (
            "Unstyled default widget",
            "Border noise",
            "Colorless hierarchy",
            "Truecolor gamble",
            "Keybinding folklore",
            "One-size render",
            "Squeeze blindness",
        ):
            self.assertIn(marker, content, marker)

    def test_skill_bodies_carry_the_tui_hooks(self) -> None:
        self.assertIn("references/tui-craft.md", _body("frontend"))
        self.assertIn("80x24 and 120x40", _body("frontend"))
        self.assertIn("80x24 and 120x40", _body("visual-qa"))
        self.assertIn("screenshot-equivalent", _body("visual-qa"))


class ScreenshotLoopTests(unittest.TestCase):
    def test_the_loop_captures_named_widths_until_the_list_is_empty(self) -> None:
        content = _reference("frontend", "references/screenshot-loop.md")
        self.assertIn(DESIGN_NAMED_BAR, _unwrapped(content))
        self.assertIn("1440px, 768px, and 375px", _unwrapped(content))
        self.assertIn("Exit only when the difference list is empty", _unwrapped(content))
        # The web widths are the counterpart of the TUI's named sizes.
        self.assertIn("80x24 and 120x40", _unwrapped(content))

    def test_live_environment_comes_before_the_code(self) -> None:
        content = _reference("frontend", "references/screenshot-loop.md")
        self.assertIn("Live environment first", content)
        self.assertIn("working blind", content)

    def test_comparison_target_precedence_ends_at_the_contract_gate(self) -> None:
        # Supplied target first, DESIGN.md second, and no target at all is a
        # stop — iterating toward an unstated target converges on generic.
        content = _reference("frontend", "references/screenshot-loop.md")
        self.assertIn("A user-supplied mock", content)
        self.assertIn("Otherwise `DESIGN.md` is the target", content)
        self.assertIn("Neither exists: stop", content)

    def test_findings_are_triaged_with_captures_attached(self) -> None:
        content = _reference("frontend", "references/screenshot-loop.md")
        for marker in ("[Blocker]", "[High]", "[Medium]", "Nit:"):
            self.assertIn(marker, content, marker)
        self.assertIn("problems, not prescriptions", _unwrapped(content))
        self.assertIn("Every finding attaches the capture", _unwrapped(content))

    def test_the_loop_defers_to_visual_qa_instead_of_duplicating_it(self) -> None:
        # The capture inventory and the verdict both stay with visual-qa;
        # the loop only ends its own difference list.
        content = _reference("frontend", "references/screenshot-loop.md")
        self.assertIn("viewport_state_capture_matrix/v1", content)
        self.assertIn("it is not PASS", _unwrapped(content))
        self.assertIn("prepared claim, not an observed one", _unwrapped(content))

    def test_frontend_body_carries_the_loop_hook(self) -> None:
        body = _body("frontend")
        self.assertIn("references/screenshot-loop.md", body)
        self.assertIn("1440/768/375px", body)
        self.assertIn("until the difference list is empty", body)


class ReferenceTokenExtractionTests(unittest.TestCase):
    def test_extraction_covers_static_and_live_references(self) -> None:
        content = _reference("frontend", "references/reference-token-extraction.md")
        self.assertIn("Static reference", content)
        self.assertIn("Live URL reference", content)
        self.assertIn("OMH itself never launches a browser", _unwrapped(content))
        self.assertIn("never copy logos", content)

    def test_fidelity_qa_is_stated_in_omh_vocabulary(self) -> None:
        # "reference-fidelity mode" was a dangling capability name; the QA
        # request is stated through the artifacts visual-qa actually owns.
        content = _reference("frontend", "references/reference-token-extraction.md")
        self.assertIn("visual_qa_plan/v1", content)
        self.assertIn("visual-fidelity review perspective", content)
        self.assertNotIn("reference-fidelity mode", content)


class DesignCritiqueRubricTests(unittest.TestCase):
    def test_rubric_axes_each_carry_a_fail_condition(self) -> None:
        content = _reference("design-quality-gate", "references/design-critique-rubric.md")
        for axis in (
            "Hierarchy",
            "Type discipline",
            "Spacing rhythm",
            "Color system",
            "State coverage",
            "Signature",
            "Motion restraint",
            "CJK",
        ):
            self.assertIn(axis, content, axis)
        self.assertGreaterEqual(content.count("FAIL:"), 8)

    def test_verdicts_name_the_direction_vocabulary_and_its_source(self) -> None:
        # The rubric judges inside the claimed direction, so it must carry
        # the four direction names AND the skill-rooted path to their
        # definitions — design-quality-gate covers deck/PDF/poster surfaces
        # where the frontend skill would never be consulted first.
        content = _reference("design-quality-gate", "references/design-critique-rubric.md")
        self.assertIn("omh-frontend/references/taste-foundations.md", content)
        for direction in ("operational", "minimalist/editorial", "premium/soft", "bold/expressive"):
            self.assertIn(direction, content, direction)
        self.assertIn("judge inside it", content)
        self.assertIn("smallest change", content)
        self.assertIn("never passes work", content)

    def test_the_rubric_question_is_the_named_bar(self) -> None:
        content = _reference("design-quality-gate", "references/design-critique-rubric.md")
        self.assertIn(DESIGN_NAMED_BAR, content)
        self.assertIn("Technically clean but flat fails", content)


class VisualVerdictContractTests(unittest.TestCase):
    def test_the_verdict_shape_is_json_only_with_an_integer_score(self) -> None:
        # A band or a letter grade makes two rounds incomparable, so the score
        # is pinned as a whole number and the object ships alone.
        content = _reference("visual-qa", "references/visual-verdict-contract.md")
        self.assertIn("```json", content)
        self.assertIn('"score"', content)
        self.assertIn('"verdict"', content)
        self.assertIn('"differences"', content)
        self.assertIn("an integer from 0 to 100", content)
        self.assertIn("Not a band, not a letter, not a range", _unwrapped(content))
        self.assertIn("one JSON object and nothing else", _unwrapped(content))

    def test_every_difference_is_paired_with_its_suggestion(self) -> None:
        content = _unwrapped(_reference("visual-qa", "references/visual-verdict-contract.md"))
        self.assertIn('"suggestion"', content)
        self.assertIn("A difference with no suggestion is an unfinished finding", content)
        self.assertIn("a suggestion with no difference is an opinion", content)

    def test_ninety_is_the_pass_line_and_below_it_owes_a_rerun(self) -> None:
        # The whole point of the number is a stopping rule; a sub-threshold
        # round has to buy a real recapture, not a re-score of the same
        # images or a softer adjective.
        content = _unwrapped(_reference("visual-qa", "references/visual-verdict-contract.md"))
        self.assertIn("**90 is the pass line.**", content)
        self.assertIn("the same pages, states, and viewports are recaptured", content)
        self.assertIn("Rescoring the same captures is not a round", content)
        self.assertIn("An exhausted budget is a reported blocker, never a quiet `PASS`", content)
        for verdict in ("`PASS`", "`REVISE`", "`BLOCK`"):
            self.assertIn(verdict, content, verdict)

    def test_pixel_diff_is_demoted_to_hotspot_localization(self) -> None:
        content = _unwrapped(_reference("visual-qa", "references/visual-verdict-contract.md"))
        self.assertIn("Pixel diff is the secondary aid", content)
        self.assertIn("answers where two images differ", content)
        self.assertIn("it never produces the `score`", content)
        self.assertIn("a region with no diff is still judged on the rubric axes", content)
        self.assertIn("design-critique-rubric.md", content)

    def test_the_contract_stays_prepared_and_executor_neutral(self) -> None:
        content = _unwrapped(_reference("visual-qa", "references/visual-verdict-contract.md"))
        self.assertIn("OMH prepares this contract; it does not run it", content)
        self.assertIn("whichever executor or wrapper lane the user selected", content)
        self.assertIn("prepared claim, not an observed one", content)

    def test_the_rubric_carries_the_score_hook_and_the_default_axes(self) -> None:
        content = _reference("design-quality-gate", "references/design-critique-rubric.md")
        self.assertIn("omh-visual-qa/references/visual-verdict-contract.md", content)
        self.assertIn("90 is the pass line", content)
        self.assertIn("Default-prior fit", content)
        self.assertIn("Chosen, not inherited", content)

    def test_visual_qa_body_carries_the_threshold_and_the_pointer(self) -> None:
        body = _body("visual-qa")
        self.assertIn("references/visual-verdict-contract.md", body)
        self.assertIn("integer 0-100 score", body)
        self.assertIn("Hold 90 as the pass line", body)
        self.assertIn("rescoring the same captures is not a new round", body)
        self.assertIn("Pixel diff localizes hotspots only", body)


class SkillBodyPointerTests(unittest.TestCase):
    def test_frontend_body_carries_the_named_bar_and_all_three_pointers(self) -> None:
        body = _body("frontend")
        self.assertIn("Linear/Stripe/Supabase class", body)
        self.assertIn(FLAT_FAILS, body)
        self.assertIn("references/design-system-contract.md", body)
        self.assertIn("references/taste-foundations.md", body)
        self.assertIn("references/reference-token-extraction.md", body)
        self.assertIn("no component code before the contract exists", body)

    def test_frontend_body_names_the_format_and_hands_file_mechanics_to_design_md(self) -> None:
        # The two DESIGN.md contracts meet in the always-loaded body, so the
        # body itself says which format a new file uses, that the earlier
        # layout stays valid, and that Hermes' bundled skill owns authoring,
        # lint, diff, and export.
        body = _body("frontend")
        self.assertIn("A new DESIGN.md is Google's DESIGN.md format", body)
        self.assertIn("converted only when the user asks", body)
        self.assertIn("Hermes' bundled `design-md` skill is available", body)
        self.assertIn("export a file in the format through it, never a file in the earlier layout", body)
        self.assertIn("lint is evidence only from a run that executed, otherwise `not_observed`", body)

    def test_frontend_body_carries_the_default_prior_and_the_override_test(self) -> None:
        # The prior and the "negations are not overrides" rule are wrong to
        # discover after a surface shipped, so they stay in the always-loaded
        # body rather than only in the on-demand reference.
        body = _body("frontend")
        self.assertIn("model's own default aesthetic", body)
        self.assertIn("cream grounds, serif display faces, and muted terracotta accents", body)
        self.assertIn("data-dense UIs", body)
        self.assertIn("an override counts only when it carries concrete tokens", body)
        self.assertIn("hex palette and a typeface stack", body)

    def test_quality_gate_body_points_at_the_rubric(self) -> None:
        body = _body("design-quality-gate")
        self.assertIn("references/design-critique-rubric.md", body)
        self.assertIn(FLAT_FAILS, body)

    def test_orchestration_body_uses_a_skill_rooted_reference_path(self) -> None:
        # design-orchestration ships no references/ directory of its own, so
        # a bare relative path would resolve to nothing from its skill root.
        body = _body("design-orchestration")
        self.assertIn("omh-frontend/references/taste-foundations.md", body)
        self.assertIn(FLAT_FAILS, body)


if __name__ == "__main__":
    unittest.main()
