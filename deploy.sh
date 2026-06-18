#!/bin/bash
# Usage: ./deploy.sh ubuntu@<EC2_IP>
# Example: ./deploy.sh ubuntu@54.123.45.67

set -e

SERVER=${1:?"請提供 EC2 位址，例如: ./deploy.sh ubuntu@54.123.45.67"}
REMOTE_DIR="/opt/urban-renewal"

echo "▸ 部署到 $SERVER ..."

# 確保遠端目錄存在
ssh "$SERVER" "mkdir -p $REMOTE_DIR"

# 同步程式碼（排除不需要的檔案）
rsync -avz --progress \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.egg-info' \
  --exclude='.git' \
  --exclude='*.pdf' \
  --exclude='demo_report_*.html' \
  --exclude='事業計劃報告書*' \
  ./ "$SERVER:$REMOTE_DIR/"

# 確保 Docker 與 Docker Compose 已安裝
ssh "$SERVER" "which docker > /dev/null || (curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker \$USER)"

# 重新 build 並啟動
ssh "$SERVER" "cd $REMOTE_DIR && docker compose -f docker-compose.prod.yml up -d --build"

echo "✓ 部署完成"
echo "  服務位址：http://$(echo $SERVER | cut -d@ -f2)"
echo "  健康檢查：http://$(echo $SERVER | cut -d@ -f2)/health"
