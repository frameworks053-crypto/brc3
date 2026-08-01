"""After Effects ExtendScript 검증.

AE 는 여기서 돌릴 수 없으므로, tests/ae_mock/harness.mjs 가 AE API 를 흉내내어
실제 .jsx 를 실행하고 결과 상태를 JSON 으로 돌려준다. node 가 없으면 건너뛴다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "ae_mock" / "harness.mjs"
SCRIPTS = ROOT / "plpipe" / "assets" / "ae"

NODE = shutil.which("node")


def run_harness(mode: str, script: str) -> dict:
    proc = subprocess.run(
        [NODE, str(HARNESS), mode, str(SCRIPTS / script)],
        capture_output=True, text=True, errors="replace",
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"하네스 실행 실패 ({mode}/{script}):\n{proc.stderr[-3000:]}"
        )
    return json.loads(proc.stdout)


@unittest.skipUnless(NODE, "node 가 없어 ExtendScript 검증을 건너뜁니다")
class TestBuildProjectSegments(unittest.TestCase):
    """segments 모드: 곡별 슬롯 컴프를 만들고 렌더 큐에 담는다."""

    @classmethod
    def setUpClass(cls):
        cls.result = run_harness("build", "build_project.jsx")

    def test_creates_one_comp_per_track(self):
        names = [c["name"] for c in self.result["built"]]
        self.assertEqual(names, ["PL_SLOT_01", "PL_SLOT_02", "PL_SLOT_03"])

    def test_comp_duration_matches_job(self):
        durations = [c["duration"] for c in self.result["built"]]
        self.assertEqual(durations, [181.291666, 204.708333, 195.125])

    def test_each_image_is_swapped_in(self):
        for i, comp in enumerate(self.result["built"], start=1):
            image = next(l for l in comp["layers"] if l["name"] == "IMAGE")
            self.assertEqual(image["source"], f"/proj/drop/images/0{i}.png")
        self.assertEqual(len(self.result["imported"]), 3)

    def test_original_template_comp_is_untouched(self):
        # 원본 SLOT 의 이미지는 플레이스홀더 그대로여야 한다.
        self.assertEqual(self.result["originalSlotImageSource"], "placeholder.png")

    def test_title_and_index_text_are_set(self):
        for i, comp in enumerate(self.result["built"], start=1):
            title = next(l for l in comp["layers"] if l["name"] == "TITLE")
            index = next(l for l in comp["layers"] if l["name"] == "INDEX")
            self.assertEqual(title["text"], f'곡 "{i}"')
            self.assertEqual(index["text"], f"{i:02d}")

    def test_keyframed_scale_is_left_alone(self):
        # 켄번즈 같은 모션이 걸린 레이어는 스케일을 덮어쓰면 안 된다.
        for comp in self.result["built"]:
            image = next(l for l in comp["layers"] if l["name"] == "IMAGE")
            self.assertTrue(image["scaleKeyed"])
            self.assertEqual(image["scaleValue"], [100, 100])

    def test_layers_are_extended_to_cover_comp(self):
        for comp in self.result["built"]:
            for layer in comp["layers"]:
                self.assertGreaterEqual(layer["outPoint"], comp["duration"] - 1e-6)

    def test_render_queue_has_one_item_per_track(self):
        queue = self.result["renderQueue"]
        self.assertEqual(len(queue), 3)
        for i, item in enumerate(queue, start=1):
            self.assertEqual(item["comp"], f"PL_SLOT_{i:02d}")
            self.assertEqual(item["settings"], "Best Settings")
            self.assertEqual(item["module"], "Lossless")
            self.assertEqual(item["output"], f"/proj/work/segments/0{i}.mov")

    def test_saves_to_new_file_and_closes(self):
        self.assertEqual(self.result["savedTo"], "/proj/work/ae/demo.aep")
        self.assertTrue(self.result["closed"])


@unittest.skipUnless(NODE, "node 가 없어 ExtendScript 검증을 건너뜁니다")
class TestBuildProjectFull(unittest.TestCase):
    """full 모드: 메인 컴프에 슬롯을 배치하고 마스터 오디오를 얹는다."""

    @classmethod
    def setUpClass(cls):
        cls.result = run_harness("build-full", "build_project.jsx")
        cls.main = cls.result["main"]
        cls.names = [l["name"] for l in cls.main["layers"]]

    def test_places_every_segment_including_repeats(self):
        slots = [n for n in self.names if n.startswith("PL_SLOT")]
        self.assertEqual(len(slots), 6)  # 3곡 × 2회

    def test_placements_are_in_chronological_order(self):
        starts = [l["startTime"] for l in self.main["layers"]
                  if l["name"].startswith("PL_SLOT")]
        self.assertEqual(starts, sorted(starts))

    def test_master_audio_replaces_scratch_track(self):
        audio = next(l for l in self.main["layers"] if l["name"] == "AUDIO")
        self.assertEqual(audio["source"], "/proj/out/master.wav")
        self.assertEqual(audio["inPoint"], 0)
        self.assertAlmostEqual(audio["outPoint"], self.main["duration"], places=6)

    def test_template_own_layers_survive(self):
        # 배경·로고 같은 레이어는 재빌드 후에도 남아 있어야 한다.
        self.assertIn("BACKGROUND", self.names)
        self.assertIn("CHANNEL LOGO", self.names)

    def test_stale_slot_instances_are_removed(self):
        # 원본 MAIN 에 있던 "SLOT 1" … "SLOT 13" 인스턴스는 사라져야 한다.
        self.assertFalse([n for n in self.names if n.startswith("SLOT ")])

    def test_slots_sit_above_background_and_below_overlays(self):
        # AE 의 layers.add() 는 맨 위에 넣기 때문에, 되돌려놓지 않으면
        # 로고가 이미지에 가려진다.
        slots = [i for i, n in enumerate(self.names) if n.startswith("PL_SLOT")]
        self.assertLess(self.names.index("CHANNEL LOGO"), min(slots))
        self.assertGreater(self.names.index("BACKGROUND"), max(slots))

    def test_render_queue_has_only_the_main_comp(self):
        queue = self.result["renderQueue"]
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["comp"], "PL_MAIN")
        self.assertEqual(queue[0]["output"], "/proj/work/ae/demo.mov")


@unittest.skipUnless(NODE, "node 가 없어 ExtendScript 검증을 건너뜁니다")
class TestBuildProjectExistingComps(unittest.TestCase):
    """곡별 컴프를 손으로 만들어 둔 템플릿.

    레이어 이름이 곡 제목·이미지 파일명이라 고정 이름으로 찾을 수 없고,
    슬롯 컴프를 복제하는 대신 기존 컴프의 내용만 바꿔야 한다.
    """

    # 인트로("Change - Things 21")는 곡이 아니라 목록에 없다.
    COMPS = ["Change - Things", "Change - Things 2", "Change - Things 3"]
    TITLES = ["Neon Rain", "Late Transfer", "Blue Hour"]

    @classmethod
    def setUpClass(cls):
        cls.result = run_harness("build-existing", "build_project.jsx")
        cls.main = cls.result["main"]
        cls.names = [l["name"] for l in cls.main["layers"]]

    def test_edits_existing_comps_instead_of_duplicating(self):
        built = [c["name"] for c in self.result["built"]]
        self.assertEqual(sorted(built), sorted(self.COMPS))
        self.assertFalse([n for n in built if n.startswith("PL_SLOT")])

    def test_image_selector_finds_layer_by_kind(self):
        # "@still" 로 찾는다. 레이어 이름은 이미지 파일명이라 고정돼 있지 않다.
        by_name = {c["name"]: c for c in self.result["built"]}
        for i, comp_name in enumerate(self.COMPS, start=1):
            stills = [l for l in by_name[comp_name]["layers"]
                      if str(l["source"] or "").startswith("/proj/drop/images/")]
            self.assertEqual(len(stills), 1, comp_name)
            self.assertEqual(stills[0]["source"], f"/proj/drop/images/0{i}.png")

    def test_title_selector_finds_text_layer_by_kind(self):
        by_name = {c["name"]: c for c in self.result["built"]}
        for comp_name, title in zip(self.COMPS, self.TITLES):
            texts = [l["text"] for l in by_name[comp_name]["layers"]
                     if l["text"] is not None]
            self.assertIn(title, texts)

    def test_extra_still_is_left_alone_and_reported(self):
        # 정리 안 된 여분 스틸이 있는 컴프는 건드리지 않고 경고만 남긴다.
        # 시나리오에서 세 번째 컴프에만 여분 스틸이 있다.
        leftover = [c for c in self.result["built"]
                    if c["name"] == self.COMPS[2]][0]
        sources = [l["source"] for l in leftover["layers"]]
        self.assertIn("u3887476322_leftover_df845f19.png", sources)
        self.assertTrue(
            [line for line in self.result["log"]
             if "still 레이어가 2개" in line],
            "여분 스틸에 대한 경고가 없습니다",
        )

    def test_master_audio_replaces_episode_track(self):
        audio = next(l for l in self.main["layers"] if l["name"] == "ep07_full.wav")
        self.assertEqual(audio["source"], "/proj/drop/master.wav")
        self.assertAlmostEqual(audio["outPoint"], self.main["duration"], places=4)

    def test_main_overlays_survive(self):
        for name in ("LOGO.png", "AUDIO SPECTRUM", "146383_overlay.mp4",
                     "Shape Layer 1", "Song Title", "tagline"):
            self.assertIn(name, self.names)

    def test_slots_go_to_the_bottom_of_the_stack(self):
        # 이 템플릿은 곡 컴프가 맨 아래에 깔리고 그 위에 로고·스펙트럼·텍스트가
        # 얹힌다. AE 의 layers.add() 는 맨 위에 넣으므로 되돌려야 한다.
        slots = [i for i, n in enumerate(self.names) if n.startswith("Change")]
        overlays = [self.names.index(n) for n in
                    ("LOGO.png", "AUDIO SPECTRUM", "Song Title", "tagline")]
        self.assertLess(max(overlays), min(slots))

    def test_slots_are_placed_at_cue_times(self):
        starts = [l["startTime"] for l in self.main["layers"]
                  if l["name"].startswith("Change")]
        self.assertEqual(starts, sorted(starts))
        self.assertAlmostEqual(starts[0], 0.0, places=6)

    def test_intro_comp_is_left_completely_alone(self):
        """곡이 아닌 컴프(인트로)는 목록에 없으므로 손대면 안 된다.

        메인에서 걷어내지도, 이미지를 바꾸지도, 타이밍을 옮기지도 않아야
        한다. 손으로 맞춰둔 인트로가 그대로 남아야 하기 때문이다.
        """
        self.assertIn("Change - Things 21", self.names)
        intro = next(l for l in self.main["layers"]
                     if l["name"] == "Change - Things 21")
        self.assertEqual(intro["startTime"], 0)
        self.assertEqual(intro["outPoint"], 12.0)
        self.assertEqual(
            [l["source"] for l in self.result["intro"]["layers"]],
            ["intro_art.png"],
        )

    def test_intro_stays_above_the_song_comps(self):
        self.assertLess(self.names.index("Change - Things 21"),
                        min(i for i, n in enumerate(self.names)
                            if n in self.COMPS))

    def test_renders_the_main_comp_only(self):
        queue = self.result["renderQueue"]
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["comp"], "PL_MAIN")


@unittest.skipUnless(NODE, "node 가 없어 ExtendScript 검증을 건너뜁니다")
class TestDumpTemplate(unittest.TestCase):
    """AE 에서 직접 돌리는 단독 구조 리포트 스크립트."""

    @classmethod
    def setUpClass(cls):
        cls.report = run_harness("dump", "dump_template.jsx")["report"]

    def test_report_was_written(self):
        self.assertIsNotNone(self.report)

    def test_lists_every_comp(self):
        for name in ("MAIN", "SLOT"):
            self.assertIn(name, self.report)

    def test_counts_slot_usage(self):
        # SLOT 이 MAIN 안에서 13번 쓰인다는 걸 알려줘야 구조를 파악할 수 있다.
        self.assertIn("MAIN×13", self.report)

    def test_flags_keyframed_scale(self):
        # 어떤 레이어에 모션이 걸려 있는지 알아야 안 건드릴 걸 판단할 수 있다.
        self.assertIn("스케일 키프레임", self.report)

    def test_shows_placeholder_text(self):
        self.assertIn("TRACK TITLE", self.report)

    def test_suggests_config_mapping(self):
        for line in ('slot_comp   = "SLOT"',
                     'image_layer = "IMAGE"',
                     'title_layer = "TITLE"',
                     'audio_layer = "AUDIO"'):
            self.assertIn(line, self.report)

    def test_lists_render_templates(self):
        self.assertIn("Best Settings", self.report)
        self.assertIn("Lossless", self.report)


@unittest.skipUnless(NODE, "node 가 없어 ExtendScript 검증을 건너뜁니다")
class TestExtendScriptCompatibility(unittest.TestCase):
    """ExtendScript 는 ES3 수준이라 최신 문법을 쓸 수 없다."""

    FORBIDDEN = [
        (r"\b(?:let|const)\s+\w", "let/const"),
        (r"=>", "화살표 함수"),
        (r"\bJSON\s*\.", "JSON 전역"),
        (r"\.forEach\s*\(", "Array.forEach"),
        (r"\.map\s*\(", "Array.map"),
        (r"\.trim\s*\(", "String.trim"),
        (r"`", "템플릿 리터럴"),
    ]

    def test_no_modern_syntax(self):
        import re

        for script in sorted(SCRIPTS.glob("*.jsx")):
            src = script.read_text(encoding="utf-8")
            code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
            code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
            for pattern, label in self.FORBIDDEN:
                match = re.search(pattern, code)
                if match:
                    line = code[: match.start()].count("\n") + 1
                    self.fail(f"{script.name}:{line} 에서 {label} 사용 — "
                              f"ExtendScript 에서 동작하지 않습니다")

    def test_scripts_parse(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            for script in sorted(SCRIPTS.glob("*.jsx")):
                # node --check 는 .jsx 확장자를 안 받으므로 .js 로 복사.
                copy = Path(tmp) / (script.stem + ".js")
                copy.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
                proc = subprocess.run([NODE, "--check", str(copy)],
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0,
                                 f"{script.name} 문법 오류:\n{proc.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)


@unittest.skipUnless(NODE, "node 가 없어 ExtendScript 검증을 건너뜁니다")
class TestSwapImagesScript(unittest.TestCase):
    """AE 에서 바로 실행하는 이미지 교체 스크립트.

    설치나 설정 없이 쓰는 경로라, 이미지가 엉뚱한 곡에 붙으면 조용히
    잘못된 영상이 나온다. 매칭 결과를 직접 확인한다.
    """

    @classmethod
    def setUpClass(cls):
        harness = ROOT / "tests" / "ae_mock" / "swap_harness.mjs"
        proc = subprocess.run(
            [NODE, str(harness), str(SCRIPTS / "swap_images.jsx")],
            capture_output=True, text=True, errors="replace",
        )
        if proc.returncode != 0:
            raise AssertionError(f"하네스 실행 실패:\n{proc.stderr[-3000:]}")
        cls.result = json.loads(proc.stdout)
        cls.rows = cls.result["rows"]
        cls.mapped = [r for r in cls.rows if r["image"]]

    def test_images_are_ordered_by_leading_number(self):
        # 폴더에서 읽은 순서는 뒤죽박죽이다. "10-*" 이 "2-*" 보다 뒤여야 한다.
        numbers = [int(r["image"].split("-")[0].split(".")[0]) for r in self.mapped]
        self.assertEqual(numbers, sorted(numbers))
        self.assertEqual(numbers, list(range(1, 14)))

    def test_intro_is_excluded_by_its_short_placement(self):
        intro = self.rows[0]
        self.assertEqual(intro["comp"], "Intro")
        self.assertEqual(intro["image"], "")
        self.assertIn("길이", intro["why"])

    def test_disabled_leftover_is_excluded(self):
        stale = self.rows[-1]
        self.assertEqual(stale["image"], "")
        self.assertIn("꺼져", stale["why"])

    def test_every_song_gets_exactly_one_image(self):
        self.assertEqual(len(self.mapped), 13)
        self.assertEqual(len({r["image"] for r in self.mapped}), 13)

    def test_songs_are_listed_in_timeline_order(self):
        names = [r["comp"] for r in self.mapped]
        self.assertEqual(names[0], "Change - Things")
        self.assertEqual(names[-1], "Change - Things 13")

    def test_status_line_reports_both_counts(self):
        self.assertIn("13개", self.result["status"])
        self.assertIn("13장", self.result["status"])


@unittest.skipUnless(NODE, "node 가 없어 ExtendScript 검증을 건너뜁니다")
class TestFitFromTracklist(unittest.TestCase):
    """메모장 트랙리스트로 곡 컴프를 배치하고 제목을 바꾸는 스크립트.

    지난 회차 배치가 남아 있는 상태에서 이번 회차 트랙리스트를 먹인다.
    """

    # 실제 메모장은 트랙리스트만 있지 않다. 설명란 초안이라 훅 문장,
    # 머리글, "total runtime", 크레딧, 댓글 초안이 같이 들어 있다.
    TRACKLIST = """[여기에 첫 두 줄 훅 문장 — 예: chill neo soul & r&b for late nights]

tracklist
0:00 Nothing to Report
3:41 Two Sugars
7:31 Whatever You're About to Say
11:31 So Far So Good
15:42 Better Than the Rumor
20:01 Slow Is a Choice
24:30 Both of Us Were Right
28:52 Six Months Late
33:01 Thank You and I'm Sorry
36:59 Nothing You Did
40:49 What the Dark Is For
44:36 Something Green
48:05 Anytime You Want

total runtime: 52:44

all music & artwork created by warm tape society

[핀 댓글용 질문 초안: which track is your 3am pick?]
"""
    AUDIO_END = 3164.0

    TITLES = [
        "Nothing to Report", "Two Sugars", "Whatever You're About to Say",
        "So Far So Good", "Better Than the Rumor", "Slow Is a Choice",
        "Both of Us Were Right", "Six Months Late", "Thank You and I'm Sorry",
        "Nothing You Did", "What the Dark Is For", "Something Green",
        "Anytime You Want",
    ]

    @classmethod
    def run_script(cls, extra=0, kind=""):
        import tempfile

        tmp = Path(tempfile.mkdtemp()) / "description_draft.txt"
        tmp.write_text(cls.TRACKLIST, encoding="utf-8")
        harness = ROOT / "tests" / "ae_mock" / "tracklist_harness.mjs"
        argv = [NODE, str(harness), str(SCRIPTS / "fit_from_tracklist.jsx"),
                str(tmp), str(extra)]
        if kind:
            argv.append(kind)
        proc = subprocess.run(argv, capture_output=True, text=True,
                              errors="replace")
        if proc.returncode != 0:
            raise AssertionError(f"하네스 실행 실패:\n{proc.stderr[-3000:]}")
        return json.loads(proc.stdout)

    @classmethod
    def setUpClass(cls):
        cls.result = cls.run_script()
        cls.fps = cls.result["fps"]
        cls.songs = [a for a in cls.result["applied"] if a["comp"] != "Intro"]

    def test_every_song_gets_its_title(self):
        titles = [a["title"] for a in self.songs]
        self.assertEqual(titles[0], "Nothing to Report")
        self.assertEqual(titles[-1], "Anytime You Want")
        self.assertEqual(len(titles), 13)
        self.assertFalse([t for t in titles if t and t.startswith("지난회차")])

    def test_prose_lines_are_not_treated_as_tracks(self):
        """훅 문장·머리글·크레딧·댓글 초안이 곡으로 잡히면 전부 밀린다.

        줄 맨 앞에 시각이 있는 줄만 곡으로 본다.
        """
        titles = [a["title"] for a in self.songs]
        for junk in ("tracklist", "total runtime",
                     "all music & artwork created by warm tape society"):
            self.assertFalse([t for t in titles if t and junk in t], junk)
        self.assertFalse([t for t in titles if t and t.startswith("[")])

    def test_trailing_time_is_not_a_track_start(self):
        # "total runtime: 52:44" 는 시각이 줄 끝에 있으므로 곡이 아니다.
        self.assertNotIn(3164.0, [round(a["start"], 3) for a in self.songs])

    def test_selection_falls_back_to_the_track_count(self):
        # 지난 회차 배치라 길이가 제각각이라 구조 추측이 빗나간다.
        # 그때는 메모장에 적힌 곡 수를 더 믿는다.
        self.assertEqual(len(self.songs), 13)

    def test_starts_come_from_the_tracklist(self):
        self.assertAlmostEqual(self.songs[0]["start"], 0.0, places=3)
        # 3:41 = 221초, 프레임 경계로 올림된 값
        self.assertAlmostEqual(self.songs[1]["start"], 221.0, delta=1 / self.fps)
        # 48:05 = 2885초
        self.assertAlmostEqual(self.songs[-1]["start"], 2885.0, delta=1 / self.fps)

    def test_every_placement_lands_on_a_frame(self):
        for song in self.songs:
            for value in (song["start"], song["out"]):
                frames = value * self.fps
                self.assertAlmostEqual(frames, round(frames), places=6,
                                       msg=f"{song['comp']} 이 프레임에 안 맞음")

    def test_songs_are_contiguous(self):
        for a, b in zip(self.songs, self.songs[1:]):
            self.assertAlmostEqual(a["out"], b["start"], places=9)

    def test_last_song_runs_to_the_end_of_the_audio(self):
        # 오디오 길이까지 이어지되 프레임 경계로 올림된다.
        self.assertGreaterEqual(self.songs[-1]["out"], self.AUDIO_END)
        self.assertLess(self.songs[-1]["out"], self.AUDIO_END + 1 / self.fps)

    def test_intro_is_left_alone(self):
        intro = [a for a in self.result["applied"] if a["comp"] == "Intro"][0]
        self.assertEqual(intro["start"], 0)
        self.assertEqual(intro["out"], 11)

    def test_status_names_the_audio_it_measured_against(self):
        self.assertIn("ep08_full.wav", self.result["status"])
        self.assertIn("13곡", self.result["status"])
        self.assertNotIn("개수가 다릅니다", self.result["status"])

    def _titled(self, result):
        return [a for a in result["applied"] if a["title"] in self.TITLES]

    def test_leftover_comps_do_not_take_titles(self):
        """정리 안 된 잔재 컴프가 섞여 있어도 곡 수만큼만 처리한다.

        지난 회차 잔재가 남은 프로젝트에서 실제로 겪은 상황이다.
        """
        for extra in (1, 3, 6):
            with self.subTest(잔재=extra):
                result = self.run_script(extra)
                titled = self._titled(result)
                self.assertEqual(len(titled), 13)
                self.assertFalse([a["comp"] for a in titled
                                  if a["comp"].startswith(("잔재", "Intro"))])

    def test_disabled_leftovers_are_dropped_even_when_long(self):
        # 꺼진 레이어는 길어도 곡이 아니다.
        result = self.run_script(3, "off")
        titled = self._titled(result)
        self.assertEqual(len(titled), 13)
        self.assertFalse([a["comp"] for a in titled
                          if a["comp"].startswith("잔재")])
