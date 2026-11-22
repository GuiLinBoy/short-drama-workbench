#!/usr/bin/env python3
"""One-shot submit/query with a durable local duplicate guard. No automatic retries."""
import argparse,hashlib,json,os,subprocess,tempfile,time
from pathlib import Path

def atomic(path,data):
    fd,name=tempfile.mkstemp(dir=path.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)

def find_ids(value):
    ids=set()
    if isinstance(value,dict):
        for key,v in value.items():
            if key=='submit_id' and isinstance(v,(str,int)) and str(v):ids.add(str(v))
            else:ids.update(find_ids(v))
    elif isinstance(value,list):
        for v in value:ids.update(find_ids(v))
    return ids

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',type=Path);p.add_argument('--task-dir',type=Path,required=True)
    mode=p.add_mutually_exclusive_group();mode.add_argument('--execute',action='store_true');mode.add_argument('--query',action='store_true')
    mode.add_argument('--recover-id');mode.add_argument('--mark-status',choices=['succeeded','failed','cancelled'])
    p.add_argument('--evidence',help='Response evidence for recovered ID or terminal status')
    p.add_argument('--timeout',type=int,default=60);a=p.parse_args()
    if a.timeout<1:p.error('Timeout must be positive')
    task=a.task_dir;state_path=task/'state.json'
    if a.query or a.recover_id or a.mark_status:
        state=json.loads(state_path.read_text(encoding='utf-8'))
        if a.recover_id or a.mark_status:
            if not a.evidence:p.error('Require evidence from actual service response/list_task')
            if a.recover_id:
                if state.get('submit_id') and state['submit_id']!=a.recover_id:p.error('Cannot replace an existing different task ID')
                state.update(submit_id=a.recover_id,status='submitted',evidence=a.evidence)
            else:state.update(status=a.mark_status,evidence=a.evidence)
            atomic(state_path,state);print(json.dumps(state,ensure_ascii=False));return
        if not state.get('submit_id'):p.error('No known ID; reconcile with dreamina list_task first')
        command=['dreamina','query_result','--submit_id',state['submit_id']]
        result=subprocess.run(command,capture_output=True,text=True,timeout=a.timeout)
        (task/'query-response.txt').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        print(result.stdout);print(result.stderr)
        raise SystemExit(result.returncode)
    if not a.request:p.error('--request is required for preview/submit')
    request=json.loads(a.request.read_text(encoding='utf-8'));argv=request['argv']
    allowed={'text2image','image2image','multimodal2video','text2video'}
    if not isinstance(argv,list) or len(argv)<2 or not all(isinstance(v,str) for v in argv) or argv[0]!='dreamina' or argv[1] not in allowed:
        p.error('Invalid generator argv')
    digest=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
    if not a.execute:
        print(json.dumps({'preview':True,'request_sha256':digest,'argv':argv},ensure_ascii=False,indent=2));return
    for ref in request.get('references',[]):
        path=Path(ref['path'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=ref.get('sha256'):
            p.error('Reference changed since preparation; prepare a new request')
    task.mkdir(parents=True,exist_ok=True)
    # Exclusive creation survives interruption; NEVER auto-remove to retry an unknown submit.
    try:
        with (task/'submission.lock').open('x') as f:f.write(digest)
    except FileExistsError:p.error('Attempt already reserved; query or reconcile it, do not resubmit')
    atomic(task/'request.json',request)
    state={'request_sha256':digest,'status':'submitting','submitted_at':time.time(),'submit_id':None}
    atomic(state_path,state)
    try:
        result=subprocess.run(argv,capture_output=True,text=True,timeout=a.timeout)
        (task/'response.txt').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        state['returncode']=result.returncode
        try:ids=find_ids(json.loads(result.stdout))
        except (json.JSONDecodeError,TypeError):ids=set()
        if len(ids)==1:state.update(submit_id=next(iter(ids)),status='submitted')
        else:state['status']='unknown'
    except (subprocess.TimeoutExpired,OSError) as e:
        state.update(status='unknown',error=str(e))
    atomic(state_path,state);print(json.dumps(state,ensure_ascii=False,indent=2))
    if state['status']=='unknown':raise SystemExit(2)
if __name__=='__main__':main()
