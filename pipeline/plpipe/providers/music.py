"""음악 생성 프로바이더.

Suno 는 공식 공개 API 가 없다. `suno_api` 프로바이더는 sunoapi.org 계열
서드파티 래퍼의 일반적인 요청/응답 형태를 따르며, 응답 스키마가 조금씩
다른 래퍼들도 커버하도록 여러 후보 경로를 확인한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ProviderError, dig, download, poll, request


class MusicProvider:
    name = "base"

    def generate(self, track_index: int, prompt: str, dst_dir: Path) -> Path:
        raise NotImplementedError


class ManualMusic(MusicProvider):
    """직접 만들어 drop/audio 에 넣는 방식."""

    name = "manual"

    def generate(self, track_index: int, prompt: str, dst_dir: Path) -> Path:
        raise ProviderError(
            "music.provider 가 'manual' 입니다. Suno 에서 곡을 만들어 "
            f"{dst_dir} 에 `01-제목.mp3` 처럼 번호를 붙여 넣은 뒤 "
            "`plpipe ingest` 를 실행하세요."
        )


class SunoAPI(MusicProvider):
    name = "suno_api"

    def __init__(self, base_url: str, api_key: str, **opts: Any) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = opts.get("model", "chirp-v4")
        self.instrumental = bool(opts.get("instrumental", True))
        self.poll_seconds = float(opts.get("poll_seconds", 10))
        self.poll_timeout = float(opts.get("poll_timeout", 900))

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def generate(self, track_index: int, prompt: str, dst_dir: Path) -> Path:
        task_id = self._submit(prompt)
        url = poll(
            lambda: self._audio_url(task_id),
            interval=self.poll_seconds,
            timeout=self.poll_timeout,
            what=f"트랙 {track_index} 생성",
        )
        return download(url, dst_dir / f"{track_index:02d}.mp3")

    def _submit(self, prompt: str) -> str:
        body = request(
            f"{self.base_url}/api/v1/generate",
            method="POST",
            headers=self._headers,
            payload={
                "prompt": prompt,
                "model": self.model,
                "customMode": False,
                "instrumental": self.instrumental,
            },
        )
        task_id = dig(body, "data.taskId", "data.task_id", "taskId", "id")
        if not task_id:
            raise ProviderError(f"작업 ID 를 찾지 못했습니다. 응답: {body}")
        return str(task_id)

    def _audio_url(self, task_id: str) -> str | None:
        body = request(
            f"{self.base_url}/api/v1/generate/record-info?taskId={task_id}",
            headers=self._headers,
        )
        status = str(dig(body, "data.status", "status", default="")).upper()
        if status in {"FAILED", "ERROR", "CREATE_TASK_FAILED", "GENERATE_AUDIO_FAILED"}:
            raise ProviderError(f"생성 실패 (status={status}): {body}")
        return dig(
            body,
            "data.response.sunoData.0.audioUrl",
            "data.response.sunoData.0.audio_url",
            "data.data.0.audio_url",
            "data.0.audio_url",
        )


def build(kind: str, cfg, section: str = "music") -> MusicProvider:
    """설정에서 프로바이더 인스턴스를 만든다."""
    if kind == "manual":
        return ManualMusic()
    if kind == "suno_api":
        opts = dict(cfg.get(f"{section}.suno_api", {}) or {})
        base_url = opts.pop("base_url", "https://api.sunoapi.org")
        opts.pop("api_key_env", None)
        return SunoAPI(
            base_url=base_url,
            api_key=cfg.secret(f"{section}.suno_api.api_key_env"),
            **opts,
        )
    raise ProviderError(
        f"알 수 없는 music.provider: {kind!r} (manual | suno_api)"
    )
