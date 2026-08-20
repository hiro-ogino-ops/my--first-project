"""全部署が共有するプロンプト部品。

本文や構成は必ずタグで囲んで渡す。モデルにとって境界が明確になるだけでなく、
オフラインのスタブ実装が同じタグを読んで動けるようになる。
"""

from __future__ import annotations

from .config import Settings


def company_system(settings: Settings, role: str) -> str:
    """会社の前提 + 役割、をシステムプロンプトにする。"""
    c = settings.company
    prohibitions = "\n".join(f"- {p}" for p in c.prohibitions) or "- （指定なし）"
    return (
        f"あなたは「{c.name}」という、noteで有料記事を継続的に売る小さな会社の{role}です。\n"
        f"\n"
        f"# 会社の方針\n"
        f"- ミッション: {c.mission}\n"
        f"- 読者: {c.audience}\n"
        f"- 扱う領域: {', '.join(c.domains)}\n"
        f"- トーン: {c.tone}\n"
        f"\n"
        f"# 禁止事項\n"
        f"{prohibitions}\n"
        f"\n"
        f"読者は情報にお金を払う人です。読み終えて何も実行できない文章は、この会社では失敗とみなします。"
    )


def tagged(tag: str, body: str) -> str:
    return f"<{tag}>\n{body}\n</{tag}>"


def extract_tag(text: str, tag: str) -> str:
    """<tag> ... </tag> の中身を取り出す。見つからなければ空文字。"""
    start = text.find(f"<{tag}>")
    end = text.find(f"</{tag}>")
    if start == -1 or end == -1 or end < start:
        return ""
    return text[start + len(tag) + 2 : end].strip()
