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
from datetime import datetime, timezone

import logging

from config import settings
from db import get_conn, _now, log_event

# token 簽章密鑰。必須從 settings 讀：pydantic-settings 讀 .env 時不會寫回 os.environ，
# 先前用 os.environ.get 導致 .env 裡的 AUTH_SECRET 永遠讀不到。
if settings.AUTH_SECRET:
    _SECRET = settings.AUTH_SECRET.encode()
else:
    _SECRET = secrets.token_hex(32).encode()
    logging.warning("[auth] AUTH_SECRET 未設定，本次啟動使用隨機密鑰；重啟後所有人需重新登入。"
                    "正式環境請在 .env 設定固定值。")
_TOKEN_TTL = 12 * 3600            # 12 小時
_PBKDF2_ROUNDS = 200_000
_WS_TICKET_TTL = 60               # 護理端 WebSocket 連線票券：60 秒內有效


class LoginLocked(Exception):
    """同一帳號短時間內連續登入失敗太多次，暫時鎖定（2026-11 同儕審查：原本可無限次猜密碼）。"""


# ── 密碼雜湊 ──────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


_DUMMY_HASH = hash_password(secrets.token_hex(8))


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
                *, pseudonym: str | None = None, created_by: str | None = None,
                **patient_fields) -> int:
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
    log_event("user_created", pseudonym, {"account": account, "role": role}, actor=created_by)
    return uid


# ── 登入與 token ──────────────────────────────────────────────────
def _recent_failures(account: str) -> int:
    """最近 LOGIN_LOCK_MINUTES 分鐘內、上次成功登入之後的失敗次數（依 events 表計算，重啟不歸零）。"""
    since = datetime.fromtimestamp(time.time() - settings.LOGIN_LOCK_MINUTES * 60, timezone.utc).isoformat()
    with get_conn() as conn:
        last_ok = conn.execute(
            "SELECT MAX(ts) AS t FROM events WHERE event_type='login' AND actor=?", (account,)
        ).fetchone()["t"]
        start = max(since, last_ok or "")
        return conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE event_type='login_failed' AND actor=? AND ts > ?",
            (account, start),
        ).fetchone()["c"]


def authenticate(account: str, password: str) -> dict | None:
    """驗證帳密，成功回傳 {token, role, account, patient_code}；失敗回 None；鎖定中丟 LoginLocked。

    鎖定以「帳號」為單位（不存在的帳號也計數，避免藉此探測帳號是否存在）。
    代價：有人故意打錯別人的帳號，可讓對方暫時無法登入 —— 時間短（預設 15 分鐘），且留有紀錄。"""
    if _recent_failures(account) >= settings.LOGIN_MAX_FAILURES:
        log_event("login_locked", None, {"account": account}, actor=account)
        raise LoginLocked(account)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash, role FROM users WHERE account=?", (account,)
        ).fetchone()
        if not row:
            verify_password(password, _DUMMY_HASH)   # 讓「帳號不存在」與「密碼錯誤」耗時相同
            log_event("login_failed", None, {"account": account}, actor=account)
            return None
        if not verify_password(password, row["password_hash"]):
            log_event("login_failed", None, {"account": account}, actor=account)
            return None
        patient_code = None
        if row["role"] == "patient":
            p = conn.execute(
                "SELECT pseudonym FROM patients WHERE user_id=?", (row["id"],)
            ).fetchone()
            patient_code = p["pseudonym"] if p else None
    token = _make_token(account, row["role"], patient_code)
    log_event("login", patient_code, {"account": account, "role": row["role"]}, actor=account)
    return {"token": token, "role": row["role"], "account": account,
            "patient_code": patient_code}


def _make_token(account: str, role: str, patient_code: str | None) -> str:
    payload = {"account": account, "role": role, "patient_code": patient_code,
               "exp": int(time.time()) + _TOKEN_TTL}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _sign(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def make_ws_ticket(user: dict) -> str:
    """護理端 WebSocket 用的短效票券。瀏覽器的 WebSocket 不能帶自訂 header，憑證只能放在網址，
    而網址常被寫進伺服器／代理的存取紀錄 —— 所以網址裡放的是 60 秒就失效、且不能拿來呼叫 API 的票券，
    不再放 12 小時有效的登入 token（2026-11 同儕審查）。"""
    return _sign({"account": user["account"], "role": user["role"], "typ": "ws_ticket",
                  "exp": int(time.time()) + _WS_TICKET_TTL})


def verify_ws_ticket(ticket: str) -> dict | None:
    payload = _verify_signed(ticket)
    if not payload or payload.get("typ") != "ws_ticket":
        return None
    return payload


def verify_token(token: str) -> dict | None:
    """驗證登入 token（API 用）。WebSocket 票券（typ=ws_ticket）不可當登入 token 使用。"""
    payload = _verify_signed(token)
    if not payload or payload.get("typ"):
        return None
    return payload


def _verify_signed(token: str) -> dict | None:
    """驗證簽章與到期，回傳 payload 或 None。"""
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
