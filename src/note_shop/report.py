"""KPI レポート。

会社が続くかどうかを決めるのは売上そのものではなく、
「1記事あたりいくら残るか」と「その1記事に何時間かかるか」。前者をここで出す。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .store import Store


@dataclass
class NetBreakdown:
    gross: int
    platform_fee: int
    note_fee: int
    variable_cost: int

    @property
    def net(self) -> int:
        return self.gross - self.platform_fee - self.note_fee - self.variable_cost


def net_breakdown(settings: Settings, gross: int, article_count: int) -> NetBreakdown:
    """売上総額から手取りを計算する。手数料率は config の前提値。"""
    economics = settings.economics
    platform_fee = int(round(gross * economics.platform_fee_rate))
    note_fee = int(round((gross - platform_fee) * economics.note_fee_rate))
    variable_cost = economics.variable_cost_per_article * article_count
    return NetBreakdown(gross, platform_fee, note_fee, variable_cost)


def render(settings: Settings, store: Store, lookback_days: int = 90) -> str:
    rows = store.sales_summary(lookback_days)
    totals = store.totals(lookback_days)
    published = [t for t in store.list_topics() if t[2] in {"designed", "published"}]
    breakdown = net_breakdown(settings, totals["gross"], len(published) or 1)

    lines = [
        f"# {settings.company.name} 直近{lookback_days}日のKPI",
        "",
        f"- 販売本数: {totals['quantity']} 本",
        f"- 売上総額: {totals['gross']:,} 円",
        f"- 決済手数料(想定): -{breakdown.platform_fee:,} 円",
        f"- note利用料(想定): -{breakdown.note_fee:,} 円",
        f"- 変動費(想定): -{breakdown.variable_cost:,} 円",
        f"- **手取り(想定): {breakdown.net:,} 円**",
        f"- 制作済み記事: {len(published)} 本",
    ]
    if published:
        lines.append(f"- 1記事あたり手取り: {breakdown.net // len(published):,} 円")

    lines += ["", "## 記事別", ""]
    if rows:
        lines.append("| 記事 | 本数 | 売上 | 単価 |")
        lines.append("|---|---:|---:|---:|")
        for row in rows:
            lines.append(
                f"| {row['title']} | {row['quantity']} | {row['gross']:,} | {row['unit_price']:,} |"
            )
    else:
        lines.append("（売上データがまだありません。`noteshop sales import` で取り込んでください）")

    lines += [
        "",
        "> 手数料率は config/company.yaml の前提値です。実際の入金額と必ず突き合わせてください。",
    ]
    return "\n".join(lines)
