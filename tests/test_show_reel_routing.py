from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from _local_package import load_local_package

load_local_package()
from omh.paths import resolve_paths
from omh.routing.chat import route_chat_message
from omh.routing.recommend import recommend_skills
from omh.skills.skill_record import skill_record
from omh.wrapper_contract import build_chat_interaction_payload
from omh.plugin_bundle.omh.awareness import awareness_primer_payload, awareness_route_hint, workflow_context_card_for_workflow
from omh.plugin_bundle.omh.hooks.llm_hooks import pre_llm_call
from omh.plugin_bundle.omh.show_reel_cues import show_reel_request_admitted


# Each of these dispatched `show-reel` under the first admission grammar, which
# took any words before the noun and any sentence with the noun alone. The
# film is the object of another request, a causative, or a bare noun.
WRONG_DISPATCH_CONTROLS = (
    "Make sure the launch film for Tuesday is uploaded to the drive.",
    "Write a tweet announcing the launch film.",
    "Make the product teaser video in the footer smaller.",
    "Make the launch film with subtitles the default.",
    "Make the show reel in the README link work.",
    "Make our launch film, website and app icons consistent.",
    "showreel",
    "Show reel.",
    "show reel?",
    "Create a GitHub issue titled showreel.",
    "Write a Slack post announcing the launch film.",
    "Create a Jira ticket tracking the launch film.",
    "Design a thumbnail showing the product teaser video.",
    "Create a spreadsheet listing every launch film.",
    "Create the launch film and deck folder structure.",
)


def _named_skills(route: dict) -> set[str]:
    named = {str(route.get("selected_skill") or ""), str(route.get("candidate_skill") or "")}
    for row in (route.get("candidate_handoff") or {}).get("candidates") or []:
        if isinstance(row, dict):
            named.add(str(row.get("skill") or ""))
    return named


class ShowReelAdmissionTests(unittest.TestCase):
    def test_lead_anchored_production_requests_are_admitted(self):
        for message in (
            "Create a product showcase video for a Japanese audience.",
            "Please create a 60-second launch film about our new app.",
            "Could you make a short animated product film for the store page?",
            "Create an animated portfolio of our agency's projects.",
            "Make a showreel.",
            "Produce a showreel: 45 seconds, English and Korean.",
            "Make a 2-minute launch film covering the three new features.",
            "Hey, create a cinematic launch film for the conference keynote.",
            "Create a showreel that introduces OMH.",
            "제품 쇼케이스 영상 만들어줘",
            "제품 쇼케이스 영상을 영어로 만들어줘",
            "製品ショーケース動画を作ってください",
            "请帮我制作一个产品展示短片",
        ):
            with self.subTest(message=message):
                self.assertTrue(show_reel_request_admitted(message))

    def test_a_film_inside_another_request_is_not_admitted(self):
        for message in WRONG_DISPATCH_CONTROLS + (
            "Make the launch film shorter.",
            "Make our showreel public.",
            "Create our launch film.",
            "Write a product showcase video script.",
            "Create a product showcase video translation.",
            "Create a cinematic product showcase video.md file.",
            "Make a launch film with subtitles.",
            "Create a very bold hand-drawn launch film.",
            "Do not create a product showcase video.",
            'Translate "Create a product showcase video" into Japanese.',
            "Showreelish is our internal code name, not a video.",
            "제품 쇼케이스 영상을 참고로 정적인 카드로 만들어줘",
            "제품 쇼케이스 영상 문구를 영어로 만들어줘",
            "製品ショーケース動画を英語で作ってはいけない",
            "请不要制作一个产品展示短片",
            "请帮我制作一个产品展示短片的字幕",
            # Asked rather than told, an idiom, a definite object.
            "Make a show reel? No, just send the old one.",
            "Make a launch film, right?",
            "Make a new launch film? Should we even?",
            "Make a launch film, or should I hire an agency?",
            "Make a showreel on the website load faster.",
            "Do launch films need subtitles?",
            "Run the launch film by legal.",
            "Make the launch film that we discussed shorter.",
            # A second request chained on in the same sentence.
            "Make a launch film and post it to Slack.",
            "Produce a launch film, then deploy the landing page.",
            "Create a launch film: also review the PR that adds the video player.",
            "Make a showreel from these clips and translate the subtitles to Japanese.",
        ):
            with self.subTest(message=message):
                self.assertFalse(show_reel_request_admitted(message))

    def test_a_courtesy_opener_keeps_an_asked_request(self):
        self.assertTrue(show_reel_request_admitted("Can you make a show reel?"))


class ShowReelRoutingTests(unittest.TestCase):
    def test_wrong_dispatch_controls_never_name_show_reel(self):
        for message in WRONG_DISPATCH_CONTROLS:
            with self.subTest(message=message):
                route = route_chat_message(message, source="discord")
                self.assertNotIn("show-reel", _named_skills(route))
                self.assertNotEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")

    def test_controls_keep_their_own_owner(self):
        for message, owner in (
            ("Create a GitHub issue titled showreel.", "github-issue-intake"),
            ("Write a Slack post announcing the launch film.", "connector-operator"),
            ("Create a Jira ticket tracking the launch film.", "connector-operator"),
            ("Design a thumbnail showing the product teaser video.", "img-summary"),
            ("Create a spreadsheet listing every launch film.", "materials-package"),
            ("Use this product showcase video as a reference for a static infographic.", "img-summary"),
            ("Summarize this launch film transcript for the weekly report.", "media-input-operator"),
            ("Use the code-review skill to create a launch film from the attached transcript.", "code-review"),
            ("Produce a launch film, then deploy the landing page.", "frontend"),
            ("Make a launch film for the investors, then do a market sizing for the deck.", "materials-package"),
        ):
            with self.subTest(message=message):
                self.assertEqual(route_chat_message(message, source="discord")["selected_skill"], owner)

    def test_production_requests_and_explicit_names_dispatch(self):
        with TemporaryDirectory() as temp:
            paths = resolve_paths(Path(temp) / "omh", Path(temp) / "hermes")
            for message in (
                "Create a product showcase video for a Japanese audience.",
                "Create a show reel of my design work.",
                "Make a motion showcase for the new dashboard.",
                "Cut a show reel from last year's client work.",
                "Please produce a product showcase video using our brand guide.",
                "제품 쇼케이스 영상 만들어줘",
                "Use the show-reel skill to make a launch film from these notes.",
                "/show-reel a launch film for the beta",
                "$show-reel produce a 30-second teaser",
                "omh-show-reel create an audience-specific film",
            ):
                with self.subTest(message=message):
                    route = route_chat_message(message, source="discord")
                    self.assertEqual(route["action"], "dispatch")
                    self.assertEqual(route["selected_skill"], "show-reel")
                    if not message.startswith(("Use the", "/", "$")):
                        # Like the other admission-held workflows, the hint
                        # reads the bare hyphenated name, not a sigil or "use
                        # the X skill" (`awareness._ADMISSION_HELD_WORKFLOWS`).
                        self.assertEqual(awareness_route_hint(message)["primary_workflow"], "show-reel")
                    card = build_chat_interaction_payload(message, source="discord", paths=paths)
                    self.assertEqual(card["chat_response"]["kind"], "show_reel")

    def test_nonproduction_nouns_earn_no_recommendation(self):
        for message in (
            "Convert this product showcase video to a smaller MP4.",
            "Review the typography in this product showcase video.",
            "Write a product showcase video script.",
            "Make a vivid caption for a launch film.",
            "제품 쇼케이스 영상 문구를 영어로 번역해줘",
            "製品ショーケース動画という文章を英語に翻訳して",
            "把产品展示短片这句话翻译成英文",
        ):
            with self.subTest(message=message):
                self.assertNotIn("show-reel", [r["skill"] for r in recommend_skills(message, limit=200)])
                self.assertNotEqual(route_chat_message(message, source="discord").get("selected_skill"), "show-reel")

    def test_negative_senses_and_sibling_boundaries_do_not_dispatch_here(self):
        for message in (
            "Which fishing reel should I buy?",
            "The tape reel is damaged.",
            "What is the plot of this film?",
            "Convert this MP4 to a smaller file.",
            "Make a static infographic summary card",
            "Create a PowerPoint deck",
            "Show the video attachment in this bug report",
        ):
            with self.subTest(message=message):
                self.assertNotEqual(route_chat_message(message, source="discord").get("selected_skill"), "show-reel")


class ShowReelSurfaceTests(unittest.TestCase):
    def test_real_messenger_surface_has_dedicated_card_not_generic_plan(self):
        with TemporaryDirectory() as temp:
            paths = resolve_paths(Path(temp) / "omh", Path(temp) / "hermes")
            for source in ("discord", "slack", "telegram"):
                for message in ("omh-show-reel create an audience-specific film", "Create a product showcase video for a Japanese audience."):
                    with self.subTest(source=source, message=message):
                        response = build_chat_interaction_payload(message, source=source, paths=paths)["chat_response"]
                        self.assertEqual(response["kind"], "show_reel")
                        self.assertEqual(response["state"]["next_action"], "prepare_show_reel")
                        self.assertTrue(response["state"]["evidence_not_observed"])
                        for word in ("audience", "source", "renderer"):
                            self.assertIn(word, str(response).lower())

    def test_preparation_policy_harness_and_awareness_are_explicit(self):
        record = skill_record("show-reel")
        self.assertEqual(record.policy.next_action, "prepare_show_reel")
        self.assertEqual(record.next_action_label, "prepare a show reel")
        self.assertEqual(record.harness.name, "show-reel")
        lane = next(x for x in awareness_primer_payload()["lanes"] if x["id"] == "materials_and_visuals")
        self.assertIn("show-reel", lane["skills"])
        self.assertEqual(workflow_context_card_for_workflow("show-reel")["id"], "materials_and_visuals")

    def test_plugin_hint_agrees_with_real_route(self):
        for message in ("Create a product showcase video for a Japanese audience.", "omh-show-reel اصنع عرضًا للمنتج"):
            with self.subTest(message=message):
                self.assertEqual(route_chat_message(message, source="discord")["selected_skill"], "show-reel")
                hint = awareness_route_hint(message)
                self.assertEqual(hint["selected_workflow"], "show-reel")
                self.assertEqual(hint["primary_next_action"], "prepare_show_reel")
                self.assertEqual(hint["hints"][0]["workflow_context_card"]["id"], "materials_and_visuals")

    def test_production_refreshes_real_mid_session_hook(self):
        with TemporaryDirectory() as temp:
            for message in ("Create a product showcase video for a Japanese audience.", "제품 쇼케이스 영상 만들어줘"):
                with self.subTest(message=message):
                    payload = pre_llm_call(omh_home=temp, hermes_home=temp, user_message=message, is_first_turn=False)
                    self.assertIsNotNone(payload)
                    self.assertIn("prepare_show_reel", str(payload))
            for message in WRONG_DISPATCH_CONTROLS:
                with self.subTest(message=message):
                    payload = pre_llm_call(omh_home=temp, hermes_home=temp, user_message=message, is_first_turn=False)
                    self.assertNotIn("prepare_show_reel", str(payload))


if __name__ == "__main__":
    unittest.main()
