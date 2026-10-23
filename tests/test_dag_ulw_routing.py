"""Reaching `dag-ulw`: by its two-word name the way people type it, and by intent for big parallel work.

The corpus cases in `quality/routing_precision.py` hold the routed outcome;
these pin the readings it rests on (`dag_ulw_cues`: the name, a question
about it, and big work by size), that the router and Hermes' own route hint
read them alike, and that the bare words stay where they were: `dag` with
data pipelines, `ulw` / `ultrawork` with `ultrawork`.
"""

from __future__ import annotations

import unittest
from unittest import mock

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.awareness import awareness_route_hint, awareness_route_hint_context_from_payload  # noqa: E402
from omh.plugin_bundle.omh.reference_regions import reference_regions  # noqa: E402
from omh.plugin_bundle.omh.dag_ulw_cues import (  # noqa: E402
    asks_about_dag_ulw,
    big_work_for_dag_ulw,
    dag_ulw_control,
    declines_dag_ulw,
    dispatches_dag_ulw,
    invokes_dag_ulw,
    work_over_many_targets,
)
from omh.routing.chat import route_chat_message  # noqa: E402
from omh.routing.policy import skill_is_negated  # noqa: E402

INVOKES = (
    "dag ulw add the export API and its docs",
    "dag ulw해줘 결제 모듈이랑 알림 모듈",
    "dag ulw로 해줘",
    "dag ulw 해주세요",
    "결제 모듈 dag ulw로 고쳐줘",
    "ulw dag split the auth refactor",
    "/dag-ulw build the search feature",
    "$dag-ulw refactor the parser",
    "./dag-ulw 해줘",
    "use dag ulw for the release checklist",
    "please dag ulw the migration",
    "refactor the billing modules with dag ulw",
    "Dag-ULW 해주세요",
    "dag ultrawork the onboarding flow",
    # A request put politely as a question.
    "can you dag ulw the migration?",
    "could you please dag ulw the billing refactor",
    "could you run dag ulw on the billing refactor?",
    # An `or` between two targets weighs nothing.
    "can you dag ulw the auth or billing refactor?",
)
DOES_NOT_INVOKE = (
    "don't use dag ulw for this, just fix the typo",
    "dag ulw 말고 그냥 고쳐줘",
    "dag ulw는 쓰지 마",
    "not dag ulw, just answer",
    "the airflow dag failed last night",
    "open the dag file",
    "is dag ulw any good?",
    "어제 돌린 dag ulw 결과가 어떻게 됐어?",
    # Questions about the skill, answered as a question about any other skill is.
    "dag ulw는 뭐야?",
    "dag ulw 설명해줘",
    "dag ulw 결과 알려줘",
    "dag ulw 문서 보여줘",
    "dag ulw 어떻게 써?",
    "dag ulw 쓰면 비용이 얼마나 나와?",
    "dag ulw is broken, it never settles the verify node",
    "how does dag-ulw handle repairs?",
    "tell me about dag-ulw",
    "can you explain dag ulw?",
    "could you describe dag ulw",
    "can you not dag ulw this?",
    # A polite question that weighs the run against another way, or asks what you would do.
    "can you dag ulw this or should I just fix it myself?",
    "would you dag ulw, or just ultrawork, the auth refactor?",
    "could you dag ulw this, or is that overkill?",
    "would you dag ulw this or not?",
    "can you dag ulw the migration? or is that overkill",
    "would you dag ulw it, if you were me?",
    "can you dag ulw the migration 아니면 그냥 ultrawork로 할까?",
    # Asking to stop using it declines it.
    "can you stop using dag ulw for small fixes?",
    "ulw dagger",
    "ulw로 다 고쳐줘",
    "ultrawork the auth refactor",
)


class InvocationTests(unittest.TestCase):
    def test_the_name_in_the_shapes_people_type_invokes_it(self) -> None:
        for message in INVOKES:
            with self.subTest(message=message):
                self.assertTrue(invokes_dag_ulw(message))

    def test_a_declined_bare_or_merely_mentioned_name_does_not(self) -> None:
        for message in DOES_NOT_INVOKE:
            with self.subTest(message=message):
                self.assertFalse(invokes_dag_ulw(message))

    def test_a_declined_name_leaves_the_candidate_list(self) -> None:
        self.assertTrue(declines_dag_ulw("don't use dag ulw for this"))
        self.assertTrue(skill_is_negated("dag ulw 말고 그냥 고쳐줘", "dag-ulw"))
        self.assertFalse(skill_is_negated("dag ulw add the API", "dag-ulw"))


class RouterTests(unittest.TestCase):
    def test_the_explicit_forms_dispatch_dag_ulw(self) -> None:
        for message in ("dag ulw해줘 결제 모듈이랑 알림 모듈 같이 리팩터링해", "ulw dag split the auth refactor across the service",
                        "refactor the billing and invoicing modules with dag ulw",
                        # A failure that is the work, or a report with a request after it, still runs it.
                        "dag ulw the build that broke", "dag ulw 실패한 테스트 고쳐줘", "dag ulw 또 실패했어, 다시 돌려줘",
                        "/dag-ulw broke the build, fix it", "dag ulw failed again, rerun it", "dag ulw why the build is failing",
                        # Work on a cancel, named as such.
                        "dag ulw 결제 취소 처리 로직 구현해줘", "dag ulw 취소 버튼 만들어줘", "dag ulw cancel order flow",
                        "dag ulw add a cancel button", "dag ulw 취소 버튼 눌러도 안 멈추는 버그 고쳐줘",
                        "dag ulw kill switch for flags",
                        # A request after a use clause, or the name said again as the request.
                        "dag ulw 돌리고 나서 PR 올려", "dag ulw failed, dag ulw it again"):
            with self.subTest(message=message):
                route = route_chat_message(message, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "dag-ulw"))

    def test_bare_words_keep_their_owners(self) -> None:
        self.assertEqual(route_chat_message("ultrawork the auth refactor across the service")["selected_skill"],
                         "ultrawork")
        self.assertEqual(route_chat_message("$ulw-work fix the build")["selected_skill"], "ultrawork")
        for message in ("the airflow dag failed last night, check the scheduler logs", "open the dag file"):
            with self.subTest(message=message):
                route = route_chat_message(message)
                self.assertNotEqual(route["candidate_skill"], "dag-ulw")

    def test_size_evidence_reads_how_the_work_splits_or_how_wide_it_reaches(self) -> None:
        for message in ("implement the export feature in parallel across the api and ui modules",
                        "api, ui, docs 작업을 병렬로 나눠서 만들어줘", "migrate every module from requests to httpx",
                        "fan out across the services, then integrate"):
            with self.subTest(message=message):
                self.assertTrue(big_work_for_dag_ulw(message))
        for message in ("fix the flaky checkout test", "README 오타 고쳐줘",
                        # A concurrency word or a second step that names the subject, not the work's shape.
                        "fix the bug where uploads run in parallel and corrupt the file", "병렬 처리 버그 고쳐줘",
                        "동시에 두 요청이 오면 캐시가 깨지는 버그 고쳐줘", "병렬로 진행되는 작업이 데드락 걸리는 버그 고쳐줘",
                        "the parallel work queue drops jobs, fix it", "the fan out of this function is too high, fix it",
                        "add the field to the form, then integrate it with the api",
                        # A fault two named parts cause at once, with one fix asked for.
                        "api 모듈과 ui 모듈이 동시에 요청하면 캐시가 깨져, 고쳐줘",
                        "the payment and order services crashed when called concurrently, fix it",
                        # A remark on a measure, and a small text task.
                        "the fan out across the services is too high", "our split into parallel lanes was too coarse",
                        "split the paragraph into parallel parts for the translation", "이 문단을 병렬로 나눠서 번역해줘",
                        # A question or an explain request about how work splits.
                        "explain how to split the work into parallel lanes", "how do I split the work into parallel lanes?",
                        "what does it mean to fan out across services", "병렬로 나눠서 하는 방법 설명해줘",
                        "help me understand how to fan out across services",
                        "what we need is an explanation of how to split work into parallel lanes",
                        "병렬로 나눠서 하는 방법 알려줘", "병렬로 나눠서 작업하는 방법이 뭐야",
                        "show me how to split the work into parallel lanes", "is it possible to split the work into parallel lanes",
                        # Only the question mark says it: a question, not a request put as one.
                        "should we split the api, ui and docs work into parallel lanes?",
                        "i'd like to know how to split the work into parallel lanes",
                        "why not just explain how to split the work into parallel lanes",
                        # Talking it over, or a split weighed against another way.
                        "can we talk about how to split the work into parallel lanes?",
                        "could we split the work into parallel lanes, or is it overkill?",
                        # A condition clause, not a request, after the concurrency word.
                        "api 모듈과 ui 모듈이 동시에 처리해서 캐시가 깨져, 고쳐줘",
                        "api 모듈과 ui 모듈이 동시에 진행해서 데드락이 걸려 고쳐줘",
                        # Ultrawork named anywhere stays ultrawork's.
                        "fix the auth and billing modules in parallel with ulw"):
            with self.subTest(message=message):
                self.assertFalse(big_work_for_dag_ulw(message))
        for message in ("api 모듈과 ui 모듈 테스트가 실패하는데 병렬로 고쳐줘", "the migration is big, split it into parallel lanes",
                        "can you split the api, ui and docs work into parallel lanes?",
                        "split the paragraphs of the manual into parallel parts for translation",
                        "api, ui, docs 작업을 병렬로 나눠서 구현하고 변경 내용 설명해줘",
                        # Fixes said to run in parallel a word or two on, or the work said to run at once.
                        "api 모듈과 ui 모듈 테스트가 깨졌어, 병렬로 각각 고쳐줘",
                        "api 모듈과 ui 모듈 작업을 동시에 진행해줘, 실패하는 테스트도 고쳐줘",
                        # Openers that propose the work, requests put as questions, and an explain clause after the work.
                        "how about we split the api, ui and docs work into parallel lanes",
                        "why don't we split the api, ui and docs work into parallel lanes",
                        "what we need is to split the api, ui and docs work into parallel lanes",
                        "tell me when done: split the api, ui and docs work into parallel lanes",
                        "can we split the api, ui and docs work into parallel lanes?",
                        "split the api, ui and docs work into parallel lanes, ok?",
                        "api, ui, docs 작업을 병렬로 나눠서 구현해줄 수 있어?",
                        "api, ui, docs 작업을 병렬로 나눠서 구현하고 나눈 방법 설명해줘",
                        # A paragraph or sentence that is not what the split's object is.
                        "split the api, ui and docs work into parallel lanes and fix the paragraph in the README",
                        "split the paragraph parser and the sentence splitter work into parallel lanes",
                        "api, ui, docs 작업을 병렬로 나눠서 구현하고 README 한 문단 추가해줘",
                        "what i want is to split the api, ui and docs work into parallel lanes",
                        "그냥 병렬로 나눠서 하지 뭐", "api, ui, docs 작업을 병렬로 나눠서 진행해주고 다 됐는지 알려줘",
                        "api, ui, docs 작업을 병렬로 나눠서 구현하면 어때?"):
            with self.subTest(message=message):
                # Fixes said to run in parallel, a split asked for after a remark, a polite request, and many paragraphs.
                self.assertTrue(big_work_for_dag_ulw(message))

    def test_ultrawork_capabilities_answer_before_the_size_reading(self) -> None:
        # The retired-engine alias route answers coordinated workers before scoring, lanes phrase or not.
        route = route_chat_message("run three coordinated workers on one shared task list in parallel lanes")
        self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "ultrawork"))

    def _dag_ulw_starts(self, message: str) -> tuple[bool, bool, str]:
        """Whether the router dispatches dag-ulw, whether the route hint starts it, and the route's candidate."""
        route = route_chat_message(message, source="discord")
        hints = awareness_route_hint(message)["hints"]
        hint_starts = any(hint.get("workflow") == "dag-ulw" and hint.get("next_action") == "start_dag_run" for hint in hints)
        return route["selected_skill"] == "dag-ulw", hint_starts, str(route.get("candidate_skill") or "")

    def test_work_over_many_targets_runs_as_a_graph(self) -> None:
        # Positive evidence (`dispatches_dag_ulw`): a change verb from the allow list, in request form, on a breadth
        # object. The router dispatches dag-ulw whichever lane scored, and the hint starts it on the same reading.
        for message in ("rlaope로 올린 hermes upstream pr들 전부 확인해서 닫을건 닫고 리뷰반영할건 리뷰반영하고 rebase할건 리베이스하고",
                        "열린 PR들 전부 리베이스해줘", "fix all the failing tests", "update every service to node 22",
                        "implement search and export features in parallel", "모든 패키지 버전 올려줘",
                        "브랜치들 전부 main에 맞춰 리베이스해줘", "billing 말고 모든 서비스 node 22로 올려줘",
                        "update the dependencies in all services", "블로그 pr 전부 머지해줘",
                        # A request ending said as a word of its own, or a 해, 놔 or ㄱ ending (#2232 review).
                        "열린 PR들 전부 리베이스 해줘", "모든 서비스 node 22로 업데이트 해주세요", "PR들 다 리베이스해",
                        "모든 패키지 버전 올려놔", "PR들 전부 리베이스 ㄱ", "모든 서비스 업데이트 좀 해줘",
                        # A past or a completion in a modifier names which units.
                        "내가 올렸던 PR들 전부 리베이스해줘", "어제 실패했던 테스트 전부 고쳐줘",
                        "충돌났던 브랜치들 전부 다시 리베이스해줘", "완료된 이슈들 전부 닫아줘",
                        # The owner's English change verbs inside a Korean request, and a push after a change.
                        "hermes upstream에 낸 PR들 전부 확인해서 리뷰 코멘트 반영하고 rebase까지 ㄱㄱ",
                        "열린 PR들 전부 리뷰 코멘트 반영하고 force push ㄱㄱ", "PR들 전부 rebase 해줘",
                        # A change, then a report on it.
                        "fix all the failing tests and tell me which ones were flaky",
                        # Many PRs or branches stay many: 들, a breadth word or 모든 on the PR or the branch itself.
                        "열린 PR 전부 리베이스해줘", "main 브랜치에 PR들 전부 머지해줘", "모든 PR 테스트 고쳐줘",
                        # A change aimed so a thing does not happen, or units a fault describes, are no fault.
                        "모든 테스트 통과하도록 고쳐줘", "모든 모듈 print 안 쓰게 고쳐줘", "안 돌아가는 테스트 전부 고쳐줘",
                        "모든 페이지 더블클릭 안 되게 고쳐줘",
                        # More request endings, a modifier past, the owner's words (#2232 review 2).
                        "모든 서비스 업데이트 해주세용", "PR들 전부 리베이스해줘요", "모든 서비스 업데이트 해 놔줘", "모든 모듈에 로깅 넣어 줘",
                        "모든 서비스 업데이트 시켜줘", "모든 패키지 버전 올리자", "PR들 전부 리베이스 고", "PR들 전부 리베이스해주셈",
                        "모든 서비스 업데이트 부탁", "충돌났었던 브랜치들 전부 리베이스해줘", "모든 패키지 upgrade해줘",
                        "PR들 전부 확인해서 close할 건 close하고 리베이스하고", "PR들 몽땅 리베이스해줘", "모든 패키지 최신화해줘",
                        "for each of my PRs: rebase onto main and fix the conflicts", "for every open PR, rebase it onto main",
                        # A count of PRs, repos or branches with a breadth word after it is many of them.
                        "PR 3개 전부 리베이스해줘", "PR 5개 다 머지해줘", "레포 3개 전부 의존성 올려줘", "풀리퀘 3개 전부 리베이스해줘",
                        # The same request now is no schedule.
                        "rebase every open PR now", "지금 바로 열린 PR들 전부 리베이스해줘", "이번에 한 번만 열린 PR들 전부 리베이스해줘",
                        # A change aimed so a thing does not happen, with 수정 as with 고쳐.
                        "모든 페이지 버튼 안 보이게 수정해줘", "모든 페이지 링크 안 열리게 수정해줘", "모든 서비스 로그 안 찍히게 수정해줘",
                        "모든 페이지 더블클릭 안 되게 수정해줘"):
            with self.subTest(message=message):
                self.assertTrue(dispatches_dag_ulw(message))
                self.assertEqual(self._dag_ulw_starts(message)[:2], (True, True))
        # Work over many targets without that evidence is offered, never started (`work_over_many_targets`).
        for message in ("review all open PRs", "check all open pull requests and close the stale ones",
                        "migrate across all modules to the new logger"):
            with self.subTest(message=message):
                self.assertFalse(dispatches_dag_ulw(message))
                self.assertTrue(work_over_many_targets(message))
                self.assertEqual(self._dag_ulw_starts(message)[:2], (False, False))
        # Where no rule names it, the hint offers dag-ulw (ask once), and the router asks about it.
        offer = [hint for hint in awareness_route_hint("migrate across all modules to the new logger")["hints"]
                 if hint.get("workflow") == "dag-ulw"]
        self.assertEqual([hint.get("next_action") for hint in offer], ["ask_one_clarification"])
        self.assertEqual(self._dag_ulw_starts("migrate across all modules to the new logger")[2], "dag-ulw")

    def test_each_hard_exclude_alone_keeps_a_change_off_the_graph(self) -> None:
        # Each row is a change the router would dispatch on but for one hard exclude: a skill or engine named first
        # (with a Korean particle, in Korean, or after omh), a past, one pull request's scope (by number, in words or
        # as 풀리퀘), a stop or a hold, a completion, a condition, work until a condition, parallel running as the
        # work's subject, a recurring schedule, one branch (named or current), one PR singled out by a modifier.
        for message in ("team 열린 PR들 전부 리베이스해줘", "plan 모든 패키지 버전 올려줘", "PR들 전부 리베이스했으니 머지해줘",
                        "PR 2232 파일들 전부 고쳐줘", "fix all the files in this PR", "PR들 전부 리베이스 중단하고 머지해줘",
                        "브랜치들 다 정리됨 이제 전부 머지해줘", "테스트 다 통과하면 모든 PR 머지해줘",
                        "fix all the failing tests until CI is green", "fix all the modules that run in parallel",
                        "loop로 모든 서비스 업데이트해줘", "루프로 모든 서비스 업데이트해줘", "omh loop 모든 서비스 업데이트해줘",
                        "update all snapshots in PR number 2240", "2240번 풀리퀘 모든 테스트 다시 고쳐줘",
                        "테스트 수정 완료, 이제 PR들 전부 머지해줘", "rebase all my open PRs every hour", "매일 아침 PR들 전부 리베이스해줘",
                        "pull 951 테스트들 다 고쳐줘", "fix all the failing tests on feature/dag-default",
                        "fix all the failing tests on the current branch", "현재 브랜치 테스트 전부 고쳐줘", "어제 내가 연 PR 테스트 전부 고쳐줘",
                        # A PR number without a counter stays one scope, the numbered twins of "PR 3개".
                        "PR 3 테스트 전부 고쳐줘", "3번 PR 테스트 전부 고쳐줘", "#3 테스트 전부 고쳐줘",
                        # A schedule in its other shapes, and one PR picked out by an adjective (#2232 review 3).
                        "2시간에 한 번씩 레포들 전부 의존성 올려줘", "하루 두 번 열린 PR들 전부 리베이스해줘",
                        "rebase every open PR each morning", "격주로 모든 서비스 로그 포맷 고쳐줘", "cron으로 모든 서비스 업데이트해줘",
                        "주 2회 모든 패키지 업데이트해줘", "update all services twice a day", "스케줄 걸어서 모든 서비스 업데이트해줘",
                        "fix every test in my latest pull request",
                        # A noun with 마다 other than a unit of work, 매 + a noun, an interval, a clock word, every
                        # other day, an event that repeats the work, and one PR picked out by 하나 (#2232 review 4).
                        "반나절마다 PR들 전부 리베이스해줘", "스프린트마다 모든 패키지 올려줘", "배포마다 모든 서비스 설정 업데이트해줘",
                        "매 분기마다 모든 레포 라이선스 업데이트해줘", "매 스프린트 모든 패키지 올려줘", "3일 간격으로 모든 서비스 업데이트해줘",
                        "fix all failing tests on every push", "rebase all open PRs on each merge", "자정에 PR들 전부 리베이스해줘",
                        "하루 걸러 모든 서비스 업데이트해줘", "우리 팀 PR 하나 테스트 다 고쳐줘"):
            with self.subTest(message=message):
                self.assertFalse(dispatches_dag_ulw(message))
                router, hint, _candidate = self._dag_ulw_starts(message)
                self.assertFalse(router)
                self.assertFalse(hint)
        # A review is offered, never dispatched: the allow list holds only changes.
        self.assertFalse(dispatches_dag_ulw("PR들 전부 리뷰해줘"))
        self.assertTrue(work_over_many_targets("PR들 전부 리뷰해줘"))

    def test_a_skill_named_or_invoked_keeps_a_change_over_many_targets(self) -> None:
        # The name with a Korean particle, in Korean, after omh, or as the instrument anywhere, and an explicit
        # invocation of a skill or the loop, keep the request on that skill: the router does not dispatch dag-ulw and
        # the hint does not start it.
        for message, skill in (("loop로 모든 서비스 업데이트해줘", "loop"), ("루프로 모든 서비스 업데이트해줘", "loop"),
                               ("loop으로 PR들 전부 리베이스해줘", "loop"), ("omh loop 모든 서비스 업데이트해줘", "loop"),
                               ("github-event-ops로 열린 PR들 전부 리베이스해줘", "github-event-ops"),
                               ("github-event-ops에서 열린 PR들 전부 리베이스해줘", "github-event-ops"),
                               ("deploy-and-monitor로 모든 서비스 업데이트해줘", "deploy-and-monitor"),
                               ("git-workflow로 브랜치들 전부 리베이스해줘", "git-workflow"),
                               ("use git-workflow: 열린 PR들 전부 리베이스해줘", "git-workflow"),
                               ("rebase all my open PRs with git-workflow", "git-workflow")):
            with self.subTest(message=message):
                route = route_chat_message(message, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", skill))
                self.assertEqual(self._dag_ulw_starts(message)[:2], (False, False))
        for message in ("team으로 모든 서비스 업데이트해줘", "ralph로 모든 서비스 업데이트해줘", "maestro로 모든 패키지 버전 올려줘",
                        "refactor-plan으로 모든 모듈 python 3.12로 올려줘", "refactor-plan를 써서 모든 모듈 python 3.12로 올려줘",
                        "code-review로 열린 PR들 전부 고쳐줘",
                        "열린 PR들 전부 git-workflow로 리베이스해줘", "모든 서비스 루프로 돌면서 업데이트해줘"):
            with self.subTest(message=message):
                self.assertFalse(dispatches_dag_ulw(message))
                self.assertEqual(self._dag_ulw_starts(message)[:2], (False, False))
        # The cue reads the change; the explicit invocation is what keeps it off the graph, on both surfaces.
        self.assertTrue(dispatches_dag_ulw("use git-workflow: 열린 PR들 전부 리베이스해줘"))

    def test_a_pull_request_link_is_one_scope(self) -> None:
        # The executable text leaves the link out, so the callers pass it: the PR it points at is one scope.
        message = "https://github.com/rlaope/oh-my-hermes/pull/2240 의 테스트 전부 고쳐줘"
        regions = reference_regions(message)
        self.assertTrue(dispatches_dag_ulw(regions.executable_text))
        self.assertFalse(dispatches_dag_ulw(regions.executable_text, regions.links))
        self.assertFalse(work_over_many_targets(regions.executable_text, regions.links))
        self.assertEqual(self._dag_ulw_starts(message)[:2], (False, False))

    def test_a_count_is_breadth_only_on_prs_repos_or_branches_with_a_breadth_word(self) -> None:
        # Lead decision on the #2232 review: "PR 3개 전부" and "PR 5개 다" are many PRs, while a count of anything else,
        # or a count with no breadth word, is a small single task that starts nothing, as on main.
        for message in ("PR 3개 전부 리베이스해줘", "PR 5개 다 머지해줘", "레포 3개 전부 의존성 올려줘", "브랜치 2개 모두 main에 리베이스해줘"):
            with self.subTest(message=message):
                self.assertEqual(self._dag_ulw_starts(message)[:2], (True, True))
        for message in ("파일 2개 수정해줘", "테스트 3개 고쳐줘", "파일 두 개 이름 바꿔줘", "이슈 2건 닫아줘", "PR 2개 머지해줘",
                        "엔드포인트 2개 인증 붙여줘", "서비스 세 개 node 22로 올려줘", "PR 3개 리베이스해줘", "파일 2개 전부 수정해줘",
                        "테스트 3개 다 고쳐줘", "서비스 세 개 다 업데이트해줘", "PR 3개 중 하나 리베이스해줘"):
            with self.subTest(message=message):
                self.assertFalse(dispatches_dag_ulw(message))
                self.assertFalse(work_over_many_targets(message))
                router, hint, candidate = self._dag_ulw_starts(message)
                self.assertEqual((router, hint), (False, False))
                self.assertNotEqual(candidate, "dag-ulw")

    def test_a_unit_of_work_with_madda_is_each_of_them_not_a_schedule(self) -> None:
        # "서비스마다" and "PR마다" mean each of them, so they route as they did before the schedule reader read 마다:
        # a breadth object with a change runs as a graph, and one with none starts nothing. Any other noun with 마다 is
        # a schedule (`test_each_hard_exclude_alone_keeps_a_change_off_the_graph`).
        for message in ("PR마다 테스트 전부 고쳐줘", "각 서비스마다 로깅 추가해줘"):
            with self.subTest(message=message):
                self.assertEqual(self._dag_ulw_starts(message)[:2], (True, True))
        for message in ("서비스마다 로깅 추가해줘", "레포마다 CI 설정 고쳐줘", "모듈마다 로거 붙여줘", "패키지마다 버전 올려줘"):
            with self.subTest(message=message):
                self.assertEqual(self._dag_ulw_starts(message)[:2], (False, False))

    def test_a_missing_skill_catalog_is_not_kept(self) -> None:
        # With no sidecar the catalog names are unknown, and that is not kept: once the sidecar answers, a request
        # that opens with a catalog skill's name is that skill's again.
        from omh.plugin_bundle.omh import dag_ulw_cues

        dag_ulw_cues._CATALOG_SKILL_NAMES.clear()
        try:
            with mock.patch.object(dag_ulw_cues._skill_shortlist, "catalog_skill_names", return_value=None):
                self.assertFalse(dag_ulw_cues._opens_with_a_skill_name("code-review 모든 패키지 버전 올려줘"))
            with mock.patch.object(
                dag_ulw_cues._skill_shortlist, "catalog_skill_names", return_value=frozenset({"code-review"})
            ):
                self.assertTrue(dag_ulw_cues._opens_with_a_skill_name("code-review 모든 패키지 버전 올려줘"))
        finally:
            dag_ulw_cues._CATALOG_SKILL_NAMES.clear()

    def test_no_new_work_over_many_targets_starts_or_offers_nothing(self) -> None:
        # Neither the router nor the hint starts dag-ulw, and no offer names it: a past or a completion, a stop or a
        # hold, a negation or no need, a lookup, a summary or a share, a report, one numbered pull request, a
        # condition, one command over everything, one change in many places, a question, work until a condition, and
        # a request that opens with a skill's name, ulw or a retired engine (#2232 review).
        for message in ("열린 PR들 전부 리베이스했습니다", "PR들 전부 머지했어요", "PR들 전부 머지됐네", "PR들 전부 닫혔네",
                        "브랜치들 다 정리됨", "upstream PR들 다 리뷰 반영 끝", "PR들 전부 확인했어",
                        "열린 PR들 전부 머지 보류해줘", "PR들 전부 리베이스 중단해줘", "모든 서비스 배포 멈춰줘",
                        "모든 서비스 업데이트 취소해줘", "PR들 전부 머지 금지", "PR들 전부 머지하지 마",
                        "PR들 전부 머지하면 안 돼", "PR들 전부 머지할 필요는 없어", "실패하는 테스트 다 안 고쳐도 돼",
                        "upstream PR들 전부 현황 파악해줘", "PR들 전부 요약해줘", "모든 레포 라이선스 조사해줘",
                        "모든 모듈 구조 설명해줘", "각 엔드포인트 응답 시간 측정해줘", "PR들 전부 슬랙에 공유해줘",
                        "PR들 전부 알려줘", "모든 서비스 상태 확인해줘", "check the status of all open PRs",
                        "review all open PRs and send me a list of risky ones", "audit all the repos and send me the findings",
                        "PR들 전부 리뷰하고 결과만 정리해줘", "review all open PRs and just tell me which are safe",
                        "review every file in pull request 2232 and fix what's wrong", "2232번 PR 파일들 전부 리뷰해줘",
                        "내 PR 파일들 전부 리뷰해줘", "review all the files touched by PR 2232", "review all the files in this PR",
                        "fix all the tests in this PR", "fix the login bug on every page", "fix the bug in all three services",
                        "update the copyright year in all files", "테스트 다 통과하면 머지해줘", "PR 다 올렸으면 리뷰 요청해줘",
                        "서비스 다 내려가면 나한테 전화해줘", "패키지 다 설치되면 서버 띄워줘", "전체 서비스 재시작해줘",
                        "모든 브랜치 푸시해줘", "전체 테스트 돌려줘", "모든 파일 포맷팅해줘", "모든 서비스 env 파일 하나로 합쳐줘",
                        "각 파일 첫 줄만 뽑아줘", "README 오타 전부 고쳐줘", "모든 테스트가 깨졌어, 고쳐줘", "PR들 전부 머지됐어?",
                        "are all the tests passing?", "fix all the failing tests until the suite is green",
                        "모든 테스트 통과할 때까지 다 고쳐줘", "team 실패하는 테스트 다 고쳐", "ask 실패하는 테스트 다 고쳐",
                        "ulw 실패하는 테스트 다 고쳐", "울트라워크로 모든 서비스 업데이트해줘", "프로파일 전부 지워줘",
                        "고객서비스 전부 개선해줘", "이 버그 pr 리뷰해줘",
                        # #2232 review: one numbered pull request in more forms, one fault said as "does not work", a
                        # push over everything, a clause before a bare request ending, a report after a review.
                        "update all snapshots in PR number 2240", "github.com/rlaope/oh-my-hermes/pull/2240 의 테스트 전부 고쳐줘",
                        "https://github.com/rlaope/oh-my-hermes/pull/2240 의 테스트 전부 고쳐줘",
                        "fix all the tests in https://github.com/rlaope/oh-my-hermes/pull/2240",
                        "2240번 풀리퀘 모든 테스트 다시 고쳐줘", "2240번 풀 리퀘스트 테스트 전부 고쳐줘",
                        "모든 서비스에서 로그인이 안 돼 고쳐줘", "모든 서비스에서 결제가 안 돼 고쳐줘",
                        "모든 페이지에서 버튼이 안 눌려, 고쳐줘", "모든 페이지에서 모달이 안 떠 고쳐줘", "모든 서비스 로그인 먹통이야 고쳐줘",
                        "모든 브랜치 push 해줘", "PR들 다 리베이스하려고 해", "PR들 전부 리베이스해야 해",
                        "PR들 전부 리베이스 완료됨", "review all open PRs and send me a list of risky ones",
                        # #2232 review 2: a recurring schedule, one PR or branch singled out, one fault in any words,
                        # and no-work twins of the new request forms.
                        "30분마다 열린 PR들 전부 리베이스해줘", "매주 월요일 레포들 전부 의존성 업데이트해줘",
                        "update every service nightly", "아침마다 모든 브랜치 main에 리베이스해줘", "PR 올릴 때마다 테스트 전부 고쳐줘",
                        "방금 올린 PR 파일들 전부 리뷰 반영해줘", "feature/dag-default 브랜치 테스트 전부 고쳐줘",
                        "모든 테스트가 안 돌아가 고쳐줘", "PR들 전부 CI가 안 돌아 고쳐줘", "모든 페이지 다크모드가 안 먹어 고쳐줘",
                        "모든 페이지가 하얗게 나와 고쳐줘", "모든 테스트가 돌지 않아 고쳐줘", "모든 서비스에서 로그인이 안 돼 수정 부탁",
                        "모든 서비스 업데이트 해 놨어", "PR들 몽땅 머지됐어", "모든 패키지 최신화 완료",
                        "for each of my PRs, tell me the status", "for each of my PRs: summarize the changes",
                        "PR들 몽땅 리베이스할까?", "PR 3개 중 하나 리베이스해줘"):
            with self.subTest(message=message):
                self.assertFalse(dispatches_dag_ulw(message))
                self.assertFalse(work_over_many_targets(message))
                router, hint, candidate = self._dag_ulw_starts(message)
                self.assertFalse(router)
                self.assertFalse(hint)
                self.assertNotEqual(candidate, "dag-ulw")

    def test_a_question_about_dag_ulw_is_answered_whichever_skill_scored(self) -> None:
        for message in ("dag ulw는 뭐야?", "dag ulw 설명해줘", "how does dag-ulw handle repairs?", "tell me about dag-ulw",
                        "dag ulw 결과 알려줘", "dag ulw 쓰면 비용이 얼마나 나와?", "dag ulw 문서 보여줘", "dag ulw 어떻게 써?",
                        "dag ulw is broken, it never settles the verify node", "can you explain dag ulw?",
                        "can you dag ulw this or should I just fix it myself?", "would you dag ulw it, if you were me?",
                        "can you dag ulw the migration 아니면 그냥 ultrawork로 할까?",
                        # A complaint after the name reports a problem (`name_report_cues`).
                        "dag ulw broke my build", "dag ulw failed again", "dag ulw 너무 느려", "dag ulw 또 실패했어",
                        "dag ulw 때문에 빌드 깨졌어", "ulw dag ordering looks wrong in the docs", "dag ulw 멈춘 듯",
                        "dag ulw spawned too many agents", "dag ulw 결과가 이상해",
                        # So does a question after it, whatever it asks about: dag-ulw only runs work.
                        "dag ulw run finished, now what", "dag ulw 결제 api 뭐야", "dag ulw 결제 취소 api 뭐야",
                        "dag ulw 이거 어떻게 써", "dag ulw how does the verify node work"):
            with self.subTest(message=message):
                self.assertTrue(asks_about_dag_ulw(message))
                route = route_chat_message(message, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("fallback", "oh-my-hermes"))
                self.assertFalse([hint for hint in awareness_route_hint(message)["hints"]
                                  if hint.get("workflow") == "dag-ulw"])
        for message in ("dag ulw는 취소 어떻게 해?", "dag ulw 취소할 수 있어?", "dag ulw 취소 어떻게 해", "dag ulw 취소 방법 알려줘",
                        "dag ulw 취소할 수 있어", "dag ulw 취소 가능해", "dag ulw 취소 뭐야", "dag ulw 취소 왜 했어",
                        "dag ulw 취소해줘 왜 안 돼", "dag ulw 멈출 수 있어", "dag ulw 중단하면 어떻게 돼",
                        # Whether it stopped: a past or passive form, or 여부.
                        "dag ulw 취소됐어?", "dag ulw 멈췄어?", "dag ulw 중단됐나?", "dag ulw 종료됐어?", "dag ulw 취소했어?",
                        "dag ulw 주문 취소됐어?", "dag ulw 취소된 거 맞아?", "dag ulw 취소 완료됐어?", "dag ulw 그만뒀어?",
                        "dag ulw 결과 보고 취소 여부 결정할게",
                        # A status check before a past stop asks whether it stopped; it is no re-run.
                        "dag ulw 확인해줘, 취소됐는지", "dag ulw 보여줘, 취소된 거", "dag ulw 봐줘, 아까 취소한 거 끝났는지",
                        "dag ulw 살펴봐줘, 중단된 거", "dag ulw 체크해줘 아까 멈춘 거", "dag ulw 확인해줘 아까 멈춘 거",
                        "dag ulw 취소 눌렀어?", "dag ulw 멈춘 거 확인해줘", "dag ulw 취소 여부 확인해줘",
                        # A sigil before a question is a question.
                        "/dag-ulw 취소 어떻게 해", "$dag-ulw 취소 방법 알려줘"):
            with self.subTest(message=message):
                # Asking how to cancel, or whether one can, is a question about the skill, not a stop or a run.
                self.assertEqual(dag_ulw_control(message), "")
                self.assertTrue(asks_about_dag_ulw(message))
                self.assertFalse(invokes_dag_ulw(message))
                self.assertEqual(route_chat_message(message, source="discord")["action"], "fallback")
                self.assertFalse(awareness_route_hint(message)["hints"])
        for message in ("/dag-ulw what is it?", "/dag-ulw how do I cancel the run?"):
            with self.subTest(message=message):
                self.assertFalse(invokes_dag_ulw(message))
                route = route_chat_message(message, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("fallback", "oh-my-hermes"))
        self.assertTrue(invokes_dag_ulw("/dag-ulw migrate the billing service"))
        for message in ("/dag-ulw explain the plan, then run the migration",
                        "/dag-ulw is the auth refactor done? if not, finish it",
                        "/dag-ulw what's left of the migration, finish it",
                        "/dag-ulw how do I fix the auth bug? fix it"):
            with self.subTest(message=message):
                # A question that goes on to ask for work is still a command.
                self.assertTrue(invokes_dag_ulw(message))
        self.assertEqual(dag_ulw_control("dag ulw 그만큼 빠르게"), "")  # 그만큼 ("that much") holds no stop word
        self.assertFalse(asks_about_dag_ulw("dag ulw add the export API and its docs"))
        self.assertFalse(asks_about_dag_ulw("can you dag ulw the migration?"))

    def test_a_polite_request_runs_it_on_both_paths(self) -> None:
        for message in ("can you dag ulw the migration?", "could you please dag ulw the billing refactor"):
            with self.subTest(message=message):
                route = route_chat_message(message, source="discord")
                self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "dag-ulw"))
                hints = awareness_route_hint(message)["hints"]
                self.assertEqual((hints[0]["workflow"], hints[0]["next_action"]), ("dag-ulw", "start_dag_run"))

    def test_small_coding_work_stays_on_ultrawork(self) -> None:
        for message in ("rename the helper function in the parser module", "fix the flaky checkout test",
                        "build a login component"):
            with self.subTest(message=message):
                self.assertEqual(route_chat_message(message)["selected_skill"], "ultrawork")

    def test_inside_hermes_the_route_hint_reaches_dag_ulw_the_same_way(self) -> None:
        # The review's three sentences: big work by intent reaches dag-ulw in Hermes' own per-turn hint too.
        for message in ("implement the export feature in parallel across the api and ui modules, then integrate",
                        "split this into parallel work lanes with disjoint ownership",
                        "do the api and the ui in parallel then integrate"):
            with self.subTest(message=message):
                hints = awareness_route_hint(message)["hints"]
                self.assertEqual((hints[0]["workflow"], hints[0]["next_action"]), ("dag-ulw", "start_dag_run"))
                self.assertEqual(route_chat_message(message, source="discord")["selected_skill"], "dag-ulw")
        for message in ("fix the bug where uploads run in parallel and corrupt the file", "list all modules that import requests",
                        "the fan out of this function is too high, fix it", "동시에 두 요청이 오면 캐시가 깨지는 버그 고쳐줘",
                        "the fan out across the services is too high", "split the paragraph into parallel parts for the translation",
                        "what does it mean to fan out across services", "api 모듈과 ui 모듈이 동시에 요청하면 캐시가 깨져, 고쳐줘"):
            with self.subTest(message=message):
                self.assertFalse([hint for hint in awareness_route_hint(message)["hints"]
                                  if hint.get("workflow") == "dag-ulw"])

    def test_a_stop_or_status_word_for_the_run_starts_nothing_and_points_at_the_run(self) -> None:
        # The delta review's sentences: a person stopping or checking a paid run must not be steered into another.
        cancel = ("dag ulw stop", "dag ulw cancel the run", "dag ulw 멈춰", "dag ulw 그만해", "dag ulw 취소",
                  "dag ulw 중지해줘", "dag ulw 돌린 거 취소해줘", "/dag-ulw stop", "cancel dag ulw",
                  # A verb ending after any words, or a word for the run before the stop noun (owner decision O7).
                  "dag ulw 주문 취소해줘", "dag ulw 주문취소해줘", "dag ulw 작업 취소", "dag ulw 실행 중단", "dag ulw 런 취소",
                  "dag ulw 지금 거 취소", "dag ulw 지금 돌리는 거 취소", "dag ulw의 실행 취소",
                  # Polite and casual verb endings, and 그만 돌려.
                  "dag ulw 취소하세요", "dag ulw 취소해요", "dag ulw 취소할게", "dag ulw 취소합니다", "dag ulw 취소 부탁해",
                  "dag ulw 취소 부탁드려요", "dag ulw 취소 좀 해줘", "dag ulw 취소요", "dag ulw 취소좀", "dag ulw 취소 ㄱ",
                  "dag ulw 주문 취소하세요", "dag ulw 그거 취소해요", "dag ulw 그만 돌려", "dag ulw 멈춰요", "dag ulw 그만하세요",
                  # A modified word for the run, or a stop adverb.
                  "dag ulw 진행 중인 작업 취소", "dag ulw 현재 작업 취소", "dag ulw 이번 실행 취소", "dag ulw 방금 거 취소",
                  "dag ulw 아까 그거 취소", "dag ulw 모든 작업 취소", "dag ulw 전체 취소", "dag ulw 바로 취소", "dag ulw 즉시 중단",
                  "dag ulw 지금 진행 중인 작업 바로 취소해줘", "dag ulw 지금 바로 취소", "dag ulw 그것도 취소",
                  "dag ulw 이전 거 취소", "dag ulw 취소요",
                  # The commonest polite ending and other plain stops, and stop words beyond 취소/중지/중단.
                  "dag ulw 취소해줘요", "dag ulw 중단해줘요", "dag ulw 멈춰줘요", "dag ulw 그만해줘요", "dag ulw 작업 취소해줘요",
                  "dag ulw 주문 취소해줘요", "dag ulw 취소시켜줘요", "dag ulw 취소해줄래요", "dag ulw 그만둬요", "dag ulw 그만 돌리자",
                  "dag ulw 멈춰라", "dag ulw 취소해봐", "dag ulw 취소 ㄱㄱ", "dag ulw 취소하고 싶어", "dag ulw 취소해야 해",
                  "dag ulw 정지해줘", "dag ulw 종료해줘", "dag ulw 스톱", "dag ulw 꺼줘", "dag ulw 취소해줄 수 있어?",
                  "dag ulw 다른 거 취소", "dag ulw 저거 말고 이거 취소", "dag ulw 테스트 실패하면 멈춰",
                  # The act of stopping after the bare stop word or words for the run, and a stop that did not take.
                  "dag ulw 작업 취소 처리해줘", "dag ulw 지금 돌리는 거 취소 처리해줘", "dag ulw 이번 실행 중단 처리해줘",
                  "dag ulw 런 종료 처리해줘", "dag ulw 취소 처리해줘", "dag ulw 작업 취소 진행해줘", "dag ulw 취소 요청해줘",
                  "dag ulw 런 중단 요청 보내줘", "dag ulw 취소 눌러줘", "dag ulw 취소 실행해줘", "dag ulw 중단 명령 내려줘",
                  "dag ulw 취소 처리", "dag ulw 작업 취소 좀 진행해 주세요", "dag ulw 취소 요청",
                  # A halt put on the run, a processed cancel with a softener, and a cancel button pressed while the
                  # run goes on.
                  "dag ulw 작업 중단 걸어줘", "dag ulw 중단 걸어줘", "dag ulw 작업 취소 처리 좀",
                  "dag ulw 취소 버튼 눌렀는데 계속 돌아가",
                  # A stop with its reason after it.
                  "dag ulw stop, too expensive", "dag ulw cancel, wrong task",
                  "dag ulw 취소했는데 계속 돌아가", "dag ulw 취소했는데 아직 안 멈춰", "dag ulw 취소 눌렀는데 계속 돌아가",
                  "dag ulw 멈춤",
                  "dag ulw 취소해 주시겠어요?", "dag ulw 취소해주실래요",
                  # A polite opener or a trailing please before an English stop word.
                  "can you stop dag ulw", "could you cancel the dag ulw run", "can you please cancel dag ulw?",
                  "would you stop the dag ulw run now", "stop dag ulw please")
        status = ("dag ulw status", "dag ulw 상태 보여줘", "dag ulw 진행 상황", "dag ulw 로그 보여줘")
        for message, action in [*((m, "cancel") for m in cancel), *((m, "show_status") for m in status)]:
            with self.subTest(message=message):
                self.assertFalse(invokes_dag_ulw(message))
                self.assertEqual(route_chat_message(message, source="discord")["action"], "fallback")
                payload = awareness_route_hint(message)
                self.assertEqual([(hint["workflow"], hint["next_action"]) for hint in payload["hints"]],
                                 [("dag-ulw", action)])
                context = awareness_route_hint_context_from_payload(payload)
                self.assertIn("call omh_dag action=cancel to stop the run in progress, or action=status", context)
                self.assertNotIn("Start at once", context)
                self.assertNotIn("plan_todo=", context)
        # Work that names a cancel button, a status page or a log-in is still work.
        for message in ("dag ulw add a cancel button to the checkout", "dag ulw build the status page",
                        "dag ulw stop words in the search index", "dag ulw 결제 취소 기능 구현해줘",
                        "dag ulw 상태 관리 모듈 리팩터링해줘", "dag ulw 로그인 기능 구현해줘", "dag ulw 주문 취소 기능 구현해줘",
                        # A work noun or verb after the stop word, or a clause that makes it a condition of the work.
                        "dag ulw 주문 취소 로직 좀 정리해줘요", "dag ulw 결제 취소 플로우 리팩터링 ㄱ",
                        "dag ulw 주문 취소 시 환불되게 해줘", "dag ulw 종료 조건 정리해줘",
                        # 그만큼 ("that much") holds no stop word.
                        "dag ulw 그만큼 빠르게 해줘",
                        # Work words after the stop word, a work-condition clause with nothing else, and an object
                        # before 꺼줘.
                        "dag ulw 결제 취소 이슈 해결해줘", "dag ulw 결제 취소 버그 잡아줘", "dag ulw 결제 취소 디버깅해줘",
                        "dag ulw 결제 취소 쿼리 최적화해줘", "dag ulw 결제 취소 응답 캐싱해줘", "dag ulw 결제 취소 스펙 검토해줘",
                        "dag ulw 주문 취소 문서 써줘", "dag ulw 주문 취소 dto 정의해줘", "dag ulw 중단점 걸고 디버깅해줘",
                        "dag ulw로 중단점 디버깅해줘", "dag ulw 결제 취소 시 쿠폰 복구해줘", "dag ulw 다크모드 꺼줘",
                        "dag ulw 디버그 로그 꺼줘", "dag ulw로 주문 취소 API 만들어줘", "dag ulw 종료 시 정리 작업 추가해줘",
                        # Past the stop word's own ending, a request verb with its own stem: work, whatever the verb.
                        "dag ulw 결제 취소 내역 확인해줘", "dag ulw 결제 취소 내역 롤백해줘", "dag ulw 결제 취소 내역 막아 줘",
                        "dag ulw 결제 취소 내역 재시도하게 해줘", "dag ulw 결제 취소 웹훅 재전송해줘", "dag ulw 종료 훅 등록해줘",
                        "dag ulw 그레이스풀 종료 지원해줘", "dag ulw 배치 중지 스크립트 짜줘", "dag ulw 알림 꺼줘",
                        # A condition clause right before the last stop word, and a past form inside work.
                        "dag ulw 결제 모듈 리팩터링하고 실패하면 중단해줘", "dag ulw 결제 모듈 리팩터링하다가 실패하면 중단",
                        "dag ulw 취소된 주문 정리해줘", "dag ulw 정지된 사용자 목록 api 만들어줘",
                        # A condition after the stop word with no request verb at the end: the condition alone decides.
                        "dag ulw 결제 취소 시 쿠폰 복구", "dag ulw 취소하면 쿠폰 복구",
                        # Work on a cancel, not the act of cancelling the run; and a re-run that names a past stop.
                        "dag ulw 취소 처리 로직 만들어줘", "dag ulw 취소 요청 큐 리팩터링해줘",
                        "dag ulw 다시 돌려줘, 아까 취소한 거", "dag ulw 다시 실행해줘. 어제 중단된 거",
                        "dag ulw 재시작해줘 취소했던 거", "dag ulw 다시 돌려줘 아까 취소한 거", "dag ulw 다시 해줘, 아까 취소한 거 말고",
                        "dag ulw 이어서 해줘 중단된 거", "dag ulw 계속 해줘, 아까 중단된 거", "dag ulw 마저 해줘 중단된 거",
                        "dag ulw 이어서 해줘, 아까 멈춘 거", "dag ulw 마저 해줘, 아까 멈춘 거", "dag ulw 멈춤 버튼 만들어줘",
                        "dag ulw 취소했는데 계속 돌아가는 버그 고쳐줘"):
            with self.subTest(message=message):
                self.assertTrue(invokes_dag_ulw(message))
                self.assertEqual(route_chat_message(message, source="discord")["selected_skill"], "dag-ulw")

    def test_a_stop_noun_after_other_words_asks_whether_to_stop_or_run(self) -> None:
        # Owner decision O7: "주문 취소" names an order cancel as easily as a cancel of the run, so with no verb ending
        # it neither cancels the run nor starts one; both paths ask, naming both readings.
        for message in ("dag ulw 주문 취소", "dag ulw 결제 취소", "dag ulw 예약 취소", "dag ulw 구독 취소",
                        "dag ulw 장바구니 주문 취소", "dag ulw 업로드 중단", "dag ulw 주문취소", "dag ulw 주문을 취소",
                        # Past three words a verb ending asks too: the words may still name the work.
                        "dag ulw 지금 돌리는 결제 모듈 작업 취소해줘", "dag ulw 아까 내가 시작한 결제 작업 좀 취소해줘",
                        # After other words a bare 요, 좀 or ㄱ is no verb; and any other stop with no work after it.
                        "dag ulw 주문 취소요", "dag ulw 결제 취소 좀", "dag ulw 주문 취소 ㄱ",
                        "dag ulw 취소 바람", "dag ulw로 취소해줘", "dag ulw 이 주문 취소", "dag ulw 결과 취소",
                        # The last stop word decides: a stop of the work on a cancel feature asks, never runs.
                        "dag ulw 결제 취소 기능 만드는 거 그만해줘", "dag ulw 주문 취소 로직 작업 중단해줘",
                        "dag ulw 결제 취소 api 작업 멈춰", "dag ulw 주문 취소 기능 구현하던 거 취소해줘",
                        "dag ulw 결제 중단 처리 리팩터링 그만해", "dag ulw 종료 시 정리 작업 하던 거 멈춰줘",
                        "dag ulw 결제 취소 테스트 돌리는 거 중단해", "dag ulw 아까 시작한 주문 취소 기능 작업 취소해줘",
                        "dag ulw 결제 취소 플로우 리팩터링 중단해주세요",
                        "dag ulw 결제 모듈에 주문 취소 기능 넣고 기존 주문 취소해줘",
                        # A stop of the run, then more work: stop and restart, so it asks.
                        "dag ulw 지금 거 취소하고 결제 모듈부터 다시 해줘", "dag ulw 취소하고 다시 처음부터 구현해줘",
                        "dag ulw 취소하고 결과 정리해줘",
                        # 꺼줘 after a word for the run, and a stop left with only its ending past many words.
                        "dag ulw 결제 모듈 작업 꺼줘", "dag ulw 아까 돌린 리팩터링 작업 꺼줘", "dag ulw 테스트 돌리는 거 꺼줘",
                        "dag ulw 결제 작업 취소 좀 빨리 해줘", "dag ulw 아까 그 결제 작업 취소 다시 해줘", "dag ulw 중단하면 돼",
                        # A request before a present stop: it may stop the run in progress, so it still asks.
                        "dag ulw 결제 모듈 리팩터링해줘. 기존 런은 취소",
                        # The stop word's own ending is not a request verb of its own (시켜줘, 둬줘).
                        "dag ulw 아까 내가 시작한 결제 작업 중단시켜줘", "dag ulw 아까 내가 시작한 결제 작업 그만둬줘",
                        # The act of stopping after words that may name the work: processing a cancel, a halt put on
                        # it, a cancel button pressed. The act names no work of its own.
                        "dag ulw 결제 취소 처리해줘", "dag ulw 주문 취소 처리해줘", "dag ulw 결제 취소 진행해줘",
                        "dag ulw 결제 작업 중단 걸어줘", "dag ulw 취소 버튼 눌러줘", "dag ulw 결제 취소 처리 좀",
                        "dag ulw 결제 취소 요청 보내줘",
                        # An English stop word beside words that may name the work.
                        "dag ulw cancel order", "dag ulw order cancel", "dag ulw cancel the migration",
                        "dag ulw stop the billing job"):
            with self.subTest(message=message):
                self.assertEqual(dag_ulw_control(message), "cancel_or_run")
                self.assertFalse(invokes_dag_ulw(message))
                route = route_chat_message(message, source="discord")
                self.assertEqual((route["action"], route["selected_skill"], route["candidate_skill"]),
                                 ("clarify", "oh-my-hermes", "dag-ulw"))
                self.assertIn("stop the dag-ulw run in progress, or start a dag-ulw run", route["clarification"])
                payload = awareness_route_hint(message)
                self.assertEqual([(hint["workflow"], hint["next_action"]) for hint in payload["hints"]],
                                 [("dag-ulw", "ask_one_clarification")])
                self.assertIn("it does neither until the user says which", payload["hints"][0]["reason"])
                context = awareness_route_hint_context_from_payload(payload)
                self.assertIn("Neither define a run nor cancel one yet: ask one short question", context)
                self.assertNotIn("Start at once", context)
                self.assertNotIn("call omh_dag action=cancel to stop the run in progress, or action=status", context)
                self.assertNotIn("plan_todo=", context)

    def test_a_dag_ulw_hint_asks_the_first_response_to_start_at_once(self) -> None:
        # Its lane's card says to plan first and ask; dag-ulw starts at once (owner decision D1), by name or by size.
        for message in ("dag ulw add the export API, its UI button and docs, then verify with the test suite",
                        "split this into parallel work lanes with disjoint ownership"):
            with self.subTest(message=message):
                context = awareness_route_hint_context_from_payload(awareness_route_hint(message))
                self.assertIn("first_response_shape=Start at once: plan the graph and call omh_dag define", context)

    def test_big_parallel_work_reaches_dag_ulw_without_its_name(self) -> None:
        route = route_chat_message("implement the export feature in parallel across the api and ui modules, then integrate")
        self.assertEqual((route["action"], route["selected_skill"]), ("dispatch", "dag-ulw"))


class SigilTests(unittest.TestCase):
    """After the `/dag-ulw` sigil. Hermes sends every `/dag-ulw` turn as one, so these are the turns that run."""

    def _routed(self, message: str) -> tuple[str, str, list[tuple[str, str]]]:
        route = route_chat_message(message, source="discord")
        hints = [(str(hint["workflow"]), str(hint["next_action"])) for hint in awareness_route_hint(message)["hints"]]
        return str(route["action"]), str(route["selected_skill"]), hints

    def test_a_question_after_the_sigil_starts_no_run_in_korean_or_english(self) -> None:
        for message in ("/dag-ulw 이게 뭐야?", "/dag-ulw 이게 뭐야", "/dag-ulw 어떻게 써?", "/dag-ulw dag-ulw가 뭐야?",
                        "/dag-ulw 결제 api 뭐야", "/dag-ulw CI 통과했어?", "/dag-ulw 결제 모듈 리팩터링 끝났어?",
                        "/dag-ulw 이거 병렬로 돌릴 수 있어?", "/dag-ulw what is it?", "/dag-ulw how does it work?",
                        # A question mark that ends it, in any language, as after the bare name.
                        "/dag-ulw did the anti-slop sweep finish?", "/dag-ulw Kubernetes是什么？",
                        # A question after skills stacked on the sigil invokes none of them.
                        "/dag-ulw /ulw-plan what is it?", "/dag-ulw /ulw-plan 이게 뭐야?",
                        # A capability question routes as it does without the sigil, not to dag-ulw's own card.
                        "/dag-ulw 슬랙에서 /omh 치면 뭐가 떠야해?"):
            with self.subTest(message=message):
                action, skill, hints = self._routed(message)
                self.assertFalse(invokes_dag_ulw(message))
                self.assertTrue(asks_about_dag_ulw(message))
                self.assertNotEqual((action, skill), ("dispatch", "dag-ulw"))
                self.assertNotIn(("dag-ulw", "start_dag_run"), hints)
                self.assertFalse([hint for hint in hints if hint[1] in ("present_plan", "start_dag_run")])

    def test_status_and_stop_words_after_the_sigil_point_at_the_run(self) -> None:
        for message, control, hint in (("/dag-ulw 뭐 남았어?", "status", ("dag-ulw", "show_status")),
                                       ("/dag-ulw 상태 보여줘", "status", ("dag-ulw", "show_status")),
                                       ("dag ulw 뭐 남았어?", "status", ("dag-ulw", "show_status")),
                                       ("/dag-ulw what's left?", "status", ("dag-ulw", "show_status")),
                                       ("/dag-ulw 취소해줘", "cancel", ("dag-ulw", "cancel")),
                                       ("/dag-ulw /ulw-plan 취소해줘", "cancel", ("dag-ulw", "cancel")),
                                       ("/dag-ulw 주문 취소", "cancel_or_run", ("dag-ulw", "ask_one_clarification"))):
            with self.subTest(message=message):
                self.assertEqual(dag_ulw_control(message), control)
                action, skill, hints = self._routed(message)
                self.assertNotEqual(action, "dispatch")
                self.assertEqual(hints, [hint])

    def test_a_request_after_the_sigil_still_runs_it(self) -> None:
        for message in ("/dag-ulw 왜 깨졌는지 찾아서 고쳐줘", "/dag-ulw 뭐가 문제인지 보고 PR들 정리해",
                        "/dag-ulw 내가 올린 upstream pr들 전부 확인해서 닫을건 닫고 리뷰반영할건 리뷰반영하고 rebase할건 "
                        "리베이스하고 ㄱㄱ",
                        "/dag-ulw api, ui, docs 작업을 병렬로 나눠서 구현해줄 수 있어?", "/dag-ulw 결제 모듈 고쳐줄래?",
                        "/dag-ulw 나눠서 해주실래요?", "/dag-ulw how do I fix the auth bug? fix it",
                        "/dag-ulw can you dag ulw the migration?", "/dag-ulw migrate the billing service?",
                        "/dag-ulw can we split the api, ui and docs work into parallel lanes?",
                        "/dag-ulw /ulw-plan migrate billing in parts", "/dag-ulw broke the build, fix it"):
            with self.subTest(message=message):
                action, skill, hints = self._routed(message)
                self.assertTrue(invokes_dag_ulw(message))
                self.assertEqual((action, skill), ("dispatch", "dag-ulw"))
                self.assertEqual(hints[0], ("dag-ulw", "start_dag_run"))


if __name__ == "__main__":
    unittest.main()
