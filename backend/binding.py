"""
LINE 通行碼綁定：管理者為病患發一組一次性通行碼，病患在 LINE 輸入後，
其 LINE userId 就綁定到該病患代號。之後 LINE 訊息的身分一律由綁定決定（見 security.py）。

取代 v2.0 架構書的「在 LINE 輸入 P001 就綁定」—— 那讓任何人都能冒用他人身分。

- 通行碼 8 碼（顯示為 XXXX-XXXX），字元集去掉易混淆的 0 O 1 I L，方便長輩輸入
- 一次性、72 小時過期；重新發碼會作廢前一組未使用的碼
- 資料庫只存 SHA-256 雜湊，明碼只在發碼當下回傳一次
- 同一 LINE 帳號 15 分鐘內輸錯 5 次即暫時鎖定，防暴力猜碼
- 一位病患同時只綁一個 LINE；新綁定會讓舊綁定失效
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from db import _now, get_conn, log_event

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 8
CODE_TTL = timedelta(hours=72)
MAX_FAILS = 5
FAIL_WINDOW = timedelta(minutes=15)


def normalize(raw: str) -> str:
    return "".join(ch for ch in (raw or "").upper() if ch not in " -\t\n")


def looks_like_code(raw: str) -> bool:
    n = normalize(raw)
    return len(n) == CODE_LEN and all(ch in ALPHABET for ch in n)


def _hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _line_actor(line_user_id: str) -> str:
    return f"line:{line_user_id}"


def patient_exists(patient_code: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM patients WHERE pseudonym=?", (patient_code,)).fetchone() is not None


def issue_code(patient_code: str, issued_by: str) -> dict:
    if not patient_exists(patient_code):
        raise LookupError(patient_code)
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    now = datetime.now(timezone.utc)
    expires = now + CODE_TTL
    with get_conn() as conn:
        conn.execute(   # 作廢這位病患先前未使用的碼
            "UPDATE binding_codes SET revoked_at=? "
            "WHERE patient_code=? AND used_at IS NULL AND revoked_at IS NULL",
            (_iso(now), patient_code),
        )
        conn.execute(
            "INSERT INTO binding_codes(code_hash, patient_code, created_by, created_at, expires_at) "
            "VALUES (?,?,?,?,?)",
            (_hash(code), patient_code, issued_by, _iso(now), _iso(expires)),
        )
    log_event("binding_code_issued", patient_code, {"expires_at": _iso(expires)}, actor=issued_by)
    return {"patient_code": patient_code, "code": f"{code[:4]}-{code[4:]}", "expires_at": _iso(expires)}


def patient_for_line(line_user_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT patient_code FROM line_bindings WHERE line_user_id=? AND revoked_at IS NULL "
            "ORDER BY id DESC LIMIT 1", (line_user_id,)
        ).fetchone()
    return row["patient_code"] if row else None


def _recent_failures(line_user_id: str) -> int:
    since = _iso(datetime.now(timezone.utc) - FAIL_WINDOW)
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM events WHERE event_type='binding_failed' AND actor=? AND ts>=?",
            (_line_actor(line_user_id), since),
        ).fetchone()["c"]


def redeem(line_user_id: str, raw_code: str) -> dict:
    """回傳 {"ok": bool, "reason": ..., "patient_code": ...}。
    reason：bound（成功）| already_bound | rate_limited | invalid"""
    existing = patient_for_line(line_user_id)
    if existing:
        return {"ok": False, "reason": "already_bound", "patient_code": existing}
    if _recent_failures(line_user_id) >= MAX_FAILS:
        return {"ok": False, "reason": "rate_limited"}

    now = _now()
    claimed = False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, patient_code FROM binding_codes WHERE code_hash=? "
            "AND used_at IS NULL AND revoked_at IS NULL AND expires_at > ?",
            (_hash(normalize(raw_code)), now),
        ).fetchone()
        if row:
            # 以 used_at IS NULL 為條件更新，兩人同時兌換同一組碼時只有一人成功
            claimed = conn.execute(
                "UPDATE binding_codes SET used_at=? WHERE id=? AND used_at IS NULL", (now, row["id"])
            ).rowcount == 1
            if claimed:
                conn.execute(   # 一位病患同時只綁一個 LINE
                    "UPDATE line_bindings SET revoked_at=? WHERE patient_code=? AND revoked_at IS NULL",
                    (now, row["patient_code"]),
                )
                conn.execute(
                    "INSERT INTO line_bindings(line_user_id, patient_code, bound_at) VALUES (?,?,?)",
                    (line_user_id, row["patient_code"], now),
                )
    if claimed:
        log_event("line_bound", row["patient_code"], {}, actor=_line_actor(line_user_id))
        return {"ok": True, "reason": "bound", "patient_code": row["patient_code"]}
    log_event("binding_failed", None, {}, actor=_line_actor(line_user_id))
    return {"ok": False, "reason": "invalid"}


def unbind(patient_code: str, by: str) -> bool:
    with get_conn() as conn:
        n = conn.execute(
            "UPDATE line_bindings SET revoked_at=? WHERE patient_code=? AND revoked_at IS NULL",
            (_now(), patient_code),
        ).rowcount
    if n:
        log_event("line_unbound", patient_code, {}, actor=by)
    return n > 0


def list_patients() -> list[dict]:
    """管理者用：所有病患與 LINE 綁定狀態。不含真名。"""
    now = _now()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.pseudonym, p.cancer_type, p.age, p.gender, p.education_level,
                      (SELECT bound_at FROM line_bindings b
                        WHERE b.patient_code=p.pseudonym AND b.revoked_at IS NULL
                        ORDER BY b.id DESC LIMIT 1) AS bound_at,
                      (SELECT expires_at FROM binding_codes c
                        WHERE c.patient_code=p.pseudonym AND c.used_at IS NULL
                          AND c.revoked_at IS NULL AND c.expires_at > ?
                        ORDER BY c.id DESC LIMIT 1) AS pending_code_expires_at
               FROM patients p ORDER BY p.pseudonym""",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]
