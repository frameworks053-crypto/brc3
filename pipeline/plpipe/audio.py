"""오디오 마스터링: 곡별 라우드니스 정규화 → 타임라인 계산 → 이어붙이기."""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import ffmpeg
from .project import Project, Track


# NTSC 계열 프레임레이트는 실제로 1000/1001 배다. 설정에 29.97 처럼
# 반올림한 값을 적어도 정확한 분수로 계산해야 긴 영상에서 어긋나지 않는다.
_NTSC = {23.976: 24000 / 1001, 29.97: 30000 / 1001,
         47.952: 48000 / 1001, 59.94: 60000 / 1001}


def exact_fps(fps: float) -> float:
    """29.97 같은 반올림 표기를 정확한 NTSC 분수로 바꾼다."""
    for rounded, exact in _NTSC.items():
        if abs(fps - rounded) < 0.01:
            return exact
    return fps


def snap_up(seconds: float, fps: float) -> float:
    """프레임 경계로 올림한다. AE 는 컴프 길이를 프레임 단위로만 잡는다."""
    if fps <= 0:
        return seconds
    fps = exact_fps(fps)
    return math.ceil(seconds * fps - 1e-6) / fps


@dataclass(frozen=True)
class Segment:
    """펼쳐진 재생 순서상의 한 구간."""

    order: int      # 전체 순서 (0부터)
    pass_no: int    # 0 = 1회차, 1 = 반복
    index: int      # 트랙 번호
    title: str
    start: float    # 영상 기준 시작 시각(초)
    end: float      # 영상 기준 끝 시각(초)
    gap_after: float = 0.0  # 뒤에 붙는 무음(양수) 또는 크로스페이드(음수)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def step(self) -> float:
        """다음 구간 시작까지의 간격. 항상 프레임 경계에 맞는다."""
        return self.duration + self.gap_after


@dataclass(frozen=True)
class Timeline:
    segments: tuple[Segment, ...]
    total: float          # lead_out 까지 포함한 전체 길이
    lead_in: float
    lead_out: float
    gap: float            # 설정상의 요청값 (실제 간격은 Segment.gap_after)
    crossfade: float
    fps: float = 0.0

    @property
    def tail_gap(self) -> float:
        """마지막 구간 뒤에 붙은 여분. 영상 조립 때 잘라내야 한다."""
        return self.segments[-1].gap_after if self.segments else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": [asdict(s) for s in self.segments],
            "total": self.total,
            "lead_in": self.lead_in,
            "lead_out": self.lead_out,
            "gap": self.gap,
            "crossfade": self.crossfade,
            "fps": self.fps,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Timeline":
        return cls(
            segments=tuple(Segment(**s) for s in d.get("segments", [])),
            total=float(d.get("total", 0.0)),
            lead_in=float(d.get("lead_in", 0.0)),
            lead_out=float(d.get("lead_out", 0.0)),
            gap=float(d.get("gap", 0.0)),
            crossfade=float(d.get("crossfade", 0.0)),
            fps=float(d.get("fps", 0.0)),
        )


def build_timeline(
    items: Sequence[tuple[int, Track]],
    *,
    fps: float = 0.0,
    gap: float = 0.0,
    crossfade: float = 0.0,
    lead_in: float = 0.0,
    lead_out: float = 0.0,
) -> Timeline:
    """재생 순서와 곡 길이로부터 각 구간의 시작/끝 시각을 계산한다.

    간격과 크로스페이드는 부호만 다른 같은 값이다. 다음 곡의 시작은
    `이전 곡 시작 + 이전 곡 길이 + advance` 이고, advance 는 간격이면 +gap,
    크로스페이드면 -crossfade 다. crossfade > 0 이면 gap 은 무시한다.

    fps 를 주면 '한 구간의 시작에서 다음 구간의 시작까지'가 항상 정수
    프레임이 되도록 간격을 최대 한 프레임까지 늘린다. AE 는 컴프 길이를
    프레임 단위로만 잡기 때문에, 이렇게 맞춰두지 않으면 곡마다 반 프레임씩
    오차가 쌓여 26구간 뒤에는 영상이 오디오보다 1초 가까이 밀린다.
    간격이 곡마다 최대 40ms 남짓 달라지지만 귀로는 구분되지 않는다.
    """
    if crossfade > 0:
        advance = -crossfade
        gap = 0.0
    else:
        advance = gap
        crossfade = 0.0

    lead_in = snap_up(lead_in, fps)
    lead_out = snap_up(lead_out, fps)

    segments: list[Segment] = []
    cursor = lead_in
    for order, (pass_no, track) in enumerate(items):
        if track.duration is None:
            raise ValueError(
                f"트랙 {track.index} 의 길이를 모릅니다. `plpipe audio` 를 먼저 실행하세요."
            )
        if crossfade > 0 and crossfade >= track.duration:
            raise ValueError(
                f"크로스페이드({crossfade}s)가 트랙 {track.index} 길이"
                f"({track.duration:.1f}s)보다 깁니다."
            )
        step = snap_up(track.duration + advance, fps)
        start = cursor
        end = start + track.duration
        # 여기서 반올림하면 프레임 정렬이 깨진다. step 이 정확히 정수 프레임이어야
        # AE 가 잡는 컴프 길이와 오디오 타임라인이 어긋나지 않는다. manifest 는
        # 사람이 아니라 기계가 읽는 파일이므로 자릿수는 그대로 둔다.
        segments.append(
            Segment(
                order=order,
                pass_no=pass_no,
                index=track.index,
                title=track.display_title,
                start=start,
                end=end,
                gap_after=step - track.duration,
            )
        )
        cursor = start + step

    total = (segments[-1].end if segments else lead_in) + lead_out
    return Timeline(
        segments=tuple(segments),
        total=total,
        lead_in=lead_in,
        lead_out=lead_out,
        gap=gap,
        crossfade=crossfade,
        fps=fps,
    )


def build_timeline_from_cues(
    items: Sequence[tuple[int, Track]],
    cues: Sequence[float],
    total: float,
    *,
    fps: float = 0.0,
) -> Timeline:
    """이미 합쳐진 마스터 오디오의 곡 시작 시각으로 타임라인을 만든다.

    파이프라인이 오디오를 합치지 않으므로 간격을 계산할 게 없다. 각 구간은
    '이 곡이 시작하는 순간부터 다음 곡이 시작하는 순간까지' 이고, 첫 구간은
    0 초까지, 마지막 구간은 끝까지 늘려서 화면이 비는 곳이 없게 한다.
    경계는 프레임에 맞춘다.
    """
    if len(cues) != len(items):
        raise ValueError(
            f"곡 경계 {len(cues)}개가 구간 {len(items)}개와 맞지 않습니다."
        )

    edges = [snap_up(c, fps) for c in cues]
    edges[0] = 0.0
    for i in range(1, len(edges)):
        if edges[i] <= edges[i - 1]:
            raise ValueError(
                f"{i + 1}번 곡 경계({cues[i]:.2f}s)가 앞 구간보다 앞서거나 같습니다."
            )
    end_total = snap_up(total, fps)

    segments: list[Segment] = []
    for order, (pass_no, track) in enumerate(items):
        start = edges[order]
        end = edges[order + 1] if order + 1 < len(edges) else end_total
        segments.append(
            Segment(
                order=order,
                pass_no=pass_no,
                index=track.index,
                title=track.display_title,
                start=start,
                end=end,
                gap_after=0.0,
            )
        )

    return Timeline(
        segments=tuple(segments), total=end_total,
        lead_in=0.0, lead_out=0.0, gap=0.0, crossfade=0.0, fps=fps,
    )


# ── 정규화 ──────────────────────────────────────────────────
def _trim_chain(threshold_db: float | None) -> str:
    """앞뒤 무음 제거 필터. 뒤쪽은 뒤집어서 앞을 자르고 되돌린다."""
    if threshold_db is None:
        return ""
    one = (
        f"silenceremove=start_periods=1:start_silence=0.05"
        f":start_threshold={threshold_db}dB:detection=peak"
    )
    return f"{one},areverse,{one},areverse"


def normalize_track(
    src: Path,
    dst: Path,
    *,
    target_lufs: float,
    true_peak: float,
    loudness_range: float,
    sample_rate: int,
    trim_silence_db: float | None,
) -> float:
    """2패스 loudnorm 으로 정규화한 wav 를 만들고, 결과 길이(초)를 돌려준다."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    trim = _trim_chain(trim_silence_db)
    prefix = f"{trim}," if trim else ""

    measured = ffmpeg.measure_loudness_chain(src, prefix)

    loudnorm = (
        f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={loudness_range}"
        f":measured_I={measured['input_i']}"
        f":measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}"
        f":measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}"
        f":linear=true:print_format=summary"
    )
    # loudnorm 2패스는 내부적으로 192kHz 로 올리므로 반드시 되돌린다.
    chain = f"{prefix}{loudnorm},aresample={sample_rate}:resampler=soxr"

    ffmpeg.run([
        "-i", str(src),
        "-af", chain,
        "-ar", str(sample_rate),
        "-ac", "2",
        "-c:a", "pcm_s16le",
        str(dst),
    ])
    return ffmpeg.duration(dst)


def normalize_all(project: Project, cfg: dict[str, Any], *, force: bool = False) -> None:
    """모든 트랙을 정규화하고 manifest 에 길이를 기록한다."""
    norm_dir = project.work / "norm"
    for track in project.tracks:
        src = project.path(track.audio)
        if src is None or not src.is_file():
            raise FileNotFoundError(
                f"트랙 {track.index} 의 오디오 파일이 없습니다. `plpipe ingest` 를 실행하세요."
            )
        dst = norm_dir / f"{track.index:02d}.wav"
        if dst.is_file() and not force and track.duration:
            print(f"  [{track.index:02d}] 이미 정규화됨 — 건너뜀")
            continue
        print(f"  [{track.index:02d}] {track.display_title} 정규화 중…")
        track.duration = round(
            normalize_track(
                src,
                dst,
                target_lufs=float(cfg.get("target_lufs", -14.0)),
                true_peak=float(cfg.get("true_peak", -1.0)),
                loudness_range=float(cfg.get("loudness_range", 11.0)),
                sample_rate=int(cfg.get("sample_rate", 48000)),
                trim_silence_db=cfg.get("trim_silence_db"),
            ),
            3,
        )
        track.normalized = project.rel(dst)
    project.save()


# ── 이어붙이기 ──────────────────────────────────────────────
def _silence(path: Path, seconds: float, sample_rate: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run([
        "-f", "lavfi",
        "-i", f"anullsrc=r={sample_rate}:cl=stereo",
        "-t", f"{seconds:.6f}",
        "-c:a", "pcm_s16le",
        str(path),
    ])
    return path


def _concat_demuxer(parts: Iterable[Path], list_file: Path, dst: Path) -> None:
    lines = []
    for part in parts:
        # concat 디먹서 문법상 작은따옴표는 이스케이프해야 한다.
        escaped = str(part.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ffmpeg.run([
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(dst),
    ])


def concat_master(
    project: Project,
    timeline: Timeline,
    items: Sequence[tuple[int, Track]],
    *,
    sample_rate: int,
    master_format: str = "wav",
) -> Path:
    """정규화된 트랙들을 타임라인대로 이어붙여 마스터 오디오를 만든다."""
    work = project.work / "concat"
    work.mkdir(parents=True, exist_ok=True)
    dst = project.out / f"master.{master_format}"
    dst.parent.mkdir(parents=True, exist_ok=True)

    norm_paths = [project.path(t.normalized) for _, t in items]
    for path, (_, track) in zip(norm_paths, items):
        if path is None or not path.is_file():
            raise FileNotFoundError(f"정규화 파일이 없습니다: 트랙 {track.index}")

    if timeline.crossfade > 0:
        _concat_crossfade(norm_paths, timeline, dst, sample_rate)  # type: ignore[arg-type]
    else:
        # 간격은 구간마다 최대 한 프레임씩 다르므로(프레임 정렬) 무음 파일을
        # 길이별로 만들어 재사용한다.
        parts: list[Path] = []
        cache: dict[int, Path] = {}

        def silence_for(seconds: float) -> Path | None:
            key = round(seconds * 1000)
            if key <= 0:
                return None
            if key not in cache:
                cache[key] = _silence(work / f"gap_{key}.wav", key / 1000, sample_rate)
            return cache[key]

        lead = silence_for(timeline.lead_in)
        if lead:
            parts.append(lead)
        for i, (path, seg) in enumerate(zip(norm_paths, timeline.segments)):
            parts.append(path)  # type: ignore[arg-type]
            if i < len(norm_paths) - 1:
                gap_file = silence_for(seg.gap_after)
                if gap_file:
                    parts.append(gap_file)
        tail = silence_for(timeline.lead_out)
        if tail:
            parts.append(tail)
        _concat_demuxer(parts, work / "concat.txt", dst)

    return dst


def _concat_crossfade(
    paths: Sequence[Path], timeline: Timeline, dst: Path, sample_rate: int
) -> None:
    """acrossfade 를 체인으로 걸어 이어붙인다.

    크로스페이드 길이는 구간마다 조금씩 다를 수 있다(프레임 정렬). acrossfade
    는 이어붙이기마다 따로 걸리므로 구간별 값을 그대로 쓸 수 있다.
    """
    inputs: list[str] = []
    for path in paths:
        inputs += ["-i", str(path)]

    chain: list[str] = []
    current = "[0:a]"
    for i in range(1, len(paths)):
        label = f"[x{i}]"
        # i 번째 이어붙이기에는 앞 구간(i-1)의 gap_after 가 적용된다.
        overlap = -timeline.segments[i - 1].gap_after
        chain.append(
            f"{current}[{i}:a]acrossfade=d={overlap:.6f}:c1=tri:c2=tri{label}"
        )
        current = label
    pad = []
    if timeline.lead_in > 0:
        pad.append(f"adelay={int(timeline.lead_in * 1000)}:all=1")
    if timeline.lead_out > 0:
        pad.append(f"apad=pad_dur={timeline.lead_out}")
    pad.append(f"aresample={sample_rate}")
    chain.append(f"{current}{','.join(pad)}[out]")

    ffmpeg.run([
        *inputs,
        "-filter_complex", ";".join(chain),
        "-map", "[out]",
        "-ar", str(sample_rate),
        "-ac", "2",
        "-c:a", "pcm_s16le",
        str(dst),
    ])
