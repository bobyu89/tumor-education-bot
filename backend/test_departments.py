"""
科別知識庫（第 2 階段）測試：新增科別只需新增 knowledge/<科別>/manifest.json 與單張，不改程式。

用一個測試用的假科別「cardiology-test」（2 份虛構單張）驗證：
  - 科別註冊表與設定檔檢查
  - 紅旗：通用核心兩科都適用；發燒等級、體溫門檻、擴充規則依科別不同
  - 症狀評估協定依科別不同
  - 索引帶科別標記，檢索只查病患所屬科別
  - 緊急電話與安全文字依科別覆寫
  - 管理者 API：建立病患時指定科別、列出科別

執行：cd backend && python -X utf8 test_departments.py
"""
import testutil  # 必須最先
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

import departments

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1; print(f"  ✓ {name}")
    else:
        _fail += 1; print(f"  ✗ {name}  ← 失敗")


ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="kb_dept_"))
KB = TMP / "knowledge"
(KB / "hemato-oncology").mkdir(parents=True)
hemato = json.loads((ROOT / "knowledge/hemato-oncology/manifest.json").read_text(encoding="utf-8"))
hemato["source_dir"] = str(ROOT / "Obsidian_Vault/衛教知識庫")
(KB / "hemato-oncology/manifest.json").write_text(json.dumps(hemato, ensure_ascii=False), encoding="utf-8")

# ── 測試用假科別 ──
CARDIO_SRC = TMP / "cardio_leaflets"
CARDIO_SRC.mkdir()
LEAFLET = """---
title: {title}
code: {code}
topic: {topic}
category: 心臟
doc_version: 2025.01 一版
rag_index: true
---

# {title}

## 居家照護

{body}

## 何時通知醫護人員

若喘到無法平躺、體重兩天增加兩公斤以上，請立即就醫。
"""
(CARDIO_SRC / "CV-01 心衰竭居家照護.md").write_text(LEAFLET.format(
    title="心衰竭居家照護", code="CV-01", topic="心衰竭",
    body="每天早上起床排尿後、吃早餐前量體重並記錄。限制鹽分攝取，依醫囑控制水分。"), encoding="utf-8")
(CARDIO_SRC / "CV-02 下肢水腫.md").write_text(LEAFLET.format(
    title="下肢水腫的照顧", code="CV-02", topic="下肢水腫",
    body="休息時把腳抬高，避免久站久坐。穿著醫師建議的彈性襪。"), encoding="utf-8")
(KB / "cardiology-test").mkdir()
(KB / "cardiology-test/manifest.json").write_text(json.dumps({
    "id": "cardiology-test", "name": "心臟內科（測試）", "source_dir": str(CARDIO_SRC),
    "emergency_contact": "請立即撥打 119，或聯絡心臟內科 (02) 0000-0000",
    "redflag": {"fever_level": "medium", "temp_high": None, "temp_medium": 38.0,
                "extra_high": [["cardiorespiratory", "re:體重.{0,6}(增加|多了).{0,4}(2|兩|二)公斤"],
                               ["cardiorespiratory", "無法平躺"]],
                "extra_medium": [["symptom", "腳腫"]]},
    "safety_text": {"cardiorespiratory": "喘到無法平躺或胸痛，可能是心衰竭惡化，需要立即處理（衛教單張 CV-01）。"},
    "assessment": [{"key": "edema", "name": "下肢水腫", "keywords": ["腳腫", "水腫"], "doc_code": "CV-02",
                    "field": "days", "question": "腫了幾天呢？（請給個數字）", "escalate_count": 3}],
}, ensure_ascii=False), encoding="utf-8")

departments.KNOWLEDGE_DIR = KB
departments.load_all(reload=True)

print("=== 科別註冊表 ===")
check("載入兩個科別", set(departments.load_all()) == {"hemato-oncology", "cardiology-test"})
check("預設科別為血液腫瘤科", departments.get().id == "hemato-oncology")
try:
    departments.get("nope")
    check("未知科別 → KeyError", False)
except KeyError:
    check("未知科別 → KeyError", True)

bad = TMP / "bad" / "knowledge" / "broken"
bad.mkdir(parents=True)
(bad / "manifest.json").write_text(json.dumps({"id": "broken", "name": "缺來源"}), encoding="utf-8")
_saved = departments.KNOWLEDGE_DIR
departments.KNOWLEDGE_DIR = bad.parent
try:
    departments.load_all(reload=True)
    check("設定檔缺 source_dir → 啟動即失敗", False)
except departments.ManifestError as e:
    check("設定檔缺 source_dir → 啟動即失敗", "source_dir" in str(e))
(bad / "manifest.json").write_text(json.dumps({"id": "other-name", "name": "x", "source_dir": str(CARDIO_SRC)}), encoding="utf-8")
try:
    departments.load_all(reload=True)
    check("id 與資料夾名稱不符 → 失敗", False)
except departments.ManifestError:
    check("id 與資料夾名稱不符 → 失敗", True)
departments.KNOWLEDGE_DIR = _saved
departments.load_all(reload=True)

print("\n=== 紅旗：通用核心＋科別擴充 ===")
from redflag import screen
H, C = "hemato-oncology", "cardiology-test"
check("發燒 38.5：血液腫瘤科 HIGH", screen("我發燒38.5度", False, H).severity == "high")
check("發燒 38.5：心臟科 MEDIUM（不是化療病人）", screen("我發燒38.5度", False, C).severity == "medium")
check("胸痛：兩科都 HIGH（通用核心）", screen("我胸痛", False, H).severity == screen("我胸痛", False, C).severity == "high")
check("自傷：兩科都 HIGH（通用核心）", screen("我想死", False, C).category == "self_harm")
check("心臟科擴充規則：體重兩天增加 2 公斤 → HIGH", screen("這兩天體重增加了2公斤", False, C).severity == "high")
check("同一句在血液腫瘤科不觸發", screen("這兩天體重增加了2公斤", False, H).severity == "none")
check("心臟科擴充 MEDIUM：腳腫", screen("我腳腫", False, C).severity == "medium")

print("\n=== 症狀評估協定依科別 ===")
import assessment
check("心臟科：「腳腫」→ 下肢水腫評估", (assessment.detect_symptom("我的腳腫起來", C) or object()).__dict__.get("key") == "edema")
check("血液腫瘤科沒有水腫協定", assessment.detect_symptom("我的腳腫起來", H) is None)
check("心臟科沒有腹瀉協定", assessment.detect_symptom("我一直拉肚子", C) is None)
check("水腫協定鎖定單張 CV-02", assessment.get_protocol("edema", C).doc_code == "CV-02")

print("\n=== 索引帶科別標記 ===")
sys.path.insert(0, str(ROOT / "scripts"))
import index_vault
chunks, report = index_vault.build_chunks()
cardio_chunks = [c for c in chunks if c.dept == C]
check("心臟科 2 份單張都切出段落", {c.code for c in cardio_chunks} == {"CV-01", "CV-02"})
check("短章節不會被丟掉（一句話的「何時通知醫護人員」）",
      any(c.section == "何時通知醫護人員" for c in cardio_chunks))
check("血液腫瘤科 13 份照常", len({c.code for c in chunks if c.dept == H}) == 13)
check("每個段落都帶科別", all(c.dept for c in chunks))

import chromadb
from chromadb import EmbeddingFunction


class StubEF(EmbeddingFunction):
    def __init__(self):
        pass

    def __call__(self, input):
        return [[float((hash(t) >> s) & 0xFF) / 255 for s in range(0, 64, 8)] for t in input]

    @staticmethod
    def name():
        return "stub"


col = chromadb.EphemeralClient().get_or_create_collection("dept_test", embedding_function=StubEF(),
                                                          metadata={"hnsw:space": "cosine"})
index_vault.sync_collection(col, chunks)
only_c = col.query(query_texts=["每天量體重"], n_results=10, where={"dept": C})["metadatas"][0]
check("依科別檢索：只回心臟科單張", only_c and all(m["dept"] == C and m["code"].startswith("CV-") for m in only_c))

print("\n=== 對話流程：科別決定紅旗、檢索範圍與緊急電話 ===")
import db
db.DB_PATH = TMP / "dept.sqlite3"
db.init_db()
import auth
auth.create_user("P801", "x-pass-1", "patient", pseudonym="P801", department=H)
auth.create_user("P802", "x-pass-2", "patient", pseudonym="P802", department=C)
import chat_service
import llm_client
import rag
from llm_client import LLMResult

seen = {}


def fake_query(q, **kw):
    seen["dept"] = kw.get("dept")
    code = "CV-01" if kw.get("dept") == C else "ONC-25"
    return [{"source": f"{code} 測試", "code": code, "doc_version": "v", "content": "測試內容", "score": 0.9}]


class FakeLLM:
    def generate(self, system_prompt, *a, **k):
        seen["prompt"] = system_prompt
        return LLMResult("請每天量體重（CV-01）", "claude-sonnet-5-5", "anthropic", 10, 5)


_orig_q, _orig_k = rag.retriever.query, rag.retriever.known_codes
rag.retriever.query = fake_query
rag.retriever.known_codes = lambda: frozenset({"CV-01", "CV-02", "ONC-25"})
llm_client._client = FakeLLM()

r = asyncio.run(chat_service.process_message("P802", "我發燒38.5度", channel="line"))
check("心臟科病人發燒 → 不走急症短路", r.is_emergency is False)
check("檢索限定心臟科", seen.get("dept") == C)
check("提示詞用心臟科的緊急電話", "心臟內科 (02) 0000-0000" in seen.get("prompt", ""))
r = asyncio.run(chat_service.process_message("P801", "我發燒38.5度", channel="line"))
check("血液腫瘤科病人發燒 → 急症短路，引用 ONC-25", r.is_emergency and "ONC-25" in r.response)
r = asyncio.run(chat_service.process_message("P802", "我喘到無法平躺", channel="line"))
check("心臟科急症 → 心臟科的安全文字與緊急電話",
      r.is_emergency and "CV-01" in r.response and "心臟內科 (02) 0000-0000" in r.response)
r = asyncio.run(chat_service.process_message("P802", "我的腳腫起來", channel="line"))
check("心臟科症狀評估：問嚴重度並附按鈕", "幾分" in r.response and r.quick_replies)
rag.retriever.query, rag.retriever.known_codes = _orig_q, _orig_k
llm_client._client = None

print("\n=== 管理者 API ===")
from fastapi.testclient import TestClient
import main
auth.create_user("ADM9", "admin-pass-9", "admin")
with TestClient(main.app) as client:
    A = testutil.login_headers(client, "ADM9", "admin-pass-9")
    lst = client.get("/departments", headers=A).json()
    check("GET /departments 列出兩科，標示預設", {d["id"] for d in lst} == {H, C}
          and next(d for d in lst if d["id"] == H)["default"])
    r = client.post("/admin/patients", json={"pseudonym": "P803", "department": "nope"}, headers=A)
    check("未知科別 → 400", r.status_code == 400)
    r = client.post("/admin/patients", json={"pseudonym": "P803", "department": C}, headers=A)
    check("建立心臟科病人", r.status_code == 201 and r.json()["department"] == C)
    r = client.post("/admin/patients", json={"pseudonym": "P804"}, headers=A)
    check("未指定 → 預設科別", r.json()["department"] == H)
    rows = {p["pseudonym"]: p for p in client.get("/admin/patients", headers=A).json()}
    check("病患清單顯示科別", rows["P803"]["department"] == C)

departments.KNOWLEDGE_DIR = ROOT / "knowledge"
departments.load_all(reload=True)
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n通過 {_pass} / {_pass + _fail}")
if _fail:
    raise SystemExit(1)
