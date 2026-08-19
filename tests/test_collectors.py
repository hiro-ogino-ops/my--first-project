from __future__ import annotations

from note_shop.collectors.manual import ManualInboxCollector


def test_インボックスのJSONとCSVを両方読む(settings):
    signals = ManualInboxCollector().collect(settings.staff.research)
    sources = {s.source for s in signals}
    assert len(signals) == 5
    assert {"x", "threads", "manual"} <= sources
    assert all(s.text for s in signals)


def test_インボックスが無くても落ちない(settings):
    config = settings.staff.research
    object.__setattr__(config, "manual", {"inbox": "/does/not/exist"})
    assert ManualInboxCollector().collect(config) == []


def test_壊れたファイルは読み飛ばす(settings, tmp_path):
    inbox = tmp_path / "broken"
    inbox.mkdir()
    (inbox / "bad.json").write_text("{ これはJSONではない", encoding="utf-8")
    (inbox / "good.json").write_text(
        '[{"source":"x","external_id":"x:9","text":"生きている"}]', encoding="utf-8"
    )
    object.__setattr__(settings.staff.research, "manual", {"inbox": str(inbox)})
    signals = ManualInboxCollector().collect(settings.staff.research)
    assert [s.external_id for s in signals] == ["x:9"]
