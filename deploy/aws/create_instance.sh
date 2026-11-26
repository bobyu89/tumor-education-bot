#!/usr/bin/env bash
# 在 AWS CloudShell 執行：在台北區（ap-east-2）建立一台跑腫瘤衛教機器人的 EC2 主機。
# 步驟說明：docs/deploy/AWS部署步驟.md
#
#   把這個檔案與同資料夾的 install.sh 上傳到 CloudShell，然後：
#       bash create_instance.sh
#
# 會建立（名稱都以 tumor-bot 開頭，重複執行不會重複建立）：
#   - EC2 主機 t3.medium（2 vCPU／4 GB），Ubuntu 24.04，30 GB 加密硬碟，開機自動安裝 Docker
#   - 安全群組：對外只開 80、443；不開 SSH（用瀏覽器的 Session Manager 或 EC2 Instance Connect 登入）
#   - 固定 IP（Elastic IP）：網址 <IP>.sslip.io 才不會因重開機而變
#   - IAM 角色：讓主機能用 Session Manager 登入（不需要 SSH 金鑰）
# 可用環境變數覆寫：REGION、NAME、INSTANCE_TYPE、DISK_GB
set -euo pipefail

REGION="${REGION:-ap-east-2}"
NAME="${NAME:-tumor-bot}"
TYPE="${INSTANCE_TYPE:-t3.medium}"
DISK_GB="${DISK_GB:-30}"
export AWS_DEFAULT_REGION="$REGION" AWS_PAGER=""
HERE="$(cd "$(dirname "$0")" && pwd)"

say()  { printf '\n\033[1m▶ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$HERE/install.sh" ] || fail "找不到 install.sh：請把 deploy/aws/install.sh 也上傳到同一個資料夾"

show_info() {
  local iid="$1" ip
  ip=$(aws ec2 describe-instances --instance-ids "$iid" \
        --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
  cat <<EOF

主機：$iid（$REGION）
固定 IP：$ip
網址（安裝完成後）：https://${ip//./-}.sslip.io

下一步：
  1. EC2 主控台 → 執行個體 → 勾選 $NAME → 右上「連線」(Connect)
     → 選「Session Manager」（或「EC2 Instance Connect」）→ 連線
     （主機剛開機時要等 2–3 分鐘才連得上）
  2. 在主機的終端機輸入：
       sudo bash /opt/tumor-bot/install.sh
EOF
}

# ── 0. 地區是否已啟用 ──
say "檢查 $REGION"
aws ec2 describe-availability-zones --query 'AvailabilityZones[0].ZoneName' --output text >/dev/null 2>&1 \
  || fail "無法使用 $REGION。請到主控台右上角帳號名稱 → Account → AWS Regions，啟用 Asia Pacific (Taipei)，等幾分鐘後再執行。"

existing=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [ -n "$existing" ]; then
  echo "已經有名為 $NAME 的主機，不重複建立。"
  show_info "$existing"
  exit 0
fi

# ── 1. 主機規格 ──
offered() {
  aws ec2 describe-instance-type-offerings --location-type region \
    --filters "Name=instance-type,Values=$1" --query 'InstanceTypeOfferings[0].InstanceType' --output text
}
if [ "$(offered "$TYPE")" != "$TYPE" ]; then
  for t in t3a.medium t3.medium; do
    if [ "$(offered "$t")" = "$t" ]; then echo "$TYPE 在 $REGION 沒有提供，改用 $t"; TYPE="$t"; break; fi
  done
  [ "$(offered "$TYPE")" = "$TYPE" ] || fail "$REGION 沒有 t3.medium／t3a.medium，請用 INSTANCE_TYPE=<規格> bash create_instance.sh 指定（需 2 vCPU、4 GB 以上）"
fi
echo "規格：$TYPE"

# ── 2. 映像：Ubuntu 24.04（x86_64）──
say "尋找 Ubuntu 24.04 映像"
AMI=$(aws ssm get-parameter \
        --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
        --query Parameter.Value --output text 2>/dev/null || true)
if [ -z "$AMI" ] || [ "$AMI" = "None" ]; then
  AMI=$(aws ec2 describe-images --owners 099720109477 \
          --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" "Name=state,Values=available" \
          --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text)
fi
[ -n "$AMI" ] && [ "$AMI" != "None" ] || fail "找不到 Ubuntu 24.04 映像"
echo "映像：$AMI"

# ── 3. 網路：預設 VPC 與安全群組 ──
say "設定網路"
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
if [ "$VPC" = "None" ] || [ -z "$VPC" ]; then
  VPC=$(aws ec2 create-default-vpc --query 'Vpc.VpcId' --output text)
  echo "已建立預設 VPC $VPC"
fi
SG=$(aws ec2 describe-security-groups --filters "Name=vpc-id,Values=$VPC" "Name=group-name,Values=$NAME-web" \
       --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG" = "None" ] || [ -z "$SG" ]; then
  SG=$(aws ec2 create-security-group --group-name "$NAME-web" --vpc-id "$VPC" \
         --description "tumor-bot: HTTPS only (80 for certificate renewal)" --query GroupId --output text)
  aws ec2 authorize-security-group-ingress --group-id "$SG" --ip-permissions \
    'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]' \
    'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]' >/dev/null
  # EC2 Instance Connect（瀏覽器登入）：只允許 AWS 自己的 Instance Connect 服務連 SSH，不對外開放
  PL=$(aws ec2 describe-managed-prefix-lists \
         --filters "Name=prefix-list-name,Values=com.amazonaws.$REGION.ec2-instance-connect" \
         --query 'PrefixLists[0].PrefixListId' --output text 2>/dev/null || true)
  if [ -n "$PL" ] && [ "$PL" != "None" ]; then
    aws ec2 authorize-security-group-ingress --group-id "$SG" --ip-permissions \
      "IpProtocol=tcp,FromPort=22,ToPort=22,PrefixListIds=[{PrefixListId=$PL}]" >/dev/null
  fi
fi
echo "安全群組：$SG（對外只開 80、443）"

# ── 4. Session Manager 用的 IAM 角色 ──
say "設定 Session Manager 登入權限"
ROLE="$NAME-ssm"
if ! aws iam get-instance-profile --instance-profile-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
  echo "等待權限生效…"; sleep 15
fi

# ── 5. 開機腳本：安裝 Docker，並把 install.sh 放到主機上 ──
USER_DATA=$(cat <<EOF
#!/bin/bash
set -eux
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 docker-buildx git openssl
systemctl enable --now docker
usermod -aG docker ubuntu
# 2 GB swap：首次建立索引時記憶體用量較高，避免被系統砍掉
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
mkdir -p /opt/tumor-bot
base64 -d > /opt/tumor-bot/install.sh <<'B64'
$(base64 -w0 "$HERE/install.sh")
B64
chmod 700 /opt/tumor-bot/install.sh
touch /opt/tumor-bot/.bootstrap-done
EOF
)

# ── 6. 建立主機 ──
say "建立主機（$TYPE、${DISK_GB} GB）"
for attempt in 1 2 3 4; do
  IID=$(aws ec2 run-instances \
    --image-id "$AMI" --instance-type "$TYPE" \
    --security-group-ids "$SG" \
    --iam-instance-profile "Name=$ROLE" \
    --metadata-options HttpTokens=required,HttpEndpoint=enabled \
    --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":$DISK_GB,\"VolumeType\":\"gp3\",\"Encrypted\":true,\"DeleteOnTermination\":false}}]" \
    --disable-api-termination \
    --user-data "$USER_DATA" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" "ResourceType=volume,Tags=[{Key=Name,Value=$NAME-data}]" \
    --query 'Instances[0].InstanceId' --output text 2>/tmp/run.err) && break
  # 剛建立的 IAM 角色偶爾還沒傳播到 EC2，稍等再試
  grep -qi "instance profile" /tmp/run.err && { echo "IAM 角色尚未生效，15 秒後重試（$attempt/4）"; sleep 15; continue; }
  cat /tmp/run.err >&2; fail "建立主機失敗"
done
[ -n "${IID:-}" ] || fail "建立主機失敗（IAM 角色一直沒有生效，請過幾分鐘再執行一次）"
echo "主機 $IID 啟動中…"
aws ec2 wait instance-running --instance-ids "$IID"

# ── 7. 固定 IP ──
say "配置固定 IP"
ALLOC=$(aws ec2 allocate-address --domain vpc \
          --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME}]" \
          --query AllocationId --output text)
aws ec2 associate-address --instance-id "$IID" --allocation-id "$ALLOC" >/dev/null

say "完成"
show_info "$IID"
