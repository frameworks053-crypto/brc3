"""최종 영상 조립.

segments 모드에서는 AE 가 곡별 짧은 클립만 렌더하고, 여기서 ffmpeg 이
재생 순서대로 이어붙인 뒤 마스터 오디오를 얹는다. 2회차 반복 구간은
같은 클립 파일을 다시 참조하므로 렌더 시간이 절반으로 준다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from . import ffmpeg
from .audio import Timeline
from .project import Project, Track


class RenderError(RuntimeError):
    pass


def _concat_list(paths: Sequence[Path], dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for path in paths:
        escaped = str(path.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dst


def stills_to_segments(
    project: Project, timeline: Timeline, cfg
) -> list[Path]:
    """AE 없이 정지 이미지만으로 곡별 클립을 만든다 (테스트/폴백용)."""
    seg_dir = project.work / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    # 클립 길이는 타임라인의 '다음 곡까지의 간격'에서 가져온다. 오디오를 직접
    # 합쳐 넣는 경우 곡별 파일이 없어 track.duration 이 비어 있기 때문이다.
    span_by_index: dict[int, float] = {}
    for seg in timeline.segments:
        span_by_index.setdefault(seg.index, seg.step)
    width = int(cfg.get("video.width", 1920))
    height = int(cfg.get("video.height", 1080))
    fps = cfg.get("video.fps", 24)

    outputs: list[Path] = []
    for track in project.tracks:
        image = project.path(track.image)
        if image is None or not image.is_file():
            raise RenderError(f"트랙 {track.index} 의 이미지가 없습니다.")
        length = span_by_index.get(track.index)
        if length is None:
            raise RenderError(f"트랙 {track.index} 이 타임라인에 없습니다.")
        dst = seg_dir / f"{track.index:02d}.mp4"
        print(f"  [{track.index:02d}] 스틸 클립 생성 ({length:.1f}s)")
        ffmpeg.run([
            "-loop", "1", "-i", str(image),
            "-t", f"{length:.3f}",
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,format=yuv420p",
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            str(dst),
        ])
        track.segment = project.rel(dst)
        outputs.append(dst)
    project.save()
    return outputs


def collect_segments(project: Project, cfg) -> dict[int, Path]:
    """AE 가 렌더한 곡별 클립을 찾아 트랙 번호로 매핑한다."""
    seg_dir = project.work / "segments"
    ext = cfg.get("ae.intermediate_ext", "mov")
    found: dict[int, Path] = {}
    for track in project.tracks:
        candidates = [seg_dir / f"{track.index:02d}.{ext}",
                      seg_dir / f"{track.index:02d}.mp4"]
        # 출력 모듈이 파일명에 확장자를 덧붙이는 경우도 있다.
        candidates += sorted(seg_dir.glob(f"{track.index:02d}.*"))
        for candidate in candidates:
            if candidate.is_file() and candidate.stat().st_size > 0:
                found[track.index] = candidate
                track.segment = project.rel(candidate)
                break
        else:
            raise RenderError(
                f"트랙 {track.index} 의 렌더 결과가 없습니다: {seg_dir}\n"
                "aerender 가 끝났는지, 출력 모듈 확장자가 "
                f"ae.intermediate_ext({ext!r}) 와 맞는지 확인하세요."
            )
    project.save()
    return found


def assemble(
    project: Project,
    timeline: Timeline,
    items: Sequence[tuple[int, Track]],
    segments: dict[int, Path],
    master_audio: Path,
    cfg,
) -> Path:
    """곡별 클립 + 마스터 오디오 → 최종 mp4."""
    work = project.work / "assemble"
    work.mkdir(parents=True, exist_ok=True)
    dst = project.out / f"{project.slug}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)

    ordered = [segments[track.index] for _, track in items]
    concat_len = sum(seg.step for seg in timeline.segments)
    # 마지막 곡 뒤에는 간격이 없으므로, 그만큼 줄인 게 본문 길이다.
    body = concat_len - timeline.tail_gap

    chain: list[str] = []
    if body < concat_len - 1e-3:
        chain += [f"trim=end={body:.3f}", "setpts=PTS-STARTPTS"]
    tail = timeline.lead_out + max(0.0, body - concat_len)
    chain.append(
        f"tpad=start_duration={timeline.lead_in:.3f}:start_mode=clone"
        f":stop_duration={tail:.3f}:stop_mode=clone"
    )
    chain.append("format=yuv420p")

    list_file = _concat_list(ordered, work / "segments.txt")
    print(f"  최종 인코딩: {len(ordered)}개 클립 → {_hms(timeline.total)}")
    ffmpeg.run([
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-i", str(master_audio),
        "-filter_complex", f"[0:v]{','.join(chain)}[v]",
        "-map", "[v]", "-map", "1:a",
        "-t", f"{timeline.total:.3f}",
        "-c:v", "libx264",
        "-preset", str(cfg.get("video.preset", "slow")),
        "-crf", str(cfg.get("video.crf", 18)),
        "-r", str(cfg.get("video.fps", 24)),
        "-c:a", "aac", "-b:a", str(cfg.get("video.audio_bitrate", "320k")),
        "-movflags", "+faststart",
        str(dst),
    ])
    return dst


def encode_full(
    project: Project, timeline: Timeline, intermediate: Path,
    master_audio: Path, cfg
) -> Path:
    """full 모드: AE 가 낸 무손실 중간본에 오디오를 얹어 인코딩한다."""
    dst = project.out / f"{project.slug}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not intermediate.is_file():
        raise RenderError(f"AE 중간본이 없습니다: {intermediate}")
    print(f"  최종 인코딩: {intermediate.name} → {dst.name}")
    ffmpeg.run([
        "-i", str(intermediate),
        "-i", str(master_audio),
        "-map", "0:v", "-map", "1:a",
        "-t", f"{timeline.total:.3f}",
        "-c:v", "libx264",
        "-preset", str(cfg.get("video.preset", "slow")),
        "-crf", str(cfg.get("video.crf", 18)),
        "-r", str(cfg.get("video.fps", 24)),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", str(cfg.get("video.audio_bitrate", "320k")),
        "-movflags", "+faststart",
        str(dst),
    ])
    return dst


def _hms(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
