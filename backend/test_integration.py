"""
整合測試（不需 API key）：app 啟動、/health、/login、紅旗 /chat 短路。
驗證 rag.py 用 ONNX 嵌入層載入成功、main.py 全鏈路可跑。

執行：cd backend && python -X utf8 test_integration.py
"""
import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_integ.sqlite3"
db.DB_PATH.unlink(missing_ok=True)

from fastapi.testclient import TestClient
import main

_pass = _fail = 0
def check(name, cond):
    global _pass, _fail
    if cond: _pass += 1; print(f"  ✓ {name}")
    else: _fail += 1; print(f"  ✗ {name}  ← 失敗")

with TestClient(main.app) as client:   # with 觸發 startup（init_db + seed）
    print("=== /health ===")
    r = client.get("/health")
    check("/health 200", r.status_code == 200)
    check("回報 rag_chunks", "rag_chunks" in r.json())
    print(f"    rag_chunks = {r.json().get('rag_chunks')}, provider = {r.json().get('llm_provider')}")

    print("\n=== /login（seed 帳號）===")
    r = client.post("/login", json={"account": "R001", "password": "research123"})
    check("研究者登入 200", r.status_code == 200 and r.json()["role"] == "researcher")
    r = client.post("/login", json={"account": "R001", "password": "wrong"})
    check("錯密碼 401", r.status_code == 401)
    r = client.post("/login", json={"account": "P001", "password": "patient123"})
    check("病患登入帶 patient_code", r.json().get("patient_code") == "P001")

    print("\n=== 紅旗 /chat 短路（不呼叫 LLM）===")
    r = client.post("/chat", json={"patient_id": "P001", "message": "我突然胸痛喘不過氣"})
    body = r.json()
    check("回應 200", r.status_code == 200)
    check("標記為 emergency", body["is_emergency"] is True)
    check("回覆含呼叫鈴指示", "呼叫鈴" in body["response"])
    check("未走 RAG（sources 空）", body["sources"] == [])

    print("\n=== 紅旗事件已落庫 ===")
    sess = client.get("/sessions").json()
    p001 = next((s for s in sess if s["patient_code"] == "P001"), None)
    check("P001 有 has_emergency", p001 and p001["has_emergency"] is True)

    print("\n=== 品質閘：無來源時 deflect（以空 RAG 模擬）===")
    # LLM 正常路徑需有效 API key，整合測試不涵蓋；此處直接驗證「無來源→轉介」邏輯，
    # 暫時讓 retriever 回傳空來源，確認不呼叫 LLM 也不編造。
    import rag
    _orig = rag.retriever.query
    rag.retriever.query = lambda *a, **k: []
    try:
        r = client.post("/chat", json={"patient_id": "P001", "message": "化療期間可以做什麼運動"})
        body = r.json()
        check("無來源被轉介（未呼叫 LLM、未編造）",
              "護理師" in body["response"] and body["sources"] == [])
    finally:
        rag.retriever.query = _orig

print(f"\n{'='*40}\n通過 {_pass} / {_pass + _fail}")
db.DB_PATH.unlink(missing_ok=True)
sys.exit(0 if _fail == 0 else 1)
