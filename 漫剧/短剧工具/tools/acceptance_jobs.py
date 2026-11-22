"""Finite trusted jobs for the user-approved Snow Gate acceptance project.

The device manifest pins this program, dependencies, interpreter and exact action.
Agents cannot supply shell commands, paths, parameters, credentials or approvals.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
import authorized_cli as launcher
import proposal_sink as sink

ROOT = Path(__file__).resolve().parents[1]
CLIPS = ('Clip00001', 'Clip00002', 'Clip00003')
ACTIONS = {'status':'总导演', 'plan-register':'编剧', 'image-character':'美术', 'image-scene':'美术',
           'image-sheet':'美术', 'image-mark':'美术', 'approve-assets':'总导演', 'narration':'剪辑',
           'video-smoke':'摄影', 'voice-analyze':'摄影', 'voice-cut':'摄影', 'approve-voice':'总导演',
           'asr-final':'剪辑', 'assemble':'剪辑', 'approve-film':'总导演',
           'inspect-smoke':'总导演','inspect-film':'总导演'}
for _i in range(1,4):
    for _phase in ('draft','final','redo'):
        ACTIONS[f'video-{_phase}-{_i}']='摄影'
    for _phase in ('draft','final'):
        ACTIONS[f'mark-{_phase}-{_i}']='摄影'
        ACTIONS[f'approve-{_phase}-{_i}']='总导演'
        ACTIONS[f'inspect-{_phase}-{_i}']='总导演'
        if _i>1:
            ACTIONS[f'tail-{_phase}-{_i}']='摄影'
            ACTIONS[f'continuity-{_phase}-{_i}']='摄影'


def role_for(action):
    if action not in ACTIONS: raise ValueError('unapproved fixed action')
    return ACTIONS[action]


def load(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def digest(path):
    with path.open('rb') as source: return hashlib.file_digest(source,'sha256').hexdigest()


def write_new(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(value,ensure_ascii=False,indent=2)+'\n') if not isinstance(value,str) else value
    with path.open('x',encoding='utf-8',newline='\n') as output: output.write(raw)


def status(root):
    states=[]
    for path in sorted(root.glob('tasks/*/attempt-*/state.json')):
        value=load(path)
        states.append({'path':path.relative_to(root).as_posix(), **{k:value.get(k) for k in
                       ('status','task_id','downloaded_to','settlement_pending_reason')}})
    assets=[{k:row.get(k) for k in ('id','status','image','sheet','prompt')} for row in load(root/'assets.json',[])]
    clips=[{k:row.get(k) for k in ('id','status','video','draft','ending_frame','narration')} for row in load(root/'clips.json',[])]
    counter=load(root/'运行记录/执行限额计数.json',{}) or {}
    return {'assets':assets, 'clips':clips,
            'voices':load(root/'voices.json',[]), 'budget':load(root/'approvals/budget.json',[]),
            'caps':counter.get('totals'), 'real_task_states':states}


class Runner:
    def __init__(self, root): self.root=Path(root).resolve(); self.steps=[]

    def cli(self, tool, *args):
        command=[sys.executable,'-I',str(ROOT/'tools/authorized_cli.py'),'--project-scope',str(self.root),tool,*map(str,args)]
        env=launcher.clean_environment(os.environ); env['PYTHONIOENCODING']='utf-8'
        result=subprocess.run(command,cwd=self.root,env=env,capture_output=True,text=True,encoding='utf-8',timeout=3700)
        self.steps.append({'tool':tool,'exitCode':result.returncode,
                           'stdoutSha256':hashlib.sha256(result.stdout.encode()).hexdigest()})
        if result.returncode: raise ValueError(f'fixed {tool} operation stopped (exit {result.returncode}); inspect original task evidence, no automatic new attempt')
        try: return json.loads(result.stdout)
        except ValueError: return {'completed':True,'stdoutSha256':self.steps[-1]['stdoutSha256']}

    def media(self, name, *args):
        binary=launcher.checked_binary(name,launcher.runtime_tools(self.root))
        env=launcher.clean_environment(os.environ)
        result=subprocess.run([binary,*map(str,args)],cwd=self.root,env=env,capture_output=True,text=True,
                              encoding='utf-8',errors='replace',timeout=900)
        if result.returncode: raise ValueError('fixed local media operation failed')
        return result.stdout

    def state(self, role, command, target=None, data=None, fields=None):
        args=['--project',self.root,'--role',role,command]
        if target: args.append(target)
        if data is not None: args+=['--json',json.dumps(data,ensure_ascii=False)]
        for key,value in (fields or {}).items(): args+=['--set',key+'='+json.dumps(value,ensure_ascii=False)]
        return self.cli('state',*args)

    def generate(self, provider, task, submit):
        if not (task/'state.json').exists(): self.cli(provider,*submit)
        result=self.cli('wait','--project',self.root,'--provider',provider,'--task-dir',task,'--max-wait','3500')
        if result.get('status')!='DELIVERED': raise ValueError('generation not delivered and settled; do not resubmit')
        return result

    def latest(self, role, kind, clip):
        binding=load(self.root/'approvals/scope-binding.json')
        if not binding: raise ValueError('trusted live scope binding missing')
        bot=binding['roles'][role]
        folder=self.root/'准备输入/岗位产物'/bot/clip
        files=sorted(folder.glob(kind+'.v*.json'))
        if not files: raise ValueError('actual completed role proposal is required')
        record=sink.verified_record(files[-1])
        origin=record.get('origin')
        if isinstance(origin,dict) and origin.get('source')=='CLI_NATIVE_RESULT':
            if origin.get('simulation') is not False or origin.get('executionMode')!='restricted-inference':
                raise ValueError('simulated or unrestricted CLI receipt cannot enter production jobs')
        if record['scopeId']!=binding['scopeId'] or record['botId']!=bot or record['proposal']['kind']!=kind:
            raise ValueError('proposal scope/role mismatch')
        return record

    def plan_files(self):
        return {'assets':self.root/'准备输入/资产提案.json','clips':self.root/'准备输入/分镜提案.json'}

    def plan_items(self, kind):
        return load(self.plan_files()[kind])

    def plan(self):
        for kind in ('assets','clips'):
            incoming=self.plan_items(kind)
            current=load(self.root/(kind+'.json'),[])
            for item in incoming:
                found=next((row for row in current if row['id']==item['id']),None)
                if found:
                    if any(found.get(k)!=item.get(k) for k in ('id','type','episode','duration','detail') if k in item):
                        raise ValueError('registered plan differs; no overwrite')
                    continue
                self.state('编剧','add-asset' if kind=='assets' else 'add-clip',data=item)
        return self.cli('validate','--project-root',self.root,'--stage','plan')

    def image(self, kind):
        aid,flag={'character':('C00001-A','image'),'scene':('S00001','scene'),'sheet':('C00001-A','sheet')}[kind]
        asset=next(row for row in load(self.root/'assets.json',[]) if row['id']==aid)
        request=self.root/f'requests/{aid}-{flag}.json'; task=self.root/f'tasks/image-{aid}-{flag}/attempt-001'
        if not request.exists():
            args=['prepare','--project',self.root,'--asset',aid,'--kind',flag,'--prompt',self.root/asset['sheet_prompt' if flag=='sheet' else 'prompt'],'--output',request]
            if flag=='sheet': args+=['--ref','人物='+str(self.root/asset['image'])]
            self.cli('image',*args)
        return self.generate('image',task,['submit','--request',request,'--task-dir',task,'--execute','--budget',self.root/'approvals/budget.json','--batch','EP001-参考图'])

    def narration(self):
        request=self.root/'单集制作/EP001/旁白/requests.json'
        if not request.exists(): self.cli('tts','prepare','--project',self.root,'--episode','EP001','--catalog',self.root/'approvals/tts-catalog.json')
        results=[]
        for i in (2,3):
            entry=f'Clip{i:05d}-1'; task=self.root/f'tasks/EP001-旁白-{entry}/attempt-001'
            results.append(self.generate('tts',task,['submit','--request',request,'--entry',entry,'--task-dir',task,'--execute',
                           '--budget',self.root/'approvals/budget.json','--batch','EP001-旁白',
                           '--output',self.root/f'单集制作/EP001/旁白/{entry}.wav']))
        return results

    def video_prompt(self, phase, cliprow):
        return self.root/cliprow['prompt']

    def video(self, phase, i):
        clip=CLIPS[i-1]; smoke=phase=='smoke'; cliprow=next(row for row in load(self.root/'clips.json',[]) if row['id']=='EP001/'+clip)
        task=self.root/f'tasks/EP001-{clip}-{phase}/attempt-001'; request=task.parent/'request.json'
        output=self.root/(f'单集制作/EP001/最小验证/{clip}.mp4' if smoke else cliprow['draft']['video'] if phase=='draft' else
                          f'单集制作/EP001/返工/{clip}.mp4' if phase=='redo' else cliprow['video'])
        if not request.exists():
            args=['prepare','--prompt',self.video_prompt(phase,cliprow),'--output',request,'--duration',4 if smoke else 8,
                  '--ratio','9:16','--resolution','480P' if phase in ('smoke','draft') else '768P','--purpose',f'EP001/{clip} {phase}']
            for aid in ('C00001-A','S00001'):
                asset=next(row for row in load(self.root/'assets.json',[]) if row['id']==aid)
                if asset['status']!='approved': raise ValueError('reference assets require actual director technical approval')
                args+=['--ref',aid+'='+str(self.root/asset['image'])]
            if i>1:
                prev=CLIPS[i-2]; prevrow=next(row for row in load(self.root/'clips.json') if row['id']=='EP001/'+prev)
                frame=prevrow['draft']['ending_frame'] if phase=='draft' else prevrow['ending_frame']
                args+=['--ref',prev+'-尾帧='+str(self.root/frame)]
            if phase in ('final','redo') and any(shot.get('dialogue') for shot in cliprow.get('shots', [])):
                voice=next(row for row in load(self.root/'voices.json',[]) if row['character']=='C00001' and row['status']=='approved')
                args+=['--ref','音色参考='+str(self.root/voice['file'])+'@reference_audio']
            self.cli('h3',*args)
        batch={'smoke':'最小验证','draft':'试生成','final':'定稿','redo':'重做'}[phase]
        result=self.generate('h3',task,['submit','--request',request,'--task-dir',task,'--output',output,'--execute',
                             '--budget',self.root/'approvals/budget.json','--batch','EP001-'+batch])
        if not smoke:
            fields={'draft.task_dir' if phase=='draft' else 'task_dir':task.relative_to(self.root).as_posix(),
                    'draft.status' if phase=='draft' else 'status':'generated'}
            if phase=='redo': fields['video']=output.relative_to(self.root).as_posix()
            self.state('摄影','set-clip','EP001/'+clip,fields=fields)
        return {'delivery':result,'inspection':self.inspect('smoke' if smoke else 'draft' if phase=='draft' else 'final',i)}

    def tail(self, phase, i):
        clip=CLIPS[i-1]; report=self.root/f'单集制作/EP001/承接报告/{clip}-{phase}.json'
        result=load(report) if report.exists() else self.cli('tail','--project-root',self.root,'--clip-id','EP001/'+clip,'--pass',phase)
        source=self.root/result['source_video']
        if digest(source)!=result['source_sha256']: raise ValueError('tail source changed')
        self.state('摄影','set-clip',result['continue_from'],fields={('draft.ending_frame' if phase=='draft' else 'ending_frame'):result['ending_frame']})
        return result

    def continuity(self, phase, i):
        clip=CLIPS[i-1]; review=self.latest('摄影','continuity',clip)
        data=review['proposal']['data']; report=load(self.root/f'单集制作/EP001/承接报告/{clip}-{phase}.json')
        frame=self.root/report['ending_frame']
        if digest(frame)!=data['sourceFrameSha256']: raise ValueError('continuity must use the actual current-pass tail')
        row=next(c for c in load(self.root/'clips.json') if c['id']=='EP001/'+clip)
        return self.cli('h3','set-continuity','--prompt',self.root/row['prompt'],'--frame',frame,
                        '--definition',data['lines'][0],'--opening',data['lines'][1])

    def approve(self, target, i=1):
        review=self.latest('总导演','technical-review',CLIPS[i-1]); data=review['proposal']['data']
        if data['target']!=target or data['decision']!='pass': raise ValueError('actual director pass report required')
        evidence=load(self.root/'approvals/review-evidence.json',{})
        for artifact in data['artifacts']:
            entry=evidence.get(artifact['id'],{})
            path=(self.root/entry.get('path','')).resolve()
            if not path.is_relative_to(self.root) or not path.is_file() or digest(path)!=artifact['sha256'] or entry.get('reviewTaskId')!=review['taskId']:
                raise ValueError('technical review evidence changed or belongs to another task')
        if target=='assets':
            required={'C00001-A:image','C00001-A:sheet','S00001:image'}
            if {item['id'] for item in data['artifacts']}!=required: raise ValueError('review must cover character, sheet and scene')
            for row in load(self.root/'assets.json'): self.state('总导演','set-asset',row['id'],fields={'status':'approved'})
        elif target in ('draft','final'):
            prefix=target+':'+CLIPS[i-1]
            if {item['id'] for item in data['artifacts']}!={prefix,prefix+':open',prefix+':middle',prefix+':end'}:
                raise ValueError('review must cover this exact clip and its three real frames')
            self.state('总导演','set-clip','EP001/'+CLIPS[i-1],fields={('draft.status' if target=='draft' else 'status'):'approved'})
        elif target=='voice':
            if {item['id'] for item in data['artifacts']}!={'C00001:voice'}: raise ValueError('voice review target mismatch')
            asr=load(self.root/'reports/voice-template-asr.json')
            if not asr or ''.join(c for c in asr['text'] if c.isalnum())!='这一次我不走了': raise ValueError('voice template real ASR mismatch')
            self.state('总导演','set-voice','C00001',fields={'status':'approved'})
        elif target=='film':
            if {item['id'] for item in data['artifacts']}!={'film','film:open','film:middle','film:end'}:
                raise ValueError('film review must cover the actual rendered film and three frames')
            validation=load(self.root/'单集制作/EP001/成片-v001.validation.json')
            if not validation: raise ValueError('rendered validation report missing')
        write_new(self.root/f'reports/AUTO_QA-{target}-{i}-{review["reportId"]}.json',review)
        return {'status':'AUTO_QA_PASS','sourceTaskId':review['taskId'],'target':target}

    def voice_analyze(self):
        source=self.root/'单集制作/EP001/试生成/Clip00001.mp4'; output=self.root/'reports/voice-anchor-asr.json'
        if not output.exists(): self.cli('align','align',source,'--out',output)
        return load(output)

    def voice_cut(self):
        review=self.latest('摄影','voice-range','Clip00001'); data=review['proposal']['data']
        source=self.root/'单集制作/EP001/试生成/Clip00001.mp4'; output=self.root/'音色/C00001.wav'
        registry=load(self.root/'approvals/review-evidence.json',{}); evidence=registry.get(data['voiceSourceId'],{})
        if evidence.get('sha256')!=digest(source): raise ValueError('voice source differs from reviewed evidence')
        existing=load(self.root/'voices.json',[])
        if existing and any(row.get('start')!=data['startMs']/1000 or row.get('end')!=data['endMs']/1000 for row in existing):
            raise ValueError('voice range changed; preserve old template and explicitly version the correction')
        if output.exists() and not existing: raise ValueError('unregistered existing voice template requires reconciliation, no silent reuse')
        if not output.exists(): self.cli('align','cut-voice','--project',self.root,'--character','C00001','--source',source,
                         '--start',data['startMs']/1000,'--end',data['endMs']/1000,'--out',output)
        if not existing:
            self.state('摄影','add-voice',data={'character':'C00001','file':'音色/C00001.wav','source_clip':'EP001/Clip00001',
                      'start':data['startMs']/1000,'end':data['endMs']/1000,'text':'这一次，我不走了。','forms':['C00001-A'],'status':'generated'})
        asr=self.root/'reports/voice-template-asr.json'
        if not asr.exists(): self.cli('align','align',output,'--out',asr)
        return {'file':'音色/C00001.wav','sha256':digest(output),'sourceReportId':review['reportId']}

    def inspect(self, phase, i=1):
        clip=CLIPS[i-1]
        if phase=='film': source=self.root/'单集制作/EP001/成片-v001.mp4'; prefix='film'
        elif phase=='smoke': source=self.root/'单集制作/EP001/最小验证/Clip00001.mp4'; prefix='smoke'
        else:
            row=next(c for c in load(self.root/'clips.json') if c['id']=='EP001/'+clip)
            source=self.root/(row['draft']['video'] if phase=='draft' else row['video']); prefix=phase+':'+clip
        source=source.resolve()
        if not source.is_relative_to(self.root) or not source.is_file(): raise ValueError('inspection source outside project or missing')
        sha=digest(source); folder=self.root/f'reports/inspect-{phase}-{i}-{sha[:12]}'
        info=json.loads(self.media('ffprobe','-v','quiet','-show_format','-show_streams','-of','json',source))
        duration=float(info['format']['duration']); frames=[]
        for label,at in (('open',.05),('middle',duration/2),('end',max(.05,duration-.1))):
            output=folder/(label+'.jpg'); output.parent.mkdir(parents=True,exist_ok=True)
            if not output.exists(): self.media('ffmpeg','-v','error','-n','-ss',at,'-i',source,'-frames:v','1','-vf','scale=540:-2',output)
            frames.append({'id':prefix+':'+label,'path':output.relative_to(self.root).as_posix(),'sha256':digest(output),'type':'image'})
        report={'id':prefix,'path':source.relative_to(self.root).as_posix(),'sha256':sha,'type':'video','duration':duration,'probe':info,'frames':frames}
        if not (folder/'inspection.json').exists(): write_new(folder/'inspection.json',report)
        return report

    def asr(self):
        reports=[]
        paths=[]; hashes=[]
        for clip in CLIPS:
            row=next(c for c in load(self.root/'clips.json') if c['id']=='EP001/'+clip)
            sha=digest(self.root/row['video']); hashes.append(sha)
            output=self.root/f'reports/{clip}-final-asr-{sha[:12]}.json'; paths.append(output.relative_to(self.root).as_posix())
            if not output.exists(): self.cli('align','align',self.root/row['video'],'--out',output)
            reports.append(load(output))
        normalized=lambda value: ''.join(c for c in value if c.isalnum())
        if normalized(reports[0]['text'])!=normalized('这一次，我不走了。') or any(normalized(r['text']) for r in reports[1:]):
            raise ValueError('real ASR differs from dialogue/silence script; revise or reconcile, no fake subtitle')
        script=self.root/'准备输入/对白字幕原文.txt'
        if not script.exists(): write_new(script,'这一次，我不走了。\n')
        first=next(c for c in load(self.root/'clips.json') if c['id']=='EP001/Clip00001')
        output=self.root/f'单集制作/EP001/字幕/对白-{hashes[0][:12]}.srt'
        if not output.exists(): self.cli('align','subtitle',self.root/first['video'],'--script',script,'--out',output)
        record={'asr':'REAL_LOCAL_ASR','reports':paths,'sourceSha256':hashes,'srt':output.relative_to(self.root).as_posix()}
        marker=self.asr_record_path(hashes)
        if not marker.exists(): write_new(marker,record)
        return record

    def asr_record_path(self, hashes=None):
        if hashes is None:
            rows=load(self.root/'clips.json'); hashes=[digest(self.root/next(c for c in rows if c['id']=='EP001/'+clip)['video']) for clip in CLIPS]
        key=hashlib.sha256(''.join(hashes).encode()).hexdigest()[:16]
        return self.root/f'reports/asr-final-{key}.json'

    def run(self, action):
        role_for(action)
        if action=='status': return status(self.root)
        if action=='plan-register': return self.plan()
        if action.startswith('image-') and action!='image-mark': return self.image(action[6:])
        if action=='image-mark':
            return [self.state('美术','set-asset',a['id'],fields={'status':'generated'}) for a in load(self.root/'assets.json')]
        if action=='approve-assets': return self.approve('assets')
        if action=='narration': return self.narration()
        if action=='video-smoke': return self.video('smoke',1)
        if action=='voice-analyze': return self.voice_analyze()
        if action=='voice-cut': return self.voice_cut()
        if action=='approve-voice': return self.approve('voice')
        if action=='asr-final': return self.asr()
        if action=='assemble':
            record=load(self.asr_record_path())
            if not record: raise ValueError('current final video fingerprints have no real ASR record')
            return self.cli('assemble','--project',self.root,'--episode','EP001','--srt',record['srt'],
                            '--burn-subtitles','--out',self.root/'单集制作/EP001/成片-v001.mp4')
        if action=='approve-film': return self.approve('film')
        if action in ('inspect-smoke','inspect-film'): return self.inspect(action.split('-')[1])
        operation,phase,index=action.split('-'); i=int(index)
        if operation=='video': return self.video(phase,i)
        if operation=='tail': return self.tail(phase,i)
        if operation=='continuity': return self.continuity(phase,i)
        if operation=='mark': return self.state('摄影','set-clip','EP001/'+CLIPS[i-1],fields={('draft.status' if phase=='draft' else 'status'):'generated'})
        if operation=='approve': return self.approve(phase,i)
        if operation=='inspect': return self.inspect(phase,i)
        raise ValueError('unknown fixed job')


def main():
    for stream in (sys.stdout,sys.stderr): stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path)
    parser.add_argument('--action',required=True,choices=tuple(ACTIONS))
    args=parser.parse_args(); root=launcher.scope_root(args.project); runner=Runner(root)
    result=runner.run(args.action)
    receipt={'action':args.action,'role':role_for(args.action),'result':result,'steps':runner.steps,
             'execution_source':'NEXBOT_DEVICE_WHEN_CALLED_BY_SCOPED_DEVICE','not_personal_user_review':True}
    path=root/f'运行记录/固定任务-{args.action}-{uuid.uuid4().hex[:8]}.json'; write_new(path,receipt)
    print(json.dumps({**receipt,'receipt':path.relative_to(root).as_posix()},ensure_ascii=False))


if __name__=='__main__':
    try: main()
    except (ValueError,KeyError,StopIteration) as error: raise SystemExit('STOP: '+str(error))
