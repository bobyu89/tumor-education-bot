# AWS 部署步驟（EC2 台北）

在 AWS 台北區（ap-east-2）開一台主機，跑腫瘤衛教機器人。全程在瀏覽器操作，不需要在自己電腦安裝任何東西。
約需 30 分鐘（多數時間在等主機安裝）。

> **系統目前仍不可用於真實病患**：紅旗詞庫需先經臨床審核與獨立測試集驗證。這台主機先用於演示與 Claude 實測。

## 會建立什麼、要花多少錢

| 項目 | 規格 | 估計費用（美元／月） |
|---|---|---|
| 主機 EC2 | t3.medium：2 vCPU、4 GB 記憶體，Ubuntu 24.04 | 約 40 |
| 硬碟 EBS | 30 GB gp3，加密 | 約 3 |
| 固定 IP | Elastic IP（公有 IPv4） | 約 3.6 |
| **合計** | | **約 45–50**（以 AWS 主控台顯示的台北價格為準） |

另外 Claude API 依用量計費（Sonnet 5.5 每則回答約 US$0.01）。
不用時可以「停止」主機：停止期間不收主機費，硬碟與固定 IP 仍會收費（約 US$7／月）。

## 步驟 0：準備

1. **合併 PR #1**（或決定要部署哪個分支）
   安裝腳本預設部署 `main`，而目前的系統改動都還在 PR #1 的分支上。
   - 建議：在 GitHub 合併 PR #1 後再部署。
   - 若先不合併：第 4 步改用 `sudo BRANCH=ccr-7e03a287-h1n6by bash /opt/tumor-bot/install.sh`
2. **Anthropic API 金鑰**（第 4 步才用）：資料要留在美國，所以金鑰要建在美國資料區域的工作區：
   console.anthropic.com → Settings → Workspaces → Create Workspace，**Workspace geo 選 US**（建立後不能改），
   建議同時把允許的 inference geo 限定為 `us`。再到 API Keys → Create Key，工作區選剛建的那個，複製 `sk-ant-` 開頭的金鑰。
   程式預設每個請求都帶 `inference_geo=us`（`ANTHROPIC_INFERENCE_GEO`，Claude 價格 1.1 倍）。
3. **下載三個檔案**到自己電腦：GitHub repo → `deploy/aws/` →
   `create_instance.sh`、`install.sh`、`enable_backup.sh`，各自打開後按右上「Download raw file」。

## 步驟 1：啟用台北區（只需做一次）

AWS 主控台右上角帳號名稱 → **Account** → 往下找 **AWS Regions** → 找到 **Asia Pacific (Taipei)** → **Enable**。
通常幾分鐘內生效。

## 步驟 2：在 CloudShell 建立主機

1. 主控台右上角把地區切到 **Asia Pacific (Taipei)**。
2. 點右上角的 **CloudShell** 圖示（`>_`），等終端機出現。
3. CloudShell 右上 **Actions → Upload file**，把 `create_instance.sh`、`install.sh`、`enable_backup.sh` 都上傳（一次一個）。
4. 輸入：
   ```bash
   bash create_instance.sh
   ```
5. 約 2 分鐘後完成，畫面會顯示**主機編號、固定 IP 與網址**（`https://<IP>.sslip.io`），請記下來。

腳本建立的東西：主機 `tumor-bot`、加密硬碟 `tumor-bot-data`、安全群組 `tumor-bot-web`（對外只開 80、443）、固定 IP、IAM 角色 `tumor-bot-ssm`（讓你從瀏覽器登入主機），
以及每日加密快照規則（台灣時間 02:00，保留 7 份）。
主機開啟了「終止保護」，硬碟設為主機刪除後仍保留，避免誤刪資料。

## 步驟 3：登入主機

EC2 主控台 → **執行個體（Instances）** → 勾選 `tumor-bot` → 右上 **連線（Connect）** →
選 **Session Manager** → **連線**。（剛開機的主機要等 2–3 分鐘才連得上；Session Manager 不行時改選 EC2 Instance Connect）

## 步驟 4：安裝與啟動

在主機的終端機輸入：
```bash
sudo bash /opt/tumor-bot/install.sh
```
腳本會依序：

1. **顯示一行 GitHub 部署金鑰**（`ssh-ed25519 …`）。到 GitHub repo → **Settings → Deploy keys → Add deploy key**，
   Title 填 `aws-ec2`，Key 貼上那一行，**「Allow write access」不要勾**，按 Add key。回到主機按 Enter。
   （這把金鑰只能讀這個 repo，不能改程式。）
2. **請你貼上 Anthropic API 金鑰**：畫面不會顯示你貼的內容，貼上後直接按 Enter。
   金鑰只存在主機上的 `.env`（權限只有管理者可讀），不會進 GitHub。
3. **問要不要建立示範帳號**：示範帳號的密碼是公開的範例密碼，放在網路上任何人都能登入，**建議選 N**。
4. **顯示管理者密碼**：只顯示一次，請記下來。
5. 建置並啟動。第一次要下載嵌入模型、建立索引，約 5–10 分鐘，看到「✓ 完成」即可。

## 步驟 5：開始使用

1. 開啟 `https://<IP>.sslip.io`（第一次要等約 1 分鐘申請 HTTPS 憑證）。
2. 用 `admin` 與剛才的管理者密碼登入 → 管理者畫面建立護理師與測試病人帳號。
3. 用病人帳號登入、提問，確認回答有標註單張代碼（例如「（ONC-17）」）。

接下來可以請 Claude 對這個網址跑 99 題實測。

## 日常維護

| 要做的事 | 指令（在主機上） |
|---|---|
| 更新到 GitHub 最新版本 | `sudo bash /opt/tumor-bot/install.sh` |
| 看紀錄 | `cd /opt/tumor-bot/app && sudo docker compose logs --tail 100 app` |
| 改設定（換金鑰、改模型） | `sudo nano /opt/tumor-bot/app/.env`，改完再執行一次 install.sh |
| 首次登入後清掉管理者密碼 | 在 `.env` 把 `ADMIN_PASSWORD=` 後面清空（帳號已建立，不影響登入） |

**衛教單張改版**：用管理者帳號登入 →「📚 單張管理」上傳 Word／PDF，預覽後上架，立即生效（見 `docs/knowledge/單張管理後台.md`）。

**備份**：病人資料與後台上架的單張（`data/leaflets/`）都在主機硬碟（已加密）的 Docker volume 裡。
`create_instance.sh` 會自動執行 `enable_backup.sh`：先確認硬碟有加密，再建立每日快照規則（台灣時間 02:00，保留 7 份，快照同樣加密）。
之前已建好主機的人，在 CloudShell 上傳 `enable_backup.sh` 後執行 `bash enable_backup.sh` 即可。保留份數可用 `RETAIN=30 bash enable_backup.sh` 調整。

**還原測試**（上線前做一次）：EC2 → 快照 → 選 `tumor-bot-snapshot` → 建立磁碟區 → 掛到一台測試主機，確認 `tumor_bot.sqlite3` 打得開。
需要另存一份到主機以外的地方時，可在主機上用 `deploy/backup.sh` 產生加密備份檔（見部署說明「加密備份」）。

**硬碟沒有加密時**（早期用其他方式建立的主機）：停止主機 → 對硬碟建立快照 → 「複製快照」時勾選加密 →
從加密快照建立新硬碟 → 換掉主機的根磁碟。或直接用 `create_instance.sh` 另建一台，再用 `deploy/backup.sh`／`restore.sh` 搬資料。

## 停止與刪除

- **暫停**：EC2 主控台 → 勾選主機 → 執行個體狀態 → **停止**。之後按「啟動」即可恢復，網址不變。
- **完全刪除**（會刪掉所有資料，請先備份）：
  1. 主機 → 動作 → 執行個體設定 → 變更終止保護 → 關閉 → 再「終止」主機
  2. EC2 → 磁碟區（Volumes）→ 刪除 `tumor-bot-data`
  3. EC2 → 彈性 IP → 釋放 `tumor-bot` 的 IP

## 常見問題

- **CloudShell 顯示「無法使用 ap-east-2」**：台北區還沒啟用，或剛啟用還沒生效（見步驟 1）。
- **沒有 t3.medium**：腳本會自動改用 t3a.medium；都沒有時用 `INSTANCE_TYPE=<規格> bash create_instance.sh`（需 2 vCPU、4 GB 以上）。
- **網址打不開／憑證錯誤**：確認安全群組有開 80、443；看 Caddy 紀錄 `sudo docker compose logs caddy`。
  sslip.io 是免費的共用網址服務，若憑證申請失敗，可改用自己的網域：網域的 DNS 設 A 紀錄指向固定 IP，
  再把 `.env` 的 `DOMAIN` 改成該網域、執行 install.sh。
- **要接 LINE**：在 `.env` 填 `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`，
  LINE Developers Console 的 Webhook URL 設為 `https://<網址>/line/webhook`。
