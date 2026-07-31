"""plpipe 명령줄 인터페이스."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import ae as ae_mod
from . import audio as audio_mod
from . import check as check_mod
from . import cue as cue_mod
from . import setup_wizard as wiz
from . import ffmpeg, metadata, render
from .config import Config, ConfigError, EXAMPLE_NAME
from .project import (
    AUDIO_EXTS,
    IMAGE_EXTS,
    Project,
    ProjectError,
    parse_index,
    scan_media,
    slugify,
)
from .providers import ProviderError, image as image_provider, music as music_provider

ASSETS = Path(__file__).resolve().parent / "assets"


# ── 공통 헬퍼 ────────────────────────────────────────────────
def _load(args) -> tuple[Config, Project]:
    cfg = Config.load(Path(args.config).resolve() if args.config else None)
    proj = Project.resolve(cfg.projects_dir, getattr(args, "project", None))
    return cfg, proj


def _repeat(cfg) -> int:
    return int(cfg.get("project.repeat", 0))


def _timeline(proj: Project) -> audio_mod.Timeline:
    if not proj.timeline:
        raise ProjectError("타임라인이 없습니다. `plpipe audio` 를 먼저 실행하세요.")
    return audio_mod.Timeline.from_dict(proj.timeline)


def _manual_master(proj: Project, cfg) -> Path | None:
    """직접 합쳐서 넣는 경우의 마스터 오디오 경로."""
    if cfg.get("audio.source", "pipeline") != "manual":
        return None
    raw = cfg.get("audio.master", "drop/master.wav")
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = proj.root / path
    return path


def _master(proj: Project, cfg) -> Path:
    manual = _manual_master(proj, cfg)
    if manual is not None:
        if not manual.is_file():
            raise ProjectError(
                f"마스터 오디오가 없습니다: {manual}\n"
                "합쳐둔 오디오 파일을 그 위치에 두거나 [audio] master 를 고치세요."
            )
        return manual
    fmt = cfg.get("audio.master_format", "wav")
    path = proj.out / f"master.{fmt}"
    if not path.is_file():
        raise ProjectError(f"마스터 오디오가 없습니다: {path}\n`plpipe audio` 를 실행하세요.")
    return path


def _hms(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


# ── 명령 ────────────────────────────────────────────────────
def cmd_init(args) -> int:
    target = Path(args.dir or ".").resolve()
    target.mkdir(parents=True, exist_ok=True)
    dst = target / "config.toml"
    if dst.exists() and not args.force:
        print(f"이미 있습니다: {dst} (덮어쓰려면 --force)")
        return 1
    shutil.copyfile(ASSETS / EXAMPLE_NAME, dst)
    (target / "projects").mkdir(exist_ok=True)
    (target / "templates").mkdir(exist_ok=True)
    print(f"생성: {dst}")
    print(f"생성: {target / 'projects'}/  (프로젝트가 쌓이는 곳)")
    print(f"생성: {target / 'templates'}/  (여기에 AE 템플릿 .aep 를 두세요)")
    print("\n다음: config.toml 을 열어 [ae] 항목을 채우고 `plpipe new <이름>` 을 실행하세요.")
    return 0


def cmd_new(args) -> int:
    cfg = Config.load(Path(args.config).resolve() if args.config else None)
    slug = slugify(args.name)
    count = args.tracks or int(cfg.get("project.track_count", 13))

    seeds: list[tuple[str, str]] = []
    if args.titles:
        for line in Path(args.titles).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            title, _, prompt = line.partition("|")
            seeds.append((title.strip(), prompt.strip()))
        if seeds:
            count = len(seeds)

    proj = Project.create(cfg.projects_dir, slug, count, vars=_parse_vars(args.var))
    for track, (title, prompt) in zip(proj.tracks, seeds):
        track.title = title
        track.prompt = prompt
    proj.save()

    print(f"프로젝트 생성: {proj.root}")
    print(f"  트랙 {count}개 · 반복 {_repeat(cfg)}회 (총 {count * (_repeat(cfg) + 1)}구간)")
    print(f"\n곡 파일  → {proj.drop_audio}")
    print(f"이미지   → {proj.drop_images}")
    print("\n번호를 앞에 붙여 넣으세요: 01-제목.mp3 / 01-제목.png")
    return 0


def _parse_vars(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        key, _, value = pair.partition("=")
        if not _:
            raise SystemExit(f"--var 는 key=value 형식이어야 합니다: {pair!r}")
        out[key.strip()] = value.strip()
    return out


def _warn_autosave(template: Path, yes: bool) -> bool:
    """자동저장본이면 경고하고 계속할지 묻는다."""
    if not wiz.looks_like_autosave(template):
        return True
    print()
    print("  ! 이 파일은 After Effects 자동저장본으로 보입니다.")
    print("    작업 중 어느 시점의 스냅샷이라 지금 편집 상태와 다를 수 있습니다.")
    print("    평소 저장해 쓰시는 .aep 를 가리키는 편이 안전합니다.")
    return wiz.ask_yes("  그래도 이 파일로 진행할까요?", False, assume_yes=yes)


def cmd_setup(args) -> int:
    """물어보고 config.toml 을 대신 써 준다."""
    root = Path(args.dir or ".").resolve()
    root.mkdir(parents=True, exist_ok=True)
    dst = root / "config.toml"
    yes = bool(getattr(args, "yes", False))

    print(f"작업 폴더: {root}\n")
    if dst.exists() and not args.force:
        print(f"config.toml 이 이미 있습니다: {dst}")
        if not wiz.ask_yes("덮어쓸까요?", False, assume_yes=yes):
            return 1
    (root / "templates").mkdir(exist_ok=True)
    (root / "projects").mkdir(exist_ok=True)

    # 1. AE 템플릿 찾기 ─────────────────────────────────────
    # --template 로 직접 지정하면 작업 폴더 밖에 있어도 된다. 원본을
    # 옮기거나 복사하지 않고 있는 자리에서 그대로 쓸 수 있다.
    if args.template:
        template = Path(args.template).expanduser()
        if not template.is_file():
            print(f".aep 파일을 찾지 못했습니다: {template}")
            return 1
        print(f"AE 템플릿: {template}")
        if not _warn_autosave(template, yes):
            return 1
    else:
        candidates = wiz.find_templates(root)
        if not candidates:
            print(f"이 폴더에서 .aep 파일을 찾지 못했습니다: {root}\n")
            print("둘 중 하나로 해결하세요:")
            print(f"  · .aep 를 {root} 안에 두고 다시 실행")
            print("  · 또는 있는 자리 그대로 쓰기:")
            print('      plpipe setup --template "C:/경로/내파일.aep"')
            return 1
        rels = [str(p.relative_to(root)) for p in candidates]
        chosen = wiz.confirm_or_choose("AE 템플릿", rels[0], rels, assume_yes=yes)
        template = candidates[rels.index(chosen)]
        if not _warn_autosave(template, yes):
            return 1
    print()

    # 2. 템플릿 구조 읽기 ───────────────────────────────────
    dump_path = root / "work" / "ae-template.json"
    dump = None
    if dump_path.is_file() and not args.reread:
        stored = json.loads(dump_path.read_text(encoding="utf-8"))
        if not wiz.dump_has_timing(stored):
            # 예전 형식이라 곡/인트로 구분에 필요한 정보가 없다. 다시 읽는다.
            print("전에 읽어둔 구조에는 타이밍 정보가 없어 다시 읽습니다.")
        elif wiz.ask_yes("전에 읽어둔 템플릿 구조를 그대로 쓸까요?", True,
                         assume_yes=yes):
            dump = stored
    if dump is None:
        print("After Effects 로 템플릿 구조를 읽는 중… (창이 잠깐 뜹니다)")
        stub = Config(path=dst if dst.exists() else root / "config.toml",
                      data={"ae": {"project": str(template),
                                   "app": args.ae_app or ""}})
        try:
            dump = ae_mod.inspect_template(stub, dump_path)
        except ae_mod.AEError as exc:
            print(f"\n템플릿을 읽지 못했습니다:\n  {exc}\n")
            print("AE 를 닫고 다시 시도하거나, --ae-app 으로 실행파일 경로를 알려주세요.")
            return 1
    print()

    info = wiz.derive_from_dump(dump)

    # 3. 메인 컴프 ──────────────────────────────────────────
    comp_names = list(info["comps"])
    main_comp = wiz.confirm_or_choose(
        "최종 출력할 메인 컴프", info["main_comp"], comp_names, assume_yes=yes)
    if main_comp != info["main_comp"]:
        main = info["comps"].get(main_comp, {})
        info["main_comp"] = main_comp
        info["precomps"] = wiz.precomps_in(main)
        info["width"] = main.get("width", info["width"])
        info["height"] = main.get("height", info["height"])
        info["fps"] = main.get("fps", info["fps"])
        info["has_audio_layer"] = any(
            l.get("kind") == "audio" for l in main.get("layers", []))
    print(f"  → {main_comp}  {info['width']}x{info['height']} @{info['fps']}fps\n")

    # 4. 곡 컴프 ────────────────────────────────────────────
    precomps = info["precomps"]
    if not precomps:
        print(f"'{main_comp}' 안에 프리컴프가 없습니다. 다른 컴프인지 확인해 주세요.")
        return 1
    guessed = wiz.guess_non_songs(info["comps"].get(main_comp, {}), info["comps"])
    print(f"'{main_comp}' 안의 컴프 {len(precomps)}개입니다.")
    for i, name in enumerate(precomps, start=1):
        why = guessed.get(i - 1)
        mark = f"   ← 곡 아님? ({', '.join(why)})" if why else ""
        print(f"  {i}. {name}{mark}")

    default = ",".join(str(i + 1) for i in sorted(guessed))
    if args.songs and len(precomps) != args.songs:
        need = len(precomps) - args.songs
        print(f"\n곡은 {args.songs}개인데 컴프가 {len(precomps)}개입니다. "
              f"{need}개를 빼야 합니다.")
    if guessed:
        print(f"\n곡이 아닌 것으로 {len(guessed)}개를 골랐습니다. "
              "맞으면 Enter, 다르면 번호를 다시 입력하세요.")
    else:
        print("\n전부 같은 모양이라 곡이 아닌 걸 가려내지 못했습니다.")
        print("인트로처럼 곡이 아닌 게 있으면 번호를, 없으면 Enter 를 누르세요.")
    # 알아들을 수 없는 입력을 조용히 무시하면 엉뚱한 설정이 만들어진다.
    # 다시 물어본다.
    while True:
        raw = wiz.ask("곡이 아닌 것의 번호 (쉼표로 구분, 없으면 0)",
                      default or "0", assume_yes=yes)
        cleaned = raw.strip().lower()
        if cleaned in ("0", "없음", "none", "-"):
            excluded: set[int] = set()
            break
        pieces = [p for p in cleaned.replace(" ", "").split(",") if p]
        bad = [p for p in pieces
               if not p.isdigit() or not 1 <= int(p) <= len(precomps)]
        if bad:
            print(f"  '{', '.join(bad)}' 을(를) 못 알아들었습니다. "
                  f"1 ~ {len(precomps)} 사이 번호를 쉼표로 구분해서 "
                  "입력하거나, 뺄 게 없으면 0 을 입력하세요.")
            if yes:
                excluded = set()
                break
            continue
        excluded = {int(p) - 1 for p in pieces}
        if args.songs and len(precomps) - len(excluded) != args.songs:
            got = len(precomps) - len(excluded)
            print(f"  곡이 {got}개 남습니다. {args.songs}개여야 하는데 "
                  f"{abs(got - args.songs)}개 {'많습니다' if got > args.songs else '모자랍니다'}.")
            print(f"  빼야 할 개수: {len(precomps) - args.songs}개")
            if yes:
                break
            continue
        break
    songs = [n for i, n in enumerate(precomps) if i not in excluded]

    if args.songs and len(songs) != args.songs:
        print(f"\n곡 수가 맞지 않습니다: {len(songs)}개 (기대: {args.songs}개)")
        return 1
    if not songs:
        print("곡이 하나도 남지 않았습니다.")
        return 1
    print(f"  → 곡 {len(songs)}개" +
          (f" (제외 {len(excluded)}개)" if excluded else "") + "\n")

    # 5. 나머지 ─────────────────────────────────────────────
    manual = wiz.ask_yes("오디오를 직접 합쳐서 넣으시나요?", True, assume_yes=yes)
    repeat = 1 if wiz.ask_yes("전체를 한 번 더 반복해서 길이를 두 배로 할까요?",
                              False, assume_yes=yes) else 0
    channel = wiz.ask("채널 이름", root.name, assume_yes=yes)

    app = aerender = ""
    try:
        app = str(ae_mod.find_binary("app", args.ae_app or None))
        aerender = str(ae_mod.find_binary("aerender", None))
    except ae_mod.AEError:
        pass

    try:
        project_path = str(template.resolve().relative_to(root))
    except ValueError:
        # 작업 폴더 밖이면 절대경로 그대로 쓴다.
        project_path = str(template.resolve())

    answers = {
        "project": project_path,
        "main_comp": main_comp,
        "slot_comps": songs,
        "width": info["width"], "height": info["height"], "fps": info["fps"],
        "mode": "full",
        "audio_source": "manual" if manual else "pipeline",
        "repeat": repeat,
        "channel_name": channel,
        "image_layer": wiz.pick_selector(info["comps"], songs, "still") or "@image",
        "title_layer": wiz.pick_selector(info["comps"], songs, "text"),
        "audio_layer": "@audio" if info["has_audio_layer"] else "",
        "render_settings": info["render_settings"],
        "output_module": info["output_module"],
        "app": app, "aerender": aerender,
    }
    dst.write_text(wiz.render_config(answers), encoding="utf-8")

    print(f"\n{dst} 를 만들었습니다.\n")
    print("이어서 확인해 보세요:")
    print("  plpipe check")
    return 0


def cmd_check(args) -> int:
    """렌더 전에 설정이 맞는지 점검한다."""
    cfg = Config.load(Path(args.config).resolve() if args.config else None)
    try:
        proj = Project.resolve(cfg.projects_dir, getattr(args, "project", None))
    except ProjectError:
        proj = None  # 아직 프로젝트를 안 만들었어도 설정은 검사할 수 있다.

    print(f"설정: {cfg.path}")
    if proj is not None:
        print(f"프로젝트: {proj.slug}")
    print()
    return 1 if check_mod.report(check_mod.run(cfg, proj)) else 0


def cmd_status(args) -> int:
    cfg, proj = _load(args)
    repeat = _repeat(cfg)
    print(f"프로젝트: {proj.slug}  ({proj.root})")
    print(f"생성일: {proj.created}")
    if proj.vars:
        print("변수: " + ", ".join(f"{k}={v}" for k, v in proj.vars.items()))
    print()
    print(f"{'#':>3}  {'제목':<28} {'오디오':<7} {'이미지':<7} {'길이':>8}")
    for track in proj.tracks:
        dur = f"{track.duration:.1f}s" if track.duration else "-"
        print(
            f"{track.index:>3}  {track.display_title[:28]:<28} "
            f"{'있음' if track.audio else '없음':<7} "
            f"{'있음' if track.image else '없음':<7} {dur:>8}"
        )
    if proj.timeline:
        tl = _timeline(proj)
        print(f"\n총 길이: {_hms(tl.total)}  ({len(tl.segments)}구간, 반복 {repeat}회)")
    if proj.stage:
        print("\n진행: " + ", ".join(f"{k}={v}" for k, v in proj.stage.items()))
    return 0


def cmd_prompts(args) -> int:
    """수동 생성용 프롬프트를 뽑아준다."""
    cfg, proj = _load(args)
    out = proj.out / "prompts.txt"
    lines: list[str] = []
    for track in proj.tracks:
        lines.append(f"── {track.index:02d}. {track.display_title}")
        lines.append(f"  [음악] {track.prompt or '(manifest.json 에 prompt 를 채우세요)'}")
        lines.append(
            f"  [이미지] {track.image_prompt or '(manifest.json 에 image_prompt 를 채우세요)'}"
        )
        lines.append("")
    text = "\n".join(lines)
    proj.out.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"저장: {out}")
    return 0


def cmd_music(args) -> int:
    cfg, proj = _load(args)
    kind = cfg.get("music.provider", "manual")
    provider = music_provider.build(kind, cfg)
    print(f"음악 프로바이더: {provider.name}")
    for track in proj.tracks:
        if track.audio and not args.force:
            continue
        if not track.prompt:
            raise ProviderError(
                f"트랙 {track.index} 에 prompt 가 없습니다. manifest.json 을 채우세요."
            )
        print(f"  [{track.index:02d}] 생성 중…")
        path = provider.generate(track.index, track.prompt, proj.drop_audio)
        track.audio = proj.rel(path)
        proj.save()
    proj.mark("music")
    return 0


def cmd_images(args) -> int:
    cfg, proj = _load(args)
    kind = cfg.get("image.provider", "manual")
    provider = image_provider.build(kind, cfg)
    width = int(cfg.get("image.width", 2048))
    height = int(cfg.get("image.height", 1152))
    print(f"이미지 프로바이더: {provider.name}")
    for track in proj.tracks:
        if track.image and not args.force:
            continue
        prompt = track.image_prompt or track.prompt
        if not prompt:
            raise ProviderError(
                f"트랙 {track.index} 에 image_prompt 가 없습니다. manifest.json 을 채우세요."
            )
        print(f"  [{track.index:02d}] 생성 중…")
        path = provider.generate(track.index, prompt, proj.drop_images, width, height)
        track.image = proj.rel(path)
        proj.save()
    proj.mark("images")
    return 0


def cmd_ingest(args) -> int:
    """drop 폴더의 파일을 트랙에 배정한다.

    파일명 앞에 번호가 있으면 그 번호로, 없으면 정렬 순서대로 붙인다.
    """
    cfg, proj = _load(args)
    audio_files = scan_media(proj.drop_audio, AUDIO_EXTS)
    image_files = scan_media(proj.drop_images, IMAGE_EXTS)

    def assign(files: list[Path], attr: str, label: str) -> int:
        by_index: dict[int, Path] = {}
        loose: list[Path] = []
        for path in files:
            idx = parse_index(path)
            if idx is not None and 1 <= idx <= len(proj.tracks) and idx not in by_index:
                by_index[idx] = path
            else:
                loose.append(path)
        # 번호 없는 파일은 빈 자리에 순서대로 채운다.
        for track in proj.tracks:
            if track.index in by_index or not loose:
                continue
            by_index[track.index] = loose.pop(0)

        count = 0
        for track in proj.tracks:
            path = by_index.get(track.index)
            if path is None:
                continue
            setattr(track, attr, proj.rel(path))
            if attr == "audio" and not track.title:
                track.title = _title_from(path)
            count += 1
        if loose:
            print(f"  경고: 배정하지 못한 {label} {len(loose)}개 "
                  f"(트랙 {len(proj.tracks)}개보다 파일이 많습니다)")
        return count

    n_audio = assign(audio_files, "audio", "오디오")
    n_image = assign(image_files, "image", "이미지")
    proj.save()

    # 오디오를 직접 합쳐서 넣는 경우 곡별 파일은 없어도 된다.
    # (있으면 경계 검출 결과를 대조하는 데 쓴다.)
    audio_optional = cfg.get("audio.source", "pipeline") == "manual"

    print(f"오디오 {n_audio}/{len(proj.tracks)} · 이미지 {n_image}/{len(proj.tracks)} 배정")
    missing_audio = [t.index for t in proj.missing_audio()]
    missing_image = [t.index for t in proj.missing_images()]
    if missing_audio:
        note = " (선택 사항)" if audio_optional else ""
        print(f"  오디오 없음{note}: {missing_audio}")
    if missing_image:
        print(f"  이미지 없음: {missing_image}")
    if missing_image or (missing_audio and not audio_optional):
        return 1
    proj.mark("ingest")
    return 0


def _title_from(path: Path) -> str:
    stem = path.stem
    # "01 - Neon Rain" / "01_neon-rain" → "Neon Rain"
    stripped = stem
    idx = parse_index(path)
    if idx is not None:
        stripped = stem[len(str(idx)) :] if stem.startswith(str(idx)) else stem
        stripped = stripped.lstrip(" -_.")
    return stripped.replace("_", " ").strip() or stem


def cmd_cue(args) -> int:
    """이미 합쳐진 마스터 오디오에서 곡 경계를 찾는다."""
    cfg, proj = _load(args)
    master = _master(proj, cfg)
    fps = float(cfg.get("video.fps", 24))

    threshold = args.threshold if args.threshold is not None else float(
        cfg.get("audio.cue_threshold_db", -45.0))
    min_silence = args.min_silence if args.min_silence is not None else float(
        cfg.get("audio.cue_min_silence", 0.4))

    print(f"곡 경계 검출: {master.name}  (임계 {threshold}dB, 최소 무음 {min_silence}s)")
    cues, total = cue_mod.detect(
        master, len(proj.tracks), threshold_db=threshold, min_silence=min_silence
    )

    warnings = cue_mod.compare_with_tracks(
        cues, total, [t.duration for t in proj.tracks]
    )

    items = list(proj.sequence(0))
    timeline = audio_mod.build_timeline_from_cues(items, cues, total, fps=fps)
    proj.timeline = timeline.to_dict()
    proj.save()

    print(f"\n{'#':>3}  {'시작':>10}  {'구간':>9}  제목")
    for seg in timeline.segments:
        print(f"{seg.index:>3}  {_hms(seg.start):>10}  "
              f"{seg.duration:>8.1f}s  {seg.title}")
    print(f"\n총 길이 {_hms(timeline.total)}")

    for warning in warnings:
        print(f"경고: {warning}")
    if warnings:
        print("\n경계가 틀렸으면 --threshold / --min-silence 를 조정하거나,")
        print("manifest.json 의 timeline.segments 에서 start 를 직접 고치세요.")
    proj.mark("cue")
    return 0


def cmd_audio(args) -> int:
    cfg, proj = _load(args)

    if cfg.get("audio.source", "pipeline") == "manual":
        # 오디오를 직접 합쳐서 넣는 경우: 합치지 않고 경계만 찾는다.
        print("오디오 소스가 'manual' 입니다. 합치지 않고 곡 경계만 검출합니다.")
        return cmd_cue(args)

    if proj.missing_audio():
        raise ProjectError("오디오가 배정되지 않은 트랙이 있습니다. `plpipe ingest` 를 실행하세요.")

    print("라우드니스 정규화")
    audio_mod.normalize_all(proj, cfg.get("audio", {}) or {}, force=args.force)

    repeat = _repeat(cfg)
    items = list(proj.sequence(repeat))
    timeline = audio_mod.build_timeline(
        items,
        fps=float(cfg.get("video.fps", 24)),
        gap=float(cfg.get("project.gap", 0.0)),
        crossfade=float(cfg.get("project.crossfade", 0.0)),
        lead_in=float(cfg.get("project.lead_in", 0.0)),
        lead_out=float(cfg.get("project.lead_out", 0.0)),
    )
    proj.timeline = timeline.to_dict()
    proj.save()

    print(f"타임라인: {len(timeline.segments)}구간 · 총 {_hms(timeline.total)}")
    print("마스터 오디오 생성")
    master = audio_mod.concat_master(
        proj, timeline, items,
        sample_rate=int(cfg.get("audio.sample_rate", 48000)),
        master_format=str(cfg.get("audio.master_format", "wav")),
    )
    actual = ffmpeg.duration(master)
    drift = abs(actual - timeline.total)
    print(f"  {master}  ({_hms(actual)})")
    if drift > 0.5:
        print(f"  경고: 계산된 길이와 {drift:.2f}s 차이가 납니다.")
    proj.mark("audio")
    return 0


def cmd_ae_inspect(args) -> int:
    cfg = Config.load(Path(args.config).resolve() if args.config else None)
    out = cfg.root / "work" / "ae-template.json"
    report = ae_mod.inspect_template(cfg, out)
    print(f"컴프 {len(report.get('comps', []))}개\n")
    for comp in report.get("comps", []):
        print(f"  {comp['name']}  {comp['width']}x{comp['height']} "
              f"@{comp['fps']}fps  {comp['duration']}s")
        for layer in comp.get("layers", []):
            flag = " [키프레임된 스케일]" if layer.get("scale_keyframed") else ""
            print(f"      {layer['index']:>2}. {layer['name']}  ({layer['kind']}){flag}")
        print()
    templates = report.get("templates", {})
    if templates.get("render_settings"):
        print("렌더 설정 템플릿: " + ", ".join(templates["render_settings"]))
    if templates.get("output_modules"):
        print("출력 모듈 템플릿: " + ", ".join(templates["output_modules"]))
    print(f"\n원본: {out}")
    print("이 이름들을 config.toml 의 [ae] 항목에 적어주세요.")
    return 0


def cmd_ae_build(args) -> int:
    cfg, proj = _load(args)
    timeline = _timeline(proj)
    items = list(proj.sequence(_repeat(cfg)))
    master = _master(proj, cfg) if cfg.get("ae.mode", "segments") == "full" else None
    job = ae_mod.build_job(proj, timeline, items, cfg, master)
    aep = ae_mod.build_ae_project(job, proj, cfg)
    print(f"AE 프로젝트: {aep}")
    proj.mark("ae_build")
    return 0


def cmd_render(args) -> int:
    cfg, proj = _load(args)
    timeline = _timeline(proj)
    items = list(proj.sequence(_repeat(cfg)))
    master = _master(proj, cfg)
    mode = cfg.get("ae.mode", "segments")

    if mode == "ffmpeg":
        print("AE 없이 스틸 이미지로 클립 생성")
        render.stills_to_segments(proj, timeline, cfg)
        segments = {t.index: proj.path(t.segment) for t in proj.tracks}  # type: ignore[misc]
    else:
        if not args.skip_ae:
            aep = proj.work / "ae" / f"{proj.slug}.aep"
            if not aep.is_file():
                raise ProjectError(f"AE 프로젝트가 없습니다: {aep}\n`plpipe ae build` 를 실행하세요.")
            ae_mod.aerender(aep, cfg)
        if mode == "full":
            ext = cfg.get("ae.intermediate_ext", "mov")
            intermediate = proj.work / "ae" / f"{proj.slug}.{ext}"
            out = render.encode_full(proj, timeline, intermediate, master, cfg)
            print(f"완성: {out}")
            proj.mark("render")
            return 0
        segments = render.collect_segments(proj, cfg)

    out = render.assemble(proj, timeline, items, segments, master, cfg)  # type: ignore[arg-type]
    print(f"완성: {out}  ({_hms(ffmpeg.duration(out))})")
    proj.mark("render")
    return 0


def cmd_meta(args) -> int:
    cfg, proj = _load(args)
    timeline = _timeline(proj)
    path, warnings = metadata.write_all(proj, timeline, cfg)
    print(path.read_text(encoding="utf-8"))
    for warning in warnings:
        print(f"경고: {warning}")
    proj.mark("meta")
    return 0


def cmd_run(args) -> int:
    """ingest → audio → ae build → render → meta 를 한 번에."""
    steps = [
        ("ingest", cmd_ingest),
        ("audio", cmd_audio),
        ("ae build", cmd_ae_build),
        ("render", cmd_render),
        ("meta", cmd_meta),
    ]
    cfg = Config.load(Path(args.config).resolve() if args.config else None)
    if cfg.get("ae.mode", "segments") == "ffmpeg":
        steps = [s for s in steps if s[0] != "ae build"]

    for name, fn in steps:
        print(f"\n{'=' * 8} {name} {'=' * 8}")
        code = fn(args)
        if code:
            print(f"\n{name} 단계에서 중단되었습니다.")
            return code
    print("\n전체 완료. out/ 폴더의 mp4 와 metadata.txt 로 업로드하세요.")
    return 0


# ── 파서 ────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plpipe",
        description="유튜브 플레이리스트 영상 파이프라인 (생성 → 마스터링 → AE → 렌더)",
    )
    parser.add_argument("-c", "--config", help="config.toml 경로")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, fn, help_text: str, *, needs_project: bool = True):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=fn)
        if needs_project:
            p.add_argument("-p", "--project", help="프로젝트 slug (기본: 가장 최근)")
        return p

    p_init = add("init", cmd_init, "작업 폴더에 config.toml 을 만든다", needs_project=False)
    p_init.add_argument("dir", nargs="?", help="대상 폴더 (기본: 현재 폴더)")
    p_init.add_argument("--force", action="store_true")

    p_new = add("new", cmd_new, "새 영상 프로젝트를 만든다", needs_project=False)
    p_new.add_argument("name", help="프로젝트 이름")
    p_new.add_argument("-n", "--tracks", type=int, help="곡 수 (기본: 설정값)")
    p_new.add_argument("--titles", help="곡 제목 목록 파일 (`제목 | 프롬프트` 한 줄에 하나)")
    p_new.add_argument("--var", action="append", help="제목 템플릿 변수 (key=value)")

    p_setup = add("setup", cmd_setup, "물어보고 config.toml 을 대신 만들어 준다",
                  needs_project=False)
    p_setup.add_argument("dir", nargs="?", help="작업 폴더 (기본: 현재 폴더)")
    p_setup.add_argument("--force", action="store_true", help="기존 설정을 덮어쓴다")
    p_setup.add_argument("--reread", action="store_true",
                         help="템플릿 구조를 다시 읽는다")
    p_setup.add_argument("--template", help=".aep 경로 (작업 폴더 밖이어도 됨)")
    p_setup.add_argument("--songs", type=int,
                         help="곡 수를 못 박는다. 이 수와 다르면 넘어가지 않는다")
    p_setup.add_argument("--ae-app", help="AfterFX 실행파일 경로")
    p_setup.add_argument("--yes", action="store_true", help="전부 기본값으로")

    add("check", cmd_check, "렌더 전에 설정이 맞는지 점검한다")
    add("status", cmd_status, "프로젝트 진행 상황을 본다")
    add("prompts", cmd_prompts, "수동 생성용 프롬프트를 뽑는다")

    p_music = add("music", cmd_music, "설정된 프로바이더로 곡을 생성한다")
    p_music.add_argument("--force", action="store_true", help="이미 있는 것도 다시 생성")

    p_images = add("images", cmd_images, "설정된 프로바이더로 커버 아트를 생성한다")
    p_images.add_argument("--force", action="store_true")

    add("ingest", cmd_ingest, "drop 폴더의 파일을 트랙에 배정한다")

    def add_cue_options(parser_obj):
        parser_obj.add_argument("--threshold", type=float,
                                help="무음으로 볼 기준 dB (기본 -45)")
        parser_obj.add_argument("--min-silence", type=float,
                                help="곡 경계로 볼 최소 무음 길이(초, 기본 0.4)")

    p_audio = add("audio", cmd_audio, "정규화 + 타임라인 + 마스터 오디오")
    p_audio.add_argument("--force", action="store_true", help="정규화를 다시 수행")
    add_cue_options(p_audio)

    p_cue = add("cue", cmd_cue, "합쳐진 마스터 오디오에서 곡 경계를 찾는다")
    add_cue_options(p_cue)

    p_ae = sub.add_parser("ae", help="After Effects 관련")
    ae_sub = p_ae.add_subparsers(dest="ae_command", required=True)
    p_inspect = ae_sub.add_parser("inspect", help="템플릿 구조를 덤프한다")
    p_inspect.set_defaults(func=cmd_ae_inspect)
    p_build = ae_sub.add_parser("build", help="템플릿에서 렌더용 .aep 를 만든다")
    p_build.set_defaults(func=cmd_ae_build)
    p_build.add_argument("-p", "--project")

    p_render = add("render", cmd_render, "aerender 실행 후 최종 mp4 조립")
    p_render.add_argument("--skip-ae", action="store_true",
                          help="이미 렌더된 클립을 재사용하고 조립만 한다")

    add("meta", cmd_meta, "제목/설명/챕터를 만든다")

    p_run = add("run", cmd_run, "ingest부터 meta까지 전부 실행")
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--skip-ae", action="store_true")
    add_cue_options(p_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not ffmpeg.available() and args.command not in {"init", "new", "status", "prompts"}:
        print("경고: ffmpeg/ffprobe 를 찾을 수 없습니다. 오디오·렌더 단계가 실패합니다.\n",
              file=sys.stderr)
    try:
        return args.func(args)
    except wiz.SetupCancelled as exc:
        print(f"\n{exc} 설정을 만들지 않았습니다.", file=sys.stderr)
        return 130
    except (ConfigError, ProjectError, ProviderError, ae_mod.AEError,
            render.RenderError, ffmpeg.FFmpegError, FileNotFoundError) as exc:
        print(f"\n오류: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n중단됨", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
