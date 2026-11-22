"""Read-only voice closeout derived from actual canvas approvals and manifests.

Never extracts media, changes approvals, starts jobs, or writes production state.
The executor handles the returned actions in the same turn as video acceptance.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))

VIDEO = {'.mp4', '.mov', '.webm'}
STATES = {
    'extract': '提取并登记音色',
    'register': '核对音色文件与登记',
    'submit': '将音色样本放到画布确认',
    'confirm': '等待画布音色确认',
    'adopt': '同步已通过音色的采用状态',
    'complete': '音色收尾完成',
}


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def inside(root, name):
    if not isinstance(name, str) or not name:
        raise ValueError('声音收尾缺少作品内文件路径')
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('声音收尾文件超出本作品')
    return path


def sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def stamp(item):
    try:
        return datetime.fromisoformat(item.get('submittedAt', '').replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0


def ancestors(item, by_id, seen=None):
    seen = set() if seen is None else seen
    result = []
    for key in item.get('dependsOn', []):
        if key in seen or key not in by_id:
            continue
        seen.add(key)
        parent = by_id[key]
        result.append(parent)
        result.extend(ancestors(parent, by_id, seen))
    return result


def speakers(request, assets, separate_forms=()):
    """Use the reviewed request's actual dialogue, not presence in a reference image."""
    body = request.get('prompt', '')
    if request.get('params', {}).get('mute_audio') or '<d>' not in body:
        return []
    names = {}
    for asset in assets:
        key, name = asset.get('id', ''), asset.get('name', '')
        if re.fullmatch(r'C\d{5}(?:-[A-Z]+)?', key) and name:
            names.setdefault(name, set()).add(key if key in separate_forms else key.split('-')[0])
    def named(line):
        found = set()
        for name, keys in names.items():
            if re.search(re.escape(name) + r'(?=[，,。;；\s(（]|$)', line):
                found.update(keys)
        return next(iter(found)) if len(found) == 1 else None
    definitions = re.search(r'^subject_definitions:[ \t]*\r?\n(.*?)(?=^[a-z_]+:[ \t]*\r?$|\Z)', body, re.M | re.S)
    subjects = {m[1]: named(m[2]) for m in re.finditer(r'^<Subject (\d+)>[ \t]*(.+)$',
                definitions[1] if definitions else '', re.M)}
    found = []
    for dialogue in re.finditer(r'<d>.*?</d>', body, re.S):
        prefix = body[:dialogue.start()].rsplit('\n', 1)[-1]
        subject = re.search(r'<Subject (\d+)>\s*\(S\d+\)', prefix)
        person = subjects.get(subject[1]) if subject else named(prefix)
        if not person:
            raise ValueError('已通过对白缺少可核对的人物绑定，不能略过音色收尾')
        if person not in found:
            found.append(person)
    if not found:
        raise ValueError('已通过请求存在未闭合或无法识别的对白，须核对原始说话人')
    return found


def approved_sources(root, items, assets, separate_forms=()):
    by_id = {item['id']: item for item in items}
    result, errors = [], []
    for item in items:
        if item.get('kind') != 'video':
            continue
        approved = item.get('status') == 'approved' and item.get('canApprove')
        was_approved = any(h.get('action') == 'approve' for h in item.get('history', []))
        if item.get('status') == 'changes_requested':
            continue
        if not approved and not was_approved:
            continue
        parents = ancestors(item, by_id)
        prompt = next((p for p in parents if p.get('kind') == 'video_prompt' and p.get('subject') == item.get('subject')), None)
        if not prompt or not prompt.get('request'):
            errors.append(f'{item.get("subject")}: 已通过视频缺少原请求，须核对说话人和声音来源')
            continue
        request = prompt['request']
        if 'prompt' not in request:
            request = read(inside(root, request['path']), {})
        try:
            people = speakers(request, assets, separate_forms)
        except ValueError as error:
            errors.append(f'{item.get("subject")}: {error}')
            continue
        if not people:
            continue
        files = [f for f in item.get('files', []) if Path(f.get('path', '')).suffix.lower() in VIDEO]
        valid = []
        for file in files:
            path = inside(root, file['path'])
            # ReviewStore has already hashed current files. Avoid hashing videos again on every UI refresh.
            exists = file.get('exists', file.get('resource', {}).get('exists'))
            if exists is None:
                exists = path.is_file() and file.get('sha256') == sha(path)
            if exists and file.get('sha256'):
                valid.append(file)
        if not approved or not valid:
            errors.append(f'{item.get("subject")}: 已接受的说话视频来源已变化，须先核对，不能重新冒充首次定音')
            continue
        for person in people:
            result.append({'character': person, 'sourceClip': item['subject'], 'sourceReviewId': item['id'],
                           'sourceFiles': [{k: f[k] for k in ('path', 'sha256')} for f in valid],
                           'submittedAt': item.get('submittedAt', ''),
                           '_time': min((stamp({'submittedAt': h.get('time', '')}) for h in item.get('history', [])
                                         if h.get('action') == 'approve'), default=stamp(item))})
    return sorted(result, key=lambda row: (row['_time'], row['sourceReviewId'])), errors


def audit(root, items=None, episode=None):
    root = Path(root).resolve()
    if items is None:
        from canvas_review import ReviewStore
        items = ReviewStore(root).snapshot()['items']
    assets = read(root / 'assets.json', [])
    voices = read(root / 'voices.json', [])
    clips = {c['id']: c for c in read(root / 'clips.json', [])}
    policy = read(root / '协作/声音方案/执行规则-v001.json', {})
    separate = {v['character'] for v in voices if '-' in v.get('character', '')}
    separate.update(c for cs in policy.get('requiredVoices', {}).values() for c in cs if '-' in c)
    sources, errors = approved_sources(root, items, assets, separate)
    by_id = {item['id']: item for item in items}
    rows = []
    for character in dict.fromkeys(s['character'] for s in sources):
        origins = [s for s in sources if s['character'] == character]
        source = origins[0]
        adopted = clips.get(source['sourceClip'], {}).get('video')
        source = next((s for s in origins if s['sourceClip'] == source['sourceClip'] and
                       any(f['path'] == adopted for f in s['sourceFiles'])), source)
        if episode and not any(s['sourceClip'].split('/')[0] == episode for s in origins):
            continue
        row = {k: v for k, v in source.items() if not k.startswith('_')}
        row['name'] = next((a['name'] for a in assets if a.get('id', '').split('-')[0] == character.split('-')[0]), character)
        row.update(state='extract', preparationComplete=False, complete=False, problems=[])
        registered = [v for v in voices if v.get('character') == character]
        if not registered:
            row['problems'].append('首次说话视频已通过，尚未提取和登记音色')
        elif len(registered) != 1:
            row.update(state='register'); row['problems'].append('同一角色存在重复音色登记')
        else:
            voice = registered[0]
            path = inside(root, voice.get('file'))
            row['voiceFile'] = voice['file']
            valid = path.is_file() and voice.get('fingerprint', {}).get('file') == sha(path)
            valid = valid and voice.get('source_clip') in {s['sourceClip'] for s in origins}
            if valid:
                try:
                    with wave.open(str(path), 'rb') as wav:
                        row['seconds'] = wav.getnframes() / wav.getframerate()
                        row['shortSample'] = row['seconds'] < 3
                        valid = row['seconds'] > 0
                except (wave.Error, EOFError):
                    valid = False
            if not valid:
                row.update(state='register'); row['problems'].append('音色文件、指纹、有效音频或已通过来源不匹配')
            else:
                candidates = sorted((i for i in items if i.get('kind') == 'audio' and i.get('subject') == character), key=stamp)
                sample = candidates[-1] if candidates else None
                matches = sample and any(f.get('path') == voice['file'] and f.get('sha256') == sha(path) for f in sample.get('files', []))
                origin_ids = {s['sourceReviewId'] for s in origins if s['sourceClip'] == voice.get('source_clip')}
                provenance = sample and origin_ids.intersection(p['id'] for p in ancestors(sample, by_id))
                if not matches or not provenance:
                    row.update(state='submit'); row['problems'].append('尚无绑定同一文件及已通过来源的声音画布节点')
                else:
                    row['sampleReviewId'] = sample['id']
                    if sample.get('status') == 'approved' and sample.get('canApprove'):
                        row.update(state='complete' if voice.get('status') == 'approved' else 'adopt', preparationComplete=True)
                        row['complete'] = voice.get('status') == 'approved'
                    elif sample.get('status') in ('pending', 'deferred') and sample.get('canApprove'):
                        row.update(state='confirm', preparationComplete=True)
                    else:
                        row.update(state='submit'); row['problems'].append('声音样本被否定、已变化或依赖未就绪，须修正后重新送审')
        row['action'] = STATES[row['state']]
        rows.append(row)
    return {'version': 1, 'characters': rows, 'errors': errors,
            'preparationComplete': not errors and all(r['preparationComplete'] for r in rows),
            'complete': not errors and all(r['complete'] for r in rows),
            'pendingProduction': [r['character'] for r in rows if not r['preparationComplete'] or r['state'] == 'adopt'],
            'awaitingConfirmation': [r['character'] for r in rows if r['state'] == 'confirm']}


def safe_audit(root, items=None, episode=None):
    try:
        return audit(root, items, episode)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return {'version': 1, 'characters': [], 'errors': [f'音色收尾核对失败：{error}'],
                'preparationComplete': False, 'complete': False, 'pendingProduction': [], 'awaitingConfirmation': []}


def require_prepared(root, items=None):
    report = safe_audit(root, items)
    if not report['preparationComplete'] or report['pendingProduction']:
        detail = report['errors'] + [f'{r["name"]}：{r["action"]}' for r in report['characters'] if not r['preparationComplete'] or r['state'] == 'adopt']
        raise ValueError('首次说话视频的音色收尾未完成；' + '；'.join(detail))
    return report


def require_first_voice(root, character, items=None):
    report = safe_audit(root, items)
    if report['errors']:
        raise ValueError('首次定音来源无法核对：' + '；'.join(report['errors']))
    registered = read(Path(root) / 'voices.json', [])
    if any(r['character'] == character for r in report['characters']) or any(v.get('character') == character for v in registered):
        raise ValueError(f'{character}已有通过的说话视频或音色登记，不得再次使用首次定音例外；必须引用已通过音色')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--episode')
    parser.add_argument('--require', choices=('prepared', 'confirmed'))
    args = parser.parse_args()
    result = safe_audit(args.project, episode=args.episode)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    okay = result['complete'] if args.require == 'confirmed' else result['preparationComplete'] and not result['pendingProduction']
    return int(bool(args.require) and not okay)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
