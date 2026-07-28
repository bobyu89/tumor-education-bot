"""
Phase 3a 測試（不需 API key）：統計聚合、CSV 去識別化、權限把關。

執行：cd backend && python -X utf8 test_phase3.py
"""
import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_p3.sqlite3"
db.DB_PATH.unlink(missing_ok=True)
db.init_db()

import auth
import stats

_pass = _fail = 0
def check(name, cond):
    global _pass, _fail
    if cond: _pass += 1; print(f"  ✓ {name}")
    else: _fail += 1; print(f"  ✗ {name}  ← 失敗")


# ── 塞入已知資料 ──
auth.create_user("P001", "patient123", "patient", pseudonym="P001",
                 real_name="王大明真名", age=58, cancer_type="口腔癌")
auth.create_user("R001", "research123", "researcher")
db.add_message("P001", "patient", "我一直拉肚子", redflag_level="medium")
db.add_message("P001", "bot", "建議…", rag_sources=["ONC-22 腹瀉"], answer_quality="grounded")
db.add_symptom_score("P001", "diarrhea", 8, {"count": 7}, "patient_initiated")
db.add_symptom_score("P001", "diarrhea", 3, {"count": 2}, "patient_initiated")
db.add_symptom_score("P001", "nausea", 5, {}, "patient_initiated")
db.add_alert("P001", "diarrhea:8", "medium", 1, ["websocket"])
db.add_alert("P001", "胸痛", "high", 1, ["websocket"])

print("=== 世代總覽 ===")
ov = stats.cohort_overview()
check("病患數 = 1", ov["patients"] == 1)
check("high alert = 1", ov["alerts"]["high"] == 1)
check("medium alert = 1", ov["alerts"]["medium"] == 1)
diarrhea = next(s for s in ov["symptoms"] if s["symptom"] == "diarrhea")
check("腹瀉樣本數 = 2", diarrhea["n"] == 2)
check("腹瀉平均 = 5.5", abs(diarrhea["avg_score"] - 5.5) < 0.01)
check("腹瀉嚴重數(≥7) = 1", diarrhea["severe_n"] == 1)
check("品質分布含 grounded", ov["answer_quality"].get("grounded") == 1)

print("\n=== 個別病患趨勢 ===")
tr = stats.patient_trends("P001")
check("腹瀉序列 2 點", len(tr["symptom_series"]["diarrhea"]) == 2)
check("噁心序列 1 點", len(tr["symptom_series"]["nausea"]) == 1)
check("alert 2 筆", len(tr["alerts"]) == 2)

print("\n=== CSV 去識別化（關鍵）===")
csv_scores = stats.export_csv("symptom_scores")
check("CSV 有表頭 patient_code", csv_scores.splitlines()[0].startswith("patient_code"))
check("CSV 含假名 P001", "P001" in csv_scores)
check("CSV 不含真名『王大明真名』", "王大明真名" not in csv_scores)
csv_msg = stats.export_csv("messages")
check("messages CSV 也不含真名", "王大明真名" not in csv_msg)
try:
    stats.export_csv("users")   # users 不在白名單（含密碼）
    check("不可匯出 users → 應擋下", False)
except ValueError:
    check("不可匯出 users（白名單擋下）", True)

print("\n=== 權限把關（TestClient）===")
from fastapi.testclient import TestClient
import main
with TestClient(main.app) as client:
    r = client.get("/stats/overview")
    check("無 token → 401", r.status_code == 401)
    pat = client.post("/login", json={"account": "P001", "password": "patient123"}).json()
    r = client.get("/stats/overview", headers={"X-Auth-Token": pat["token"]})
    check("病患 token → 403", r.status_code == 403)
    res = client.post("/login", json={"account": "R001", "password": "research123"}).json()
    r = client.get("/stats/overview", headers={"X-Auth-Token": res["token"]})
    check("研究者 token → 200", r.status_code == 200)
    r = client.get("/export/symptom_scores.csv", headers={"X-Auth-Token": res["token"]})
    check("匯出 CSV → 200 且 text/csv", r.status_code == 200 and "text/csv" in r.headers["content-type"])
    check("匯出內容不含真名", "王大明真名" not in r.text)

print(f"\n{'='*40}\n通過 {_pass} / {_pass + _fail}")
db.DB_PATH.unlink(missing_ok=True)
sys.exit(0 if _fail == 0 else 1)
