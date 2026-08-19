"""SNS への実投稿。

X も Threads も API 側にネイティブの予約投稿機能がないため、
予約は自社の台帳（posts テーブル）で持ち、時間が来たものをここが投げる。
cron や systemd timer から `noteshop sales run-due` を叩く運用を想定。
"""

from __future__ import annotations

import os

from ..collectors._http import describe_http_error, post_json

# チャンネルごとの本文上限（日本語は X では2文字分として数えられる）
LIMITS = {"x": 140, "threads": 500}

X_ENDPOINT = "https://api.x.com/2/tweets"
THREADS_BASE = "https://graph.threads.net/v1.0"


class PublishError(RuntimeError):
    """投稿に失敗したことを呼び出し側に伝える。"""


def fits(channel: str, text: str) -> bool:
    return len(text) <= LIMITS.get(channel, 500)


def publish(channel: str, text: str) -> str:
    """指定チャンネルに投稿し、投稿IDを返す。"""
    if channel == "x":
        return _publish_x(text)
    if channel == "threads":
        return _publish_threads(text)
    raise PublishError(f"未対応のチャンネルです: {channel}")


def _publish_x(text: str) -> str:
    token = os.environ.get("X_ACCESS_TOKEN", "").strip()
    if not token:
        raise PublishError(
            "X_ACCESS_TOKEN（ユーザー権限のアクセストークン）が未設定です。"
            "アプリ専用の Bearer トークンでは投稿できません。"
        )
    try:
        payload = post_json(X_ENDPOINT, {"text": text}, {"Authorization": f"Bearer {token}"})
    except Exception as exc:
        raise PublishError(f"X への投稿に失敗しました: {describe_http_error(exc)}") from exc
    return str(payload.get("data", {}).get("id", ""))


def _publish_threads(text: str) -> str:
    token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
    user_id = os.environ.get("THREADS_USER_ID", "").strip()
    if not (token and user_id):
        raise PublishError("THREADS_ACCESS_TOKEN と THREADS_USER_ID の両方が必要です")

    try:
        # Threads は「コンテナ作成 → 公開」の2段階。
        container = post_json(
            f"{THREADS_BASE}/{user_id}/threads",
            {"media_type": "TEXT", "text": text, "access_token": token},
            {},
        )
        published = post_json(
            f"{THREADS_BASE}/{user_id}/threads_publish",
            {"creation_id": container["id"], "access_token": token},
            {},
        )
    except Exception as exc:
        raise PublishError(f"Threads への投稿に失敗しました: {describe_http_error(exc)}") from exc
    return str(published.get("id", ""))
