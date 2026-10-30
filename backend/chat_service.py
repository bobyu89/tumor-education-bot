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
from websocket_manager import manager

Channel = str   # "web" | "line"


def _help_text(channel: Channel) -> str:
    if channel == "line":
        return f"{settings.EMERGENCY_CONTACT}。"
    return "請按旁邊的呼叫鈴通知護理師。"


def _emergency_reply(channel: Channel) -> str:
    if channel == "line":
        return ("這個狀況需要立即處理，請不要等待訊息回覆。"
                f"{settings.EMERGENCY_CONTACT}。系統已記錄並通知醫療團隊。")
    return ("這個狀況需要馬上讓護理師或醫師知道，請按旁邊的呼叫鈴，"
            "系統已同時發出警示。若情況緊急請直接撥打護理站電話或119。")


def _escalation_reply(channel: Channel, symptom_name: str, score) -> str | None:
    if channel == "line":
        return (f"您的「{symptom_name}」聽起來比較嚴重（嚴重度 {score}/10）。"
                "系統已記錄並通知醫療團隊，但請不要等待回覆，"
                f"{settings.EMERGENCY_CONTACT}。")
    return None   # 網頁沿用 assessment 原本的回覆


async def process_message(pid: str, text: str, channel: Channel = "web") -> ChatResponse:
    db.ensure_session(pid)

    # ① 紅旗瞬間篩檢
    flag = redflag_screen(text)
    msg_id = db.add_message(pid, "patient", text,
                            redflag_level=flag.severity,
                            redflag_terms=flag.matched_keywords)

    if flag.severity == "high":
        await manager.broadcast_alert({
            "type": "EMERGENCY_ALERT", "severity": "high",
            "patient_id": pid,                       # 只送假名代號，不送真名（資安審查 S2/S5）
            "channel": channel,
            "message": text,
            "keywords": flag.matched_keywords,
            "via": flag.via,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        db.add_alert(pid, "；".join(flag.matched_keywords) or f"vector:{flag.via}",
                     "high", msg_id, notified_channels=["websocket"])
        db.log_event("redflag_high", pid,
                     {"terms": flag.matched_keywords, "via": flag.via, "channel": channel})
        reply = _emergency_reply(channel)
        db.add_message(pid, "bot", reply, answer_quality="redflag_shortcut")
        return _response(pid, reply, is_emergency=True, keywords=flag.matched_keywords)

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
            return await _rag_answer(pid, _edu_query(ar), flag, msg_id, channel, lead=lead)
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
    await manager.broadcast_alert({
        "type": "SYMPTOM_ALERT", "severity": ar.escalate_level,
        "patient_id": pid, "channel": channel,
        "message": f"{_sym_name(ar.symptom)} 嚴重度 {ar.score}/10",
        "keywords": [ar.symptom], "via": "assessment",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    db.add_alert(pid, f"{_sym_name(ar.symptom)} {ar.score}/10", ar.escalate_level, msg_id,
                 notified_channels=["websocket"])
    reply = _escalation_reply(channel, _sym_name(ar.symptom), ar.score) or ar.reply
    db.add_message(pid, "bot", reply, answer_quality="assessment_escalated")
    return _response(pid, reply, is_emergency=True, keywords=[ar.symptom])


def _sym_name(key: str) -> str:
    p = assessment._BY_KEY.get(key)
    return p.name if p else key


def _edu_query(ar) -> str:
    """評估後衛教用的 RAG 查詢：用症狀名，命中對應 ONC 主題。"""
    return f"{_sym_name(ar.symptom)}的自我照顧與注意事項"


async def _rag_answer(pid: str, query_text: str, flag, msg_id: int, channel: Channel,
                      *, lead: str = "") -> ChatResponse:
    """RAG → 品質前置 → LLM → 品質標記 → 落庫。一般問答與評估後衛教共用。"""
    profile = load_profile(pid)
    rag_docs = retriever.query(query_text)
    sources = [d["source"] for d in rag_docs]

    if not quality.pre_check(rag_docs):
        reply = quality.DEFLECT_TEXT
        db.add_message(pid, "bot", reply, rag_sources=[], answer_quality="deflected_no_source")
        _record_medium_alert(pid, flag, msg_id)
        return _response(pid, reply, keywords=flag.matched_keywords)

    system_prompt = build_prompt(profile, rag_docs)
    history = db.get_history(pid, limit=12)
    model_id = settings.PRIMARY_MODEL

    try:
        result = await asyncio.to_thread(
            get_client().generate, system_prompt, history, query_text, model_id
        )
    except LLMError as e:
        logging.error(f"[LLM Error] {e}")
        es = str(e)
        if "429" in es or "RESOURCE_EXHAUSTED" in es:
            reply = "⚠️ 今日諮詢次數暫時用完，請明天再試。"
        elif "timeout" in es.lower():
            reply = "⚠️ 回應逾時，請重新提問。"
        else:
            reply = "⚠️ 系統暫時無法回應，請稍後再試。"
        reply += f"如有緊急狀況，{_help_text(channel)}"
        db.add_message(pid, "bot", reply, rag_sources=sources, model_id=model_id,
                       provider=settings.LLM_PROVIDER, answer_quality="llm_error")
        _record_medium_alert(pid, flag, msg_id)
        return _response(pid, lead + reply, sources=sources, keywords=flag.matched_keywords)

    reply = result.text
    db.add_message(pid, "bot", lead + reply, rag_sources=sources,
                   model_id=model_id, provider=result.provider,
                   prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
                   answer_quality=quality.classify(reply, rag_docs))
    _record_medium_alert(pid, flag, msg_id)
    return _response(pid, lead + reply, sources=sources, keywords=flag.matched_keywords)


def _record_medium_alert(pid: str, flag, msg_id: int):
    if flag.severity == "medium":
        db.add_alert(pid, "；".join(flag.matched_keywords) or f"vector:{flag.via}",
                     "medium", msg_id, notified_channels=[])


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
