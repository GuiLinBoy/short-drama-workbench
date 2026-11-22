"""Wait on one already submitted task. Never creates or re-posts a generation.

The fixed five-minute poll interval does not consume LLM turns. Deadline expiry
preserves the task and reservation; it is not a platform failure or refund.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]


def fail(message):raise SystemExit('ERROR: '+message)
def read_state(task):
    try:return json.loads((task/'state.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):fail('task state missing or invalid; refusing to submit anything')


def delivered(project,provider,state):
    if state.get('status')!='succeeded':return False
    if provider=='image':
        rows=state.get('deliveries') or []
        expected=(state.get('charge') or {}).get('images')
        if not rows or len(rows)!=expected:return False
        for row in rows:
            path=(project/row.get('delivered','')).resolve()
            if row.get('status')!='delivered' or not path.is_relative_to(project) or not path.is_file():return False
            if hashlib.sha256(path.read_bytes()).hexdigest()!=row.get('sha256'):return False
        return True
    path=Path(state.get('downloaded_to') or '.')
    return path.is_absolute() and path.resolve().is_relative_to(project) and path.is_file() and path.stat().st_size>0


def execution_entry(project):
    project=Path(project).resolve()
    if (project/'协作/工作区.json').exists():
        from codex_workflow import Work
        Work(project)  # Invalid Codex metadata must not fall back to the old entry.
        return ROOT/'tools/codex_cli.py'
    return ROOT/'tools/authorized_cli.py'


def query_once(project,provider,task):
    command=[sys.executable,'-I',str(execution_entry(project)),'--project-scope',str(project),provider,
             'fetch' if provider=='image' else 'query','--task-dir',str(task)]
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=330)
    # Provider CLI persists complete query/attempt evidence; do not re-emit raw responses.
    return result.returncode


def wait_for_generation(project,provider,task,max_wait=3600,query=None,sleep=time.sleep,clock=time.monotonic):
    project=Path(project).resolve();task=Path(task).resolve()
    if provider not in ('h3','tts','image') or not task.is_relative_to(project/'tasks'):
        fail('wait task/provider outside the approved project')
    if type(max_wait) is not int or not 0<max_wait<=3600:fail('wait deadline must be 1..3600 seconds')
    started=clock();query=query or (lambda:query_once(project,provider,task))
    queries=0
    def report(status,state):
        return {'status':status,'provider':provider,'task_dir':str(task),'task_id':state.get('task_id'),
                'query_calls':queries,'waited_seconds':round(clock()-started,2),'create_requests':0,
                'accounting_settled':(state.get('charge') or {}).get('settled')}
    while True:
        state=read_state(task)
        if queries and clock()-started>=max_wait:return report('STILL_PENDING',state)
        if provider!='image' and not state.get('task_id'):return report('UNKNOWN_SUBMISSION',state)
        if provider=='image' and delivered(project,provider,state):return report('DELIVERED',state)
        if provider=='image' and state.get('status') in ('failed','blocked','rejected','bad_image','needs_recompose'):
            return report('TERMINAL_FAILURE',state)
        if provider=='image' and not state.get('pending_urls'):return report('UNKNOWN_OR_UNDELIVERED_IMAGE',state)
        try:code=query()
        except subprocess.TimeoutExpired:code=2
        queries+=1;state=read_state(task)
        if code==0 and delivered(project,provider,state):
            if provider!='image' and state.get('charge') and not state['charge'].get('settled'):
                return report('DELIVERED_ACCOUNTING_PENDING',state)
            return report('DELIVERED',state)
        if state.get('status') in ('failed','blocked','cancelled','rejected','bad_image','needs_recompose'):
            return report('TERMINAL_FAILURE',state)
        remaining=max_wait-(clock()-started)
        if remaining<=0:return report('STILL_PENDING',state)
        sleep(min(300,remaining))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--provider',choices=('h3','tts','image'),required=True)
    parser.add_argument('--task-dir',type=Path,required=True)
    parser.add_argument('--max-wait',type=int,default=3600)
    a=parser.parse_args()
    if execution_entry(a.project).name=='codex_cli.py':
        from codex_workflow import Work
        project=Work(a.project).root
    else:
        from authorized_cli import scope_root
        project=scope_root(a.project)
    result=wait_for_generation(project,a.provider,a.task_dir,a.max_wait)
    report=a.task_dir/('wait-'+uuid.uuid4().hex[:8]+'.json')
    with report.open('x',encoding='utf-8',newline='\n') as output:json.dump(result,output,ensure_ascii=False,indent=2)
    print(json.dumps({**result,'report':str(report)},ensure_ascii=False))
    raise SystemExit(0 if result['status']=='DELIVERED' else 4 if result['status']=='STILL_PENDING' else 2)


if __name__=='__main__':main()
