"""LRCLIB 供給元・キャッシュ・取得サービスのテスト。

外部APIは呼ばず、応答を差し替えたダミーで検証する。
歌詞本文はすべて架空の文言。
"""

from __future__ import annotations

import pytest

from spotify_lyrics._http import HttpError
from spotify_lyrics.lyrics.base import LyricsService
from spotify_lyrics.lyrics.cache import LyricsCache
from spotify_lyrics.lyrics.lrclib import LrcLibProvider
from spotify_lyrics.models import Lyrics, LyricLine, Track

TRACK = Track(title="Dummy Song", artist="Dummy Artist", album="Dummy Album", duration_ms=200_000)

SYNCED = "[00:12.00] 一行目のダミー\n[00:18.00] 二行目のダミー\n"
PLAIN = "一行目のダミー\n二行目のダミー\n"


class FakeHttp:
    """URL ごとに応答（または例外）を返す差し替え用。呼ばれた回数と引数を覚える。"""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, *, params=None, headers=None, timeout=None, **kwargs):
        self.calls.append((url, params or {}))
        for suffix, value in self.responses.items():
            if url.endswith(suffix):
                if isinstance(value, Exception):
                    raise value
                return value
        raise HttpError(404, "not found")


def test_完全一致で同期歌詞が取れる():
    http = FakeHttp({"/get": {"syncedLyrics": SYNCED, "plainLyrics": PLAIN}})
    lyrics = LrcLibProvider(http=http).fetch(TRACK)
    assert lyrics.synced is True
    assert [line.time_ms for line in lyrics.lines] == [12_000, 18_000]
    assert lyrics.source == "lrclib"


def test_完全一致には曲の長さを秒で渡す():
    http = FakeHttp({"/get": {"plainLyrics": PLAIN}})
    LrcLibProvider(http=http).fetch(TRACK)
    assert http.calls[0][1]["duration"] == 200


def test_同期歌詞が無ければプレーン歌詞に落とす():
    http = FakeHttp({"/get": {"syncedLyrics": "", "plainLyrics": PLAIN}})
    lyrics = LrcLibProvider(http=http).fetch(TRACK)
    assert lyrics.synced is False
    assert [line.text for line in lyrics.lines] == ["一行目のダミー", "二行目のダミー"]


def test_インストは歌詞なしと区別する():
    http = FakeHttp({"/get": {"instrumental": True, "plainLyrics": ""}})
    lyrics = LrcLibProvider(http=http).fetch(TRACK)
    assert lyrics.instrumental is True
    assert lyrics.is_empty is False


def test_完全一致が外れたら検索に回る():
    http = FakeHttp(
        {
            "/get": HttpError(404, ""),
            "/search": [
                {
                    "trackName": "Dummy Song",
                    "artistName": "Dummy Artist",
                    "duration": 200,
                    "syncedLyrics": SYNCED,
                }
            ],
        }
    )
    lyrics = LrcLibProvider(http=http).fetch(TRACK)
    assert lyrics.synced is True
    assert [url for url, _ in http.calls] == [
        "https://lrclib.net/api/get",
        "https://lrclib.net/api/search",
    ]


def test_尺が離れすぎた候補は別の曲として捨てる():
    http = FakeHttp(
        {
            "/get": HttpError(404, ""),
            "/search": [
                {
                    "trackName": "Dummy Song",
                    "artistName": "Dummy Artist",
                    "duration": 60,  # 200秒の曲とは別物
                    "plainLyrics": PLAIN,
                }
            ],
        }
    )
    assert LrcLibProvider(http=http).fetch(TRACK) is None


def test_曲名もアーティストも違う候補は選ばない():
    http = FakeHttp(
        {
            "/get": HttpError(404, ""),
            "/search": [
                {
                    "trackName": "まったく別の曲",
                    "artistName": "別の人",
                    "duration": 200,
                    "plainLyrics": PLAIN,
                }
            ],
        }
    )
    assert LrcLibProvider(http=http).fetch(TRACK) is None


def test_候補が複数なら同期歌詞と尺の近さで選ぶ():
    http = FakeHttp(
        {
            "/get": HttpError(404, ""),
            "/search": [
                {
                    "trackName": "Dummy Song",
                    "artistName": "Dummy Artist",
                    "duration": 203,
                    "plainLyrics": PLAIN,
                },
                {
                    "trackName": "Dummy Song",
                    "artistName": "Dummy Artist",
                    "duration": 200,
                    "syncedLyrics": SYNCED,
                },
            ],
        }
    )
    assert LrcLibProvider(http=http).fetch(TRACK).synced is True


def test_共演表記は主アーティストで問い合わせる():
    http = FakeHttp({"/get": {"plainLyrics": PLAIN}})
    LrcLibProvider(http=http).fetch(
        Track(title="Dummy Song", artist="Dummy Artist, Someone Else", duration_ms=200_000)
    )
    assert http.calls[0][1]["artist_name"] == "Dummy Artist"


def test_404以外の失敗は握りつぶさない():
    http = FakeHttp({"/get": HttpError(500, "boom")})
    with pytest.raises(HttpError):
        LrcLibProvider(http=http).fetch(TRACK)


# ------------------------------------------------------------------ キャッシュ
def test_キャッシュは保存して取り出せる(tmp_path):
    cache = LyricsCache(tmp_path)
    lyrics = Lyrics(lines=(LyricLine("ダミー", 1000),), synced=True, source="lrclib")
    cache.put(TRACK.key, lyrics)
    assert cache.get(TRACK.key) == lyrics


def test_期限切れのキャッシュは捨てる(tmp_path):
    cache = LyricsCache(tmp_path, hit_ttl=-1)
    cache.put(TRACK.key, Lyrics(lines=(LyricLine("ダミー", 0),), synced=True))
    assert cache.get(TRACK.key) is None


def test_キャッシュを消すと件数を返す(tmp_path):
    cache = LyricsCache(tmp_path)
    cache.put("a", Lyrics())
    cache.put("b", Lyrics())
    assert cache.clear() == 2
    assert cache.get("a") is None


# ------------------------------------------------------------------ サービス
class StubProvider:
    def __init__(self, name, result) -> None:
        self.name = name
        self.result = result
        self.calls = 0

    def fetch(self, track):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_2回目はキャッシュから返して外に出ない(tmp_path):
    provider = StubProvider("stub", Lyrics(lines=(LyricLine("ダミー", 0),), synced=True))
    service = LyricsService([provider], cache=LyricsCache(tmp_path))
    service.get(TRACK)
    service.get(TRACK)
    assert provider.calls == 1


def test_見つからなかったことも覚える(tmp_path):
    provider = StubProvider("stub", None)
    service = LyricsService([provider], cache=LyricsCache(tmp_path))
    assert service.get(TRACK) is None
    assert service.get(TRACK) is None
    assert provider.calls == 1


def test_同期歌詞が見つかったらそこで打ち切る(tmp_path):
    first = StubProvider("a", Lyrics(lines=(LyricLine("ダミー", 0),), synced=True))
    second = StubProvider("b", Lyrics(lines=(LyricLine("ダミー"),), synced=False))
    service = LyricsService([first, second], cache=None)
    assert service.get(TRACK).synced is True
    assert second.calls == 0


def test_同期なししか無ければそれを使う(tmp_path):
    first = StubProvider("a", Lyrics(lines=(LyricLine("ダミー"),), synced=False))
    second = StubProvider("b", None)
    service = LyricsService([first, second], cache=None)
    assert service.get(TRACK).synced is False


def test_通信断のときは歌詞なしを焼き付けない(tmp_path):
    provider = StubProvider("stub", ConnectionError("offline"))
    cache = LyricsCache(tmp_path)
    service = LyricsService([provider], cache=cache)
    with pytest.raises(LookupError):
        service.get(TRACK)
    assert cache.get(TRACK.key) is None


def test_refreshを指定するとキャッシュを無視する(tmp_path):
    provider = StubProvider("stub", Lyrics(lines=(LyricLine("ダミー", 0),), synced=True))
    service = LyricsService([provider], cache=LyricsCache(tmp_path))
    service.get(TRACK)
    service.get(TRACK, refresh=True)
    assert provider.calls == 2
