"""SNS運用担当。

自分の X アカウントを伸ばすための毎日の動きを受け持つ。

1. 分析     … リサーチ担当が集めた「伸びているポスト」から、伸びた理由を型として抽出する
2. 作成     … その型で1日分（既定8本）のポストを作る
3. 振り返り … 取り込んだインプレッションから、伸びた型・滑った型を判定する
4. 除外     … 滑った書き出しを台帳に積み、次の案から機械的に外す
5. 時間割   … 実績の良い時間帯から順に投稿枠を組み、予約台帳に入れる

判定・除外・時間割はすべてコードで決める。同じデータなら毎回同じ結論になるようにするため。
AI が担うのは、型の言語化と本文の作成だけ。
"""

from __future__ import annotations

import csv
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from ..config import SnsConfig, Settings
from ..llm import LLMClient
from ..models import (
    DailyPost,
    DraftBatch,
    DraftPost,
    PatternAnalysis,
    PostMetric,
    PostPattern,
    PromoPost,
    TrendSignal,
)
from ..prompts import company_system, tagged
from ..publish import social
from ..store import Store

ROLE = "SNS運用担当"
OTHER = "その他"

WIN, LOSE, EVEN, PENDING = "伸びた", "滑った", "普通", "保留"

# X のアナリティクス等のエクスポートの列名ゆれを吸収する。
# エクスポートの列名は変わることがあるので、合わなければここに足す。
METRIC_HEADERS = {
    "external_id": ("post_id", "tweet_id", "id", "ポストID", "ツイートID",
                    "post_link", "url", "link", "ポストのリンク"),
    "text": ("text", "post_text", "tweet_text", "本文", "ポスト本文", "ポストのテキスト"),
    "impressions": ("impressions", "impression_count", "インプレッション", "インプレッション数"),
    "likes": ("likes", "like_count", "いいね", "いいね数"),
    "reposts": ("reposts", "retweets", "retweet_count", "リポスト", "リポスト数"),
    "replies": ("replies", "reply_count", "返信", "返信数"),
}
MATCH_PREFIX = 30  # 本文で照合するときに比べる先頭文字数


# ------------------------------------------------------------------ 判定結果
@dataclass
class GroupStat:
    key: str
    n: int
    mean_ratio: float
    verdict: str


@dataclass
class SnsReview:
    baseline: float = 0.0
    patterns: list[GroupStat] = field(default_factory=list)
    hook_types: list[GroupStat] = field(default_factory=list)
    hours: list[GroupStat] = field(default_factory=list)
    top_posts: list[tuple[DailyPost, float]] = field(default_factory=list)
    slid_posts: list[tuple[DailyPost, float]] = field(default_factory=list)

    def scores(self, attr: str) -> dict[str, float]:
        """判定がついたものだけの平均倍率。保留は含めない（＝既定値1.0で扱われる）。"""
        return {s.key: s.mean_ratio for s in getattr(self, attr) if s.verdict != PENDING}


@dataclass
class Screened:
    kept: list[DraftPost] = field(default_factory=list)
    rejected: list[tuple[DraftPost, str]] = field(default_factory=list)


class SnsManager:
    def __init__(self, settings: Settings, llm: LLMClient, store: Store) -> None:
        self.settings = settings
        self.llm = llm
        self.store = store

    @property
    def config(self) -> SnsConfig:
        return self.settings.staff.sns

    # ---------------------------------------------------------------- ① 分析
    def analyze(self) -> PatternAnalysis:
        """直近の伸びているポストから『伸びた理由』を型にする。"""
        signals = self.store.recent_signals(
            self.settings.staff.research.lookback_hours, limit=self.config.signals_for_analysis
        )
        if not signals:
            raise RuntimeError(
                "直近のシグナルがありません。`noteshop research collect` を先に実行してください。"
            )
        known = list(self.store.pattern_library())
        analysis = self.llm.generate_structured(
            company_system(self.settings, ROLE),
            _analysis_prompt(signals, known, self.config),
            PatternAnalysis,
        )
        patterns = [
            p.model_copy(update={"hook_type": _coerce(p.hook_type, self.config.hook_types)})
            for p in analysis.patterns
        ]
        analysis = analysis.model_copy(update={"analysis_date": date.today(), "patterns": patterns})
        self.store.save_pattern_analysis(analysis)
        print(f"[sns] 伸びた型を {len(patterns)} 件抽出しました")
        for p in patterns:
            print(f"  - {p.name}（{p.hook_type}）: {p.why_it_worked[:50]}")
        return analysis

    # ------------------------------------------------------------ ③ 実績取込
    def import_metrics(self, path: str | Path) -> int:
        """インプレッションの CSV を取り込み、自分のポストに紐づける。紐づいた件数を返す。"""
        rows = read_metrics_csv(Path(path))
        posts = self.store.daily_posts()
        by_id = {p.external_id: p for p in posts if p.external_id}
        by_text = {_normalize(p.text)[:MATCH_PREFIX]: p for p in posts}

        matched = 0
        for row in rows:
            post = by_id.get(row.external_id) if row.external_id else None
            if post is None and row.text:
                post = by_text.get(_normalize(row.text)[:MATCH_PREFIX])
            if post is None:
                continue
            self.store.record_metric(post.post_slug, row)
            matched += 1
        print(f"[sns] {Path(path).name}: {matched} / {len(rows)} 件を自分のポストに紐づけました")
        return matched

    def import_metrics_inbox(self) -> int:
        """data/metrics/ に置かれた CSV をすべて取り込む（何度読んでも結果は同じ）。"""
        inbox = Path(self.config.metrics_inbox)
        if not inbox.exists():
            return 0
        return sum(self.import_metrics(p) for p in sorted(inbox.glob("*.csv")))

    # ------------------------------------------------------- ③④ 振り返り・除外
    def review(self, now: datetime | None = None, write: bool = True) -> SnsReview:
        """伸びた型・滑った型を判定し、滑った書き出しを除外リストに積む。"""
        now = now or datetime.now()
        since = now.date() - timedelta(days=self.config.review_days)
        result = build_review(self.store.daily_posts(since, measured_only=True), self.config)
        if not result.baseline:
            print("[sns] 計測済みのポストがまだ無いため、振り返りは保留です")
            return result

        expires = now + timedelta(days=self.config.block_days)
        newly = 0
        for post, ratio in result.slid_posts:
            newly += self.store.block_hook(
                "text", post.hook, f"{post.draft_date} 中央値の{ratio:.2f}倍", expires
            )
        for stat in result.hook_types:
            if stat.verdict == LOSE:
                newly += self.store.block_hook(
                    "type", stat.key, f"{stat.n}本の平均が中央値の{stat.mean_ratio:.2f}倍", expires
                )
        print(f"[sns] 中央値 {result.baseline:.0f} インプで判定。除外リストに {newly} 件追加")
        if write:
            path = self._out_dir() / f"review-{now:%Y-%m-%d}.md"
            path.write_text(render_review(result, self.store.active_blocks(now)), encoding="utf-8")
            print(f"[sns] 振り返り: {path}")
        return result

    # ------------------------------------------------------- ②⑤ 作成・時間割
    def draft(self, day: date | None = None, count: int | None = None,
              force: bool = False, now: datetime | None = None) -> list[DailyPost]:
        """1日分のポストを作り、時間割に並べて予約台帳に入れる。"""
        now = now or datetime.now()
        day = day or now.date()
        count = count or self.config.posts_per_day

        if self.store.daily_posts(day=day):
            if not force:
                raise RuntimeError(f"{day} の分は作成済みです。作り直すなら --force を付けてください。")
            self.store.clear_daily_posts(day)

        patterns = self._patterns_for(day)
        stats = build_review(
            self.store.daily_posts(day - timedelta(days=self.config.review_days), measured_only=True),
            self.config,
        )
        plan = allocate([p.name for p in patterns], stats.scores("patterns"), count,
                        self.config.lose_ratio)
        slots = build_timetable(day, count, self.config.slot_times, stats.scores("hours"),
                                self.config.min_gap_minutes, not_before=now)
        if len(slots) < count:
            print(f"[sns] 今日の残り時間では {len(slots)} 枠しか取れません")
            plan = plan[: len(slots)]

        blocks = self.store.active_blocks(now)
        blocked_texts = [b["value"] for b in blocks if b["kind"] == "text"]
        blocked_types = {b["value"] for b in blocks if b["kind"] == "type"}
        sources = [s.text for s in self.store.recent_signals(
            self.settings.staff.research.lookback_hours, limit=self.config.signals_for_analysis)]

        screened = Screened()
        wanted = math.ceil(len(plan) * self.config.candidates_ratio)
        for _ in range(2):  # 足りなければ1回だけ作り直す
            batch = self.llm.generate_structured(
                company_system(self.settings, ROLE),
                _draft_prompt(patterns, plan, wanted, blocked_texts, blocked_types, self.config),
                DraftBatch,
            )
            names = {p.name for p in patterns}
            candidates = [
                c.model_copy(update={
                    "text": c.text.strip(),
                    "pattern": c.pattern if c.pattern in names else OTHER,
                    "hook_type": _coerce(c.hook_type, self.config.hook_types),
                })
                for c in batch.posts
            ]
            screen(candidates, screened, blocked_texts, blocked_types, sources, self.config)
            if len(screened.kept) >= len(plan):
                break

        chosen = pick(screened.kept, plan)
        if len(chosen) < len(plan):
            print(f"[sns] 条件を満たす案が {len(chosen)} 本しか残りませんでした（目標 {len(plan)} 本）")

        posts = assign(day, chosen, slots, stats.scores("hours"))
        self.store.save_daily_posts(posts)
        self.store.schedule_posts(
            PromoPost(channel="x", text=p.text, scheduled_at=p.scheduled_at, article_slug=p.post_slug)
            for p in posts
        )
        path = self._out_dir() / f"{day:%Y-%m-%d}.md"
        path.write_text(render_timetable(day, posts, screened.rejected), encoding="utf-8")
        print(f"[sns] {day} の時間割（{len(posts)}本）: {path}")
        for p in posts:
            print(f"  - {p.scheduled_at:%H:%M} [{p.pattern}/{p.hook_type}] {p.hook}")
        return posts

    # ------------------------------------------------------------ まとめて
    def daily(self, now: datetime | None = None) -> list[DailyPost]:
        """毎朝の動き: 実績取込 → 振り返り・除外 → 分析 → 作成・時間割。"""
        print("=== SNS運用 ===")
        self.import_metrics_inbox()
        self.review(now)
        self.analyze()
        return self.draft(now=now)

    # ------------------------------------------------------------------ 内部
    def _patterns_for(self, day: date) -> list[PostPattern]:
        """今日の分析で出た型 ＋ 過去に伸びた実績がある型。"""
        analysis = self.store.latest_pattern_analysis()
        if analysis is None or not analysis.patterns:
            raise RuntimeError("型の分析がありません。`noteshop sns analyze` を先に実行してください。")
        patterns = list(analysis.patterns)
        library = self.store.pattern_library()
        since = day - timedelta(days=self.config.review_days)
        stats = build_review(self.store.daily_posts(since, measured_only=True), self.config)
        for stat in stats.patterns:
            if stat.verdict == WIN and stat.key in library and stat.key not in {p.name for p in patterns}:
                patterns.append(library[stat.key])
        return patterns

    def _out_dir(self) -> Path:
        path = self.settings.out_dir / "sns"
        path.mkdir(parents=True, exist_ok=True)
        return path


# ================================================================== 判定ロジック
def build_review(posts: list[DailyPost], config: SnsConfig) -> SnsReview:
    """計測済みポストを、全体の中央値に対する倍率で判定する。"""
    measured = [p for p in posts if p.impressions is not None]
    if not measured:
        return SnsReview()
    baseline = statistics.median(p.impressions for p in measured)
    if baseline <= 0:
        return SnsReview()

    ratios = [(p, p.impressions / baseline) for p in measured]

    def group(key_of) -> list[GroupStat]:
        buckets: dict[str, list[float]] = {}
        for post, ratio in ratios:
            buckets.setdefault(key_of(post), []).append(ratio)
        stats = [
            GroupStat(key, len(rs), statistics.mean(rs), _verdict(len(rs), statistics.mean(rs), config))
            for key, rs in buckets.items()
        ]
        return sorted(stats, key=lambda s: (-s.mean_ratio, s.key))

    ranked = sorted(ratios, key=lambda pr: -pr[1])
    return SnsReview(
        baseline=baseline,
        patterns=group(lambda p: p.pattern),
        hook_types=group(lambda p: p.hook_type),
        hours=group(lambda p: f"{p.scheduled_at.hour:02d}"),
        top_posts=[pr for pr in ranked if pr[1] >= config.win_ratio],
        slid_posts=[pr for pr in ranked if pr[1] <= config.lose_ratio],
    )


def _verdict(n: int, mean_ratio: float, config: SnsConfig) -> str:
    if n < config.min_samples:
        return PENDING
    if mean_ratio >= config.win_ratio:
        return WIN
    if mean_ratio <= config.lose_ratio:
        return LOSE
    return EVEN


def allocate(patterns: list[str], scores: dict[str, float], count: int,
             lose_ratio: float) -> list[str]:
    """何本をどの型で作るか。実績の良い型ほど多く、滑った型は外す（全部滑ったら全部使う）。

    実績の無い型は倍率1.0として扱うので、新しい型にも枠が回る。
    """
    usable = [p for p in patterns if scores.get(p, 1.0) > lose_ratio] or list(patterns)
    if not usable or count <= 0:
        return []
    weights = [scores.get(p, 1.0) for p in usable]
    total = sum(weights)
    raw = [w / total * count for w in weights]
    counts = [int(r) for r in raw]
    # 端数の大きい順に残りを配る（同点は元の順）
    order = sorted(range(len(usable)), key=lambda i: (-(raw[i] - counts[i]), i))
    for i in order[: count - sum(counts)]:
        counts[i] += 1
    ranked = sorted(range(len(usable)), key=lambda i: (-weights[i], i))
    return [usable[i] for i in ranked for _ in range(counts[i])]


def build_timetable(day: date, count: int, slot_times: list[str], hour_scores: dict[str, float],
                    min_gap_minutes: int, not_before: datetime | None = None) -> list[datetime]:
    """投稿時刻を決める。実績の良い時間帯から順に、間隔を空けて取る。

    実績の無い時間帯は倍率1.0。間隔を守ると枠が足りないときは間隔を半分、0と緩める。
    過ぎた時刻と日付をまたぐ時刻は使わない。
    """
    candidates = sorted({datetime.combine(day, time.fromisoformat(t)) for t in slot_times})
    if not_before is not None:
        candidates = [c for c in candidates if c >= not_before]
    ranked = sorted(candidates, key=lambda c: (-hour_scores.get(f"{c.hour:02d}", 1.0), c))

    chosen: list[datetime] = []
    for gap in (min_gap_minutes, min_gap_minutes // 2, 0):
        for c in ranked:
            if len(chosen) >= count:
                break
            if c not in chosen and all(abs((c - x).total_seconds()) >= gap * 60 for x in chosen):
                chosen.append(c)
        if len(chosen) >= count:
            break

    # 候補時刻そのものが足りないときは、最後の枠から間隔を空けて足す
    step = timedelta(minutes=max(min_gap_minutes, 30))
    while len(chosen) < count and chosen:
        nxt = max(chosen) + step
        if nxt.date() != day:
            break
        chosen.append(nxt)
    return sorted(chosen)


def screen(candidates: list[DraftPost], into: Screened, blocked_texts: list[str],
           blocked_types: set[str], sources: list[str], config: SnsConfig) -> Screened:
    """案を機械的に選別する。AI が指示を守らなくても、ここで必ず落ちる。"""
    for c in candidates:
        hook = hook_of(c.text)
        reason = ""
        if not c.text:
            reason = "本文が空"
        elif c.hook_type in blocked_types:
            reason = f"滑った書き出しの種類（{c.hook_type}）"
        elif any(similarity(hook, b) >= config.hook_similarity for b in blocked_texts):
            reason = "滑った書き出しに近い"
        elif not social.fits("x", c.text):
            reason = "文字数超過"
        elif any(similarity(c.text, s) >= config.source_similarity for s in sources):
            reason = "参考ポストに近すぎる（流用防止）"
        elif any(similarity(hook, hook_of(k.text)) >= config.hook_similarity for k in into.kept):
            reason = "同じ日の書き出しと重複"
        if reason:
            into.rejected.append((c, reason))
        else:
            into.kept.append(c)
    return into


def pick(kept: list[DraftPost], plan: list[str]) -> list[DraftPost]:
    """配分どおりに選ぶ。その型の案が足りなければ、残りの案で埋める。"""
    pool = list(kept)
    chosen: list[DraftPost] = []
    for name in plan:
        match = next((c for c in pool if c.pattern == name), None)
        if match is not None:
            pool.remove(match)
            chosen.append(match)
    for c in pool:
        if len(chosen) >= len(plan):
            break
        chosen.append(c)
    return chosen


def assign(day: date, posts: list[DraftPost], slots: list[datetime],
           hour_scores: dict[str, float]) -> list[DailyPost]:
    """配分の先頭（＝実績の良い型）から順に、実績の良い時間帯へ入れる。"""
    best_first = sorted(slots, key=lambda s: (-hour_scores.get(f"{s.hour:02d}", 1.0), s))
    pairs = sorted(zip(best_first, posts), key=lambda pair: pair[0])
    return [
        DailyPost(
            post_slug=f"daily-{day:%Y%m%d}-{i:02d}",
            draft_date=day,
            scheduled_at=slot,
            pattern=post.pattern,
            hook_type=post.hook_type,
            hook=hook_of(post.text),
            text=post.text,
        )
        for i, (slot, post) in enumerate(pairs, start=1)
    ]


# ================================================================== 文字列処理
def hook_of(text: str) -> str:
    """書き出し＝最初の1行。長ければ最初の文で切る。"""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(first) <= 40:
        return first
    m = re.search(r"[。！？!?]", first[:40])
    return first[: m.end()] if m else first[:40]


def _normalize(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"\s+", "", text)


def similarity(a: str, b: str) -> float:
    a, b = _normalize(a), _normalize(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _coerce(value: str, allowed: list[str]) -> str:
    return value if value in allowed else OTHER


# ================================================================== CSV 読み
def read_metrics_csv(path: Path) -> list[PostMetric]:
    if not path.exists():
        raise FileNotFoundError(f"実績CSVが見つかりません: {path}")
    rows: list[PostMetric] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            picked = {key: _pick(row, names) for key, names in METRIC_HEADERS.items()}
            if not picked["impressions"]:
                continue
            rows.append(
                PostMetric(
                    external_id=_post_id(picked["external_id"]),
                    text=picked["text"],
                    impressions=_int(picked["impressions"]),
                    likes=_int(picked["likes"]),
                    reposts=_int(picked["reposts"]),
                    replies=_int(picked["replies"]),
                )
            )
    return rows


def _pick(row: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        if row.get(name.lower()):
            return row[name.lower()]
    return ""


def _post_id(value: str) -> str:
    """URL で渡されても投稿IDだけを取り出す。"""
    m = re.search(r"/status/(\d+)", value)
    return m.group(1) if m else value


def _int(value: str) -> int:
    return int(float(value.replace(",", ""))) if value else 0


# ================================================================== 出力
def render_timetable(day: date, posts: list[DailyPost],
                     rejected: list[tuple[DraftPost, str]]) -> str:
    lines = [f"# {day} の投稿時間割（{len(posts)}本）", ""]
    for p in posts:
        lines += [f"## {p.scheduled_at:%H:%M}　型: {p.pattern}／書き出し: {p.hook_type}", "", p.text, ""]
    if rejected:
        lines += ["---", "", "## 外した案", ""]
        lines += [f"- {reason}: {hook_of(c.text)}" for c, reason in rejected]
    return "\n".join(lines) + "\n"


def render_review(review: SnsReview, blocks: list[dict]) -> str:
    def section(title: str, stats: list[GroupStat]) -> list[str]:
        rows = [f"- {s.verdict}　{s.key}: {s.n}本 / 中央値の{s.mean_ratio:.2f}倍" for s in stats]
        return [f"## {title}", "", *(rows or ["- （データなし）"]), ""]

    lines = [f"# 振り返り（中央値 {review.baseline:.0f} インプ）", ""]
    lines += section("型", review.patterns)
    lines += section("書き出しの種類", review.hook_types)
    lines += section("時間帯", review.hours)
    lines += ["## 伸びたポスト", ""]
    lines += [f"- {r:.2f}倍 {p.hook}" for p, r in review.top_posts] or ["- なし"]
    lines += ["", "## 滑ったポスト", ""]
    lines += [f"- {r:.2f}倍 {p.hook}" for p, r in review.slid_posts] or ["- なし"]
    lines += ["", "## いま除外している書き出し", ""]
    lines += [f"- [{b['kind']}] {b['value']}（{b['reason']}／{b['expires_at'][:10]}まで）"
              for b in blocks] or ["- なし"]
    return "\n".join(lines) + "\n"


# ================================================================== プロンプト
def _analysis_prompt(signals: list[TrendSignal], known: list[str], config: SnsConfig) -> str:
    signal_lines = "\n".join(
        f"- [{s.external_id}] (反応{s.engagement}) {s.text[:200]}" for s in signals
    )
    return (
        "今朝集めた、伸びているポストです。\n\n"
        f"{tagged('シグナル', signal_lines)}\n\n"
        f"{tagged('書き出しの種類', chr(10).join(config.hook_types))}\n\n"
        f"{tagged('過去に使った型の名前', chr(10).join(known) or '（まだありません）')}\n\n"
        f"これらが伸びた理由を、真似できる『型』として{config.patterns_per_analysis}件に絞ってください。\n"
        "条件:\n"
        "- 話題ではなく構造を抜き出す（書き出し・展開・締めの並び）\n"
        "- why_it_worked は『読者がなぜ反応したか』を書く。『共感を呼んだ』のような抽象語で終わらせない\n"
        "- hook_type は書き出しの種類の一覧から必ず1つ選ぶ\n"
        "- 過去に使った型と同じ構造なら、その名前をそのまま使う（名前を変えると実績が引き継がれない）\n"
        "- template は元ポストの文言を含めない。【】の穴埋めだけで構造が分かるようにする\n"
        "- evidence_ids には根拠にしたシグナルの角括弧内のIDを入れる"
    )


def _draft_prompt(patterns: list[PostPattern], plan: list[str], wanted: int,
                  blocked_texts: list[str], blocked_types: set[str], config: SnsConfig) -> str:
    pattern_lines = "\n".join(
        f"- {p.name}（書き出し: {p.hook_type}）構成: {p.structure} / 型: {p.template}"
        for p in patterns
    )
    counts: dict[str, int] = {}
    for name in plan:
        counts[name] = counts.get(name, 0) + 1
    plan_lines = "\n".join(f"- {name}: {n}本" for name, n in counts.items())
    allowed_types = [t for t in config.hook_types if t not in blocked_types]
    banned = "\n".join(f"- {t}" for t in blocked_texts[-30:]) or "（なし）"
    return (
        f"{tagged('型', pattern_lines)}\n\n"
        f"{tagged('配分', plan_lines)}\n\n"
        f"{tagged('件数', str(wanted))}\n\n"
        f"{tagged('書き出しの種類', chr(10).join(allowed_types))}\n\n"
        f"{tagged('使ってはいけない書き出し', banned)}\n\n"
        f"自分のアカウントで投稿するポストを{wanted}本書いてください。"
        "配分の比率を目安に、多めに作ってください（こちらで選別します）。\n"
        "条件:\n"
        "- 型の構造だけを借りる。参考にした他人のポストの言い回しは使わない\n"
        "- 1行目を書き出しとし、1本ごとに書き出しを変える\n"
        "- 使ってはいけない書き出しと、似た言い回しは使わない\n"
        "- 全角140文字以内。URLとハッシュタグは入れない\n"
        "- 会社の領域で、自分が実際に書ける内容に限る。煽り表現は使わない\n"
        "- pattern は型の名前、hook_type は書き出しの種類の一覧から選ぶ"
    )
