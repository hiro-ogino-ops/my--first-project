"""リサーチ担当。

毎朝 X と Threads を回って伸びているポストを集め、
売上データと突き合わせて「いま需要があるテーマ」を朝会資料にまとめる。
"""

from __future__ import annotations

from datetime import date

from ..collectors import build_collectors
from ..config import Settings
from ..llm import LLMClient
from ..models import ResearchBrief, TrendSignal
from ..prompts import company_system, tagged
from ..store import Store

ROLE = "リサーチ担当"


class Researcher:
    def __init__(self, settings: Settings, llm: LLMClient, store: Store) -> None:
        self.settings = settings
        self.llm = llm
        self.store = store

    # ------------------------------------------------------------------ 収集
    def collect(self) -> list[TrendSignal]:
        """設定された全ソースから集めて台帳に追加する。重複は自動で弾かれる。"""
        signals: list[TrendSignal] = []
        for collector in build_collectors(self.settings.staff.research):
            found = collector.collect(self.settings.staff.research)
            print(f"[research] {collector.name}: {len(found)} 件")
            signals.extend(found)
        added = self.store.save_signals(signals)
        print(f"[research] 台帳に {added} 件を新規登録しました（取得 {len(signals)} 件）")
        return signals

    # ------------------------------------------------------------ 朝会資料化
    def brief(self) -> ResearchBrief:
        """直近のシグナルと売上を突き合わせてテーマを抽出する。"""
        config = self.settings.staff.research
        signals = self.store.recent_signals(config.lookback_hours, limit=config.top_n)
        if not signals:
            raise RuntimeError(
                "直近のシグナルがありません。`noteshop research collect` を先に実行するか、"
                f"{config.manual.get('inbox', 'data/inbox')} にポストを置いてください。"
            )

        sales = self.store.sales_summary(self.settings.staff.planning.sales_lookback_days)
        brief = self.llm.generate_structured(
            company_system(self.settings, ROLE),
            _brief_prompt(signals, sales, config.themes_per_brief),
            ResearchBrief,
        )
        # 日付はモデルの推測ではなく実行日を正とする。
        brief = brief.model_copy(update={"brief_date": date.today()})
        self.store.save_brief(brief)
        print(f"[research] {brief.brief_date} のブリーフを作成（テーマ {len(brief.themes)} 件）")
        return brief


def _brief_prompt(signals: list[TrendSignal], sales: list[dict], theme_count: int) -> str:
    signal_lines = "\n".join(
        f"- [{s.external_id}] ({s.source} / 反応{s.engagement}) {s.text[:180]}"
        for s in signals
    )
    if sales:
        sales_lines = "\n".join(
            f"- {row['title']}: {row['quantity']}本 / {row['gross']}円" for row in sales[:10]
        )
    else:
        sales_lines = "- （まだ売上データがありません）"

    return (
        "今朝集めた X / Threads のポストと、自社の販売実績です。\n\n"
        f"{tagged('シグナル', signal_lines)}\n\n"
        f"{tagged('自社の売上', sales_lines)}\n\n"
        "これらから、いま需要が立ち上がっているテーマを"
        f"{theme_count}件に絞って抽出してください。\n"
        "条件:\n"
        "- 反応が多いだけの話題ではなく、『お金を払ってでも解決したい困りごと』が背後にあるものを選ぶ\n"
        "- 自社が既に売れている領域と隣接し、次の一手になるものを優先する\n"
        "- 各テーマの evidence_ids には、根拠にしたシグナルの角括弧内のIDを入れる\n"
        "- summary は3行以内で、今日の意思決定に必要なことだけ書く"
    )
