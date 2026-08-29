"""本体のループ。

Spotify のポーリングは数秒に1回で十分だが、歌詞は1秒未満で切り替わる。
そこで「再生位置は前回の観測から推定し、描画だけ細かく回す」構造にしている。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .auth import AuthError, TokenStore
from .config import Settings
from .display import Frame, Screen, render
from .keyboard import KeyReader
from .lyrics import LrcLibProvider, LyricsCache, LyricsService
from .models import Lyrics, Playback
from .spotify import RateLimited, SpotifyClient

DRAW_INTERVAL = 0.2  # 描画の刻み（秒）
OFFSET_STEP = 250  # [ ] を1回押したときの補正量（ミリ秒）


@dataclass
class State:
    """いま画面に出しているもの。"""

    playback: Playback | None = None
    lyrics: Lyrics | None = None
    track_key: str = ""
    status: str = ""
    offset_ms: int = 0
    next_poll_at: float = field(default_factory=lambda: 0.0)


class LyricsApp:
    def __init__(self, settings: Settings, *, client=None, service=None) -> None:
        self.settings = settings
        self.client = client or SpotifyClient(
            settings.require_client_id(), TokenStore(settings.token_path)
        )
        self.service = service or LyricsService(
            [LrcLibProvider()], cache=LyricsCache(settings.cache_dir)
        )
        self.state = State(offset_ms=settings.offset_ms)

    # ------------------------------------------------------------------ 1周期
    def poll(self) -> None:
        """Spotify を見に行き、曲が変わっていたら歌詞を取り直す。"""
        try:
            playback = self.client.now_playing()
        except RateLimited as exc:
            self.state.status = f"レート制限中（{exc.retry_after:.0f}秒）"
            self.state.next_poll_at = time.monotonic() + exc.retry_after
            return
        except ConnectionError:
            self.state.status = "オフライン（再試行します）"
            self.state.next_poll_at = time.monotonic() + 10
            return

        self.state.playback = playback
        self.state.next_poll_at = time.monotonic() + self.settings.poll_interval

        if playback is None:
            self.state.track_key = ""
            self.state.lyrics = None
            self.state.status = ""
            return

        if playback.track.key != self.state.track_key:
            self.state.track_key = playback.track.key
            self.state.lyrics = None
            self.state.status = ""
            self.fetch_lyrics()

    def fetch_lyrics(self, *, refresh: bool = False) -> None:
        playback = self.state.playback
        if playback is None:
            return
        try:
            self.state.lyrics = self.service.get(playback.track, refresh=refresh) or Lyrics(
                source="none"
            )
            self.state.status = ""
        except LookupError as exc:
            self.state.lyrics = Lyrics(source="none")
            self.state.status = f"歌詞の取得に失敗: {exc}"
        except ConnectionError:
            self.state.lyrics = None
            self.state.status = "オフライン（歌詞を取得できません）"

    def frame(self, size: tuple[int, int], *, color: bool) -> Frame:
        return render(
            self.state.playback,
            self.state.lyrics,
            size=size,
            offset_ms=self.state.offset_ms,
            status=self.state.status,
            color=color,
        )

    def handle_key(self, key: str) -> bool:
        """押されたキーを処理する。False を返したら終了。"""
        if key in ("q", "Q", "\x03", "\x04"):  # q / Ctrl-C / Ctrl-D
            return False
        if key == "[":
            self.state.offset_ms -= OFFSET_STEP
        elif key == "]":
            self.state.offset_ms += OFFSET_STEP
        elif key in ("0", "="):
            self.state.offset_ms = 0
        elif key in ("r", "R"):
            self.state.status = "再取得中…"
            self.fetch_lyrics(refresh=True)
        return True

    # ------------------------------------------------------------------ ループ
    def run(self) -> int:
        with Screen(color=self.settings.color) as screen, KeyReader() as keys:
            while True:
                if time.monotonic() >= self.state.next_poll_at:
                    try:
                        self.poll()
                    except AuthError as exc:
                        print(f"\n{exc}")
                        return 2
                screen.draw(self.frame(screen.size, color=screen.color))
                key = keys.poll(DRAW_INTERVAL)
                if key is not None and not self.handle_key(key):
                    return 0
