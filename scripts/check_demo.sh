#!/bin/bash
# 一鍵檢查 demo 狀態（在本機 Mac 跑即可）。
# 用法：
#   bash scripts/check_demo.sh                      # 用預設 Pod 網址
#   bash scripts/check_demo.sh <Pod臨時通道網址>     # Pod 網址重啟後會變，傳新的進來
#
# 兩個 demo：
#   AWS 穩定站（PaddleOCR，可跑真案）：固定網址
#   Pod VLM demo（地端 VLM，僅非 PII）：cloudflared 臨時通道，每次重啟換網址
#   （Pod 上重撈網址：grep -oE "https://[a-z-]+\.trycloudflare\.com" /mnt/shenyi-data/cf_web.log | tail -1）

AWS="https://urban-renewal.sakilu-dev.uk"
POD="${1:-https://adequate-watts-choosing-intensive.trycloudflare.com}"

check() {
  local name="$1" url="$2"
  printf "── %s\n   %s\n" "$name" "$url"
  local code health
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$url/" 2>/dev/null)
  health=$(curl -s --max-time 15 "$url/health" 2>/dev/null)
  if [ "$code" = "200" ]; then
    echo "   ✅ HTTP $code | ${health:-（/health 無回應）}"
  else
    echo "   ❌ 無回應（HTTP ${code:-timeout}）— 站台可能已停，或網址已變"
  fi
  echo
}

echo "========== Demo 狀態檢查 =========="
check "AWS 穩定站（PaddleOCR·可跑真案）" "$AWS"
check "Pod VLM demo（地端 VLM·僅非 PII）" "$POD"
echo "註：Pod trycloudflare 網址每次重啟會變；若 Pod 那條 ❌，請上 Pod 重撈 cf_web.log 最新網址，再："
echo "    bash scripts/check_demo.sh <新網址>"
