from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()
from omh.knowledge_connections import KnowledgeConnectionOptions
from omh.wiki_blueprint import (
    SEED_PAGE_CAP,
    UNKNOWN_AUDIENCE,
    WIKI_BLUEPRINT_SCHEMA_VERSION,
    WikiBlueprintRequest,
    build_wiki_blueprint,
    ecosystem_candidates,
    normalize_audience,
    select_models,
    wiki_ecosystem_coverage,
)
from omh.wiki_patterns import wiki_agent_reader_rules, wiki_operation_rules, wiki_pattern, wiki_patterns


def _blueprint(**kwargs: object) -> dict[str, object]:
    return build_wiki_blueprint(WikiBlueprintRequest(**kwargs))  # type: ignore[arg-type]


class AudienceTests(unittest.TestCase):
    def test_known_aliases_normalize(self) -> None:
        self.assertEqual(normalize_audience("Team"), "team")
        self.assertEqual(normalize_audience("개인"), "personal")
        self.assertEqual(normalize_audience("small group"), "small_group")
        self.assertEqual(normalize_audience("company"), "organization")

    def test_absent_or_unrecognized_audience_stays_unknown(self) -> None:
        self.assertEqual(normalize_audience(""), UNKNOWN_AUDIENCE)
        self.assertEqual(normalize_audience("   "), UNKNOWN_AUDIENCE)
        self.assertEqual(normalize_audience("a handful of robots"), UNKNOWN_AUDIENCE)


class ModelSelectionTests(unittest.TestCase):
    def test_knowledge_type_outranks_audience_default(self) -> None:
        primary, _ = select_models(
            audience="team",
            knowledge_types=("decisions we keep relitigating",),
            destination_kind="notion_knowledge_base",
        )
        self.assertEqual(primary.name, "Decision log (ADR)")

    def test_audience_default_applies_without_knowledge_types(self) -> None:
        personal, _ = select_models(audience="personal", knowledge_types=(), destination_kind="markdown_vault")
        team, _ = select_models(audience="team", knowledge_types=(), destination_kind="notion_knowledge_base")
        self.assertEqual(personal.name, "PARA")
        self.assertEqual(team.name, "Diátaxis")

    def test_repo_destination_pulls_in_docs_as_code(self) -> None:
        _, alternative = select_models(
            audience="team",
            knowledge_types=("onboarding",),
            destination_kind="markdown_folder",
        )
        self.assertEqual(alternative.name, "Docs-as-code")

    def test_unstated_destination_is_not_a_repository_signal(self) -> None:
        """`local_markdown_folder` is the classifier's default when nothing was said."""
        primary, _ = select_models(
            audience="personal",
            knowledge_types=(),
            destination_kind="local_markdown_folder",
        )
        self.assertNotEqual(primary.name, "Docs-as-code")
        self.assertEqual(primary.name, "PARA")

    def test_audience_outranks_knowledge_type_when_the_model_does_not_fit(self) -> None:
        """A solo developer documenting a repo matches docs-as-code on content, not on audience."""
        primary, alternative = select_models(
            audience="personal",
            knowledge_types=("code",),
            destination_kind="markdown_folder",
        )
        self.assertNotEqual(primary.name, "Docs-as-code")
        # Demoted, not dropped: its audience note explains what it would cost.
        self.assertEqual(alternative.name, "Docs-as-code")

    def test_fallback_alternative_fits_the_audience(self) -> None:
        _, alternative = select_models(
            audience="organization",
            knowledge_types=("procedures",),
            destination_kind="local_markdown_folder",
        )
        self.assertNotEqual(alternative.name, "PARA")
        self.assertIn("organization", wiki_pattern(alternative.name).suits_audiences)

    def test_alternative_is_always_distinct(self) -> None:
        for audience in ("personal", "small_group", "team", "organization", UNKNOWN_AUDIENCE):
            for knowledge_types in ((), ("decision",), ("research", "glossary")):
                primary, alternative = select_models(
                    audience=audience,
                    knowledge_types=knowledge_types,
                    destination_kind="markdown_vault",
                )
                self.assertNotEqual(primary.name, alternative.name, (audience, knowledge_types))

    def test_every_mapped_model_name_exists_in_the_pattern_table(self) -> None:
        names = {pattern.name for pattern in wiki_patterns()}
        for knowledge_types in (("decision",), ("onboarding",), ("code",), ("research",), ("glossary",), ("project",)):
            primary, alternative = select_models(
                audience=UNKNOWN_AUDIENCE,
                knowledge_types=knowledge_types,
                destination_kind="notion_knowledge_base",
            )
            self.assertIn(primary.name, names)
            self.assertIn(alternative.name, names)


class AgentReaderTests(unittest.TestCase):
    def test_an_agent_reader_is_detected_from_the_message(self) -> None:
        for text in (
            "my colleague and the Hermes agent will read this",
            "개인 볼트인데 에이전트도 읽어",
            "a wiki Claude can search",
        ):
            with self.subTest(text=text):
                self.assertTrue(_blueprint(text=text)["agent_readers"])

    def test_a_human_only_wiki_carries_no_agent_rules(self) -> None:
        blueprint = _blueprint(text="a wiki for my teammates", audience_scale="team")
        self.assertFalse(blueprint["agent_readers"])
        self.assertEqual(blueprint["agent_reader_rules"], [])

    def test_agent_readers_get_the_requirements_a_person_does_not_need(self) -> None:
        blueprint = _blueprint(text="the agent reads this too", audience_scale="team")
        topics = {row["topic"] for row in blueprint["agent_reader_rules"]}
        self.assertIn("Stable page identity", topics)
        self.assertIn("One topic per page", topics)
        self.assertEqual(len(topics), len(wiki_agent_reader_rules()))

    def test_a_model_that_relocates_pages_is_not_primary_for_an_agent(self) -> None:
        """PARA moves pages between projects/ and archive/, so an agent's citations rot."""
        human = _blueprint(text="my own vault", audience_scale="personal")
        agent = _blueprint(text="my own vault, and Claude reads it", audience_scale="personal")

        self.assertEqual(human["organization_model"]["name"], "PARA")
        self.assertNotEqual(agent["organization_model"]["name"], "PARA")
        self.assertEqual(agent["alternative_model"]["name"], "PARA")

    def test_audience_fit_still_outranks_agent_fit(self) -> None:
        """Two demotions applied in sequence let the second undo the first."""
        blueprint = _blueprint(
            text="my own repo docs, and an agent reads them",
            audience_scale="personal",
            knowledge_types=("code",),
        )
        primary = blueprint["organization_model"]["name"]
        self.assertNotEqual(primary, "Docs-as-code")
        self.assertNotEqual(primary, "PARA")
        self.assertIn("personal", wiki_pattern(primary).suits_audiences)
        self.assertTrue(wiki_pattern(primary).suits_agent_readers)


class BlueprintTests(unittest.TestCase):
    def test_shared_and_personal_get_different_operating_rules(self) -> None:
        team = _blueprint(text="set up a wiki in Notion", audience_scale="team", maintenance_owner="platform")
        solo = _blueprint(text="organize my Obsidian vault", audience_scale="personal", maintenance_owner="me")

        self.assertTrue(team["shared_audience"])
        self.assertFalse(solo["shared_audience"])
        ownership = {rule.topic: rule for rule in wiki_operation_rules()}["Ownership"]
        team_rules = {row["topic"]: row["rule"] for row in team["maintenance"]["rules"]}
        solo_rules = {row["topic"]: row["rule"] for row in solo["maintenance"]["rules"]}
        self.assertEqual(team_rules["Ownership"], ownership.shared)
        self.assertEqual(solo_rules["Ownership"], ownership.personal)

    def test_destination_classification_is_reused_not_reinvented(self) -> None:
        notion = _blueprint(text="save our onboarding docs into a Notion knowledge base")
        obsidian = _blueprint(
            text="structure my notes",
            connection=KnowledgeConnectionOptions(knowledge_store="my Obsidian vault"),
        )
        self.assertEqual(notion["destination"]["kind"], "notion_knowledge_base")
        self.assertEqual(obsidian["destination"]["vendor_hint"], "obsidian")
        self.assertFalse(notion["destination"]["write_observed"])

    def test_two_writers_get_multi_writer_rules_not_solo_ones(self) -> None:
        """Two people plus an agent is not a solo vault: naming has to be agreed."""
        pair = _blueprint(
            text="my colleague, an agent, and I will read and write this",
            audience_scale="small group",
            maintenance_owner="the two of us",
        )
        rules = {rule.topic: rule for rule in wiki_operation_rules()}
        conventions = {row["topic"]: row["rule"] for row in pair["conventions"]}

        self.assertTrue(pair["shared_audience"])
        self.assertEqual(conventions["Naming"], rules["Naming"].shared)
        self.assertEqual(conventions["Linking"], rules["Linking"].shared)

    def test_a_store_named_in_the_message_is_not_asked_for_again(self) -> None:
        """Re-asking for what the message already said is the round-trip this removes."""
        for text in (
            "set up a Notion knowledge base for the team",
            "keep our docs in Google Drive",
            "structure my Obsidian vault",
            "a markdown folder in the repo",
        ):
            with self.subTest(text=text):
                blueprint = _blueprint(
                    text=text,
                    audience_scale="team",
                    maintenance_owner="me",
                    knowledge_types=("decisions",),
                )
                self.assertEqual(blueprint["missing_facts"], [], blueprint["destination"]["kind"])

    def test_a_vault_named_in_the_message_is_the_destination(self) -> None:
        """Naming a vault while designing a wiki is choosing where it lives."""
        for text in ("structure my Obsidian vault", "옵시디언 볼트 구조 잡아줘"):
            with self.subTest(text=text):
                destination = _blueprint(text=text, audience_scale="personal")["destination"]
                self.assertEqual(destination["kind"], "markdown_vault")
                self.assertEqual(destination["vendor_hint"], "obsidian")

    def test_research_department_vendor_neutrality_is_left_alone(self) -> None:
        """The promotion is wiki-local; a passing mention in research ops is not a commitment."""
        from omh.workflows.research_department import build_research_department_plan

        plan = build_research_department_plan(
            "daily research department using NotebookLM and Obsidian if possible",
            created_at="2026-06-17T00:00:00Z",
        )
        self.assertEqual(plan["knowledge_store"]["type"], "local_markdown_folder")
        self.assertEqual(plan["knowledge_store"]["vendor_hint"], "")

    def test_missing_facts_name_what_the_interview_still_needs(self) -> None:
        bare = _blueprint(text="help me build a wiki")
        self.assertIn("audience scale (personal, small group, team, or organization)", bare["missing_facts"])
        self.assertIn("maintenance owner and review cadence", bare["missing_facts"])
        self.assertIn("knowledge types the wiki must hold", bare["missing_facts"])

    def test_unowned_wiki_is_recorded_rather_than_assumed(self) -> None:
        blueprint = _blueprint(text="a wiki for myself", audience_scale="personal")
        self.assertEqual(blueprint["maintenance"]["owner"], "unmaintained")
        self.assertFalse(blueprint["maintenance"]["owner_known"])

    def test_answered_interview_leaves_no_open_questions(self) -> None:
        blueprint = _blueprint(
            text="stand up a team wiki",
            audience_scale="team",
            maintenance_owner="platform team",
            knowledge_types=("decisions", "onboarding"),
            connection=KnowledgeConnectionOptions(knowledge_store="Notion workspace"),
        )
        self.assertEqual(blueprint["missing_facts"], [])

    def test_blueprint_never_claims_the_store_exists(self) -> None:
        blueprint = _blueprint(text="build a wiki in Notion", audience_scale="team")
        self.assertEqual(blueprint["schema_version"], WIKI_BLUEPRINT_SCHEMA_VERSION)
        self.assertEqual(blueprint["status"], "prepared")
        self.assertIn("not evidence that a store was created", blueprint["claim_boundary"])
        self.assertFalse(blueprint["destination"]["query_observed"])

    def test_seed_page_cap_keeps_the_blueprint_startable(self) -> None:
        self.assertEqual(_blueprint(text="wiki")["seed_page_cap"], SEED_PAGE_CAP)
        self.assertLessEqual(SEED_PAGE_CAP, 10)

    def test_model_payload_carries_breaking_conditions_and_an_alternative(self) -> None:
        blueprint = _blueprint(text="team wiki", audience_scale="team", knowledge_types=("decisions",))
        model = blueprint["organization_model"]
        self.assertTrue(model["breaks_when"])
        self.assertTrue(model["fits_when"])
        self.assertNotEqual(blueprint["alternative_model"]["name"], model["name"])


class EcosystemTests(unittest.TestCase):
    def test_candidates_are_knowledge_material_not_storage_backends(self) -> None:
        ids = {coverage.item.id for coverage in wiki_ecosystem_coverage()}
        self.assertIn("hermeswiki", ids)
        # A secrets vault matches "vault" but is not wiki-construction material.
        self.assertNotIn("1claw-hermes", ids)

    def test_candidate_rows_stay_metadata_only(self) -> None:
        for candidate in ecosystem_candidates():
            self.assertEqual(sorted(candidate), ["coverage_status", "id", "name", "url"])

    def test_candidate_order_is_stable(self) -> None:
        ids = [candidate["id"] for candidate in ecosystem_candidates()]
        self.assertEqual(ids, sorted(ids))


if __name__ == "__main__":
    unittest.main()
