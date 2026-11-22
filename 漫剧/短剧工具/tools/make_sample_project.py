#!/usr/bin/env python3
"""生成一个符合数据规范 v2 的样例作品（长篇模式、3D 国漫、9:16），全部是假素材，不联网、不花钱。

样例停在"参考图生成中、试生成之前"：能通过 --stage plan，不能通过 --stage assets（有资产还没通过）。
用于演示数据结构，也给 pipeline_state.py / validate_project.py 的测试当底稿。

用法：make_sample_project.py <目标目录>   （目标目录必须不存在或为空）
"""
import json, struct, sys, wave, zlib
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # Windows 中文系统下 -I 会忽略 PYTHONUTF8，强制输出 UTF-8
    _s.reconfigure(encoding="utf-8")

PROJECT = {
    "schema_version": 2,
    "title": "样例作品",
    "mode": "series",
    "genre": "3d-guoman",
    "ratio": "9:16",
    "character_sheet_ratio": "16:9",
    "target_duration": 24,
    "max_revisions": 2,
    "source": {"path": "原文/样例.txt", "sha256": None, "range": "第1章"},
    "image": {"provider": "nexorai", "model": "grok-imagine-image-quality"},
    "video": {"provider": "minimax-h3", "model": "MiniMax-H3", "default_resolution": "768P", "draft_resolution": "768P",
              "final_resolution": "768P", "use_context_ir": False, "audio": "generate"},
    "narration": {"provider": "compshare-indextts-2.5", "voice_id": "承泽", "speed": 1.0},
    "post": {"loudness_lufs": -14, "true_peak_db": -1, "bgm_duck_db": 12, "subtitles": "srt", "burn_subtitles": False,
             "aigc_label": {"visible": True, "metadata": True, "text": "本片含 AI 生成内容",
                            "badge": "AI 生成", "badge_position": "top-right"}},
    "publish": False,
}

ASSETS = [
    {"id": "C00001-A", "type": "character", "name": "林舟-基础形态", "detail": "角色/C00001-A-林舟.txt",
     "prompt": "角色/C00001-A-林舟.prompt.txt", "image": "角色/C00001-A-林舟.png",
     "sheet": "角色/C00001-A-林舟.sheet.png", "sheet_prompt": "角色/C00001-A-林舟.sheet.prompt.txt",
     "voice": "A young man in his early twenties, mid-low pitch, clear and steady timbre, measured pace.",
     "status": "generated", "source_asset_id": None, "relation": "independent", "chapter_ranges": [[1, 1]]},
    {"id": "C00002-A", "type": "character", "name": "苏晚-基础形态", "detail": "角色/C00002-A-苏晚.txt",
     "prompt": "角色/C00002-A-苏晚.prompt.txt", "image": "角色/C00002-A-苏晚.png",
     "voice": "A young woman around twenty, bright mid-high pitch, crisp timbre, slightly quick pace.",
     "status": "planned", "source_asset_id": None, "relation": "independent", "chapter_ranges": [[1, 1]]},
    {"id": "S00001", "type": "scene", "name": "雪夜山门", "detail": "场景/S00001-雪夜山门.txt",
     "prompt": "场景/S00001-雪夜山门.prompt.txt", "image": "场景/S00001-雪夜山门.png", "status": "generated"},
    {"id": "P00001", "type": "prop", "name": "青锋剑", "detail": "道具/P00001-青锋剑.txt",
     "prompt": "道具/P00001-青锋剑.prompt.txt", "image": "道具/P00001-青锋剑.png", "status": "planned"},
]

CLIPS = [
    {"id": "EP001/Clip00001", "episode": "EP001", "duration": 8, "assets": ["C00001-A", "S00001"],
     "prompt": "单集制作/EP001/视频_Clip00001.prompt.txt", "video": "单集制作/EP001/视频_Clip00001.mp4",
     "continue_from": None, "status": "planned", "voice_anchor": ["C00001"],
     "shots": [{"start": 0, "end": 4, "visual": "雪夜山门全景，林舟走近"},
               {"start": 4, "end": 8, "visual": "中景，林舟抬头", "dialogue": "林舟：这扇门，我一定要推开。"}],
     "draft": {"video": "单集制作/EP001/试生成/视频_Clip00001.mp4", "status": "planned",
               "task_dir": "tasks/EP001-Clip00001-draft/attempt-001"}},
    {"id": "EP001/Clip00002", "episode": "EP001", "duration": 6, "assets": ["C00001-A", "C00002-A", "S00001"],
     "prompt": "单集制作/EP001/视频_Clip00002.prompt.txt", "video": "单集制作/EP001/视频_Clip00002.mp4",
     "continue_from": "EP001/Clip00001", "continuity_reason": "同一场景连续动作", "status": "planned",
     "voice_anchor": ["C00002"],
     "draft": {"video": "单集制作/EP001/试生成/视频_Clip00002.mp4", "status": "planned"}},
    {"id": "EP001/Clip00003", "episode": "EP001", "duration": 10, "assets": ["S00001", "P00001"],
     "prompt": "单集制作/EP001/视频_Clip00003.prompt.txt", "video": "单集制作/EP001/视频_Clip00003.mp4",
     "continue_from": None, "status": "planned",
     "narration": [{"id": "Clip00003-1", "start": 2, "reserved": 4, "text": "那一夜，山门外的雪下得很大。",
                    "audio": "单集制作/EP001/旁白/Clip00003-1.wav", "narration_duration": 3.6}],
     "draft": {"video": "单集制作/EP001/试生成/视频_Clip00003.mp4", "status": "planned"}},
]

BUDGET = [{"batch": "EP001-试生成", "kind": "video", "unit": "points", "limit": 120, "used": 0},
          {"batch": "EP001-参考图", "kind": "image", "unit": "images", "limit": 12, "used": 0}]

TEXTS = {
    "原文/样例.txt": "第一章 雪夜\n\n那一夜，山门外的雪下得很大。林舟站在门前……（样例原文，仅作演示）\n",
    "大纲.txt": "第1章：林舟雪夜来到山门，遇见守门的苏晚。（样例）\n",
    "读取进度.json": json.dumps({"source": "原文/样例.txt", "read_to": 1, "chapters": [[1, 1]]}, ensure_ascii=False) + "\n",
    "风格素材.md": "# 风格素材（样例）\n\n风格句：3D stylized Chinese xianxia animation, soft rim light, cool snowy palette.\n",
    "角色/C00001-A-林舟.txt": "林舟：二十出头的少年剑客，黑发束起，青色布衣。（样例）\n",
    "角色/C00001-A-林舟.prompt.txt": "（样例）林舟视频参考图提示词\n",
    "角色/C00001-A-林舟.sheet.prompt.txt": "（样例）林舟三视图提示词\n",
    "角色/C00002-A-苏晚.txt": "苏晚：守门弟子，白衣，手持灯笼。（样例）\n",
    "场景/S00001-雪夜山门.txt": "雪夜山门：石阶、朱漆大门、积雪。（样例）\n",
    "场景/S00001-雪夜山门.prompt.txt": "（样例）雪夜山门场景提示词\n",
    "道具/P00001-青锋剑.txt": "青锋剑：剑身细长，青色剑穗。（样例）\n",
    "单集制作/EP001/文字分镜.txt": "EP001 文字分镜（样例）\nClip00001 8 秒 …\nClip00002 6 秒 …\nClip00003 10 秒 …\n",
    "单集制作/EP001/视频_Clip00001.prompt.txt": "(sample) six-part H3 prompt for Clip00001\n",
    "单集制作/EP001/视频_Clip00002.prompt.txt": "(sample) six-part H3 prompt for Clip00002\n",
    "单集制作/EP001/视频_Clip00003.prompt.txt": "(sample) six-part H3 prompt for Clip00003\n",
}


def tiny_png(path, rgb, size=32):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    row = b"\x00" + bytes(rgb) * size
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))


def silent_wav(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))


def build(root):
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"ERROR: 目标目录不为空：{root}")
    for rel, text in TEXTS.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="\n")
    tiny_png(root / "角色/C00001-A-林舟.png", (40, 90, 160))
    tiny_png(root / "角色/C00001-A-林舟.sheet.png", (40, 90, 160))
    tiny_png(root / "场景/S00001-雪夜山门.png", (220, 220, 235))
    (root / "单集制作/EP001/旁白").mkdir(parents=True, exist_ok=True)
    silent_wav(root / "单集制作/EP001/旁白/Clip00003-1.wav", 3.6)
    for d in ("单集制作/EP001/试生成", "单集制作/EP001/尾帧", "音色", "tasks", "reports", "logs"):
        (root / d).mkdir(parents=True, exist_ok=True)
    for name, data in (("project.json", PROJECT), ("assets.json", ASSETS), ("clips.json", CLIPS),
                       ("approvals/budget.json", BUDGET)):
        f = root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return root


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print("样例作品已生成：", build(sys.argv[1]))
