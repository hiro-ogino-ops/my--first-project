"""企画担当。

リサーチの朝会資料と、実際に売れたデータを突き合わせて「次に出す note」を提案する。
売れた記事の価格帯・本数・テーマの重なりを根拠として必ずプロンプトに載せる。
"""

from __future__ import annotations

from ..config import Settings
from ..llm import LLMClient
from ..models import Topic, TopicBatch
from ..prompts import company_system, tagged
from ..store import Store

ROLE = "企画担当"


class Planner:
    def __init__(self, settings: Settings, llm: LLMClient, store: Store) -> None:
        self.settings = settings
        self.llm = llm
        self.store = store

    def propose(self) -> list[Topic]:
        config = self.settings.staff.planning
        brief = self.store.latest_brief()
        sales = self.store.sales_summary(config.sales_lookback_days)
        published = self.store.list_topics()

        batch = self.llm.generate_structured(
            company_system(self.settings, ROLE),
            _plan_prompt(brief, sales, published, config.topics_per_planning, config.min_evidence),
            TopicBatch,
        )
        for topic in batch.topics:
            self.store.save_topic(topic, status="planned")
        print(f"[planning] {len(batch.topics)} 件の企画を登録しました")
        for topic in batch.topics:
            print(f"  - {topic.slug}  {topic.title}（需要{topic.demand_score}/濃さ{topic.depth_score}）")
        return batch.topics


def _plan_prompt(brief, sales, published, count: int, min_evidence: int) -> str:
    if brief:
        theme_lines = "\n".join(
            f"- {t.name}（需要{t.demand_score}）: {t.why_now} / 困りごと: {t.audience_pain}"
            for t in brief.themes
        )
        brief_block = f"要約: {brief.summary}\n{theme_lines}"
    else:
        brief_block = "- （リサーチのブリーフがまだありません。会社の領域から発想してください）"

    if sales:
        sales_lines = "\n".join(
            f"- {row['title']}: {row['quantity']}本 / 累計{row['gross']}円 / 単価{row['unit_price']}円"
            for row in sales[:10]
        )
    else:
        sales_lines = "- （売上データなし。初回は会社の領域の入口になるテーマから始める）"

    published_lines = "\n".join(f"- [{s}] {t}（{st}）" for s, t, st in published[:30]) or "- （まだなし）"

    return (
        f"{tagged('今朝のリサーチ', brief_block)}\n\n"
        f"{tagged('売れた実績', sales_lines)}\n\n"
        f"{tagged('既存の企画と記事', published_lines)}\n\n"
        f"次に出す note を {count} 件提案してください。\n"
        "条件:\n"
        "- 『売れた実績』のどこを見てそう判断したかを sales_rationale に必ず書く\n"
        f"- evidence には根拠にしたテーマ名かURLを最低 {min_evidence} 件入れる\n"
        "- 既存の企画と内容が重なるものは出さない。隣接するが別の困りごとを扱う\n"
        "- outline は、その通りに書けば記事が完成する粒度まで具体化する\n"
        "- demand_score はリサーチの観測に、depth_score は書ける手順の濃さに対応させる"
    )
