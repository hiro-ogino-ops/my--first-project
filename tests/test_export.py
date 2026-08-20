from __future__ import annotations

from note_shop.models import Article, DesignAsset, Product, Topic
from note_shop.publish.exporter import PAID_MARKER, render_markdown
from note_shop.staff.design import sanitize_svg

BODY = """# タイトル

導入です。

## 見出しA

中身A。

## 見出しB

中身B。
"""


def _product() -> Product:
    topic = Topic(
        title="タイトル", angle="角度", reader_problem="困りごと", promise="約束",
        outline=["A", "B"], keywords=["K"], depth_score=3, demand_score=3,
    )
    return Product(
        article=Article(topic=topic, body=BODY, lead="リード文。", paid_teaser="- 手順の全体像"),
        price=680,
        paid_line_index=2,
        tags=["K"],
        assets=[DesignAsset(kind="cover", path="cover.svg", alt="alt")],
    )


def test_本文先頭のH1は落とされる():
    markdown = render_markdown(_product())
    assert "# タイトル\n" not in markdown
    assert markdown.startswith("リード文。")


def test_有料ラインの目印が1つだけ入る():
    markdown = render_markdown(_product())
    assert markdown.count(PAID_MARKER) == 1


def test_無料パートと有料パートが目印で分かれる():
    markdown = render_markdown(_product())
    free, paid = markdown.split(PAID_MARKER)
    assert "見出しA" in free
    assert "見出しB" in paid
    assert "この先で分かること" in free


def test_SVGからスクリプトと外部読み込みを落とす():
    raw = (
        "説明文\n```svg\n<svg xmlns='http://www.w3.org/2000/svg'>"
        "<script>alert(1)</script>"
        "<image href='https://example.com/a.png'/>"
        "<rect onclick=\"steal()\" width='10'/>"
        "</svg>\n```"
    )
    svg = sanitize_svg(raw)
    assert svg.startswith("<svg")
    assert "script" not in svg
    assert "image" not in svg
    assert "onclick" not in svg
    assert "<rect" in svg


def test_SVGが無い出力はエラーになる():
    import pytest

    with pytest.raises(ValueError):
        sanitize_svg("すみません、画像は作れませんでした。")


def test_有料ラインの位置がH1除去後の段落番号と一致する(settings, tmp_path):
    """build_product の index と render_markdown の切り出しがズレていないこと。"""
    from note_shop.publish.exporter import body_without_title, build_product
    from note_shop.pricing import split_paragraphs
    from note_shop.store import Store

    store = Store(tmp_path / "t.db")
    product_source = _product()
    store.save_topic(product_source.article.topic)
    store.save_article(product_source.article)

    product = build_product(settings, store, product_source.article.topic.slug)
    paragraphs = split_paragraphs(body_without_title(product.article.body))
    markdown = render_markdown(product)
    free = markdown.split(PAID_MARKER)[0]

    # index より前の段落はすべて無料側に出ているはず
    for paragraph in paragraphs[: product.paid_line_index]:
        assert paragraph in free
    # index 以降は無料側に出ていない
    for paragraph in paragraphs[product.paid_line_index :]:
        assert paragraph not in free


def test_全文無料の記事には有料ラインが入らない(settings, tmp_path):
    """free_ratio を 1.0 にすると、マーカーもティーザーも出ないこと。"""
    from note_shop.publish.exporter import build_product
    from note_shop.store import Store

    object.__setattr__(settings.product, "free_ratio", 1.0)
    store = Store(tmp_path / "t.db")
    source = _product()
    store.save_topic(source.article.topic)
    store.save_article(source.article)

    product = build_product(settings, store, source.article.topic.slug)
    markdown = render_markdown(product)

    assert PAID_MARKER not in markdown
    assert "この先で分かること" not in markdown
    assert "見出しB" in markdown, "本文が途中で切れている"


def test_全文無料の記事は値段がつかない(settings, tmp_path):
    from note_shop.publish.exporter import build_product
    from note_shop.store import Store

    object.__setattr__(settings.product, "free_ratio", 1.0)
    store = Store(tmp_path / "t.db")
    source = _product()
    store.save_topic(source.article.topic)
    store.save_article(source.article)

    assert build_product(settings, store, source.article.topic.slug).price == 0


def test_通常の記事は値段がつく(settings, tmp_path):
    from note_shop.publish.exporter import build_product
    from note_shop.store import Store

    store = Store(tmp_path / "t.db")
    source = _product()
    store.save_topic(source.article.topic)
    store.save_article(source.article)

    product = build_product(settings, store, source.article.topic.slug)
    assert product.price >= settings.product.price.min
