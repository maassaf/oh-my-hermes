from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _cli_harness import run_cli
from _local_package import load_local_package

load_local_package()
from omh.install.compression_defaults import (
    compression_fallback_candidates,
    compression_settings,
    ensure_compression_defaults,
)


SINGLE_ENDPOINT_CONFIG = """version: 1
fallback_providers:
  - provider: openai-codex
    model: gpt-5.5
auxiliary:
  vision:
    provider: auto
    model:
  compression:
    provider: sglang_proxy
    model: glm-5.2-ultrafast
skills:
  external_dirs:
    - ~/.omh/skills
"""


class CompressionSettingsTests(unittest.TestCase):
    def test_reads_pinned_compression_slot(self) -> None:
        settings = compression_settings(SINGLE_ENDPOINT_CONFIG)

        self.assertTrue(settings["configured"])
        self.assertEqual(settings["provider"], "sglang_proxy")
        self.assertEqual(settings["model"], "glm-5.2-ultrafast")
        self.assertFalse(settings["has_fallback_chain"])

    def test_missing_auxiliary_or_compression_is_not_configured(self) -> None:
        for text in ("", "skills:\n  external_dirs:\n    - ~/.omh/skills\n", "auxiliary:\n  vision:\n    provider: auto\n"):
            with self.subTest(text=text):
                self.assertFalse(compression_settings(text)["configured"])

    def test_fallback_candidates_ignore_the_auxiliary_block(self) -> None:
        text = SINGLE_ENDPOINT_CONFIG.replace(
            "  compression:\n    provider: sglang_proxy\n    model: glm-5.2-ultrafast\n",
            "  compression:\n    provider: sglang_proxy\n    model: glm-5.2-ultrafast\n"
            "    fallback_chain:\n      - provider: never-harvest-me\n        model: x\n",
        )

        self.assertEqual(compression_fallback_candidates(text), [{"provider": "openai-codex", "model": "gpt-5.5"}])


class EnsureCompressionDefaultsTests(unittest.TestCase):
    def test_adds_fallback_chain_derived_from_configured_fallback_provider(self) -> None:
        change = ensure_compression_defaults(SINGLE_ENDPOINT_CONFIG)

        self.assertTrue(change.changed)
        self.assertEqual(change.message, "added auxiliary.compression fallback chain")
        self.assertIn(
            "  compression:\n"
            "    provider: sglang_proxy\n"
            "    model: glm-5.2-ultrafast\n"
            "    fallback_chain:\n"
            "      - provider: openai-codex\n"
            "        model: gpt-5.5\n",
            change.text,
        )
        settings = compression_settings(change.text)
        self.assertTrue(settings["has_fallback_chain"])
        self.assertEqual(settings["provider"], "sglang_proxy")

    def test_is_idempotent_and_preserves_unrelated_keys(self) -> None:
        first = ensure_compression_defaults(SINGLE_ENDPOINT_CONFIG)
        second = ensure_compression_defaults(first.text)

        self.assertFalse(second.changed)
        self.assertEqual(second.text, first.text)
        self.assertIn("version: 1", first.text)
        self.assertIn("  vision:\n    provider: auto", first.text)
        self.assertIn("skills:\n  external_dirs:\n    - ~/.omh/skills", first.text)

    def test_never_overwrites_a_user_authored_fallback_chain(self) -> None:
        text = SINGLE_ENDPOINT_CONFIG.replace(
            "    model: glm-5.2-ultrafast\n",
            "    model: glm-5.2-ultrafast\n    fallback_chain:\n      - provider: my-own\n        model: my-model\n",
        )

        change = ensure_compression_defaults(text)

        self.assertFalse(change.changed)
        self.assertEqual(change.message, "compression fallback chain already present")
        self.assertEqual(change.text, text)
        self.assertIn("      - provider: my-own\n", change.text)
        self.assertEqual(change.text.count("fallback_chain:"), 1)

    def test_does_nothing_without_a_configured_fallback_provider(self) -> None:
        text = SINGLE_ENDPOINT_CONFIG.replace(
            "fallback_providers:\n  - provider: openai-codex\n    model: gpt-5.5\n", ""
        )

        change = ensure_compression_defaults(text)

        self.assertFalse(change.changed)
        self.assertEqual(change.message, "no configured fallback provider to derive a compression chain from")
        self.assertEqual(change.text, text)

    def test_does_nothing_when_the_only_fallback_is_the_compression_provider(self) -> None:
        text = SINGLE_ENDPOINT_CONFIG.replace("provider: openai-codex", "provider: sglang_proxy")

        change = ensure_compression_defaults(text)

        self.assertFalse(change.changed)
        self.assertEqual(change.message, "no configured fallback provider to derive a compression chain from")

    def test_does_nothing_when_auxiliary_compression_is_absent(self) -> None:
        text = "skills:\n  external_dirs:\n    - ~/.omh/skills\n"

        change = ensure_compression_defaults(text)

        self.assertFalse(change.changed)
        self.assertEqual(change.message, "auxiliary.compression not configured")
        self.assertEqual(change.text, text)


class ApplyCompressionDefaultsCliTests(unittest.TestCase):
    def _apply(self, root: Path) -> dict[str, object]:
        status, stdout, stderr = run_cli(
            ["--omh-home", str(root / ".omh"), "--hermes-home", str(root / ".hermes"), "apply", "--json"]
        )
        self.assertEqual(stderr, "")
        self.assertEqual(status, 0)
        return json.loads(stdout)

    def test_apply_writes_compression_fallback_chain_and_stays_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / ".hermes" / "config.yaml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(SINGLE_ENDPOINT_CONFIG, encoding="utf-8")

            first = self._apply(root)
            written = config_path.read_text(encoding="utf-8")

            self.assertTrue(first["compression_defaults"]["changed"])
            self.assertIn("    fallback_chain:\n      - provider: openai-codex\n", written)
            self.assertTrue(compression_settings(written)["has_fallback_chain"])

            second = self._apply(root)

            self.assertFalse(second["compression_defaults"]["changed"])
            self.assertEqual(config_path.read_text(encoding="utf-8"), written)

    def test_apply_leaves_a_config_without_auxiliary_compression_alone(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = self._apply(root)
            written = (root / ".hermes" / "config.yaml").read_text(encoding="utf-8")

            self.assertFalse(result["compression_defaults"]["changed"])
            self.assertEqual(result["compression_defaults"]["message"], "auxiliary.compression not configured")
            self.assertNotIn("fallback_chain", written)
            self.assertIn("external_dirs", written)


if __name__ == "__main__":
    unittest.main()
