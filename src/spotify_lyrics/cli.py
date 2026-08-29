"""spotify-lyrics コマンド。

引数なしで起動したら、そのまま歌詞表示を始める（一番よく使う操作なので）。
"""

from __future__ import annotations

import argparse
import sys

from .app import LyricsApp
from .auth import AuthError, TokenStore, login
from .config import load_settings
from .lyrics import LrcLibProvider, LyricsCache, LyricsService
from .models import Track
from .spotify import RateLimited, SpotifyClient
from .sync import format_ms


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()

    if getattr(args, "offset", None) is not None:
        settings = _replace(settings, offset_ms=args.offset)
    if getattr(args, "interval", None) is not None:
        settings = _replace(settings, poll_interval=args.interval)
    if getattr(args, "no_color", False):
        settings = _replace(settings, color=False)

    try:
        return args.handler(args, settings) or 0
    except AuthError as exc:
        print(f"認証エラー: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RateLimited as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ConnectionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


# ------------------------------------------------------------------ ハンドラ
def _cmd_login(args, settings) -> int:
    del args
    login(settings)
    print(f"ログインしました。トークンは {settings.token_path} に保存しました。")
    print("アクセストークンは期限が切れる前に自動で更新されます。")
    return 0


def _cmd_logout(args, settings) -> int:
    del args
    TokenStore(settings.token_path).clear()
    print("ログアウトしました。")
    return 0


def _cmd_show(args, settings) -> int:
    app = LyricsApp(settings)
    if args.once:
        app.poll()
        return _print_once(app)
    return app.run()


def _cmd_search(args, settings) -> int:
    """再生中かどうかに関係なく、曲名とアーティストを指定して歌詞を探す。"""
    service = _service(settings, use_cache=not args.no_cache)
    track = Track(
        title=args.title,
        artist=args.artist or "",
        album=args.album or "",
        duration_ms=int((args.duration or 0) * 1000),
    )
    lyrics = service.get(track, refresh=args.no_cache)
    if lyrics is None or lyrics.is_empty:
        print("歌詞は見つかりませんでした。", file=sys.stderr)
        return 1
    if lyrics.instrumental:
        print("♪ インストゥルメンタル（歌詞なしとして登録されています）")
        return 0
    print(_render_plain(lyrics))
    return 0


def _cmd_cache(args, settings) -> int:
    cache = LyricsCache(settings.cache_dir)
    if args.action == "path":
        print(settings.cache_dir)
    elif args.action == "clear":
        print(f"{cache.clear()} 件のキャッシュを削除しました。")
    return 0


def _cmd_status(args, settings) -> int:
    """設定とログイン状態を確認する（うまく動かないときの最初の一手）。"""
    del args
    token = TokenStore(settings.token_path).load()
    print(f"Client ID      : {settings.client_id or '(未設定)'}")
    print(f"Redirect URI   : {settings.redirect_uri}")
    print(f"保存先          : {settings.home}")
    print(f"ログイン         : {'済み' if token else '未ログイン'}")
    if not settings.client_id:
        return 2
    if not token:
        return 2

    client = SpotifyClient(settings.client_id, TokenStore(settings.token_path))
    playback = client.now_playing()
    print(f"再生中          : {playback.track if playback else '(なし)'}")
    return 0


# ------------------------------------------------------------------ 補助
def _print_once(app: LyricsApp) -> int:
    playback = app.state.playback
    if playback is None:
        print("いま再生中の曲はありません。", file=sys.stderr)
        return 1
    lyrics = app.state.lyrics
    print(f"{playback.track}  [{format_ms(playback.position_ms())} / {format_ms(playback.track.duration_ms)}]")
    print()
    if lyrics is None or lyrics.is_empty:
        print(app.state.status or "歌詞は見つかりませんでした。", file=sys.stderr)
        return 1
    if lyrics.instrumental:
        print("♪ インストゥルメンタル")
        return 0
    print(_render_plain(lyrics))
    return 0


def _render_plain(lyrics) -> str:
    if lyrics.synced:
        return "\n".join(
            f"[{format_ms(line.time_ms or 0)}] {line.text}" if line.text else ""
            for line in lyrics.lines
        )
    return lyrics.plain_text()


def _service(settings, *, use_cache: bool) -> LyricsService:
    cache = LyricsCache(settings.cache_dir) if use_cache else None
    return LyricsService([LrcLibProvider()], cache=cache)


def _replace(settings, **changes):
    from dataclasses import replace

    return replace(settings, **changes)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spotify-lyrics",
        description="Spotify で再生中の曲の歌詞をネットから探して表示します。",
    )
    sub = parser.add_subparsers(dest="command")

    show = sub.add_parser("show", help="再生中の曲の歌詞を表示し続ける（既定）")
    _add_show_options(show)
    show.set_defaults(handler=_cmd_show)

    login_parser = sub.add_parser("login", help="Spotify にログインする（初回のみ）")
    login_parser.set_defaults(handler=_cmd_login)

    logout_parser = sub.add_parser("logout", help="保存したトークンを消す")
    logout_parser.set_defaults(handler=_cmd_logout)

    search = sub.add_parser("search", help="曲名を指定して歌詞を探す")
    search.add_argument("title", help="曲名")
    search.add_argument("-a", "--artist", help="アーティスト名")
    search.add_argument("--album", help="アルバム名")
    search.add_argument("--duration", type=float, help="曲の長さ（秒）。同名異曲の切り分けに効く")
    search.add_argument("--no-cache", action="store_true", help="キャッシュを使わず取り直す")
    search.set_defaults(handler=_cmd_search)

    cache = sub.add_parser("cache", help="歌詞キャッシュの操作")
    cache.add_argument("action", choices=["path", "clear"])
    cache.set_defaults(handler=_cmd_cache)

    status = sub.add_parser("status", help="設定とログイン状態を確認する")
    status.set_defaults(handler=_cmd_status)

    # 引数なしは show と同じ扱いにする
    _add_show_options(parser)
    parser.set_defaults(handler=_cmd_show, command="show")
    return parser


def _add_show_options(target: argparse.ArgumentParser) -> None:
    target.add_argument("--once", action="store_true", help="1回だけ取得して標準出力に流す")
    target.add_argument("--offset", type=int, help="歌詞のずれをミリ秒で補正（正で早く進む）")
    target.add_argument("--interval", type=float, help="Spotify を見に行く間隔（秒、既定3）")
    target.add_argument("--no-color", action="store_true", help="色を使わない")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
