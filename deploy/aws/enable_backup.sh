#!/usr/bin/env bash
# 在 AWS CloudShell 執行：為腫瘤衛教機器人的資料硬碟設定「每日加密快照」，並確認硬碟本身有加密。
# create_instance.sh 建好主機後會自動執行；已經有主機的人可以單獨執行：
#       bash enable_backup.sh
#
# 做的事（重複執行不會重複建立）：
#   1. 確認名為 <NAME>-data 的硬碟是加密的（沒加密就停下來，請依 docs/deploy/AWS部署步驟.md 處理）
#   2. 建立 Data Lifecycle Manager 需要的預設角色（AWSDataLifecycleManagerDefaultRole）
#   3. 建立每日快照規則：台灣時間每天 02:00，保留 RETAIN 份（預設 7）。加密硬碟的快照自動加密
# 可用環境變數覆寫：REGION、NAME、RETAIN
set -euo pipefail

REGION="${REGION:-ap-east-2}"
NAME="${NAME:-tumor-bot}"
RETAIN="${RETAIN:-7}"
export AWS_DEFAULT_REGION="$REGION" AWS_PAGER=""

say()  { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

say "確認資料硬碟有加密"
vols=$(aws ec2 describe-volumes --filters "Name=tag:Name,Values=$NAME-data" \
         --query 'Volumes[].[VolumeId,Encrypted]' --output text)
[ -n "$vols" ] || fail "找不到名為 $NAME-data 的硬碟（主機是不是用別的名稱建立的？可用 NAME=<名稱> bash enable_backup.sh）"
echo "$vols"
if echo "$vols" | awk '{print $2}' | grep -qv True; then
  fail "有硬碟沒有加密。請依 docs/deploy/AWS部署步驟.md「硬碟沒有加密時」處理後再執行。"
fi

say "Data Lifecycle Manager 預設角色"
aws dlm create-default-role --resource-type snapshot >/dev/null 2>&1 || true
ROLE_ARN=$(aws iam get-role --role-name AWSDataLifecycleManagerDefaultRole --query Role.Arn --output text 2>/dev/null) \
  || fail "無法建立或讀取 AWSDataLifecycleManagerDefaultRole，請確認帳號有 IAM 權限"
echo "$ROLE_ARN"

say "每日快照規則（台灣時間 02:00，保留 $RETAIN 份）"
existing=$(aws dlm get-lifecycle-policies --target-tags "Name=$NAME-data" \
             --query 'Policies[?State==`ENABLED`].PolicyId' --output text 2>/dev/null || true)
if [ -n "$existing" ] && [ "$existing" != "None" ]; then
  echo "已經有規則 $existing，不重複建立。"
else
  aws dlm create-lifecycle-policy \
    --description "$NAME daily encrypted snapshots" \
    --state ENABLED \
    --execution-role-arn "$ROLE_ARN" \
    --tags "Name=$NAME-backup" \
    --policy-details "{
      \"PolicyType\": \"EBS_SNAPSHOT_MANAGEMENT\",
      \"ResourceTypes\": [\"VOLUME\"],
      \"TargetTags\": [{\"Key\": \"Name\", \"Value\": \"$NAME-data\"}],
      \"Schedules\": [{
        \"Name\": \"daily\",
        \"CopyTags\": true,
        \"TagsToAdd\": [{\"Key\": \"Name\", \"Value\": \"$NAME-snapshot\"}],
        \"CreateRule\": {\"Interval\": 24, \"IntervalUnit\": \"HOURS\", \"Times\": [\"18:00\"]},
        \"RetainRule\": {\"Count\": $RETAIN}
      }]
    }" --query PolicyId --output text
fi

cat <<EOF

完成。快照會在 EC2 主控台 → 快照（Snapshots）出現，名稱 $NAME-snapshot，和硬碟一樣是加密的。
建議上線前試一次還原：從快照建立硬碟 → 掛到測試主機 → 確認資料庫打得開（步驟見 docs/deploy/AWS部署步驟.md）。
EOF
