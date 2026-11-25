"""
Claude Sonnet 5.5 / Opus 5.5 串接測試（不呼叫 API、不需 API key、不花錢）。

用假的 API 物件攔截 messages.create 的參數，驗證：
  - 不送 temperature（Sonnet 5.5 / Opus 5.5 會回 400），改用 output_config.effort
  - 預設開啟 server-side fallback；可用設定關閉
  - 只取 text 區塊（略過 thinking）；記錄實際回答的模型
  - 拒答（stop_reason=refusal）→ refused=True；chat_service 改回固定轉介並進待處理清單
  - SDK 例外轉成 chat_service 能分類的 LLMError
  - 目前這則訊息不會在 history 與 user_message 重複送出

執行：cd backend && python -X utf8 test_llm_claude.py
"""
import testutil  # 必須最先
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import db
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_llm_claude.sqlite3"
db.DB_PATH.unlink(missing_ok=True)

import anthropic
import httpx2

import chat_service
import llm_client
import rag
from config import settings
from llm_client import ClaudeClient, LLMError, LLMResult

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1; print(f"  ✓ {name}")
    else:
        _fail += 1; print(f"  ✗ {name}  ← 失敗")


def fake_resp(text="好的，以下是建議。", stop_reason="end_turn", model="claude-sonnet-5-5"):
    return NS(
        stop_reason=stop_reason, stop_details=None, model=model, _request_id="req_test",
        content=[NS(type="thinking", thinking=""), NS(type="text", text=text)],
        usage=NS(input_tokens=1200, output_tokens=300),
    )


class FakeAPI:
    def __init__(self, resp=None, exc=None):
        self.calls, self.resp, self.exc = [], resp, exc

    def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return self.resp


def client_with(api_beta, api_ga=None):
    c = ClaudeClient()
    c.client = NS(beta=NS(messages=api_beta), messages=api_ga or FakeAPI())
    return c


settings.LLM_PROVIDER = "anthropic"
settings.PRIMARY_MODEL = "claude-sonnet-5-5"
HIST = [{"role": "patient", "content": "我嘴巴破了"}, {"role": "bot", "content": "請問嚴重度幾分？"}]

print("=== 請求參數 ===")
c = ClaudeClient()
req = c.build_request("系統提示", HIST, "要怎麼漱口？", "claude-sonnet-5-5")
check("不送 temperature / top_p / top_k", not ({"temperature", "top_p", "top_k"} & set(req)))
check("output_config.effort = low（預設）", req["output_config"] == {"effort": "low"})
check("不設定 thinking（兩個模型皆為 adaptive）", "thinking" not in req)
check("max_tokens 足夠容納思考（≥2000）", req["max_tokens"] >= 2000)
check("server-side fallback 預設開啟", req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"])
check("角色對應 patient→user、bot→assistant",
      [m["role"] for m in req["messages"]] == ["user", "assistant", "user"])
check("system prompt 走原生 system 欄位", req["system"] == "系統提示")
settings.ANTHROPIC_REFUSAL_FALLBACK = False
req2 = c.build_request("s", [], "q", "claude-opus-5-5")
check("關閉 fallback：不帶 betas／fallbacks", "fallbacks" not in req2 and "betas" not in req2)
check("Opus 5.5 模型 ID", req2["model"] == "claude-opus-5-5")
settings.ANTHROPIC_REFUSAL_FALLBACK = True

print("\n=== 回應解析 ===")
beta = FakeAPI(fake_resp())
r = client_with(beta).generate("s", HIST, "q", "claude-sonnet-5-5")
check("走 beta endpoint（fallback 需要）", len(beta.calls) == 1)
check("只取 text 區塊、略過 thinking", r.text == "好的，以下是建議。")
check("記錄 token 用量", r.prompt_tokens == 1200 and r.completion_tokens == 300)
r = client_with(FakeAPI(fake_resp(model="claude-sonnet-5"))).generate("s", [], "q", "claude-sonnet-5-5")
check("fallback 時記錄實際回答的模型", r.model_id == "claude-sonnet-5")
r = client_with(FakeAPI(fake_resp(text="", stop_reason="refusal"))).generate("s", [], "q", "claude-sonnet-5-5")
check("拒答 → refused=True、text 為空", r.refused is True and r.text == "")
settings.ANTHROPIC_REFUSAL_FALLBACK = False
ga = FakeAPI(fake_resp())
client_with(FakeAPI(), ga).generate("s", [], "q", "claude-sonnet-5-5")
check("關閉 fallback 時走一般 endpoint", len(ga.calls) == 1)
settings.ANTHROPIC_REFUSAL_FALLBACK = True

print("\n=== 真實 SDK 序列化（本機模擬 HTTP，不連網）===")
import json as _json
captured = {}


def _handler(request):
    captured["body"] = _json.loads(request.content)
    captured["headers"] = dict(request.headers)
    return httpx2.Response(200, json={
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                    {"type": "text", "text": "請用軟毛牙刷。"}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5}})


real = ClaudeClient()
real.client = anthropic.Anthropic(api_key="sk-ant-test-dummy",
                                  http_client=httpx2.Client(transport=httpx2.MockTransport(_handler)))
r = real.generate("系統提示", HIST, "要怎麼漱口？", "claude-sonnet-5-5")
b = captured["body"]
check("SDK 送出的 body 不含 temperature", "temperature" not in b)
check("SDK 送出 output_config.effort", b.get("output_config") == {"effort": "low"})
check("SDK 送出 fallbacks=default", b.get("fallbacks") == "default")
check("SDK 帶 fallback beta header", "server-side-fallback-2026-07-01" in captured["headers"].get("anthropic-beta", ""))
check("SDK 回應解析：只取 text", r.text == "請用軟毛牙刷。" and r.model_id == "claude-sonnet-5-5")

print("\n=== 例外轉換 ===")
req_obj = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
for exc, needle in [
    (anthropic.APITimeoutError(request=req_obj), "timeout"),
    (anthropic.RateLimitError("rate", response=httpx2.Response(429, request=req_obj), body=None), "429"),
    (anthropic.APIConnectionError(request=req_obj), "connection"),
]:
    try:
        client_with(FakeAPI(exc=exc)).generate("s", [], "q", "claude-sonnet-5-5")
        check(f"{type(exc).__name__} → LLMError", False)
    except LLMError as e:
        check(f"{type(exc).__name__} → LLMError（含「{needle}」）", needle in str(e).lower())

print("\n=== get_client 防呆 ===")
llm_client._client = None
settings.PRIMARY_MODEL = "gemma-3-12b-it"
try:
    llm_client.get_client(); check("anthropic + gemma 模型 → 明確錯誤", False)
except LLMError as e:
    check("anthropic + gemma 模型 → 明確錯誤", "claude-sonnet-5-5" in str(e))
settings.PRIMARY_MODEL = "claude-sonnet-5-5"

print("\n=== chat_service 整合（假 LLM）===")
db.init_db()
db.ensure_session("P900")
seen = {}


class FakeLLM:
    def __init__(self, result):
        self.result = result

    def generate(self, system_prompt, history, user_message, model_id):
        seen["history"], seen["user"] = history, user_message
        return self.result


_orig_query = rag.retriever.query
rag.retriever.query = lambda *a, **k: [{"source": "ONC-17 口腔黏膜炎", "content": "餐後以軟毛牙刷清潔口腔。", "score": 0.9}]

llm_client._client = FakeLLM(LLMResult("可以用軟毛牙刷。", "claude-sonnet-5", "anthropic", 100, 50))
r = asyncio.run(chat_service.process_message("P900", "刷牙要注意什麼", channel="line"))
check("正常回答", "軟毛牙刷" in r.response)
check("目前訊息不在 history 重複", not seen["history"] or seen["history"][-1]["content"] != seen["user"])
with db.get_conn() as conn:
    m = conn.execute("SELECT model_id, answer_quality FROM messages WHERE patient_code='P900' AND role='bot' "
                     "ORDER BY id DESC").fetchone()
check("messages 記錄實際回答的模型", m["model_id"] == "claude-sonnet-5")

llm_client._client = FakeLLM(LLMResult("", "claude-sonnet-5-5", "anthropic", 100, 0, refused=True))
r = asyncio.run(chat_service.process_message("P900", "口腔要怎麼保養", channel="line"))
check("拒答 → 固定轉介文字（不空白）", "沒辦法回答" in r.response)
with db.get_conn() as conn:
    m = conn.execute("SELECT answer_quality FROM messages WHERE patient_code='P900' AND role='bot' "
                     "ORDER BY id DESC").fetchone()
    a = conn.execute("SELECT level, trigger FROM alerts WHERE patient_code='P900' ORDER BY id DESC").fetchone()
check("標記 answer_quality = llm_refusal", m["answer_quality"] == "llm_refusal")
check("進護理端待處理清單（info）", a["level"] == "info" and "模型未回答" in a["trigger"])

rag.retriever.query = _orig_query
llm_client._client = None

print(f"\n通過 {_pass} / {_pass + _fail}")
if _fail:
    raise SystemExit(1)
