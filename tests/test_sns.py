"""SNS運用担当: 型の分析 → 1日分 → 振り返り → 除外 → 時間割 のループ。"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from note_shop.config import SnsConfig
from note_shop.models import DailyPost, DraftPost
from note_shop.staff.sns import (
    LOSE,
    PENDING,
    WIN,
    Screened,
    allocate,
    build_review,
    build_timetable,
    hook_of,
    read_metrics_csv,
    screen,
)
from note_shop.stub import OFFLINE_OPENINGS

DAY = date(2026, 10, 12)
MORNING = datetime(2026, 10, 12, 6, 0)


def _post(i: int, impressions: int, pattern: str = "A", hook_type: str = "数字",
          hour: int = 7) -> DailyPost:
    return DailyPost(
        post_slug=f"daily-20261001-{i:02d}", draft_date=date(2026, 10, 1),
        scheduled_at=datetime(2026, 10, 1, hour, 0), pattern=pattern, hook_type=hook_type,
        hook=f"書き出し{i}", text=f"書き出し{i}\n本文", impressions=impressions,
    )


# ------------------------------------------------------------------ 判定
def test_中央値に対する倍率で伸びた型と滑った型を判定する():
    posts = [
        _post(1, 3000, "A", "数字", 7), _post(2, 2600, "A", "数字", 7),
        _post(3, 1000, "B", "問いかけ", 12), _post(4, 1000, "B", "問いかけ", 12),
        _post(5, 200, "C", "断言", 22), _post(6, 300, "C", "断言", 22),
        _post(7, 5000, "D", "比較", 20),
    ]
    review = build_review(posts, SnsConfig())
    verdicts = {s.key: s.verdict for s in review.patterns}
    assert review.baseline == 1000
    assert verdicts["A"] == WIN
    assert verdicts["C"] == LOSE
    assert verdicts["D"] == PENDING  # 1本だけでは判定しない
    assert {p.hook for p, _ in review.slid_posts} == {"書き出し5", "書き出し6"}


def test_計測が無ければ判定しない():
    assert build_review([], SnsConfig()).baseline == 0


# ------------------------------------------------------------------ 配分
def test_滑った型には枠を回さず伸びた型を厚くする():
    plan = allocate(["A", "B", "C"], {"A": 2.0, "C": 0.3}, 8, lose_ratio=0.7)
    assert len(plan) == 8
    assert "C" not in plan
    assert plan.count("A") > plan.count("B") >= 1
    assert plan[0] == "A"


def test_全部滑っていても空にはしない():
    assert len(allocate(["A", "B"], {"A": 0.1, "B": 0.2}, 8, lose_ratio=0.7)) == 8


# ---------------------------------------------------------------- 時間割
def test_実績の良い時間帯から間隔を空けて8枠取る():
    config = SnsConfig()
    slots = build_timetable(DAY, 8, config.slot_times, {"20": 2.0, "07": 0.4}, 60)
    assert len(slots) == 8
    assert slots == sorted(slots)
    assert datetime(2026, 10, 12, 20, 0) in slots
    assert datetime(2026, 10, 12, 7, 0) not in slots
    gaps = [(b - a).total_seconds() for a, b in zip(slots, slots[1:])]
    assert min(gaps) >= 3600


def test_過ぎた時刻には入れない():
    slots = build_timetable(DAY, 8, SnsConfig().slot_times, {}, 60,
                            not_before=datetime(2026, 10, 12, 19, 30))
    assert all(s.hour >= 20 for s in slots)
    assert all(s.date() == DAY for s in slots)


# ------------------------------------------------------------------ 選別
def test_滑った書き出しと流用と文字数超過は機械的に落とす():
    config = SnsConfig()
    candidates = [
        DraftPost(pattern="A", hook_type="数字", text="退職者のアカウント、最後に棚卸ししたのはいつですか？\n本文"),
        DraftPost(pattern="A", hook_type="断言", text="ゼロトラストは製品名ではない。\n本文"),
        DraftPost(pattern="A", hook_type="比較", text="在宅の日だけ社内システムが激重になる件、原因はVPN装置の同時接続数だった。"),
        DraftPost(pattern="A", hook_type="数字", text="あ" * 141),
        DraftPost(pattern="A", hook_type="比較", text="VPN装置とクラウド型、30人規模ならどちらか。\n本文"),
    ]
    result = screen(
        candidates, Screened(),
        blocked_texts=["退職者のアカウント、最後に棚卸ししたのはいつですか。"],
        blocked_types={"断言"},
        sources=["在宅の日だけ社内システムが激重になる件、原因はVPN装置の同時接続数だった。ライセンス買い足す前に確認すべき項目が3つある。"],
        config=config,
    )
    reasons = [r for _, r in result.rejected]
    assert len(result.kept) == 1
    assert result.kept[0].text.startswith("VPN装置とクラウド型")
    assert any("滑った書き出しに近い" in r for r in reasons)
    assert any("種類" in r for r in reasons)
    assert any("流用防止" in r for r in reasons)
    assert any("文字数超過" in r for r in reasons)


def test_書き出しは1行目():
    assert hook_of("\n一行目です\n二行目") == "一行目です"


# --------------------------------------------------------------- CSV取込
def test_実績CSVは列名ゆれとURLと桁区切りを吸収する(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text(
        "ポストのリンク,ポスト本文,インプレッション数,いいね\n"
        "https://x.com/me/status/123,本文,\"1,234\",5\n",
        encoding="utf-8",
    )
    [row] = read_metrics_csv(path)
    assert (row.external_id, row.impressions, row.likes) == ("123", 1234, 5)


# --------------------------------------------------------------- 通し
def test_1日8本作って時間割と予約台帳に入る(company):
    company.researcher.collect()
    company.sns.analyze()
    posts = company.sns.draft(day=DAY, now=MORNING)

    assert len(posts) == 8
    assert len({p.hook for p in posts}) == 8
    assert all(p.scheduled_at.date() == DAY for p in posts)
    queued = [p for _, p in company.store.pending_posts() if p.article_slug.startswith("daily-")]
    assert len(queued) == 8
    assert (company.settings.out_dir / "sns" / "2026-10-12.md").exists()


def test_同じ日は作り直し指定が無ければ重ねて作らない(company):
    company.researcher.collect()
    company.sns.analyze()
    company.sns.draft(day=DAY, now=MORNING)
    with pytest.raises(RuntimeError):
        company.sns.draft(day=DAY, now=MORNING)
    assert len(company.sns.draft(day=DAY, now=MORNING, force=True)) == 8
    assert len(company.store.daily_posts(day=DAY)) == 8


def test_滑った書き出しは翌日の案から外れる(company, tmp_path):
    company.researcher.collect()
    company.sns.analyze()
    day1 = company.sns.draft(day=DAY, now=MORNING)

    # 1本だけ大きく滑った実績を本文照合で取り込む
    slid = day1[0]
    rows = ["text,impressions"] + [
        f"\"{p.text}\",{100 if p is slid else 1000}" for p in day1
    ]
    csv_path = tmp_path / "metrics.csv"
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert company.sns.import_metrics(csv_path) == 8

    company.sns.review(now=datetime(2026, 10, 13, 6, 0))
    assert any(b["value"] == slid.hook for b in company.store.active_blocks(datetime(2026, 10, 13)))

    day2 = company.sns.draft(day=date(2026, 10, 13), now=datetime(2026, 10, 13, 6, 0))
    assert slid.hook not in {p.hook for p in day2}
    assert slid.hook in OFFLINE_OPENINGS  # スタブは毎回同じ案を出すので、外れたのは選別の結果
