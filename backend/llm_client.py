"""
LLM 供應商抽象層。

目的：換模型只改一個檔 + 一個環境變數，不動 main.py。
  現在：Gemma 3 12B（Google AI Studio，免費）
  之後：Claude Sonnet 5（Anthropic，付費，預設不拿資料訓練）
        —— 換 Sonnet 5 同時解掉免費 Google 方案會用病患對話訓練的 IRB 問題。

每次呼叫回傳 LLMResult，含 model_id / provider / tokens，供 messages 表記錄，
研究時可依 model_id 區分「哪些資料由哪個模型產生」。

選擇由 config.LLM_PROVIDER 決定（"google" | "anthropic"）。
"""
from __future__ import annotations

from dataclasses import dataclass

from config import settings


@dataclass
class LLMResult:
    text: str
    model_id: str
    provider: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMError(Exception):
    """供 main.py 分類降級（429 / timeout / 其他）。"""


# ── Gemma（現用）──────────────────────────────────────────────────
class GemmaClient:
    provider = "google"

    def __init__(self):
        from google import genai
        self._genai = genai
        from google.genai import types
        self._types = types
        self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)

    def generate(self, system_prompt: str, history: list[dict], user_message: str,
                 model_id: str) -> LLMResult:
        types = self._types
        # Gemma 不支援 system_instruction，改把精簡指令注入成 user/model 對話開頭
        contents = [
            types.Content(role="user", parts=[types.Part.from_text(text=system_prompt)]),
            types.Content(role="model", parts=[types.Part.from_text(text="好的，我已了解。請問有什麼需要幫助的？")]),
        ]
        for msg in history:
            role = "user" if msg["role"] in ("user", "patient") else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_message)]))

        cfg = types.GenerateContentConfig(max_output_tokens=800, temperature=0.7)
        try:
            resp = self.client.models.generate_content(model=model_id, contents=contents, config=cfg)
        except Exception as e:
            raise LLMError(str(e)) from e

        usage = getattr(resp, "usage_metadata", None)
        return LLMResult(
            text=resp.text or "",
            model_id=model_id,
            provider=self.provider,
            prompt_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            completion_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
        )


# ── Claude Sonnet 5（之後啟用）────────────────────────────────────
class ClaudeClient:
    provider = "anthropic"

    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def generate(self, system_prompt: str, history: list[dict], user_message: str,
                 model_id: str) -> LLMResult:
        # Claude 支援原生 system prompt（不像 Gemma 要塞進對話）
        messages = []
        for msg in history:
            role = "user" if msg["role"] in ("user", "patient") else "assistant"
            messages.append({"role": role, "content": msg["content"]})
        messages.append({"role": "user", "content": user_message})
        try:
            resp = self.client.messages.create(
                model=model_id, system=system_prompt, messages=messages,
                max_tokens=800, temperature=0.7,
            )
        except Exception as e:
            raise LLMError(str(e)) from e
        return LLMResult(
            text="".join(b.text for b in resp.content if b.type == "text"),
            model_id=model_id,
            provider=self.provider,
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens,
        )


# ── 工廠 ──────────────────────────────────────────────────────────
_client = None


def get_client():
    """依 config.LLM_PROVIDER 回傳單例 client。"""
    global _client
    if _client is None:
        if settings.LLM_PROVIDER == "anthropic":
            _client = ClaudeClient()
        else:
            _client = GemmaClient()
    return _client
