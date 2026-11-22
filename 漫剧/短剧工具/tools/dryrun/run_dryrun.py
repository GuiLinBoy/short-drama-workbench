"""Run real CLI tools against loopback stand-ins in a brand-new isolated root.

Default includes a separate real local ASR checkpoint. --skip-local-asr is for
repeatable integration tests and is always recorded as NOT_RUN, never PASS.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

HERE=Path(__file__).resolve().parent
TOOLS=HERE.parents[1]
sys.path.insert(0,str(HERE))
from fixtures import create,json_file,write,hash_file,prompt_text
from mock_services import MockServices

CLI={'state':TOOLS/'tools/pipeline_state.py','image':TOOLS/'skills/nexorai-image/scripts/nexorai_image.py',
     'h3':TOOLS/'skills/minimax-h3-video/scripts/h3_task.py','tts':TOOLS/'tools/audio/narrate.py',
     'align':TOOLS/'tools/audio/align.py','tail':TOOLS/'tools/ending_frame.py',
     'assemble':TOOLS/'tools/audio/assemble.py','validate':TOOLS/'tools/validate_project.py','lessons':TOOLS/'tools/lessons.py'}
GUARD=TOOLS/'tools/run_offline_checks.py'


def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def assert_true(value,message):
    if not value:raise RuntimeError(message)


def audit(event,args):
    if event in ('socket.connect','socket.getaddrinfo'):
        host=args[1][0] if event=='socket.connect' else args[0]
        try:ok=ipaddress.ip_address(host).is_loopback
        except ValueError:ok=host=='localhost'
        if not ok:raise RuntimeError('DRYRUN: external network forbidden')


def snapshot():
    base=TOOLS.parents[1]
    manifest=load(TOOLS/'交接/2026-11-06/文件清单-SHA256.json')
    files=[x['path'] for x in manifest['files'] if x['path'].replace('\\','/').startswith('漫剧/')
           and not x['path'].replace('\\','/').startswith('漫剧/短剧工具/')]
    hashes={p:hash_file(base/p) for p in files}
    inventory=[]
    for folder,dirs,names in os.walk(TOOLS.parent):
        dirs[:]=[d for d in dirs if d not in ('.git','短剧工具')]
        inventory.extend(str((Path(folder)/n).relative_to(TOOLS.parent)) for n in names)
    return {'known_hashes':hashes,'inventory':sorted(inventory)}


class Runner:
    def __init__(self,root,service):
        self.root=root
        self.service=service
        self.steps=[]
        self.payload=0
        scratch=root/'scratch'
        scratch.mkdir()
        names=('PATH','SystemRoot','WINDIR','COMSPEC','PATHEXT','LOCALAPPDATA')
        # Only read whitelisted values; real credentials are not read/inherited.
        self.env={k:os.environ[k] for k in names if k in os.environ}
        self.env.update(TEMP=str(scratch),TMP=str(scratch),PYTHONIOENCODING='utf-8',
                        MINIMAX_H3_API_KEY='sk-ml-DRYRUN-FAKE',NEXORAI_API_KEY='sk-ml-DRYRUN-FAKE',
                        H3_BASE_URL=service.base,NEXORAI_BASE_URL=service.base+'/v1',
                        HF_HUB_OFFLINE='1',MODELSCOPE_OFFLINE='1',
                        GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM='1')
        self.logs=root/'logs'
        self.logs.mkdir()

    def command(self,tool,args,env=None):
        return [sys.executable,'-I',str(GUARD),'--child',str(CLI[tool]),*map(str,args)],{**self.env,**(env or {})}

    def record(self,label,command,p,expected):
        number=len(self.steps)+1
        log=self.logs/f'{number:03d}.log'
        log.write_text('SIMULATED\n'+p.stdout+'\nSTDERR\n'+p.stderr,encoding='utf-8',newline='\n')
        entry={'step':number,'label':label,'command':list(map(str,command)),
               'returncode':p.returncode,'expected':expected,'log':str(log)}
        self.steps.append(entry)
        assert_true(p.returncode==expected,f'{label}: exit {p.returncode}, expected {expected}; see {log}\n{p.stderr[-700:]}')
        try:return json.loads(p.stdout)
        except ValueError:return {'stdout':p.stdout}

    def cli(self,tool,*args,expected=0,env=None,label=None):
        command,environ=self.command(tool,args,env)
        p=subprocess.run(command,env=environ,cwd=TOOLS,capture_output=True,text=True,encoding='utf-8',timeout=240)
        return self.record(label or tool+' '+str(args[0]),command,p,expected)

    def start(self,tool,*args):
        command,env=self.command(tool,args)
        return command,subprocess.Popen(command,env=env,cwd=TOOLS,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')

    def finish(self,label,handle):
        command,p=handle
        out,err=p.communicate(timeout=120)
        return self.record(label,command,subprocess.CompletedProcess(command,p.returncode,out,err),0)

    def media(self,*args):
        command=['ffmpeg','-v','error',*map(str,args)]
        p=subprocess.run(command,env=self.env,cwd=TOOLS,capture_output=True,text=True,encoding='utf-8',timeout=120)
        return self.record('local ffmpeg',command,p,0)

    def state(self,project,role,command,target=None,data=None,sets=()):
        args=['--project',project,'--role',role,command]
        if target:args.append(target)
        if data is not None:
            self.payload+=1
            path=self.root/'commands'/f'{self.payload:04d}.json'
            json_file(path,data)
            args+=['--json-file',path]
        for key,value in sets:
            raw=json.dumps(value,ensure_ascii=False) if isinstance(value,(list,dict,bool,int,float)) or value is None else str(value)
            args+=['--set',key+'='+raw]
        return self.cli('state',*args,label=role+' '+command+' '+str(target or ''))

    def budget(self,p,batch):return next(x['used'] for x in load(p/'approvals/budget.json') if x['batch']==batch)


def init_projects(r,projects):
    for project in projects:
        p=project['root']
        for a in project['assets']:r.state(p,'编剧','add-asset',data=a)
        for c in project['clips']:r.state(p,'编剧','add-clip',data=c)
        r.cli('validate','--project-root',p,'--stage','plan')
        r.cli('tts','prepare','--project',p,'--episode','EP001')


def image_request(r,project,aid,kind):
    p=project['root']
    asset=next(a for a in project['assets'] if a['id']==aid)
    request=p/'requests'/f'{aid}-{kind}.json'
    args=['prepare','--project',p,'--asset',aid,'--kind',kind,'--prompt',p/asset['sheet_prompt' if kind=='sheet' else 'prompt'],'--output',request]
    if kind=='sheet':args+=['--ref','人物='+str(p/asset['image'])]
    r.cli('image',*args)
    return request,p/'tasks'/f'image-{aid}-{kind}'/'attempt-001'


def image_submit_args(project,request,task):
    return ['submit','--request',request,'--task-dir',task,'--execute','--budget',project['root']/'approvals/budget.json','--batch','EP001-参考图']


def images(r,projects):
    a,b=projects
    ar,at=image_request(r,a,'C00001-A','image')
    br,bt=image_request(r,b,'C00001-A','image')
    r.service.image_hold.clear()
    handle=r.start('image',*image_submit_args(a,ar,at))
    try:
        assert_true(r.service.image_started.wait(20),'mock image did not start')
        r.cli('image',*image_submit_args(b,br,bt),expected=4,label='EXPECTED: cross-project image queue')
        assert_true(r.budget(b['root'],'EP001-参考图')==0,'queued image charged budget')
    finally:r.service.image_hold.set()
    r.finish('A image success after queue probe',handle)
    r.cli('image',*image_submit_args(b,br,bt))
    for project in projects:
        p=project['root']
        for aid,kind in [('C00001-A','sheet'),('S00001','scene')]:
            request,task=image_request(r,project,aid,kind)
            r.cli('image',*image_submit_args(project,request,task))
        for a in project['assets']:
            r.state(p,'美术','set-asset',a['id'],sets=[('status','generated')])
            r.state(p,'总导演','set-asset',a['id'],sets=[('status','approved')])


def tts_args(project,index):
    p=project['root']
    entry=f'Clip{index:05d}-1'
    task=p/'tasks'/f'EP001-旁白-{entry}'/'attempt-001'
    out=p/f'单集制作/EP001/旁白/{entry}.wav'
    return ['submit','--request',p/'单集制作/EP001/旁白/requests.json','--entry',entry,'--task-dir',task,
            '--execute','--budget',p/'approvals/budget.json','--batch','EP001-旁白','--output',out],task


def finish_job(r,tool,task,faults=False):
    state=load(task/'state.json')
    job=r.service.jobs[state['task_id']]
    if faults:
        before=r.budget(Path(state['charge']['file']).parents[1],state['charge']['batch'])
        job['query_failures']=1
        r.cli(tool,'query','--task-dir',task,expected=2 if tool=='tts' else 2,label='EXPECTED: temporary query failure')
        assert_true(r.budget(Path(state['charge']['file']).parents[1],state['charge']['batch'])==before,'query failure refunded budget')
    r.cli(tool,'query','--task-dir',task)  # queued remains an occupied slot
    if faults:
        job['download_failures']=1
        r.cli(tool,'query','--task-dir',task,expected=1,label='EXPECTED: download failure after settlement')
    r.cli(tool,'query','--task-dir',task)
    used=r.budget(Path(state['charge']['file']).parents[1],state['charge']['batch'])
    r.cli(tool,'query','--task-dir',task,label='idempotent repeated query')
    assert_true(r.budget(Path(state['charge']['file']).parents[1],state['charge']['batch'])==used,'repeated settlement changed budget')


def video_request(r,project,index,pass_,voice):
    p=project['root'];c=project['clips'][index-1]
    tail=index>1
    # File content belongs to the simulated writer/camera workflow; manifests use CLI only.
    prompt=p/c['prompt']
    prompt.write_text(prompt_text(project['letter'],index,tail,voice),encoding='utf-8',newline='\n')
    refs=[]
    if tail:
        data=r.cli('tail','--project-root',p,'--clip-id',c['id'],'--pass',pass_)
        field='draft.ending_frame' if pass_=='draft' else 'ending_frame'
        r.state(p,'摄影','set-clip',c['continue_from'],sets=[(field,data['ending_frame'])])
        # Recording a new artifact correctly invalidates the previous approval.
        # Simulate the director reviewing this updated artifact before assembly.
        r.state(p,'总导演','set-clip',c['continue_from'],
                sets=[('draft.status' if pass_=='draft' else 'status','approved')])
        frame=p/data['ending_frame']
        r.cli('h3','set-continuity','--prompt',prompt,'--frame',frame,'--definition','SIMULATED: subject remains centered.',
              '--opening','SIMULATED: continue the test pattern from this frame.')
        refs += ['--ref',f'Clip{index-1:05d}-尾帧={frame}']
    for label,rel in [('C00001-A','资产/C00001-A.png'),('S00001','资产/S00001.png')]:
        refs += ['--ref',f'{label}={p/rel}']
    if voice:refs+=['--ref','音色参考='+str(p/'音色/C00001.wav')+'@reference_audio']
    task=p/'tasks'/f'EP001-Clip{index:05d}-{pass_}'/'attempt-001'
    request=task.parent/'request.json'
    r.cli('h3','prepare','--prompt',prompt,'--output',request,'--duration',c['duration'],
          '--ratio',project['project']['ratio'],'--resolution','480P' if pass_=='draft' else '768P',*refs)
    return request,task,p/(c['draft']['video'] if pass_=='draft' else c['video'])


def video_submit(r,project,index,pass_,voice=False,queue=False):
    req,task,out=video_request(r,project,index,pass_,voice)
    r.cli('h3','submit','--request',req,'--task-dir',task,'--output',out,'--execute',
          '--budget',project['root']/'approvals/budget.json','--batch','EP001-试生成' if pass_=='draft' else 'EP001-定稿',
          expected=4 if queue else 0)
    if not queue:r.state(project['root'],'摄影','set-clip',project['clips'][index-1]['id'],
                          sets=[('draft.task_dir' if pass_=='draft' else 'task_dir',task.relative_to(project['root']).as_posix())])
    return task


def mark_video(r,project,index,pass_):
    field='draft.status' if pass_=='draft' else 'status'
    r.state(project['root'],'摄影','set-clip',project['clips'][index-1]['id'],sets=[(field,'generated')])
    r.state(project['root'],'总导演','set-clip',project['clips'][index-1]['id'],sets=[(field,'approved')])


def voices(r,project):
    p=project['root'];source=p/project['clips'][0]['draft']['video']
    r.cli('align','cut-voice','--project',p,'--character','C00001','--source',source,
          '--start','.4','--end','3.8','--out','音色/C00001.wav')
    r.state(p,'摄影','add-voice',data={'character':'C00001','file':'音色/C00001.wav','source_clip':'EP001/Clip00001',
            'start':.4,'end':3.8,'text':'SIMULATED synthetic tone','forms':['C00001-A'],'status':'generated'})
    r.state(p,'总导演','set-voice','C00001',sets=[('status','approved')])


def generation(r,projects,manju):
    a,b=projects
    task=video_submit(r,a,1,'draft')
    args,speech=tts_args(b,1)
    r.cli('tts',*args,expected=4,label='EXPECTED: H3 occupies shared speech pool')
    finish_job(r,'h3',task,True)
    mark_video(r,a,1,'draft')
    r.cli('tts',*args)
    r.cli('tts','query','--task-dir',speech)
    pending=r.cli('h3','pending','--all','--project',manju)
    assert_true(pending['slots_free']==0,'queued speech missing from shared pool')
    # A new H3 attempt is blocked by the queued speech job (opposite direction).
    request=task.parent/'request.json'
    r.cli('h3','submit','--request',request,'--task-dir',a['root']/'tasks/shared-queue-probe/attempt-001',
          '--execute','--budget',a['root']/'approvals/budget.json','--batch','EP001-试生成',expected=4,
          label='EXPECTED: queued speech occupies shared video pool')
    finish_job(r,'tts',speech)
    for project in projects:
        p=project['root']
        # A foreign project's budget is always refused before any provider POST.
        foreign=b['root'] if project is a else a['root']
        req=p/'单集制作/EP001/旁白/requests.json'
        before_jobs=len(r.service.jobs)
        r.cli('tts','submit','--request',req,'--entry','Clip00001-1','--task-dir',p/'tasks/wrong-budget/attempt-001',
              '--execute','--budget',foreign/'approvals/budget.json','--batch','EP001-旁白',expected=2,
              label='EXPECTED: foreign project budget rejected')
        assert_true(len(r.service.jobs)==before_jobs,'foreign budget reached provider')
        for index in range(1,len(project['clips'])+1):
            if project is b and index==1:continue
            args,task=tts_args(project,index)
            r.cli('tts',*args)
            finish_job(r,'tts',task,index==1)
        if project is b:
            task=video_submit(r,project,1,'draft')
            finish_job(r,'h3',task)
            mark_video(r,project,1,'draft')
        voices(r,project)
        for index in range(2,len(project['clips'])+1):
            task=video_submit(r,project,index,'draft',True)
            finish_job(r,'h3',task)
            mark_video(r,project,index,'draft')
        for index in range(1,len(project['clips'])+1):
            task=video_submit(r,project,index,'final',True)
            finish_job(r,'h3',task)
            mark_video(r,project,index,'final')


def finish_projects(r,projects,manju):
    movies=[]
    music=manju/'曲库/模拟配乐.wav'
    music.parent.mkdir(parents=True)
    r.media('-f','lavfi','-i','sine=frequency=440:duration=1',music)
    for project in projects:
        p=project['root']
        r.cli('validate','--project-root',p,'--stage','assets')
        r.cli('validate','--project-root',p,'--stage','assemble')
        # Controlled timestamps verify CLI alignment; not real recognition of tones.
        text='离线测试台词。'
        stamps=[];segments=[];off=0
        for c in project['clips']:
            stamps.extend([[round((off+.8+i*.2)*1000),round((off+.98+i*.2)*1000)] for i in range(6)])
            segments.append([off+.8,off+1.98]);off+=c['duration']
        fixture=p/'reports/asr-STUB.json'
        json_file(fixture,{'text':text*len(project['clips']),'timestamp':stamps,'speech_segments':segments,'source':'ASR_STUB'})
        script=p/'单集制作/EP001/对白.txt';write(script,'\n'.join([text]*len(project['clips'])))
        listing=p/'reports/final-list.txt'
        write(listing,''.join("file '"+(p/c['video']).as_posix()+"'\n" for c in project['clips']))
        audio=p/'reports/mock-dialogue.wav'
        r.media('-f','concat','-safe','0','-i',listing,'-vn','-ac','1','-ar','16000',audio)
        fake={'ASR_FAKE_RESULT':str(fixture)}
        r.cli('align','align',audio,'--out',p/'reports/alignment-STUB.json',env=fake)
        srt=p/'单集制作/EP001/对白.srt'
        r.cli('align','subtitle',audio,'--script',script,'--out',srt,env=fake)
        r.cli('align','check',audio,*sum((['--dialogue',text] for _ in project['clips']),[]),env=fake)
        r.cli('assemble','--project',p,'--episode','EP001','--srt',srt.relative_to(p),'--music',music,'--music-loop','--fps','12')
        movie=p/'单集制作/EP001/成片.mp4'
        report=load(movie.with_suffix('.validation.json'))
        protected={str(path):hash_file(path) for path in (movie,movie.with_suffix('.srt'),movie.with_suffix('.validation.json'))}
        r.cli('assemble','--project',p,'--episode','EP001','--srt',srt.relative_to(p),
              '--music',music,'--music-loop','--fps','12',expected=2,label='EXPECTED: preserve existing final outputs')
        assert_true(all(hash_file(path)==digest for path,digest in protected.items()),'repeat render modified existing outputs')
        for t in (1.2,sum(c['duration'] for c in project['clips'])-1):
            r.media('-ss',t,'-i',movie,'-frames:v','1',p/'reports'/f'frame-{t:.1f}.png')
        write(p/'reports/复盘.md','SIMULATED：仅证明脚本衔接，非真实 bot、声音或生成质量验收。\n')
        r.cli('lessons','add','--target','project','--project',p,'--source',f'SIMULATED/{p.name}',
              '--phenomenon',f'{project["letter"]}号离线演练事务验收','--fix','SIMULATED: preserve project boundaries',
              '--confirm','SIMULATED approval; not user authorization',env={'LESSONS_TOOLS':str(manju/'短剧工具')})
        movies.append({'project':p.name,'movie':str(movie),'subtitle':str(movie.with_suffix('.srt')),
                       'validation':report,'recognition':'ASR_STUB','repeated_render_preserved':True})
    return movies


def accounting(r,projects,manju):
    ledger=[json.loads(line) for line in (manju/'全局账本.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    ids=[e['operation_id'] for e in ledger]
    assert_true(len(ids)==len(set(ids)),'duplicate ledger event IDs')
    totals=[]
    for project in projects:
        p=project['root'];sums={b['batch']:0 for b in load(p/'approvals/budget.json')}
        ledger_sums={batch:0 for batch in sums}
        for path in p.glob('tasks/*/attempt-*/state.json'):
            s=load(path);charge=s['charge']
            assert_true(Path(charge['file'])==p/'approvals/budget.json','task charged foreign budget')
            if s['provider']=='nexorai':amount=charge['images']
            else:
                job=r.service.jobs[s['task_id']]
                assert_true(charge.get('settled') and charge['actual_points']==job['actual'],'task did not settle exact mock usage')
                amount=job['actual']
            sums[charge['batch']]+=amount
        for b in load(p/'approvals/budget.json'):
            assert_true(abs(b['used']-sums[b['batch']])<.00001,'batch and mock usage differ')
        for entry in ledger:
            if entry['project']!=p.name:continue
            if entry['event']=='charge':delta=entry.get('images',entry.get('points',0))
            elif entry['event']=='settle':delta=entry['actual_points']-entry['estimated_points']
            else:raise RuntimeError('unexpected ledger event in successful dryrun')
            ledger_sums[entry['batch']]+=delta
        assert_true(all(abs(ledger_sums[b]-value)<.00001 for b,value in sums.items()),'ledger and provider usage differ')
        totals.append({'project':p.name,'actual':sums,'ledger_net':ledger_sums})
    assert_true(sum(x['actual']['EP001-参考图'] for x in totals)==sum(x['n'] for x in r.service.images),'image ledger and mock differ')
    for e in ledger:assert_true(Path(e['task_dir']).is_relative_to(manju/'作品'),'ledger escaped isolated root')
    return {'matched':True,'projects':totals,'ledger_events':len(ledger),'unique_events':len(ids)}


def local_asr(r):
    from asr_smoke import run_checkpoint
    return run_checkpoint(r)


def main():
    for stream in (sys.stdout,sys.stderr):stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-local-asr',action='store_true')
    parser.add_argument('--output-root',type=Path)
    a=parser.parse_args()
    root=(a.output_root or TOOLS/'.offline-checks'/('dryrun-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])).resolve()
    assert_true(root.is_relative_to(TOOLS/'.offline-checks'),'dryrun root must be inside project .offline-checks')
    root.mkdir(parents=True,exist_ok=False)  # never clear or overwrite an earlier run
    before=snapshot()
    sys.addaudithook(audit)
    service=MockServices(root/'mock-provider').start()
    r=Runner(root,service)
    report={'approvals':'SIMULATED','mock_chain_passed':False,'local_asr':{'status':'NOT_RUN'},'projects':[]}
    error=None
    try:
        manju,projects=create(root)
        init_projects(r,projects)
        images(r,projects)
        generation(r,projects,manju)
        report['projects']=finish_projects(r,projects,manju)
        report['accounting']=accounting(r,projects,manju)
        report['mock_chain_passed']=True
        if not a.skip_local_asr:report['local_asr']=local_asr(r)
    except Exception as e:
        error=f'{type(e).__name__}: {e}'
        report['error']=error
    finally:
        service.close()
        json_file(root/'mock-provider-report.json',service.report())
        json_file(root/'steps.json',r.steps)
        after=snapshot()
        report['real_data_changes']=[p for p,h in before['known_hashes'].items() if after['known_hashes'].get(p)!=h]
        if before['inventory']!=after['inventory']:report['real_data_changes'].append('data inventory changed')
        report.update(run_root=str(root),steps=len(r.steps),production_ready=False,real_bots_tested=False,
                      offline_acceptance_passed=bool(report['mock_chain_passed'] and report['local_asr']['status']=='PASS' and not report['real_data_changes']))
        path=root/'report.json'
        json_file(path,report)
    print(json.dumps({'report':str(path),'mock_chain_passed':report['mock_chain_passed'],
                      'local_asr':report['local_asr']['status'],'error':error},ensure_ascii=False))
    raise SystemExit(int(bool(error or report['real_data_changes'] or not a.skip_local_asr and report['local_asr']['status']!='PASS')))


if __name__=='__main__':main()
