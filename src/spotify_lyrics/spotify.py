"""Spotify Web API から「今なにを再生しているか」を取る。

使うエンドポイントは /v1/me/player/currently-playing の1本だけ。
再生していないときは 204（本文なし）が返るので、None と区別して扱う。
"""

from __future__ import annotations

import time
from typing import Any, Callable

from ._http import HttpError, request_json
from .auth import AuthError, Token, TokenStore, refresh_token
from .models import Playback, Track

CURRENTLY_PLAYING_URL = "https://api.spotify.com/v1/me/player/currently-playing"


class RateLimited(RuntimeError):
    """429。Retry-After の秒数を持つ。"""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"レート制限中です（{retry_after:.0f} 秒待ちます）")
        self.retry_after = retry_after


class SpotifyClient:
    """アクセストークンの更新まで面倒を見る薄いクライアント。"""

    def __init__(
        self,
        client_id: str,
        store: TokenStore,
        *,
        http: Callable[..., Any] = request_json,
    ) -> None:
        self.client_id = client_id
        self.store = store
        self._http = http
        self._token: Token | None = None

    def token(self) -> Token:
        token = self._token or self.store.load()
        if token is None:
            raise AuthError("未ログインです。`spotify-lyrics login` を実行してください。")
        if token.expired():
            token = refresh_token(self.client_id, token)
            self.store.save(token)
        self._token = token
        return token

    def now_playing(self) -> Playback | None:
        """再生中の「曲」を返す。停止中・ポッドキャスト・広告のときは None。"""
        payload = self._get(CURRENTLY_PLAYING_URL, {"additional_types": "track"})
        if not payload:
            return None

        item = payload.get("item")
        if not item or payload.get("currently_playing_type") != "track":
            # ポッドキャストや広告。歌詞の対象外なので曲なし扱いにする。
            return None

        artists = [a.get("name", "") for a in item.get("artists", []) if a.get("name")]
        track = Track(
            title=item.get("name", ""),
            artist=", ".join(artists),
            album=(item.get("album") or {}).get("name", ""),
            duration_ms=int(item.get("duration_ms") or 0),
            track_id=item.get("id"),
        )
        return Playback(
            track=track,
            progress_ms=int(payload.get("progress_ms") or 0),
            is_playing=bool(payload.get("is_playing")),
            fetched_at=time.monotonic(),
        )

    # ------------------------------------------------------------------ 内部
    def _get(self, url: str, params: dict[str, Any]) -> Any:
        for attempt in (1, 2):
            token = self.token()
            headers = {"Authorization": f"Bearer {token.access_token}"}
            try:
                return self._http(url, params=params, headers=headers)
            except HttpError as exc:
                if exc.status == 401 and attempt == 1:
                    # 期限内でも失効することがある（権限変更など）。1度だけ更新して再試行。
                    self._token = refresh_token(self.client_id, token)
                    self.store.save(self._token)
                    continue
                if exc.status == 429:
                    raise RateLimited(exc.retry_after or 5.0) from exc
                if exc.status == 403:
                    raise AuthError(
                        "権限が足りません。アプリの設定を見直して `spotify-lyrics login` からやり直してください。"
                    ) from exc
                raise
        return None  # pragma: no cover - ループは必ず return か raise で抜ける
