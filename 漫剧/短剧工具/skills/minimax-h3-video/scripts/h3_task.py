#!/usr/bin/env python3
"""MiniMax H3 video tasks: prepare (offline), submit (preview unless --execute), query/download, cancel, balance.

Safety rules (same spirit as the dreamina skill's submit_task.py):
- prepare never touches the network; submit previews unless --execute.
- every attempt directory is reserved once (submission.lock); an attempt is never re-submitted
  automatically. An unknown submit may be re-sent only with --retry-unknown, which reuses the same
  Idempotency-Key so the platform returns the original task instead of creating a second one.
- the API key is read from the macOS Keychain (service MINIMAX_H3_API_KEY) or the env var of the
  same name; it is never written to disk or printed. Data URLs are never written to disk either.
"""
import argparse, base64, hashlib, json, math, mimetypes, os, re, shutil, subprocess, sys, tempfile, time
from datetime import datetime
from contextlib import contextmanager

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt
import urllib.error, urllib.request
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # Windows 中文系统下 -I 会忽略 PYTHONUTF8，强制输出 UTF-8
    _s.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import generation_runtime as runtime

BASE = os.environ.get("H3_BASE_URL", "https://cp.compshare.cn").rstrip("/")
UA = "novel-to-video-h3/1.0"
QUEUE_EXIT = 4
MODELS = ("MiniMax-H3", "minimax-h3-lite")
RESOLUTIONS = ("480P", "768P", "1080P", "2K", "4K")
RATIOS = ("adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
ROLES = ("reference", "first_frame", "last_frame", "reference_video", "reference_audio")
TERMINAL = {"succeeded", "failed", "cancelled", "blocked"}
POINTS_PER_SECOND = {"480P": 5, "768P": 10, "1080P": 19, "2K": 20, "4K": 22}
# Asset IDs (C/S/P/K + 5 digits, optional form suffix), tail frames, and "...参考" labels such as 音色参考.
LABEL = re.compile(r"\[((?:[A-Z]\d{5}(?:-[A-Z]+)?)|(?:Clip\d+-尾帧)|(?:[^\[\]\s]{1,24}参考))\]")
RAW_TAG = re.compile(r"<\s*(?:Picture|Video|Audio)\s*\d+\s*>|(?:图|图片)\s*\d+|@图片?\d+|<Image\s*\d+>", re.I)
MiB = 1024 * 1024


def fail(msg):
    print("ERROR: " + msg, file=sys.stderr)
    raise SystemExit(1)


def replace_with_retry(src, dst, timeout=10):
    """os.replace with retries: on Windows it fails while another process has dst open for reading."""
    deadline, delay = time.monotonic() + timeout, 0.02
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


atomic_json = runtime.atomic_json

def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def windows_credential(target):
    """Read a generic credential from Windows Credential Manager (stored with: cmdkey /generic:<target> /user:x /pass:<key>)."""
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes as wt

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR), ("Comment", wt.LPWSTR),
                    ("LastWritten", wt.FILETIME), ("CredentialBlobSize", wt.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wt.DWORD),
                    ("AttributeCount", wt.DWORD), ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR),
                    ("UserName", wt.LPWSTR)]
    advapi = ctypes.WinDLL("advapi32")
    advapi.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    advapi.CredReadW.restype = wt.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    cred = ctypes.POINTER(CREDENTIAL)()
    if not advapi.CredReadW(target, 1, 0, ctypes.byref(cred)):  # 1 = CRED_TYPE_GENERIC
        return ""
    try:
        blob = ctypes.string_at(cred.contents.CredentialBlob, cred.contents.CredentialBlobSize)
    finally:
        advapi.CredFree(cred)
    for enc in ("utf-16-le", "utf-8"):  # cmdkey stores UTF-16LE
        try:
            text = blob.decode(enc).strip("\x00 \r\n")
        except UnicodeDecodeError:
            continue
        if text.isascii():
            return text
    return ""


def api_key():
    """Env var first (explicit override, used by tests), then Windows Credential Manager, then macOS Keychain."""
    key = os.environ.get("MINIMAX_H3_API_KEY", "").strip()
    if not key:
        key = windows_credential("MINIMAX_H3_API_KEY")
    if not key and sys.platform == "darwin" and shutil.which("security"):
        r = subprocess.run(["security", "find-generic-password", "-s", "MINIMAX_H3_API_KEY", "-w"],
                           capture_output=True, text=True)
        key = r.stdout.strip()
    if not key.startswith("sk-ml-"):
        fail("MINIMAX_H3_API_KEY not found (env, Windows Credential Manager or Keychain) or not an H3 key (sk-ml-...)")
    return key


def http(method, path, body=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Authorization": "Bearer " + api_key(), "User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:1000]}


# ---------------------------------------------------------------- prepare

DIALOGUE = re.compile(r"<d>(.*?)</d>", re.S)
DIALOGUE_SYMBOLS = [
    (re.compile(r"…|\.\.\.|——|--"), "ellipsis/dash (untested on H3; prefer commas)"),
    (re.compile(r"[~～]"), "tilde (official guide removes it)"),
    (re.compile(r"[!！?？]{2,}"), "repeated !/? (use one mark)"),
    (re.compile(r"[（(][^）)]*[）)]"), "parenthesized stage direction or (laughs)-style tag (may be spoken aloud; write it outside <d>)"),
    (re.compile(r"<#[\d.]+#>"), "Speech-2.8 pause tag (not H3 syntax)"),
    (re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]"), "emoji"),
]


# Continuity sentences (decision H5-A): the screenwriter wraps the two sentences that depend on the
# previous clip's real tail frame in markers; the camera bot may rewrite only their contents
# (set-continuity) after looking at the real frame. prepare strips the markers.
CONT = re.compile(r"\{\{(承接定义|承接开场)(·已核对)?：(.*?)\}\}", re.S)


def strip_continuity(body):
    found = [(m.group(1), bool(m.group(2))) for m in CONT.finditer(body)]
    return CONT.sub(lambda m: m.group(3).strip(), body), found


# Anti-gibberish (H3-specific, community evidence): H3 tends to fill speech-free time with invented
# speech, and long reference audio is reported to cause a ~0.5 s burst at the start.
REF_AUDIO_MAX_S = 5.0


def audio_seconds(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def silence_warnings(body, refs, audio_mode):
    out = []
    has_people = any(re.fullmatch(r"C\d{5}(?:-[A-Z]+)?", r["label"]) for r in refs) or "<Subject" in body
    has_dialogue = "<d>" in body
    declared = re.search(r"\bsilently\b|No one speaks|无人(?:说话|讲话|开口)|没有人(?:说话|讲话)|全程无(?:对白|台词)", body, re.I)
    if has_people and not has_dialogue and not declared:
        out.append("characters on screen but no dialogue or explicit silence declaration (无人说话 / No one speaks): H3 may invent speech")
    if audio_mode != "mute":
        m = re.search(r"overall_soundscape:\s*(.*?)(?:\n\s*\n|non_diegetic_music:|$)", body, re.S)
        if not m or not m.group(1).strip() or m.group(1).strip().upper().startswith("N/A"):
            out.append("overall_soundscape missing/empty: H3 tends to fill silence with speech; describe concrete ambience")
    for r in refs:
        if r["role"] == "reference_audio":
            sec = audio_seconds(r["path"])
            if sec is not None and sec > REF_AUDIO_MAX_S:
                out.append(f"reference audio {r['label']} is {sec:.1f} s; keep voice templates around 4 s (max {REF_AUDIO_MAX_S:g})")
    return out


def dialogue_warnings(body):
    out = []
    for i, line in enumerate(DIALOGUE.findall(body), 1):
        text = re.sub(r"^\s*\[[^\]]+\]\s*", "", line)  # drop [Chinese]
        if "<cutoff>" not in text and not re.search(r"[。？！.?!]\s*$", text):
            out.append(f"dialogue {i} does not end with 。？！")
        for pat, why in DIALOGUE_SYMBOLS:
            if pat.search(text):
                out.append(f"dialogue {i}: {why}")
    return out


def has_narration_instruction(body):
    # A prohibition is not a request for narration; actual narration still warns.
    positive = re.sub(r"\bno\s+narrator\b|(?:禁止|不要|不允许|不需要|无需|不得|不)(?:生成|添加|出现|加入|使用|有)?(?:任何)?旁白|无旁白", "", body, flags=re.I)
    return bool(re.search(r"\bnarrator\b|旁白", positive, re.I))


def parse_ref(item):
    """LABEL=PATH or LABEL=PATH@role. role defaults to reference."""
    label, sep, rest = item.partition("=")
    if not sep or not label or not rest:
        fail("--ref needs LABEL=PATH[@role]: " + item)
    path, role = rest, "reference"
    if "@" in rest:
        head, tail = rest.rsplit("@", 1)
        if tail in ROLES:
            path, role = head, tail
    p = Path(path).expanduser().resolve()
    if not p.is_file() or not p.stat().st_size:
        fail("missing/empty reference: " + path)
    return {"label": label.strip("[]"), "path": str(p), "role": role}


def kind_of(ref):
    if ref["role"] == "reference_video":
        return "video"
    if ref["role"] == "reference_audio":
        return "audio"
    return "image"


def billing(request):
    """Documented CompShare tariff, verified 2026-11-07; video input is not free."""
    params=request['params']
    resolution=params['resolution']
    seconds=params['duration']
    context=params.get('use_context_ir',False)
    if (resolution not in POINTS_PER_SECOND or type(context) is not bool or type(seconds) is not int
            or not 4<=seconds<=30):
        fail('invalid duration/resolution/use_context_ir for billing')
    durations=[]
    for ref in request.get('references',[]):
        if kind_of(ref)!='video':continue
        value=audio_seconds(ref['path'])
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            fail('cannot measure reference video duration; refusing an incomplete cost estimate')
        durations.append(value)
    input_seconds=math.ceil(sum(durations))
    output_points=seconds*POINTS_PER_SECOND[resolution]
    input_points=input_seconds*(5 if resolution=='480P' else 10)
    context_points=5 if context else 0
    return {'tariff':'compshare-h3-20261107','output_seconds':seconds,'input_seconds':input_seconds,
            'output_points':output_points,'input_points':input_points,'context_ir_points':context_points,
            'total_points':output_points+input_points+context_points}


def cmd_prepare(a):
    body = a.prompt.read_text(encoding="utf-8").strip()
    if not body:
        fail("prompt is empty")
    body, cont = strip_continuity(body)
    if "{{" in body or "}}" in body:
        fail("unrecognized {{...}} marker; only {{承接定义：...}} and {{承接开场：...}} are allowed")
    if RAW_TAG.search(body):
        fail("prompt contains fixed numbers like <Picture 1>/图1/@图片1; write [C00001-A]-style labels and let the script number them")
    refs = [parse_ref(r) for r in a.ref]
    labels = [r["label"] for r in refs]
    if len(set(labels)) != len(labels):
        fail("duplicate reference labels")
    if a.output.exists():
        fail("output exists; choose a new request filename")

    frames = [r for r in refs if r["role"] in ("first_frame", "last_frame")]
    others = [r for r in refs if r not in frames]
    if frames and others:
        fail("H3 cannot mix first/last frame with reference images/videos/audio; pick one mode")
    for role in ("first_frame", "last_frame"):
        if sum(r["role"] == role for r in refs) > 1:
            fail(f"at most one {role}")
    counts = {k: sum(kind_of(r) == k for r in others) for k in ("image", "video", "audio")}
    if counts["image"] > 9 or counts["video"] > 3 or counts["audio"] > 3 or sum(counts.values()) > 12:
        fail(f"reference limits exceeded (images<=9, videos<=3, audio<=3, total<=12): {counts}")
    if counts["audio"] and not (counts["image"] or counts["video"]):
        fail("reference audio cannot be used alone; add a reference image or video")

    if not 4 <= a.duration <= 30:
        fail("duration must be an integer 4..30")
    context_ir = a.context_ir == "on"
    if a.duration > 15 and not context_ir:
        fail("duration > 15 requires --context-ir on")
    if a.duration > 15 and not a.allow_long:
        fail("duration > 15: official H3 limit is 15 s; split the clip (or pass --allow-long deliberately)")
    if a.skill_id and not context_ir:
        fail("--skill-id requires --context-ir on")

    used = LABEL.findall(body)
    missing = sorted(set(used) - set(labels))
    if missing:
        fail("labels used in prompt but not attached: " + ", ".join(missing))
    unused = [l for l in labels if l not in used]
    warnings = [f"attached but never referenced in prompt: {', '.join(unused)}"] if unused else []
    if a.duration > 15:
        warnings.append("duration > 15 (--allow-long): CompShare's longer mode is undocumented and forces Context-IR")
    if counts["image"] > 5:
        warnings.append(f"{counts['image']} reference images; 4-5 is usually more stable")
    if frames == [] and refs and "subject_definitions:" not in body:
        warnings.append("full-reference mode without the six-section structure (subject_definitions ...)")
    warnings += dialogue_warnings(body)
    warnings += silence_warnings(body, refs, a.audio)
    tails = [r for r in refs if r["label"].endswith("尾帧")]
    if tails and not any(ok for _, ok in cont):
        warnings.append("continues from a tail frame but the continuity sentences are not verified; run set-continuity after viewing the real frame")
    elif tails:
        log = a.prompt.with_name(a.prompt.stem + ".continuity.jsonl")
        last = None
        if log.is_file():
            lines = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
            last = json.loads(lines[-1]).get("frame_sha256") if lines else None
        if last not in {sha256_file(r["path"]) for r in tails}:
            warnings.append("continuity sentences were verified against a different tail frame (e.g. the draft's); "
                            "for the final pass view the final video's tail frame and run set-continuity again")
    if has_narration_instruction(body):
        warnings.append("narrator found: narration is dubbed in post, not generated by H3; write 'No one speaks' for that span")
    if re.search(r"non_diegetic_music:\s*(?!N/A)\S", body):
        warnings.append("non_diegetic_music is not N/A; per-clip BGM will not match across clips")

    # Number each media type separately in content order, as the API does.
    numbers, n = {}, {"image": 0, "video": 0, "audio": 0}
    tag = {"image": "Picture", "video": "Video", "audio": "Audio"}
    for r in refs:
        k = kind_of(r)
        n[k] += 1
        numbers[r["label"]] = f"<{tag[k]} {n[k]}>"
        r["tag"] = numbers[r["label"]]
        r["sha256"] = sha256_file(r["path"])
    final = LABEL.sub(lambda m: numbers.get(m.group(1), m.group(0)), body)
    if len(final) > 7000:
        fail(f"prompt is {len(final)} chars after mapping; H3 limit is 7000")

    request = {
        "schema_version": 1,
        "provider": "minimax-h3",
        "purpose": a.purpose,
        "prompt_source": str(a.prompt.resolve()),
        "prompt": final,
        "references": refs,
        "params": {
            "model": a.model, "duration": a.duration, "ratio": a.ratio, "resolution": a.resolution,
            "use_context_ir": context_ir, "mute_audio": a.audio == "mute", "aigc_watermark": False,
            **({"skill_id": a.skill_id} if a.skill_id else {}),
        },
        "warnings": warnings,
    }
    request['billing']=billing(request)
    request['estimate_points']=request['billing']['total_points']
    atomic_json(a.output, request)
    a.output.with_suffix(".submit.prompt.txt").write_text(final + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"request": str(a.output), "estimate_points": request["estimate_points"],
                      "mapping": {r["label"]: r["tag"] for r in refs}, "warnings": request["warnings"]},
                     ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- budget
# approvals/budget.json is a list of batches written by the director:
#   {"batch": "EP001-试生成", "kind": "video", "unit": "points", "limit": 400, "used": 0, ...}
# Only this script changes "used". A submit charges the estimate before calling the API;
# a definite rejection or a confirmed release refunds it; query faults/unknown stay reserved.

exclusive_lock = runtime.exclusive_lock

def refund(state):
    runtime.refund(state)

def global_dir(path):
    """<漫剧>/作品/<名> → <漫剧>/；<漫剧>/ 本身原样返回。"""
    p = Path(path).resolve()
    return p if (p / "全局设置.json").exists() or p.name == "漫剧" else p.parent.parent


def settings_path(project_root):
    return global_dir(project_root) / "全局设置.json"


DEFAULT_SETTINGS = {"h3": {"max_running_tasks": 3},
                    "nexorai": {"images_cap_per_day": 50, "images_per_batch": 12, "max_running": 2},
                    "active_projects_max": 3}


def load_settings(project_root):
    f = settings_path(project_root)
    if not f.exists():
        atomic_json(f, DEFAULT_SETTINGS)                     # 第一次用时生成保守默认值；之后只有你能改
    try:
        cap = json.loads(f.read_text(encoding="utf-8"))["h3"]["max_running_tasks"]
        if not isinstance(cap, int) or cap < 1:
            raise ValueError
        return cap
    except (OSError, ValueError, KeyError, TypeError):
        fail(f"{f} 格式不对：要有 h3.max_running_tasks（正整数）；只有你能改这个文件")


def running_tasks(works_dir):
    return runtime.scan(works_dir)

def _check_concurrency(project_root, estimate):
    """检查并发与余额（调用方必须已持有 全局设置.lock）。并发满时退出码 4、余额不够时退出码 2。"""
    cap = load_settings(project_root)
    running = running_tasks(global_dir(project_root) / "作品")
    if len(running) >= cap:
        print(json.dumps({"status": "queued", "running": len(running), "cap": cap,
                          "running_tasks": running}, ensure_ascii=False, indent=2))
        print(f"全局并发已满（{len(running)}/{cap}）：本次不提交、不扣额度，这个 attempt 之后可以直接再提交。"
              f"排队中，不报错、不重试；等有任务结束后再试。", file=sys.stderr)
        raise SystemExit(QUEUE_EXIT)
    code, resp = http("GET", "/minimax/v2/query/point_usage_summary")
    if code != 200:
        # 决定 2026-11-03：余额接口失败只警告，不拦提交。余额真不够时平台会拒绝并退款。
        print(f"WARNING: balance check failed (http {code}); submitting anyway — "
              f"the platform rejects underfunded submits and refunds them", file=sys.stderr)
        return {"checked": False}
    avail = resp.get("available_points", resp.get("total_points", 0))
    if float(avail) - estimate < -1e-9:
        fail(f"余额不够：可用 {avail} - 本次预估 {estimate} < 0。请充值或减少批次额度（全局上限就是账户余额）")
    return {"checked": True, "available_points": avail}


# ---------------------------------------------------------------- submit

def data_url(path):
    p = Path(path)
    raw = p.read_bytes()
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if mime.startswith("image/") and len(raw) > 8 * MiB:
        # Large PNGs bloat the 72 MiB request; H3 downsizes short edge >1080 anyway.
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "ref.jpg"
            if shutil.which("sips"):
                cmd = ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "90", str(p), "--out", str(out)]
            else:  # Windows/Linux: FFmpeg is a project dependency anyway
                cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(p), "-q:v", "3", str(out)]
            subprocess.run(cmd, check=True, capture_output=True)
            raw, mime = out.read_bytes(), "image/jpeg"
    family = mime.split("/")[0]
    limit = {"video": 50, "audio": 15}.get(family, 30)
    if len(raw) > limit * MiB:
        fail(f"{p.name} is {len(raw) / MiB:.1f} MiB; limit {limit} MiB")
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def build_body(request):
    content = [{"type": "text", "text": request["prompt"]}]
    role_map = {"reference": "reference_image", "first_frame": "first_frame", "last_frame": "last_frame",
                "reference_video": "reference_video", "reference_audio": "reference_audio"}
    for r in request["references"]:
        k = kind_of(r)
        field = {"image": "image_url", "video": "video_url", "audio": "audio_url"}[k]
        content.append({"type": field, field: {"url": data_url(r["path"])}, "role": role_map[r["role"]]})
    body = {"content": content, **request["params"]}
    size = len(json.dumps(body).encode())
    if size > 72 * MiB:
        fail(f"request body {size / MiB:.1f} MiB exceeds 72 MiB; use fewer/smaller references")
    return body


def project_of(task_dir):
    """Task dirs live at <project>/tasks/<task name>/attempt-NNN; return <project>."""
    t = Path(task_dir).resolve()
    if not re.fullmatch(r"attempt-\d{3,}", t.name) or t.parent.parent.name != "tasks":
        fail("--task-dir must be <project>/tasks/<task name>/attempt-NNN")
    return t.parent.parent.parent


def cmd_submit(a):
    request = json.loads(a.request.read_text(encoding="utf-8"))
    if request.get("provider") != "minimax-h3":
        fail("not an H3 request")
    current_billing=billing(request)
    if request.get('estimate_points')!=current_billing['total_points']:
        fail('request estimate is stale or incomplete; prepare a new request with the full H3 tariff')
    request['billing']=current_billing
    for r in request["references"]:
        if not Path(r["path"]).is_file() or sha256_file(r["path"]) != r["sha256"]:
            fail("reference changed since prepare: " + r["label"] + "; prepare a new request")
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    preview = {"preview": not a.execute, "request_sha256": digest, "estimate_points": request["estimate_points"],
               "params": request["params"], "references": [(r["label"], r["tag"], r["role"]) for r in request["references"]],
               "warnings": request.get("warnings", [])}
    if not a.execute:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return

    task, state_path = a.task_dir, a.task_dir / "state.json"
    if not a.retry_unknown and not (a.budget and a.batch):
        fail("--execute needs --budget <approvals/budget.json> and --batch <name>; no unbudgeted submits")
    root = project_of(task)
    if not a.retry_unknown and Path(a.budget).resolve() != root / "approvals" / "budget.json":
        fail(f"--budget must be this project's approvals/budget.json ({root / 'approvals' / 'budget.json'}); "
             "budgets elsewhere are not accepted")
    task.mkdir(parents=True, exist_ok=True)
    if a.retry_unknown:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("status") != "unknown" or state.get("request_sha256") != digest:
            fail("--retry-unknown only for an unknown attempt of this exact request")
    else:
        with runtime.exclusive_lock(global_dir(root) / "全局设置.lock"):
            runtime.check_output(root, a.output)
            balance = _check_concurrency(root, request["estimate_points"])
            state = {"provider": "minimax-h3", "request_sha256": digest,
                     "idempotency_key": runtime.identity(task, digest), "status": "submitting",
                     "submitted_at": time.time(), "task_id": None,
                     "estimate_points": request["estimate_points"], "output": a.output and str(a.output),
                     "requested_resolution":request['params']['resolution'],"requested_duration":request['params']['duration'],
                     "requested_model":request['params']['model'],"purpose":request.get('purpose')}
            atomic_json(task / "request.json", request)
            runtime.reserve(state, task, "video", a.budget, a.batch, request["estimate_points"],
                            {"points": request["estimate_points"], "available_points": balance.get("available_points")})

    try:
        code, resp = http("POST", "/minimax/v2/video_generation", build_body(request),
                          {"Idempotency-Key": state["idempotency_key"]}, timeout=a.timeout)
        atomic_json(task / "response.json", {"http": code, "body": resp})
        if 200 <= code < 300 and resp.get("task_id"):
            state.update(status="submitted", task_id=resp["task_id"])
        elif code in (400, 401, 403, 404, 422, 429):
            state.update(status="rejected", error=resp)  # definite: nothing was created
            refund(state)
        else:
            state.update(status="unknown", error=resp)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        state.update(status="unknown", error=f"{type(e).__name__}: {e}")
    atomic_json(state_path, state)
    print(json.dumps({k: v for k, v in state.items() if k != "idempotency_key"}, ensure_ascii=False, indent=2))
    if state["status"] != "submitted":
        raise SystemExit(2)


# ---------------------------------------------------------------- query / download

def download(url, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=out.parent, prefix=out.name + ".", suffix=".part")
    os.close(fd)
    tmp = Path(name)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r, tmp.open("wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                            "-of", "json", str(tmp)], capture_output=True, text=True)
    if probe.returncode:
        tmp.unlink(missing_ok=True)
        fail("downloaded file is not decodable: " + probe.stderr[:300])
    runtime.publish_output(tmp, out)
    return json.loads(probe.stdout)


def settle(state, task):
    """成功后按平台返回的实际用量结算：作品批次把多扣的退回（决定 2026-11-03），差额记进全局账本。"""
    ch = state.get("charge")
    if not ch or ch.get("settled") or ch.get("refunded"):
        return
    usage = state.get("usage") or {}
    actual = usage.get("points")
    if actual is None:
        seconds, res = usage.get("output_seconds"), state.get("delivered_resolution")
        saved_path=Path(task)/'request.json'
        saved=json.loads(saved_path.read_text(encoding='utf-8')) if saved_path.is_file() else {}
        saved_input=(saved.get('billing') or {}).get('input_seconds')
        if saved_input is None and 'references' in saved and not any(kind_of(r)=='video' for r in saved['references']):
            saved_input=0
        input_seconds=usage.get('input_seconds',saved_input)
        context=usage.get('use_context_ir',(saved.get('params') or {}).get('use_context_ir'))
        if (res not in POINTS_PER_SECOND or type(context) is not bool
                or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0
                       for v in (seconds,input_seconds))):
            state['settlement_pending_reason']='Full billing usage unavailable; keep reservation, do not guess a refund'
            return
        actual=seconds*POINTS_PER_SECOND[res]+math.ceil(input_seconds)*(5 if res=='480P' else 10)+(5 if context else 0)
    state.pop('settlement_pending_reason',None)
    runtime.settle(state, task, actual, {"usage": usage})


def cmd_query(a):
    state_path = a.task_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["task_dir"] = str(a.task_dir)
    if not state.get("task_id"):
        fail(f"no task_id (status {state.get('status')}); for unknown use submit --retry-unknown")
    code, resp = http("GET", "/minimax/v2/query/video_generation/" + state["task_id"])
    atomic_json(a.task_dir / "query-response.json", {"http": code, "body": resp, "at": time.time()})
    task = resp.get("task") or {} if isinstance(resp, dict) else {}
    if code != 200 or not isinstance(task, dict) or not task.get("status"):
        print(json.dumps({"http": code, "body": resp}, ensure_ascii=False))
        raise SystemExit(2)
    st = task.get("status")
    if st not in TERMINAL | {"queued", "pending", "generating", "running", "submitted"}:
        fail("unrecognized platform status; reservation unchanged")
    if task.get("id", state["task_id"]) != state["task_id"]:
        fail("query returned a different task ID")
    if state.get("status") in TERMINAL and st != state["status"]:
        fail("platform status regressed or conflicts with saved terminal state; reconciliation required")
    state.update(status=st if st in TERMINAL else "pending", platform_status=st,
                 eta=task.get("estimated_remaining_seconds"), usage=task.get("usage"),
                 delivered_resolution=task.get("resolution"), error=task.get("error"))
    if st in ("failed", "blocked", "cancelled"):
        if task.get("point_status") == "released":
            refund(state)
        else:
            state["refund_pending"] = True
    if st == "succeeded":
        settle(state, a.task_dir)
    atomic_json(state_path, state)  # billing must survive download failure
    out = Path(a.output or state.get("output") or "")
    if st == "succeeded" and str(out) not in ("", ".") and not state.get("downloaded_to"):
        runtime.check_output(project_of(a.task_dir), out)
        url = (task.get("content") or {}).get("url")
        if not url:
            fail("succeeded without content.url")
        state["media"] = download(url, out)
        state["downloaded_to"] = str(out)
    atomic_json(state_path, state)
    print(json.dumps({k: state.get(k) for k in ("status", "platform_status", "eta", "delivered_resolution",
                                                "usage", "error", "downloaded_to")}, ensure_ascii=False, indent=2))
    if state.get("charge", {}).get("settlement_pending"):
        fail("actual cost exceeds approved budget; settlement pending, new submits blocked")


def cmd_set_continuity(a):
    """Replace only the contents of the continuity markers, after viewing the real tail frame."""
    text = a.prompt.read_text(encoding="utf-8")
    new = {"承接定义": a.definition, "承接开场": a.opening}
    present = {m.group(1) for m in CONT.finditer(text)}
    for name, value in new.items():
        if value is None:
            continue
        if name not in present:
            fail(f"prompt has no {{{{{name}：...}}}} marker")
        if not value.strip() or "\0" in value or "<d>" in value or "{{" in value or "}}" in value or len(value) > 600:
            fail(f"{name}: write one Chinese or English description (labels allowed), no dialogue, max 600 chars")
    if not Path(a.frame).is_file():
        fail("missing tail frame: " + str(a.frame))
    changes = []

    def sub(m):
        name, old = m.group(1), m.group(3)
        value = new.get(name)
        if value is None:
            return m.group(0)
        changes.append({"marker": name, "old": old.strip(), "new": value.strip()})
        return "{{" + name + "·已核对：" + value.strip() + "}}"
    out = CONT.sub(sub, text)
    tmp = a.prompt.with_suffix(a.prompt.suffix + ".tmp")
    tmp.write_text(out, encoding="utf-8", newline="\n")
    replace_with_retry(tmp, a.prompt)
    log = a.prompt.with_name(a.prompt.stem + ".continuity.jsonl")
    with log.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"at": time.time(), "frame": str(Path(a.frame).resolve()),
                            "frame_sha256": sha256_file(a.frame), "changes": changes}, ensure_ascii=False) + "\n")
    print(json.dumps({"updated": [c["marker"] for c in changes], "log": str(log)}, ensure_ascii=False, indent=2))


def cmd_cancel(a):
    state = json.loads((a.task_dir / "state.json").read_text(encoding="utf-8"))
    code, resp = http("DELETE", "/minimax/v2/video_generation/" + state["task_id"])
    print(json.dumps({"http": code, "body": resp}, ensure_ascii=False, indent=2))


def cmd_balance(a):
    code, resp = http("GET", "/minimax/v2/query/point_usage_summary")
    print(json.dumps({"http": code, "body": resp}, ensure_ascii=False, indent=2))


def cmd_pending(a):
    """只读、不联网：列出未结束的 H3 任务。--all 扫所有作品，否则 --project 一部。"""
    root = (a.project or Path.cwd()).resolve()
    if a.all:
        works = root / "作品" if root.name != "作品" else root      # --project 传 漫剧/ 或 漫剧/作品/ 都可以
    else:
        works = root.parent                                         # root 是 <漫剧>/作品/<名>，上一层就是 作品/
    all_tasks = running_tasks(works)
    tasks = all_tasks
    if not a.all:
        tasks = [t for t in tasks if Path(t["task_dir"]).is_relative_to(root)]
    running = [t for t in tasks if t["status"] != "unknown"]
    unknown = [t for t in tasks if t["status"] == "unknown"]
    for t in tasks:
        if t["minutes"] >= 60:
            t["over_60min"] = True                          # 报告总导演：不算失败，不重新提交（OVERRIDES §3）
    cap = runtime.read_cap(global_dir(root))
    print(json.dumps({"running": running, "unknown": unknown, "count": len(running), "cap": cap,
                      "occupied": len(all_tasks), "slots_free": max(cap - len(all_tasks), 0)}, ensure_ascii=False, indent=2))


def cmd_poll(a):
    """全局例行查询（归摄影）：对所有未结束任务各查一次，成功的下载并结算。"""
    root = (a.project or Path.cwd()).resolve()
    if a.all:
        works = root / "作品" if root.name != "作品" else root
    else:
        works = root.parent                                         # root 是 <漫剧>/作品/<名>，上一层就是 作品/
    tasks = [t for t in running_tasks(works) if Path(t["task_dir"]).is_relative_to(root)]
    results = []
    for t in tasks:
        task_dir = Path(t["task_dir"])
        if t["provider"] != "minimax-h3":
            continue
        if t["status"] == "unknown" or not t.get("task_id"):
            results.append({**t, "note": "unknown: 用 submit --retry-unknown 处理，poll 不碰它"})
            continue
        args = ["query", "--task-dir", task_dir]
        out = a.output_for(t["task_dir"]) if hasattr(a, "output_for") else None
        if out:
            args += ["--output", out]
        r = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), *map(str, args)],
                           capture_output=True, text=True, encoding="utf-8",
                           env={**os.environ, "PYTHONUTF8": "1"})
        entry = {**t, "exit": r.returncode}
        try:
            entry["result"] = json.loads(r.stdout)
        except ValueError:
            entry["error"] = (r.stderr or r.stdout).strip()[:300]
        results.append(entry)
    changed = [r for r in results if r.get("result", {}).get("status") not in ("pending", None)]
    print(json.dumps({"polled": len(results), "changed": len(changed),
                      "cap": runtime.read_cap(global_dir(root)), "results": results}, ensure_ascii=False, indent=2))


def cmd_skills(a):
    code, resp = http("GET", "/minimax/v2/skills?page_size=100")
    items = resp.get("items", [])
    print(json.dumps([{k: s.get(k) for k in ("id", "name", "description", "official")} for s in items],
                     ensure_ascii=False, indent=2))


cmd_submit = runtime.attempt_command(cmd_submit)
cmd_query = runtime.attempt_command(cmd_query)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("prepare", help="build request.json offline (no network)")
    q.add_argument("--prompt", type=Path, required=True)
    q.add_argument("--output", type=Path, required=True)
    q.add_argument("--ref", action="append", default=[], metavar="LABEL=PATH[@role]",
                   help="role: reference (default), first_frame, last_frame, reference_video, reference_audio")
    q.add_argument("--duration", type=int, required=True)
    q.add_argument("--ratio", choices=RATIOS, default="16:9")
    q.add_argument("--resolution", choices=RESOLUTIONS, default="768P")
    q.add_argument("--model", choices=MODELS, default="MiniMax-H3")
    q.add_argument("--audio", choices=("generate", "mute"), default="generate")
    q.add_argument("--context-ir", choices=("on", "off"), default="off")
    q.add_argument("--skill-id")
    q.add_argument("--allow-long", action="store_true", help="permit 16-30 s (undocumented CompShare mode)")
    q.add_argument("--purpose", default="", help="e.g. EP001/Clip00002 draft")
    q.set_defaults(fn=cmd_prepare)

    s = sub.add_parser("submit", help="preview; with --execute submit once")
    s.add_argument("--request", type=Path, required=True)
    s.add_argument("--task-dir", type=Path, required=True)
    s.add_argument("--output", type=Path, help="where query will save the finished video")
    s.add_argument("--execute", action="store_true")
    s.add_argument("--budget", type=Path, help="project approvals/budget.json (required with --execute)")
    s.add_argument("--batch", help="approved batch name in the budget file")
    s.add_argument("--retry-unknown", action="store_true")
    s.add_argument("--timeout", type=int, default=120)
    s.set_defaults(fn=cmd_submit)

    g = sub.add_parser("query", help="query once; download on success")
    g.add_argument("--task-dir", type=Path, required=True)
    g.add_argument("--output", type=Path)
    g.set_defaults(fn=cmd_query)

    k = sub.add_parser("set-continuity", help="camera bot: rewrite only the continuity sentences after viewing the real tail frame")
    k.add_argument("--prompt", type=Path, required=True)
    k.add_argument("--frame", type=Path, required=True, help="the real tail frame you looked at")
    k.add_argument("--definition", help="new text for {{承接定义：...}}")
    k.add_argument("--opening", help="new text for {{承接开场：...}}")
    k.set_defaults(fn=cmd_set_continuity)

    c = sub.add_parser("cancel"); c.add_argument("--task-dir", type=Path, required=True); c.set_defaults(fn=cmd_cancel)
    pd = sub.add_parser("pending", help="list unfinished tasks (offline)")
    pd.add_argument("--all", action="store_true", help="scan every project under <漫剧>/作品/")
    pd.add_argument("--project", type=Path, help="one project (default: cwd; with --all: the 漫剧 root)")
    pd.set_defaults(fn=cmd_pending)
    pl = sub.add_parser("poll", help="query every unfinished task once; download + settle on success")
    pl.add_argument("--all", action="store_true")
    pl.add_argument("--project", type=Path)
    pl.set_defaults(fn=cmd_poll)
    sub.add_parser("balance").set_defaults(fn=cmd_balance)
    sub.add_parser("skills").set_defaults(fn=cmd_skills)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
