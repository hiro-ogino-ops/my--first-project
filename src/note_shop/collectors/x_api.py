"""X (Twitter) API v2 の Recent search で伸びているポストを集める。

必要な環境変数:
    X_BEARER_TOKEN  … X API v2 のアプリ用 Bearer トークン

注意:
    Recent search (/2/tweets/search/recent) は有料プラン向けのエンドポイントで、
    プランによって呼べる回数・期間が変わる。権限やレート制限で失敗した場合は
    警告を出して空リストを返し、会社の朝の動きは止めない。
"""

from __future__ import annotations

import os

from ..config import ResearchConfig
from ..models import TrendSignal
from ._http import describe_http_error, get_json

ENDPOINT = "https://api.x.com/2/tweets/search/recent"


class XCollector:
    name = "x"

    def collect(self, config: ResearchConfig) -> list[TrendSignal]:
        token = os.environ.get("X_BEARER_TOKEN", "").strip()
        if not token:
            print("[research] X_BEARER_TOKEN が未設定のため X の収集をスキップしました")
            return []

        settings = config.x
        min_engagement = int(settings.get("min_engagement", 0))
        signals: list[TrendSignal] = []

        for query in settings.get("queries", []):
            try:
                payload = get_json(
                    ENDPOINT,
                    {
                        "query": query,
                        "max_results": min(int(settings.get("max_results", 50)), 100),
                        "tweet.fields": "public_metrics,created_at,author_id",
                        "expansions": "author_id",
                        "user.fields": "username",
                    },
                    {"Authorization": f"Bearer {token}"},
                )
            except Exception as exc:  # ネットワーク・権限・レート制限をまとめて吸収する
                print(f"[research] X の検索に失敗しました ({query}): {describe_http_error(exc)}")
                continue

            usernames = {
                u["id"]: u.get("username", "")
                for u in payload.get("includes", {}).get("users", [])
            }
            for tweet in payload.get("data", []):
                metrics = tweet.get("public_metrics", {})
                signal = TrendSignal(
                    source="x",
                    external_id=f"x:{tweet['id']}",
                    url=f"https://x.com/i/status/{tweet['id']}",
                    author=usernames.get(tweet.get("author_id", ""), ""),
                    text=tweet.get("text", ""),
                    likes=metrics.get("like_count", 0),
                    reposts=metrics.get("retweet_count", 0),
                    replies=metrics.get("reply_count", 0),
                )
                if signal.engagement >= min_engagement:
                    signals.append(signal)
        return signals
