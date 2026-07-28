# Phase 3a 設計書 — 研究統計與 CSV 匯出

**日期**：2026-07-17
**狀態**：實作中
**前置**：Phase 1（持久化）、Phase 2（症狀分數）已完成

---

## 目標

把 Phase 1/2 一直在收集的資料變成研究者看得到、匯得出的統計：
- 世代總覽（cohort overview）：病患數、各症狀平均嚴重度、紅旗次數、回答品質分布。
- 個別病患症狀趨勢（時間序列）。
- 去識別化 CSV 匯出，供 SPSS/R 分析。

Phase 3 的其他部分（知識測驗、主動定時回報、Telegram 主持人通知）不在本 slice；
Telegram 需使用者先申請 bot token，另行處理。

## 去識別化（研究倫理硬要求）

所有統計與匯出**只用假名代號**（pseudonym, 如 P001），**永不輸出 `patients.real_name`**。
`patients` 表的真名欄位不參與任何 stats/export 查詢。這與 Phase 1 的分表設計一致。

## 權限

`/stats/*` 與 `/export/*` 一律要求 **researcher 或 admin** 的有效 token（`auth.verify_token`），
透過 `X-Auth-Token` header 傳遞。病患 token 會被擋（403）。無 token → 401。
（Phase 1 的 `/sessions`、`/history` 暫未 gate，屬既有狀態，本期先聚焦 stats/export 的把關。）

## 元件

| 檔案 | 動作 | 職責 |
|---|---|---|
| `backend/stats.py` | 新增 | 聚合查詢 + CSV 序列化（純 DB 讀，不需 API key） |
| `backend/main.py` | 加端點 | `/stats/overview`、`/stats/patient/{code}`、`/export/{table}.csv`，researcher gate |
| `frontend/index.html` | 加畫面 | 研究者頁新增「統計 / 匯出」區：總覽表、症狀趨勢、CSV 下載鈕 |

## API

```
GET /stats/overview                 → 世代總覽
  { patients: n, messages: n, alerts: {high, medium},
    symptoms: [{symptom, n, avg_score, max_score, escalated}],
    answer_quality: {grounded, deflected_no_source, ...} }

GET /stats/patient/{code}           → 個別病患
  { patient_code, symptom_series: {symptom: [{ts, score}]}, alerts: [...] }

GET /export/{table}.csv             → 去識別化 CSV（table ∈ symptom_scores | messages | alerts | comprehension）
  text/csv；欄位只含假名代號，不含真名
```

## CSV 欄位

- `symptom_scores`：patient_code, esas_symptom, score, extra, ts, source
- `messages`：patient_code, role, redflag_level, answer_quality, model_id, provider, ts, content
- `alerts`：patient_code, trigger, level, ts
- `comprehension`：patient_code, onc_code, rating, ts

（messages 含 content —— 是研究對話資料，已用假名去識別化。）

## 前端（研究者頁）

- **世代總覽**：一張表列各症狀的樣本數、平均分、升級數；上方顯示病患數與紅旗數。
- **症狀趨勢**：選一位病患 + 一個症狀，用 inline SVG 畫簡單折線（不引入外部 CDN，符合單檔 SPA）。
- **匯出**：四個下載鈕（各 table 一個 CSV），fetch 時帶 `X-Auth-Token`。

## 測試

- 聚合正確：塞入已知分數 → overview 的平均/計數正確。
- 去識別化：匯出的 CSV **不含** real_name，只有 pseudonym。
- 權限：無 token → 401；病患 token → 403；研究者 token → 200。
- 全部不需 API key。

## 非目標

- 知識測驗前後測、主動定時回報、Telegram 通知（後續）。
- 進階統計檢定（t 檢定等）—— 匯出 CSV 後在 SPSS/R 做。
