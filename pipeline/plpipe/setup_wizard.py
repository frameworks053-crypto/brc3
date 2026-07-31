"""`plpipe setup` — 물어보고 config.toml 을 대신 써 준다.

설정 항목이 서른 개쯤 되는데, 그중 대부분은 템플릿을 읽어보면 알 수 있는
값이다. AE 로 템플릿 구조를 한 번 읽고 나면 해상도·프레임레이트·컴프 이름·
렌더 템플릿까지 다 채울 수 있으므로, 사람에게는 기계가 알 수 없는 것만 묻는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from . import ae as ae_mod

# AE 가 자동으로 만드는 폴더들. 여기 있는 .aep 는 후보에서 뺀다.
_SKIP_DIRS = {"adobe after effects auto-save", "auto-save", "backup"}

# 자동저장 파일 이름의 특징. 폴더 밖으로 복사해 와도 이름으로 알아본다.
_AUTOSAVE_HINTS = ("auto-save", "autosave", "자동 저장", "자동저장")


def looks_like_autosave(path: Path) -> bool:
    """자동저장본인지. 작업 중 스냅샷이라 지금 상태와 다를 수 있다."""
    lowered = str(path).lower()
    return any(hint in lowered for hint in _AUTOSAVE_HINTS)


class SetupCancelled(RuntimeError):
    pass


# ── 입력 헬퍼 ────────────────────────────────────────────────
def ask(prompt: str, default: str = "", *, assume_yes: bool = False) -> str:
    if assume_yes:
        print(f"{prompt} [{default}]")
        return default
    shown = f"{prompt} [{default}]: " if default else f"{prompt}: "
    try:
        answer = input(shown).strip()
    except EOFError:
        raise SetupCancelled("입력이 끊겼습니다.")
    return answer or default


def ask_int(prompt: str, default: int, *, assume_yes: bool = False) -> int:
    while True:
        raw = ask(prompt, str(default), assume_yes=assume_yes)
        try:
            return int(raw)
        except ValueError:
            print("  숫자를 입력해 주세요.")


def ask_choice(
    prompt: str, options: Sequence[str], default: int = 1, *, assume_yes: bool = False
) -> int:
    """1부터 시작하는 번호를 고르게 한다. 항목이 하나면 그냥 그걸 쓴다."""
    if len(options) == 1:
        print(f"{prompt}\n  → {options[0]}")
        return 0
    for i, option in enumerate(options, start=1):
        print(f"  {i}. {option}")
    while True:
        raw = ask(prompt, str(default), assume_yes=assume_yes)
        try:
            n = int(raw)
        except ValueError:
            print("  번호를 입력해 주세요.")
            continue
        if 1 <= n <= len(options):
            return n - 1
        print(f"  1 ~ {len(options)} 사이로 입력해 주세요.")


def confirm_or_choose(
    label: str, guess: str, options: Sequence[str], *, assume_yes: bool = False
) -> str:
    """추측한 값을 먼저 보여주고, 아니라고 할 때만 목록을 펼친다.

    컴프가 스무 개 넘는 프로젝트에서 목록부터 들이밀면 고르기 어렵다.
    """
    if guess and guess in options:
        if ask_yes(f"{label}: '{guess}' 맞나요?", True, assume_yes=assume_yes):
            return guess
    print(f"\n{label} 을(를) 골라 주세요.")
    default = (options.index(guess) + 1) if guess in options else 1
    return options[ask_choice("번호", options, default, assume_yes=assume_yes)]


def ask_yes(prompt: str, default: bool = True, *, assume_yes: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = ask(prompt, "Y" if default else "N", assume_yes=assume_yes).lower()
    if raw in ("y", "yes", "네", "ㅇ", "Y"):
        return True
    if raw in ("n", "no", "아니오", "ㄴ"):
        return False
    return default


# ── 탐색 ────────────────────────────────────────────────────
def find_templates(root: Path) -> list[Path]:
    """작업 폴더 아래에서 .aep 를 찾는다. 자동저장 폴더는 뺀다."""
    found: list[Path] = []
    for path in sorted(root.rglob("*.aep")):
        parts = {p.lower() for p in path.parts}
        if parts & _SKIP_DIRS:
            continue
        if "projects" in parts:      # 파이프라인이 만든 결과물
            continue
        found.append(path)
    return found


def guess_main_comp(comps: list[dict[str, Any]]) -> str:
    """프리컴프를 가장 많이 담고 있는 컴프가 메인일 가능성이 높다."""
    best, best_score = "", -1
    for comp in comps:
        precomps = sum(1 for l in comp.get("layers", [])
                       if l.get("kind") == "precomp")
        if precomps > best_score:
            best, best_score = comp["name"], precomps
    return best


def precomps_in(comp: dict[str, Any]) -> list[str]:
    """메인 컴프 안에 놓인 프리컴프 이름을 위에서 아래 순서로."""
    out: list[str] = []
    for layer in comp.get("layers", []):
        if layer.get("kind") == "precomp":
            name = layer.get("source") or layer.get("name")
            if name:
                out.append(name)
    return out


def dump_has_timing(dump: dict[str, Any]) -> bool:
    """덤프에 레이어 타이밍이 들어 있는지.

    예전 형식에는 없어서 '길이가 짧으면 곡이 아니다' 판단을 할 수 없다.
    그럴 땐 템플릿을 다시 읽는 게 낫다.
    """
    for comp in dump.get("comps", []):
        for layer in comp.get("layers", []):
            if layer.get("kind") == "precomp" and "out" in layer:
                return True
    return False


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if not ordered:
        return 0.0
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def guess_non_songs(
    main: dict[str, Any], comps: dict[str, Any]
) -> dict[int, list[str]]:
    """메인 컴프에 놓인 프리컴프 중 곡이 아닐 것 같은 것을 골라낸다.

    돌려주는 값은 {0부터 세는 순번: [그렇게 본 이유]}.

    판단 근거 세 가지. 확실한 것부터:
      · 타임라인에 놓인 길이가 다른 것들보다 훨씬 짧다 (인트로·아웃트로)
      · 레이어가 꺼져 있다 (쓰지 않는 잔재)
      · 대부분의 컴프에는 제목 텍스트가 있는데 이것만 없다

    구조가 다 똑같으면 아무것도 돌려주지 않는다. 그때는 기계가 알 수 없으니
    사람에게 물어야 한다.
    """
    entries: list[dict[str, Any]] = []
    for layer in main.get("layers", []):
        if layer.get("kind") != "precomp":
            continue
        name = layer.get("source") or layer.get("name")
        span = None
        if layer.get("out") is not None and layer.get("in") is not None:
            span = float(layer["out"]) - float(layer["in"])
        comp = comps.get(name, {})
        entries.append({
            "name": name,
            "span": span if (span is None or span > 0) else None,
            "enabled": layer.get("enabled", True),
            "has_text": any(l.get("kind") == "text"
                            for l in comp.get("layers", [])),
        })

    reasons: dict[int, list[str]] = {}

    def note(i: int, why: str) -> None:
        reasons.setdefault(i, []).append(why)

    # 길이 — 중앙값의 40% 미만이면 곡이 아닐 가능성이 크다.
    spans = [e["span"] for e in entries if e["span"] is not None]
    if len(spans) >= 3:
        middle = _median(spans)
        if middle > 0:
            for i, entry in enumerate(entries):
                span = entry["span"]
                if span is not None and span < middle * 0.4:
                    note(i, f"길이 {span:.0f}초 (보통 {middle:.0f}초)")

    # 꺼진 레이어.
    for i, entry in enumerate(entries):
        if not entry["enabled"]:
            note(i, "레이어가 꺼져 있음")

    # 제목 텍스트 — 대다수가 갖고 있을 때만 신호로 본다.
    with_text = sum(1 for e in entries if e["has_text"])
    if entries and with_text >= len(entries) * 0.6:
        for i, entry in enumerate(entries):
            if not entry["has_text"]:
                note(i, "제목 텍스트 레이어 없음")

    return reasons


def pick_selector(comps: dict[str, Any], names: Sequence[str], kind: str) -> str:
    """곡 컴프 전부에서 통하는 선택자를 고른다.

    모든 컴프에 딱 하나씩 있으면 "@kind", 여러 개인 컴프가 있으면 첫 번째를
    쓰겠다는 뜻으로 그대로 "@kind" 를 쓰되 호출한 쪽이 알려준다.
    """
    matches = "still" if kind == "still" else kind
    counts = []
    for name in names:
        comp = comps.get(name)
        if comp is None:
            continue
        counts.append(sum(1 for l in comp.get("layers", [])
                          if l.get("kind") == matches))
    if not counts or max(counts) == 0:
        return ""
    return f"@{matches}"


# ── 설정 파일 쓰기 ──────────────────────────────────────────
def _toml_string(value: str) -> str:
    # 윈도우 경로의 역슬래시를 안전하게 다루려면 슬래시로 바꾸는 게 낫다.
    return '"' + value.replace("\\", "/").replace('"', '\\"') + '"'


def _toml_list(values: Sequence[str], comments: Sequence[str] = ()) -> str:
    quoted = [_toml_string(v) + "," for v in values]
    width = max((len(q) for q in quoted), default=0)
    lines = ["["]
    for i, item in enumerate(quoted):
        comment = f"  # {comments[i]}" if i < len(comments) and comments[i] else ""
        lines.append(f"  {item.ljust(width) if comment else item}{comment}")
    lines.append("]")
    return "\n".join(lines)


def render_config(answers: dict[str, Any]) -> str:
    songs = answers["slot_comps"]
    comments = [f"{i}번 곡  ← {i:02d}-*.png" for i in range(1, len(songs) + 1)]

    manual_audio = answers["audio_source"] == "manual"
    lines = [
        "# plpipe 설정 — `plpipe setup` 이 만들었습니다.",
        "# 값을 바꾸고 싶으면 `plpipe setup` 을 다시 돌리거나 여기서 고치세요.",
        "# 고친 뒤에는 `plpipe check` 로 확인하세요.",
        "",
        "[project]",
        f"track_count = {len(songs)}",
        f"repeat      = {answers['repeat']}"
        + ("        # 0 = 한 번만 재생, 1 = 두 번 재생" if True else ""),
        "",
        "[video]",
        f"width  = {answers['width']}",
        f"height = {answers['height']}",
        f"fps    = {answers['fps']}",
        "",
        "[audio]",
    ]
    if manual_audio:
        lines += [
            '# 합쳐둔 오디오를 그대로 쓰고, 무음을 찾아 곡 경계를 자동 검출합니다.',
            'source = "manual"',
            'master = "drop/master.wav"',
        ]
    else:
        lines += [
            '# 곡별 파일을 정규화해서 파이프라인이 합칩니다.',
            'source = "pipeline"',
        ]
    lines += [
        "",
        "[music]",
        'provider = "manual"',
        "",
        "[image]",
        'provider = "manual"',
        f"width  = {answers['width']}",
        f"height = {answers['height']}",
        "",
        "[ae]",
        f"mode      = {_toml_string(answers['mode'])}",
        f"project   = {_toml_string(answers['project'])}",
        f"main_comp = {_toml_string(answers['main_comp'])}",
        "",
        "# 곡 컴프를 재생 순서대로. 목록에 없는 컴프는 건드리지 않습니다.",
        "slot_comps = " + _toml_list(songs, comments),
        "",
        "# 레이어를 이름 대신 종류로 찾습니다.",
        f"image_layer = {_toml_string(answers['image_layer'])}",
        f"title_layer = {_toml_string(answers['title_layer'])}",
        'index_layer = ""',
        f"audio_layer = {_toml_string(answers['audio_layer'])}",
        "",
        f"render_settings  = {_toml_string(answers['render_settings'])}",
        f"output_module    = {_toml_string(answers['output_module'])}",
        'intermediate_ext = "mov"',
        "",
    ]
    app = answers.get("app", "")
    aerender = answers.get("aerender", "")
    if app or aerender:
        lines += [
            "# 자동으로 찾은 AE 경로입니다.",
            f"app      = {_toml_string(app)}",
            f"aerender = {_toml_string(aerender)}",
            "",
        ]
    lines += [
        "[channel]",
        f"name        = {_toml_string(answers['channel_name'])}",
        'title       = "{mood} · {n} tracks"',
        'language    = "ko"',
        "category_id = 10",
        'tags = ["lofi", "playlist", "study music"]',
        "",
        "[paths]",
        'projects = "projects"',
        "",
    ]
    return "\n".join(lines)


def derive_from_dump(dump: dict[str, Any]) -> dict[str, Any]:
    """템플릿 구조에서 뽑아낼 수 있는 값을 전부 뽑는다."""
    comps = {c["name"]: c for c in dump.get("comps", [])}
    main_name = guess_main_comp(list(comps.values()))
    main = comps.get(main_name, {})
    templates = dump.get("templates", {})

    render_settings = "Best Settings"
    for candidate in templates.get("render_settings", []):
        if candidate == "Best Settings":
            render_settings = candidate
            break
    else:
        if templates.get("render_settings"):
            render_settings = templates["render_settings"][0]

    output_module = "Lossless"
    if output_module not in templates.get("output_modules", [output_module]):
        output_module = templates["output_modules"][0]

    return {
        "comps": comps,
        "main_comp": main_name,
        "width": main.get("width", 1920),
        "height": main.get("height", 1080),
        "fps": main.get("fps", 24),
        "precomps": precomps_in(main),
        "render_settings": render_settings,
        "output_module": output_module,
        "has_audio_layer": any(l.get("kind") == "audio"
                               for l in main.get("layers", [])),
    }
