#!/usr/bin/env bash
# 還原 deploy/backup.sh 做的加密備份。
#
#   只解開來看（不動正式資料），解到一個只有 root 能讀的暫存資料夾：
#       sudo bash deploy/restore.sh backups/tumor-bot-20261127-0230.tar.gz.enc --extract-only
#   還原到正式環境（會先停止服務；目前的資料庫另存為 tumor_bot.sqlite3.before-restore）：
#       sudo bash deploy/restore.sh backups/tumor-bot-20261127-0230.tar.gz.enc
#
# 可用環境變數覆寫：BACKUP_PASS_FILE、COMPOSE（預設 "docker compose"）
set -euo pipefail

FILE="${1:-}"
MODE="${2:-}"
PASS_FILE="${BACKUP_PASS_FILE:-/root/.tumor-bot-backup-pass}"
COMPOSE="${COMPOSE:-docker compose}"

fail() { echo "✗ $*" >&2; exit 1; }
[ -f "$FILE" ] || fail "用法：bash deploy/restore.sh <備份檔.tar.gz.enc> [--extract-only]"
[ -s "$PASS_FILE" ] || fail "找不到備份密碼檔 $PASS_FILE"

umask 077
work=$(mktemp -d)
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$PASS_FILE" -in "$FILE" | tar -xzf - -C "$work" \
  || { rm -rf "$work"; fail "解密失敗：密碼檔不對，或備份檔損壞"; }

if [ "$MODE" = "--extract-only" ]; then
  echo "已解開到 $work（含病人資料，看完請刪除：rm -rf $work）"
  ls -la "$work"
  exit 0
fi
trap 'rm -rf "$work"' EXIT

echo "停止服務並還原…"
$COMPOSE stop app
$COMPOSE run --rm --no-deps -T -v "$work:/restore:ro" --entrypoint sh app -c '
  set -e
  if [ -f /app/data/tumor_bot.sqlite3 ]; then
    cp /app/data/tumor_bot.sqlite3 /app/data/tumor_bot.sqlite3.before-restore
  fi
  rm -f /app/data/tumor_bot.sqlite3-wal /app/data/tumor_bot.sqlite3-shm
  cp /restore/tumor_bot.sqlite3 /app/data/tumor_bot.sqlite3
  if [ -d /restore/leaflets ]; then rm -rf /app/data/leaflets && cp -r /restore/leaflets /app/data/leaflets; fi
'
$COMPOSE start app
echo "還原完成。原本的資料庫留在容器的 /app/data/tumor_bot.sqlite3.before-restore，確認無誤後可刪除。"
