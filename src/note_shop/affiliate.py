"""アフィリエイト担当（Amazonアソシエイト）。

記事に出てきた機材を、人間が登録した商品台帳（config/amazon-catalog.yaml）から選び、
PR表記・アソシエイトの開示文・アソシエイトリンクを本文に差し込む。

ここもモデルに任せない。
- 商品を選ぶのは台帳とキーワード一致だけ。AIは存在しない ASIN を平気で作るため。
- 価格は載せない。Amazon の価格は変わり続け、古い価格の掲載は規約違反になりうる。
- 自分で検証していない商品は既定で載せない（company.tone と同じ方針）。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import BaseModel, Field

from .config import AffiliateConfig

ASIN_PATTERN = re.compile(r"^[A-Z0-9]{10}$")
# amazon.co.jp のトラッキングIDは末尾が -22（それ以外は他国のIDを取り違えている可能性が高い）
JP_TAG_PATTERN = re.compile(r"^[A-Za-z0-9_-]+-22$")
AMAZON_LINK_PATTERN = re.compile(r"https://www\.amazon\.co\.jp/[^\s)\"'>]+")
PRICE_PATTERN = re.compile(r"([0-9０-９][0-9０-９,，]*\s*円|[¥￥]\s*[0-9０-９])")

SECTION_HEADING = "## この記事で扱った機材"


class CatalogItem(BaseModel):
    """台帳の1商品。人間が自分で登録する。"""

    asin: str
    name: str
    keywords: list[str] = Field(default_factory=list, description="本文と照合する語")
    verified: bool = False
    verified_scope: str = Field(default="", description="自分で検証した範囲（記事にそのまま出る）")
    note: str = Field(default="", description="向いている人・注意点など、1行の補足")


@dataclass
class Placement:
    """記事1本に差し込む商品と、その根拠。"""

    item: CatalogItem
    url: str
    matched: list[str] = field(default_factory=list)


@dataclass
class ComplianceIssue:
    level: str  # "error" / "warning"
    message: str


class EarningRecord(BaseModel):
    """アソシエイトのレポートから取り込む1行（日別・商品別の紹介料）。"""

    report_date: date
    asin: str = ""
    name: str = ""
    clicks: int = 0
    ordered: int = 0
    shipped: int = 0
    revenue: int = 0
    tracking_id: str = ""


# ------------------------------------------------------------------ 台帳
def load_catalog(path: str | Path) -> list[CatalogItem]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"商品台帳 {path} が見つかりません。config/amazon-catalog.yaml を用意してください。"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = [CatalogItem(**raw) for raw in data.get("products") or []]
    for item in items:
        if not ASIN_PATTERN.match(item.asin):
            raise ValueError(f"ASIN の形式が不正です: {item.asin}（{item.name}）")
    return items


def build_link(asin: str, tracking_id: str) -> str:
    """商品ページへのアソシエイトリンク。短縮URLは使わない（中身が読めるように）。"""
    return f"https://www.amazon.co.jp/dp/{asin}?tag={tracking_id}"


# ------------------------------------------------------------------ 選定
def select_products(text: str, catalog: Iterable[CatalogItem],
                    config: AffiliateConfig) -> list[Placement]:
    """本文に実際に出てきた語で商品を選ぶ。出てこない商品は差し込まない。"""
    normalized = _normalize(text)
    scored: list[tuple[int, int, Placement]] = []
    for order, item in enumerate(catalog):
        if not item.verified and not config.allow_unverified:
            continue
        matched = [kw for kw in item.keywords if kw and _normalize(kw) in normalized]
        if len(matched) < config.min_keyword_hits:
            continue
        placement = Placement(item, build_link(item.asin, config.tracking_id), matched)
        # 一致語が多い順、同点なら台帳の並び順（人間の優先順位）
        scored.append((-len(matched), order, placement))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [placement for _, _, placement in scored[: config.max_products_per_article]]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


# ------------------------------------------------------------------ 差し込み
def disclosure(config: AffiliateConfig) -> str:
    return config.disclosure.format(site_name=config.site_name)


def render_header(config: AffiliateConfig) -> str:
    """本文の冒頭に置く広告表記。リンクより前に見えないと意味がない。"""
    return f"{config.pr_label}\n\n{disclosure(config)}"


def render_section(placements: list[Placement]) -> str:
    lines = [SECTION_HEADING, ""]
    for p in placements:
        status = (
            f"検証済み: {p.item.verified_scope}" if p.item.verified
            else "未検証（この記事の筆者は実機で確認していません）"
        )
        lines.append(f"- [{p.item.name}]({p.url})")
        lines.append(f"  - {status}")
        if p.item.note:
            lines.append(f"  - {p.item.note}")
    lines += ["", "※ 価格と在庫はリンク先で最新の情報を確認してください。"]
    return "\n".join(lines)


# ------------------------------------------------------------------ 検査
def check_compliance(markdown: str, config: AffiliateConfig) -> list[ComplianceIssue]:
    """書き出した note.md を、貼る前に機械的に検査する。"""
    issues: list[ComplianceIssue] = []
    links = AMAZON_LINK_PATTERN.findall(markdown)
    if not links:
        return issues

    if not config.tracking_id:
        issues.append(ComplianceIssue("error", "affiliate.tracking_id が未設定です"))
    elif not JP_TAG_PATTERN.match(config.tracking_id):
        issues.append(ComplianceIssue(
            "warning", f"トラッキングID {config.tracking_id} が amazon.co.jp の形式(-22)ではありません"
        ))

    for link in links:
        if f"tag={config.tracking_id}" not in link:
            issues.append(ComplianceIssue("error", f"トラッキングIDの無いリンクがあります: {link}"))

    first_link = markdown.find("https://www.amazon.co.jp/")
    head = markdown[:first_link]
    if config.pr_label not in head:
        issues.append(ComplianceIssue("error", "最初のリンクより前に広告表記がありません"))
    if disclosure(config) not in markdown:
        issues.append(ComplianceIssue("error", "アソシエイトの開示文がありません"))

    for line in markdown.splitlines():
        if "amazon.co.jp" in line and PRICE_PATTERN.search(line):
            issues.append(ComplianceIssue("error", f"リンクと同じ行に価格があります: {line.strip()}"))
    return issues


# ------------------------------------------------------------------ 売上取り込み
# アソシエイトのレポートCSVは列名が変わることがあるため、ゆれを吸収する。
# 実際にダウンロードしたCSVの列名がここに無ければ追記すること。
EARNING_HEADERS = {
    "report_date": ("date", "日付", "Date"),
    "asin": ("asin", "ASIN"),
    "name": ("name", "商品名", "Name", "Product Name"),
    "clicks": ("clicks", "クリック数", "Clicks"),
    "ordered": ("ordered", "注文数", "Items Ordered"),
    "shipped": ("shipped", "発送済み商品数", "Items Shipped"),
    "revenue": ("revenue", "紹介料", "Ad Fees", "Earnings"),
    "tracking_id": ("tracking_id", "トラッキングID", "Tracking ID"),
}


def read_earnings_csv(path: Path) -> list[EarningRecord]:
    if not path.exists():
        raise FileNotFoundError(f"アソシエイトのレポートCSVが見つかりません: {path}")

    records: list[EarningRecord] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            picked = {key: _pick(row, names) for key, names in EARNING_HEADERS.items()}
            if not picked["report_date"]:
                continue
            records.append(EarningRecord(
                report_date=_parse_date(picked["report_date"]),
                asin=picked["asin"],
                name=picked["name"],
                clicks=_to_int(picked["clicks"]),
                ordered=_to_int(picked["ordered"]),
                shipped=_to_int(picked["shipped"]),
                revenue=_to_int(picked["revenue"]),
                tracking_id=picked["tracking_id"],
            ))
    return records


def _pick(row: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        if row.get(name):
            return str(row[name]).strip()
    return ""


def _to_int(value: str) -> int:
    cleaned = re.sub(r"[^0-9.\-]", "", value or "")
    return int(float(cleaned)) if cleaned not in {"", "-", "."} else 0


def _parse_date(value: str) -> date:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"日付を解釈できません: {value}")
