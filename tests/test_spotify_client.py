"""Spotify クライアントと認可まわりのテスト。外部へは一切出ない。"""

from __future__ import annotations

import base64
import hashlib
import time
import urllib.parse

import pytest

from spotify_lyrics._http import HttpError
from spotify_lyrics.auth import (
    AuthError,
    Token,
    TokenStore,
    build_authorize_url,
    make_challenge,
    make_verifier,
)
from spotify_lyrics.spotify import RateLimited, SpotifyClient

NOW_PLAYING = {
    "currently_playing_type": "track",
    "is_playing": True,
    "progress_ms": 42_000,
    "item": {
        "id": "track123",
        "name": "Dummy Song",
        "duration_ms": 200_000,
        "album": {"name": "Dummy Album"},
        "artists": [{"name": "Dummy Artist"}, {"name": "Someone Else"}],
    },
}


def _store(tmp_path, *, expires_in: float = 3600) -> TokenStore:
    store = TokenStore(tmp_path / "token.json")
    store.save(Token("access", "refresh", time.time() + expires_in))
    return store


def test_再生中の曲を取り出す(tmp_path):
    client = SpotifyClient("cid", _store(tmp_path), http=lambda *a, **k: NOW_PLAYING)
    playback = client.now_playing()
    assert playback.track.title == "Dummy Song"
    assert playback.track.artist == "Dummy Artist, Someone Else"
    assert playback.track.album == "Dummy Album"
    assert playback.track.duration_ms == 200_000
    assert playback.progress_ms == 42_000
    assert playback.is_playing is True


def test_何も再生していなければNone(tmp_path):
    # 204 のとき本文は空になり、request_json は None を返す
    client = SpotifyClient("cid", _store(tmp_path), http=lambda *a, **k: None)
    assert client.now_playing() is None


def test_ポッドキャストは対象外(tmp_path):
    payload = {**NOW_PLAYING, "currently_playing_type": "episode"}
    client = SpotifyClient("cid", _store(tmp_path), http=lambda *a, **k: payload)
    assert client.now_playing() is None


def test_広告のように曲情報が無ければNone(tmp_path):
    payload = {"currently_playing_type": "ad", "item": None, "is_playing": True}
    client = SpotifyClient("cid", _store(tmp_path), http=lambda *a, **k: payload)
    assert client.now_playing() is None


def test_認証トークンをヘッダに載せる(tmp_path):
    seen = {}

    def http(url, *, params=None, headers=None, **kwargs):
        seen.update(headers or {})
        return NOW_PLAYING

    SpotifyClient("cid", _store(tmp_path), http=http).now_playing()
    assert seen["Authorization"] == "Bearer access"


def test_401なら1度だけ更新して再試行する(tmp_path, monkeypatch):
    calls = {"http": 0, "refresh": 0}

    def http(url, *, params=None, headers=None, **kwargs):
        calls["http"] += 1
        if calls["http"] == 1:
            raise HttpError(401, "expired")
        return NOW_PLAYING

    def fake_refresh(client_id, token):
        calls["refresh"] += 1
        return Token("access2", "refresh", time.time() + 3600)

    monkeypatch.setattr("spotify_lyrics.spotify.refresh_token", fake_refresh)
    client = SpotifyClient("cid", _store(tmp_path), http=http)
    assert client.now_playing().track.title == "Dummy Song"
    assert calls == {"http": 2, "refresh": 1}


def test_429はRetry_After付きで上げる(tmp_path):
    def http(*a, **k):
        raise HttpError(429, "slow down", retry_after=7.0)

    with pytest.raises(RateLimited) as exc:
        SpotifyClient("cid", _store(tmp_path), http=http).now_playing()
    assert exc.value.retry_after == 7.0


def test_未ログインなら案内する(tmp_path):
    client = SpotifyClient("cid", TokenStore(tmp_path / "none.json"))
    with pytest.raises(AuthError, match="login"):
        client.now_playing()


def test_期限切れなら取得前に更新する(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "spotify_lyrics.spotify.refresh_token",
        lambda cid, token: Token("fresh", "refresh", time.time() + 3600),
    )
    store = _store(tmp_path, expires_in=-10)
    client = SpotifyClient("cid", store, http=lambda *a, **k: NOW_PLAYING)
    client.now_playing()
    assert store.load().access_token == "fresh"


# ------------------------------------------------------------------ トークン
def test_トークンは保存して読み戻せる(tmp_path):
    store = TokenStore(tmp_path / "token.json")
    token = Token("a", "r", time.time() + 100)
    store.save(token)
    assert store.load() == token
    store.clear()
    assert store.load() is None


def test_壊れたトークンファイルは未ログイン扱い(tmp_path):
    path = tmp_path / "token.json"
    path.write_text("{ぐちゃぐちゃ", encoding="utf-8")
    assert TokenStore(path).load() is None


def test_期限は少し手前で切れたことにする():
    assert Token("a", "r", time.time() + 30).expired() is True
    assert Token("a", "r", time.time() + 3600).expired() is False


def test_更新応答にリフレッシュトークンが無ければ前の値を使う():
    previous = Token("old", "keepme", 0)
    token = Token.from_response({"access_token": "new", "expires_in": 10}, previous=previous)
    assert token.refresh_token == "keepme"


# ------------------------------------------------------------------ PKCE
def test_チャレンジはverifierのSHA256をbase64url化したもの():
    verifier = make_verifier()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert make_challenge(verifier) == expected.decode().rstrip("=")


def test_verifierは規格の長さに収まる():
    verifier = make_verifier()
    assert 43 <= len(verifier) <= 128
    assert make_verifier() != make_verifier()


def test_認可URLにPKCEとスコープが載る():
    url = build_authorize_url("cid", "http://127.0.0.1:8888/callback", make_verifier(), "state1")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["client_id"] == ["cid"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == ["state1"]
    assert "user-read-currently-playing" in query["scope"][0]
