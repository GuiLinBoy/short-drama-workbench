#!/usr/bin/env python3
"""Local final render: validated timeline -> mix -> normalize -> captions/labels -> verify -> publish.

All generation/recognition services are outside this tool. Dialogue timing comes
from --srt or --speech-timeline, never guessed from background sound. Existing
outputs are preserved. A render is complete only with its matching validation
report, written last. Human visual review is still required.
"""
import argparse
import hashlib
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generation_runtime as runtime
from audio.delivery_checks import publication_context

for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8")

def _default_font(env_name, win_name, mac_candidates):
    if os.environ.get(env_name):
        return Path(os.environ[env_name])
    if os.name == "nt":
        return Path(r"D:\fonts") / win_name
    for candidate in mac_candidates:
        if candidate.exists():
            return candidate
    return mac_candidates[0]


FONT = _default_font(
    "SUBTITLE_FONT",
    "SourceHanSansSC-Regular.otf",
    [
        Path.home() / "ManjuFonts/SourceHanSansSC-Regular.otf",
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
    ],
)
FONT_BOLD = _default_font(
    "SUBTITLE_FONT_BOLD",
    "SourceHanSansSC-Bold.otf",
    [
        Path.home() / "ManjuFonts/SourceHanSansSC-Bold.otf",
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    ],
)
RATIO_WH = {"9:16": (720,1280), "16:9": (1280,720), "1:1": (1080,1080),
            "4:3": (960,720), "3:4": (720,960), "21:9": (1680,720)}
DEFAULT_REMOTE_RENDER_URL = "http://10.197.123.1:8000"
DEFAULT_REMOTE_TOKEN_FILE = Path.home() / "Downloads/token.txt"
_FFMPEG_FILTERS_CACHE = None


def _local_ffmpeg_filters():
    global _FFMPEG_FILTERS_CACHE
    if _FFMPEG_FILTERS_CACHE is None:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, encoding="utf-8")
        found = set()
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].strip(".") != "":
                found.add(parts[1])
        _FFMPEG_FILTERS_CACHE = found
    return _FFMPEG_FILTERS_CACHE


def _remote_render_token():
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
        fail("远端渲染令牌格式无效")
    return token


def _resolve_remote_render():
    if os.environ.get("ASR_REMOTE_DISABLE") == "1":
        return None, None
    url = os.environ.get("ASR_REMOTE_URL", "").strip().rstrip("/") or DEFAULT_REMOTE_RENDER_URL
    token = _remote_render_token()
    if not token:
        return None, None
    return url, token


def _build_multipart(fields, files):
    boundary = "----ManjuRender" + uuid.uuid4().hex
    chunks = []
    for name, value in fields:
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
        )
    for name, filename, content_type, data in files:
        chunks.append(
            (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
            + data
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _render_remote_mp4(base_url, token, video_path, srt_path, overlay_text, overlay_end, out_path):
    fields = []
    files = [("video", video_path.name, "video/mp4", video_path.read_bytes())]
    if srt_path is not None:
        files.append(("srt", srt_path.name, "application/x-subrip", srt_path.read_bytes()))
        fields.append(("subtitle_basis", "output"))
    if overlay_text:
        fields.extend([
            ("overlay", overlay_text),
            ("overlay_position", "top"),
            ("overlay_start", "0"),
            ("overlay_end", f"{overlay_end:.3f}"),
        ])
    body, content_type = _build_multipart(fields, files)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        f"{base_url}/v1/video/render",
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": content_type,
        },
    )
    try:
        with opener.open(req, timeout=600) as resp:
            out_path.write_bytes(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "replace")[:200]
        fail(f"远端渲染返回 HTTP {e.code}: {err_body}")
    except Exception as e:
        fail(f"远端渲染请求失败（{base_url}）：{e}")


def fail(message, code=2):
    print("ERROR: " + str(message), file=sys.stderr)
    raise SystemExit(code)


def run(argv):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        fail("FFmpeg 失败: " + (result.stderr or str(result.returncode))[-600:])
    return result


def probe(path):
    return json.loads(run(["ffprobe","-v","error","-show_streams","-show_format","-of","json",path]).stdout)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def contained(root, name):
    root = root.resolve()
    p = (root / name).resolve()
    if not p.is_relative_to(root):
        fail(f"路径必须在作品目录内：{name}")
    return p


def ff_escape(text):
    return str(text).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def number(value, label, minimum=0, maximum=float("inf")):
    if isinstance(value, bool):
        fail(label + " 必须为数值")
    try:
        value = float(value)
    except (ValueError, TypeError):
        fail(label + " 必须为数值")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        fail(f"{label} 超出范围或不是有限数值")
    return value


def output_dimensions(ratio):
    if ratio not in RATIO_WH:
        fail(f"不支持或未明确的画幅：{ratio}")
    return RATIO_WH[ratio]


def should_burn(project, cli):
    configured = (project.get("post") or {}).get("burn_subtitles")
    value = cli if cli is not None else configured if configured is not None else project.get("mode") == "short"
    if not isinstance(value, bool):
        fail("burn_subtitles 必须为布尔值")
    return value


def check_publication(project):
    label = (project.get("post") or {}).get("aigc_label") or {}
    if project.get("publish"):
        if label.get("visible") is not True or label.get("metadata") is not True:
            fail("公开发布成片必须同时开启可见 AI 标识与元数据标识")
        if not label.get("text", "本片含 AI 生成内容").strip() or not label.get("badge", "AI 生成").strip():
            fail("公开发布 AI 标识文字不能为空")


def effective_ai_label(project, post, hide_visible=False):
    label = dict(post.get("aigc_label") or {})
    if hide_visible:
        if project.get("publish"):
            fail("无可见 AI 标识选项仅限本地预览，不能用于公开发布")
        label["visible"] = False
    return label


def srt_sec(text):
    match = re.fullmatch(r"(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})", text.strip())
    if not match:
        fail("SRT 时间格式错误：" + text)
    h,m,s,ms = map(int, match.groups())
    return h*3600+m*60+s+ms/1000


def parse_srt(text):
    if not text.strip():
        return []
    result = []
    for block in re.split(r"\n\s*\n", text.lstrip("\ufeff").strip().replace("\r\n","\n")):
        lines = block.splitlines()
        if len(lines)<3 or not lines[0].strip().isdigit() or lines[1].count("-->")!=1:
            fail("SRT 存在损坏的字幕块，不静默丢弃")
        a,b = lines[1].split("-->")
        result.append({"start":srt_sec(a), "end":srt_sec(b), "text":"\n".join(lines[2:])})
    return result


def validate_cues(entries, total):
    for cue in entries:
        start = number(cue.get("start"), "字幕开始")
        end = number(cue.get("end"), "字幕结束")
        if start>=end or end>total+.001 or not isinstance(cue.get("text"),str) or not cue["text"].strip():
            fail("字幕时间越界、时长非正或文本为空")


def wrap_caption(text, width=32, max_lines=3):
    lines, line, used = [], "", 0
    for ch in text:
        cost = 2 if unicodedata.east_asian_width(ch) in ("W","F") else 1
        if ch=="\n" or used+cost>width:
            lines.append(line)
            line, used = "",0
        if ch!="\n":
            line+=ch
            used+=cost
    if line:
        lines.append(line)
    if len(lines)>max_lines:
        fail("单条字幕超过三行，请先拆分剧本与时间轴，不截断字幕")
    return "\n".join(lines)


def render_srt(entries):
    def fmt(t):
        ms = round(t*1000)
        return f"{ms//3600000:02d}:{ms%3600000//60000:02d}:{ms%60000//1000:02d},{ms%1000:03d}"
    return "".join(f"{i}\n{fmt(e['start'])} --> {fmt(e['end'])}\n{wrap_caption(e['text'])}\n\n"
                   for i,e in enumerate(sorted(entries,key=lambda x:x["start"]),1))


def merge_windows(windows):
    merged=[]
    normalized=[]
    for a,b in windows:
        a,b=number(a,"人声开始"),number(b,"人声结束")
        if a>=b:
            fail("人声区间时长必须为正")
        normalized.append((a,b))
    for a,b in sorted(normalized):
        if merged and a<=merged[-1][1]:
            merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:
            merged.append((a,b))
    return merged


def volume_filter(windows, duck_db, attack=.05, release=.15):
    """Union windows, fixed configured attenuation, with short edge ramps."""
    activity=[]
    for a,b in merge_windows(windows):
        activity.append(f"max(0,min(1,min((t-({a-attack:.6f}))/{attack},({b+release:.6f}-t)/{release})))")
    if not activity or duck_db==0:
        return "anull"
    union=activity[0]
    for part in activity[1:]:
        union=f"max({union},{part})"
    gain=10**(-duck_db/20)
    return f"volume=eval=frame:volume='1-(1-{gain:.9f})*({union})'"


def load_inputs(a):
    root=a.project.resolve()
    project=json.loads((root/"project.json").read_text(encoding="utf-8"))
    clips=[c for c in json.loads((root/"clips.json").read_text(encoding="utf-8")) if c.get("episode")==a.episode]
    if not clips or len({c["id"] for c in clips})!=len(clips):
        fail("没有选中片段或片段编号重复")
    for c in clips:
        if not c.get("video") or not (c.get("status")=="approved" or
                c.get("final_from_draft") and (c.get("draft") or {}).get("status")=="approved"):
            fail(f"{c['id']} 还没有 approved 的定稿")
    return root,project,clips


def music_for(a, root, publish=False, usage=None):
    if not a.music:
        return None,None
    global_root=root.parent.parent
    path=(global_root/"曲库"/a.music).resolve() if not Path(a.music).is_absolute() else Path(a.music).resolve()
    if not path.is_file():
        fail("配乐文件不存在")
    lib=global_root/"曲库"/"曲库.json"
    matches=[]
    if lib.is_file():
        for t in json.loads(lib.read_text(encoding="utf-8")).get("tracks",[]):
            registered=contained(lib.parent,t["file"])
            if registered==path:
                matches.append(t)
    if len(matches)>1:
        fail("曲库同一路径重复登记")
    track=matches[0] if matches else {}
    evidence=track.get("license_evidence")
    has_text=lambda key: isinstance(track.get(key),str) and bool(track[key].strip())
    verified=(track.get("license_verified") is True and has_text("license")
              and has_text("source_url") and track["source_url"].startswith(("https://","http://")) and has_text("attribution")
              and track.get("sha256")==digest(path) and isinstance(evidence,str)
               and contained(lib.parent,evidence).is_file() and contained(lib.parent,evidence).stat().st_size > 0)
    if publish and not verified:
        fail("公开发布的配乐缺少已核实授权、来源、署名、音频指纹或本地授权证据")
    scope=track.get('usage_scope') or {}
    def covers(key, value):
        values=scope.get(key, []) if isinstance(scope,dict) else []
        return isinstance(values,list) and all(isinstance(x,str) for x in values) and ('*' in values or bool(value) and value in values)
    scoped=bool(usage) and covers('platforms',usage.get('platform')) and covers('uses',usage.get('use'))
    if publish and usage is not None and not scoped:
        fail('配乐授权记录未覆盖当前平台/用途，须核实 usage_scope；不能将试听或旧用途许可直接套用')
    if not verified:
        print("WARNING: 配乐版权未标为完整核实，仅限内部测试",file=sys.stderr)
    return path,{"path":str(path),"verified":bool(verified),"license":track.get("license"),
                 "source_url":track.get("source_url"),"attribution":track.get("attribution"),
                 "license_evidence":evidence,"registry_path":str(lib) if lib.is_file() else None,
                 "evidence_path":str(contained(lib.parent,evidence)) if verified else None,
                  "usage_scope":scope,"usage_verified":scoped,
                 "loopable":track.get("loopable") is True}


def loudness(path, target, peak, allow_silence=False):
    result=run(["ffmpeg","-hide_banner","-nostats","-i",path,"-af",
                f"loudnorm=I={target}:TP={peak}:LRA=11:print_format=json","-f","null","-"])
    blocks=re.findall(r"\{[^{}]+\}",result.stderr)
    if not blocks:
        fail("无法读取响度/真峰值测量，拒绝当成通过")
    data=json.loads(blocks[-1])
    if allow_silence and float(data['input_i']) == float('-inf') and float(data['input_tp']) == float('-inf'):
        return None
    if not all(math.isfinite(float(data[k])) for k in ("input_i","input_tp","input_lra","input_thresh","target_offset")):
        fail("全静音或响度无法测量，不能证明达到响度目标")
    return data


def exact_audio_filter(duration):
    samples = round(duration * 48000)
    return (f'aresample=48000,highpass=f=65,apad=whole_len={samples},'
            f'atrim=end_sample={samples},asetpts=N/SR/TB')


def verify_audio_samples(path, expected):
    frames=json.loads(run(['ffprobe','-v','error','-select_streams','a:0','-show_frames',
                          '-show_entries','frame=nb_samples','-of','json',path]).stdout).get('frames', [])
    if not frames or any(type(f.get('nb_samples')) is not int or f['nb_samples'] <= 0 for f in frames):
        fail('音轨样本数缺失或无效，禁止拼接')
    samples=sum(f['nb_samples'] for f in frames)
    if samples != expected:
        fail('音轨样本数与保留帧时长不一致，禁止拼接')
    return samples


def verify_output_timing(info, total, expected_frames):
    video=next((s for s in info['streams'] if s['codec_type']=='video'), {})
    duration=number(video.get('duration'), '实际视频流时长', .001)
    try:
        frames=int(video.get('nb_frames', ''))
    except (TypeError, ValueError):
        fail('成片缺少可核验的视频帧数')
    if frames != expected_frames or abs(duration-total) > .001:
        fail('成片视频帧数或时长与剪辑时间线不一致')
    actual=number(info['format']['duration'], '实际容器时长', .001)
    # AAC can pad the tail by up to two 1024-sample frames. This tolerance is
    # ONLY for the container tail; it never excuses video-frame or speech drift.
    if actual < total-.001 or actual-total > 2048/48000+.001:
        audio=next((s for s in info['streams'] if s['codec_type']=='audio'), {})
        fail(f'成片容器时长差超过 AAC 尾部填充范围：expected={total:.9f}, '
             f'container={actual:.9f}, video={duration:.9f}, '
             f'audio_start={audio.get("start_time")}, audio_duration={audio.get("duration")}')
    return actual


def normalize_final(source, destination, target, peak, duration=None):
    # Leave AAC encoding headroom, then measure the actual encoded result.
    ceiling=peak-.5
    data=loudness(source,target,ceiling)
    filt=(f"loudnorm=I={target}:TP={ceiling}:LRA=11:measured_I={data['input_i']}:"
          f"measured_TP={data['input_tp']}:measured_LRA={data['input_lra']}:"
          f"measured_thresh={data['input_thresh']}:offset={data['target_offset']}:linear=true")
    # Sparse transients can survive dynamic loudnorm and overshoot again during
    # AAC encoding. Limit peaks at 192 kHz without auto makeup gain, compensate
    # the limiter's lookahead, then rebuild the exact final sample clock below.
    # This does not relax either the published true-peak or loudness check.
    limiter_ceiling=peak-1.5
    filt+=(f',aresample=192000,alimiter=limit={10**(limiter_ceiling/20):.9f}:'
           'level=false:attack=5:release=50:latency=true')
    if duration is not None:
        # The final loudnorm pass can leave an encoder/resampler tail and
        # non-continuous packet timestamps. Rebuild the sample clock only after
        # normalization, with the already-verified video timeline as its length.
        samples=round(duration*48000)
        filt+=(f',aresample=48000,apad=whole_len={samples},'
               f'atrim=end_sample={samples},asetpts=N/SR/TB')
    normalized_audio=destination.with_suffix('.normalized.wav')
    run(["ffmpeg","-v","error","-y","-i",source,"-vn","-af",filt,
         "-c:a","pcm_f32le","-ar","48000","-ac","2",normalized_audio])
    pcm_measurement=loudness(normalized_audio,target,peak)
    limited_measurement=pcm_measurement
    makeup_db=0.
    # The conservative transient limiter can leave an otherwise valid mix below
    # the loudness floor. Use measured peak headroom for a constant correction;
    # never relax the final AAC checks or apply another dynamic normalization.
    if float(pcm_measurement['input_i']) < target-1.:
        makeup_db=min(target-float(pcm_measurement['input_i']),
                      max(0.,peak-.6-float(pcm_measurement['input_tp'])))
        if makeup_db > .01:
            corrected_audio=destination.with_suffix('.compensated.wav')
            correction=f'volume={makeup_db:.6f}dB'
            if duration is not None:
                correction+=(f',aresample=48000,apad=whole_len={samples},'
                             f'atrim=end_sample={samples},asetpts=N/SR/TB')
            run(["ffmpeg","-v","error","-y","-i",normalized_audio,"-af",correction,
                 "-c:a","pcm_f32le","-ar","48000","-ac","2",corrected_audio])
            normalized_audio=corrected_audio
            pcm_measurement=loudness(normalized_audio,target,peak)
    if duration is not None:
        verify_audio_samples(normalized_audio,samples)
    run(["ffmpeg","-v","error","-y","-i",source,"-i",normalized_audio,
         "-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac",
         "-b:a","384k","-ar","48000","-ac","2",destination])
    encoded_measurement=loudness(destination,target,peak)
    print(json.dumps({'final_audio_diagnostics':{'pcm_lufs':pcm_measurement['input_i'],
          'pcm_true_peak':pcm_measurement['input_tp'],'aac_lufs':encoded_measurement['input_i'],
          'aac_true_peak':encoded_measurement['input_tp'],'aac_bitrate':384000}},ensure_ascii=False),file=sys.stderr)
    return {'method':'two-pass loudnorm + oversampled transient limiter',
            'normalization_ceiling_db':ceiling,'limiter_ceiling_db':limiter_ceiling,
            'limiter_latency_compensated':True,'input_measurement':data,
            'limited_pcm_measurement':limited_measurement,
            'constant_makeup_gain_db':makeup_db,'makeup_peak_headroom_db':.6,
            'normalized_pcm_measurement':pcm_measurement,
            'encoded_audio_measurement':encoded_measurement,'aac_bitrate':384000}


def check_output_bundle(root,out,sources):
    paths=[out,out.with_suffix(".srt"),out.with_suffix(".validation.json")]
    for p in paths:
        if not p.is_relative_to(root):
            fail("输出路径必须在作品内")
        for src in sources:
            if p==src or p.exists() and src.exists() and os.path.samefile(p,src):
                fail("输出不能是任何源素材或源清单（--overwrite 也不允许）")
        if p.exists():
            fail("输出或旁文件已存在；--overwrite 不覆盖既有作品，请指定新的 --out")
    return paths


def clip_level_target(entry):
    """Explicit quieter treatment for an ambience/foley-only edit entry."""
    audio = entry.get('audio', {})
    if not isinstance(audio, dict) or set(audio) - {'target_lufs', 'reason'}:
        fail('片段声音覆盖参数错误')
    if audio and (not isinstance(audio.get('reason'), str) or not audio['reason'].strip()):
        fail('片段声音响度覆盖必须注明实际用途')
    return number(audio.get('target_lufs', -16), '片段响度目标', -70, -5)


def load_edit_plan(root, name, clips):
    """An explicit local edit timeline; never rewrites adopted source records."""
    if not name:
        return None, {}
    path=contained(root,name)
    plan=json.loads(path.read_text(encoding="utf-8"))
    if plan.get("version")!=1 or not isinstance(plan.get("clips"),list):
        fail("剪辑时间线格式错误")
    if [e.get("id") for e in plan["clips"]]!=[c["id"] for c in clips]:
        fail("剪辑时间线必须按采用顺序覆盖全部片段，不可遗漏或重复")
    for e,c in zip(plan["clips"],clips):
        if e.get("source")!=c["video"] or e.get("sha256")!=digest(contained(root,c["video"])):
            fail("剪辑来源与当前已采用视频不一致")
        if c.get("fingerprint",{}).get("video")!=e["sha256"]:
            fail("采用视频指纹不匹配")
        clip_level_target(e)
        if c.get("narration"):
            fail("含旁白的片段需先建立对应剪辑后的旁白时间线")
        ranges=e.get("ranges")
        if not isinstance(ranges,list) or not ranges:
            fail("剪辑片段缺少保留帧范围")
        previous=0
        for pair in ranges:
            if (not isinstance(pair,list) or len(pair)!=2 or
                any(type(n) is not int for n in pair) or not previous<=pair[0]<pair[1]):
                fail("保留帧范围必须递增、不重叠且为非负整数")
            previous=pair[1]
        color=e.get("color",{})
        if set(color)-{"brightness","contrast","saturation","gamma"}:
            fail("未知调色字段")
        for k,v in color.items():
            number(v,"调色参数",-.1 if k=="brightness" else .8,.1 if k=="brightness" else 1.2)
    return path,plan


def edit_filters(entry, info, fps, width, height, has_audio):
    """Frame-aligned video/audio trims, joined before normalization."""
    video=next(s for s in info["streams"] if s["codec_type"]=="video")
    rate=float(Fraction(video["avg_frame_rate"]))
    frames=int(video.get("nb_frames") or round(float(video["duration"])*rate))
    if abs(rate-fps)>.0001 or entry.get("source_frames")!=frames:
        fail("剪辑时间线的源帧率/总帧数与实测不一致")
    parts=[];links="";total=0.
    for i,(start,end) in enumerate(entry["ranges"]):
        if end>frames:
            fail("剪辑范围超出源视频")
        duration=(end-start)/rate;total+=duration
        parts.append(f"[0:v]trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS[v{i}]")
        audio="0:a" if has_audio else "1:a"
        parts.append(f"[{audio}]atrim=start={start/rate:.9f}:end={end/rate:.9f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.005,afade=t=out:st={max(0,duration-.005):.9f}:d=0.005[a{i}]")
        links+=f"[v{i}][a{i}]"
    parts.append(f"{links}concat=n={len(entry['ranges'])}:v=1:a=1[cutv][cuta]")
    color=entry.get("color",{})
    grade=","+"eq="+":".join(f"{k}={v}" for k,v in color.items()) if color else ""
    parts.append(f"[cutv]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p{grade}[outv]")
    # Dynamic loudnorm can emit a short frame with a following timestamp gap.
    # PCM concatenation would collapse that gap. Preserve every sample here;
    # measure and apply constant gain separately, then assert exact sample count.
    parts.append(f"[cuta]{exact_audio_filter(total)}[outa]")
    return ";".join(parts),total


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",type=Path,required=True)
    parser.add_argument("--episode",required=True)
    parser.add_argument("--music")
    parser.add_argument("--music-loop",action="store_true",help="明确允许循环内部测试配乐；已登记 loopable 曲目自动循环")
    parser.add_argument("--srt",help="对白 SRT（项目内路径）；旁白由实际音频时长追加")
    parser.add_argument("--speech-timeline",help="项目内 JSON：{segments:[[起秒,止秒]],source:manual-reviewed|local-vad}")
    parser.add_argument("--edit-plan",help="项目内版本化剪辑时间线，绑定已采用来源SHA和实测帧数")
    parser.add_argument("--no-visible-ai-label",action="store_true",help="仅本次本地预览不叠加画面 AI 文字；保留元数据配置，不改变项目默认值")
    parser.add_argument('--purpose', choices=('preview','public-release'),
                        help='本次交付用途；默认同时参考发布意图与 publish，预览不等于可发布')
    group=parser.add_mutually_exclusive_group()
    group.add_argument("--burn-subtitles",dest="burn",action="store_true")
    group.add_argument("--no-burn-subtitles",dest="burn",action="store_false")
    parser.set_defaults(burn=None)
    parser.add_argument("--out",type=Path)
    parser.add_argument("--fps",type=int,default=30)
    parser.add_argument("--overwrite",action="store_true",help="兼容旧命令；仍拒绝覆盖已有输出，请使用新路径")
    a=parser.parse_args()
    if not re.fullmatch(r"EP\d{3,}",a.episode):
        fail("episode 必须是 EP 加至少三位数字")
    root,pj,clips=load_inputs(a)
    from validate_project import production_config_errors
    config_errors=production_config_errors(pj)
    if config_errors:
        fail('；'.join(config_errors))
    edit_path,edit=load_edit_plan(root,a.edit_plan,clips)
    post=dict(pj.get("post") or {})
    overrides=edit.get("post",{})
    if set(overrides)-{"bgm_gain_db","bgm_duck_db","loudness_lufs","true_peak_db"}:
        fail("剪辑时间线包含不支持的声音覆盖参数")
    post.update(overrides)
    width,height=output_dimensions(pj.get("ratio"))
    if edit:
        output=edit.get("output",{})
        dims=output.get("size")
        if (not isinstance(dims,list) or len(dims)!=2 or
            any(type(x) is not int or x%2 or not 64<=x<=4096 for x in dims)):
            fail("剪辑输出尺寸必须是明确的偶数像素")
        if abs((dims[0]/dims[1])/(width/height)-1)>.03:
            fail("剪辑尺寸与项目画幅偏离超过3%")
        width,height=dims
        if output.get("fps")!=a.fps:
            fail("剪辑时间线与输出帧率不一致")
        subtitles=output.get('subtitles', {})
        if not isinstance(subtitles,dict) or set(subtitles)-{'font_size','outline','margin_v'}:
            fail('剪辑字幕样式参数错误')
        subtitle_size=number(subtitles.get('font_size',11),'字幕字号',8,40)
        subtitle_outline=number(subtitles.get('outline',.6),'字幕描边',0,3)
        subtitle_margin=number(subtitles.get('margin_v',26),'字幕下边距',10,80)
    lufs=number(post.get("loudness_lufs",-14),"响度",-70,-5)
    peak=number(post.get("true_peak_db",-1),"真峰值",-8.5,0)
    duck=number(post.get("bgm_duck_db",12),"配乐压低",0,60)
    narration_duck=number(post.get("narration_duck_db",6),"旁白期间原声压低",0,60)
    bgm_gain=number(post.get("bgm_gain_db",0),"配乐基础增益",-60,0)
    if not 1<=a.fps<=120:
        fail("fps 必须在 1..120 内")
    try:
        delivery_context=publication_context(pj,a.purpose)
    except ValueError as error:
        fail(error)
    public_release=delivery_context['purpose']=='public-release'
    effective_project={**pj,'publish':public_release,'post':post}
    check_publication(effective_project)
    label=effective_ai_label(effective_project,post,a.no_visible_ai_label)
    if any(type(label[k]) is not bool for k in ("visible","metadata") if k in label):
        fail("AI 标识开关必须为布尔值")
    want_burn=should_burn(pj,a.burn)
    music,music_info=music_for(a,root,public_release,delivery_context)
    out=contained(root,a.out or f"单集制作/{a.episode}/成片.mp4")
    if out.suffix.lower()!=".mp4":
        fail("输出必须是 .mp4")
    sources={root/"project.json",root/"clips.json"}
    if edit_path:sources.add(edit_path)
    offsets=[]
    total=0.
    expected_frames=0
    info_list=[]
    narrations=[]
    for ci,c in enumerate(clips):
        src=contained(root,c["video"])
        info=probe(src)
        videos=[s for s in info["streams"] if s["codec_type"]=="video"]
        if not videos:
            fail(f"{c['id']} 没有视频流")
        duration=number(videos[0].get("duration") or info["format"]["duration"],"视频时长",.001)
        if edit:
            _,duration=edit_filters(edit['clips'][ci],info,a.fps,width,height,
                                    any(s['codec_type']=='audio' for s in info['streams']))
        elif abs(duration-number(c["duration"],"分镜时长",.001))>max(.25,2/a.fps):
            fail("视频时长与分镜差异过大")
        frames=round(duration*a.fps)
        if frames < 1:
            fail('片段不足一个输出帧')
        duration=frames/a.fps
        expected_frames+=frames
        offsets.append(total)
        total+=duration
        sources.add(src)
        info_list.append((src,info,duration))
        for n in c.get("narration") or []:
            if not n.get("audio"):
                fail(f"{c['id']} 的旁白还没配音")
            wav=contained(root,n["audio"])
            measured=number(probe(wav)["format"]["duration"],"旁白时长",.001)
            if not any(s["codec_type"]=="audio" for s in probe(wav)["streams"]):
                fail("旁白文件没有音轨")
            if abs(measured-number(n.get("narration_duration"),"登记旁白时长",.001))>.1:
                fail("登记旁白时长与音频不符，先重新核对")
            local=number(n.get("start",0),"旁白开始")
            if local+measured>duration+.001:
                fail("旁白超出所属片段，不能静默截断或串入下一段")
            narrations.append({"wav":wav,"start":offsets[-1]+local,"end":offsets[-1]+local+measured,"text":n["text"]})
            sources.add(wav)
    dialogue=[]
    if a.srt:
        path=contained(root,a.srt)
        sources.add(path)
        dialogue=parse_srt(path.read_text(encoding="utf-8"))
    validate_cues(dialogue,total)
    cues=dialogue+[{k:n[k] for k in ("start","end","text")} for n in narrations]
    validate_cues(cues,total)
    srt_text=render_srt(cues)
    speech=[(c["start"],c["end"]) for c in dialogue]
    speech_source="dialogue-srt" if a.srt else None
    if a.speech_timeline:
        path=contained(root,a.speech_timeline)
        sources.add(path)
        timeline=json.loads(path.read_text(encoding="utf-8"))
        if timeline.get("source") not in ("manual-reviewed","local-vad"):
            fail("人声区间必须注明 manual-reviewed 或 local-vad 来源")
        segments=timeline.get("segments")
        if not isinstance(segments,list) or any(not isinstance(p,list) or len(p)!=2 for p in segments):
            fail("人声区间必须是开始/结束数值对列表")
        validate_cues([{"start":p[0],"end":p[1],"text":"speech"} for p in segments],total)
        speech.extend(tuple(p) for p in segments)
        speech_source=timeline["source"]
    if music and not speech_source:
        fail("配乐混音需要对白 SRT 或 --speech-timeline，不猜测对白位置")
    if music:
        sources.add(music)
        for key in ("registry_path","evidence_path"):
            if music_info.get(key):
                sources.add(Path(music_info[key]))
        music_duration=number(probe(music)["format"]["duration"],"配乐时长",.001)
        loop=bool(a.music_loop or music_info["loopable"])
        if music_duration<total-.01 and not loop:
            fail("配乐过短且未允许循环，不用静音补尾；请选新曲或显式 --music-loop")
    else:
        loop=False
    hashes={str(p):digest(p) for p in sources}
    out.parent.mkdir(parents=True,exist_ok=True)
    with runtime.exclusive_lock(root/".assemble.lock"):
        outputs=check_output_bundle(root,out,sources)
        with tempfile.TemporaryDirectory(prefix="assemble2-",dir=out.parent) as folder:
            tmp=Path(folder)
            normalized=[]
            clip_levels=[]
            for i,(src,info,duration) in enumerate(info_list):
                dst=tmp/f"clip-{i}.mkv"
                argv=["ffmpeg","-v","error","-y","-i",src]
                has_audio=any(s["codec_type"]=="audio" for s in info["streams"])
                if not has_audio:
                    argv += ["-f","lavfi","-i","anullsrc=r=48000:cl=stereo"]
                vf=(f"setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={a.fps},format=yuv420p")
                af='asetpts=PTS-STARTPTS,'+exact_audio_filter(duration)
                if edit:
                    filters,_=edit_filters(edit['clips'][i],info,a.fps,width,height,has_audio)
                    argv += ["-filter_complex",filters,"-map","[outv]","-map","[outa]"]
                else:
                    argv += ["-map","0:v:0","-map","0:a:0" if has_audio else "1:a:0","-vf",vf,"-af",af]
                argv += ["-t",duration,"-c:v","libx264","-preset","fast","-crf","18","-c:a","pcm_s16le","-ar","48000","-ac","2",dst]
                run(argv)
                level_target=clip_level_target(edit['clips'][i] if edit else {})
                measure=loudness(dst,level_target,-1.5,allow_silence=True)
                gain=min(level_target-float(measure['input_i']),-1.5-float(measure['input_tp'])) if measure else 0.
                leveled=tmp/f"clip-{i}-leveled.mkv"
                run(["ffmpeg","-v","error","-y","-i",dst,"-map","0:v:0","-map","0:a:0",
                     "-c:v","copy","-af",f"volume={gain:.6f}dB","-c:a","pcm_s16le","-ar","48000","-ac","2",leveled])
                samples=verify_audio_samples(leveled,round(duration*48000))
                clip_levels.append({'id':clips[i]['id'],'measured_lufs':float(measure['input_i']) if measure else None,
                                    'target_lufs':level_target,
                                    'silent':measure is None,'constant_gain_db':gain,
                                    'audio_samples':samples,'expected_samples':round(duration*48000)})
                dst=leveled
                normalized.append(dst)
            listing=tmp/"list.txt"
            listing.write_text("".join(f"file '{p.name}'\n" for p in normalized),encoding="utf-8",newline="\n")
            base=tmp/'base.mkv'
            run(["ffmpeg","-v","error","-y","-f","concat","-safe","1","-i",listing,"-c","copy",base])
            verify_audio_samples(base,sum(c['expected_samples'] for c in clip_levels))
            argv=["ffmpeg","-v","error","-y","-i",base]
            for n in narrations:
                argv += ["-i",n["wav"]]
            if music:
                if loop:
                    argv += ["-stream_loop","-1"]
                argv += ["-i",music]
            narration_windows=[(n["start"],n["end"]) for n in narrations]
            filters=[f"[0:a]{volume_filter(narration_windows,narration_duck)}[main]"]
            inputs="[main]"
            for i,n in enumerate(narrations):
                delay=round(n["start"]*48000)
                filters.append(f"[{i+1}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={delay}S:all=1[n{i}]")
                inputs+=f"[n{i}]"
            filters.append(f"{inputs}amix=inputs={1+len(narrations)}:duration=first:normalize=0:dropout_transition=0[withn]")
            all_windows=merge_windows(speech+narration_windows)
            if music:
                music_fades=f",afade=t=in:d=1.5,afade=t=out:st={max(0,total-2):.6f}:d=2" if edit else ""
                filters.append(f"[{len(narrations)+1}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                               f"atrim=0:{total:.6f},volume={bgm_gain}dB,{volume_filter(all_windows,duck)}{music_fades}[bgm]")
                filters.append("[withn][bgm]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[outa]")
            else:
                filters.append("[withn]anull[outa]")
            mixed=tmp/'mixed.mkv'
            run(argv+["-filter_complex",";".join(filters),"-map","0:v:0","-map","[outa]","-t",total,
                      "-c:v","copy","-c:a","pcm_s16le","-ar","48000","-ac","2",mixed])
            loud=tmp/"loud.mp4"
            final_normalization=normalize_final(mixed,loud,lufs,peak,total)
            srt_stage=tmp/"captions.srt"
            srt_stage.write_text(srt_text,encoding="utf-8",newline="\n")
            # Matroska's millisecond timestamp grid must not become variable
            # MP4 frame durations when no captions/labels happen to be enabled.
            need_subtitles = bool(want_burn and cues)
            need_label = bool(label.get("visible"))
            if need_label:
                position=label.get("badge_position","top-right")
                positions={"top-right":("w-tw-24","24"),"top-left":("24","24"),
                           "bottom-right":("w-tw-24","h-th-24"),"bottom-left":("24","h-th-24")}
                if position not in positions:
                    fail("未知 AI 角标位置")
                for filename,text in [("notice.txt",label.get("text","本片含 AI 生成内容")),("badge.txt",label.get("badge","AI 生成"))]:
                    size=28 if filename=="notice.txt" else 16
                    if not isinstance(text,str) or not text.strip():
                        fail("AI 标识文字为空")
                    if "\n" in text or len(text)*size>width-48:
                        fail("AI 标识文字可能超出画面，请缩短标识")
            avail_filters = _local_ffmpeg_filters()
            missing_visual = ((need_subtitles and "subtitles" not in avail_filters)
                              or (need_label and "drawtext" not in avail_filters))
            remote_url, remote_token = _resolve_remote_render() if missing_visual else (None, None)
            used_remote_render = False
            final=tmp/"final.mp4"
            if missing_visual and remote_url:
                base_vf=[f'fps={a.fps}']
                if edit:
                    base_vf.append(f"fade=t=out:st={max(0,total-.35):.6f}:d=0.35")
                pre_render=tmp/"pre-render.mp4"
                run(["ffmpeg","-v","error","-y","-i",loud,"-vf",",".join(base_vf),
                     "-c:v","libx264","-preset","fast","-crf","18","-c:a","copy",pre_render])
                remote_mp4=tmp/"remote-rendered.mp4"
                overlay_text=label.get("text","本片含 AI 生成内容") if need_label else None
                _render_remote_mp4(remote_url,remote_token,pre_render,
                                   srt_stage if need_subtitles else None,
                                   overlay_text,min(3.0,total),remote_mp4)
                argv=["ffmpeg","-v","error","-y","-i",remote_mp4,"-i",loud,
                      "-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","copy","-movflags","+faststart"]
                if label.get("metadata"):
                    argv += ["-metadata","comment="+label.get("text","本片含 AI 生成内容")+"（AI 生成合成内容）"]
                run(argv+[final])
                used_remote_render = True
            else:
                vf=[f'fps={a.fps}']
                if need_subtitles:
                    if not FONT.is_file():
                        fail("字幕字体不存在")
                    style="FontName=Source Han Sans SC,FontSize=18,Outline=1,MarginV=36"
                    if edit:
                        style=(f"FontName=Source Han Sans SC,FontSize={subtitle_size},Bold=1,"
                               f"Outline={subtitle_outline},Shadow=0,MarginV={subtitle_margin},Alignment=2")
                    vf.append(f"subtitles=filename='{ff_escape(srt_stage)}':fontsdir='{ff_escape(FONT.parent)}':force_style='{style}'")
                if edit:
                    vf.append(f"fade=t=out:st={max(0,total-.35):.6f}:d=0.35")
                if need_label:
                    if not FONT_BOLD.is_file():
                        fail("标识字体不存在")
                    for filename,text in [("notice.txt",label.get("text","本片含 AI 生成内容")),("badge.txt",label.get("badge","AI 生成"))]:
                        (tmp/filename).write_text(text,encoding="utf-8",newline="\n")
                    font=f"fontfile='{ff_escape(FONT_BOLD)}'"
                    vf.append(f"drawtext={font}:textfile='{ff_escape(tmp/'notice.txt')}':expansion=none:fontsize=28:"
                              "fontcolor=white:borderw=1:bordercolor=black:x=(w-tw)/2:y=24:enable='lt(t,3)'")
                    x,y=positions[position]
                    vf.append(f"drawtext={font}:textfile='{ff_escape(tmp/'badge.txt')}':expansion=none:fontsize=16:"
                              f"fontcolor=white:borderw=1:bordercolor=black:x={x}:y={y}")
                argv=["ffmpeg","-v","error","-y","-i",loud]
                if vf:
                    argv += ["-vf",",".join(vf),"-c:v","libx264","-preset","fast","-crf","18"]
                else:
                    argv += ["-c:v","copy"]
                argv += ["-c:a","copy","-movflags","+faststart"]
                if label.get("metadata"):
                    argv += ["-metadata","comment="+label.get("text","本片含 AI 生成内容")+"（AI 生成合成内容）"]
                run(argv+[final])
            run(["ffmpeg","-v","error","-xerror","-i",final,"-f","null","-"])
            info=probe(final)
            actual=verify_output_timing(info,total,expected_frames)
            video=next((s for s in info["streams"] if s["codec_type"]=="video"),{})
            audio=next((s for s in info["streams"] if s["codec_type"]=="audio"),{})
            if (video.get("width"),video.get("height"))!=(width,height):
                fail("成片时长或分辨率校验失败")
            try:
                fps=float(Fraction(video.get("avg_frame_rate","0")))
            except (ValueError, ZeroDivisionError):
                fail("成片帧率无法核对")
            if abs(fps-a.fps)>.001 or video.get("pix_fmt")!="yuv420p":
                fail("成片帧率或像素格式不符")
            if audio.get("channels")!=2 or int(audio.get("sample_rate",0))!=48000:
                fail("成片须有双声道 48kHz 音轨")
            measure=loudness(final,lufs,peak)
            measured,measured_peak=float(measure["input_i"]),float(measure["input_tp"])
            if abs(measured-lufs)>1.0 or measured_peak>peak+.1:
                fail(f"成片响度/真峰值不达标：{measured} LUFS / {measured_peak} dBTP")
            if label.get("metadata") and "AI" not in info["format"].get("tags",{}).get("comment",""):
                fail("AI 元数据未写入最终文件")
            if any(digest(Path(p))!=h for p,h in hashes.items()):
                fail("合成期间源文件改变，停止交付")
            report={"episode":a.episode,"ratio":pj["ratio"],"width":width,"height":height,"fps":a.fps,
                    "clips":[{"id":c["id"],"duration":d,"from_draft":bool(c.get("final_from_draft"))}
                             for c,(_,_,d) in zip(clips,info_list)],"expected_duration":total,"actual_duration":actual,
                    "burn_subtitles":want_burn,"loudness_target":lufs,"measured_lufs":measured,
                    "true_peak_target":peak,"measured_true_peak_db":measured_peak,"full_decode":"passed",
                    "aigc_metadata":bool(label.get("metadata")),"visual_review":"required（人工逐段看）",
                    "local_preview_without_visible_ai_label":a.no_visible_ai_label,
                    "input_sha256":hashes,"music":music_info,"final_audio_normalization":final_normalization,"steps":{"srt":str(outputs[1]),
                    "burn_in":{"subtitles":bool(want_burn and cues),"aigc_label":bool(label.get("visible")),
                               "remote_render":remote_url if used_remote_render else None},
                    "narration_music":{"narrations":len(narrations),"music":bool(music),"music_duck_db":duck if music else None,
                    "narration_duck_db":narration_duck,"speech_source":speech_source,"duck_windows":all_windows,
                    "narration_windows":narration_windows,"bgm_gain_db":bgm_gain,"music_looped":bool(loop)}},
                    "audio_integrity":{"intermediates":"lossless PCM","clip_audio_levels":clip_levels,
                                       "expected_video_frames":expected_frames},
                    "delivery":{"status":"complete","purpose":delivery_context['purpose'],
                                "video_sha256":digest(final),"srt_sha256":digest(srt_stage)},
                    "release":{"status":"technical_checks_passed_review_required" if public_release else "local_preview_only",
                               "context":delivery_context,"actual_listening_review":"required", "visual_review":"required"}}
            if edit:
                report['edit_plan']={'path':str(edit_path),'sha256':hashes[str(edit_path)],'clips':edit['clips'],
                                     'source_dimensions_preserved':all((next(s for s in info['streams'] if s['codec_type']=='video')['width'],
                                                                        next(s for s in info['streams'] if s['codec_type']=='video')['height'])==(width,height)
                                                                       for _,info,_ in info_list),'audio_intermediates':'lossless PCM',
                                     'final_fade_seconds':.35,'music_fades_seconds':[1.5,2] if music else None}
                report['edit_plan']['clip_audio_levels']=clip_levels
            rep_stage=tmp/"report.json"
            runtime.atomic_json(rep_stage,report)
            # Each file is no-clobber. Report last is the completion marker, not a
            # claim that three filesystem entries can be atomically committed.
            runtime.publish_output(srt_stage,outputs[1])
            runtime.publish_output(final,out)
            runtime.publish_output(rep_stage,outputs[2])
    print(json.dumps({"written":str(out),"report":str(outputs[2]),"duration":actual,
                      "measured_lufs":measured,"measured_true_peak_db":measured_peak,
                      "next":["亲自检查字幕、AI 标识位置并试听，交总导演审核"]},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
