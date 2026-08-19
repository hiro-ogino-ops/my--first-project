from __future__ import annotations

from note_shop.models import Topic
from note_shop.pricing import build_tags, decide_price, paid_line_index

BODY = """# タイトル

導入の段落です。

## 最初の見出し

本文が続きます。ここはまだ無料で読める想定です。

## 次の見出し

ここから先が有料の想定です。

さらに続きます。
"""


def _topic(depth: int = 3, demand: int = 3, keywords=None) -> Topic:
    return Topic(
        title="テスト企画",
        angle="角度",
        reader_problem="困りごと",
        promise="約束",
        outline=["A", "B"],
        keywords=keywords if keywords is not None else ["生成AI", " 手順 ", "生成AI", "#タグ"],
        depth_score=depth,
        demand_score=demand,
    )


def test_価格は濃さと需要で上がる(settings):
    low = decide_price(_topic(depth=1, demand=1), settings)
    high = decide_price(_topic(depth=5, demand=5), settings)
    assert low < high


def test_価格は設定した上下限に収まる(settings):
    rule = settings.product.price
    for depth in range(1, 6):
        for demand in range(1, 6):
            price = decide_price(_topic(depth, demand), settings)
            assert rule.min <= price <= rule.max
            assert price % rule.round_to == 0


def test_同じ企画なら価格は毎回同じ(settings):
    topic = _topic(4, 2)
    assert decide_price(topic, settings) == decide_price(topic, settings)


def test_有料ラインは見出しの直後にはならない():
    index = paid_line_index(BODY, free_ratio=0.35)
    paragraphs = [p for p in BODY.split("\n\n") if p.strip()]
    assert 0 < index < len(paragraphs)
    assert not paragraphs[index - 1].lstrip().startswith("#")


def test_有料ラインは本文が空でも落ちない():
    assert paid_line_index("", free_ratio=0.5) == 0


def test_タグは重複と空白を取り除く():
    tags = build_tags(_topic(), limit=5)
    assert tags == ["生成AI", "手順", "タグ"]


def test_タグは上限で切られる():
    topic = _topic(keywords=[f"タグ{i}" for i in range(10)])
    assert len(build_tags(topic, limit=3)) == 3
