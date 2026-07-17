"""
Phase 2 症狀評估測試（不需 API key、不需 torch）。
測偵測、ESAS-r 解析、狀態機、升級門檻、持久化、中止，以及 /chat 的偵測與升級路由。

執行：cd backend && python -X utf8 test_phase2.py
"""
import sys
import tempfile
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.gettempdir()) / "tumor_bot_p2.sqlite3"
db.DB_PATH.unlink(missing_ok=True)
db.init_db()

import assessment

_pass = _fail = 0
def check(name, cond):
    global _pass, _fail
    if cond: _pass += 1; print(f"  ✓ {name}")
    else: _fail += 1; print(f"  ✗ {name}  ← 失敗")


print("=== 症狀偵測 ===")
check("拉肚子 → diarrhea", assessment.detect_symptom("我一直拉肚子").key == "diarrhea")
check("嘴巴破 → mucositis", assessment.detect_symptom("化療後嘴巴破了").key == "mucositis")
check("手麻 → neuropathy", assessment.detect_symptom("我手腳麻麻的").key == "neuropathy")
check("掉髮 → hair_loss", assessment.detect_symptom("頭髮一直掉").key == "hair_loss")
check("純問候 → 無症狀", assessment.detect_symptom("你好啊") is None)

print("\n=== ESAS-r 0–10 解析 ===")
check("數字 8", assessment.parse_severity("大概8分") == 8)
check("超過10→夾為10", assessment.parse_severity("15") == 10)
check("程度詞『受不了』→9", assessment.parse_severity("痛到受不了") == 9)
check("程度詞『有點』→4", assessment.parse_severity("有點不舒服") == 4)
check("程度詞『輕微』→2", assessment.parse_severity("輕微而已") == 2)
check("無法解析→None", assessment.parse_severity("嗯嗯") is None)

print("\n=== 狀態機：無欄位症狀（口腔黏膜炎）低分 → 衛教、不升級 ===")
db.clear_assessment_state("T1")
proto = assessment.detect_symptom("嘴巴破")
r0 = assessment.start("T1", proto)
check("start 回問嚴重度", "幾分" in r0.reply)
check("狀態已持久化", db.get_assessment_state("T1")["step"] == "ask_severity")
r1 = assessment.advance("T1", db.get_assessment_state("T1"), "2分")
check("低分 → 完成且 educate", r1.done and r1.educate and not r1.escalate)
check("低分 → 未升級、分數落庫", db.get_symptom_scores("T1", "mucositis")[0]["score"] == 2)
check("狀態已清除", db.get_assessment_state("T1") is None)

print("\n=== 狀態機：無欄位症狀高分 → 升級 ===")
db.clear_assessment_state("T2")
assessment.start("T2", assessment._BY_KEY["mucositis"])
r = assessment.advance("T2", db.get_assessment_state("T2"), "9")
check("高分（≥7）→ escalate", r.done and r.escalate and r.escalate_level == "medium")
check("回覆提到已通知護理師", "護理師" in r.reply)

print("\n=== 狀態機：有欄位症狀（腹瀉）低分但次數多 → 升級 ===")
db.clear_assessment_state("T3")
assessment.start("T3", assessment._BY_KEY["diarrhea"])
r_sev = assessment.advance("T3", db.get_assessment_state("T3"), "3分")
check("低分但有欄位 → 續問次數", db.get_assessment_state("T3")["step"] == "ask_field")
check("續問句是次數", "幾次" in r_sev.reply)
r_cnt = assessment.advance("T3", db.get_assessment_state("T3"), "一天七次")
check("『一天七次』正確解析為 7（不被『一天』干擾）", r_cnt.extra.get("count") == 7)
check("次數≥6 → 升級（即使分數低）", r_cnt.escalate)
import json as _json
_extra = _json.loads(db.get_symptom_scores("T3", "diarrhea")[0]["extra"])
check("次數落庫於 extra=7", _extra.get("count") == 7)

print("\n=== 中止：評估中說『先不用』→ 放棄、清狀態 ===")
db.clear_assessment_state("T4")
assessment.start("T4", assessment._BY_KEY["fatigue"])
r = assessment.advance("T4", db.get_assessment_state("T4"), "先不用了")
check("中止 → done", r.done)
check("中止 → 狀態清除", db.get_assessment_state("T4") is None)

print("\n=== 落髮永不升級（心理支持）===")
db.clear_assessment_state("T5")
assessment.start("T5", assessment._BY_KEY["hair_loss"])
r = assessment.advance("T5", db.get_assessment_state("T5"), "10")
check("落髮 10 分也不升級", not r.escalate and r.educate)

print("\n=== 解析失敗 → 重問，不卡住 ===")
db.clear_assessment_state("T6")
assessment.start("T6", assessment._BY_KEY["mucositis"])
r = assessment.advance("T6", db.get_assessment_state("T6"), "嗯我不知道")
check("解析不出 → 重問且未完成", (not r.done) and "0 到 10" in r.reply)

# ── /chat 路由（不觸及 LLM）──
print("\n=== /chat 路由：偵測症狀 → 回評估問句 ===")
from fastapi.testclient import TestClient
import main
with TestClient(main.app) as client:
    client.post("/login", json={"account": "P001", "password": "patient123"})
    db.clear_assessment_state("P001")
    r = client.post("/chat", json={"patient_id": "P001", "message": "我嘴巴破了好不舒服"}).json()
    check("偵測 → 回問嚴重度（未進 LLM）", "幾分" in r["response"] and r["sources"] == [])
    r = client.post("/chat", json={"patient_id": "P001", "message": "9分"}).json()
    check("高分 → 升級為 emergency（未進 LLM）", r["is_emergency"] is True and "護理師" in r["response"])

print(f"\n{'='*40}\n通過 {_pass} / {_pass + _fail}")
db.DB_PATH.unlink(missing_ok=True)
sys.exit(0 if _fail == 0 else 1)
