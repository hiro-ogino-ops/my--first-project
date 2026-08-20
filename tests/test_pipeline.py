"""オフラインモードで6部署が最後まで通ることを確認する通しテスト。"""

from __future__ import annotations

import json
from datetime import datetime

from note_shop.publish.exporter import PAID_MARKER


def test_朝会から制作までが一本で通る(company, settings):
    topics = company.morning()
    assert topics

    slug = topics[0].slug
    out_dir = company.produce(slug, note_url="https://note.com/example/n/x",
                              base=datetime(2026, 8, 19, 9, 0))

    note_md = (out_dir / "note.md").read_text(encoding="utf-8")
    assert PAID_MARKER in note_md

    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["slug"] == slug
    assert settings.product.price.min <= meta["price"] <= settings.product.price.max
    assert meta["assets"], "表紙が作られていない"

    assert (out_dir / "promo.txt").exists()
    assert company.store.list_topics("designed")[0][0] == slug


def test_予約投稿はautopostが無効なら外に出ない(company, monkeypatch):
    topics = company.morning()
    slug = topics[0].slug
    company.produce(slug, base=datetime(2026, 8, 19, 9, 0))

    def 呼ばれてはいけない(*args, **kwargs):
        raise AssertionError("autopost が false なのに投稿しようとした")

    monkeypatch.setattr("note_shop.publish.social.publish", 呼ばれてはいけない)
    assert company.sales.run_due(now=datetime(2027, 1, 1)) == 0
    # 予約は消えずに残る
    assert company.store.pending_posts(slug)


def test_検品は執筆担当と別のクライアントを持つ(company):
    assert company.inspector.llm is not company.llm


def test_シグナルが無ければブリーフは作らずに知らせる(settings, tmp_path):
    from note_shop.staff import Company

    object.__setattr__(settings.staff.research, "manual", {"inbox": str(tmp_path / "empty")})
    with Company(settings) as c:
        c.researcher.collect()
        try:
            c.researcher.brief()
        except RuntimeError as exc:
            assert "シグナル" in str(exc)
        else:
            raise AssertionError("シグナルなしでブリーフが作られてしまった")
