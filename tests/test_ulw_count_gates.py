"""Count gates for the ULW engine roster on human-facing surfaces.

The user-facing ULW engine roster is the canonical list of
``omh.skills.catalog.ulw_inventory_payload()`` -- retired engines left these
surfaces when #954 stage 5 shipped, so the count gates read the canonical
count, not ``ULW_ENGINE_SKILL_NAMES`` (which keeps all twelve names for
display prefixing and reference contracts).
Five surfaces state that count in prose: the site's ``ulw.lead`` sentence
(``site/index.html`` fallback text plus all four locales of ``site/i18n.js``)
and the count sentence in each of the four READMEs. Each README also carries a
ULW table whose row count must match the roster.

These gates read the numeral per locale and compare it to the roster size, so
the sentence can be reworded freely as long as the number stays true. The row
gate matches the table-row form (``| ⚡ `ulw-``, and ``| ⚡ `dag-ulw``, the one engine
whose label is its own name) rather than a bare ``ulw-``
substring, because prose lines also mention ``ulw-`` and a bare grep would
encode an off-by-one that turns into a false failure when prose is reworded.
"""

from __future__ import annotations

import re
from pathlib import Path
import unittest

from _local_package import load_local_package

load_local_package()
from omh.skills.catalog import ulw_inventory_payload

_ENGLISH_NUMBER_WORDS = {
    "Eight": 8,
    "Nine": 9,
    "Ten": 10,
    "Eleven": 11,
    "Twelve": 12,
    "Thirteen": 13,
    "Fourteen": 14,
    "Fifteen": 15,
}

_CHINESE_NUMERALS = {
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
}

_README_TABLE_ROW = re.compile(r"^\| ⚡ `(?:ulw-|dag-ulw`)", re.MULTILINE)

_README_COUNT_PATTERNS = {
    "README.md": re.compile(r"\b([A-Z][a-z]+) `ulw-` workflows\."),
    "README.ko.md": re.compile(r"\b(\d+)개의 `ulw-` workflow\."),
    "README.ja.md": re.compile(r"\b(\d+) 個の `ulw-` workflow。"),
    "README.zh.md": re.compile(r"([八九十一二三四五]+)个 `ulw-` workflow。"),
}

_SITE_LEAD_PATTERNS = {
    "en": re.compile(r"^([A-Z][a-z]+) long-horizon lanes\."),
    "ko": re.compile(r"^(\d+)개의 장기 레인\."),
    "ja": re.compile(r"^(\d+) の長期レーン。"),
    "zh": re.compile(r"^([八九十一二三四五]+)条长周期车道。"),
}


def _numeral_to_int(surface: str, numeral: str) -> int:
    if numeral.isdigit():
        return int(numeral)
    if numeral in _ENGLISH_NUMBER_WORDS:
        return _ENGLISH_NUMBER_WORDS[numeral]
    if numeral in _CHINESE_NUMERALS:
        return _CHINESE_NUMERALS[numeral]
    raise AssertionError(f"{surface}: unmapped numeral {numeral!r}")


def _site_lead_entries() -> dict[str, str]:
    """Return the ``ulw.lead`` locale strings from ``site/i18n.js``."""
    source = Path("site/i18n.js").read_text(encoding="utf-8")
    match = re.search(r'"ulw\.lead":\s*\{(.*?)\}', source, re.DOTALL)
    if match is None:
        raise AssertionError('site/i18n.js: no "ulw.lead" entry found')
    entries = dict(re.findall(r'(\w+):\s*"([^"]*)"', match.group(1)))
    missing = {"en", "ko", "ja", "zh"} - set(entries)
    if missing:
        raise AssertionError(f"site/i18n.js ulw.lead: missing locales {sorted(missing)}")
    return entries


class UlwCountGateTest(unittest.TestCase):
    def test_site_lead_states_engine_count_in_all_locales(self) -> None:
        expected = len(ulw_inventory_payload()["canonical_engines"])
        entries = _site_lead_entries()
        for locale, pattern in _SITE_LEAD_PATTERNS.items():
            with self.subTest(locale=locale):
                match = pattern.search(entries[locale])
                self.assertIsNotNone(
                    match,
                    f"site/i18n.js ulw.lead[{locale}]: count sentence not found "
                    f"in {entries[locale]!r}",
                )
                stated = _numeral_to_int(f"site/i18n.js ulw.lead[{locale}]", match.group(1))
                self.assertEqual(
                    stated,
                    expected,
                    f"site/i18n.js ulw.lead[{locale}] states {stated} ULW engines; "
                    f"source has {expected}",
                )

    def test_site_html_fallback_states_engine_count(self) -> None:
        expected = len(ulw_inventory_payload()["canonical_engines"])
        html = Path("site/index.html").read_text(encoding="utf-8")
        match = re.search(r'data-i18n="ulw\.lead">([A-Z][a-z]+) long-horizon lanes\.', html)
        self.assertIsNotNone(match, "site/index.html: ulw.lead fallback sentence not found")
        stated = _numeral_to_int("site/index.html ulw.lead", match.group(1))
        self.assertEqual(
            stated,
            expected,
            f"site/index.html ulw.lead fallback states {stated} ULW engines; "
            f"source has {expected}",
        )

    def test_site_hero_stat_states_engine_count(self) -> None:
        expected = len(ulw_inventory_payload()["canonical_engines"])
        html = Path("site/index.html").read_text(encoding="utf-8")
        match = re.search(
            r'data-count-to="(\d+)">(\d+)</strong>'
            r'<span data-i18n="stat\.workflows">',
            html,
        )
        self.assertIsNotNone(match, "site/index.html: ulw workflows hero stat not found")
        for stated in (int(match.group(1)), int(match.group(2))):
            self.assertEqual(
                stated,
                expected,
                f"site/index.html hero stat states {stated} ulw workflows; "
                f"source has {expected}",
            )

    def test_readme_count_sentences_state_engine_count(self) -> None:
        expected = len(ulw_inventory_payload()["canonical_engines"])
        for name, pattern in _README_COUNT_PATTERNS.items():
            with self.subTest(readme=name):
                text = Path(name).read_text(encoding="utf-8")
                match = pattern.search(text)
                self.assertIsNotNone(match, f"{name}: ULW count sentence not found")
                stated = _numeral_to_int(name, match.group(1))
                self.assertEqual(
                    stated,
                    expected,
                    f"{name} states {stated} ULW engines; source has {expected}",
                )

    def test_readme_ulw_table_row_counts_match_source(self) -> None:
        expected = len(ulw_inventory_payload()["canonical_engines"])
        for name in _README_COUNT_PATTERNS:
            with self.subTest(readme=name):
                text = Path(name).read_text(encoding="utf-8")
                rows = len(_README_TABLE_ROW.findall(text))
                self.assertEqual(
                    rows,
                    expected,
                    f"{name} has {rows} ULW table rows; source has {expected} engines",
                )


if __name__ == "__main__":
    unittest.main()
