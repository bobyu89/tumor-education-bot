"""
帳號 + 雜湊密碼 + 角色認證。取代原本「猜到 R001 就能看光病患對話」的白名單。

三角色：patient / researcher / admin，由管理者發放帳密（無自助註冊）。
密碼用 PBKDF2-HMAC-SHA256 雜湊（標準庫，不需安裝 bcrypt，也避開 torch 相依）。

登入回傳一個帶簽章的 token（HMAC，含 account/role/到期），前端後續帶著它呼叫 API。
這是輕量作法，足夠研究用；正式醫療環境若要更嚴謹，之後可換 JWT/OAuth2
（見 [[Review 問題清單]] #7）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from db import get_conn, _now, log_event

# token 簽章密鑰：優先讀環境變數，否則本機隨機生成（重啟後舊 token 失效）
_SECRET = os.environ.get("AUTH_SECRET", secrets.token_hex(32)).encode()
_TOKEN_TTL = 12 * 3600            # 12 小時
_PBKDF2_ROUNDS = 200_000


# ── 密碼雜湊 ──────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt_hex, dk_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


# ── 帳號管理（管理者用）──────────────────────────────────────────
def create_user(account: str, password: str, role: str,
                *, pseudonym: str | None = None, **patient_fields) -> int:
    """建立帳號。role='patient' 時同時在 patients 表建立對應資料。"""
    if role not in ("patient", "researcher", "admin"):
        raise ValueError(f"未知角色：{role}")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users(account, password_hash, role, created_at) VALUES (?,?,?,?)",
            (account, hash_password(password), role, _now()),
        )
        uid = cur.lastrowid
        if role == "patient":
            conn.execute(
                """INSERT INTO patients
                   (user_id, pseudonym, real_name, age, gender, cancer_type,
                    diagnosis, medications, education_level, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    uid, pseudonym or account,
                    patient_fields.get("real_name"),
                    patient_fields.get("age"),
                    patient_fields.get("gender"),
                    patient_fields.get("cancer_type"),
                    json.dumps(patient_fields.get("diagnosis", []), ensure_ascii=False),
                    json.dumps(patient_fields.get("medications", []), ensure_ascii=False),
                    patient_fields.get("education_level", "general"),
                    _now(),
                ),
            )
    log_event("user_created", pseudonym, {"account": account, "role": role})
    return uid


# ── 登入與 token ──────────────────────────────────────────────────
def authenticate(account: str, password: str) -> dict | None:
    """驗證帳密，成功回傳 {token, role, account, patient_code}。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash, role FROM users WHERE account=?", (account,)
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        patient_code = None
        if row["role"] == "patient":
            p = conn.execute(
                "SELECT pseudonym FROM patients WHERE user_id=?", (row["id"],)
            ).fetchone()
            patient_code = p["pseudonym"] if p else None
    token = _make_token(account, row["role"], patient_code)
    log_event("login", patient_code, {"account": account, "role": row["role"]})
    return {"token": token, "role": row["role"], "account": account,
            "patient_code": patient_code}


def _make_token(account: str, role: str, patient_code: str | None) -> str:
    payload = {"account": account, "role": role, "patient_code": patient_code,
               "exp": int(time.time()) + _TOKEN_TTL}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(token: str) -> dict | None:
    """驗證 token 簽章與到期，回傳 payload 或 None。"""
    try:
        body, sig = token.split(".")
        expected = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except (ValueError, AttributeError, json.JSONDecodeError):
        return None
