"""デザイン担当。

表紙（見出し画像）と記事内の図版を作る。画像生成モデルは使わず、
モデルには SVG を書かせて、こちらで検証・ラスタライズする。
理由は3つ: 文字が崩れない・ブランドの色を強制できる・差分が読める。

PNG 変換は cairosvg か rsvg-convert があれば行う。無い環境では SVG のまま残し、
note には PNG/JPG で入稿する必要がある旨を警告する。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from ..config import Settings
from ..llm import LLMClient
from ..models import Article, DesignAsset
from ..prompts import company_system, tagged
from ..store import Store

ROLE = "デザイン担当"


class Designer:
    def __init__(self, settings: Settings, llm: LLMClient, store: Store) -> None:
        self.settings = settings
        self.llm = llm
        self.store = store

    def design(self, slug: str) -> list[DesignAsset]:
        article = self.store.get_article(slug)
        if article is None:
            raise KeyError(f"記事 {slug} が見つかりません。先に執筆してください。")

        asset_dir = self.settings.out_dir / slug / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        assets = [self._cover(article, asset_dir)]
        assets.extend(self._inlines(article, asset_dir))

        self.store.save_assets(slug, assets)
        self.store.set_topic_status(slug, "designed")
        print(f"[design] {slug}: 画像 {len(assets)} 点を {asset_dir} に出力しました")
        return assets

    # ------------------------------------------------------------------ 表紙
    def _cover(self, article: Article, asset_dir: Path) -> DesignAsset:
        config = self.settings.staff.design
        svg = sanitize_svg(
            self.llm.generate_text(
                company_system(self.settings, ROLE),
                _cover_prompt(article, config.palette, config.cover_width, config.cover_height),
                task="cover_svg",
            )
        )
        path = _write_image(asset_dir / "cover.svg", svg)
        return DesignAsset(
            kind="cover",
            path=str(path),
            alt=article.topic.title,
            caption="見出し画像",
        )

    # ------------------------------------------------------------ 記事内の図
    def _inlines(self, article: Article, asset_dir: Path) -> list[DesignAsset]:
        headings = _headings(article.body)[: self.settings.staff.design.inline_images]
        assets: list[DesignAsset] = []
        for i, heading in enumerate(headings, start=1):
            svg = sanitize_svg(
                self.llm.generate_text(
                    company_system(self.settings, ROLE),
                    _inline_prompt(heading, article.body, self.settings.staff.design.palette),
                    task="inline_svg",
                )
            )
            path = _write_image(asset_dir / f"figure-{i}.svg", svg)
            assets.append(
                DesignAsset(kind="inline", path=str(path), alt=heading, caption=heading)
            )
        return assets


# ------------------------------------------------------------------ ユーティリティ
def _headings(body: str) -> list[str]:
    return [line.lstrip("# ").strip() for line in body.splitlines() if line.startswith("## ")]


def sanitize_svg(raw: str) -> str:
    """モデル出力から <svg>…</svg> だけを取り出し、危険な要素を落とす。"""
    match = re.search(r"<svg\b.*?</svg>", raw, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError("SVG を取り出せませんでした（モデル出力に <svg> がありません）")
    svg = match.group(0)
    # 外部読み込みとスクリプトは持ち込ませない。
    svg = re.sub(r"<script\b.*?</script>", "", svg, flags=re.DOTALL | re.IGNORECASE)
    svg = re.sub(r'\son\w+="[^"]*"', "", svg, flags=re.IGNORECASE)
    svg = re.sub(r"<(image|foreignObject)\b.*?(/>|</\1>)", "", svg, flags=re.DOTALL | re.IGNORECASE)
    return svg


def _write_image(svg_path: Path, svg: str) -> Path:
    """SVG を書き出し、可能なら PNG に変換してそのパスを返す。"""
    svg_path.write_text(svg, encoding="utf-8")
    png_path = svg_path.with_suffix(".png")
    if rasterize(svg_path, png_path):
        return png_path
    print(
        f"[design] {svg_path.name} を PNG に変換できませんでした。"
        "note には PNG/JPG で入稿する必要があります "
        "(pip install cairosvg か librsvg の導入を検討してください)"
    )
    return svg_path


def rasterize(svg_path: Path, png_path: Path) -> bool:
    """SVG を PNG にする。使える手段がなければ False。"""
    try:
        import cairosvg  # type: ignore[import-not-found]

        cairosvg.svg2png(url=str(svg_path), write_to=str(png_path))
        return True
    except ImportError:
        pass
    except Exception as exc:  # pragma: no cover - 変換系の失敗はここでまとめて握る
        print(f"[design] cairosvg での変換に失敗: {exc}")

    converter = shutil.which("rsvg-convert")
    if converter:
        result = subprocess.run(
            [converter, str(svg_path), "-o", str(png_path)],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    return False


# ------------------------------------------------------------------ プロンプト
def _cover_prompt(article: Article, palette: list[str], width: int, height: int) -> str:
    return (
        f"{tagged('タイトル', article.topic.title)}\n\n"
        f"{tagged('リード', article.lead)}\n\n"
        f"{tagged('使う色', ', '.join(palette))}\n\n"
        f"note の見出し画像を SVG で作ってください（{width}x{height}）。\n"
        "条件:\n"
        "- タイトルは読める大きさで入れる。長い場合は意味の切れ目で改行する\n"
        "- 使う色は指定パレットのみ。グラデーションは1つまで\n"
        "- 外部フォント・外部画像・script は使わない（font-family は sans-serif 等の総称名のみ）\n"
        "- スマホのタイムラインで縮小表示されても文字が潰れないコントラストにする\n"
        "- SVG のコードだけを出力する"
    )


def _inline_prompt(heading: str, body: str, palette: list[str]) -> str:
    return (
        f"{tagged('見出し', heading)}\n\n"
        f"{tagged('本文', body[:3000])}\n\n"
        f"{tagged('使う色', ', '.join(palette))}\n\n"
        "この見出しの内容を1枚で理解させる図を SVG で作ってください（1200x600）。\n"
        "条件:\n"
        "- 本文に書かれている手順や関係だけを図にする。新しい情報を足さない\n"
        "- 文字は最小限。図を見れば流れが分かる状態にする\n"
        "- 外部フォント・外部画像・script は使わない\n"
        "- SVG のコードだけを出力する"
    )
