"""
AI 腫瘤衛教機器人 FastAPI 主程式（Phase 1：地基與安全層）

/chat 新流程（修正緊急延遲 bug）：
  ① 紅旗瞬間篩檢（redflag.screen）—— 不進 RAG/LLM，< 1 秒
     HIGH → 立刻 broadcast_alert + 立刻回固定安全文字 + 落庫 → return
  ② RAG 查詢
  ③ 品質前置：無合格來源 → 回「需請護理師」（不進 LLM）
  ④ LLM 生成（llm_client，供應商抽象）
  ⑤ 品質標記
  ⑥ 全部落 SQLite（含紅旗等級、RAG來源、model、tokens、品質）
     MEDIUM 紅旗在此併入 alerts
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse
from pathlib import Path
from datetime import datetime, timezone
import asyncio
import logging

from config import settings
from models import ChatRequest, ChatResponse, PatientProfile, LoginRequest
import db
import auth
import quality
import assessment
import stats
from redflag import screen as redflag_screen
from rag import retriever
from websocket_manager import manager
from prompt import build_prompt
from llm_client import get_client, LLMError

app = FastAPI(
    title="AI 腫瘤衛教機器人",
    version="1.1.0",
    description="RAG + LLM 個人化癌症化療衛教系統（Phase 1：持久化 + 紅旗 + 品質閘 + 認證）",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.on_event("startup")
async def startup():
    db.init_db()
    _seed_if_empty()


def _seed_if_empty():
    """首次啟動植入測試帳號與示範對話（僅在 DB 空時）。"""
    with db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    if n > 0:
        return
    # 測試帳號（正式環境請由管理者改密碼／重發）
    auth.create_user("P001", "patient123", "patient", pseudonym="P001",
                     real_name="測試病患", age=58, gender="男",
                     cancer_type="口腔癌", diagnosis=["口腔癌", "化學治療中"],
                     medications=["Cisplatin", "5-Fluorouracil"],
                     education_level="general")
    auth.create_user("R001", "research123", "researcher")
    auth.create_user("A001", "admin123", "admin")
    # 示範對話，讓研究者介面一開始有內容
    db.add_message("P001", "patient", "我化療之後嘴巴一直破，很痛怎麼辦？", redflag_level="none")
    db.add_message("P001", "bot",
                   "口腔黏膜炎是化療常見副作用。建議每天用溫鹽水漱口4-6次，避免辛辣燙食，保持嘴唇濕潤。嚴重疼痛時請告知護理師。",
                   rag_sources=["ONC-17 口腔黏膜炎"], answer_quality="grounded")
    logging.info("[seed] 已植入測試帳號 P001/R001/A001 與示範對話")


# ── 認證 ──────────────────────────────────────────────────────────
@app.post("/login")
async def login(req: LoginRequest):
    result = auth.authenticate(req.account.strip(), req.password)
    if not result:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    return result


def _require_role(token: str, *roles: str) -> dict:
    payload = auth.verify_token(token or "")
    if not payload:
        raise HTTPException(status_code=401, detail="請先登入")
    if payload["role"] not in roles:
        raise HTTPException(status_code=403, detail="權限不足")
    return payload


# ── 首頁 ──────────────────────────────────────────────────────────
@app.get("/")
async def serve_frontend():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "AI 腫瘤衛教機器人 API v1.1"}


# ── 對話 ──────────────────────────────────────────────────────────
@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    pid = request.patient_id
    db.ensure_session(pid)

    # ① 紅旗瞬間篩檢 —— 先於 RAG/LLM/評估
    flag = redflag_screen(request.message)
    msg_id = db.add_message(pid, "patient", request.message,
                            redflag_level=flag.severity,
                            redflag_terms=flag.matched_keywords)

    # ①-a HIGH：立刻推播 + 立刻回固定文字，不進 RAG/LLM/評估
    if flag.severity == "high":
        profile = _load_profile(pid) or request.patient_profile
        await manager.broadcast_alert({
            "type": "EMERGENCY_ALERT", "severity": "high",
            "patient_id": pid,
            "patient_name": profile.name if profile else pid,
            "message": request.message,
            "keywords": flag.matched_keywords,
            "via": flag.via,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        db.add_alert(pid, "；".join(flag.matched_keywords) or f"vector:{flag.via}",
                     "high", msg_id, notified_channels=["websocket"])
        db.log_event("redflag_high", pid, {"terms": flag.matched_keywords, "via": flag.via})
        reply = ("這個狀況需要馬上讓護理師或醫師知道，請按旁邊的呼叫鈴，"
                 "我已經同時通知護理站了。若情況緊急請直接撥打護理站電話或119。")
        db.add_message(pid, "bot", reply, answer_quality="redflag_shortcut")
        return ChatResponse(response=reply, sources=[], is_emergency=True,
                            emergency_keywords=flag.matched_keywords,
                            session_id=pid, timestamp=datetime.now())

    # ② 症狀結構化追問（Phase 2）
    state = db.get_assessment_state(pid)
    if state:                                   # 評估進行中 → 處理回覆
        ar = assessment.advance(pid, state, request.message)
        if not ar.done:                         # 還在問 → 回問題
            return _assess_reply(pid, ar, flag)
        if ar.escalate:                         # 嚴重 → 升級紅旗
            return await _escalate(pid, ar, msg_id)
        if ar.educate:                          # 未達門檻 → 走該症狀衛教
            lead = f"了解了，您的「{_sym_name(ar.symptom)}」嚴重度是 {ar.score}/10。以下提供一些照護建議：\n\n"
            return await _rag_answer(pid, _edu_query(ar), flag, msg_id, lead=lead)
        return _assess_reply(pid, ar, flag)     # 中止：回中止語
    else:                                       # 無進行中評估 → 是否偵測到新症狀
        proto = assessment.detect_symptom(request.message)
        if proto is not None:
            ar = assessment.start(pid, proto)
            return _assess_reply(pid, ar, flag)

    # ③ 一般 RAG 問答
    return await _rag_answer(pid, request.message, flag, msg_id,
                             profile=request.patient_profile)


def _assess_reply(pid: str, ar, flag) -> ChatResponse:
    db.add_message(pid, "bot", ar.reply, answer_quality="assessment")
    return ChatResponse(response=ar.reply, sources=[], is_emergency=False,
                        emergency_keywords=flag.matched_keywords,
                        session_id=pid, timestamp=datetime.now())


async def _escalate(pid: str, ar, msg_id: int) -> ChatResponse:
    profile = _load_profile(pid)
    await manager.broadcast_alert({
        "type": "SYMPTOM_ALERT", "severity": ar.escalate_level,
        "patient_id": pid, "patient_name": profile.name if profile else pid,
        "message": f"{_sym_name(ar.symptom)} 嚴重度 {ar.score}/10",
        "keywords": [ar.symptom], "via": "assessment",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    db.add_alert(pid, f"{ar.symptom}:{ar.score}", ar.escalate_level, msg_id,
                 notified_channels=["websocket"])
    db.add_message(pid, "bot", ar.reply, answer_quality="assessment_escalated")
    return ChatResponse(response=ar.reply, sources=[], is_emergency=True,
                        emergency_keywords=[ar.symptom],
                        session_id=pid, timestamp=datetime.now())


def _sym_name(key: str) -> str:
    p = assessment._BY_KEY.get(key)
    return p.name if p else key


def _edu_query(ar) -> str:
    """評估後衛教用的 RAG 查詢：用症狀名，命中對應 ONC 主題。"""
    return f"{_sym_name(ar.symptom)}的自我照顧與注意事項"


async def _rag_answer(pid: str, query_text: str, flag, msg_id: int,
                      *, profile=None, lead: str = "") -> ChatResponse:
    """RAG → 品質前置 → LLM → 品質標記 → 落庫。一般問答與評估後衛教共用。"""
    profile = _load_profile(pid) or profile
    rag_docs = retriever.query(query_text)

    # 品質前置：無來源 → 不進 LLM
    if not quality.pre_check(rag_docs):
        reply = quality.DEFLECT_TEXT
        db.add_message(pid, "bot", reply, rag_sources=[], answer_quality="deflected_no_source")
        _record_medium_alert(pid, flag, msg_id)
        return ChatResponse(response=reply, sources=[], is_emergency=False,
                            emergency_keywords=flag.matched_keywords,
                            session_id=pid, timestamp=datetime.now())

    system_prompt = build_prompt(profile, rag_docs)
    history = db.get_history(pid, limit=12)
    model_id = settings.PRIMARY_MODEL

    try:
        result = await asyncio.to_thread(
            get_client().generate, system_prompt, history, query_text, model_id
        )
        reply = result.text
        prompt_tokens, completion_tokens, provider = (
            result.prompt_tokens, result.completion_tokens, result.provider)
    except LLMError as e:
        logging.error(f"[LLM Error] {e}")
        es = str(e)
        if "429" in es or "RESOURCE_EXHAUSTED" in es:
            reply = "⚠️ 今日諮詢次數暫時用完，請明天再試。如有緊急狀況請按呼叫鈴通知護理師。"
        elif "timeout" in es.lower():
            reply = "⚠️ 回應逾時，請重新提問。如有緊急狀況請按呼叫鈴通知護理師。"
        else:
            reply = "⚠️ 系統暫時無法回應，請稍後再試或通知護理師。"
        db.add_message(pid, "bot", reply, rag_sources=[d["source"] for d in rag_docs],
                       model_id=model_id, provider=settings.LLM_PROVIDER, answer_quality="llm_error")
        _record_medium_alert(pid, flag, msg_id)
        return ChatResponse(response=lead + reply, sources=[d["source"] for d in rag_docs],
                            is_emergency=False, emergency_keywords=flag.matched_keywords,
                            session_id=pid, timestamp=datetime.now())

    q = quality.classify(reply, rag_docs)
    db.add_message(pid, "bot", lead + reply, rag_sources=[d["source"] for d in rag_docs],
                   model_id=model_id, provider=provider,
                   prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                   answer_quality=q)
    _record_medium_alert(pid, flag, msg_id)
    return ChatResponse(response=lead + reply, sources=[d["source"] for d in rag_docs],
                        is_emergency=False, emergency_keywords=flag.matched_keywords,
                        session_id=pid, timestamp=datetime.now())


def _record_medium_alert(pid: str, flag, msg_id: int):
    if flag.severity == "medium":
        db.add_alert(pid, "；".join(flag.matched_keywords) or f"vector:{flag.via}",
                     "medium", msg_id, notified_channels=[])


def _load_profile(patient_code: str) -> PatientProfile | None:
    import json
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT pseudonym, real_name, age, cancer_type, diagnosis, medications, education_level "
            "FROM patients WHERE pseudonym=?", (patient_code,)
        ).fetchone()
    if not row:
        return None
    return PatientProfile(
        patient_id=row["pseudonym"], name=row["real_name"] or "病患",
        age=row["age"],
        diagnosis=json.loads(row["diagnosis"] or "[]"),
        medications=json.loads(row["medications"] or "[]"),
        education_level=row["education_level"] or "general",
    )


# ── 研究者查詢 ────────────────────────────────────────────────────
@app.get("/history/{patient_id}")
async def get_history(patient_id: str):
    return {"patient_id": patient_id, "messages": db.get_patient_messages(patient_id)}


@app.get("/sessions")
async def list_sessions():
    return db.list_sessions_summary()


# ── 研究統計與匯出（Phase 3a，僅 researcher/admin）────────────────
@app.get("/stats/overview")
async def stats_overview(x_auth_token: str = Header(default="")):
    _require_role(x_auth_token, "researcher", "admin")
    return stats.cohort_overview()


@app.get("/stats/patient/{code}")
async def stats_patient(code: str, x_auth_token: str = Header(default="")):
    _require_role(x_auth_token, "researcher", "admin")
    return stats.patient_trends(code)


@app.get("/export/{table}.csv")
async def export_table(table: str, x_auth_token: str = Header(default="")):
    _require_role(x_auth_token, "researcher", "admin")
    try:
        content = stats.export_csv(table)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    db.log_event("export_csv", None, {"table": table})
    return PlainTextResponse(
        content, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'},
    )


@app.websocket("/ws/nurse")
async def nurse_websocket(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "rag_chunks": retriever.collection.count(),
        "llm_provider": settings.LLM_PROVIDER,
        "nurse_connections": manager.connection_count,
    }
