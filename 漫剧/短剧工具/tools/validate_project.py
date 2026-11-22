#!/usr/bin/env python3
"""作品结构校验（数据规范 v2）。先调用原技能包的 v1 校验（skill/scripts/validate_project.py，原样不改），再加 v2 检查。
只检查结构和文件引用，不判断画面和声音质量。

用法：validate_project.py --project-root <作品目录> [--stage plan|assets|assemble]
输出 JSON：{"ok", "stage", "schema_version", "errors", "warnings"}；有错误时退出码为 1。
"""
import argparse, hashlib, importlib.util, json, re, sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):  # Windows 中文系统下 -I 会忽略 PYTHONUTF8，强制输出 UTF-8
    _s.reconfigure(encoding="utf-8")

TOOLS_ROOT = Path(__file__).resolve().parent.parent          # 短剧工具/
sys.path.insert(0, str(TOOLS_ROOT / 'tools'))
V1_SCRIPT = TOOLS_ROOT / "skill" / "scripts" / "validate_project.py"
STATUSES = ("planned", "generated", "approved", "failed")
RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
RESOLUTIONS = ("480P", "768P", "1080P")
DEPRECATED = ("image_model", "video_model", "poll_interval", "poll_window", "max_concurrency")
BUDGET_UNITS = {"video": "points", "image": "images", "speech": "points"}
VOICE_SECONDS = (3, 5)   # H3 音色模板建议 3–5 秒（设计文档 §8.5.1）


def load_v1():
    spec = importlib.util.spec_from_file_location("validate_project_v1", V1_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def production_config_errors(project):
    """Reject explicitly unresolved proposals in production, not in planning."""
    errors=[]
    if not isinstance(project,dict):
        return ['project.json 顶层必须是对象']
    if project.get('specification_status') == 'proposal_pending_user_review':
        errors.append('project.json 仍是初始化待审提案，须从已采用依据同步当前配置')
    for section in ('image','video'):
        value=project.get(section)
        if isinstance(value,dict) and 'model' in value:
            model=value['model']
            if not isinstance(model,str) or not model.strip() or re.search(r'待核实|尚未批准|待确认|\b(?:TODO|TBD|placeholder)\b',model,re.I):
                errors.append(f'project.json 的 {section}.model 仍是占位，不能进入生产')
    return errors


class Checker:
    def __init__(self, root, stage, episode=None):
        self.root, self.stage, self.episode = root, stage, episode
        self.errors, self.warnings = [], []

    def stage_of(self, clip):
        """按集校验时，只有这一集的片段按 --stage 查，其他集只查结构（plan）。"""
        if self.episode is None or clip.get("episode") == self.episode:
            return self.stage
        return "plan"

    def fingerprint(self, label, fp, files):
        """核对 pipeline_state 在标 generated 时记下的文件指纹（数据规范-v2 §7.5）。"""
        if not isinstance(fp, dict):
            return
        for key, rel in files.items():
            want = fp.get(key)
            f = self.root / rel if isinstance(rel, str) else None
            if want and f is not None and f.is_file():
                if hashlib.sha256(f.read_bytes()).hexdigest() != want:
                    self.err(f"{label}: {key} 文件指纹不符（{rel} 在标 generated 之后被改过），需要产出者重新确认")

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def path(self, value, label):
        """合法的相对路径就返回绝对路径，否则记错误并返回 None。"""
        if not isinstance(value, str) or not value:
            self.err(f"{label}: 路径为空或不是字符串")
            return None
        p = Path(value)
        if p.is_absolute() or not (self.root / p).resolve().is_relative_to(self.root):
            self.err(f"{label}: 路径跑出了作品目录 {value}")
            return None
        return self.root / p

    def need_file(self, value, label):
        p = self.path(value, label)
        if p is not None and (not p.is_file() or not p.stat().st_size):
            self.err(f"{label}: 文件不存在或为空 {value}")

    def read(self, name, optional=False):
        f = self.root / name
        if not f.exists():
            if not optional:
                self.err(f"缺少 {name}")
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            self.err(f"{name} 不是合法 JSON：{e}")
            return None

    # -------------------------------------------------------------- project.json
    def project(self, pj):
        if not isinstance(pj, dict):
            self.err("project.json 顶层必须是对象")
            return
        if self.stage != 'plan':
            self.errors.extend(production_config_errors(pj))
        try:
            from audio.delivery_checks import publication_context
            public_release=publication_context(pj)['purpose']=='public-release'
        except ValueError as error:
            self.err(str(error));public_release=False
        for k in DEPRECATED:
            if k in pj:
                self.warn(f"project.json 的 {k} 已不再使用（OVERRIDES §3）")
        if not isinstance(pj.get("title"), str) or not pj.get("title"):
            self.err("project.json: 缺少 title")
        if pj.get("mode") not in ("series", "short"):
            self.err("project.json: mode 只能是 series 或 short")
        genre = pj.get("genre")
        if not isinstance(genre, str) or not re.fullmatch(r"[a-z0-9-]+", genre) or not (TOOLS_ROOT / "genres" / f"{genre}.md").is_file():
            self.err(f"project.json: genre {genre!r} 没有对应的 genres/<genre>.md")
        if pj.get("ratio") not in RATIOS:
            self.err(f"project.json: ratio 只能是 {' '.join(RATIOS)}")
        csr = pj.get("character_sheet_ratio")
        if csr is not None and csr not in RATIOS:
            self.err("project.json: character_sheet_ratio 不是支持的比例")
        img = pj.get("image")
        if not isinstance(img, dict) or img.get("provider") != "nexorai" or not isinstance(img.get("model"), str):
            self.err("project.json: image 要写 provider=nexorai 和 model")
        vid = pj.get("video")
        if not isinstance(vid, dict):
            self.err("project.json: 缺少 video")
        else:
            if vid.get("provider") != "minimax-h3" or not isinstance(vid.get("model"), str):
                self.err("project.json: video 要写 provider=minimax-h3 和 model")
            for k in ("draft_resolution", "final_resolution"):
                if vid.get(k) not in RESOLUTIONS:
                    self.err(f"project.json: video.{k} 只能是 {'/'.join(RESOLUTIONS)}")
            if vid.get("audio") not in ("generate", "mute"):
                self.err("project.json: video.audio 只能是 generate 或 mute")
        nar = pj.get("narration")
        if nar is not None and (not isinstance(nar, dict) or not nar.get("provider") or not nar.get("voice_id")):
            self.err("project.json: narration 要写 provider 和 voice_id")
        post = pj.get("post")
        if not isinstance(post, dict):
            self.err("project.json: 缺少 post")
            return
        for k in ("loudness_lufs", "true_peak_db", "bgm_duck_db"):
            if k in post and not is_num(post[k]):
                self.err(f"project.json: post.{k} 必须是数字")
        if post.get("subtitles", "srt") not in ("srt", "none"):
            self.err("project.json: post.subtitles 只能是 srt 或 none")
        label = post.get("aigc_label")
        if not isinstance(label, dict) or not all(isinstance(label.get(k), bool) for k in ("visible", "metadata")):
            self.err("project.json: post.aigc_label 要有 visible、metadata 两个开关")
        elif public_release and not (label["visible"] and label["metadata"]):
            self.err("project.json: 发布用途（含 delivery_intent）要求 AI 标识两个开关开启")

    # -------------------------------------------------------------- assets.json
    def assets(self, assets):
        for a in assets:
            aid = a.get("id", "?")
            is_char = a.get("type") == "character"
            for k in ("sheet", "sheet_prompt"):
                if k in a:
                    if not is_char:
                        self.warn(f"{aid}: {k} 只用于角色")
                    self.path(a[k], f"{aid}.{k}")
            if "voice" in a:
                if not is_char:
                    self.warn(f"{aid}: voice 只用于角色")
                elif not isinstance(a["voice"], str) or not a["voice"].strip():
                    self.err(f"{aid}: voice 必须是非空的英文音色描述句")
            if a.get("status") == "approved" and a.get("sheet"):
                self.need_file(a["sheet"], f"{aid}.sheet（已通过的角色两张图都要在）")
            if a.get("status") in ("generated", "approved"):
                self.need_file(a.get("image"), f"{aid}.image（状态 {a.get('status')}）")
                self.fingerprint(aid, a.get("fingerprint"), {"image": a.get("image"), "sheet": a.get("sheet")})

    # -------------------------------------------------------------- clips.json
    def clips(self, clips, assets, mode):
        forms = {a["id"] for a in assets if a.get("type") == "character" and isinstance(a.get("id"), str)}
        chars = {f.split("-")[0] for f in forms}
        asset_by_id = {a.get("id"): a for a in assets}
        scoped_assets = set()
        for c in clips:
            cid = c.get("id", "?")
            cstage = self.stage_of(c)
            d = c.get("duration")
            if not (isinstance(d, int) and not isinstance(d, bool) and 4 <= d <= 15):
                self.err(f"{cid}: duration 必须是 4–15 的整数（OVERRIDES §3），现在是 {d!r}")
            if c.get("status") in ("generated", "approved"):
                self.need_file(c.get("video"), f"{cid}.video（定稿状态 {c.get('status')}）")
                self.fingerprint(cid, c.get("fingerprint"), {"video": c.get("video"), "prompt": c.get("prompt")})
            if self.episode is not None and cstage in ("assets", "assemble"):
                # 按集校验时由这里做 v1 的阶段检查（v1 只能查整部作品）
                self.need_file(c.get("prompt"), f"{cid}.prompt")
                scoped_assets.update(x for x in c.get("assets", []) if isinstance(x, str))
                if cstage == "assemble":
                    self.need_file(c.get("video"), f"{cid}.video")
                    if c.get("status") != "approved":
                        self.err(f"{cid}: 合成前定稿必须已通过（当前 {c.get('status')}）")
            ffd = c.get("final_from_draft")
            if ffd is not None and not isinstance(ffd, bool):
                self.err(f"{cid}: final_from_draft 必须是 true/false")
            elif ffd and c.get("status") in ("generated", "approved"):
                dr = c.get("draft") if isinstance(c.get("draft"), dict) else {}
                if c.get("video") != dr.get("video"):
                    self.err(f"{cid}: 用试生成作为定稿时，video 要指向试生成文件 {dr.get('video')}")
                if dr.get("status") != "approved":
                    self.err(f"{cid}: 用试生成作为定稿，试生成本身要先通过")
                if cstage == "assemble":
                    self.warn(f"{cid}: 用 480P 试生成作为定稿，合成时会被放大，合成报告要注明")
            for k in ("task_dir", "ending_frame"):
                if k in c and c[k] is not None:
                    self.path(c[k], f"{cid}.{k}")
            draft = c.get("draft")
            if draft is not None:
                if not isinstance(draft, dict):
                    self.err(f"{cid}: draft 必须是对象")
                else:
                    if draft.get("status") not in STATUSES:
                        self.err(f"{cid}: draft.status 无效")
                    for k in ("video", "task_dir", "ending_frame"):
                        if draft.get(k) is not None:
                            self.path(draft[k], f"{cid}.draft.{k}")
                    if draft.get("status") in ("generated", "approved"):
                        self.need_file(draft.get("video"), f"{cid}.draft.video（试生成状态 {draft.get('status')}）")
                        self.fingerprint(f"{cid}.draft", draft.get("fingerprint"), {"video": draft.get("video")})
            if "needs_draft" in c and not isinstance(c["needs_draft"], bool):
                self.err(f"{cid}: needs_draft 必须是 true/false")
            if mode == "short" and "needs_draft" not in c:
                self.warn(f"{cid}: 短片模式要标 needs_draft（需试生成：是/否）")
            anchors = c.get("voice_anchor", [])
            if not isinstance(anchors, list):
                self.err(f"{cid}: voice_anchor 必须是数组")
                anchors = []
            clip_assets = {x for x in c.get("assets", []) if isinstance(x, str)}
            clip_chars = {x.split("-")[0] for x in clip_assets}
            for ch in anchors:
                # 角色编号 C00001；会改变声音的形态单独定音时写形态编号 C00001-C（§8.5.1）
                if not isinstance(ch, str) or not re.fullmatch(r"C\d{5}(-[A-Z]+)?", ch) or ch not in (chars | forms):
                    self.err(f"{cid}: voice_anchor 里的 {ch!r} 不是已登记的角色或形态编号")
                elif ch not in (clip_assets if "-" in ch else clip_chars):
                    self.warn(f"{cid}: 定音角色 {ch} 不在这个片段的 assets 里")
            self.narration(c, cid, d, cstage)
            if (mode == "series" and cstage == "assemble" and c.get("status") == "approved"
                    and (not isinstance(draft, dict) or draft.get("status") != "approved")):
                self.warn(f"{cid}: 定稿已通过但试生成没有通过记录（如果是你决定跳过试生成，可以忽略）")
        for aid in sorted(scoped_assets):
            a = asset_by_id.get(aid)
            if a is None:
                continue                      # 未登记的资产 v1 已经报过
            self.need_file(a.get("prompt"), f"{aid}.prompt")
            self.need_file(a.get("image"), f"{aid}.image")
            if a.get("status") != "approved":
                self.err(f"{aid}: {self.episode} 用到的资产必须已通过（当前 {a.get('status')}）")

    def narration(self, c, cid, duration, stage):
        items = c.get("narration")
        if items is None:
            return
        if not isinstance(items, list):
            self.err(f"{cid}: narration 必须是数组")
            return
        clipno = cid.split("/")[-1]
        seen = set()
        for n in items:
            if not isinstance(n, dict):
                self.err(f"{cid}: narration 的每一项都必须是对象")
                continue
            nid = n.get("id")
            label = f"{cid}.narration[{nid}]"
            if not isinstance(nid, str) or not re.fullmatch(re.escape(clipno) + r"-\d+", nid):
                self.err(f"{label}: id 要写成 {clipno}-序号")
            if nid in seen:
                self.err(f"{label}: id 重复")
            seen.add(nid)
            start, reserved = n.get("start"), n.get("reserved")
            if not is_num(start) or start < 0 or not is_num(reserved) or reserved <= 0:
                self.err(f"{label}: start 要 ≥ 0，reserved 要 > 0")
            elif is_num(duration) and start + reserved > duration + 1e-9:
                self.err(f"{label}: start + reserved = {start + reserved} 超过片段时长 {duration}")
            if not isinstance(n.get("text"), str) or not n["text"].strip():
                self.err(f"{label}: text 不能为空")
            if "audio" in n:
                self.need_file(n["audio"], f"{label}.audio")
            elif stage == "assemble":
                self.err(f"{label}: 合成前旁白必须已配好（缺 audio）")
            if stage == "assemble" and n.get("narration_duration") is None:
                self.err(f"{label}: 合成前要有 narration_duration")
            nd = n.get("narration_duration")
            if nd is not None:
                if not is_num(nd) or nd <= 0:
                    self.err(f"{label}: narration_duration 必须是正数")
                elif is_num(reserved) and nd > reserved + 1:
                    self.warn(f"{label}: 旁白实际 {nd} 秒，超出预留 {reserved} 秒 1 秒以上，编剧要调整，总导演要告诉你")
                elif is_num(reserved) and nd > reserved:
                    self.warn(f"{label}: 旁白实际 {nd} 秒，超出预留 {reserved} 秒，编剧要调整这个片段")

    # -------------------------------------------------------------- voices.json
    def voices(self, voices, assets, clips):
        if not isinstance(voices, list):
            self.err("voices.json 顶层必须是数组")
            return
        asset_ids = {a.get("id") for a in assets}
        clip_ids = {c.get("id") for c in clips}
        seen = set()
        for v in voices:
            ch = v.get("character")
            label = f"voices[{ch}]"
            for k in ("character", "file", "source_clip", "start", "end", "text", "status", "forms"):
                if k not in v:
                    self.err(f"{label}: 缺少 {k}")
            if not isinstance(ch, str) or not re.fullmatch(r"C\d{5}(-[A-Z]+)?", ch):
                self.err(f"{label}: character 编号格式不对")
                continue
            if ch in seen:
                self.err(f"{label}: 同一个角色只能登记一条")
            seen.add(ch)
            if v.get("status") not in STATUSES:
                self.err(f"{label}: status 无效")
            if v.get("status") in ("generated", "approved"):
                self.need_file(v.get("file"), f"{label}.file")
                self.fingerprint(label, v.get("fingerprint"), {"file": v.get("file")})
            else:
                self.path(v.get("file"), f"{label}.file")
            if v.get("source_clip") not in clip_ids:
                self.err(f"{label}: source_clip {v.get('source_clip')!r} 不在 clips.json 里")
            s, e = v.get("start"), v.get("end")
            if not is_num(s) or not is_num(e) or s < 0 or e <= s:
                self.err(f"{label}: start/end 无效")
            else:
                spans=v.get('source_segments',[{'start':s,'end':e}])
                valid=isinstance(spans,list) and 1<=len(spans)<=8
                previous=s;seconds=0
                for span in spans if isinstance(spans,list) else []:
                    if (not isinstance(span,dict) or set(span)!={'start','end'} or
                        not is_num(span.get('start')) or not is_num(span.get('end')) or
                        not previous<=span['start']<span['end']<=e):
                        valid=False;break
                    seconds+=span['end']-span['start'];previous=span['end']
                if not valid:self.err(f'{label}: source_segments 无效')
                elif not VOICE_SECONDS[0] <= seconds <= VOICE_SECONDS[1]:
                    self.warn(f"{label}: 模板时长（实际拼接）{seconds:.2f} 秒，不在 H3 建议范围内")
            base = ch.split("-")[0]
            forms = v.get("forms")
            if not isinstance(forms, list) or not forms:
                self.err(f"{label}: forms 必须是非空数组")
                continue
            for f in forms:
                if f not in asset_ids or not str(f).startswith(base + "-"):
                    self.err(f"{label}: forms 里的 {f!r} 不是这个角色在 assets.json 里的形态")
            if "-" in ch and ch not in forms:
                self.err(f"{label}: 按形态登记的模板，forms 要包含 {ch} 本身")

    # -------------------------------------------------------------- approvals/budget.json
    def budget(self, batches):
        if not isinstance(batches, list):
            self.err("approvals/budget.json 顶层必须是数组")
            return
        seen = set()
        for b in batches:
            name = b.get("batch") if isinstance(b, dict) else None
            label = f"budget[{name}]"
            if not isinstance(name, str) or not name:
                self.err("approvals/budget.json: 每个批次要有 batch 名称")
                continue
            if name in seen:
                self.err(f"{label}: 批次名重复")
            seen.add(name)
            kind = b.get("kind")
            if kind not in BUDGET_UNITS:
                self.err(f"{label}: kind 只能是 {'/'.join(BUDGET_UNITS)}")
            elif b.get("unit", BUDGET_UNITS[kind]) != BUDGET_UNITS[kind]:
                self.err(f"{label}: {kind} 的单位应为 {BUDGET_UNITS[kind]}")
            lim, used = b.get("limit"), b.get("used", 0)
            if not is_num(lim) or lim < 0 or not is_num(used) or used < 0:
                self.err(f"{label}: limit/used 必须是非负数")
            elif used > lim + 1e-9:
                self.err(f"{label}: 已用 {used} 超过额度 {lim}")


def schema_version_of(root):
    try:
        return json.loads((root / "project.json").read_text(encoding="utf-8")).get("schema_version")
    except (OSError, ValueError, AttributeError):
        return None


def validate(root, stage, episode=None):
    ck = Checker(root, stage, episode)
    try:
        # 按集校验时 v1 只做结构检查（plan），阶段检查由 v2 按集做（v1 只能查整部作品）
        ck.errors.extend(load_v1().validate(root, "plan" if episode else stage))
    except (ValueError, KeyError, TypeError, OSError, AttributeError) as e:
        ck.err(f"v1 校验无法完成：{e}")
    pj = ck.read("project.json")
    version = pj.get("schema_version") if isinstance(pj, dict) else None
    if version != 2:
        ck.warn("project.json 不是 schema_version 2：只做了 v1 检查，请按 数据规范-v2.md 升级")
        return ck, version
    ck.project(pj)
    assets = ck.read("assets.json") or []
    clips = ck.read("clips.json") or []
    if isinstance(assets, list) and isinstance(clips, list):
        assets = [a for a in assets if isinstance(a, dict)]
        clips = [c for c in clips if isinstance(c, dict)]
        ck.assets(assets)
        ck.clips(clips, assets, pj.get("mode"))
        voices = ck.read("voices.json", optional=True)
        if voices is not None:
            ck.voices(voices, assets, clips)
    budget = ck.read("approvals/budget.json", optional=True)
    if budget is not None:
        ck.budget(budget)
    from voice_lifecycle import safe_audit
    ck.voice_closeout = safe_audit(root, episode=episode)
    for message in ck.voice_closeout['errors']:
        (ck.err if stage == 'assemble' else ck.warn)(message)
    for item in ck.voice_closeout['characters']:
        if not item['complete']:
            (ck.err if stage == 'assemble' else ck.warn)(f'音色收尾：{item["name"]}—{item["action"]}')
    return ck, version


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project-root", type=Path, required=True)
    p.add_argument("--stage", choices=["plan", "assets", "assemble"], default="plan")
    p.add_argument("--episode", help="只按这一集做阶段检查（例如 EP001）；其他集只查结构")
    a = p.parse_args()
    if a.episode and not re.fullmatch(r"EP\d{3,}", a.episode):
        print("ERROR: --episode 要写成 EP001 这样的格式", file=sys.stderr)
        sys.exit(2)
    if a.episode and schema_version_of(a.project_root.resolve()) != 2:
        print("ERROR: --episode 只支持 schema_version 2 的作品", file=sys.stderr)
        sys.exit(2)
    ck, version = validate(a.project_root.resolve(), a.stage, a.episode)
    print(json.dumps({"ok": not ck.errors, "stage": a.stage, "episode": a.episode, "schema_version": version,
                      "errors": ck.errors, "warnings": ck.warnings,
                      "voice_closeout": getattr(ck, 'voice_closeout', None)}, ensure_ascii=False, indent=2))
    sys.exit(bool(ck.errors))


if __name__ == "__main__":
    main()
