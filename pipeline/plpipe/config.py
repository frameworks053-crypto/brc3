"""설정 로딩. tomllib(표준 라이브러리)만 사용하므로 외부 의존성이 없다."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_NAME = "config.toml"
EXAMPLE_NAME = "config.example.toml"


class ConfigError(RuntimeError):
    pass


def find_config(start: Path | None = None) -> Path:
    """현재 디렉터리부터 위로 올라가며 config.toml 을 찾는다."""
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        path = candidate / CONFIG_NAME
        if path.is_file():
            return path
    raise ConfigError(
        f"{CONFIG_NAME} 을 찾을 수 없습니다. `plpipe init` 을 먼저 실행하세요."
    )


@dataclass
class Config:
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        """설정 파일이 있는 폴더. 모든 상대경로의 기준."""
        return self.path.parent

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or find_config()
        raw = path.read_bytes()

        # 윈도우 메모장이 "UTF-8" 로 저장하면 앞에 BOM 을 붙인다. TOML 은
        # 이걸 허용하지 않아서 "1행 1열이 잘못됨" 이라는 엉뚱한 오류가 난다.
        # 그냥 떼어내면 되는 문제라 조용히 처리한다.
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigError(
                f"{path} 를 읽지 못했습니다. UTF-8 로 저장되어 있지 않습니다.\n"
                "메모장에서 [파일 > 다른 이름으로 저장] 을 누르고 아래쪽\n"
                "'인코딩' 을 'UTF-8' 로 바꿔서 덮어써 주세요.\n"
                "(한글 주석이 들어 있으면 ANSI 로는 저장하면 안 됩니다.)"
            ) from exc

        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(
                f"{path} 의 문법이 잘못됐습니다.\n  {exc}\n\n"
                "자주 나오는 원인:\n"
                "  · 윈도우 경로의 역슬래시 — \"C:\\Program Files\" 대신\n"
                "    \"C:/Program Files\" 처럼 슬래시를 쓰거나 작은따옴표로 감싸세요.\n"
                "  · 문자열에 큰따옴표를 안 씀 — mode = full  →  mode = \"full\"\n"
                "  · 목록의 대괄호나 쉼표 빠짐"
            ) from exc
        return cls(path=path, data=data)

    # ── 접근 헬퍼 ────────────────────────────────────────────
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        value = self.get(dotted, _MISSING)
        if value is _MISSING:
            raise ConfigError(f"설정 `{dotted}` 이(가) 없습니다. {self.path} 를 확인하세요.")
        return value

    def resolve(self, dotted: str, default: str | None = None) -> Path | None:
        """설정값을 config.toml 기준 절대경로로 변환한다."""
        raw = self.get(dotted, default)
        if not raw:
            return None
        p = Path(str(raw)).expanduser()
        return p if p.is_absolute() else (self.root / p).resolve()

    @property
    def projects_dir(self) -> Path:
        return self.resolve("paths.projects", "projects")  # type: ignore[return-value]

    def secret(self, env_key_setting: str) -> str:
        """`*.api_key_env` 설정이 가리키는 환경변수를 읽는다.

        키 자체는 절대 설정 파일에 두지 않는다.
        """
        env_name = self.get(env_key_setting)
        if not env_name:
            raise ConfigError(f"설정 `{env_key_setting}` 이(가) 비어 있습니다.")
        value = os.environ.get(str(env_name))
        if not value:
            raise ConfigError(
                f"환경변수 {env_name} 이(가) 설정되지 않았습니다. "
                f"API 키를 export 한 뒤 다시 실행하세요."
            )
        return value


_MISSING = object()
