"""이미 합쳐진 마스터 오디오에서 곡 경계를 찾아낸다.

오디오를 직접 믹스해서 넣는 경우, 파이프라인은 각 곡이 몇 초에 시작하는지
알아야 이미지 전환 시점과 챕터 타임스탬프를 맞출 수 있다.
ffmpeg 의 silencedetect 로 곡 사이 무음을 찾아 경계를 잡고, 곡별 원본
파일이 있으면 그 길이와 대조해 검증한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import ffmpeg

# silencedetect 가 stderr 로 뱉는 줄:
#   [silencedetect @ ...] silence_start: 123.456
#   [silencedetect @ ...] silence_end: 125.0 | silence_duration: 1.544
_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END_RE = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")


class CueError(RuntimeError):
    pass


@dataclass(frozen=True)
class Silence:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def middle(self) -> float:
        return (self.start + self.end) / 2


def find_silences(
    path: Path, *, threshold_db: float = -45.0, min_duration: float = 0.4
) -> list[Silence]:
    """무음 구간 목록. 파일 맨 앞/뒤의 무음도 포함된다."""
    proc = ffmpeg.run_capture([
        "-i", str(path),
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
        "-f", "null", "-",
    ])
    total = ffmpeg.duration(path)

    silences: list[Silence] = []
    pending: float | None = None
    for line in proc.splitlines():
        start = _START_RE.search(line)
        if start:
            pending = float(start.group(1))
            continue
        end = _END_RE.search(line)
        if end and pending is not None:
            silences.append(Silence(start=max(0.0, pending), end=float(end.group(1))))
            pending = None
    # 파일 끝까지 무음이면 silence_end 가 안 나온다.
    if pending is not None:
        silences.append(Silence(start=max(0.0, pending), end=total))
    return silences


def cues_from_silences(
    silences: Sequence[Silence], total: float, expected: int
) -> list[float]:
    """무음 구간에서 곡 시작 시각을 뽑는다.

    첫 곡은 리드인 무음 뒤에서 시작하고, 이후 곡들은 무음 구간의 중간을
    경계로 본다. 무음이 곡 수보다 많이 잡히면 긴 것부터 고른다.
    """
    if expected < 1:
        raise CueError("곡 수는 1 이상이어야 합니다.")

    lead_in = 0.0
    body = list(silences)
    # 0 초 근처에서 시작하는 무음은 리드인이지 곡 경계가 아니다.
    if body and body[0].start <= 0.05:
        lead_in = body[0].end
        body.pop(0)
    # 끝까지 이어지는 무음은 리드아웃.
    if body and abs(body[-1].end - total) <= 0.05:
        body.pop()

    needed = expected - 1
    if len(body) < needed:
        raise CueError(
            f"곡 경계를 {len(body)}개밖에 찾지 못했습니다 ({needed}개 필요). "
            "곡 사이 무음이 너무 짧거나 조용하지 않을 수 있습니다.\n"
            "`--threshold` 를 올리거나(-30 등) `--min-silence` 를 낮춰보세요."
        )
    # 긴 무음이 진짜 곡 경계일 가능성이 높다. 그중 시간순 앞에서부터 needed 개.
    chosen = sorted(
        sorted(body, key=lambda s: s.duration, reverse=True)[:needed],
        key=lambda s: s.start,
    )
    return [lead_in] + [s.middle for s in chosen]


def detect(
    master: Path,
    expected: int,
    *,
    threshold_db: float = -45.0,
    min_silence: float = 0.4,
) -> tuple[list[float], float]:
    """(곡 시작 시각 목록, 전체 길이) 를 돌려준다."""
    total = ffmpeg.duration(master)
    silences = find_silences(master, threshold_db=threshold_db,
                             min_duration=min_silence)
    return cues_from_silences(silences, total, expected), total


def compare_with_tracks(
    cues: Sequence[float], total: float, durations: Sequence[float | None]
) -> list[str]:
    """검출한 경계를 곡별 원본 길이와 대조해 이상한 곳을 알려준다."""
    problems: list[str] = []
    for i, expected in enumerate(durations):
        if expected is None:
            continue
        start = cues[i]
        end = cues[i + 1] if i + 1 < len(cues) else total
        measured = end - start
        # 경계는 무음 한가운데라서 곡 길이보다 간격만큼 길게 나온다.
        # 원본보다 짧으면 경계를 잘못 잡은 것이다.
        if measured < expected - 0.5:
            problems.append(
                f"{i + 1}번 곡: 검출 구간 {measured:.1f}s 가 원본 "
                f"{expected:.1f}s 보다 짧습니다. 경계를 잘못 잡았을 수 있습니다."
            )
        elif measured > expected + 10.0:
            problems.append(
                f"{i + 1}번 곡: 검출 구간 {measured:.1f}s 가 원본 "
                f"{expected:.1f}s 보다 {measured - expected:.0f}s 깁니다. "
                "곡 경계 하나를 놓쳤을 수 있습니다."
            )
    return problems
