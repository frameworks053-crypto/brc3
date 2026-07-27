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
        with path.open("rb") as fh:
            data = tomllib.load(fh)
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
