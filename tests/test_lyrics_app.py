"""アプリのループと描画、設定読み込みのテスト。"""

from __future__ import annotations

import time

from spotify_lyrics.app import LyricsApp
from spotify_lyrics.config import DEFAULT_REDIRECT_URI, load_settings
from spotify_lyrics.display import render
from spotify_lyrics.models import Lyrics, LyricLine, Playback, Track
from spotify_lyrics.spotify import RateLimited

TRACK_A = Track(title="Dummy A", artist="Dummy Artist", duration_ms=200_000, track_id="a")
TRACK_B = Track(title="Dummy B", artist="Dummy Artist", duration_ms=180_000, track_id="b")
LYRICS = Lyrics(
    lines=(LyricLine("一行目のダミー", 1000), LyricLine("二行目のダミー", 5000)),
    synced=True,
    source="lrclib",
)


class FakeClient:
    def __init__(self, *playbacks) -> None:
        self.queue = list(playbacks)
        self.calls = 0

    def now_playing(self):
        self.calls += 1
        value = self.queue.pop(0) if self.queue else None
        if isinstance(value, Exception):
            raise value
        return value


class FakeService:
    def __init__(self, result=LYRICS) -> None:
        self.result = result
        self.tracks: list[Track] = []

    def get(self, track, *, refresh=False):
        self.tracks.append(track)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _playback(track, progress=1500, playing=True) -> Playback:
    return Playback(track, progress, playing, time.monotonic())


def _settings(tmp_path):
    return load_settings(
        {"SPOTIFY_CLIENT_ID": "cid", "SPOTIFY_LYRICS_HOME": str(tmp_path)}
    )


def test_曲を取得したら歌詞も取りに行く(tmp_path):
    service = FakeService()
    app = LyricsApp(_settings(tmp_path), client=FakeClient(_playback(TRACK_A)), service=service)
    app.poll()
    assert app.state.lyrics is LYRICS
    assert [t.title for t in service.tracks] == ["Dummy A"]


def test_同じ曲のままなら歌詞を取り直さない(tmp_path):
    service = FakeService()
    client = FakeClient(_playback(TRACK_A), _playback(TRACK_A, progress=9000))
    app = LyricsApp(_settings(tmp_path), client=client, service=service)
    app.poll()
    app.state.next_poll_at = 0
    app.poll()
    assert len(service.tracks) == 1


def test_曲が変わったら歌詞を取り直す(tmp_path):
    service = FakeService()
    client = FakeClient(_playback(TRACK_A), _playback(TRACK_B))
    app = LyricsApp(_settings(tmp_path), client=client, service=service)
    app.poll()
    app.poll()
    assert [t.title for t in service.tracks] == ["Dummy A", "Dummy B"]


def test_再生が止まったら歌詞を捨てる(tmp_path):
    client = FakeClient(_playback(TRACK_A), None)
    app = LyricsApp(_settings(tmp_path), client=client, service=FakeService())
    app.poll()
    app.poll()
    assert app.state.lyrics is None
    assert app.state.track_key == ""


def test_レート制限中は次のポーリングを遅らせる(tmp_path):
    client = FakeClient(RateLimited(30.0))
    app = LyricsApp(_settings(tmp_path), client=client, service=FakeService())
    app.poll()
    assert app.state.next_poll_at - time.monotonic() > 25
    assert "レート制限" in app.state.status


def test_通信断でも落ちない(tmp_path):
    client = FakeClient(ConnectionError("offline"))
    app = LyricsApp(_settings(tmp_path), client=client, service=FakeService())
    app.poll()
    assert "オフライン" in app.state.status


def test_歌詞の取得失敗は状態に残す(tmp_path):
    service = FakeService(LookupError("lrclib: HTTP 500"))
    app = LyricsApp(_settings(tmp_path), client=FakeClient(_playback(TRACK_A)), service=service)
    app.poll()
    assert "取得に失敗" in app.state.status
    assert app.state.lyrics.is_empty


def test_キー操作で補正を動かせる(tmp_path):
    app = LyricsApp(_settings(tmp_path), client=FakeClient(), service=FakeService())
    app.handle_key("]")
    app.handle_key("]")
    assert app.state.offset_ms == 500
    app.handle_key("[")
    assert app.state.offset_ms == 250
    app.handle_key("0")
    assert app.state.offset_ms == 0


def test_qで終了する(tmp_path):
    app = LyricsApp(_settings(tmp_path), client=FakeClient(), service=FakeService())
    assert app.handle_key("q") is False
    assert app.handle_key("]") is True


def test_rで歌詞を取り直す(tmp_path):
    service = FakeService()
    app = LyricsApp(_settings(tmp_path), client=FakeClient(_playback(TRACK_A)), service=service)
    app.poll()
    app.handle_key("r")
    assert len(service.tracks) == 2


# ------------------------------------------------------------------ 描画
def test_現在行が本文に出る():
    frame = render(_playback(TRACK_A, progress=6000), LYRICS, size=(80, 24), color=False)
    assert "二行目のダミー" in frame.text
    assert "Dummy A" in frame.text


def test_同じ状態なら再描画しないための署名が一致する():
    playback = _playback(TRACK_A, progress=1500)
    first = render(playback, LYRICS, size=(80, 24), color=False)
    second = render(playback, LYRICS, size=(80, 24), color=False)
    assert first.signature == second.signature


def test_行が進めば署名が変わる():
    before = render(_playback(TRACK_A, progress=1500), LYRICS, size=(80, 24), color=False)
    after = render(_playback(TRACK_A, progress=6000), LYRICS, size=(80, 24), color=False)
    assert before.signature != after.signature


def test_再生していないときは案内を出す():
    frame = render(None, None, size=(80, 24), color=False)
    assert "再生" in frame.text


def test_歌詞が無いときはそう言う():
    frame = render(_playback(TRACK_A), Lyrics(source="none"), size=(80, 24), color=False)
    assert "見つかりません" in frame.text


def test_インストは専用の表示になる():
    frame = render(_playback(TRACK_A), Lyrics(instrumental=True), size=(80, 24), color=False)
    assert "インストゥルメンタル" in frame.text


def test_色を切ると制御文字が残らない():
    frame = render(_playback(TRACK_A), LYRICS, size=(80, 24), color=False)
    assert "\x1b[" not in frame.text


def test_狭い端末でも行がはみ出さない():
    long_track = Track(title="と" * 200, artist="あ" * 200, duration_ms=200_000, track_id="x")
    frame = render(_playback(long_track), LYRICS, size=(30, 10), color=False)
    assert max(len(line) for line in frame.text.splitlines()) <= 30


# ------------------------------------------------------------------ 設定
def test_既定のリダイレクト先はループバック():
    settings = load_settings({})
    assert settings.redirect_uri == DEFAULT_REDIRECT_URI
    assert settings.poll_interval == 3.0


def test_ClientIDが無ければ手順つきで断る():
    try:
        load_settings({}).require_client_id()
    except ValueError as exc:
        assert "SPOTIFY_CLIENT_ID" in str(exc)
        assert "dashboard" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("例外が上がるべき")


def test_環境変数で保存先と補正を変えられる(tmp_path):
    settings = load_settings(
        {
            "SPOTIFY_CLIENT_ID": "cid",
            "SPOTIFY_LYRICS_HOME": str(tmp_path),
            "SPOTIFY_LYRICS_OFFSET_MS": "-500",
        }
    )
    assert settings.token_path == tmp_path / "token.json"
    assert settings.cache_dir == tmp_path / "lyrics-cache"
    assert settings.offset_ms == -500


def test_壊れた数値は既定値に落とす():
    assert load_settings({"SPOTIFY_LYRICS_POLL_INTERVAL": "はやく"}).poll_interval == 3.0
