from __future__ import annotations

from pathlib import Path

import pytest

from note_shop import affiliate
from note_shop.config import AffiliateConfig
from note_shop.models import Article, Topic
from note_shop.publish.exporter import PAID_MARKER, export
from note_shop.store import Store

REPO_ROOT = Path(__file__).resolve().parents[1]

CATALOG = """
products:
  - asin: "B000000001"
    name: "サンプルVPNルーター RX-100"
    keywords: ["VPNルーター", "RX-100"]
    verified: true
    verified_scope: "10台規模で2週間運用"
  - asin: "B000000002"
    name: "サンプルセキュリティキー"
    keywords: ["セキュリティキー"]
    verified: false
  - asin: "B000000003"
    name: "記事に出てこない商品"
    keywords: ["NAS"]
    verified: true
"""

BODY = """# タイトル

導入です。拠点には VPNルーター を置きます。

## 手順

RX-100 の設定画面を開きます。セキュリティキー も併用します。
"""


def _config(tmp_path, **overrides) -> AffiliateConfig:
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(CATALOG, encoding="utf-8")
    values = dict(enabled=True, tracking_id="example-22", site_name="テスト屋",
                  catalog_path=str(catalog))
    values.update(overrides)
    return AffiliateConfig(**values)


def test_本文に出た検証済み商品だけが選ばれる(tmp_path):
    config = _config(tmp_path)
    picked = affiliate.select_products(BODY, affiliate.load_catalog(config.catalog_path), config)
    assert [p.item.asin for p in picked] == ["B000000001"]
    assert picked[0].url == "https://www.amazon.co.jp/dp/B000000001?tag=example-22"
    assert set(picked[0].matched) == {"VPNルーター", "RX-100"}


def test_未検証を許可すると未検証の商品も選ばれる(tmp_path):
    config = _config(tmp_path, allow_unverified=True)
    picked = affiliate.select_products(BODY, affiliate.load_catalog(config.catalog_path), config)
    assert [p.item.asin for p in picked] == ["B000000001", "B000000002"]
    assert "未検証" in affiliate.render_section(picked)


def test_不正なASINは台帳の読み込みで止まる(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text('products:\n  - asin: "abc"\n    name: "x"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        affiliate.load_catalog(bad)


def test_同梱の台帳テンプレートは空で読める():
    assert affiliate.load_catalog(REPO_ROOT / "config" / "amazon-catalog.yaml") == []


def test_表記が揃っていれば検査を通る(tmp_path):
    config = _config(tmp_path)
    picked = affiliate.select_products(BODY, affiliate.load_catalog(config.catalog_path), config)
    markdown = "\n\n".join([affiliate.render_header(config), "本文", affiliate.render_section(picked)])
    assert affiliate.check_compliance(markdown, config) == []


def test_広告表記が無いと検査で止まる(tmp_path):
    config = _config(tmp_path)
    markdown = "本文 https://www.amazon.co.jp/dp/B000000001?tag=example-22"
    messages = [i.message for i in affiliate.check_compliance(markdown, config) if i.level == "error"]
    assert any("広告表記" in m for m in messages)
    assert any("開示文" in m for m in messages)


def test_価格とタグ無しリンクは検査で止まる(tmp_path):
    config = _config(tmp_path)
    markdown = "\n\n".join([
        affiliate.render_header(config),
        "- [商品](https://www.amazon.co.jp/dp/B000000001?tag=example-22) 今なら 3,980円",
        "- https://www.amazon.co.jp/dp/B000000002",
    ])
    messages = [i.message for i in affiliate.check_compliance(markdown, config)]
    assert any("価格" in m for m in messages)
    assert any("トラッキングIDの無いリンク" in m for m in messages)


def test_日本以外のトラッキングIDは警告になる(tmp_path):
    config = _config(tmp_path, tracking_id="example-20")
    markdown = "\n\n".join([
        affiliate.render_header(config),
        "https://www.amazon.co.jp/dp/B000000001?tag=example-20",
    ])
    issues = affiliate.check_compliance(markdown, config)
    assert [i.level for i in issues] == ["warning"]


def _save_article(store: Store) -> str:
    topic = Topic(
        title="タイトル", angle="角度", reader_problem="困りごと", promise="約束",
        outline=["A"], keywords=["VPN"], depth_score=3, demand_score=3,
    )
    store.save_topic(topic)
    store.save_article(Article(topic=topic, body=BODY, lead="リード文。"))
    return topic.slug


def test_書き出すと冒頭に広告表記_末尾にリンクが入る(settings, tmp_path):
    object.__setattr__(settings, "affiliate", _config(tmp_path))
    store = Store(tmp_path / "t.db")
    slug = _save_article(store)

    out_dir = export(settings, store, slug)
    markdown = (out_dir / "note.md").read_text(encoding="utf-8")

    assert markdown.startswith("【PR】")
    assert "Amazonのアソシエイトとして、テスト屋は" in markdown
    assert markdown.rstrip().endswith("最新の情報を確認してください。")
    assert markdown.count("amazon.co.jp/dp/") == 1
    # 有料記事なら、リンクは有料エリア側に入る
    if PAID_MARKER in markdown:
        assert "amazon.co.jp" in markdown.split(PAID_MARKER)[1]


def test_無効のままなら何も差し込まない(settings, tmp_path):
    store = Store(tmp_path / "t.db")
    slug = _save_article(store)
    markdown = (export(settings, store, slug) / "note.md").read_text(encoding="utf-8")
    assert "amazon" not in markdown
    assert "【PR】" not in markdown


def test_トラッキングID未設定で有効化すると止まる(settings, tmp_path):
    object.__setattr__(settings, "affiliate", _config(tmp_path, tracking_id=""))
    store = Store(tmp_path / "t.db")
    slug = _save_article(store)
    with pytest.raises(RuntimeError):
        export(settings, store, slug)


def test_紹介料を取り込むと掲載記事と結びつく(settings, tmp_path):
    object.__setattr__(settings, "affiliate", _config(tmp_path))
    store = Store(tmp_path / "t.db")
    slug = _save_article(store)
    export(settings, store, slug)

    records = affiliate.read_earnings_csv(REPO_ROOT / "examples" / "amazon-earnings-sample.csv")
    assert store.import_earnings(records) == 3
    assert store.import_earnings(records) == 0, "同じCSVの二重取り込みで紹介料が増えている"

    rows = {r["asin"]: r for r in store.earnings_summary(lookback_days=100000)}
    assert rows["B000000001"]["revenue"] == 450
    assert rows["B000000001"]["clicks"] == 20
    assert rows["B000000001"]["slugs"] == slug
    assert rows["B000000002"]["slugs"] is None
