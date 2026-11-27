"""
LLM 供應商抽象層。

目的：換模型只改環境變數，不動 main.py。
  開發／演示：mock（不需 API key）或 Gemma 3 12B（Google AI Studio）
  正式收案：Anthropic Claude Sonnet 5.5（claude-sonnet-5-5）或 Opus 5.5（claude-opus-5-5），
            兩者擇一，由設計論文的模擬病人評估決定。
  ⚠️ Google AI Studio 免費方案的條款允許用輸入資料改善產品，不可用於真實病患。

每次呼叫回傳 LLMResult，含 model_id / provider / tokens，供 messages 表記錄，
研究時可依 model_id 區分「哪些資料由哪個模型產生」。

選擇由 config.LLM_PROVIDER 決定（"google" | "anthropic" | "mock"）；mock 為離線演示模式，見 MockClient。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from config import settings
from prompt import UNANSWERABLE_MARK


@dataclass
class LLMResult:
    text: str
    model_id: str                       # 實際產生回答的模型（server-side fallback 時與請求的模型不同）
    provider: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    refused: bool = False               # 模型基於安全政策拒答（stop_reason == "refusal"）
    cache_read_tokens: int = 0          # CAG：從 prompt cache 讀取的 token（計費約 1/10）
    cache_write_tokens: int = 0         # CAG：寫入 prompt cache 的 token（5m 約 1.25 倍、1h 約 2 倍）
    refusal_category: str | None = None   # 拒答時 stop_details.category（cyber、bio… 或 None）


def system_text(system_prompt: "str | list[str]") -> str:
    """CAG 的 system 是 [固定前段, 變動後段]；不支援分段的供應商合併成一段。"""
    return system_prompt if isinstance(system_prompt, str) else "\n\n".join(system_prompt)


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
        system_prompt = system_text(system_prompt)
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


# ── Claude Sonnet 5.5 / Opus 5.5（正式收案）────────────────────────
class ClaudeClient:
    """Anthropic Messages API。

    與舊版（Sonnet 5、temperature=0.7）的差異 —— Sonnet 5.5 / Opus 5.5 的 API 規則：
      - 不可送 temperature / top_p / top_k（會回 400）；回答的深度改用 output_config.effort 控制。
        衛教問答不需要深度推理，預設 effort=low（LLM_EFFORT），回應較快。
      - 兩個模型都是 adaptive thinking（Opus 5.5 無法關閉），不另外設定 thinking。
        思考 token 也算在 max_tokens 內，所以 max_tokens 不能設太小（LLM_MAX_TOKENS）。
      - 可能因安全政策拒答（stop_reason == "refusal"）。研究收案預設 ANTHROPIC_REFUSAL_FALLBACK=false：
        不換模型，同模型重試一次，全部回覆出自同一個模型。設 true 時開啟 server-side fallback（fallbacks="default"），
        由 API 改用伺服器指定的備援模型重跑；實際回答的模型都記在 LLMResult.model_id（resp.model）。
        整條鏈仍拒答時回傳 refused=True，由 chat_service 改回固定轉介文字（不顯示空白）。
      - 資料所在地：ANTHROPIC_INFERENCE_GEO=us（預設）時每個請求帶 inference_geo="us"，只在美國境內運算。
        以 extra_body 送出，舊版 SDK 也能用。
    """
    provider = "anthropic"
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self):
        import anthropic
        self._anthropic = anthropic
        # 沒設 ANTHROPIC_API_KEY 時交給 SDK 依環境解析憑證（ANTHROPIC_AUTH_TOKEN、ant auth login 等）
        kwargs = {"api_key": settings.ANTHROPIC_API_KEY} if settings.ANTHROPIC_API_KEY else {}
        self.client = anthropic.Anthropic(timeout=settings.LLM_TIMEOUT_SEC, **kwargs)

    def build_request(self, system_prompt: str, history: list[dict], user_message: str,
                      model_id: str) -> dict:
        """組出 messages.create 的參數（獨立出來方便測試，不需呼叫 API）。"""
        messages = []
        for msg in history:
            role = "user" if msg["role"] in ("user", "patient") else "assistant"
            messages.append({"role": role, "content": msg["content"]})
        messages.append({"role": "user", "content": user_message})
        if isinstance(system_prompt, str):
            system = system_prompt
        else:
            # CAG：固定前段（安全規則＋本科全部單張＋回答規則）標記快取；同科別的下一則提問直接讀快取
            cache = {"type": "ephemeral"}
            if settings.CACHE_TTL == "1h":
                cache["ttl"] = "1h"
            system = [{"type": "text", "text": system_prompt[0], "cache_control": cache},
                      *({"type": "text", "text": t} for t in system_prompt[1:])]
        req = {
            "model": model_id,
            "max_tokens": settings.LLM_MAX_TOKENS,
            "system": system,
            "messages": messages,
            "output_config": {"effort": settings.LLM_EFFORT},
        }
        if settings.ANTHROPIC_INFERENCE_GEO:
            req["extra_body"] = {"inference_geo": settings.ANTHROPIC_INFERENCE_GEO}
        if settings.ANTHROPIC_REFUSAL_FALLBACK:
            req["betas"] = [self.FALLBACK_BETA]
            req["fallbacks"] = "default"
        return req

    def generate(self, system_prompt: str, history: list[dict], user_message: str,
                 model_id: str) -> LLMResult:
        req = self.build_request(system_prompt, history, user_message, model_id)
        resp = self._create(req)
        result = self.parse_response(resp, model_id)
        if not result.refused:
            return result
        # 拒答且 server-side fallback 沒有接手時重試一次（docs/knowledge/RAG與CAG比較.md 的 S1）：
        # - 備援模型忙碌：API 在 stop_details.recommended_model 建議改用的模型 → 照建議重試
        #   （IRB 要求單一模型，ANTHROPIC_REFUSAL_FALLBACK=false 時不換模型）
        # - 其他（例如 general_harms、bio 類安全分類器誤判，Sonnet 5.5 的 "default" 備援不處理這幾類）：
        #   同模型、同內容重試一次。實測這類拒答是偶發的（同一題重跑 11 次 0 次拒答）
        recommended = getattr(getattr(resp, "stop_details", None), "recommended_model", None)
        if recommended and recommended != result.model_id and settings.ANTHROPIC_REFUSAL_FALLBACK:
            logging.warning("[LLM] 拒答（%s）後改用建議模型 %s 重試", result.refusal_category, recommended)
            retry = self.build_request(system_prompt, history, user_message, recommended)
            retry.pop("fallbacks", None)
            retry.pop("betas", None)
            retry_model = recommended
        else:
            logging.warning("[LLM] 拒答（%s）後以同模型重試一次", result.refusal_category)
            retry, retry_model = req, model_id
        retried = self.parse_response(self._create(retry), retry_model)
        if retried.refused:
            retried.refusal_category = retried.refusal_category or result.refusal_category
        return retried

    def _create(self, req: dict):
        a = self._anthropic
        api = self.client.beta.messages if "betas" in req else self.client.messages
        try:
            return api.create(**req)
        except a.RateLimitError as e:
            raise LLMError(f"429 rate limited: {e}") from e
        except a.APITimeoutError as e:
            raise LLMError(f"timeout: {e}") from e
        except a.APIStatusError as e:
            raise LLMError(f"{e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError(f"connection error: {e}") from e

    def parse_response(self, resp, requested_model: str) -> LLMResult:
        refused = resp.stop_reason == "refusal"
        details = getattr(resp, "stop_details", None) if refused else None
        if refused:
            logging.warning("[LLM] 模型拒答 category=%s recommended_model=%s request_id=%s",
                            getattr(details, "category", None), getattr(details, "recommended_model", None),
                            getattr(resp, "_request_id", None))
        text = "" if refused else "".join(b.text for b in resp.content if b.type == "text")
        cache_read = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
        return LLMResult(
            text=text,
            model_id=getattr(resp, "model", None) or requested_model,
            provider=self.provider,
            # 輸入 token 總數（含快取讀寫），研究資料估算用量時才不會漏掉 CAG 的知識庫部分
            prompt_tokens=(resp.usage.input_tokens or 0) + cache_read + cache_write,
            completion_tokens=resp.usage.output_tokens,
            refused=refused,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            refusal_category=(getattr(details, "category", None) or "unknown") if refused else None,
        )


# ── 演示模式（不需 API key）──────────────────────────────────────
_RAG_BLOCK = re.compile(
    r"\[資料\d+｜來源：(?P<source>[^｜\]]+)｜相關度：(?P<score>[\d.]+)\]\n(?P<content>.*?)(?=\n\n\[資料|\n\n── |\Z)",
    re.S,
)
# 演示模式（沒有真的 LLM）用相關度模擬「資料無法回答」，避免離題硬答。相似度的尺度依嵌入模型而不同：
# e5 的分數集中在 0.8–0.9（開發集：0.86 保留 73/79 題範圍內問題、擋掉 7/15 題範圍外問題）。
# 正式環境不靠這個門檻 —— 範圍外問題由 Claude 依 prompt 規則輸出「無法回答」標記。
_MOCK_MIN_SCORE = {"paraphrase-multilingual-MiniLM-L12-v2": 0.45, "multilingual-e5-small": 0.86}


def _mock_min_score() -> float:
    from embedding import EMBED_MODEL
    return _MOCK_MIN_SCORE.get(EMBED_MODEL, 0.0)


class MockClient:
    """離線演示用：不呼叫任何雲端模型，直接把 RAG 檢索到的衛教段落整理成回覆。

    用途：
      - 沒有 API key 的環境（課堂演示、口試、CI）也能走完整條 /chat 流程
        （紅旗 → 評估 → RAG → 品質閘 → 落庫），只有「生成」這一步換成規則組裝。
      - 回覆內容 100% 來自檢索段落，方便展示「回答有憑有據」；
        每項標註單張代碼；相關度都太低時，比照 prompt 規則只輸出「無法回答」標記
        （品質閘標 deflected_unanswerable，chat_service 改成固定轉介文字）。
    啟用：.env 設 LLM_PROVIDER=mock。
    """
    provider = "mock"

    def generate(self, system_prompt: str, history: list[dict], user_message: str,
                 model_id: str) -> LLMResult:
        # 演示模式只支援 RAG 格式（依相關度挑段落）；CAG 的全單張沒有相關度，會一律輸出「無法回答」
        system_prompt = system_text(system_prompt)
        docs = [(m.group("source").strip(), m.group("content").strip())
                for m in _RAG_BLOCK.finditer(system_prompt)
                if float(m.group("score")) >= _mock_min_score()]
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
            code = source.split()[0]
            lines.append(f"根據衛教資料《{source}》：")
            lines += [f"• {p}。（{code}）" for p in points[:n_points]]
            lines.append("")
        if not lines:
            text = UNANSWERABLE_MARK            # 比照 prompt.grounding_rules()：資料無法回答時只輸出標記
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
            if not settings.PRIMARY_MODEL.startswith("claude-"):
                raise LLMError(f"LLM_PROVIDER=anthropic 但 PRIMARY_MODEL={settings.PRIMARY_MODEL!r}；"
                               "請在 .env 設 PRIMARY_MODEL=claude-sonnet-5-5 或 claude-opus-5-5")
            _client = ClaudeClient()
        elif settings.LLM_PROVIDER == "mock":
            _client = MockClient()
        else:
            _client = GemmaClient()
    return _client
