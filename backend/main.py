"""
AI 腫瘤衛教機器人 FastAPI 主程式（google-genai SDK）
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from datetime import datetime
from google import genai
from google.genai import types

from config import settings
from models import ChatRequest, ChatResponse, PatientProfile
from alert import detect_emergency
from rag import retriever
from websocket_manager import manager
from prompt import build_prompt

# 初始化 Gemini client
gemini_client = genai.Client(api_key=settings.GOOGLE_API_KEY)

app = FastAPI(
    title="AI 腫瘤衛教機器人",
    version="1.0.0",
    description="基於 RAG + Gemini 的個人化癌症化療衛教系統"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 靜態前端
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# 記憶體 session store
sessions: dict[str, list] = {}
patient_profiles: dict[str, PatientProfile] = {}

# 合法代碼白名單
VALID_PATIENT_CODES = {"P001"}
VALID_RESEARCHER_CODES = {"R001"}


@app.on_event("startup")
async def seed_test_data():
    """啟動時植入測試帳號與示範對話"""
    # 測試病患 P001
    patient_profiles["P001"] = PatientProfile(
        patient_id="P001",
        name="測試病患",
        age=58,
        diagnosis=["口腔癌", "化學治療中"],
        medications=["Cisplatin", "5-Fluorouracil"],
        education_level="general",
    )
    # 植入兩輪示範對話，讓研究者介面一開始就有內容可看
    sessions["P001"] = [
        {"role": "user",    "content": "我化療之後嘴巴一直破，很痛怎麼辦？"},
        {"role": "assistant","content": "口腔黏膜炎是化療常見副作用。建議每天用溫鹽水漱口4-6次，避免辛辣燙食，保持嘴唇濕潤。嚴重疼痛時請告知護理師。"},
        {"role": "user",    "content": "那我可以喝果汁嗎？"},
        {"role": "assistant","content": "可以，但建議選擇不太酸的果汁（如蘋果汁），並用吸管飲用，減少刺激口腔黏膜。"},
    ]


@app.get("/verify/{code}")
async def verify_code(code: str):
    """前端登入驗證：回傳代碼對應角色"""
    code = code.strip().upper()
    if code in VALID_PATIENT_CODES:
        return {"valid": True, "role": "patient", "code": code}
    if code in VALID_RESEARCHER_CODES:
        return {"valid": True, "role": "researcher", "code": code}
    return {"valid": False, "role": None, "code": code}


@app.get("/")
async def serve_frontend():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "AI 腫瘤衛教機器人 API v1.0"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    session_id = request.patient_id
    if session_id not in sessions:
        sessions[session_id] = []

    # 1. 緊急關鍵字偵測
    alert = detect_emergency(request.message)

    # 2. RAG 查詢
    profile = patient_profiles.get(request.patient_id) or request.patient_profile
    rag_docs = retriever.query(request.message)

    # 3. 組裝個人化 System Prompt
    system_prompt = build_prompt(profile, rag_docs)

    # 4. 選擇模型
    model_id = settings.SECONDARY_MODEL if alert.is_emergency else settings.PRIMARY_MODEL

    # 5. 對話歷史（最近 6 輪）
    history = sessions[session_id][-12:]

    # 6. 組裝 contents（Gemma 不支援 system_instruction，改注入精簡指令）
    is_gemma = "gemma" in model_id.lower()
    contents = []

    if is_gemma:
        # Gemma：將精簡版指令注入成 user/model 對話開頭
        rag_context = "\n".join(
            f"[資料{i+1}] {d['content'][:200]}" for i, d in enumerate(rag_docs[:3])
        ) if rag_docs else "無相關衛教資料"
        gemma_instruction = (
            "你是腫瘤衛教護理師助理。安全規則：若提到胸痛/呼吸困難/想自傷，"
            "立即說「請按呼叫鈴」。不診斷、不建議調整藥量。"
            f"\n\n衛教資料：\n{rag_context}\n\n"
            "請用親切中文回答以下問題，若資料不足請誠實說明。"
        )
        contents.append(types.Content(
            role="user",
            parts=[types.Part.from_text(text=gemma_instruction)]
        ))
        contents.append(types.Content(
            role="model",
            parts=[types.Part.from_text(text="好的，我已了解。請問有什麼需要幫助的？")]
        ))

    # 加入對話歷史
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(
            role=role,
            parts=[types.Part.from_text(text=msg["content"])]
        ))

    # 最新使用者訊息
    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_text(text=request.message)]
    ))

    # 7. 呼叫 API（在 thread pool 執行，避免阻塞 async event loop）
    import asyncio

    def _call_api():
        if is_gemma:
            cfg = types.GenerateContentConfig(max_output_tokens=800, temperature=0.7)
        else:
            cfg = types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=800,
                temperature=0.7,
            )
        return gemini_client.models.generate_content(
            model=model_id, contents=contents, config=cfg
        )

    try:
        resp_obj = await asyncio.to_thread(_call_api)
        reply = resp_obj.text
    except Exception as e:
        import logging
        logging.error(f"[Gemma API Error] {e}")
        err_str = str(e)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            reply = "⚠️ 今日諮詢次數暫時用完，請明天再試。如有緊急狀況請按呼叫鈴通知護理師。"
        elif "timeout" in err_str.lower():
            reply = "⚠️ 回應逾時，請重新提問。如有緊急狀況請按呼叫鈴通知護理師。"
        else:
            reply = "⚠️ 系統暫時無法回應，請稍後再試或通知護理師。"

    # 8. 更新對話紀錄
    history.append({"role": "user", "content": request.message})
    history.append({"role": "assistant", "content": reply})
    sessions[session_id] = history

    # 9. 緊急事件 WebSocket 推播
    if alert.is_emergency:
        patient_name = profile.name if profile else request.patient_id
        await manager.broadcast_alert({
            "type": "EMERGENCY_ALERT",
            "severity": alert.severity,
            "patient_id": request.patient_id,
            "patient_name": patient_name,
            "message": request.message,
            "keywords": alert.matched_keywords,
            "timestamp": datetime.now().isoformat()
        })

    return ChatResponse(
        response=reply,
        sources=[d["source"] for d in rag_docs],
        is_emergency=alert.is_emergency,
        emergency_keywords=alert.matched_keywords,
        session_id=session_id,
        timestamp=datetime.now()
    )


@app.post("/patient/{patient_id}")
async def upsert_patient(patient_id: str, profile: PatientProfile):
    patient_profiles[patient_id] = profile
    return {"status": "ok", "patient_id": patient_id}


@app.get("/history/{patient_id}")
async def get_history(patient_id: str):
    return {
        "patient_id": patient_id,
        "messages": sessions.get(patient_id, [])
    }


@app.get("/sessions")
async def list_sessions():
    """研究者用：列出所有 session 摘要"""
    result = []
    for pid, msgs in sessions.items():
        profile = patient_profiles.get(pid)
        flags = []
        for msg in msgs:
            if msg["role"] == "user":
                a = detect_emergency(msg["content"])
                if a.is_emergency:
                    flags.append({
                        "content": msg["content"],
                        "severity": a.severity,
                        "keywords": a.matched_keywords
                    })

        result.append({
            "patient_id": pid,
            "patient_name": profile.name if profile else pid,
            "message_count": len(msgs),
            "red_flags": flags,
            "last_message": msgs[-1]["content"][:50] if msgs else "",
            "has_emergency": len(flags) > 0
        })
    return result


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
        "active_sessions": len(sessions),
        "nurse_connections": manager.connection_count
    }
