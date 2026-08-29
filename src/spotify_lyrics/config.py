"""環境変数からアプリの設定を組み立てる。

設定ファイルは作らない。必要なのは Client ID ひとつだけで、
残りは既定値で動くようにしてある。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# ループバックは Spotify が http のまま許可している唯一の宛先。
# "localhost" は 2025 年以降ダッシュボードで弾かれるので 127.0.0.1 を既定にする。
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
DEFAULT_POLL_INTERVAL = 3.0
SCOPES = "user-read-currently-playing user-read-playback-state"


@dataclass(frozen=True)
class Settings:
    client_id: str
    redirect_uri: str
    home: Path
    poll_interval: float
    offset_ms: int
    color: bool

    @property
    def token_path(self) -> Path:
        return self.home / "token.json"

    @property
    def cache_dir(self) -> Path:
        return self.home / "lyrics-cache"

    def require_client_id(self) -> str:
        if not self.client_id:
            raise ValueError(
                "SPOTIFY_CLIENT_ID が未設定です。\n"
                "  1. https://developer.spotify.com/dashboard でアプリを作る\n"
                f"  2. Redirect URI に {self.redirect_uri} を登録する\n"
                "  3. .env か環境変数に SPOTIFY_CLIENT_ID を入れる"
            )
        return self.client_id


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """環境変数（と .env）から設定を読む。"""
    environ = dict(os.environ if env is None else env)
    if env is None:
        environ.update({k: v for k, v in _read_dotenv(Path(".env")).items() if not os.environ.get(k)})

    home = Path(environ.get("SPOTIFY_LYRICS_HOME") or _default_home()).expanduser()
    return Settings(
        client_id=environ.get("SPOTIFY_CLIENT_ID", "").strip(),
        redirect_uri=environ.get("SPOTIFY_REDIRECT_URI", "").strip() or DEFAULT_REDIRECT_URI,
        home=home,
        poll_interval=_as_float(environ.get("SPOTIFY_LYRICS_POLL_INTERVAL"), DEFAULT_POLL_INTERVAL),
        offset_ms=int(_as_float(environ.get("SPOTIFY_LYRICS_OFFSET_MS"), 0.0)),
        color=environ.get("NO_COLOR") is None,
    )


def _default_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) / "spotify-lyrics" if base else Path.home() / ".config" / "spotify-lyrics"


def _as_float(value: str | None, fallback: float) -> float:
    try:
        return float(value) if value not in (None, "") else fallback
    except ValueError:
        return fallback


def _read_dotenv(path: Path) -> dict[str, str]:
    """.env を最低限だけ読む（KEY=VALUE、# はコメント）。"""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values
