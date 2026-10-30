"""
每人活動 LOG 與人員稽核測試。不需 API key。

執行：cd backend && python -X utf8 test_logs.py
"""
import testutil  # 必須最先（設定 DEMO_SEED）
import json
import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_logs.sqlite3"
db.DB_PATH.unlink(missing_ok=True)

from fastapi.testclient import TestClient

import main
import rag
rag.retriever.query = lambda *a, **k: []     # 不呼叫外部 LLM

_pass = _fail = 0
def check(name, cond):
    global _pass, _fail
    if cond: _pass += 1; print(f"  ✓ {name}")
    else: _fail += 1; print(f"  ✗ {name}  ← 失敗")


with TestClient(main.app) as client:
    P = testutil.login_headers(client, "P001", "patient123")
    R = testutil.login_headers(client, "R001", "research123")
    A = testutil.login_headers(client, "A001", "admin123")

    # 製造各種活動
    client.post("/chat", json={"message": "我突然胸痛"}, headers=P)              # 紅旗
    client.post("/chat", json={"message": "我一直拉肚子"}, headers=P)            # 開始追問
    client.post("/chat", json={"message": "3分"}, headers=P)
    client.post("/chat", json={"message": "一天兩次"}, headers=P)                # 完成 → 症狀分數
    client.post("/quiz/ONC-17", headers=P, json={"answers": {"1": "O"}, "phase": "pre"})
    client.get("/history/P001", headers=R)                                       # 研究者查看
    client.get("/export/messages.csv", headers=R)                                # 研究者匯出

    print("=== 病患 LOG：權限 ===")
    check("無 token → 401", client.get("/logs/patient/P001").status_code == 401)
    check("病患 token → 403（病患不能看 LOG）", client.get("/logs/patient/P001", headers=P).status_code == 403)
    check("非法代號 → 400", client.get("/logs/patient/x1", headers=R).status_code == 400)

    print("\n=== 病患 LOG：內容 ===")
    log = client.get("/logs/patient/P001", headers=R).json()["items"]
    kinds = {it["kind"] for it in log}
    for k in ["message", "alert", "symptom", "quiz", "access", "event"]:
        check(f"包含 {k}", k in kinds)
    access = [it for it in log if it["kind"] == "access"]
    check("看得到『R001 查看對話紀錄』", any(it["who"] == "R001" and it["summary"] == "查看對話紀錄" for it in access))
    check("查看 LOG 本身也被記錄（下一次查看可見）",
          any(it["summary"] == "查看活動 LOG" for it in
              client.get("/logs/patient/P001", headers=R).json()["items"]))
    check("時間由新到舊", all(log[i]["ts"] >= log[i + 1]["ts"] for i in range(len(log) - 1)))
    check("LOG 不含真名", "測試病患" not in json.dumps(log, ensure_ascii=False))
    check("不重複列出紅旗（alerts 與 events 各一次）",
          sum(1 for it in log if "胸痛" in it["summary"] and it["kind"] == "alert") == 1)

    print("\n=== 人員稽核：限管理者 ===")
    check("研究者 → 403", client.get("/logs/staff", headers=R).status_code == 403)
    check("病患 → 403", client.get("/logs/staff", headers=P).status_code == 403)
    staff = client.get("/logs/staff", headers=A).json()["items"]
    actions = {(it["actor"], it["event_type"]) for it in staff}
    check("記到 R001 登入", ("R001", "login") in actions)
    check("記到 R001 查看 P001 對話", any(it["actor"] == "R001" and it["event_type"] == "view_history"
                                         and it["target"] == "P001" for it in staff))
    check("記到 R001 匯出 messages", any(it["actor"] == "R001" and it["event_type"] == "export_csv"
                                         and it["target"] == "messages" for it in staff))
    check("不含病患自己的登入", not any(it["actor"] == "P001" for it in staff))

    client.post("/login", json={"account": "R001", "password": "wrong"})
    staff = client.get("/logs/staff", headers=A).json()["items"]
    check("登入失敗也記錄（可偵測暴力破解）",
          any(it["actor"] == "R001" and it["event_type"] == "login_failed" for it in staff))

    only_r = client.get("/logs/staff?account=R001", headers=A).json()["items"]
    check("可篩選單一人員", only_r and all(it["actor"] == "R001" for it in only_r))

print(f"\n{'='*40}\n通過 {_pass} / {_pass + _fail}")
db.DB_PATH.unlink(missing_ok=True)
sys.exit(0 if _fail == 0 else 1)
