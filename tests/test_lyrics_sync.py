"""再生位置から現在行を割り出す部分のテスト。"""

from __future__ import annotations

from spotify_lyrics.models import LyricLine, Playback, Track
from spotify_lyrics.sync import active_index, format_ms, window

LINES = [LyricLine(f"ダミー{i}", i * 1000) for i in range(1, 6)]  # 1s, 2s, ... 5s


def test_最初の行より前は現在行なし():
    assert active_index(LINES, 500) == -1


def test_行の時刻ちょうどはその行に入る():
    assert active_index(LINES, 1000) == 0
    assert active_index(LINES, 3000) == 2


def test_次の行が来るまで同じ行が続く():
    assert active_index(LINES, 1999) == 0
    assert active_index(LINES, 2000) == 1


def test_最後の行以降は最後の行のまま():
    assert active_index(LINES, 999_999) == len(LINES) - 1


def test_補正を足すと歌詞が早く進む():
    assert active_index(LINES, 900) == -1
    assert active_index(LINES, 900, offset_ms=200) == 0


def test_時刻の無い歌詞では現在行を出さない():
    assert active_index([LyricLine("ダミー")], 5000) == -1


def test_表示範囲は現在行を中央に寄せる():
    assert window(LINES, 2, 3) == (1, 4)


def test_表示範囲は先頭と末尾で画面を埋める向きに寄る():
    assert window(LINES, 0, 3) == (0, 3)
    assert window(LINES, 4, 3) == (2, 5)


def test_行数が画面より少なければ全部出す():
    assert window(LINES, 0, 10) == (0, 5)


def test_再生中は経過時間ぶん位置が進む():
    playback = Playback(
        track=Track(title="曲", artist="人", duration_ms=200_000),
        progress_ms=10_000,
        is_playing=True,
        fetched_at=100.0,
    )
    assert playback.position_ms(now=102.5) == 12_500


def test_一時停止中は位置が進まない():
    playback = Playback(
        track=Track(title="曲", artist="人", duration_ms=200_000),
        progress_ms=10_000,
        is_playing=False,
        fetched_at=100.0,
    )
    assert playback.position_ms(now=999.0) == 10_000


def test_推定位置は曲の長さを超えない():
    playback = Playback(
        track=Track(title="曲", artist="人", duration_ms=11_000),
        progress_ms=10_000,
        is_playing=True,
        fetched_at=100.0,
    )
    assert playback.position_ms(now=200.0) == 11_000


def test_時間表示はmm_ss():
    assert format_ms(0) == "0:00"
    assert format_ms(65_400) == "1:05"
    assert format_ms(-5) == "0:00"
