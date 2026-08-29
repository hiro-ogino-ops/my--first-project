"""LRCLIB（https://lrclib.net）から歌詞を探す。

この供給元を選んだ理由:
  - APIキーもアカウントも要らない（配布するアプリで唯一現実的）
  - 同期歌詞（LRC）を持っている曲が多い
  - 曲の長さで照合できるので、同名異曲を掴みにくい

探し方は2段階。まず /api/get で「曲名・アーティスト・アルバム・尺」の完全一致を狙い、
外れたら /api/search であいまい検索して、こちらで採点して選ぶ。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Callable

from .._http import HttpError, request_json
from ..models import Lyrics, Track, normalize, strip_decorations
from .lrc import parse_lrc, parse_plain

API_BASE = "https://lrclib.net/api"
# 尺がこれ以上ずれていたら別の曲とみなす
DURATION_TOLERANCE_SEC = 8


class LrcLibProvider:
    name = "lrclib"

    def __init__(self, *, http: Callable[..., Any] = request_json, timeout: float = 10.0) -> None:
        self._http = http
        self._timeout = timeout

    def fetch(self, track: Track) -> Lyrics | None:
        record = self._exact(track) or self._search(track)
        if record is None:
            return None
        return _to_lyrics(record)

    # ------------------------------------------------------------------ 段階1
    def _exact(self, track: Track) -> dict | None:
        params = {
            "track_name": track.title,
            "artist_name": _primary_artist(track.artist),
            "album_name": track.album,
            "duration": track.duration_sec or None,
        }
        try:
            return self._http(f"{API_BASE}/get", params=params, timeout=self._timeout)
        except HttpError as exc:
            if exc.status == 404:
                return None
            raise

    # ------------------------------------------------------------------ 段階2
    def _search(self, track: Track) -> dict | None:
        """曲名の装飾（Remastered など）を落として検索し、候補を採点して選ぶ。"""
        queries = [
            {"track_name": track.title, "artist_name": _primary_artist(track.artist)},
            {"track_name": strip_decorations(track.title), "artist_name": _primary_artist(track.artist)},
            {"q": f"{_primary_artist(track.artist)} {strip_decorations(track.title)}".strip()},
        ]

        seen: list[dict] = []
        for params in queries:
            try:
                results = self._http(f"{API_BASE}/search", params=params, timeout=self._timeout)
            except HttpError as exc:
                if exc.status == 404:
                    continue
                raise
            if results:
                seen.extend(results)
            best = _pick(seen, track)
            if best is not None:
                return best
        return None


def _pick(candidates: list[dict], track: Track) -> dict | None:
    """候補から1件選ぶ。尺が近いこと > 曲名・アーティストの一致 > 同期歌詞があること。"""
    scored: list[tuple[float, dict]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        duration = float(item.get("duration") or 0)
        gap = abs(duration - track.duration_sec) if track.duration_sec and duration else None
        if gap is not None and gap > DURATION_TOLERANCE_SEC:
            continue

        score = 0.0
        score += 3.0 * _ratio(item.get("trackName", ""), track.title)
        score += 2.0 * _ratio(item.get("artistName", ""), track.artist)
        if gap is not None:
            score += 2.0 * (1 - gap / DURATION_TOLERANCE_SEC)
        if item.get("syncedLyrics"):
            score += 1.5
        if item.get("instrumental"):
            score -= 0.5
        scored.append((score, item))

    if not scored:
        return None
    score, best = max(scored, key=lambda pair: pair[0])
    # 曲名もアーティストも似ていない候補を掴まないための下限
    return best if score >= 2.5 else None


def _ratio(left: str, right: str) -> float:
    a, b = normalize(left or ""), normalize(right or "")
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _primary_artist(artist: str) -> str:
    """「A, B」の共演表記は先頭だけにする。LRCLIB 側は主アーティストで登録されがち。"""
    return artist.split(",")[0].strip() if artist else ""


def _to_lyrics(record: dict) -> Lyrics | None:
    if record.get("instrumental"):
        return Lyrics(lines=(), synced=False, source="lrclib", instrumental=True)

    synced = record.get("syncedLyrics") or ""
    if synced.strip():
        lines = parse_lrc(synced)
        if lines:
            return Lyrics(lines=tuple(lines), synced=True, source="lrclib")

    plain = record.get("plainLyrics") or ""
    if plain.strip():
        return Lyrics(lines=tuple(parse_plain(plain)), synced=False, source="lrclib")
    return None
