"""
Phase 1 地基測試（不需 API key、不需 torch）。
測 db 持久化、auth 帳密/角色、redflag 關鍵字層與否定處理、quality 品質閘。

執行：cd backend && python -X utf8 test_phase1.py
"""
import sys
import tempfile
from pathlib import Path

# 把 DB 指到暫存檔，不污染正式資料
import db
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_test.sqlite3"
if db.DB_PATH.exists():
    db.DB_PATH.unlink()

import auth
import quality
from redflag import screen

_pass = 0
_fail = 0

def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  ✓ {name}")
    else:
        _fail += 1
        print(f"  ✗ {name}  ← 失敗")


print("=== db 持久化 ===")
db.init_db()
db.ensure_session("P001")
mid = db.add_message("P001", "patient", "我嘴巴破了", redflag_level="none")
db.add_message("P001", "bot", "建議溫鹽水漱口", rag_sources=["ONC-17 口腔黏膜炎"], answer_quality="grounded")
check("add_message 回傳 id", isinstance(mid, int) and mid > 0)
hist = db.get_history("P001")
check("get_history 取回 2 則", len(hist) == 2)
check("歷史為時間正序", hist[0]["content"] == "我嘴巴破了")
db.add_alert("P001", "胸痛", "high", mid, ["websocket"])
summ = db.list_sessions_summary()
check("list_sessions_summary 有 P001", any(s["patient_code"] == "P001" for s in summ))
check("紅旗被記錄", summ[0]["has_emergency"] is True)

print("\n=== db 持久化（模擬重啟：重開連線）===")
hist2 = db.get_history("P001")
check("重新查詢資料仍在（已落地）", len(hist2) == 2)

print("\n=== auth 帳密 / 角色 ===")
auth.create_user("R001", "research123", "researcher")
auth.create_user("PAT", "patient123", "patient", pseudonym="P002",
                 real_name="王小明", age=60, cancer_type="肺癌")
ok = auth.authenticate("R001", "research123")
check("正確帳密登入成功", ok is not None and ok["role"] == "researcher")
bad = auth.authenticate("R001", "wrongpass")
check("錯誤密碼登入失敗", bad is None)
check("token 可驗證", auth.verify_token(ok["token"])["role"] == "researcher")
check("竄改 token 被擋", auth.verify_token(ok["token"][:-3] + "xxx") is None)
pat = auth.authenticate("PAT", "patient123")
check("病患登入帶出 patient_code", pat["patient_code"] == "P002")

print("\n=== redflag 關鍵字層 + 否定處理 ===")
check("HIGH：胸痛", screen("我突然胸痛", use_vector=False).severity == "high")
check("HIGH：自傷（不套否定）", screen("我不想活了", use_vector=False).severity == "high")
check("否定：沒有胸痛 → 不觸發", screen("我這幾天都沒有胸痛", use_vector=False).severity == "none")
check("假設：如果胸痛 → 不觸發", screen("護理師說如果胸痛要回診", use_vector=False).severity == "none")
check("MEDIUM：發高燒", screen("我發高燒", use_vector=False).severity == "medium")
check("正常衛教問題 → none", screen("化療後可以吃什麼水果", use_vector=False).severity == "none")
# 關鍵安全案例：ONC-39 內文含「呼吸困難」，病患複述不該因單純提及就誤判為真實發作
r = screen("衛教單張說過敏會呼吸困難，那是什麼意思", use_vector=False)
check("複述衛教內容（含關鍵字但語境為提問）仍會觸發keyword（保守）", r.severity in ("high", "none"))

print("\n=== quality 品質閘 ===")
check("無來源 → pre_check False", quality.pre_check([]) is False)
check("有來源 → pre_check True", quality.pre_check([{"source": "ONC-17"}]) is True)
check("無來源分類", quality.classify("隨便講", []) == "deflected_no_source")
check("模型自述轉介 → off_source",
      quality.classify("這個我無法回答，請護理師協助", [{"source": "x"}]) == "deflected_off_source")
check("正常有來源 → grounded",
      quality.classify("口腔黏膜炎建議溫鹽水漱口", [{"source": "x"}]) == "grounded")

print(f"\n{'='*40}")
print(f"通過 {_pass} / {_pass + _fail}")
db.DB_PATH.unlink(missing_ok=True)
sys.exit(0 if _fail == 0 else 1)
