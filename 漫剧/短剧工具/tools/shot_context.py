"""Read current project/neighbor evidence and validate the existing internal handoff review.

No media generation, canvas approval, adoption, budget or readiness writes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
DIMENSIONS = ('people', 'props', 'space', 'camera', 'sound')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def inside(root, name):
    if not isinstance(name, str) or not name:
        raise ValueError('衔接依据缺少作品内路径')
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('衔接依据越出本作品')
    return path


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def snapshot(root, clip_id, items=None):
    root = Path(root).resolve()
    files = {}
    def pin(name):
        path = inside(root, name)
        files[path.relative_to(root).as_posix()] = sha(path) if path.is_file() else None
        return path
    def read(name, required=True):
        path = pin(name)
        if not path.is_file():
            if required:
                raise ValueError('制作前状态文件缺失：' + name)
            return None
        return json.loads(path.read_text(encoding='utf-8-sig'))
    project = read('project.json')
    assets, voices = read('assets.json'), read('voices.json')
    # Bind relevant rows and neighbors, not unrelated episode progress.
    clips = json.loads(inside(root, 'clips.json').read_text(encoding='utf-8-sig'))
    ids = [c['id'] for c in clips]
    if len(ids) != len(set(ids)) or ids.count(clip_id) != 1:
        raise ValueError('当前片段不存在或清单编号重复')
    episode = [c for c in clips if c['id'].split('/')[0] == clip_id.split('/')[0]]
    index = next(n for n, c in enumerate(episode) if c['id'] == clip_id)
    current = episode[index]
    previous = episode[index - 1] if index else None
    following = episode[index + 1] if index + 1 < len(episode) else None
    upstream_ids = list(dict.fromkeys(c for c in (previous and previous['id'], current.get('continue_from')) if c))
    if clip_id in upstream_ids or any(c not in ids for c in upstream_ids):
        raise ValueError('承接来源缺失或指向本段')
    upstream = [next(c for c in clips if c['id'] == key) for key in upstream_ids]
    neighbors = {c['id']: c for c in [*upstream, current, *([following] if following else [])]}
    for c in neighbors.values():
        for row in (c, c.get('draft') or {}):
            for key in ('prompt', 'video', 'ending_frame', 'source_story'):
                if row.get(key):
                    pin(row[key])
    asset_ids = {key for c in neighbors.values() for key in c.get('assets', [])}
    for a in assets:
        if a.get('id') in asset_ids:
            for key in ('image', 'sheet', 'prompt'):
                if a.get(key):
                    pin(a[key])
    for voice in voices:
        if voice.get('file'):
            pin(voice['file'])
    for name in ('作品约定.md', '创作画布.json', '协作/声音方案/执行规则-v001.json'):
        read(name, False) if name.endswith('.json') else pin(name)
    if items is None:
        from canvas_review import ReviewStore
        items = ReviewStore(root).snapshot()['items']
    # The current prompt's own approval is checked separately by the submission gate.
    relevant = [i for i in items if (i.get('subject') in neighbors and i.get('kind') in ('video', 'image'))
                or i.get('subject') in asset_ids or i.get('kind') == 'audio']
    reviews = [{k: i.get(k) for k in ('id', 'subject', 'kind', 'status', 'canApprove', 'fingerprint')}
               for i in sorted(relevant, key=lambda i: i['id'])]
    def state(c):
        path = c.get('video')
        actual = None
        accepted = [i for i in relevant if i.get('subject') == c['id'] and i.get('kind') == 'video'
                    and i.get('status') == 'approved' and i.get('canApprove')]
        if c.get('status') == 'approved' and path and files.get(path):
            for i in accepted:
                if any(f.get('path') == path and f.get('sha256') == files[path] for f in i.get('files', [])):
                    actual = {'path': path, 'sha256': files[path], 'reviewId': i['id']}
        return {'clip': c['id'], 'manifest': c, 'actual': actual,
                'adoptionPending': bool(accepted) and actual is None}
    result = {'version': 1, 'projectRoot': str(root), 'clip': clip_id, 'project': project,
              'current': current, 'previous': previous['id'] if previous else None,
              'upstream': [state(c) for c in upstream], 'next': state(following) if following else None,
              'files': files, 'reviews': reviews}
    result['fingerprint'] = digest(result)
    return result


def validate_handoff(root, context, handoff, prompt_path):
    """Validate concrete evidence and completeness; actual viewing remains a professional duty."""
    root = Path(root).resolve()
    if not isinstance(handoff, dict) or handoff.get('contextFingerprint') != context['fingerprint']:
        raise ValueError('缺少本轮状态读取，或上下段/素材/审核已变化；先重读再核对衔接')
    evidence = {}
    def note(text, field):
        if not isinstance(text, str) or len(text.strip()) < 6 or re.search(r'待补|待核对|未核对|TODO|TBD|^(通过|正常|同上|不适用)[。！! ]*$', text, re.I):
            raise ValueError('衔接核对缺少具体内容：' + field)
    def row(value, label):
        if not isinstance(value, dict) or not isinstance(value.get('state'), dict) or set(value['state']) != set(DIMENSIONS):
            raise ValueError(label + '须覆盖人物、持物、空间、镜头视线、声音五项状态')
        for field in DIMENSIONS:
            note(value['state'][field], label + '.' + field)
        names = value.get('evidence')
        if not isinstance(names, list) or not names:
            raise ValueError(label + '缺少实际依据文件')
        for name in names:
            path = inside(root, name)
            if not path.is_file():
                raise ValueError(label + '依据文件不存在：' + name)
            evidence[path.relative_to(root).as_posix()] = sha(path)
        return names
    current, after = handoff.get('current'), handoff.get('next')
    current_files = row(current, '本段怎么接')
    note(current.get('reason'), '本段接法及省略的动作')
    mode = current.get('mode')
    if mode not in ('opening', 'continuous', 'cut', 'independent'):
        raise ValueError('接法须明确 opening / continuous / cut / independent')
    if bool(context['current'].get('continue_from')) != (mode == 'continuous'):
        raise ValueError('连续接法与 continue_from 不一致，不能改成独立起幅绕过尾帧')
    if (not context['upstream']) != (mode == 'opening'):
        raise ValueError('只有没有上段和承接来源的开场才能免上段核对')
    prompt = inside(root, prompt_path).relative_to(root).as_posix()
    if prompt not in current_files:
        raise ValueError('本段接法未核对当前实际提示词')
    previous = handoff.get('previous')
    if not isinstance(previous, list) or not all(isinstance(r, dict) for r in previous) or [r.get('clip') for r in previous] != [r['clip'] for r in context['upstream']]:
        raise ValueError('上段核对遗漏相邻片段或显式承接来源')
    for value, source in zip(previous, context['upstream']):
        names = row(value, '上段实际状态 ' + source['clip'])
        actual = source['actual']
        if source['adoptionPending']:
            raise ValueError('上段结果已通过但采用或文件尚未同步，先补齐：' + source['clip'])
        if actual:
            if value.get('basis') != 'actual' or value.get('reviewId') != actual['reviewId'] or actual['path'] not in names:
                raise ValueError('上段已有实际采用视频，必须核对该视频及真实通过记录')
            if any(re.search(r'预计|假设|尚未生成', text) for text in value['state'].values()):
                raise ValueError('不能把上段预计状态当作实际状态')
        else:
            if mode != 'independent' or value.get('basis') != 'planned':
                raise ValueError('需要上段实际状态但素材尚未就绪；先补齐，独立转场须明确说明')
            planned = source['manifest'].get('prompt')
            if 'clips.json' not in names and planned not in names:
                raise ValueError('独立制作须引用真实清单或上段规划，不能凭聊天印象')
    names = row(after, '给下段留下什么')
    note(after.get('transition'), '片尾交接或收束目的')
    target = context['next']
    if after.get('clip') != (target['clip'] if target else None) or after.get('basis') != 'target':
        raise ValueError('片尾须写给当前下段的目标状态；未生成本段不能预标实测')
    if prompt not in names:
        raise ValueError('片尾目标未核对当前提示词')
    if target:
        if target['adoptionPending']:
            raise ValueError('下段已通过但采用尚未同步，先核对现有结果')
        if target['actual']:
            if target['actual']['path'] not in names or after.get('reviewId') != target['actual']['reviewId']:
                raise ValueError('下段已有采用视频，须反向核对它的真实开场')
        elif target['manifest'].get('prompt') not in names and 'clips.json' not in names:
            raise ValueError('须读取下段规划后确定本段片尾，不能只看当前提示词')
    return evidence


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', required=True, type=Path)
    p.add_argument('--clip', required=True)
    a = p.parse_args()
    print(json.dumps(snapshot(a.project, a.clip), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
