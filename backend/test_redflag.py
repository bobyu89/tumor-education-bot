"""
紅旗 v2 回歸測試：跑 redflag_cases.json（與演示引擎共用的測試集）。

注意：這是開發者撰寫的回歸集（含 2026-11 同儕審查者的實測句），用來防止規則退化；
不能取代由臨床人員獨立撰寫的 held-out 驗證集。
執行：cd backend && python -X utf8 test_redflag.py
"""
import json
from pathlib import Path

from redflag import clauses, normalize, parse_temperature, screen

passed = failed = 0


def check(name: str, cond: bool, detail: str = ""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  ✗ {name} {detail}")


cases = json.loads((Path(__file__).parent / "redflag_cases.json").read_text(encoding="utf-8"))["cases"]

print("=== redflag_cases.json（關鍵字層）===")
by_level = {"high": [0, 0], "medium": [0, 0], "none": [0, 0]}
for c in cases:
    r = screen(c["text"], use_vector=False)
    ok = r.severity == c["expect"] and ("category" not in c or r.category == c["category"])
    by_level[c["expect"]][0] += ok
    by_level[c["expect"]][1] += 1
    check(c["text"], ok, f"→ {r.severity}/{r.category} {r.matched_keywords}，預期 {c['expect']}/{c.get('category', '-')}")
for lvl, (ok, n) in by_level.items():
    print(f"  預期 {lvl:6s}：{ok}/{n}")

print("\n=== 工具函式 ===")
check("NFKC：全形數字轉半形", normalize("耳溫３８．２") == "耳溫38.2")
check("小數點不切子句", clauses("體溫37.6度") == ["體溫37.6度"])
check("轉折詞切子句", clauses("沒有發燒但是胸痛") == ["沒有發燒", "但是胸痛"])
check("『不但』不切", clauses("不但發燒還一直吐") == ["不但發燒還一直吐"])
check("體溫：38.5度", parse_temperature("發燒38.5度") == 38.5)
check("體溫：中文數字三十九度", parse_temperature("燒到三十九度") == 39.0)
check("體溫：環境溫度不算", parse_temperature("外面38度") is None)
check("體溫：非體溫範圍不算", parse_temperature("化療第10天") is None)

# 結構檢查：任何關鍵字本身都不可被子句切分規則切斷（例：「不過」曾把「喘不過氣」切成兩段）
import redflag as _rf
_split = [p for _, p in _rf.HIGH_PHYSICAL + _rf.MEDIUM if not p.startswith("re:") and len(clauses(p)) > 1]
_split += [k for k in _rf.HIGH_SELF_HARM if len(clauses(k)) > 1]
check("沒有關鍵字會被子句切分切斷", not _split, str(_split))

print("\n=== 自傷類不套否定 ===")
check("「我不想活了」", screen("我不想活了", use_vector=False).category == "self_harm")
check("「如果可以我想死」（假設語境仍觸發）", screen("如果可以我想死", use_vector=False).severity == "high")

print("\n=== 向量補充層（嵌入模型可用時才測）===")
import redflag as _rf2
_rf2._load_vectors()
if not _rf2._embedder:
    print("  （嵌入模型未載入，略過）")
else:
    BENIGN = ["我手腳麻麻的", "嘴巴破了好痛", "一直想吐", "我好累", "便秘好幾天", "皮膚很癢", "頭髮一直掉",
              "吃不下飯", "睡不好", "口很乾", "肚子有點脹", "腳有點腫", "手指麻麻的", "好想吐喔",
              "拉肚子三次", "我覺得有點累", "心情不好", "頭有點暈", "關節痠痛"]
    upgraded = [t for t in BENIGN if _rf2._vector_screen(t) is not None]
    check("常見症狀描述不會被向量層升級", not upgraded, str(upgraded))
    for t in ["我快不行了", "喘不上來"]:
        check(f"向量層仍能補抓「{t}」", screen(t).severity == "high")

print(f"\n通過 {passed} / {passed + failed}")
if failed:
    raise SystemExit(1)
