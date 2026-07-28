"""
研究統計與 CSV 匯出（Phase 3a）。純 DB 讀，不需 API key。

去識別化硬要求：所有查詢只用假名代號（patient_code / pseudonym），
永不觸碰 patients.real_name。研究匯出 = 天然去識別化。
"""
from __future__ import annotations

import csv
import io

from db import get_conn

# 可匯出的表與欄位（白名單，避免任意 SQL；real_name 不在任何清單中）
EXPORT_TABLES = {
    "symptom_scores": ["patient_code", "esas_symptom", "score", "extra", "ts", "source"],
    "messages": ["patient_code", "role", "redflag_level", "answer_quality",
                 "model_id", "provider", "ts", "content"],
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

    return {
        "patients": patients,
        "messages": messages,
        "alerts": {"high": high, "medium": medium},
        "symptoms": symptoms,
        "answer_quality": answer_quality,
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
        writer.writerow([r[c] for c in cols])
    return buf.getvalue()
