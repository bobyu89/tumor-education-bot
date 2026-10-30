"""
資安修正回歸測試（對應 Obsidian_Vault/專案管理/資安審查.md 的編號）。不需 API key。

執行：cd backend && python -X utf8 test_security.py
"""
import testutil  # 必須最先（設定 DEMO_SEED）
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_sec.sqlite3"
db.DB_PATH.unlink(missing_ok=True)

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main
import rag
import stats
from chat_service import load_profile

# 本檔不測 LLM：檢索一律回空 → 走「無來源轉介」，確保測試不會真的呼叫外部 API
rag.retriever.query = lambda *a, **k: []
from config import settings
from prompt import build_prompt

FRONTEND = (Path(__file__).resolve().parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")

_pass = _fail = 0
def check(name, cond):
    global _pass, _fail
    if cond: _pass += 1; print(f"  ✓ {name}")
    else: _fail += 1; print(f"  ✗ {name}  ← 失敗")


with TestClient(main.app) as client:
    P = testutil.login_headers(client, "P001", "patient123")
    R = testutil.login_headers(client, "R001", "research123")

    print("=== S1 病患對話需研究者權限 ===")
    check("/history 無 token → 401", client.get("/history/P001").status_code == 401)
    check("/history 病患 token → 403", client.get("/history/P001", headers=P).status_code == 403)
    check("/history 研究者 → 200", client.get("/history/P001", headers=R).status_code == 200)
    check("/sessions 無 token → 401", client.get("/sessions").status_code == 401)
    check("/sessions 病患 token → 403", client.get("/sessions", headers=P).status_code == 403)

    print("\n=== S2 WebSocket 需研究者 token，且推播不含真名 ===")
    for label, url in [("無 token", "/ws/nurse"), ("病患 token", f"/ws/nurse?token={P['X-Auth-Token']}")]:
        try:
            with client.websocket_connect(url) as ws:
                ws.receive_text()
            check(f"WS {label} → 應被拒", False)
        except WebSocketDisconnect:
            check(f"WS {label} → 被拒", True)
    with client.websocket_connect(f"/ws/nurse?token={R['X-Auth-Token']}") as ws:
        client.post("/chat", json={"message": "我突然胸痛"}, headers=P)
        alert = ws.receive_json()
    check("研究者 WS 收到警示", alert.get("type") == "EMERGENCY_ALERT")
    check("警示不含 patient_name 欄位", "patient_name" not in alert)
    check("警示不含真名", "測試病患" not in json.dumps(alert, ensure_ascii=False))

    print("\n=== S3 身分只來自 token，body 自報的 patient_id 無效 ===")
    check("/chat 無 token → 401", client.post("/chat", json={"message": "hi"}).status_code == 401)
    check("研究者不能以病患身分發話 → 403",
          client.post("/chat", json={"message": "hi"}, headers=R).status_code == 403)
    client.post("/chat", json={"patient_id": "P999", "message": "冒名測試訊息"}, headers=P)
    check("body 填 P999 → 未寫入 P999", db.get_patient_messages("P999") == [])
    check("訊息記在 token 的 P001 名下",
          any(m["content"] == "冒名測試訊息" for m in db.get_patient_messages("P001")))
    r = client.post("/quiz/ONC-17", headers=P,
                    json={"patient_id": "P999", "answers": {"1": "O"}, "phase": "pre"})
    with db.get_conn() as conn:
        n999 = conn.execute("SELECT COUNT(*) c FROM quiz_results WHERE patient_code='P999'").fetchone()["c"]
    check("測驗 body 填 P999 → 未寫入 P999", r.status_code == 200 and n999 == 0)
    check("/quiz 提交無 token → 401",
          client.post("/quiz/ONC-17", json={"answers": {}, "phase": "pre"}).status_code == 401)

    print("\n=== S4 病患代號格式驗證 ===")
    check("非法代號 → 400", client.get("/history/P001x", headers=R).status_code == 400)
    check("注入字串 → 400",
          client.get("/history/x%27%29%3Balert%281%29", headers=R).status_code == 400)
    check("前端代號改放 data-code，不拼進 onclick",
          "onclick=\"selectPatient(this.dataset.code)\"" in FRONTEND
          and "selectPatient('${s.patient_id}')" not in FRONTEND)
    check("escHtml 跳脫單引號", "replace(/'/g,'&#39;')" in FRONTEND)

    print("\n=== S5 真名不進 LLM prompt ===")
    prompt_text = build_prompt(load_profile("P001"), [])
    check("prompt 不含真名『測試病患』", "測試病患" not in prompt_text)
    check("prompt 不含『姓名』欄位", "姓名" not in prompt_text)

    print("\n=== 前後端契約（儀表板欄位必須對得上）===")
    sess = client.get("/sessions", headers=R).json()
    s0 = next(s for s in sess if s["patient_code"] == "P001")
    check("sessions 欄位", {"patient_code", "red_flags", "has_emergency", "last_message",
                            "message_count"} <= set(s0))
    check("red_flags 欄位", s0["red_flags"] and {"trigger", "level", "ts"} <= set(s0["red_flags"][0]))
    msgs = client.get("/history/P001", headers=R).json()["messages"]
    check("history 角色只有 patient/bot", {m["role"] for m in msgs} <= {"patient", "bot"})
    check("history 帶 redflag_level", all("redflag_level" in m for m in msgs))
    for needle in ["s.patient_code", "f.trigger", "f.level", "msg.role === 'patient'", "msg.redflag_level"]:
        check(f"前端讀取 {needle}", needle in FRONTEND)
    for stale in ["s.patient_id", "f.keywords", "msg.role === 'user'", "getElementById('code-input')"]:
        check(f"前端不再使用舊欄位 {stale}", stale not in FRONTEND)

print("\n=== S6 正式環境不植入預設帳密 ===")
check("登入頁不再顯示示範密碼", "patient123" not in FRONTEND and "research123" not in FRONTEND)
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_sec_boot.sqlite3"
db.DB_PATH.unlink(missing_ok=True)
db.init_db()
settings.DEMO_SEED, settings.ADMIN_PASSWORD = False, ""
main._bootstrap_accounts()
with db.get_conn() as conn:
    n = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
check("DEMO_SEED=false 且無 ADMIN_PASSWORD → 不建任何帳號", n == 0)
settings.ADMIN_ACCOUNT, settings.ADMIN_PASSWORD = "chief", "S3cure-Long-Passphrase!"
main._bootstrap_accounts()
import auth
check("ADMIN_PASSWORD → 建立管理者", (auth.authenticate("chief", "S3cure-Long-Passphrase!") or {}).get("role") == "admin")
check("示範帳號 A001/admin123 不存在", auth.authenticate("A001", "admin123") is None)
db.DB_PATH.unlink(missing_ok=True)

print("\n=== M1 AUTH_SECRET 固定時，重啟後 token 仍有效 ===")
env = {**os.environ, "AUTH_SECRET": "test-fixed-secret-123", "PYTHONUTF8": "1"}
backend = str(Path(__file__).resolve().parent)
mk = subprocess.run([sys.executable, "-c",
                     "import auth;print(auth._make_token('R001','researcher',None))"],
                    cwd=backend, env=env, capture_output=True, text=True)
token = mk.stdout.strip().splitlines()[-1]
vf = subprocess.run([sys.executable, "-c",
                     f"import auth;print(bool(auth.verify_token({token!r})))"],
                    cwd=backend, env=env, capture_output=True, text=True)
check("另一個行程（模擬重啟）驗證得過", vf.stdout.strip().endswith("True"))
import inspect
check("auth 從 settings 讀密鑰（.env 才會生效）", "settings.AUTH_SECRET" in inspect.getsource(auth))

print("\n=== M4 CSV 公式注入 ===")
check("= 開頭加單引號", stats._csv_safe("=HYPERLINK(\"http://x\")").startswith("'="))
check("@ 開頭加單引號", stats._csv_safe("@SUM(A1)") == "'@SUM(A1)")
check("一般文字不變", stats._csv_safe("我嘴巴破了") == "我嘴巴破了")
check("數字不變", stats._csv_safe(7) == 7)

print(f"\n{'='*40}\n通過 {_pass} / {_pass + _fail}")
sys.exit(0 if _fail == 0 else 1)
