from __future__ import annotations

from datetime import date, datetime, timedelta

from note_shop.models import PromoPost, SaleRecord, Topic, TrendSignal
from note_shop.store import Store


def _topic(title: str = "テスト企画") -> Topic:
    return Topic(
        title=title, angle="角度", reader_problem="困りごと", promise="約束",
        outline=["A"], keywords=["K"], depth_score=3, demand_score=3,
    )


def _store(tmp_path) -> Store:
    return Store(tmp_path / "t.db")


def test_同じシグナルは二重登録されない(tmp_path):
    store = _store(tmp_path)
    signal = TrendSignal(source="x", external_id="x:1", text="本文", likes=10)
    assert store.save_signals([signal]) == 1
    assert store.save_signals([signal]) == 0
    assert len(store.recent_signals(hours=24)) == 1


def test_古いシグナルは対象期間から外れる(tmp_path):
    store = _store(tmp_path)
    store.save_signals([
        TrendSignal(source="x", external_id="x:old", text="古い",
                    collected_at=datetime.now() - timedelta(days=3)),
        TrendSignal(source="x", external_id="x:new", text="新しい"),
    ])
    ids = [s.external_id for s in store.recent_signals(hours=24)]
    assert ids == ["x:new"]


def test_企画は保存して取り出せる(tmp_path):
    store = _store(tmp_path)
    topic = _topic()
    store.save_topic(topic)
    assert store.get_topic(topic.slug).title == topic.title
    assert store.list_topics("planned")[0][0] == topic.slug

    store.set_topic_status(topic.slug, "written")
    assert store.list_topics("planned") == []
    assert store.list_topics("written")[0][2] == "written"


def test_売上は記事ごとに集計される(tmp_path):
    store = _store(tmp_path)
    store.save_topic(_topic("売れた記事"))
    slug = _topic("売れた記事").slug
    store.import_sales([
        SaleRecord(sale_date=date.today(), slug=slug, quantity=3, unit_price=680),
        SaleRecord(sale_date=date.today() - timedelta(days=1), slug=slug, quantity=2, unit_price=680),
    ])
    summary = store.sales_summary(lookback_days=30)
    assert summary[0]["quantity"] == 5
    assert summary[0]["gross"] == 5 * 680
    assert summary[0]["title"] == "売れた記事"


def test_同一の売上行は取り込み直しても増えない(tmp_path):
    store = _store(tmp_path)
    record = SaleRecord(sale_date=date.today(), slug="a", quantity=1, unit_price=300)
    assert store.import_sales([record]) == 1
    assert store.import_sales([record]) == 0


def test_予約投稿は時間が来たものだけ取り出される(tmp_path):
    store = _store(tmp_path)
    now = datetime(2026, 8, 19, 12, 0)
    store.schedule_posts([
        PromoPost(channel="x", text="いま", scheduled_at=now - timedelta(hours=1), article_slug="a"),
        PromoPost(channel="x", text="あと", scheduled_at=now + timedelta(hours=1), article_slug="a"),
    ])
    due = store.due_posts(now)
    assert [p.text for _, p in due] == ["いま"]

    store.mark_post(due[0][0], "posted", "12345")
    assert store.due_posts(now) == []
    assert len(store.pending_posts("a")) == 1


def test_本文を上書きしても価格とタグは残る(tmp_path):
    """検品が本文を書き換えても、商品化の結果を消さないこと。"""
    from note_shop.models import Article

    store = _store(tmp_path)
    topic = _topic()
    article = Article(topic=topic, body="もとの本文")
    store.save_article(article, path="out/a/note.md", price=980, tags=["タグ"])

    store.save_article(article.model_copy(update={"body": "検品後の本文"}))

    meta = store.article_meta(topic.slug)
    assert meta["price"] == 980
    assert meta["tags"] == ["タグ"]
    assert meta["path"] == "out/a/note.md"
    assert store.get_article(topic.slug).body == "検品後の本文"
