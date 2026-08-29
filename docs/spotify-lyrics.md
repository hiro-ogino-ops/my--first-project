# spotify-lyrics — 再生中の曲の歌詞を出す

Spotify で今かかっている曲を1曲ずつ見張って、歌詞をネットから探し、
再生位置に合わせて現在行をハイライトしながら表示するターミナルアプリです。

```
▶ Dummy Song
  Dummy Artist — Dummy Album
  ────────────·············· 1:23 / 3:20

    （2行前の歌詞。暗く表示される）
    （1行前の歌詞）
    いま歌っている行がここに出る
    （次の行）
    （その次の行）

  q 終了   [ / ] 補正   r 再取得   出典 lrclib
```

---

## 準備

### 1. Spotify のアプリを作る

1. https://developer.spotify.com/dashboard で **Create app**
2. Redirect URI に `http://127.0.0.1:8888/callback` を登録
   （`localhost` は現在のダッシュボードでは弾かれます。**127.0.0.1** で登録してください）
3. API に **Web API** を選ぶ
4. 発行された **Client ID** を控える（Client Secret は使いません）

### 2. 設定して入れる

```bash
pip install -e .
cp .env.example .env     # SPOTIFY_CLIENT_ID を入れる
```

### 3. ログインする（初回だけ）

```bash
spotify-lyrics login
```

ブラウザが開いて認可画面が出ます。許可するとローカルに戻ってきて、
トークンが `~/.config/spotify-lyrics/token.json`（パーミッション 0600）に保存されます。
以後はアクセストークンが自動更新されるので、ログインし直す必要はありません。

---

## 使う

```bash
spotify-lyrics                    # 再生中の曲の歌詞を出し続ける（既定）
spotify-lyrics --once             # 1回だけ取って標準出力に流す（パイプ向き）
spotify-lyrics --offset -500      # 歌詞が500ms早すぎるときの補正
spotify-lyrics search "曲名" -a "アーティスト" --duration 210
spotify-lyrics status             # 設定とログイン状態の確認
spotify-lyrics cache clear        # 歌詞キャッシュを消す
spotify-lyrics logout
```

表示中のキー操作:

| キー | 動作 |
|---|---|
| `q` | 終了 |
| `[` / `]` | 歌詞のタイミングを 250ms ずつ手前／先へ |
| `0` | 補正をゼロに戻す |
| `r` | キャッシュを無視して歌詞を取り直す |

---

## 仕組み

```
Spotify Web API                 LRCLIB
/me/player/currently-playing    /api/get → 外れたら /api/search
        │                              │
        ▼                              ▼
   再生中の曲 ─────────────────▶ 歌詞（LRC or プレーン）
        │                              │
        │  progress_ms + 経過時間      │  時刻つきなら二分探索で現在行
        └──────────────┬───────────────┘
                       ▼
                ターミナル表示（0.2秒ごとに再描画）
```

### 設計上の判断

- **PKCE を使い、クライアントシークレットを置かない。**
  このアプリは各自のPCで動くパブリッククライアントなので、シークレットを配る形は取れません。
  Client ID だけで完結します。

- **Spotify は3秒に1回しか見に行かない。** 歌詞は1秒未満で切り替わりますが、
  それに合わせて API を叩くとレート制限に当たります。
  「最後に観測した位置＋そこからの経過時間」で現在位置を推定し、描画だけ細かく回しています。

- **歌詞は LRCLIB から。** APIキーもアカウントも要らず、同期歌詞（LRC）を持つ曲が多く、
  **曲の尺で照合できる**ので同名異曲を掴みにくい、という理由です。
  供給元を足したい場合は `LyricsProvider`（`fetch(track) -> Lyrics | None`）を満たすクラスを書いて
  `LyricsService` に渡してください。

- **完全一致が外れたら装飾を落として検索する。** Spotify 側の曲名は
  `Song (Remastered 2011)` `Song - Live` のようになっていることが多く、そのままでは当たりません。
  装飾を落として再検索し、候補は「尺の近さ・曲名とアーティストの一致・同期歌詞の有無」で採点します。
  点数が閾値に届かなければ**あえて何も返しません**（別の曲の歌詞を出すほうが害が大きいため）。

- **見つからなかったことも1日キャッシュする。** 歌詞が無い曲を再生するたびに
  外部APIを叩かないためです。ただし**通信エラーのときは記録しません**（オフラインを「歌詞なし」として焼き付けないため）。

- **ポッドキャストと広告は対象外。** `currently_playing_type` が `track` のときだけ歌詞を探します。

### ファイル

| ファイル | 役割 |
|---|---|
| `auth.py` | PKCE 認可、トークンの保存と更新 |
| `spotify.py` | 再生中の曲の取得（401なら1度だけ更新して再試行、429は待つ） |
| `lyrics/lrclib.py` | LRCLIB での検索と候補の採点 |
| `lyrics/lrc.py` | LRC 形式のパース |
| `lyrics/cache.py` | ディスクキャッシュ |
| `sync.py` | 再生位置 → 現在行 |
| `display.py` | ANSI での描画 |
| `app.py` | ポーリングと描画のループ |

---

## うまくいかないとき

| 症状 | 見るところ |
|---|---|
| `SPOTIFY_CLIENT_ID が未設定です` | `.env` か環境変数。`spotify-lyrics status` で確認できます |
| ログインでブラウザが戻ってこない | ダッシュボードの Redirect URI と `SPOTIFY_REDIRECT_URI` が**完全一致**しているか |
| `127.0.0.1:8888 で待ち受けられませんでした` | ポートが埋まっています。別ポートにして、ダッシュボード側も直してください |
| 曲は出るが歌詞が出ない | LRCLIB にその曲が無い可能性があります。`spotify-lyrics search` で直接探すと切り分けられます |
| 歌詞が数秒ずれる | `[` `]` で補正するか、`SPOTIFY_LYRICS_OFFSET_MS` に入れて固定 |
| 違う曲の歌詞が出る | `spotify-lyrics cache clear` のうえ、`search` に `--duration` を付けて確認 |
| `レート制限中` | Spotify の 429 です。自動で待つので放置で戻ります |

---

## 注意

- **歌詞の著作権は各権利者にあります。** このアプリは LRCLIB が配信しているものを
  取得して手元の画面に出すだけで、歌詞そのものはリポジトリに含みません。
  再配布や商用利用をする場合は、各供給元と権利者の条件を確認してください。
- **LRCLIB は有志が運営する無料サービスです。** 短い間隔で叩き続けないよう、
  取得結果はキャッシュし、User-Agent を明示しています。この配慮は外さないでください。
- **トークンは平文の JSON で保存されます**（0600）。共有マシンでは `spotify-lyrics logout` を。
- Spotify の Web API は**再生中の情報を読むだけ**の権限しか要求していません
  （`user-read-currently-playing`, `user-read-playback-state`）。再生の操作はしません。
