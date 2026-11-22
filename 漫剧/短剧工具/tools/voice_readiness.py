"""Voice policy and approved-template checks used by mandatory H3 preflight."""
import json,hashlib,re
from pathlib import Path

def _read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def _sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def _inside(root,name):
    p=(root/name).resolve()
    if not p.is_relative_to(root):raise ValueError('voice prerequisite path escaped project')
    return p
def _review_items(root):
    from canvas_review import ReviewStore
    return ReviewStore(root).snapshot()['items']

def require_voice_templates(root,req,required,cfg,reviews):
    """Check actual templates independently of the candidate's own approval state."""
    root=Path(root).resolve()
    if req['params'].get('mute_audio'):raise ValueError('当前声音路线为原生对白，不允许静音绕过声音基准')
    if any(r['role'] in ('first_frame','last_frame') for r in req['references']) and required:raise ValueError('严格首帧与音频参考不能混用；先调整并确认声音路线')
    voices=_read(root/'voices.json') if (root/'voices.json').exists() else []
    audio=[r for r in req['references'] if r['role']=='reference_audio']
    if len(audio)!=len(required):raise ValueError('实际请求未绑定全部且仅本段说话人的音色模板')
    context=[]
    for char in required:
        found=[v for v in voices if v['character']==char]
        if len(found)!=1 or found[0]['status']!='approved':raise ValueError(f'{char}音色模板尚未确认')
        voice=found[0];path=_inside(root,voice['file'])
        if not path.is_file() or voice.get('fingerprint',{}).get('file')!=_sha(path):raise ValueError(f'{char}音色文件指纹不匹配')
        review_id=cfg.get('voiceReviewIds',{}).get(char)
        sample=next((i for i in reviews if i['id']==review_id),None)
        if not sample or sample.get('kind')!='audio' or sample['status']!='approved' or not sample.get('canApprove'):raise ValueError(f'{char}声音样本未通过画布确认')
        if sample.get('subject')!=char or not any(f.get('path')==path.relative_to(root).as_posix() and f.get('sha256')==_sha(path) for f in sample.get('files',[])):
            raise ValueError(f'{char}登记音色与该人物实际通过的样本不一致')
        matching=[a for a in audio if _inside(root,a['path'])==path and a['sha256']==_sha(path) and a['label']==char+'音色参考']
        if len(matching)!=1 or not re.fullmatch(r'<Audio [1-3]>',matching[0].get('tag','')) or matching[0]['tag'] not in req['prompt']:raise ValueError(f'{char}音色未正确绑定至当前提示词')
        context.append({'voice':voice,'sampleReviewId':review_id,'sampleFingerprint':sample.get('fingerprint')})
    return context

def require_authorized_revoice(root,request_path,req,required,cfg,reviews,selected):
    """A rejected, unadopted sample may be replaced only by an exact user-authorized request."""
    root=Path(root).resolve();request_path=Path(request_path).resolve()
    rel=request_path.relative_to(root).as_posix()
    candidates=[a for a in cfg.get('revoiceRequests',[]) if a.get('request')==rel]
    if not candidates:return None
    if len(candidates)!=1:raise ValueError('重新定音授权不唯一')
    entry=candidates[0];generation=selected.get('generation') or {}
    expected={'request':rel,'requestSHA256':_sha(request_path),'clip':selected.get('subject'),
              'taskDir':generation.get('taskDir'),'batch':generation.get('batch'),
              'duration':req['params'].get('duration'),'resolution':req['params'].get('resolution')}
    character=entry.get('character')
    if not character or required!=[character] or generation.get('tool')!='h3' or any(entry.get(k)!=v for k,v in expected.items()):
        raise ValueError('重新定音授权与本次人物、请求、批次或规格不符')
    if req['params'].get('mute_audio') or any(r['role']!='reference' for r in req['references']) or re.search(r'<Audio\s|音色参考',req['prompt']):
        raise ValueError('无引用重新定音只能使用普通图片和原生对白，不能夹带声音或视频参考')
    if any(a.get('request')==rel for a in cfg.get('bootstrapRequests',[])):
        raise ValueError('重新定音不得冒充首次定音')
    by_id={i['id']:i for i in reviews}
    plan=by_id.get(cfg.get('planReviewId'),{})
    if plan.get('status')!='approved' or not plan.get('canApprove'):
        raise ValueError('重新定音方案尚未确认')
    snapshot=_inside(root,cfg.get('snapshotFile',''))
    authorization=_inside(root,entry.get('authorization',''))
    if not snapshot.is_file() or _read(snapshot)!=cfg or not authorization.is_file() or _sha(authorization)!=entry.get('authorizationSHA256'):
        raise ValueError('重新定音方案或授权文件已变化')
    auth=_read(authorization)
    if (auth.get('status')!='USER_APPROVED_CODEX_MEDIA_EXECUTION' or auth.get('approved') is not True
        or auth.get('projectRoot')!=str(root) or not auth.get('workId') or not auth.get('userQuote')
        or auth.get('voiceDecision')!='regenerate_without_reference' or auth.get('character')!=character
        or any(auth.get(k)!=v for k,v in expected.items())):
        raise ValueError('缺少用户针对本请求的不引用旧声音重生成授权')
    for path in (snapshot,authorization):
        if not any(f.get('path')==path.relative_to(root).as_posix() and f.get('sha256')==_sha(path) for f in plan.get('files',[])):
            raise ValueError('重新定音授权与已审方案未绑定同一文件')
    sample=by_id.get(entry.get('rejectedSampleReviewId'),{})
    if (sample.get('kind')!='audio' or sample.get('subject')!=character or sample.get('status')!='changes_requested'
        or not sample.get('head') or sample.get('fingerprint')!=entry.get('rejectedSampleFingerprint')
        or sample.get('head')!=entry.get('rejectedSampleHead')):
        raise ValueError('未核实用户不采用的同一声音样本及决定')
    source=by_id.get(entry.get('sourceVideoReviewId'),{})
    if (source.get('kind')!='video' or source.get('subject')!=expected['clip'] or source.get('status')!='approved'
        or not source.get('canApprove') or source.get('id') not in sample.get('dependsOn',[])):
        raise ValueError('重新定音的原视频来源或真实批准不匹配')
    voices=_read(root/'voices.json') if (root/'voices.json').exists() else []
    registered=[v for v in voices if v.get('character')==character]
    if len(registered)!=1 or registered[0].get('status')!='generated':
        raise ValueError('重新定音仅支持尚未采用的候选声音，不能替换已采用声线')
    voice=registered[0];path=_inside(root,voice['file'])
    if (voice.get('source_clip')!=expected['clip'] or not path.is_file() or voice.get('fingerprint',{}).get('file')!=_sha(path)
        or not any(f.get('path')==voice['file'] and f.get('sha256')==_sha(path) for f in sample.get('files',[]))):
        raise ValueError('不采用的声音与登记、文件或原视频不一致')
    from voice_lifecycle import safe_audit
    closeout=safe_audit(root,reviews)
    own=[r for r in closeout['characters'] if r['character']==character]
    other=[r for r in closeout['characters'] if r['character']!=character and (not r['preparationComplete'] or r['state']=='adopt')]
    if closeout['errors'] or len(own)!=1 or own[0]['sourceReviewId']!=source['id'] or own[0].get('sampleReviewId')!=sample['id'] or other:
        raise ValueError('重新定音来源无法核对，或其他人物声音收尾仍未完成')
    return {'action':'explicit_revoice','character':character,'authorization':entry['authorization'],
            'authorizationSHA256':entry['authorizationSHA256'],'rejectedSampleReviewId':sample['id'],
            'rejectedSampleHead':sample['head'],'sourceVideoReviewId':source['id'],**expected}

def require_voice_readiness(root,request_path,task_dir,batch):
    root=Path(root).resolve();policy=root/'协作/声音方案/执行规则-v001.json'
    if not policy.exists():raise ValueError('缺少本作品声音前置规则；不能跳过共享音色检查')
    cfg=_read(policy)
    if cfg.get('version')!=1:raise ValueError('voice prerequisite policy version invalid')
    request_path=_inside(root,request_path);task=_inside(root,task_dir);req=_read(request_path)
    if request_path.relative_to(root).as_posix() in cfg.get('disabledRequests',[]):
        raise ValueError('该请求已被用户停用，不得执行')
    reviews=_review_items(root)
    from voice_lifecycle import require_prepared, require_first_voice
    plan=next((i for i in reviews if i['id']==cfg['planReviewId']),None)
    if not plan or plan['status']!='approved' or not plan.get('canApprove'):raise ValueError('声音方案尚未确认，停止视频提交')
    if cfg.get('snapshotFile'):
        if _read(_inside(root,cfg['snapshotFile']))!=cfg or cfg['snapshotFile'] not in [f['path'] for f in plan['files']]:raise ValueError('声音执行规则与已审方案不一致')
    matches=[i for i in reviews if i.get('generation')=={'tool':'h3','batch':batch,'taskDir':task.relative_to(root).as_posix()} and i.get('request',{}).get('path')==request_path.relative_to(root).as_posix()]
    if len(matches)!=1 or matches[0]['status']!='approved' or not matches[0].get('canApprove'):raise ValueError('声音检查无法对应唯一已确认请求')
    clip=matches[0]['subject'];required=cfg['requiredVoices'].get(clip)
    if not isinstance(required,list):raise ValueError('片段缺少说话人声音方案')
    revoice=require_authorized_revoice(root,request_path,req,required,cfg,reviews,matches[0])
    if revoice:return revoice
    require_prepared(root,reviews)
    anchors=[a for a in cfg.get('bootstrapRequests',[]) if a['request']==request_path.relative_to(root).as_posix() and a['requestSHA256']==_sha(request_path) and a['taskDir']==task.relative_to(root).as_posix() and a['batch']==batch and a['clip']==clip]
    if anchors:
        if len(anchors)!=1 or required!=[anchors[0]['character']] or req['params']['duration']!=anchors[0]['duration'] or req['params']['resolution']!=anchors[0]['resolution'] or any(r['role']=='reference_audio' for r in req['references']):raise ValueError('定音例外与本次精确请求不符')
        require_first_voice(root,anchors[0]['character'],reviews)
        return
    require_voice_templates(root,req,required,cfg,reviews)
