#!/usr/bin/env bash
set -euo pipefail

# macOS 快速新建作品脚本（纯本地：创建目录、标准 project.json、安装共享规范入口并校验）
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [[ $# -lt 1 || "$1" == "-h" || "$1" == "--help" ]]; then
  cat <<'EOF'
用法: ./新建作品.sh <作品名> [题材] [画幅] [模式] [目标时长秒]

参数说明:
  作品名       必填，将在 漫剧/作品/<作品名>/ 下创建独立目录
  题材         可选，默认 3d-guoman（可选：3d-guoman / live-action / urban）
  画幅         可选，默认 9:16（可选：9:16 / 16:9 / 3:4 / 4:3 / 1:1 / 21:9）
  模式         可选，默认 short（可选：short 短片 / series 长篇）
  目标时长秒   可选，默认 60

示例:
  ./新建作品.sh 雪夜短片
  ./新建作品.sh 都市反转 3d-guoman 9:16 series 90
EOF
  exit 0
fi

TITLE="$1"
GENRE="${2:-3d-guoman}"
RATIO="${3:-9:16}"
MODE="${4:-short}"
DURATION="${5:-60}"

case "$GENRE" in
  3d-guoman|live-action|urban) ;;
  *) echo "错误：不支持的题材 '$GENRE'（可选：3d-guoman / live-action / urban）" >&2; exit 1 ;;
esac

case "$RATIO" in
  9:16|16:9|3:4|4:3|1:1|21:9) ;;
  *) echo "错误：不支持的画幅 '$RATIO'" >&2; exit 1 ;;
esac

case "$MODE" in
  short|series) ;;
  *) echo "错误：不支持的模式 '$MODE'（可选：short / series）" >&2; exit 1 ;;
esac

if [[ "$TITLE" == *"/"* || "$TITLE" == *"\\"* || "$TITLE" == "." || "$TITLE" == ".." ]]; then
  echo "错误：作品名不能包含路径分隔符。" >&2
  exit 1
fi

PYTHON_BIN=""
for bin in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if command -v "$bin" >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v "$bin")"
    break
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  echo "错误：未找到 python3。" >&2
  exit 1
fi

PROJECT_DIR="$ROOT_DIR/漫剧/作品/$TITLE"
if [[ -e "$PROJECT_DIR" ]]; then
  echo "错误：作品目录已存在：$PROJECT_DIR" >&2
  exit 1
fi

IMAGE_MODEL="grok-imagine-image-quality"
if [[ "$GENRE" == "live-action" || "$GENRE" == "urban" ]]; then
  IMAGE_MODEL="grok-imagine-image-2.0"
fi

BURN_SUBTITLES="true"
if [[ "$MODE" == "series" ]]; then
  BURN_SUBTITLES="false"
fi

mkdir -p "$PROJECT_DIR/原文" "$PROJECT_DIR/reports" "$PROJECT_DIR/logs"

"$PYTHON_BIN" -I -X utf8 - "$PROJECT_DIR/project.json" "$TITLE" "$MODE" "$GENRE" "$RATIO" "$DURATION" "$IMAGE_MODEL" "$BURN_SUBTITLES" <<'PY'
import json, sys
from pathlib import Path

out, title, mode, genre, ratio, duration, image_model, burn = sys.argv[1:9]
data = {
    "schema_version": 2,
    "title": title,
    "mode": mode,
    "genre": genre,
    "ratio": ratio,
    "character_sheet_ratio": "16:9",
    "target_duration": int(duration),
    "max_revisions": 2,
    "image": {
        "provider": "nexorai",
        "model": image_model,
    },
    "video": {
        "provider": "minimax-h3",
        "model": "MiniMax-H3",
        "draft_resolution": "768P",
        "final_resolution": "768P",
        "use_context_ir": False,
        "audio": "generate",
    },
    "post": {
        "loudness_lufs": -14,
        "true_peak_db": -1,
        "bgm_duck_db": 12,
        "subtitles": "srt",
        "burn_subtitles": burn == "true",
        "aigc_label": {
            "visible": True,
            "metadata": True,
            "text": "本片含 AI 生成内容",
            "badge": "AI 生成",
            "badge_position": "top-right",
        },
    },
    "publish": False,
}
Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
PY

"$PYTHON_BIN" -I -X utf8 "$ROOT_DIR/漫剧/短剧工具/tools/project_rules.py" --project "$PROJECT_DIR" --install

echo ""
echo "作品已创建：$PROJECT_DIR"
echo "下一步："
echo "  1. 将剧本/小说原稿放入：$PROJECT_DIR/原文/"
echo "  2. 双击 启动画布.command 打开 http://127.0.0.1:8778/ 查看作品"
echo "  3. 对执行者说：按本工作区 AGENTS.md 和 本机工作流-macOS.md 制作《${TITLE}》"
