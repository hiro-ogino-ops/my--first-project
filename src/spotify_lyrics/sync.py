"""再生位置から「いま歌っている行」を割り出す。

行数が多い曲でも毎フレーム線形探索したくないので二分探索を使う。
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Sequence

from .models import LyricLine


def active_index(lines: Sequence[LyricLine], position_ms: int, offset_ms: int = 0) -> int:
    """現在行のインデックス。まだ1行目が来ていなければ -1。

    offset_ms を足すと歌詞が「早く」進む（表示がずれるときの手動補正用）。
    """
    times = [line.time_ms for line in lines if line.time_ms is not None]
    if not times or len(times) != len(lines):
        return -1
    return bisect_right(times, position_ms + offset_ms) - 1


def window(lines: Sequence[LyricLine], index: int, height: int) -> tuple[int, int]:
    """現在行が真ん中に来るような表示範囲 [start, end) を返す。

    曲の先頭と末尾では中央に寄せられないので、画面を埋める向きに寄せる。
    """
    if height <= 0 or not lines:
        return (0, 0)
    if len(lines) <= height:
        return (0, len(lines))

    start = max(0, index - height // 2)
    start = min(start, len(lines) - height)
    return (start, start + height)


def format_ms(value: int) -> str:
    """1234567 → "20:34"。"""
    seconds = max(0, value) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"
