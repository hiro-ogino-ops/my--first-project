"""noteshop コマンド。

部署名がそのままサブコマンドになっている。
朝は `noteshop morning`、記事を1本仕上げるときは `noteshop produce <slug>`。
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_settings
from .models import SaleRecord
from .publish import browser, exporter
from .report import render
from .staff import Company

# 売上CSVの列名ゆれを吸収する
SALES_HEADERS = {
    "sale_date": ("sale_date", "date", "日付", "販売日"),
    "slug": ("slug", "article", "記事", "記事ID"),
    "quantity": ("quantity", "count", "本数", "数量"),
    "unit_price": ("unit_price", "price", "単価", "価格"),
    "source": ("source", "経路"),
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 1

    try:
        settings = load_settings(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"設定の読み込みに失敗しました: {exc}", file=sys.stderr)
        return 2

    if settings.offline:
        # 一覧やレポートをパイプで繋げるよう、案内は stderr に出す。
        print(
            "[noteshop] オフラインモードで動作中（ANTHROPIC_API_KEY 未設定 または NOTESHOP_OFFLINE=1）",
            file=sys.stderr,
        )

    try:
        with Company(settings) as company:
            return args.handler(args, company) or 0
    except (KeyError, RuntimeError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


# ------------------------------------------------------------------ ハンドラ
def _cmd_research(args, company: Company) -> int:
    if args.action in {"collect", "all"}:
        company.researcher.collect()
    if args.action in {"brief", "all"}:
        company.researcher.brief()
    return 0


def _cmd_plan(args, company: Company) -> int:
    del args
    company.planner.propose()
    return 0


def _cmd_write(args, company: Company) -> int:
    company.writer.write(args.slug)
    return 0


def _cmd_qa(args, company: Company) -> int:
    _, report = company.inspector.check(args.slug)
    for finding in report.findings:
        print(f"  [{finding.kind}] {finding.before} — {finding.note}")
    return 0


def _cmd_design(args, company: Company) -> int:
    company.designer.design(args.slug)
    return 0


def _cmd_export(args, company: Company) -> int:
    exporter.export(company.settings, company.store, args.slug)
    return 0


def _cmd_promote(args, company: Company) -> int:
    company.sales.promote(args.slug, note_url=args.url)
    return 0


def _cmd_morning(args, company: Company) -> int:
    del args
    company.morning()
    return 0


def _cmd_produce(args, company: Company) -> int:
    company.produce(args.slug, note_url=args.url)
    return 0


def _cmd_sales(args, company: Company) -> int:
    if args.action == "run-due":
        company.sales.run_due()
    elif args.action == "import":
        added = company.store.import_sales(_read_sales_csv(Path(args.path)))
        print(f"[sales] 売上 {added} 件を取り込みました")
    elif args.action == "queue":
        for _, post in company.store.pending_posts(args.slug):
            print(f"{post.scheduled_at:%Y-%m-%d %H:%M} [{post.channel}] {post.text}")
    return 0


def _cmd_topics(args, company: Company) -> int:
    rows = company.store.list_topics(args.status)
    if not rows:
        print("企画がまだありません。`noteshop plan` を実行してください。")
    for slug, title, status in rows:
        print(f"{status:<10} {slug:<40} {title}")
    return 0


def _cmd_report(args, company: Company) -> int:
    text = render(company.settings, company.store, args.days)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"\n[report] {args.out} に保存しました")
    return 0


def _cmd_publish(args, company: Company) -> int:
    """note へのブラウザ自動投稿（オプトイン）。"""
    product = exporter.build_product(company.settings, company.store, args.slug)
    cover = next((a.path for a in product.assets if a.kind == "cover"), None)
    markdown = exporter.render_markdown(product)
    url = browser.publish_draft(
        product.article.topic.title,
        markdown,
        cover_path=cover,
        accepted_tos=args.i_accept_tos,
        headless=not args.headed,
    )
    company.store.set_topic_status(args.slug, "published")
    print(f"[publish] 下書きを作成しました: {url}")
    print("価格と有料ラインは note の管理画面で確認してから公開してください。")
    return 0


# -------------------------------------------------------------------- パーサ
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="noteshop",
        description="note自動販売会社を動かすコマンド",
    )
    parser.add_argument(
        "--config", default=str(DEFAULT_CONFIG_PATH), help="会社設定ファイル (既定: config/company.yaml)"
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("research", help="リサーチ担当: X/Threads の収集と朝会資料の作成")
    p.add_argument("action", choices=["collect", "brief", "all"], nargs="?", default="all")
    p.set_defaults(handler=_cmd_research)

    p = sub.add_parser("plan", help="企画担当: 次に出す note を提案")
    p.set_defaults(handler=_cmd_plan)

    p = sub.add_parser("write", help="執筆担当: 本文を書く")
    p.add_argument("slug")
    p.set_defaults(handler=_cmd_write)

    p = sub.add_parser("qa", help="検品担当: AIっぽい言い回しを直す")
    p.add_argument("slug")
    p.set_defaults(handler=_cmd_qa)

    p = sub.add_parser("design", help="デザイン担当: 表紙と記事内の図を作る")
    p.add_argument("slug")
    p.set_defaults(handler=_cmd_design)

    p = sub.add_parser("export", help="note 入稿用の一式を書き出す")
    p.add_argument("slug")
    p.set_defaults(handler=_cmd_export)

    p = sub.add_parser("promote", help="営業担当: 集客ポストを作って予約する")
    p.add_argument("slug")
    p.add_argument("--url", default="", help="公開済み note の URL")
    p.set_defaults(handler=_cmd_promote)

    p = sub.add_parser("morning", help="毎朝の動き（収集→ブリーフ→企画）")
    p.set_defaults(handler=_cmd_morning)

    p = sub.add_parser("produce", help="企画1件を執筆→検品→デザイン→商品化→予約まで")
    p.add_argument("slug")
    p.add_argument("--url", default="", help="公開済み note の URL")
    p.set_defaults(handler=_cmd_produce)

    p = sub.add_parser("sales", help="予約投稿の実行と売上の取り込み")
    p.add_argument("action", choices=["run-due", "import", "queue"])
    p.add_argument("path", nargs="?", help="import のときの CSV パス")
    p.add_argument("--slug", default=None, help="queue のときの絞り込み")
    p.set_defaults(handler=_cmd_sales)

    p = sub.add_parser("topics", help="企画の一覧")
    p.add_argument("--status", default=None,
                   choices=["planned", "written", "checked", "designed", "published", "dropped"])
    p.set_defaults(handler=_cmd_topics)

    p = sub.add_parser("report", help="KPI レポート")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--out", default=None, help="Markdown を保存するパス")
    p.set_defaults(handler=_cmd_report)

    p = sub.add_parser("publish", help="note へブラウザで下書き投稿（既定で無効）")
    p.add_argument("slug")
    p.add_argument("--i-accept-tos", action="store_true",
                   help="docs/legal-and-risk.md を読み、自己責任で実行することに同意する")
    p.add_argument("--headed", action="store_true", help="ブラウザを表示して実行する")
    p.set_defaults(handler=_cmd_publish)

    return parser


# ------------------------------------------------------------------ CSV 読み
def _read_sales_csv(path: Path) -> list[SaleRecord]:
    if not path.exists():
        raise FileNotFoundError(f"売上CSVが見つかりません: {path}")

    records: list[SaleRecord] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            picked = {key: _pick(row, names) for key, names in SALES_HEADERS.items()}
            if not picked["slug"]:
                continue
            records.append(
                SaleRecord(
                    sale_date=_parse_date(picked["sale_date"]),
                    slug=picked["slug"],
                    quantity=int(picked["quantity"] or 1),
                    unit_price=int(float(picked["unit_price"] or 0)),
                    source=picked["source"] or "note",
                )
            )
    return records


def _pick(row: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        if row.get(name):
            return str(row[name]).strip()
    return ""


def _parse_date(value: str) -> date:
    if not value:
        return date.today()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"日付を解釈できません: {value}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
