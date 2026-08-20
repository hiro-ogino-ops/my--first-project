"""執筆担当。

売れた note と、過去に配信したレターを『型』として参照しながら本文を書く。
参照するのは文章そのものではなく、構成・語り口・どこで読者を掴んでいるか。
"""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..llm import LLMClient
from ..models import Article, Topic
from ..prompts import company_system, tagged
from ..store import Store

ROLE = "執筆担当"
MAX_REFERENCE_CHARS = 2500


class Writer:
    def __init__(self, settings: Settings, llm: LLMClient, store: Store) -> None:
        self.settings = settings
        self.llm = llm
        self.store = store

    def write(self, slug: str) -> Article:
        topic = self.store.get_topic(slug)
        if topic is None:
            raise KeyError(f"企画 {slug} が見つかりません")

        references = self._references()
        letters = self._letters()
        system = company_system(self.settings, ROLE)

        body = self.llm.generate_text(
            system,
            _body_prompt(topic, references, letters, self.settings.product.target_chars),
            long=True,
            task="write_body",
        )
        lead = self.llm.generate_text(system, _lead_prompt(topic), task="lead")
        teaser = self.llm.generate_text(system, _teaser_prompt(topic, body), task="teaser")

        article = Article(topic=topic, body=body.strip(), lead=lead.strip(), paid_teaser=teaser.strip())
        self.store.save_article(article)
        self.store.set_topic_status(slug, "written")
        print(f"[writing] {slug} を執筆しました（{article.char_count}文字）")
        return article

    # -------------------------------------------------------------- 参照資料
    def _references(self) -> list[tuple[str, str]]:
        """売れた記事の本文を上位から取り出す。まだ実績がなければ空。"""
        config = self.settings.staff.writing
        summary = self.store.sales_summary(self.settings.staff.planning.sales_lookback_days)
        references: list[tuple[str, str]] = []
        for row in summary[: config.reference_top_n]:
            article = self.store.get_article(row["slug"])
            if article:
                references.append((f"{row['title']}（{row['quantity']}本）", article.body))
        return references

    def _letters(self) -> list[tuple[str, str]]:
        """過去に配信したレターを読み込む。"""
        letter_dir = Path(self.settings.staff.writing.letter_dir)
        if not letter_dir.exists():
            return []
        letters: list[tuple[str, str]] = []
        for path in sorted(letter_dir.glob("*")):
            if path.suffix.lower() in {".md", ".txt"}:
                letters.append((path.stem, path.read_text(encoding="utf-8")))
        return letters[: self.settings.staff.writing.reference_top_n]


def _clip(text: str) -> str:
    return text[:MAX_REFERENCE_CHARS] + ("…" if len(text) > MAX_REFERENCE_CHARS else "")


def _body_prompt(
    topic: Topic,
    references: list[tuple[str, str]],
    letters: list[tuple[str, str]],
    target_chars: int,
) -> str:
    reference_block = "\n\n".join(f"### {name}\n{_clip(body)}" for name, body in references)
    letter_block = "\n\n".join(f"### {name}\n{_clip(body)}" for name, body in letters)

    parts = [
        tagged("タイトル", topic.title),
        tagged("構成", "\n".join(f"- {h}" for h in topic.outline)),
        tagged(
            "企画意図",
            f"切り口: {topic.angle}\n読者の困りごと: {topic.reader_problem}\n"
            f"読後の約束: {topic.promise}",
        ),
    ]
    if reference_block:
        parts.append(tagged("売れた記事", reference_block))
    if letter_block:
        parts.append(tagged("過去のレター", letter_block))

    parts.append(
        "上の構成に沿って note の本文を Markdown で書いてください。\n"
        "条件:\n"
        f"- 全体で {target_chars} 文字前後\n"
        "- 見出しは構成の順に `## ` で立てる。タイトルは `# ` で1つだけ\n"
        "- 各見出しの中には、必ず『具体的な手順』か『実際の例』を1つ以上入れる\n"
        "- 読者がその場で真似できない抽象論だけの段落を作らない\n"
        "- 『売れた記事』『過去のレター』は文体と構成の参考にするだけで、文章を流用しない\n"
        "- 前置きや「以下に本文を示します」といったメタな断りは書かず、本文だけを出力する"
    )
    return "\n\n".join(parts)


def _lead_prompt(topic: Topic) -> str:
    return (
        f"{tagged('タイトル', topic.title)}\n\n"
        f"{tagged('読者の困りごと', topic.reader_problem)}\n\n"
        f"{tagged('読後の約束', topic.promise)}\n\n"
        "この記事の冒頭リード文を2〜3文で書いてください。"
        "読者が『これは自分の話だ』と気づく具体的な状況から入り、最後に読後に得られるものを置く。"
        "煽り文句と誇張は使わない。リード文だけを出力する。"
    )


def _teaser_prompt(topic: Topic, body: str) -> str:
    return (
        f"{tagged('タイトル', topic.title)}\n\n"
        f"{tagged('本文', body[:4000])}\n\n"
        "有料ラインの直前に置く『この先で分かること』を箇条書き3点で書いてください。\n"
        "条件:\n"
        "- 本文に実際に書かれている内容だけを挙げる（書いていないことを匂わせない）\n"
        "- 各項目は名詞止めで、何が手に入るかが一読で分かること\n"
        "- 箇条書きだけを出力する"
    )
