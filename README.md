# 腫瘤衛教機器人　Oncology Patient Education Chatbot

針對化學治療病患的 RAG 衛教問答系統。知識庫為三軍總醫院護理部血液腫瘤科的 13 份衛教單張，
檢索後由 LLM 依病患年齡／教育程度組裝個人化回答，並在偵測到緊急關鍵字時即時推播護理師。

> ⚠️ 本系統僅供衛教參考，不做診斷、不建議藥物劑量。碰真實病患前請先完成 IRB 與資料隱私規範
> （見 [`docs/specs/`](docs/specs/)）。

## 目前狀態：Phase 1（地基與安全層）

本版重點是把三個地基問題補穩，讓後續的症狀追問（Phase 2）與研究統計（Phase 3）有可靠基礎：

| 問題 | 修正 |
|---|---|
| 緊急警示延遲（偵測到紅旗卻等 LLM 跑完才通知） | `/chat` 改為**紅旗先篩、命中即刻推播+回覆**，不進 RAG/LLM |
| 資料易失（session 存記憶體，重啟清空） | SQLite 持久化（`backend/db.py`），身分與研究資料分表（去識別化） |
| 認證形同虛設（猜到 R001 就能看光對話） | 帳號 + 雜湊密碼 + 角色（`backend/auth.py`），由管理者發放 |

另含：回答品質閘（無來源不編造）、LLM 供應商抽象（Gemma → Sonnet 5 換一個環境變數）、
紅旗改用「關鍵字為底 + 否定處理 + 向量補充」。

完整設計見 [`docs/specs/2026-07-16-phase1-foundation-design.md`](docs/specs/2026-07-16-phase1-foundation-design.md)。

## 架構

```
病患訊息
  → ① 紅旗瞬間篩檢（redflag.py）  HIGH → 立刻推播+固定回覆 → 結束
  → ② RAG 查詢（rag.py + chroma_db）
  → ③ 品質前置（quality.py）      無來源 → 轉介護理師
  → ④ LLM 生成（llm_client.py）
  → ⑤ 品質標記 → ⑥ 全部落 SQLite（db.py）
```

| 模組 | 職責 |
|---|---|
| `backend/main.py` | FastAPI，`/chat` 流程與 API 路由 |
| `backend/db.py` | SQLite 持久化，身分與研究資料分表 |
| `backend/auth.py` | 帳密雜湊 + 角色（patient/researcher/admin） |
| `backend/redflag.py` | 紅旗偵測：關鍵字 + 否定 + 向量補充 |
| `backend/quality.py` | 回答品質閘 |
| `backend/llm_client.py` | LLM 供應商抽象（google / anthropic） |
| `backend/rag.py` | 向量檢索 |
| `backend/embedding.py` | 共用嵌入層，torch → ONNX 自動 fallback |
| `scripts/index_vault.py` | 從 Markdown 知識庫建立向量索引 |
| `frontend/index.html` | 單頁前端（登入 / 病患聊天 / 研究者儀表板） |

## 快速開始

```bash
# 1. 安裝套件
cd backend && pip install -r requirements.txt

# 2. 設定 .env（專案根目錄）
GOOGLE_API_KEY=<你的 Google AI Studio API Key>
LLM_PROVIDER=google
PRIMARY_MODEL=gemma-3-12b-it

# 3. 建立向量索引（首次會下載嵌入模型）
python -X utf8 scripts/index_vault.py --rebuild

# 4. 啟動
cd backend && python -X utf8 -m uvicorn main:app --port 8000

# 5. 瀏覽器開 http://localhost:8000
```

### 測試帳號（首次啟動自動建立）

| 帳號 | 密碼 | 角色 |
|---|---|---|
| P001 | patient123 | 病患 |
| R001 | research123 | 研究者 |
| A001 | admin123 | 管理者 |

> 正式環境請立即更換這些密碼。

## 測試

```bash
cd backend
python -X utf8 test_phase1.py        # 地基單元測試（db/auth/redflag/quality），不需 API key
python -X utf8 test_integration.py   # 整合測試（啟動/登入/紅旗/品質閘），不需 API key
```

## 換成 Claude Sonnet 5

```bash
# .env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=<你的 key>
PRIMARY_MODEL=claude-sonnet-5
```

不需改任何程式。換 Sonnet 5 同時解決「免費 Google 方案會用對話訓練模型」的隱私問題。

## 安全須知

- **`.env` 不進版控**（`.gitignore` 已排除）。若曾外流過任何 API key，請立即至供應商後台撤銷重發。
- **`chroma_db/`、`models/`、`data/*.sqlite3` 不進版控** —— 向量庫可由 `scripts/index_vault.py` 重建，
  嵌入模型另行下載，SQLite 含病患資料絕不上傳。
- 認證目前為帳密 + 雜湊 + 簽章 token，足夠研究用；正式醫療環境建議升級 JWT/OAuth2。

## 藍圖

- **Phase 1（本版）**：持久化 + 紅旗 + 品質閘 + 認證 ✅
- **Phase 2**：ESAS-r 症狀結構化追問 → 分級升級紅旗
- **Phase 3**：症狀趨勢圖 + CSV 匯出、主動定時回報、知識測驗前後測、Telegram 主持人通知
