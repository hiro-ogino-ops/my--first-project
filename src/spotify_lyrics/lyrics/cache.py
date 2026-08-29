"""取得した歌詞のディスクキャッシュ。

同じ曲を再生するたびに外部APIを叩くのは、相手にも自分にも無駄なので挟む。
見つからなかった結果も短い期限で覚える（同じ曲で毎回404を踏まないため）。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from ..models import Lyrics

HIT_TTL = 60 * 60 * 24 * 30  # 30日
MISS_TTL = 60 * 60 * 24  # 1日（そのうち誰かが投稿するかもしれない）


class LyricsCache:
    def __init__(self, directory: Path, *, hit_ttl: int = HIT_TTL, miss_ttl: int = MISS_TTL) -> None:
        self.directory = directory
        self.hit_ttl = hit_ttl
        self.miss_ttl = miss_ttl

    def _path(self, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        return self.directory / f"{digest}.json"

    def get(self, key: str) -> Lyrics | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            path.unlink(missing_ok=True)
            return None

        lyrics = Lyrics.from_dict(payload.get("lyrics", {}))
        ttl = self.miss_ttl if lyrics.is_empty else self.hit_ttl
        if time.time() - float(payload.get("saved_at", 0)) > ttl:
            path.unlink(missing_ok=True)
            return None
        return lyrics

    def put(self, key: str, lyrics: Lyrics) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"key": key, "saved_at": time.time(), "lyrics": lyrics.to_dict()}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def clear(self) -> int:
        if not self.directory.is_dir():
            return 0
        files = list(self.directory.glob("*.json"))
        for path in files:
            path.unlink(missing_ok=True)
        return len(files)
