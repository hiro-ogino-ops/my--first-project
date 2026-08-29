"""LRC パーサと歌詞モデルのテスト。

歌詞そのものは外部サービスから取ってくるものなので、
ここでは実在しないダミーの文言だけを使う。
"""

from __future__ import annotations

from spotify_lyrics.lyrics.lrc import parse_lrc, parse_plain
from spotify_lyrics.models import Lyrics, LyricLine, normalize, strip_decorations

SAMPLE = """[ar:ダミー歌手]
[ti:ダミー曲]
[00:05.00] 一行目のダミー
[00:10.50] 二行目のダミー
[00:15.5] 三行目のダミー
[00:20.00][01:00.00] 繰り返すダミー
"""


def test_タイムタグを時刻順の行にする():
    lines = parse_lrc(SAMPLE)
    assert [line.time_ms for line in lines] == [5000, 10500, 15500, 20000, 60000]
    assert lines[0].text == "一行目のダミー"


def test_同じ行に複数のタイムタグがあれば展開する():
    lines = parse_lrc(SAMPLE)
    repeated = [line.text for line in lines if line.time_ms in (20000, 60000)]
    assert repeated == ["繰り返すダミー", "繰り返すダミー"]


def test_小数点の桁数に応じてミリ秒を換算する():
    assert parse_lrc("[00:01.5] あ")[0].time_ms == 500 + 1000
    assert parse_lrc("[00:01.05] あ")[0].time_ms == 50 + 1000
    assert parse_lrc("[00:01.345] あ")[0].time_ms == 345 + 1000


def test_offsetタグは符号を反転して全行に効く():
    lines = parse_lrc("[offset:+200]\n[00:10.00] あ")
    assert lines[0].time_ms == 10000 - 200


def test_メタタグだけの行は歌詞にしない():
    assert parse_lrc("[ar:誰か]\n[al:何か]") == []


def test_タイムタグが無ければ空を返す():
    assert parse_lrc("ただの本文\nもう一行") == []


def test_プレーン歌詞は前後の空行だけ落とす():
    lines = parse_plain("\n\n一行目\n\n二行目\n\n")
    assert [line.text for line in lines] == ["一行目", "", "二行目"]


def test_歌詞の往復変換で内容が保たれる():
    original = Lyrics(
        lines=(LyricLine("ダミー", 1000), LyricLine("", 2000)),
        synced=True,
        source="lrclib",
    )
    assert Lyrics.from_dict(original.to_dict()) == original


def test_インストは空扱いにしない():
    assert Lyrics(instrumental=True).is_empty is False
    assert Lyrics().is_empty is True


def test_曲名の再発表記を落とす():
    assert strip_decorations("Song Name (Remastered 2011)") == "Song Name"
    assert strip_decorations("Song Name - 2009 Remaster") == "Song Name"
    assert strip_decorations("Song Name") == "Song Name"


def test_正規化は全角と記号のゆれを吸収する():
    assert normalize("Ｓｏｎｇ, Name!") == normalize("song name")
