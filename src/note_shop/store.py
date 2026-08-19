"""会社の共有台帳（SQLite）。

部署はここを介してだけ情報を受け渡す。ファイル出力は成果物、DBは事実の記録。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Article,
    DesignAsset,
    PromoPost,
    QAReport,
    ResearchBrief,
    SaleRecord,
    Topic,
    TopicStatus,
    TrendSignal,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    external_id TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    url         TEXT,
    author      TEXT,
    text        TEXT NOT NULL,
    likes       INTEGER DEFAULT 0,
    reposts     INTEGER DEFAULT 0,
    replies     INTEGER DEFAULT 0,
    posted_at   TEXT,
    collected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefs (
    brief_date TEXT PRIMARY KEY,
    summary    TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topics (
    slug         TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    status       TEXT NOT NULL,
    depth_score  INTEGER,
    demand_score INTEGER,
    created_at   TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS articles (
    slug       TEXT PRIMARY KEY,
    path       TEXT,
    char_count INTEGER,
    price      INTEGER,
    tags       TEXT,
    written_at TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS qa_reports (
    slug               TEXT PRIMARY KEY,
    change_ratio       REAL,
    needs_human_review INTEGER,
    payload            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    slug    TEXT NOT NULL,
    kind    TEXT NOT NULL,
    path    TEXT NOT NULL,
    alt     TEXT,
    caption TEXT,
    UNIQUE(slug, path)
);
CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    slug         TEXT NOT NULL,
    channel      TEXT NOT NULL,
    text         TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    status       TEXT NOT NULL,
    external_id  TEXT DEFAULT '',
    UNIQUE(slug, channel, scheduled_at)
);
CREATE TABLE IF NOT EXISTS sales (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_date  TEXT NOT NULL,
    slug       TEXT NOT NULL,
    quantity   INTEGER NOT NULL,
    unit_price INTEGER NOT NULL,
    source     TEXT NOT NULL,
    UNIQUE(sale_date, slug, unit_price, source)
);
"""


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value else None


class Store:
    """会社の台帳。with 文でも素の生成でも使える。"""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ リサーチ
    def save_signals(self, signals: Iterable[TrendSignal]) -> int:
        """新規シグナルだけ追加し、追加できた件数を返す。"""
        added = 0
        for s in signals:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO signals
                   (external_id, source, url, author, text, likes, reposts, replies,
                    posted_at, collected_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    s.external_id, s.source, s.url, s.author, s.text,
                    s.likes, s.reposts, s.replies,
                    _iso(s.posted_at), _iso(s.collected_at),
                ),
            )
            added += cur.rowcount
        self.conn.commit()
        return added

    def recent_signals(self, hours: int, limit: int = 200) -> list[TrendSignal]:
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            """SELECT * FROM signals WHERE collected_at >= ?
               ORDER BY (likes + reposts + replies) DESC LIMIT ?""",
            (since, limit),
        ).fetchall()
        return [_row_to_signal(r) for r in rows]

    def save_brief(self, brief: ResearchBrief) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO briefs (brief_date, summary, payload) VALUES (?,?,?)",
            (brief.brief_date.isoformat(), brief.summary, brief.model_dump_json()),
        )
        self.conn.commit()

    def latest_brief(self) -> ResearchBrief | None:
        row = self.conn.execute(
            "SELECT payload FROM briefs ORDER BY brief_date DESC LIMIT 1"
        ).fetchone()
        return ResearchBrief.model_validate_json(row["payload"]) if row else None

    # -------------------------------------------------------------- 企画
    def save_topic(self, topic: Topic, status: TopicStatus = "planned") -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO topics
               (slug, title, status, depth_score, demand_score, created_at, payload)
               VALUES (?,?,?,?,?,COALESCE(
                   (SELECT created_at FROM topics WHERE slug = ?), ?), ?)""",
            (
                topic.slug, topic.title, status, topic.depth_score, topic.demand_score,
                topic.slug, datetime.now().isoformat(), topic.model_dump_json(),
            ),
        )
        self.conn.commit()

    def get_topic(self, slug: str) -> Topic | None:
        row = self.conn.execute("SELECT payload FROM topics WHERE slug = ?", (slug,)).fetchone()
        return Topic.model_validate_json(row["payload"]) if row else None

    def list_topics(self, status: TopicStatus | None = None) -> list[tuple[str, str, str]]:
        """(slug, title, status) の一覧を新しい順に返す。"""
        if status:
            rows = self.conn.execute(
                "SELECT slug, title, status FROM topics WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT slug, title, status FROM topics ORDER BY created_at DESC"
            ).fetchall()
        return [(r["slug"], r["title"], r["status"]) for r in rows]

    def set_topic_status(self, slug: str, status: TopicStatus) -> None:
        self.conn.execute("UPDATE topics SET status = ? WHERE slug = ?", (status, slug))
        self.conn.commit()

    # -------------------------------------------------------------- 執筆
    def save_article(self, article: Article, *, path: str | None = None,
                     price: int | None = None, tags: list[str] | None = None) -> None:
        """本文を保存する。

        path / price / tags を省略した場合は、既に入っている値を保つ。
        検品が本文を上書きしたときに商品化の結果を消さないため。
        """
        slug = article.topic.slug
        existing = self.article_meta(slug) or {}
        self.conn.execute(
            """INSERT OR REPLACE INTO articles
               (slug, path, char_count, price, tags, written_at, payload)
               VALUES (?,?,?,?,?,?,?)""",
            (
                slug,
                path if path is not None else existing.get("path", ""),
                article.char_count,
                price if price is not None else existing.get("price", 0),
                json.dumps(
                    tags if tags is not None else existing.get("tags", []), ensure_ascii=False
                ),
                _iso(article.written_at), article.model_dump_json(),
            ),
        )
        self.conn.commit()

    def get_article(self, slug: str) -> Article | None:
        row = self.conn.execute("SELECT payload FROM articles WHERE slug = ?", (slug,)).fetchone()
        return Article.model_validate_json(row["payload"]) if row else None

    def article_meta(self, slug: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT slug, path, char_count, price, tags FROM articles WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            return None
        meta = dict(row)
        meta["tags"] = json.loads(meta["tags"] or "[]")
        return meta

    # -------------------------------------------------------------- 検品
    def save_qa(self, slug: str, report: QAReport) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO qa_reports
               (slug, change_ratio, needs_human_review, payload) VALUES (?,?,?,?)""",
            (slug, report.change_ratio, int(report.needs_human_review), report.model_dump_json()),
        )
        self.conn.commit()

    def get_qa(self, slug: str) -> QAReport | None:
        row = self.conn.execute(
            "SELECT payload FROM qa_reports WHERE slug = ?", (slug,)
        ).fetchone()
        return QAReport.model_validate_json(row["payload"]) if row else None

    # ------------------------------------------------------------ デザイン
    def save_assets(self, slug: str, assets: Iterable[DesignAsset]) -> None:
        for a in assets:
            self.conn.execute(
                """INSERT OR REPLACE INTO assets (slug, kind, path, alt, caption)
                   VALUES (?,?,?,?,?)""",
                (slug, a.kind, a.path, a.alt, a.caption),
            )
        self.conn.commit()

    def get_assets(self, slug: str) -> list[DesignAsset]:
        rows = self.conn.execute(
            "SELECT kind, path, alt, caption FROM assets WHERE slug = ? ORDER BY id", (slug,)
        ).fetchall()
        return [DesignAsset(**dict(r)) for r in rows]

    # -------------------------------------------------------------- 営業
    def schedule_posts(self, posts: Iterable[PromoPost]) -> int:
        added = 0
        for p in posts:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO posts
                   (slug, channel, text, scheduled_at, status, external_id)
                   VALUES (?,?,?,?,?,?)""",
                (p.article_slug, p.channel, p.text, _iso(p.scheduled_at), p.status, p.external_id),
            )
            added += cur.rowcount
        self.conn.commit()
        return added

    def due_posts(self, now: datetime | None = None) -> list[tuple[int, PromoPost]]:
        now = now or datetime.now()
        rows = self.conn.execute(
            """SELECT * FROM posts WHERE status = 'scheduled' AND scheduled_at <= ?
               ORDER BY scheduled_at""",
            (now.isoformat(),),
        ).fetchall()
        return [(r["id"], _row_to_post(r)) for r in rows]

    def pending_posts(self, slug: str | None = None) -> list[tuple[int, PromoPost]]:
        sql = "SELECT * FROM posts WHERE status = 'scheduled'"
        args: tuple[Any, ...] = ()
        if slug:
            sql += " AND slug = ?"
            args = (slug,)
        rows = self.conn.execute(sql + " ORDER BY scheduled_at", args).fetchall()
        return [(r["id"], _row_to_post(r)) for r in rows]

    def mark_post(self, post_id: int, status: str, external_id: str = "") -> None:
        self.conn.execute(
            "UPDATE posts SET status = ?, external_id = ? WHERE id = ?",
            (status, external_id, post_id),
        )
        self.conn.commit()

    # -------------------------------------------------------------- 売上
    def import_sales(self, records: Iterable[SaleRecord]) -> int:
        added = 0
        for r in records:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO sales (sale_date, slug, quantity, unit_price, source)
                   VALUES (?,?,?,?,?)""",
                (r.sale_date.isoformat(), r.slug, r.quantity, r.unit_price, r.source),
            )
            added += cur.rowcount
        self.conn.commit()
        return added

    def sales_summary(self, lookback_days: int = 90) -> list[dict[str, Any]]:
        """記事ごとの売上を多い順に返す。企画担当と執筆担当の一次情報。"""
        since = (date.today() - timedelta(days=lookback_days)).isoformat()
        rows = self.conn.execute(
            """SELECT s.slug,
                      COALESCE(t.title, s.slug) AS title,
                      SUM(s.quantity)                  AS quantity,
                      SUM(s.quantity * s.unit_price)   AS gross,
                      MAX(s.unit_price)                AS unit_price
               FROM sales s
               LEFT JOIN topics t ON t.slug = s.slug
               WHERE s.sale_date >= ?
               GROUP BY s.slug
               ORDER BY gross DESC""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]

    def totals(self, lookback_days: int = 90) -> dict[str, int]:
        since = (date.today() - timedelta(days=lookback_days)).isoformat()
        row = self.conn.execute(
            """SELECT COALESCE(SUM(quantity), 0) AS quantity,
                      COALESCE(SUM(quantity * unit_price), 0) AS gross
               FROM sales WHERE sale_date >= ?""",
            (since,),
        ).fetchone()
        return {"quantity": row["quantity"], "gross": row["gross"]}


def _row_to_signal(row: sqlite3.Row) -> TrendSignal:
    return TrendSignal(
        source=row["source"],
        external_id=row["external_id"],
        url=row["url"] or "",
        author=row["author"] or "",
        text=row["text"],
        likes=row["likes"],
        reposts=row["reposts"],
        replies=row["replies"],
        posted_at=datetime.fromisoformat(row["posted_at"]) if row["posted_at"] else None,
        collected_at=datetime.fromisoformat(row["collected_at"]),
    )


def _row_to_post(row: sqlite3.Row) -> PromoPost:
    return PromoPost(
        channel=row["channel"],
        text=row["text"],
        scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
        status=row["status"],
        article_slug=row["slug"],
        external_id=row["external_id"] or "",
    )
