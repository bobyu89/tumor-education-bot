#!/usr/bin/env bash
# 加密備份：把病人資料庫與後台上架的單張打包、加密，存成一個檔案。主機上不會留下未加密的副本。
#
#   第一次：產生備份密碼檔（只有 root 能讀；密碼另外抄一份放在安全的地方，弄丟就解不開）
#       sudo install -m 600 /dev/null /root/.tumor-bot-backup-pass
#       sudo sh -c 'openssl rand -base64 32 > /root/.tumor-bot-backup-pass'
#   備份（在 docker-compose.yml 所在的資料夾執行）：
#       sudo bash deploy/backup.sh
#   每天自動備份（台灣時間 02:30）：
#       echo '30 2 * * * root cd /opt/tumor-bot/app && bash deploy/backup.sh >> /var/log/tumor-bot-backup.log 2>&1' \
#         | sudo tee /etc/cron.d/tumor-bot-backup
#   還原：見 deploy/restore.sh
#
# 可用環境變數覆寫：BACKUP_DIR（預設 ./backups）、BACKUP_PASS_FILE、KEEP_DAYS（預設 14）、
#                   COMPOSE（預設 "docker compose"）
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
PASS_FILE="${BACKUP_PASS_FILE:-/root/.tumor-bot-backup-pass}"
KEEP_DAYS="${KEEP_DAYS:-14}"
COMPOSE="${COMPOSE:-docker compose}"
STAMP="$(date +%Y%m%d-%H%M)"
OUT="$BACKUP_DIR/tumor-bot-$STAMP.tar.gz.enc"

fail() { echo "✗ $*" >&2; exit 1; }

[ -s "$PASS_FILE" ] || fail "找不到備份密碼檔 $PASS_FILE（產生方式見本檔開頭）"
perm=$(stat -c %a "$PASS_FILE")
[ "$perm" = "600" ] || [ "$perm" = "400" ] || fail "$PASS_FILE 的權限是 $perm，請改成 600：chmod 600 $PASS_FILE"
command -v openssl >/dev/null || fail "主機沒有 openssl"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
umask 077

# 1. 在容器裡用 SQLite 線上備份（服務不必停），再把資料庫與單張覆蓋層打包，從標準輸出直接送去加密
# 2. 加密：AES-256-CBC，PBKDF2 20 萬次；寫完先存成 .part，成功才改名，避免留下不完整的檔案
$COMPOSE exec -T app sh -c '
  set -e
  tmp=$(mktemp -d)
  trap "rm -rf $tmp" EXIT
  python - "$tmp/tumor_bot.sqlite3" <<PY
import sqlite3, sys
src = sqlite3.connect("/app/data/tumor_bot.sqlite3")
dst = sqlite3.connect(sys.argv[1])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close(); src.close()
if ok != "ok":
    sys.exit("integrity_check: " + ok)
PY
  if [ -d /app/data/leaflets ]; then cp -r /app/data/leaflets "$tmp/leaflets"; fi
  tar -czf - -C "$tmp" .
' | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$PASS_FILE" -out "$OUT.part"

[ -s "$OUT.part" ] || { rm -f "$OUT.part"; fail "備份檔是空的"; }
# 確認解得開、內容完整
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$PASS_FILE" -in "$OUT.part" \
  | tar -tzf - | grep -q 'tumor_bot.sqlite3' || { rm -f "$OUT.part"; fail "備份檔解密驗證失敗"; }
mv "$OUT.part" "$OUT"

# 3. 刪除超過 KEEP_DAYS 天的舊備份
find "$BACKUP_DIR" -maxdepth 1 -name 'tumor-bot-*.tar.gz.enc' -mtime +"$KEEP_DAYS" -delete

echo "$(date '+%F %T') 備份完成：$OUT（$(du -h "$OUT" | cut -f1)）"
