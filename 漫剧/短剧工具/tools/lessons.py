#!/usr/bin/env python3
"""经验工具：确认后精确修改允许的经验表，登记可恢复的本地操作。

add / update / pitfall / retract 只写经验与登记，不自动提交 Git。
list / status / commit（不带 --execute）只读。recover 必须带 --confirm。
显式 commit --execute 需要另行得到用户批准；--confirm 只记录确认原话，
不是权限证明。旧记录缺少恢复证据时拒绝猜测撤销。
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0,str(Path(__file__).resolve().parent))
import generation_runtime as runtime

for stream in (sys.stdout,sys.stderr):
    stream.reconfigure(encoding="utf-8")

GENRES=("3d-guoman","live-action","urban")
LEVELS=("观察","规则")
MAX_ROWS=20
L_COLS=["编号","日期","来源（作品 / 集）","现象","规避方法","等级","次数"]
R_COLS=["编号","日期","来源（作品 / 集）","写入文件","一句话内容","等级","你的确认","git 提交"]
P_COLS=["#","现象","原因或推测","对策","来源","状态"]
TARGETS=("genre","manual","pitfall","nexorai","project","postproduction")
L_TABLE_HEADER="| "+" | ".join(L_COLS)+" |\n|---|---|---|---|---|---|---|"


def fail(message):
    raise SystemExit("ERROR: "+str(message))


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None


def today():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def escaped(value):
    return str(value).replace("\\","\\\\").replace("|","\\|").replace("\r"," ").replace("\n"," ")


def row(cells):
    return "| "+" | ".join(escaped(c) for c in cells)+" |"


def cell_spans(line):
    if not line.startswith("|") or not line.rstrip().endswith("|"):
        fail("不是合法的表格行")
    ends=[]
    slash=0
    for i,ch in enumerate(line):
        if ch=="|" and slash%2==0:
            ends.append(i)
        slash=slash+1 if ch=="\\" else 0
    if len(ends)<2:
        fail("表格分隔符损坏")
    return [(a+1,b) for a,b in zip(ends,ends[1:])]


def cells(line):
    result=[]
    for a,b in cell_spans(line):
        raw=line[a:b].strip()
        result.append(re.sub(r"\\([\\|])",lambda m:m.group(1),raw))
    return result


def set_cells(line,updates):
    spans=cell_spans(line)
    for column,value in sorted(updates.items(),reverse=True):
        a,b=spans[column]
        line=line[:a]+" "+escaped(value)+" "+line[b:]
    return line


def headings(lines):
    result=[]
    fence=None
    for i,line in enumerate(lines):
        match=re.match(r"^\s*(`{3,}|~{3,})",line)
        if match:
            marker=match.group(1)
            if fence is None:
                fence=(marker[0],len(marker))
            elif marker[0]==fence[0] and len(marker)>=fence[1]:
                fence=None
            continue
        if fence:
            continue
        match=re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$",line)
        if match:
            result.append((i,len(match.group(1)),match.group(2)))
    return result


def section(lines):
    hs=headings(lines)
    matches=[(i,depth) for i,depth,title in hs if depth==2 and title in ("实战经验","实战经验（复盘沉淀）")]
    if len(matches)!=1:
        fail("必须有唯一、真实的实战经验章节，不能写正文或代码示例")
    start,depth=matches[0]
    end=next((i for i,d,_ in hs if i>start and d<=depth),len(lines))
    return start+1,end


def table(text,kind="normal"):
    lines=text.splitlines(keepends=True)
    bare=[line.rstrip("\n") for line in lines]
    columns=L_COLS if kind=="normal" else P_COLS if kind=="pitfall" else R_COLS
    if kind=="normal":
        start,end=section(bare)
    else:
        start,end=0,len(lines)
    # Fenced examples never count as a table.
    fenced=set()
    fence=False
    for i,line in enumerate(bare):
        if re.match(r"^\s*(`{3,}|~{3,})",line):
            fence=not fence
            fenced.add(i)
        elif fence:
            fenced.add(i)
    headers=[]
    for i in range(start,end):
        if i not in fenced and bare[i].startswith("|") and cells(bare[i])==columns:
            headers.append(i)
    if len(headers)!=1:
        fail("找不到唯一的实战经验表头" if kind=="normal" else "找不到唯一表头")
    h=headers[0]
    if h+1>=end or len(cells(bare[h+1]))!=len(columns) or not all(re.fullmatch(r":?-{3,}:?",c) for c in cells(bare[h+1])):
        fail("表头分隔行损坏")
    if kind=="pitfall":
        end=next((i for i,_,_ in headings(bare) if i>h),len(lines))
    rows={}
    last=h+1
    for i in range(h+2,end):
        if i in fenced or not bare[i].startswith("|"):
            continue
        parts=cells(bare[i])
        pattern=r"[1-9]\d*" if kind=="pitfall" else r"L\d{3,}"
        if len(parts)!=len(columns) or not re.fullmatch(pattern,parts[0]) or parts[0] in rows:
            fail("表格列数、编号或唯一性不合法")
        rows[parts[0]]={"line":bare[i],"index":i,"cells":parts}
        last=i
    return {"lines":lines,"rows":rows,"insert":last+1}


def prepare_table(text,kind,allow_project=False):
    if text is None:
        if not allow_project:
            fail("目标文件不存在")
        text="# 作品约定\n\n## 实战经验\n\n"+L_TABLE_HEADER+"\n"
    if kind!="normal":
        table(text,kind)
        return text
    lines=text.splitlines(keepends=True)
    bare=[line.rstrip("\n") for line in lines]
    hs=[title for _,_,title in headings(bare) if title in ("实战经验","实战经验（复盘沉淀）")]
    if not hs and allow_project:
        if any(line.startswith("|") and cells(line)==L_COLS for line in bare):
            fail("旧作品表没有章节边界，需人工核对后迁移")
        text+=("" if text.endswith("\n") else "\n")+"\n## 实战经验\n\n"+L_TABLE_HEADER+"\n"
    else:
        start,end=section(bare)
        existing=[i for i in range(start,end) if bare[i].startswith("|")]
        if not existing:
            lines.insert(end,"\n"+L_TABLE_HEADER+"\n\n")
            text="".join(lines)
    table(text)
    return text


def change_row(text,kind,key,before,after):
    info=table(text,kind)
    hit=info["rows"].get(str(key))
    actual=hit["line"] if hit else None
    if actual!=before:
        fail(f"目标行 {key} 已被后续修改；停止，不能盲目撤销或覆盖")
    lines=info["lines"]
    if hit:
        if after is None:
            del lines[hit["index"]]
        else:
            ending="\n" if lines[hit["index"]].endswith("\n") else ""
            lines[hit["index"]]=after+ending
    elif after is not None:
        lines.insert(info["insert"],after+"\n")
    return "".join(lines)


def check_level(level,count,evidence):
    if level not in LEVELS:
        fail("等级只能是观察或规则")
    if not isinstance(count,int) or count<1:
        fail("次数必须为正整数")
    if level=="规则" and count<2 and not (evidence and evidence.strip()):
        fail("标规则或已证实需要次数 ≥2 或 --evidence 明确证据")


def confirm(value):
    if not value or not value.strip():
        fail("缺少 --confirm 确认原话")


def atomic_text(path,text):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,suffix=".tmp")
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        replace_file(Path(name),path)
    finally:
        Path(name).unlink(missing_ok=True)


def replace_file(source,destination):
    deadline=time.monotonic()+10
    while True:
        try:
            os.replace(source,destination)
            return
        except PermissionError:
            if time.monotonic()>=deadline:
                raise
            time.sleep(.05)


def git_env():
    names=("PATH","SystemRoot","WINDIR","COMSPEC","PATHEXT","USERPROFILE","HOMEDRIVE","HOMEPATH",
           "APPDATA","LOCALAPPDATA","TEMP","TMP","GIT_AUTHOR_NAME","GIT_AUTHOR_EMAIL",
           "GIT_COMMITTER_NAME","GIT_COMMITTER_EMAIL","GIT_CONFIG_GLOBAL","GIT_CONFIG_NOSYSTEM")
    return {**{k:os.environ[k] for k in names if k in os.environ},"GIT_OPTIONAL_LOCKS":"0"}


def git(root,*args,env=None,data=None,check=True):
    p=subprocess.run(["git","-C",str(root),*map(str,args)],input=data,env=env or git_env(),capture_output=True)
    if check and p.returncode:
        fail("Git 操作失败（未报告为成功）："+p.stderr.decode("utf-8","replace")[-400:])
    return p


class Store:
    def __init__(self,tools):
        self.tools=Path(tools).resolve()
        self.root=self.tools.parent
        self.ledger=self.root/"经验库.md"
        self.operations=self.root/"经验操作"
        self.receipts=self.operations/"提交回执"
        self.lock=self.root/"经验操作.lock"
        self.paths={"manual":self.tools/"镜头语言手册.md",
                    "nexorai":self.tools/"skills/nexorai-image/SKILL.md",
                    "postproduction":self.tools/"skill/references/post-production.md",
                    "pitfall":self.tools/"skills/minimax-h3-video/references/pitfalls.md"}

    def read(self,path,missing=False):
        if not Path(path).resolve().is_relative_to(self.root):
            fail("经验文件路径越界，不读取")
        if not path.exists() and missing:
            return None
        raw=path.read_bytes()
        if b"\r" in raw or b"\x00" in raw:
            fail(f"{path} 不是 LF 文本；不自动改写文件编码")
        return raw.decode("utf-8")

    def target(self,a):
        if a.target=="genre":
            if a.genre not in GENRES:
                fail("--genre 不在允许清单")
            p=self.tools/"genres"/f"{a.genre}.md"
        elif a.target=="project":
            if not a.project:
                fail("--target project 需要 --project")
            project=Path(a.project).resolve()
            if project.parent!=self.root/"作品" or not project.is_dir():
                fail("作品必须位于当前漫剧根的作品目录下一层，不能跨根或越界")
            p=project/"作品约定.md"
        else:
            p=self.paths[a.target]
        return self.scoped(p)

    def scoped(self,p,aux=False):
        p=Path(p).resolve()
        allowed=set(x.resolve() for x in self.paths.values())|{(self.tools/"genres"/f"{g}.md").resolve() for g in GENRES}
        project=(p.name=="作品约定.md" and p.parent.parent==self.root/"作品")
        if aux and p==self.ledger:
            return p
        if not p.is_relative_to(self.root) or p not in allowed and not project:
            fail("经验目标路径越界或不在白名单")
        return p

    def record_path(self,lid):
        if not re.fullmatch(r"L\d{3,}",lid):
            fail("非法经验操作编号")
        p=self.operations/f"{lid}.json"
        if not p.resolve().is_relative_to(self.root):
            fail("经验操作目录越界")
        return p

    def records(self):
        result=[]
        for p in sorted(self.operations.glob("L*.json")):
            self.record_path(p.stem)
            rec=runtime.read_json(p)
            if rec.get("id")!=p.stem or rec.get("version")!=1 or rec.get("phase") not in ("prepared","committed"):
                fail("经验操作记录损坏")
            self.record_path(rec["id"])
            primary=rec["primary"]
            target=self.scoped(self.root/primary["path"])
            if primary["table"] not in ("normal","pitfall") or (primary["table"]=="pitfall")!=(target==self.paths["pitfall"]):
                fail("经验操作的表格类型不符")
            if any(c["path"] not in (rec["primary"]["path"],"经验库.md") for c in rec["changes"]):
                fail("经验事务参与文件越出本次目标")
            result.append(rec)
        return sorted(result,key=lambda r:int(r["id"][1:]))

    def record(self,lid):
        p=self.record_path(lid)
        if not p.is_file():
            fail("旧登记没有操作恢复证据；请人工核对，不猜测撤销")
        return next((r for r in self.records() if r["id"]==lid),None)

    def ledger_text(self):
        text=self.read(self.ledger,True)
        if text is None:
            return "# 经验库（登记表）\n\n"+row(R_COLS)+"\n|---|---|---|---|---|---|---|---|\n"
        table(text,"registry")
        return text

    def next_id(self):
        ids=[int(k[1:]) for k in table(self.ledger_text(),"registry")["rows"]]
        ids += [int(r["id"][1:]) for r in self.records()]
        return f"L{max(ids,default=0)+1:03d}"

    def active(self,lid,ledger=None):
        hit=table(ledger or self.ledger_text(),"registry")["rows"].get(lid)
        return bool(hit and not hit["cells"][5].startswith("已撤销"))

    def dedup(self,phenomenon):
        compact=lambda s:re.sub(r"\s+","",s)
        key=compact(phenomenon)
        if len(key)<4:
            return
        for lid,hit in table(self.ledger_text(),"registry")["rows"].items():
            other=compact(hit["cells"][4])
            if self.active(lid) and len(other)>=4 and (key[:12] in other or other[:12] in key):
                fail(f"已有相似记录 {lid}；请核对后 update，不重复写入")

    def baseline(self,path,before):
        repo=self.tools if path.is_relative_to(self.tools) else self.root
        if not (repo/".git").exists():
            return {"repo":str(repo),"head":None,"clean":False}
        head=git(repo,"rev-parse","HEAD",check=False)
        if head.returncode:
            return {"repo":str(repo),"head":None,"clean":False}
        old=git(repo,"show",f"HEAD:{path.relative_to(repo).as_posix()}",check=False)
        clean=(sha(before)==hashlib.sha256(old.stdout).hexdigest()) if old.returncode==0 else before is None
        return {"repo":str(repo),"head":head.stdout.decode().strip(),"clean":clean}

    def begin(self,meta,target,before,after,ledger_after,registry_changes=None):
        lid=meta["id"]
        changes=[]
        for path,old,new in [(target,before,after),(self.ledger,self.read(self.ledger,True),ledger_after)]:
            if old==new:
                continue
            a=old or ""
            patches=[{"offset":i,"before":a[i:j],"after":new[x:y]} for tag,i,j,x,y in
                     difflib.SequenceMatcher(a=a,b=new,autojunk=False).get_opcodes() if tag!="equal"]
            changes.append({"path":path.relative_to(self.root).as_posix(),"before_hash":sha(old),"after_hash":sha(new),
                            "edits":patches,"baseline":self.baseline(path,old)})
        record={"version":1,"phase":"prepared","date":today(),**meta,"changes":changes,
                "registry_changes":registry_changes or []}
        runtime.atomic_json(self.record_path(lid),record)
        self.apply(record)
        return record

    def apply(self,record):
        if record["phase"]=="committed":
            return
        prepared=[]
        # Validate every participant before changing any additional file.
        for change in record["changes"]:
            path=self.scoped(self.root/change["path"],aux=True)
            current=self.read(path,True)
            if sha(current)==change["after_hash"]:
                continue
            if sha(current)!=change["before_hash"]:
                fail(f"事务 {record['id']} 的参与文件被改动；保留证据并停止恢复")
            text=current or ""
            for edit in sorted(change["edits"],key=lambda e:e["offset"],reverse=True):
                at=edit["offset"]
                if text[at:at+len(edit["before"])]!=edit["before"]:
                    fail("经验事务片段校验失败")
                text=text[:at]+edit["after"]+text[at+len(edit["before"]):]
            if sha(text)!=change["after_hash"]:
                fail("经验事务结果指纹不匹配")
            prepared.append((path,text))
        for path,text in prepared:
            atomic_text(path,text)
        record["phase"]="committed"
        runtime.atomic_json(self.record_path(record["id"]),record)

    def recover(self):
        recovered=[]
        for record in self.records():
            if record["phase"]!="committed":
                self.apply(record)
                recovered.append(record["id"])
        rows=table(self.ledger_text(),"registry")["rows"]
        if any(r["id"] not in rows for r in self.records() if r["phase"]=="committed"):
            fail("经验登记与操作记录不一致，停止写入，不能重建或清空登记")
        return recovered

    def register(self,lid,source,path,summary,level,approval,ledger=None):
        text=ledger or self.ledger_text()
        new=row([lid,today(),source,path.relative_to(self.root).as_posix(),summary,level,approval,f"见经验操作/{lid}.json"])
        return change_row(text,"registry",lid,None,new)

    def add(self,a):
        check_level(a.level,a.count,a.evidence)
        if a.target=="pitfall":
            fail("新增坑请用 pitfall --new-pitfall")
        if not a.source.strip() or not a.phenomenon.strip() or not a.fix.strip():
            fail("source/phenomenon/fix 不能为空")
        self.dedup(a.phenomenon)
        path=self.target(a)
        before=self.read(path,True)
        text=prepare_table(before,"normal",a.target=="project")
        if len(table(text)["rows"])>=MAX_ROWS:
            fail("实战经验已达上限 20，请先经确认合并")
        lid=self.next_id()
        new=row([lid,today(),a.source,a.phenomenon,a.fix,a.level,a.count])
        after=change_row(text,"normal",lid,None,new)
        meta={"id":lid,"kind":"add","source":a.source,"confirm":a.confirm,"evidence":a.evidence,
              "primary":{"path":path.relative_to(self.root).as_posix(),"table":"normal","key":lid,"before":None,"after":new}}
        self.begin(meta,path,before,after,self.register(lid,a.source,path,a.phenomenon,a.level,a.confirm))
        return {"written":lid,"target":meta["primary"]["path"],"row":new,"git":None}

    def update(self,a):
        previous=self.record(a.lesson_id)
        if not self.active(a.lesson_id):
            fail("该经验操作已撤销")
        primary=previous["primary"]
        if primary["table"]!="normal":
            fail("坑表用 pitfall --row 更新")
        path=self.scoped(self.root/primary["path"])
        before=self.read(path)
        hit=table(before)["rows"].get(primary["key"])
        if not hit:
            fail("目标经验行不存在")
        old=hit["cells"]
        count=a.count if a.count is not None else int(old[6])
        level=a.level or old[5]
        check_level(level,count,a.evidence)
        if a.fix is not None and not a.fix.strip():
            fail("规避方法不能为空")
        if all(x is None for x in (a.fix,a.count,a.level,a.evidence)):
            fail("update 需要至少一项更新内容")
        new=set_cells(hit["line"],{4:a.fix if a.fix is not None else old[4],5:level,6:count})
        after=change_row(before,"normal",primary["key"],hit["line"],new)
        lid=self.next_id()
        meta={"id":lid,"kind":"update","source":a.source or old[2],"confirm":a.confirm,"evidence":a.evidence,
              "primary":{**primary,"before":hit["line"],"after":new}}
        self.begin(meta,path,before,after,self.register(lid,meta["source"],path,f"更新 {primary['key']}：{old[3]}",level,a.confirm))
        return {"written":lid,"updated":primary["key"],"git":None}

    def pitfall(self,a):
        if not a.source.strip():
            fail("source 不能为空")
        path=self.paths["pitfall"]
        before=self.read(path)
        info=table(before,"pitfall")
        check_level(a.level,a.count,a.evidence)
        if a.new_pitfall:
            if not a.phenomenon or not a.phenomenon.strip() or not a.fix or not a.fix.strip():
                fail("新增坑需要 phenomenon 和 fix")
            self.dedup(a.phenomenon)
            key_text=re.sub(r"\s+","",a.phenomenon)
            for existing in info["rows"].values():
                other=re.sub(r"\s+","",existing["cells"][1])
                if len(key_text)>=4 and len(other)>=4 and (key_text[:12] in other or other[:12] in key_text):
                    fail("坑表已有相似记录，请更新已有坑，不重复新增")
            key=str(max(map(int,info["rows"]),default=0)+1)
            status=f"观察（{a.count} 次）" if a.level=="观察" else f"已证实（{a.source}，{a.count} 次）"
            old=None
            new=row([key,a.phenomenon,a.cause or "待分析",a.fix,a.evidence or f"复盘（{a.source}）",status])
            summary=f"坑表新增 #{key}：{a.phenomenon}"
            kind="pitfall-add"
        else:
            key=str(a.row)
            hit=info["rows"].get(key)
            if not hit:
                fail("坑表编号不存在")
            if not a.status or not a.status.strip():
                fail("更新坑表需要 --status")
            status=a.status.strip()
            if status.startswith(("已证实","规则")):
                check_level("规则",a.count,a.evidence)
            elif status not in ("待验证","未证实","无效") and not status.startswith("观察"):
                fail("未知坑表状态，不能绕过等级/证据检查")
            old=hit["line"]
            new=set_cells(old,{5:status})
            summary=f"坑 #{key} 状态：{hit['cells'][5]} → {status}"
            kind="pitfall-update"
        lid=self.next_id()
        after=change_row(before,"pitfall",key,old,new)
        meta={"id":lid,"kind":kind,"source":a.source,"confirm":a.confirm,"evidence":a.evidence,
              "primary":{"path":path.relative_to(self.root).as_posix(),"table":"pitfall","key":key,"before":old,"after":new}}
        self.begin(meta,path,before,after,self.register(lid,a.source,path,summary,a.level if a.new_pitfall else "状态更新",a.confirm))
        return {"written":lid,"pitfall_no":int(key),"row":a.row,"old":cells(old)[5] if old else None,"new":status,"status":status,"git":None}

    def retract(self,a):
        previous=self.record(a.lesson_id)
        if not self.active(a.lesson_id):
            fail("已经撤销过，不能重复撤销")
        primary=previous["primary"]
        for later in self.records():
            p=later["primary"]
            if (int(later["id"][1:])>int(a.lesson_id[1:]) and later["kind"]!="retract" and self.active(later["id"])
                    and (p["path"],p["table"],p["key"])==(primary["path"],primary["table"],primary["key"])):
                fail("有后续依赖操作，先撤销后续修改")
        path=self.scoped(self.root/primary["path"])
        before=self.read(path)
        after=change_row(before,primary["table"],primary["key"],primary["after"],primary["before"])
        ledger=self.ledger_text()
        related=[]
        # Undoing a retraction restores the earlier registry status too.
        if previous["kind"]=="retract":
            for edit in reversed(previous["registry_changes"]):
                ledger=change_row(ledger,"registry",edit["key"],edit["after"],edit["before"])
                related.append({"key":edit["key"],"before":edit["after"],"after":edit["before"]})
        hit=table(ledger,"registry")["rows"].get(a.lesson_id)
        marked=set_cells(hit["line"],{5:f"已撤销（{a.confirm}）"})
        ledger=change_row(ledger,"registry",a.lesson_id,hit["line"],marked)
        related.append({"key":a.lesson_id,"before":hit["line"],"after":marked})
        lid=self.next_id()
        meta={"id":lid,"kind":"retract","source":previous["source"],"confirm":a.confirm,"retracts":a.lesson_id,
              "primary":{**primary,"before":primary["after"],"after":primary["before"]}}
        ledger=self.register(lid,previous["source"],path,f"撤销 {a.lesson_id}","撤销操作",a.confirm,ledger)
        self.begin(meta,path,before,after,ledger,related)
        return {"written":lid,"retracted":a.lesson_id,"git":None}

    def list(self,a):
        path=self.target(a)
        text=self.read(path,True)
        if text is None:
            fail("目标文件不存在")
        info=table(prepare_table(text,"pitfall" if a.target=="pitfall" else "normal",a.target=="project"),
                   "pitfall" if a.target=="pitfall" else "normal")
        return {"file":str(path),"rows":len(info["rows"]),"items":[h["line"] for h in info["rows"].values()],
                "pending":[r["id"] for r in self.records() if r["phase"]!="committed"]}

    def commit(self,a):
        try:
            return commit_operation(self,a)
        except SystemExit as e:
            if a.execute and (self.receipts/f"{a.lesson_id}.json").exists():
                fail(f"{e}；请检查 Git 提交回执，可能有部分仓库已提交；没有自动回滚或清理")
            raise

    def pending_git(self):
        pending=[]
        for p in self.receipts.glob("L*.json"):
            if not p.resolve().is_relative_to(self.root):
                fail("Git 回执目录越界，不读取")
            if runtime.read_json(p).get("phase")!="complete":
                pending.append(p.stem)
        return pending



def binary_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def git_plan(store,record,completed=None,owned_index=None):
    completed=completed or {}
    if record["phase"]!="committed" or any(r["phase"]!="committed" for r in store.records()):
        fail("先 recover 完成文件事务，再考虑 Git 提交")
    groups={}
    blockers=[]
    for change in record["changes"]:
        path=store.scoped(store.root/change["path"],aux=True)
        baseline=change["baseline"]
        repo=Path(baseline["repo"])
        expected=store.tools if path.is_relative_to(store.tools) else store.root
        if repo!=expected:
            fail("操作记录仓库归属损坏")
        g=groups.setdefault(str(repo),{"repo":str(repo),"head":baseline["head"],"files":[]})
        if not baseline["clean"] or not baseline["head"]:
            blockers.append(f"{path.name} 在经验操作前已有未提交改动或不在已初始化仓库")
        if g["head"]!=baseline["head"]:
            blockers.append("操作参与文件的仓库基线不一致")
        if binary_sha(path)!=change["after_hash"]:
            blockers.append(f"{path.name} 在经验操作后又被修改")
        g["files"].append({"path":path.relative_to(repo).as_posix(),"sha256":change["after_hash"]})
    # The immutable completed operation record belongs to the data repository.
    rp=store.record_path(record["id"])
    data=groups.get(str(store.root))
    if data is None:
        fail("操作记录缺少数据仓库参与者")
    data["files"].append({"path":rp.relative_to(store.root).as_posix(),"sha256":binary_sha(rp)})
    for g in groups.values():
        repo=Path(g["repo"])
        head=git(repo,"rev-parse","HEAD",check=False)
        if head.returncode or head.stdout.decode().strip()!=completed.get(g["repo"],g["head"]):
            blockers.append(f"{repo.name} HEAD 已变化")
        if git(repo,"diff","--cached","--quiet",check=False).returncode:
            blockers.append(f"{repo.name} 已有暂存改动，不自动清理或夹带")
        branch=git(repo,"symbolic-ref","-q","HEAD",check=False)
        if branch.returncode:
            blockers.append("不支持分离 HEAD 的自动提交")
        else:
            g["ref"]=branch.stdout.decode().strip()
        index=git(repo,"rev-parse","--git-path","index",check=False)
        gdir=git(repo,"rev-parse","--absolute-git-dir",check=False)
        if index.returncode or gdir.returncode:
            blockers.append("无法定位仓库索引")
            continue
        g["index"]=str((repo/index.stdout.decode().strip()).resolve())
        g["git_dir"]=str(Path(gdir.stdout.decode().strip()).resolve())
        if Path(g["index"]+".lock").exists() and g["index"]!=owned_index:
            blockers.append("Git 索引已有锁，保留原锁并停止提交")
        if not Path(g["index"]).is_relative_to(store.root) or not Path(g["git_dir"]).is_relative_to(store.root):
            blockers.append("Git 管理目录不在当前漫剧根内，需人工处理")
        hooks=git(repo,"config","--get","core.hooksPath",check=False)
        sign=git(repo,"config","--get","commit.gpgsign",check=False)
        hook_dir=Path(g["git_dir"])/"hooks"
        active_hooks=hook_dir.is_dir() and any(p.is_file() and not p.name.endswith(".sample") for p in hook_dir.iterdir())
        if hooks.stdout.strip() or active_hooks:
            blockers.append("仓库有自定义提交钩子，需人工审阅并提交，不跳过钩子")
        if sign.stdout.strip().lower() in (b"true",b"yes",b"1",b"on"):
            blockers.append("仓库要求签名提交，需用户手动提交，不降级签名设置")
    return {"operation":record["id"],"preview":True,"repositories":list(groups.values()),"blockers":blockers}


def finish_git_group(store,receipt,g):
    repo=Path(g["repo"])
    # Receipt paths must still refer to the actual repository, not arbitrary files.
    current_index=(repo/git(repo,"rev-parse","--git-path","index").stdout.decode().strip()).resolve()
    if str(current_index)!=g["index"] or not current_index.is_relative_to(store.root):
        fail("Git 回执索引路径不符，停止恢复")
    index=current_index
    lock=Path(str(index)+".lock")
    head=git(repo,"rev-parse","HEAD").stdout.decode().strip()
    if head==g["new_head"] and binary_sha(index)==g["ready_hash"] and not lock.exists():
        g["phase"]="complete"
    else:
        if head not in (g["head"],g["new_head"]) or binary_sha(index)!=g["index_before_hash"]:
            fail("Git HEAD/索引在提交中断后改变；保留回执，人工核对")
        if binary_sha(lock)!=g["ready_hash"]:
            fail("Git 索引锁状态不明；不删除或覆盖未知锁")
        if head==g["head"]:
            git(repo,"update-ref",g["ref"],g["new_head"],g["head"])
        replace_file(lock,index)
        g["phase"]="complete"
    runtime.atomic_json(store.receipts/f"{receipt['operation']}.json",receipt)


def prepare_git_group(store,receipt,g):
    repo=Path(g["repo"])
    index=Path(g["index"])
    lock=Path(str(index)+".lock")
    fd=None
    temp=None
    ready=False
    created=False
    lock_identity=None
    owned_hash=hashlib.sha256(b"").hexdigest()
    try:
        # A native Git index.lock excludes concurrent git add/commit operations.
        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        created=True
        st=os.fstat(fd)
        lock_identity=(st.st_dev,st.st_ino)
        completed={p["repo"]:p["new_head"] for p in receipt["repositories"] if p["phase"]=="complete"}
        again=git_plan(store,store.record(receipt["operation"]),completed,g["index"])
        if again["blockers"]:
            fail("；".join(again["blockers"]))
        g["index_before_hash"]=binary_sha(index)
        handle,name=tempfile.mkstemp(dir=g["git_dir"],prefix="lessons-index-",suffix=".tmp")
        os.close(handle)
        temp=Path(name)
        temp.unlink()  # Git read-tree expects an absent or valid index, not an empty file.
        env={**git_env(),"GIT_INDEX_FILE":str(temp)}
        git(repo,"read-tree",g["head"],env=env)
        for item in g["files"]:
            path=repo/item["path"]
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=item["sha256"]:
                fail("待提交内容改变，停止提交")
            oid=git(repo,"hash-object","-w","--stdin",data=raw).stdout.decode().strip()
            tree=git(repo,"ls-tree",g["head"],"--",item["path"]).stdout.decode("utf-8")
            mode=tree.split()[0] if tree.strip() else "100644"
            if mode not in ("100644","100755"):
                fail("不提交符号链接或子模块目标")
            git(repo,"update-index","--add","--cacheinfo",mode,oid,item["path"],env=env)
        tree=git(repo,"write-tree",env=env).stdout.decode().strip()
        message=f"经验 {receipt['operation']}：已确认的精确变更\n".encode("utf-8")
        g["new_head"]=git(repo,"commit-tree",tree,"-p",g["head"],data=message).stdout.decode().strip()
        ready_index=temp.read_bytes()
        g["ready_hash"]=hashlib.sha256(ready_index).hexdigest()
        owned_hash=g["ready_hash"]
        with os.fdopen(fd,"wb") as f:
            fd=None
            f.write(ready_index)
            f.flush()
            os.fsync(f.fileno())
        g["phase"]="ready"
        runtime.atomic_json(store.receipts/f"{receipt['operation']}.json",receipt)
        ready=True
        finish_git_group(store,receipt,g)
    finally:
        if fd is not None:
            os.close(fd)
        if created and not ready and lock.exists():
            # We created this lock exclusively and never advanced HEAD. Do not
            # remove a pre-existing lock (fd creation would have failed).
            st=lock.stat()
            if (st.st_dev,st.st_ino)==lock_identity and binary_sha(lock)==owned_hash:
                lock.unlink()
        if temp is not None and temp.exists():
            temp.unlink()


def commit_operation(store,a):
    record=store.record(a.lesson_id)
    receipt_path=store.receipts/f"{a.lesson_id}.json"
    if not receipt_path.resolve().is_relative_to(store.root):
        fail("Git 回执路径越界")
    receipt=runtime.read_json(receipt_path) if receipt_path.exists() else None
    if not a.execute:
        if receipt:
            return {"preview":True,"operation":a.lesson_id,"receipt":receipt}
        return git_plan(store,record)
    with runtime.exclusive_lock(store.lock):
        if any(r["phase"]!="committed" for r in store.records()):
            fail("存在未完成文件事务，先 recover")
        other=[lid for lid in store.pending_git() if lid!=a.lesson_id]
        if other:
            fail("其他 Git 提交未完成，先处理回执")
        if receipt is None:
            plan=git_plan(store,record)
            if plan["blockers"]:
                fail("；".join(plan["blockers"]))
            receipt={"operation":a.lesson_id,"phase":"pending","confirm":a.confirm,
                     "repositories":[{**g,"phase":"pending"} for g in plan["repositories"]]}
            runtime.atomic_json(receipt_path,receipt)
        expected_files={c["path"]:c["after_hash"] for c in record["changes"]}
        expected_files[store.record_path(a.lesson_id).relative_to(store.root).as_posix()]=binary_sha(store.record_path(a.lesson_id))
        for g in receipt["repositories"]:
            repo=Path(g["repo"])
            if repo not in (store.root,store.tools):
                fail("Git 回执仓库不在本操作根内")
            actual_index=(repo/git(repo,"rev-parse","--git-path","index").stdout.decode().strip()).resolve()
            actual_dir=Path(git(repo,"rev-parse","--absolute-git-dir").stdout.decode().strip()).resolve()
            if str(actual_index)!=g["index"] or str(actual_dir)!=g["git_dir"] or not actual_dir.is_relative_to(store.root):
                fail("Git 回执路径不符合实际仓库")
            for item in g["files"]:
                path=(repo/item["path"]).resolve()
                if not path.is_relative_to(repo) or not path.is_relative_to(store.root):
                    fail("Git 回执参与文件越界")
                if expected_files.get(path.relative_to(store.root).as_posix())!=item["sha256"]:
                    fail("Git 回执不属于原经验事务")
            if g["phase"]=="pending":
                prepare_git_group(store,receipt,g)
            elif g["phase"]=="ready":
                finish_git_group(store,receipt,g)
            elif git(Path(g["repo"]),"rev-parse","HEAD").stdout.decode().strip()!=g["new_head"]:
                fail("已提交仓库随后发生变化；保留回执，停止自动继续")
        receipt["phase"]="complete"
        runtime.atomic_json(receipt_path,receipt)
        return {"operation":a.lesson_id,"commits":{g["repo"]:g["new_head"] for g in receipt["repositories"]},
                "note":"提交回执单独保存；两个仓库不宣称整体原子提交"}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="cmd",required=True)
    def target_args(s):
        s.add_argument("--target",choices=TARGETS,required=True)
        s.add_argument("--genre")
        s.add_argument("--project")
    def evidence_args(s,optional=False):
        s.add_argument("--level",default=None if optional else "观察")
        s.add_argument("--count",type=int,default=None if optional else 1)
        s.add_argument("--evidence")
        s.add_argument("--confirm",required=True)
    s=sub.add_parser("add")
    target_args(s)
    for flag in ("source","phenomenon","fix"):
        s.add_argument("--"+flag,required=True)
    evidence_args(s)
    s=sub.add_parser("update")
    s.add_argument("lesson_id")
    s.add_argument("--source")
    s.add_argument("--fix")
    evidence_args(s,True)
    s=sub.add_parser("pitfall")
    group=s.add_mutually_exclusive_group(required=True)
    group.add_argument("--new-pitfall",action="store_true")
    group.add_argument("--row",type=int)
    for flag in ("status","cause","phenomenon","fix"):
        s.add_argument("--"+flag)
    s.add_argument("--source",required=True)
    evidence_args(s)
    s=sub.add_parser("retract")
    s.add_argument("lesson_id")
    s.add_argument("--confirm",required=True)
    s=sub.add_parser("list")
    target_args(s)
    sub.add_parser("status")
    s=sub.add_parser("recover")
    s.add_argument("--confirm",required=True)
    s=sub.add_parser("commit")
    s.add_argument("lesson_id")
    s.add_argument("--execute",action="store_true")
    s.add_argument("--confirm")
    a=p.parse_args()
    store=Store(os.environ.get("LESSONS_TOOLS",Path(__file__).resolve().parent.parent))
    if a.cmd=="list":
        result=store.list(a)
    elif a.cmd=="status":
        result={"operations":[{"id":r["id"],"phase":r["phase"],"kind":r["kind"]} for r in store.records()]}
    elif a.cmd=="commit":
        if a.execute:
            confirm(a.confirm)
        result=store.commit(a)
    else:
        confirm(a.confirm)
        with runtime.exclusive_lock(store.lock):
            if store.pending_git():
                fail("有未完成 Git 提交回执，先显式恢复提交，不继续经验写入")
            recovered=store.recover()
            result={"recovered":recovered} if a.cmd=="recover" else getattr(store,a.cmd)(a)
            if recovered:
                result["recovered"]=recovered
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    try:
        main()
    except (OSError,ValueError) as e:
        fail(f"操作未完成：{type(e).__name__}；检查 prepared 事务或 Git 提交回执，可能有已完成的部分；未自动回滚或清理")
