"""Local deterministic PCM, not listening/artistic approval."""
from __future__ import annotations

import array
from pathlib import Path
import runpy
import sys
import tempfile
import unittest

KIT = Path(__file__).resolve().parents[1] / "src/skills/show_reel_toolkit"


def load(name):
    path = KIT / "scripts" / name
    if not path.is_file():
        raise AssertionError(f"missing real local audio helper {name}")
    sys.path.insert(0, str(path.parent))
    try:
        return runpy.run_path(str(path))
    finally:
        sys.path.pop(0)


class SynthTests(unittest.TestCase):
    def test_pcm_output_refuses_parent_symlink_before_writing(self):
        api = load("synth_music.py")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = root / "outside"
            real.mkdir()
            link = root / "link"
            try:
                link.symlink_to(real, target_is_directory=True)
            except OSError:
                self.skipTest("symlink capability unavailable")
            with self.assertRaises(ValueError):
                api["write_wav"](link / "escape.wav", array.array("f", [.1, .1]), 8000)
            self.assertFalse((real / "escape.wav").exists())

    def test_seeded_section_score_sfx_repeat_and_align_samples(self):
        api = load("synth_music.py")
        spec = {"duration": .2, "sample_rate": 8000, "seed": 17, "sections": [{"start": 0, "end": .2, "notes": [48, 55, 60], "energy": .6}], "events": [{"kind": "tick", "at": .1, "align": "peak", "gain_db": -12}]}
        a, stems = api["synthesize"](spec)
        b, _ = api["synthesize"](spec)
        self.assertEqual(a.tobytes(), b.tobytes())
        self.assertEqual(len(a), 3200)
        self.assertGreater(max(abs(v) for v in a), 0)
        event = stems["sfx"]
        self.assertEqual(max(range(len(event) // 2), key=lambda i: abs(event[2*i]) + abs(event[2*i+1])), 800)
        other, _ = api["synthesize"]({**spec, "seed": 18})
        self.assertNotEqual(a.tobytes(), other.tobytes())
        with self.assertRaises(ValueError):
            api["synthesize"]({**spec, "events": [{"kind": "unknown", "at": .1}]})


class MixTests(unittest.TestCase):
    def test_required_tracks_duck_automation_stems_and_fail_closed(self):
        api = load("mix.py")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rate = 8000
            music = array.array("f", [.08] * (rate*2))
            voice = array.array("f", [0.] * (rate*2))
            for i in range(2000, 4000):
                voice[2*i] = voice[2*i+1] = .2
            api["write_wav"](root / "music.wav", music, rate)
            api["write_wav"](root / "voice.wav", voice, rate)
            spec = {"duration": 1, "sample_rate": rate, "mode": "narration+bed/SFX", "music": {"file": "music.wav", "gain_db": 0, "automation": [[0, 0], [1, 0]]}, "narration": {"file": "voice.wav"}, "sfx": []}
            out, stems, report = api["mix"](spec, root)
            bed = stems["bed"]
            self.assertLess(abs(bed[6000]), abs(bed[600]))
            self.assertGreater(abs(bed[15000]), abs(bed[6000]))
            self.assertEqual(len(out), 16000)
            self.assertLessEqual(max(abs(v) for v in out), 10**(-1/20)+1e-6)
            self.assertIn("speech_active_samples", report)
            for mode, changes in [("music-only", {"music": {"file": "absent.wav"}}), ("narration", {"narration": {"file": "absent.wav"}})]:
                with self.assertRaises((ValueError, FileNotFoundError)):
                    api["mix"]({**spec, "mode": mode, **changes}, root)
            muted, _, muted_report = api["mix"]({"duration": 1, "sample_rate": rate, "mode": "muted"}, root)
            self.assertFalse(any(muted))
            self.assertEqual(muted_report["loudness"], "not_applicable_muted")
            with self.assertRaises(ValueError):
                api["parse_loudnorm"]("unparseable")
            (root / "corrupt.wav").write_bytes(b"not audio")
            with self.assertRaises((ValueError, EOFError)):
                api["read_wav"](root / "corrupt.wav", rate)


if __name__ == "__main__":
    unittest.main()
