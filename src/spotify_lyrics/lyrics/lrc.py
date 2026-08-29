"""LRC 形式（`[mm:ss.xx] 歌詞`）のパーサ。

同期歌詞はこの形式で配られるのが事実上の標準。
1行に複数のタイムタグが付くことがある（サビの繰り返し）ので、そこも展開する。
"""

from __future__ import annotations

import re

from ..models import LyricLine

# [00:12.34] / [00:12.345] / [0:12] のいずれも許す
_TAG = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# [ar:...] [ti:...] [offset:+200] などのメタタグ
_META = re.compile(r"^\[[a-zA-Z#]+:(.*)\]$")
_OFFSET = re.compile(r"^\[offset:\s*([+-]?\d+)\s*\]$", re.IGNORECASE)


def parse_lrc(text: str) -> list[LyricLine]:
    """LRC を時刻順の行に変換する。タイムタグが1つも無ければ空リストを返す。"""
    lines: list[LyricLine] = []
    offset_ms = 0

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue

        offset_match = _OFFSET.match(stripped)
        if offset_match:
            # LRC の offset は「歌詞を早める」向きが正。表示側の補正と符号を揃える。
            offset_ms = -int(offset_match.group(1))
            continue

        tags = list(_TAG.finditer(stripped))
        if not tags:
            continue
        if _META.match(stripped):
            continue

        body = stripped[tags[-1].end() :].strip()
        for tag in tags:
            lines.append(LyricLine(text=body, time_ms=_to_ms(tag) + offset_ms))

    lines.sort(key=lambda line: (line.time_ms or 0))
    return lines


def _to_ms(match: re.Match[str]) -> int:
    minutes, seconds, fraction = match.group(1), match.group(2), match.group(3) or "0"
    # ".5" は 500ms、".05" は 50ms、".345" は 345ms。桁数に合わせて換算する。
    millis = int(fraction) * 10 ** (3 - len(fraction))
    return (int(minutes) * 60 + int(seconds)) * 1000 + millis


def parse_plain(text: str) -> list[LyricLine]:
    """時刻なしの歌詞を行に割る。前後の空行だけ落とす。"""
    rows = [line.rstrip() for line in text.splitlines()]
    while rows and not rows[0].strip():
        rows.pop(0)
    while rows and not rows[-1].strip():
        rows.pop()
    return [LyricLine(text=row) for row in rows]
