"""설정 점검 (`plpipe check`).

4K 30분 렌더는 몇 시간이 걸린다. 그전에 잡을 수 있는 실수는 다 잡아야 한다.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plpipe import check as check_mod  # noqa: E402
from plpipe.config import Config  # noqa: E402


def make_dump() -> dict:
    """사용자가 보내준 템플릿 리포트를 옮긴 구조.

    - 곡별 컴프가 따로 있고 레이어 이름이 곡 제목·파일명이다
    - 일부 컴프에 정리 안 된 스틸이 2장 있다
    - 'Change - Things 21' 에는 텍스트 레이어가 없다
    - Main 안에 로고·스펙트럼·오디오가 들어 있다
    """
    comps = []
    for idx in [21, 1, 2, 3, 4]:
        name = "Change - Things" if idx == 1 else f"Change - Things {idx}"
        layers = [{"index": 1, "name": f"제목{idx}", "kind": "text"},
                  {"index": 2, "name": f"scene_{idx}.png", "kind": "still"}]
        if idx in (3, 4):
            layers.append({"index": 3, "name": f"leftover_{idx}.png", "kind": "still"})
        if idx == 21:
            layers = [layers[1]]          # 텍스트 레이어 없음
        comps.append({"name": name, "width": 3840, "height": 2160,
                      "fps": 29.97, "duration": 3629.997, "layers": layers})

    comps.append({
        "name": "Main", "width": 3840, "height": 2160, "fps": 29.97,
        "duration": 1773.974,
        "layers": [
            {"index": 1, "name": "LOGO.png", "kind": "still"},
            {"index": 2, "name": "overlay.mp4", "kind": "footage"},
            {"index": 3, "name": "AUDIO SPECTRUM", "kind": "solid"},
            {"index": 4, "name": "ep07_full.wav", "kind": "audio"},
            {"index": 5, "name": "Song Title", "kind": "text"},
        ],
    })
    return {
        "project": "lofi.aep",
        "comps": comps,
        "templates": {
            "render_settings": ["Best Settings", "Draft Settings"],
            "output_modules": ["Lossless", "AIFF 48kHz", "Photoshop"],
        },
    }


BASE_CONFIG = """
[project]
track_count = 5

[audio]
source = "manual"

[video]
fps = 29.97

[ae]
mode = "full"
app = "/bin/sh"
aerender = "/bin/sh"
project = "templates/t.aep"
main_comp = "Main"
slot_comps = ["Change - Things 21", "Change - Things", "Change - Things 2",
              "Change - Things 3", "Change - Things 4"]
image_layer = "@still"
title_layer = "@text"
audio_layer = "@audio"
render_settings = "Best Settings"
output_module = "Lossless"
"""


class CheckCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "templates").mkdir()
        (self.root / "templates" / "t.aep").write_bytes(b"stub")
        (self.root / "work").mkdir()
        (self.root / "work" / "ae-template.json").write_text(
            json.dumps(make_dump(), ensure_ascii=False), encoding="utf-8")

    def check(self, body: str = BASE_CONFIG) -> dict[str, check_mod.Result]:
        path = self.root / "config.toml"
        path.write_text(body, encoding="utf-8")
        results = check_mod.run(Config.load(path))
        return {r.label: r for r in results}

    def assertOk(self, results, label):
        self.assertIn(label, results)
        self.assertEqual(results[label].status, check_mod.OK,
                         f"{label}: {results[label].detail}")

    def assertFails(self, results, label, contains=None):
        self.assertIn(label, results)
        self.assertEqual(results[label].status, check_mod.FAIL,
                         f"{label}: {results[label].detail}")
        if contains:
            self.assertIn(contains, results[label].detail + results[label].fix)


class TestValidConfig(CheckCase):
    def test_a_correct_config_has_no_failures(self):
        results = self.check()
        failures = [r.label for r in results.values() if r.failed]
        self.assertEqual(failures, [])

    def test_recognises_comps_and_layers(self):
        results = self.check()
        for label in ("메인 컴프", "곡 컴프", "오디오 레이어",
                      "렌더 설정", "출력 모듈"):
            self.assertOk(results, label)


class TestCatchesMistakes(CheckCase):
    def test_wrong_case_comp_name_suggests_the_right_one(self):
        results = self.check(BASE_CONFIG.replace('main_comp = "Main"',
                                                 'main_comp = "MAIN"'))
        self.assertFails(results, "메인 컴프", "Main")

    def test_trailing_space_in_comp_name(self):
        results = self.check(BASE_CONFIG.replace('"Change - Things 2"',
                                                 '"Change - Things 2 "'))
        self.assertFails(results, "곡 컴프", "공백")

    def test_slot_count_must_match_track_count(self):
        results = self.check(BASE_CONFIG.replace("track_count = 5",
                                                 "track_count = 13"))
        self.assertFails(results, "곡 컴프 목록", "track_count")

    def test_unknown_output_module_lists_the_real_ones(self):
        results = self.check(BASE_CONFIG.replace('output_module = "Lossless"',
                                                 'output_module = "H.264"'))
        self.assertFails(results, "출력 모듈", "Lossless")

    def test_missing_template_file(self):
        (self.root / "templates" / "t.aep").unlink()
        self.assertFails(self.check(), "AE 템플릿")

    def test_unknown_render_mode(self):
        results = self.check(BASE_CONFIG.replace('mode = "full"', 'mode = "turbo"'))
        self.assertFails(results, "렌더 모드", "segments")

    def test_image_selector_matching_nothing_is_fatal(self):
        # 이미지가 없으면 빌드가 멈추므로 오류다.
        results = self.check(BASE_CONFIG.replace('image_layer = "@still"',
                                                 'image_layer = "@camera"'))
        self.assertFails(results, "이미지 레이어")


class TestOptionalAndAmbiguous(CheckCase):
    def test_missing_title_layer_is_only_a_warning(self):
        # 'Change - Things 21' 에는 텍스트 레이어가 없다. 빌드는 그냥
        # 건너뛰므로 오류가 아니라 알림이어야 한다.
        results = self.check()
        self.assertEqual(results["제목 레이어"].status, check_mod.WARN)
        self.assertIn("Change - Things 21", results["제목 레이어"].detail)

    def test_multiple_stills_are_flagged_but_not_fatal(self):
        results = self.check()
        self.assertEqual(results["이미지 레이어"].status, check_mod.WARN)
        self.assertIn("Change - Things 3", results["이미지 레이어"].detail)

    def test_selecting_the_last_still_resolves_the_ambiguity(self):
        results = self.check(BASE_CONFIG.replace('image_layer = "@still"',
                                                 'image_layer = "@still:last"'))
        self.assertOk(results, "이미지 레이어")


class TestWithoutDump(CheckCase):
    def test_suggests_running_inspect(self):
        (self.root / "work" / "ae-template.json").unlink()
        results = self.check()
        self.assertEqual(results["템플릿 구조 대조"].status, check_mod.WARN)
        self.assertIn("ae inspect", results["템플릿 구조 대조"].fix)

    def test_ffmpeg_mode_does_not_require_after_effects(self):
        (self.root / "templates" / "t.aep").unlink()
        results = self.check(BASE_CONFIG.replace('mode = "full"', 'mode = "ffmpeg"'))
        self.assertOk(results, "After Effects")
        self.assertOk(results, "AE 템플릿")


if __name__ == "__main__":
    unittest.main(verbosity=2)
