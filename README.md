# note自動販売会社

note の有料記事を「企画 → 執筆 → 検品 → デザイン → 告知 → 集計」まで半自動で回すための、
小さな会社まるごとのリポジトリです。6つの部署がそれぞれ独立したモジュールになっていて、
共有の台帳（SQLite）を介して仕事を受け渡します。

```
リサーチ担当 ──▶ 企画担当 ──▶ 執筆担当 ──▶ 検品担当 ──▶ デザイン担当 ──▶ 営業担当
   X/Threads      売上と照合     売れた記事と    AIっぽさを      表紙と図版      集客ポストを
   から収集        して提案      レターを参照    人の言葉に                     予約枠へ
        │              │              │              │              │              │
        └──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘
                            共有台帳 (data/noteshop.db)
```

| 部署 | 実装 | 何をするか |
|---|---|---|
| リサーチ担当 | `staff/research.py` | 毎朝 X / Threads から伸びているポストを集め、需要テーマを抽出する |
| 企画担当 | `staff/planning.py` | 売れたデータと照らして「次に出す note」を根拠つきで提案する |
| 執筆担当 | `staff/writing.py` | 売れた記事と過去のレターを型にして本文を書く |
| 検品担当 | `staff/qa.py` | **別コンテキストのAI**が、AIっぽい言い回しだけを人の文章に直す |
| デザイン担当 | `staff/design.py` | 表紙と記事内の図をSVGで作り、PNGに変換する |
| 営業担当 | `staff/sales.py` | 集客ポストを作り、公開からの経過時間に合わせて予約投稿する |

---

## 使い方

### 1. 用意する

```bash
pip install -e ".[dev]"
cp .env.example .env          # ANTHROPIC_API_KEY を入れる
```

`ANTHROPIC_API_KEY` が無くても**オフラインモードで全工程が最後まで動きます**（スタブ生成）。
動線の確認はこれで足ります。

### 2. 会社を定義する

`config/company.yaml` を書き換えます。最低限ここだけ:

```yaml
company:
  name: "あなたの屋号"
  mission: "誰の何を、どう解決して売るのか"
  audience: "読者は誰か（1セグメントに絞る）"
  domains: ["扱う領域は1〜3個まで"]
```

**領域を増やさないでください。** 本数ではなく購入率で戦う設計です
（→ [docs/business-plan.md](docs/business-plan.md)）。

### 3. 動かす

```bash
# サンプルのSNSポストとレターを置く
mkdir -p data/inbox data/letters
cp examples/inbox/* data/inbox/
cp examples/letters/* data/letters/

# 毎朝: リサーチ → 企画
noteshop morning
noteshop topics --status planned

# 記事を1本作り切る: 執筆 → 検品 → デザイン → 営業 → 商品化
noteshop produce <slug>
```

`out/<slug>/` に一式が出ます。

```
out/<slug>/
├── note.md        # note に貼る本文（<!-- ここから有料エリア --> が有料ラインの目印）
├── meta.json      # 価格・タグ・検品結果・企画の根拠
├── promo.txt      # 予約済みの集客ポスト
└── assets/        # 表紙と記事内の図
```

### 4. 売上を戻す

```bash
noteshop sales import path/to/note-sales.csv
noteshop report --days 30
```

取り込んだ売上は、次の企画と執筆の入力になります。**このループが閉じて初めて会社になります。**

---

## コマンド一覧

```
noteshop morning              リサーチ → 企画
noteshop produce <slug>       執筆 → 検品 → デザイン → 営業 → 商品化
noteshop research [collect|brief|all]
noteshop plan
noteshop write | qa | design | export | promote  <slug>
noteshop sales [run-due|import|queue]
noteshop topics [--status ...]
noteshop report [--days N] [--out PATH]
noteshop publish <slug> --i-accept-tos    # note へブラウザで下書き投稿（既定で無効）
```

---

## 設計上の判断

いくつか、意図的にそうしている点があります。

- **価格はAIが決めない。** 値付けは会社の方針であって生成結果ではないので、
  `pricing.py` が設定の重み付けから決定論的に計算します（同じ企画なら常に同じ値段）。
- **検品は執筆と別のクライアント。** 同じ文脈で自分の文章を直させても、AIっぽさは抜けません。
  検品担当には企画意図を渡さず、本文だけを見せています。モデルも別にできます（`staff.qa.model`）。
- **検品が書き換えすぎたら人間に回す。** 変更率と文字数の減少を機械的に測り、
  内容にまで手が入った疑いがあれば `needs_human_review` を立てます。
- **画像は画像生成モデルを使わずSVG。** 文字が崩れず、ブランド色を強制でき、差分が読めるからです。
- **外に出るものは既定でオフ。** SNS 投稿も note 投稿も、明示的に有効化するまで
  ファイルに書き出すだけです。

---

## 注意

- **note に投稿用の公開APIはありません。** ブラウザ自動投稿は規約リスクがあり、既定で無効です。
  推奨は `out/<slug>/` を人間が貼ることです（1本3分）。
- **価格と有料ラインの最終確認は人間の仕事です。** 誤課金・本文流出は取り返しがつきません。
- **手数料率は前提値です。** 最初の入金で実測値に置き換えてください。

詳細 → [docs/legal-and-risk.md](docs/legal-and-risk.md)

---

## おまけ: spotify-lyrics

このリポジトリには、note 自動販売会社とは独立したもう1つのアプリが入っています。
**Spotify で再生中の曲の歌詞をネットから探して、再生位置に合わせて表示する**ターミナルアプリです。

```bash
pip install -e .
# .env に SPOTIFY_CLIENT_ID を入れる（Client Secret は不要）
spotify-lyrics login          # 初回だけ
spotify-lyrics                # 再生中の曲の歌詞が流れ始める
```

| コマンド | 何をするか |
|---|---|
| `spotify-lyrics` | 再生中の曲の歌詞を、現在行をハイライトしながら表示し続ける |
| `spotify-lyrics --once` | 1回だけ取得して標準出力に流す |
| `spotify-lyrics search "曲名" -a "アーティスト"` | 曲を指定して歌詞を探す |
| `spotify-lyrics status` | 設定とログイン状態を確認する |

- 認可は **PKCE**。クライアントシークレットを手元に置きません。
- 歌詞は **LRCLIB**（APIキー不要、同期歌詞あり）から。完全一致が外れたら装飾を落として検索し、
  尺と曲名で採点して選びます。確信が持てなければ**何も出しません**。
- Spotify は3秒に1回だけ見に行き、その間の再生位置は経過時間から推定します。
- 依存は標準ライブラリだけです。

詳細と設計上の判断 → [docs/spotify-lyrics.md](docs/spotify-lyrics.md)

---

## ドキュメント

| 文書 | 内容 |
|---|---|
| [docs/business-plan.md](docs/business-plan.md) | 収益モデル・単位経済・KPI・成長段階・やらないこと |
| [docs/operations.md](docs/operations.md) | 日次/週次の運用手順、cron設定、トラブル対応 |
| [docs/legal-and-risk.md](docs/legal-and-risk.md) | 各サービスの規約、法務上の注意、認証情報の扱い |
| [docs/spotify-lyrics.md](docs/spotify-lyrics.md) | おまけアプリ `spotify-lyrics` の使い方と設計 |

## 開発

```bash
pytest        # 124件。オフラインモードで6部署の通しテストと spotify-lyrics まで含む
```
