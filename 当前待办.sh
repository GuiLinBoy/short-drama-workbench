#!/usr/bin/env bash
set -euo pipefail

# 一键汇总当前作品的画布审核结论、音色收尾与下一步待办（适合在画布点完审核后快速续跑）
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
  echo "错误：未找到 python3。" >&2
  exit 1
fi

WORKS_DIR="$ROOT_DIR/漫剧/作品"
TOOLS_DIR="$ROOT_DIR/漫剧/短剧工具/tools"

PROJECT_INPUT="${1:-}"
if [[ "$PROJECT_INPUT" == "-h" || "$PROJECT_INPUT" == "--help" ]]; then
  cat <<'EOF'
用法: ./当前待办.sh [作品名或作品绝对路径]

不传参数时：
  - 若 漫剧/作品/ 下仅有 1 部作品，自动选中；
  - 若有多部作品，列出作品名供选择。
EOF
  exit 0
fi

"$PYTHON_BIN" -I -X utf8 - "$WORKS_DIR" "$TOOLS_DIR" "$PROJECT_INPUT" <<'PY'
import json
import sys
from pathlib import Path

works_dir = Path(sys.argv[1]).resolve()
tools_dir = Path(sys.argv[2]).resolve()
arg = sys.argv[3].strip()
sys.path.insert(0, str(tools_dir))

import canvas_review
import voice_lifecycle


def resolve_project():
    if arg:
        p = Path(arg)
        if not p.is_absolute():
            p = works_dir / arg
        if (p / "project.json").is_file():
            return p.resolve()
        print(f"错误：找不到有效作品目录（缺少 project.json）：{p}", file=sys.stderr)
        raise SystemExit(1)
    found = sorted([d for d in works_dir.iterdir() if d.is_dir() and (d / "project.json").is_file()]) if works_dir.is_dir() else []
    if not found:
        print("当前 漫剧/作品/ 下还没有作品。请先运行：./新建作品.sh <作品名>")
        raise SystemExit(0)
    if len(found) > 1:
        print("检测到多部作品，请指定作品名：")
        for d in found:
            print(f"  ./当前待办.sh \"{d.name}\"")
        raise SystemExit(0)
    return found[0].resolve()


project = resolve_project()
pj = json.loads((project / "project.json").read_text(encoding="utf-8-sig"))

print(f"=== 作品：{pj.get('title', project.name)} ===")
print(f"目录：{project}")
print(f"规格：mode={pj.get('mode')} | genre={pj.get('genre')} | ratio={pj.get('ratio')} | video={(pj.get('video') or {}).get('final_resolution')}")

# 1. 画布审核状态
store = canvas_review.ReviewStore(project)
items = store.list_items()
by_status = {}
for item in items:
    by_status.setdefault(item.get("status", "unknown"), []).append(item)

print("\n--- 1. 画布审核汇总 ---")
if not items:
    print("暂无画布送审项（可进入立项/分镜阶段准备首批送审材料）。")
else:
    counts = {k: len(v) for k, v in sorted(by_status.items())}
    print("各状态数量：", json.dumps(counts, ensure_ascii=False))
    for status, label in (
        ("revision_requested", "【要求修改】"),
        ("pending", "【待用户确认】"),
        ("needs_re_review", "【内容变更需重审】"),
        ("deferred", "【已暂缓】"),
        ("approved", "【已通过】"),
    ):
        rows = by_status.get(status) or []
        if not rows:
            continue
        print(f"{label} ({len(rows)}):")
        for r in rows[:10]:
            latest = r.get("latestDecision") or {}
            comment = (latest.get("comment") or "").strip()
            extra = f" | 意见：{comment}" if comment else ""
            print(f"  - [{r.get('kind')}] {r.get('subject')} ({r.get('id')}){extra}")
        if len(rows) > 10:
            print(f"  ... 其余 {len(rows) - 10} 项")

# 2. 音色收尾状态
print("\n--- 2. 首次开口音色收尾 ---")
vc = voice_lifecycle.safe_audit(project)
pending_prep = vc.get("pendingPreparation") or vc.get("needsPreparation") or []
pending_conf = vc.get("pendingConfirmation") or vc.get("needsConfirmation") or []
if vc.get("error"):
    print(f"提示：{vc['error']}")
elif not pending_prep and not pending_conf:
    print("无待收尾音色。")
else:
    if pending_prep:
        print("待提取/登记/送审：", json.dumps(pending_prep, ensure_ascii=False))
    if pending_conf:
        print("待画布试听确认：", json.dumps(pending_conf, ensure_ascii=False))

# 3. 建议发给 AI 的续跑指令
print("\n--- 3. 可直接发给 AI 的续跑提示 ---")
if by_status.get("revision_requested"):
    print(f"请读取《{project.name}》画布审核中【要求修改】的意见并原位返工（作品目录：{project}）。")
elif pending_prep:
    print(f"请为《{project.name}》完成已通过首次开口视频的音色提取、登记与送审（作品目录：{project}）。")
elif by_status.get("pending") or by_status.get("needs_re_review"):
    print(f"画布 http://127.0.0.1:8778/ 仍有待你确认项；确认后再告诉 AI 继续推进《{project.name}》。")
else:
    print(f"画布当前无阻塞项，请继续推进《{project.name}》下一阶段制作（作品目录：{project}）。")
PY
