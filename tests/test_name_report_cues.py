"""A complaint, a question or a stop that opens with a skill's name is about that skill, not a request to run it.

The corpus cases in `quality/routing_precision.py` hold the routed outcome; these pin the reading it rests on
(`name_report_cues`), for every skill rather than one, and that the router and Hermes' own route hint read it alike:
the router answers directly, and the hint neither offers the skill nor calls it explicitly invoked. A request that
names a failure, a report followed by a request, and a sigil still run the skill.
"""

from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.awareness import awareness_route_hint, awareness_route_hint_context_from_payload  # noqa: E402
from omh.plugin_bundle.omh.name_report_cues import (  # noqa: E402
    about_the_skill,
    asks_after_the_name,
    reports_on_the_name,
    stops_the_name,
)
from omh.routing.chat import explicit_skill_invocation, route_chat_message  # noqa: E402

REPORTS = (
    # The name, or the skill's own output, is the subject of a finite verb.
    "broke my build", "failed again", "failed on the auth module", "crashed, logs attached", "hung for an hour",
    "keeps failing", "took 20 minutes", "spawned too many agents", "output was wrong", "ordering looks wrong",
    "is too slow", "doesn't work", "hangs on the second node", "run finished, now what", "hung, kill it",
    # Korean: a complaint, a negation or a degree adverb before the predicate, a past tense with nothing of its own
    # before it, the name as the cause, a use of it before what happened, or a stop word alone.
    "너무 느려", "이상해", "결과가 이상해", "안 돼", "작동 안 해", "먹통이야", "무한루프 돌아", "또 실패했어",
    "20분이나 걸렸어", "브랜치 날려먹었어", "테스트를 다 지웠어", "결제 모듈에서 또 실패했어", "돌다가 죽었어",
    "결제 모듈 리팩터링 끝났어?", "때문에 빌드 깨졌어", "때문에 생긴 버그 고쳐줘", "쓰니까 빌드 깨졌어",
)
STOPS = ("stop", "cancel it", "멈춰", "계속 멈춰", "그만해", "지금 취소")
NOT_REPORTS = (
    # A request, or a report with a request after it.
    "fix the broken build", "the build that broke", "the failed migration", "failed again, rerun it",
    "broke my build, fix it", "has to migrate billing before friday", "made the plan, execute it",
    "또 실패했어, 다시 돌려줘", "결과가 이상해, 다시 해줘", "실패한 테스트 고쳐줘", "깨진 빌드 고쳐줘", "돌려", "이거 해",
    # A bare noun after a past tense or third-person verb makes a noun phrase: the work.
    "failed tests", "updated docs", "stuck jobs", "crashed pods in staging", "tests for the billing module",
    "speed up the build", "seed the database",
    # A clause of its own after the name: a problem for the skill to work on.
    "the build is broken", "결제 모듈이 느려", "빌드가 깨졌어", "빌드 깨졌어",
    # A noun phrase: what to work on.
    "결제 모듈 리팩터링", "느린 쿼리", "타임아웃 에러", "깨진 테스트", "실행",
    # A colon or a comma addresses the skill.
    ": the build is broken",
)
QUESTIONS_ABOUT_IT = ("what is it", "how does it work", "how do I cancel it", "what now", "이거 어떻게 써", "어떻게 써",
                      "뭐가 문제야", "결과 어디 있어", "이제 뭐 해")
QUESTIONS_ABOUT_ANYTHING = ("결제 api 뭐야", "why is the build failing", "how should we split the billing refactor",
                            "what's wrong with the auth tests")
NOT_QUESTIONS = ("why the build is failing", "what the auth module needs, then build it", "how to split the work",
                 "어떻게든 고쳐줘", "뭐든 해줘", "왜 빌드가 깨지는지")


class ReadingTests(unittest.TestCase):
    def test_the_words_after_the_name_report_on_it(self) -> None:
        for rest in REPORTS:
            with self.subTest(rest=rest):
                self.assertTrue(reports_on_the_name(rest))
        for rest in NOT_REPORTS:
            with self.subTest(rest=rest):
                self.assertFalse(reports_on_the_name(rest))

    def test_a_stop_word_alone(self) -> None:
        for rest in STOPS:
            with self.subTest(rest=rest):
                self.assertTrue(stops_the_name(rest))
        for rest in ("stop the billing job", "kill switch for the feature flags", "취소 버튼 만들어줘"):
            with self.subTest(rest=rest):
                self.assertFalse(stops_the_name(rest))

    def test_a_question_about_the_skill_and_one_about_anything(self) -> None:
        for rest in QUESTIONS_ABOUT_IT:
            with self.subTest(rest=rest):
                self.assertTrue(asks_after_the_name(rest))
        for rest in QUESTIONS_ABOUT_ANYTHING:
            with self.subTest(rest=rest):
                # A skill may be asked a question as its work; one that only runs work may not.
                self.assertFalse(asks_after_the_name(rest))
                self.assertTrue(asks_after_the_name(rest, about_anything=True))
        for rest in NOT_QUESTIONS:
            with self.subTest(rest=rest):
                self.assertFalse(asks_after_the_name(rest, about_anything=True))


class RouteTests(unittest.TestCase):
    def test_a_complaint_question_or_stop_after_a_name_is_answered_on_both_paths(self) -> None:
        for message in ("ultrawork broke my build", "ralplan broke my build", "maestro failed again",
                        "ultraqa 또 실패했어", "code-review 결과가 이상해", "skill-health crashed", "ulw 또 실패했어",
                        "ralph broke my build", "ultrawork 때문에 생긴 버그 고쳐줘", "build-failure-triage hung again",
                        # A stop or a question about the skill, whatever its name.
                        "ultrawork 이거 어떻게 써", "research how does it work", "wiki what is it", "ultrawork 결제 api 뭐야",
                        "ultrawork stop", "loop 멈춰", "team stop",
                        # A yes-or-no question after an engine, a stop with its reason or said twice, what is going on
                        # after ralplan, and a question that asks ralplan for no plan.
                        "ultrawork still running?", "ultrawork 써도 돼?", "ultrawork stop, too expensive",
                        "ultrawork 멈춰 멈춰", "ralplan what's going on", "ralplan 결제 api 뭐야",
                        # A question about the skill itself after one whose work is answering questions.
                        "app-debugging what is it", "running-work-board 이거 어떻게 써"):
            with self.subTest(message=message):
                self.assertIsNone(explicit_skill_invocation(message))
                route = route_chat_message(message, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("fallback", "oh-my-hermes"))
                payload = awareness_route_hint(message)
                self.assertEqual(payload["hints"], [])
                context = awareness_route_hint_context_from_payload(payload)
                self.assertNotIn("explicitly invoked", context)
                self.assertNotIn("Start at once", context)

    def test_the_hint_reads_the_colon_and_dag_ulws_own_words_as_the_router_does(self) -> None:
        # A colon after the name addresses the skill, so the hint still names it invoked; a question about a dag-ulw
        # run's result is answered, and the hint offers no ultrawork run for its leading `ulw`.
        payload = awareness_route_hint("ultrawork: broke my build")
        self.assertEqual([hint["workflow"] for hint in payload["hints"]], ["ulw-work"])
        self.assertIn("explicitly invoked", awareness_route_hint_context_from_payload(payload))
        for message in ("ulw dag 결과 정리해줘", "ulw dag 결과 보고서 써줘"):
            with self.subTest(message=message):
                self.assertEqual(route_chat_message(message, source="discord")["action"], "fallback")
                self.assertEqual(awareness_route_hint(message)["hints"], [])

    def test_a_report_after_a_name_that_is_also_a_word_keeps_its_route(self) -> None:
        # "frontend", "loop", "plan" may be the person's own frontend, loop or plan, and "code review" written with
        # a space their own review: the router reads such a sentence as it did before.
        for message, skill in (("frontend is broken on safari", "frontend"), ("research failed again", "research"),
                               ("wiki is outdated", "wiki"), ("backend 너무 느려", "backend")):
            with self.subTest(message=message):
                self.assertFalse(about_the_skill(message.split()[0], message.split(maxsplit=1)[1]))
                self.assertEqual(explicit_skill_invocation(message), skill)
        self.assertFalse(about_the_skill("code review", "takes forever on our team"))
        self.assertTrue(about_the_skill("code-review", "takes forever"))

    def test_a_request_that_names_a_failure_still_runs_the_skill(self) -> None:
        for message, skill in (("ultrawork fix the broken build", "ultrawork"),
                               ("ralplan the failed migration", "ralplan"),
                               ("ultrawork failed again, rerun it", "ultrawork"),
                               ("ultrawork the build is broken", "ultrawork"),
                               ("ultrawork failed tests", "ultrawork"),
                               ("code-review 실패한 테스트 고쳐줘", "code-review"),
                               ("/ultrawork broke my build", "ultrawork"),
                               ("$code-review 결과가 이상해", "code-review"),
                               ("use ultrawork, it broke my build", "ultrawork"),
                               ("research kubernetes operator patterns for this design", "research"),
                               # A request after a use clause, after credit, as the name said again, or as "again".
                               ("ultrawork 하고 나서 배포해", "ultrawork"),
                               ("ultrawork 덕분에 빨리 끝났어, 결제 모듈도 이렇게 해줘", "ultrawork"),
                               ("ultrawork failed again, ultrawork it again", "ultrawork"),
                               ("ultrawork failed again, one more time", "ultrawork"),
                               ("ultrawork 또 실패했어, 다시", "ultrawork"),
                               # A question that deliberates the work is ralplan's, and a status question is the work
                               # of a skill that answers it.
                               ("ralplan how should we split the billing refactor", "ralplan"),
                               ("app-debugging what's wrong", "app-debugging"),
                               ("running-work-board what's running", "running-work-board")):
            with self.subTest(message=message):
                self.assertEqual(explicit_skill_invocation(message), skill)
                route = route_chat_message(message, source="discord")
                self.assertEqual(route["action"], "dispatch")


if __name__ == "__main__":
    unittest.main()
