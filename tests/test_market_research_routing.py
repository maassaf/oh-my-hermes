"""English market-method routing and non-market negative controls."""
from __future__ import annotations

import unittest

from _route_owner import route_owner
from omh.routing.chat import route_chat_message
from omh.routing.recommend import recommend_skills
from omh.wrapper.contract import build_chat_interaction_payload


def _names_market_research(prompt: str) -> bool:
    if any(row["skill"] == "market-research" for row in recommend_skills(prompt, limit=100)):
        return True
    decision = route_chat_message(prompt)
    payload = build_chat_interaction_payload(prompt)
    candidates = (payload.get("candidate_handoff") or {}).get("candidates") or []
    return (
        "market-research" in {decision.get("selected_skill"), decision.get("candidate_skill")}
        or any(candidate.get("skill") == "market-research" for candidate in candidates)
        or payload["chat_response"]["kind"] == "market_research"
    )


class MarketResearchRoutingTests(unittest.TestCase):
    def test_domain_requests_get_market_card_and_policy(self) -> None:
        for prompt in (
            "Estimate TAM SAM SOM for US dental clinics using market sizing with sources and sensitivity",
            "Prepare a customer evidence analysis and JTBD evidence matrix for dental buyers",
            "Do a competitive pricing analysis of Figma versus Sketch",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(route_owner(route_chat_message(prompt)), "market-research")
                self.assertEqual(recommend_skills(prompt)[0]["next_action"], "prepare_market_research")
                payload = build_chat_interaction_payload(prompt)
                self.assertEqual(payload["chat_response"]["kind"], "market_research")
                self.assertEqual(payload["next_action"], "prepare_market_research")

    def test_a_request_that_leads_with_a_method_is_admitted(self) -> None:
        for prompt in (
            "Perform a competitive pricing analysis across the three CRM tools",
            "Please conduct competitive pricing analysis for dental SaaS tools",
            "Create competitor pricing profiles for Figma, Sketch and Penpot",
            "Can you do market sizing for dental clinics in Korea?",
            "hey, estimate tam sam som for pet insurance in germany",
            "Do market sizing: home espresso machines in the US",
            "Hey, could you estimate TAM SAM SOM for pet insurance in Germany?",
            "Could you do a competitive pricing analysis of Figma versus Sketch?",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(route_owner(route_chat_message(prompt)), "market-research")
                self.assertEqual(build_chat_interaction_payload(prompt)["chat_response"]["kind"], "market_research")

    def test_a_method_named_in_passing_does_not_name_the_skill(self) -> None:
        # Material to send or translate, a concept to explain, evidence for a
        # decision another skill owns, a topic of other writing, a method
        # after a sentence break or in a later clause, a lead verb whose
        # object is something else, the skill's name, and Korean.
        for prompt in (
            "Send the market sizing slide to Slack",
            "Post the TAM SAM SOM chart to Discord",
            "Translate competitive pricing analysis into Korean",
            "Explain TAM SAM SOM to me like I'm five",
            "explain market size sensitivity to interest rates for my econ homework",
            "Should we kill or pivot? I have a market sizing and customer evidence analysis",
            "Should we build this product? our market sizing says the TAM is huge",
            "Write a blog post about why startups should perform competitive pricing analysis",
            "Our interns help us research. Competitive pricing analysis is their favorite part; draft a thank-you card for them",
            "Things we did not compare: competitor pricing profiles. Write that up as a status note",
            "the report says we should prepare competitor pricing profiles next quarter; summarize the report",
            "Can you check whether we should conduct a competitive pricing analysis at all?",
            "prepare the market sizing deck for the board",
            "run competitive pricing analysis tests in CI",
            "analyze this CSV of competitor pricing profiles",
            "I read a book on market-research methods, recommend a similar one",
            "we need a market-research person on the team; draft a job post",
            "tam sam som are my three cats, suggest a name for the fourth",
            "the market stall is closing; price the used monitor",
            "경쟁사 가격표를 슬랙에 보내줘",
            "Do market sizing and TAM mean the same thing?",
            "Run the market sizing by legal before we publish",
            "Estimate TAM SAM SOM? No, I just want to know what the acronym means",
            "Do TAM SAM SOM for me? Nah, just send the old deck to the board",
            "Do market sizing for fun on weekends, is that normal?",
            "Do a quick market sizing of how many pizzas I need for 20 people",
            "Perform competitive pricing analysis on this pull request diff",
            "Compare competitive pricing analysis with value-based pricing in theory",
            "Hey, do market sizing and TAM differ?",
            "Please, do market sizing and TAM mean the same thing?",
            "Please run market sizing for fun, is that weird?",
            "Hey can you estimate TAM SAM SOM? Never mind, just explain the acronym",
        ):
            with self.subTest(prompt=prompt):
                self.assertFalse(_names_market_research(prompt))

    def test_adjacent_requests_retain_their_owner(self) -> None:
        for prompt, owner in (
            ("triage this customer feedback backlog", "feedback-triage"),
            ("analyze this CSV and summarize anomalies", "data-analysis"),
            ("explain this paper PDF section by section", "paper-learning"),
            ("prepare a research brief for the board", "research-brief"),
            ("set up a Scout Analyst Briefer source inbox for weekly competitors", "research-department"),
            ("fix the checkout bug in our app", "ultrawork"),
            ("explain this paper PDF on competitive pricing analysis section by section", "paper-learning"),
            ("set up a Scout Analyst Briefer source inbox for weekly competitor pricing profiles", "research-department"),
            ("analyze this CSV of competitor pricing profiles and summarize anomalies", "data-analysis"),
            ("Create a research brief for the board from the supplied competitor pricing profiles", "research-brief"),
            ("Triage this customer feedback backlog about competitive pricing analysis", "feedback-triage"),
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(route_owner(route_chat_message(prompt)), owner)

    def test_a_request_another_guard_holds_stays_with_that_guard(self) -> None:
        # No boost and no stand-down: a vendor comparison the research-brief
        # guard holds is not taken, even when it leads with a method phrase.
        prompt = "Prepare competitor pricing profiles for three dental vendors"
        decision = route_chat_message(prompt)
        self.assertNotEqual(decision.get("selected_skill"), "market-research")
        self.assertNotEqual(build_chat_interaction_payload(prompt)["chat_response"]["kind"], "market_research")


if __name__ == "__main__":
    unittest.main()
