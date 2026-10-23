"""Locale acceptance coverage is a method contract, not font/TTS certification."""
from __future__ import annotations

import unittest

from _local_package import load_local_package

load_local_package()
from omh.skills.skill_record import project, skill_record


class ShowReelLocaleContractTests(unittest.TestCase):
    def test_native_and_portable_keep_language_and_failure_contracts(self):
        record = skill_record("show-reel")
        for target in ("hermes", "agent-skills"):
            with self.subTest(target=target):
                files = project(record, target)
                prefix = "omh-show-reel/"
                for name in ("brief-and-locales", "storyboard-and-evidence", "renderer-adapter", "assets-and-rights", "render-qa-delivery", "source-package", "procedure"):
                    self.assertIn(prefix + "references/" + name + ".md", files)
                locales = " ".join(files[prefix + "references/brief-and-locales.md"].split())
                for marker in ("reply_language", "audience_locales", "content_locale", "onscreen_text", "subtitle_text", "narration_script", "pronunciation_text", "voice_policy", "font_policy", "direction_policy", "current explicit", "stable preferences", "user language", "--language", "OMH_LANG", "EN", "KO", "JA", "ZH", "AR", "Mixed", "BCP47", "bidi", "shaping", "line break", "measured", "HOLD"):
                    self.assertIn(marker, locales)
                for forbidden in ("language_code=ko", "language_code=kor", "Marcus", "--locale", "~/.claude", "characters per minute"):
                    self.assertNotIn(forbidden, "\n".join(files.values()))
                qa = files[prefix + "references/render-qa-delivery.md"]
                for marker in ("delivery-size", "keep/fix/rebuild", "weakest", "STT only", "true peak", "color", "filesize", "pageerror", "partial", "attached", "received", "published"):
                    self.assertIn(marker, qa)

    def test_compact_body_owns_first_steps_without_full_contract_duplication(self):
        record = skill_record("show-reel")
        files = project(record, "hermes")
        body = files["omh-show-reel/SKILL.md"]
        full = files["omh-show-reel/references/full-contract.md"]
        for line in record.definition.opening_steps:
            self.assertIn(line, body)
            self.assertNotIn(line, full)
        self.assertNotIn("skill_view(", "\n".join(project(record, "agent-skills").values()))


if __name__ == "__main__":
    unittest.main()
