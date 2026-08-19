# 運用マニュアル

6部署をどう回すか。所要時間の目安は、慣れた状態で **1日15分 + 記事1本につき30分** です。

---

## 組織図とコマンドの対応

| 部署 | 仕事 | コマンド |
|---|---|---|
| リサーチ担当 | 毎朝 X / Threads から伸びているポストを集め、需要テーマを抽出 | `noteshop research` |
| 企画担当 | 売上データと突き合わせて次に出す note を提案 | `noteshop plan` |
| 執筆担当 | 売れた記事と過去のレターを型にして本文を書く | `noteshop write <slug>` |
| 検品担当 | 別コンテキストのAIがAIっぽい言い回しだけを直す | `noteshop qa <slug>` |
| デザイン担当 | 表紙と記事内の図をSVGで作る | `noteshop design <slug>` |
| 営業担当 | 集客ポストを作り予約枠に入れる／時間が来たら投稿 | `noteshop promote <slug>` / `noteshop sales run-due` |

まとめて動かす2つのコマンド:

```bash
noteshop morning            # リサーチ → 企画
noteshop produce <slug>     # 執筆 → 検品 → デザイン → 営業 → 商品化
```

---

## 日次

### 朝（自動・5分）

```bash
noteshop morning
noteshop topics --status planned
```

`morning` が出した企画から、その日に作る1本を選びます。**ここが人間の判断です。**
AI の提案をそのまま採用し続けると、企画がリサーチの平均値に寄っていきます。

### 制作（記事を出す日だけ・30分）

```bash
noteshop produce <slug> --url "https://note.com/<あなた>/n/<記事ID>"
```

出力は `out/<slug>/` に揃います:

```
out/<slug>/
├── note.md        # note に貼る本文（<!-- ここから有料エリア --> が有料ラインの目印）
├── meta.json      # 価格・タグ・検品結果・企画の根拠
├── promo.txt      # 予約済みの集客ポスト
└── assets/        # 表紙と記事内の図
```

**人間がやること（この順に3分）:**

1. `meta.json` の `qa.needs_human_review` を見る。true なら本文を読む
2. `note.md` の `<!-- ここから有料エリア -->` の位置が納得できるか見る
3. note に貼り、**価格を自分の目で設定**して公開する

### 予約投稿の消化（cron）

```bash
# 毎時0分に、時間が来た予約投稿を処理する
0 * * * * cd /path/to/project && /path/to/venv/bin/noteshop sales run-due >> logs/sales.log 2>&1
```

`staff.sales.autopost` が `false` の間は、投稿せずに内容を表示するだけです。
**まずは false のまま1〜2週間回して、生成される告知文が自分の言葉として許容できるか確認してください。**

朝のリサーチも cron に載せるなら:

```bash
0 7 * * * cd /path/to/project && /path/to/venv/bin/noteshop morning >> logs/morning.log 2>&1
```

---

## 週次

```bash
noteshop sales import path/to/note-sales.csv   # note からエクスポートした売上を取り込む
noteshop report --days 30 --out out/report.md
```

見るのは4つだけです（[business-plan.md](business-plan.md#4-kpiツリー) のKPI表）。

週次でやる判断:

- **購入率が落ちている** → 無料パートの切り方（`product.free_ratio`）か、テーマ選定を疑う
- **人間レビュー率が3割を超えた** → 執筆プロンプトか、参照している「売れた記事」が古い
- **同じテーマばかり提案される** → `staff.research.sources` を増やすか、`company.domains` を見直す

---

## リサーチのデータ源

X / Threads の公開検索は API プランと権限に強く依存します。この会社は
**取れない環境でも止まらない**ように作ってあります。

| ソース | 設定 | 必要なもの |
|---|---|---|
| `manual` | 既定で有効 | なし。`data/inbox/` に JSON か CSV を置くだけ |
| `x` | `staff.research.sources` に `x` を追加 | `X_BEARER_TOKEN`（Recent search が使える有料プラン） |
| `threads` | 同 `threads` を追加 | `THREADS_ACCESS_TOKEN`（`threads_keyword_search` 権限） |

API が使えない・審査が通らない場合は `manual` だけで運用できます。形式は
`examples/inbox/` を見てください。手で集めるのが面倒なら、既存のSNS分析ツールの
エクスポートを CSV としてそのまま置く運用が現実的です。

---

## 過去のレターを執筆担当に読ませる

`data/letters/` に `.md` か `.txt` を置くと、執筆担当が文体と構成の参考にします
（文章の流用はしません）。メール配信サービスから過去号をエクスポートして置いてください。

---

## トラブル時

| 症状 | 原因と対処 |
|---|---|
| `オフラインモードで動作中` と出る | `ANTHROPIC_API_KEY` が未設定。スタブ生成なので商品にはなりません |
| `直近のシグナルがありません` | `noteshop research collect` を先に実行するか、`data/inbox/` にファイルを置く |
| PNG に変換できない | `pip install cairosvg` か librsvg を入れる。無い場合 SVG のまま残るので手で変換 |
| 予約投稿が溜まる | `noteshop sales queue` で確認。cron が動いていないか、autopost が false |
| 検品でほぼ毎回レビュー要求 | `staff.qa.max_change_ratio` が厳しすぎるか、執筆の出力が不安定。まず本文を読む |
