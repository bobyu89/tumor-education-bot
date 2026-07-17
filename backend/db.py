"""
SQLite 持久化層。

取代原本的記憶體 session（重啟即清空）。所有病患資料、對話、紅旗、
症狀分數、稽核日誌都落地，供研究匯出。

設計原則：
- 身分與研究資料分表：真名只在 patients，其餘表只存假名代號（pseudonym, 如 P001）
  → 匯出走假名 = 天然去識別化。
- messages 存滿軌跡（紅旗等級、RAG來源、model、tokens、品質）→ 一表餵養品質稽核與研究。
- 第二、三期的表（symptom_scores / comprehension_checks / quiz_results）現在就建、先不填
  → 避免日後 migration 動到已存資料。
- 只用標準庫 sqlite3，不依賴 torch（避免 Smart App Control 封鎖問題）。

執行緒安全：FastAPI 在 thread pool 呼叫，故每次操作開新連線（check_same_thread 由
連線層處理），並開啟 WAL 提升並發讀寫。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2   # v2：新增 assessment_state（Phase 2）
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tumor_bot.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Schema ────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account       TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('patient','researcher','admin')),
    created_at    TEXT NOT NULL
);

-- 敏感身分表：真名只存在這裡，其餘表一律用 pseudonym
CREATE TABLE IF NOT EXISTS patients (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id),
    pseudonym       TEXT UNIQUE NOT NULL,     -- P001
    real_name       TEXT,
    age             INTEGER,
    gender          TEXT,
    cancer_type     TEXT,                     -- 分組變項
    diagnosis       TEXT,                     -- json array
    medications     TEXT,                     -- json array
    education_level TEXT DEFAULT 'general',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_code  TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    ended_at      TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_code      TEXT NOT NULL,
    role              TEXT NOT NULL CHECK(role IN ('patient','bot')),
    content           TEXT NOT NULL,
    ts                TEXT NOT NULL,
    redflag_level     TEXT,                   -- none|medium|high
    redflag_terms     TEXT,                   -- json array
    rag_sources       TEXT,                   -- json array
    model_id          TEXT,
    provider          TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    answer_quality    TEXT                    -- grounded|deflected_no_source|deflected_off_source|redflag_shortcut
);

CREATE TABLE IF NOT EXISTS alerts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_code      TEXT NOT NULL,
    trigger           TEXT,                   -- 觸發的症狀/關鍵字
    level             TEXT NOT NULL,          -- medium|high
    source_message_id INTEGER,
    ts                TEXT NOT NULL,
    notified_channels TEXT,                   -- json array: ["websocket","telegram"]
    ack_by            TEXT,
    ack_at            TEXT
);

-- 以下三張為第二、三期用，現在就建、先不填
CREATE TABLE IF NOT EXISTS symptom_scores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_code  TEXT NOT NULL,
    esas_symptom  TEXT NOT NULL,
    score         INTEGER,                    -- 0-10
    extra         TEXT,                       -- json: 頻率/天數等
    ts            TEXT NOT NULL,
    source        TEXT                        -- patient_initiated|scheduled
);

CREATE TABLE IF NOT EXISTS comprehension_checks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_code      TEXT NOT NULL,
    onc_code          TEXT,                   -- 衛教主題 ONC-17…
    rating            TEXT,                   -- 完全懂|不確定|不懂
    ts                TEXT NOT NULL,
    source_message_id INTEGER
);

CREATE TABLE IF NOT EXISTS quiz_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_code  TEXT NOT NULL,
    onc_code      TEXT,
    phase         TEXT,                       -- pre|post
    score         INTEGER,
    ts            TEXT NOT NULL
);

-- 只增不改的稽核日誌（IRB 常要求可重建當時發生什麼）
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    patient_code TEXT,
    event_type   TEXT NOT NULL,
    payload      TEXT                          -- json
);

-- 進行中的症狀評估狀態（Phase 2）。一人同時一個評估，可持久化、重啟後續作。
CREATE TABLE IF NOT EXISTS assessment_state (
    patient_code TEXT PRIMARY KEY,
    symptom      TEXT,
    step         TEXT,                         -- ask_severity | ask_field
    data         TEXT,                         -- json：已收集欄位
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_patient ON messages(patient_code, ts);
CREATE INDEX IF NOT EXISTS idx_alerts_patient   ON alerts(patient_code, ts);
CREATE INDEX IF NOT EXISTS idx_symptom_patient  ON symptom_scores(patient_code, ts);
"""


def init_db() -> None:
    """建立所有表；冪等，可重複呼叫。"""
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        if row["v"] is None:
            conn.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _now()),
            )


# ── 稽核日誌 ──────────────────────────────────────────────────────
def log_event(event_type: str, patient_code: str | None = None, payload: dict | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO events(ts, patient_code, event_type, payload) VALUES (?,?,?,?)",
            (_now(), patient_code, event_type, json.dumps(payload or {}, ensure_ascii=False)),
        )


# ── Sessions ──────────────────────────────────────────────────────
def ensure_session(patient_code: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE patient_code=? AND ended_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (patient_code,),
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO sessions(patient_code, started_at) VALUES (?, ?)",
            (patient_code, _now()),
        )
        return cur.lastrowid


# ── Messages ──────────────────────────────────────────────────────
def add_message(
    patient_code: str,
    role: str,
    content: str,
    *,
    redflag_level: str | None = None,
    redflag_terms: list[str] | None = None,
    rag_sources: list[str] | None = None,
    model_id: str | None = None,
    provider: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    answer_quality: str | None = None,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO messages
               (patient_code, role, content, ts, redflag_level, redflag_terms,
                rag_sources, model_id, provider, prompt_tokens, completion_tokens, answer_quality)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                patient_code, role, content, _now(), redflag_level,
                json.dumps(redflag_terms or [], ensure_ascii=False),
                json.dumps(rag_sources or [], ensure_ascii=False),
                model_id, provider, prompt_tokens, completion_tokens, answer_quality,
            ),
        )
        return cur.lastrowid


def get_history(patient_code: str, limit: int = 12) -> list[dict]:
    """回傳最近 N 則（時間正序），供組裝對話歷史。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE patient_code=? ORDER BY id DESC LIMIT ?",
            (patient_code, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ── Alerts ────────────────────────────────────────────────────────
def add_alert(
    patient_code: str,
    trigger: str,
    level: str,
    source_message_id: int | None,
    notified_channels: list[str] | None = None,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO alerts
               (patient_code, trigger, level, source_message_id, ts, notified_channels)
               VALUES (?,?,?,?,?,?)""",
            (
                patient_code, trigger, level, source_message_id, _now(),
                json.dumps(notified_channels or [], ensure_ascii=False),
            ),
        )
        return cur.lastrowid


# ── 研究者查詢用 ──────────────────────────────────────────────────
def list_sessions_summary() -> list[dict]:
    with get_conn() as conn:
        patients = conn.execute(
            "SELECT DISTINCT patient_code FROM messages"
        ).fetchall()
        out = []
        for p in patients:
            code = p["patient_code"]
            cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE patient_code=?", (code,)
            ).fetchone()["c"]
            flags = conn.execute(
                "SELECT trigger, level, ts FROM alerts WHERE patient_code=? ORDER BY ts DESC",
                (code,),
            ).fetchall()
            last = conn.execute(
                "SELECT content FROM messages WHERE patient_code=? ORDER BY id DESC LIMIT 1",
                (code,),
            ).fetchone()
            out.append({
                "patient_code": code,
                "message_count": cnt,
                "red_flags": [dict(f) for f in flags],
                "has_emergency": any(f["level"] == "high" for f in flags),
                "last_message": (last["content"][:50] if last else ""),
            })
        return out


def get_patient_messages(patient_code: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, ts, redflag_level, answer_quality "
            "FROM messages WHERE patient_code=? ORDER BY id",
            (patient_code,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 症狀評估（Phase 2）────────────────────────────────────────────
def get_assessment_state(patient_code: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT symptom, step, data FROM assessment_state WHERE patient_code=?",
            (patient_code,),
        ).fetchone()
    if not row:
        return None
    return {"symptom": row["symptom"], "step": row["step"],
            "data": json.loads(row["data"] or "{}")}


def set_assessment_state(patient_code: str, symptom: str, step: str, data: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO assessment_state(patient_code, symptom, step, data, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(patient_code) DO UPDATE SET
                 symptom=excluded.symptom, step=excluded.step,
                 data=excluded.data, updated_at=excluded.updated_at""",
            (patient_code, symptom, step, json.dumps(data, ensure_ascii=False), _now()),
        )


def clear_assessment_state(patient_code: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM assessment_state WHERE patient_code=?", (patient_code,))


def add_symptom_score(patient_code: str, esas_symptom: str, score: int | None,
                      extra: dict | None = None, source: str = "patient_initiated") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO symptom_scores(patient_code, esas_symptom, score, extra, ts, source)
               VALUES (?,?,?,?,?,?)""",
            (patient_code, esas_symptom, score,
             json.dumps(extra or {}, ensure_ascii=False), _now(), source),
        )
        return cur.lastrowid


def get_symptom_scores(patient_code: str, symptom: str | None = None) -> list[dict]:
    """研究/趨勢用：取某病患的症狀分數（可指定症狀）。"""
    with get_conn() as conn:
        if symptom:
            rows = conn.execute(
                "SELECT esas_symptom, score, extra, ts, source FROM symptom_scores "
                "WHERE patient_code=? AND esas_symptom=? ORDER BY ts",
                (patient_code, symptom),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT esas_symptom, score, extra, ts, source FROM symptom_scores "
                "WHERE patient_code=? ORDER BY ts",
                (patient_code,),
            ).fetchall()
    return [dict(r) for r in rows]
