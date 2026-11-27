# 腫瘤衛教機器人

針對化學治療病患的 RAG 衛教問答系統。後端 FastAPI（`backend/`），知識庫為 13 份血液腫瘤科
衛教單張向量化後存於 ChromaDB。詳見 [README.md](README.md) 與 [`docs/specs/`](docs/specs/)。

- 病患資料存於 SQLite（`backend/db.py`），身分與研究資料分表（去識別化）。**含病患資料的檔案（`.env`、`data/*.sqlite3`、`chroma_db/`、`models/`）絕不進版控。**
- 嵌入層 `backend/embedding.py`（ONNX，不需 torch）：檢索用 multilingual-e5-small（`EMBED_MODEL`，查詢／段落加 `query: `／`passage: ` 前綴），紅旗向量層固定用 MiniLM（門檻在該模型上校準，不跟著換）。索引記錄建庫模型，不一致時 `index_vault.py` 自動重建、`rag.py` 拒絕檢索。換模型前先跑 `scripts/eval_retrieval.py`，見 `docs/knowledge/檢索評估.md`。
- 測試不需 API key：`cd backend && python -X utf8 test_phase1.py`、`test_phase2.py`、`test_phase3.py`、`test_quiz.py`、`test_integration.py`、`test_redflag.py`、`test_alerts.py`、`test_llm_claude.py`、`test_kb_update.py`、`test_departments.py`、`test_leaflets.py`、`test_cag.py`、`test_retrieval.py`（需已同步的索引）；演示引擎 `node scripts/test_pages_engine.js`。
- 知識庫交給模型的方式 `KB_MODE`：`rag`（預設，檢索相關段落）、`cag`（`backend/kb.py`：本科全部單張放進 system 固定前段，Claude prompt caching）、`auto`（單張總字數 ≤ `CAG_MAX_CHARS` 用 cag）。實測比較用 `scripts/live_llm_eval.py --kb rag cag`。
- 正式收案 LLM 為 Anthropic Claude Sonnet 5.5 或 Opus 5.5（`LLM_PROVIDER=anthropic`）；這兩個模型不接受 `temperature`，以 `LLM_EFFORT` 控制。
- 衛教知識庫以院內現行 13 份單張為準，不修改單張內容；範圍外問題由系統轉介。
- 科別：`knowledge/<科別>/manifest.json`（單張來源、緊急電話、紅旗擴充、症狀評估協定）；病患綁定科別，檢索、紅旗與評估都依科別。紅旗分「通用核心」（`redflag.py`）與「科別擴充」（manifest）。新增科別見 `docs/knowledge/新增科別說明.md`，不需改程式。
- 管理者可在網頁「單張管理」上傳 Word／Markdown／PDF、上架、停用、退回（`backend/leaflets.py`，見 `docs/knowledge/單張管理後台.md`）：上架版本寫在 `data/leaflets/<科別>/<代碼>.md` 覆蓋層（同代碼優先於原始單張），版本紀錄在 `leaflet_versions`，上架即同步索引。
- 直接改 repo 單張時：新增、改版（改 frontmatter 的 `doc_version`）或停用（`rag_index: false`）後，執行 `python -X utf8 scripts/index_vault.py` 同步：舊版與停用單張的段落會被刪除，不需 `--rebuild`。引用核對用知識庫實際的單張代碼，不寫死代碼格式。
- 紅旗規則單一來源是 `backend/redflag.py`，病患端安全文字是 `backend/safety_text.py`；改了要跑 `python -X utf8 scripts/build_pages.py` 重建 `docs/demo_data.js`（演示引擎直接讀匯出的規則）。
- Cloudflare 測試部署：`deploy/cloudflare/`（Worker + 容器，網站密碼保護；硬碟暫時、只供測試），映像用根目錄 Dockerfile 加 `BAKE_MODELS=1`；步驟見 `docs/deploy/Cloudflare測試部署.md`。
- 演示不需 API key：`.env` 設 `LLM_PROVIDER=mock`；`scripts/seed_demo.py --reset` 植入模擬病患，`scripts/demo_dry_run.py` 彩排 9 個情境；腳本在 `docs/demo/演示腳本.md`。

## Agent skills

### Issue tracker

Issues live as GitHub issues in `bobyu89/tumor-education-bot`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical labels: `needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
