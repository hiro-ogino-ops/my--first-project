"""リサーチ担当が使う収集口。

X / Threads の公開検索は API プランや権限に強く依存するため、
「取れない環境でも会社が止まらない」ことを最優先に、
手動インボックス（manual）を常に使えるフォールバックとして用意している。
"""

from __future__ import annotations

from typing import Protocol

from ..config import ResearchConfig
from ..models import TrendSignal


class TrendCollector(Protocol):
    name: str

    def collect(self, config: ResearchConfig) -> list[TrendSignal]:
        """設定に従ってポストを集める。失敗しても例外を投げず空リストを返すこと。"""


def build_collectors(config: ResearchConfig) -> list[TrendCollector]:
    """設定の sources から収集口を組み立てる。未知の名前は無視する。"""
    from .manual import ManualInboxCollector
    from .threads_api import ThreadsCollector
    from .x_api import XCollector

    registry = {
        "x": XCollector,
        "threads": ThreadsCollector,
        "manual": ManualInboxCollector,
    }
    collectors: list[TrendCollector] = []
    for name in config.sources:
        factory = registry.get(name)
        if factory is not None:
            collectors.append(factory())
    return collectors
