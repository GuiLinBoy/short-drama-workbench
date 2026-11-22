#!/usr/bin/env python3
"""从 h3_execution_checks.analyze() 自动生成总导演 6 项内部复核草稿骨架。

用途：大幅减少每段 H3 提交前手工拼写 JSON 结构、证据路径、contextFingerprint 与 risks 键名的重复劳动。
安全边界：草稿中所有需人工判断的文字均保留 [待核对] 占位，不能直接冒充通过；填完真实判断后再交给
`h3_execution_checks.py record` 校验落盘。
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import h3_execution_checks as h3c
import shot_context as sc

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")


def _existing_rel(root: Path, candidates):
    seen, out = set(), []
    for item in candidates:
        if not item or not isinstance(item, str):
            continue
        p = (root / item).resolve()
        if p.is_file() and p.is_relative_to(root):
            rel = p.relative_to(root).as_posix()
            if rel not in seen:
                seen.add(rel)
                out.append(rel)
    return out


def _empty_state(prefix):
    return {dim: f"[待核对] {prefix}：{dim}" for dim in sc.DIMENSIONS}


def build_draft(root: Path, request_path: Path):
    root = root.resolve()
    report = h3c.analyze(root, request_path)
    req_rel = request_path.resolve().relative_to(root).as_posix() if request_path.resolve().is_relative_to(root) else str(request_path)
    prompt_rel = report.get("sourcePrompt") or ""
    inputs = report.get("inputs") or {}

    media_files = []
    for group in ("images", "audios", "videos", "firstLastFrames"):
        for item in inputs.get(group) or []:
            if isinstance(item, dict) and item.get("path"):
                media_files.append(item["path"])
    base_evidence = _existing_rel(root, [prompt_rel, req_rel, "clips.json", "project.json"])
    ref_evidence = _existing_rel(root, media_files + [prompt_rel, req_rel]) or base_evidence

    ctx = report.get("shotContext") or {}
    cur = ctx.get("current") or {}
    upstream = ctx.get("upstream") or []
    nxt = ctx.get("next")

    if not upstream:
        mode = "opening"
    elif cur.get("continue_from"):
        mode = "continuous"
    else:
        mode = "cut"

    prev_rows = []
    for src in upstream:
        actual = src.get("actual")
        if actual:
            ev = _existing_rel(root, [actual.get("path"), (src.get("manifest") or {}).get("prompt"), "clips.json"])
            prev_rows.append({
                "clip": src["clip"],
                "basis": "actual",
                "reviewId": actual.get("reviewId"),
                "evidence": ev or base_evidence,
                "state": _empty_state(f"上段 {src['clip']} 实际片尾状态"),
            })
        else:
            ev = _existing_rel(root, [(src.get("manifest") or {}).get("prompt"), "clips.json"])
            prev_rows.append({
                "clip": src["clip"],
                "basis": "planned",
                "evidence": ev or base_evidence,
                "state": _empty_state(f"上段 {src['clip']} 规划状态（独立转场时说明）"),
            })

    next_ev = _existing_rel(root, [prompt_rel, ((nxt.get("manifest") or {}).get("prompt") if nxt else None), "clips.json"])
    handoff = {
        "contextFingerprint": ctx.get("fingerprint", ""),
        "current": {
            "mode": mode,
            "reason": f"[待核对] 说明本段接法（{mode}）及省略或延续的关键动作",
            "evidence": _existing_rel(root, [prompt_rel, req_rel] + media_files[:2]) or base_evidence,
            "state": _empty_state("本段开场状态"),
        },
        "previous": prev_rows,
        "next": {
            "clip": nxt["clip"] if nxt else None,
            "basis": "target",
            "transition": "[待核对] 说明本段片尾交接或收束目标",
            "evidence": next_ev or base_evidence,
            "state": _empty_state("本段片尾目标状态"),
        },
    }

    checks = {}
    for key, title in h3c.MANUAL.items():
        row = {
            "verdict": "pass",
            "note": f"[待核对] {title}：填写具体核验结论（不少于 12 字）",
            "evidence": ref_evidence if key in ("reference_roles", "framing_and_speakers") else base_evidence,
        }
        if key == "continuity_and_axis":
            row["handoff"] = handoff
        checks[key] = row

    risks = {
        r["code"]: f"[待核对] 风险（{r.get('message', r['code'])}）：填写具体取舍与限制（不少于 12 字）"
        for r in (report.get("risks") or [])
    }

    return report, {
        "reviewer": "总导演",
        "checks": checks,
        "risks": risks,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path, help="作品绝对目录")
    parser.add_argument("--request", required=True, type=Path, help="H3 实际请求 JSON 路径")
    parser.add_argument("--output", type=Path, help="草稿输出路径（默认写入 reports/执行检查/复核草稿-<clip>.json）")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有草稿文件")
    args = parser.parse_args()

    root = args.project.resolve()
    req_path = args.request if args.request.is_absolute() else (root / args.request)
    report, draft = build_draft(root, req_path)

    out = args.output
    if out is None:
        clip_tag = str((report.get("clipContext") or {}).get("clip") or req_path.stem).replace("/", "-")
        out = root / "reports/执行检查" / f"复核草稿-{clip_tag}.json"
    elif not out.is_absolute():
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not args.overwrite:
        print(f"ERROR: 目标草稿已存在（加 --overwrite 可覆盖）：{out}", file=sys.stderr)
        return 2

    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    summary = {
        "draft": str(out),
        "technicalPass": report["technicalPass"],
        "errors": [e.get("message") for e in report.get("errors") or []],
        "riskCodes": [r["code"] for r in report.get("risks") or []],
        "next": f"核对并替换草稿中的 [待核对] 内容后，运行 h3_execution_checks.py record --project \"{root}\" --request \"{req_path}\" --assessments \"{out}\"",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
