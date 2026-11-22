#!/usr/bin/env python3
"""抽承接用的尾帧（试生成和定稿分开），依据：数据规范-v2.md §3、设计文档 §8.1。

替代原技能包 skill/scripts/extract_ending_frame.py 的 --manifest 模式（原版只读定稿视频，
尾帧文件名和文档对不上，而且用 `which` 找 ffmpeg，在 Windows PowerShell 下跑不了；原版保持不改）。

用法：ending_frame.py --project-root <作品目录> --clip-id EP001/Clip00002 --pass draft|final
- draft：从上一段的试生成视频（draft.video）抽，存 单集制作/EPxxx/尾帧/ClipNNNNN-draft.png
- final：从上一段的定稿视频（video）抽，存 单集制作/EPxxx/尾帧/ClipNNNNN.png（定稿承接必须用定稿尾帧）
只抽图和写承接报告，不改 clips.json；尾帧路径由摄影用 pipeline_state.py 写进上一段的 draft.ending_frame / ending_frame。
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile
from datetime import datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # Windows 中文系统下 -I 会忽略 PYTHONUTF8，强制输出 UTF-8
    _s.reconfigure(encoding="utf-8")


def fail(msg):
    print("ERROR: " + msg, file=sys.stderr)
    raise SystemExit(1)


def inside(root, rel, label):
    if not isinstance(rel, str) or not rel:
        fail(f"{label} 为空")
    p = (root / rel).resolve()
    if Path(rel).is_absolute() or not p.is_relative_to(root):
        fail(f"{label} 跑出了作品目录：{rel}")
    return p


def extract_last_frame(video, out):
    if not shutil.which("ffmpeg"):
        fail("找不到 ffmpeg：请确认已安装并在 PATH 里（装完要重启终端或应用）")
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out.parent, suffix=".png")
    os.close(fd)
    try:
        # 解码到最后一帧；用 -frames:v 1 拿到的是第一帧
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-map", "0:v:0", "-an",
                            "-fps_mode", "passthrough", "-update", "1", tmp], capture_output=True, text=True)
        if r.returncode != 0 or Path(tmp).stat().st_size == 0:
            fail(f"抽帧失败：{video}\n{r.stderr.strip()}")
        try:
            os.link(tmp, out)
        except FileExistsError:
            fail(f"尾帧已存在，不覆盖：{out}")
    finally:
        Path(tmp).unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project-root", type=Path, required=True)
    p.add_argument("--clip-id", required=True, help="要生成的片段，例如 EP001/Clip00002（从它的 continue_from 抽尾帧）")
    p.add_argument("--pass", dest="pass_", choices=("draft", "final"), required=True)
    a = p.parse_args()
    root = a.project_root.resolve()
    clips = json.loads((root / "clips.json").read_text(encoding="utf-8"))
    ids = [c.get("id") for c in clips]
    if a.clip_id not in ids:
        fail(f"clips.json 里没有 {a.clip_id}")
    target = clips[ids.index(a.clip_id)]
    source_id = target.get("continue_from")
    if not source_id:
        print(json.dumps({"clip": a.clip_id, "continue_from": None, "ending_frame": None,
                          "note": "这一段不承接，不需要尾帧"}, ensure_ascii=False, indent=2))
        return
    if source_id not in ids or ids.index(source_id) >= ids.index(a.clip_id):
        fail(f"承接来源 {source_id} 必须是排在 {a.clip_id} 前面的片段")
    source = clips[ids.index(source_id)]
    warnings = []
    if a.pass_ == "draft":
        draft = source.get("draft") if isinstance(source.get("draft"), dict) else {}
        rel, status = draft.get("video"), draft.get("status")
        if not rel:
            fail(f"{source_id} 没有 draft.video，试生成阶段没法承接")
    else:
        rel, status = source.get("video"), source.get("status")
        if status not in ("generated", "approved"):
            warnings.append(f"{source_id} 的定稿状态是 {status}，定稿承接应当用已生成的定稿视频")
    video = inside(root, rel, f"{source_id} 的视频")
    if not video.is_file() or not video.stat().st_size:
        fail(f"{source_id} 的视频不存在或为空：{rel}")
    episode, clipno = source_id.split("/")
    frame_rel = f"单集制作/{episode}/尾帧/{clipno}{'-draft' if a.pass_ == 'draft' else ''}.png"
    frame = inside(root, frame_rel, "尾帧")
    tepisode, tclip = a.clip_id.split("/")
    rpt = root / "单集制作" / tepisode / "承接报告" / f"{tclip}-{a.pass_}.json"
    if frame.exists() or rpt.exists():
        fail("尾帧或承接报告已存在；请人工审阅后另开尝试，不覆盖已有文件")
    extract_last_frame(video, frame)
    report = {
        "clip": a.clip_id, "pass": a.pass_, "continue_from": source_id, "source_video": rel,
        "source_status": status, "source_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "ending_frame": frame_rel, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "warnings": warnings,
        "next": [f"亲自看过 {frame_rel} 后，用 h3_task.py set-continuity --frame {frame_rel} 改 {a.clip_id} 的承接句",
                 f"pipeline_state.py --role 摄影 set-clip {source_id} --set "
                 f"{'draft.ending_frame' if a.pass_ == 'draft' else 'ending_frame'}={frame_rel}"],
    }
    rpt.parent.mkdir(parents=True, exist_ok=True)
    with open(rpt, "x", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
