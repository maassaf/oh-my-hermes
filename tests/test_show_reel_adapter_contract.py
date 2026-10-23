"""PIN core nonexecution and the optional declared companion boundary."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from _local_package import load_local_package

load_local_package()
from omh.skills.skill_record import project, skill_record


class ShowReelAdapterContractTests(unittest.TestCase):
    def test_canvas_recipe_converts_and_sets_color_on_frames_not_only_encoder_flags(self):
        files = project(skill_record("show-reel"), "hermes")
        recipe = files["omh-show-reel/references/renderer-adapter.md"]
        self.assertIn("verified sRGB", recipe)
        self.assertIn("itrc=iec61966-2-1", recipe)
        self.assertIn("setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709", recipe)

    def test_projection_is_pure_preparation_without_hidden_runtime(self):
        with patch("subprocess.Popen", side_effect=AssertionError("hidden subprocess")), patch("socket.socket", side_effect=AssertionError("hidden socket")):
            record = skill_record("show-reel")
            for target in ("hermes", "agent-skills"):
                files = project(record, target)
                self.assertTrue(files)
                combined = " ".join("\n".join(files.values()).split())
                self.assertNotIn("omh show-reel render", combined)
                self.assertNotIn("omh runtime workflow-artifact show-reel", combined)
                self.assertFalse(record.definition.host_requires_tools)
                recipe = " ".join(files["omh-show-reel/references/renderer-adapter.md"].split())
                for marker in ("host-tool recipe", "optional local companion", "deterministic", "seek(tSeconds)", "partial", "resume", "timeout", "cancel", "pageerror", "document.fonts.ready", "frame", "FFmpeg", "modified", "toolkit/LICENSE.upstream", "toolkit/upstream-manifest.json"):
                    self.assertIn(marker.lower(), recipe.lower())


if __name__ == "__main__":
    unittest.main()
