"""Claude へのアクセスを 2 メソッドに閉じ込める薄いラッパ。

パイプラインの各工程は LLMClient プロトコルにだけ依存するので、
オフラインのスタブ実装に差し替えればネットワークなしで全工程が回る。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol, TypeVar

from pydantic import BaseModel

from .config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    """パイプラインが必要とする生成能力。"""

    def generate_text(
        self, system: str, prompt: str, *, long: bool = False, task: str = "generic"
    ) -> str:
        """自由記述のテキストを生成する。

        long=True で長文用の上限に切り替える。task は工程名で、オフライン実装が
        どのスタブを返すか決めるために使う（本番実装では無視される）。
        """

    def generate_structured(self, system: str, prompt: str, schema: type[T]) -> T:
        """Pydantic スキーマに沿った構造化データを生成する。"""


class ClaudeClient:
    """Anthropic SDK 経由の本番実装。"""

    def __init__(self, settings: Settings) -> None:
        import anthropic  # 遅延 import: オフライン運用では読み込まない

        self._client = anthropic.Anthropic()
        self._llm = settings.llm

    def generate_text(
        self, system: str, prompt: str, *, long: bool = False, task: str = "generic"
    ) -> str:
        del task  # 本番実装では工程名を使わない
        max_tokens = self._llm.max_tokens_long if long else self._llm.max_tokens_short
        # 長文でも HTTP タイムアウトに当たらないよう、常にストリーミングで受ける。
        with self._client.messages.stream(
            model=self._llm.model,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": self._llm.effort},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()

        if message.stop_reason == "refusal":
            raise RuntimeError(
                "生成が安全性の判断で停止しました。プロンプトまたは企画内容を見直してください。"
            )
        return "".join(block.text for block in message.content if block.type == "text").strip()

    def generate_structured(self, system: str, prompt: str, schema: type[T]) -> T:
        # parse() は output_format をスキーマに変換して output_config.format にマージするので、
        # effort をこちらから渡しても上書きされない。
        response = self._client.messages.parse(
            model=self._llm.model,
            max_tokens=self._llm.max_tokens_short,
            system=system,
            output_config={"effort": self._llm.effort},
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError(f"{schema.__name__} の構造化出力を取得できませんでした")
        return parsed


def build_client(settings: Settings, model: str | None = None) -> LLMClient:
    """設定と環境変数から適切なクライアントを選ぶ。

    model を渡すと、その工程だけ別モデルで動かせる（検品担当が執筆と別のAIを
    使うためのフック）。
    """
    if model and model != settings.llm.model:
        settings = replace(settings, llm=replace(settings.llm, model=model))
    if settings.offline:
        from .stub import OfflineClient

        return OfflineClient(settings)
    return ClaudeClient(settings)
