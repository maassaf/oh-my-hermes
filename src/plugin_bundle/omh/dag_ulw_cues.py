"""When a message reaches `dag-ulw`: one reading for the router and the in-Hermes route hint.

By name. People type the name with a space, either way round ("dag ulw", "ulw dag"), and in Korean with the
verb glued to it ("dag ulw해줘"). A message invokes the skill when the name opens the request (after a sigil,
``use``, ``run``, ``please`` or a polite ``can you``), when the request runs something with it ("refactor auth
with dag ulw"), or when a Korean run ending follows it ("결제 모듈 dag ulw로 고쳐줘"). A negated mention never does
("don't use dag ulw", "dag ulw 말고"), and neither does a question about the skill ("dag ulw는 뭐야?",
"dag ulw 설명해줘", "dag ulw 결과 알려줘", "how does dag-ulw handle repairs?"): those are answered, the
way a question about any other skill is, and so is one after the sigil ("/dag-ulw 이게 뭐야?"). A word for the
run in progress ("dag ulw stop", "dag ulw 취소", "dag ulw 상태 보여줘", "dag ulw 뭐 남았어?") never does either: it
points at that run's `omh_dag` cancel or status. A Korean stop noun
after other words ("dag ulw 주문 취소"), or an English stop word beside them ("dag ulw cancel order"), is neither a run
nor a cancel until the person says which. A complaint or a question after the bare name is answered too ("dag ulw
broke my build", "dag ulw 결제 api 뭐야"): `name_report_cues` reads it, for every skill's name.

By size. A request that says how the work splits or how wide it reaches (`work_has_several_parts`), does not ask
to be told how, and does not name ultrawork is big work for `dag-ulw`; the router reads it only on a route it
already gave `ultrawork`, and the route hint on its coding-delivery hint. Where no hint rule matched, the hint
reads only an outright split (`work_is_split`).

Callers pass the executable text, so a quoted, fenced or relayed mention never reaches these readers.
Stdlib only: the plugin bundle loads this file with Hermes' interpreter (#1623).
"""

from __future__ import annotations

from functools import lru_cache
import re
import unicodedata
from typing import Final

from . import skill_shortlist as _skill_shortlist
from .name_report_cues import COINED_NAMES, asks_after_the_name, asks_for_something, reports_on_the_name

DAG_ULW: Final = "dag-ulw"

_NAME: Final = r"(?:dag[\s_-]*(?:ulw|ultrawork)|(?:ulw|ultrawork)[\s_-]*dag)(?![a-z0-9_-])"
# A request put politely as a question ("can you dag ulw the migration?") still asks to run it.
_POLITE_OPENER: Final = r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)"
_LEAD: Final = re.compile(
    rf"^(?:(?:please|pls)\s+)?{_POLITE_OPENER}?(?:(?:use|run|start)\s+(?:omh\s+)?)?(?:\$|\./|/|@)?{_NAME}"
)
_WITH: Final = re.compile(rf"\b(?:with|via|using|through)\s+(?:the\s+)?{_NAME}")
# The name with a run ending right after it ("dag ulw해줘"), or the name as an instrument ("dag ulw로") and,
# within four words, a request ending ("dag ulw로 결제 모듈 고쳐줘").
_KOREAN_RUN: Final = re.compile(
    rf"{_NAME}\s*(?:해\s*줘|해\s*주세요|해\b|돌려|실행|진행|부탁)"
    rf"|{_NAME}\s*(?:으로|로)\s+(?:\S+\s+){{0,3}}\S*(?:줘|주세요)"
)
_NEGATED: Final = re.compile(
    rf"(?:\b(?:don'?t|do not|never|not|without|instead of|other than)\s+(?:use\s+|run\s+)?(?:the\s+)?{_NAME})"
    rf"|(?:\b(?:stop|quit)\s+(?:using|running)\s+(?:the\s+)?{_NAME})"
    rf"|(?:{_NAME}\s*(?:은|는|을|를)?\s*(?:말고|쓰지|하지\s*마|대신|아니))"
)
# A question about the skill, not a request to run it: a topic or subject particle on the name ("dag ulw는",
# "dag ulw가"); a word right after the name that asks about it (what it is, how it works, what it costs, its
# docs or result); an English verb that describes it ("dag ulw is broken"); or an English question that opens
# before the name, put politely or not ("can you explain dag ulw?"). A question mark anywhere at the end is read
# by `asks_about_dag_ulw` itself.
_ASKED_ABOUT: Final = re.compile(
    rf"{_NAME}\s*(?:은|는|이란|란|이|가)(?![가-힣])"
    rf"|{_NAME}\s*(?:설명|뭐|뭔|무엇|무슨|알려|결과|문서|사용법|어떻게|어때|쓰면|비용|원리|왜)"
    rf"|{_NAME}\s+(?:is|are|was|were|does|should|has|seems?|vs\.?|versus)\b"
    rf"|^{_POLITE_OPENER}?(?:how|what|why|when|where|who|which|is|are|does|should|tell\s+me|explain|describe)\b"
    rf"(?:\s+\S+){{0,6}}?\s+(?:the\s+)?(?:\$|/)?{_NAME}"
)
_POLITE_REQUEST: Final = re.compile(rf"^(?:(?:please|pls)\s+)?{_POLITE_OPENER}")
# The name with nothing before it, as a report or a question opens (`name_report_cues`).
_BARE_LEAD: Final = re.compile(rf"^{_NAME}")
# A polite question that weighs the run against another way asks about it, not for it ("can you dag ulw this or
# should I just fix it myself?", "could you dag ulw this, or is that overkill?", "would you dag ulw it, if you were
# me?"). An `or` between two targets weighs nothing ("can you dag ulw the auth or billing refactor?").
_OR_ANOTHER_WAY: Final = (
    r"\bor\s+(?:should|shall|is|are|would|will|could|can|do|does|just|not|maybe|keep|leave|skip|ultrawork|ulw)\b"
)
_WEIGHED: Final = re.compile(rf"{_NAME}.*(?:{_OR_ANOTHER_WAY}|아니면)|\bif\s+you\s+were\b")
# A word for the run a person already has, said to the name: stop or cancel it ("dag ulw stop", "dag ulw 멈춰",
# "dag ulw 돌린 거 취소해줘", "cancel dag ulw", "can you stop dag ulw"), or show how it stands ("dag ulw status",
# "dag ulw 상태 보여줘", "dag ulw 진행 상황"). The words must end the request, or be all it asks about, so a thing
# named for them stays work ("dag ulw add a cancel button", "dag ulw 결제 취소 기능 구현해줘",
# "dag ulw 상태 관리 모듈 리팩터링해줘").
_RUN_OBJECT: Final = r"(?:\s+(?:it|this|that|now|please|everything|all|the\s+(?:run|dag|graph|job)))*"
# Korean says a stop two ways. 멈춰, 그만 and 꺼 are a stop whatever comes before them. 취소, 중지, 중단, 정지 and 종료
# are nouns too, and after other words they can name the thing to stop as easily as the run ("dag ulw 주문 취소" is an
# order cancel as much as a cancel of the run). They stop the run with a verb ending, plain or polite, after up to
# three words ("dag ulw 주문 취소해줘", "dag ulw 취소해줘요", "dag ulw 취소 부탁해"); or alone, after stop adverbs or
# after words for the run itself, with a verb ending or a bare 요, 좀 or ㄱ ("dag ulw 취소요", "dag ulw 지금 바로 취소",
# "dag ulw 진행 중인 작업 취소", "dag ulw 그것도 취소"). After other words a bare 요, 좀 or ㄱ is no verb, so
# "dag ulw 주문 취소요" stays the noun phrase. The folded text is NFKC, which turns the jamo ㄱ into its conjoining
# form, so the pattern carries that form.
_KOREAN_STOP_NOUN: Final = r"(?:취소|중지|중단|정지|종료|스톱|스탑|멈춤)"
_KOREAN_VERB_TAIL: Final = (
    r"\s*(?:좀\s*)?(?:해\s*(?:줘|주세요|줄래|주라|봐|라|버려|줄\s*수\s*있(?:어|나|을까)|주시겠어|주실래|주실\s*수\s*있(?:나|을까))?"
    r"|하자|하세요|할게|할래|합니다|하고\s*싶어"
    r"|해야\s*(?:해|돼|겠어)|시켜\s*(?:줘|주세요)?|둬\s*(?:줘|주세요)?|부탁(?:해|드려|드립니다|합니다)?|줘|주세요|줄래)\s*요?"
)
_KOREAN_PARTICLE_TAIL: Final = rf"\s*(?:요|좀|{unicodedata.normalize('NFKC', 'ㄱ')}+)"
_KOREAN_RUN_MODIFIER: Final = (
    r"(?:현재|지금|이번|이전|방금|아까|다른|모든|전체|전부|모두|그|이|저|진행\s*중인|실행\s*중인|돌리던|돌리는|돌던|돌아가는"
    r"|돌린)"
)
_KOREAN_RUN_NOUN: Final = (
    r"(?:작업|실행|런|run|job|그것도|이것도|그거도|이거도|그거|이거|저거|거|것|전부|모두|다|전체)"
)
_KOREAN_STOP_ADVERB: Final = r"(?:바로|당장|즉시|일단|그냥|얼른|빨리|지금)"
# A stop said as the act of stopping, after the bare stop word or words for the run ("dag ulw 작업 취소 처리해줘",
# "dag ulw 런 중단 요청 보내줘", "dag ulw 취소 눌러줘"): a stop of the run, not work on one.
_KOREAN_STOP_ACT: Final = (
    rf"\s*(?:좀\s*)?(?:(?:처리|진행|요청|실행)(?:{_KOREAN_VERB_TAIL})?|(?:요청|명령)\s*(?:보내|내려)\s*(?:줘|주세요)"
    r"|(?:눌러|걸어)\s*(?:줘|주세요))\s*요?"
)
# The same act after other words, alone after the stop word or with the button it is pressed on ("dag ulw 결제 취소
# 처리해줘", "dag ulw 결제 작업 중단 걸어줘", "dag ulw 취소 버튼 눌러줘"): it names no work of its own, so the words
# before the stop word decide, as for a bare stop noun (owner decision O7).
_KOREAN_STOP_ACT_ALONE: Final = re.compile(rf"^{_KOREAN_STOP_ACT}(?:{_KOREAN_PARTICLE_TAIL})?\s*[.!?~]*$")
# The button a stop is pressed on is the stop's own ("dag ulw 취소 버튼 눌러줘", "dag ulw 취소 버튼 눌렀는데 계속
# 돌아가"), not work, unless other work words follow ("… 버튼 눌러도 안 멈추는 버그 고쳐줘").
_KOREAN_STOP_BUTTON: Final = re.compile(r"^\s*버튼\s*(?:을|를)?(?=\s*눌)")
_STOP: Final = re.compile(
    rf"{_NAME}[\s,:]*(?:please\s+)?(?:stop|cancel|abort|halt|kill){_RUN_OBJECT}\s*[.!?]*$"
    rf"|^(?:please\s+)?{_POLITE_OPENER}?(?:stop|cancel|abort|halt|kill)\s+(?:the\s+|this\s+|that\s+|my\s+)?"
    rf"(?:current\s+|running\s+)?(?:\$|/)?{_NAME}(?:\s+(?:run|job|graph))?(?:\s+(?:now|please))*\s*[.!?]*$"
    rf"|{_NAME}[\s,:]*(?!으?로\s)(?:의\s*)?(?:\S+\s+){{0,3}}?(?:멈춰(?:라)?|멈추(?:세요|자)?|그만(?:\s*(?:돌려|돌리자))?)"
    rf"(?:{_KOREAN_VERB_TAIL}|{_KOREAN_PARTICLE_TAIL})?\s*[.!?~]*$"
    rf"|{_NAME}[\s,:]*(?!으?로\s)(?:의\s*)?(?:\S+\s+){{0,3}}?\S*?{_KOREAN_STOP_NOUN}{_KOREAN_VERB_TAIL}\s*[.!?~]*$"
    rf"|{_NAME}[\s,:]*(?:의\s*)?(?:\S+\s+말고\s+)?(?:(?:{_KOREAN_RUN_MODIFIER}\s*){{0,2}}{_KOREAN_RUN_NOUN}\s*)?"
    rf"(?:{_KOREAN_STOP_ADVERB}\s*){{0,2}}"
    rf"(?:{_KOREAN_STOP_NOUN}(?:{_KOREAN_VERB_TAIL}|{_KOREAN_PARTICLE_TAIL}|{_KOREAN_STOP_ACT}(?:{_KOREAN_PARTICLE_TAIL})?)?"
    rf"|꺼\s*(?:줘|주세요|라)?\s*요?)\s*[.!?~]*$"
)
# Any other Korean stop word after the name is never a run (owner decision O7). The last stop word decides. With
# nothing after it that names work, it may stop the run or name the work, so it asks which ("dag ulw 주문 취소",
# "dag ulw 취소 요청", "dag ulw 결제 취소 기능 만드는 거 그만해줘"). The message is work instead when:
# - a work noun or verb follows it ("dag ulw 주문 취소 기능 만들어요", "dag ulw 결제 취소 이슈 해결해줘"), or a clause
#   after it makes the stop a condition of the work ("dag ulw 주문 취소 시 환불되게 해줘");
# - past the stop word's own ending, a request verb with its own stem ends the message ("dag ulw 결제 취소 내역
#   확인해줘", "dag ulw 종료 훅 등록해줘"), where a stop request leaves nothing but its ending ("… 작업 취소해줘");
# - a condition clause comes right before it ("dag ulw 결제 모듈 리팩터링하고 실패하면 중단해줘");
# - a work word is the object of 꺼줘 ("dag ulw 다크모드 꺼줘"), unless a word for the run is ("dag ulw 결제 모듈
#   작업 꺼줘").
# A how word after it asks about stopping, and so does a past or passive form ("dag ulw 취소됐어?", "dag ulw
# 멈췄어?") or 여부: those are answered (`asks_about_dag_ulw`).
_STOP_AFTER_NAME: Final = re.compile(
    rf"{_NAME}(?P<before>.*)(?P<stem>{_KOREAN_STOP_NOUN}|멈추|멈춰|멈출|멈췄|멈춘|그만(?!큼)|꺼\s*(?:줘|주세요|라))(?P<after>.*)$",
    re.DOTALL,
)
_KOREAN_WORK_WORD: Final = re.compile(
    r"기능|로직|버튼|화면|페이지|모듈|컴포넌트|서비스|엔드포인트|핸들러|함수|메서드|클래스|코드|처리|구현|만들|추가|리팩터"
    r"|고쳐|고치|수정|정리|작성|개발|테스트|변경|개선|설계|연동|적용|배포|마이그레이션|흐름|플로우|정책|조건|이벤트|알림|넣"
    r"|붙여|바꿔|분리|나눠|해결|잡아|버그|이슈|디버깅|디버그|최적화|캐싱|캐시|검토|스펙|문서|써\s*(?:줘|주세요)|정의|쿼리"
    r"|스키마|설정|중단점|모드|로그|\b(?:api|ui|button|page|feature|logic|flow|handler|endpoint|dto|tests?)\b"
)
_KOREAN_WORK_CONDITION: Final = re.compile(r"^\s*시\s|[할될]\s*때|[하되]면(?!\s*(?:돼|된다|되잖아))|\S(?:되게|되도록|하도록)")
_KOREAN_CONDITION_BEFORE: Final = re.compile(r"(?:[하되]면|[할될]\s*때|다가|\s시)\s*$")
_KOREAN_RUN_OBJECT_BEFORE: Final = re.compile(r"(?:작업|거|것|run|job|그거|이거|저거)\s*$")
_KOREAN_STOP_ENDING: Final = re.compile(rf"(?:{_KOREAN_VERB_TAIL}|{_KOREAN_PARTICLE_TAIL})")
_KOREAN_REQUEST_VERB: Final = re.compile(r"(?P<stem>\S*?)(?:해)?(?:줘|주세요|줄래|주라)요?|(?P<stem2>\S*?)(?:해요|하자|해라|하세요)")
_KOREAN_NOT_A_VERB_STEM: Final = frozenset(
    {"", "해", "하", "좀", "부탁", "다시", "빨리", "지금", "그냥", "일단", "바로", "얼른", "당장", "꼭", "제발", "어서",
     "게", "되게", "하게", "도록", "되도록"}
)
_KOREAN_HOW: Final = re.compile(r"어떻게|방법|뭐|왜|가능|(?:^|[할될])\s*수\s*있|되나|될까|돼\s*요?\s*[?？]")
_KOREAN_PAST: Final = re.compile(r"^\s*(?:완료\s*)?(?:됐|되었|됨|된|했|하였|한\s*거|뒀|눌렀)|여부")
# A sigil before a question is a question, not a command ("/dag-ulw what is it?", "/dag-ulw how do I cancel it?"),
# unless a request for work follows it ("/dag-ulw explain the plan, then run the migration", "/dag-ulw is the auth
# refactor done? if not, finish it").
_SIGIL_QUESTION: Final = re.compile(
    r"^(?!.*(?:\b(?:then|and)\s+(?:run|do|start|finish|implement|fix|migrate|build)\b|\bif\s+not\b"
    r"|[,?!.]\s*(?:finish|run|do|fix|start|implement)\b))"
    rf"(?:\$|\./|/){_NAME}[\s,:]+(?:how|what|why|when|where|who|which|is|are|does|do\s+i|can\s+i|should|tell\s+me"
    r"|explain|describe)\b",
    re.DOTALL,
)
# A stop the person already made, said while the run still goes ("dag ulw 취소했는데 계속 돌아가", "dag ulw 취소
# 눌렀는데 계속 돌아가"): stop it again.
_KOREAN_PRESSED: Final = re.compile(r"\s*눌렀")
_REQUEST_BEFORE_STOP: Final = re.compile(r"(?:^|\s)(?:다시\s*)?(?!해|하|좀)\S+?(?:해)?(?:줘|주세요)(?=[\s,.]|$)|(?:다시|이어서|계속|마저)\s*해\s*(?:줘|주세요)")
_STATUS_REQUEST: Final = re.compile(r"(?:확인|체크|봐|보여|알려|살펴|점검|조회)\S*?(?:해)?(?:줘|주세요)")
_KOREAN_STILL_RUNNING: Final = re.compile(r"(?:계속|아직|여전히)\s*(?:돌아|돌고|실행|진행|안\s*멈)|안\s*(?:멈춰|멈추|꺼져|꺼지)")
# A stop of the run right after the name, then more work ("dag ulw 지금 거 취소하고 결제 모듈부터 다시 해줘",
# "dag ulw 취소하고 다시 처음부터 구현해줘"): stop and restart, or work on a cancel, so it asks before either.
_STOP_THEN_WORK: Final = re.compile(
    rf"{_NAME}[\s,:]*(?:의\s*)?(?:(?:{_KOREAN_RUN_MODIFIER}\s*){{0,2}}{_KOREAN_RUN_NOUN}\s*)?"
    rf"(?:{_KOREAN_STOP_ADVERB}\s*){{0,2}}(?:{_KOREAN_STOP_NOUN}|멈추|그만)\s*(?:하|시키)?고(?:서)?\s"
)
# An English stop word with other words after the name, none of them the run's own ("dag ulw cancel order", "dag ulw
# order cancel", "dag ulw cancel the migration"): like a Korean stop noun after other words, it may stop the run or
# name the work, so it asks which (owner decision O7). A work word makes it work ("dag ulw add a cancel button",
# "dag ulw cancel order flow", "dag ulw implement order cancel").
_EN_STOP_WORD: Final = r"(?:stop|cancel|abort|halt|kill)"
_EN_STOP_AFTER_NAME: Final = re.compile(
    rf"^{_NAME}[\s,:]+(?:(?:\S+\s+){{1,3}}{_EN_STOP_WORD}|{_EN_STOP_WORD}\s+\S+(?:\s+\S+){{0,3}})\s*[.!?]*$"
)
_EN_WORK_WORD: Final = re.compile(
    r"\b(?:add|implement|build|create|make|write|fix|refactor|support|design|migrate|handle|handling|test|tests"
    r"|button|feature|flow|endpoint|api|handler|logic|page|ui|function|method|module|service|bug|issue|dto|schema"
    r"|query|filter|words?|tokens?|signals?|conditions?|switch(?:es)?|flags?|toggles?)\b"
)
_STATUS: Final = re.compile(
    rf"{_NAME}[\s,:]*(?:status|progress|logs?)(?:\s+(?:please|now|of\s+the\s+(?:run|dag|graph)))*\s*[.!?]*$"
    rf"|{_NAME}[\s,:]*(?:의\s*)?(?:상태|진행\s*상황|진행\s*상태|진행도|로그)\s*(?:좀\s*)?"
    rf"(?:보여|알려|확인|어때|어떻게|는\s*[?？]|[?？]|$)"
    # What is left of the run is how it stands ("dag ulw 뭐 남았어?", "dag ulw what's left?").
    rf"|{_NAME}[\s,:]*(?:뭐|얼마나|몇\s*개)\s*(?:더\s*)?남았(?:어|나|니|어요|나요|습니까)?\s*[.!?？]*$"
    rf"|{_NAME}[\s,:]*(?:what'?s|what\s+is|how\s+much\s+is)\s+(?:still\s+)?left\s*[.!?]*$"
)


# Words said to a run, read on their own, after any skill's name: a stop with nothing but the run as its object, and
# a request to see how the run stands. English, Korean, Japanese and Chinese; Japanese and Chinese have a short closed
# list, since no trigger pack carries a stop or status word. A stop word with a task object is work ("kill the zombie
# processes", "로그 폭주 멈춰줘", "abort the rebase"). After an invoked skill the run may also be named by the skill's
# own words (`_run_stop_alone`).
def _run_stop_source(names: str = "", nouns: str = "") -> str:
    """`_RUN_STOP_ALONE`'s pattern, with ``names`` (the invoked skill's names, said bare: "stop ulw-loop") and
    ``nouns`` (the words for its own run, which a determiner may come before: "stop the loop", "stop debugging",
    "리뷰 취소") as alternations that may also be the stop's object."""
    objects = [r"it|this|that|now|please|everything|all|the\s+(?:run|dag|graph|job)"]
    korean_nouns = [_KOREAN_RUN_NOUN]
    own = "|".join(words for words in (names, nouns) if words)
    if names:
        objects.append(rf"(?:{names})(?:\s+(?:run|job|session))?")
        korean_nouns.append(names)
    if nouns:
        objects.append(rf"(?:(?:the|this|that|my|your|current|running)\s+)*(?:{nouns})(?:\s+(?:run|job|session))?")
        korean_nouns.append(nouns)
    run_object = rf"(?:\s+(?:{'|'.join(objects)}))*"
    run_noun = rf"(?:{'|'.join(korean_nouns)})"
    # The skill's own run in Japanese and Chinese, before or after the stop ("ループを止めて", "停止循环", "计划取消").
    ja_noun = rf"作業|実行|これ|それ{'|' + own if own else ''}"
    zh_after = rf"运行|执行|任务|吧{'|' + own if own else ''}"
    zh_before = rf"|^(?:{own})(?:停止|取消|中止)吧?[。.!！]*$" if own else ""
    korean_run_phrase = (
        rf"(?:(?:{_KOREAN_RUN_MODIFIER}\s*){{0,2}}{run_noun}\s*(?:을|를|은|는|도)?\s*)?(?:{_KOREAN_STOP_ADVERB}\s*){{0,2}}"
    )
    return (
        rf"^(?:please\s+)?(?:stop|cancel|abort|halt|kill){run_object}\s*[.!?]*$"
        rf"|^{korean_run_phrase}"
        rf"(?:{_KOREAN_STOP_NOUN}(?:{_KOREAN_VERB_TAIL}|{_KOREAN_PARTICLE_TAIL}|{_KOREAN_STOP_ACT}(?:{_KOREAN_PARTICLE_TAIL})?)?"
        rf"|(?:멈춰(?:라)?|멈추(?:세요|자)?|그만(?:\s*(?:돌려|돌리자))?)(?:{_KOREAN_VERB_TAIL}|{_KOREAN_PARTICLE_TAIL})?"
        rf"|꺼\s*(?:줘|주세요|라)?\s*요?)\s*[.!?~]*$"
        rf"|^(?:(?:{ja_noun})を?)?(?:停止|中止|キャンセル)(?:して)?(?:ください)?[。.!！]*$"
        rf"|^(?:(?:{ja_noun})を)?(?:止めて|やめて)(?:ください)?[。.!！]*$"
        rf"|^(?:请)?(?:停止|取消|停下|中止)(?:{zh_after})?[。.!！]*${zh_before}"
        r"|^(?:别做了|不要做了|算了|停一下)吧?[。.!！]*$"
    )


_RUN_STOP_ALONE: Final = re.compile(_run_stop_source())


def _alternation(words: tuple[str, ...]) -> str:
    return "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))


@lru_cache(maxsize=256)
def _run_stop_alone(names: tuple[str, ...], nouns: tuple[str, ...]) -> re.Pattern[str]:
    if not names and not nouns:
        return _RUN_STOP_ALONE
    return re.compile(_run_stop_source(_alternation(names), _alternation(nouns)))


_STOP_WORD: Final = re.compile(r"stop|cancel|abort|halt|kill|멈춰|그만|그만해|취소|중단|중지|정지")
# A Korean stop said as a verb, then why ("그만해 비싸", "멈춰 너무 오래 걸려"), as "stop, too expensive" says it with a
# comma. A bare stop noun is not a verb: after "취소" the words may be its object ("취소 버튼"). The why closes on a
# predicate: a noun after the stop is its object ("취소해 주문", "cancel the order"), and so is a why that names work or
# asks how or why (`stops_the_run`). The stop is a few words at most (`_KOREAN_STOP_WORDS_MAX`).
_KOREAN_STOP_VERB_END: Final = re.compile(
    rf"(?:(?:멈춰(?:라)?|멈추(?:세요|자))(?:{_KOREAN_VERB_TAIL})?|(?:그만|{_KOREAN_STOP_NOUN})(?:{_KOREAN_VERB_TAIL}))$"
)
_KOREAN_STOP_WORDS_MAX: Final = 6
# A why after a Korean stop verb is a reason when it closes on a predicate ending ("필요 없어", "내가 할게", "이거
# 아니야", "지금 회의 중"), reads as a report on the run (`reports_on_the_name`: "비싸", "너무 오래 걸려"), or is only
# fillers and stop words ("일단", "잠깐만", "멈춰 멈춰 제발"). A noun is the stop's object, so a loanword that closes
# on a vowel is too ("취소해 주문", "멈춰 서버").
_KOREAN_REASON_END: Final = re.compile(
    r"(?:[어아여해워야냐요다네게걸까래대데든군음함됨임듯]|아니라고|아님|중)$"
)
# A noun that closes like a reason: a loanword ("플레이어", "하드웨어", "리뷰어") or a lone counter or quantifier ("서버 두
# 대", "주문 다"), the stop's object.
_KOREAN_NOUN_LIKE_A_REASON: Final = re.compile(r"(?:^|\s)(?:다|대)$|(?:레이어|웨어|뷰어)$")


def _ends_in_a_past(word: str) -> bool:
    """Whether a Korean word ends in a past tense, a syllable closing on ㅆ then 어 or 아 ("보냈어", "됐어")."""
    if len(word) < 2:
        return False
    code = ord(word[-2]) - 0xAC00
    return word[-1] in "어아" and 0 <= code < 11172 and code % 28 == 20


_KOREAN_FILLER_REASON: Final = re.compile(
    r"^(?:(?:일단|잠깐만?|제발|좀|그냥|지금|빨리|당장|얼른|바로|멈춰|그만|그만해|취소|중단)\s*)+$"
)
# A word that only answers or interrupts before the stop ("no stop", "no, stop", "잠깐 멈춰").
_INTERJECTION: Final = re.compile(r"^(?:no|nope|wait|ok|okay|아니|아니야|잠깐|야)(?:\s*[,!.]+\s*|\s+)(?=\S)")
# What the skill is, in Japanese and Chinese, which `asks_after_the_name` does not read.
_JA_ZH_META: Final = re.compile(
    r"^(?:何これ|これ(?:は)?何(?:ですか)?|使い方(?:は)?|どうやって使う(?:の)?|(?:这|这个)是什么|是什么|怎么用)[?？。]*$"
)
_RUN_STATUS: Final = re.compile(
    r"^(?:(?:please\s+)?show\s+(?:me\s+)?(?:the\s+)?)?(?:status|progress|logs?)"
    r"(?:\s+(?:please|now|of\s+the\s+(?:run|dag|graph|job)))*\s*[.!?]*$"
    r"|^(?:please\s+)?show\s+(?:me\s+)?(?:the\s+)?(?:run|dag|graph|job)\s*[.!?]*$"
    r"|^(?:is\s+it|is\s+the\s+(?:run|job)|are\s+we)\s+(?:still\s+)?(?:running|done|finished|stuck)\s*\?*$"
    r"|^(?:still\s+)?(?:running|done|finished|stuck)\s*\?+$"
    r"|^(?:what'?s|what\s+is|how\s+much\s+is)\s+(?:still\s+)?left\s*[.!?]*$"
    r"|^how\s+(?:much|many)\s+(?:\S+\s+){0,2}(?:is|are)\s+(?:done|left|remaining|finished)\s*\?*$"
    r"|^(?:지금\s*)?(?:상태|진행\s*상황|진행\s*상태|진행도|로그)\s*(?:좀\s*)?"
    r"(?:(?:보여|알려|확인)\S*|어때\S*|어떻게\s*돼\S*|는\s*[?？])?\s*[.!?？]*$"
    r"|^(?:뭐|얼마나|몇\s*개)\s*(?:더\s*)?남았(?:어|나|니|어요|나요|습니까)?\s*[.!?？]*$"
    r"|^(?:다\s*)?(?:끝났|돌아가고\s*있|진행\s*중이)(?:어|나|니|어요|나요|습니까)?\s*[?？]\s*$"
    r"|^어디까지\s*(?:됐|했|왔)\S*\s*[?？]*$"
    r"|^(?:今の)?(?:状態|進捗|進行状況)(?:は|を)?(?:どう|教えて|見せて)?(?:ですか|ください)?[?？。]*$"
    r"|^(?:当前)?(?:状态|进度|进展)(?:怎么样|如何)?[?？。]*$"
)


def _folded(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def stops_the_run(rest: str, *, names: tuple[str, ...] = (), nouns: tuple[str, ...] = ()) -> bool:
    """Whether ``rest``, the words after a skill's name, stop its run and name no task: a stop word alone, said twice,
    or with only the run as its object ("stop", "멈춰 멈춰", "cancel the run", "이거 취소", "작업 중단해줘", "실행 중인
    거 멈춰줘", "停止", "止めて", "取消", "别做了"), then at most a clause that asks for nothing ("stop, too expensive",
    "그만해 비싸"). After an invoked skill the run may be named by the skill's ``names``, said bare ("stop ulw-loop"),
    or by the ``nouns`` for its own run ("stop the loop", "stop debugging", "리뷰 취소"). A past or a question about
    stopping is no stop ("취소됐어?")."""
    folded = _INTERJECTION.sub("", _folded(rest).strip(" ,:"), count=1)
    parts = re.split(r"\s*[,;.!~]+\s*", folded, maxsplit=1)
    first, after = parts[0], parts[1] if len(parts) > 1 else ""
    if not first or (after and asks_for_something(", " + after)):
        return False
    if all(_STOP_WORD.fullmatch(word) for word in first.split()):
        return True
    alone = _run_stop_alone(names, nouns)
    if alone.match(first):
        return True
    words = first.split()
    for cut in range(1, min(len(words), _KOREAN_STOP_WORDS_MAX + 1)):
        if not _KOREAN_STOP_VERB_END.search(" ".join(words[max(0, cut - 2) : cut])):  # "그만 해줘"
            continue
        stop, why = " ".join(words[:cut]), " ".join(words[cut:])
        if alone.match(stop):
            # Not a why that asks how or why, which asks about stopping ("취소해줘 왜 안 돼": `_asks_about_stopping`).
            reason = why.rstrip(".!?~ ")
            return bool(
                _KOREAN_FILLER_REASON.match(reason)
                or (_KOREAN_REASON_END.search(reason) and not _KOREAN_NOUN_LIKE_A_REASON.search(reason))
                or _ends_in_a_past(reason)
                or reports_on_the_name(reason)
            ) and not (
                asks_for_something(", " + why) or _KOREAN_WORK_WORD.search(why) or _KOREAN_HOW.search(why)
            )
    return False


def ja_zh_asks_what_it_is(rest: str) -> bool:
    """Whether ``rest`` asks, in Japanese or Chinese, what the skill is or how to use it ("何これ", "这是什么")."""
    return bool(_JA_ZH_META.match(_folded(rest).strip(" ,:")))


def asks_how_the_run_stands(rest: str) -> bool:
    """Whether ``rest``, the words after a skill's name, ask to see how its run stands ("status", "show status", "show
    me the run", "is it running?", "what's left?", "상태 보여줘", "뭐 남았어?", "끝났어?", "状態", "状态")."""
    return bool(_RUN_STATUS.match(_folded(rest).strip(" ,:")))


def dag_ulw_control(text: str) -> str:
    """``cancel`` or ``status`` when ``text`` says that word to dag-ulw about the run in progress,
    ``cancel_or_run`` when another Korean stop word follows the name with no work after it (`_STOP_AFTER_NAME`), an
    English stop word comes with other words and no work (`_EN_STOP_AFTER_NAME`), or a stop of the run is followed
    by more work (`_STOP_THEN_WORK`), else "". After the sigil the same words read the same ("/dag-ulw cancel order"
    asks, as "dag ulw cancel order" does)."""
    folded = _folded(text)
    if _SIGIL_LEAD.match(folded):
        folded = folded.lstrip("$./")
        name = _BARE_LEAD.match(folded)
        stacked = _STACKED_SIGILS.match(folded[name.end() :]) if name else None
        if name and stacked:  # "/dag-ulw /ulw-plan 상태 보여줘": the words after the stacked skills
            folded = folded[: name.end()] + folded[name.end() + stacked.end() :]
    if _STOP.search(folded):
        return "cancel"
    first = re.match(r"^(.*?)\s*[,;.!?~]+\s*(\S.*)$", folded)
    if first and _STOP.search(first.group(1)) and not asks_for_something(", " + first.group(2)):
        return "cancel"  # "dag ulw stop, too expensive": a stop, then why
    if _STATUS.search(folded):
        return "status"
    name = _BARE_LEAD.match(folded)
    if name and stops_the_run(folded[name.end() :]):
        return "cancel"  # "dag ulw 停止", "dag ulw 止めて"
    if name and asks_how_the_run_stands(folded[name.end() :]):
        return "status"  # "dag ulw show status", "dag ulw show me the run", "dag ulw 状态"
    if _STOP_THEN_WORK.search(folded):
        return "cancel_or_run"
    if _EN_STOP_AFTER_NAME.match(folded) and not _EN_WORK_WORD.search(folded):
        return "cancel_or_run"
    stop = _STOP_AFTER_NAME.search(folded)
    if not stop or _stop_names_work(stop):
        return ""
    after = _after_the_stop(stop)
    if (_KOREAN_PAST.search(after) or _KOREAN_PRESSED.match(after)) and _KOREAN_STILL_RUNNING.search(after):
        return "cancel"
    return "" if _asks_about_stopping(stop) else "cancel_or_run"


def _ends_in_its_own_request(after: str) -> bool:
    """Whether, past the stop word's own ending, ``after`` ends in a request verb with its own stem."""
    ending = _KOREAN_STOP_ENDING.match(after)
    words = after[ending.end() if ending else 0 :].strip(" .!?~").split()
    verb = _KOREAN_REQUEST_VERB.fullmatch(words[-1]) if words else None
    if not verb:
        return False
    stem = verb.group("stem") if verb.group("stem") is not None else verb.group("stem2")
    if stem in ("", "해", "하") and len(words) > 1:
        stem = words[-2]
    return stem not in _KOREAN_NOT_A_VERB_STEM


def _after_the_stop(stop: re.Match[str]) -> str:
    """The words after the last stop word, without the button it is pressed on (`_KOREAN_STOP_BUTTON`)."""
    return _KOREAN_STOP_BUTTON.sub("", stop.group("after"), count=1)


def _stop_names_work(stop: re.Match[str]) -> bool:
    """Whether the words around the last stop word (`_STOP_AFTER_NAME`) make the message work, not a stop."""
    before, stem = stop.group("before", "stem")
    after = _after_the_stop(stop)
    if stem.startswith("꺼"):
        return bool(_KOREAN_WORK_WORD.search(before)) and not _KOREAN_RUN_OBJECT_BEFORE.search(before)
    if _KOREAN_STOP_ACT_ALONE.match(after):
        return False  # "dag ulw 결제 취소 처리해줘": the act of stopping, after words that may name the work
    if _KOREAN_WORK_WORD.search(after):
        return True
    if _KOREAN_HOW.search(after):
        return False  # a question about stopping (`_asks_about_stopping`), even after …하면 ("중단하면 어떻게 돼")
    if _KOREAN_WORK_CONDITION.search(after) or _KOREAN_CONDITION_BEFORE.search(before):
        return True
    past = bool(_KOREAN_PAST.search(after)) or stem in ("멈췄", "멈춘")
    if past and _REQUEST_BEFORE_STOP.search(before) and not _STATUS_REQUEST.search(before):
        return True  # a request of its own, then the stop it names: "dag ulw 다시 돌려줘, 아까 취소한 거"
    if past and _STATUS_REQUEST.search(after):
        return False  # a status check of a past stop: "dag ulw 멈춘 거 확인해줘", "dag ulw 취소 여부 확인해줘"
    return _ends_in_its_own_request(after)


def _asks_about_stopping(stop: re.Match[str] | None) -> bool:
    """Whether the last stop word asks how to stop, or whether it stopped, rather than stopping or naming work."""
    if stop is None or _stop_names_work(stop):
        return False
    after = _after_the_stop(stop)
    return bool(_KOREAN_HOW.search(after) or _KOREAN_PAST.search(after) or stop.group("stem") in ("멈췄", "멈춘"))


def reports_or_asks_after_the_name(text: str) -> bool:
    """Whether ``text`` opens with the bare name and goes on to report on dag-ulw or ask a question
    (`name_report_cues`), rather than to say a stop or status word for its run (`dag_ulw_control`)."""
    folded = _folded(text)
    bare = _BARE_LEAD.match(folded)
    if not bare or dag_ulw_control(folded):
        return False
    rest = folded[bare.end() :]
    return (not re.search(_NAME, rest) and reports_on_the_name(rest)) or asks_after_the_name(rest, about_anything=True)


def reports_on_dag_ulw(text: str) -> bool:
    """Whether ``text`` opens with the bare name and goes on to report on dag-ulw, asking nothing ("dag-ulw is
    broken"): after another skill's sigil that is work for the skill, not a question about dag-ulw."""
    folded = _folded(text)
    bare = _BARE_LEAD.match(folded)
    if not bare:
        return False
    rest = folded[bare.end() :]
    return reports_on_the_name(rest) and not asks_after_the_name(rest, about_anything=True)


def asks_about_dag_ulw(text: str) -> bool:
    """Whether ``text`` names `dag-ulw` to ask about it rather than to run it."""
    folded = _folded(text)
    if not re.search(_NAME, folded):
        return False
    if _ASKED_ABOUT.search(folded) or _SIGIL_QUESTION.match(folded) or _sigil_asks(folded):
        return True
    if _asks_about_stopping(_STOP_AFTER_NAME.search(folded)):
        return True  # "dag ulw 취소 어떻게 해", "dag ulw 멈출 수 있어", "dag ulw 취소됐어?"
    if reports_or_asks_after_the_name(folded):
        return True  # "dag ulw broke my build", "dag ulw 또 실패했어", "dag ulw 결제 api 뭐야", "dag ulw run finished, now what"
    if _POLITE_REQUEST.match(folded):
        return bool(_WEIGHED.search(folded))
    return folded.endswith(("?", "？"))


_SIGIL_LEAD: Final = re.compile(rf"^(?:\$|\./|/){_NAME}")
# Further skills stacked after the sigil ("/dag-ulw /ulw-plan what is it?"): Hermes loads each, and the words after
# the last one are the instruction.
_STACKED_SIGILS: Final = re.compile(r"(?:\s+(?:\$|\./|/)[a-z0-9][a-z0-9_-]*)+(?=\s|$)")
# A Korean question that asks for the work to be done for the person, with 주다 ("구현해줄 수 있어?", "나눠서 해주실래요?",
# "고쳐주시겠어요?"): a polite request, as "can you dag ulw the migration?" is.
_KOREAN_ASKS_TO_BE_DONE: Final = re.compile(r"(?:줄|주실)\s*(?:수\s*있|래|거)|주시겠|줄래")
# Work proposed as a question is a request for it ("can we split the api, ui and docs work into parallel lanes?").
_PROPOSED: Final = re.compile(r"^(?:can|could|shall)\s+we\b")


def _sigil_asks(folded: str) -> bool:
    """Whether the instruction after a sigil, and after any skills stacked on it, is a question rather than a command.

    A question word opens it in English (`_SIGIL_QUESTION`), or it asks a question and asks for nothing
    (`asks_after_the_name`: "/dag-ulw 이게 뭐야?", "/dag-ulw 어떻게 써?", "/dag-ulw we committed a secret, what now"), or
    it weighs the run against another way (`_WEIGHED`). In any language it is a question when it ends
    in a question mark and asks for nothing, as after the bare name ("/dag-ulw CI 통과했어?", "/dag-ulw did the sweep
    finish?", "/dag-ulw Kubernetes是什么？"). A request that holds a question word stays one ("/dag-ulw 왜 깨졌는지
    찾아서 고쳐줘"), and so does one that opens on a request verb, is asked politely or is proposed ("/dag-ulw migrate
    the billing service?", "/dag-ulw 나눠서 구현해줄 수 있어?", "/dag-ulw can you dag ulw the migration?", "/dag-ulw can
    we split the work into parallel lanes?").
    """
    lead = _SIGIL_LEAD.match(folded)
    if not lead:
        return False
    if _WEIGHED.search(folded):
        return True  # it weighs the run against another way, as a polite question does ("/dag-ulw … or should I …?")
    rest = folded[lead.end() :]
    stacked = _STACKED_SIGILS.match(rest)
    if stacked:
        rest = rest[stacked.end() :]
        if _SIGIL_QUESTION.match(f"/{DAG_ULW} {rest.strip()}"):
            return True
    rest = rest.strip(" ,:")
    if asks_after_the_name(rest, about_anything=True) or _ASKED_ABOUT.search(rest) or ja_zh_asks_what_it_is(rest):
        return True  # _ASKED_ABOUT: the name again, asked about ("/dag-ulw can you explain dag ulw?")
    # ", " makes the opening clause one `asks_for_something` reads too: "/dag-ulw migrate the billing service?" opens
    # on a request verb, so it is a request with a question mark.
    return rest.endswith(("?", "？")) and not (
        asks_for_something(f", {rest}") or _KOREAN_ASKS_TO_BE_DONE.search(rest) or _PROPOSED.match(rest)
    )


def asks_after_the_sigil(text: str) -> bool:
    """Whether ``text`` opens with the `dag-ulw` sigil and asks a question after it, and after any skills stacked on
    it ("/dag-ulw what is it?", "/dag-ulw 이게 뭐야?", "/dag-ulw /ulw-plan what is it?"): the stacked skills are not
    invoked either."""
    folded = _folded(text)
    return bool(_SIGIL_LEAD.match(folded)) and bool(_SIGIL_QUESTION.match(folded) or _sigil_asks(folded))


def invokes_dag_ulw(text: str) -> bool:
    """Whether ``text`` (the executable part of a message) asks to run `dag-ulw`.

    A sigil (``/dag-ulw``, ``$dag-ulw``) is a command whatever follows it, as it is for every skill, except a word
    for the run in progress (`dag_ulw_control`), a question after it, in English or Korean and after any skills
    stacked on it (`_sigil_asks`), or a question about stopping it: stopping, checking or asking never starts a run.
    Hermes sends every `/dag-ulw` turn this way, so it is the form that runs.
    """
    folded = _folded(text)
    if not folded or _NEGATED.search(folded) or dag_ulw_control(folded):
        return False
    if _SIGIL_LEAD.match(folded):
        return not (
            _SIGIL_QUESTION.match(folded) or _sigil_asks(folded) or _asks_about_stopping(_STOP_AFTER_NAME.search(folded))
        )
    if asks_about_dag_ulw(folded):
        return False
    return bool(_LEAD.match(folded) or _WITH.search(folded) or _KOREAN_RUN.search(folded))


def declines_dag_ulw(text: str) -> bool:
    """Whether ``text`` names `dag-ulw` only to decline it ("don't use dag ulw", "dag ulw 말고")."""
    return bool(_NEGATED.search(_folded(text)))


# ── big work by size ─────────────────────────────────────────────────────

# How the WORK is split (lanes, a fan-out, parts integrated afterwards) or how wide it reaches (every module, the
# whole codebase). A bare concurrency word is not one: "in parallel", "concurrently", "병렬로" and "동시에" name
# the bug or the feature as often as the work's shape ("fix the bug where uploads run in parallel",
# "동시에 두 요청이 오면 캐시가 깨지는 버그 고쳐줘"), so they count only beside several named parts
# (`_PART_WORDS`). Each of these left the list after an unrelated sentence using it moved: "then integrate" alone,
# "parallel lanes" alone, "parallel work" and "parallel implementation" as nouns ("the parallel work queue drops
# jobs, fix it"), "fan out" as a noun or of something other than the work ("the fan out of this function is too
# high", "fan out the notifications to all subscribers"), and a Korean concurrency word with a work verb
# ("병렬로 구현", "동시에 진행"); the probes are kept with the measurement.
SHAPE_CUES: Final = (
    "into parallel lanes",
    "into parallel work",
    "into parallel parts",
    "into parallel tasks",
    "in parallel lanes",
    "parallel then integrate",
    "fan out across",
    "병렬로 나눠",
    "나눠서 병렬로",
)
BREADTH_CUES: Final = (
    "across the codebase",
    "across every module",
    "across all modules",
    "across modules",
    "every module",
    "all modules",
    "whole codebase",
    "entire codebase",
    "여러 모듈",
    "모든 모듈",
    "전체 모듈",
    "코드베이스 전체",
)
CONCURRENCY_CUES: Final = ("in parallel", "concurrently", "simultaneously", "at the same time", "병렬로", "동시에")
# Several named parts: an English plural part noun, or a Korean part noun said at least twice
# ("api 모듈과 ui 모듈"), since Korean marks no plural.
_PART_WORDS: Final = frozenset(
    {"modules", "services", "packages", "components", "apps", "repos", "repositories", "microservices", "libraries"}
)
_KOREAN_PART_WORDS: Final = ("모듈", "서비스", "패키지", "컴포넌트")
_ULTRAWORK_STEMS: Final = ("ultrawork", "ulw", "울트라워크")
# A shape cue counts only where the work is asked for. Not in the subject of a remark ("the fan out across the
# services is too high"), and not where the split's object is one paragraph or sentence, a small text task ("split
# the paragraph into parallel parts for the translation", "이 문단을 병렬로 나눠서 번역해줘"); a paragraph named
# elsewhere ("the paragraph parser", "fix the paragraph in the README") changes nothing.
_REMARK_SUBJECT: Final = re.compile(
    r"^(?:the|a|an|this|that|these|those|our|its|their|my|your)\s+([^.?!,;]*?)\s(?:is|are|was|were|seems|looks)\b"
)
_SMALL_TEXT_OBJECT: Final = re.compile(
    r"\bsplit\s+(?:the|this|that|a|one|my|your)\s+(?:paragraph|sentence)\s+into\b|(?:이|그|저|한)\s*(?:문단|문장|단락)(?:을|를)\s"
)
# A fault the parts cause together, with one fix asked for: the concurrency word says when the bug happens, not how
# the work splits ("api 모듈과 ui 모듈이 동시에 요청하면 캐시가 깨져, 고쳐줘").
_FAULT: Final = re.compile(
    r"\b(?:breaks|broke|fails|failed|crash(?:es|ed)|hangs|hung|deadlocks|deadlocked|corrupts|corrupted"
    r"|(?:is|are|gets?|got)\s+(?:broken|corrupted|stuck)|times?\s+out|timed\s+out)\b"
    r"|깨져|깨지|깨졌|터져|터지|터졌|죽어|죽었|실패해|실패하|실패했|꼬여|꼬이|꼬였|(?:에러|오류)가?\s*(?:나|났|떠|뜨)"
    r"|충돌이?\s*(?:나|났)|데드락|교착"
)
_FIX_REQUEST: Final = re.compile(r"\b(?:fix|resolve|debug|repair)\b|고쳐|수정해|해결해")
# Unless the concurrency word says how the work or the fixes run ("테스트가 실패하는데 병렬로 고쳐줘", "병렬로 각각
# 고쳐줘", "동시에 진행해줘, 실패하는 테스트도 고쳐줘", "fix them in parallel"): a request, a verb with a request
# ending, follows it at most two words on. In the fault it is a condition ("동시에 요청하면 캐시가 깨져", "동시에
# 처리해서 캐시가 깨져").
_PARALLEL_WORK: Final = re.compile(
    r"(?:병렬로|동시에)\s*(?:(?:각각|따로|모두|전부|다|같이|함께|한꺼번에|\S+해서)\s*){0,2}"
    r"(?:고쳐|수정해|해결해|진행해|작업해|처리해|구현해)\s*(?:줘|주세요|줄래|봐)"
    r"|\bfix\s+(?:them|both|all|each|these|those)(?:\s+of\s+them)?\s+"
    r"(?:in\s+parallel|concurrently|simultaneously|at\s+the\s+same\s+time)\b"
)
# A question or an explain request about how work splits asks to be told, not for the work ("explain how to split
# the work into parallel lanes", "show me how to split ...", "is it possible to split ...", "how do I split the work
# into parallel lanes?", "병렬로 나누는 방법 설명해줘", "병렬로 나눠서 하는 법 알려줘"). Not an opener that proposes the
# work ("how about we split ...", "why don't we split ...", "what we need is to split ...", "tell me when done:
# split ..."), not a Korean explain clause after the work it explains ("... 구현하고 나눈 방법 설명해줘"), and not a
# request put as a question ("can we split ...?", "let's split ...?", "..., ok?", "... 구현해줄 수 있어?").
_ASKS_TO_BE_TOLD: Final = re.compile(
    rf"^{_POLITE_OPENER}?(?:how(?!\s+about)|what(?!\s+(?:we|i)\s+(?:need|want)(?:\s+is\s+to\b|\s*:))|why\s+not\s+just\s+explain"
    r"|why(?!\s+(?:don'?t|not)\b)|explain|describe|tell\s+me(?!\s+when)|show\s+me\s+how|help\s+me\s+understand"
    r"|i(?:\s+(?:want|need)|['’]d\s+like|\s+would\s+like)\s+to\s+know|is\s+it\s+possible)\b"
)
# Talking it over is not asking for it either ("can we talk about how to split ...", "can we discuss whether to
# split ..."), nor is a question that weighs the split against another way ("could we split ..., or is it overkill?").
_TALKS_IT_OVER: Final = re.compile(r"\b(?:talk|think)\s+(?:about|through)\b|\bdiscuss\b|\bwhether\s+(?:to|we|i)\b")
_WEIGHS_THE_SPLIT: Final = re.compile(_OR_ANOTHER_WAY)
_KOREAN_ASKS_TO_BE_TOLD: Final = re.compile(
    r"(?:방법|는\s*법)\s*(?:을|를|이|가)?\s*(?:좀\s*)?(?:설명해|알려|뭐)|지\s*(?:를\s*)?(?:좀\s*)?(?:설명해|알려)"
)
_KOREAN_WORK_FIRST: Final = re.compile(
    r"(?:구현|만들|고치|고쳐|수정|작성|리팩터링|추가|개발|빌드|배포|진행|테스트|마이그레이션|적용|처리)\S*(?:고|서)\s"
)
_REQUEST_AS_QUESTION: Final = re.compile(
    rf"^(?:(?:please|pls)\s+)?(?:{_POLITE_OPENER}|(?:can|could|shall)\s+we\b|let'?s\b)"
    r"|,\s*(?:ok|okay|alright|right)\s*[?？]$|(?:줄\s*수\s*있|주실\s*수\s*있|줄래|주시겠|주겠)\S*\s*[?？]$"
    r"|면\s*어때요?\s*[?？]$"
)


def _cue_text(text: str) -> str:
    """Lower-cased and accent-folded, the way the router's `contains_cue_phrase` reads a message."""
    folded = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(character for character in folded if not unicodedata.combining(character))


def _compact(text: str) -> str:
    return "".join(character for character in text if character.isalnum())


def _contains(text: str, cues: tuple[str, ...]) -> bool:
    """`contains_cue_phrase`'s match: the cue in the text, or with every space and mark taken out of both."""
    compact = _compact(text)
    return any(_cue_text(cue) in text or _compact(_cue_text(cue)) in compact for cue in cues)


def names_ultrawork(text: str) -> bool:
    """Whether a word of ``text`` carries ultrawork's name or alias (`ulw`, `ulw-work`, `ultrawork로`)."""
    words = re.findall(r"[^\W_]+", _folded(text))
    return any(stem in word for word in words for stem in _ULTRAWORK_STEMS)


def _asks_for_a_split(text: str) -> bool:
    """Whether a `SHAPE_CUES` phrase in ``text`` asks for the work to split (`_REMARK_SUBJECT`, `_SMALL_TEXT_OBJECT`).

    The regular expressions read the composed text: `_cue_text` takes Hangul apart into its letters."""
    if not _contains(_cue_text(text), SHAPE_CUES):
        return False
    folded = _folded(text)
    subject = _REMARK_SUBJECT.match(folded)
    if subject and _contains(_cue_text(subject.group(1)), SHAPE_CUES):
        return False
    return not _SMALL_TEXT_OBJECT.search(folded)


def _asks_to_be_told(text: str) -> bool:
    folded = _folded(text)
    if _ASKS_TO_BE_TOLD.search(folded) or _TALKS_IT_OVER.search(folded):
        return True
    if _KOREAN_ASKS_TO_BE_TOLD.search(folded) and not _KOREAN_WORK_FIRST.search(folded):
        return True
    if not folded.endswith(("?", "？")):
        return False
    return bool(_WEIGHS_THE_SPLIT.search(folded)) or not _REQUEST_AS_QUESTION.search(folded)


# Work said over many targets, which splits into parallel parts (owner decision: only multi-part work moves). Two
# readings, with the same hard excludes (`_hard_excluded`):
# - `dispatches_dag_ulw`, positive evidence the router dispatches and the route hint starts on: a change verb from a
#   closed allow list, in request form, on a breadth object that names independent units ("열린 PR들 전부
#   리베이스해줘", "fix all the failing tests", "update every service to node 22", two named parts in parallel);
# - `work_over_many_targets`, a breadth word on such units with a request that may be work but is not that evidence
#   ("review all open PRs"): the router may only ask once with dag-ulw as the candidate, and the hint offers it.
_KOREAN_UNIT: Final = r"(?:pr|prs|pull\s*request|레포|리포|저장소|서비스|모듈|패키지|컴포넌트|엔드포인트|테스트|이슈|파일|페이지|브랜치)"
# A count of units is breadth only for PRs, repos and branches, and only with a breadth word after it ("PR 3개 전부",
# "PR 5개 다", "레포 3개 전부"; lead decision on the #2232 review). A count of anything else, or one with no breadth
# word, is a small single task ("파일 2개 수정해줘", "테스트 3개 고쳐줘", "PR 3개 리베이스해줘"). A count is never one
# PR's number either (`_ONE_SCOPE`).
_KOREAN_COUNTED_UNIT: Final = r"(?:pr|pull\s*request|풀\s*리퀘(?:스트)?|레포|리포|repo|브랜치|branch)"
_KOREAN_COUNT: Final = r"(?:두|세|네|다섯|여러|[2-9]|\d{2,})\s*(?:개|건)"
_KOREAN_BREADTH_OBJECT: Final = (
    rf"(?:(?<![가-힣])(?:마이크로)?{_KOREAN_UNIT}(?:들)?\s*(?:을|를|은|는)?\s*(?:전부|모두|다|싹|몽땅|하나하나|각각)(?![가-힣])"
    rf"|(?<![가-힣a-z]){_KOREAN_COUNTED_UNIT}(?:들)?\s*{_KOREAN_COUNT}\s*(?:을|를|은|는)?\s*(?:전부|모두|다|몽땅)(?![가-힣])"
    rf"|(?<![가-힣])(?:모든|전체|각)\s*(?:마이크로)?{_KOREAN_UNIT}(?:들)?(?:을|를|에|의|에서)?)"
)
_EN_UNITS: Final = (
    r"(?:prs?|pull\s+requests?|repos?|repositor(?:y|ies)|(?:micro)?services?|modules?|packages?|components?|endpoints?"
    r"|tests?|issues?|files?|pages?|apps?|branch(?:es)?)"
)
# A fault word that only describes the units ("실패하는 테스트", "the failing tests") names which units, not one fault.
_FAULTY_UNIT: Final = re.compile(
    r"(?:실패하는|실패한|실패했(?:었)?던|깨진|깨지는|깨졌(?:었)?던|터진|터졌(?:었)?던|충돌난|충돌났(?:었)?던|failing|failed|broken"
    rf"|flaky)\s+(?:{_KOREAN_UNIT}|{_EN_UNITS})"
)
# One fault said in Korean as something that does not work, in any words, with one fix asked: 안 or 못 before a
# predicate ("안 돌아가", "안 먹어", "못 해"), …지 않 or …지 못 ("돌지 않아"), a fault noun ("먹통", "고장"), or a symptom
# clause, a subject with 이 or 가 and its predicate right before the fix ("페이지가 하얗게 나와 고쳐줘", "아이콘이 깨져 보여
# 고쳐줘", "페이지가 느려 고쳐줘").
_KOREAN_NEGATED_PREDICATE: Final = re.compile(
    r"(?<![가-힣])(?:안|못)\s*(?!(?:의|에|에서|으로|을|이)(?![가-힣]))(?P<word>[가-힣]+)|지\s*(?P<negation>(?:않|못)[가-힣]*)"
)
_KOREAN_FAULT_NOUN: Final = re.compile(r"먹통|고장")
_KOREAN_SYMPTOM_BEFORE_FIX: Final = re.compile(
    r"[가-힣](?:이|가)\s+(?:\S+\s+){0,2}?(?P<word>[가-힣]+)\s*[,.~]?\s+(?:고쳐|고치|수정|해결|fix\b)"
)
_FIX_VERB: Final = re.compile(r"\b(?:fix|resolve|debug|repair)\b|고쳐|고치|수정|해결")


def _closes_a_clause(word: str) -> bool:
    """Whether a Korean predicate ``word`` closes its clause, as a symptom does ("안 돌아가", "느려", "나와", "안됨"), rather
    than aiming the change (…게, …도록: "안 되게 고쳐줘", "쓰지 않게"), going on to more (…고), or describing a noun (…는,
    …던, …은, a final ㄴ: "안 돌아가는 테스트")."""
    code = ord(word[-1]) - 0xAC00
    return not (word.endswith(("게", "도록", "고", "는", "던", "은")) or (0 <= code < 11172 and code % 28 == 4))


def _one_fault(folded: str) -> bool:
    """Whether ``folded`` asks to fix one fault however wide it reaches (`_FAULT`, or a Korean "does not work"), rather
    than for work on each unit; a fault word that only describes the units names which units (`_FAULTY_UNIT`), and
    a concurrency word that says how the fixes run is no fault (`_PARALLEL_WORK`)."""
    plain = _FAULTY_UNIT.sub(" ", folded)
    if not _FIX_VERB.search(plain) or _PARALLEL_WORK.search(plain):
        return False
    if _FAULT.search(plain) or _KOREAN_FAULT_NOUN.search(plain):
        return True
    if any(
        _closes_a_clause(match.group("word") or match.group("negation"))
        for match in _KOREAN_NEGATED_PREDICATE.finditer(plain)
    ):
        return True
    return any(
        match.group("word") not in ("해", "돼") and _closes_a_clause(match.group("word"))
        for match in _KOREAN_SYMPTOM_BEFORE_FIX.finditer(plain)
    )
_EN_PARTS_IN_PARALLEL: Final = re.compile(
    r"^(?:please\s+)?(?:implement|build|develop|ship|write|add|create)\s+(?:the\s+)?\S+(?:,\s*\S+)*\s+(?:and|&)\s+\S+\s+"
    r"(?:features|endpoints|pages|screens|apis|modules|services|components)\s+(?:in\s+parallel|concurrently)\b"
)
_G: Final = unicodedata.normalize("NFKC", "ㄱ")
# The owner writes some English change verbs inside a Korean request ("rebase까지 ㄱㄱ", "upgrade해줘", "close하고"): one is
# a change where a Korean request ending, 까지 or 하고 follows it.
_KOREAN_ENDING_AFTER_EN: Final = rf"(?=\s*(?:까지|하고|해|부탁|진행|{_G}|고고|$))"
_EN_VERB_IN_KOREAN: Final = (
    rf"(?<![a-z])(?:rebase|merge|bump|fix|upgrade|update|close){_KOREAN_ENDING_AFTER_EN}"
)
# A push is one command over everything ("모든 브랜치 push 해줘", as "모든 브랜치 푸시해줘"); said last, after a change, it is
# that change's final step ("리뷰 코멘트 반영하고 force push ㄱㄱ").
_PUSH_IN_KOREAN: Final = re.compile(rf"(?<![a-z])(?:force\s*)?push{_KOREAN_ENDING_AFTER_EN}")
# The change verbs the router dispatches a graph on: a closed allow list. Reading, explaining, summarising,
# measuring, comparing, surveying, sharing, notifying, checking, reviewing and auditing are not on it, and neither is
# one command over everything (run, restart, push, deploy, format, delete, merge into one).
_KOREAN_CHANGE_VERB: Final = (
    r"(?:고쳐|고치|수정|업데이트|업그레이드|올려|올리|교체|추가|옮겨|옮기|바꿔|바꾸|리베이스|반영|닫|적용|붙여|붙이|통일|만들"
    rf"|마이그레이션|변경|리팩터링|리팩토링|머지|이전|해결|넣|최신화|{_EN_VERB_IN_KOREAN})"
)
_EN_CHANGE_VERB: Final = (
    r"(?:fix|update|upgrade|migrate|refactor|port|convert|add|rename|rebase|close|merge|bump|address|resolve|implement"
    r"|build|replace|move|apply|patch)"
)
# The verbs a request may be offered dag-ulw on, without starting it: the change verbs, and reviewing or triaging
# each unit ("review all open PRs", "PR들 전부 리뷰해줘").
_KOREAN_OFFER_VERB: Final = rf"(?:{_KOREAN_CHANGE_VERB}|리뷰|검토|트리아지|라벨)"
_EN_OFFER_VERB: Final = rf"(?:{_EN_CHANGE_VERB}|review|triage|label)"
_EN_BREADTH: Final = (
    rf"(?:\b(?:all|every|each)\s+(?:of\s+)?(?:the\s+|my\s+|our\s+)?(?:\S+\s+){{0,2}}?{_EN_UNITS}\b"
    rf"|\bacross\s+(?:all\s+|every\s+)?(?:the\s+)?{_EN_UNITS}\b)"
)
# An English request over many targets: an imperative whose verb is on the offer list, or a read first and a change
# after it ("check all open pull requests and close the stale ones").
# The units said first, then what to do with each ("for each of my PRs: rebase onto main", "for every open PR, rebase
# it").
_EN_FOR_EACH: Final = (
    rf"for\s+(?:each|every|all)\s+(?:of\s+)?(?:the\s+|my\s+|our\s+)?(?:\S+\s+){{0,2}}?{_EN_UNITS}\b\s*[:,]?\s*(?:please\s+)?"
)
_EN_OFFER: Final = re.compile(
    rf"^(?:please\s+)?{_POLITE_OPENER}?(?:{_EN_OFFER_VERB}\b[^.?!]*?{_EN_BREADTH}"
    rf"|(?:check|go\s+through|look\s+at)\b[^.?!]*?{_EN_BREADTH}[^.?!]*?\band\s+(?:then\s+)?{_EN_CHANGE_VERB}\b"
    rf"|{_EN_FOR_EACH}{_EN_OFFER_VERB}\b)"
)
_EN_LATER_CLAUSE_VERB: Final = re.compile(r"\band\s+(?:then\s+)?(?:just\s+)?([a-z]+)")
_KOREAN_OFFER: Final = re.compile(rf"{_KOREAN_BREADTH_OBJECT}\s*(?:\S+\s+){{0,3}}\S*{_KOREAN_OFFER_VERB}")
# An explicit request: a Korean request ending on the verb, the message's last word ("리베이스해줘", "리베이스해줘요",
# "리베이스해", "올려놔", "올리자", "리베이스해주셈"), or said as a word of its own right after the verb ("리베이스 해줘",
# "업데이트 좀 해 주세요", "업데이트 해 놔줘", "업데이트 시켜줘", "업데이트 부탁", "고쳐 줘", "리베이스 ㄱ", "리베이스 고"); or a
# chain of two or more change verbs joined by 고 ("닫을건 닫고 리뷰반영할건 리뷰반영하고 rebase할건 리베이스하고").
_KOREAN_GIVE: Final = r"(?:줘요|줘|주세요|주세용|주셈|줄래)"
_KOREAN_REQUEST_END: Final = re.compile(
    rf"(?:해\s*{_KOREAN_GIVE}|하자|{_KOREAN_GIVE}|해라|해|놔|둬|자|{_G}+|고고)\s*[.!~]*$"
)
_KOREAN_BARE_REQUEST_END: Final = re.compile(
    r"\s(?:(?:좀|꼭|다시|빨리|얼른|바로|제발)\s+)?"
    rf"(?:(?:진행\s*)?해\s*(?:{_KOREAN_GIVE}|(?:놔|둬)\s*{_KOREAN_GIVE}?)?|{_KOREAN_GIVE}|시켜\s*{_KOREAN_GIVE}?|하자"
    rf"|부탁(?:해요|해|드려요|드립니다|합니다|요)?|(?:해\s*)?주시기\s*바랍니다|바랍니다|(?:놔|둬)\s*{_KOREAN_GIVE}?|{_G}+|고고|고)"
    r"\s*[.!~]*$"
)
# The verb a bare ending is said after closes its word, or carries only its own ending ("고쳐", "만들어", "업데이트해",
# "rebase까지"): not a clause before it ("리베이스하려고 해", "리베이스해야 해").
_VERB_BEFORE_A_BARE_END: Final = r"(?:아|어|여|해|하)?(?:까지|도|만)?$"
_KOREAN_CHAIN_END: Final = re.compile(r"고\s*[.!~]*$")


def _korean_request_verb(folded: str) -> tuple[str, bool]:
    """The word that holds a Korean request's verb, and whether a request ending said on its own follows it (the
    word before it) rather than closing it (the last word); "" when the message makes no request."""
    bare = _KOREAN_BARE_REQUEST_END.search(folded)
    if bare:
        words = folded[: bare.start()].split()
        return (words[-1] if words else ""), True
    return (folded.rstrip(" .!~").split()[-1] if _KOREAN_REQUEST_END.search(folded) else ""), False


def _asks_for(folded: str, verbs: str) -> bool:
    """Whether a Korean ``folded`` asks, in request form, for one of ``verbs``: on the request's own verb, a push after
    a change, or a chain of two or more changes closed by 고 or ㄱ."""
    verb, bare = _korean_request_verb(folded)
    if re.search(rf"(?:{verbs}){_VERB_BEFORE_A_BARE_END}" if bare else verbs, verb):
        return True  # the request is the change ("… 리베이스해줘", "… 리베이스 해줘", "… rebase까지 ㄱㄱ")
    if _PUSH_IN_KOREAN.match(verb) and re.search(_KOREAN_CHANGE_VERB, folded):
        return True  # "… 리뷰 코멘트 반영하고 force push ㄱㄱ"
    closed = _KOREAN_CHAIN_END.search(folded) or folded.rstrip(" .!~").endswith(_G)
    return bool(closed) and len(re.findall(_KOREAN_CHANGE_VERB, folded)) >= 2


# The change verb is on the breadth object: right after it in Korean (a few words of its own between), or as an
# English imperative whose object is the breadth phrase ("fix all the failing tests", "rebase all my open pull
# requests"), a bare object on every unit ("add auth to every endpoint"), or a plural object in all of them ("update
# the dependencies in all services"). A singular object in many places is one change ("fix the login bug on every
# page", "update the license header in every file").
_KOREAN_CHANGE_ON_BREADTH: Final = re.compile(rf"{_KOREAN_BREADTH_OBJECT}\s*(?:\S+\s+){{0,3}}\S*{_KOREAN_CHANGE_VERB}")
_EN_CHANGE_ON_BREADTH: Final = re.compile(
    rf"^(?:please\s+)?{_POLITE_OPENER}?{_EN_CHANGE_VERB}\s+"
    rf"(?:(?:all|every|each)\s+(?:of\s+)?(?:the\s+|my\s+|our\s+)?(?:\S+\s+){{0,2}}?{_EN_UNITS}\b"
    rf"|(?!(?:the|a|an|this|that)\b)\S+(?:\s+\S+)?\s+(?:to|in|on|across|for)\s+(?:all|every|each)\s+(?:of\s+)?(?:the\s+)?(?:\S+\s+){{0,2}}?{_EN_UNITS}\b"
    rf"|(?:the|all\s+(?:of\s+)?the|all)\s+(?:\S+\s+)?\S+s\s+(?:in|across|for|of)\s+(?:all|every|each)\s+(?:of\s+)?(?:the\s+)?"
    rf"(?:\S+\s+){{0,2}}?{_EN_UNITS}\b)"
    # A read first, then a change on the same targets by pronoun ("go through all my open PRs and rebase them").
    rf"|^(?:please\s+)?{_POLITE_OPENER}?(?:go\s+through|check|look\s+at)\b[^.?!]*?{_EN_BREADTH}[^.?!]*?"
    rf"\band\s+(?:then\s+)?{_EN_CHANGE_VERB}\s+(?:them|each(?:\s+one)?|all\s+of\s+them)\b"
    rf"|^(?:please\s+)?{_EN_FOR_EACH}{_EN_CHANGE_VERB}\b"
)


# Hard excludes, which win over every reading above (lead decision on the #2232 review):
# - a stop or a hold on the work ("PR들 전부 머지 보류해줘", "모든 서비스 배포 멈춰줘", "PR들 전부 머지 금지");
_STOP_OR_HOLD: Final = re.compile(
    r"중단|취소|멈춰|멈추|멈춤|보류|금지|그만|\b(?:stop|cancel|hold|halt|pause|abort|freeze)\b"
)
# - a condition ("테스트 다 통과하면 머지해줘", "PR 다 올렸으면 리뷰 요청해줘", "if all the tests pass, merge"); a noun that ends in
#   면 (화면, 측면, 전면) is no condition;
_CONDITION: Final = re.compile(
    r"(?<![화측전양단표방내외평])면(?=[\s,.!?~]|$)|거든|\b(?:if|when|once|after|unless|whenever|before)\b"
)
# - a completion said as a noun or a bare word ("브랜치들 다 정리됨", "upstream PR들 다 리뷰 반영 끝", "테스트 수정 완료"),
#   not one that names the units ("완료된 이슈들 전부 닫아줘") or asks for it ("… 리베이스 완료해줘");
_COMPLETED: Final = re.compile(
    r"됨(?=[\s,.!?~]|$)|(?<![가-힣])끝(?=[\s,.!?~]|$)|완료(?=[\s,.!?~]|$|됨|됐|되었|했|함|임|입니다|이에요|예요|요)"
)
# - a recurring schedule, which stays on ulw-loop, Hermes' /loop or an automation ("매일 아침 PR들 전부 리베이스해줘", "30분마다
#   …", "PR 올릴 때마다 …", "rebase all my open PRs every hour", "update every service nightly");
# The shapes a schedule takes, not a word list:
# - a recurring period: any noun with 마다 but a unit of work (아침마다, 반나절마다, 스프린트마다, 배포마다, 커밋마다,
#   때마다, while "서비스마다" and "PR마다" mean each of them: `_says_a_schedule`), 매일, 매주, 매 + a noun (매 분기, 매
#   스프린트, 매 릴리즈), every <time>, each <time>;
# - a frequency over a period (2시간에 한 번씩, 일주일에 한 번, 하루 두 번, 주 2회, 2회씩, twice a day, 3 times a week), an
#   interval (3일 간격, 하루 걸러, every other day), an alternating period (격일, 격주, 격월);
# - a clock time (9시에, 자정, 정오, 새벽, at 9am), and a scheduler named for it (cron, 크론, 스케줄, schedule로, 스케줄
#   걸어서, 예약해서);
# - an event that repeats the work (on every push, after each deploy, on every PR), as 배포마다 and 커밋마다 do.
# A one-off request in the now stays work ("지금 바로", "이번에 한 번만", "now").
_KOREAN_NUMBER: Final = r"(?:\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|몇)"
_TIME_UNIT_KO: Final = r"(?:초|분|시간|일|주일|주|달|개월|년|해)"
_TIME_UNIT_EN: Final = (
    r"(?:second|minute|min|hour|day|week|month|year|morning|afternoon|evening|night|weekday|weekend|monday|tuesday"
    r"|wednesday|thursday|friday|saturday|sunday)"
)
_SCHEDULE: Final = re.compile(
    r"(?<![가-힣])매(?:일|주|달|월|년|시간|분)(?![가-힣])|(?<![가-힣])매일(?=아침|밤|저녁|오전|오후)|(?<![가-힣])매\s+(?=\S)"
    r"|주기적|정기적|\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?\s*(?:에|마다|정각)|(?<![가-힣])(?:자정|정오|새벽)(?:에|마다)?(?![가-힣])"
    rf"|(?:(?<![가-힣]){_KOREAN_NUMBER}|일)\s*{_TIME_UNIT_KO}\s*에\s*{_KOREAN_NUMBER}\s*번|하루\s*(?:에\s*)?{_KOREAN_NUMBER}\s*번"
    rf"|{_KOREAN_NUMBER}\s*{_TIME_UNIT_KO}\s*간격|(?:하루|이틀|사흘|나흘|한\s*주|일주일|\d+\s*일)\s*걸러"
    r"|(?<![가-힣])(?:주|월|연|하루)\s*\d+\s*회|\d+\s*회\s*씩|(?<![가-힣])격(?:일|주|월)(?:로|으로|에|마다|씩)?(?![가-힣])"
    r"|(?<![a-z])cron(?![a-z])|크론|스케줄(?!러)|schedule(?=\s*(?:으로|로|에|을|를|걸))|예약(?:해서|으로|을\s*걸)"
    rf"|\bevery\s+(?:\d+\s+|other\s+)?{_TIME_UNIT_EN}s?\b|\beach\s+{_TIME_UNIT_EN}\b"
    r"|\b(?:once|twice|thrice|(?:\d+|two|three|four|five|several)\s+times)\s+(?:a|per|each|every)\s+(?:hour|day|week|month|year)\b"
    r"|\b(?:on|after|upon)\s+(?:every|each)\s+(?:new\s+)?(?:push|commit|merge|deploy|deployment|release|build|pr|pull\s+request)\b"
    r"|\b(?:hourly|daily|nightly|weekly|monthly|periodically)\b|\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b"
    r"|\bon\s+a\s+schedule\b"
)
# What comes before 마다: a unit of work makes it "each of them" (`_KOREAN_UNIT`), anything else a recurrence.
_BEFORE_MADA: Final = re.compile(r"(?P<noun>[^\s,.!?~]+?)(?:들)?\s*마다")
_WORK_UNIT_BEFORE_MADA: Final = re.compile(rf"(?:마이크로)?(?:{_KOREAN_UNIT}|풀\s*리퀘(?:스트)?|repos?)")


def _says_a_schedule(folded: str) -> bool:
    """Whether ``folded`` says a recurring schedule (`_SCHEDULE`), or a noun other than a unit of work with 마다."""
    return bool(_SCHEDULE.search(folded)) or any(
        not _WORK_UNIT_BEFORE_MADA.fullmatch(match.group("noun")) for match in _BEFORE_MADA.finditer(folded)
    )
# A pull request's address, written bare or as a link the caller passes (`reference_regions(...).links`: the
# executable text has none).
_PULL_REQUEST_ADDRESS: Final = re.compile(r"/pulls?/\d+|/merge_requests/\d+")
# - one numbered pull request, or the person's own one ("PR 2232", "#2232", "2232번 PR", "내 PR", "pull request 2232",
#   "pull 951", "files touched by PR 2232", "this PR", "이 PR"); 이 or 그 must open its word, so 버그 and 블로그 are no
#   scope, and a number with a counter is a count of PRs, not one ("PR 3개", "PR 5건": `_KOREAN_COUNT`). One branch is
#   one scope the same way: a named one ("feature/dag-default", "fix/login-bug") and the current one ("current
#   branch", "this branch", "이 브랜치");
_ONE_SCOPE: Final = re.compile(
    r"\bpr\s*#?\s*\d+(?!\d|\s*(?:개|건))|#\s*\d+|\d+\s*번\s*(?:pr|이슈)|(?<![가-힣])내\s*pr(?!\s*들)"
    r"|\bpull\s+request\s+#?\d+(?!\d|\s*(?:개|건))|\bpull\s*#?\s*\d+(?!\d|\s*(?:개|건))"
    r"|(?<![\w/.-])(?:feature|feat|fix|bugfix|hotfix|release|chore|docs|refactor|omh|dev|wip)/[\w.-]+"
    r"|\b(?:current|my\s+current)\s+branch\b"
    # A pull request's address or its number said in words ("github.com/…/pull/2240", "PR number 2240", "2240번
    # 풀리퀘", "풀 리퀘스트 2240").
    rf"|{_PULL_REQUEST_ADDRESS.pattern}|\b(?:pr|pull\s+request)\s+(?:number|no\.?|num\.?)\s*#?\s*\d+|\bpr\s*번호\s*\d+"
    r"|\d+\s*번?\s*풀\s*리퀘(?:스트)?|풀\s*리퀘(?:스트)?\s*#?\s*\d+(?!\d|\s*(?:개|건))"
    r"|\b(?:this|that)\s+(?:pr|pull\s+request|branch|commit|diff)\b|(?<![가-힣])(?:이|그)\s*(?:pr|브랜치|커밋)(?![a-z])"
    r"|\b(?:in|of|from|on)\s+(?:the|my)\s+(?:pr|pull\s+request|branch|commit|diff)\b|\btouched\s+by\b"
    # One PR or branch picked out by an adjective or a possessive, or by 하나, never a plural ("my latest pull request",
    # "the last PR", "PR 하나").
    r"|(?<![a-z가-힣])(?:pr|pull\s*request|풀\s*리퀘(?:스트)?|브랜치)\s*(?:하나|한\s*개|1\s*개)(?![가-힣])"
    r"|\b(?:my|the|your|our)\s+(?:latest|last|newest|most\s+recent|current)\s+(?:pr|pull\s+request)\b"
)
# - one PR or one branch singled out by a modifier, with the breadth on what is inside it ("어제 내가 연 PR 테스트 전부",
#   "방금 올린 PR 파일들 전부", "feature/x 브랜치 테스트 전부", "현재 브랜치 테스트 전부"): no 들 and no breadth word on the PR
#   or the branch itself, and no 모든, 전체, 각 or 여러 before it ("PR들 전부", "모든 PR 테스트", "열린 PR 전부" stay many).
_ONE_CONTAINER: Final = re.compile(
    r"(?:(?P<before>\S+)\s+)?(?<![a-z가-힣])(?:pr|pull\s*request|풀\s*리퀘(?:스트)?|브랜치)(?:의|에서|에\s*있는|안의|안에\s*있는)?"
    r"\s+(?:리뷰\s+)?(?:테스트|파일|라인|줄|커밋|코드|변경|스냅샷|함수|코멘트|충돌|에러|오류|경고|린트|타입)"
)
_MANY_BEFORE_A_CONTAINER: Final = frozenset({"모든", "전체", "각", "여러", "all", "every", "each"})


def _names_one_container(folded: str) -> bool:
    return any(match.group("before") not in _MANY_BEFORE_A_CONTAINER for match in _ONE_CONTAINER.finditer(folded))


# A past: a syllable closing on ㅆ, other than 있 and 겠 ("리베이스했습니다", "머지했어요", "머지됐네", "닫혔네"). A past
# in a modifier names which units, and reports nothing ("내가 올렸던 PR들", "어제 실패했던 테스트", "충돌났었던 브랜치들").
_NOT_A_PAST: Final = frozenset("있겠")


def _closes_on_ss(character: str) -> bool:
    code = ord(character) - 0xAC00
    return 0 <= code < 11172 and code % 28 == 20


def _says_a_past(folded: str) -> bool:
    return any(
        _closes_on_ss(character)
        and character not in _NOT_A_PAST
        and folded[index + 1 :].lstrip("었았였")[:1] != "던"
        for index, character in enumerate(folded)
    )


# Words that ask for no work, or only a report: a negation, no need, a lookup or a status, a question ("PR들 전부
# 머지하지 마", "PR들 전부 알려줘", "모든 서비스 상태 확인해줘", "check the status of all open PRs", "review all open PRs and
# just tell me which are safe"), and one thing in many places ("fix the bug in all three services").
_NO_WORK_ASKED: Final = re.compile(
    r"했다|됐다|했음|됐음|했잖아|했는데|했지만|했더니|지\s*마|지\s*말|안\s*해|필요\s*없|도\s*돼|도\s*된다|도\s*괜찮"
    r"|알려\s*줘|알려\s*주세요|보여\s*줘|찾아|링크\s*(?:줘|주세요|보내)|읽어|상태|몇\s*개|목록|리스트|어때|뭐야|[?？]"
    r"|pr\s*들?\s*(?:을|를)?\s*(?:전부|모두|다)\s*정리"
    r"|\b(?:tell\s+me|let\s+me\s+know|report|summari[sz]e|show\s+me|status|which\s+(?:ones|are|is)|do\s+not|don'?t"
    r"|without|but\s+not|never|only)\b|\band\s+list\b|\blist\s+(?:them|the|which|all)\b"
    r"|\b(?:the|this|that|a)\s+(?:\S+\s+){0,2}\S+(?<!s)\s+(?:in|on)\s+(?:all|every|each)\b"
)
# A change, then a report on what it did ("fix all the failing tests and tell me which ones were flaky"): the report
# is the change's last clause, not a request of its own. Only after a change on the breadth object
# (`_EN_CHANGE_ON_BREADTH`): a review or an audit that ends in a report asks only for the report ("review all open PRs
# and just tell me which are safe").
_EN_REPORT_TAIL: Final = re.compile(
    r"(?:,\s*|\s+)and\s+(?:then\s+)?(?:tell\s+me|let\s+me\s+know|report|send\s+me|show\s+me)\b[^.?!]*[.!]?$"
)
# Parallel running is the work's subject, not its shape ("fix the race where uploads run in parallel across modules").
_RUNTIME_PARALLEL: Final = re.compile(
    r"\b(?:run|runs|running|execute|executes|load|loads)\s+in\s+parallel\b|\brace(?:\s+condition)?s?\b|\bdeadlock"
)
# Work until a condition holds stays on the loop and ultrawork lanes ("… until every test passes", "다 통과할 때까지").
_UNTIL: Final = re.compile(r"\buntil\b|\btill\b|때까지")
# A retired engine's name, which opens a request for that engine as a skill's name does ("team 실패하는 테스트 다 고쳐"),
# and a name said in Korean ("루프로 모든 서비스 업데이트해줘").
_RETIRED_ENGINE_NAMES: Final = frozenset({"team", "maestro", "ralph", "ultrapilot", "ultraprocess", "ultragoal"})
_KOREAN_NAMES: Final = frozenset({"루프"})
# A Korean particle on the name keeps it the name ("loop로", "refactor-plan으로", "github-event-ops에서"), and so does
# OMH's own name before it ("omh loop …", "use omh github-event-ops …").
_NAME_PARTICLE: Final = re.compile(r"(?:으로|로|을|를|에서|에게|한테|이랑|랑|와|과)$")
_OMH_OPENER: Final = re.compile(r"^(?:use\s+)?(?:omh|oh-my-hermes(?:-agent)?)\s+")
# A skill named as the instrument anywhere names it for the work too, as dag-ulw's own name does (`_WITH`,
# `_KOREAN_RUN`): "열린 PR들 전부 git-workflow로 리베이스해줘", "rebase all my open PRs with git-workflow".
_KOREAN_INSTRUMENT: Final = re.compile(r"(?P<name>\S+?)(?:으로|로)(?:\s|$)")
_EN_INSTRUMENT: Final = re.compile(r"\b(?:with|using|via|through)\s+(?:the\s+)?(?P<name>[a-z0-9][a-z0-9_-]*)")


# The catalog's names, kept once the sidecar answered. A missing sidecar is not kept: the next call reads it again.
_CATALOG_SKILL_NAMES: dict[str, frozenset[str]] = {}


def _skill_names() -> frozenset[str]:
    names = _CATALOG_SKILL_NAMES.get("names")
    if names is None:
        catalog = _skill_shortlist.catalog_skill_names()
        if not catalog:
            return COINED_NAMES | _RETIRED_ENGINE_NAMES | _KOREAN_NAMES
        names = _CATALOG_SKILL_NAMES.setdefault("names", catalog | COINED_NAMES | _RETIRED_ENGINE_NAMES | _KOREAN_NAMES)
    return names


def _opens_with_a_skill_name(folded: str) -> bool:
    """Whether ``folded`` opens with a skill's name, its label or a retired engine's, with or without a Korean
    particle: a request named for that skill."""
    words = re.findall(r"[^\s,:]+", _OMH_OPENER.sub("", folded, count=1))[:3]
    names = _skill_names()
    return any(
        joined in names or _NAME_PARTICLE.sub("", joined) in names
        for length in (1, 2, 3)
        for joined in ("-".join(words[:length]), " ".join(words[:length]))
    )


def _names_a_skill_as_the_instrument(folded: str) -> bool:
    """Whether ``folded`` names a skill, the loop or a retired engine as what the work is done with, anywhere."""
    names = _skill_names()
    return any(
        match.group("name").strip(",:") in names
        for pattern in (_KOREAN_INSTRUMENT, _EN_INSTRUMENT)
        for match in pattern.finditer(folded)
    )


def _hard_excluded(folded: str, links: tuple[str, ...]) -> bool:
    """Whether ``folded`` is no request for work over many targets whatever else it says: a sigil (the invoked skill's
    work), a skill or engine named first or as the instrument, a stop or a hold, a condition, a past or a completion,
    one pull request's or one branch's scope (in its words or its ``links``), a recurring schedule, work until a
    condition, or parallel running as its subject."""
    return bool(
        folded.startswith(("/", "$", "@", "./"))
        or any(_PULL_REQUEST_ADDRESS.search(link.casefold()) for link in links)
        or _opens_with_a_skill_name(folded)
        or _names_a_skill_as_the_instrument(folded)
        or _STOP_OR_HOLD.search(folded)
        or _CONDITION.search(folded)
        or _COMPLETED.search(folded)
        or _says_a_past(folded)
        or _ONE_SCOPE.search(folded)
        or _names_one_container(folded)
        or _says_a_schedule(folded)
        or _UNTIL.search(folded)
        or _RUNTIME_PARALLEL.search(folded)
        or names_ultrawork(folded)
    )


def _work_over_many_targets(text: str, links: tuple[str, ...]) -> bool:
    """Whether ``text`` asks, in request form, for an offer-list verb on a breadth object that names independent units
    (`_KOREAN_OFFER`, `_EN_OFFER`), or for two named parts in parallel, and nothing excludes it."""
    folded = _folded(text)
    if _hard_excluded(folded, links):
        return False
    if _EN_CHANGE_ON_BREADTH.search(folded):
        folded = _EN_REPORT_TAIL.sub("", folded)
    if _NO_WORK_ASKED.search(folded):
        return False
    if _EN_PARTS_IN_PARALLEL.search(folded):
        return True
    if _EN_OFFER.search(folded):
        # Every clause after the first is a change or a review too, not a report ("… and send me a list").
        return all(re.fullmatch(_EN_OFFER_VERB, verb) for verb in _EN_LATER_CLAUSE_VERB.findall(folded))
    # The request is the change or the review, not a summary.
    return bool(_KOREAN_OFFER.search(folded)) and _asks_for(folded, _KOREAN_OFFER_VERB)


def dispatches_dag_ulw(text: str, links: tuple[str, ...] = ()) -> bool:
    """Whether ``text`` gives the positive evidence the router dispatches a dag-ulw graph on, and the route hint starts
    one on: a change verb from the closed allow list (`_KOREAN_CHANGE_VERB`, `_EN_CHANGE_VERB`), on a breadth object
    that names independent units, in request form ("열린 PR들 전부 리베이스해줘", "fix all the failing tests", the owner's
    "pr들 전부 확인해서 닫을건 닫고 … 리베이스하고"), or two named parts asked for in parallel. Nothing hard-excluded
    (`_hard_excluded`) qualifies, and neither does a request that only reads, reports or runs one command. ``links``
    are the message's links, which the executable text leaves out."""
    folded = _folded(text)
    if not _work_over_many_targets(text, links) or _asks_to_be_told(text):
        return False
    if _one_fault(folded):
        return False  # one fault to fix, however wide ("모든 테스트가 깨졌어, 고쳐줘", "모든 테스트가 안 돌아가 고쳐줘")
    if _EN_CHANGE_ON_BREADTH.search(folded) or _EN_PARTS_IN_PARALLEL.search(folded):
        return True
    # A chain of changes joined by 고 counts too, closed by 고 or ㄱㄱ ("닫을건 닫고 … 리베이스하고 ㄱㄱ").
    return bool(_KOREAN_CHANGE_ON_BREADTH.search(folded)) and _asks_for(folded, _KOREAN_CHANGE_VERB)


def work_has_several_parts(text: str) -> bool:
    """Whether ``text`` says its work splits into parts or reaches across many (`SHAPE_CUES`, `BREADTH_CUES`), or
    runs at once across several named parts (a `CONCURRENCY_CUES` phrase beside `_PART_WORDS`) for a reason other
    than a fault it asks to fix (`_FAULT`)."""
    folded = _cue_text(text)
    if _asks_for_a_split(text) or _contains(folded, BREADTH_CUES):
        return True
    if not _contains(folded, CONCURRENCY_CUES):
        return False
    plain = _folded(text)
    if _FAULT.search(plain) and _FIX_REQUEST.search(plain) and not _PARALLEL_WORK.search(plain):
        return False
    words = set(re.findall(r"[^\W_]+", folded))
    korean_parts = sum(folded.count(_cue_text(word)) for word in _KOREAN_PART_WORDS)
    return not words.isdisjoint(_PART_WORDS) or korean_parts >= 2


def work_is_split(text: str) -> bool:
    """Whether ``text`` asks outright for its work to split (`SHAPE_CUES`), does not ask to be told how
    (`_ASKS_TO_BE_TOLD`), and does not name ultrawork.

    The route hint reads this where no rule of its own matched: breadth ("all modules") or a concurrency word
    alone is ordinary in a question about code, while a split ("fan out", "split into parallel") asks for work.
    """
    return not names_ultrawork(text) and not _asks_to_be_told(text) and _asks_for_a_split(text)


def work_over_many_targets(text: str, links: tuple[str, ...] = ()) -> bool:
    """Whether ``text`` asks for work over many targets without the evidence `dispatches_dag_ulw` needs ("review all
    open PRs", "PR들 전부 확인해서 정리해줘"): nothing hard-excluded, no question, and not one fault with one fix. The
    router may ask once with dag-ulw as the candidate, and the route hint offers it; neither starts a run."""
    if not _work_over_many_targets(text, links) or _asks_to_be_told(text):
        return False
    return not _one_fault(_folded(text))


def big_work_for_dag_ulw(text: str) -> bool:
    """Whether ``text`` asks for big work by size, rather than to be told about it, and does not name ultrawork,
    which keeps a request named for it."""
    return not names_ultrawork(text) and not _asks_to_be_told(text) and work_has_several_parts(text)
