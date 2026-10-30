"""
身分與權限 —— 所有端點都從這裡取得「目前使用者」。

修正資安審查 S1–S3 的共同根因：系統原本只有「登入」這個動作，但登入結果沒有任何端點在用，
病患身分是從請求 body 自報的 patient_id 決定。現在：
  - 網頁：身分來自簽章 token（X-Auth-Token header；WebSocket 用 ?token=）
  - LINE：身分來自經簽章驗證的 LINE userId → 病患代號綁定（見 binding.py）
兩者最後都只交出一個病患代號給下游，下游不需要知道訊息從哪個管道來。
"""
from __future__ import annotations

import re

from fastapi import Depends, Header, HTTPException

import auth

# 病患代號格式。代號會出現在研究者頁面與匯出檔，限定格式可杜絕注入（資安審查 S4）。
PATIENT_CODE_RE = re.compile(r"^P\d{3,6}$")


def is_valid_patient_code(code: str) -> bool:
    return bool(code) and PATIENT_CODE_RE.fullmatch(code) is not None


def check_patient_code(code: str) -> str:
    if not is_valid_patient_code(code):
        raise HTTPException(status_code=400, detail="病患代號格式錯誤")
    return code


def current_user(x_auth_token: str = Header(default="")) -> dict:
    payload = auth.verify_token(x_auth_token)
    if not payload:
        raise HTTPException(status_code=401, detail="請先登入")
    return payload


def require_role(*roles: str):
    def dependency(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="權限不足")
        return user
    return dependency


staff_user = require_role("researcher", "admin")
admin_user = require_role("admin")


def patient_user(user: dict = Depends(require_role("patient"))) -> dict:
    """病患端點用：保證 token 內有合法病患代號。"""
    if not is_valid_patient_code(user.get("patient_code") or ""):
        raise HTTPException(status_code=403, detail="此帳號未對應病患")
    return user


def staff_from_ws_token(token: str) -> dict | None:
    """WebSocket 無法帶自訂 header，改由 query 參數 ?token= 驗證。限研究者/管理者。"""
    payload = auth.verify_token(token or "")
    if not payload or payload["role"] not in ("researcher", "admin"):
        return None
    return payload
