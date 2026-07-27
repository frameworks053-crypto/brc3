"""ffmpeg / ffprobe 래퍼."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Sequence


class FFmpegError(RuntimeError):
    pass


def _binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FFmpegError(
            f"{name} 을(를) 찾을 수 없습니다. ffmpeg 를 설치하고 PATH 에 추가하세요.\n"
            "  Windows: winget install Gyan.FFmpeg\n"
            "  macOS  : brew install ffmpeg"
        )
    return path


def available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def run(args: Sequence[str], *, quiet: bool = True) -> subprocess.CompletedProcess:
    cmd = [_binary("ffmpeg"), "-hide_banner", "-nostdin", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise FFmpegError(f"ffmpeg 실패 (exit {proc.returncode}):\n{tail}")
    if not quiet and proc.stderr:
        print(proc.stderr)
    return proc


def probe(path: Path) -> dict:
    cmd = [
        _binary("ffprobe"),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe 실패: {path}\n{proc.stderr.strip()}")
    return json.loads(proc.stdout)


def duration(path: Path) -> float:
    """미디어 길이(초). 컨테이너 값이 없으면 오디오 스트림에서 찾는다."""
    info = probe(path)
    fmt_dur = info.get("format", {}).get("duration")
    if fmt_dur:
        return float(fmt_dur)
    for stream in info.get("streams", []):
        if stream.get("duration"):
            return float(stream["duration"])
    raise FFmpegError(f"길이를 알 수 없습니다: {path}")


def measure_loudness(path: Path) -> dict[str, float]:
    """loudnorm 1차 패스. 측정값 dict 를 돌려준다."""
    return measure_loudness_chain(path, "")


def measure_loudness_chain(path: Path, prefix: str = "") -> dict[str, float]:
    """1차 패스를 돌리되 loudnorm 앞에 필터 체인을 붙일 수 있다.

    무음 제거 같은 전처리를 하면 라우드니스도 달라지므로, 2차 패스와
    똑같은 전처리를 걸고 측정해야 결과가 맞는다. `prefix` 는 콤마로
    끝나거나 빈 문자열이어야 한다.
    """
    cmd = [
        _binary("ffmpeg"), "-hide_banner", "-nostdin",
        "-i", str(path),
        "-af", f"{prefix}loudnorm=print_format=json",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise FFmpegError(f"라우드니스 측정 실패: {path}\n{tail}")
    return parse_loudnorm_json(proc.stderr)


def parse_loudnorm_json(stderr: str) -> dict[str, float]:
    """loudnorm 이 stderr 끝에 뱉는 JSON 블록을 파싱한다."""
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise FFmpegError("loudnorm 측정값을 파싱하지 못했습니다.")
    raw = json.loads(stderr[start : end + 1])
    out: dict[str, float] = {}
    # 완전 무음 트랙이면 "-inf" 가 온다. float() 은 이걸 그대로 받아들이지만
    # 2차 패스에 measured_I=-inf 를 넘기면 ffmpeg 가 거부하므로, 유한한
    # 하한값으로 눌러 준다.
    floors = {"input_i": -70.0, "input_thresh": -70.0, "input_tp": -99.0}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        try:
            value = float(raw.get(key))
        except (TypeError, ValueError):
            value = float("nan")
        if not math.isfinite(value):
            value = floors.get(key, 0.0)
        out[key] = value
    return out
