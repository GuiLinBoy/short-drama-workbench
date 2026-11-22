#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import re
import subprocess
import sys
from pathlib import Path


OUTLINE_BLOCK_TITLE_PATTERN = re.compile(r"^(第.+章\s+.+|第.+个故事\s+.+)$")
OUTLINE_CONTINUE_PATTERNS = (
    "本故事开头画面是否延续上一个故事：",
    "本章开头画面是否延续上一章：",
)
SUMMARY_PATTERN = "延续画面简述："
CLIP_PATTERN = re.compile(r"^视频_Clip(\d+)\.mp4$")
EP_PATTERN = re.compile(r"^EP(\d+)$")
STORYBOARD_CLIP_PATTERN = re.compile(r"^##\s*Clip\s*(\d+)\s*$", re.IGNORECASE)
CLIP_CONTINUE_PATTERN = "是否延续上一个clip画面："
CLIP_SUMMARY_PATTERN = "内容概述："


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def ensure_relative(path_str: str, arg_name: str) -> Path:
    path = Path(path_str)
    if path.is_absolute() or ".." in path.parts:
        fail(f"{arg_name} 必须使用相对路径，当前收到绝对路径: {path}")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="处理跨集或同集 clip 承接，抽取上一条视频的尾帧并生成承接信息文件。"
    )
    parser.add_argument("--project-root", default=".", help="项目根目录，相对路径，默认当前目录")
    parser.add_argument("--episode-dir", help="当前集目录，例如 单集制作/EP002")
    parser.add_argument("--outline", default="大纲.txt", help="大纲文件相对路径，默认 大纲.txt")
    parser.add_argument("--storyboard", help="文字分镜文件相对路径；同集 clip 模式默认使用 <episode-dir>/文字分镜.txt")
    parser.add_argument("--clip", type=int, help="当前要生成的 clip 编号，例如 --clip 2 表示为 Clip00002 判断是否承接 Clip00001")
    parser.add_argument("--ending-frame-name", default="全集-尾帧.png", help="上一集跨集尾帧文件名")
    parser.add_argument("--report-name", default="全集-首帧承接信息.txt", help="当前集跨集承接结果文件名")
    parser.add_argument("--manifest", help="clips.json relative to project root")
    parser.add_argument("--clip-id", help="EP001/Clip00002")
    return parser.parse_args()


def read_outline_blocks(outline_path: Path) -> list[dict[str, str]]:
    lines = outline_path.read_text(encoding="utf-8").splitlines()
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if OUTLINE_BLOCK_TITLE_PATTERN.match(line):
            if current:
                blocks.append(current)
            current = {"title": line, "continue": "", "summary": "不适用"}
            continue
        if current is None:
            continue
        matched_continue_prefix = next(
            (pattern for pattern in OUTLINE_CONTINUE_PATTERNS if line.startswith(pattern)),
            None,
        )
        if matched_continue_prefix:
            current["continue"] = line.split("：", 1)[1].strip()
        elif line.startswith(SUMMARY_PATTERN):
            current["summary"] = line.split("：", 1)[1].strip()

    if current:
        blocks.append(current)
    return blocks


def read_storyboard_blocks(storyboard_path: Path) -> dict[int, dict[str, str]]:
    lines = storyboard_path.read_text(encoding="utf-8").splitlines()
    blocks: dict[int, dict[str, str]] = {}
    current_number: int | None = None
    current: dict[str, str] | None = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        match = STORYBOARD_CLIP_PATTERN.match(line)
        if match:
            if current_number is not None and current is not None:
                blocks[current_number] = current
            current_number = int(match.group(1))
            current = {"continue": "", "summary": ""}
            continue
        if current is None:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if line.startswith(CLIP_CONTINUE_PATTERN):
            current["continue"] = line.split("：", 1)[1].strip()
        elif line.startswith(CLIP_SUMMARY_PATTERN):
            current["summary"] = line.split("：", 1)[1].strip()

    if current_number is not None and current is not None:
        blocks[current_number] = current
    return blocks


def parse_episode_number(episode_dir: Path) -> int:
    match = EP_PATTERN.match(episode_dir.name)
    if not match:
        fail(f"无法从目录名识别集编号: {episode_dir}")
    return int(match.group(1))


def find_last_clip(prev_episode_dir: Path) -> Path:
    candidates: list[tuple[int, Path]] = []
    for child in prev_episode_dir.iterdir():
        if not child.is_file():
            continue
        match = CLIP_PATTERN.match(child.name)
        if match:
            candidates.append((int(match.group(1)), child))
    if not candidates:
        fail(f"未找到视频 clip: {prev_episode_dir}")
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def find_clip_by_number(episode_dir: Path, clip_number: int) -> Path:
    for child in episode_dir.iterdir():
        if not child.is_file():
            continue
        match = CLIP_PATTERN.match(child.name)
        if match and int(match.group(1)) == clip_number:
            return child
    fail(f"未找到视频 clip: {episode_dir}/视频_Clip{clip_number:05d}.mp4")


def format_clip_label(clip_number: int) -> str:
    return f"Clip{clip_number:05d}"


def check_binary(name: str) -> None:
    result = subprocess.run(["which", name], capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"未找到可执行命令: {name}")


def extract_last_frame(video_path: Path, output_path: Path) -> None:
    check_binary("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=output_path.parent, suffix=".png")
    os.close(fd)
    try:
        # Decode to EOF; -frames:v 1 would retain the FIRST frame after seeking.
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video_path),
                        "-map", "0:v:0", "-an", "-fps_mode", "passthrough",
                        "-update", "1", tmp], check=True, capture_output=True)
        if Path(tmp).stat().st_size == 0:
            fail("Video contains no decodable frames")
        os.replace(tmp, output_path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def project_path(root: Path, relative: str) -> Path:
    path = (root / ensure_relative(relative, "manifest path")).resolve()
    if not path.is_relative_to(root):
        fail("Path escapes project root")
    return path


def handle_manifest(args):
    root = Path(args.project_root).resolve()
    clips = json.loads(project_path(root, args.manifest).read_text(encoding="utf-8"))
    ids = [c["id"] for c in clips]
    if len(ids) != len(set(ids)) or args.clip_id not in ids:
        fail("Duplicate IDs or missing target clip")
    current = clips[ids.index(args.clip_id)]
    project_path(root, current["video"])
    source_id = current.get("continue_from")
    report = {"clip": args.clip_id, "continue_from": source_id, "ending_frame": None}
    if source_id:
        if source_id not in ids or ids.index(source_id) >= ids.index(args.clip_id):
            fail("Continuity source must precede target")
        source = clips[ids.index(source_id)]
        video = project_path(root, source["video"])
        frame = video.with_name(video.stem + "-尾帧.png")
        extract_last_frame(video, frame)
        report.update(ending_frame=frame.relative_to(root).as_posix(),
                      source_video=source["video"],
                      source_sha256=hashlib.sha256(video.read_bytes()).hexdigest())
    target = project_path(root, current["video"])
    target.parent.mkdir(parents=True, exist_ok=True)
    path = target.with_name(target.stem + "-首帧承接信息.json")
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    print(json.dumps(report, ensure_ascii=False))


def write_report(
    report_path: Path,
    should_continue: bool,
    current_episode_rel: Path,
    prev_episode_rel: Path | None,
    last_clip_rel: Path | None,
    frame_rel: Path | None,
    summary: str,
) -> None:
    lines = [
        f"是否承接上一个故事尾帧：{'是' if should_continue else '否'}",
        f"当前集：{current_episode_rel.as_posix()}",
        f"上一集：{prev_episode_rel.as_posix() if prev_episode_rel else '不适用'}",
        f"上一集最后一个视频：{last_clip_rel.as_posix() if last_clip_rel else '不适用'}",
        f"尾帧图片：{frame_rel.as_posix() if frame_rel else '不适用'}",
        f"延续画面简述：{summary if should_continue else '不适用'}",
        "建议用途：作为本故事首个 clip 的起始参考图"
        if should_continue
        else "建议用途：本故事首个 clip 直接使用常规参考图方案",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_clip_report(
    report_path: Path,
    should_continue: bool,
    current_episode_rel: Path,
    current_clip: int,
    prev_clip_video_rel: Path | None,
    frame_rel: Path | None,
    reason: str,
) -> None:
    prev_clip = current_clip - 1
    lines = [
        f"是否延续上一个clip画面：{'是' if should_continue else '否'}",
        f"当前集：{current_episode_rel.as_posix()}",
        f"当前clip：{format_clip_label(current_clip)}",
        f"上一个clip：{format_clip_label(prev_clip) if current_clip > 1 else '不适用'}",
        f"上一个clip视频：{prev_clip_video_rel.as_posix() if prev_clip_video_rel else '不适用'}",
        f"尾帧图片：{frame_rel.as_posix() if frame_rel else '不适用'}",
        f"延续依据：{reason if should_continue else '不适用'}",
        "建议用途：作为当前 clip 的起始参考图" if should_continue else "建议用途：当前 clip 直接使用常规参考图方案",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def handle_clip_mode(args: argparse.Namespace, project_root: Path, episode_dir: Path, episode_dir_rel: Path) -> None:
    current_clip = args.clip
    if current_clip is None or current_clip < 1:
        fail("--clip 必须是大于等于 1 的整数")

    storyboard_rel = ensure_relative(
        args.storyboard if args.storyboard else (episode_dir_rel / "文字分镜.txt").as_posix(),
        "--storyboard",
    )
    storyboard_path = project_path(project_root, str(storyboard_rel))
    if not storyboard_path.exists():
        fail(f"文字分镜文件不存在: {storyboard_rel}")

    report_path = episode_dir / f"{format_clip_label(current_clip)}-首帧承接信息.txt"
    blocks = read_storyboard_blocks(storyboard_path)
    current_block = blocks.get(current_clip)
    if current_block is None:
        fail(f"文字分镜中不存在 {format_clip_label(current_clip)}")

    if current_clip == 1:
        write_clip_report(
            report_path=report_path,
            should_continue=False,
            current_episode_rel=episode_dir_rel,
            current_clip=current_clip,
            prev_clip_video_rel=None,
            frame_rel=None,
            reason="不适用",
        )
        print(report_path.relative_to(project_root).as_posix())
        return

    if current_block.get("continue") not in ("是", "否"):
        fail("Missing or invalid continuity field; expected 是/否")
    should_continue = current_block.get("continue") == "是"
    summary = current_block.get("summary", "").strip() or "文字分镜中该 clip 标记为延续上一个clip画面"

    prev_clip_video = None
    frame_path = episode_dir / f"{format_clip_label(current_clip - 1)}-尾帧.png"

    if not should_continue:
        write_clip_report(
            report_path=report_path,
            should_continue=False,
            current_episode_rel=episode_dir_rel,
            current_clip=current_clip,
            prev_clip_video_rel=None,
            frame_rel=None,
            reason="不适用",
        )
        print(report_path.relative_to(project_root).as_posix())
        return

    prev_clip_video = find_clip_by_number(episode_dir, current_clip - 1)
    extract_last_frame(prev_clip_video, frame_path)
    write_clip_report(
        report_path=report_path,
        should_continue=True,
        current_episode_rel=episode_dir_rel,
        current_clip=current_clip,
        prev_clip_video_rel=prev_clip_video.relative_to(project_root),
        frame_rel=frame_path.relative_to(project_root),
        reason=summary,
    )
    print(report_path.relative_to(project_root).as_posix())


def main() -> None:
    args = parse_args()
    if args.manifest:
        if not args.clip_id: fail("--manifest requires --clip-id")
        handle_manifest(args)
        return
    if not args.episode_dir: fail("Legacy mode requires --episode-dir")
    for value in (args.ending_frame_name, args.report_name):
        if Path(value).name != value or value in (".", ".."):
            fail("Output name must be a filename, not a path")
    project_root_rel = Path(args.project_root)
    episode_dir_rel = ensure_relative(args.episode_dir, "--episode-dir")
    outline_rel = ensure_relative(args.outline, "--outline")

    project_root = project_root_rel.resolve()
    episode_dir = project_path(project_root, str(episode_dir_rel))
    outline_path = project_path(project_root, str(outline_rel))

    if not episode_dir.exists():
        fail(f"当前集目录不存在: {episode_dir_rel}")

    if args.clip is not None:
        handle_clip_mode(args, project_root, episode_dir, episode_dir_rel)
        return

    if not outline_path.exists():
        fail(f"大纲文件不存在: {outline_rel}")

    episode_number = parse_episode_number(episode_dir)
    if episode_number <= 1:
        report_path = episode_dir / args.report_name
        write_report(
            report_path=report_path,
            should_continue=False,
            current_episode_rel=episode_dir_rel,
            prev_episode_rel=None,
            last_clip_rel=None,
            frame_rel=None,
            summary="不适用",
        )
        print(report_path.relative_to(project_root).as_posix())
        return

    blocks = read_outline_blocks(outline_path)
    story_index = episode_number - 1
    if story_index >= len(blocks):
        fail(f"大纲故事数量不足，无法匹配 {episode_dir_rel}")

    current_block = blocks[story_index]
    if current_block.get("continue") not in ("是", "否"):
        fail("Missing or invalid continuity field; expected 是/否")
    should_continue = current_block.get("continue") == "是"
    summary = current_block.get("summary", "不适用")

    prev_episode_name = f"EP{episode_number - 1:03d}"
    prev_episode_rel = episode_dir_rel.parent / prev_episode_name
    report_path = episode_dir / args.report_name

    if not should_continue:
        write_report(
            report_path=report_path,
            should_continue=False,
            current_episode_rel=episode_dir_rel,
            prev_episode_rel=prev_episode_rel,
            last_clip_rel=None,
            frame_rel=None,
            summary="不适用",
        )
        print(report_path.relative_to(project_root).as_posix())
        return

    prev_episode_dir = project_path(project_root, str(prev_episode_rel))
    if not prev_episode_dir.exists():
        fail(f"字段要求承接上一个故事尾帧，但上一集目录不存在: {prev_episode_rel}")

    last_clip = find_last_clip(prev_episode_dir)
    frame_path = prev_episode_dir / args.ending_frame_name
    extract_last_frame(last_clip, frame_path)

    write_report(
        report_path=report_path,
        should_continue=True,
        current_episode_rel=episode_dir_rel,
        prev_episode_rel=prev_episode_rel,
        last_clip_rel=last_clip.relative_to(project_root),
        frame_rel=frame_path.relative_to(project_root),
        summary=summary,
    )
    print(report_path.relative_to(project_root).as_posix())


if __name__ == "__main__":
    main()
