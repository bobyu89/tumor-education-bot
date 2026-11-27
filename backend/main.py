"""
AI 腫瘤衛教機器人 FastAPI 主程式 —— 只放路由。

- 身分：一律經 security.py 取得（token 或 LINE 綁定），請求 body 不能自報身分
- 病患訊息處理：chat_service.process_message（網頁與 LINE 共用）
- 研究者每次查看病患資料都記入 events（actor = 帳號），供存取稽核
"""
from fastapi import (BackgroundTasks, Depends, FastAPI, HTTPException, Query,
                     Request, WebSocket, WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import json
import logging
import secrets
from datetime import datetime, timezone

import activity
import auth
import binding
import db
import departments
import leaflets
import line_bot
import quiz
import stats
from chat_service import process_message
from config import settings
from models import (ChatRequest, ChatResponse, LeafletAction, LeafletDraftUpdate, LeafletUpload, LoginRequest,
                    PatientCreate, QuizSubmit)
from rag import retriever
from security import (admin_user, check_patient_code, is_valid_patient_code,
                      patient_user, staff_from_ws_ticket, staff_user)
from websocket_manager import manager

app = FastAPI(
    title="AI 腫瘤衛教機器人",
    version="1.2.0",
    description="RAG + LLM 個人化癌症化療衛教系統",
)

# 前端由同一個服務提供（同網域），預設不需要 CORS。只有前端另外部署時才開白名單。
_cors = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
if _cors:
    app.add_middleware(
        CORSMiddleware, allow_origins=_cors,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Auth-Token"],
    )

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.on_event("startup")
async def startup():
    db.init_db()
    _bootstrap_accounts()


def _bootstrap_accounts():
    """正式環境不植入任何預設帳密（資安審查 S6）。
    - DEMO_SEED=true：本機開發用，植入 P001/R001/A001 示範帳號
    - ADMIN_PASSWORD 有值且尚無管理者：建立第一個管理者
    """
    if settings.DEMO_SEED:
        _seed_demo_if_empty()
    with db.get_conn() as conn:
        has_admin = conn.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone()
        has_any = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    if not has_admin and settings.ADMIN_PASSWORD:
        auth.create_user(settings.ADMIN_ACCOUNT, settings.ADMIN_PASSWORD, "admin",
                         created_by="bootstrap")
        logging.info(f"[bootstrap] 已建立管理者帳號 {settings.ADMIN_ACCOUNT}")
    elif not has_any:
        logging.warning("[bootstrap] 尚無任何帳號。請在 .env 設定 ADMIN_PASSWORD，"
                        "或本機開發時設定 DEMO_SEED=true。")


def _seed_demo_if_empty():
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return
    auth.create_user("P001", "patient123", "patient", pseudonym="P001",
                     real_name="測試病患", age=58, gender="男",
                     cancer_type="口腔癌", diagnosis=["口腔癌", "化學治療中"],
                     medications=["Cisplatin", "5-Fluorouracil"],
                     education_level="general", created_by="demo_seed")
    auth.create_user("R001", "research123", "researcher", created_by="demo_seed")
    auth.create_user("A001", "admin123", "admin", created_by="demo_seed")
    db.add_message("P001", "patient", "我化療之後嘴巴一直破，很痛怎麼辦？", redflag_level="none")
    db.add_message("P001", "bot",
                   "口腔黏膜炎是化療常見副作用。建議每天用溫鹽水漱口4-6次，避免辛辣燙食，"
                   "保持嘴唇濕潤。嚴重疼痛時請告知護理師。",
                   rag_sources=["ONC-17 口腔黏膜炎"], answer_quality="grounded")
    logging.warning("[demo] 已植入示範帳號 P001/R001/A001（僅限本機，勿用於正式環境）")


def _audit(user: dict, event: str, patient_code: str | None = None, **payload):
    """記錄研究者/管理者對資料的存取。"""
    db.log_event(event, patient_code, payload, actor=user["account"])


# ── 認證 ──────────────────────────────────────────────────────────
@app.post("/login")
async def login(req: LoginRequest):
    try:
        result = auth.authenticate(req.account.strip(), req.password)
    except auth.LoginLocked:
        raise HTTPException(status_code=429,
                            detail=f"登入失敗次數過多，請 {settings.LOGIN_LOCK_MINUTES} 分鐘後再試，或聯絡管理者")
    if not result:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    return result


@app.get("/")
async def serve_frontend():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "AI 腫瘤衛教機器人 API"}


# ── 病患 ──────────────────────────────────────────────────────────
@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user: dict = Depends(patient_user)):
    return await process_message(user["patient_code"], req.message, channel="web")


@app.get("/quiz/topics")
async def quiz_topics():
    return quiz.topics()


@app.get("/quiz/{onc_code}")
async def quiz_get(onc_code: str):
    q = quiz.get_questions(onc_code)
    if not q:
        raise HTTPException(status_code=404, detail="無此主題測驗")
    return q


@app.post("/quiz/{onc_code}")
async def quiz_submit(onc_code: str, req: QuizSubmit, user: dict = Depends(patient_user)):
    try:
        result = quiz.score(user["patient_code"], onc_code, req.answers, req.phase)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="無此主題測驗")
    return result


# ── 研究者 / 管理者 ───────────────────────────────────────────────
@app.get("/sessions")
async def list_sessions(user: dict = Depends(staff_user)):
    _audit(user, "view_sessions")
    return db.list_sessions_summary()


@app.get("/history/{patient_code}")
async def get_history(patient_code: str, user: dict = Depends(staff_user)):
    check_patient_code(patient_code)
    _audit(user, "view_history", patient_code)
    return {"patient_code": patient_code, "messages": db.get_patient_messages(patient_code)}


@app.get("/stats/overview")
async def stats_overview(user: dict = Depends(staff_user)):
    return stats.cohort_overview()


@app.get("/stats/patient/{patient_code}")
async def stats_patient(patient_code: str, user: dict = Depends(staff_user)):
    check_patient_code(patient_code)
    _audit(user, "view_trends", patient_code)
    return stats.patient_trends(patient_code)


@app.get("/export/{table}.csv")
async def export_table(table: str, user: dict = Depends(staff_user)):
    try:
        content = stats.export_csv(table)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _audit(user, "export_csv", table=table)
    return PlainTextResponse(
        content, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'},
    )


# ── 警示確認（護理端）──────────────────────────────────────────────
def _sla_minutes(level: str) -> int:
    return {"high": settings.ALERT_SLA_HIGH_MIN, "medium": settings.ALERT_SLA_MEDIUM_MIN}.get(
        level, settings.ALERT_SLA_INFO_MIN)


@app.get("/alerts")
async def list_alerts(status: str = Query(default="open", pattern="^(open|acked|all)$"),
                      user: dict = Depends(staff_user)):
    """未確認（或全部）警示，high 優先。overdue＝超過確認時限仍未確認（時限見 config.ALERT_SLA_*）。"""
    _audit(user, "view_alerts", status=status)
    now = datetime.now(timezone.utc)
    items = db.list_alerts(status)
    for a in items:
        age = (now - datetime.fromisoformat(a["ts"])).total_seconds() / 60
        a["age_min"] = int(age)
        a["sla_min"] = _sla_minutes(a["level"])
        a["overdue"] = a["ack_at"] is None and age > a["sla_min"]
    return {"status": status, "nurse_connections": manager.connection_count, "items": items}


@app.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: int, user: dict = Depends(staff_user)):
    """確認警示（表示已有人處理）。只能確認一次，保留第一位確認者。"""
    result = db.ack_alert(alert_id, user["account"])
    if result == "not_found":
        raise HTTPException(status_code=404, detail="無此警示")
    if result == "already":
        raise HTTPException(status_code=409, detail="此警示已被確認")
    _audit(user, "ack_alert", alert_id=alert_id)
    return {"ok": True, "alert_id": alert_id, "ack_by": user["account"]}


# ── 活動 LOG ──────────────────────────────────────────────────────
@app.get("/logs/patient/{patient_code}")
async def patient_log(patient_code: str, user: dict = Depends(staff_user)):
    """一位病患的完整活動時間軸（含誰看過他的資料）。查看本身也會被記錄。"""
    check_patient_code(patient_code)
    _audit(user, "view_activity_log", patient_code)
    return {"patient_code": patient_code, "items": activity.patient_activity(patient_code)}


@app.get("/logs/staff")
async def staff_log(account: str | None = None, user: dict = Depends(admin_user)):
    """研究者/管理者的操作紀錄，限管理者。"""
    _audit(user, "view_staff_audit", account=account)
    return {"account": account, "items": activity.staff_audit(account)}


# ── 管理者：病患與 LINE 通行碼 ────────────────────────────────────
@app.get("/admin/patients")
async def admin_list_patients(user: dict = Depends(admin_user)):
    return binding.list_patients()


@app.post("/admin/patients", status_code=201)
async def admin_create_patient(req: PatientCreate, user: dict = Depends(admin_user)):
    """建立病患。平台不收真名：代號與真人的對照由研究團隊另外保管（資料最小化）。
    病患走 LINE，不需要網頁密碼，所以設一組隨機密碼。"""
    if not is_valid_patient_code(req.pseudonym):
        raise HTTPException(status_code=400, detail="代號格式需為 P 加 3–6 位數字，例如 P012")
    if binding.patient_exists(req.pseudonym):
        raise HTTPException(status_code=409, detail="此代號已存在")
    if req.department and not departments.exists(req.department):
        raise HTTPException(status_code=400, detail=f"未知的科別：{req.department}")
    auth.create_user(req.pseudonym, secrets.token_urlsafe(24), "patient",
                     pseudonym=req.pseudonym, created_by=user["account"],
                     cancer_type=req.cancer_type, age=req.age, gender=req.gender,
                     education_level=req.education_level,
                     department=req.department or settings.DEFAULT_DEPARTMENT)
    return {"pseudonym": req.pseudonym, "department": req.department or settings.DEFAULT_DEPARTMENT}


@app.get("/departments")
async def list_departments(user: dict = Depends(staff_user)):
    """可用科別（knowledge/*/manifest.json），供建立病患時選擇。"""
    return [{"id": d.id, "name": d.name, "default": d.id == settings.DEFAULT_DEPARTMENT}
            for d in departments.load_all().values()]


@app.post("/admin/patients/{patient_code}/binding-code")
async def admin_issue_code(patient_code: str, user: dict = Depends(admin_user)):
    """發一組一次性通行碼。明碼只在這次回應出現，資料庫只存雜湊。"""
    check_patient_code(patient_code)
    try:
        return binding.issue_code(patient_code, user["account"])
    except LookupError:
        raise HTTPException(status_code=404, detail="查無此病患")


@app.delete("/admin/patients/{patient_code}/binding")
async def admin_unbind(patient_code: str, user: dict = Depends(admin_user)):
    check_patient_code(patient_code)
    if not binding.unbind(patient_code, user["account"]):
        raise HTTPException(status_code=404, detail="此病患目前沒有綁定 LINE")
    return {"unbound": patient_code}


# ── 管理者：單張管理（上傳 → 預覽 → 上架／停用／退回）─────────────
def _leaflet_call(fn, *args, **kwargs):
    """leaflets 模組的錯誤轉成 HTTP 狀態碼：可修正的問題 400、找不到 404。"""
    try:
        return fn(*args, **kwargs)
    except leaflets.LeafletError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _chunk_counts(dept: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    if retriever.collection.count():
        for m in retriever.collection.get(where={"dept": dept}, include=["metadatas"])["metadatas"]:
            counts[m.get("code", "")] = counts.get(m.get("code", ""), 0) + 1
    return counts


@app.get("/admin/leaflets")
async def admin_list_leaflets(dept: str = Query(default=None), user: dict = Depends(admin_user)):
    dept = dept or settings.DEFAULT_DEPARTMENT
    return {"dept": dept, "items": _leaflet_call(leaflets.list_leaflets, dept, _chunk_counts(dept))}


@app.get("/admin/leaflets/{dept}/{code}/versions")
async def admin_leaflet_versions(dept: str, code: str, user: dict = Depends(admin_user)):
    item = next((x for x in _leaflet_call(leaflets.list_leaflets, dept) if x["code"] == code.upper()), None)
    return {"dept": dept, "code": code.upper(), "current": item,
            "versions": _leaflet_call(leaflets.versions, dept, code)}


@app.post("/admin/leaflets/drafts", status_code=201)
async def admin_upload_leaflet(req: LeafletUpload, user: dict = Depends(admin_user)):
    meta = req.model_dump(include={"code", "title", "topic", "category", "doc_version", "source"})
    return _leaflet_call(leaflets.create_draft, req.dept, req.filename, req.content_b64, meta, user["account"])


@app.get("/admin/leaflets/drafts/{version_id}")
async def admin_get_draft(version_id: int, user: dict = Depends(admin_user)):
    return _leaflet_call(leaflets.get_draft, version_id)


@app.put("/admin/leaflets/drafts/{version_id}")
async def admin_update_draft(version_id: int, req: LeafletDraftUpdate, user: dict = Depends(admin_user)):
    meta = {k: v for k, v in req.model_dump(exclude={"body", "reviewed"}).items() if v is not None}
    return _leaflet_call(leaflets.update_draft, version_id, meta, req.body, req.reviewed)


@app.delete("/admin/leaflets/drafts/{version_id}")
async def admin_discard_draft(version_id: int, user: dict = Depends(admin_user)):
    _leaflet_call(leaflets.discard_draft, version_id, user["account"])
    return {"discarded": version_id}


@app.post("/admin/leaflets/drafts/{version_id}/publish")
async def admin_publish_leaflet(version_id: int, req: LeafletAction, user: dict = Depends(admin_user)):
    """上架並同步向量索引（約數秒）：下一則病人提問就用新版。"""
    import asyncio
    return await asyncio.to_thread(_leaflet_call, leaflets.publish, version_id, user["account"],
                                   req.reviewed, req.note)


@app.post("/admin/leaflets/{dept}/{code}/retire")
async def admin_retire_leaflet(dept: str, code: str, req: LeafletAction, user: dict = Depends(admin_user)):
    import asyncio
    return await asyncio.to_thread(_leaflet_call, leaflets.retire, dept, code, user["account"], req.note)


@app.post("/admin/leaflets/{dept}/{code}/restore")
async def admin_restore_leaflet(dept: str, code: str, req: LeafletAction, user: dict = Depends(admin_user)):
    import asyncio
    return await asyncio.to_thread(_leaflet_call, leaflets.restore, dept, code, req.version_id,
                                   user["account"], req.note)


@app.get("/admin/leaflets/versions/{version_id}/original")
async def admin_leaflet_original(version_id: int, user: dict = Depends(admin_user)):
    path, name = _leaflet_call(leaflets.original_file, version_id)
    return FileResponse(path, filename=name)


# ── LINE webhook（病患端）─────────────────────────────────────────
@app.post("/line/webhook")
async def line_webhook(request: Request, background: BackgroundTasks):
    """立刻回 200，事件交給背景處理（LINE 要求快速回應，LLM 回答則可能要數十秒）。"""
    if not line_bot.enabled():
        raise HTTPException(status_code=404)     # 未設定 LINE 金鑰時當作不存在
    body = await request.body()
    if not line_bot.verify_signature(body, request.headers.get("X-Line-Signature", "")):
        raise HTTPException(status_code=400, detail="invalid signature")
    try:
        events = json.loads(body).get("events", [])
    except (json.JSONDecodeError, AttributeError):
        raise HTTPException(status_code=400, detail="invalid body")
    for event in events:
        event_id = event.get("webhookEventId")
        if event_id and not line_bot.first_seen(event_id):
            continue                              # LINE 重送的事件，已處理過
        background.add_task(line_bot.handle_event, event)
    return {"status": "ok"}


@app.post("/ws/ticket")
async def ws_ticket(user: dict = Depends(staff_user)):
    """取得 60 秒有效的 WebSocket 連線票券（網址裡只放票券，不放登入 token）。"""
    return {"ticket": auth.make_ws_ticket(user)}


@app.websocket("/ws/nurse")
async def nurse_websocket(websocket: WebSocket, ticket: str = Query(default="")):
    # 瀏覽器的 WebSocket 不能帶自訂 header → 用 ?ticket= 短效票券驗證（資安審查 S2；2026-11 同儕審查）
    if staff_from_ws_ticket(ticket) is None:
        await websocket.close(code=1008)
        return
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/health")
async def health():
    return {"status": "ok", "rag_chunks": retriever.collection.count(),
            "nurse_connections": manager.connection_count}
