# 腫瘤衛教機器人　Oncology Patient Education Chatbot

針對化學治療病患的 RAG 衛教問答系統。知識庫為三軍總醫院護理部血液腫瘤科的 13 份衛教單張，
檢索後由 LLM 依病患年齡／教育程度組裝個人化回答，並在偵測到緊急關鍵字時即時推播護理師。

> ⚠️ 本系統僅供衛教參考，不做診斷、不建議藥物劑量。碰真實病患前請先完成 IRB 與資料隱私規範
> （見 [`docs/specs/`](docs/specs/)）。

**線上演示（GitHub Pages，純前端）**：<https://bobyu89.github.io/tumor-education-bot/> —— 紅旗攔截、症狀追問、
有來源的衛教回覆與護理站即時警示都在瀏覽器內模擬，不需安裝、不需 API key。頁面由 `scripts/build_pages.py` 產生資料、
`docs/demo_engine.js` 移植後端規則（`node scripts/test_pages_engine.js` 驗證）。

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
cd backend && pip install -r requirements.txt && cd ..

# 2. 設定 .env（專案根目錄；.env 不進版控）
cp .env.example .env
#    預設 LLM_PROVIDER=mock（演示模式，不需 API key）
#    要用真實模型：LLM_PROVIDER=google + GOOGLE_API_KEY=<Google AI Studio 金鑰>
#    正式環境務必設定：AUTH_SECRET（固定後重啟不會把大家登出）、
#                      ADMIN_PASSWORD（尚無管理者時，啟動會建立帳號 admin）

# 3. 建立向量索引（首次會下載嵌入模型）
python -X utf8 scripts/index_vault.py --rebuild

# 4.（可選）植入模擬案例：5 位虛構病患、兩週對話／症狀／警示／測驗
python -X utf8 scripts/seed_demo.py --reset

# 5. 啟動（本機遊玩可直接執行 start.ps1）
cd backend && python -X utf8 -m uvicorn main:app --port 8000

# 6. 瀏覽器開 http://localhost:8000
```

### 演示（不需 API key）

完整演示腳本見 [`docs/demo/演示腳本.md`](docs/demo/演示腳本.md)：9 個情境（一般問答、症狀評估、
升級警示、HIGH 紅旗、否定語境、自傷語意、知識庫外轉介、測驗前後測、研究者匯出），
每個情境給輸入原文與預期畫面。`python -X utf8 scripts/demo_dry_run.py` 會把全部情境自動跑一遍當彩排。

演示模式（`LLM_PROVIDER=mock`）只把「生成」換成本地規則從檢索段落組裝，紅旗／評估／RAG／品質閘／落庫
與正式模式完全相同；換真實模型只改 `.env`。

### 測試帳號（僅限本機）

本機要有帳號可登入，二擇一：
- 執行 `scripts/seed_demo.py --reset`：建立下列帳號與兩週模擬資料
- 或用 `start.ps1` 啟動（會設定 `DEMO_SEED=true`）：首次啟動只建立 P001／R001／A001

| 帳號 | 密碼 | 角色 |
|---|---|---|
| P001–P005 | patient123 | 病患（模擬，各有不同年齡／癌別／語氣） |
| R001 | research123 | 研究者 |
| A001 | admin123 | 管理者 |

> **正式環境預設不建立任何預設帳號。** 第一個管理者由 `.env` 的 `ADMIN_PASSWORD` 建立，
> 其餘帳號由管理者發放。

## 測試

```bash
cd backend
python -X utf8 test_security.py      # 資安回歸測試（對應資安審查各項）+ 前後端欄位契約
python -X utf8 test_phase1.py        # 地基單元測試（db/auth/redflag/quality）
python -X utf8 test_phase2.py        # 症狀追問
python -X utf8 test_phase3.py        # 研究統計與匯出
python -X utf8 test_quiz.py          # 知識測驗
python -X utf8 test_integration.py   # 整合測試（啟動/登入/紅旗/品質閘）
```

全部不需 API key，也不會呼叫外部 LLM。

## LINE 病患端

架構：**病患用 LINE**，研究者與管理者用網頁。兩個管道共用同一條處理流程（紅旗 → 症狀追問 → RAG）。

### 綁定流程

1. 管理者在網頁「管理者專區 → 病患與 LINE 綁定」新增病患（只填代號與研究變項，**不收真名**）
2. 按「發通行碼」→ 畫面顯示 8 碼通行碼（如 `K7RM-4QXP`），**只顯示一次**，交給病患
3. 病患加入 LINE 官方帳號，在聊天室輸入通行碼 → 綁定完成，之後直接對話

通行碼 72 小時有效、限用一次；同一 LINE 帳號 15 分鐘內輸錯 5 次會暫時鎖定。

### 啟用

```bash
# .env
LINE_CHANNEL_SECRET=<LINE Developers → Basic settings>
LINE_CHANNEL_ACCESS_TOKEN=<LINE Developers → Messaging API → Issue>
EMERGENCY_CONTACT=請立即撥打 119，或聯絡血液腫瘤科 (02) 8792-7056   # 請與醫院確認
```

Webhook URL 填 `https://<你的網域>/line/webhook`（LINE 只接受 HTTPS）。兩個金鑰都沒設時，
這個端點會回 404，等同不存在。

- 居家病患沒有呼叫鈴，LINE 管道的緊急回覆改為撥打電話，且不承諾「護理師會回覆」
- 衛教回覆以 Flex 訊息送出，手機通知預覽只顯示「您有一則新的衛教回覆」，不露出症狀
- 尚未綁定的人若傳來急症訊息，仍會先給緊急指引

## 每人活動 LOG

- **病患 LOG**（研究者、管理者）：研究者頁選病患 → 「📋 LOG」。對話、紅旗、症狀分數、測驗、
  LINE 綁定，以及**誰在何時看過他的資料**
- **人員稽核**（僅管理者）：每位研究者／管理者的登入、登入失敗、查看了誰、匯出了什麼、發了哪些通行碼

查看 LOG 這個動作本身也會被記錄。

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
- **身分一律來自登入 token**（網頁）或 LINE 綁定，請求內容不能自報病患代號。
  研究者頁、WebSocket 警示、匯出都需要研究者以上權限。
- 送進 LLM 的內容不含病患真名；警示推播只用假名代號。
- 完整資安審查與尚未處理的項目見 `Obsidian_Vault/專案管理/資安審查.md`。

## 藍圖

- **Phase 1**：持久化 + 紅旗 + 品質閘 + 認證 ✅
- **Phase 2**：ESAS-r 症狀結構化追問 → 分級升級紅旗 ✅
- **Phase 3a**：研究統計 + 去識別化 CSV 匯出（研究者專屬）✅
- **Phase 3b（本版）**：知識測驗前後測（`backend/quiz.py`）✅
- **資安與 LINE**：身分貫穿全系統、每人 LOG、LINE 通行碼綁定 ✅
- **Phase 3c**：主動定時回報、Telegram 主持人通知（需先申請 bot token）

### Phase 3b：知識測驗（`backend/quiz.py`）

前後測（pre/post）衡量衛教成效。題庫 `backend/quiz_bank.json` 由
`scripts/build_quiz_bank.py` 從 13 份 ONC 單張的「護理指導評值」抽出（是非題 + 選擇題 + 正解），
研究用途經嚴格解析與人工校正（ONC-21 原件解答題號誤植已修正並註明）。

| 端點 | 用途 |
|---|---|
| `GET /quiz/topics` | 13 個可測驗主題 |
| `GET /quiz/{onc_code}` | 出題（**不含答案**，防作弊） |
| `POST /quiz/{onc_code}` | 提交作答，評分並存 `quiz_results`（phase=pre/post） |

病患端由聊天畫面右上 📝 進入測驗；研究者 `/stats/overview` 可看各主題 pre/post 平均分。

### Phase 3a：研究統計與匯出（`backend/stats.py`）

研究者/管理者專屬端點（帶 `X-Auth-Token`）：

| 端點 | 用途 |
|---|---|
| `GET /stats/overview` | 世代總覽：病患數、各症狀平均/嚴重度、紅旗數、回答品質分布 |
| `GET /stats/patient/{code}` | 個別病患症狀時間序列 |
| `GET /export/{table}.csv` | 去識別化 CSV（symptom_scores / messages / alerts / comprehension_checks） |

**去識別化**：所有統計與匯出只用假名代號（P001…），永不輸出真實姓名。
研究者頁新增「症狀統計」與「CSV 匯出」區。設計見
[`docs/specs/2026-07-17-phase3a-stats-export-design.md`](docs/specs/2026-07-17-phase3a-stats-export-design.md)。

### Phase 2：症狀追問（`backend/assessment.py`）

病患**回報症狀**時，機器人先做一段簡短的 ESAS-r 0–10 評估再回應：

```
「我一直拉肚子」
  → 「聽起來您有腹瀉的不舒服…0 到 10 分您會給幾分？」
  → 「3 分」  → 「今天大概拉了幾次？」
  → 「一天七次」
     · 次數≥6 或分數≥7 → 升級紅旗、通知護理師
     · 否則           → 給 ONC-22 腹瀉衛教
```

分數落 `symptom_scores`（供第三期趨勢圖與研究匯出）。固定欄位驅動「問什麼」，
措辭用溫暖模板（不需 API key），LLM 潤飾與自由文字抽取列為後續強化。
支援 10 種化療症狀；發燒/出血/呼吸困難/自傷等由 Phase 1 紅旗直接攔截，不進評估。
設計見 [`docs/specs/2026-07-17-phase2-symptom-assessment-design.md`](docs/specs/2026-07-17-phase2-symptom-assessment-design.md)。
