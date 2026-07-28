"""`plpipe setup` — 물어보고 config.toml 을 대신 만드는 부분.

사용자가 TOML 을 직접 편집하지 않아도 되게 하는 게 목적이므로,
템플릿 구조에서 뽑아낸 값이 실제로 맞는지가 핵심이다.
"""

from __future__ import annotations

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plpipe import setup_wizard as wiz  # noqa: E402
from test_check import make_dump  # noqa: E402


class TestTemplateDiscovery(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def touch(self, rel: str):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return path

    def test_finds_aep_files(self):
        self.touch("templates/warm tape society.aep")
        found = wiz.find_templates(self.root)
        self.assertEqual([p.name for p in found], ["warm tape society.aep"])

    def test_skips_after_effects_auto_save_folder(self):
        self.touch("templates/real.aep")
        self.touch("Adobe After Effects Auto-Save/default auto-save 20.aep")
        found = wiz.find_templates(self.root)
        self.assertEqual([p.name for p in found], ["real.aep"])

    def test_skips_pipeline_output(self):
        self.touch("templates/real.aep")
        self.touch("projects/ep08/work/ae/ep08.aep")
        self.assertEqual([p.name for p in wiz.find_templates(self.root)],
                         ["real.aep"])

    def test_no_templates_returns_empty(self):
        self.assertEqual(wiz.find_templates(self.root), [])


class TestDerivingFromTemplate(unittest.TestCase):
    def setUp(self):
        self.info = wiz.derive_from_dump(make_dump())

    def test_picks_the_comp_holding_the_precomps_as_main(self):
        self.assertEqual(self.info["main_comp"], "Main")

    def test_reads_resolution_and_frame_rate_from_main(self):
        self.assertEqual((self.info["width"], self.info["height"]), (3840, 2160))
        self.assertEqual(self.info["fps"], 29.97)

    def test_lists_precomps_in_stack_order(self):
        # 사용자는 이 목록에서 인트로만 빼면 된다.
        self.assertEqual(self.info["precomps"][0], "Change - Things 21")
        self.assertEqual(len(self.info["precomps"]), 5)

    def test_notices_the_audio_layer(self):
        self.assertTrue(self.info["has_audio_layer"])

    def test_uses_render_templates_the_project_actually_has(self):
        self.assertEqual(self.info["render_settings"], "Best Settings")
        self.assertEqual(self.info["output_module"], "Lossless")

    def test_falls_back_when_preferred_template_is_missing(self):
        dump = make_dump()
        dump["templates"]["output_modules"] = ["Photoshop", "AIFF 48kHz"]
        dump["templates"]["render_settings"] = ["Draft Settings"]
        info = wiz.derive_from_dump(dump)
        self.assertEqual(info["output_module"], "Photoshop")
        self.assertEqual(info["render_settings"], "Draft Settings")


class TestSelectorChoice(unittest.TestCase):
    def setUp(self):
        self.comps = {c["name"]: c for c in make_dump()["comps"]}
        self.songs = ["Change - Things", "Change - Things 2",
                      "Change - Things 3", "Change - Things 4"]

    def test_picks_still_selector_when_every_comp_has_one(self):
        self.assertEqual(wiz.pick_selector(self.comps, self.songs, "still"),
                         "@still")

    def test_picks_text_selector(self):
        self.assertEqual(wiz.pick_selector(self.comps, self.songs, "text"),
                         "@text")

    def test_returns_blank_when_no_comp_has_that_kind(self):
        self.assertEqual(wiz.pick_selector(self.comps, self.songs, "camera"), "")


class TestGeneratedConfig(unittest.TestCase):
    ANSWERS = {
        "project": "templates/warm tape society.aep",
        "main_comp": "Main",
        "slot_comps": ["Change - Things", "Change - Things 2", "Change - Things 3"],
        "width": 3840, "height": 2160, "fps": 29.97,
        "mode": "full",
        "audio_source": "manual",
        "repeat": 0,
        "channel_name": "warm tape society",
        "image_layer": "@still",
        "title_layer": "@text",
        "audio_layer": "@audio",
        "render_settings": "Best Settings",
        "output_module": "Lossless",
        "app": r"C:\Program Files\Adobe\Adobe After Effects 2021\Support Files\AfterFX.exe",
        "aerender": "",
    }

    def parsed(self, **overrides):
        answers = {**self.ANSWERS, **overrides}
        return tomllib.loads(wiz.render_config(answers))

    def test_output_is_valid_toml(self):
        self.assertEqual(self.parsed()["ae"]["main_comp"], "Main")

    def test_track_count_matches_slot_comps(self):
        data = self.parsed()
        self.assertEqual(data["project"]["track_count"],
                         len(data["ae"]["slot_comps"]))

    def test_windows_paths_are_written_with_forward_slashes(self):
        # 역슬래시를 그대로 쓰면 TOML 이 이스케이프로 해석해 깨진다.
        app = self.parsed()["ae"]["app"]
        self.assertNotIn("\\", app)
        self.assertIn("AfterFX.exe", app)

    def test_manual_audio_writes_the_master_path(self):
        audio = self.parsed()["audio"]
        self.assertEqual(audio["source"], "manual")
        self.assertEqual(audio["master"], "drop/master.wav")

    def test_pipeline_audio_omits_the_master_path(self):
        audio = self.parsed(audio_source="pipeline")["audio"]
        self.assertEqual(audio["source"], "pipeline")
        self.assertNotIn("master", audio)

    def test_video_settings_come_from_the_template(self):
        video = self.parsed()["video"]
        self.assertEqual((video["width"], video["height"], video["fps"]),
                         (3840, 2160, 29.97))

    def test_channel_name_with_quotes_survives(self):
        data = self.parsed(channel_name='He said "hi"')
        self.assertEqual(data["channel"]["name"], 'He said "hi"')

    def test_blank_selector_is_written_as_empty_string(self):
        data = self.parsed(title_layer="")
        self.assertEqual(data["ae"]["title_layer"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
