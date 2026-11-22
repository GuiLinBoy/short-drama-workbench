#!/usr/bin/env bash
set -euo pipefail

# macOS Finder 双击启动画布入口
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$ROOT_DIR/漫剧/短剧工具/canvas/start.sh" "$@"
