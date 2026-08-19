"""会社そのもの。6部署を組み立てて、日々の動きを1本の流れにする。

- morning(): リサーチ → 企画（毎朝の動き）
- produce(): 執筆 → 検品 → デザイン → 営業 → 商品化（1記事を作り切る動き）
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import Settings
from ..llm import LLMClient, build_client
from ..models import Topic
from ..publish import exporter
from ..store import Store
from .design import Designer
from .planning import Planner
from .qa import Inspector
from .research import Researcher
from .sales import SalesRep
from .writing import Writer


class Company:
    def __init__(self, settings: Settings, store: Store | None = None,
                 llm: LLMClient | None = None) -> None:
        self.settings = settings
        self.store = store or Store(settings.db_path)
        self.llm = llm or build_client(settings)

        self.researcher = Researcher(settings, self.llm, self.store)
        self.planner = Planner(settings, self.llm, self.store)
        self.writer = Writer(settings, self.llm, self.store)
        # 検品だけは別インスタンス（必要なら別モデル）を持たせる。
        self.inspector = Inspector(settings, self.store)
        self.designer = Designer(settings, self.llm, self.store)
        self.sales = SalesRep(settings, self.llm, self.store)

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "Company":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ 朝会
    def morning(self) -> list[Topic]:
        """毎朝の動き: 収集 → ブリーフ → 次に出す note の提案。"""
        print("=== 朝会 ===")
        self.researcher.collect()
        self.researcher.brief()
        return self.planner.propose()

    # -------------------------------------------------------------- 1本作る
    def produce(self, slug: str, note_url: str = "", base: datetime | None = None) -> Path:
        """企画1件を、note に貼れる商品と予約済みの集客ポストにするまで。"""
        print(f"=== 制作: {slug} ===")
        self.writer.write(slug)
        _, report = self.inspector.check(slug)
        self.designer.design(slug)
        # 集客ポストを先に用意してから書き出すと、out/<slug>/ だけで入稿が完結する。
        self.sales.promote(slug, note_url=note_url, base=base)
        out_dir = exporter.export(self.settings, self.store, slug)

        if report.needs_human_review:
            print(f"[company] 人間レビューが必要です: {report.reviewer_note}")
        return out_dir
