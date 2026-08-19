from __future__ import annotations

from datetime import datetime

from note_shop.publish import social
from note_shop.staff.sales import _trim, next_slot


def test_予約は次の希望時刻に丸められる():
    slot = next_slot(datetime(2026, 8, 19, 9, 30), [8, 12, 21])
    assert slot == datetime(2026, 8, 19, 12, 0)


def test_希望時刻を過ぎたら翌日の最初の枠になる():
    slot = next_slot(datetime(2026, 8, 19, 22, 0), [8, 12, 21])
    assert slot == datetime(2026, 8, 20, 8, 0)


def test_ちょうどの時刻はその枠が使われる():
    slot = next_slot(datetime(2026, 8, 19, 8, 0), [8, 12, 21])
    assert slot == datetime(2026, 8, 19, 8, 0)


def test_上限を超えた投稿は本文側だけ削られる():
    url = "https://note.com/example/n/xxxx"
    text = "あ" * 200 + f"\n{url}\n#note"
    trimmed = _trim(text, "x")
    assert social.fits("x", trimmed)
    assert trimmed.endswith("#note")
    assert url in trimmed


def test_上限内の投稿はそのまま():
    text = "短い告知です。"
    assert _trim(text, "x") == text


def test_予約枠が重なっても投稿が捨てられない(company, monkeypatch):
    """ポスト数が予約枠より多いケース。台帳の重複排除で消えないこと。"""
    from note_shop.models import PromoDraft

    settings = company.settings
    object.__setattr__(settings.staff.sales, "posts_per_article", 4)
    object.__setattr__(settings.staff.sales, "schedule_offsets_hours", [0])
    object.__setattr__(settings.staff.sales, "channels", ["x"])

    topics = company.planner.propose()
    slug = topics[0].slug
    company.writer.write(slug)

    # 営業担当のクライアントは他部署と共有なので、企画・執筆を終えてから差し替える。
    monkeypatch.setattr(
        company.sales.llm,
        "generate_structured",
        lambda *a, **k: PromoDraft(posts=[f"告知{i}" for i in range(4)], hashtags=[]),
    )
    posts = company.sales.promote(slug, base=datetime(2026, 8, 19, 9, 0))

    assert len({p.scheduled_at for p in posts}) == 4
    assert len(company.store.pending_posts(slug)) == 4
