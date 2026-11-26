#!/usr/bin/env bash
# 在 EC2 主機上執行（create_instance.sh 已把本檔放到 /opt/tumor-bot/install.sh）：
#     sudo bash /opt/tumor-bot/install.sh
#
# 第一次：產生 GitHub 部署金鑰 → 下載程式 → 建立 .env（API 金鑰在這裡輸入，畫面不顯示）→ 啟動
# 之後再執行：更新到 GitHub 上的最新版本並重新啟動（.env 與資料都保留）
#
# 部署其他分支：sudo BRANCH=<分支名稱> bash /opt/tumor-bot/install.sh
set -euo pipefail

REPO="${REPO:-git@github.com:bobyu89/tumor-education-bot.git}"
BRANCH="${BRANCH:-main}"
BASE=/opt/tumor-bot
APP=$BASE/app
KEY=$BASE/deploy_key
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

say()  { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail "請用 sudo 執行：sudo bash $0"

# ── 0. 等開機安裝（Docker）完成 ──
if [ ! -f $BASE/.bootstrap-done ] || ! command -v docker >/dev/null; then
  say "等待主機開機安裝 Docker（約 2–3 分鐘）"
  cloud-init status --wait >/dev/null || true
  command -v docker >/dev/null || fail "Docker 沒有裝好，請看 /var/log/cloud-init-output.log"
fi

# ── 1. GitHub 部署金鑰（只能讀這個 repo）──
if [ ! -f "$KEY" ]; then
  say "產生 GitHub 部署金鑰"
  ssh-keygen -t ed25519 -N "" -C "tumor-bot-deploy" -f "$KEY" >/dev/null
fi
until git ls-remote "$REPO" HEAD >/dev/null 2>&1; do
  cat <<EOF

請把下面這一整行加到 GitHub：
  repo 頁面 → Settings → Deploy keys → Add deploy key
  Title 隨意（例如 aws-ec2），Key 貼上這一行，「Allow write access」不要勾

$(cat "$KEY.pub")

EOF
  read -rp "加好之後按 Enter 繼續… " _
done
echo "GitHub 連線正常"

# ── 2. 下載或更新程式 ──
if [ -d "$APP/.git" ]; then
  say "更新程式（$BRANCH）"
  git -C "$APP" fetch --quiet origin "$BRANCH"
  git -C "$APP" checkout --quiet -B "$BRANCH" "origin/$BRANCH"
else
  say "下載程式（$BRANCH）"
  git clone --quiet --branch "$BRANCH" "$REPO" "$APP"
fi
cd "$APP"
echo "版本：$(git log --oneline -1)"

# ── 3. 設定 .env（只在第一次建立；之後保留）──
set_env() {   # 移除舊的（含註解掉的）同名設定，再寫入新值
  sed -i "/^#\?[[:space:]]*$1=/d" .env
  printf '%s=%s\n' "$1" "$2" >> .env
}
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
IP=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4)
if [ ! -f .env ]; then
  say "建立設定檔 .env"
  cp .env.example .env
  chmod 600 .env
  ADMIN_PW=$(openssl rand -base64 18 | tr -d '/+=')
  set_env AUTH_SECRET "$(openssl rand -hex 32)"
  set_env ADMIN_ACCOUNT admin
  set_env ADMIN_PASSWORD "$ADMIN_PW"
  set_env DOMAIN "${IP//./-}.sslip.io"
  set_env PRIMARY_MODEL claude-sonnet-5-5
  set_env LLM_EFFORT low

  echo
  echo "請貼上 Anthropic API 金鑰（sk-ant- 開頭；畫面不會顯示；直接按 Enter = 先用演示模式，之後再補）"
  read -rsp "ANTHROPIC_API_KEY: " API_KEY; echo
  if [ -n "$API_KEY" ]; then
    set_env LLM_PROVIDER anthropic
    set_env ANTHROPIC_API_KEY "$API_KEY"
  else
    set_env LLM_PROVIDER mock
    echo "已設為演示模式（LLM_PROVIDER=mock）。之後要補金鑰：sudo nano $APP/.env，再執行本腳本。"
  fi
  unset API_KEY

  echo
  echo "要建立示範帳號嗎？（P001／R001／A001，密碼是公開的範例密碼，任何知道的人都能登入）"
  read -rp "建立示範帳號？[y/N] " DEMO
  case "$DEMO" in [yY]*) set_env DEMO_SEED true ;; *) set_env DEMO_SEED false ;; esac

  cat <<EOF

管理者帳號：admin
管理者密碼：$ADMIN_PW
（只顯示這一次，請立刻記下來。登入後可在管理者畫面建立病人與護理師帳號）
EOF
  read -rp "記好了按 Enter 繼續… " _
else
  echo ".env 已存在，保留原設定"
fi
DOMAIN=$(grep -E '^DOMAIN=' .env | cut -d= -f2-)

# ── 4. 啟動 ──
say "建置並啟動（第一次約 5–10 分鐘：下載嵌入模型並建立索引）"
docker compose --profile https up -d --build
docker image prune -f >/dev/null

say "等待系統就緒"
for i in $(seq 1 90); do
  if out=$(curl -sf http://127.0.0.1:8000/health 2>/dev/null); then
    echo "$out"; break
  fi
  [ "$i" = 90 ] && fail "15 分鐘內沒有啟動完成，請看紀錄：cd $APP && docker compose logs --tail 80 app"
  sleep 10
done

cat <<EOF

✓ 完成：https://$DOMAIN
  （第一次開啟時 Caddy 要先申請 HTTPS 憑證，可能要等 1 分鐘）

常用指令（在主機上）：
  更新到最新版本：   sudo bash $BASE/install.sh
  看紀錄：           cd $APP && sudo docker compose logs --tail 100 app
  改設定（如金鑰）：  sudo nano $APP/.env   改完再執行一次 install.sh
  重新啟動：         cd $APP && sudo docker compose --profile https restart
EOF
