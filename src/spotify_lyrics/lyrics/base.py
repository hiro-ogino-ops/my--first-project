"""歌詞供給元の共通インタフェースと、キャッシュ込みの取得サービス。"""

from __future__ import annotations

from typing import Protocol, Sequence

from ..models import Lyrics, Track


class LyricsProvider(Protocol):
    """歌詞をネットから探してくる係。"""

    name: str

    def fetch(self, track: Track) -> Lyrics | None:
        """見つかれば Lyrics、無ければ None。通信エラーは例外のまま上げてよい。"""
        ...


class LyricsService:
    """複数の供給元を順に当たり、結果をキャッシュする。

    同期歌詞（時刻つき）を優先し、無ければプレーン歌詞で妥協する。
    """

    def __init__(self, providers: Sequence[LyricsProvider], cache=None) -> None:
        self.providers = list(providers)
        self.cache = cache

    def get(self, track: Track, *, refresh: bool = False) -> Lyrics | None:
        if self.cache is not None and not refresh:
            cached = self.cache.get(track.key)
            if cached is not None:
                # 見つからなかったこともキャッシュする（毎回叩きに行かないため）
                return cached if not cached.is_empty or cached.instrumental else None

        best: Lyrics | None = None
        errors: list[str] = []
        for provider in self.providers:
            try:
                found = provider.fetch(track)
            except Exception as exc:  # 1つ落ちても他の供給元は試す
                errors.append(f"{provider.name}: {type(exc).__name__}: {exc}")
                continue
            if found is None:
                continue
            if found.synced or found.instrumental:
                best = found
                break
            best = best or found

        if best is None and errors and self.cache is not None:
            # 通信断のときに「歌詞なし」を焼き付けない
            raise LookupError("；".join(errors))

        if self.cache is not None:
            self.cache.put(track.key, best or Lyrics(source="none"))
        return best
