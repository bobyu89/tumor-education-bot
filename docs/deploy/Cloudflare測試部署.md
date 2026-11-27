# Cloudflare 測試部署

把機器人放到 Cloudflare 上，用網址測試（含真的 Claude 回答）。**只適合測試**：

- 容器硬碟是暫時的：**閒置 30 分鐘休眠、或重新部署後，資料庫會清空**（示範帳號會自動重建）。不可放真實病人資料。
- 正式收案請用有永久硬碟的主機（見 `AWS部署步驟.md` 或 `部署說明.md`）。

## 架構

```
瀏覽器 ──HTTPS──▶ Worker（網站密碼）──▶ 容器（同一個 Docker 映像：FastAPI + 向量索引 + 嵌入模型）
                                          └─▶ Anthropic API（金鑰存在 Cloudflare Secret）
```

- 設定在 `deploy/cloudflare/`（`wrangler.jsonc`、`src/index.js`）；映像用 repo 根目錄的 `Dockerfile`，
  建置時加 `BAKE_MODELS=1`，把嵌入模型與索引做進映像（喚醒時不必再下載 0.9 GB）。
- 整個網站有一道**網站密碼**（瀏覽器跳出的帳號密碼視窗，帳號隨意）：避免知道網址的人用示範帳號消耗 Claude 額度。
- 容器規格 standard-1（1/2 vCPU、4 GB 記憶體）；只跑一個實例。

## 費用

| 項目 | 費用 |
|---|---|
| Workers Paid 方案（使用 Containers 必要） | 每月 US$5，含每月約 6 小時的容器記憶體用量 |
| 超過的容器用量 | 開著的每小時約 US$0.04（閒置 30 分鐘自動休眠，休眠不收運算費） |
| Claude API | 依用量，Sonnet 5.5 每則回答約 US$0.01；99 題實測約 US$5 |

## 步驟

### 1. 開通方案（只需一次）
Cloudflare 後台 → **Workers & Pages** → **Plans**，升級為 **Workers Paid**（每月 US$5）。

### 2. 讓 Cloudflare 從 GitHub 建置
1. 後台 → **Workers & Pages** → **Create** → **Import a repository**。
2. 連結 GitHub，授權「Cloudflare Workers and Pages」存取 `bobyu89/tumor-education-bot`。
3. 設定：
   - Project name：`tumor-education-bot`（需與 `wrangler.jsonc` 的 name 相同）
   - Production branch：`main`
   - **Root directory：`deploy/cloudflare`**
   - Deploy command：`npx wrangler deploy`（預設值）
4. 按 Deploy。第一次建置約 10–20 分鐘（安裝套件、下載嵌入模型、建索引、上傳約 4 GB 映像）。

### 3. 設定 Secrets（在第一次開網站之前）
後台 → Workers & Pages → `tumor-education-bot` → **Settings** → **Variables and Secrets** → Add，類型選 **Secret**：

| 名稱 | 必填 | 說明 |
|---|---|---|
| `SITE_PASSWORD` | ✅ | 進網站的密碼（自訂一組，給測試者） |
| `ANTHROPIC_API_KEY` | 建議 | Claude API 金鑰；沒設時用演示模式（不呼叫 Claude） |
| `AUTH_SECRET` | | 登入簽章密鑰；沒設時每次容器啟動隨機產生 |

> 容器啟動時才讀 Secret。若網站已經開過、之後才改 Secret，要等容器閒置 30 分鐘休眠後才生效。

### 4. 開始測試
1. 開 `https://tumor-education-bot.<你的子網域>.workers.dev`（網址在 Worker 頁面上）。
2. 瀏覽器跳出帳號密碼視窗：帳號隨意，密碼填 `SITE_PASSWORD`。
3. 第一次（或休眠後）要等 30–90 秒容器啟動。
4. 示範帳號：病人 `P001` / `patient123`、研究者 `R001` / `research123`、管理者 `A001` / `admin123`。

### 換模型
`wrangler.jsonc` 的 `vars.PRIMARY_MODEL` 改成 `claude-opus-5-5`，推到 `main` 會自動重新部署。

## 疑難排解

- **「尚未設定網站密碼」**：完成步驟 3。
- **「系統啟動失敗」**：後台 → Workers & Pages → Containers 查看容器紀錄。
- **建置失敗**：Worker 頁面 → Deployments → 該次建置的紀錄。常見原因是沒有 Workers Paid 方案，或 Root directory 沒設成 `deploy/cloudflare`。
- 回答都很像「根據衛教資料……（演示模式）」：沒有讀到 `ANTHROPIC_API_KEY`，確認 Secret 名稱並等容器重新啟動。
