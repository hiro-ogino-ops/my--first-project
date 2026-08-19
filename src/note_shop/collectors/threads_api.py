"""Threads API のキーワード検索で伸びているポストを集める。

必要な環境変数:
    THREADS_ACCESS_TOKEN … Threads API のアクセストークン

注意:
    キーワード検索はアプリに threads_keyword_search 権限が必要で、審査を通していない
    アプリでは 400/403 が返る。取得できない場合は空リストを返し、manual インボックスに
    フォールバックさせる運用を想定している。
"""

from __future__ import annotations

import os
from datetime import datetime

from ..config import ResearchConfig
from ..models import TrendSignal
from ._http import describe_http_error, get_json

ENDPOINT = "https://graph.threads.net/v1.0/keyword_search"


class ThreadsCollector:
    name = "threads"

    def collect(self, config: ResearchConfig) -> list[TrendSignal]:
        token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
        if not token:
            print("[research] THREADS_ACCESS_TOKEN が未設定のため Threads の収集をスキップしました")
            return []

        settings = config.threads
        signals: list[TrendSignal] = []
        for keyword in settings.get("keywords", []):
            try:
                payload = get_json(
                    ENDPOINT,
                    {
                        "q": keyword,
                        "search_type": "TOP",
                        "fields": "id,text,permalink,username,timestamp",
                        "limit": int(settings.get("max_results", 50)),
                        "access_token": token,
                    },
                    {},
                )
            except Exception as exc:
                print(
                    f"[research] Threads の検索に失敗しました ({keyword}): "
                    f"{describe_http_error(exc)}"
                )
                continue

            for item in payload.get("data", []):
                timestamp = item.get("timestamp")
                signals.append(
                    TrendSignal(
                        source="threads",
                        external_id=f"threads:{item['id']}",
                        url=item.get("permalink", ""),
                        author=item.get("username", ""),
                        text=item.get("text", ""),
                        posted_at=_parse_timestamp(timestamp),
                    )
                )
        return signals


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None
