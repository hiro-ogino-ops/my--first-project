"""config/company.yaml を読み込んで型付きの設定オブジェクトにする。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/company.yaml")


@dataclass(frozen=True)
class CompanyProfile:
    name: str
    mission: str
    audience: str
    domains: list[str]
    tone: str
    prohibitions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PriceRule:
    min: int
    max: int
    default: int
    depth_weight: int
    demand_weight: int
    round_to: int


@dataclass(frozen=True)
class ProductRule:
    target_chars: int
    free_ratio: float
    price: PriceRule
    tags_per_article: int


@dataclass(frozen=True)
class ResearchConfig:
    sources: list[str]
    lookback_hours: int
    top_n: int
    themes_per_brief: int
    x: dict[str, Any] = field(default_factory=dict)
    threads: dict[str, Any] = field(default_factory=dict)
    manual: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanningConfig:
    topics_per_planning: int
    sales_lookback_days: int
    min_evidence: int


@dataclass(frozen=True)
class WritingConfig:
    reference_top_n: int
    letter_dir: str


@dataclass(frozen=True)
class QAConfig:
    model: str
    max_change_ratio: float
    banned_phrases: list[str]


@dataclass(frozen=True)
class DesignConfig:
    cover_width: int
    cover_height: int
    inline_images: int
    palette: list[str]


@dataclass(frozen=True)
class SalesConfig:
    channels: list[str]
    posts_per_article: int
    schedule_offsets_hours: list[int]
    preferred_hours: list[int]
    autopost: bool


@dataclass(frozen=True)
class Staff:
    research: ResearchConfig
    planning: PlanningConfig
    writing: WritingConfig
    qa: QAConfig
    design: DesignConfig
    sales: SalesConfig


@dataclass(frozen=True)
class Operations:
    articles_per_week: int


@dataclass(frozen=True)
class Economics:
    platform_fee_rate: float
    note_fee_rate: float
    withdrawal_fee: int
    variable_cost_per_article: int


@dataclass(frozen=True)
class LLMSettings:
    model: str
    effort: str
    max_tokens_short: int
    max_tokens_long: int


@dataclass(frozen=True)
class Settings:
    company: CompanyProfile
    product: ProductRule
    staff: Staff
    operations: Operations
    economics: Economics
    llm: LLMSettings
    out_dir: Path
    db_path: Path

    @property
    def offline(self) -> bool:
        """API を呼ばずスタブ生成で動かすかどうか。

        NOTESHOP_OFFLINE が明示されていればそれに従い、
        指定がなければ認証情報の有無で決める。
        """
        flag = os.environ.get("NOTESHOP_OFFLINE", "").strip().lower()
        if flag in {"1", "true", "yes"}:
            return True
        if flag in {"0", "false", "no"}:
            return False
        return not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _require(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ValueError(f"設定ファイルに '{key}' セクションがありません")
    return data[key]


def load_settings(path: str | Path = DEFAULT_CONFIG_PATH) -> Settings:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"会社設定 {path} が見つかりません。config/company.yaml を用意してください。"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    product_raw = dict(_require(data, "product"))
    product = ProductRule(
        target_chars=product_raw["target_chars"],
        free_ratio=product_raw["free_ratio"],
        price=PriceRule(**product_raw["price"]),
        tags_per_article=product_raw["tags_per_article"],
    )

    staff_raw = _require(data, "staff")
    staff = Staff(
        research=ResearchConfig(**staff_raw["research"]),
        planning=PlanningConfig(**staff_raw["planning"]),
        writing=WritingConfig(**staff_raw["writing"]),
        qa=QAConfig(**staff_raw["qa"]),
        design=DesignConfig(**staff_raw["design"]),
        sales=SalesConfig(**staff_raw["sales"]),
    )

    paths = data.get("paths", {})
    return Settings(
        company=CompanyProfile(**_require(data, "company")),
        product=product,
        staff=staff,
        operations=Operations(**_require(data, "operations")),
        economics=Economics(**_require(data, "economics")),
        llm=LLMSettings(**_require(data, "llm")),
        out_dir=Path(paths.get("out_dir", "out")),
        db_path=Path(paths.get("db_path", "data/noteshop.db")),
    )
