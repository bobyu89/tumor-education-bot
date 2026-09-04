"""
演示彩排：用 LLM_PROVIDER=mock 把「演示腳本」的每個情境跑一遍，印出對話逐字稿並逐項驗證。
不需 API key、不動正式資料庫（用暫存 SQLite），可當 CI 測試。

執行（專案根目錄）：
    python -X utf8 scripts/demo_dry_run.py

情境對照 docs/demo/演示腳本.md：
  S1 一般衛教問答（有來源、grounded）
  S2 症狀評估未達門檻 → 衛教
  S3 症狀評估達門檻 → 升級紅旗、通知護理師
  S4 HIGH 紅旗瞬間短路（不進 RAG/LLM）+ 護理師 WebSocket 收到推播
  S5 否定語境不誤觸（「沒有胸痛」）
  S6 自傷語意從寬觸發（「撐不下去」）
  S7 知識庫外問題 → 誠實轉介，不編造
  S8 知識測驗前後測
  S9 研究者統計與去識別化匯出（需 token）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ["LLM_PROVIDER"] = "mock"          # 先於 config 載入

import db                                    # noqa: E402
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_demo_dry_run.sqlite3"
for suffix in ("", "-wal", "-shm"):
    Path(str(db.DB_PATH) + suffix).unlink(missing_ok=True)

from fastapi.testclient import TestClient    # noqa: E402
import main                                  # noqa: E402

_pass = _fail = 0


def check(name: str, cond: bool) -> None:
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"     ✓ {name}")
    else:
        _fail += 1
        print(f"     ✗ {name}  ← 失敗")


def say(client: TestClient, pid: str, text: str) -> dict:
    print(f"  👤 {pid}：{text}")
    body = client.post("/chat", json={"patient_id": pid, "message": text}).json()
    tag = " 🚨" if body["is_emergency"] else ""
    reply = body["response"].replace("\n", "\n        ")
    print(f"  🤖 {reply}{tag}")
    if body["sources"]:
        print(f"     📚 來源：{', '.join(dict.fromkeys(body['sources']))}")
    return body


def title(s: str) -> None:
    print(f"\n{'─' * 64}\n{s}\n{'─' * 64}")


with TestClient(main.app) as client:
    h = client.get("/health").json()
    print(f"/health → provider={h['llm_provider']}, rag_chunks={h['rag_chunks']}")
    check("演示模式 provider=mock", h["llm_provider"] == "mock")
    check("向量庫有內容（請先跑 scripts/index_vault.py --rebuild）", h["rag_chunks"] > 0)

    title("S1 一般衛教問答：P001（58 歲，general 語氣）")
    r = say(client, "P001", "化療後掉頭髮會長回來嗎")
    check("有來源", len(r["sources"]) > 0)
    check("非緊急", r["is_emergency"] is False)
    check("回覆引用衛教資料", "根據衛教資料" in r["response"])

    title("S2 症狀評估未達門檻 → 衛教：腹瀉 3 分、一天四次")
    r = say(client, "P001", "我一直拉肚子")
    check("先問嚴重度（未進 RAG/LLM）", "幾分" in r["response"] and r["sources"] == [])
    r = say(client, "P001", "3分")
    check("續問次數", "幾次" in r["response"])
    r = say(client, "P001", "一天四次")
    check("未達門檻 → 帶嚴重度導語的衛教", "3/10" in r["response"] and len(r["sources"]) > 0)
    check("分數已落庫", db.get_symptom_scores("P001", "diarrhea")[-1]["score"] == 3)

    title("S3 症狀評估達門檻 → 升級：噁心嘔吐 8 分、六次")
    r = say(client, "P001", "打完針之後一直想吐")
    r = say(client, "P001", "8")
    r = say(client, "P001", "吐了六次")
    check("升級為 emergency、通知護理師", r["is_emergency"] is True and "護理師" in r["response"])
    check("alert 落庫（medium）",
          any(a["level"] == "medium" and a["trigger"].startswith("nausea")
              for s in client.get("/sessions").json() if s["patient_code"] == "P001"
              for a in s["red_flags"]))

    title("S4 HIGH 紅旗瞬間短路 + 護理師 WebSocket 推播")
    with client.websocket_connect("/ws/nurse") as ws:
        r = say(client, "P001", "我現在胸痛而且喘不過氣")
        check("emergency、固定安全回覆、不進 RAG", r["is_emergency"] and "呼叫鈴" in r["response"] and r["sources"] == [])
        check("命中關鍵字 胸痛/喘不過氣", set(r["emergency_keywords"]) >= {"胸痛", "喘不過氣"})
        evt = json.loads(ws.receive_text())
        print(f"  📟 護理站收到：{evt['type']} severity={evt['severity']} keywords={evt['keywords']}")
        check("護理師端即時收到 EMERGENCY_ALERT", evt["type"] == "EMERGENCY_ALERT" and evt["severity"] == "high")

    title("S5 否定語境不誤觸：「沒有胸痛」")
    r = say(client, "P001", "醫師說如果胸痛要回診，我現在沒有胸痛，平常要注意什麼")
    check("未觸發緊急", r["is_emergency"] is False)

    title("S6 自傷語意從寬觸發：「撐不下去」")
    r = say(client, "P001", "最近每天都好累，覺得撐不下去了")
    check("觸發 HIGH（不套否定規則）", r["is_emergency"] is True and "撐不下去" in r["emergency_keywords"])

    title("S7 知識庫外問題 → 誠實轉介，不編造")
    r = say(client, "P001", "請問醫院的停車場在哪裡")
    check("轉介護理師、未編造衛教內容", "護理師" in r["response"] and "根據衛教資料" not in r["response"])
    last = db.get_patient_messages("P001")[-1]
    check("品質閘標記 deflected_*", (last["answer_quality"] or "").startswith("deflected"))

    title("S8 知識測驗前後測（ONC-22 腹瀉）")
    q = client.get("/quiz/ONC-22").json()
    print(f"  📝 {q['topic']}：{len(q['questions'])} 題，第 1 題：{q['questions'][0]['text'][:30]}…")
    check("出題不含答案", all("answer" not in i for i in q["questions"]))
    pre = client.post("/quiz/ONC-22", json={"patient_id": "P001", "phase": "pre",
                      "answers": {"1": "O", "2": "O", "3": "X", "4": "1", "5": "4", "6": "4"}}).json()
    post = client.post("/quiz/ONC-22", json={"patient_id": "P001", "phase": "post",
                       "answers": {"1": "X", "2": "O", "3": "X", "4": "4", "5": "4", "6": "4"}}).json()
    print(f"  📊 前測 {pre['score']}/{pre['total']} → 後測 {post['score']}/{post['total']}")
    check("後測分數高於前測", post["score"] > pre["score"])

    title("S9 研究者統計與去識別化匯出")
    tok = client.post("/login", json={"account": "R001", "password": "research123"}).json()["token"]
    check("未帶 token 存取統計 → 401", client.get("/stats/overview").status_code == 401)
    ov = client.get("/stats/overview", headers={"X-Auth-Token": tok}).json()
    print(f"  📈 病患 {ov['patients']}、訊息 {ov['messages']}、警示 high={ov['alerts']['high']} medium={ov['alerts']['medium']}")
    print(f"     症狀：{[(s['symptom'], s['avg_score']) for s in ov['symptoms']]}")
    print(f"     品質：{ov['answer_quality']}")
    check("統計含症狀與品質分布", ov["symptoms"] and ov["answer_quality"])
    csv = client.get("/export/messages.csv", headers={"X-Auth-Token": tok}).text
    check("CSV 只含假名、不含真名", "P001" in csv and "測試病患" not in csv and "real_name" not in csv)
    ptok = client.post("/login", json={"account": "P001", "password": "patient123"}).json()["token"]
    check("病患 token 存取匯出 → 403", client.get("/export/alerts.csv", headers={"X-Auth-Token": ptok}).status_code == 403)

print(f"\n{'=' * 64}\n彩排結果：通過 {_pass} / {_pass + _fail}")
for suffix in ("", "-wal", "-shm"):
    Path(str(db.DB_PATH) + suffix).unlink(missing_ok=True)
sys.exit(0 if _fail == 0 else 1)
