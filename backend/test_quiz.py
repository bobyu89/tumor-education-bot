"""
Phase 3b 知識測驗測試（不需 API key）。
驗證題庫、出題不含答案、評分正確、前後測落庫與統計。

執行：cd backend && python -X utf8 test_quiz.py
"""
import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_quiz.sqlite3"
db.DB_PATH.unlink(missing_ok=True)
db.init_db()

import quiz
import stats

_pass = _fail = 0
def check(name, cond):
    global _pass, _fail
    if cond: _pass += 1; print(f"  ✓ {name}")
    else: _fail += 1; print(f"  ✗ {name}  ← 失敗")


print("=== 題庫 ===")
tp = quiz.topics()
check("13 個主題", len(tp) == 13)
check("ONC-17 在清單", any(t["onc_code"] == "ONC-17" for t in tp))

print("\n=== 出題不含答案（防作弊）===")
q = quiz.get_questions("ONC-17")
check("回傳 6 題", len(q["questions"]) == 6)
check("題目不含 answer 欄位", all("answer" not in item for item in q["questions"]))
check("選擇題附選項", any(item["type"] == "mc" and item.get("options") for item in q["questions"]))
check("未知主題 → None", quiz.get_questions("ONC-99") is None)

print("\n=== 評分：ONC-17 正解 1.O 2.O 3.O 4.1 5.4 6.4 ===")
full = quiz.score("Q1", "ONC-17", {"1":"O","2":"O","3":"O","4":"1","5":"4","6":"4"}, "pre")
check("全對 → 6/6", full["score"] == 6 and full["total"] == 6)
half = quiz.score("Q1", "ONC-17", {"1":"X","2":"O","3":"O","4":"2","5":"4","6":"4"}, "post")
check("錯 2 題 → 4/6", half["score"] == 4)
check("details 標出第1題錯", any(d["n"]==1 and not d["correct"] for d in half["details"]))
check("details 不外洩到分數以外（含正解供檢討）", half["details"][0]["answer"] == "O")

print("\n=== 前後測落庫 + 統計 ===")
ov = stats.cohort_overview()
check("quiz 統計含 ONC-17", "ONC-17" in ov["quiz"])
check("ONC-17 有 pre 與 post", "pre" in ov["quiz"]["ONC-17"] and "post" in ov["quiz"]["ONC-17"])
check("pre 平均 = 6", ov["quiz"]["ONC-17"]["pre"]["avg"] == 6)
check("post 平均 = 4", ov["quiz"]["ONC-17"]["post"]["avg"] == 4)

print("\n=== phase 驗證 ===")
try:
    quiz.score("Q1", "ONC-17", {"1":"O"}, "middle")
    check("非法 phase → 應擋", False)
except ValueError:
    check("非法 phase 被擋（pre/post）", True)

print("\n=== 端點（TestClient）===")
from fastapi.testclient import TestClient
import main
with TestClient(main.app) as client:
    r = client.get("/quiz/topics")
    check("/quiz/topics → 13", r.status_code == 200 and len(r.json()) == 13)
    r = client.get("/quiz/ONC-22")
    check("/quiz/ONC-22 出題且無答案", r.status_code == 200 and all("answer" not in i for i in r.json()["questions"]))
    r = client.post("/quiz/ONC-22", json={"patient_id": "P001",
        "answers": {"1":"X","2":"O","3":"X","4":"4","5":"4","6":"4"}, "phase": "post"})
    check("/quiz/ONC-22 提交 → 6/6（腹瀉正解）", r.status_code == 200 and r.json()["score"] == 6)
    r = client.get("/quiz/ONC-99")
    check("未知主題 → 404", r.status_code == 404)

print(f"\n{'='*40}\n通過 {_pass} / {_pass + _fail}")
db.DB_PATH.unlink(missing_ok=True)
sys.exit(0 if _fail == 0 else 1)
