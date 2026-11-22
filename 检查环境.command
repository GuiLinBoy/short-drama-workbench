#!/usr/bin/env bash
set -euo pipefail

# macOS 环境检查脚本（不联网、不读取密钥、不生成媒体）
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PYTHON_BIN=""
for bin in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if command -v "$bin" >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v "$bin")"
    break
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "错误：未检测到 python3，请先安装 Python 3.11+（例如：brew install python@3.12）。"
  exit 1
fi

"$PYTHON_BIN" -I -X utf8 "$ROOT_DIR/漫剧/短剧工具/tools/portable/doctor.py" || true

echo ""
if command -v ffmpeg >/dev/null 2>&1; then
  FILTERS="$(ffmpeg -hide_banner -filters 2>/dev/null || true)"
  for flt in loudnorm drawtext subtitles; do
    if echo "$FILTERS" | grep -qE "[[:space:]]${flt}[[:space:]]"; then
      echo "FFmpeg 滤镜 ${flt}：支持"
    else
      echo "FFmpeg 滤镜 ${flt}：本机缺失（已自动支持由 ZeroTier /v1/video/render 远端渲染，无需另装 ffmpeg-full）"
    fi
  done
fi

if command -v lsof >/dev/null 2>&1; then
  LISTEN_PID="$(lsof -nP -iTCP:8778 -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true)"
  if [[ -n "$LISTEN_PID" ]]; then
    echo "画布端口 8778：已被占用（PID ${LISTEN_PID}）"
  else
    echo "画布端口 8778：空闲（可用）"
  fi
fi

REMOTE_ASR_URL="${ASR_REMOTE_URL:-http://10.197.123.1:8000}"
REMOTE_TOKEN_FILE="${ASR_REMOTE_TOKEN_FILE:-$HOME/Downloads/token.txt}"
if [[ -f "$REMOTE_TOKEN_FILE" ]]; then
  echo "远端 ZeroTier ASR 令牌文件：存在（${REMOTE_TOKEN_FILE}）"
else
  echo "远端 ZeroTier ASR 令牌文件：未配置（可用 ASR_REMOTE_TOKEN_FILE 或钥匙串 ZEROTIER_VIDEO_API_TOKEN）"
fi
if command -v curl >/dev/null 2>&1; then
  if curl --disable --noproxy '*' --silent --fail --connect-timeout 2 --max-time 4 "${REMOTE_ASR_URL%/}/health" >/dev/null 2>&1; then
    echo "远端 ZeroTier ASR 服务（${REMOTE_ASR_URL}）：在线可用（无需本机安装 FunASR）"
  else
    echo "远端 ZeroTier ASR 服务（${REMOTE_ASR_URL}）：当前离线或未连通"
  fi
fi
