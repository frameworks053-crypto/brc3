"""프로바이더 공통 유틸. 표준 라이브러리만 사용한다."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class ProviderError(RuntimeError):
    pass


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 120,
) -> Any:
    """JSON 요청/응답. 실패하면 본문을 포함한 ProviderError 를 던진다."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise ProviderError(f"{method} {url} → HTTP {exc.code}\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"{method} {url} → 연결 실패: {exc.reason}") from exc

    if not body.strip():
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"{url} 응답이 JSON 이 아닙니다:\n{body[:500]}") from exc


def download(url: str, dst: Path, *, timeout: int = 300) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp, tmp.open("wb") as fh:
            while chunk := resp.read(1 << 16):
                fh.write(chunk)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        tmp.unlink(missing_ok=True)
        raise ProviderError(f"다운로드 실패: {url}\n{exc}") from exc
    tmp.replace(dst)
    return dst


def poll(fn, *, interval: float, timeout: float, what: str = "작업"):
    """fn() 이 참 같은 값을 낼 때까지 기다린다. 시간 초과면 예외."""
    deadline = time.monotonic() + timeout
    while True:
        result = fn()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise ProviderError(f"{what} 대기 시간({timeout:.0f}s)을 초과했습니다.")
        time.sleep(interval)


def dig(obj: Any, *paths: str, default: Any = None) -> Any:
    """`a.b.0.c` 형태의 경로 여러 개를 순서대로 시도한다.

    서드파티 래퍼마다 응답 스키마가 조금씩 달라서, 후보를 여러 개 받아
    처음 맞는 걸 쓴다.
    """
    for path in paths:
        node = obj
        ok = True
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                ok = False
                break
        if ok and node is not None:
            return node
    return default
