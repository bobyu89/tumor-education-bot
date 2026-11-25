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
import departments
from safety_text import escalation_reply

ESAS_SEVERE = 7   # ESAS-r ≥ 7 視為嚴重


# ── 症狀協定 ──────────────────────────────────────────────────────
# 協定內容放在各科別的設定檔：knowledge/<科別>/manifest.json 的 assessment 區塊（不寫死在程式裡）。
# escalate_count：特有欄位（次數/天數）達此值也升級；None 表示只看 ESAS 分數
@dataclass
class Protocol:
    key: str
    name: str
    keywords: list[str]
    doc_code: str | None = None            # 評估後衛教要鎖定的單張代碼
    field_key: str | None = None          # 特有欄位（如 count / days）
    field_question: str | None = None
    escalate_count: int | None = None
    escalates: bool = True                 # 例如落髮不升級
    high_count: int | None = None          # 特有欄位達此值 → 升級為 HIGH（例：CTCAE v5.0 grade 3）

    @classmethod
    def from_dict(cls, d: dict) -> "Protocol":
        return cls(key=d["key"], name=d["name"], keywords=list(d["keywords"]),
                   doc_code=d.get("doc_code"), field_key=d.get("field"),
                   field_question=d.get("question"), escalate_count=d.get("escalate_count"),
                   escalates=d.get("escalates", True), high_count=d.get("high_count"))


_protocol_cache: dict[int, list[Protocol]] = {}


def protocols(dept: str | None = None) -> list[Protocol]:
    """科別的症狀評估協定（未指定用預設科別）。科別設定重新載入時自動更新。"""
    d = departments.get(dept)
    key = id(d)
    if key not in _protocol_cache:
        _protocol_cache[key] = [Protocol.from_dict(x) for x in d.assessment]
    return _protocol_cache[key]


def get_protocol(key: str, dept: str | None = None) -> Protocol | None:
    return next((p for p in protocols(dept) if p.key == key), None)


# 與舊程式相容：預設科別的協定
PROTOCOLS: list[Protocol] = protocols()
_BY_KEY = {p.key: p for p in PROTOCOLS}

# 病患想中止評估的訊號
_ABORT = ["先不用", "不用了", "算了", "沒事了", "不想說", "跳過", "不用問"]

# 可點選的回覆：長輩不用自己打數字（2026-11 同儕審查，護理審查 minor）。
# LINE Quick Reply 上限 13 個按鈕；最後一顆一律是「先不用了」（命中 _ABORT，可隨時中止評估）。
STOP_CHOICE = "先不用了"
SEVERITY_CHOICES = [str(i) for i in range(11)] + [STOP_CHOICE]


def field_choices(proto: "Protocol") -> list[str]:
    if proto.field_key == "count":
        return [f"{i}次" for i in range(1, 10)] + ["10次以上", STOP_CHOICE]
    if proto.field_key == "days":
        return [f"{i}天" for i in range(1, 7)] + ["7天以上", STOP_CHOICE]
    return []


@dataclass
class AssessmentResult:
    reply: str
    done: bool = False
    escalate: bool = False
    escalate_level: str = "medium"
    educate: bool = False
    symptom: str | None = None
    doc_code: str | None = None
    score: int | None = None
    extra: dict = dc_field(default_factory=dict)
    choices: list[str] = dc_field(default_factory=list)   # 可點選的回覆（LINE Quick Reply／網頁按鈕）


# ── 偵測 ──────────────────────────────────────────────────────────
# 資訊型提問的線索：問預防、假設、原因，而不是在描述當下的不舒服。
# 例：「化療前要怎麼預防想吐」不該被反問「您會給幾分」（2026-11 同儕審查，護理審查第 6 點）
INFO_CUES = ["預防", "避免", "會不會", "化療前", "打針前", "如果", "萬一", "要是", "假如",
             "是什麼", "為什麼", "原因", "什麼時候會", "多久會"]
# 描述當下狀況的線索：有這些詞就仍然進症狀評估（例：「我一直拉肚子要怎麼避免脫水」）
PRESENT_CUES = ["一直", "現在", "已經", "今天", "昨天", "這幾天", "好幾天", "剛剛", "目前", "正在"]


def is_info_question(text: str) -> bool:
    return any(c in text for c in INFO_CUES) and not any(c in text for c in PRESENT_CUES)


def detect_symptom(text: str, dept: str | None = None) -> Protocol | None:
    if is_info_question(text):
        return None                       # 交給一般衛教（RAG），不進 0–10 分評估
    for p in protocols(dept):
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
    return AssessmentResult(reply=q, symptom=proto.key, choices=SEVERITY_CHOICES)


def advance(patient_code: str, state: dict, message: str, dept: str | None = None) -> AssessmentResult:
    proto = get_protocol(state["symptom"], dept)
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
                symptom=proto.key, choices=SEVERITY_CHOICES)
        data["score"] = score
        # 有特有欄位 → 續問；否則完成
        if proto.field_key:
            db.set_assessment_state(patient_code, proto.key, "ask_field", data)
            return AssessmentResult(reply=proto.field_question, symptom=proto.key,
                                    choices=field_choices(proto))
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
    over_high = (proto.high_count is not None and proto.field_key in extra
                 and extra[proto.field_key] >= proto.high_count)
    level = "high" if escalate and over_high else "medium"

    db.log_event("assessment_complete", patient_code,
                 {"symptom": proto.key, "score": score, "extra": extra, "escalate": escalate})

    if escalate:
        detail = f"，{_fmt_field(proto, extra)}" if _fmt_field(proto, extra) else ""
        # 網頁版回覆；LINE 由 chat_service 依管道改寫（含聯絡電話、不承諾即時回覆）
        reply = escalation_reply("web", proto.name, score, detail, level, "")
        return AssessmentResult(reply=reply, done=True, escalate=True,
                                escalate_level=level, symptom=proto.key,
                                doc_code=proto.doc_code, score=score, extra=extra)

    # 未達門檻 → 交回 main.py 走 RAG 衛教
    return AssessmentResult(reply="", done=True, educate=True, symptom=proto.key,
                            doc_code=proto.doc_code, score=score, extra=extra)


def _fmt_field(proto: Protocol, extra: dict) -> str:
    if proto.field_key and proto.field_key in extra:
        unit = {"count": "次", "days": "天"}.get(proto.field_key, "")
        label = {"count": "次數", "days": "天數"}.get(proto.field_key, proto.field_key)
        return f"{label}約 {extra[proto.field_key]} {unit}"
    return ""
