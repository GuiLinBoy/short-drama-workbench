#!/usr/bin/env python3
"""旁白合成（剪辑）：把 clips.json 里的旁白逐条交给优云智算 IndexTTS 2.5，下载 WAV 并写回实际时长。

依据：设计文档 §8.5.5、数据规范-v2.md §3（narration 字段）、接口文档
<https://www.compshare.cn/docs/minimax-h3/api/text-to-speech-api>（2026-11-05 核对）。

  prepare  离线：读 clips.json --episode 里所有待配的旁白，写 request.json（不联网）
  submit   默认只预览；--execute 才创建平台任务（必须带本作品的 approvals/budget.json 和 speech 批次）
  query    查一次任务，成功后下载 WAV、写回 narration.audio 和 narration_duration
  status   打印某个 attempt 的状态
  voices   只读：列出平台预设音色（立项时挑旁白音色用）

规则：
- 和 H3 共用同一把 sk-ml- 密钥和积分池（钥匙串/凭据管理器 MINIMAX_H3_API_KEY），每 100 字 1 积分。
- 每条旁白一个 attempt 目录 tasks/EP001-旁白/attempt-NNN，只提交一次；幂等键包含作品绝对路径、任务/attempt 和请求哈希，
  超时/断线算 unknown，只能 --retry-unknown（同一个键），不能新开 attempt 重复提交。
- 平台计费是"先冻结后结算"（point_status: reserved → consumed/released）：
  确认 point_status=released 才退回批次额度；成功按返回费用结算；查询故障不退款。
- 并发检查用全局设置.json 的 h3.max_running_tasks 同一名额池（旁白和视频共享全局并发与余额）。
- 旁白实际时长写 narration_duration（只此字段，由剪辑写）；超过 reserved 提醒，超过 1 秒上报总导演。
- 密钥不写进任何文件、日志或输出。
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request
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

MiB = 1024 * 1024
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generation_runtime as runtime
import pipeline_state

BASE = os.environ.get("H3_BASE_URL", "https://cp.compshare.cn").rstrip("/")
UA = "novel-to-video-narration/1.0"
QUEUE_EXIT = 4   # "排队中，稍后再试"——对 bot 不是错误，attempt 原样保留

# ------------------------------------------------------------------ 共用 runtime：锁、原子写入与可恢复计费


def fail(msg, code=2):
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


exclusive_lock = runtime.exclusive_lock

def replace_with_retry(src, dst, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


atomic_json = runtime.atomic_json

def cred_read_windows(target):
    if os.name != "nt":
        return None
    import ctypes, ctypes.wintypes as wt
    advapi = ctypes.windll.advapi32

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
                    ("Comment", wt.LPWSTR), ("LastWritten", wt.FILETIME), ("CredentialBlobSize", wt.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wt.DWORD),
                    ("AttributeCount", wt.DWORD), ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR),
                    ("UserName", wt.LPWSTR)]
    advapi.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    advapi.CredReadW.restype = wt.BOOL
    cred = ctypes.POINTER(CREDENTIAL)()
    if not advapi.CredReadW(target, 1, 0, ctypes.byref(cred)):
        return None
    try:
        blob = ctypes.string_at(cred.contents.CredentialBlob, cred.contents.CredentialBlobSize)
        return blob.decode("utf-16-le").strip() if cred.contents.CredentialBlobSize % 2 == 0 else blob.decode("utf-8", "replace").strip()
    finally:
        ctypes.windll.kernel32.LocalFree(cred.contents.CredentialBlob)


def api_key():
    k = os.environ.get("MINIMAX_H3_API_KEY", "").strip()
    if k and k.startswith("sk-ml-"):
        return k
    k = cred_read_windows("MINIMAX_H3_API_KEY")
    if k and k.startswith("sk-ml-"):
        return k
    if sys.platform == "darwin" and shutil.which("security"):
        r = subprocess.run(["security", "find-generic-password", "-s", "MINIMAX_H3_API_KEY", "-w"],
                           capture_output=True, text=True)
        k = r.stdout.strip() if r.returncode == 0 else ""
        if k and k.startswith("sk-ml-"):
            return k
    fail("MINIMAX_H3_API_KEY not found (env, Windows Credential Manager or macOS Keychain) or not an H3 key (sk-ml-...)")


def http(method, path, body=None, headers=None, timeout=60):
    req = urllib.request.Request(BASE + path, method=method, data=body,
                                 headers={"Authorization": "Bearer " + api_key(), "User-Agent": UA,
                                          **(headers or {})})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ConnectionError(f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------ 全局层（和 h3_task.py 一致的全局文件）


def global_dir(path):
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
        atomic_json(f, DEFAULT_SETTINGS)
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
    """检查并发与余额（调用方必须已持有 全局设置.lock）。并发满退出码 4、余额不够退出码 2。"""
    cap = load_settings(project_root)
    running = running_tasks(global_dir(project_root) / "作品")
    if len(running) >= cap:
        print(json.dumps({"status": "queued", "running": len(running), "cap": cap,
                          "running_tasks": running}, ensure_ascii=False, indent=2))
        print(f"全局并发已满（{len(running)}/{cap}）：本次不提交、不扣额度，这个 attempt 之后可以直接再提交。"
              f"排队中，不报错、不重试；等有任务结束后再试。", file=sys.stderr)
        raise SystemExit(QUEUE_EXIT)
    code, resp = http("GET", "/audio/v1/pricing")
    if code != 200 or not resp.get("enabled"):
        print(f"WARNING: pricing check failed (http {code}); submitting anyway", file=sys.stderr)
        return {"checked": False}
    avail = (resp.get("balance") or {}).get("available_points", 0)
    if float(avail) - estimate < -1e-9:
        fail(f"余额不够：可用 {avail} - 本次预估 {estimate} < 0。请充值（全局上限就是账户余额）")
    return {"checked": True, "available_points": avail}


def refund(state):
    runtime.refund(state)

def settle(state, root):
    actual = state.get("point_cost")
    if actual is not None:
        runtime.settle(state, state["task_dir"], actual,
                       {"billable_characters": state.get("billable_characters")})

def unit_price():
    code, resp = http("GET", "/audio/v1/pricing")
    if code != 200 or not resp.get("enabled"):
        fail(f"pricing 接口不可用（http {code}），无法估算积分")
    return float(resp["costs"]["text_to_speech"]) / float(resp.get("unit_characters", 100)), resp


def cmd_prepare(a):
    root = Path(a.project).resolve()
    clips = json.loads((root / "clips.json").read_text(encoding="utf-8"))
    pj = json.loads((root / "project.json").read_text(encoding="utf-8"))
    narr = pj.get("narration") or {}
    if not narr.get("provider") or not narr.get("voice_id"):
        fail("project.json 没有 narration.provider / narration.voice_id；先由总导演立项时定音色")
    todo = []
    for c in clips:
        if a.episode and c.get("episode") != a.episode:
            continue
        for n in c.get("narration") or []:
            if not n.get("text"):
                continue
            if n.get("audio") and Path(a.project, n["audio"]).is_file() and not a.redo:
                continue                                          # 已配好，跳过
            todo.append({"clip_id": c["id"], "entry_id": n["id"], "text": n["text"],
                         "start": n.get("start", 0), "reserved": n.get("reserved"),
                         "existing_audio": n.get("audio")})
    if not todo:
        print(json.dumps({"todo": 0, "note": "没有待配的旁白（都已有 audio，或这一集没有旁白）"}, ensure_ascii=False))
        return
    catalog_path = Path(a.catalog) if getattr(a, "catalog", None) else root / "approvals" / "tts-catalog.json"
    catalog = runtime.read_json(catalog_path) if catalog_path.exists() else {}
    configured = narr["voice_id"]
    if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", configured):
        voice_id = configured
    else:
        matches = [v for v in catalog.get("voices", []) if v.get("name") == configured or v.get("voice_id") == configured]
        if len(matches) != 1 or not matches[0].get("voice_id"):
            fail("音色名称必须在本地 tts-catalog.json 中唯一解析；请提供已核对的音色 ID 或本地目录，不自动联网")
        voice_id = matches[0]["voice_id"]
    if narr["provider"] != "compshare-indextts-2.5":
        fail("unsupported narration provider")
    explicit_price = getattr(a, "unit_price", None)
    price = float(catalog.get("unit_price", .01) if explicit_price is None else explicit_price)
    if not 0 < price < float("inf"):
        fail("unit price must be finite and positive")
    reqs = []
    total = 0.0
    for t in todo:
        chars = sum(1 for ch in t["text"] if ch.isalnum())          # 平台只按字母/汉字/数字计费
        est = round(max(chars, 1) * price, 2)
        total += est
        body = {"text": t["text"], "language": "ZH",
                "voice": {"source": "preset", "voice_id": voice_id}}
        if narr.get("speed"):
            body["speed"] = float(narr["speed"])
            if not 0 < body["speed"] < float("inf"):
                fail("speed must be finite and positive")
        reqs.append({"provider": "compshare-indextts", "clip_id": t["clip_id"], "entry_id": t["entry_id"],
                     "estimate_points": est, "billable_characters": chars, "params": body,
                      "source_fingerprint": pipeline_state.narration_fingerprint(root, t["clip_id"], t["entry_id"]),
                     "warnings": (["旁白时长可能超过预留 %ss" % t["reserved"]]
                                  if t["reserved"] and chars / 4.0 > t["reserved"] else [])})
    out = root / ("单集制作/" + (a.episode or "EP001") + "/旁白/requests.json")
    atomic_json(out, {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                      "unit_price": price, "pricing_source": "local-estimate-not-live-quote", "total_estimate_points": round(total, 2), "requests": reqs})
    print(json.dumps({"todo": len(reqs), "estimate_points_each": [r["estimate_points"] for r in reqs],
                      "total_estimate_points": round(total, 2), "written": str(out)}, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ submit

def project_of(task_dir):
    t = Path(task_dir).resolve()
    if not re.fullmatch(r"attempt-\d{3,}", t.name) or t.parent.parent.name != "tasks":
        fail("--task-dir must be <project>/tasks/<task name>/attempt-NNN")
    return t.parent.parent.parent


def cmd_submit(a):
    bundle = json.loads(a.request.read_text(encoding="utf-8"))
    matches = [r for r in bundle["requests"] if r["entry_id"] == a.entry
               and (not getattr(a, "clip", None) or r["clip_id"] == a.clip)]
    if len(matches) != 1:
        fail(f"requests.json 的旁白条目 {a.entry} 不存在或不唯一；可用 --clip 指定片段")
    req = matches[0]
    if req.get("provider") != "compshare-indextts":
        fail("not an IndexTTS request")
    digest = hashlib.sha256(json.dumps(req, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    preview = {"preview": not a.execute, "request_sha256": digest, "entry": req["entry_id"],
               "clip_id": req["clip_id"], "estimate_points": req["estimate_points"],
               "params": req["params"], "warnings": req.get("warnings", [])}
    if not a.execute:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return

    task, state_path = a.task_dir, a.task_dir / "state.json"
    if not a.retry_unknown and not (a.budget and a.batch):
        fail("--execute needs --budget <approvals/budget.json> and --batch <name>; no unbudgeted submits")
    root = project_of(task)
    if not a.retry_unknown and Path(a.budget).resolve() != root / "approvals" / "budget.json":
        fail(f"--budget must be this project's approvals/budget.json ({root / 'approvals' / 'budget.json'})")
    task.mkdir(parents=True, exist_ok=True)
    if a.retry_unknown:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("status") != "unknown" or state.get("request_sha256") != digest:
            fail("--retry-unknown only for an unknown attempt of this exact request")
    else:
        if not req.get("source_fingerprint"):
            fail("旧请求没有旁白来源指纹，请重新 prepare，不自动提交")
        # Confirm prepared source text still belongs to this project before spending.
        pipeline_state.check_narration(root, req["clip_id"], req["entry_id"], req["source_fingerprint"],
                                       expected_text=req["params"]["text"])
        with runtime.exclusive_lock(global_dir(root) / "全局设置.lock"):
            runtime.check_output(root, a.output)
            balance = _check_concurrency(root, req["estimate_points"])
            state = {"provider": "compshare-indextts", "request_sha256": digest,
                     "idempotency_key": runtime.identity(task, digest), "status": "submitting",
                     "submitted_at": time.time(), "task_id": None, "entry": req["entry_id"],
                     "clip_id": req["clip_id"], "source_fingerprint": req["source_fingerprint"],
                     "estimate_points": req["estimate_points"], "output": a.output and str(a.output),
                     "submitted_characters":len(req['params']['text']),
                     "billable_characters":sum(ch.isalnum() for ch in req['params']['text'])}
            atomic_json(task / "request.json", req)
            runtime.reserve(state, task, "speech", a.budget, a.batch, req["estimate_points"],
                            {"points": req["estimate_points"], "available_points": balance.get("available_points")})

    try:
        code, resp = http("POST", "/audio/v1/speech/jobs", json.dumps(req["params"], ensure_ascii=False).encode("utf-8"),
                          {"Idempotency-Key": state["idempotency_key"]}, timeout=a.timeout)
        atomic_json(task / "response.json", {"http": code, "body": resp})
        tid = resp.get("task_id") or (resp.get("task") or {}).get("task_id") or resp.get("id")
        if 200 <= code < 300 and tid:
            state.update(status="submitted", task_id=tid)
        elif code in (400, 401, 403, 404, 422, 429):
            state.update(status="rejected", error=resp)
            refund(state)
        else:
            state.update(status="unknown", error=resp)
    except ConnectionError as e:
        state.update(status="unknown", error=str(e))
    atomic_json(state_path, state)
    print(json.dumps({k: v for k, v in state.items() if k != "idempotency_key"}, ensure_ascii=False, indent=2))
    if state["status"] != "submitted":
        raise SystemExit(2)


# ------------------------------------------------------------------ query / download

def ffprobe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        fail("下载的 WAV 解码失败: " + r.stderr[:200])
    return float(r.stdout.strip())


def cmd_query(a):
    state_path = a.task_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["task_dir"] = str(a.task_dir)
    if not state.get("task_id"):
        fail(f"no task_id (status {state.get('status')}); for unknown use submit --retry-unknown")
    code, resp = http("GET", "/audio/v1/speech/jobs/" + state["task_id"])
    atomic_json(a.task_dir / "query-response.json", {"http": code, "body": resp, "at": time.time()})
    if code != 200 or not isinstance(resp, dict):
        fail(f"query unavailable (http {code}); reservation unchanged")
    task = resp.get("task") or resp
    if not isinstance(task, dict) or not (task.get("status") or task.get("state")):
        fail("invalid query response; reservation unchanged")
    st = task.get("status") or task.get("state")
    if st not in ("pending", "queued", "generating", "running", "succeeded", "failed", "cancelled"):
        fail("unrecognized task status; reservation unchanged")
    if task.get("task_id", state["task_id"]) != state["task_id"]:
        fail("query returned a different task ID")
    if state.get("status") in ("succeeded", "failed", "cancelled") and st != state["status"]:
        fail("platform terminal status conflict; reconciliation required")
    state.update(platform_status=st,
                 status=st,
                 point_cost=task.get("point_cost"), point_status=task.get("point_status"),
                 billable_characters=task.get("billable_characters"),
                 error=task.get("error_message") or task.get("error"))
    root = project_of(a.task_dir)
    if st in ("failed", "cancelled"):
        state.update(status=st, error=task.get("error_message") or resp)
        if task.get("point_status") == "released":
            refund(state)
        else:
            state["refund_pending"] = True
    atomic_json(state_path, state)
    if st == "succeeded":
        settle(state, root)
        out = Path(a.output or state.get("output") or "")
        if str(out) not in ("", ".") and not state.get("downloaded_to"):
            runtime.check_output(root, out)
            url = task.get("output_url")
            if not url:
                fail("succeeded without output_url")
            out.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=out.parent, prefix=out.name + ".", suffix=".part")
            os.close(fd)
            tmp = Path(name)
            dl = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(dl, timeout=300) as r, tmp.open("wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            dur = ffprobe_duration(tmp)
            runtime.publish_output(tmp, out)
            state["downloaded_to"] = str(out)
            state["narration_duration"] = round(dur, 2)
            atomic_json(state_path, state)
        # Delivery and source attachment are separate: stale audio is retained in
        # the attempt, but cannot be attached to changed narration.
        if state.get("downloaded_to"):
            if not Path(state["downloaded_to"]).is_file():
                fail("previously downloaded audio is missing; refusing to attach")
            reserved = pipeline_state.attach_narration(
                root, state["clip_id"], state["entry"], state.get("source_fingerprint"),
                Path(state["downloaded_to"]), state["narration_duration"])
            dur = state["narration_duration"]
            warn = []
            if reserved and dur > reserved:
                warn.append(f"旁白实际 {dur}s 超过预留 {reserved}s，由编剧调整（超过 1 秒要报告总导演）")
            if warn:
                print("WARNING: " + "; ".join(warn), file=sys.stderr)
    atomic_json(state_path, state)
    print(json.dumps({k: state.get(k) for k in ("status", "platform_status", "point_cost", "point_status",
                                                "billable_characters", "error", "downloaded_to",
                                                "narration_duration")}, ensure_ascii=False, indent=2))
    if state.get("charge", {}).get("settlement_pending"):
        fail("实际费用超过批准额度，保留待结算差额并禁止该批次继续提交，请报告总导演")
    if state["status"] not in ("succeeded", "pending", "generating", "queued", "running"):
        raise SystemExit(2)


def cmd_status(a):
    state = json.loads((a.task_dir / "state.json").read_text(encoding="utf-8"))
    print(json.dumps({k: v for k, v in state.items() if k != "idempotency_key"}, ensure_ascii=False, indent=2))


def cmd_voices(a):
    code, resp = http("GET", "/audio/v1/voices" + ("?kind=" + a.kind if a.kind else ""))
    if code != 200:
        fail(f"voices query failed (http {code})")
    items = resp.get("items", [])
    for v in items:
        v.pop("audio_url", None)                                    # 试听链接很长，单独 --full 才显示
    if getattr(a, "output", None):
        atomic_json(a.output, {"voices": items, "source": "explicit-online-voices"})
    print(json.dumps({"http": code, "voices": items, "total": resp.get("total"),
                      "audio_urls_hidden": True}, ensure_ascii=False, indent=2))


def cmd_pending(a):
    root = (a.project or Path.cwd()).resolve()
    works = (root / "作品" if root.name != "作品" else root) if a.all else root.parent
    tasks = running_tasks(works)
    mine = [t for t in tasks if t["provider"] == "compshare-indextts"
            and (a.all or Path(t["task_dir"]).is_relative_to(root))]
    print(json.dumps({"pending": mine, "count": len(mine)}, ensure_ascii=False, indent=2))


cmd_submit = runtime.attempt_command(cmd_submit)
cmd_query = runtime.attempt_command(cmd_query)


def main():
    p = argparse.ArgumentParser(description="旁白合成（IndexTTS 2.5）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("prepare")
    sp.add_argument("--project", required=True, help="作品目录")
    sp.add_argument("--episode", default=None, help="只配这一集，如 EP001")
    sp.add_argument("--redo", action="store_true", help="重新生成已有 audio 的旁白（编剧改词后用）")
    sp.add_argument("--catalog", type=Path, help="本地音色目录（voices 数组、可选 unit_price）")
    sp.add_argument("--unit-price", type=float, help="本地估价：每个计费字符的积分，不自动联网")
    sp.set_defaults(func=cmd_prepare)

    sp = sub.add_parser("submit")
    sp.add_argument("--request", type=Path, required=True, help="prepare 写的 requests.json")
    sp.add_argument("--clip", help="条目 ID 跨集重复时，显式指定完整片段 ID")
    sp.add_argument("--entry", required=True, help="旁白条目 id，如 Clip00003-1")
    sp.add_argument("--task-dir", type=Path, required=True, help="<作品>/tasks/<任务名>/attempt-NNN")
    sp.add_argument("--execute", action="store_true", help="真正提交（默认只预览）")
    sp.add_argument("--budget", type=Path, help="approvals/budget.json")
    sp.add_argument("--batch", help="speech 批次名")
    sp.add_argument("--retry-unknown", action="store_true")
    sp.add_argument("--output", type=Path, help="WAV 输出路径")
    sp.add_argument("--timeout", type=int, default=60)
    sp.set_defaults(func=cmd_submit)

    sp = sub.add_parser("query")
    sp.add_argument("--task-dir", type=Path, required=True)
    sp.add_argument("--output", type=Path)
    sp.set_defaults(func=cmd_query)

    sp = sub.add_parser("status")
    sp.add_argument("--task-dir", type=Path, required=True)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("pricing", help="显式在线只读报价；prepare 不调用")
    sp.set_defaults(func=lambda a: print(json.dumps({"unit_price": unit_price()[0], "source": "online"})))

    sp = sub.add_parser("voices")
    sp.add_argument("--output", type=Path, help="保存本地音色目录，供离线 prepare 使用")
    sp.add_argument("--kind", choices=("preset", "custom"), default="preset")
    sp.set_defaults(func=cmd_voices)

    sp = sub.add_parser("pending")
    sp.add_argument("--project", type=Path, help="作品目录或 漫剧/ 目录")
    sp.add_argument("--all", action="store_true", help="扫所有作品")
    sp.set_defaults(func=cmd_pending)

    a = p.parse_args()
    try:
        a.func(a)
    except pipeline_state.Refused as e:
        fail(str(e))


if __name__ == "__main__":
    main()
