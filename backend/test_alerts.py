"""
警示通知與確認（P0②，2026-11 同儕審查）測試，不需 API key。

驗證：
  - notified_channels 據實記錄：沒有護理端在線 → []，並留 alert_undelivered 事件；有人在線 → ["websocket"]
  - MEDIUM 推播並進入未確認清單（v1 只落庫不通知）；走症狀評估路徑也只記一次
  - 知識庫外提問進入清單（level=info），且回覆不承諾「護理師會回覆」
  - GET /alerts 排序與逾時標示；POST /alerts/{id}/ack 只能確認一次、需 staff 權限、有稽核
  - 病患端文字：LINE 不說「已通知醫療團隊」、不叫居家病患按呼叫鈴；發燒／自傷有對應指引
  - 症狀評估：腹瀉 ≥7 次／天（CTCAE grade 3）升級為 HIGH

執行：cd backend && python -X utf8 test_alerts.py
"""
import testutil  # 必須最先（設定 DEMO_SEED）
import asyncio
import json
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_alerts.sqlite3"
db.DB_PATH.unlink(missing_ok=True)

from fastapi.testclient import TestClient
import main
import rag
from chat_service import process_message
from prompt import build_prompt

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1; print(f"  ✓ {name}")
    else:
        _fail += 1; print(f"  ✗ {name}  ← 失敗")


def last_alert(pid: str, level: str | None = None) -> dict | None:
    with db.get_conn() as conn:
        q = "SELECT * FROM alerts WHERE patient_code=?" + (" AND level=?" if level else "") + " ORDER BY id DESC"
        row = conn.execute(q, (pid, level) if level else (pid,)).fetchone()
    return dict(row) if row else None


def count_alerts(pid: str) -> int:
    with db.get_conn() as conn:
        return conn.execute("SELECT COUNT(*) c FROM alerts WHERE patient_code=?", (pid,)).fetchone()["c"]


def events(kind: str) -> list[dict]:
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM events WHERE event_type=?", (kind,)).fetchall()]


with TestClient(main.app) as client:
    P = testutil.login_headers(client, "P001", "patient123")
    R = testutil.login_headers(client, "R001", "research123")

    print("=== HIGH：沒有護理端在線 → 據實記錄未送達 ===")
    r = client.post("/chat", json={"message": "我發燒38.5度一直發抖"}, headers=P).json()
    a = last_alert("P001", "high")
    check("觸發 HIGH", r["is_emergency"] is True)
    check("notified_channels = []（沒人在線）", a and json.loads(a["notified_channels"]) == [])
    check("留下 alert_undelivered 事件", any(json.loads(e["payload"] or "{}").get("alert_id") == a["id"]
                                           for e in events("alert_undelivered")))
    check("網頁回覆：發燒指引（不可自行吃退燒藥）", "退燒藥" in r["response"])
    check("網頁回覆：以呼叫鈴為主", "呼叫鈴" in r["response"])

    print("\n=== HIGH：有護理端在線 → 實際推播 ===")
    with client.websocket_connect(f"/ws/nurse?ticket={client.post('/ws/ticket', headers=R).json()['ticket']}") as ws:
        r = client.post("/chat", json={"message": "我昨天沒睡好今天胸痛"}, headers=P).json()
        msg = json.loads(ws.receive_text())
        check("v1 否定誤殺案例現在觸發 HIGH", r["is_emergency"] is True)
        check("護理端收到 EMERGENCY_ALERT（含類別）",
              msg["type"] == "EMERGENCY_ALERT" and msg["category"] == "cardiorespiratory")
        a = last_alert("P001", "high")
        check("notified_channels = ['websocket']", json.loads(a["notified_channels"]) == ["websocket"])
        h = client.get("/health").json()
        check("/health 回報在線護理端數", h["nurse_connections"] == 1)

    print("\n=== MEDIUM：推播並進清單，走評估路徑也只記一次 ===")
    n0 = count_alerts("P001")
    P2 = P
    r = client.post("/chat", json={"message": "我一直拉肚子"}, headers=P2).json()
    check("進入症狀評估（問幾分）", "幾分" in r["response"])
    a = last_alert("P001", "medium")
    check("MEDIUM 已落庫", a is not None and "一直拉肚子" in a["trigger"])
    check("只記一次", count_alerts("P001") == n0 + 1)

    print("\n=== 症狀評估：腹瀉 8 次／天 → HIGH（CTCAE grade 3）===")
    client.post("/chat", json={"message": "5分"}, headers=P2)
    r = client.post("/chat", json={"message": "今天拉了8次"}, headers=P2).json()
    a = last_alert("P001")
    check("升級為 high", a["level"] == "high" and "腹瀉" in a["trigger"])
    check("回覆建議盡快就醫", "盡快就醫" in r["response"])

    print("\n=== 知識庫外提問 → info 進清單、不承諾回覆 ===")
    _orig = rag.retriever.query
    rag.retriever.query = lambda *a, **k: []
    r = client.post("/chat", json={"message": "健保卡遺失怎麼補辦"}, headers=P).json()
    rag.retriever.query = _orig
    a = last_alert("P001", "info")
    check("info 警示落庫", a is not None and "健保卡" in a["trigger"])
    check("回覆不含『護理師會盡快回覆』", "會盡快回覆" not in r["response"])
    sess = client.get("/sessions", headers=R).json()
    p1 = next(s for s in sess if s["patient_code"] == "P001")
    check("info 不算紅旗數", all(f["level"] in ("high", "medium") for f in p1["red_flags"]))

    print("\n=== GET /alerts ===")
    check("病患不能看清單", client.get("/alerts", headers=P).status_code in (401, 403))
    lst = client.get("/alerts", headers=R).json()
    items = lst["items"]
    check("回傳未確認清單", len(items) >= 4 and all(i["ack_at"] is None for i in items))
    levels = [i["level"] for i in items]
    check("high 排在最前面", levels == sorted(levels, key=lambda l: {"high": 0, "medium": 1, "info": 2}[l]))
    check("附原始訊息內容", any("胸痛" in (i["message"] or "") for i in items))
    check("附逾時欄位", all({"age_min", "sla_min", "overdue"} <= set(i) for i in items))
    # 把一筆 HIGH 的時間改到 1 小時前 → 應標示逾時
    old_id = next(i["id"] for i in items if i["level"] == "high")
    with db.get_conn() as conn:
        conn.execute("UPDATE alerts SET ts=? WHERE id=?", ("2020-01-01T00:00:00+00:00", old_id))
    it = next(i for i in client.get("/alerts", headers=R).json()["items"] if i["id"] == old_id)
    check("超過時限 → overdue", it["overdue"] is True)

    print("\n=== POST /alerts/{id}/ack ===")
    check("病患不能確認", client.post(f"/alerts/{old_id}/ack", headers=P).status_code in (401, 403))
    r = client.post(f"/alerts/{old_id}/ack", headers=R)
    check("確認成功", r.status_code == 200 and r.json()["ack_by"] == "R001")
    check("重複確認 409", client.post(f"/alerts/{old_id}/ack", headers=R).status_code == 409)
    check("不存在 404", client.post("/alerts/999999/ack", headers=R).status_code == 404)
    check("確認後不在未確認清單", all(i["id"] != old_id for i in client.get("/alerts", headers=R).json()["items"]))
    acked = client.get("/alerts?status=acked", headers=R).json()["items"]
    check("出現在已確認清單（含確認者與時間）", any(i["id"] == old_id and i["ack_by"] == "R001" and i["ack_at"] for i in acked))
    check("確認動作有稽核紀錄", any(e["actor"] == "R001" for e in events("ack_alert")))
    check("status 參數驗證", client.get("/alerts?status=bogus", headers=R).status_code == 422)

print("\n=== LINE 管道文字（居家病患）===")
r = asyncio.run(process_message("P001", "燒到39度", channel="line"))
check("發燒：不可自行吃退燒藥", "退燒藥" in r.response)
check("聲明不是 24 小時即時監看", "不是 24 小時" in r.response)
check("不宣稱『已通知醫療團隊』", "已記錄並通知醫療團隊" not in r.response)
check("不叫居家病患按呼叫鈴", "呼叫鈴" not in r.response)
r = asyncio.run(process_message("P001", "我想一了百了", channel="line"))
check("自傷：附 24 小時安心專線 1925", "1925" in r.response)
check("自傷：同理語句", "您不是一個人" in r.response)
_orig = rag.retriever.query
rag.retriever.query = lambda *a, **k: []
r = asyncio.run(process_message("P001", "可以幫我掛號嗎", channel="line"))
rag.retriever.query = _orig
check("轉介：不保證有人回覆", "不保證" in r.response and "會盡快回覆" not in r.response)

print("\n=== LLM system prompt 安全規則依管道 ===")
check("LINE：不叫按呼叫鈴、給電話", "呼叫鈴" not in build_prompt(None, [], channel="line")
      and "119" in build_prompt(None, [], channel="line"))
check("網頁：呼叫鈴", "呼叫鈴" in build_prompt(None, [], channel="web"))
check("安全規則涵蓋發燒", "發燒" in build_prompt(None, []))

print(f"\n通過 {_pass} / {_pass + _fail}")
if _fail:
    raise SystemExit(1)
