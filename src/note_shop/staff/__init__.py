"""社員（＝各部署のエージェント）。

リサーチ → 企画 → 執筆 → 検品 → デザイン → 営業 の順に、
共有台帳（store.Store）を介して仕事を受け渡す。
"""

from .company import Company

__all__ = ["Company"]
