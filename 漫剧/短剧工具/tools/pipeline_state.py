#!/usr/bin/env python3
"""共享清单（assets.json / clips.json / voices.json）的唯一修改入口。依据：数据规范-v2.md §7。

- 带锁串行化写入，先写临时文件再 os.replace 原子替换；
- 按 --role 和字段归属表限制能改的字段、能做的状态转换（approved 只能由总导演写）；
- 默认写前保留 .backup/（最近50份）；用户明确指定 storage.manifest_backups=false 的作品不额外备份、不清理旧备份；
- 每次修改追加一行到 logs/state-changes.jsonl。

用法示例：
  pipeline_state.py --project <作品目录> --role 编剧 add-clip --json '{"id": "EP001/Clip00001", ...}'
  pipeline_state.py --project <作品目录> --role 摄影 set-clip EP001/Clip00001 --set draft.status=generated
  pipeline_state.py --project <作品目录> --role 剪辑 set-clip EP001/Clip00003 --set narration.Clip00003-1.narration_duration=3.6
  pipeline_state.py --project <作品目录> show clips EP001/Clip00001
"""
import argparse, copy, hashlib, json, os, re, shutil, sys, tempfile, time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

for _s in (sys.stdout, sys.stderr):  # Windows 中文系统下 -I 会忽略 PYTHONUTF8，强制输出 UTF-8
    _s.reconfigure(encoding="utf-8")

ROLES = ("总导演", "编剧", "美术", "摄影", "剪辑")
STATUSES = ("planned", "generated", "approved", "failed")
KEEP_BACKUPS = 50

# ------------------------------------------------------------------ 字段归属表（数据规范-v2 §7）
# 每个文件：create = 新建条目时允许出现的字段及谁能建；edit = 新建之后每个字段由谁改。
# 路径写法：嵌套字段用 "."，narration 按条目 id 定位，归一化为 "narration.*.字段"。
ASSET_EDIT = {
    **{k: {"编剧"} for k in ("name", "detail", "usage", "source_asset_id", "relation", "chapter_ranges",
                            "visual_summary", "source_evidence", "design_additions", "voice")},
    **{k: {"美术"} for k in ("prompt", "image", "sheet", "sheet_prompt", "style_revision", "prompt_sha256")},
    "status": {"美术", "总导演"},
}
CLIP_EDIT = {
    **{k: {"编剧"} for k in ("duration", "assets", "prompt", "continue_from", "continuity_reason", "source_story",
                            "shots", "needs_draft", "voice_anchor",
                            "narration.*.id", "narration.*.start", "narration.*.reserved", "narration.*.text")},
    **{k: {"剪辑"} for k in ("narration.*.audio", "narration.*.narration_duration")},
    **{k: {"摄影"} for k in ("video", "task_dir", "ending_frame", "draft.video", "draft.task_dir", "draft.ending_frame")},
    "final_from_draft": {"总导演"},
    "status": {"摄影", "总导演"},
    "draft.status": {"摄影", "总导演"},
}
VOICE_EDIT = {
    **{k: {"摄影"} for k in ("file", "source_clip", "start", "end", "text", "forms", "source_segments")},
    "status": {"摄影", "总导演"},
}

FILES = {
    "assets": {"file": "assets.json", "key": "id", "creator": "编剧", "producer": "美术", "edit": ASSET_EDIT,
               "required": ("id", "type", "name", "detail", "prompt", "image", "status"),
               "create_extra": {"prompt", "image", "sheet", "sheet_prompt"}, "init_status": "planned"},
    "clips": {"file": "clips.json", "key": "id", "creator": "编剧", "producer": "摄影", "edit": CLIP_EDIT,
              "required": ("id", "episode", "duration", "assets", "prompt", "video", "continue_from", "status"),
              "create_extra": {"video", "draft.video"}, "init_status": "planned"},
    "voices": {"file": "voices.json", "key": "character", "creator": "摄影", "producer": "摄影", "edit": VOICE_EDIT,
               "required": ("character", "file", "source_clip", "start", "end", "text", "status", "forms"),
               "create_extra": set(), "init_status": "generated"},
}
ID_PATTERNS = {
    "assets": r"(C\d{5}-[A-Z]+|S\d{5}|P\d{5})",
    "clips": r"EP\d{3,}/Clip\d{5,}",
    "voices": r"C\d{5}(-[A-Z]+)?",
}
# 状态变成 generated 时自动记录这些文件的指纹（sha256）；总导演标 approved 前核对指纹（数据规范-v2 §7.5）
FINGERPRINT = {
    "assets": {"status": ("image", "sheet")},
    "clips": {"status": ("video", "prompt"), "draft.status": ("draft.video",)},
    "voices": {"status": ("file",)},
}
# 内容变了自动降级（数据规范-v2 §7.5）：(改了这些字段, 哪个状态, 降成什么)
#   降成 generated：只对 approved 生效（产出物换了，要重新审）
#   降成 planned：对 generated/approved 生效（规划变了，要重新生成）
DOWNGRADE = {
    "assets": [({"prompt", "image", "sheet", "sheet_prompt"}, "status", "generated"),
               ({"detail", "visual_summary", "design_additions"}, "status", "planned")],
    "clips": [({"video", "ending_frame"}, "status", "generated"),
              ({"draft.video", "draft.ending_frame"}, "draft.status", "generated"),
              ({"duration", "assets", "prompt", "continue_from", "shots"}, "status", "planned"),
              ({"duration", "assets", "prompt", "continue_from", "shots"}, "draft.status", "planned")],
    "voices": [({"file", "start", "end", "source_clip"}, "status", "generated")],
}
NESTED = {"draft"}            # 对象型嵌套字段
NESTED_LIST = {"narration"}   # 按 id 定位的条目列表
JSON_FIELDS = {"duration", "start", "end", "reserved", "narration_duration", "needs_draft", "final_from_draft",
               "fingerprint", "assets", "voice_anchor",
               "forms", "source_segments", "chapter_ranges", "shots", "narration", "draft", "continue_from", "source_asset_id",
               "design_additions", "source_evidence", "style_revision"}


class Refused(Exception):
    pass


def fail(msg):
    print("ERROR: " + msg, file=sys.stderr)
    raise SystemExit(1)


LOCK_TIMEOUT_S = 60      # 等锁最长时间；超时报错，不无限等
REPLACE_TIMEOUT_S = 10   # Windows 上目标文件正被别的进程读取时 os.replace 会失败，重试这么久


@contextmanager
def exclusive_lock(lock_path, timeout=LOCK_TIMEOUT_S):
    """跨进程排他锁（POSIX 用 flock，Windows 用 msvcrt.locking），等满 timeout 秒拿不到就报错。"""
    deadline = time.monotonic() + timeout
    with open(lock_path, "a+") as lk:
        while True:
            try:
                if fcntl:
                    fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    lk.seek(0)
                    msvcrt.locking(lk.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise Refused(f"等锁超过 {timeout} 秒：可能有别的进程卡住了，稍后再试（{lock_path}）")
                time.sleep(0.1)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(lk, fcntl.LOCK_UN)
            else:
                lk.seek(0)
                msvcrt.locking(lk.fileno(), msvcrt.LK_UNLCK, 1)


def replace_with_retry(src, dst, timeout=REPLACE_TIMEOUT_S):
    deadline = time.monotonic() + timeout
    delay = 0.02
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise Refused(f"{Path(dst).name} 一直被别的程序占用，{timeout} 秒内没能写入；关掉正在打开它的程序后重试")
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        replace_with_retry(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def manifest_backups_enabled(root):
    config_path=root/'project.json'
    config=json.loads(config_path.read_text(encoding='utf-8')) if config_path.is_file() else {}
    storage=config.get('storage',{})
    if not isinstance(storage,dict) or type(storage.get('manifest_backups',True)) is not bool:
        raise Refused('storage.manifest_backups 必须为布尔值')
    return storage.get('manifest_backups',True)


def restore_bytes(path, content):
    fd,tmp=tempfile.mkstemp(dir=path.parent,suffix='.rollback.tmp')
    try:
        with os.fdopen(fd,'wb') as f:f.write(content)
        replace_with_retry(tmp,path)
    finally:Path(tmp).unlink(missing_ok=True)


def backup(root, path):
    """把当前文件复制一份到 .backup/，返回备份路径（文件不存在时返回 None）。"""
    if not manifest_backups_enabled(root) or not path.exists():
        return None
    bdir = root / ".backup"
    bdir.mkdir(exist_ok=True)
    dst = bdir / f"{path.name}.{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
    shutil.copy2(path, dst)
    return dst


def prune_backups(root, name):
    if not manifest_backups_enabled(root):return
    olds = sorted((root / ".backup").glob(name + ".*"))
    for old in olds[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)


# ------------------------------------------------------------------ 叶子字段展开与比较
def leaves(record):
    """把一条记录展开成 {路径: 值}；draft 展开一层，narration 按条目 id 展开。"""
    out = {}
    for k, v in record.items():
        if k in NESTED and isinstance(v, dict):
            for sk, sv in v.items():
                out[f"{k}.{sk}"] = sv
        elif k in NESTED_LIST and isinstance(v, list):
            for item in v:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise Refused(f"{k} 的每一项都必须是带字符串 id 的对象")
                for sk, sv in item.items():
                    out[f"{k}.{item['id']}.{sk}"] = sv
        else:
            out[k] = v
    return out


def normalize(path):
    parts = path.split(".")
    if parts[0] in NESTED_LIST and len(parts) == 3:
        return f"{parts[0]}.*.{parts[2]}"
    return path


def diff(old, new):
    lo, ln = leaves(old), leaves(new)
    return {p: [lo.get(p), ln.get(p)] for p in sorted(set(lo) | set(ln)) if lo.get(p) != ln.get(p)}


def check_status(spec, role, field, old, new):
    if new not in STATUSES:
        raise Refused(f"{field} 只能是 {'/'.join(STATUSES)}，不能是 {new!r}")
    if old == new:
        return
    if role == "总导演":
        if new == "approved" and old != "generated":
            raise Refused(f"{field}：只有 generated 的条目才能标 approved（当前 {old}）")
        if new == "generated":
            raise Refused(f"{field}：generated 由产出者（{spec['producer']}）写，总导演不能代写")
        return
    if role != spec["producer"]:
        raise Refused(f"{field}：{role} 不能改状态")
    if new == "approved":
        raise Refused(f"{field}：approved 只能由总导演写")
    if new == "generated" and old not in ("planned", "failed"):
        raise Refused(f"{field}：{role} 只能把 planned/failed 改成 generated（当前 {old}）")
    if new == "failed" and old not in ("planned", "generated"):
        raise Refused(f"{field}：{role} 只能把 planned/generated 改成 failed（当前 {old}）")
    if new == "planned":
        raise Refused(f"{field}：改回 planned 只能由总导演做")


def check_edit(spec, role, old, new):
    changes = diff(old, new)
    removed_items = {p.split(".")[1] for p, (o, n) in changes.items()
                     if p.split(".")[0] in NESTED_LIST and n is None} if changes else set()
    new_ids = {i.get("id") for i in new.get("narration", []) if isinstance(i, dict)}
    clearing_draft = "draft" in new and new["draft"] is None and isinstance(old.get("draft"), dict)
    if clearing_draft and role != "总导演":
        raise Refused("清空整个 draft（试生成记录）只能由总导演做")
    for path, (o, n) in changes.items():
        parts = path.split(".")
        norm = normalize(path)
        if clearing_draft and parts[0] == "draft":
            continue
        if parts[0] == spec["key"] or parts[0] in ("type", "episode"):
            raise Refused(f"{parts[0]} 建好后不能改（要改由总导演删了重建）")
        # narration 整条删除：归创建者（编剧）
        if parts[0] in NESTED_LIST and parts[1] in removed_items and parts[1] not in new_ids:
            if role != "编剧":
                raise Refused(f"只有编剧能删除旁白条目 {parts[1]}")
            continue
        if parts[-1] == "status" and norm in ("status", "draft.status"):
            check_status(spec, role, norm, o, n)
            continue
        owners = spec["edit"].get(norm)
        if owners is None:
            raise Refused(f"字段 {norm} 不在数据规范 v2 的归属表里")
        if role not in owners:
            raise Refused(f"字段 {norm} 归 {'/'.join(sorted(owners))}，{role} 不能改")
    return changes


def get_path(rec, path):
    head, _, tail = path.partition(".")
    if not tail:
        return rec.get(head)
    sub = rec.get(head)
    return sub.get(tail) if isinstance(sub, dict) else None


def set_path(rec, path, value):
    head, _, tail = path.partition(".")
    if not tail:
        rec[head] = value
    elif isinstance(rec.get(head), dict):
        rec[head][tail] = value


def pop_path(rec, path):
    head, _, tail = path.partition(".")
    if not tail:
        rec.pop(head, None)
    elif isinstance(rec.get(head), dict):
        rec[head].pop(tail, None)


def sha256_of(root, rel):
    import hashlib
    f = root / rel
    if not isinstance(rel, str) or not f.is_file() or not f.stat().st_size:
        return None
    return hashlib.sha256(f.read_bytes()).hexdigest()


def fingerprint_field(status_field):
    return "draft.fingerprint" if status_field.startswith("draft.") else "fingerprint"


def apply_auto(root, kind, old, rec, user_changes):
    """系统自动规则（数据规范-v2 §7.5）：内容变了自动降级、旁白改词作废旧配音、generated 时记录文件指纹、
    approved 前核对指纹。返回自动改动的字段路径。"""
    auto = set()
    changed = {normalize(p) for p in user_changes}
    # 1) 编剧改了旁白文字：这一条的旧配音作废
    if kind == "clips":
        old_items = {i.get("id"): i for i in old.get("narration", []) if isinstance(i, dict)}
        for item in rec.get("narration", []) or []:
            prev = old_items.get(item.get("id"))
            if prev and prev.get("text") != item.get("text"):
                for k in ("audio", "narration_duration"):
                    if k in item:
                        item.pop(k)
                        auto.add(f"narration.{item['id']}.{k}")
    # 2) 内容变了自动降级（这次命令没有显式改这个状态时）
    for fields, status_field, to in DOWNGRADE.get(kind, []):
        if not (changed & fields) or status_field in changed:
            continue
        cur = get_path(rec, status_field)
        if (to == "generated" and cur == "approved") or (to == "planned" and cur in ("generated", "approved")):
            set_path(rec, status_field, to)
            auto.add(status_field)
    # 3) 指纹：变成 generated 时记录；标 approved 前核对；回到 planned 时清掉
    for status_field, files in FINGERPRINT.get(kind, {}).items():
        before, after = get_path(old, status_field), get_path(rec, status_field)
        fp_field = fingerprint_field(status_field)
        if after == "generated" and (before != "generated" or changed & set(files)):
            fp = {}
            for f in files:
                rel = get_path(rec, f)
                if rel is None and f == "sheet":
                    continue
                digest = sha256_of(root, rel)
                if digest is None:
                    raise Refused(f"{status_field} 要改成 generated，但 {f} 指向的文件不存在或为空：{rel}")
                fp[f.split(".")[-1]] = digest
            set_path(rec, fp_field, fp)
            auto.add(fp_field)
        elif after == "approved" and before != "approved":
            fp = get_path(rec, fp_field) or {}
            for f in files:
                rel = get_path(rec, f)
                key = f.split(".")[-1]
                if key in fp and sha256_of(root, rel) != fp[key]:
                    raise Refused(f"{f}（{rel}）在标 generated 之后被改过；请产出者重新确认并标 generated 后再审")
        elif after == "planned" and get_path(rec, fp_field) is not None:
            pop_path(rec, fp_field)
            auto.add(fp_field)
    return auto


def check_create(spec, kind, role, rec):
    if role != spec["creator"]:
        raise Refused(f"{spec['file']} 的新条目只能由{spec['creator']}建")
    rec.setdefault("status", spec["init_status"])
    if rec["status"] != spec["init_status"]:
        raise Refused(f"新建条目的状态只能是 {spec['init_status']}")
    missing = [k for k in spec["required"] if k not in rec]
    if missing:
        raise Refused("缺少必填字段：" + ", ".join(missing))
    key = rec[spec["key"]]
    if not isinstance(key, str) or not re.fullmatch(ID_PATTERNS[kind], key):
        raise Refused(f"编号格式不对：{key!r}")
    for path, value in leaves(rec).items():
        norm = normalize(path)
        if norm == "draft.status":
            if value != "planned":
                raise Refused("新建片段时 draft.status 只能是 planned")
            continue
        if norm in (spec["key"], "status", "type", "episode"):
            continue
        owners = spec["edit"].get(norm)
        if owners is None:
            raise Refused(f"字段 {norm} 不在数据规范 v2 的归属表里")
        if role not in owners and norm not in spec["create_extra"]:
            raise Refused(f"字段 {norm} 归 {'/'.join(sorted(owners))}，新建时不能由{role}填写")


# ------------------------------------------------------------------ 写入前的类型和路径检查（数据规范-v2 §0、§2–§4）
PATH_FIELDS = {"detail", "prompt", "image", "sheet", "sheet_prompt", "video", "task_dir", "ending_frame", "file",
               "draft.video", "draft.task_dir", "draft.ending_frame", "narration.*.audio"}
NUMBER_FIELDS = {"start", "end", "narration.*.start", "narration.*.reserved", "narration.*.narration_duration"}
LIST_FIELDS = {"assets", "voice_anchor", "forms", "chapter_ranges", "shots", "source_segments"}
NULLABLE = {"continue_from", "source_asset_id", "task_dir", "ending_frame", "draft.task_dir", "draft.ending_frame"}


def check_values(root, rec):
    if 'source_segments' in rec:
        segments = rec['source_segments']
        if not isinstance(segments, list) or not 1 <= len(segments) <= 8:
            raise Refused('source_segments 必须包含1–8个同源音频范围')
        previous = rec.get('start', 0)
        for segment in segments:
            if (not isinstance(segment, dict) or set(segment) != {'start', 'end'} or
                any(type(segment[k]) not in (int, float) for k in ('start', 'end')) or
                not previous <= segment['start'] < segment['end'] <= rec.get('end', -1)):
                raise Refused('source_segments 必须为顺序、不重叠且位于start/end内的范围')
            previous = segment['end']
    for path, value in leaves(rec).items():
        norm = normalize(path)
        if value is None and (norm in NULLABLE or norm.startswith("narration.")):
            continue
        if norm in PATH_FIELDS:
            if not isinstance(value, str) or not value:
                raise Refused(f"{path} 必须是非空的相对路径")
            p = Path(value)
            wp = PureWindowsPath(value)
            resolved_root = root.resolve()
            if p.is_absolute() or p.drive or wp.is_absolute() or wp.drive or not (resolved_root / p).resolve().is_relative_to(resolved_root):
                raise Refused(f"{path} 必须是作品目录内的相对路径：{value}")
        elif norm == "duration":
            if not (isinstance(value, int) and not isinstance(value, bool) and 4 <= value <= 15):
                raise Refused(f"duration 必须是 4–15 的整数（OVERRIDES §3），不能是 {value!r}")
        elif norm in NUMBER_FIELDS:
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise Refused(f"{path} 必须是非负数字，不能是 {value!r}")
        elif norm in ("needs_draft", "final_from_draft"):
            if not isinstance(value, bool):
                raise Refused(f"{norm} 必须是 true 或 false")
        elif norm in LIST_FIELDS:
            if not isinstance(value, list):
                raise Refused(f"{path} 必须是数组")


# ------------------------------------------------------------------ --set 解析
def parse_value(path, raw):
    if path.split(".")[-1] in JSON_FIELDS:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def apply_set(rec, path, value):
    parts = path.split(".")
    if parts[0] in NESTED_LIST and len(parts) == 3:
        items = rec.setdefault(parts[0], [])
        hit = [i for i in items if isinstance(i, dict) and i.get("id") == parts[1]]
        if not hit:
            raise Refused(f"{parts[0]} 里没有条目 {parts[1]}")
        hit[0][parts[2]] = value
    elif parts[0] in NESTED and len(parts) == 2:
        sub = rec.setdefault(parts[0], {})
        if not isinstance(sub, dict):
            raise Refused(f"{parts[0]} 必须是对象")
        sub[parts[1]] = value
    elif len(parts) == 1:
        rec[path] = value
    else:
        raise Refused(f"不支持的字段路径：{path}")


# ------------------------------------------------------------------ 主流程
def load(path):
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise Refused(f"{path.name} 顶层必须是数组")
    return data


def log_change(root, entry):
    log = root / "logs" / "state-changes.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def mutate(root, role, kind, action, target, payload, sets, after=None, expected_narration=None):
    spec = FILES[kind]
    path = root / spec["file"]
    with exclusive_lock(str(root / ".pipeline_state.lock")):
        data = load(path)
        if expected_narration is not None:
            nid, fingerprint = expected_narration
            _check_narration(root, target, nid, fingerprint)
        key = spec["key"]
        idx = {r.get(key): i for i, r in enumerate(data)}
        if action == "add":
            rec = copy.deepcopy(payload or {})
            for p, v in sets:
                apply_set(rec, p, v)
            target = rec.get(key)
            if target in idx:
                raise Refused(f"{target} 已经存在，改字段请用 set-")
            check_create(spec, kind, role, rec)
            auto = apply_auto(root, kind, {}, rec, {})
            changes = {p: [None, v] for p, v in leaves(rec).items()}
            pos = len(data)
            if after is not None:
                if after not in idx:
                    raise Refused(f"--after 指定的 {after} 不存在")
                pos = idx[after] + 1
            new_data = data[:pos] + [rec] + data[pos:]
        elif action == "set":
            if target not in idx:
                raise Refused(f"{spec['file']} 里没有 {target}")
            old = data[idx[target]]
            rec = copy.deepcopy(old)
            if payload:
                for k, v in payload.items():
                    rec[k] = v
            for p, v in sets:
                apply_set(rec, p, v)
            changes = check_edit(spec, role, old, rec)
            auto = apply_auto(root, kind, old, rec, changes)
            changes = diff(old, rec)
            if not changes:
                return {"ok": True, "file": spec["file"], "target": target, "action": action, "changes": {}}
            new_data = list(data)
            new_data[idx[target]] = rec
        elif action == "remove":
            if role != "总导演":
                raise Refused("删除条目只能由总导演做")
            if target not in idx:
                raise Refused(f"{spec['file']} 里没有 {target}")
            changes = {p: [v, None] for p, v in leaves(data[idx[target]]).items()}
            auto = set()
            new_data = [r for r in data if r.get(key) != target]
        else:
            raise Refused("未知操作 " + action)
        if action != "remove":
            check_values(root, rec)
        entry = {"time": datetime.now().astimezone().isoformat(timespec="seconds"), "role": role,
                 "file": spec["file"], "target": target, "action": action, "changes": changes,
                 "auto": sorted(auto)}
        previous=path.read_bytes() if path.is_file() else None
        saved = backup(root, path)
        try:
            atomic_json(path, new_data)
        except BaseException:
            if saved:
                saved.unlink(missing_ok=True)       # 没写成，这份备份没有意义
            raise
        try:
            log_change(root, entry)
        except BaseException:
            # 日志写不进去就撤销这次修改，保证"每次修改都有日志"
            if saved:
                replace_with_retry(shutil.copy2(saved, path.with_suffix(".rollback.tmp")), path)
                saved.unlink(missing_ok=True)
            elif previous is not None:
                restore_bytes(path,previous)
            else:
                path.unlink(missing_ok=True)
            raise
        prune_backups(root, path.name)
        result = {"ok": True, **{k: entry[k] for k in ("file", "target", "action", "changes", "auto")}}
        if kind == 'voices' or kind == 'clips' and any(k in changes for k in ('status', 'draft.status', 'video', 'draft.video')):
            # Saving an actual video approval remains possible before voice extraction.
            # The separate closeout cannot be silently mistaken for completed adoption.
            from voice_lifecycle import safe_audit
            result['voice_closeout'] = safe_audit(root)
        return result


def narration_fingerprint(root, clip_id, entry_id):
    """Bind prepared text + voice config to project and exact clip/entry."""
    clips = load(root / "clips.json")
    hits = [n for c in clips if c.get("id") == clip_id
            for n in c.get("narration", []) if n.get("id") == entry_id]
    if len(hits) != 1:
        raise Refused("旁白 clip/entry 不存在或不唯一")
    config = json.loads((root / "project.json").read_text(encoding="utf-8")).get("narration")
    source = [os.path.normcase(str(root.resolve())), clip_id, entry_id, hits[0].get("text"), config]
    return hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _check_narration(root, clip_id, entry_id, expected):
    if not expected or narration_fingerprint(root, clip_id, entry_id) != expected:
        raise Refused("旁白文字/音色/归属已变化，拒绝挂接过期音频；原结果保留但不挂接")


def check_narration(root, clip_id, entry_id, expected, expected_text=None):
    with exclusive_lock(str(root / ".pipeline_state.lock")):
        _check_narration(root, clip_id, entry_id, expected)
        if expected_text is not None:
            clips = load(root / "clips.json")
            actual = next(n["text"] for c in clips if c["id"] == clip_id
                          for n in c.get("narration", []) if n["id"] == entry_id)
            if actual != expected_text:
                raise Refused("请求文本与指纹来源不符，请重新 prepare")


def attach_narration(root, clip_id, entry_id, expected, audio, duration):
    rel = audio.resolve().relative_to(root.resolve()).as_posix()
    mutate(root, "剪辑", "clips", "set", clip_id, None,
           [(f"narration.{entry_id}.audio", rel),
            (f"narration.{entry_id}.narration_duration", duration)],
           expected_narration=(entry_id, expected))
    # Read-only informational warning; attachment precondition was checked under lock.
    clips = load(root / "clips.json")
    return next(n.get("reserved") for c in clips if c["id"] == clip_id
                for n in c.get("narration", []) if n["id"] == entry_id)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", type=Path, required=True, help="作品目录")
    p.add_argument("--role", choices=ROLES, help="谁在改（show 不需要）")
    sub = p.add_subparsers(dest="cmd", required=True)
    for kind in FILES:
        single = kind[:-1]
        a = sub.add_parser(f"add-{single}", help=f"新建一条 {FILES[kind]['file']} 记录")
        a.add_argument("--json", help="整条记录（JSON 对象）")
        a.add_argument("--json-file", type=Path, help="从 UTF-8 文件读整条记录（PowerShell 5.1 传 --json 会吞掉双引号时用）")
        a.add_argument("--set", action="append", default=[], metavar="字段=值")
        if kind == "clips":
            a.add_argument("--after", help="插在这个片段后面（默认追加到最后）")
        s = sub.add_parser(f"set-{single}", help=f"修改 {FILES[kind]['file']} 里的一条记录")
        s.add_argument("target")
        s.add_argument("--json", help="要覆盖的顶层字段（JSON 对象）")
        s.add_argument("--json-file", type=Path, help="从 UTF-8 文件读要覆盖的顶层字段")
        s.add_argument("--set", action="append", default=[], metavar="字段=值")
        r = sub.add_parser(f"remove-{single}", help="删除一条记录（只有总导演）")
        r.add_argument("target")
    sh = sub.add_parser("show", help="只读：打印整份清单或其中一条")
    sh.add_argument("kind", choices=list(FILES))
    sh.add_argument("target", nargs="?")
    a = p.parse_args()
    root = a.project.resolve()
    if not root.is_dir():
        fail(f"作品目录不存在：{root}")
    try:
        if a.cmd == "show":
            data = load(root / FILES[a.kind]["file"])
            if a.target:
                hit = [r for r in data if r.get(FILES[a.kind]["key"]) == a.target]
                if not hit:
                    raise Refused(f"没有 {a.target}")
                data = hit[0]
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return
        if not a.role:
            raise Refused("修改操作必须带 --role")
        action, single = a.cmd.split("-", 1)
        kind = single + "s"
        payload = None
        if getattr(a, "json", None) and getattr(a, "json_file", None):
            raise Refused("--json 和 --json-file 只能用一个")
        raw = getattr(a, "json", None)
        if getattr(a, "json_file", None):
            raw = a.json_file.read_text(encoding="utf-8-sig")
        if raw:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise Refused("--json 必须是 JSON 对象")
        sets = []
        for item in getattr(a, "set", []) or []:
            if "=" not in item:
                raise Refused(f"--set 要写成 字段=值：{item}")
            k, v = item.split("=", 1)
            sets.append((k, parse_value(k, v)))
        result = mutate(root, a.role, kind, action, getattr(a, "target", None), payload, sets,
                        getattr(a, "after", None))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Refused as e:
        fail(str(e))
    except json.JSONDecodeError as e:
        fail(f"JSON 格式错误：{e}")
    except OSError as e:
        fail(f"读写文件失败（这次修改没有生效）：{e}")


if __name__ == "__main__":
    main()
