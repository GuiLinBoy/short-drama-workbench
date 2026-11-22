#!/usr/bin/env bash
set -euo pipefail

# macOS / POSIX 正式画布启动脚本
# 固定正式入口：http://127.0.0.1:8778/（不自动切换端口）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 兼容 Finder 双击 .command 时未加载 Homebrew PATH 的情况
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

AUTO_OPEN=1
CUSTOM_PORT=""
SERVER_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-open)
      AUTO_OPEN=0
      shift
      ;;
    --port)
      if [[ $# -lt 2 ]]; then
        echo "错误：--port 需要指定端口号。" >&2
        exit 1
      fi
      CUSTOM_PORT="$2"
      SERVER_ARGS+=("$1" "$2")
      shift 2
      ;;
    --port=*)
      CUSTOM_PORT="${1#*=}"
      SERVER_ARGS+=("$1")
      shift
      ;;
    -h|--help)
      cat <<'EOF'
用法: ./start.sh [选项]

选项:
  --no-open              启动后不自动打开浏览器
  --read-only            仅查看，禁止新增审核记录
  --preview              隔离预览模式（需配合 --data-root 与 --port）
  --data-root <DIR>      指定漫剧数据根目录
  --port <PORT>          指定端口（正式模式固定 8778）
  -h, --help             显示帮助信息
EOF
      exit 0
      ;;
    *)
      SERVER_ARGS+=("$1")
      shift
      ;;
  esac
done

PORT="${CUSTOM_PORT:-8778}"
URL="http://127.0.0.1:${PORT}/"

# 查找可用的 Python 3（优先 3.11+，画布最低兼容 3.10+）
find_python() {
  local candidates=(
    "python3"
    "/opt/homebrew/bin/python3"
    "/usr/local/bin/python3"
    "/opt/homebrew/opt/python@3.13/libexec/bin/python3"
    "/opt/homebrew/opt/python@3.12/libexec/bin/python3"
    "/opt/homebrew/opt/python@3.11/libexec/bin/python3"
    "/usr/bin/python3"
  )
  local fallback=""
  for bin in "${candidates[@]}"; do
    if command -v "$bin" >/dev/null 2>&1; then
      local resolved
      resolved="$(command -v "$bin")"
      if "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
        echo "$resolved"
        return 0
      fi
      if [[ -z "$fallback" ]] && "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        fallback="$resolved"
      fi
    fi
  done
  if [[ -n "$fallback" ]]; then
    echo "提示：未检测到 Python 3.11+，当前使用 3.10 仅启动画布；完整制作工具需要 Python 3.11+。" >&2
    echo "$fallback"
    return 0
  fi
  return 1
}

PYTHON_BIN="$(find_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "错误：未找到 Python 3.10+（推荐安装 Python 3.11 或 3.12：brew install python@3.12）。" >&2
  exit 1
fi

if [[ ! -f "dist/index.html" ]]; then
  echo "错误：缺少已构建页面 dist/index.html。" >&2
  echo "请先在以下目录执行构建：" >&2
  echo "  cd \"$SCRIPT_DIR\"" >&2
  echo "  npm ci --ignore-scripts --no-audit --no-fund && npm run build" >&2
  exit 1
fi

# 检查端口占用（遵循规则：不自动改端口）
if command -v lsof >/dev/null 2>&1; then
  LISTEN_PID="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true)"
  if [[ -n "$LISTEN_PID" ]]; then
    LISTEN_CMD="$(ps -p "$LISTEN_PID" -o command= 2>/dev/null || true)"
    if [[ "$LISTEN_CMD" == *"server.py"* ]]; then
      echo "画布服务已在运行（PID: ${LISTEN_PID}）：${URL}"
      if [[ "$AUTO_OPEN" -eq 1 ]] && command -v open >/dev/null 2>&1; then
        open "$URL"
      fi
      exit 0
    else
      echo "无法启动画布：端口 ${PORT} 已被其他进程占用（未自动切换端口）。" >&2
      echo "占用进程：PID ${LISTEN_PID} — ${LISTEN_CMD}" >&2
      exit 1
    fi
  fi
fi

echo "使用 Python: $("$PYTHON_BIN" --version) ($PYTHON_BIN)"
echo "画布地址: ${URL}"
echo "保持此终端窗口打开，按 Ctrl+C 停止服务。"
echo ""

# 服务就绪后自动在默认浏览器打开
if [[ "$AUTO_OPEN" -eq 1 ]] && command -v open >/dev/null 2>&1; then
  (
    for _ in $(seq 1 40); do
      if "$PYTHON_BIN" -c "import socket, sys; s = socket.socket(); s.settimeout(0.2); rc = s.connect_ex(('127.0.0.1', int(sys.argv[1]))); s.close(); raise SystemExit(0 if rc == 0 else 1)" "$PORT" 2>/dev/null; then
        open "$URL"
        exit 0
      fi
      sleep 0.25
    done
  ) &
fi

if [[ -z "$CUSTOM_PORT" ]]; then
  exec "$PYTHON_BIN" -I -X utf8 server.py --port 8778 ${SERVER_ARGS[@]+"${SERVER_ARGS[@]}"}
else
  exec "$PYTHON_BIN" -I -X utf8 server.py ${SERVER_ARGS[@]+"${SERVER_ARGS[@]}"}
fi
