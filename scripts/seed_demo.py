"""
植入「模擬案例」供演示（不需 API key）。

建立 5 位虛構病患（P001–P005）、研究者 R001、管理者 A001，並回填過去兩週的
對話紀錄、症狀分數時間序列、紅旗警示、知識測驗前後測與稽核事件，
讓研究者儀表板（/sessions、/stats/overview、CSV 匯出）一打開就有內容可看。

所有人名皆為虛構並標註「（模擬）」；資料只寫入本機 SQLite（data/，不進版控）。

執行（專案根目錄）：
    python -X utf8 scripts/seed_demo.py            # 只在帳號不存在時新增
    python -X utf8 scripts/seed_demo.py --reset    # 先刪掉 data/tumor_bot.sqlite3 再植入

之後啟動後端：cd backend && python -X utf8 -m uvicorn main:app --port 8000
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import db      # noqa: E402
import auth    # noqa: E402

NOW = datetime.now(timezone.utc)
DEMO_MODEL = "demo-seed"      # messages.model_id / provider 標記，研究匯出時可與真實 LLM 區分
DEMO_PROVIDER = "seed"

# ── 帳號 ──────────────────────────────────────────────────────────
PATIENTS = [
    dict(account="P001", password="patient123", real_name="陳先生（模擬）", age=58, gender="男",
         cancer_type="口腔癌", diagnosis=["口腔癌", "化學治療中"],
         medications=["Cisplatin", "5-Fluorouracil"], education_level="general"),
    dict(account="P002", password="patient123", real_name="王女士（模擬）", age=72, gender="女",
         cancer_type="大腸癌", diagnosis=["大腸癌第三期", "術後輔助化療"],
         medications=["Oxaliplatin", "5-Fluorouracil", "Leucovorin"], education_level="simple"),
    dict(account="P003", password="patient123", real_name="林小姐（模擬）", age=45, gender="女",
         cancer_type="乳癌", diagnosis=["乳癌第二期", "術前化療"],
         medications=["Doxorubicin", "Cyclophosphamide", "Paclitaxel"], education_level="general"),
    dict(account="P004", password="patient123", real_name="張先生（模擬）", age=63, gender="男",
         cancer_type="肺癌", diagnosis=["非小細胞肺癌第三期"],
         medications=["Carboplatin", "Paclitaxel"], education_level="detailed"),
    dict(account="P005", password="patient123", real_name="黃先生（模擬）", age=35, gender="男",
         cancer_type="淋巴瘤", diagnosis=["瀰漫性大 B 細胞淋巴瘤"],
         medications=["Rituximab", "Cyclophosphamide", "Doxorubicin", "Vincristine", "Prednisolone"],
         education_level="general"),
]
STAFF = [("R001", "research123", "researcher"), ("A001", "admin123", "admin")]


def _ts(days_ago: float, hour: int = 10, minute: int = 0) -> str:
    t = (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return t.isoformat()


# ── 低階寫入（可指定時間戳；db.py 的 add_* 一律用現在時間，回填歷史需自行指定）──
def _msg(conn, code, role, content, ts, *, redflag_level="none", redflag_terms=None,
         rag_sources=None, answer_quality=None, llm=False) -> int:
    cur = conn.execute(
        """INSERT INTO messages(patient_code, role, content, ts, redflag_level, redflag_terms,
                                rag_sources, model_id, provider, prompt_tokens, completion_tokens,
                                answer_quality)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (code, role, content, ts, redflag_level,
         json.dumps(redflag_terms or [], ensure_ascii=False),
         json.dumps(rag_sources or [], ensure_ascii=False),
         DEMO_MODEL if llm else None, DEMO_PROVIDER if llm else None,
         900 if llm else None, 180 if llm else None, answer_quality),
    )
    return cur.lastrowid


def _alert(conn, code, trigger, level, msg_id, ts, channels=("websocket",), ack_by=None):
    conn.execute(
        """INSERT INTO alerts(patient_code, trigger, level, source_message_id, ts,
                              notified_channels, ack_by, ack_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (code, trigger, level, msg_id, ts, json.dumps(list(channels)), ack_by,
         ts if ack_by else None),
    )


def _score(conn, code, symptom, score, ts, extra=None):
    conn.execute(
        "INSERT INTO symptom_scores(patient_code, esas_symptom, score, extra, ts, source) "
        "VALUES (?,?,?,?,?,?)",
        (code, symptom, score, json.dumps(extra or {}, ensure_ascii=False), ts, "patient_initiated"),
    )


def _quiz(conn, code, onc, phase, score, ts):
    conn.execute("INSERT INTO quiz_results(patient_code, onc_code, phase, score, ts) VALUES (?,?,?,?,?)",
                 (code, onc, phase, score, ts))


def _event(conn, code, event_type, payload, ts):
    conn.execute("INSERT INTO events(ts, patient_code, event_type, payload) VALUES (?,?,?,?)",
                 (ts, code, event_type, json.dumps(payload, ensure_ascii=False)))


def _session(conn, code, started, ended=None):
    conn.execute("INSERT INTO sessions(patient_code, started_at, ended_at) VALUES (?,?,?)",
                 (code, started, ended))


# ── 對話腳本：每位病患一段有代表性的歷程 ─────────────────────────
def seed_p001(conn):
    """一般衛教問答（口腔黏膜炎）→ 有來源、品質 grounded；測驗前後測。"""
    c = "P001"
    _session(conn, c, _ts(12, 9), _ts(12, 9, 20))
    _msg(conn, c, "patient", "我化療之後嘴巴一直破，很痛怎麼辦？", _ts(12, 9, 2), redflag_level="none")
    _msg(conn, c, "bot", "聽起來您有「口腔黏膜炎」的不舒服，我先簡單了解一下狀況。"
         "如果 0 分是完全不會、10 分是最嚴重，您會給幾分呢？", _ts(12, 9, 2), answer_quality="assessment")
    _msg(conn, c, "patient", "大概 4 分", _ts(12, 9, 3))
    _score(conn, c, "mucositis", 4, _ts(12, 9, 3))
    _msg(conn, c, "bot",
         "了解了，您的「口腔黏膜炎」嚴重度是 4/10。以下提供一些照護建議：\n\n"
         "口腔黏膜炎是化療常見的副作用，通常在治療後 5–10 天出現。建議：\n"
         "• 每天用溫鹽水或醫師開立的漱口水漱口 4–6 次，尤其是餐後與睡前。\n"
         "• 使用軟毛牙刷，避免辛辣、過燙、過酸或太硬的食物。\n"
         "• 保持嘴唇濕潤，可塗抹無刺激的護唇膏。\n"
         "若疼痛影響進食、口腔出現白斑或出血，請告知護理師評估是否需要止痛或抗黴菌藥物。",
         _ts(12, 9, 4), rag_sources=["ONC-17 口腔黏膜炎"], answer_quality="grounded", llm=True)
    _msg(conn, c, "patient", "漱口水可以用市面上有酒精的那種嗎？", _ts(12, 9, 6))
    _msg(conn, c, "bot",
         "不建議喔。含酒精的漱口水會刺激已經受損的口腔黏膜，讓疼痛加重。"
         "請改用溫鹽水（一杯溫開水加約半茶匙鹽）或醫師開立的無酒精漱口水。"
         "今天就可以先把家裡含酒精的漱口水收起來。",
         _ts(12, 9, 7), rag_sources=["ONC-17 口腔黏膜炎"], answer_quality="grounded", llm=True)
    _quiz(conn, c, "ONC-17", "pre", 3, _ts(12, 9, 10))
    _quiz(conn, c, "ONC-17", "post", 5, _ts(5, 15, 30))
    _event(conn, c, "quiz_submit", {"onc_code": "ONC-17", "phase": "pre", "score": 3, "total": 6}, _ts(12, 9, 10))
    _event(conn, c, "quiz_submit", {"onc_code": "ONC-17", "phase": "post", "score": 5, "total": 6}, _ts(5, 15, 30))


def seed_p002(conn):
    """72 歲長者（simple 語氣）：腹瀉評估未達門檻 → 衛教；症狀分數逐日改善；周邊神經病變提問。"""
    c = "P002"
    # 第 1 次：腹瀉 5 分、一天五次（未達 ≥6 次 / ≥7 分門檻）
    _session(conn, c, _ts(10, 14), _ts(10, 14, 15))
    _msg(conn, c, "patient", "我一直拉肚子", _ts(10, 14, 1), redflag_level="medium", redflag_terms=["一直拉肚子"])
    _msg(conn, c, "bot", "聽起來您有「腹瀉」的不舒服，我先簡單了解一下狀況。"
         "如果 0 分是完全不會、10 分是最嚴重，您會給幾分呢？", _ts(10, 14, 1), answer_quality="assessment")
    _msg(conn, c, "patient", "5分", _ts(10, 14, 2))
    _msg(conn, c, "bot", "今天大概拉了幾次呢？（請給個數字）", _ts(10, 14, 2), answer_quality="assessment")
    _msg(conn, c, "patient", "一天五次", _ts(10, 14, 3))
    _score(conn, c, "diarrhea", 5, _ts(10, 14, 3), {"count": 5})
    m = _msg(conn, c, "bot",
             "了解了，您的「腹瀉」嚴重度是 5/10。以下提供一些照護建議：\n\n"
             "拉肚子的時候，身體的水分會流失得很快。\n"
             "• 少量多餐，先吃清淡的，像白粥、白吐司。\n"
             "• 每次拉完都補一點水或運動飲料。\n"
             "一天超過六次、有血、或是頭暈站不穩，要馬上告訴護理師喔。您做得很好，慢慢會改善的。",
             _ts(10, 14, 4), rag_sources=["ONC-22 腹瀉"], answer_quality="grounded", llm=True)
    _alert(conn, c, "一直拉肚子", "medium", m, _ts(10, 14, 4), channels=(), ack_by="R001")
    _event(conn, c, "assessment_complete", {"symptom": "diarrhea", "score": 5, "extra": {"count": 5}, "escalate": False}, _ts(10, 14, 3))
    # 第 2、3 次：回報改善（時間序列可畫趨勢）
    _session(conn, c, _ts(7, 10), _ts(7, 10, 8))
    _msg(conn, c, "patient", "還是有拉肚子，但好一點了", _ts(7, 10, 1))
    _msg(conn, c, "bot", "聽起來您有「腹瀉」的不舒服，我先簡單了解一下狀況。"
         "如果 0 分是完全不會、10 分是最嚴重，您會給幾分呢？", _ts(7, 10, 1), answer_quality="assessment")
    _msg(conn, c, "patient", "3", _ts(7, 10, 2))
    _msg(conn, c, "bot", "今天大概拉了幾次呢？（請給個數字）", _ts(7, 10, 2), answer_quality="assessment")
    _msg(conn, c, "patient", "三次", _ts(7, 10, 3))
    _score(conn, c, "diarrhea", 3, _ts(7, 10, 3), {"count": 3})
    _msg(conn, c, "bot", "了解了，您的「腹瀉」嚴重度是 3/10。以下提供一些照護建議：\n\n"
         "有進步喔！請繼續少量多餐、多補水。如果再變嚴重再跟我說。",
         _ts(7, 10, 4), rag_sources=["ONC-22 腹瀉"], answer_quality="grounded", llm=True)
    _score(conn, c, "diarrhea", 2, _ts(3, 11, 0), {"count": 2})
    # 周邊神經病變（Oxaliplatin 常見）
    _session(conn, c, _ts(2, 16), _ts(2, 16, 10))
    _msg(conn, c, "patient", "手指麻麻的，碰到冰的東西會刺痛", _ts(2, 16, 1))
    _msg(conn, c, "bot", "聽起來您有「手腳麻木刺痛」的不舒服，我先簡單了解一下狀況。"
         "如果 0 分是完全不會、10 分是最嚴重，您會給幾分呢？", _ts(2, 16, 1), answer_quality="assessment")
    _msg(conn, c, "patient", "有點麻，4分", _ts(2, 16, 2))
    _score(conn, c, "neuropathy", 4, _ts(2, 16, 2))
    _msg(conn, c, "bot", "了解了，您的「手腳麻木刺痛」嚴重度是 4/10。以下提供一些照護建議：\n\n"
         "這是化療藥物影響神經的常見反應。\n"
         "• 天冷或碰冰水時戴手套，避免直接拿冰的東西。\n"
         "• 洗澡、洗碗前先用手肘試水溫，避免燙傷。\n"
         "走路時穿包鞋、不要赤腳，家裡的地板保持乾燥。如果麻到拿不穩東西或走路不穩，請告訴醫師。",
         _ts(2, 16, 3), rag_sources=["ONC-38 周邊神經病變"], answer_quality="grounded", llm=True)
    _quiz(conn, c, "ONC-22", "pre", 2, _ts(10, 14, 20))
    _quiz(conn, c, "ONC-22", "post", 5, _ts(3, 11, 10))


def seed_p003(conn):
    """落髮（永不升級，心理支持）；噁心嘔吐評估達門檻 → 升級 medium 警示；症狀改善序列。"""
    c = "P003"
    _session(conn, c, _ts(9, 20), _ts(9, 20, 12))
    _msg(conn, c, "patient", "頭髮一直掉，看到枕頭上都是頭髮好難過", _ts(9, 20, 1))
    _msg(conn, c, "bot", "聽起來您有「落髮」的不舒服，我先簡單了解一下狀況。"
         "如果 0 分是完全不會、10 分是最嚴重，您會給幾分呢？", _ts(9, 20, 1), answer_quality="assessment")
    _msg(conn, c, "patient", "8分，真的很困擾", _ts(9, 20, 2))
    _score(conn, c, "hair_loss", 8, _ts(9, 20, 2))
    _msg(conn, c, "bot", "了解了，您的「落髮」嚴重度是 8/10。以下提供一些照護建議：\n\n"
         "看到頭髮大量脫落確實很難受，這種心情很正常。想讓您知道：\n"
         "• 化療引起的落髮多數是暫時的，通常在療程結束後 1–3 個月開始長回。\n"
         "• 洗頭用溫水與溫和洗髮精，輕柔按摩，避免染燙與高溫吹整。\n"
         "• 可以考慮先剪短，或準備帽子、頭巾、假髮，院內有假髮租借資訊可詢問護理師。\n"
         "您願意的話，也可以跟我聊聊這段時間的心情。",
         _ts(9, 20, 3), rag_sources=["ONC-24 毛髮脫落"], answer_quality="grounded", llm=True)
    # 噁心嘔吐 8 分、一天 6 次 → 升級
    _session(conn, c, _ts(6, 8), _ts(6, 8, 10))
    m0 = _msg(conn, c, "patient", "打完針之後一直想吐，吃什麼都吐出來", _ts(6, 8, 1))
    _msg(conn, c, "bot", "聽起來您有「噁心嘔吐」的不舒服，我先簡單了解一下狀況。"
         "如果 0 分是完全不會、10 分是最嚴重，您會給幾分呢？", _ts(6, 8, 1), answer_quality="assessment")
    _msg(conn, c, "patient", "8", _ts(6, 8, 2))
    _msg(conn, c, "bot", "這一天下來大概吐了幾次呢？（請給個數字）", _ts(6, 8, 2), answer_quality="assessment")
    _msg(conn, c, "patient", "吐了六次", _ts(6, 8, 3))
    _score(conn, c, "nausea", 8, _ts(6, 8, 3), {"count": 6})
    _msg(conn, c, "bot", "您的「噁心嘔吐」聽起來比較嚴重（嚴重度 8/10，次數約 6 次）。"
         "我已經幫您記錄並通知護理師，請稍候，也可以直接按呼叫鈴。",
         _ts(6, 8, 3), answer_quality="assessment_escalated")
    _alert(conn, c, "nausea:8", "medium", m0, _ts(6, 8, 3), ack_by="R001")
    _event(conn, c, "assessment_complete", {"symptom": "nausea", "score": 8, "extra": {"count": 6}, "escalate": True}, _ts(6, 8, 3))
    # 用藥調整後改善
    _score(conn, c, "nausea", 6, _ts(4, 9, 0), {"count": 3})
    _score(conn, c, "nausea", 3, _ts(1, 9, 0), {"count": 1})
    _session(conn, c, _ts(1, 9), _ts(1, 9, 6))
    _msg(conn, c, "patient", "換了止吐藥之後好很多，想問吃東西要注意什麼", _ts(1, 9, 1))
    _msg(conn, c, "bot", "很高興聽到您改善了！飲食上建議：\n"
         "• 少量多餐，一天 5–6 餐，避免空腹。\n"
         "• 選擇清淡、溫度偏涼的食物，避免油炸與味道重的菜。\n"
         "• 吃飯時不要喝太多湯水，餐後不要馬上躺平。\n"
         "• 想吐的時候可以按壓手腕內側的內關穴 1–2 分鐘。\n"
         "止吐藥請依醫師指示時間服用，不要等吐了才吃。",
         _ts(1, 9, 2), rag_sources=["ONC-21 噁心嘔吐食慾不振", "ONC-35 穴位按摩"],
         answer_quality="grounded", llm=True)
    _quiz(conn, c, "ONC-21", "pre", 3, _ts(6, 8, 20))
    _quiz(conn, c, "ONC-21", "post", 6, _ts(1, 9, 10))


def seed_p004(conn):
    """detailed 語氣：白血球低下衛教；發高燒 medium；胸痛 → HIGH 紅旗短路（不進 LLM）。"""
    c = "P004"
    _session(conn, c, _ts(8, 11), _ts(8, 11, 10))
    _msg(conn, c, "patient", "白血球低下的時候，飲食和生活上要注意什麼？", _ts(8, 11, 1))
    _msg(conn, c, "bot",
         "化療後白血球（尤其是嗜中性球）通常在第 7–14 天降到最低點，感染風險最高。重點如下：\n"
         "1. 感染預防：勤洗手、避免人多密閉場所、外出戴口罩；避免接觸生病的人與寵物排泄物。\n"
         "2. 飲食：所有食物充分煮熟，避免生食（生魚片、生菜沙拉、未殺菌乳製品）；水果去皮後食用。\n"
         "3. 監測：每日量體溫，體溫 ≥ 38°C 持續 1 小時或單次 ≥ 38.3°C 即為「發燒性嗜中性球低下」，需立即就醫。\n"
         "4. 皮膚與口腔：避免外傷，注射處保持清潔，每日檢查口腔與肛門周圍有無破損。\n"
         "延伸：您使用的 Carboplatin + Paclitaxel 屬於中度骨髓抑制風險方案，醫師可能視血球數值安排 G-CSF。",
         _ts(8, 11, 3), rag_sources=["ONC-25 白血球低下"], answer_quality="grounded", llm=True)
    # 發高燒 → medium
    _session(conn, c, _ts(5, 22), _ts(5, 22, 5))
    m1 = _msg(conn, c, "patient", "我今晚發高燒到 38.6 度，有點畏寒", _ts(5, 22, 1),
              redflag_level="medium", redflag_terms=["發高燒"])
    _msg(conn, c, "bot",
         "化療後發燒需要特別謹慎，因為可能是白血球低下期的感染徵兆。\n"
         "• 體溫 ≥ 38°C 且您正處於化療後 7–14 天，建議立即聯絡醫療團隊或至急診。\n"
         "• 就醫前不要自行服用退燒藥，以免掩蓋病情；記錄體溫與發燒時間。\n"
         "• 觀察是否伴隨畏寒、發抖、喉嚨痛、排尿灼熱或注射處紅腫。\n"
         "我已幫您標記這則訊息，請立刻聯絡護理站或前往急診。",
         _ts(5, 22, 2), rag_sources=["ONC-25 白血球低下"], answer_quality="grounded", llm=True)
    _alert(conn, c, "發高燒", "medium", m1, _ts(5, 22, 2), channels=(), ack_by="R001")
    # 胸痛 → HIGH：紅旗短路，固定回覆，不進 RAG/LLM
    _session(conn, c, _ts(2, 6), _ts(2, 6, 3))
    m2 = _msg(conn, c, "patient", "我現在胸痛而且喘不過氣", _ts(2, 6, 1),
              redflag_level="high", redflag_terms=["胸痛", "喘不過氣"])
    _msg(conn, c, "bot", "這個狀況需要馬上讓護理師或醫師知道，請按旁邊的呼叫鈴，"
         "我已經同時通知護理站了。若情況緊急請直接撥打護理站電話或119。",
         _ts(2, 6, 1), answer_quality="redflag_shortcut")
    _alert(conn, c, "胸痛；喘不過氣", "high", m2, _ts(2, 6, 1), ack_by="R001")
    _event(conn, c, "redflag_high", {"terms": ["胸痛", "喘不過氣"], "via": "keyword"}, _ts(2, 6, 1))
    _quiz(conn, c, "ONC-25", "pre", 4, _ts(8, 11, 15))
    _quiz(conn, c, "ONC-25", "post", 6, _ts(4, 10, 0))


def seed_p005(conn):
    """知識庫外問題 → 轉介（不編造）；自傷語意 → HIGH；疲憊評估 → 衛教。"""
    c = "P005"
    _session(conn, c, _ts(11, 19), _ts(11, 19, 5))
    _msg(conn, c, "patient", "化療期間可以去健身房重訓嗎？", _ts(11, 19, 1))
    _msg(conn, c, "bot", "這個問題我需要請護理師為您解答，我先幫您記錄下來，護理師會盡快回覆您。",
         _ts(11, 19, 1), answer_quality="deflected_no_source")
    # 自傷語意 → HIGH（不套否定規則、不進 LLM）
    _session(conn, c, _ts(4, 23), _ts(4, 23, 4))
    m = _msg(conn, c, "patient", "最近每天都好累，覺得撐不下去了", _ts(4, 23, 1),
             redflag_level="high", redflag_terms=["撐不下去"])
    _msg(conn, c, "bot", "這個狀況需要馬上讓護理師或醫師知道，請按旁邊的呼叫鈴，"
         "我已經同時通知護理站了。若情況緊急請直接撥打護理站電話或119。",
         _ts(4, 23, 1), answer_quality="redflag_shortcut")
    _alert(conn, c, "撐不下去", "high", m, _ts(4, 23, 1), ack_by="R001")
    _event(conn, c, "redflag_high", {"terms": ["撐不下去"], "via": "keyword"}, _ts(4, 23, 1))
    # 護理師關懷後，隔天回報疲憊
    _session(conn, c, _ts(3, 10), _ts(3, 10, 8))
    _msg(conn, c, "patient", "昨天謝謝護理師來看我。我還是很累沒力氣，想知道怎麼辦", _ts(3, 10, 1))
    _msg(conn, c, "bot", "聽起來您有「疲憊」的不舒服，我先簡單了解一下狀況。"
         "如果 0 分是完全不會、10 分是最嚴重，您會給幾分呢？", _ts(3, 10, 1), answer_quality="assessment")
    _msg(conn, c, "patient", "6分吧", _ts(3, 10, 2))
    _score(conn, c, "fatigue", 6, _ts(3, 10, 2))
    _msg(conn, c, "bot", "了解了，您的「疲憊」嚴重度是 6/10。以下提供一些照護建議：\n\n"
         "癌因性疲憊跟一般的累不一樣，休息不一定能完全恢復，這不是您不夠努力。\n"
         "• 把一天的活動排在精神最好的時段，重要的事先做。\n"
         "• 午睡不超過 30 分鐘，避免影響夜間睡眠。\n"
         "• 身體允許時每天散步 10–20 分鐘，規律的輕度活動反而能減輕疲憊。\n"
         "• 疲憊也可能與貧血、睡眠或情緒有關，回診時請跟醫師提到這件事。\n"
         "如果又出現撐不下去的念頭，請隨時告訴我們，護理師與心理師都在。",
         _ts(3, 10, 3), rag_sources=["ONC-37 疲憊", "ONC-19 紅血球減少"], answer_quality="grounded", llm=True)
    _score(conn, c, "fatigue", 7, _ts(6, 10, 0))     # 更早一次自評（時間序列用）
    _score(conn, c, "fatigue", 5, _ts(1, 10, 0))
    _quiz(conn, c, "ONC-37", "pre", 2, _ts(3, 10, 15))
    _quiz(conn, c, "ONC-37", "post", 4, _ts(1, 10, 10))


# ── 主流程 ────────────────────────────────────────────────────────
def _existing_accounts(conn) -> set[str]:
    return {r["account"] for r in conn.execute("SELECT account FROM users")}


def main():
    ap = argparse.ArgumentParser(description="植入模擬案例")
    ap.add_argument("--reset", action="store_true", help="先刪除 data/tumor_bot.sqlite3")
    args = ap.parse_args()

    if args.reset and db.DB_PATH.exists():
        for suffix in ("", "-wal", "-shm"):
            Path(str(db.DB_PATH) + suffix).unlink(missing_ok=True)
        print(f"[seed] 已刪除 {db.DB_PATH}")

    db.init_db()
    with db.get_conn() as conn:
        existing = _existing_accounts(conn)

    seeders = {"P001": seed_p001, "P002": seed_p002, "P003": seed_p003,
               "P004": seed_p004, "P005": seed_p005}
    created = []
    for p in PATIENTS:
        if p["account"] in existing:
            print(f"[seed] {p['account']} 已存在，略過")
            continue
        p = dict(p)
        acc, pw = p.pop("account"), p.pop("password")
        auth.create_user(acc, pw, "patient", pseudonym=acc, **p)
        with db.get_conn() as conn:
            seeders[acc](conn)
            _event(conn, acc, "login", {"account": acc, "role": "patient"}, _ts(0, 8, 0))
        created.append(acc)
    for acc, pw, role in STAFF:
        if acc in existing:
            print(f"[seed] {acc} 已存在，略過")
            continue
        auth.create_user(acc, pw, role)
        created.append(acc)

    with db.get_conn() as conn:
        n = {t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
             for t in ("users", "messages", "alerts", "symptom_scores", "quiz_results", "events")}
    print(f"[seed] 本次新增帳號：{', '.join(created) or '（無）'}")
    print(f"[seed] 資料庫 {db.DB_PATH}")
    print("[seed] 目前筆數：" + "、".join(f"{k}={v}" for k, v in n.items()))
    print("[seed] 病患帳號 P001–P005 密碼皆為 patient123；R001/research123；A001/admin123")


if __name__ == "__main__":
    main()
