# Phase 1 設計書 — 地基與安全層

**日期**：2026-07-16
**狀態**：實作中
**範圍**：持久化 + 紅旗瞬間篩檢 + 回答品質把關 + 角色認證 + LLM 供應商抽象

---

## 背景

現行系統（`backend/`）是可運作的 RAG 衛教機器人，但有三個在 review 中確認的地基問題：

1. **緊急警示延遲**：`main.py` 第 1 步就偵測到紅旗，卻在 LLM 回應（`asyncio.to_thread`，約 25–35 秒）之後才 WebSocket 推播護理師。
2. **資料易失**：session 存記憶體，重啟清空 → 研究資料歸零。
3. **認證形同虛設**：白名單 `VALID_*_CODES`，猜到 `R001` 就能看光病患對話。

本期不做症狀追問（第二期）與三角色儀表板（第三期），只把上述地基補穩，讓後續功能有可靠的基礎。

## 目標

- 病患資料落地保存（SQLite），可供研究匯出，且身分與研究資料分表（去識別化）。
- 紅旗偵測改為「先於 LLM 執行、命中即刻推播與回覆」，並用關鍵字為底 + 否定處理 + 向量補充。
- 回答沒有 RAG 來源時不編造，改請護理師。
- 認證改為帳號 + 雜湊密碼 + 角色，由管理者發放。
- LLM 呼叫抽象化，Gemma 現用、Sonnet 5 可一檔替換。

## 非目標（明確排除）

- 症狀 ESAS-r 追問（第二期）
- 症狀趨勢圖、CSV 匯出、主動定時回報、知識測驗（第三期）
- Telegram 主持人通知（第三期後）
- 三角色的完整 Web 儀表板 UI（第三期）
- 更換嵌入模型以提升中文檢索（第二期評估）

## 前置條件（動工前需與指導教授確認，非本期工作項目）

- **免費 Google AI Studio 方案會用對話訓練模型**，碰真實病患前必須換付費 API 或本地模型。換 Sonnet 5（Anthropic API 預設不訓練）即可解此問題 → 本期的 `llm_client.py` 為此鋪路。
- IRB、知情同意、去識別化流程。
- 正式佈署環境（雲端、HTTPS、資料庫加密）由資安單位規範後決定。

---

## 元件

| 檔案 | 動作 | 職責 |
|---|---|---|
| `backend/embedding.py` | 新增 | 共用嵌入層，torch → ONNX 自動 fallback（修 Smart App Control 封鎖 torch 的問題），統一 metadata |
| `backend/db.py` | 新增 | SQLite schema + 存取層 + migration bootstrap |
| `backend/auth.py` | 新增 | 帳密雜湊、角色守門、管理者發放帳號 |
| `backend/llm_client.py` | 新增 | LLM 供應商抽象（Gemma 現用，Sonnet 5 可替換），記錄 model/provider/tokens |
| `backend/redflag.py` | 新增（取代 alert.py） | 關鍵字為底 + 否定處理 + 向量補充 |
| `backend/quality.py` | 新增 | 回答品質閘：無來源不編造 |
| `backend/main.py` | 改寫 `/chat` 流程 | 紅旗篩檢 → 即刻推播+回覆 → RAG → 品質閘 → LLM → 落 SQLite |
| `backend/rag.py` | 修正 | metadata 鍵名對齊、改用共用嵌入層 |
| `scripts/index_vault.py` | 已建 | 從 Markdown 建庫，統一 metadata |
| `frontend/index.html` | 最小改動 | 登入改帳密 |

## `/chat` 新流程

```
病患訊息
  │
  ▼
① redflag.screen(message)              ← 關鍵字 + 否定 + 向量，< 1 秒
  ├─ HIGH → 立刻 broadcast_alert()
  │        + 立刻回固定安全文字（不進 RAG/LLM）
  │        + 寫 alerts + messages + events
  │        → return
  └─ 非 HIGH ↓
② rag.query(message)
  ▼
③ quality.pre_check(rag_docs)
  ├─ 無合格來源 → 回「需請護理師解答」（不進 LLM）
  └─ 有來源 ↓
④ llm_client.generate(prompt, history)
  ▼
⑤ quality.post_check(reply, rag_docs)  ← 回答是否有引用來源
  ▼
⑥ 全部落 SQLite（messages 附紅旗等級、RAG來源、model、tokens、品質）
   MEDIUM 紅旗在此併入 alerts（非即時，但記錄）
```

## 資料表（SQLite）

見 [[Review 問題清單]] 與腦力激盪定案的 schema：

- `users`(id, account, password_hash, role, created_at)
- `patients`(user_id, real_name, age, gender, cancer_type, diagnosis, education_level, pseudonym) ← 敏感，與研究資料分離
- `sessions`(id, patient_code, started_at, ended_at)
- `messages`(id, patient_code, role, content, ts, redflag_level, redflag_terms, rag_sources, model_id, provider, prompt_tokens, completion_tokens, answer_quality)
- `alerts`(id, patient_code, trigger, level, source_message_id, ts, notified_channels, ack_by, ack_at)
- `symptom_scores`(id, patient_code, esas_symptom, score, extra, ts, source) ← 第二期用，先建表
- `comprehension_checks`(id, patient_code, onc_code, rating, ts, source_message_id) ← 先建表
- `quiz_results`(id, patient_code, onc_code, phase, score, ts) ← 先建表
- `events`(id, ts, patient_code, event_type, payload) ← 只增不改稽核日誌
- `schema_version`(version, applied_at)

**設計要點**：
- 研究資料只存 `pseudonym`（P001），真名只在 `patients`。匯出走 pseudonym → 天然去識別化。
- `messages` 存滿軌跡 → 同時餵養品質稽核與研究資料。換 Sonnet 5 後可依 `model_id` 區分資料來源。
- 第二、三期的表現在就建、先不填 → 避免日後 migration 動到已存資料。

## 紅旗設計

- **關鍵字為底（高召回，確定性）**：HIGH（胸痛、呼吸困難、想死、自傷、大量出血、失去意識…）、MEDIUM（發高燒、一直吐、傷口紅腫…）。
- **否定/語境處理**：訊息中關鍵字前若出現「沒有/不/未/如果/萬一」等，降級或不觸發 —— 但**自傷類 HIGH 從寬**（寧可誤報）。這解掉 [[ONC-39 過敏症狀]] 內文含「呼吸困難」被病患複述時的誤報。
- **向量補充（只加分不減分）**：把訊息與一組策展的紅旗範例句比對 cosine，超過門檻則升級。抓「撐不下去、喘不上來、活著好累」這類沒命中關鍵字的說法。
- **安全底線永遠在關鍵字**：向量只負責多抓，不負責放行。

## 回答品質閘

- **前置**：RAG top 結果距離都超過門檻（無合格來源）→ 不呼叫 LLM，直接回「這個問題我需要請護理師為您解答」，`answer_quality = deflected_no_source`。
- **後置**：LLM 回答後，檢查是否至少引用一個檢索來源；若模型脫離來源自由發揮 → `answer_quality = deflected_off_source`，回覆改為引導詢問護理師。
- 每則回答的 `answer_quality`（grounded / deflected_no_source / deflected_off_source）落 `messages`，供品質稽核與研究。

## LLM 供應商抽象

```python
class LLMClient(Protocol):
    def generate(self, system_prompt, history, user_message) -> LLMResult: ...
    # LLMResult: text, model_id, provider, prompt_tokens, completion_tokens

class GemmaClient(LLMClient): ...      # 現用，google-genai
class ClaudeClient(LLMClient): ...     # Sonnet 5，anthropic SDK（之後啟用）
```

`config.py` 依 `LLM_PROVIDER` 環境變數選擇。換 Sonnet 5 = 設定 `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`，不動其他程式。

## 認證

- `users` 表存 `account` + `password_hash`（bcrypt/pbkdf2）+ `role`。
- 三角色：patient / researcher / admin。管理者發放帳密，無自助註冊。
- 端點依角色守門：病患只能碰自己的資料；研究者唯讀（去識別化）；管理者可寫設定。
- 取代 `verify/{code}` 白名單。

## 錯誤處理

- LLM API 失敗 → 沿用現有的分類降級訊息（429/timeout/其他），不 crash，並記錄。
- DB 寫入失敗 → 紅旗推播不可依賴慢速寫入；alert 推播與固定回覆優先，落庫可 fire-and-forget 補寫。
- **紅旗篩檢絕不可被跳過**，即使下游任何一步失敗。

## 測試

- **紅旗召回測試集**：HIGH/MEDIUM/否定句/改寫句，HIGH 召回須近 100%。
- **品質閘**：知識庫範圍外的問題 → 必須 deflect，不得編造。
- **持久化**：寫入 → 重啟 → 資料仍在。
- **去識別化**：匯出內容不含真名。

## 待決事項（本期不決定）

- 雲端供應商、資料庫加密方式、HTTPS 憑證 → 依 IRB 與資安規範。
- 是否換更強的中文嵌入模型（現模型實測中文檢索 Top-1 僅 76%）→ 第二期評估。
