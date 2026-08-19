"""note へのブラウザ自動投稿（オプトイン機能）。

note には記事投稿用の公開 API がないため、どうしても自動化するならブラウザ操作になる。
ただしこれは note の利用規約に抵触する可能性があり、UI 変更で簡単に壊れる。
そのため:

- 既定では絶対に動かない（--i-accept-tos と NOTE_EMAIL/NOTE_PASSWORD が必要）
- 既定は「下書き保存まで」。公開は --publish を明示したときだけ
- 価格設定・有料ラインの確定は人間が行う前提（誤課金の事故を避けるため）

詳細と判断材料は docs/legal-and-risk.md を読んでください。
"""

from __future__ import annotations

import os
from pathlib import Path

NEW_POST_URL = "https://note.com/notes/new"
LOGIN_URL = "https://note.com/login"


class BrowserPublishError(RuntimeError):
    pass


def publish_draft(
    title: str,
    body: str,
    *,
    cover_path: str | None = None,
    accepted_tos: bool = False,
    publish: bool = False,
    headless: bool = True,
    timeout_ms: int = 30_000,
) -> str:
    """note のエディタに下書きを作る。返り値は到達した URL。

    accepted_tos=False の間は何もしない。
    """
    if not accepted_tos:
        raise BrowserPublishError(
            "ブラウザ自動投稿は既定で無効です。docs/legal-and-risk.md を読んだうえで "
            "--i-accept-tos を付けて実行してください。"
        )

    email = os.environ.get("NOTE_EMAIL", "").strip()
    password = os.environ.get("NOTE_PASSWORD", "").strip()
    if not (email and password):
        raise BrowserPublishError("NOTE_EMAIL と NOTE_PASSWORD を設定してください")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - 任意依存
        raise BrowserPublishError(
            "playwright が入っていません: pip install '.[browser]' && playwright install chromium"
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.set_default_timeout(timeout_ms)
        try:
            page.goto(LOGIN_URL)
            page.fill("input[name='login']", email)
            page.fill("input[name='password']", password)
            page.click("button[type='submit']")
            page.wait_for_url("https://note.com/**", timeout=timeout_ms)

            page.goto(NEW_POST_URL)
            page.fill("textarea[placeholder*='タイトル']", title)
            editor = page.locator("div[contenteditable='true']").first
            editor.click()
            editor.type(body, delay=0)

            if cover_path and Path(cover_path).exists():
                _attach_cover(page, cover_path)

            # note は自動保存されるが、確実に下書きへ落とすため明示的に待つ。
            page.wait_for_timeout(2000)
            url = page.url

            if publish:
                raise BrowserPublishError(
                    "自動公開は意図的に未実装です。価格と有料ラインの確定は "
                    "note の管理画面で人間が確認してください。"
                )
            return url
        finally:
            browser.close()


def _attach_cover(page, cover_path: str) -> None:
    """見出し画像を添付する。UI 変更で失敗しても記事本文は残す。"""
    try:
        page.set_input_files("input[type='file']", cover_path)
        page.wait_for_timeout(2000)
    except Exception as exc:  # pragma: no cover - UI依存
        print(f"[browser] 見出し画像の添付に失敗しました（手動で設定してください）: {exc}")
