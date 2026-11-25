# 腫瘤衛教機器人

針對化學治療病患的 RAG 衛教問答系統。後端 FastAPI（`backend/`），知識庫為 13 份血液腫瘤科
衛教單張向量化後存於 ChromaDB。詳見 [README.md](README.md) 與 [`docs/specs/`](docs/specs/)。

- 病患資料存於 SQLite（`backend/db.py`），身分與研究資料分表（去識別化）。**含病患資料的檔案（`.env`、`data/*.sqlite3`、`chroma_db/`、`models/`）絕不進版控。**
- 嵌入層 `backend/embedding.py` 在 torch 被封鎖時自動退回 ONNX（本機 Windows Smart App Control 會擋 torch）。
- 測試不需 API key：`cd backend && python -X utf8 test_phase1.py`、`test_phase2.py`、`test_phase3.py`、`test_quiz.py`、`test_integration.py`、`test_redflag.py`、`test_alerts.py`、`test_llm_claude.py`、`test_kb_update.py`、`test_departments.py`；演示引擎 `node scripts/test_pages_engine.js`。
- 正式收案 LLM 為 Anthropic Claude Sonnet 5.5 或 Opus 5.5（`LLM_PROVIDER=anthropic`）；這兩個模型不接受 `temperature`，以 `LLM_EFFORT` 控制。
- 衛教知識庫以院內現行 13 份單張為準，不修改單張內容；範圍外問題由系統轉介。
- 科別：`knowledge/<科別>/manifest.json`（單張來源、緊急電話、紅旗擴充、症狀評估協定）；病患綁定科別，檢索、紅旗與評估都依科別。紅旗分「通用核心」（`redflag.py`）與「科別擴充」（manifest）。新增科別見 `docs/knowledge/新增科別說明.md`，不需改程式。
- 單張新增、改版（改 frontmatter 的 `doc_version`）或停用（`rag_index: false`）後，執行 `python -X utf8 scripts/index_vault.py` 同步：舊版與停用單張的段落會被刪除，不需 `--rebuild`。引用核對用知識庫實際的單張代碼，不寫死代碼格式。
- 紅旗規則單一來源是 `backend/redflag.py`，病患端安全文字是 `backend/safety_text.py`；改了要跑 `python -X utf8 scripts/build_pages.py` 重建 `docs/demo_data.js`（演示引擎直接讀匯出的規則）。
- 演示不需 API key：`.env` 設 `LLM_PROVIDER=mock`；`scripts/seed_demo.py --reset` 植入模擬病患，`scripts/demo_dry_run.py` 彩排 9 個情境；腳本在 `docs/demo/演示腳本.md`。

## Agent skills

### Issue tracker

Issues live as GitHub issues in `bobyu89/tumor-education-bot`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical labels: `needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
