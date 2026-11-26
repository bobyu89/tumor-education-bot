"""
研究統計與 CSV 匯出（Phase 3a）。純 DB 讀，不需 API key。

去識別化硬要求：所有查詢只用假名代號（patient_code / pseudonym），永不觸碰 patients.real_name。

但「病患自己打的字」可能含個資（2026-11 同儕審查：自由文字不是天然去識別化），所以匯出時
對病患輸入的內容（messages.content 中 role=patient、alerts.trigger）遮蔽電話、身分證／居留證號、
email、LINE ID 與長串數字（可能是病歷號）。限制：**人名、地址無法用規則可靠偵測**，
正式匯出給研究分析前仍需人工抽查。LINE userId 只存在 events 表，不在任何匯出白名單內。
"""
from __future__ import annotations

import csv
import io
import re

from db import get_conn

# 可匯出的表與欄位（白名單，避免任意 SQL；real_name 不在任何清單中）
EXPORT_TABLES = {
    "symptom_scores": ["patient_code", "esas_symptom", "score", "extra", "ts", "source"],
    "messages": ["patient_code", "role", "redflag_level", "answer_quality",
                 "model_id", "provider", "rag_doc_versions", "ts", "content"],
    "alerts": ["patient_code", "trigger", "level", "ts"],
    "comprehension_checks": ["patient_code", "onc_code", "rating", "ts"],
}


# ── 世代總覽 ──────────────────────────────────────────────────────
def cohort_overview() -> dict:
    with get_conn() as conn:
        patients = conn.execute(
            "SELECT COUNT(DISTINCT patient_code) AS c FROM messages"
        ).fetchone()["c"]
        messages = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        high = conn.execute(
            "SELECT COUNT(*) AS c FROM alerts WHERE level='high'").fetchone()["c"]
        medium = conn.execute(
            "SELECT COUNT(*) AS c FROM alerts WHERE level='medium'").fetchone()["c"]

        # 各症狀：樣本數、平均分、最高分、升級數（分數≥7 視為嚴重）
        rows = conn.execute(
            """SELECT esas_symptom AS symptom,
                      COUNT(*) AS n,
                      ROUND(AVG(score), 2) AS avg_score,
                      MAX(score) AS max_score,
                      SUM(CASE WHEN score >= 7 THEN 1 ELSE 0 END) AS severe_n
               FROM symptom_scores
               GROUP BY esas_symptom
               ORDER BY n DESC"""
        ).fetchall()
        symptoms = [dict(r) for r in rows]

        # 回答品質分布
        qrows = conn.execute(
            "SELECT answer_quality AS q, COUNT(*) AS c FROM messages "
            "WHERE role='bot' AND answer_quality IS NOT NULL GROUP BY answer_quality"
        ).fetchall()
        answer_quality = {r["q"]: r["c"] for r in qrows}

        # 知識測驗前後測：各主題 pre/post 平均分
        qz = conn.execute(
            """SELECT onc_code, phase, ROUND(AVG(score), 2) AS avg_score, COUNT(*) AS n
               FROM quiz_results GROUP BY onc_code, phase"""
        ).fetchall()
        quiz: dict[str, dict] = {}
        for r in qz:
            quiz.setdefault(r["onc_code"], {})[r["phase"]] = {"avg": r["avg_score"], "n": r["n"]}

    return {
        "patients": patients,
        "messages": messages,
        "alerts": {"high": high, "medium": medium},
        "symptoms": symptoms,
        "answer_quality": answer_quality,
        "quiz": quiz,
    }


# ── 個別病患趨勢 ──────────────────────────────────────────────────
def patient_trends(patient_code: str) -> dict:
    with get_conn() as conn:
        srows = conn.execute(
            "SELECT esas_symptom, score, ts FROM symptom_scores "
            "WHERE patient_code=? AND score IS NOT NULL ORDER BY ts",
            (patient_code,),
        ).fetchall()
        arows = conn.execute(
            "SELECT trigger, level, ts FROM alerts WHERE patient_code=? ORDER BY ts",
            (patient_code,),
        ).fetchall()

    series: dict[str, list] = {}
    for r in srows:
        series.setdefault(r["esas_symptom"], []).append({"ts": r["ts"], "score": r["score"]})

    return {
        "patient_code": patient_code,
        "symptom_series": series,
        "alerts": [dict(r) for r in arows],
    }


# ── CSV 匯出（去識別化）──────────────────────────────────────────
def export_csv(table: str) -> str:
    if table not in EXPORT_TABLES:
        raise ValueError(f"不可匯出的表：{table}")
    cols = EXPORT_TABLES[table]
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} ORDER BY ts"
        ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(cols)
    for r in rows:
        writer.writerow([_csv_safe(_export_value(table, r, c)) for c in cols])
    return buf.getvalue()


# ── 匯出前遮蔽病患自行輸入的個資 ─────────────────────────────────
_PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    (re.compile(r"(?i)line\s*(?:id)?\s*[:：]?\s*@?[\w.-]{3,}"), "[LINE_ID]"),
    (re.compile(r"(?<![A-Za-z])[A-Z][1289]\d{8}(?!\d)"), "[ID_NO]"),           # 身分證／居留證號
    (re.compile(r"(?:\+?886[-\s]?|0)9\d{2}[-\s]?\d{3}[-\s]?\d{3}(?!\d)"), "[PHONE]"),   # 手機
    (re.compile(r"\(?0\d{1,2}\)?[-\s]?\d{3,4}[-\s]?\d{4}(?!\d)"), "[PHONE]"),        # 市話
    (re.compile(r"(?<![\d.])\d{7,}(?![\d.])"), "[NUMBER]"),                   # 長串數字（可能是病歷號）
]


def scrub_pii(text):
    if not isinstance(text, str):
        return text
    for pattern, repl in _PII_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _export_value(table: str, row, col: str):
    value = row[col]
    if table == "messages" and col == "content" and row["role"] == "patient":
        return scrub_pii(value)
    if table == "alerts" and col == "trigger":
        return scrub_pii(value)
    return value


_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """病患訊息若以 = + - @ 開頭，Excel 開啟時會當成公式執行（CSV injection，資安審查 M4）。
    在前面加單引號讓 Excel 當純文字。只處理字串，數字不動。"""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIX):
        return "'" + value
    return value
