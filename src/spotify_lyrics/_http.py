"""標準ライブラリだけの HTTP ヘルパ。

Spotify も LRCLIB も JSON を返す普通の REST なので、requests は入れない。
テストから差し替えられるよう、外に見せるのは `request_json` 1本にしている。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = "spotify-lyrics/0.1 (+https://github.com/hiro-ogino-ops/my--first-project)"


class HttpError(RuntimeError):
    """HTTP ステータス付きの失敗。呼び出し側が 401 / 404 / 429 を見分けられるようにする。"""

    def __init__(self, status: int, body: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status} {body}".strip())
        self.status = status
        self.body = body
        self.retry_after = retry_after


def request_json(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    form: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> Any:
    """JSON を取得する。本文が空（204 など）なら None を返す。

    `form` を渡すと application/x-www-form-urlencoded で送る（Spotify のトークン発行用）。
    """
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        url = f"{url}?{query}"

    body: bytes | None = None
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    if form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        merged["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url, data=body, headers=merged, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 固定のAPI先
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # pragma: no cover - 本文が読めないケース
            pass
        retry_after = _retry_after(exc)
        raise HttpError(exc.code, detail, retry_after=retry_after) from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(f"通信に失敗しました: {exc.reason}") from exc

    if not raw.strip():
        return None
    return json.loads(raw.decode("utf-8"))


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    value = exc.headers.get("Retry-After") if exc.headers else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
