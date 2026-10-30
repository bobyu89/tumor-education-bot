"""
LLM 供應商抽象層。

目的：換模型只改一個檔 + 一個環境變數，不動 main.py。
  現在：Gemma 3 12B（Google AI Studio，免費）
  之後：Claude Sonnet 5（Anthropic，付費，預設不拿資料訓練）
        —— 換 Sonnet 5 同時解掉免費 Google 方案會用病患對話訓練的 IRB 問題。

每次呼叫回傳 LLMResult，含 model_id / provider / tokens，供 messages 表記錄，
研究時可依 model_id 區分「哪些資料由哪個模型產生」。

選擇由 config.LLM_PROVIDER 決定（"google" | "anthropic" | "mock"）；mock 為離線演示模式，見 MockClient。
"""
from __future__ import annotations

import re
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


# ── 演示模式（不需 API key）──────────────────────────────────────
_RAG_BLOCK = re.compile(
    r"\[資料\d+｜來源：(?P<source>[^｜\]]+)｜相關度：(?P<score>[\d.]+)\]\n(?P<content>.*?)(?=\n\n\[資料|\n\n── |\Z)",
    re.S,
)
MOCK_MIN_SCORE = 0.45   # rag.py 只濾掉 distance ≥ 0.8（相關度 < 0.2），演示時再加一道門檻避免離題硬答


class MockClient:
    """離線演示用：不呼叫任何雲端模型，直接把 RAG 檢索到的衛教段落整理成回覆。

    用途：
      - 沒有 API key 的環境（課堂演示、口試、CI）也能走完整條 /chat 流程
        （紅旗 → 評估 → RAG → 品質閘 → 落庫），只有「生成」這一步換成規則組裝。
      - 回覆內容 100% 來自檢索段落，方便展示「回答有憑有據」；
        相關度都太低時，比照真實 LLM 的誠實邊界回「需請護理師」（品質閘標 deflected_off_source）。
    啟用：.env 設 LLM_PROVIDER=mock。
    """
    provider = "mock"

    def generate(self, system_prompt: str, history: list[dict], user_message: str,
                 model_id: str) -> LLMResult:
        docs = [(m.group("source").strip(), m.group("content").strip())
                for m in _RAG_BLOCK.finditer(system_prompt)
                if float(m.group("score")) >= MOCK_MIN_SCORE]
        simple = "說話像跟長輩聊天" in system_prompt      # prompt.py 的 simple 語氣模板
        n_docs, n_points = (1, 2) if simple else (2, 3)

        # 同一單張的多個 chunk 合併；去掉 chunk 開頭的【單張｜章節】標頭與「（一）飲食方面：」這類純標題
        by_source: dict[str, list[str]] = {}
        for source, content in docs:
            body = "\n".join(l for l in content.splitlines() if not re.match(r"^\s*【.*】\s*$", l))
            for s in re.split(r"[。；\n]+", body):
                s = s.strip(" -•*　")
                s = re.sub(r"^[（(][一二三四五六七八九十\d]+[）)]", "", s).strip()
                if len(s) >= 8 and not s.endswith(("：", ":")):
                    by_source.setdefault(source, []).append(s)

        lines: list[str] = []
        for source, points in list(by_source.items())[:n_docs]:
            lines.append(f"根據衛教資料《{source}》：")
            lines += [f"• {p}。" for p in points[:n_points]]
            lines.append("")
        if not lines:
            text = "這個問題我需要請護理師來幫您解答，我先幫您記錄下來。"
        else:
            lines.append("以上內容僅供衛教參考，若症狀持續或加重，請告知您的醫療團隊。")
            lines.append("（演示模式：本回覆由本地規則從衛教資料組裝，未呼叫雲端 LLM）")
            text = "\n".join(lines)
        return LLMResult(text=text, model_id="mock-rag-composer", provider=self.provider,
                         prompt_tokens=len(system_prompt), completion_tokens=len(text))


# ── 工廠 ──────────────────────────────────────────────────────────
_client = None


def get_client():
    """依 config.LLM_PROVIDER 回傳單例 client（google | anthropic | mock）。"""
    global _client
    if _client is None:
        if settings.LLM_PROVIDER == "anthropic":
            _client = ClaudeClient()
        elif settings.LLM_PROVIDER == "mock":
            _client = MockClient()
        else:
            _client = GemmaClient()
    return _client
