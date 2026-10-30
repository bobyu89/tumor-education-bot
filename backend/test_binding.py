"""
LINE 通行碼綁定與 webhook 測試。不需真的 LINE 帳號：
用測試用 Channel Secret 自行簽章，並攔截送往 LINE API 的請求。不需 API key。

執行：cd backend && python -X utf8 test_binding.py
"""
import testutil  # 必須最先（設定 DEMO_SEED）
import base64
import hashlib
import hmac
import json
import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_binding.sqlite3"
db.DB_PATH.unlink(missing_ok=True)

from fastapi.testclient import TestClient

import binding
import line_bot
import main
import rag
from config import settings

rag.retriever.query = lambda *a, **k: []     # 不呼叫外部 LLM

SENT: list[tuple[str, dict]] = []
async def _fake_post(path, payload):          # 攔截送往 LINE 的請求
    SENT.append((path, payload))
    return True
line_bot._post = _fake_post

_pass = _fail = 0
def check(name, cond):
    global _pass, _fail
    if cond: _pass += 1; print(f"  ✓ {name}")
    else: _fail += 1; print(f"  ✗ {name}  ← 失敗")


def last_text() -> str:
    msg = SENT[-1][1]["messages"][0]
    return msg["text"] if msg["type"] == "text" else msg["contents"]["body"]["contents"][0]["text"]


with TestClient(main.app) as client:
    A = testutil.login_headers(client, "A001", "admin123")
    R = testutil.login_headers(client, "R001", "research123")

    print("=== 管理者建立病患 ===")
    check("研究者不能建立病患 → 403",
          client.post("/admin/patients", json={"pseudonym": "P002"}, headers=R).status_code == 403)
    check("代號格式錯誤 → 400",
          client.post("/admin/patients", json={"pseudonym": "王小明"}, headers=A).status_code == 400)
    r = client.post("/admin/patients", headers=A,
                    json={"pseudonym": "P002", "cancer_type": "肺癌", "age": 61, "gender": "女"})
    check("建立 P002 → 201", r.status_code == 201)
    check("重複代號 → 409",
          client.post("/admin/patients", json={"pseudonym": "P002"}, headers=A).status_code == 409)

    print("\n=== 發通行碼 ===")
    check("研究者不能發碼 → 403",
          client.post("/admin/patients/P002/binding-code", headers=R).status_code == 403)
    check("不存在的病患 → 404",
          client.post("/admin/patients/P777/binding-code", headers=A).status_code == 404)
    first = client.post("/admin/patients/P002/binding-code", headers=A).json()["code"]
    issued = client.post("/admin/patients/P002/binding-code", headers=A).json()
    code = issued["code"]
    check("格式 XXXX-XXXX", len(code) == 9 and code[4] == "-")
    check("不含易混淆字元 0 O 1 I L", not set(code.replace("-", "")) & set("0O1IL"))
    with db.get_conn() as conn:
        stored = [r["code_hash"] for r in conn.execute("SELECT code_hash FROM binding_codes")]
    check("資料庫只存雜湊，不存明碼", code.replace("-", "") not in json.dumps(stored))

    print("\n=== 兌換 ===")
    check("重新發碼後，舊碼失效", binding.redeem("U_old", first)["reason"] == "invalid")
    ok = binding.redeem("U_alice", code.lower().replace("-", " "))   # 小寫、空格也接受
    check("正確通行碼 → 綁定成功", ok["ok"] and ok["patient_code"] == "P002")
    check("LINE userId 已對應 P002", binding.patient_for_line("U_alice") == "P002")
    check("同一組碼不能再用", binding.redeem("U_bob", code)["reason"] == "invalid")
    check("已綁定者再兌換 → already_bound", binding.redeem("U_alice", code)["reason"] == "already_bound")

    print("\n=== 防暴力猜碼 ===")
    for _ in range(binding.MAX_FAILS):
        binding.redeem("U_attacker", "AAAA-AAAA")
    fresh = client.post("/admin/patients/P001/binding-code", headers=A).json()["code"]
    check(f"輸錯 {binding.MAX_FAILS} 次後，即使碼正確也暫時鎖定",
          binding.redeem("U_attacker", fresh)["reason"] == "rate_limited")
    check("其他人不受影響", binding.redeem("U_carol", fresh)["ok"])

    print("\n=== 過期 ===")
    exp = client.post("/admin/patients/P002/binding-code", headers=A).json()["code"]
    with db.get_conn() as conn:
        conn.execute("UPDATE binding_codes SET expires_at='2000-01-01T00:00:00+00:00' WHERE used_at IS NULL")
    check("過期的碼不能用", binding.redeem("U_dave", exp)["reason"] == "invalid")

    print("\n=== 一位病患只綁一個 LINE ===")
    again = client.post("/admin/patients/P002/binding-code", headers=A).json()["code"]
    binding.redeem("U_alice_newphone", again)
    check("新 LINE 綁定 P002", binding.patient_for_line("U_alice_newphone") == "P002")
    check("舊 LINE 自動失效", binding.patient_for_line("U_alice") is None)

    print("\n=== 解除綁定 ===")
    check("研究者不能解除 → 403", client.delete("/admin/patients/P002/binding", headers=R).status_code == 403)
    check("管理者解除 → 200", client.delete("/admin/patients/P002/binding", headers=A).status_code == 200)
    check("解除後查不到", binding.patient_for_line("U_alice_newphone") is None)
    check("再解除一次 → 404", client.delete("/admin/patients/P002/binding", headers=A).status_code == 404)

    print("\n=== 病患清單（含綁定狀態，無真名）===")
    plist = client.get("/admin/patients", headers=A).json()
    p1 = next(p for p in plist if p["pseudonym"] == "P001")
    check("P001 顯示已綁定（U_carol）", p1["bound_at"] is not None)
    check("清單不含真名", "測試病患" not in json.dumps(plist, ensure_ascii=False))

    # ── LINE webhook ──
    print("\n=== Webhook：未設定金鑰時不存在 ===")
    settings.LINE_CHANNEL_SECRET, settings.LINE_CHANNEL_ACCESS_TOKEN = "", ""
    check("未設定 → 404", client.post("/line/webhook", content=b"{}").status_code == 404)

    settings.LINE_CHANNEL_SECRET, settings.LINE_CHANNEL_ACCESS_TOKEN = "test-channel-secret", "test-token"

    def hook(events, *, good_sig=True):
        body = json.dumps({"destination": "x", "events": events}, ensure_ascii=False).encode()
        sig = base64.b64encode(hmac.new(b"test-channel-secret", body, hashlib.sha256).digest()).decode()
        return client.post("/line/webhook", content=body,
                           headers={"X-Line-Signature": sig if good_sig else "forged",
                                    "Content-Type": "application/json"})

    def text_event(uid, text, eid):
        return {"type": "message", "webhookEventId": eid, "replyToken": f"rt-{eid}",
                "source": {"type": "user", "userId": uid},
                "message": {"type": "text", "id": eid, "text": text}}

    print("\n=== Webhook：驗簽 ===")
    check("偽造簽章 → 400", hook([], good_sig=False).status_code == 400)
    check("LINE Console『Verify』（空事件）→ 200", hook([]).status_code == 200)

    print("\n=== Webhook：病患完整流程 ===")
    hook([{"type": "follow", "webhookEventId": "e0", "replyToken": "rt0",
           "source": {"type": "user", "userId": "U_new"}}])
    check("加好友 → 歡迎並請輸入通行碼", "通行碼" in last_text())

    hook([text_event("U_new", "你好", "e1")])
    check("未綁定打招呼 → 請輸入通行碼（不算輸錯）",
          "通行碼" in last_text() and binding._recent_failures("U_new") == 0)

    hook([text_event("U_new", "我突然胸痛", "e2")])
    check("未綁定但說胸痛 → 仍先給緊急指引", "119" in last_text())

    line_code = client.post("/admin/patients/P002/binding-code", headers=A).json()["code"]
    hook([text_event("U_new", line_code, "e3")])
    check("輸入通行碼 → 綁定完成", "綁定完成" in last_text() and binding.patient_for_line("U_new") == "P002")

    SENT.clear()
    hook([text_event("U_new", "我突然胸痛喘不過氣", "e4")])
    msg = SENT[-1][1]["messages"][0]
    check("已綁定說胸痛 → 用 reply token 立即回覆", SENT[-1][0] == "/reply")
    check("居家版指引：119，不叫人按呼叫鈴", "119" in last_text() and "呼叫鈴" not in last_text())
    check("通知預覽只顯示中性文字（不露症狀）", msg["type"] == "flex" and "胸痛" not in msg["altText"])
    with db.get_conn() as conn:
        alert = conn.execute("SELECT trigger, level FROM alerts WHERE patient_code='P002'").fetchone()
    check("紅旗記在 P002 名下", alert and alert["level"] == "high")

    print("\n=== Webhook：重送去重、群組忽略 ===")
    n_before = len(SENT)
    hook([text_event("U_new", "我突然胸痛喘不過氣", "e4")])        # 同一個 webhookEventId
    check("LINE 重送同一事件 → 不重複處理", len(SENT) == n_before)
    hook([{"type": "message", "webhookEventId": "g1", "replyToken": "rtg",
           "source": {"type": "group", "groupId": "G1", "userId": "U_new"},
           "message": {"type": "text", "id": "g1", "text": "胸痛"}}])
    check("群組訊息不處理", len(SENT) == n_before)

    print("\n=== Reply token 失效時改用 Push ===")
    async def _reply_fails(path, payload):
        SENT.append((path, payload))
        return path != "/reply"
    line_bot._post = _reply_fails
    SENT.clear()
    hook([text_event("U_new", "我胸痛", "e5")])
    check("先試 reply，失敗後 push", [p for p, _ in SENT] == ["/reply", "/push"])
    line_bot._post = _fake_post

    print("\n=== LOG 有記到綁定 ===")
    log = client.get("/logs/patient/P002", headers=A).json()["items"]
    check("病患 LOG：發出通行碼", any(it["summary"] == "發出 LINE 通行碼" for it in log))
    check("病患 LOG：完成綁定（來源 LINE）",
          any(it["summary"] == "完成 LINE 綁定" and it["who"] == "LINE" for it in log))
    staff = client.get("/logs/staff?account=A001", headers=A).json()["items"]
    check("人員稽核：A001 發碼", any(it["event_type"] == "binding_code_issued" for it in staff))

print(f"\n{'='*40}\n通過 {_pass} / {_pass + _fail}")
db.DB_PATH.unlink(missing_ok=True)
sys.exit(0 if _fail == 0 else 1)
