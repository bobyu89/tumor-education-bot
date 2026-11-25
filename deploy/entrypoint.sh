#!/bin/sh
# 容器啟動：檢查必要設定 → 首次啟動建立向量索引 → 啟動 API（單一 worker）
set -eu
cd /app

fail() { echo "[entrypoint] 錯誤：$1" >&2; exit 1; }

# ── 正式環境必要設定 ──
# AUTH_SECRET 未設定時每次重啟都換密鑰，所有人會被登出；正式環境一律要求固定值
[ -n "${AUTH_SECRET:-}" ] || fail "未設定 AUTH_SECRET（請在 .env 設定一組長隨機字串，例如 openssl rand -hex 32）"
if [ "${DEMO_SEED:-false}" = "true" ]; then
  echo "[entrypoint] 警告：DEMO_SEED=true 會建立示範帳號（P001/R001/A001），正式環境請勿開啟" >&2
fi
if [ "${LLM_PROVIDER:-google}" = "google" ]; then
  echo "[entrypoint] 警告：LLM_PROVIDER=google（Google AI Studio）。免費方案條款允許用輸入資料改善產品，" \
       "不可用於真實病患；正式收案請設 LLM_PROVIDER=anthropic" >&2
fi

# ── 首次啟動：建立向量索引（同時下載嵌入模型到 /app/models）──
count=$(python -X utf8 -c "
import chromadb
try:
    print(chromadb.PersistentClient(path='chroma_db').get_collection('patient_education').count())
except Exception:
    print(0)
" 2>/dev/null | tail -n 1)
if [ "${count:-0}" = "0" ]; then
  echo "[entrypoint] 尚無向量索引，開始建立（首次啟動需下載約 470MB 嵌入模型）…"
  python -X utf8 scripts/index_vault.py --rebuild
fi

# ── 啟動 ──
# 必須是單一 worker：護理端 WebSocket 連線清單與 LINE 事件去重都在同一個行程內
cd backend
exec python -X utf8 -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 \
     --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
