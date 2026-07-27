"""커버 아트 생성 프로바이더.

Midjourney 는 공식 API 가 없어서 코드로 자동 호출할 수 없다. Midjourney 를
쓰려면 provider="manual" 로 두고 직접 뽑아 drop/images 에 넣는다.
완전 자동화가 필요하면 openai / bfl(FLUX) / fal 을 쓴다.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from .base import ProviderError, dig, download, poll, request


class ImageProvider:
    name = "base"
    ext = ".png"

    def generate(self, track_index: int, prompt: str, dst_dir: Path,
                 width: int, height: int) -> Path:
        raise NotImplementedError


class ManualImage(ImageProvider):
    name = "manual"

    def generate(self, track_index: int, prompt: str, dst_dir: Path,
                 width: int, height: int) -> Path:
        raise ProviderError(
            "image.provider 가 'manual' 입니다. Midjourney 등에서 이미지를 만들어 "
            f"{dst_dir} 에 `01-제목.png` 처럼 번호를 붙여 넣은 뒤 "
            "`plpipe ingest` 를 실행하세요.\n"
            "프롬프트는 `plpipe prompts` 로 뽑아볼 수 있습니다."
        )


class OpenAIImage(ImageProvider):
    """OpenAI 이미지 API. base64 로 바로 돌려준다."""

    name = "openai"

    def __init__(self, base_url: str, api_key: str, **opts: Any) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = opts.get("model", "gpt-image-1")
        self.quality = opts.get("quality", "high")

    def generate(self, track_index: int, prompt: str, dst_dir: Path,
                 width: int, height: int) -> Path:
        body = request(
            f"{self.base_url}/images/generations",
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}"},
            payload={
                "model": self.model,
                "prompt": prompt,
                "size": _closest_openai_size(width, height),
                "quality": self.quality,
                "n": 1,
            },
            timeout=300,
        )
        b64 = dig(body, "data.0.b64_json")
        dst = dst_dir / f"{track_index:02d}.png"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if b64:
            dst.write_bytes(base64.b64decode(b64))
            return dst
        url = dig(body, "data.0.url")
        if not url:
            raise ProviderError(f"이미지를 찾지 못했습니다. 응답: {body}")
        return download(url, dst)


class BFLImage(ImageProvider):
    """Black Forest Labs (FLUX). 비동기 제출 후 폴링."""

    name = "bfl"

    def __init__(self, base_url: str, api_key: str, **opts: Any) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = opts.get("model", "flux-pro-1.1")
        self.poll_seconds = float(opts.get("poll_seconds", 2))
        self.poll_timeout = float(opts.get("poll_timeout", 300))

    @property
    def _headers(self) -> dict[str, str]:
        return {"x-key": self.api_key}

    def generate(self, track_index: int, prompt: str, dst_dir: Path,
                 width: int, height: int) -> Path:
        submitted = request(
            f"{self.base_url}/{self.model}",
            method="POST",
            headers=self._headers,
            payload={
                # FLUX 는 가로/세로가 32의 배수여야 한다.
                "prompt": prompt,
                "width": _round_to(width, 32),
                "height": _round_to(height, 32),
                "output_format": "png",
            },
        )
        task_id = dig(submitted, "id")
        polling_url = dig(submitted, "polling_url") or f"{self.base_url}/get_result"
        if not task_id:
            raise ProviderError(f"작업 ID 를 찾지 못했습니다. 응답: {submitted}")

        def check() -> str | None:
            sep = "&" if "?" in polling_url else "?"
            body = request(f"{polling_url}{sep}id={task_id}", headers=self._headers)
            status = str(dig(body, "status", default="")).lower()
            if status in {"error", "failed", "content moderated",
                          "request moderated", "task not found"}:
                raise ProviderError(f"생성 실패 (status={status}): {body}")
            return dig(body, "result.sample")

        url = poll(
            check,
            interval=self.poll_seconds,
            timeout=self.poll_timeout,
            what=f"이미지 {track_index} 생성",
        )
        return download(url, dst_dir / f"{track_index:02d}.png")


class FalImage(ImageProvider):
    """fal.ai 동기 엔드포인트."""

    name = "fal"

    def __init__(self, base_url: str, api_key: str, **opts: Any) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = opts.get("model", "fal-ai/flux-pro/v1.1")

    def generate(self, track_index: int, prompt: str, dst_dir: Path,
                 width: int, height: int) -> Path:
        body = request(
            f"{self.base_url}/{self.model}",
            method="POST",
            headers={"Authorization": f"Key {self.api_key}"},
            payload={
                "prompt": prompt,
                "image_size": {"width": _round_to(width, 32),
                               "height": _round_to(height, 32)},
                "num_images": 1,
                "output_format": "png",
            },
            timeout=300,
        )
        url = dig(body, "images.0.url", "image.url")
        if not url:
            raise ProviderError(f"이미지를 찾지 못했습니다. 응답: {body}")
        return download(url, dst_dir / f"{track_index:02d}.png")


def _round_to(value: int, multiple: int) -> int:
    return max(multiple, int(round(value / multiple)) * multiple)


def _closest_openai_size(width: int, height: int) -> str:
    """OpenAI 는 정해진 크기만 받는다. 화면비가 가장 가까운 걸 고른다."""
    options = {"1024x1024": 1.0, "1536x1024": 1.5, "1024x1536": 1024 / 1536}
    target = width / height if height else 1.0
    return min(options, key=lambda key: abs(options[key] - target))


def build(kind: str, cfg, section: str = "image") -> ImageProvider:
    if kind == "manual":
        return ManualImage()

    classes = {"openai": OpenAIImage, "bfl": BFLImage, "fal": FalImage}
    if kind not in classes:
        raise ProviderError(
            f"알 수 없는 image.provider: {kind!r} (manual | openai | bfl | fal)"
        )
    opts = dict(cfg.get(f"{section}.{kind}", {}) or {})
    base_url = opts.pop("base_url", "")
    opts.pop("api_key_env", None)
    if not base_url:
        raise ProviderError(f"설정 `{section}.{kind}.base_url` 이 비어 있습니다.")
    return classes[kind](
        base_url=base_url,
        api_key=cfg.secret(f"{section}.{kind}.api_key_env"),
        **opts,
    )
