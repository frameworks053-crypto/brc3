"""ffmpeg / After Effects 없이 검증할 수 있는 순수 로직 테스트."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plpipe.audio import Timeline, build_timeline  # noqa: E402
from plpipe.ffmpeg import parse_loudnorm_json  # noqa: E402
from plpipe.metadata import (  # noqa: E402
    build_chapters,
    render_title,
    timestamp,
    validate_chapters,
)
from plpipe.project import Project, Track, parse_index, scan_media, slugify  # noqa: E402
from plpipe.providers.base import dig  # noqa: E402
from plpipe.providers.image import _closest_openai_size, _round_to  # noqa: E402


def tracks(durations: list[float]) -> list[Track]:
    return [
        Track(index=i + 1, title=f"Track {i + 1}", duration=d)
        for i, d in enumerate(durations)
    ]


def sequence(ts: list[Track], repeat: int) -> list[tuple[int, Track]]:
    return [(p, t) for p in range(repeat + 1) for t in ts]


class TestTimeline(unittest.TestCase):
    def test_gap_spacing(self):
        tl = build_timeline(sequence(tracks([10, 20, 30]), 0), gap=2.0)
        starts = [s.start for s in tl.segments]
        self.assertEqual(starts, [0.0, 12.0, 34.0])
        self.assertEqual(tl.total, 64.0)  # 마지막 뒤에는 간격이 없다

    def test_lead_in_and_out(self):
        tl = build_timeline(
            sequence(tracks([10, 20]), 0), gap=1.0, lead_in=3.0, lead_out=5.0
        )
        self.assertEqual([s.start for s in tl.segments], [3.0, 14.0])
        self.assertEqual(tl.total, 3 + 10 + 1 + 20 + 5)

    def test_repeat_doubles_sequence(self):
        ts = tracks([60.0] * 13)
        tl = build_timeline(sequence(ts, 1), gap=1.5, lead_in=2.0, lead_out=4.0)
        self.assertEqual(len(tl.segments), 26)
        # 2회차는 1회차와 같은 트랙 번호가 같은 순서로 반복된다.
        first = [s.index for s in tl.segments[:13]]
        second = [s.index for s in tl.segments[13:]]
        self.assertEqual(first, second)
        self.assertEqual([s.pass_no for s in tl.segments[13:]], [1] * 13)
        self.assertAlmostEqual(tl.total, 2 + 26 * 60 + 25 * 1.5 + 4)

    def test_crossfade_overlaps(self):
        tl = build_timeline(sequence(tracks([10, 20, 30]), 0), crossfade=2.0)
        self.assertEqual([s.start for s in tl.segments], [0.0, 8.0, 26.0])
        self.assertEqual(tl.gap, 0.0)  # 크로스페이드가 간격을 덮어쓴다

    def test_crossfade_longer_than_track_is_rejected(self):
        with self.assertRaises(ValueError):
            build_timeline(sequence(tracks([10, 1.0]), 0), crossfade=2.0)

    def test_missing_duration_is_rejected(self):
        ts = [Track(index=1, duration=None)]
        with self.assertRaises(ValueError):
            build_timeline(sequence(ts, 0))

    def test_roundtrip_through_dict(self):
        tl = build_timeline(sequence(tracks([10, 20]), 1), gap=1.5, lead_out=3.0)
        again = Timeline.from_dict(json.loads(json.dumps(tl.to_dict())))
        self.assertEqual(again, tl)


class TestAssemblyMath(unittest.TestCase):
    """render.assemble 이 클립을 잘라내고 덧대는 계산을 검증한다."""

    def _body_and_concat(self, durations, repeat, **kwargs):
        tl = build_timeline(sequence(tracks(durations), repeat), **kwargs)
        # render.assemble 과 같은 식.
        concat_len = sum(seg.step for seg in tl.segments)
        body = concat_len - tl.tail_gap
        return tl, body, concat_len

    def test_gap_body_matches_timeline(self):
        tl, body, concat_len = self._body_and_concat(
            [180.0, 200.0, 220.0], 1, gap=1.5, lead_in=2.0, lead_out=4.0
        )
        # 본문 + 앞뒤 여백 = 전체 길이
        self.assertAlmostEqual(body + tl.lead_in + tl.lead_out, tl.total, places=6)
        # 간격 모드에서는 이어붙인 결과가 본문보다 딱 간격 하나만큼 길다
        self.assertAlmostEqual(concat_len - body, tl.tail_gap, places=6)

    def test_crossfade_body_needs_padding(self):
        tl, body, concat_len = self._body_and_concat(
            [180.0, 200.0], 1, crossfade=3.0, lead_out=2.0
        )
        self.assertAlmostEqual(body + tl.lead_in + tl.lead_out, tl.total, places=6)
        # 크로스페이드 모드에서는 오히려 모자라므로 덧대야 한다
        self.assertAlmostEqual(body - concat_len, tl.crossfade, places=6)

    def test_realistic_13_track_hour_long_mix(self):
        # 3~4분대 곡 13개를 두 번 돌리는 실제 구성.
        durations = [181.3, 204.7, 195.1, 223.4, 168.9, 240.2, 199.8,
                     212.5, 187.6, 231.0, 176.4, 208.3, 219.7]
        tl, body, concat_len = self._body_and_concat(
            durations, 1, fps=24, gap=1.5, lead_in=2.0, lead_out=4.0
        )
        self.assertEqual(len(tl.segments), 26)
        self.assertGreater(tl.total, 3600)  # 1시간 넘음
        self.assertAlmostEqual(body + tl.lead_in + tl.lead_out, tl.total, places=6)


class TestFrameAlignment(unittest.TestCase):
    """AE 는 컴프 길이를 프레임 단위로만 잡으므로 구간 간격이 정수 프레임이어야 한다."""

    DURATIONS = [181.3, 204.7, 195.1, 223.4, 168.9, 240.2, 199.8,
                 212.5, 187.6, 231.0, 176.4, 208.3, 219.7]

    def test_every_step_is_a_whole_number_of_frames(self):
        fps = 24
        tl = build_timeline(sequence(tracks(self.DURATIONS), 1),
                            fps=fps, gap=1.5, lead_in=2.0, lead_out=4.0)
        for seg in tl.segments:
            frames = seg.step * fps
            self.assertAlmostEqual(frames, round(frames), places=6,
                                   msg=f"구간 {seg.order} 의 간격이 프레임에 안 맞음")

    def test_segment_starts_land_on_frames(self):
        fps = 24
        tl = build_timeline(sequence(tracks(self.DURATIONS), 1),
                            fps=fps, gap=1.5, lead_in=2.0, lead_out=4.0)
        for seg in tl.segments:
            frames = seg.start * fps
            self.assertAlmostEqual(frames, round(frames), places=5,
                                   msg=f"구간 {seg.order} 시작이 프레임에 안 맞음")

    def test_gap_grows_by_less_than_one_frame(self):
        fps = 24
        tl = build_timeline(sequence(tracks(self.DURATIONS), 1), fps=fps, gap=1.5)
        for seg in tl.segments:
            self.assertGreaterEqual(seg.gap_after, 1.5 - 1e-6)
            self.assertLess(seg.gap_after, 1.5 + 1 / fps)

    def test_same_track_gets_same_gap_in_both_passes(self):
        # segments 모드는 1회차 클립을 2회차에 그대로 재사용하므로
        # 같은 곡의 간격이 회차마다 달라지면 안 된다.
        tl = build_timeline(sequence(tracks(self.DURATIONS), 1), fps=24, gap=1.5)
        by_index: dict[int, float] = {}
        for seg in tl.segments:
            if seg.index in by_index:
                self.assertAlmostEqual(by_index[seg.index], seg.gap_after, places=9)
            by_index[seg.index] = seg.gap_after

    def test_crossfade_steps_also_align(self):
        fps = 30
        tl = build_timeline(sequence(tracks(self.DURATIONS), 1), fps=fps, crossfade=3.0)
        for seg in tl.segments:
            frames = seg.step * fps
            self.assertAlmostEqual(frames, round(frames), places=6)
            # 크로스페이드는 요청값보다 최대 한 프레임만큼 짧아진다.
            overlap = -seg.gap_after
            self.assertLessEqual(overlap, 3.0 + 1e-6)
            self.assertGreater(overlap, 3.0 - 1 / fps)

    def test_drift_without_alignment_would_be_significant(self):
        # 정렬을 끄면(fps=0) AE 가 각 컴프를 프레임으로 반올림하면서 오차가 쌓인다.
        # 이 테스트는 정렬이 실제로 해결하는 문제의 크기를 기록해 둔다.
        fps = 24
        loose = build_timeline(sequence(tracks(self.DURATIONS), 1), gap=1.5)
        drift = 0.0
        for seg in loose.segments:
            drift += abs(round(seg.step * fps) / fps - seg.step)
        self.assertGreater(drift, 0.2)  # 26구간 누적 오차 (0.2초 이상)

        tight = build_timeline(sequence(tracks(self.DURATIONS), 1), fps=fps, gap=1.5)
        aligned_drift = sum(
            abs(round(seg.step * fps) / fps - seg.step) for seg in tight.segments
        )
        self.assertAlmostEqual(aligned_drift, 0.0, places=6)


class TestMetadata(unittest.TestCase):
    def test_timestamp_floors_to_second(self):
        self.assertEqual(timestamp(0), "0:00")
        self.assertEqual(timestamp(65.9), "1:05")
        self.assertEqual(timestamp(3725), "1:02:05")
        self.assertEqual(timestamp(65.9, force_hours=True), "0:01:05")

    def test_first_chapter_is_forced_to_zero(self):
        tl = build_timeline(sequence(tracks([180.0] * 3), 0), gap=1.0, lead_in=5.0)
        chapters = build_chapters(tl)
        self.assertEqual(chapters[0].start, 0.0)
        self.assertEqual(chapters[1].start, 186.0)

    def test_repeat_pass_is_labelled(self):
        tl = build_timeline(sequence(tracks([180.0] * 3), 1), gap=1.0)
        chapters = build_chapters(tl, all_passes=True, mark_repeat=True)
        self.assertEqual(len(chapters), 6)
        self.assertTrue(chapters[3].label.endswith("(repeat)"))

    def test_first_pass_only(self):
        tl = build_timeline(sequence(tracks([180.0] * 3), 1), gap=1.0)
        self.assertEqual(len(build_chapters(tl, all_passes=False)), 3)

    def test_validation_catches_youtube_rules(self):
        tl = build_timeline(sequence(tracks([180.0, 5.0, 180.0]), 0), gap=1.0)
        problems = validate_chapters(build_chapters(tl), tl.total)
        self.assertTrue(any("10초 미만" in p for p in problems))

        short = build_chapters(build_timeline(sequence(tracks([180.0]), 0)))
        self.assertTrue(any("최소" in p for p in validate_chapters(short, 180.0)))

    def test_title_template(self):
        proj = Project(root=Path("/tmp/x"), slug="lofi-night",
                       vars={"mood": "Midnight Lofi", "activity": "study"},
                       tracks=tracks([180.0] * 13))
        tl = build_timeline(sequence(proj.tracks, 1), gap=1.5)
        title = render_title("{mood} — {n} songs to {activity} to · {duration}",
                             proj, tl)
        self.assertEqual(title, "Midnight Lofi — 13 songs to study to · 1:18:37")

    def test_unknown_placeholder_is_dropped(self):
        proj = Project(root=Path("/tmp/x"), slug="s", tracks=tracks([60.0] * 3))
        tl = build_timeline(sequence(proj.tracks, 0))
        self.assertEqual(render_title("{nope} {slug}", proj, tl), "s")


class TestProjectFiles(unittest.TestCase):
    def test_index_parsing(self):
        self.assertEqual(parse_index(Path("01 - Neon Rain.mp3")), 1)
        self.assertEqual(parse_index(Path("13_rain.png")), 13)
        self.assertEqual(parse_index(Path("7.png")), 7)
        self.assertIsNone(parse_index(Path("neon rain.mp3")))

    def test_slugify_keeps_hangul(self):
        self.assertEqual(slugify("Lofi 밤 Mix!"), "lofi-밤-mix")
        self.assertEqual(slugify("  !!!  "), "untitled")

    def test_scan_sorts_numerically_not_lexically(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ["2-b.mp3", "10-j.mp3", "1-a.mp3", "notes.txt", ".hidden.mp3"]:
                (root / name).write_bytes(b"")
            found = [p.name for p in scan_media(root, {".mp3"})]
            self.assertEqual(found, ["1-a.mp3", "2-b.mp3", "10-j.mp3"])

    def test_manifest_roundtrip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.create(Path(tmp), "demo", 13, {"mood": "night"})
            proj.tracks[0].title = "Neon Rain"
            proj.tracks[0].duration = 212.5
            proj.save()

            again = Project.load(proj.root)
            self.assertEqual(len(again.tracks), 13)
            self.assertEqual(again.tracks[0].title, "Neon Rain")
            self.assertEqual(again.tracks[0].duration, 212.5)
            self.assertEqual(again.vars["mood"], "night")
            self.assertTrue(again.drop_audio.is_dir())
            self.assertTrue(again.drop_images.is_dir())

    def test_sequence_expands_repeats(self):
        proj = Project(root=Path("/tmp/x"), slug="s", tracks=tracks([1.0] * 13))
        self.assertEqual(len(list(proj.sequence(1))), 26)
        self.assertEqual(len(list(proj.sequence(0))), 13)


class TestProviderHelpers(unittest.TestCase):
    def test_dig_tries_each_path(self):
        body = {"data": {"response": {"sunoData": [{"audioUrl": "u"}]}}}
        self.assertEqual(
            dig(body, "data.audio_url", "data.response.sunoData.0.audioUrl"), "u"
        )
        self.assertIsNone(dig(body, "nope.here"))
        self.assertEqual(dig(body, "nope", default="x"), "x")

    def test_flux_dimensions_snap_to_32(self):
        self.assertEqual(_round_to(2048, 32), 2048)
        self.assertEqual(_round_to(1150, 32), 1152)
        self.assertEqual(_round_to(10, 32), 32)

    def test_openai_size_picks_closest_ratio(self):
        self.assertEqual(_closest_openai_size(2048, 1152), "1536x1024")
        self.assertEqual(_closest_openai_size(1024, 1024), "1024x1024")
        self.assertEqual(_closest_openai_size(1080, 1920), "1024x1536")

    def test_loudnorm_parsing_handles_silence(self):
        stderr = (
            "some ffmpeg noise\n"
            '{ "input_i" : "-inf", "input_tp" : "-120.0", "input_lra" : "0.0",'
            ' "input_thresh" : "-inf", "target_offset" : "0.0" }'
        )
        measured = parse_loudnorm_json(stderr)
        self.assertEqual(measured["input_i"], -70.0)
        self.assertEqual(measured["input_tp"], -120.0)


class TestAEJob(unittest.TestCase):
    def test_job_embeds_as_valid_js_object(self):
        from plpipe.ae import AEJob

        job = AEJob(
            mode="segments",
            project="C:/t/템플릿.aep",
            save_as="C:/t/out.aep",
            names={"slot_comp": "SLOT"},
            video={"width": 1920, "height": 1080, "fps": 24},
            slots=[{"index": 1, "title": 'He said "hi"', "duration": 181.5}],
            main={},
            render={"settings": "Best Settings"},
        )
        # 파이썬 json 은 유효한 자바스크립트 객체 리터럴이므로 그대로 넣을 수 있다.
        payload = json.loads(job.to_json())
        self.assertEqual(payload["slots"][0]["title"], 'He said "hi"')
        self.assertEqual(payload["project"], "C:/t/템플릿.aep")
        self.assertNotIn("\\u", job.to_json())  # 한글이 이스케이프되지 않는다


if __name__ == "__main__":
    unittest.main(verbosity=2)
