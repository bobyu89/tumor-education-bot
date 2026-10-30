"""
測試共用工具。必須在其他後端模組之前 import（要在 config 讀設定前設好環境變數）。
"""
import os

os.environ.setdefault("DEMO_SEED", "true")     # 測試需要 P001/R001/A001 示範帳號


def login_headers(client, account: str, password: str) -> dict:
    r = client.post("/login", json={"account": account, "password": password})
    assert r.status_code == 200, f"登入失敗 {account}: {r.status_code} {r.text}"
    return {"X-Auth-Token": r.json()["token"]}
