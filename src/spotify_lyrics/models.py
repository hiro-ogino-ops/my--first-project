"""再生状態と歌詞を表す型。

Spotify のポーリング間隔（数秒）より歌詞の1行は短いので、
「最後に取得した位置＋経過時間」で現在位置を推定できるようにしてある。
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    """再生中の曲。歌詞検索のキーになる情報だけ持つ。"""

    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    track_id: str | None = None

    def __str__(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title

    @property
    def duration_sec(self) -> int:
        return round(self.duration_ms / 1000)

    @property
    def key(self) -> str:
        """キャッシュと同一曲判定に使う正規化キー。

        track_id があればそれが一番確実だが、ローカルファイル再生では無いことがあるので
        その場合は曲名・アーティスト・尺から作る。
        """
        if self.track_id:
            return f"id:{self.track_id}"
        return "meta:" + "|".join(
            [normalize(self.title), normalize(self.artist), str(self.duration_sec)]
        )


@dataclass(frozen=True)
class Playback:
    """ある瞬間に観測した再生状態。"""

    track: Track
    progress_ms: int
    is_playing: bool
    fetched_at: float  # time.monotonic() の値

    def position_ms(self, now: float | None = None) -> int:
        """今この瞬間の再生位置（ミリ秒）を推定する。

        一時停止中は観測値をそのまま返す。再生中は取得からの経過時間を足し、
        曲の長さでクリップする。
        """
        if not self.is_playing:
            return max(0, self.progress_ms)
        current = time.monotonic() if now is None else now
        position = self.progress_ms + int((current - self.fetched_at) * 1000)
        if self.track.duration_ms:
            position = min(position, self.track.duration_ms)
        return max(0, position)


@dataclass(frozen=True)
class LyricLine:
    """歌詞1行。time_ms が None なら時刻情報なし（プレーン歌詞）。"""

    text: str
    time_ms: int | None = None

    @property
    def is_interlude(self) -> bool:
        return not self.text.strip()


@dataclass(frozen=True)
class Lyrics:
    """1曲ぶんの歌詞。

    本文は外部サービスから取得したものをそのまま保持し、加工はしない。
    """

    lines: tuple[LyricLine, ...] = ()
    synced: bool = False
    source: str = ""
    instrumental: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.instrumental and not any(line.text.strip() for line in self.lines)

    def plain_text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def to_dict(self) -> dict:
        return {
            "lines": [{"text": line.text, "time_ms": line.time_ms} for line in self.lines],
            "synced": self.synced,
            "source": self.source,
            "instrumental": self.instrumental,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Lyrics":
        lines = tuple(
            LyricLine(text=item.get("text", ""), time_ms=item.get("time_ms"))
            for item in payload.get("lines", [])
        )
        return cls(
            lines=lines,
            synced=bool(payload.get("synced")),
            source=str(payload.get("source", "")),
            instrumental=bool(payload.get("instrumental")),
        )


# 「(Remastered 2011)」「- Live」「feat. X」など、歌詞検索の邪魔になる装飾
_NOISE = re.compile(
    r"\s*[\(\[]\s*(?:remaster(?:ed)?|live|acoustic|deluxe|bonus|mono|stereo|explicit|"
    r"radio edit|single version|album version|feat\.?|ft\.?)[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
_DASH_SUFFIX = re.compile(
    r"\s+-\s+(?:remaster(?:ed)?|live|acoustic|\d{4}\s+remaster).*$", re.IGNORECASE
)


def strip_decorations(title: str) -> str:
    """曲名から再発盤やライブ表記を落とす。検索が当たらないときの2手目に使う。"""
    cleaned = _NOISE.sub("", title)
    cleaned = _DASH_SUFFIX.sub("", cleaned)
    return cleaned.strip() or title.strip()


def normalize(value: str) -> str:
    """比較用の正規化。全角/半角と記号のゆれを吸収する。"""
    folded = unicodedata.normalize("NFKC", value).casefold()
    folded = _NOISE.sub("", folded)
    return re.sub(r"[^\w\s]", "", folded).strip()
