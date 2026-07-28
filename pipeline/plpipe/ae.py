"""After Effects 연동.

AE 는 ExtendScript 로만 자동화된다. 파이썬이 작업 명세(JSON)를 만들어
템플릿 .jsx 앞에 붙인 뒤 AE 에 실행시키고, 렌더는 aerender CLI 가 맡는다.

핵심 최적화: 1시간짜리 컴프를 통째로 렌더하는 대신(mode="full"),
곡별 짧은 클립만 렌더하고(mode="segments") ffmpeg 로 이어붙인다.
2회차 반복 구간은 1회차와 화면이 같으므로 클립을 재사용한다.
즉 13곡 × 2회 = 1시간이 아니라, 13개 클립(약 30분어치)만 렌더하면 된다.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .audio import Timeline
from .project import Project, Track

JSX_DIR = Path(__file__).resolve().parent / "assets" / "ae"


class AEError(RuntimeError):
    pass


# ── 실행파일 탐색 ────────────────────────────────────────────
_WIN_ROOTS = [Path("C:/Program Files/Adobe"), Path("C:/Program Files (x86)/Adobe")]
_MAC_ROOT = Path("/Applications")


def _candidates(binary: str) -> list[Path]:
    system = platform.system()
    found: list[Path] = []
    if system == "Windows":
        for root in _WIN_ROOTS:
            if root.is_dir():
                found += sorted(root.glob(f"Adobe After Effects */Support Files/{binary}.exe"))
    elif system == "Darwin":
        if binary == "aerender":
            found += sorted(_MAC_ROOT.glob("Adobe After Effects */aerender"))
        else:
            found += sorted(
                _MAC_ROOT.glob(
                    "Adobe After Effects */Adobe After Effects *.app/Contents/MacOS/Adobe After Effects *"
                )
            )
    # 최신 버전(이름순 뒤쪽)을 먼저.
    return sorted(found, reverse=True)


def find_binary(kind: str, configured: str | None = None) -> Path:
    """kind 는 'app'(AfterFX) 또는 'aerender'."""
    if configured:
        path = Path(configured).expanduser()
        if not path.exists():
            raise AEError(f"설정된 AE 경로가 존재하지 않습니다: {path}")
        return path

    binary = "aerender" if kind == "aerender" else "AfterFX"
    which = shutil.which(binary)
    if which:
        return Path(which)
    for candidate in _candidates(binary):
        if candidate.exists():
            return candidate
    raise AEError(
        f"{binary} 을(를) 찾지 못했습니다. config.toml 의 [ae] {kind} 에 "
        "전체 경로를 직접 적어주세요."
    )


# ── 작업 명세 ────────────────────────────────────────────────
@dataclass
class AEJob:
    mode: str
    project: str          # 템플릿 .aep 절대경로
    save_as: str          # 빌드 결과 .aep 절대경로
    names: dict[str, str]  # 컴프/레이어 이름 매핑
    video: dict[str, Any]
    slots: list[dict[str, Any]]
    main: dict[str, Any]
    render: dict[str, str]

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2)


def build_job(
    project: Project,
    timeline: Timeline,
    items: Sequence[tuple[int, Track]],
    cfg,
    master_audio: Path | None,
) -> AEJob:
    """manifest + 타임라인으로부터 AE 작업 명세를 만든다."""
    ae = cfg.get("ae", {}) or {}
    mode = ae.get("mode", "segments")
    if mode not in {"segments", "full"}:
        raise AEError(f"ae.mode 는 'segments' 또는 'full' 이어야 합니다: {mode!r}")

    template = cfg.resolve("ae.project")
    if template is None or not template.exists():
        raise AEError(
            f"AE 템플릿을 찾을 수 없습니다: {ae.get('project')!r}\n"
            "config.toml 의 [ae] project 경로를 확인하세요."
        )

    fps = float(cfg.get("video.fps", 24))

    ae_dir = project.work / "ae"
    seg_dir = project.work / "segments"
    ae_dir.mkdir(parents=True, exist_ok=True)
    seg_dir.mkdir(parents=True, exist_ok=True)

    ext = ae.get("intermediate_ext", "mov")

    # 곡별 컴프가 이미 템플릿에 있으면 복제하지 않고 그 안의 내용만 바꾼다.
    existing = list(ae.get("slot_comps", []) or [])
    if existing and len(existing) != len(project.tracks):
        raise AEError(
            f"[ae] slot_comps 에 컴프 이름이 {len(existing)}개 있는데 "
            f"트랙은 {len(project.tracks)}개입니다. 개수를 맞춰주세요."
        )

    # 구간 길이는 트랙 번호로 조회한다 (cue 모드에서는 gap 이 0).
    span_by_index: dict[int, float] = {}
    for seg in timeline.segments:
        span_by_index.setdefault(seg.index, seg.step)

    slots: list[dict[str, Any]] = []
    for position, track in enumerate(project.tracks):
        image = project.path(track.image)
        if image is None or not image.is_file():
            raise AEError(f"트랙 {track.index} 의 이미지가 없습니다.")
        span = span_by_index.get(track.index)
        if span is None:
            raise AEError(f"트랙 {track.index} 이 타임라인에 없습니다.")
        slot: dict[str, Any] = {
            "index": track.index,
            "name": f"PL_SLOT_{track.index:02d}",
            "title": track.display_title,
            "label": f"{track.index:02d}",
            "image": str(image.resolve()),
            # segments 모드의 클립 길이는 '다음 곡까지의 간격'과 같아야 한다.
            "duration": round(span if mode == "segments" else (track.duration or span), 3),
            "output": str((seg_dir / f"{track.index:02d}.{ext}").resolve()),
        }
        if existing:
            slot["existing"] = existing[position]
        slots.append(slot)

    slot_name_by_index = {s["index"]: s.get("existing") or s["name"] for s in slots}
    main = {
        "name": "PL_MAIN",
        "duration": timeline.total,
        "audio": str(master_audio.resolve()) if master_audio else "",
        "output": str((project.work / "ae" / f"{project.slug}.{ext}").resolve()),
        "placements": [
            {"slot": slot_name_by_index[seg.index], "start": seg.start,
             "end": seg.end + max(0.0, seg.gap_after)}
            for seg in timeline.segments
        ],
    }

    return AEJob(
        mode=mode,
        project=str(template.resolve()),
        save_as=str((ae_dir / f"{project.slug}.aep").resolve()),
        names={
            "main_comp": ae.get("main_comp", "MAIN"),
            "slot_comp": ae.get("slot_comp", "SLOT"),
            "image_layer": ae.get("image_layer", "IMAGE"),
            "title_layer": ae.get("title_layer", "TITLE"),
            "index_layer": ae.get("index_layer", "INDEX"),
            "audio_layer": ae.get("audio_layer", "AUDIO"),
        },
        video={
            "width": int(cfg.get("video.width", 1920)),
            "height": int(cfg.get("video.height", 1080)),
            "fps": fps,
        },
        slots=slots,
        main=main,
        render={
            "settings": ae.get("render_settings", "Best Settings"),
            "module": ae.get("output_module", "Lossless"),
        },
    )


# ── 실행 ────────────────────────────────────────────────────
def _compose_script(job: AEJob, template_name: str, dst: Path) -> Path:
    """작업 명세를 앞에 박아 넣은 실행용 .jsx 를 만든다.

    AE 는 스크립트에 인자를 넘기는 깔끔한 방법이 없어서, JSON 을 전역
    변수로 선언한 뒤 템플릿 본문을 이어 붙인다.
    """
    body = (JSX_DIR / template_name).read_text(encoding="utf-8")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(
        f"var PLPIPE_JOB = {job.to_json()};\n\n{body}",
        encoding="utf-8",
    )
    return dst


def run_script(script: Path, cfg, *, timeout: int = 3600) -> str:
    app = find_binary("app", cfg.get("ae.app") or None)
    cmd = [str(app), "-noui", "-r", str(script.resolve())]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise AEError(
            f"AE 스크립트 실행 실패 (exit {proc.returncode}):\n"
            f"{proc.stdout.strip()}\n{proc.stderr.strip()}"
        )
    return proc.stdout


def build_ae_project(job: AEJob, project: Project, cfg) -> Path:
    script = _compose_script(
        job, "build_project.jsx", project.work / "ae" / "build.jsx"
    )
    log = project.work / "ae" / "build.log"
    print(f"  AE 프로젝트 빌드 중… ({job.mode} 모드, 슬롯 {len(job.slots)}개)")
    output = run_script(script, cfg)
    log.write_text(output, encoding="utf-8")

    result = Path(job.save_as)
    if not result.exists():
        raise AEError(
            f"AE 가 프로젝트를 저장하지 않았습니다: {result}\n로그: {log}"
        )
    return result


def aerender(aep: Path, cfg, *, timeout: int = 24 * 3600) -> None:
    """렌더 큐에 담긴 항목을 전부 렌더한다."""
    binary = find_binary("aerender", cfg.get("ae.aerender") or None)
    cmd = [str(binary), "-project", str(aep.resolve()), "-continueOnMissingFootage"]
    print(f"  aerender 실행: {aep.name}")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", bufsize=1,
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        del tail[:-40]
        if "PROGRESS" in line or "aerender" in line.lower():
            print(f"    {line}")
    if proc.wait(timeout=timeout) != 0:
        raise AEError("aerender 실패:\n" + "\n".join(tail))


def inspect_template(cfg, out_path: Path) -> dict[str, Any]:
    """템플릿 .aep 의 컴프/레이어 구조를 JSON 으로 덤프한다."""
    template = cfg.resolve("ae.project")
    if template is None or not template.exists():
        raise AEError(f"AE 템플릿을 찾을 수 없습니다: {cfg.get('ae.project')!r}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    stub = AEJob(
        mode="inspect",
        project=str(template.resolve()),
        save_as=str(out_path.resolve()),
        names={}, video={}, slots=[], main={}, render={},
    )
    script = _compose_script(stub, "inspect_template.jsx",
                             out_path.parent / "inspect.jsx")
    run_script(script, cfg, timeout=600)
    if not out_path.exists():
        raise AEError(f"구조 덤프가 생성되지 않았습니다: {out_path}")
    return json.loads(out_path.read_text(encoding="utf-8"))
