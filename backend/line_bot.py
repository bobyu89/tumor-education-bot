"""
LINE Messaging API webhook（病患端）。LINE_CHANNEL_SECRET 與 LINE_CHANNEL_ACCESS_TOKEN 皆有值才啟用。

避開 v2.0 架構書第五章的三個問題：
  - 不用 asyncio.run（在事件迴圈內會直接崩潰）
  - webhook 立刻回 200，處理交給背景任務，不阻塞
  - 先用 reply token 回覆；若已失效（LLM 回答較慢時）改用 Push API
紅旗路徑不經 LLM、毫秒級完成，所以急症一定能用 reply token 即時回覆。

隱私：衛教回覆以 Flex 訊息送出，altText 用中性文字 —— 手機鎖定畫面與通知預覽只會看到
altText，不會露出症狀或診斷內容。（上線前請以實機確認通知預覽行為。）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import sqlite3

import httpx

import binding
from chat_service import _emergency_reply, process_message
from config import settings
from db import _now, get_conn
from redflag import screen as redflag_screen

API = "https://api.line.me/v2/bot/message"
MAX_TEXT = 4900   # LINE 單則上限 5000 字

WELCOME = ("您好，這裡是腫瘤衛教小幫手。\n"
           "請輸入護理師提供的 8 碼通行碼完成綁定（例如 ABCD-EFGH）。")
ASK_CODE = "請先輸入護理師提供的 8 碼通行碼完成綁定（例如 ABCD-EFGH）。"
BOUND = "綁定完成！之後可以直接在這裡詢問化療相關的照護問題。"
INVALID = "通行碼不正確或已過期，請確認後重新輸入，或向護理師索取新的通行碼。"
RATE_LIMITED = "通行碼輸入錯誤次數過多，請 15 分鐘後再試，或聯絡護理師重新取得通行碼。"
TEXT_ONLY = "目前只能處理文字訊息喔。"


def enabled() -> bool:
    return bool(settings.LINE_CHANNEL_SECRET and settings.LINE_CHANNEL_ACCESS_TOKEN)


def verify_signature(body: bytes, signature: str) -> bool:
    """LINE 以 Channel Secret 對原始 body 做 HMAC-SHA256，base64 放在 X-Line-Signature。"""
    mac = hmac.new(settings.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature or "")


def first_seen(event_id: str) -> bool:
    """LINE 會重送 webhook；同一事件只處理一次，避免同一個急症通知兩次。"""
    try:
        with get_conn() as conn:
            conn.execute("INSERT INTO line_events_seen(event_id, ts) VALUES (?,?)", (event_id, _now()))
        return True
    except sqlite3.IntegrityError:
        return False


async def handle_event(event: dict) -> None:
    source = event.get("source") or {}
    user_id = source.get("userId")
    if source.get("type") != "user" or not user_id:
        return                                   # 不處理群組/聊天室
    reply_token = event.get("replyToken")
    etype = event.get("type")

    if etype == "follow":
        await send(user_id, reply_token, WELCOME)
        return
    if etype != "message":
        return
    message = event.get("message") or {}
    if message.get("type") != "text":
        await send(user_id, reply_token, TEXT_ONLY)
        return
    text = (message.get("text") or "").strip()

    patient_code = binding.patient_for_line(user_id)
    if patient_code is None:
        await _handle_unbound(user_id, reply_token, text)
        return

    resp = await process_message(patient_code, text, channel="line")
    await send(user_id, reply_token, resp.response, private=True, urgent=resp.is_emergency)


async def _handle_unbound(user_id: str, reply_token: str, text: str) -> None:
    # 尚未綁定、不知道是誰，但急症仍要先給緊急指引
    if redflag_screen(text, use_vector=False).severity == "high":
        await send(user_id, reply_token, _emergency_reply("line") + "\n\n" + ASK_CODE, urgent=True)
        return
    if not binding.looks_like_code(text):        # 打招呼等不算輸錯，避免被鎖
        await send(user_id, reply_token, ASK_CODE)
        return
    result = binding.redeem(user_id, text)
    if result["ok"]:
        await send(user_id, reply_token, f"{BOUND}\n如有緊急狀況，{settings.EMERGENCY_CONTACT}。")
    elif result["reason"] == "rate_limited":
        await send(user_id, reply_token, RATE_LIMITED)
    else:
        await send(user_id, reply_token, INVALID)


def build_message(text: str, *, private: bool = False, urgent: bool = False) -> dict:
    text = text[:MAX_TEXT]
    if not private and not urgent:
        return {"type": "text", "text": text}
    alt = "⚠️ 請立即查看：重要提醒" if urgent else "您有一則新的衛教回覆"
    return {
        "type": "flex", "altText": alt,
        "contents": {"type": "bubble", "body": {
            "type": "box", "layout": "vertical",
            "contents": [{"type": "text", "text": text, "wrap": True}],
        }},
    }


async def send(user_id: str, reply_token: str | None, text: str, *,
               private: bool = False, urgent: bool = False) -> None:
    msg = build_message(text, private=private, urgent=urgent)
    if reply_token and await _post("/reply", {"replyToken": reply_token, "messages": [msg]}):
        return
    await _post("/push", {"to": user_id, "messages": [msg]})


async def _post(path: str, payload: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                API + path, json=payload,
                headers={"Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}"},
            )
    except httpx.HTTPError as e:
        logging.error(f"[line] {path} 連線失敗：{e}")
        return False
    if r.status_code == 200:
        return True
    logging.warning(f"[line] {path} 回應 {r.status_code}：{r.text[:200]}")
    return False
