"""営業担当。

記事1本につき集客ポストを複数作り、公開からの経過時間に合わせて予約枠に入れる。
X / Threads の API には予約投稿がないので、予約は自社の台帳で持ち、
時間が来たものを `run_due()` が投げる（cron から叩く前提）。

autopost が false の間は、台帳に積むだけで外には一切出さない。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..config import Settings
from ..llm import LLMClient
from ..models import Article, PromoDraft, PromoPost
from ..prompts import company_system, tagged
from ..publish import social
from ..store import Store

ROLE = "営業担当"


class SalesRep:
    def __init__(self, settings: Settings, llm: LLMClient, store: Store) -> None:
        self.settings = settings
        self.llm = llm
        self.store = store

    # ------------------------------------------------------------ 集客ポスト
    def promote(self, slug: str, note_url: str = "", base: datetime | None = None) -> list[PromoPost]:
        article = self.store.get_article(slug)
        if article is None:
            raise KeyError(f"記事 {slug} が見つかりません。先に執筆してください。")

        config = self.settings.staff.sales
        draft = self.llm.generate_structured(
            company_system(self.settings, ROLE),
            _promo_prompt(article, note_url, config.posts_per_article),
            PromoDraft,
        )

        posts = self._schedule(draft, slug, note_url, base or datetime.now())
        added = self.store.schedule_posts(posts)
        print(f"[sales] {slug}: {added} 件を予約しました（生成 {len(posts)} 件）")
        for post in posts:
            fit = "" if social.fits(post.channel, post.text) else "  ※文字数超過"
            print(f"  - {post.scheduled_at:%m/%d %H:%M} [{post.channel}] {post.text[:40]}…{fit}")
        return posts

    def _schedule(
        self, draft: PromoDraft, slug: str, note_url: str, base: datetime
    ) -> list[PromoPost]:
        config = self.settings.staff.sales
        tail = (" " + " ".join(draft.hashtags)).rstrip()
        posts: list[PromoPost] = []
        used: set[datetime] = set()
        for i, text in enumerate(draft.posts[: config.posts_per_article]):
            offset = config.schedule_offsets_hours[min(i, len(config.schedule_offsets_hours) - 1)]
            when = next_slot(base + timedelta(hours=offset), config.preferred_hours)
            # ポスト数が予約枠より多いと同じ時刻に重なる。台帳は重複を弾くので、
            # 捨てられないよう15分ずつずらす。
            while when in used:
                when += timedelta(minutes=15)
            used.add(when)
            body = _compose(text, note_url, tail)
            for channel in config.channels:
                posts.append(
                    PromoPost(
                        channel=channel,
                        text=_trim(body, channel),
                        scheduled_at=when,
                        article_slug=slug,
                    )
                )
        return posts

    # -------------------------------------------------------------- 予約実行
    def run_due(self, now: datetime | None = None) -> int:
        """時間が来た予約を処理する。投稿できた件数を返す。"""
        due = self.store.due_posts(now)
        if not due:
            print("[sales] 実行すべき予約はありません")
            return 0

        if not self.settings.staff.sales.autopost:
            print(f"[sales] autopost が false のため {len(due)} 件は手動投稿待ちです:")
            for _, post in due:
                print(f"  - [{post.channel}] {post.text}")
            return 0

        posted = 0
        for post_id, post in due:
            try:
                external_id = social.publish(post.channel, post.text)
            except social.PublishError as exc:
                self.store.mark_post(post_id, "failed")
                print(f"[sales] 投稿失敗 ({post.channel}): {exc}")
                continue
            self.store.mark_post(post_id, "posted", external_id)
            posted += 1
            print(f"[sales] 投稿しました [{post.channel}] {external_id}")
        return posted


# ------------------------------------------------------------------ 予約枠計算
def next_slot(candidate: datetime, preferred_hours: list[int]) -> datetime:
    """候補時刻以降で、最も近い『投稿したい時刻』に丸める。"""
    if not preferred_hours:
        return candidate
    for hour in sorted(preferred_hours):
        slot = candidate.replace(hour=hour, minute=0, second=0, microsecond=0)
        if slot >= candidate:
            return slot
    first = min(preferred_hours)
    return (candidate + timedelta(days=1)).replace(
        hour=first, minute=0, second=0, microsecond=0
    )


def _compose(text: str, note_url: str, tail: str) -> str:
    parts = [text.strip()]
    if note_url:
        parts.append(note_url)
    if tail:
        parts.append(tail)
    return "\n".join(parts)


def _trim(text: str, channel: str) -> str:
    """上限を超えたら本文側を削る（URLとタグは残す）。"""
    if social.fits(channel, text):
        return text
    limit = social.LIMITS.get(channel, 500)
    lines = text.split("\n")
    overflow = len(text) - limit + 1
    lines[0] = lines[0][: max(0, len(lines[0]) - overflow)].rstrip() + "…"
    return "\n".join(lines)


# ------------------------------------------------------------------ プロンプト
def _promo_prompt(article: Article, note_url: str, count: int) -> str:
    return (
        f"{tagged('タイトル', article.topic.title)}\n\n"
        f"{tagged('リード', article.lead)}\n\n"
        f"{tagged('この先で分かること', article.paid_teaser)}\n\n"
        f"{tagged('記事URL', note_url or '（公開後に差し込みます）')}\n\n"
        f"この記事の集客ポストを {count} 本書いてください。\n"
        "条件:\n"
        "- 1本目は『読者の困りごと』から入る。2本目以降は角度を変える"
        "（具体的な数字／失敗談／記事の一節の引用など）\n"
        "- 全角60文字前後。URLとハッシュタグは含めずに本文だけ書く（こちらで付けます）\n"
        "- 「必見」「衝撃」「知らないと損」のような煽り表現は使わない\n"
        "- 記事に書いていないことを匂わせない\n"
        "- hashtags は2〜3個、日本語話者が実際に追っているタグに限る"
    )
