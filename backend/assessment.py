"""
症狀結構化追問引擎（Phase 2）。

病患回報症狀時，先做一段簡短的 ESAS-r 0–10 評估（了解狀況），再依嚴重度：
  嚴重（達門檻）→ 升級為紅旗，通知護理師；
  可自我照護    → 交回 main.py 走該症狀的 RAG 衛教。

混合設計（見 spec）：固定欄位驅動「問什麼」，措辭用溫暖模板（不需 API key、可測試）；
LLM 潤飾問句與自由文字抽取列為 enhancement，本期用規則解析。

狀態存 db.assessment_state（可持久化，重啟後續作）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

import db

ESAS_SEVERE = 7   # ESAS-r ≥ 7 視為嚴重


# ── 症狀協定 ──────────────────────────────────────────────────────
# escalate_count：特有欄位（次數/天數）達此值也升級；None 表示只看 ESAS 分數
@dataclass
class Protocol:
    key: str
    name: str
    keywords: list[str]
    onc_code: str | None = None
    field_key: str | None = None          # 特有欄位（如 count / days）
    field_question: str | None = None
    escalate_count: int | None = None
    escalates: bool = True                 # hair_loss 不升級


PROTOCOLS: list[Protocol] = [
    Protocol("nausea", "噁心嘔吐", ["想吐", "噁心", "嘔吐", "反胃", "吐了"],
             "ONC-21", "count", "這一天下來大概吐了幾次呢？（請給個數字）", escalate_count=5),
    Protocol("diarrhea", "腹瀉", ["拉肚子", "腹瀉", "一直拉", "水便"],
             "ONC-22", "count", "今天大概拉了幾次呢？（請給個數字）", escalate_count=6),
    Protocol("constipation", "便祕", ["便祕", "便秘", "大不出來", "解不出來", "沒排便"],
             "ONC-23", "days", "已經幾天沒有排便了呢？（請給個數字）", escalate_count=3),
    Protocol("mucositis", "口腔黏膜炎", ["嘴巴破", "口腔破", "口腔潰瘍", "嘴破", "黏膜"],
             "ONC-17"),
    Protocol("neuropathy", "手腳麻木刺痛", ["手麻", "腳麻", "手腳麻", "刺痛", "麻木"],
             "ONC-38"),
    Protocol("fatigue", "疲憊", ["很累", "疲憊", "沒力氣", "沒體力", "累到"],
             "ONC-37"),
    Protocol("appetite", "食慾不振", ["吃不下", "沒胃口", "沒食慾", "不想吃"],
             "ONC-21"),
    Protocol("rash", "皮膚紅疹", ["紅疹", "起疹子", "皮膚癢", "長疹子", "皮膚紅"],
             "ONC-40"),
    Protocol("hair_loss", "落髮", ["掉髮", "落髮", "頭髮掉", "頭髮一直掉"],
             "ONC-24", escalates=False),
    Protocol("pain", "疼痛", ["好痛", "很痛", "疼痛", "在痛", "痛得"],
             None),   # 泛用疼痛，無單一 ONC 對應
]

_BY_KEY = {p.key: p for p in PROTOCOLS}

# 病患想中止評估的訊號
_ABORT = ["先不用", "不用了", "算了", "沒事了", "不想說", "跳過", "不用問"]


@dataclass
class AssessmentResult:
    reply: str
    done: bool = False
    escalate: bool = False
    escalate_level: str = "medium"
    educate: bool = False
    symptom: str | None = None
    onc_code: str | None = None
    score: int | None = None
    extra: dict = dc_field(default_factory=dict)


# ── 偵測 ──────────────────────────────────────────────────────────
def detect_symptom(text: str) -> Protocol | None:
    for p in PROTOCOLS:
        if any(kw in text for kw in p.keywords):
            return p
    return None


# ── 解析 ──────────────────────────────────────────────────────────
_CN_DIGIT = {"零": 0, "一": 1, "兩": 2, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(s: str) -> int | None:
    """把中文數字字串轉整數，支援 個位、十、十X、X十、X十X（足夠次數/天數用）。"""
    s = s.strip()
    if not s:
        return None
    if "十" in s:
        left, _, right = s.partition("十")
        tens = _CN_DIGIT.get(left, 1) if left else 1     # 「十」= 10、「二十」=20
        ones = _CN_DIGIT.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(s) == 1 and s in _CN_DIGIT:
        return _CN_DIGIT[s]
    return None


def _extract_number(text: str) -> int | None:
    """泛用取數（用於嚴重度）：優先阿拉伯數字，否則第一個中文數字。"""
    m = re.search(r"\d+", text)
    if m:
        return int(m.group())
    m = re.search(r"[零一二兩三四五六七八九十]+", text)
    if m:
        return _cn_to_int(m.group())
    return None


def _extract_count(text: str, units: str) -> int | None:
    """欄位取數：數字須緊接單位（如『七次』『三天』），避免撞到『一天』的『一』。"""
    # 阿拉伯數字 + 單位
    m = re.search(rf"(\d+)\s*[{units}]", text)
    if m:
        return int(m.group(1))
    # 中文數字 + 單位
    m = re.search(rf"([零一二兩三四五六七八九十]+)\s*[{units}]", text)
    if m:
        return _cn_to_int(m.group(1))
    # 退而求其次：整句取數（可能是純數字回覆）
    return _extract_number(text)


def parse_severity(text: str) -> int | None:
    """0–10。優先數字；否則用程度詞對應。"""
    n = _extract_number(text)
    if n is not None:
        return max(0, min(10, n))
    # 程度詞 → 分數
    if any(w in text for w in ["受不了", "非常", "超級", "最嚴重", "太痛", "崩潰", "撐不住"]):
        return 9
    if any(w in text for w in ["很", "蠻", "挺", "滿", "厲害", "難受"]):
        return 7
    if any(w in text for w in ["有點", "一點", "普通", "還好", "普普"]):
        return 4
    if any(w in text for w in ["輕微", "不太", "不會很", "還好啦", "沒很"]):
        return 2
    if any(w in text for w in ["不會", "沒有", "還好沒事", "不痛"]):
        return 0
    return None


# ── 狀態機 ────────────────────────────────────────────────────────
def start(patient_code: str, proto: Protocol) -> AssessmentResult:
    db.set_assessment_state(patient_code, proto.key, "ask_severity", {})
    db.log_event("assessment_start", patient_code, {"symptom": proto.key})
    q = (f"聽起來您有「{proto.name}」的不舒服，我先簡單了解一下狀況。"
         f"如果 0 分是完全不會、10 分是最嚴重，您會給幾分呢？")
    return AssessmentResult(reply=q, symptom=proto.key)


def advance(patient_code: str, state: dict, message: str) -> AssessmentResult:
    proto = _BY_KEY.get(state["symptom"])
    if proto is None:                       # 資料異常，放棄
        db.clear_assessment_state(patient_code)
        return AssessmentResult(reply="", done=True)

    # 中止意圖
    if any(w in message for w in _ABORT):
        db.clear_assessment_state(patient_code)
        db.log_event("assessment_abort", patient_code, {"symptom": proto.key})
        return AssessmentResult(reply="好的，那我們先不談這個。您有其他想了解的嗎？",
                                done=True, symptom=proto.key)

    data = state["data"]

    if state["step"] == "ask_severity":
        score = parse_severity(message)
        if score is None:
            return AssessmentResult(
                reply="方便用 0 到 10 的數字告訴我嗎？（0＝完全不會，10＝最嚴重）",
                symptom=proto.key)
        data["score"] = score
        # 有特有欄位 → 續問；否則完成
        if proto.field_key:
            db.set_assessment_state(patient_code, proto.key, "ask_field", data)
            return AssessmentResult(reply=proto.field_question, symptom=proto.key)
        return _complete(patient_code, proto, data)

    if state["step"] == "ask_field":
        units = "次回趟遍" if proto.field_key == "count" else "天日"
        cnt = _extract_count(message, units)
        if cnt is not None:
            data[proto.field_key] = cnt
        # 欄位可有可無，抓不到就略過，不卡病患
        return _complete(patient_code, proto, data)

    # 未知步驟，重置
    db.clear_assessment_state(patient_code)
    return AssessmentResult(reply="", done=True)


def _complete(patient_code: str, proto: Protocol, data: dict) -> AssessmentResult:
    score = data.get("score")
    extra = {k: v for k, v in data.items() if k != "score"}

    # 落庫
    db.add_symptom_score(patient_code, proto.key, score, extra, source="patient_initiated")
    db.clear_assessment_state(patient_code)

    # 升級判斷
    over_score = score is not None and score >= ESAS_SEVERE
    over_count = (proto.escalate_count is not None
                  and proto.field_key in extra
                  and extra[proto.field_key] >= proto.escalate_count)
    escalate = proto.escalates and (over_score or over_count)

    db.log_event("assessment_complete", patient_code,
                 {"symptom": proto.key, "score": score, "extra": extra, "escalate": escalate})

    if escalate:
        reply = (f"您的「{proto.name}」聽起來比較嚴重（嚴重度 {score}/10"
                 f"{'，' + _fmt_field(proto, extra) if _fmt_field(proto, extra) else ''}）。"
                 f"我已經幫您記錄並通知護理師，請稍候，也可以直接按呼叫鈴。")
        return AssessmentResult(reply=reply, done=True, escalate=True,
                                escalate_level="medium", symptom=proto.key,
                                onc_code=proto.onc_code, score=score, extra=extra)

    # 未達門檻 → 交回 main.py 走 RAG 衛教
    return AssessmentResult(reply="", done=True, educate=True, symptom=proto.key,
                            onc_code=proto.onc_code, score=score, extra=extra)


def _fmt_field(proto: Protocol, extra: dict) -> str:
    if proto.field_key and proto.field_key in extra:
        unit = {"count": "次", "days": "天"}.get(proto.field_key, "")
        label = {"count": "次數", "days": "天數"}.get(proto.field_key, proto.field_key)
        return f"{label}約 {extra[proto.field_key]} {unit}"
    return ""
