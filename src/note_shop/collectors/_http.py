"""収集口が共有する最小限の HTTP ヘルパ（標準ライブラリのみ）。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def get_json(url: str, params: dict[str, Any], headers: dict[str, str], timeout: int = 20) -> dict:
    """GET して JSON を返す。失敗は呼び出し側で握れるよう例外をそのまま上げる。"""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    request = urllib.request.Request(f"{url}?{query}", headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 固定のAPI先
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 20) -> dict:
    """JSON を POST して JSON を返す。"""
    body = json.dumps(payload).encode("utf-8")
    merged = {"Content-Type": "application/json", **headers}
    request = urllib.request.Request(url, data=body, headers=merged, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def describe_http_error(exc: Exception) -> str:
    """HTTPError の本文まで含めた読めるメッセージにする。"""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = exc.read().decode("utf-8")[:300]
        except Exception:  # pragma: no cover - 本文が読めないケース
            detail = ""
        return f"HTTP {exc.code} {exc.reason} {detail}".strip()
    return f"{type(exc).__name__}: {exc}"
