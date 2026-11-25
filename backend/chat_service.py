"""
「病患說了一句話」的完整處理流程。網頁 /chat 與 LINE webhook 共用同一個入口 process_message()。

流程：
  ① 紅旗瞬間篩檢（redflag.screen）—— 不進 RAG/LLM，毫秒級
     HIGH → 立刻推播 + 立刻回固定安全文字 + 落庫 → return
  ② 症狀結構化追問（assessment）—— 進行中則推進；偵測到新症狀則開始
  ③ 一般 RAG 問答：品質前置 → LLM → 品質標記 → 落庫

channel 決定給病患的求助指引：
  web  —— 住院病患在病房用網頁，可以按呼叫鈴
  line —— 化療後居家病患，沒有呼叫鈴；指引改為撥打電話，且不承諾「護理師會回覆」，
          因為系統無法保證當下有人在看警示

警示紀錄（alerts 表）據實記錄（2026-11 同儕審查 M2）：
  - notified_channels 只在 WebSocket「實際送達 ≥1 個在線護理端」時才寫 ["websocket"]，否則為 []；
  - high / medium 都推播並落庫，護理端以 GET /alerts 看未確認清單、POST /alerts/{id}/ack 確認；
  - 知識庫外提問記為 level="info"（待處理問題），讓問題進入護理端清單，而不是石沉大海；
  - 病患端文字不宣稱「已通知」，見 safety_text.py。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import assessment
import db
import quality
from config import settings
from llm_client import LLMError, get_client
from models import ChatResponse, PatientProfile
from prompt import build_prompt
from rag import retriever
from redflag import screen as redflag_screen
from safety_text import deflect_reply, emergency_reply, escalation_reply, help_text
from websocket_manager import manager

Channel = str   # "web" | "line"


def _help_text(channel: Channel) -> str:
    return help_text(channel, settings.EMERGENCY_CONTACT)


def _emergency_reply(channel: Channel, category: str = "other") -> str:
    return emergency_reply(channel, category, settings.EMERGENCY_CONTACT)


async def _notify(payload: dict, pid: str, trigger: str, level: str, msg_id: int | None) -> int:
    """推播給在線護理端並落庫；notified_channels 依「實際送達」據實記錄。回傳 alert id。"""
    delivered = await manager.broadcast_alert(payload)
    channels = ["websocket"] if delivered else []
    alert_id = db.add_alert(pid, trigger, level, msg_id, notified_channels=channels)
    if not delivered and level == "high":
        # 沒有任何護理端在線：留下可稽核的紀錄（供每日檢視「無人即時看到的 HIGH」）
        db.log_event("alert_undelivered", pid, {"alert_id": alert_id, "level": level, "trigger": trigger})
    return alert_id


async def process_message(pid: str, text: str, channel: Channel = "web") -> ChatResponse:
    db.ensure_session(pid)

    # ① 紅旗瞬間篩檢（向量補充層會跑嵌入模型，丟到 thread 執行，不阻塞事件迴圈）
    flag = await asyncio.to_thread(redflag_screen, text)
    msg_id = db.add_message(pid, "patient", text,
                            redflag_level=flag.severity,
                            redflag_terms=flag.matched_keywords)

    if flag.severity == "high":
        await _notify({
            "type": "EMERGENCY_ALERT", "severity": "high",
            "patient_id": pid,                       # 只送假名代號，不送真名（資安審查 S2/S5）
            "channel": channel,
            "message": text,
            "keywords": flag.matched_keywords,
            "category": flag.category,
            "via": flag.via,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, pid, "；".join(flag.matched_keywords) or f"vector:{flag.via}", "high", msg_id)
        db.log_event("redflag_high", pid,
                     {"terms": flag.matched_keywords, "via": flag.via, "channel": channel,
                      "category": flag.category})
        reply = _emergency_reply(channel, flag.category)
        db.add_message(pid, "bot", reply, answer_quality="redflag_shortcut")
        return _response(pid, reply, is_emergency=True, keywords=flag.matched_keywords)

    # MEDIUM：推播（非緊急）＋進入未確認清單，不論之後走評估或衛教路徑都只記一次
    await _record_medium_alert(pid, flag, msg_id, channel, text)

    # ② 症狀結構化追問
    state = db.get_assessment_state(pid)
    if state:
        ar = assessment.advance(pid, state, text)
        if not ar.done:
            return _assess_reply(pid, ar, flag)
        if ar.escalate:
            return await _escalate(pid, ar, msg_id, channel)
        if ar.educate:
            lead = (f"了解了，您的「{_sym_name(ar.symptom)}」嚴重度是 {ar.score}/10。"
                    "以下提供一些照護建議：\n\n")
            return await _rag_answer(pid, _edu_query(ar), flag, msg_id, channel, lead=lead,
                                     code=ar.onc_code)
        return _assess_reply(pid, ar, flag)          # 病患中止評估
    proto = assessment.detect_symptom(text)
    if proto is not None:
        return _assess_reply(pid, assessment.start(pid, proto), flag)

    # ③ 一般 RAG 問答
    return await _rag_answer(pid, text, flag, msg_id, channel)


def _response(pid: str, reply: str, *, sources=None, is_emergency=False, keywords=None) -> ChatResponse:
    return ChatResponse(response=reply, sources=sources or [], is_emergency=is_emergency,
                        emergency_keywords=keywords or [], session_id=pid,
                        timestamp=datetime.now())


def _assess_reply(pid: str, ar, flag) -> ChatResponse:
    db.add_message(pid, "bot", ar.reply, answer_quality="assessment")
    return _response(pid, ar.reply, keywords=flag.matched_keywords)


async def _escalate(pid: str, ar, msg_id: int, channel: Channel) -> ChatResponse:
    name = _sym_name(ar.symptom)
    await _notify({
        "type": "SYMPTOM_ALERT", "severity": ar.escalate_level,
        "patient_id": pid, "channel": channel,
        "message": f"{name} 嚴重度 {ar.score}/10",
        "keywords": [ar.symptom], "via": "assessment",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, pid, f"{name} {ar.score}/10", ar.escalate_level, msg_id)
    if channel == "line":
        proto = assessment._BY_KEY.get(ar.symptom)
        field = assessment._fmt_field(proto, ar.extra) if proto else ""
        reply = escalation_reply("line", name, ar.score, f"，{field}" if field else "",
                                 ar.escalate_level, settings.EMERGENCY_CONTACT)
    else:
        reply = ar.reply
    db.add_message(pid, "bot", reply, answer_quality="assessment_escalated")
    return _response(pid, reply, is_emergency=True, keywords=[ar.symptom])


def _sym_name(key: str) -> str:
    p = assessment._BY_KEY.get(key)
    return p.name if p else key


def _edu_query(ar) -> str:
    """評估後衛教用的 RAG 查詢：用症狀名，命中對應 ONC 主題。"""
    return f"{_sym_name(ar.symptom)}的自我照顧與注意事項"


async def _rag_answer(pid: str, query_text: str, flag, msg_id: int, channel: Channel,
                      *, lead: str = "", code: str | None = None) -> ChatResponse:
    """RAG → 品質前置 → LLM → 品質標記 → 落庫。一般問答與評估後衛教共用。
    code：限制檢索在單一衛教單張（評估後衛教用，症狀→單張對應是確定的，不該靠語意猜）。"""
    profile = load_profile(pid)
    rag_docs = await asyncio.to_thread(retriever.query, query_text, code_filter=code)
    sources = [d["source"] for d in rag_docs]

    if not quality.pre_check(rag_docs):
        reply = deflect_reply(channel, settings.EMERGENCY_CONTACT)
        db.add_message(pid, "bot", reply, rag_sources=[], answer_quality="deflected_no_source")
        # 知識庫外提問進入護理端待處理清單（level=info，不推播、不承諾回覆）
        db.add_alert(pid, f"知識庫外提問：{query_text[:40]}", "info", msg_id, notified_channels=[])
        return _response(pid, reply, keywords=flag.matched_keywords)

    system_prompt = build_prompt(profile, rag_docs, channel=channel)
    history = db.get_history(pid, limit=12)
    # 病患這則訊息在流程開頭已落庫，會出現在 history 最後；query_text 又會再送一次 → 去重，避免重複
    if history and history[-1]["role"] == "patient" and history[-1]["content"] == query_text:
        history = history[:-1]
    model_id = settings.PRIMARY_MODEL

    try:
        result = await asyncio.to_thread(
            get_client().generate, system_prompt, history, query_text, model_id
        )
    except LLMError as e:
        logging.error(f"[LLM Error] {e}")
        es = str(e)
        if "429" in es or "RESOURCE_EXHAUSTED" in es:
            reply = "⚠️ 目前諮詢人數較多，請稍後再試。"
        elif "timeout" in es.lower():
            reply = "⚠️ 回應逾時，請重新提問。"
        else:
            reply = "⚠️ 系統暫時無法回應，請稍後再試。"
        reply += f"如有緊急狀況，{_help_text(channel)}"
        db.add_message(pid, "bot", reply, rag_sources=sources, model_id=model_id,
                       provider=settings.LLM_PROVIDER, answer_quality="llm_error")
        return _response(pid, lead + reply, sources=sources, keywords=flag.matched_keywords)

    label = "llm_refusal" if (result.refused or not result.text.strip()) else quality.classify(result.text, rag_docs)
    if label in ("llm_refusal", "deflected_unanswerable"):
        # 模型拒答、回傳空白，或依規則表示「資料無法回答」：不顯示模型的文字，
        # 改回固定轉介文字，並把問題放進護理端待處理清單
        reply = deflect_reply(channel, settings.EMERGENCY_CONTACT)
        db.add_message(pid, "bot", lead + reply, rag_sources=sources,
                       model_id=result.model_id, provider=result.provider,
                       prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
                       answer_quality=label)
        db.add_alert(pid, f"衛教資料無法回答：{query_text[:40]}", "info", msg_id, notified_channels=[])
        return _response(pid, lead + reply, sources=sources, keywords=flag.matched_keywords)

    reply = result.text
    db.add_message(pid, "bot", lead + reply, rag_sources=sources,
                   model_id=result.model_id, provider=result.provider,
                   prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
                   answer_quality=label)
    return _response(pid, lead + reply, sources=sources, keywords=flag.matched_keywords)


async def _record_medium_alert(pid: str, flag, msg_id: int, channel: Channel, text: str):
    """MEDIUM 紅旗：推播（非緊急）並落庫，進入護理端未確認清單。v1 只落庫、不通知任何人。"""
    if flag.severity != "medium":
        return
    await _notify({
        "type": "MEDIUM_FLAG", "severity": "medium",
        "patient_id": pid, "channel": channel, "message": text,
        "keywords": flag.matched_keywords, "category": flag.category, "via": flag.via,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, pid, "；".join(flag.matched_keywords) or f"vector:{flag.via}", "medium", msg_id)


def load_profile(patient_code: str) -> PatientProfile | None:
    """組 prompt 用的病患資料。刻意不讀 real_name：真名不該送進第三方 LLM（資安審查 S5）。"""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT pseudonym, age, cancer_type, diagnosis, medications, education_level "
            "FROM patients WHERE pseudonym=?", (patient_code,)
        ).fetchone()
    if not row:
        return None
    return PatientProfile(
        patient_id=row["pseudonym"],
        age=row["age"],
        diagnosis=json.loads(row["diagnosis"] or "[]"),
        medications=json.loads(row["medications"] or "[]"),
        education_level=row["education_level"] or "general",
    )
