"""価格と有料ラインの決定。

ここはモデルに任せない。値付けは会社の方針であって文章生成の結果ではないため、
config の重み付けから決定論的に計算する（同じ企画なら常に同じ値段になる）。
"""

from __future__ import annotations

import re

from .config import Settings
from .models import Topic


def decide_price(topic: Topic, settings: Settings) -> int:
    """企画の濃さと需要から価格を決める。"""
    rule = settings.product.price
    # depth_score / demand_score は 1〜5。1 を基準(0)として正規化する。
    depth = (topic.depth_score - 1) / 4
    demand = (topic.demand_score - 1) / 4
    raw = rule.default + depth * rule.depth_weight + demand * rule.demand_weight
    price = int(round(raw / rule.round_to) * rule.round_to)
    return max(rule.min, min(rule.max, price))


def split_paragraphs(body: str) -> list[str]:
    """空行区切りで段落に分ける。見出しも1段落として扱う。"""
    return [block.strip() for block in re.split(r"\n\s*\n", body.strip()) if block.strip()]


def paid_line_index(body: str, free_ratio: float) -> int:
    """無料で読ませる割合から、有料ラインを置く段落インデックスを決める。

    見出しの直後で切ると読者が宙ぶらりんになるため、見出しの手前まで戻す。
    free_ratio が 1.0 以上なら段落数をそのまま返す。有料エリアが無い＝全文無料。
    """
    paragraphs = split_paragraphs(body)
    if not paragraphs:
        return 0
    if free_ratio >= 1.0:
        return len(paragraphs)

    target_chars = sum(len(p) for p in paragraphs) * free_ratio
    running = 0
    index = 0
    for i, paragraph in enumerate(paragraphs):
        running += len(paragraph)
        index = i + 1
        if running >= target_chars:
            break

    # 見出し直後になっていたら、その見出しの手前に下げる。
    while index > 1 and paragraphs[index - 1].lstrip().startswith("#"):
        index -= 1
    return max(1, min(index, len(paragraphs) - 1)) if len(paragraphs) > 1 else 1


def build_tags(topic: Topic, limit: int) -> list[str]:
    """キーワードから note のタグを作る。空白除去と重複排除だけ行う。"""
    seen: list[str] = []
    for word in topic.keywords:
        tag = re.sub(r"\s+", "", word).lstrip("#")
        if tag and tag not in seen:
            seen.append(tag)
    return seen[:limit]
