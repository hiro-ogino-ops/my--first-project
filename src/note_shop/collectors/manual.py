"""手動インボックス収集。

data/inbox/ に置かれた JSON / CSV を読み込む。X や Threads の API 権限がなくても
（あるいは公式アプリからのエクスポートでも）リサーチ担当を動かせるようにするための口。

JSON: TrendSignal のフィールドを持つオブジェクトの配列
CSV : source,external_id,url,author,text,likes,reposts,replies,posted_at
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from ..config import ResearchConfig
from ..models import TrendSignal


class ManualInboxCollector:
    name = "manual"

    def collect(self, config: ResearchConfig) -> list[TrendSignal]:
        inbox = Path(config.manual.get("inbox", "data/inbox"))
        if not inbox.exists():
            return []

        signals: list[TrendSignal] = []
        for path in sorted(inbox.iterdir()):
            try:
                if path.suffix.lower() == ".json":
                    signals.extend(_load_json(path))
                elif path.suffix.lower() == ".csv":
                    signals.extend(_load_csv(path))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"[research] {path.name} を読み飛ばしました: {exc}")
        return signals


def _load_json(path: Path) -> list[TrendSignal]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("data", [])
    return [_build(dict(item), path) for item in items]


def _load_csv(path: Path) -> list[TrendSignal]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [_build(row, path) for row in csv.DictReader(fh)]


def _build(row: dict, path: Path) -> TrendSignal:
    posted_at = row.get("posted_at") or None
    return TrendSignal(
        source=row.get("source") or "manual",
        external_id=str(row.get("external_id") or f"{path.stem}:{row.get('url') or row['text'][:24]}"),
        url=row.get("url") or "",
        author=row.get("author") or "",
        text=row["text"],
        likes=int(row.get("likes") or 0),
        reposts=int(row.get("reposts") or 0),
        replies=int(row.get("replies") or 0),
        posted_at=datetime.fromisoformat(posted_at) if posted_at else None,
    )
