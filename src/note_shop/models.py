"""部署間を流れるデータ構造。

Pydantic モデルは Claude の structured outputs のスキーマとしてもそのまま使う。
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal["x", "threads", "manual", "note"]
TopicStatus = Literal["planned", "written", "checked", "designed", "published", "dropped"]
PostStatus = Literal["scheduled", "posted", "failed", "skipped"]


# ---------------------------------------------------------------- リサーチ担当
class TrendSignal(BaseModel):
    """X / Threads から拾った1件のポスト。"""

    source: Channel
    external_id: str
    url: str = ""
    author: str = ""
    text: str
    likes: int = 0
    reposts: int = 0
    replies: int = 0
    posted_at: datetime | None = None
    collected_at: datetime = Field(default_factory=datetime.now)

    @property
    def engagement(self) -> int:
        return self.likes + self.reposts + self.replies


class Theme(BaseModel):
    """シグナル群から抽出した『いま伸びているテーマ』。"""

    name: str = Field(description="テーマ名。20文字以内")
    why_now: str = Field(description="なぜ今このテーマが伸びているのか")
    audience_pain: str = Field(description="そのテーマの裏にある読者の困りごと")
    demand_score: int = Field(ge=1, le=5, description="需要の強さ。5が最も強い")
    evidence_ids: list[str] = Field(
        default_factory=list, description="根拠にしたシグナルの external_id"
    )


class ResearchBrief(BaseModel):
    """毎朝リサーチ担当が出す朝会資料。"""

    brief_date: date
    summary: str = Field(description="3行以内の要約")
    themes: list[Theme]


# ------------------------------------------------------------------ 企画担当
class Topic(BaseModel):
    """1本の記事企画。"""

    title: str = Field(description="note のタイトル。28文字前後、煽らず具体的に")
    angle: str = Field(description="この記事だけの切り口。既出記事との差分を一文で")
    reader_problem: str = Field(description="読者が今まさに困っていること")
    promise: str = Field(description="読み終えた読者が『できるようになる』こと")
    outline: list[str] = Field(description="見出しの並び。5〜8個")
    keywords: list[str] = Field(description="検索・タグ候補となる語")
    depth_score: int = Field(ge=1, le=5, description="手順の具体性と独自性。5が最も濃い")
    demand_score: int = Field(ge=1, le=5, description="リサーチが観測した需要の強さ")
    evidence: list[str] = Field(
        default_factory=list, description="根拠にしたテーマ名やシグナルのURL"
    )
    sales_rationale: str = Field(
        default="", description="過去の売上データのどこを見てこの企画にしたか"
    )

    @property
    def slug(self) -> str:
        return make_slug(self.title)


class TopicBatch(BaseModel):
    """企画コマンドの構造化出力。"""

    topics: list[Topic]


# ------------------------------------------------------------------ 執筆担当
class Article(BaseModel):
    """執筆済みの本文。"""

    topic: Topic
    body: str
    lead: str = Field(default="", description="冒頭のリード文")
    paid_teaser: str = Field(default="", description="有料ライン直前の『この先で分かること』")
    written_at: datetime = Field(default_factory=datetime.now)

    @property
    def char_count(self) -> int:
        return len(re.sub(r"\s", "", self.body))


# ------------------------------------------------------------------ 検品担当
class QAFinding(BaseModel):
    """検品で見つかった1件の指摘。"""

    kind: Literal["ai_phrasing", "banned_phrase", "repetition", "fact_risk"]
    before: str
    after: str = ""
    note: str = ""


class QAReport(BaseModel):
    """検品結果。本文の意味は変えず、言い回しだけを直した記録。"""

    findings: list[QAFinding] = Field(default_factory=list)
    change_ratio: float = 0.0
    needs_human_review: bool = False
    reviewer_note: str = ""


# ---------------------------------------------------------------- デザイン担当
class DesignAsset(BaseModel):
    """表紙・記事内画像1点。"""

    kind: Literal["cover", "inline"]
    path: str
    alt: str
    caption: str = ""


# ------------------------------------------------------------------ 営業担当
class PromoPost(BaseModel):
    """集客ポスト1本。"""

    channel: Channel
    text: str
    scheduled_at: datetime
    status: PostStatus = "scheduled"
    article_slug: str = ""
    external_id: str = ""


class PromoDraft(BaseModel):
    """営業担当の構造化出力（時刻はコード側で決める）。"""

    posts: list[str] = Field(description="投稿本文。それぞれ単独で成立すること")
    hashtags: list[str] = Field(default_factory=list)


# -------------------------------------------------------------------- 商品/売上
class Product(BaseModel):
    """note に貼れる状態まで仕上がった商品。"""

    article: Article
    price: int
    paid_line_index: int = Field(description="有料ラインを挿入する段落インデックス")
    tags: list[str]
    assets: list[DesignAsset] = Field(default_factory=list)


class SaleRecord(BaseModel):
    """1件の売上（note のダッシュボードから取り込む）。"""

    sale_date: date
    slug: str
    quantity: int
    unit_price: int
    source: str = "note"

    @property
    def gross(self) -> int:
        return self.quantity * self.unit_price


def make_slug(title: str) -> str:
    """日本語タイトルからも安定した ID を作る。

    ASCII 部分があればそれを活かし、なければタイトルのハッシュを使う。
    """
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    if ascii_part:
        return f"{ascii_part[:32].strip('-')}-{digest}"
    return f"note-{digest}"
