"""
CAG 模式與實測後修正（P1、P3、Q1、Q2）測試，不需 API key。

  - kb.full_docs：本科全部單張、排除參考資料與評值、依代碼穩定排序、尊重後台覆蓋層（改版、停用）
  - build_system_blocks：固定前段（安全規則＋全部單張＋回答規則）與變動後段（語氣、病患資訊）
  - ClaudeClient：兩段 system 時第一段加 cache_control；用量含快取讀寫
  - 提示詞：P1（可執行建議限資料內）、P3（不得改寫修飾詞）
  - 分類器：Q1（已引用單張時「無法回答」字樣不算轉介）、Q2（緊急指引標為 safety_escalation）
  - chat_service：KB_MODE=cag 時送出兩段 system，來源與版本只記實際引用的單張

執行：cd backend && python -X utf8 test_cag.py
"""
import testutil  # noqa: F401  必須最先
import asyncio
import tempfile
import types
from pathlib import Path

import db
import kb
import leaflets
import prompt
import quality
from config import settings
from llm_client import ClaudeClient, LLMResult

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1; print(f"  ✓ {name}")
    else:
        _fail += 1; print(f"  ✗ {name}  ← 失敗")


TMP = Path(tempfile.mkdtemp(prefix="cag_"))
db.DB_PATH = TMP / "t.sqlite3"
DEPT = "hemato-oncology"

print("=== kb.full_docs ===")
kb.invalidate()
docs = kb.full_docs(DEPT)
codes = list(dict.fromkeys(d["code"] for d in docs))
check("包含全部 13 份單張", len(codes) == 13)
check("依代碼排序（快取前綴固定）", codes == sorted(codes))
check("排除「參考資料」「護理指導評值」", not any(d["section"] in ("參考資料", "護理指導評值") for d in docs))
check("欄位與 rag.query 相同（code、source、section、doc_version）",
      all({"code", "source", "section", "doc_version", "content"} <= d.keys() for d in docs))
check("ONC-22 腹瀉單張的「低纖維、蛋白」原文在內", any("低纖維、蛋白的食物" in d["content"] for d in docs if d["code"] == "ONC-22"))
check("code 參數只取一份單張", {d["code"] for d in kb.full_docs(DEPT, "ONC-17")} == {"ONC-17"})
total = kb.total_chars(DEPT)
print(f"  （本科單張全文 {total} 字）")

od = leaflets.store_dir() / DEPT
od.mkdir(parents=True, exist_ok=True)
(od / "ONC-18.md").write_text("---\ncode: ONC-18\ntitle: x\ncategory: c\ndoc_version: v9\nrag_index: false\n---\n停用", encoding="utf-8")
(od / "ONC-99.md").write_text("---\ncode: ONC-99\ntitle: 測試\ntopic: 測試\ncategory: c\ndoc_version: v1\nrag_index: true\n---\n## 概述\n新單張內容。", encoding="utf-8")
kb.invalidate()
over = kb.full_docs(DEPT)
check("後台停用的單張不放進 CAG", not any(d["code"] == "ONC-18" for d in over))
check("後台上架的新單張放進 CAG", any(d["code"] == "ONC-99" for d in over))
for p in od.glob("*.md"):
    p.unlink()
kb.invalidate()

print("\n=== 模式選擇 ===")
orig_mode, orig_max, orig_provider = settings.KB_MODE, settings.CAG_MAX_CHARS, settings.LLM_PROVIDER
settings.LLM_PROVIDER = "anthropic"
settings.KB_MODE = "rag"
check("KB_MODE=rag", kb.mode_for(DEPT) == "rag")
settings.KB_MODE = "cag"
check("KB_MODE=cag", kb.mode_for(DEPT) == "cag")
settings.KB_MODE = "auto"
check("auto：單張量小用 cag", kb.mode_for(DEPT) == "cag")
settings.CAG_MAX_CHARS = total - 1
check("auto：超過 CAG_MAX_CHARS 退回 rag", kb.mode_for(DEPT) == "rag")
settings.CAG_MAX_CHARS = orig_max
settings.LLM_PROVIDER = "google"
check("auto：非 Anthropic（無 prompt caching）用 rag", kb.mode_for(DEPT) == "rag")
settings.LLM_PROVIDER = "mock"
settings.KB_MODE = "cag"
check("演示模式（mock）即使設 cag 也用 rag", kb.mode_for(DEPT) == "rag")
check("預設 KB_MODE 是 auto", type(settings)().KB_MODE == "auto")
settings.KB_MODE, settings.LLM_PROVIDER = orig_mode, orig_provider

print("\n=== CAG 的 system prompt ===")
profile = types.SimpleNamespace(age=70, education_level="general", diagnosis=["測試診斷XYZ"], medications=[])
blocks = prompt.build_system_blocks(profile, docs, channel="line", emergency_contact="請撥 119")
check("分成兩段", isinstance(blocks, list) and len(blocks) == 2)
check("前段以安全規則開頭", blocks[0].startswith("重要安全規則"))
check("前段含全部單張", all(f"【單張 {c}" in blocks[0] for c in codes))
check("前段含回答規則與 CAG 專用規則（只引用用到的單張）", "回答規則" in blocks[0] and "本科全部的衛教單張" in blocks[0])
check("前段不含病患資訊（才能跨病人快取）", "測試診斷XYZ" not in blocks[0] and "── 病患資訊 ──" not in blocks[0])
check("後段含語氣與病患資訊（70 歲自動用淺白語氣）", "測試診斷XYZ" in blocks[1] and "說話像跟長輩聊天" in blocks[1])
other = prompt.build_system_blocks(None, docs, channel="line", emergency_contact="請撥 119")
check("不同病人前段完全相同（快取命中）", other[0] == blocks[0])

print("\n=== 提示詞修正（P1、P3）===")
rules = prompt.grounding_rules("ONC-22")
check("P3：不得改寫修飾詞（低／高）", "不要改寫、增減資料中的數值" in rules and "低／高" in rules)
check("P1：可執行建議必須取自資料", "必須取自上方衛教資料" in prompt.TONE_TEMPLATES["general"])
check("P1：鼓勵的話不得含醫療內容", "不得包含任何醫療或衛教內容" in prompt.TONE_TEMPLATES["simple"])
check("RAG 的 prompt 也套用 P3", "不要改寫、增減資料中的數值" in prompt.build_prompt(None, docs[:3]))

print("\n=== Claude 請求（快取）===")
c = ClaudeClient.__new__(ClaudeClient)
req = c.build_request(blocks, [], "腹瀉要吃什麼", "claude-sonnet-5-5")
orig_ttl = settings.CACHE_TTL
check("第一段加 cache_control（預設保存 1 小時）",
      req["system"][0].get("cache_control") == {"type": "ephemeral", "ttl": "1h"})
settings.CACHE_TTL = "5m"
check("CACHE_TTL=5m 時用預設 5 分鐘",
      c.build_request(blocks, [], "q", "m")["system"][0]["cache_control"] == {"type": "ephemeral"})
settings.CACHE_TTL = orig_ttl
check("第二段不快取", "cache_control" not in req["system"][1])
check("RAG 仍送字串 system", isinstance(c.build_request("abc", [], "q", "m")["system"], str))
usage = types.SimpleNamespace(input_tokens=500, output_tokens=300, cache_read_input_tokens=20000,
                              cache_creation_input_tokens=0)
resp = types.SimpleNamespace(stop_reason="end_turn", model="claude-sonnet-5-5", usage=usage,
                             content=[types.SimpleNamespace(type="text", text="回答（ONC-22）")])
r = c.parse_response(resp, "claude-sonnet-5-5")
check("用量含快取讀取（輸入 token 總數）", r.prompt_tokens == 20500 and r.cache_read_tokens == 20000)

print("\n=== 拒答處理（S1）===")
calls = []


def fake_create(req):
    calls.append(req)
    if len(calls) == 1:
        return types.SimpleNamespace(stop_reason="refusal", model=req["model"], usage=usage, content=[],
                                     stop_details=types.SimpleNamespace(category=None, recommended_model="claude-opus-5-5"))
    return types.SimpleNamespace(stop_reason="end_turn", model=req["model"], usage=usage,
                                 content=[types.SimpleNamespace(type="text", text="回答（ONC-38）")])


c._create = fake_create
r = c.generate(blocks, [], "手腳對冷熱感覺變遲鈍", "claude-sonnet-5-5")
check("拒答且有建議模型 → 改用建議模型重試一次", len(calls) == 2 and calls[1]["model"] == "claude-opus-5-5")
check("重試不再帶 server-side fallback 參數", "fallbacks" not in calls[1] and "betas" not in calls[1])
check("重試成功後回傳答案與實際模型", not r.refused and r.model_id == "claude-opus-5-5")
calls.clear()
c._create = lambda req: (calls.append(req), types.SimpleNamespace(
    stop_reason="refusal", model=req["model"], usage=usage, content=[],
    stop_details=types.SimpleNamespace(category="bio", recommended_model=None)))[1]
r = c.generate(blocks, [], "q", "claude-sonnet-5-5")
check("沒有建議模型時同模型重試一次（S1：分類器偶發誤判）",
      len(calls) == 2 and calls[1]["model"] == "claude-sonnet-5-5" and calls[1] == calls[0])
check("重試仍拒答 → 回報拒答並記錄類別（不無限重試）", r.refused and r.refusal_category == "bio")
orig_fb = settings.ANTHROPIC_REFUSAL_FALLBACK
settings.ANTHROPIC_REFUSAL_FALLBACK = False
calls.clear()
c._create = fake_create
r = c.generate(blocks, [], "q", "claude-sonnet-5-5")
check("IRB 單一模型模式：不換建議模型，只用同模型重試",
      len(calls) == 2 and calls[1]["model"] == "claude-sonnet-5-5" and not r.refused)
settings.ANTHROPIC_REFUSAL_FALLBACK = orig_fb

print("\n=== 分類器修正（Q1、Q2）===")
d22 = [x for x in docs if x["code"] == "ONC-25"][:2]
known = frozenset(codes)
check("Q1：已引用單張，句中有「無法回答」仍算 grounded",
      quality.classify("生魚片要避免（ONC-25）。其他部分我無法回答。", d22, known) == "grounded")
check("Q1：沒有引用時「請詢問醫」仍算轉介",
      quality.classify("這個問題請詢問醫師。", d22, known) == "deflected_off_source")
check("Q2：緊急指引（119）不算 uncited", quality.classify("請立即撥打 119。", d22, known) == "safety_escalation")
check("Q2：一般未引用仍是 uncited", quality.classify("多喝水。", d22, known) == "uncited")
check("無法回答標記照舊", quality.classify("[無法回答]", d22, known) == "deflected_unanswerable")

print("\n=== chat_service（KB_MODE=cag）===")
import auth
import chat_service

db.init_db()
auth.create_user("P801", "x-pass-801", "patient", pseudonym="P801", department=DEPT)
captured = {}


class FakeClient:
    def generate(self, system_prompt, history, user_message, model_id):
        captured["system"] = system_prompt
        return LLMResult(text="改善後可吃低纖維、蛋白的食物，如稀飯、去皮雞肉（ONC-22）。", model_id=model_id,
                         provider="anthropic", prompt_tokens=20000, completion_tokens=50,
                         cache_read_tokens=19500, cache_write_tokens=0)


orig_client = chat_service.get_client
chat_service.get_client = lambda: FakeClient()
settings.KB_MODE = "cag"
settings.LLM_PROVIDER = "anthropic"
try:
    resp = asyncio.run(chat_service.process_message("P801", "白血球低的時候飲食要注意什麼", channel="web"))
finally:
    settings.KB_MODE, settings.LLM_PROVIDER = orig_mode, orig_provider
    chat_service.get_client = orig_client
check("送出兩段 system", isinstance(captured.get("system"), list) and len(captured["system"]) == 2)
check("回答照原文（低纖維、蛋白）", "低纖維、蛋白" in resp.response)
check("來源只列實際引用的單張", resp.sources == ["ONC-22 腹瀉"] or (len(resp.sources) == 1 and resp.sources[0].startswith("ONC-22")))
with db.get_conn() as conn:
    m = conn.execute("SELECT answer_quality, rag_doc_versions, cache_read_tokens, cache_write_tokens "
                     "FROM messages WHERE patient_code='P801' "
                     "AND role='bot' ORDER BY id DESC").fetchone()
check("品質標記 grounded", m["answer_quality"] == "grounded")
check("單張版本只記引用的 ONC-22", list(__import__("json").loads(m["rag_doc_versions"])) == ["ONC-22"])
check("K2：快取用量寫入資料庫", m["cache_read_tokens"] == 19500 and m["cache_write_tokens"] == 0)
import stats
header = stats.export_csv("messages").splitlines()[0]
check("K2：研究匯出包含快取用量與拒答類別", "cache_read_tokens" in header and "refusal_category" in header)
with db.get_conn() as conn:
    check("資料庫 schema v7", conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()["v"] == 7)

print(f"\n通過 {_pass} / {_pass + _fail}")
if _fail:
    raise SystemExit(1)
