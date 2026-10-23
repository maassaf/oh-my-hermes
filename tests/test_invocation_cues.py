"""After an explicit invocation the default is to run the skill; a stop, a run-status request or a question about the
skill is answered instead (`invocation_cues.invoked_instruction`).

The corpus cases in `quality/routing_precision.py` hold the routed outcome of a few; these pin the reading for every
form, typed and as Hermes sends a slash skill, and that the router, the wrapper and Hermes' own route hint read it
alike.
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh import dag_ulw_cues  # noqa: E402
from omh.plugin_bundle.omh.awareness import awareness_route_hint  # noqa: E402
from omh.plugin_bundle.omh.hermes_session_commands import person_request_text  # noqa: E402
from omh.plugin_bundle.omh.invocation_cues import META, STATUS, STATUS_IS_THE_WORK, STOP, invoked_instruction  # noqa: E402
from omh.quality.routing_precision import _slash_skill_turn  # noqa: E402
from omh.routing.chat import route_chat_message  # noqa: E402
from omh.wrapper.contract import build_chat_interaction_payload  # noqa: E402

# A stop that names no task object, in each language.
STOPS = (
    "/ulw-work stop", "/ulw-loop stop", "/ulw-interview stop", "/omh-plan stop", "/omh-code-review stop",
    "/omh-app-debugging stop", "/ulw-work cancel", "/ulw-work 그만", "/ulw-work 취소해줘", "/ulw-work 이거 취소",
    "$ulw-work stop", "/ralplan 멈춰", "/ulw-work 停止", "/ulw-plan 止めて", "/omh-plan 取消",
    "/ulw-work 别做了", "/ulw-work 不要做了", "/ulw-loop 算了", "/omh-plan 停一下",
    # doctor runs on a status request (#2221 L4), and a stop after it is still a stop.
    "/omh-doctor 멈춰", "/omh-doctor stop",
)
# A stop whose object is the invoked skill's own run, or its name, and a stop said with why.
OWN_RUN_STOPS = (
    "/ulw-loop stop the loop", "/ulw-loop stop looping", "/ulw-loop 루프 멈춰", "/ulw-loop cancel the loop",
    "/ulw-work stop the work", "/ulw-work stop ultrawork", "/ulw-loop stop ulw-loop", "/omh-code-review stop the review",
    "/omh-code-review 리뷰 취소", "/omh-plan stop planning", "/omh-plan cancel the plan", "/ulw-plan stop the plan",
    "/ulw-interview 인터뷰 그만", "/omh-app-debugging stop debugging", "/omh-app-debugging stop the investigation",
    "/ulw-loop 그만해 비싸", "/ulw-loop 멈춰 너무 오래 걸려", "/ulw-loop no stop", "/omh-frontend stop omh-frontend",
    "/omh-backend stop backend", "/omh-plan 취소해 필요 없어", "/ulw-work 그만해 내가 할게", "/omh-code-review 코드 리뷰 취소",
    "/ulw-research stop the research", "/ulw-research 리서치 그만", "/omh-production-audit stop the audit",
    "/omh-tech-debt-audit 감사 중단", "/ulw-qa stop the qa run", "/omh-data-analysis cancel the analysis",
    "/omh-codebase-onboarding stop the tour", "/omh-build-failure-triage stop triaging", "/omh-paper-learning stop learning",
    "/ulw-loop ループを止めて", "/omh-code-review レビューをキャンセル", "/ulw-loop 停止循环", "/omh-code-review 取消审查",
    "/ulw-plan 计划取消",
    # A stop then only fillers, a negation or what the person is in the middle of.
    "/ulw-work 멈춰 일단", "/ulw-work 멈춰 잠깐만", "/ulw-work 취소해 이거 아니야", "/ulw-work 그만해 그거 아니야",
    "/ulw-work 그만해 지금 회의 중", "/ulw-work 멈춰 멈춰 멈춰 제발",
    # The sigil's name in any case, as Hermes matches it.
    "/ULW-LOOP stop the loop", "/Omh-Code-Review 리뷰 취소", "/ulw-work 멈춰 그거 아님",
    # A plain declarative ending is a reason; only a loanword or a counter is the stop's object.
    "/ulw-work 멈춰 필요 없다", "/ulw-loop 그만해 필요없다", "/omh-code-review 멈춰 이건 아니다",
)
# A question about the skill itself.
META_QUESTIONS = (
    "/ulw-plan what does this do?", "/ulw-plan what is it?", "/ulw-plan 이게 뭐야?", "/ulw-plan 어떻게 써?",
    "/ulw-plan 何これ", "/omh-plan 这是什么", "/omh-app-debugging what does this do?", "/ulw-plan what is ulw-plan?",
    "/omh-code-review what is omh-code-review?", "/omh-code-review what does this skill do?",
    "/omh-code-review 이 스킬 뭐야?", "/omh-paper-learning what does this skill do?",
    # How to use the skill, after any skill.
    "/omh-code-review 어떻게 써?", "/omh-code-review how do I use it?", "/omh-paper-learning 어떻게 써?",
    # The skill named by its label or its hyphenated name.
    "/omh-rust what is omh-rust?", "/ulw-loop what is ulw-loop?", "/omh-plan what is omh-plan?",
    "/ulw-work what is ulw?", "/ULW-LOOP what is ULW-LOOP?", "/omh-docs 사용법 알려줘",
)
# How the run stands, after a skill whose work is not that status.
STATUS_REQUESTS = (
    "/ulw-work status", "/ulw-work show status", "/ulw-work show me the run", "/ulw-loop 상태 보여줘",
    "/omh-code-review 진행상황 알려줘", "/ulw-work 뭐 남았어?", "/ultrawork still running?",
    "/omh-app-debugging is it running?", "/omh-app-debugging 끝났어?", "/ulw-plan 状態", "/omh-plan 状态",
    # ulw-context aligns project terms; its status is no work of its own.
    "/ulw-context status", "/ulw-context how much context is left?",
    # A skill whose job is not reporting a state: its status is the status card's to show.
    "/omh-cancel 상태 보여줘",
)
# Everything else runs the skill: a stop verb with a task object, a question with one, and a status request to a
# skill whose own work that status is.
REQUESTS = (
    ("/ulw-work kill the zombie processes", "ultrawork"), ("/omh-frontend stop the modal from flickering", "frontend"),
    ("/omh-git-workflow abort the rebase", "git-workflow"), ("/ulw-work 중복 결제 멈추게 해줘", "ultrawork"),
    ("/ulw-loop 로그 폭주 멈춰줘", "loop"), ("/ulw-work stop the flaky retry from hiding failures", "ultrawork"),
    ("/omh-verification-gate is it done?", "verification-gate"), ("/omh-code-review is it safe to merge?", "code-review"),
    ("/ulw-plan how should we migrate billing?", "ralplan"), ("/research is rust faster than go?", "research"),
    ("/omh-app-debugging why did the run stop?", "app-debugging"), ("/omh-app-debugging 왜 멈췄어?", "app-debugging"),
    ("/ulw-plan plan the billing migration", "ralplan"), ("/omh-code-review this PR", "code-review"),
    ("/omh-running-work-board status", "running-work-board"),
    ("/omh-running-work-board what's running?", "running-work-board"),
    ("/omh-deploy-and-monitor status", "deploy-and-monitor"),
    ("/omh-ops-observability-card status", "ops-observability-card"),
    # A stop of something other than the skill's own run, or with a task after it.
    ("/ulw-loop stop the loop when tests pass", "loop"), ("/omh-code-review 리뷰 취소 기능 만들어줘", "code-review"),
    ("/omh-data-pipelines stop the pipelines", "data-pipelines"), ("/omh-frontend stop the frontend", "frontend"),
    ("/ulw-loop 그만해 그리고 결제 모듈 고쳐줘", "loop"), ("/ulw-loop no stop words in the tokenizer", "loop"),
    ("/ulw-work 예약 취소 버튼", "ultrawork"),
    # A stop after the cancel skill is its work.
    ("/omh-cancel stop", "cancel"), ("/omh-cancel 취소해줘", "cancel"), ("/omh-cancel 그만", "cancel"),
    # A this/it question after a skill that explains or judges what the person points at.
    ("/omh-code-review how is it?", "code-review"), ("/omh-code-review what is it?", "code-review"),
    ("/omh-code-review 这是什么", "code-review"), ("/omh-codebase-onboarding what does this do?", "codebase-onboarding"),
    ("/omh-paper-learning what does this mean?", "paper-learning"), ("/omh-paper-learning what is it?", "paper-learning"),
    # A skill with no determiner or with "a" is any skill, and a name after an article is a thing, not the skill.
    ("/omh-docs what is a skill?", "product-docs"), ("/omh-docs 스킬이 뭐야?", "product-docs"),
    ("/omh-skill-scout what can a skill do?", "skill-scout"), ("/omh-frontend what does the frontend do?", "frontend"),
    ("/ulw-research what is the research about?", "research"), ("/ulw-context what is the context?", "context"),
    # A single common word that is a skill's name is its material: the language, not the skill.
    ("/omh-rust what is rust?", "rust"), ("/omh-rust how does rust work?", "rust"),
    # A stop verb before a task noun is that thing's stop; an uppercase sigil runs its skill as lower case does.
    ("/ulw-work 멈춰 서버", "ultrawork"), ("/ULW-LOOP fix the flaky test until green", "loop"),
    # A loanword or a counter after the stop is its object, not a reason.
    ("/ulw-work 멈춰 리뷰어", "ultrawork"), ("/ulw-work 멈춰 플레이어", "ultrawork"), ("/ulw-work 멈춰 서버 두 대", "ultrawork"),
    ("/ulw-work 취소해 주문 다", "ultrawork"),
    # Agent progress is agent-ops-review's own report.
    ("/omh-agent-ops-review status", "agent-ops-review"),
    # A stop verb before a task noun, a domain word that shares an own-run noun, and a stop of something else.
    ("/ulw-work 취소해 주문", "ultrawork"), ("/ulw-loop 무한 루프 멈춰", "loop"),
    ("/ulw-research stop the research paper from leaking", "research"), ("/ulw-work 取消订单", "ultrawork"),
    ("/ulw-work サーバーを止めて", "ultrawork"),
)

# Each skill whose own work is a run's status (`invocation_cues.STATUS_IS_THE_WORK`, #2221 L3): a status request after
# its sigil, and the awareness hint it keeps. Five carry no hint of their own on their sigil whatever follows it
# (measured on 7c129aca9: "/omh-running-work-board show the board for this repo" hints nothing), so the router alone
# pins those; the hinted six are pinned by both. doctor joined for #2221 L4: the status card shows no install health.
STATUS_IS_THE_WORK_REQUESTS = {
    "running-work-board": ("/omh-running-work-board status", ""),
    "deploy-and-monitor": ("/omh-deploy-and-monitor 상태 보여줘", ""),
    "verification-gate": ("/omh-verification-gate is it done?", "verification-gate"),
    "ops-observability-card": ("/omh-ops-observability-card is it running?", ""),
    "live-incident-response": ("/omh-live-incident-response status", "live-incident-response"),
    "context-budget-review": ("/omh-context-budget-review 끝났어?", "context-budget-review"),
    "harness-session-inventory": ("/omh-harness-session-inventory show me the status", ""),
    "skill-health": ("/omh-skill-health 상태 보여줘", "skill-health"),
    "run-efficiency": ("/omh-run-efficiency status", "run-efficiency"),
    "agent-ops-review": ("/omh-agent-ops-review status", ""),
    "doctor": ("/omh-doctor status", "doctor"),
}


def _forms(message: str) -> tuple[tuple[str, str], ...]:
    """The typed message and, for a skill Hermes lists under its label (`ulw-work`, `omh-plan`), its scaffold."""
    name, rest = message.split(maxsplit=1)
    if not name.startswith(("/ulw-", "/omh-")):
        return (("typed", message),)
    return ("typed", message), ("scaffold", _slash_skill_turn(name.lstrip("/"), rest))


class ReadingTests(unittest.TestCase):
    def test_the_instruction_reads_as_stop_status_meta_or_work(self) -> None:
        self.assertEqual(invoked_instruction("ultrawork", "stop"), STOP)
        self.assertEqual(invoked_instruction("ultrawork", "이거 취소"), STOP)
        self.assertEqual(invoked_instruction("ultrawork", "kill the zombie processes"), "")
        self.assertEqual(invoked_instruction("ultrawork", "status"), STATUS)
        self.assertEqual(invoked_instruction("ralplan", "何これ"), META)
        self.assertEqual(invoked_instruction("code-review", "is it safe to merge?"), "")
        # A question about dag-ulw is about a skill; a report on it is work for the skill invoked.
        self.assertEqual(invoked_instruction("loop", "dag-ulw가 뭐야?"), META)
        self.assertEqual(invoked_instruction("ultrawork", "dag-ulw is broken"), "")
        # A status request is the work of a skill whose work is that status.
        self.assertEqual(invoked_instruction("running-work-board", "status"), "")
        self.assertEqual(invoked_instruction("ops-observability-card", "status"), "")
        self.assertEqual(invoked_instruction("app-debugging", "is it running?"), STATUS)
        self.assertEqual(invoked_instruction("cancel", "상태 보여줘"), STATUS)
        self.assertEqual(invoked_instruction("loop", "멈춰 ? ?"), "")  # reads, and does not raise
        self.assertEqual(invoked_instruction("skill-health", "상태 보여줘"), "")

    def test_a_stop_of_the_skills_own_run_is_a_stop(self) -> None:
        self.assertEqual(invoked_instruction("loop", "stop the loop"), STOP)
        self.assertEqual(invoked_instruction("loop", "루프 그만"), STOP)
        self.assertEqual(invoked_instruction("ralplan", "stop the plan"), STOP)  # its label is ulw-plan
        self.assertEqual(invoked_instruction("ultrawork", "stop ulw"), STOP)
        self.assertEqual(invoked_instruction("deep-interview", "cancel the interview please"), STOP)
        self.assertEqual(invoked_instruction("loop", "no, stop"), STOP)
        self.assertEqual(invoked_instruction("ultrawork", "算了"), STOP)
        # Another thing named, a condition, a task after the stop, or a stop noun before its object: work.
        self.assertEqual(invoked_instruction("loop", "stop the loop when tests pass"), "")
        self.assertEqual(invoked_instruction("plan", "cancel the plan for the free tier"), "")
        self.assertEqual(invoked_instruction("code-review", "cancel the review request"), "")
        self.assertEqual(invoked_instruction("ultrawork", "stop the review"), "")
        self.assertEqual(invoked_instruction("frontend", "stop the frontend"), "")
        self.assertEqual(invoked_instruction("loop", "멈춰 로그 폭주 원인 찾아줘"), "")
        self.assertEqual(invoked_instruction("loop", "no, don't stop"), "")
        self.assertEqual(invoked_instruction("ultrawork", "算了一下成本"), "")
        # The cancel skill's work is the stop.
        self.assertEqual(invoked_instruction("cancel", "stop"), "")
        # A Korean stop then a noun is a stop of that thing; then a predicate, a stop said with why.
        self.assertEqual(invoked_instruction("ultrawork", "취소해 주문"), "")
        self.assertEqual(invoked_instruction("plan", "취소해 필요 없어"), STOP)
        self.assertEqual(invoked_instruction("code-review", "코드 리뷰 취소"), STOP)
        self.assertEqual(invoked_instruction("loop", "무한 루프 멈춰"), "")

    def test_a_long_clause_is_read_in_linear_time(self) -> None:
        # A Korean stop with why is read on its first few words: the clause's length adds no searches.
        calls: list[int] = []
        pattern = dag_ulw_cues._KOREAN_STOP_VERB_END

        class Counting:
            def search(self, text: str) -> object:
                calls.append(len(text))
                return pattern.search(text)

        with mock.patch.object(dag_ulw_cues, "_KOREAN_STOP_VERB_END", Counting()):
            self.assertEqual(invoked_instruction("loop", "결제 " * 15_000), "")
        self.assertLessEqual(len(calls), dag_ulw_cues._KOREAN_STOP_WORDS_MAX)
        self.assertLess(sum(calls), 200)
        started = time.perf_counter()
        invoked_instruction("ultrawork", "결제 모듈 리팩터링 " * 3_000)  # 30k characters, no punctuation
        self.assertLess(time.perf_counter() - started, 5.0)  # a generous bound; the reading takes milliseconds

    def test_a_question_about_this_after_a_skill_that_judges_material_is_its_work(self) -> None:
        for name in ("code-review", "codebase-onboarding", "paper-learning"):
            with self.subTest(name=name):
                self.assertEqual(invoked_instruction(name, "what is it?"), "")
                self.assertEqual(invoked_instruction(name, "이게 뭐야?"), "")
                self.assertEqual(invoked_instruction(name, "what does this skill do?"), META)
                self.assertEqual(invoked_instruction(name, "이 스킬 뭐야?"), META)
        self.assertEqual(invoked_instruction("code-review", "what is omh-code-review?"), META)
        self.assertEqual(invoked_instruction("code-review", "how should we structure code-review here?"), "")
        self.assertEqual(invoked_instruction("ultrawork", "what does this do?"), META)
        # Only a name in its skill form names the skill; a common word that is also its name does not.
        self.assertEqual(invoked_instruction("rust", "what is rust?"), "")
        self.assertEqual(invoked_instruction("rust", "what is omh-rust?"), META)
        self.assertEqual(invoked_instruction("loop", "what is ulw-loop?"), META)
        self.assertEqual(invoked_instruction("ultrawork", "what is ultrawork?"), META)


class RouteTests(unittest.TestCase):
    def test_a_stop_or_a_question_about_the_skill_is_answered(self) -> None:
        for message in STOPS + OWN_RUN_STOPS + META_QUESTIONS:
            for form, text in _forms(message):
                with self.subTest(message=message, form=form):
                    if form == "scaffold":
                        self.assertEqual(person_request_text(text), message)
                    self.assertNotEqual(route_chat_message(text, source="discord")["action"], "dispatch")
                    self.assertEqual(awareness_route_hint(text)["hints"], [])
                    interaction = build_chat_interaction_payload(text, source="discord")
                    self.assertEqual(interaction["next_action"], "answer_directly")

    def test_a_status_request_shows_the_status_card(self) -> None:
        for message in STATUS_REQUESTS:
            for form, text in _forms(message):
                with self.subTest(message=message, form=form):
                    route = route_chat_message(text, source="discord")
                    self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "oh-my-hermes"))
                    self.assertEqual(awareness_route_hint(text)["hints"], [])
                    interaction = build_chat_interaction_payload(text, source="discord")
                    self.assertEqual((interaction["mode"], interaction["next_action"]), ("status", "show_status"))

    def test_any_other_instruction_runs_the_skill(self) -> None:
        for message, skill in REQUESTS:
            for form, text in _forms(message):
                with self.subTest(message=message, form=form):
                    route = route_chat_message(text, source="discord")
                    self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", skill))
                    interaction = build_chat_interaction_payload(text, source="discord")
                    self.assertEqual(interaction["route"]["selected_skill"], skill)


class StatusIsTheWorkTests(unittest.TestCase):
    def test_every_member_runs_on_a_status_request_after_its_sigil(self) -> None:
        self.assertEqual(set(STATUS_IS_THE_WORK_REQUESTS), STATUS_IS_THE_WORK)  # a new member brings its case here
        for skill, (message, hinted) in STATUS_IS_THE_WORK_REQUESTS.items():
            self.assertEqual(invoked_instruction(skill, message.split(maxsplit=1)[1]), "")
            for form, text in _forms(message):
                with self.subTest(skill=skill, form=form):
                    route = route_chat_message(text, source="discord")
                    self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", skill))
                    self.assertEqual(awareness_route_hint(text)["primary_workflow"], hinted)

    def test_doctor_runs_on_its_status_and_its_bare_sigil_and_answers_a_question_about_it(self) -> None:
        # #2221 L4: the status request joined doctor's work; its bare sigil runs it as before, and a question about the
        # skill is still answered (its stops are in STOPS).
        for text in ("/omh-doctor 상태 보여줘", "/omh-doctor"):
            with self.subTest(text=text):
                route = route_chat_message(text, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "doctor"))
                self.assertEqual(awareness_route_hint(text)["primary_workflow"], "doctor")
        self.assertEqual(invoked_instruction("doctor", "이게 뭐야"), META)
        self.assertNotEqual(route_chat_message("/omh-doctor 이게 뭐야", source="discord")["action"], "dispatch")


if __name__ == "__main__":
    unittest.main()
