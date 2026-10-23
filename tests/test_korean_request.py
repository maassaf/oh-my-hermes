"""The Korean request reader the two safety gates share (`korean_request`)."""

from __future__ import annotations

import unicodedata
import unittest

from _local_package import load_local_package

load_local_package()

from omh.plugin_bundle.omh.korean_request import korean_request


class KoreanRequestTests(unittest.TestCase):
    def test_the_predicate_is_the_verb_a_request_ending_closes(self) -> None:
        for message, predicate in (
            ("내 카드로 결제해줘", "결제"),
            ("결제해 주세요", "결제"),
            ("결제 해 줘", "결제"),
            ("결제 좀 해줘", "결제"),
            ("결제를 해줘", "결제"),
            ("결제까지 진행해 주세요", "결제"),
            ("결제 부탁드립니다", "결제"),
            ("비식별 처리해줘", "비식별"),
            ("이 책 사 주세요", "사"),
            ("엄마한테 돈 보내줘", "보내"),
            ("결제해줘~ ㅎㅎ", "결제"),
            ("내 카드로 결제해 줘라", "결제"),
            # The everyday sense of a spend word ends on another verb.
            ("결제 시스템 설계해줘", "설계"),
            ("결제 버그 고쳐줘", "고쳐"),
        ):
            with self.subTest(message=message):
                request = korean_request(message)
                self.assertIsNotNone(request)
                assert request is not None
                self.assertEqual(request.predicate, predicate)

    def test_a_sentence_that_ends_on_no_request_is_none(self) -> None:
        for message in (
            "결제했어",
            "결제하지 마",
            "송금해 줬어",
            "결제 안 해줘도 돼",
            "결제해줘 버튼이 안 눌려",
            "결제해줘?",
            "결제가 왜 실패했어?",
        ):
            with self.subTest(message=message):
                self.assertIsNone(korean_request(message))

    def test_an_asking_ending_is_a_request_with_its_question_mark(self) -> None:
        for message in ("결제해 줄 수 있어?", "결제해줄래?", "결제해줄수있어?", "결제해 주시겠어요?"):
            with self.subTest(message=message):
                request = korean_request(message)
                self.assertIsNotNone(request)
                assert request is not None
                self.assertEqual(request.predicate, "결제")

    def test_only_the_first_sentence_past_the_openers_is_read(self) -> None:
        self.assertIsNone(korean_request("결제 페이지가 깨졌어. 내 카드로 결제해줘"))
        request = korean_request("내 카드로 결제해줘. 영수증은 메일로")
        assert request is not None
        self.assertEqual((request.predicate, request.clause), ("결제", "내 카드로 결제해줘"))
        # A decimal point ends no sentence.
        request = korean_request("이더 0.1개 송금해줘")
        assert request is not None
        self.assertEqual(request.predicate, "송금")
        # A greeting sentence, or an address, before the request is skipped.
        for message, clause in (
            ("안녕하세요. 제 카드로 결제해 주세요.", "제 카드로 결제해 주세요"),
            ("헤르메스, 내 카드로 결제해줘", "내 카드로 결제해줘"),
            ("헤르메스. 내 카드로 결제해줘", "내 카드로 결제해줘"),
            ("헤르메스야 개인정보 지우고 외부에 공유해줘", "개인정보 지우고 외부에 공유해줘"),
        ):
            with self.subTest(message=message):
                request = korean_request(message)
                assert request is not None
                self.assertEqual(request.clause, clause)

    def test_labelled_and_quoted_text_is_not_the_request(self) -> None:
        for message in (
            "버튼 라벨: 내 카드로 결제해줘",
            "사용자 발화 예시: 고객 정보 마스킹해서 벤더한테 보내줘",
            '테스트 케이스 이름 "카드로 결제해줘"',
        ):
            with self.subTest(message=message):
                self.assertIsNone(korean_request(message))
        request = korean_request('이 문장 영어로 번역해줘 "내 카드로 결제해줘"')
        assert request is not None
        self.assertEqual(request.predicate, "번역")
        # A clock time is not a label.
        request = korean_request("10:30에 결제해줘")
        assert request is not None
        self.assertEqual(request.predicate, "결제")

    def test_a_single_quote_holding_hangul_is_quoted_and_an_apostrophe_is_not(self) -> None:
        for message, clause in (
            # A Korean particle may follow the closing mark.
            ("'고객 정보'를 마스킹해서 벤더한테 보내줘", "를 마스킹해서 벤더한테 보내줘"),
            ("이 문장 번역해줘 '내 카드로 결제해줘'", "이 문장 번역해줘"),
            # An English apostrophe opens and closes nothing, on either fold.
            ("고객 정보 마스킹해서 Acme's team에 보내줘. It's urgent.", "고객 정보 마스킹해서 acme s team에 보내줘"),
            ("고객 정보 마스킹해서 Acme's team에 보내줘", "고객 정보 마스킹해서 acme s team에 보내줘"),
            # A mark before a Latin letter closes none: no quote here at all.
            ("'고객 정보 마스킹해서 벤더에 보내줘. It's urgent", "고객 정보 마스킹해서 벤더에 보내줘"),
            # Nor one after a digit ("2024's"): the quote is the one around 급함.
            ("고객 정보 마스킹해서 2024's 리포트 팀에 보내줘. '급함'", "고객 정보 마스킹해서 2024 s 리포트 팀에 보내줘"),
        ):
            for fold in ("NFC", "NFD"):
                with self.subTest(message=message, fold=fold):
                    request = korean_request(unicodedata.normalize(fold, message))
                    assert request is not None
                    self.assertEqual(request.clause, clause)

    def test_the_sentences_after_the_request_are_kept(self) -> None:
        request = korean_request("고객 정보 마스킹해서 개발팀에 보내줘. 급해요! 벤더한테도 보내줘.")
        assert request is not None
        self.assertEqual(request.later, ("급해요", "벤더한테도 보내줘"))
        self.assertEqual(korean_request("고객 정보 마스킹해줘").later, ())

    def test_a_negated_clause_is_dropped(self) -> None:
        for message, clause, predicate in (
            ("카드로 결제하지 말고 견적만 보내줘", "견적만 보내줘", "보내"),
            ("결제 말고 100만원 견적만 보내줘", "100만원 견적만 보내줘", "보내"),
            ("벤더한테 보내지 말고 개인정보만 마스킹해줘", "개인정보만 마스킹해줘", "마스킹"),
            ("결제하지 않고 장바구니에 담아줘", "장바구니에 담아줘", "담아"),
            ("내 카드로 결제하지 마시고 회의실만 예약해 주세요", "회의실만 예약해 주세요", "예약"),
            ("내 카드로 결제 없이 회의실 예약만 해줘", "회의실 예약만 해줘", "예약"),
        ):
            with self.subTest(message=message):
                request = korean_request(message)
                assert request is not None
                self.assertEqual((request.clause, request.predicate), (clause, predicate))

    def test_the_light_verbs_passed_are_kept(self) -> None:
        for message, light_verbs in (("결제 처리해줘", ("처리",)), ("결제 진행해줘", ("진행",)), ("결제해줘", ())):
            with self.subTest(message=message):
                request = korean_request(message)
                assert request is not None
                self.assertEqual((request.predicate, request.light_verbs), ("결제", light_verbs))

    def test_joined_clauses_and_before_clauses(self) -> None:
        request = korean_request("고객 정보 마스킹해서 벤더한테 보내줘")
        assert request is not None
        self.assertEqual((request.predicate, request.joined), ("보내", (("마스킹", 2),)))
        request = korean_request("개인정보 제거한 다음에 외부에 올려줘")
        assert request is not None
        self.assertEqual((request.predicate, request.joined), ("올려", (("제거", 1),)))
        request = korean_request("벤더한테 보내기 전에 개인정보 마스킹해줘")
        assert request is not None
        self.assertEqual((request.predicate, request.before), ("마스킹", ("보내",)))
        request = korean_request("외부에 업로드하기 전에 개인정보 지워줘")
        assert request is not None
        self.assertEqual((request.predicate, request.before), ("지워", ("업로드",)))
        # A verb's noun before "후", "뒤" or "다음" joins the next clause too.
        request = korean_request("상담 기록 개인정보 마스킹 후 외부 업체에 전달해줘")
        assert request is not None
        self.assertEqual((request.predicate, request.joined), ("전달", (("마스킹", 3),)))

    def test_the_router_fold_reads_as_the_hint_fold(self) -> None:
        # The router folds Hangul into jamo (NFKD); the hint keeps it composed.
        for message in ("내 카드로 결제해줘", "결제 좀 해줘", "고객 정보 마스킹해서 벤더한테 보내줘", "결제했어"):
            with self.subTest(message=message):
                composed = korean_request(message)
                folded = korean_request(unicodedata.normalize("NFKD", message))
                if composed is None:
                    self.assertIsNone(folded)
                    continue
                assert folded is not None
                self.assertEqual(folded, composed)

    def test_a_loop_prompt_needs_no_request_ending(self) -> None:
        self.assertIsNone(korean_request("재고 생기면 결제"))
        request = korean_request("재고 생기면 결제", require_ending=False)
        assert request is not None
        self.assertEqual(request.predicate, "결제")


if __name__ == "__main__":
    unittest.main()
