#!/usr/bin/env python3
"""NexorAI 生图（美术）：离线准备请求 → 带预算批次提交一次 → 尺寸核验后交付。

依据：短剧工具/参考文档/使用NexorAI生成图片.md、调研/NexorAI生图模型调研.md、设计文档 §7.7、数据规范-v2.md。

  prepare  离线：选模型、检查图号和参考图、写 request.json（不联网）
  submit   默认只预览；--execute 才提交（必须带本作品的 approvals/budget.json 和生图批次）
  fetch    结果是 url 但下载失败时，再下载一次（不会重新生成）
  status   打印某个 attempt 的状态
  resume   额度用完后当天暂停生图；你确认额度恢复后用它提前恢复
  models   只读：列出平台可用模型（排查用）

规则：
- 密钥按顺序读：环境变量 NEXORAI_API_KEY → Windows 凭据管理器；不写进任何文件、日志或输出，base64 也不打印。
- 每个 attempt 只提交一次；同步接口没有幂等键，超时/断线算 unknown（额度保持扣除），
  重新生成要新开 attempt 并加 --after-unknown <那次的 attempt> 明确承认可能重复消耗。
- 不自动重试、不自动换模型。明确失败、被拦截、限流、额度用完都退回额度。
- 尺寸：解码后读真实宽高，严格比例才算合格；差得不多（裁掉 ≤16%）就居中裁切，否则不交付。只裁切不拉伸，原图保留。
- 不改 assets.json：美术亲自看过图后，用 tools/pipeline_state.py 标 generated。
"""
import argparse, base64, hashlib, http.client, json, os, re, shutil, socket, subprocess, sys, tempfile, time, uuid
import urllib.error, urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

for _s in (sys.stdout, sys.stderr):  # Windows 中文系统下 -I 会忽略 PYTHONUTF8，强制输出 UTF-8
    _s.reconfigure(encoding="utf-8")

TOOLS = Path(__file__).resolve().parents[3]                 # 短剧工具/
sys.path.insert(0, str(TOOLS / "tools"))
import generation_runtime as runtime

PROFILE = json.loads((TOOLS / "profiles" / "nexorai.json").read_text(encoding="utf-8"))
BASE = os.environ.get("NEXORAI_BASE_URL", PROFILE["api"]["base"]).rstrip("/")
UA = PROFILE["api"]["user_agent"]
KINDS = {"image": "character", "sheet": "character", "scene": "scene", "prop": "prop"}
ROLES = ("人物", "姿势", "服饰")
DEFAULT_SETTINGS = {"h3": {"max_running_tasks": 3},
                    "nexorai": {"images_cap_per_day": 50, "images_per_batch": 12, "max_running": 2},
                    "active_projects_max": 3}
REAL_PERSON = re.compile(r"明星|网红|名人|演员|艺人|celebrity|famous actor|looks like [A-Z][a-z]+ [A-Z]", re.I)
FIG = re.compile(r"图\s*(\d+)")


def fail(msg, code=1):
    print("ERROR: " + msg, file=sys.stderr)
    raise SystemExit(code)


def now():
    return datetime.now().astimezone()


# ---------------------------------------------------------------- 文件
def replace_with_retry(src, dst, timeout=10):
    """Windows 上目标文件正被别的进程读取时 os.replace 会失败，重试一会儿。"""
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


exclusive_lock = runtime.exclusive_lock

def inside(root, rel, label):
    if not isinstance(rel, str) or not rel:
        fail(f"{label} 为空")
    p = Path(rel)
    if p.is_absolute() or p.drive or not (root / p).resolve().is_relative_to(root):
        fail(f"{label} 必须是作品目录内的相对路径：{rel}")
    return (root / p).resolve()


# ---------------------------------------------------------------- 密钥
def windows_credential(target):
    """读 Windows 凭据管理器里的普通凭据（cmdkey /generic:<target> /user:x /pass:<key> 存的）。"""
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
    for enc in ("utf-16-le", "utf-8"):  # cmdkey 存的是 UTF-16LE
        try:
            text = blob.decode(enc).strip("\x00 \r\n")
        except UnicodeDecodeError:
            continue
        if text.isascii():
            return text
    return ""


def api_key():
    name = PROFILE["api"]["env_var"]
    key = os.environ.get(name, "").strip() or windows_credential(name)
    if not key and sys.platform == "darwin" and shutil.which("security"):
        r = subprocess.run(["security", "find-generic-password", "-s", name, "-w"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            key = r.stdout.strip()
    if not key:
        fail(f"{name} 没找到（环境变量、Windows 凭据管理器或 macOS 钥匙串）")
    return key


# ---------------------------------------------------------------- 图片（用 FFmpeg，免装第三方库）
def need_ffmpeg():
    for b in ("ffmpeg", "ffprobe"):
        if not shutil.which(b):
            fail(f"找不到 {b}：请确认 FFmpeg 已安装并在 PATH 里（装完要重启终端或应用）")


def probe(path):
    """完整解码一遍确认图片能打开，返回 (宽, 高)；打不开返回 None。"""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "json", str(path)], capture_output=True, text=True)
    try:
        s = json.loads(r.stdout)["streams"][0]
        w, h = int(s["width"]), int(s["height"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    d = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    if d.returncode != 0 or d.stderr.strip():
        return None
    return w, h


def ffmpeg_out(src, dst, vf=None, extra=()):
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dst.parent, suffix=dst.suffix)
    os.close(fd)
    try:
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src)] + (["-vf", vf] if vf else []) + list(extra) + \
              ["-frames:v", "1", "-update", "1", tmp]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not Path(tmp).stat().st_size:
            fail(f"图片处理失败：{src.name}\n{r.stderr.strip()[:500]}")
        replace_with_retry(tmp, dst)
    finally:
        Path(tmp).unlink(missing_ok=True)


def ratio_of(text):
    a, b = (int(x) for x in text.split(":"))
    return a, b


def crop_plan(w, h, ratio, max_loss):
    """返回 (动作, 裁切框, 损失)：exact / crop / recompose。"""
    rw, rh = ratio_of(ratio)
    if w * rh == h * rw:
        return "exact", (w, h, 0, 0), 0.0
    k = min(w // rw, h // rh)
    if k <= 0:
        return "recompose", None, 1.0
    cw, ch = rw * k, rh * k
    loss = 1 - (cw * ch) / (w * h)
    if loss > max_loss + 1e-9:
        return "recompose", (cw, ch, (w - cw) // 2, (h - ch) // 2), loss
    return "crop", (cw, ch, (w - cw) // 2, (h - ch) // 2), loss


def sniff_ext(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".bin"


# ---------------------------------------------------------------- 作品、全局设置、账本
def load_project(root):
    try:
        pj = json.loads((root / "project.json").read_text(encoding="utf-8"))
        assets = json.loads((root / "assets.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        fail(f"读不了作品清单：{e}")
    return pj, assets


def global_dir(project_root):
    return project_root.parent.parent                      # 漫剧/作品/<作品名> → 漫剧/


def load_settings(project_root):
    f = global_dir(project_root) / "全局设置.json"
    if not f.exists():
        atomic_json(f, DEFAULT_SETTINGS)                    # 第一次用时按保守默认值生成；之后只有你能改
    try:
        s = json.loads(f.read_text(encoding="utf-8"))["nexorai"]
        return int(s["images_cap_per_day"]), int(s["images_per_batch"])
    except (OSError, ValueError, KeyError, TypeError):
        fail(f"{f} 格式不对：要有 nexorai.images_cap_per_day 和 nexorai.images_per_batch")


QUEUE_EXIT = 4   # "排队中，稍后再试"——对 bot 不是错误，attempt 原样保留


def load_concurrency_cap(project_root):
    f = global_dir(project_root) / "全局设置.json"
    try:
        cap = json.loads(f.read_text(encoding="utf-8"))["nexorai"]["max_running"]
        if not isinstance(cap, int) or cap < 1:
            raise ValueError
        return cap
    except (OSError, ValueError, KeyError, TypeError):
        fail(f"{f} 格式不对：要有 nexorai.max_running（正整数）；只有你能改这个文件")


def running_images(works_dir):
    return runtime.scan(works_dir, {"nexorai"})


def ledger_path(project_root):
    return global_dir(project_root) / "全局账本.jsonl"


def ledger_read(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def ledger_add(path, entry):
    entry = {"time": now().isoformat(timespec="seconds"), "provider": "nexorai", **entry}
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def today_used(entries, day):
    used = 0
    for e in entries:
        if e.get("provider") == "nexorai" and e.get("date") == day:
            used += e.get("images", 0) if e.get("event") == "charge" else -e.get("images", 0) if e.get("event") == "refund" else 0
    return used


def paused_today(entries, day, model=None, project=None):
    last = None
    for e in entries:
        if e.get("provider") == "nexorai" and e.get("date") == day and e.get("event") in ("quota_exhausted", "resume"):
            if e.get("model") is not None and e["model"] != model:
                continue
            if e["event"] == "resume" and e.get("model") is not None and e.get("project") != project:
                continue
            last = e["event"]
    return last == "quota_exhausted"


def choose_model(pj, purpose, a):
    genre = PROFILE["genre_alias"].get(pj.get("genre"), pj.get("genre"))
    table = PROFILE["priority"].get(genre)
    if not table:
        fail(f"profiles/nexorai.json 里没有题材 {pj.get('genre')!r} 的模型优先级")
    ranked = table[purpose]
    if a.model:
        if not a.reason:
            fail("用指定模型（--model）必须写 --reason，说明为什么不用题材配置里的模型")
        return a.model, {"source": "override", "reason": a.reason, "priority": ranked}
    if not 1 <= a.rank <= len(ranked):
        fail(f"--rank 只能是 1–{len(ranked)}")
    return ranked[a.rank - 1], {"source": f"genres:{genre}/{purpose}#{a.rank}", "priority": ranked}


def cmd_prepare(a):
    root = a.project.resolve()
    pj, assets = load_project(root)
    asset = next((x for x in assets if x.get("id") == a.asset), None)
    if not asset:
        fail(f"assets.json 里没有 {a.asset}")
    if KINDS[a.kind] != asset.get("type"):
        fail(f"{a.asset} 是 {asset.get('type')}，不能出 {a.kind} 图")
    target_field = "sheet" if a.kind == "sheet" else "image"
    target = asset.get(target_field)
    if not target:
        fail(f"{a.asset} 的 {target_field} 还没有计划路径：请编剧先在 assets.json 里填上")
    inside(root, target, f"{a.asset}.{target_field}")
    ratio = pj.get("character_sheet_ratio", "16:9") if a.kind == "sheet" else pj.get("ratio")
    if not ratio or not re.fullmatch(r"\d+:\d+", ratio):
        fail(f"作品画幅不对：{ratio!r}")
    refs = []
    for item in a.ref:
        role, sep, path = item.partition("=")
        if not sep or role not in ROLES:
            fail(f"--ref 要写成 人物=路径 / 姿势=路径 / 服饰=路径：{item}")
        p = Path(path).expanduser().resolve()
        if not p.is_file() or not p.stat().st_size:
            fail(f"参考图不存在或为空：{path}")
        refs.append({"n": len(refs) + 1, "role": role, "path": str(p), "sha256": sha256_file(p)})
    if len(refs) > PROFILE["max_references"]:
        fail(f"参考图最多 {PROFILE['max_references']} 张")
    if a.kind == "sheet" and not any(r["role"] == "人物" for r in refs):
        fail("三视图要以这个角色的视频参考图为参考：加 --ref 人物=<视频参考图>")
    purpose = "scene" if a.kind == "scene" else "edit" if refs else "reference"
    model, choice = choose_model(pj, purpose, a)
    is_grok = model.startswith("grok")
    if not is_grok and ratio not in PROFILE["gpt_sizes"]:
        fail(f"GPT 模型只用于 {'/'.join(PROFILE['gpt_sizes'])}（横图比例不可控）；这张要 {ratio}，请换 Grok 模型")
    if not 1 <= a.n <= PROFILE["max_images_per_request"]:
        fail(f"--n 只能是 1–{PROFILE['max_images_per_request']}")

    prompt = a.prompt.read_text(encoding="utf-8").strip()
    if not prompt:
        fail("提示词是空的")
    used = sorted({int(x) for x in FIG.findall(prompt)})
    bad = [n for n in used if not 1 <= n <= len(refs)]
    if bad:
        fail(f"提示词里写了图{bad}，但只附了 {len(refs)} 张参考图；有几张图就编几个号")
    warnings = []
    unused = [r["n"] for r in refs if r["n"] not in used]
    if unused:
        warnings.append(f"附了但提示词里没提到：图{unused}；三参考要在提示词里写清每张图负责什么")
    if PROFILE["genre_alias"].get(pj.get("genre"), pj.get("genre")) == "live-action" and REAL_PERSON.search(prompt):
        warnings.append("写实题材：提示词里像是提到了真人/明星，角色不能像真实存在的人（genres/live-action.md §3）")
    if choice["source"] == "override":
        warnings.append(f"没用题材配置里的模型：{choice['reason']}")
    if purpose == "edit" and len(refs) >= 3 and is_grok:
        warnings.append("三张参考一起融合：Grok 能否按分工执行还没验证（统一验证项）")

    endpoint = "/images/edits" if refs else "/images/generations"
    params = {"model": model, "n": a.n}
    if is_grok:
        params["aspect_ratio"] = ratio                     # Grok 必须传，否则出横图；size 无效
    else:
        params.update(size=PROFILE["gpt_sizes"][ratio], quality="high")
        if endpoint == "/images/generations":
            params["output_format"] = "png"
    request = {
        "schema_version": 1, "provider": "nexorai", "project": str(root), "asset": a.asset, "kind": a.kind,
        "purpose": purpose, "endpoint": endpoint, "params": params, "prompt": prompt,
        "prompt_source": str(a.prompt.resolve()), "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "references": refs, "target": target, "target_field": target_field, "ratio": ratio,
        "model_choice": choice, "estimate_images": a.n, "warnings": warnings,
        "prepared_at": now().isoformat(timespec="seconds"),
    }
    out = a.output
    if out.exists():
        fail("request 文件已存在：换一个文件名（每份请求只准备一次）")
    atomic_json(out, request)
    print(json.dumps({"request": str(out), "model": model, "endpoint": endpoint, "ratio": ratio,
                      "references": [f"图{r['n']}={r['role']}" for r in refs], "estimate_images": a.n,
                      "warnings": warnings}, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- submit
def task_layout(task_dir):
    t = Path(task_dir).resolve()
    if not re.fullmatch(r"attempt-\d{3,}", t.name) or t.parent.parent.name != "tasks":
        fail("--task-dir 必须是 <作品>/tasks/<任务名>/attempt-NNN")
    return t, t.parent.parent.parent


def multipart(fields, files):
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for name, path in files:
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="image[]"; filename="{name}"\r\n'
                     f'Content-Type: image/jpeg\r\n\r\n'.encode() + Path(path).read_bytes() + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def classify_error(code, text):
    t = text.lower()
    if code == 402 or re.search(r"quota|insufficient|balance|credit|额度|余额|配额", t):
        return "quota_exhausted"
    if re.search(r"content.?policy|moderation|safety|sensitive|violat|blocked|违规|审核|敏感", t):
        return "blocked"
    if code == 429:
        return "rate_limited"
    return "failed"


def scrub(o):
    if isinstance(o, dict):
        return {k: ("<base64 已省略>" if k == "b64_json" else scrub(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    if isinstance(o, str) and len(o) > 500:
        return o[:120] + f"...<{len(o)} chars>"
    return o


def download(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def deliver(state, root, task):
    """把已下载的原图逐张核验、裁切、转格式，写到目标路径。返回最终状态。"""
    need_ffmpeg()
    request = json.loads((task / "request.json").read_text(encoding="utf-8"))
    target = inside(root, request["target"], "交付路径")
    results, ok = [], True
    for i, orig in enumerate(state["originals"], 1):
        src = task / orig
        size = probe(src)
        if not size:
            results.append({"original": orig, "status": "bad_image"})
            ok = False
            continue
        w, h = size
        action, box, loss = crop_plan(w, h, request["ratio"], PROFILE["crop"]["max_loss"])
        dst = target if i == 1 else target.with_name(f"{target.stem}-候选{i}{target.suffix}")
        rec = {"original": orig, "original_size": [w, h], "action": action, "loss": round(loss, 4)}
        if action == "recompose":
            rec["status"] = "needs_recompose"
            ok = False
        else:
            cw, ch, x, y = box
            ffmpeg_out(src, dst, vf=None if action == "exact" else f"crop={cw}:{ch}:{x}:{y}",
                       extra=("-q:v", "2") if dst.suffix.lower() in (".jpg", ".jpeg") else ())
            final = probe(dst)
            rw, rh = ratio_of(request["ratio"])
            if not final or final[0] * rh != final[1] * rw:
                fail(f"交付文件核验失败：{dst}")
            rec.update(status="delivered", delivered=dst.relative_to(root).as_posix(), final_size=list(final),
                       sha256=sha256_file(dst))
            if action == "crop":
                rec["note"] = "居中裁切过：美术要确认主体没被切到"
        results.append(rec)
    state["deliveries"] = results
    if any(r["status"] == "delivered" for r in results):
        state["status"] = "succeeded" if ok else "partly_delivered"
    else:
        state["status"] = "needs_recompose" if any(r["status"] == "needs_recompose" for r in results) else "bad_image"
    return state


def save_images(resp, task, timeout):
    """从返回里取图（b64_json 或 url），存成 original-N.*。url 下载失败的记下来，可以用 fetch 再下。"""
    originals, pending = [], []
    for i, item in enumerate(resp.get("data") or [], 1):
        data = None
        if item.get("b64_json"):
            try:
                data = base64.b64decode(item["b64_json"])
            except ValueError:
                data = b""
        elif item.get("url"):
            try:
                data = download(item["url"], timeout)
            except (urllib.error.URLError, OSError, http.client.HTTPException):
                pending.append(item["url"])
                continue
        else:
            continue
        name = f"original-{i}{sniff_ext(data)}"
        (task / name).write_bytes(data)
        originals.append(name)
    return originals, pending


def refund(state, root, entries_path):
    runtime.refund(state)


def cmd_submit(a):
    request = json.loads(a.request.read_text(encoding="utf-8"))
    if request.get("provider") != "nexorai":
        fail("不是 NexorAI 的请求文件")
    task, root = task_layout(a.task_dir)
    if root != Path(request["project"]).resolve():
        fail("任务目录和请求不属于同一部作品")
    for r in request["references"]:
        if not Path(r["path"]).is_file() or sha256_file(r["path"]) != r["sha256"]:
            fail(f"参考图 图{r['n']} 在 prepare 之后变了；请重新 prepare")
    preview = {"preview": not a.execute, "model": request["params"]["model"], "endpoint": request["endpoint"],
               "params": request["params"], "references": [f"图{r['n']}={r['role']}" for r in request["references"]],
               "target": request["target"], "estimate_images": request["estimate_images"],
               "warnings": request["warnings"]}
    if not a.execute:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return
    if not (a.budget and a.batch):
        fail("--execute 必须带 --budget <作品>/approvals/budget.json 和 --batch <生图批次>；没有批次不能提交")
    budget = Path(a.budget).resolve()
    if budget != root / "approvals" / "budget.json":
        fail(f"--budget 必须是这部作品的 approvals/budget.json（{root / 'approvals' / 'budget.json'}）")
    # 同一任务里有结果不明的 attempt：必须明确承认才能再提交
    for sib in sorted(task.parent.glob("attempt-*")):
        sp = sib / "state.json"
        if sib != task and sp.exists() and json.loads(sp.read_text(encoding="utf-8")).get("status") == "unknown" \
                and sib.name not in a.after_unknown:
            fail(f"{sib.name} 的结果不明（平台可能已经出了图、扣了额度）。确认要重新生成，"
                 f"加 --after-unknown {sib.name}（可能重复消耗额度）")
    cap_day, cap_batch = load_settings(root)
    task.mkdir(parents=True, exist_ok=True)
    n = request["estimate_images"]
    if request['params'].get('n',1)!=n:
        fail('prepared image count differs from the actual request; prepare again')
    lp = ledger_path(root)
    day = now().date().isoformat()
    state = {"provider": "nexorai", "task_dir": str(task), "status": "submitting", "request_sha256":
             sha256_file(a.request), "submitted_at": now().isoformat(timespec="seconds"),
             "after_unknown": a.after_unknown, "model": request["params"]["model"]}
    # 并发检查、占位、扣额度、写状态、记账在同一把全局锁里完成：
    # 否则两个提交可能同时通过并发检查——各自扫到的"运行中"列表都还没算上对方。
    # 锁文件是 全局设置.lock（专用文件），不锁 全局设置.json 本身，读设置不会被挡。
    with exclusive_lock(str(global_dir(root) / "全局设置.lock"), timeout=60):
        cap_run = load_concurrency_cap(root)
        running = running_images(global_dir(root) / "作品")
        if len(running) >= cap_run:
            print(json.dumps({"status": "queued", "running": len(running), "cap": cap_run,
                              "running_tasks": running}, ensure_ascii=False, indent=2))
            print(f"全局生图并发已满（{len(running)}/{cap_run}）：本次不提交、不扣额度，这个 attempt 之后可以直接再提交。"
                  f"排队中，不报错、不重试；等有生图结束后再试。", file=sys.stderr)
            raise SystemExit(QUEUE_EXIT)
        entries = ledger_read(lp)
        if paused_today(entries, day, request["params"]["model"], root.name):
            fail("今天 NexorAI 返回过额度用完，生图已暂停；额度恢复后由你运行 resume")
        if today_used(entries, day) + n > cap_day:
            fail(f"每天上限 {cap_day} 已满")
        with runtime.exclusive_lock(str(budget) + ".lock"):
            batches, batch = runtime.validate_budget(budget, a.batch, "image", n)
            if int(batch["limit"]) > cap_batch:
                    fail(f"单批上限 {cap_batch}；请拆成更小的批次")
        atomic_json(task / "request.json", request)
        runtime.reserve(state, task, "image", budget, a.batch, n, {"images": n, "date": day})

    p = request["params"]
    try:
        if request["endpoint"] == "/images/edits":
            need_ffmpeg()
            files = []
            for r in request["references"]:
                jpg = task / "upload" / f"图{r['n']}.jpg"
                ffmpeg_out(Path(r["path"]), jpg, extra=("-q:v", "2"))       # 参考图先压成 JPG 再上传
                files.append((jpg.name, jpg))
            body, ctype = multipart({**{k: str(v) for k, v in p.items()}, "prompt": request["prompt"]}, files)
        else:
            body, ctype = json.dumps({**p, "prompt": request["prompt"]}).encode(), "application/json"
        req = urllib.request.Request(BASE + request["endpoint"], data=body, method="POST",
                                     headers={"Authorization": "Bearer " + api_key(), "Content-Type": ctype,
                                              "User-Agent": UA, "Accept": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=a.timeout) as r:
            raw = r.read()
        state["seconds"] = round(time.time() - t0, 1)
        resp = json.loads(raw)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")[:2000]
        state.update(status=classify_error(e.code, text), http=e.code, error=text[:500])
        if e.code == 429 and e.headers.get("Retry-After"):
            state["retry_after"] = e.headers.get("Retry-After")
        if e.code not in (400, 401, 402, 403, 404, 422, 429):
            state.update(status="unknown", error="ambiguous HTTP response; reservation retained")
            atomic_json(task / "state.json", state)
            fail("结果不明：额度保持扣除，不自动重新提交", 2)
        refund(state, root, lp)
        if state["status"] == "quota_exhausted":
            with exclusive_lock(global_dir(root) / "全局账本.lock"):
                ledger_add(lp, {"event": "quota_exhausted", "date": day, "project": root.name, "task_dir": str(task)})
        atomic_json(task / "state.json", state)
        print(json.dumps({k: state.get(k) for k in ("status", "http", "error", "retry_after")}, ensure_ascii=False, indent=2))
        hint = {"quota_exhausted": "NexorAI 额度用完：今天的生图已全部暂停，请报告总导演找你处理",
                "blocked": "内容被拦截：额度已退回，交给总导演，由你决定怎么改提示词",
                "rate_limited": "被限流：额度已退回，按 retry_after 等一会儿再新开 attempt，不要连续重试",
                "failed": "明确失败：额度已退回，报告总导演；不自动重试、不自动换模型"}[state["status"]]
        fail(hint, 3)
    except urllib.error.URLError as e:
        if isinstance(e.reason, ConnectionRefusedError):            # 请求没发出去：可以放心退回
            state.update(status="rejected", error=f"连接被拒绝：{e.reason}")
            refund(state, root, lp)
            atomic_json(task / "state.json", state)
            fail("连不上 NexorAI（请求没发出去），额度已退回", 3)
        state.update(status="unknown", error=f"{type(e.reason).__name__}: {e.reason}")
        atomic_json(task / "state.json", state)
        fail("结果不明（超时或断线）：平台可能已经出图并扣了额度，额度保持扣除。不要直接重试；"
             "确认要重新生成时新开 attempt 并加 --after-unknown " + task.name, 2)
    except (socket.timeout, TimeoutError, ConnectionError, http.client.HTTPException) as e:
        state.update(status="unknown", error=f"{type(e).__name__}: {e}")
        atomic_json(task / "state.json", state)
        fail("结果不明（超时或断线）：额度保持扣除；确认要重新生成时新开 attempt 并加 --after-unknown " + task.name, 2)
    except ValueError as e:
        state.update(status="unknown", error=f"返回不是 JSON：{e}")
        atomic_json(task / "state.json", state)
        fail("返回无法解析，结果不明：额度保持扣除，先排查", 2)

    atomic_json(task / "response.json", scrub(resp))
    originals, pending = save_images(resp, task, a.timeout)
    state["originals"], state["pending_urls"] = originals, pending
    if not originals and not pending:
        state.update(status="unknown", error="返回里没有图片，无法证明平台未计费")
        atomic_json(task / "state.json", state)
        fail("返回里没有图片：额度保持扣除，报告总导演", 3)
    if pending:
        state["status"] = "download_failed"
    if originals:
        deliver(state, root, task)
        if pending:
            state["status"] = "partly_delivered"
    atomic_json(task / "state.json", state)
    report(state, request, root)


def report(state, request, root):
    out = {k: state.get(k) for k in ("status", "model", "seconds", "deliveries", "pending_urls")}
    nxt = []
    if state["status"] in ("succeeded", "partly_delivered"):
        nxt.append("亲自看图：脸型、服装、全身、四肢、背景、和其他形态是否一致；裁切过的确认主体完整")
        nxt.append(f"通过自检后：python 短剧工具/tools/pipeline_state.py --project <作品> --role 美术 set-asset "
                   f"{request['asset']} --set status=generated")
    elif state["status"] == "needs_recompose":
        nxt.append("比例差太多，裁切会丢太多画面：原图已保留，没有交付。请改构图或换模型（经总导演同意）后新开 attempt")
    elif state["status"] == "download_failed":
        nxt.append("图已生成但下载失败：用 fetch --task-dir <这个 attempt> 再下载（不会重新生成）")
    elif state["status"] == "bad_image":
        nxt.append("返回的图片打不开：额度已消耗，报告总导演")
    out["next"] = nxt
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_fetch(a):
    task, root = task_layout(a.task_dir)
    state = json.loads((task / "state.json").read_text(encoding="utf-8"))
    if not state.get("pending_urls"):
        fail("这个 attempt 没有待下载的图片")
    request = json.loads((task / "request.json").read_text(encoding="utf-8"))
    start = len(state.get("originals", [])) + 1
    still = []
    for i, url in enumerate(state["pending_urls"], start):
        try:
            data = download(url, a.timeout)
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            still.append(url)
            continue
        name = f"original-{i}{sniff_ext(data)}"
        (task / name).write_bytes(data)
        state["originals"].append(name)
    state["pending_urls"] = still
    if state["originals"]:
        deliver(state, root, task)
        if still:
            state["status"] = "partly_delivered"
    else:
        state["status"] = "download_failed"
    atomic_json(task / "state.json", state)
    report(state, request, root)


def cmd_status(a):
    task, _ = task_layout(a.task_dir)
    print((task / "state.json").read_text(encoding="utf-8"))


def cmd_resume(a):
    root = a.project.resolve()
    lp = ledger_path(root)
    day = now().date().isoformat()
    with exclusive_lock(global_dir(root) / "全局账本.lock"):
        if not paused_today(ledger_read(lp), day, a.model, root.name):
            print(json.dumps({"resumed": False, "note": "今天没有暂停"}, ensure_ascii=False))
            return
        ledger_add(lp, {"event": "resume", "date": day, "project": root.name, "note": a.note, **({"model": a.model} if a.model else {})})
    print(json.dumps({"resumed": True, "date": day}, ensure_ascii=False))


def cmd_models(a):
    req = urllib.request.Request(BASE + "/models", headers={"Authorization": "Bearer " + api_key(), "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        fail(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    ids = sorted(m.get("id", "") for m in data.get("data", []))
    print(json.dumps({"count": len(ids), "image_models": [i for i in ids if re.search(r"image", i)]},
                     ensure_ascii=False, indent=2))


cmd_submit = runtime.attempt_command(cmd_submit)
cmd_fetch = runtime.attempt_command(cmd_fetch)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("prepare", help="离线准备请求（不联网）")
    q.add_argument("--project", type=Path, required=True)
    q.add_argument("--asset", required=True, help="资产编号，例如 C00001-A")
    q.add_argument("--kind", choices=tuple(KINDS), required=True, help="image=视频参考图 sheet=三视图 scene=场景 prop=道具")
    q.add_argument("--prompt", type=Path, required=True)
    q.add_argument("--output", type=Path, required=True, help="request.json 存到哪里")
    q.add_argument("--ref", action="append", default=[], metavar="角色=路径", help="人物=… / 姿势=… / 服饰=…，按顺序编号为图1、图2…")
    q.add_argument("--rank", type=int, default=1, help="用题材配置里第几个模型（默认 1）")
    q.add_argument("--model", help="不用题材配置的模型时指定（必须带 --reason）")
    q.add_argument("--reason")
    q.add_argument("--n", type=int, default=1, help="出几张（1–2）")
    q.set_defaults(fn=cmd_prepare)
    s = sub.add_parser("submit", help="默认预览；--execute 才提交一次")
    s.add_argument("--request", type=Path, required=True)
    s.add_argument("--task-dir", type=Path, required=True)
    s.add_argument("--execute", action="store_true")
    s.add_argument("--budget", type=Path)
    s.add_argument("--batch")
    s.add_argument("--after-unknown", action="append", default=[], metavar="attempt-NNN")
    s.add_argument("--timeout", type=float, default=PROFILE["api"]["timeout_seconds"])
    s.set_defaults(fn=cmd_submit)
    f = sub.add_parser("fetch", help="url 下载失败时再下载（不重新生成）")
    f.add_argument("--task-dir", type=Path, required=True)
    f.add_argument("--timeout", type=float, default=120)
    f.set_defaults(fn=cmd_fetch)
    t = sub.add_parser("status")
    t.add_argument("--task-dir", type=Path, required=True)
    t.set_defaults(fn=cmd_status)
    r = sub.add_parser("resume", help="额度恢复后，由你提前恢复今天的生图")
    r.add_argument("--project", type=Path, required=True)
    r.add_argument("--note", default="")
    r.add_argument("--model", help="仅恢复指定模型；受控入口要求另行明确批准")
    r.set_defaults(fn=cmd_resume)
    sub.add_parser("models", help="只读：列出平台可用模型").set_defaults(fn=cmd_models)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
