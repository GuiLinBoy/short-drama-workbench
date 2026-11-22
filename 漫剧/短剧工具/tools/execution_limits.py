"""Optional per-project, conservative submission caps; no network or credentials.

Reservations never refund physical attempt counts or worst-case points. This can
stop early after a crash, but cannot turn an unknown submission into free quota.
Policy changes and missing/corrupt accounting require human reconciliation.
"""
import hashlib
import json
from pathlib import Path
import re

import generation_runtime as rt

COUNTERS=('h3_requests','tts_requests','tts_characters','images','video_revisions')


def config(root):
    path=root/'approvals/execution-limits.json'
    if not path.exists():return None
    try:
        raw=path.read_bytes();data=json.loads(raw)
        if type(data.get('version')) is not int or data['version']!=1:raise ValueError('version')
        caps=data['caps']
        for name in (*COUNTERS,'revisions_per_clip'):
            if type(caps[name]) is not int or caps[name]<0:raise ValueError(name)
        if rt.amount(caps['total_points'])<0:raise ValueError('points')
        if type(data.get('retry_unknown')) is not bool:raise ValueError('retry flag')
        if not isinstance(data.get('allowed_resolutions'),list) or not data['allowed_resolutions']:raise ValueError('resolutions')
        return data,hashlib.sha256(raw).hexdigest()
    except (OSError,ValueError,TypeError,KeyError):rt.fail('execution-limit policy invalid; refusing submission')


def check_retry(task,retry_unknown):
    found=config(Path(task).resolve().parents[2])
    if retry_unknown and found and not found[0]['retry_unknown']:
        rt.fail('this acceptance scope forbids unknown reposts; query/reconcile the original task')


def validate_counter(data,root):
    """Check the summary against immutable reservations, not just valid JSON types."""
    derived={**{key:0 for key in COUNTERS},'reserved_points':rt.amount(0)}
    revisions={};seen=set()
    for operation,entry in data['attempts'].items():
        task=Path(entry['task_dir']).resolve();r=entry['reservation'];kind=r['kind']
        if not task.is_relative_to(root/'tasks') or operation!=rt.identity(task,'execution-limits') or str(task) in seen:
            raise ValueError('reservation identity')
        seen.add(str(task))
        if kind not in ('video','speech','image'):raise ValueError('kind')
        points=rt.amount(r['points'])
        if points<0 or any(type(r[k]) is not int or r[k]<0 for k in ('images','characters')):raise ValueError('amount')
        derived['h3_requests']+=int(kind=='video')
        derived['tts_requests']+=int(kind=='speech')
        derived['tts_characters']+=r['characters'] if kind=='speech' else 0
        derived['images']+=r['images']
        derived['reserved_points']+=points
        clip=r['revision_clip']
        if clip:
            derived['video_revisions']+=1
            revisions[clip]=revisions.get(clip,0)+1
    if any(data['totals'][key]!=derived[key] for key in COUNTERS):raise ValueError('summary differs')
    if rt.amount(data['totals']['reserved_points'])!=derived['reserved_points']:raise ValueError('points differ')
    if data['revisions_by_clip']!=revisions:raise ValueError('revisions differ')


def reserve(task,state,kind,estimate,fields,batch):
    task=Path(task).resolve();root=task.parents[2]
    if not (root/'approvals/execution-limits.json').exists():return
    with rt.exclusive_lock(root/'.execution-limits.lock'):
        policy,policy_hash=config(root)
        limits=policy['caps']
        expected={'video':'minimax-h3','speech':'compshare-indextts','image':'nexorai'}
        if kind not in expected or state.get('provider')!=expected[kind]:rt.fail('execution-limit provider mismatch')
        if kind=='video' and state.get('requested_resolution') not in policy['allowed_resolutions']:
            rt.fail('resolution not authorized for this acceptance scope')
        if 'batch_rules' in policy:
            rule=policy['batch_rules'].get(batch)
            if not isinstance(rule,dict) or rule.get('kind')!=kind:rt.fail('batch not authorized by execution policy')
            if kind=='video' and (state.get('requested_duration') not in rule.get('durations',[])
                                 or state.get('requested_resolution') not in rule.get('resolutions',[])):
                rt.fail('video duration/resolution outside the approved batch')
            if kind=='video' and 'models' in rule and state.get('requested_model') not in rule['models']:
                rt.fail('video model not authorized')
            if kind=='image' and state.get('model') not in rule.get('models',[]):rt.fail('image model not authorized')
        characters=state.get('submitted_characters',0)
        images=fields.get('images',0) if kind=='image' else 0
        if type(characters) is not int or characters<0 or type(images) is not int or images<0:
            rt.fail('invalid execution-limit quantities')
        points=rt.amount(estimate) if kind!='image' else rt.amount(0)
        if points<0:rt.fail('negative execution-limit reservation')
        if kind=='speech' and 'tts_unit_price' in policy:
            billable=state.get('billable_characters')
            if type(billable) is not int or billable<0:rt.fail('speech billable character count missing')
            if points!=rt.amount(max(billable,1))*rt.amount(policy['tts_unit_price']):
                rt.fail('speech estimate does not match the approved verified price')
        if kind=='speech' and 'tts_reservation_per_request' in policy:
            worst=rt.amount(policy['tts_reservation_per_request'])
            if worst<points:rt.fail('speech estimate exceeds conservative request reservation')
            points=worst
        revision=kind=='video' and batch==policy.get('revision_batch')
        clip=None
        if revision:
            match=re.search(r'(EP\d{3})-(Clip\d{5})',task.parent.name)
            if not match:rt.fail('revision task must identify its episode and clip')
            clip='/'.join(match.groups())
        signature={'provider':state['provider'],'request_sha256':state.get('request_sha256'),
                   'kind':kind,'points':str(points),'images':images,'characters':characters,'revision_clip':clip}
        counter=root/'运行记录/执行限额计数.json'
        if not counter.resolve().is_relative_to(root):rt.fail('execution counter escaped project')
        if counter.exists():
            try:
                data=json.loads(counter.read_text(encoding='utf-8'))
                if data['version']!=1 or data['policy_sha256']!=policy_hash:raise ValueError('counter policy')
                if not isinstance(data['attempts'],dict) or not isinstance(data['revisions_by_clip'],dict):raise ValueError('counter shape')
                totals=data['totals']
                for key in COUNTERS:
                    if type(totals[key]) is not int or totals[key]<0:raise ValueError('counter quantity')
                if rt.amount(totals['reserved_points'])<0:raise ValueError('counter points')
                validate_counter(data,root)
            except (OSError,ValueError,TypeError,KeyError):rt.fail('execution counter corrupt or policy changed; do not clear it')
        else:
            if any(root.glob('tasks/*/attempt-*/state.json')):
                rt.fail('execution counter missing while task states exist; reconcile instead of resetting quota')
            data={'version':1,'policy_sha256':policy_hash,'scope':root.name,'attempts':{},'revisions_by_clip':{},
                  'totals':{**{key:0 for key in COUNTERS},'reserved_points':0}}
        operation=rt.identity(task,'execution-limits')
        if operation in data['attempts']:
            if data['attempts'][operation]['reservation']!=signature:rt.fail('execution reservation changed for an existing attempt')
            return
        totals=data['totals']
        next_totals={**totals}
        next_totals['h3_requests']+=int(kind=='video')
        next_totals['tts_requests']+=int(kind=='speech')
        next_totals['tts_characters']+=characters if kind=='speech' else 0
        next_totals['images']+=images
        next_totals['video_revisions']+=int(revision)
        next_points=rt.amount(totals['reserved_points'])+points
        for key in COUNTERS:
            if next_totals[key]>limits[key]:rt.fail('execution limit reached: '+key)
        if next_points>rt.amount(limits['total_points']):rt.fail('execution limit reached: total_points')
        if clip and data['revisions_by_clip'].get(clip,0)+1>limits['revisions_per_clip']:
            rt.fail('execution limit reached: revisions_per_clip')
        next_totals['reserved_points']=float(next_points)
        data['totals']=next_totals
        if clip:data['revisions_by_clip'][clip]=data['revisions_by_clip'].get(clip,0)+1
        data['attempts'][operation]={'task_dir':str(task),'reservation':signature,'status':'reserved_before_network'}
        rt.atomic_json(counter,data)
