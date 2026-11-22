#!/usr/bin/env python3
"""语音识别对齐（剪辑/摄影共用）：字幕时间轴、台词质检（乱说话检查）、截音色模板。

依据：设计文档 §8.5.1–§8.5.2、数据规范-v2.md §4（voices.json）、FunASR 冒烟结论（2026-11-04：
paraformer-zh + fsmn-vad，识别准确、VAD 结尾会拖长——字级时间戳为主，VAD 边界只留余量）。

  align    识别一个视频/音频，输出 JSON：字级时间戳、句子段落、人声区间（子进程调 FunASR venv）
  check    台词质检：开头 1 秒有没有人声（乱说话）、台词文本之外有没有多出人声
  cut-voice 截音色模板：首次显式给 start/end/out 后再登记，或读 voices.json 的范围；单声道 16k，不覆盖已有文件
  subtitle 生成 SRT：文字用剧本台词（配音版），时间用识别结果

模型环境：D:\\ai剧集制作\\环境\\asr\\（venv），模型在 D:\\ai剧集制作\\环境\\models\\。
识别是本机 CPU 推理，不联网、不花积分。
"""
import argparse, json, math, os, re, shutil, subprocess, sys, tempfile, time, unicodedata, urllib.error, urllib.request
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")

_WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_ASR_PY = (_WORKSPACE_ROOT / "环境/asr/Scripts/python.exe") if os.name == "nt" else (_WORKSPACE_ROOT / "环境/asr/bin/python")
_DEFAULT_MODEL_DIR = str(_WORKSPACE_ROOT / "环境/models")
ASR_PY = Path(os.environ.get("ASR_PY", str(_DEFAULT_ASR_PY)))
MODEL_DIR = os.environ.get("ASR_MODELS", _DEFAULT_MODEL_DIR)
DEFAULT_REMOTE_ASR_URL = "http://10.197.123.1:8000"
DEFAULT_REMOTE_TOKEN_FILE = Path.home() / "Downloads/token.txt"
CJK = re.compile(r"[\u4e00-\u9fff]")


def fail(msg, code=2):
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def extract_audio(video, out_wav):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                        str(out_wav)], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        fail("抽音轨失败: " + r.stderr[:300])


# ------------------------------------------------------------------ 识别（子进程脚本，在 FunASR venv 里跑）

RECOGNIZE_SRC = r'''
import json, sys, time
for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8")
import os
os.environ.setdefault("MODELSCOPE_CACHE", sys.argv[3])
from funasr import AutoModel
wav, out, model_dir = sys.argv[1], sys.argv[2], sys.argv[3]
t0 = time.time()
model = os.environ.get("ASR_LOCAL_MODEL", "paraformer-zh")
vad_model = os.environ.get("ASR_LOCAL_VAD", "fsmn-vad")
m = AutoModel(model=model, vad_model=vad_model, disable_update=True, log_level="ERROR")
r = m.generate(input=wav, sentence_timestamp=True)[0]
ts = r.get("timestamp") or []
vad = AutoModel(model=vad_model, disable_update=True, log_level="ERROR")
segs = vad.generate(input=wav)[0]["value"]
json.dump({"text": r.get("text", ""), "timestamp": ts,
           "speech_segments": [[round(a/1000, 2), round(b/1000, 2)] for a, b in segs],
           "load_seconds": round(time.time()-t0, 1)},
          open(out, "w", encoding="utf-8"), ensure_ascii=False)
'''


def _remote_asr_token():
    """读取远端 ZeroTier ASR 令牌（环境变量 -> 令牌文件 -> macOS 钥匙串），不打印或写入日志。"""
    token = os.environ.get("ASR_REMOTE_TOKEN", "").strip()
    if not token:
        tf_env = os.environ.get("ASR_REMOTE_TOKEN_FILE", "").strip()
        tf = Path(tf_env) if tf_env else DEFAULT_REMOTE_TOKEN_FILE
        if tf.is_file():
            try:
                token = tf.read_text(encoding="utf-8-sig").strip()
            except (OSError, UnicodeError):
                token = ""
    if not token and sys.platform == "darwin" and shutil.which("security"):
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "ZEROTIER_VIDEO_API_TOKEN", "-w"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            token = r.stdout.strip()
    if token and not re.fullmatch(r"[A-Za-z0-9._~+/\-]+=*", token):
        fail("远端 ASR 令牌格式无效")
    return token


def _resolve_remote_asr():
    """返回 (base_url, token) 或 (None, None)。显式设置 ASR_PY 时保持本机路径行为。"""
    if os.environ.get("ASR_REMOTE_DISABLE") == "1":
        return None, None
    explicit_url = os.environ.get("ASR_REMOTE_URL", "").strip().rstrip("/")
    if explicit_url:
        token = _remote_asr_token()
        if not token:
            fail("已设置 ASR_REMOTE_URL，但未找到远端令牌（ASR_REMOTE_TOKEN / ASR_REMOTE_TOKEN_FILE / ~/Downloads/token.txt / macOS 钥匙串 ZEROTIER_VIDEO_API_TOKEN）")
        return explicit_url, token
    if os.environ.get("ASR_PY") or ASR_PY.exists():
        return None, None
    token = _remote_asr_token()
    if token:
        return DEFAULT_REMOTE_ASR_URL, token
    return None, None


def _recognize_remote(media, base_url, token):
    """调用 ZeroTier local-video-analysis 的 POST /v1/video/analyze（绕过代理、不自动重试）。"""
    src_path = Path(media)
    if not src_path.is_file():
        fail(f"找不到媒体文件：{src_path}")
    with tempfile.TemporaryDirectory() as td:
        mp4_path = src_path
        if src_path.suffix.lower() != ".mp4":
            mp4_path = Path(td) / "input.mp4"
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=1",
                 "-i", str(src_path), "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-ar", "16000", "-ac", "1", str(mp4_path)],
                capture_output=True, text=True, encoding="utf-8",
            )
            if r.returncode or not mp4_path.is_file():
                fail("封装非 MP4 媒体失败: " + (r.stderr or "")[:300])
        payload = mp4_path.read_bytes()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        f"{base_url}/v1/video/analyze",
        data=payload,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "video/mp4",
        },
    )
    try:
        with opener.open(req, timeout=600) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        fail(f"远端 ASR 返回 HTTP {e.code}: {body}")
    except Exception as e:
        fail(f"远端 ASR 请求失败（{base_url}）：{e}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"远端 ASR 返回非 JSON：{e}")
    return {
        "text": data.get("transcript", ""),
        "timestamp": data.get("timestamps_ms") or [],
        "speech_segments": data.get("speech_segments_seconds") or [],
        "load_seconds": data.get("processing_seconds"),
        "backend": f"remote:{base_url}",
    }


def recognize(media):
    """跑语音识别（优先测试桩 / 远端 ZeroTier API / 本机 FunASR），返回 {text, timestamp, speech_segments}。"""
    fake = os.environ.get("ASR_FAKE_RESULT")                        # 测试桩：固定识别结果，生产不设这个变量
    if fake:
        return json.loads(Path(fake).read_text(encoding="utf-8"))
    remote_url, remote_token = _resolve_remote_asr()
    if remote_url:
        return _recognize_remote(media, remote_url, remote_token)
    if not ASR_PY.exists():
        fail(f"找不到 FunASR 环境：{ASR_PY}（OVERRIDES §6；或设 ASR_PY / ASR_REMOTE_URL 环境变量）")
    local_models = [os.environ.get(k) for k in ("ASR_LOCAL_MODEL", "ASR_LOCAL_VAD")]
    if any(local_models) and not all(p and Path(p).is_dir() for p in local_models):
        fail("ASR_LOCAL_MODEL / ASR_LOCAL_VAD 必须同时指向已有的本地模型目录")
    with tempfile.TemporaryDirectory() as d:
        src, out = Path(d) / "recognize.py", Path(d) / "result.json"
        src.write_text(RECOGNIZE_SRC, encoding="utf-8", newline="\n")
        wav = Path(d) / "audio.wav"
        if Path(media).suffix.lower() in (".wav", ".mp3", ".flac", ".m4a"):
            wav = Path(media)
        else:
            extract_audio(media, wav)
        r = subprocess.run([str(ASR_PY), "-I", str(src), str(wav), str(out), MODEL_DIR],
                           capture_output=True, text=True, encoding="utf-8", timeout=1800)
        if r.returncode or not out.exists():
            fail("FunASR 识别失败: " + (r.stderr or r.stdout)[:400])
        return json.loads(out.read_text(encoding="utf-8"))


def normalized(text):
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum())


def character_times(ts, text):
    """Accept character or English-word timestamps; never index by punctuation."""
    chars = normalized(text)
    for i, pair in enumerate(ts):
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in pair)
                or pair[0] < 0 or pair[1] <= pair[0]
                or (i and (pair[0] < ts[i-1][0] or pair[1] < ts[i-1][1]))):
            fail("识别时间戳非法或倒序，不能生成字幕")
    if len(chars) == len(ts):
        return chars, ts
    tokens = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*", text)
    if len(tokens) == len(ts) and "".join(normalized(w) for w in tokens) == chars:
        # Repeat word bounds, rather than inventing precise within-word times.
        return chars, [pair for word, pair in zip(tokens, ts) for _ in normalized(word)]
    fail(f"识别字符/词数与时间戳数量不符：{len(chars)} 字符 / {len(ts)} 时间戳；须人工核对")


def sentences_from_timestamp(ts, text):
    chars, times = character_times(ts, text)
    out, cursor = [], 0
    for piece in re.split(r"[。！？!?；;，,、…]+", text):
        plain = normalized(piece)
        if not plain:
            continue
        end = cursor + len(plain)
        out.append({"text": "".join(ch for ch in piece if ch.isalnum()),
                    "start": times[cursor][0] / 1000, "end": times[end-1][1] / 1000})
        cursor = end
    return out


def align_script(result, script):
    chars, times = character_times(result["timestamp"], result["text"])
    pieces = []
    for line in script.splitlines():
        pieces.extend(p.strip() for p in re.split(r"(?<=[。！？!?])|(?<=\.)\s+", line.strip()) if normalized(p))
    target = "".join(normalized(p) for p in pieces)
    if not target or target != chars:
        fail("剧本与识别文字不匹配，停止输出字幕；请核对漏字/多字/错字，不按序号硬配")
    out, cursor = [], 0
    for piece in pieces:
        end = cursor + len(normalized(piece))
        start_time, end_time = times[cursor][0] / 1000, times[end-1][1] / 1000
        if out and start_time < out[-1]["end"] - .001:
            fail("剧本断句切进同一个词时间窗，须调整断句后重试")
        out.append({"text": piece, "start": start_time, "end": end_time})
        cursor = end
    return out


def cmd_align(a):
    if a.out and Path(a.out).exists():
        fail('对齐输出已存在，不覆盖已有文件')
    res = recognize(a.media)
    res["sentences"] = sentences_from_timestamp(res["timestamp"], res["text"])
    res["media"] = str(a.media)
    if a.out:
        Path(a.out).parent.mkdir(parents=True,exist_ok=True)
        with Path(a.out).open('x',encoding='utf-8',newline='\n') as output:
            output.write(json.dumps(res,ensure_ascii=False,indent=1))
        print(json.dumps({"written": str(a.out), "chars": len(res["timestamp"]),
                          "sentences": len(res["sentences"]),
                          "speech_segments": res["speech_segments"]}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ 台词质检（乱说话检查）

def cmd_check(a):
    res = recognize(a.media)
    problems = []
    segs = res["speech_segments"]
    # 检查 1（乱说话开头）：第一个字落在片段最开头 0.5s 内多半是冒出来的乱语（§8.5.2）。
    # 台词正常从分镜写的位置开始，不会贴着 0 秒。
    ts = res.get("timestamp") or []
    if ts:
        first_char = ts[0][0] / 1000
        if first_char < 0.5:
            problems.append(f"第一个字出现在 {first_char:.2f}s（片段最开头 0.5s 内）：疑似乱说话开头，"
                            f"补救见 H3 技能 SKILL.md（先补写法重新生成，再去音色参考，最后交给剪辑换环境声）")
    elif segs and segs[0][0] < 0.5:
        problems.append(f"片段开头 0.5s 内就有人声（{segs[0][0]:.2f}s）：疑似乱说话开头")
    # 检查 2：识别出的文字里，台词之外的字符（把各句台词按标点拆开比对）
    lines = []
    for src in a.dialogue or []:
        for piece in re.split(r"[。！？；，、…\s]+", src):
            piece = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", piece)
            if piece:
                lines.append(piece)
    recognized = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", res["text"])
    leftover = recognized
    for piece in sorted(lines, key=len, reverse=True):
        leftover = leftover.replace(piece, "", 1)
    if CJK.search(leftover) and len(leftover) >= 4:
        problems.append(f"识别出台词以外的人声约 {len(leftover)} 字：…{leftover[:40]}…（台词以外乱说话）")
    print(json.dumps({"media": str(a.media), "recognized": res["text"],
                      "speech_segments": segs, "problems": problems,
                      "ok": not problems}, ensure_ascii=False, indent=2))
    if problems:
        raise SystemExit(2)


# ------------------------------------------------------------------ 截音色模板（voices.json §4）

def cmd_cut_voice(a):
    root = Path(a.project).resolve()
    explicit = any(v is not None for v in (a.start, a.end, a.out))
    if explicit:
        if any(v is None for v in (a.start, a.end, a.out)):
            fail("登记前截取必须同时提供 --start / --end / --out")
        v = {"start": a.start, "end": a.end, "file": a.out}
    else:
        voices_path = root / "voices.json"
        if not voices_path.exists():
            fail("voices.json 不存在；首次截取请同时提供 --start / --end / --out，再用 pipeline_state.py 登记")
        voices = json.loads(voices_path.read_text(encoding="utf-8"))
        hits = [v for v in voices if v.get("character") == a.character]
        if len(hits) != 1:
            fail(f"voices.json 里 {a.character} 有 {len(hits)} 条（应正好 1 条）")
        v = hits[0]
        if v.get("status") not in ("generated", "approved"):
            fail(f"{a.character} 的状态是 {v.get('status')}，只有 generated/approved 才截取")
    start, end = float(v["start"]), float(v["end"])
    if not all(math.isfinite(x) for x in (start, end)) or start < 0 or end <= start:
        fail("截取范围必须满足 0 <= start < end，且为有限数字")
    segments = v.get('source_segments', [{'start': start, 'end': end}])
    if not isinstance(segments, list) or not 1 <= len(segments) <= 8:
        fail('source_segments 必须包含1–8个同源范围')
    previous = start
    for segment in segments:
        if (not isinstance(segment, dict) or set(segment) != {'start', 'end'} or
            any(type(segment[k]) not in (int, float) or not math.isfinite(segment[k]) for k in ('start', 'end')) or
            not previous <= segment['start'] < segment['end'] <= end):
            fail('source_segments 顺序、边界或时间无效')
        previous = segment['end']
    expected_duration = sum(s['end'] - s['start'] for s in segments)
    rel = Path(v["file"])
    out = (root / rel).resolve()
    if rel.is_absolute() or not out.is_relative_to(root) or out.suffix.lower() != ".wav":
        fail("音色输出必须是作品内的相对 WAV 路径")
    if out.exists():
        fail("音色输出已存在，请指定新路径，不覆盖已有文件")
    source = Path(a.source).resolve()
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(source)],
                           capture_output=True, text=True, encoding="utf-8")
    try:
        source_duration = float(probe.stdout)
    except ValueError:
        fail("无法读取音色来源时长")
    if probe.returncode or not math.isfinite(source_duration) or end > source_duration + .01:
        fail("截取范围超过来源时长")
    if not 3.0 <= expected_duration <= 5.0:
        print(f"WARNING: 模板时长 {expected_duration:.1f}s 不在 H3 建议 3–5s 内", file=sys.stderr)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as scratch:
        staged = Path(scratch) / "voice.wav"
        # 先验证临时文件，再以不覆盖方式发布；不修改 voices.json。
        filters = ';'.join(f"[0:a]atrim=start={s['start']}:end={s['end']},asetpts=PTS-STARTPTS[a{i}]" for i,s in enumerate(segments))
        filters += ';' + ''.join(f'[a{i}]' for i in range(len(segments))) + f'concat=n={len(segments)}:v=0:a=1[out]'
        r = subprocess.run(["ffmpeg", "-v", "error", "-n", "-i", str(source),
                            "-filter_complex", filters, "-map", "[out]", "-ac", "1", "-ar", "16000", str(staged)],
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode:
            fail("截取失败: " + r.stderr[:300])
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(staged)], capture_output=True, text=True, encoding="utf-8")
        dur = float(probe.stdout) if probe.returncode == 0 else 0.0
        if abs(dur - expected_duration) > 0.15:
            fail(f"截出的模板 {dur:.2f}s 和预期的 {expected_duration:.2f}s 差得多；核对 source 和范围")
        try:
            os.link(staged, out)
        except FileExistsError:
            fail("音色输出已存在，不覆盖并发写入的文件")
    print(json.dumps({"character": a.character, "written": str(out), "duration": round(dur, 2),
                      "expected": round(expected_duration, 2), "source_segments": segments,
                      "registration_required": explicit}, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ SRT（文字用剧本，时间用识别）

def fmt_srt(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def cmd_subtitle(a):
    """Script supplies exact text; ASR supplies validated character/word bounds."""
    res = recognize(a.media)
    if a.script:
        sents = align_script(res, Path(a.script).read_text(encoding="utf-8"))
    else:
        sents = sentences_from_timestamp(res["timestamp"], res["text"])
    out = Path(a.out)
    if out.exists():
        fail("字幕输出已存在，请指定新路径，不覆盖已有文件")
    out.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{i}\n{fmt_srt(s['start'])} --> {fmt_srt(s['end'])}\n{s['text']}\n\n"
                   for i, s in enumerate(sents, 1))
    # Exclusive creation also protects source/script files and concurrent writers.
    with out.open("x", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(json.dumps({"written": str(out), "lines": len(sents),
                      "text_source": "script" if a.script else "recognizer",
                      "test_stub": bool(os.environ.get("ASR_FAKE_RESULT"))}, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description="语音识别对齐（FunASR，本机推理）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("align", help="识别并输出时间戳 JSON")
    sp.add_argument("media")
    sp.add_argument("--out", help="结果写到这里（JSON）")
    sp.set_defaults(func=cmd_align)

    sp = sub.add_parser("check", help="乱说话质检：开头人声 + 台词外人声")
    sp.add_argument("media")
    sp.add_argument("--dialogue", action="append", help="这句的台词原文（可多次）")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("cut-voice", help="按 voices.json 截音色模板")
    sp.add_argument("--project", required=True)
    sp.add_argument("--character", required=True, help="角色编号，如 C00001")
    sp.add_argument("--source", required=True, help="定音片段的视频/音频文件")
    sp.add_argument("--start", type=float, help="登记前截取起点（须与 --end / --out 同时使用）")
    sp.add_argument("--end", type=float, help="登记前截取终点")
    sp.add_argument("--out", help="登记前截取的作品内相对 WAV 路径；不改 voices.json")
    sp.set_defaults(func=cmd_cut_voice)

    sp = sub.add_parser("subtitle", help="生成 SRT（文字用剧本，时间用识别）")
    sp.add_argument("media")
    sp.add_argument("--script", help="剧本台词文件（每行一句或整段）")
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_subtitle)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
