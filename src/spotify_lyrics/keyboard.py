"""ノンブロッキングなキー入力。

歌詞のずれを手で直せると実用度が変わるので、Enter 不要の1キー入力を用意する。
POSIX の termios が使えない環境（Windows など）では黙って無効になる。
"""

from __future__ import annotations

import sys

try:  # pragma: no cover - 環境依存
    import select
    import termios
    import tty

    _AVAILABLE = True
except ImportError:  # pragma: no cover - Windows
    _AVAILABLE = False


class KeyReader:
    """with で囲んだ間だけ端末を raw モードにする。"""

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stdin
        self.enabled = _AVAILABLE and self.stream.isatty()
        self._saved = None

    def __enter__(self) -> "KeyReader":
        if self.enabled:
            try:
                self._saved = termios.tcgetattr(self.stream)
                tty.setcbreak(self.stream.fileno())
            except (termios.error, ValueError):  # pragma: no cover - 端末が取れない
                self.enabled = False
        return self

    def __exit__(self, *exc) -> None:
        if self._saved is not None:
            termios.tcsetattr(self.stream, termios.TCSADRAIN, self._saved)

    def poll(self, timeout: float) -> str | None:
        """timeout 秒だけ待って、押されていれば1文字返す。押されなければ None。"""
        if not self.enabled:
            _sleep(timeout)
            return None
        ready, _, _ = select.select([self.stream], [], [], timeout)
        if not ready:
            return None
        return self.stream.read(1)


def _sleep(seconds: float) -> None:
    import time

    time.sleep(max(0.0, seconds))
