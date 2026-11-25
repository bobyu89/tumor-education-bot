# 腫瘤衛教機器人

針對化學治療病患的 RAG 衛教問答系統。後端 FastAPI（`backend/`），知識庫為 13 份血液腫瘤科
衛教單張向量化後存於 ChromaDB。詳見 [README.md](README.md) 與 [`docs/specs/`](docs/specs/)。

- 病患資料存於 SQLite（`backend/db.py`），身分與研究資料分表（去識別化）。**含病患資料的檔案（`.env`、`data/*.sqlite3`、`chroma_db/`、`models/`）絕不進版控。**
- 嵌入層 `backend/embedding.py` 在 torch 被封鎖時自動退回 ONNX（本機 Windows Smart App Control 會擋 torch）。
- 測試不需 API key：`cd backend && python -X utf8 test_phase1.py`、`test_phase2.py`、`test_phase3.py`、`test_quiz.py`、`test_integration.py`、`test_redflag.py`、`test_alerts.py`；演示引擎 `node scripts/test_pages_engine.js`。
- 紅旗規則單一來源是 `backend/redflag.py`，病患端安全文字是 `backend/safety_text.py`；改了要跑 `python -X utf8 scripts/build_pages.py` 重建 `docs/demo_data.js`（演示引擎直接讀匯出的規則）。
- 演示不需 API key：`.env` 設 `LLM_PROVIDER=mock`；`scripts/seed_demo.py --reset` 植入模擬病患，`scripts/demo_dry_run.py` 彩排 9 個情境；腳本在 `docs/demo/演示腳本.md`。

## Agent skills

### Issue tracker

Issues live as GitHub issues in `bobyu89/tumor-education-bot`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical labels: `needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
