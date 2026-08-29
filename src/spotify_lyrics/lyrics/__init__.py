"""歌詞の取得口。

いまの実装は LRCLIB（APIキー不要・同期歌詞あり）1つだけだが、
`LyricsProvider` を満たすクラスを足せば供給元を増やせるようにしてある。
"""

from __future__ import annotations

from .base import LyricsProvider, LyricsService
from .cache import LyricsCache
from .lrclib import LrcLibProvider

__all__ = ["LyricsProvider", "LyricsService", "LyricsCache", "LrcLibProvider"]
