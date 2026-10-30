"""
每個人的活動 LOG。

- patient_activity(code)：一位病患的完整時間軸 —— 對話、紅旗、症狀分數、測驗、了解程度、
  登入、LINE 綁定，以及「哪位研究者在何時看過他的資料」。研究者/管理者可看。
- staff_audit(account)：研究者/管理者做過的事 —— 登入、查看誰的資料、匯出、發通行碼。限管理者。

只用假名代號與帳號；不輸出 patients.real_name。
"""
from __future__ import annotations

import json

from assessment import PROTOCOLS
from db import get_conn

_SYMPTOM_NAMES = {p.key: p.name for p in PROTOCOLS}   # diarrhea → 腹瀉（畫面用；CSV 匯出保留英文代碼）

# events 中要放進病患時間軸的類型。redflag_high / quiz_submit / assessment_complete
# 已由 alerts / quiz_results / symptom_scores 呈現，不重複列。
_PATIENT_EVENTS = {
    "login": "登入",
    "assessment_start": "開始症狀追問",
    "assessment_abort": "中止症狀追問",
    "user_created": "建立帳號",
    "binding_code_issued": "發出 LINE 通行碼",
    "line_bound": "完成 LINE 綁定",
    "line_unbound": "解除 LINE 綁定",
    "binding_failed": "LINE 通行碼輸入錯誤",
    "view_history": "查看對話紀錄",
    "view_trends": "查看症狀趨勢",
    "view_activity_log": "查看活動 LOG",
}

_STAFF_EVENTS = {
    "login": "登入",
    "login_failed": "登入失敗",
    "view_sessions": "查看病患清單",
    "view_history": "查看對話紀錄",
    "view_trends": "查看症狀趨勢",
    "view_activity_log": "查看病患 LOG",
    "view_staff_audit": "查看人員稽核",
    "export_csv": "匯出 CSV",
    "user_created": "建立帳號",
    "binding_code_issued": "發出 LINE 通行碼",
    "line_unbound": "解除 LINE 綁定",
}

# 由研究者/管理者觸發、在病患時間軸上標示為「存取」的事件
_ACCESS_EVENTS = {"view_history", "view_trends", "view_activity_log"}


def patient_activity(patient_code: str, limit: int = 500) -> list[dict]:
    items: list[dict] = []
    with get_conn() as conn:
        for r in conn.execute(
            "SELECT role, content, ts, redflag_level, answer_quality FROM messages "
            "WHERE patient_code=?", (patient_code,)
        ):
            items.append({
                "ts": r["ts"], "kind": "message",
                "who": "病患" if r["role"] == "patient" else "機器人",
                "summary": r["content"][:120],
                "detail": {"redflag_level": r["redflag_level"], "answer_quality": r["answer_quality"]},
            })
        for r in conn.execute(
            "SELECT trigger, level, ts FROM alerts WHERE patient_code=?", (patient_code,)
        ):
            items.append({"ts": r["ts"], "kind": "alert", "who": "系統",
                          "summary": f"紅旗（{r['level']}）：{r['trigger']}", "detail": {}})
        for r in conn.execute(
            "SELECT esas_symptom, score, extra, ts FROM symptom_scores WHERE patient_code=?",
            (patient_code,)
        ):
            name = _SYMPTOM_NAMES.get(r["esas_symptom"], r["esas_symptom"])
            items.append({"ts": r["ts"], "kind": "symptom", "who": "病患",
                          "summary": f"症狀評估 {name} {r['score']}/10",
                          "detail": json.loads(r["extra"] or "{}")})
        for r in conn.execute(
            "SELECT onc_code, phase, score, ts FROM quiz_results WHERE patient_code=?",
            (patient_code,)
        ):
            phase = "前測" if r["phase"] == "pre" else "後測"
            items.append({"ts": r["ts"], "kind": "quiz", "who": "病患",
                          "summary": f"知識測驗 {r['onc_code']} {phase} {r['score']} 分", "detail": {}})
        for r in conn.execute(
            "SELECT onc_code, rating, ts FROM comprehension_checks WHERE patient_code=?",
            (patient_code,)
        ):
            items.append({"ts": r["ts"], "kind": "comprehension", "who": "病患",
                          "summary": f"了解程度 {r['onc_code']}：{r['rating']}", "detail": {}})
        placeholders = ",".join("?" * len(_PATIENT_EVENTS))
        for r in conn.execute(
            f"SELECT ts, event_type, actor FROM events WHERE patient_code=? "
            f"AND event_type IN ({placeholders})", (patient_code, *_PATIENT_EVENTS)
        ):
            access = r["event_type"] in _ACCESS_EVENTS
            items.append({
                "ts": r["ts"], "kind": "access" if access else "event",
                "who": _actor_label(r["actor"]) if r["actor"] else "病患",
                "summary": _PATIENT_EVENTS[r["event_type"]], "detail": {},
            })

    items.sort(key=lambda x: x["ts"], reverse=True)   # 新的在上
    return items[:limit]


def staff_audit(account: str | None = None, limit: int = 500) -> list[dict]:
    """研究者/管理者的操作紀錄。排除病患自己與 LINE 使用者（actor 以 line: 開頭）。"""
    sql = ("SELECT e.ts, e.event_type, e.actor, e.patient_code, e.payload FROM events e "
           "LEFT JOIN users u ON u.account = e.actor "
           "WHERE e.actor IS NOT NULL AND e.actor NOT LIKE 'line:%' "
           "AND (u.role IS NULL OR u.role != 'patient')")
    params: list = []
    if account:
        sql += " AND e.actor = ?"
        params.append(account)
    sql += " ORDER BY e.ts DESC LIMIT ?"
    params.append(limit)
    out = []
    with get_conn() as conn:
        for r in conn.execute(sql, params):
            payload = json.loads(r["payload"] or "{}")
            label = _STAFF_EVENTS.get(r["event_type"], r["event_type"])
            target = r["patient_code"] or payload.get("table") or ""
            out.append({"ts": r["ts"], "actor": r["actor"], "action": label,
                        "target": target, "event_type": r["event_type"]})
    return out


def _actor_label(actor: str) -> str:
    return "LINE" if actor.startswith("line:") else actor
