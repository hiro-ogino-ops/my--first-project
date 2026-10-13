"""note に貼れる形の成果物を書き出す。

既定の出口はここ。ブラウザ自動投稿を使わない場合でも、
out/<slug>/ の中身をそのまま note のエディタに貼れば公開できる状態にする。
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import affiliate
from ..config import Settings
from ..models import Product
from ..pricing import build_tags, decide_price, paid_line_index, split_paragraphs
from ..store import Store

PAID_MARKER = "<!-- ここから有料エリア -->"


def build_product(settings: Settings, store: Store, slug: str) -> Product:
    """記事・価格・有料ライン・タグ・画像を1つの商品にまとめる。"""
    article = store.get_article(slug)
    if article is None:
        raise KeyError(f"記事 {slug} が見つかりません")

    # 有料ラインは、実際に貼る本文（=H1を落とした後）の段落番号で数える。
    body = body_without_title(article.body)
    index = paid_line_index(body, settings.product.free_ratio)
    # 有料エリアが1段落も残らないなら、それは無料記事。値段をつけない。
    price = 0 if is_free(body, index) else decide_price(article.topic, settings)
    return Product(
        article=article,
        price=price,
        paid_line_index=index,
        tags=build_tags(article.topic, settings.product.tags_per_article),
        assets=store.get_assets(slug),
    )


def export(settings: Settings, store: Store, slug: str) -> Path:
    """out/<slug>/ に note 入稿用の一式を書き出す。"""
    product = build_product(settings, store, slug)
    placements = _affiliate_placements(settings, product)
    markdown = render_markdown(product, **_affiliate_blocks(settings, placements))
    # 規約に関わる不備は、ファイルを書き出す前に止める。直すまで入稿させない。
    issues = affiliate.check_compliance(markdown, settings.affiliate) if placements else []
    errors = [i.message for i in issues if i.level == "error"]
    if errors:
        raise RuntimeError("アフィリエイト表記に不備があります: " + " / ".join(errors))
    for issue in issues:
        print(f"[affiliate] 注意: {issue.message}")

    out_dir = settings.out_dir / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "note.md").write_text(markdown, encoding="utf-8")
    meta = _meta(product, store, slug)
    meta["affiliate"] = [
        {"asin": p.item.asin, "name": p.item.name, "url": p.url, "matched": p.matched,
         "verified": p.item.verified}
        for p in placements
    ]
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    store.save_placements(slug, [(p.item.asin, p.item.name, p.url) for p in placements])

    posts = store.pending_posts(slug)
    if posts:
        (out_dir / "promo.txt").write_text(
            "\n\n".join(
                f"[{p.channel}] {p.scheduled_at:%Y-%m-%d %H:%M}\n{p.text}" for _, p in posts
            ),
            encoding="utf-8",
        )

    store.save_article(product.article, path=str(out_dir / "note.md"), price=product.price,
                       tags=product.tags)
    print(f"[export] {out_dir} に書き出しました（{product.price}円 / タグ{len(product.tags)}個"
          f" / Amazonリンク{len(placements)}件）")
    return out_dir


def _affiliate_placements(settings: Settings, product: Product) -> list[affiliate.Placement]:
    config = settings.affiliate
    if not config.enabled:
        return []
    if not config.tracking_id:
        raise RuntimeError("affiliate.enabled が true ですが affiliate.tracking_id が空です")
    catalog = affiliate.load_catalog(config.catalog_path)
    text = " ".join([product.article.topic.title, *product.article.topic.keywords,
                     product.article.body])
    return affiliate.select_products(text, catalog, config)


def _affiliate_blocks(settings: Settings, placements: list[affiliate.Placement]) -> dict[str, str]:
    if not placements:
        return {}
    return {
        "header": affiliate.render_header(settings.affiliate),
        "footer": affiliate.render_section(placements),
    }


def render_markdown(product: Product, *, header: str = "", footer: str = "") -> str:
    """有料ラインの位置に目印を入れた、貼り付け用の Markdown を作る。

    note はタイトルを本文と別のフィールドで持つため、本文先頭の H1 は落とす。
    header は本文の最上部（広告表記）、footer は末尾（商品リンク）に置く。
    footer は有料記事なら有料エリア側に入る＝買った人にしか見えない。
    リンクを無料エリアに出すために本文を削らない、という方針。
    """
    paragraphs = strip_title(split_paragraphs(product.article.body))
    index = min(product.paid_line_index, len(paragraphs))

    blocks: list[str] = []
    if header:
        blocks.append(header)
    if product.article.lead:
        blocks.append(product.article.lead)
    blocks.extend(paragraphs[:index])

    # 全文無料の記事には有料ラインもティーザーも入れない。
    if index < len(paragraphs):
        if product.article.paid_teaser:
            blocks.append(f"### この先で分かること\n\n{product.article.paid_teaser}")
        blocks.append(PAID_MARKER)
        blocks.extend(paragraphs[index:])
    if footer:
        blocks.append(footer)
    return "\n\n".join(blocks).strip() + "\n"


def is_free(body: str, paid_line_index: int) -> bool:
    """有料エリアが1段落も残らない＝無料記事か。"""
    return paid_line_index >= len(split_paragraphs(body))


def strip_title(paragraphs: list[str]) -> list[str]:
    """先頭の H1 見出しを取り除く（note 側のタイトル欄と重複するため）。"""
    if paragraphs and paragraphs[0].lstrip().startswith("# "):
        return paragraphs[1:]
    return paragraphs


def body_without_title(body: str) -> str:
    """note に貼る形（H1なし）の本文を返す。段落番号の基準を1か所に揃えるための関数。"""
    return "\n\n".join(strip_title(split_paragraphs(body)))


def _meta(product: Product, store: Store, slug: str) -> dict:
    qa = store.get_qa(slug)
    return {
        "slug": slug,
        "title": product.article.topic.title,
        "price": product.price,
        "tags": product.tags,
        "char_count": product.article.char_count,
        "paid_line_index": product.paid_line_index,
        "assets": [a.model_dump() for a in product.assets],
        "qa": {
            "change_ratio": qa.change_ratio if qa else None,
            "needs_human_review": qa.needs_human_review if qa else None,
            "reviewer_note": qa.reviewer_note if qa else "",
        },
        "evidence": product.article.topic.evidence,
        "sales_rationale": product.article.topic.sales_rationale,
    }
