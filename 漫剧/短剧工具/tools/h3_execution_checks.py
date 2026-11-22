"""Offline H3 production checks and version-bound internal director review.

This is not a user approval tool. It never writes canvas decisions, budgets,
readiness, clip/voice registries or provider tasks, and never calls a service.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import wave

TOOLS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_ROOT / 'tools'))
VERSION = 1
SOURCES = (
    'OVERRIDES.md', 'skills/minimax-h3-video/SKILL.md',
    'skills/minimax-h3-video/references/prompt-format.md',
    'skills/minimax-h3-video/references/audio.md',
    'skills/minimax-h3-video/references/storyboard.md',
    'skills/minimax-h3-video/references/visual-and-camera.md',
    'skills/minimax-h3-video/references/mode-and-preflight.md',
    'skills/minimax-h3-video/references/pitfalls.md',
    'bots/摄影/SKILL.md', 'bots/总导演/SKILL.md',
    'tools/VOICE_CLOSEOUT.md', 'tools/SHOT_CONTEXT.md',
    'genres/live-action.md', 'genres/urban.md', 'genres/3d-guoman.md',
    '镜头语言手册.md',
)
MANUAL = {
    'reference_roles': '实际查看参考素材，核对身份服装、组合图取舍、背景和参考职责',
    'framing_and_speakers': '核对景别取景范围、人物大小距离、留白纵深、可见嘴部和听者',
    'continuity_and_axis': '读取当前状态，核对上段实际状态、本段接法、给下段留下的状态及相应证据',
    'performance_and_pacing': '逐镜评估动作意图、运镜、对白表演及交接，处理自动检出的风险',
    'spec_and_coverage': '核对当前规格、风格声线、段落覆盖、源素材余量和费用取舍',
    'result_check_plan': '明确生成后检查原音、台词、说话人口型、真人细节、交付尺寸及首中尾',
}
RULES = {
    'request': ('参数/模式/费用结构', 'mode-and-preflight.md §4'),
    'review_version': ('当前请求、批次、attempt及最新画布版本', 'SKILL.md 工作流程3—5'),
    'source_mapping': ('源稿与实际提交文本一致，无占位和未映射标签', 'prompt-format.md §0、3'),
    'references': ('真实附件、SHA、槽位、编号及引用', 'mode-and-preflight.md §2、4'),
    'structure': ('当前模式字段顺序及主体定义', 'prompt-format.md §1—3'),
    'dialogue': ('原对白、标签、标点、声线及说话编号', 'audio.md §1'),
    'audio_binding': ('音频→人物→说话编号以及逐句音频引用', 'audio.md 角色音色；当前作品声音方案'),
    'timing': ('本段时间、递增切点、轮流说话与片尾闭口', 'storyboard.md §6；audio.md 台词量'),
    'silence': ('听者/说完闭口、无对白明确安静', 'audio.md 防止乱说话'),
    'soundscape': ('具体环境声、旁白后期与统一配乐', 'audio.md §2—3'),
    'continuity': ('真实尾帧、已核对承接句、来源及当前文件', 'storyboard.md §4；OVERRIDES.md §4'),
    'style': ('复用已采用片段的风格光线基准', 'storyboard.md §6'),
    'residue': ('清除内部编号、成片绝对时间及执行流程文字', 'prompt-format.md §0；作品既有引用检查'),
}
LABEL = re.compile(r'\[((?:[A-Z]\d{5}(?:-[A-Z]+)?)|(?:Clip\d+-尾帧)|(?:[^\[\]\s]{1,24}参考))\]')
CONT = re.compile(r'\{\{(承接定义|承接开场)(·已核对)?：(.*?)\}\}', re.S)
MEDIA_TAG = re.compile(r'<(Picture|Video|Audio)\s+(\d+)>')
DIALOGUE = re.compile(r'<d>(.*?)</d>', re.S)
TIME = re.compile(r'(?<![\d.])(\d+(?:\.\d+)?)\s*[—–~-]\s*(\d+(?:\.\d+)?)\s*秒')
# Local authoring rule, not a claim about the model's cause of extra speech.
# After </d>, keep closure and visible actions; put delivery instructions before
# that dialogue. Limit the scan to this turn, excluding the next timed action.
POST_DIALOGUE_META = re.compile(
    r'(?:再|再次|重新|重复).{0,12}?(?:说|念|讲|读)(?:出|完)?(?:一|1)(?:次|遍)'
    r'|(?:完整|正常口语|自然口语|正常语速).{0,12}?(?:说|念|讲|读)(?:出|完)?(?:一|1)(?:次|遍)'
    r'|对白(?:时段|时间|窗口).{0,12}?(?:窗口|可用|时长|不是|不代表)'
    r'|提前说完的余下时间|不拖长尾音'
    r'|\b(?:say|speak|repeat|recite)\b[^\n.!?]{0,35}\b(?:again|once|one more time)\b', re.I)
ROOT_FIELDS = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
               'integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music')


def read(path):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('JSON字段重复：' + key)
            out[key] = value
        return out
    def invalid(value):
        raise ValueError('JSON数值不是有限值：' + value)
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique,
                      parse_constant=invalid)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def inside(root, name):
    if not isinstance(name, (str, Path)) or not str(name):
        raise ValueError('缺少本地文件路径')
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('执行检查文件不在本作品目录内')
    return path


def save(path, value):
    from generation_runtime import atomic_json
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, value)


def review_items(root):
    from canvas_review import ReviewStore
    return ReviewStore(root).snapshot()['items']


def sections(body):
    matches = list(re.finditer(r'^(' + '|'.join(ROOT_FIELDS) + r'):\s*', body, re.M))
    return [(m[1], body[m.end():matches[n + 1].start() if n + 1 < len(matches) else len(body)].strip())
            for n, m in enumerate(matches)]


def merged_pairs(pairs):
    out = []
    for who, text in pairs:
        text = re.sub(r'\s+', '', text)
        if out and out[-1][0] == who:
            out[-1][1] += text
        else:
            out.append([who, text])
    return out


def named_character(line, names):
    """Identity names can follow appearance clauses, e.g. '夹克的大强。'."""
    found=[name for name in names if re.search(re.escape(name)+r'(?:[，,。;；\s]|$)',line)]
    return (names[found[0]],found[0]) if len(found)==1 else (None,None)


def expected_dialogue(clip, names):
    pairs = []
    for shot in clip.get('shots', []):
        for line in shot.get('dialogue', '').splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r'\s*([^：:]+)[：:]\s*(.+)', line)
            if not match:
                raise ValueError('清单对白缺少明确说话人')
            name = re.sub(r'[（(].*?[）)]', '', match[1]).strip()
            if name not in names:
                raise ValueError('清单对白人物未登记：' + name)
            pairs.append((names[name], match[2]))
    return merged_pairs(pairs)


def audio_probe(root, path):
    if path.suffix.lower() == '.wav':
        with wave.open(str(path), 'rb') as source:
            return {'seconds': source.getnframes() / source.getframerate(),
                    'channels': source.getnchannels(), 'sampleRate': source.getframerate()}
    # No shell, PATH search, credentials or network; use the already pinned local binary.
    runtime = read(root / 'approvals/runtime-tools.json')['tools']['ffprobe']
    binary = Path(runtime['path'])
    if not binary.is_absolute() or sha(binary) != runtime['sha256']:
        raise ValueError('ffprobe未核实')
    done = subprocess.run([str(binary), '-v', 'error', '-show_entries',
        'format=duration:stream=codec_type,channels,sample_rate', '-of', 'json', str(path)],
        capture_output=True, text=True, encoding='utf-8', timeout=15, check=True)
    info = json.loads(done.stdout)
    streams = [s for s in info.get('streams', []) if s.get('codec_type') == 'audio']
    if len(streams) != 1:
        raise ValueError('参考音频轨不唯一')
    return {'seconds': float(info['format']['duration']), 'channels': streams[0]['channels'],
            'sampleRate': int(streams[0]['sample_rate'])}


def approved_derived_tail(root, selected, tail, video, approved):
    """Verify an exact final frame of an approved video without re-approving it."""
    from canvas_review import ReviewStore
    from authorized_cli import runtime_tools, checked_binary
    try:
        root = Path(root).resolve()
        frame = inside(root, tail['path'])
        video = inside(root, video)
        source_path, source_sha = video.relative_to(root).as_posix(), sha(video)
        if not any(item.get('kind') == 'video' and item.get('status') == 'approved'
                   and item.get('canApprove') is True and any(
                       f.get('path') == source_path and f.get('sha256') == source_sha and f.get('exists')
                       for f in item.get('files', [])) for item in approved):
            return False
        entries = [entry for entry in selected.get('derivedReferences', [])
                   if entry.get('path') == frame.relative_to(root).as_posix()
                   and entry.get('source') == source_path
                   and entry.get('sourceSha256') == source_sha]
        if len(entries) != 1:
            return False
        entry = entries[0]
        if type(entry.get('frameIndex')) is not int or not 0 <= entry['frameIndex'] < 10000:
            return False
        # Reuse byte-for-byte source decoding, not a label or cached approval flag.
        covered, problems = ReviewStore(root)._frame_coverage(
            {'derivedReferences': entries}, {'files': [{'path': entry['path'],
                'exists': frame.is_file(), 'sha256': sha(frame)}]}, approved)
        if problems or entry['path'] not in covered or sha(frame) != tail.get('sha256'):
            return False
        runtime = runtime_tools(root)
        probe = checked_binary(runtime['ffprobe']['path'], runtime)
        result = subprocess.run([probe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-select_streams', 'v:0', '-count_frames', '-show_entries', 'stream=nb_read_frames',
            '-of', 'json', str(video)], capture_output=True, text=True, encoding='utf-8',
            timeout=20, check=True)
        streams = json.loads(result.stdout).get('streams', [])
        return len(streams) == 1 and entry['frameIndex'] == int(streams[0]['nb_read_frames']) - 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, SystemExit):
        return False


def analyze(root, request_path, *, items=None, batch=None, task_dir=None):
    """Recompute checks from actual files. Request.warnings is never trusted alone."""
    root = Path(root).resolve()
    request_path = inside(root, request_path)
    req = read(request_path)
    report = {'version': VERSION, 'request': request_path.relative_to(root).as_posix(),
              'errors': [], 'risks': [], 'rules': {}, 'turns': [], 'inputs': {},
              'professionalRequired': MANUAL.copy(), 'userApprovalChanged': False}
    def check(rule, condition, message):
        report['rules'].setdefault(rule, {'title': RULES[rule][0], 'source': RULES[rule][1], 'passed': True})
        if not condition:
            report['rules'][rule]['passed'] = False
            report['errors'].append({'rule': rule, 'message': message})
        return bool(condition)
    def risk(code, message):
        report['risks'].append({'code': code, 'message': message})
    def pin(path):
        path = inside(root, path)
        report['inputs'][path.relative_to(root).as_posix()] = sha(path)
        return path

    if not check('request', isinstance(req, dict) and isinstance(req.get('params'), dict)
                 and isinstance(req.get('prompt'), str) and isinstance(req.get('references'), list)
                 and all(isinstance(r,dict) for r in req.get('references',[])),
                 '正式请求结构不完整'):
        report['technicalPass'] = False
        return report
    pin(request_path)
    project_path=root/'project.json'
    if project_path.is_file():
        from validate_project import production_config_errors
        for message in production_config_errors(read(pin(project_path))):
            check('request',False,message)
    body, params, refs = req['prompt'], req['params'], req['references']
    duration = params.get('duration')
    duration_ok = check('request', type(duration) is int and 4 <= duration <= 15, '本地H3每段必须为4—15整数秒')
    check('request', req.get('provider') == 'minimax-h3', '请求供应商不是本地H3渠道')
    check('request', params.get('model') in ('MiniMax-H3', 'minimax-h3-lite'), '未识别的模型')
    check('request', params.get('ratio') in ('adaptive','21:9','16:9','4:3','1:1','3:4','9:16'), '画幅无效')
    check('request', params.get('resolution') in ('480P','768P','1080P','2K','4K'), '分辨率无效')
    check('request', type(params.get('use_context_ir')) is bool and type(params.get('mute_audio')) is bool,
          '声音或Context-IR参数必须为布尔值')
    check('request', params.get('use_context_ir') or not params.get('skill_id'), '关闭Context-IR时不能附skill_id')
    check('request', not req.get('warnings'), 'prepare存在未处理警告，不得清空警告后提交')
    check('request', 0 < len(body) <= 7000, '实际提示词为空或超过7000字符')
    if params.get('resolution') != '768P' or params.get('model') != 'MiniMax-H3' or params.get('use_context_ir'):
        risk('nondefault_spec', '偏离默认768P/标准版/关闭Context-IR，核对现有请求审核中的明确取舍')
    billing = req.get('billing', {})
    check('request', isinstance(billing, dict) and type(req.get('estimate_points')) in (int,float)
          and req['estimate_points'] > 0 and billing.get('total_points') == req['estimate_points'], '费用估算缺失或与计费明细不符')

    items = review_items(root) if items is None else items
    rel = report['request']
    matches = [i for i in items if i.get('kind') == 'video_prompt'
               and isinstance(i.get('request'), dict) and i['request'].get('path') == rel]
    selected = matches[0] if len(matches) == 1 else None
    check('review_version', selected is not None, '请求必须对应唯一既有画布视频提示词项')
    clip = None
    if selected:
        report['review'] = {k: selected.get(k) for k in ('id','subject','fingerprint','status','canApprove')}
        generation = selected.get('generation') or {}
        report['generation'] = generation
        check('review_version', generation.get('tool') == 'h3' and bool(generation.get('batch'))
              and bool(generation.get('taskDir')), '既有审核缺少实际批次或attempt')
        if batch is not None:
            check('review_version', generation.get('batch') == batch, '批次与当前请求审核不符')
        if task_dir is not None:
            check('review_version', inside(root, task_dir) == inside(root, generation.get('taskDir','')),
                  'attempt与当前请求审核不符')
        peers = [i for i in items if i.get('subject') == selected.get('subject') and i.get('kind') == 'video_prompt']
        latest = max(peers, key=lambda i: (i.get('submittedAt') or '', i['id']))
        check('review_version', latest['id'] == selected['id'], '同一片段已有更新提示词，旧请求不得继续提交')
        check('review_version', selected.get('canApprove') is True, '当前请求或其上游素材已变化/依赖未满足')
        clips = read(root / 'clips.json')
        found = [c for c in clips if c['id'] == selected['subject']]
        check('review_version', len(found) == 1, '当前片段尚未登记采用，不能把待采用拆段直接执行')
        clip = found[0] if len(found) == 1 else None
        report['reviewDependencies'] = [{k:i.get(k) for k in ('id','fingerprint')}
            for i in items if i['id'] in selected.get('dependsOn', [])]
        # Status changes do not invalidate a content review; changed content does.
        report['clipContext'] = {k:clip.get(k) for k in ('id','shots','continue_from','continuity_reason','source_story')} if clip else None
        if clip and clip.get('source_story'):
            pin(clip['source_story'])
        if clip:
            from shot_context import snapshot
            try:
                report['shotContext'] = snapshot(root, clip['id'], items)
            except (ValueError, OSError, KeyError, TypeError) as error:
                check('continuity', False, '制作前状态读取失败：' + str(error))
    else:
        clips = read(root / 'clips.json') if (root / 'clips.json').is_file() else []

    names = {}
    for asset in read(root / 'assets.json') if (root / 'assets.json').is_file() else []:
        if re.fullmatch(r'C\d{5}(?:-[A-Z]+)?', asset.get('id','')) and asset.get('name'):
            char = asset['id'].split('-')[0]
            check('dialogue', asset['name'] not in names or names[asset['name']] == char, '人物名称不唯一')
            names[asset['name']] = char
    source = None
    try:
        source = pin(req.get('prompt_source', ''))
        report['sourcePrompt'] = source.relative_to(root).as_posix()
        original = source.read_text(encoding='utf-8-sig').strip()
    except (ValueError, OSError):
        original = ''
        check('source_mapping', False, '源提示词缺失或不在作品目录内')
    slots, labels, tags, counters = [], {}, set(), {'Picture':0,'Video':0,'Audio':0}
    for ref in refs:
        role = ref.get('role')
        check('references', role in ('reference','first_frame','last_frame','reference_video','reference_audio'), '参考槽位无效')
        kind = 'Video' if role == 'reference_video' else 'Audio' if role == 'reference_audio' else 'Picture'
        counters[kind] += 1
        check('references', ref.get('tag') == f'<{kind} {counters[kind]}>', '媒体编号未按实际附件顺序连续映射')
        check('references', ref.get('label') not in labels and bool(ref.get('label')), '参考标签为空或重复')
        labels[ref.get('label','')] = ref.get('tag','')
        tags.add(ref.get('tag',''))
        slots.append(role)
        try:
            path = pin(ref.get('path',''))
            check('references', path.stat().st_size > 0 and sha(path) == ref.get('sha256'), '参考文件已变化：' + str(ref.get('label')))
        except (OSError, ValueError):
            check('references', False, '参考文件不存在或路径越界：' + str(ref.get('label')))
        check('references', bool(ref.get('tag')) and ref.get('tag') in body, '附件没有在实际提示词中使用')
    frames = [r for r in refs if r.get('role') in ('first_frame','last_frame')]
    check('references', not frames or len(frames) == len(refs), '严格首尾帧不能与普通图片/音频/视频参考混用')
    check('references', all(slots.count(role) <= 1 for role in ('first_frame','last_frame')), '首帧或尾帧槽重复')
    check('references', counters['Picture'] <= 9 and counters['Video'] <= 3 and counters['Audio'] <= 3 and len(refs) <= 12, '参考素材数量超限')
    check('references', not counters['Audio'] or counters['Picture'] + counters['Video'] > 0, '音频参考不能单独使用')
    check('references', all(m[0] in tags for m in MEDIA_TAG.finditer(body)), '提示词引用了没有上传的媒体编号')
    stripped = CONT.sub(lambda m: m[3].strip(), original)
    mapped = LABEL.sub(lambda m: labels.get(m[1], m[0]), stripped)
    check('source_mapping', mapped == body and '{{' not in mapped and '}}' not in mapped and not LABEL.search(mapped),
          '源稿经实际标签映射后不等于请求正文，或仍含占位符')
    check('source_mapping', not MEDIA_TAG.search(original) and not re.search(r'@图片?\d+|(?:图|图片)\s*\d+',original),
          '源稿手写了媒体编号，必须由工具按实际附件映射')

    parts = sections(body)
    fields = [k for k,_ in parts]
    fullref = bool(refs and not frames)
    expected_fields = ['subject_definitions','summary','retention_analysis','detailed_description','overall_soundscape','non_diegetic_music'] if fullref else ['integrated_multimodal_description','overall_soundscape','non_diegetic_music']
    check('structure', fields == expected_fields, '模式与字段顺序不符，或存在重复/缺失字段')
    part = dict(parts)
    definitions = part.get('subject_definitions','')
    detail = part.get('detailed_description', part.get('integrated_multimodal_description',''))
    retention = part.get('retention_analysis','')
    check('structure', not re.search(r'\(S\d+\)', retention), 'retention_analysis不得包含说话编号')
    if counters['Audio']:
        check('structure', 'audio reference' in part.get('summary',''), 'summary没有说明音频参考职责')
    total = re.search(r'全段\s*(\d+)\s*秒', detail)
    if total:
        check('timing', int(total[1]) == duration, '正文总时长与请求duration不一致')
    shot_matches = list(re.finditer(r'\[Shot (\d+)\]', detail))
    check('timing', bool(shot_matches) and [int(m[1]) for m in shot_matches] == list(range(1,len(shot_matches)+1)), '镜头编号缺失、重复或不连续')
    if shot_matches:
        check('timing', not re.match(r'\s*At\s',detail[shot_matches[0].end():]), '[Shot 1]不写切入时间，后续镜头才写切点')
    if frames and duration_ok:
        frame_tags={r['role']:r['tag'] for r in frames}
        first,last=frame_tags.get('first_frame'),frame_tags.get('last_frame')
        final_shot=max(1,len(shot_matches))
        if first and last:
            alignment=(f'How the reference pictures align with the target video — {first} (from [Shot 1]) '
                f'aligns with the 0.00-second mark of the target video; {last} (from [Shot {final_shot}]) '
                f'aligns with the {duration:.2f}-second mark of the target video.')
        else:
            alignment=(f'For the target video, at {0 if first else duration:.2f} seconds into the target video, '
                f'{first or last} (from [Shot {1 if first else final_shot}]) is fully referenced.')
        check('structure',body.splitlines()[0]==alignment, '严格首尾帧模式缺少与实际附件/时长一致的首行对齐指令')
    cuts = [0.0]
    for n, match in enumerate(shot_matches[1:], 2):
        cut = re.match(r'\s*At (\d{2}):(\d{2})\.(\d{3}), the (?:camera|shot) (?:cuts|transitions|changes|switches) to', detail[match.end():])
        if not check('timing', bool(cut), 'Shot 2起须使用明确的切镜时间语法'):
            continue
        sec = int(cut[1])*60 + int(cut[2]) + int(cut[3])/1000
        check('timing', duration_ok and cuts[-1] < sec < duration, '切点不递增或超出本段时长')
        cuts.append(sec)
    if len(shot_matches) > 2:
        risk('shot_complexity', '超过推荐1—2镜，需逐镜评估新信息、动作意图和运镜')
    for m in TIME.finditer(detail):
        start,end = float(m[1]),float(m[2])
        check('timing', duration_ok and 0 <= start < end <= duration, '动作/对白/定格时间超出片段或区间无效：' + m[0])

    subjects = {}
    for line in definitions.splitlines():
        m = re.match(r'<Subject (\d+)>\s+(.+)',line)
        if not m:
            continue
        sub = int(m[1])
        check('structure', sub not in subjects, '视觉主体重复定义')
        character,name=named_character(m[2],names)
        speaker = re.findall(r'\(S(\d+)\)',m[2])
        subjects[sub] = {'character': character,'name':name,'speaker':speaker,'line':line}
        retained=[line for line in retention.splitlines() if line.startswith(f'<Subject {sub}>')]
        check('structure',len(retained)==1 and '[Shot ' in retained[0] and bool(re.search(
            r'fully_preserved|partially_preserved|attribute_transfer|weak_reference',retained[0])), '主体保留关系缺少镜头范围或合法标记')
    for shot in re.findall(r'\[Shot (\d+)\]',retention):
        check('structure',int(shot)<=len(shot_matches), '保留关系引用了不存在的镜头')
    for sub in re.findall(r'<Subject (\d+)>',body):
        if fullref:
            check('structure', int(sub) in subjects, '引用了未定义的视觉主体')
    check('dialogue', body.count('<d>') == body.count('</d>') == len(DIALOGUE.findall(detail)), '对白标签未闭合或放在了错误字段')
    pairs, seen, sub_speakers = [], {}, {}
    audio_refs = {r.get('label','').removesuffix('音色参考'):r for r in refs if r.get('role') == 'reference_audio'}
    for index,m in enumerate(DIALOGUE.finditer(detail), 1):
        payload = m[1]
        valid = payload.startswith('[Chinese]')
        check('dialogue', valid, f'对白{index}缺少[Chinese]语言标签')
        text = payload[len('[Chinese]'):].strip() if valid else payload.strip()
        check('dialogue', bool(text) and text[-1:] in '。？！' and not re.search(r'[<>\[\]（）()~～…]|[！!？?]{2,}|——|\.\.\.',text), f'对白{index}含动作/标签/装饰标点或缺终止标点')
        prefix = detail[:m.start()].rsplit('\n',1)[-1]
        who = list(re.finditer(r'<Subject (\d+)>\s*\(S(\d+)\)',prefix))
        sub = speaker = None; char = name = None
        if who:
            sub,speaker = int(who[-1][1]),int(who[-1][2])
            person = subjects.get(sub,{})
            char,name = person.get('character'),person.get('name')
        elif not fullref:
            ids = list(re.finditer(r'\(S(\d+)\)',prefix))
            candidates = [n for n in names if n in prefix]
            if ids and len(candidates)==1:
                speaker=int(ids[-1][1]);name=candidates[0];char=names[name]
        check('dialogue', char is not None and speaker is not None, f'对白{index}没有明确、可对应登记人物的说话主体')
        if char:
            pairs.append((char,text))
            if char in seen:
                check('dialogue', seen[char] == speaker, '同一人物段内说话编号发生变化')
            else:
                check('dialogue', speaker == len(seen)+1, '说话编号没有按本段首次开口顺序分配')
                seen[char]=speaker
            if sub is not None:
                sub_speakers[sub]=speaker
            audio = audio_refs.get(char)
            if audio:
                tag=audio['tag']
                check('audio_binding', len(re.findall(re.escape(tag),prefix)) == 1 and
                      set(x[0] for x in MEDIA_TAG.finditer(prefix) if x[1]=='Audio') == {tag}, f'{name}这句对白未直接且唯一引用自己的音频')
                lines=[l for l in definitions.splitlines() if l.startswith(tag)]
                check('audio_binding', len(lines)==1 and bool(re.search(re.escape(f'<Subject {sub}>')+r'\s*'+re.escape(f'(S{speaker})'),lines[0])), f'{name}音频定义缺少相同Subject和S编号')
                check('audio_binding', any(l.startswith(tag) and re.search(r':\s*(?:weak_)?reference\b|：\s*(?:weak_)?reference\b',l) for l in retention.splitlines()), f'{name}音频没有限定为声线参考')
                check('audio_binding', any(l.startswith(tag) and re.search(r'不复制|不复述|without copying|timbre',l,re.I) for l in definitions.splitlines()+retention.splitlines()), f'{name}音频参考未说明声线与原录音内容的边界')
            following=detail[m.end():].splitlines()
            after=next((line.strip() for line in following if line.strip()),'')
            check('silence', '<d>' not in after and not TIME.search(after) and
                  bool(re.search(r'闭口|闭嘴|双唇.*闭|closes? .*lips|lips.*closed',after,re.I)), f'对白{index}后缺少独立的闭口说明')
            tail_lines=[]
            for line in following:
                if TIME.search(line) or re.search(r'\[Shot \d+\]|<d>',line):
                    break
                tail_lines.append(line)
            check('dialogue', not POST_DIALOGUE_META.search('\n'.join(tail_lines)),
                  f'对白{index}结束后又出现发声/时段解释：删除尾随说明或将必要语气放在本句对白前，保留闭口和可见动作')
            prior_shots=[s for s in shot_matches if s.start()<m.start()]
            if prior_shots:
                header=detail[prior_shots[-1].end():].splitlines()[0]
                visible={int(n) for n in re.findall(r'<Subject (\d+)>',header)}
                for listener in visible-({sub} if sub is not None else set()):
                    person=subjects.get(listener,{})
                    if not person.get('character'):
                        continue
                    identified=(person.get('name') in prefix or f'<Subject {listener}>' in prefix)
                    closed=bool(re.search(r'闭口|闭嘴|双唇.*闭|lips.*closed',prefix,re.I))
                    check('silence', identified and closed, f'对白{index}没有明确可见听者{person.get("name")}闭口')
        window = TIME.search(prefix)
        check('timing', bool(window), f'对白{index}缺少本段明确时间窗口')
        if window:
            start,end=float(window[1]),float(window[2])
            if end>start:
                count=len(re.findall(r'[\u4e00-\u9fff]',text))
                cps=count/(end-start)
                turn={'character':char,'name':name,'subject':sub,'speaker':speaker,'text':text,
                      'start':start,'end':end,'characters':count,'charactersPerSecond':round(cps,3)}
                report['turns'].append(turn)
                if cps > 3.05:
                    risk(f'speech_rate_{index}',f'对白{index}约{cps:.2f}字/秒，高于约3字/秒的保守估算，须说明取舍')
                if count>30:
                    risk(f'long_dialogue_{index}',f'对白{index}共{count}字，须评估拆句/拆镜及完整表达')
                if any(start<c<end for c in cuts[1:]):
                    risk(f'cross_cut_{index}', '对白跨切镜，须逐阶段核对画内外声音与听者口型')
    for sub,person in subjects.items():
        declared=person['speaker']
        expected=[str(sub_speakers[sub])] if sub in sub_speakers else []
        check('dialogue', declared==expected, '不说话主体被分配说话编号，或主体定义与对白S编号不一致')
    for a,b in zip(report['turns'],report['turns'][1:]):
        check('timing', a['end'] <= b['start'], '对白窗口重叠，违反轮流说话约定')
        if 0 <= b['start']-a['end'] < .15:
            risk('tight_handoff', '换人交接不足0.15秒，须评估抢话风险（非供应商硬上限）')
    hold=re.search(r'最后约?[\d.]*秒[（(]([\d.]+)[—–-]([\d.]+)秒[）)]',detail)
    if hold and report['turns']:
        check('timing', report['turns'][-1]['end'] <= float(hold[1]), '台词尚未结束就要求全员闭口定格')
    from voice_lifecycle import require_prepared, require_first_voice, safe_audit
    report['voiceCloseout'] = safe_audit(root,items)
    revoice_context=None
    if not pairs:
        check('silence', bool(re.search(r'无人(?:说话|讲话|开口)|全程无(?:对白|台词)|No one speaks',detail,re.I))
              and bool(re.search(r'双唇.*闭|闭口|lips.*closed',detail,re.I)), '无对白镜头没有明确安静和闭口')
        check('audio_binding', not audio_refs, '无对白片段仍上传人物音色')
    else:
        check('silence', not params.get('mute_audio'), '当前有原生对白却请求静音')
        policy=root/'协作/声音方案/执行规则-v001.json'
        check('audio_binding', policy.is_file(), '对白片段缺少声音前置规则；不能因未配置就跳过检查')
        if policy.is_file():
            pin(policy)
            cfg=read(policy)
            if selected:
                check('audio_binding', list(seen)==cfg.get('requiredVoices',{}).get(selected['subject']), '实际说话人与当前已采用声音方案不一致')
                bootstrap=[a for a in cfg.get('bootstrapRequests',[]) if a.get('request')==rel and a.get('requestSHA256')==sha(request_path)
                    and a.get('clip')==selected['subject'] and a.get('batch')==(selected.get('generation') or {}).get('batch')
                    and a.get('taskDir')==(selected.get('generation') or {}).get('taskDir')]
                try:
                    from voice_readiness import require_authorized_revoice
                    revoice_context=require_authorized_revoice(root,request_path,req,list(seen),cfg,items,selected)
                    if revoice_context:
                        report['voiceContext']=revoice_context
                        pin(revoice_context['authorization'])
                        risk('explicit_revoice','用户明确不采用候选声线，本请求依原描述重新定音；须核对精确授权和结果，不能顺延后续片段')
                except ValueError as error:
                    check('audio_binding',False,str(error))
                check('audio_binding', set(audio_refs)==set(seen) or (len(bootstrap)==1 and not audio_refs and len(seen)==1) or bool(revoice_context), '未绑定全部且仅实际说话人的音色，或不属于精确首次/重新定音授权')
                if bootstrap:
                    try:
                        for character in seen:
                            require_first_voice(root,character,items)
                    except ValueError as error:
                        check('audio_binding',False,str(error))
                if not bootstrap and not revoice_context:
                    try:
                        from voice_readiness import require_voice_templates
                        report['voiceContext']=require_voice_templates(root,req,list(seen),cfg,items)
                    except ValueError as error:
                        check('audio_binding',False,str(error))
            if cfg.get('snapshotFile'):
                pin(cfg['snapshotFile'])
        for ref in refs:
            if ref.get('role') != 'reference_audio':
                continue
            try:
                probe=audio_probe(root,inside(root,ref['path']))
                check('audio_binding', 0 < probe['seconds'] <= 5, '音色参考时长未知、为空或超过本地5秒警告线')
                if probe['seconds']<3 or probe['channels']!=1 or probe['sampleRate']!=16000:
                    risk('voice_format_'+ref['tag'], '声音样本未达到本片建议3—5秒/单声道16kHz，须核对实际样本适用性')
            except (OSError,ValueError,KeyError,wave.Error,subprocess.SubprocessError):
                check('audio_binding', False, '音频实测失败，不能把未知时长当合格样本')
    if not revoice_context:
        try:
            report['voiceCloseout']=require_prepared(root,items)
        except ValueError as error:
            check('audio_binding',False,str(error))
    if clip:
        try:
            check('dialogue', merged_pairs(pairs)==expected_dialogue(clip,names), '实际对白的人物、顺序、字词或标点与当前采用片段清单不一致')
        except ValueError as error:
            check('dialogue',False,str(error))

    environment=part.get('overall_soundscape','').strip()
    check('soundscape', bool(environment) and environment!='N/A' if not params.get('mute_audio') else True,
          '原生音轨缺少具体环境声，不能用N/A代替静音参数')
    # Whether a described source is meaningful is a professional review, not keyword proof.
    without_dialogue=DIALOGUE.sub('',body)
    narration=re.sub(r'无旁白|不(?:生成|添加|需要|允许)?旁白|no narrator', '', without_dialogue, flags=re.I)
    check('soundscape', not re.search(r'旁白|\bnarrator\b',narration,re.I), '真正旁白应后期制作，不写入H3生成正文')
    if part.get('non_diegetic_music')!='N/A':
        risk('generated_music', '逐段生成配乐偏离默认后期统一方案，核对现有明确要求')
    process=r'送审|预算|积分|消费授权|审核流程|抽帧|prepare\b|attempt-\d|SHA256|不能把预计当实际'
    check('residue', not re.search(process,without_dialogue,re.I), '制作/费用/审核流程文字混入生成正文')
    check('residue', not re.search(r'(?:说|台词|等待|切|与|在)\s*(?:0[1-9]|1[0-7]|C\d{2})(?=[^\d.]|$)',without_dialogue), '存在裸台词编号或外部分镜编号')

    tails=[r for r in refs if r.get('label','').endswith('尾帧')]
    if clip and clip.get('continue_from'):
        prior=clip['continue_from'].rsplit('/',1)[-1]
        check('continuity', any(r.get('label')==prior+'-尾帧' for r in tails), '连续片段没有绑定实际上一段尾帧')
    if tails:
        check('continuity', refs[0] in tails, '承接尾帧应是第一个普通参考附件')
        markers=list(CONT.finditer(original))
        check('continuity', sorted(m[1] for m in markers)==['承接定义','承接开场'] and all(m[2] for m in markers), '两句承接未按真实尾帧核对')
        log=source.with_name(source.stem+'.continuity.jsonl') if source else None
        if check('continuity', log is not None and log.is_file(), '缺少真实set-continuity日志'):
            pin(log)
            last=json.loads(log.read_text(encoding='utf-8').strip().splitlines()[-1])
            check('continuity', last.get('frame_sha256') in {r.get('sha256') for r in tails}, '承接日志对应另一张尾帧')
            current={m[1]:m[3] for m in markers}
            logged={c.get('marker'):c.get('new') for c in last.get('changes',[])}
            check('continuity', current==logged and len(current)==2, '承接句在看帧登记后又被修改，须重新专业核对')
        for tail in tails:
            prior=tail['label'].removesuffix('-尾帧')
            prior_id=(selected['subject'].rsplit('/',1)[0]+'/' if selected else '')+prior
            upstream=[c for c in clips if c['id']==prior_id]
            if check('continuity', len(upstream)==1 and upstream[0].get('status')=='approved', '尾帧来源片段尚未采用'):
                parent=upstream[0]
                try:
                    video=pin(parent['video']); video_sha=sha(video)
                    approved=[i for i in items if i.get('kind')=='video' and i.get('subject')==prior_id
                              and i.get('status')=='approved' and i.get('canApprove')]
                    covered = any({video_sha,tail['sha256']} <= {f.get('sha256') for f in i.get('files',[])} for i in approved)
                    if not covered and selected:
                        covered = approved_derived_tail(root, selected, tail, video, approved)
                    check('continuity', covered,
                          '尾帧缺少同一已通过结果覆盖，且未核实为已通过源视频的原样末帧')
                except (OSError,ValueError,KeyError):
                    check('continuity', False, '尾帧的真实源视频缺失或变化')

    baselines=[c for c in clips if c.get('status')=='approved' and c.get('prompt')]
    style=''
    for first in baselines:
        baseline=inside(root,first['prompt'])
        p=dict(sections(baseline.read_text(encoding='utf-8-sig')))
        # Basic/first-frame prompts may begin with voice/identity declarations;
        # these are not a style sentence. Prefer an adopted full-reference baseline.
        baseline_detail=p.get('detailed_description','')
        if baseline_detail:
            style=baseline_detail.splitlines()[0].strip()
            pin(baseline)
            break
    if style:
        check('style', detail.startswith(style) if fullref else style in detail,
              '未原样复用已采用片段的风格光线句')
    else:
        risk('first_style_baseline', '尚无已采用片段，需核对项目已确认的首次风格基准')
    # Prefer adopted source prompts, then frozen already-approved prompt requests.
    # Never use this very candidate as evidence of its own voice consistency.
    candidates=[inside(root,c['prompt']) for c in baselines]
    for item in sorted(items,key=lambda i:i.get('submittedAt') or ''):
        if item.get('kind')=='video_prompt' and item.get('status')=='approved' and item.get('canApprove'):
            reference=item.get('request') or {}
            if isinstance(reference,dict) and reference.get('path') and reference['path']!=rel:
                candidates.append(inside(root,reference['path']))
    voice_baselines={}
    for candidate in candidates:
        if not candidate.is_file():
            continue
        value=read(candidate).get('prompt','') if candidate.suffix=='.json' else candidate.read_text(encoding='utf-8-sig')
        defs=dict(sections(value)).get('subject_definitions','')
        for line in defs.splitlines():
            if not line.startswith('<Subject '):
                continue
            character,_=named_character(line,names)
            tone=re.search(r'固定声线：([^\n]+)',line)
            if character in seen and tone and character not in voice_baselines:
                voice_baselines[character]={'text':tone[1].strip(),'source':candidate.relative_to(root).as_posix()}
                pin(candidate)
    report['voiceBaselines']=voice_baselines
    for person in subjects.values():
        character=person['character']
        if character not in seen:
            continue
        tone=re.search(r'固定声线：([^\n]+)',person['line'])
        check('dialogue', tone is not None, '说话人物缺少固定声线描述')
        baseline=voice_baselines.get(character)
        if baseline and tone:
            check('dialogue',tone[1].strip()==baseline['text'], '固定声线描述偏离已采用/已审基准：'+person['name'])
        elif not baseline:
            risk('voice_baseline_'+character, '尚无可自动对照的历史固定声线，须核对角色声音基准')
    convention=root/'作品约定.md'
    if convention.is_file():
        pin(convention)
    report['ruleSources']={name:sha(TOOLS_ROOT/name) for name in SOURCES}
    report['code']={name:sha(TOOLS_ROOT/'tools'/name) for name in ('h3_execution_checks.py','voice_readiness.py','voice_lifecycle.py','shot_context.py','validate_project.py')}
    for key,(title,source_name) in RULES.items():
        if key not in report['rules']:
            report['rules'][key]={'title':title,'source':source_name,'passed':True,
                'applicability':'not_applicable',
                'reason':'本请求没有相应输入；是否缺少应有输入由对应专业复核项判断'}
    report['postGenerationChecks']={'status':'not_yet_applicable',
        'items':['完整解码与实际尺寸/时长/音轨','原音与ASR辅助核对白字、额外人声和截尾',
                 '实际说话人、音色与口型逐句核验','身份/肤质/手指/牙齿/眼神/画面文字',
                 '首中尾、轴线站位、道具和真实片尾状态','实际费用结算与请求偏差记录'],
        'note':'提交前记录检查计划；这些结果必须在生成后验证，不能预先标通过'}
    report['technicalPass']=not report['errors']
    return report


def binding(report):
    return digest({k:report.get(k) for k in ('version','request','inputs','reviewDependencies','generation',
        'clipContext','shotContext','voiceContext','voiceBaselines','ruleSources','code') } | {'review': {k:report.get('review',{}).get(k)
                                                           for k in ('id','subject','fingerprint')}})


def record_path(root, report):
    return root/'reports/执行检查'/('h3-'+binding(report)+'.json')


def validate_assessment(root, report, assessment):
    if assessment.get('reviewer')!='总导演' or set(assessment.get('checks',{}))!=set(MANUAL):
        raise ValueError('必须逐项填写总导演内部复核；不是用户新审核')
    evidence={}
    for key,title in MANUAL.items():
        row=assessment['checks'][key]
        if row.get('verdict') not in ('pass','accepted-risk') or not isinstance(row.get('note'),str) or len(row['note'].strip())<12:
            raise ValueError('专业复核缺少具体判断：'+title)
        if not isinstance(row.get('evidence'),list) or not row['evidence']:
            raise ValueError('专业复核缺少实际依据：'+title)
        for name in row['evidence']:
            path=inside(root,name)
            if not path.is_file():
                raise ValueError('专业复核依据不存在')
            evidence[path.relative_to(root).as_posix()]=sha(path)
    from shot_context import validate_handoff
    if not report.get('shotContext'):
        raise ValueError('缺少当前作品及上下段状态读取')
    evidence.update(validate_handoff(root, report['shotContext'],
        assessment['checks']['continuity_and_axis'].get('handoff'), report['sourcePrompt']))
    decisions=assessment.get('risks',{})
    if set(decisions)!={r['code'] for r in report['risks']}:
        raise ValueError('自动风险没有逐项处理，或沿用旧风险记录')
    if any(not isinstance(v,str) or len(v.strip())<12 for v in decisions.values()):
        raise ValueError('风险取舍需要具体理由，不能只写通过')
    return evidence


def record_review(root, request_path, assessment_path, *, items=None):
    root=Path(root).resolve()
    report=analyze(root,request_path,items=items)
    if not report['technicalPass']:
        raise ValueError('自动检查未通过，专业意见不能豁免：'+report['errors'][0]['message'])
    assessment_path=inside(root,assessment_path)
    assessment=read(assessment_path)
    evidence=validate_assessment(root,report,assessment)
    record={'version':VERSION,'kind':'INTERNAL_DIRECTOR_REVIEW_NOT_USER_APPROVAL',
        'at':datetime.now(timezone.utc).isoformat(),'binding':binding(report),
        'request':report['request'],'reviewer':'总导演','assessment':assessment,
        'evidence':evidence,'assessmentFile':assessment_path.relative_to(root).as_posix(),
        'assessmentSHA256':sha(assessment_path),'audit':report}
    path=record_path(root,report)
    save(path,record)
    return path


def check_review(root, report):
    root=Path(root).resolve()
    path=record_path(root,report)
    if not path.is_file():
        return ['缺少当前请求版本的内部专业复核，不能用格式检查代替']
    record=read(path)
    if record.get('kind')!='INTERNAL_DIRECTOR_REVIEW_NOT_USER_APPROVAL' or record.get('binding')!=binding(report):
        return ['内部专业复核版本不匹配']
    for name,value in {**record.get('evidence',{}),record.get('assessmentFile',''):record.get('assessmentSHA256')}.items():
        try:
            if sha(inside(root,name))!=value:
                return ['内部专业复核依据已变化，须重新核对（无需增加用户确认）']
        except (OSError,ValueError):
            return ['内部专业复核依据丢失']
    try:
        if record.get('reviewer')!='总导演' or read(inside(root,record['assessmentFile']))!=record.get('assessment'):
            return ['内部专业复核内容与其实际依据不一致']
        if validate_assessment(root,report,record.get('assessment',{}))!=record.get('evidence'):
            return ['内部专业复核依据范围不一致']
    except (ValueError,OSError,KeyError,TypeError):
        return ['内部专业复核项目或风险处理不完整']
    return []


def require_execution_checks(root, request_path, task_dir, batch):
    report=analyze(root,request_path,batch=batch,task_dir=task_dir)
    errors=[r['message'] for r in report['errors']]+check_review(root,report)
    if errors:
        raise ValueError('H3提交前执行检查拦截：'+'；'.join(errors[:4]))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('check','record'):
        p=sub.add_parser(command)
        p.add_argument('--request',required=True,type=Path)
        if command=='check':
            p.add_argument('--output',type=Path)
        else:
            p.add_argument('--assessments',required=True,type=Path)
    a=parser.parse_args();root=a.project.resolve()
    if a.command=='record':
        path=record_review(root,a.request,a.assessments)
        print(json.dumps({'internalReview':str(path),'userApprovalChanged':False},ensure_ascii=False))
        return 0
    report=analyze(root,a.request)
    report['professionalErrors']=check_review(root,report)
    report['professionalPass']=not report['professionalErrors']
    report['notGenerationAuthorization']=True
    if a.output:
        save(inside(root,a.output),report)
    print(json.dumps({'technicalPass':report['technicalPass'],'professionalPass':report['professionalPass'],
        'errors':report['errors'],'professionalErrors':report['professionalErrors'],'risks':report['risks'],
        'currentUserReview':report.get('review',{}).get('status'),'userApprovalChanged':False},ensure_ascii=False,indent=2))
    return 0 if report['technicalPass'] and report['professionalPass'] else 2


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        raise SystemExit(main())
    except (ValueError,OSError,KeyError,TypeError) as error:
        raise SystemExit('STOP: '+str(error))
