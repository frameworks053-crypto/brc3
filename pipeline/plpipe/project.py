"""프로젝트(= 영상 한 편) 상태 관리.

한 프로젝트는 폴더 하나이고, 모든 상태는 manifest.json 에 들어간다.
어느 단계에서 끊겨도 manifest 만 있으면 이어서 진행할 수 있다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

MANIFEST_NAME = "manifest.json"

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}

# 파일명 앞의 트랙 번호: "01 - foo.mp3", "3_bar.png", "07.png"
# `\b` 를 쓰면 밑줄이 단어 문자라서 "13_rain" 이 안 잡힌다. 뒤에 숫자만
# 오지 않으면 되므로 부정 전방탐색을 쓴다. (연도 "2026-…" 는 잡히지 않는다)
_INDEX_RE = re.compile(r"^\s*(\d{1,3})(?![0-9])")


class ProjectError(RuntimeError):
    pass


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w가-힣]+", "-", text, flags=re.UNICODE)
    return re.sub(r"-{2,}", "-", text).strip("-") or "untitled"


def parse_index(path: Path) -> int | None:
    """파일명 앞부분에서 트랙 번호를 뽑는다. 없으면 None."""
    m = _INDEX_RE.match(path.stem)
    return int(m.group(1)) if m else None


def _sort_key(path: Path) -> tuple[int, str]:
    """번호가 있으면 번호순, 없으면 이름순(번호 있는 것들 뒤로)."""
    idx = parse_index(path)
    return (idx if idx is not None else 10**6, path.stem.lower())


def scan_media(folder: Path, exts: set[str]) -> list[Path]:
    if not folder.is_dir():
        return []
    files = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in exts and not p.name.startswith(".")
    ]
    return sorted(files, key=_sort_key)


@dataclass
class Track:
    index: int
    title: str = ""
    prompt: str = ""
    image_prompt: str = ""
    audio: str | None = None       # 프로젝트 폴더 기준 상대경로
    image: str | None = None
    duration: float | None = None  # 정규화 후 실제 길이(초)
    normalized: str | None = None
    segment: str | None = None     # AE 가 렌더한 곡별 클립

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "prompt": self.prompt,
            "image_prompt": self.image_prompt,
            "audio": self.audio,
            "image": self.image,
            "duration": self.duration,
            "normalized": self.normalized,
            "segment": self.segment,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Track":
        return cls(
            index=int(d["index"]),
            title=d.get("title", ""),
            prompt=d.get("prompt", ""),
            image_prompt=d.get("image_prompt", ""),
            audio=d.get("audio"),
            image=d.get("image"),
            duration=d.get("duration"),
            normalized=d.get("normalized"),
            segment=d.get("segment"),
        )

    @property
    def display_title(self) -> str:
        return self.title or f"Track {self.index:02d}"


@dataclass
class Project:
    root: Path
    slug: str
    created: str = ""
    vars: dict[str, str] = field(default_factory=dict)
    tracks: list[Track] = field(default_factory=list)
    timeline: dict[str, Any] = field(default_factory=dict)
    stage: dict[str, str] = field(default_factory=dict)

    # ── 표준 하위 폴더 ───────────────────────────────────────
    @property
    def drop_audio(self) -> Path:
        return self.root / "drop" / "audio"

    @property
    def drop_images(self) -> Path:
        return self.root / "drop" / "images"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def out(self) -> Path:
        return self.root / "out"

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME

    def path(self, rel: str | None) -> Path | None:
        """manifest 에 저장된 상대경로를 절대경로로."""
        return (self.root / rel) if rel else None

    def rel(self, path: Path) -> str:
        """절대경로를 manifest 저장용 상대경로로. 프로젝트 밖이면 절대경로 유지."""
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    # ── 생성 / 로드 / 저장 ───────────────────────────────────
    @classmethod
    def create(
        cls, projects_dir: Path, slug: str, track_count: int, vars: dict[str, str]
    ) -> "Project":
        root = projects_dir / slug
        if root.exists():
            raise ProjectError(f"프로젝트가 이미 있습니다: {root}")
        proj = cls(
            root=root,
            slug=slug,
            created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            vars=dict(vars),
            tracks=[Track(index=i) for i in range(1, track_count + 1)],
        )
        for folder in (proj.drop_audio, proj.drop_images, proj.work, proj.out):
            folder.mkdir(parents=True, exist_ok=True)
        proj.save()
        return proj

    @classmethod
    def load(cls, root: Path) -> "Project":
        path = root / MANIFEST_NAME
        if not path.is_file():
            raise ProjectError(f"manifest 가 없습니다: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            root=root,
            slug=data.get("slug", root.name),
            created=data.get("created", ""),
            vars=data.get("vars", {}),
            tracks=[Track.from_dict(t) for t in data.get("tracks", [])],
            timeline=data.get("timeline", {}),
            stage=data.get("stage", {}),
        )

    @classmethod
    def resolve(cls, projects_dir: Path, slug: str | None) -> "Project":
        """slug 를 주면 그 프로젝트, 안 주면 가장 최근 프로젝트를 연다."""
        if slug:
            return cls.load(projects_dir / slug)
        candidates = [
            p for p in projects_dir.glob("*") if (p / MANIFEST_NAME).is_file()
        ] if projects_dir.is_dir() else []
        if not candidates:
            raise ProjectError(
                f"{projects_dir} 에 프로젝트가 없습니다. `plpipe new` 로 만드세요."
            )
        latest = max(candidates, key=lambda p: (p / MANIFEST_NAME).stat().st_mtime)
        return cls.load(latest)

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "slug": self.slug,
            "created": self.created,
            "vars": self.vars,
            "tracks": [t.to_dict() for t in self.tracks],
            "timeline": self.timeline,
            "stage": self.stage,
        }
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        tmp.replace(self.manifest_path)

    def mark(self, stage: str, status: str = "done") -> None:
        self.stage[stage] = status
        self.save()

    # ── 트랙 헬퍼 ────────────────────────────────────────────
    def track(self, index: int) -> Track:
        for t in self.tracks:
            if t.index == index:
                return t
        raise ProjectError(f"트랙 {index} 가 없습니다.")

    def sequence(self, repeat: int) -> Iterator[tuple[int, Track]]:
        """반복까지 펼친 재생 순서. (pass 번호, 트랙) 를 순서대로 낸다."""
        for p in range(repeat + 1):
            for t in self.tracks:
                yield p, t

    def missing_audio(self) -> list[Track]:
        return [t for t in self.tracks if not t.audio]

    def missing_images(self) -> list[Track]:
        return [t for t in self.tracks if not t.image]
