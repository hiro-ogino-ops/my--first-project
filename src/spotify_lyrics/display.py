"""ターミナルへの描画。

外部ライブラリを足したくないので ANSI エスケープを直に書く。
再描画は画面全体を組み立ててから一度に流す（部分更新はちらつくため）。
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass

from .models import Lyrics, Playback
from .sync import active_index, format_ms, window

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
GREEN = "\x1b[38;5;42m"  # Spotify 寄りの緑
GREY = "\x1b[38;5;245m"
CLEAR = "\x1b[2J\x1b[H"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
ALT_SCREEN_ON = "\x1b[?1049h"
ALT_SCREEN_OFF = "\x1b[?1049l"


@dataclass
class Frame:
    """1回ぶんの描画内容。前フレームと同じなら描き直さないための比較にも使う。"""

    text: str
    signature: tuple


class Screen:
    """代替スクリーンを使う描画先。TTY でなければ何もしない。"""

    def __init__(self, *, color: bool = True, stream=None) -> None:
        self.stream = stream or sys.stdout
        self.is_tty = self.stream.isatty()
        self.color = color and self.is_tty
        self._last: tuple | None = None

    def __enter__(self) -> "Screen":
        if self.is_tty:
            self.stream.write(ALT_SCREEN_ON + HIDE_CURSOR)
            self.stream.flush()
        return self

    def __exit__(self, *exc) -> None:
        if self.is_tty:
            self.stream.write(SHOW_CURSOR + ALT_SCREEN_OFF)
            self.stream.flush()

    @property
    def size(self) -> tuple[int, int]:
        columns, rows = shutil.get_terminal_size(fallback=(80, 24))
        return max(20, columns), max(6, rows)

    def draw(self, frame: Frame) -> None:
        if frame.signature == self._last:
            return
        self._last = frame.signature
        self.stream.write(CLEAR + frame.text)
        self.stream.flush()

    def paint(self, text: str) -> str:
        return text if self.color else _strip_ansi(text)


def render(
    playback: Playback | None,
    lyrics: Lyrics | None,
    *,
    size: tuple[int, int],
    offset_ms: int = 0,
    status: str = "",
    color: bool = True,
) -> Frame:
    """ヘッダ（曲名・進捗）＋歌詞＋フッタ（操作説明）を組み立てる。"""
    columns, rows = size
    body_height = max(1, rows - 5)

    if playback is None:
        text = _center_message(
            "Spotify で曲を再生すると、ここに歌詞が出ます",
            status or "（ポッドキャストと広告は対象外です）",
            size,
        )
        return _finish(text, ("idle", status), color)

    position = playback.position_ms()
    header = _header(playback, position, columns, color)

    if lyrics is None:
        body, marker = _message_block("歌詞を探しています…", body_height, columns, color), "loading"
    elif lyrics.instrumental:
        body, marker = _message_block("♪ インストゥルメンタル", body_height, columns, color), "inst"
    elif lyrics.is_empty:
        body = _message_block("この曲の歌詞は見つかりませんでした", body_height, columns, color)
        marker = "missing"
    elif lyrics.synced:
        index = active_index(lyrics.lines, position, offset_ms)
        body = _synced_block(lyrics, index, body_height, columns, color)
        marker = f"synced:{index}"
    else:
        # 時刻が無いので、経過割合ぶんだけスクロールさせる
        ratio = position / playback.track.duration_ms if playback.track.duration_ms else 0.0
        top = int(max(0, len(lyrics.lines) - body_height) * min(1.0, ratio))
        body = _plain_block(lyrics, top, body_height, columns, color)
        marker = f"plain:{top}"

    footer = _footer(lyrics, offset_ms, status, columns, color)
    text = "\n".join([header, "", body, "", footer])
    signature = (playback.track.key, marker, status, offset_ms, columns, rows, playback.is_playing)
    return _finish(text, signature, color)


# ------------------------------------------------------------------ 部品
def _header(playback: Playback, position: int, columns: int, color: bool) -> str:
    track = playback.track
    icon = "▶" if playback.is_playing else "❚❚"
    title = _fit(f"{icon} {track.title}", columns)
    artist = _fit(f"  {track.artist}" + (f" — {track.album}" if track.album else ""), columns)
    bar = _progress_bar(position, track.duration_ms, columns)
    times = f"{format_ms(position)} / {format_ms(track.duration_ms)}"
    return _c(BOLD + GREEN, title, color) + "\n" + _c(GREY, artist, color) + "\n" + _c(
        DIM, f"{bar} {times}", color
    )


def _progress_bar(position: int, duration: int, columns: int) -> str:
    width = max(10, min(40, columns - 20))
    filled = int(width * position / duration) if duration else 0
    return "─" * min(filled, width) + "·" * max(0, width - filled)


def _synced_block(lyrics: Lyrics, index: int, height: int, columns: int, color: bool) -> str:
    start, end = window(lyrics.lines, max(index, 0), height)
    rows: list[str] = []
    for position in range(start, end):
        line = lyrics.lines[position]
        text = line.text.strip() or "♪"
        if position == index:
            rows.append(_c(BOLD + GREEN, _fit(f"  {text}", columns), color))
        elif position < index:
            rows.append(_c(DIM, _fit(f"  {text}", columns), color))
        else:
            rows.append(_c(GREY, _fit(f"  {text}", columns), color))
    return "\n".join(rows) or _message_block("♪", height, columns, color)


def _plain_block(lyrics: Lyrics, top: int, height: int, columns: int, color: bool) -> str:
    rows = [
        _c(GREY, _fit(f"  {line.text}", columns), color)
        for line in lyrics.lines[top : top + height]
    ]
    return "\n".join(rows)


def _message_block(message: str, height: int, columns: int, color: bool) -> str:
    pad = max(0, (height - 1) // 2)
    return "\n" * pad + _c(DIM, _fit(f"  {message}", columns), color)


def _center_message(title: str, note: str, size: tuple[int, int]) -> str:
    columns, rows = size
    pad = max(0, rows // 2 - 1)
    return "\n" * pad + _fit(f"  {title}", columns) + "\n" + _fit(f"  {note}", columns)


def _footer(lyrics: Lyrics | None, offset_ms: int, status: str, columns: int, color: bool) -> str:
    parts = ["q 終了", "[ / ] 補正", "r 再取得"]
    if offset_ms:
        parts.append(f"補正 {offset_ms:+d}ms")
    if lyrics is not None and lyrics.source:
        parts.append(f"出典 {lyrics.source}")
    if lyrics is not None and lyrics.lines and not lyrics.synced:
        parts.append("同期なし")
    if status:
        parts.append(status)
    return _c(DIM, _fit("  " + "   ".join(parts), columns), color)


def _finish(text: str, signature: tuple, color: bool) -> Frame:
    return Frame(text=text if color else _strip_ansi(text), signature=signature)


def _fit(text: str, columns: int) -> str:
    """端末幅に収める。全角を2桁として数える。"""
    limit = max(4, columns - 1)
    width, out = 0, []
    for char in text:
        step = 2 if _is_wide(char) else 1
        if width + step > limit:
            out.append("…")
            break
        out.append(char)
        width += step
    return "".join(out)


def _is_wide(char: str) -> bool:
    import unicodedata

    return unicodedata.east_asian_width(char) in ("W", "F")


def _c(code: str, text: str, color: bool) -> str:
    return f"{code}{text}{RESET}" if color else text


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)


def supports_ansi() -> bool:
    return sys.stdout.isatty() and os.environ.get("TERM") not in (None, "dumb")
