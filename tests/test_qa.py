from __future__ import annotations

from note_shop.staff.qa import build_report, detect_monotony

BANNED = ["いかがでしたでしょうか", "本記事では"]


def test_元原稿にあった禁止フレーズは指摘に載る():
    report = build_report(
        original="本記事では手順を説明します。いかがでしたでしょうか。",
        rewritten="手順を説明します。",
        banned=BANNED,
        max_change_ratio=0.9,
    )
    kinds = {f.before for f in report.findings}
    assert "本記事では" in kinds
    assert "いかがでしたでしょうか" in kinds


def test_禁止フレーズが残っていたら人間レビューに回す():
    report = build_report(
        original="本記事では手順を説明します。",
        rewritten="本記事では手順を説明します。",
        banned=BANNED,
        max_change_ratio=0.9,
    )
    assert report.needs_human_review
    assert "禁止フレーズ" in report.reviewer_note


def test_書き換えすぎは人間レビューに回す():
    report = build_report(
        original="あ" * 200,
        rewritten="い" * 200,
        banned=[],
        max_change_ratio=0.35,
    )
    assert report.change_ratio > 0.35
    assert report.needs_human_review


def test_本文が大きく短くなったら内容削除を疑う():
    report = build_report(
        original="あ" * 200,
        rewritten="あ" * 100,
        banned=[],
        max_change_ratio=0.9,
    )
    assert report.needs_human_review
    assert "短くなりました" in report.reviewer_note


def test_言い回しだけの修正は通過する():
    report = build_report(
        original="手順を説明します。まず準備をします。次に実行します。",
        rewritten="手順を説明します。まずは準備から。次に実行します。",
        banned=BANNED,
        max_change_ratio=0.35,
    )
    assert not report.needs_human_review


def test_同じ文末が続くと指摘される():
    findings = detect_monotony("準備します。実行します。確認します。記録します。")
    assert findings
    assert findings[0].kind == "repetition"


def test_文末が散っていれば指摘されない():
    findings = detect_monotony("準備しよう。実行できる。確認が要る。記録は残す。")
    assert findings == []
