from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from _local_package import load_local_package

load_local_package()
from omh.config_adapter import (
    display_key_named_twice,
    display_skin_selection,
    external_dir_registered,
    ensure_external_dir,
    external_dirs,
    remove_external_dir,
)


class ConfigAdapterTests(unittest.TestCase):
    def test_external_dirs_treats_bare_yaml_null_as_empty(self) -> None:
        for value in ("null", "Null", "NULL", "~"):
            with self.subTest(value=value):
                self.assertEqual(external_dirs(f"skills:\n  external_dirs: {value}\n"), [])

    def test_ensure_external_dir_creates_empty_config(self) -> None:
        change = ensure_external_dir("", "/tmp/omh/skills")

        self.assertTrue(change.changed)
        self.assertEqual(external_dirs(change.text), ["/tmp/omh/skills"])

    def test_ensure_external_dir_preserves_existing_keys_and_is_idempotent(self) -> None:
        original = "model: test\nskills:\n  disabled:\n    - old\n"
        first = ensure_external_dir(original, "/tmp/omh/skills")
        second = ensure_external_dir(first.text, "/tmp/omh/skills")

        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertIn("model: test", first.text)
        self.assertIn("disabled:", first.text)
        self.assertEqual(external_dirs(first.text), ["/tmp/omh/skills"])

    def test_external_dirs_accepts_yaml_sequence_at_key_indent(self) -> None:
        original = "model: test\nskills:\n  external_dirs:\n  - /tmp/omh/skills\n"

        self.assertEqual(external_dirs(original), ["/tmp/omh/skills"])
        change = ensure_external_dir(original, "/tmp/omh/skills")

        self.assertFalse(change.changed)
        self.assertEqual(change.text, original)

    def test_ensure_external_dir_preserves_key_indent_sequence_style(self) -> None:
        original = "skills:\n  external_dirs:\n  - /keep\n"
        change = ensure_external_dir(original, "/tmp/omh/skills")

        self.assertTrue(change.changed)
        self.assertEqual(external_dirs(change.text), ["/keep", "/tmp/omh/skills"])
        self.assertIn("  - /keep\n  - /tmp/omh/skills\n", change.text)

    def test_remove_external_dir_only_removes_managed_entry(self) -> None:
        original = "skills:\n  external_dirs:\n    - /keep\n    - /tmp/omh/skills\n"
        change = remove_external_dir(original, "/tmp/omh/skills")

        self.assertTrue(change.changed)
        self.assertEqual(external_dirs(change.text), ["/keep"])

    def test_remove_external_dir_from_key_indent_sequence(self) -> None:
        original = "skills:\n  external_dirs:\n  - /keep\n  - /tmp/omh/skills\n"
        change = remove_external_dir(original, "/tmp/omh/skills")

        self.assertTrue(change.changed)
        self.assertEqual(external_dirs(change.text), ["/keep"])
        self.assertIn("  - /keep\n", change.text)
        self.assertNotIn("/tmp/omh/skills", change.text)

    def test_ensure_external_dir_expands_inline_list(self) -> None:
        original = "skills:\n  external_dirs: [/keep]\n"
        change = ensure_external_dir(original, "/tmp/omh/skills")

        self.assertTrue(change.changed)
        self.assertEqual(external_dirs(change.text), ["/keep", "/tmp/omh/skills"])
        self.assertEqual(change.text.count("external_dirs:"), 1)

    def test_ensure_external_dir_expands_empty_inline_list(self) -> None:
        original = "skills:\n  external_dirs: []\n"
        change = ensure_external_dir(original, "/tmp/omh/skills")

        self.assertTrue(change.changed)
        self.assertEqual(external_dirs(change.text), ["/tmp/omh/skills"])
        self.assertEqual(change.text.count("external_dirs:"), 1)

    def test_ensure_external_dir_expands_bare_yaml_null(self) -> None:
        for value in ("null", "Null", "NULL", "~"):
            with self.subTest(value=value):
                original = f"model: test\nskills:\n  disabled:\n    - old\n  external_dirs: {value}\n"
                first = ensure_external_dir(original, "/tmp/omh/skills")
                second = ensure_external_dir(first.text, "/tmp/omh/skills")

                self.assertTrue(first.changed)
                self.assertFalse(second.changed)
                self.assertIn("model: test", first.text)
                self.assertIn("disabled:", first.text)
                self.assertEqual(external_dirs(first.text), ["/tmp/omh/skills"])
                self.assertIn("  external_dirs:\n    - /tmp/omh/skills\n", first.text)
                self.assertNotIn(f"external_dirs: {value}", first.text)

    def test_ensure_external_dir_rejects_unsupported_scalar_shapes(self) -> None:
        for value in ("'null'", '"null"', "null # comment", "~ # comment", "/tmp/omh/skills"):
            with self.subTest(value=value):
                original = f"skills:\n  external_dirs: {value}\n"
                self.assertEqual(external_dirs(original), [])
                with self.assertRaises(ValueError):
                    ensure_external_dir(original, "/tmp/omh/skills")

    def test_mutations_reject_duplicate_block_with_unsupported_inline_scalar(self) -> None:
        original = "skills:\n  external_dirs:\n    - /tmp/omh/skills\n  external_dirs: /bad\n"

        self.assertEqual(external_dirs(original), ["/tmp/omh/skills"])
        with self.assertRaises(ValueError):
            ensure_external_dir(original, "/tmp/omh/skills")
        with self.assertRaises(ValueError):
            remove_external_dir(original, "/tmp/omh/skills")

    def test_mutations_reject_duplicate_external_dirs_keys_even_when_supported(self) -> None:
        originals = [
            "skills:\n  external_dirs:\n    - /tmp/omh/skills\n  external_dirs: null\n",
            "skills:\n  external_dirs:\n    - /tmp/omh/skills\n  external_dirs: []\n",
            "skills:\n  external_dirs: [/tmp/omh/skills]\n  external_dirs:\n    - /keep\n",
        ]
        for original in originals:
            with self.subTest(original=original):
                with self.assertRaises(ValueError):
                    ensure_external_dir(original, "/tmp/omh/skills")
                with self.assertRaises(ValueError):
                    remove_external_dir(original, "/tmp/omh/skills")

    def test_remove_external_dir_from_inline_list(self) -> None:
        original = "skills:\n  external_dirs: [/keep, /tmp/omh/skills]\n"
        change = remove_external_dir(original, "/tmp/omh/skills")

        self.assertTrue(change.changed)
        self.assertEqual(external_dirs(change.text), ["/keep"])
        self.assertEqual(change.text.count("external_dirs:"), 1)

    def test_remove_external_dir_treats_bare_yaml_null_as_absent(self) -> None:
        for value in ("null", "Null", "NULL", "~"):
            with self.subTest(value=value):
                original = f"skills:\n  external_dirs: {value}\n"
                change = remove_external_dir(original, "/tmp/omh/skills")

                self.assertFalse(change.changed)
                self.assertEqual(change.text, original)
                self.assertEqual(external_dirs(change.text), [])

    def test_remove_external_dir_rejects_unsupported_scalar_shapes(self) -> None:
        for value in ("'null'", '"null"', "null # comment", "~ # comment", "/tmp/omh/skills"):
            with self.subTest(value=value):
                original = f"skills:\n  external_dirs: {value}\n"
                self.assertEqual(external_dirs(original), [])
                with self.assertRaises(ValueError):
                    remove_external_dir(original, "/tmp/omh/skills")

    def test_a_display_key_set_twice_is_named_twice_and_read_first(self) -> None:
        # The release decides a value set in two places by
        # `display_key_named_twice`, never by which one the reader returns,
        # and the reader keeps returning the first value it always did.
        for text in (
            "display:\n  skin: ares\n  skin: omh-crimson\n",
            "display.skin: ares\ndisplay:\n  skin: omh-crimson\n",
            "display:\n  skin: ares\ndisplay:\n  skin: omh-crimson\n",
        ):
            with self.subTest(text=text):
                self.assertTrue(display_key_named_twice(text, "skin"))
                self.assertEqual(display_skin_selection(text), "ares")
        for text in ("display:\n  skin: ares\n", "display.skin: ares\n", "display:\n  interface: cli\n"):
            with self.subTest(text=text):
                self.assertFalse(display_key_named_twice(text, "skin"))


if __name__ == "__main__":
    unittest.main()


class ExternalDirRegistrationTests(unittest.TestCase):
    """The installer registers `current/skills`; a running command knows only
    its generation directory. Measured live 2026-09-08: doctor and probe
    compared the two as strings and called every staged-update install
    unregistered."""

    def test_the_generation_directory_is_registered_through_the_current_pointer(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            generation = root / "generations" / "g1"
            (generation / "skills").mkdir(parents=True)
            os.symlink(generation, root / "current", target_is_directory=True)
            dirs = [(root / "current" / "skills").as_posix()]
            self.assertTrue(external_dir_registered(dirs, generation / "skills"))
            self.assertTrue(external_dir_registered(dirs, root / "current" / "skills"))
            self.assertFalse(external_dir_registered(dirs, root / "generations" / "g2" / "skills"))
            self.assertFalse(external_dir_registered([], generation / "skills"))

    def test_a_textual_match_needs_no_directory_on_disk(self) -> None:
        self.assertTrue(external_dir_registered(["/nowhere/skills"], "/nowhere/skills"))
        self.assertFalse(external_dir_registered(["/nowhere/skills"], "/elsewhere/skills"))
