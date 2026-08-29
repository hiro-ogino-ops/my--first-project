"""Spotify の認可（Authorization Code + PKCE）。

PKCE を選んだ理由: クライアントシークレットを手元に置かずに済む。
このアプリは各自のPCで動く「パブリッククライアント」なので、
シークレットを配布する形（Client Credentials や通常の Authorization Code）は取れない。
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from ._http import HttpError, request_json
from .config import SCOPES, Settings

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"


class AuthError(RuntimeError):
    """ログインが必要／やり直しが必要な状態。"""


@dataclass(frozen=True)
class Token:
    access_token: str
    refresh_token: str
    expires_at: float  # UNIX 時刻

    def expired(self, skew: float = 60.0) -> bool:
        """期限の1分前から期限切れ扱いにする（通信中に切れるのを避ける）。"""
        return time.time() >= self.expires_at - skew

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_response(cls, payload: dict, *, previous: "Token | None" = None) -> "Token":
        # refresh のレスポンスには refresh_token が入らないことがある。その場合は前の値を使い回す。
        refresh_token = payload.get("refresh_token") or (previous.refresh_token if previous else "")
        if not payload.get("access_token"):
            raise AuthError(f"トークンの応答が不正です: {payload}")
        return cls(
            access_token=payload["access_token"],
            refresh_token=refresh_token,
            expires_at=time.time() + float(payload.get("expires_in", 3600)),
        )


class TokenStore:
    """トークンをホームディレクトリに保存する。パーミッションは 0600。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Token | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return Token(
                access_token=payload["access_token"],
                refresh_token=payload.get("refresh_token", ""),
                expires_at=float(payload.get("expires_at", 0)),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def save(self, token: Token) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(token.to_dict(), indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # pragma: no cover - Windows など
            pass

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


# ------------------------------------------------------------------ PKCE
def make_verifier() -> str:
    """RFC 7636 の code_verifier（43〜128文字の URL-safe 文字列）。"""
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).decode("ascii").rstrip("=")[:128]


def make_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_authorize_url(client_id: str, redirect_uri: str, verifier: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": make_challenge(verifier),
        "state": state,
        "scope": SCOPES,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


# ------------------------------------------------------------------ トークン
def exchange_code(client_id: str, code: str, redirect_uri: str, verifier: str) -> Token:
    payload = request_json(
        TOKEN_URL,
        method="POST",
        form={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    return Token.from_response(payload)


def refresh_token(client_id: str, token: Token) -> Token:
    if not token.refresh_token:
        raise AuthError("リフレッシュトークンがありません。`spotify-lyrics login` からやり直してください。")
    try:
        payload = request_json(
            TOKEN_URL,
            method="POST",
            form={
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": client_id,
            },
        )
    except HttpError as exc:
        if exc.status in (400, 401):
            raise AuthError(
                "リフレッシュに失敗しました（権限が取り消された可能性があります）。"
                "`spotify-lyrics login` からやり直してください。"
            ) from exc
        raise
    return Token.from_response(payload, previous=token)


# ------------------------------------------------------------------ ログイン
def login(settings: Settings, *, open_browser: bool = True, timeout: float = 300.0) -> Token:
    """ブラウザを開いて認可し、ループバックで戻ってきた code をトークンに交換する。"""
    client_id = settings.require_client_id()
    verifier = make_verifier()
    state = secrets.token_urlsafe(16)
    url = build_authorize_url(client_id, settings.redirect_uri, verifier, state)

    parsed = urllib.parse.urlparse(settings.redirect_uri)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 8888

    print("ブラウザで Spotify の認可画面を開きます。開かない場合は次のURLを貼ってください:\n")
    print(f"  {url}\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover - GUI の無い環境
            pass

    result = _wait_for_callback(host, port, timeout)
    if result.get("state") != state:
        raise AuthError("state が一致しません。認可をやり直してください。")
    if "error" in result:
        raise AuthError(f"認可が拒否されました: {result['error']}")
    if "code" not in result:
        raise AuthError("認可コードを受け取れませんでした。")

    token = exchange_code(client_id, result["code"], settings.redirect_uri, verifier)
    TokenStore(settings.token_path).save(token)
    return token


def _wait_for_callback(host: str, port: int, timeout: float) -> dict[str, str]:
    """リダイレクトを1回だけ受けるローカルサーバ。"""
    received: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler の規約
            query = urllib.parse.urlparse(self.path).query
            received.update({k: v[0] for k, v in urllib.parse.parse_qs(query).items()})
            body = _CALLBACK_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # noqa: A002 - アクセスログは出さない
            pass

    try:
        server = http.server.HTTPServer((host, port), Handler)
    except OSError as exc:
        raise AuthError(
            f"{host}:{port} で待ち受けられませんでした（{exc}）。"
            "SPOTIFY_REDIRECT_URI のポートを変えて、ダッシュボードにも同じものを登録してください。"
        ) from exc

    server.timeout = timeout
    with server:
        server.handle_request()
    if not received:
        raise AuthError("認可の待ち受けがタイムアウトしました。")
    return received


_CALLBACK_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>spotify-lyrics</title>
<body style="font-family:system-ui;display:grid;place-items:center;height:90vh;margin:0">
<div style="text-align:center">
<h1 style="font-size:1.25rem">認可が完了しました</h1>
<p>このタブを閉じて、ターミナルに戻ってください。</p>
</div>
"""
