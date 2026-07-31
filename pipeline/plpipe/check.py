"""설정 점검.

렌더는 몇 시간씩 걸린다. 그전에 잘못될 만한 것들을 미리 다 잡아준다.
AE 를 띄우지 않고 검사하며, `ae inspect` 덤프가 있으면 컴프·레이어 이름이
템플릿에 실제로 있는지까지 대조한다.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import ae as ae_mod
from . import ffmpeg

OK = "OK"
WARN = "확인"
FAIL = "누락"


@dataclass
class Result:
    status: str
    label: str
    detail: str = ""
    fix: str = ""

    @property
    def failed(self) -> bool:
        return self.status == FAIL


def _selector_kind(selector: str) -> str | None:
    """'@still:2' → 'still'. 이름이나 #N 선택자면 None."""
    if selector.startswith("@"):
        return selector[1:].split(":")[0]
    return None


def _matches(kind: str, layer_kind: str) -> bool:
    if kind == "image":
        return layer_kind in ("still", "footage")
    return kind == layer_kind


def _resolve_in_dump(comp: dict[str, Any], selector: str) -> tuple[bool, int]:
    """덤프 기준으로 선택자가 몇 개 맞는지. (찾음, 후보수)"""
    layers = comp.get("layers", [])
    if not selector:
        return True, 0
    if selector.startswith("#"):
        try:
            idx = int(selector[1:])
        except ValueError:
            return False, 0
        return 1 <= idx <= len(layers), len(layers)
    kind = _selector_kind(selector)
    if kind:
        hits = [l for l in layers if _matches(kind, l.get("kind", ""))]
        return bool(hits), len(hits)
    hits = [l for l in layers if l.get("name") == selector]
    return bool(hits), len(hits)


def run(cfg, project=None) -> list[Result]:
    results: list[Result] = []

    def add(status, label, detail="", fix=""):
        results.append(Result(status, label, detail, fix))

    # ── 도구 ────────────────────────────────────────────────
    if ffmpeg.available():
        add(OK, "ffmpeg / ffprobe")
    else:
        add(FAIL, "ffmpeg / ffprobe", "PATH 에서 찾을 수 없습니다",
            "Windows: winget install Gyan.FFmpeg  ·  macOS: brew install ffmpeg")

    mode = cfg.get("ae.mode", "segments")
    if mode == "ffmpeg":
        # AE 를 아예 안 쓰는 모드라 설치 여부를 따질 필요가 없다.
        add(OK, "After Effects", "ffmpeg 모드라 필요 없음")
    else:
        for kind, label in (("app", "After Effects"), ("aerender", "aerender")):
            configured = cfg.get(f"ae.{kind}") or None
            try:
                path = ae_mod.find_binary(kind, configured)
                add(OK, label, str(path))
            except ae_mod.AEError as exc:
                add(FAIL, label, str(exc).splitlines()[0],
                    f'config.toml 의 [ae] {kind} 에 전체 경로를 적으세요')

    # ── 템플릿 ──────────────────────────────────────────────
    template = cfg.resolve("ae.project")
    if mode == "ffmpeg":
        add(OK, "AE 템플릿", "ffmpeg 모드라 필요 없음")
    elif template is None:
        add(FAIL, "AE 템플릿", "[ae] project 가 비어 있습니다",
            'project = "templates/내파일.aep"')
    elif not template.exists():
        add(FAIL, "AE 템플릿", f"파일이 없습니다: {template}",
            "경로와 파일명을 확인하세요")
    else:
        from . import setup_wizard as wiz
        if wiz.looks_like_autosave(template):
            add(WARN, "AE 템플릿", f"{template.name} — 자동저장본으로 보입니다",
                "작업 중 스냅샷이라 지금 상태와 다를 수 있습니다. "
                "평소 저장해 쓰는 .aep 를 가리키세요")
        else:
            add(OK, "AE 템플릿", template.name)

    # ── 개수 ────────────────────────────────────────────────
    track_count = int(cfg.get("project.track_count", 13))
    slot_comps = list(cfg.get("ae.slot_comps", []) or [])
    if slot_comps:
        if len(slot_comps) == track_count:
            add(OK, "곡 컴프 목록", f"{len(slot_comps)}개 (곡 수와 일치)")
        else:
            add(FAIL, "곡 컴프 목록",
                f"컴프 {len(slot_comps)}개 ≠ track_count {track_count}",
                "[ae] slot_comps 개수와 [project] track_count 를 맞추세요")
    else:
        add(OK, "슬롯 컴프", f'"{cfg.get("ae.slot_comp", "SLOT")}" 를 복제하는 방식')

    # ── 오디오 ──────────────────────────────────────────────
    source = cfg.get("audio.source", "pipeline")
    if source == "manual":
        detail = "직접 합친 파일을 쓰고, 곡 경계를 자동 검출합니다"
        if project is not None:
            raw = cfg.get("audio.master", "drop/master.wav")
            master = Path(str(raw))
            if not master.is_absolute():
                master = project.root / master
            if master.is_file():
                add(OK, "마스터 오디오", str(master.name))
            else:
                add(FAIL, "마스터 오디오", f"파일이 없습니다: {master}",
                    "합쳐둔 오디오를 그 위치에 두세요")
        add(OK, "오디오 방식", detail)
    else:
        add(OK, "오디오 방식", "파이프라인이 곡별 파일을 정규화해 합칩니다")

    # ── 렌더 모드 ───────────────────────────────────────────
    if mode == "full":
        add(OK, "렌더 모드", "full — 메인 컴프를 통째로 렌더")
    elif mode == "segments":
        add(OK, "렌더 모드", "segments — 곡별 클립만 렌더 후 이어붙임")
    elif mode == "ffmpeg":
        add(WARN, "렌더 모드", "ffmpeg — AE 를 쓰지 않는 시험용 모드입니다",
            "실제 영상을 뽑을 땐 full 또는 segments 로 바꾸세요")
    else:
        add(FAIL, "렌더 모드", f"알 수 없는 값: {mode!r}",
            "full / segments / ffmpeg 중 하나")

    # ── 템플릿 구조 대조 ────────────────────────────────────
    results += _check_against_dump(cfg, slot_comps, mode)

    # ── 프로젝트 상태 ───────────────────────────────────────
    if project is not None:
        missing_img = [t.index for t in project.missing_images()]
        if missing_img:
            add(FAIL, "이미지", f"{len(missing_img)}개 없음: {missing_img}",
                f"{project.drop_images} 에 넣고 `plpipe ingest`")
        else:
            add(OK, "이미지", f"{len(project.tracks)}장 배정됨")

    return results


def _check_against_dump(cfg, slot_comps: list[str], mode: str) -> list[Result]:
    """`plpipe ae inspect` 덤프가 있으면 이름을 실제 템플릿과 대조한다."""
    dump_path = cfg.root / "work" / "ae-template.json"
    if not dump_path.is_file():
        return [Result(
            WARN, "템플릿 구조 대조", "아직 템플릿을 읽어본 적이 없습니다",
            "`plpipe ae inspect` 를 한 번 돌리면 컴프·레이어 이름까지 검사합니다",
        )]

    try:
        dump = json.loads(dump_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Result(WARN, "템플릿 구조 대조", f"덤프를 읽지 못했습니다: {exc}")]

    comps = {c["name"]: c for c in dump.get("comps", [])}
    out: list[Result] = []

    def check_comp(name: str, label: str) -> dict | None:
        if not name:
            return None
        comp = comps.get(name)
        if comp is None:
            close = [n for n in comps if n.lower().strip() == name.lower().strip()]
            hint = f"혹시 '{close[0]}'?" if close else \
                   "템플릿의 컴프: " + ", ".join(list(comps)[:8])
            out.append(Result(FAIL, label, f"'{name}' 이(가) 템플릿에 없습니다", hint))
            return None
        return comp

    # 메인 컴프
    if mode == "full":
        main_name = cfg.get("ae.main_comp", "MAIN")
        main = check_comp(main_name, "메인 컴프")
        if main:
            out.append(Result(OK, "메인 컴프", f"{main_name} ({main['duration']}s)"))
            audio_sel = cfg.get("ae.audio_layer", "AUDIO")
            found, count = _resolve_in_dump(main, audio_sel)
            if audio_sel and not found:
                out.append(Result(
                    FAIL, "오디오 레이어",
                    f"'{main_name}' 안에서 {audio_sel} 에 맞는 레이어가 없습니다",
                    "선택자를 고치거나 빈 문자열로 두면 새 레이어를 추가합니다"))
            elif audio_sel:
                out.append(Result(OK, "오디오 레이어", f"{audio_sel} → 후보 {count}개"))

    # 곡 컴프와 그 안의 레이어
    targets = slot_comps or [cfg.get("ae.slot_comp", "SLOT")]
    missing = [n for n in targets if n not in comps]
    if missing:
        out.append(Result(
            FAIL, "곡 컴프", f"{len(missing)}개를 템플릿에서 못 찾음: {missing[:4]}",
            "이름을 정확히 옮겨 적었는지 확인하세요 (공백·하이픈 주의)"))
    else:
        out.append(Result(OK, "곡 컴프", f"{len(targets)}개 전부 템플릿에 있음"))

        # 이미지는 없으면 빌드가 멈춘다. 제목·번호는 없으면 그냥 건너뛴다.
        for sel_key, label, required in (
            ("ae.image_layer", "이미지 레이어", True),
            ("ae.title_layer", "제목 레이어", False),
            ("ae.index_layer", "번호 레이어", False),
        ):
            selector = cfg.get(sel_key, "")
            if not selector:
                continue
            # "@still:last" 처럼 몇 번째인지 직접 적었으면 후보가 여러 개여도
            # 헷갈릴 게 없다. 알림은 안 적었을 때만 띄운다.
            explicit = ":" in selector or selector.startswith("#")
            bad, ambiguous = [], []
            for name in targets:
                found, count = _resolve_in_dump(comps[name], selector)
                if not found:
                    bad.append(name)
                elif count > 1 and not explicit:
                    ambiguous.append(f"{name}({count})")
            if bad and required:
                out.append(Result(
                    FAIL, label,
                    f"{selector} 에 맞는 레이어가 없는 컴프 {len(bad)}개: {bad[:3]}",
                    "선택자를 바꾸거나 해당 컴프를 확인하세요"))
            elif bad:
                out.append(Result(
                    WARN, label,
                    f"{selector} 에 맞는 레이어가 없는 컴프 {len(bad)}개: {bad[:3]}",
                    "그 컴프에서는 이 항목을 건너뜁니다. 필요하면 "
                    "해당 컴프에 텍스트 레이어를 추가하세요"))
            elif ambiguous:
                out.append(Result(
                    WARN, label,
                    f"{selector} · 후보가 여러 개인 컴프: {ambiguous[:3]}",
                    "첫 번째를 씁니다. 다른 걸 쓰려면 :last 나 :2 를 붙이세요"))
            else:
                out.append(Result(OK, label, f"{selector} · 모든 컴프에서 1개씩"))

    # 렌더 템플릿 이름
    templates = dump.get("templates", {})
    for key, setting, label in (
        ("render_settings", "ae.render_settings", "렌더 설정"),
        ("output_modules", "ae.output_module", "출력 모듈"),
    ):
        available = templates.get(key) or []
        wanted = cfg.get(setting, "")
        if not available:
            continue
        if wanted in available:
            out.append(Result(OK, label, wanted))
        else:
            out.append(Result(FAIL, label, f"'{wanted}' 은(는) 없는 이름입니다",
                              "사용 가능: " + ", ".join(available[:6])))
    return out


def _display_width(text: str) -> int:
    """한글·한자는 터미널에서 두 칸을 차지한다."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def report(results: list[Result]) -> int:
    """결과를 출력하고 실패 개수를 돌려준다."""
    mark = {OK: "  ✓", WARN: "  !", FAIL: "  ✗"}
    width = max((_display_width(r.label) for r in results), default=10)
    for r in results:
        line = f"{mark[r.status]} {_pad(r.label, width)}"
        if r.detail:
            line += f"  {r.detail}"
        print(line)
        if r.fix and r.status != OK:
            print(f"      → {r.fix}")

    failures = sum(1 for r in results if r.failed)
    warnings = sum(1 for r in results if r.status == WARN)
    print()
    if failures:
        print(f"{failures}곳을 고쳐야 합니다." +
              (f" (확인 필요 {warnings}곳)" if warnings else ""))
    elif warnings:
        print(f"바로 진행할 수 있습니다. 확인해볼 곳 {warnings}곳.")
    else:
        print("전부 정상입니다.")
    return failures
