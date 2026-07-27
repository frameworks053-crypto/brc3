"""업로드용 메타데이터 생성: 제목, 설명, 챕터 타임스탬프, 태그."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audio import Timeline
from .project import Project

# YouTube 챕터 규칙
MIN_CHAPTERS = 3
MIN_CHAPTER_SECONDS = 10
DESCRIPTION_LIMIT = 5000
TITLE_LIMIT = 100
TAGS_CHAR_LIMIT = 500


def timestamp(seconds: float, force_hours: bool = False) -> str:
    total = int(seconds)  # 챕터는 내림해야 실제 구간보다 앞서지 않는다.
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h or force_hours:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


@dataclass
class Chapter:
    start: float
    label: str

    def line(self, force_hours: bool) -> str:
        return f"{timestamp(self.start, force_hours)} {self.label}"


def build_chapters(
    timeline: Timeline, *, all_passes: bool = True, mark_repeat: bool = True
) -> list[Chapter]:
    """타임라인에서 YouTube 챕터 목록을 만든다.

    첫 챕터는 반드시 0:00 이어야 하므로, lead-in 이 있으면 첫 곡의 시작이
    아니라 0 에서 시작시킨다.
    """
    chapters: list[Chapter] = []
    for seg in timeline.segments:
        if seg.pass_no > 0 and not all_passes:
            break
        label = seg.title
        if seg.pass_no > 0 and mark_repeat:
            label = f"{label} (repeat)"
        chapters.append(Chapter(start=seg.start, label=label))

    if chapters:
        chapters[0] = Chapter(start=0.0, label=chapters[0].label)
    return chapters


def validate_chapters(chapters: list[Chapter], total: float) -> list[str]:
    """YouTube 가 챕터를 인식하지 않는 조건을 미리 잡아낸다."""
    problems: list[str] = []
    if len(chapters) < MIN_CHAPTERS:
        problems.append(
            f"챕터가 {len(chapters)}개뿐입니다. YouTube 는 최소 {MIN_CHAPTERS}개를 요구합니다."
        )
    if chapters and chapters[0].start != 0:
        problems.append("첫 챕터가 0:00 이 아닙니다.")
    for i, chapter in enumerate(chapters):
        end = chapters[i + 1].start if i + 1 < len(chapters) else total
        if end - chapter.start < MIN_CHAPTER_SECONDS:
            problems.append(
                f"챕터 '{chapter.label}' 길이가 {end - chapter.start:.0f}초로 "
                f"{MIN_CHAPTER_SECONDS}초 미만입니다."
            )
    return problems


def render_title(template: str, project: Project, timeline: Timeline) -> str:
    values: dict[str, Any] = {
        "n": len(project.tracks),
        "duration": timestamp(timeline.total, force_hours=True),
        "slug": project.slug,
        **project.vars,
    }
    # 템플릿에 없는 자리표시자는 조용히 비운다.
    def sub(match: re.Match[str]) -> str:
        return str(values.get(match.group(1), ""))

    title = re.sub(r"\{(\w+)\}", sub, template)
    return re.sub(r"\s{2,}", " ", title).strip(" —·-")


def build_description(
    project: Project,
    timeline: Timeline,
    chapters: list[Chapter],
    cfg,
) -> str:
    force_hours = timeline.total >= 3600
    lines: list[str] = []

    intro = cfg.get("channel.description_intro")
    if intro:
        lines += [str(intro).strip(), ""]

    lines.append("Tracklist")
    for chapter in chapters:
        lines.append(chapter.line(force_hours))

    outro = cfg.get("channel.description_outro")
    if outro:
        lines += ["", str(outro).strip()]

    lines += [
        "",
        "─" * 24,
        "All music and artwork generated with AI. "
        "본 영상의 음악과 이미지는 AI로 제작되었습니다.",
    ]
    return "\n".join(lines)


def build_tags(cfg) -> list[str]:
    raw = cfg.get("channel.tags", []) or []
    tags: list[str] = []
    used = 0
    for tag in raw:
        tag = str(tag).strip()
        if not tag:
            continue
        cost = len(tag) + 1
        if used + cost > TAGS_CHAR_LIMIT:
            break
        tags.append(tag)
        used += cost
    return tags


def write_all(project: Project, timeline: Timeline, cfg) -> tuple[Path, list[str]]:
    """메타데이터 파일을 out/ 에 쓰고 (경로, 경고 목록) 을 돌려준다."""
    chapters = build_chapters(
        timeline,
        all_passes=bool(cfg.get("channel.chapters_all_passes", True)),
        mark_repeat=bool(cfg.get("channel.mark_repeat", True)),
    )
    warnings = validate_chapters(chapters, timeline.total)

    title = render_title(
        str(cfg.get("channel.title", "{slug}")), project, timeline
    )
    if len(title) > TITLE_LIMIT:
        warnings.append(f"제목이 {len(title)}자입니다. YouTube 한도는 {TITLE_LIMIT}자입니다.")

    description = build_description(project, timeline, chapters, cfg)
    if len(description) > DESCRIPTION_LIMIT:
        warnings.append(
            f"설명이 {len(description)}자입니다. YouTube 한도는 {DESCRIPTION_LIMIT}자입니다."
        )

    tags = build_tags(cfg)

    project.out.mkdir(parents=True, exist_ok=True)
    txt = project.out / "metadata.txt"
    txt.write_text(
        "\n".join([
            "── 제목 " + "─" * 30,
            title,
            "",
            "── 설명 " + "─" * 30,
            description,
            "",
            "── 태그 " + "─" * 30,
            ", ".join(tags),
            "",
        ]),
        encoding="utf-8",
    )

    (project.out / "metadata.json").write_text(
        json.dumps(
            {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": cfg.get("channel.category_id", 10),
                "defaultLanguage": cfg.get("channel.language", "ko"),
                "duration": timeline.total,
                "chapters": [{"start": c.start, "label": c.label} for c in chapters],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return txt, warnings
