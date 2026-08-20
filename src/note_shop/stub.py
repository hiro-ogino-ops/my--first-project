"""オフライン用のスタブ生成器。

API キーがなくても 6部署すべてが最後まで動くようにするための実装。
出力は決定論的なので、テストの期待値としても使える。
中身は当然ながら商品にはならない — 動線の確認と単体テスト専用。
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import TypeVar

from pydantic import BaseModel

from .config import Settings
from .models import PromoDraft, ResearchBrief, Theme, Topic, TopicBatch
from .prompts import extract_tag

T = TypeVar("T", bound=BaseModel)


class OfflineClient:
    """LLMClient プロトコルのスタブ実装。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------ text
    def generate_text(
        self, system: str, prompt: str, *, long: bool = False, task: str = "generic"
    ) -> str:
        del system, long
        handler = {
            "write_body": self._body,
            "lead": self._lead,
            "teaser": self._teaser,
            "qa_rewrite": self._qa_rewrite,
            "cover_svg": self._cover_svg,
            "inline_svg": self._inline_svg,
        }.get(task, self._generic)
        return handler(prompt)

    def _generic(self, prompt: str) -> str:
        return f"[offline] 生成をスキップしました（{_digest(prompt)}）"

    def _body(self, prompt: str) -> str:
        title = extract_tag(prompt, "タイトル") or "無題"
        outline = [
            line.strip("-・ ").strip()
            for line in extract_tag(prompt, "構成").splitlines()
            if line.strip()
        ] or ["背景", "手順", "つまずきどころ", "まとめ"]

        sections = []
        for i, heading in enumerate(outline, start=1):
            sections.append(
                f"## {heading}\n\n"
                f"[offline] ここに「{heading}」の本文が入ります。"
                f"実際の運用では執筆担当が、売れた記事とレターの型を参照して"
                f"手順ベースの本文を書きます。({i}/{len(outline)})\n"
            )
        return f"# {title}\n\n" + "\n".join(sections)

    def _lead(self, prompt: str) -> str:
        return (
            f"[offline] {extract_tag(prompt, 'タイトル') or 'この記事'}"
            "で扱う問題と、読み終えたときにできるようになることを2〜3文で書く場所です。"
        )

    def _teaser(self, prompt: str) -> str:
        del prompt
        return "[offline] この先で分かること:\n- 手順の全体像\n- つまずきやすい箇所と回避策\n- そのまま使えるテンプレート"

    def _qa_rewrite(self, prompt: str) -> str:
        """AIっぽい定型句を機械的に落とすだけの簡易検品。"""
        body = extract_tag(prompt, "本文") or prompt
        for phrase in self.settings.staff.qa.banned_phrases:
            body = body.replace(phrase, "")
        return body

    def _cover_svg(self, prompt: str) -> str:
        d = self.settings.staff.design
        title = extract_tag(prompt, "タイトル") or "note"
        bg, accent, fg = (d.palette + ["#0f172a", "#1d4ed8", "#f8fafc"])[:3]
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{d.cover_width}" '
            f'height="{d.cover_height}" viewBox="0 0 {d.cover_width} {d.cover_height}">'
            f'<rect width="100%" height="100%" fill="{bg}"/>'
            f'<rect x="0" y="{d.cover_height - 18}" width="100%" height="18" fill="{accent}"/>'
            f'<text x="72" y="{d.cover_height // 2}" font-size="56" font-family="sans-serif" '
            f'fill="{fg}">{_escape(title[:18])}</text></svg>'
        )

    def _inline_svg(self, prompt: str) -> str:
        d = self.settings.staff.design
        heading = extract_tag(prompt, "見出し") or "図"
        accent = d.palette[1] if len(d.palette) > 1 else "#1d4ed8"
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="600" '
            'viewBox="0 0 1200 600">'
            '<rect width="100%" height="100%" fill="#ffffff"/>'
            f'<rect x="40" y="40" width="1120" height="520" fill="none" stroke="{accent}" '
            'stroke-width="4"/>'
            f'<text x="80" y="120" font-size="40" font-family="sans-serif" fill="#0f172a">'
            f'{_escape(heading[:24])}</text></svg>'
        )

    # ------------------------------------------------------------ structured
    def generate_structured(self, system: str, prompt: str, schema: type[T]) -> T:
        del system
        if schema is ResearchBrief:
            return _cast(schema, self._brief(prompt))
        if schema is TopicBatch:
            return _cast(schema, self._topics(prompt))
        if schema is PromoDraft:
            return _cast(schema, self._promo(prompt))
        raise NotImplementedError(
            f"オフラインでは {schema.__name__} のスタブが未実装です"
        )

    def _brief(self, prompt: str) -> ResearchBrief:
        domains = self.settings.company.domains or ["未設定領域"]
        count = self.settings.staff.research.themes_per_brief
        themes = [
            Theme(
                name=f"[offline] {domains[i % len(domains)]}の話題 {i + 1}",
                why_now="オフライン実行のため実データからの抽出は行っていません",
                audience_pain="（実運用ではここに読者の困りごとが入ります）",
                demand_score=3,
                evidence_ids=[],
            )
            for i in range(count)
        ]
        return ResearchBrief(
            brief_date=date.today(),
            summary=f"[offline] シグナル {prompt.count('- ')} 件をスタブ要約しました。",
            themes=themes,
        )

    def _topics(self, prompt: str) -> TopicBatch:
        del prompt
        domains = self.settings.company.domains or ["未設定領域"]
        count = self.settings.staff.planning.topics_per_planning
        topics = [
            Topic(
                title=f"[offline] {domains[i % len(domains)]}の実践手順 {i + 1}",
                angle="オフラインのスタブ企画です",
                reader_problem="（実運用では観測された困りごとが入ります）",
                promise="手順をなぞれば同じ結果にたどり着ける",
                outline=["前提", "準備するもの", "手順", "つまずきどころ", "応用"],
                keywords=[domains[i % len(domains)], "手順", "テンプレート"],
                depth_score=3,
                demand_score=3,
                evidence=[],
                sales_rationale="オフライン実行のため売上データは参照していません",
            )
            for i in range(count)
        ]
        return TopicBatch(topics=topics)

    def _promo(self, prompt: str) -> PromoDraft:
        title = extract_tag(prompt, "タイトル") or "新しい記事"
        count = self.settings.staff.sales.posts_per_article
        return PromoDraft(
            posts=[
                f"[offline] 告知案{i + 1}: 「{title}」を公開しました。"
                for i in range(count)
            ],
            hashtags=["#note", "#生成AI"],
        )


def _cast(schema: type[T], value: BaseModel) -> T:
    return schema.model_validate(value.model_dump())


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
