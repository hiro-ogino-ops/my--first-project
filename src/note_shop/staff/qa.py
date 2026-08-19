"""検品担当。

執筆担当とは別コンテキストのAIに、本文を『言い回しだけ』直させる。
内容・主張・手順・数値には触れさせず、AIが書いたと分かる定型句を人の文章に戻すのが仕事。

書き換わりすぎ（内容にまで手が入った疑い）は機械的に検出し、人間レビューに回す。
"""

from __future__ import annotations

import difflib
import re

from ..config import Settings
from ..llm import LLMClient, build_client
from ..models import Article, QAFinding, QAReport
from ..prompts import company_system, extract_tag, tagged
from ..store import Store

ROLE = "検品担当（校正専任）"


class Inspector:
    def __init__(self, settings: Settings, store: Store, llm: LLMClient | None = None) -> None:
        self.settings = settings
        self.store = store
        # 執筆に使ったクライアントは意図的に使い回さない。別インスタンス・別モデル可。
        self.llm = llm or build_client(settings, settings.staff.qa.model)

    def check(self, slug: str) -> tuple[Article, QAReport]:
        article = self.store.get_article(slug)
        if article is None:
            raise KeyError(f"記事 {slug} が見つかりません。先に執筆してください。")

        config = self.settings.staff.qa
        original = article.body
        rewritten = self.llm.generate_text(
            _qa_system(self.settings),
            _qa_prompt(original, config.banned_phrases),
            long=True,
            task="qa_rewrite",
        ).strip()
        # モデルがタグごと返した場合に備えて剥がす。
        rewritten = extract_tag(rewritten, "本文") or rewritten

        report = build_report(original, rewritten, config.banned_phrases, config.max_change_ratio)
        checked = article.model_copy(update={"body": rewritten})

        self.store.save_article(checked)
        self.store.save_qa(slug, report)
        self.store.set_topic_status(slug, "checked")

        print(
            f"[qa] {slug}: 指摘{len(report.findings)}件 / 変更率{report.change_ratio:.1%}"
            + ("  ※人間レビュー必要" if report.needs_human_review else "")
        )
        return checked, report


# ---------------------------------------------------------------- 判定ロジック
def build_report(
    original: str, rewritten: str, banned: list[str], max_change_ratio: float
) -> QAReport:
    """書き換え前後を突き合わせて検品レポートを作る。"""
    findings = [
        QAFinding(
            kind="banned_phrase",
            before=phrase,
            after="",
            note="AIらしい定型句として除去または言い換え" if phrase not in rewritten else "残存",
        )
        for phrase in banned
        if phrase in original
    ]
    findings.extend(detect_monotony(rewritten))

    change_ratio = 1.0 - difflib.SequenceMatcher(None, original, rewritten).ratio()
    length_drop = 1.0 - (len(rewritten) / len(original)) if original else 0.0

    reasons = []
    if change_ratio > max_change_ratio:
        reasons.append(f"変更率が上限({max_change_ratio:.0%})を超えました")
    if length_drop > 0.2:
        reasons.append(f"本文が{length_drop:.0%}短くなりました（内容が削られた疑い）")
    if any(phrase in rewritten for phrase in banned):
        reasons.append("禁止フレーズが残っています")

    return QAReport(
        findings=findings,
        change_ratio=round(change_ratio, 4),
        needs_human_review=bool(reasons),
        reviewer_note=" / ".join(reasons),
    )


def detect_monotony(text: str, window: int = 4) -> list[QAFinding]:
    """同じ文末が連続していないか見る。AIっぽさが最も出るのはここ。"""
    sentences = [s for s in re.split(r"(?<=[。！？])\s*", text) if s.strip()]
    findings: list[QAFinding] = []
    streak = 1
    for i in range(1, len(sentences)):
        if _ending(sentences[i]) == _ending(sentences[i - 1]):
            streak += 1
        else:
            streak = 1
        if streak == window:
            findings.append(
                QAFinding(
                    kind="repetition",
                    before=sentences[i][-24:],
                    note=f"同じ文末が{window}文続いています",
                )
            )
            streak = 1
    return findings


def _ending(sentence: str) -> str:
    body = sentence.rstrip("。！？!? \n")
    return body[-3:] if len(body) >= 3 else body


# ------------------------------------------------------------------ プロンプト
def _qa_system(settings: Settings) -> str:
    return (
        company_system(settings, ROLE)
        + "\n\n"
        + "あなたは本文の中身を知らされていない校正者です。企画意図も推測しないでください。\n"
        "あなたの権限は『言い回しの修正』だけです。事実・主張・手順・数値・見出し構成・"
        "Markdown記法を変えることは越権行為です。"
    )


def _qa_prompt(body: str, banned: list[str]) -> str:
    banned_block = "\n".join(f"- {p}" for p in banned) or "- （指定なし）"
    return (
        f"{tagged('本文', body)}\n\n"
        f"{tagged('特に避けたい言い回し', banned_block)}\n\n"
        "この本文から『AIが書いた感じ』だけを取り除いてください。\n"
        "やること:\n"
        "- 定型句・空虚な接続表現・過度に整った並列を、人が書く自然な言い方に直す\n"
        "- 同じ文末（です／ます／でしょう）が続く箇所を崩す\n"
        "- 主語が大きすぎる断定（一般論の押しつけ）を、書き手の観測に基づく言い方に直す\n"
        "やらないこと:\n"
        "- 内容の追加・削除・要約\n"
        "- 見出しの変更、順序の入れ替え、Markdown記法の変更\n"
        "- 数値・固有名詞・手順の書き換え\n\n"
        "修正後の本文だけを出力してください。説明・差分・前置きは不要です。"
    )
