"""Trusted controller's data-only sink for new nex_bot scope final reports.

Not an Agent tool or HTTP endpoint. Inputs `bindings`, `status` and `assets` must
come from the trusted controller's scope API / local evidence, never a proposal.
No network, credentials, shell, project manifests, approvals or execution flags.
"""
import hashlib
import json
import os
from pathlib import Path
import re

from generation_runtime import exclusive_lock

SCENES = ('Clip00001', 'Clip00002', 'Clip00003')
KINDS = ('script', 'visual-prompt', 'continuity', 'voice-range', 'technical-review')
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z')


def require(condition, message):
    if not condition: raise ValueError(message)


def shape(value, fields):
    require(isinstance(value, dict) and set(value) == set(fields), 'unexpected or missing proposal fields')


def text(value, low, high):
    require(isinstance(value, str) and low <= len(value) <= high and '\0' not in value,
            'proposal text length or type invalid')
    try: value.encode('utf-8')
    except UnicodeError: raise ValueError('proposal text must be valid Unicode') from None


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, 'proposal integer out of range')


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    def constant(_): raise ValueError('nonfinite JSON number')
    try: return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (TypeError, json.JSONDecodeError): raise ValueError('one plain JSON object is required') from None


def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
def sha(value): return hashlib.sha256(value).hexdigest()


def within(root, path):
    value = Path(path).resolve()
    require(value.is_relative_to(root) and value != root, 'proposal path escaped the new project')
    return value


def verify_asset(root, assets, identifier, kind):
    require(isinstance(identifier, str) and identifier in assets, 'missing trusted local asset evidence')
    asset = assets[identifier]
    require(isinstance(asset, dict) and asset.get('type') == kind, 'asset evidence kind invalid')
    path = within(root, asset.get('path', ''))
    require(path.is_file() and sha(path.read_bytes()) == asset.get('sha256'), 'asset missing or changed')
    return asset


def validate_proposal(value, root=None, assets=None, *, scenes=SCENES):
    shape(value, ('schemaVersion', 'kind', 'sceneId', 'data'))
    require(type(value['schemaVersion']) is int and value['schemaVersion'] == 1, 'proposal version invalid')
    require(value['sceneId'] in scenes and value['kind'] in KINDS, 'unknown clip or proposal kind')
    data, kind = value['data'], value['kind']
    if kind == 'script':
        shape(data, ('durationMs', 'narration', 'action'))
        integer(data['durationMs'], 1, 24000); text(data['narration'], 0, 300); text(data['action'], 1, 4096)
    elif kind == 'visual-prompt':
        shape(data, ('positive', 'negative')); text(data['positive'], 1, 4096); text(data['negative'], 0, 2048)
    elif kind == 'continuity':
        shape(data, ('sourceFrameId', 'sourceFrameSha256', 'lines'))
        require(root is not None and assets is not None, 'trusted frame registry required')
        require(isinstance(data['lines'], list) and len(data['lines']) == 2, 'continuity needs exactly two lines')
        for line in data['lines']: text(line, 1, 200)
        asset = verify_asset(root, assets, data['sourceFrameId'], 'tail-frame')
        index = scenes.index(value['sceneId'])
        require(index > 0 and asset.get('clipId') == scenes[index - 1], 'tail must come from the preceding clip')
        require(data['sourceFrameSha256'] == asset['sha256'], 'proposal frame digest differs from evidence')
    elif kind == 'voice-range':
        shape(data, ('voiceSourceId', 'startMs', 'endMs'))
        require(root is not None and assets is not None, 'trusted audio registry required')
        asset = verify_asset(root, assets, data['voiceSourceId'], 'audio')
        require(asset.get('probedSha256') == asset['sha256'], 'audio duration evidence is stale')
        integer(asset.get('durationMs'), 1, 3600000)
        integer(data['startMs'], 0, asset['durationMs']); integer(data['endMs'], 1, asset['durationMs'])
        require(data['startMs'] < data['endMs'], 'voice range must have positive duration')
    else:
        shape(data, ('target', 'decision', 'artifacts', 'notes'))
        require(data['target'] in ('assets', 'draft', 'final', 'voice', 'film') and
                data['decision'] in ('pass', 'rework'), 'invalid technical review decision')
        require(isinstance(data['artifacts'], list) and 1 <= len(data['artifacts']) <= 12, 'review needs actual artifacts')
        text(data['notes'], 1, 2000)
        seen = set()
        for item in data['artifacts']:
            shape(item, ('id', 'sha256'))
            text(item['id'], 1, 100)
            require(item['id'] not in seen, 'duplicate reviewed artifact'); seen.add(item['id'])
            require(root is not None and assets is not None and item['id'] in assets, 'trusted review evidence missing')
            asset = assets[item['id']]
            require(asset.get('type') in ('image', 'video', 'audio'), 'unsupported review evidence')
            verify_asset(root, assets, item['id'], asset['type'])
            require(item['sha256'] == asset['sha256'], 'review artifact changed')
    return value


def validate_script_rollup(values, *, scenes=SCENES, duration_ms=24000, narration_limit=300):
    require(isinstance(values, list) and len(values) == len(scenes), 'all clip scripts required')
    for value in values:
        validate_proposal(value, scenes=scenes)
        require(value['kind'] == 'script', 'script rollup cannot contain other kinds')
    require({value['sceneId'] for value in values} == set(scenes), 'script clips missing or duplicated')
    require(sum(value['data']['durationMs'] for value in values) == duration_ms, 'script duration differs from work target')
    require(sum(len(value['data']['narration']) for value in values) <= narration_limit, 'cumulative narration exceeds authorization')


def verified_record(path):
    try:
        record = decode(path.read_text(encoding='utf-8'))
        saved = record.pop('recordSha256')
        require(saved == sha(canonical(record)) and record.get('state') == 'VALIDATED_DATA_ONLY',
                'stored proposal receipt changed')
        record['recordSha256'] = saved
        return record
    except (OSError, KeyError, TypeError, UnicodeError):
        raise ValueError('stored proposal receipt invalid; manual reconciliation required') from None


def receipt(root, path, record):
    return {'relativePath': path.relative_to(root).as_posix(), 'sha256': sha(path.read_bytes()),
            'scopeId': record['scopeId'], 'taskId': record['taskId'], 'reportId': record['reportId'],
            'sourceSha256': record['sourceSha256'], 'state': record['state']}


def store_proposal(root, bindings, status, task_id, report_id, assets):
    """Persist one exact final report, without running any role's production tool.

    A crash during exclusive creation may leave an incomplete receipt. Subsequent
    calls fail closed; they never delete/reset it or overwrite an earlier version.
    """
    root = Path(root).resolve()
    require(root.is_dir() and Path(bindings['root']).resolve() == root, 'trusted binding root mismatch')
    for value in (bindings['scopeId'], task_id, report_id):
        require(isinstance(value, str) and IDENTIFIER.fullmatch(value), 'invalid trusted identifier')
    members = bindings['members']
    require(isinstance(members, list) and len(members) == 5, 'five trusted bindings required')
    ids = [member['id'] for member in members]
    require(len(set(ids)) == 5 and all(isinstance(i, str) and IDENTIFIER.fullmatch(i) for i in ids),
            'trusted member IDs invalid')
    require(status.get('scopeId') == bindings['scopeId'] and
            len(status.get('members', [])) == 5 and {item['id'] for item in status['members']} == set(ids),
            'scope status does not match trusted fresh membership')
    tasks = [item for item in status.get('tasks', []) if item['id'] == task_id]
    reports = [item for item in status.get('reports', []) if item['id'] == report_id]
    require(len(tasks) == 1 and len(reports) == 1, 'exact task and report must be present in scope status')
    task, report = tasks[0], reports[0]
    require(task.get('status') == 'succeeded' and task.get('bot_id') in ids and
            report.get('bot_id') == task['bot_id'] and report.get('task_id') == task_id and
            report.get('role') == 'assistant' and report.get('kind') in ('message', 'result'),
            'report is not the completed member task final response')
    raw = report.get('content')
    text(raw, 2, 20000)
    require(raw == task.get('result_summary'), 'report differs from task final response')
    value = validate_proposal(decode(raw), root, assets)
    binding = next(item for item in members if item['id'] == task['bot_id'])
    require(value['kind'] in binding.get('allowedKinds', []), 'role is not allowed to submit this proposal kind')
    if value['kind'] == 'technical-review':
        require(all(assets[item['id']].get('reviewTaskId') == task_id
                    for item in value['data']['artifacts']), 'review lacks trusted evidence for this actual task')
    return persist_validated_proposal(root, bindings['scopeId'], task['bot_id'], task_id,
                                     report_id, value, raw)


def persist_validated_proposal(root, scope_id, bot_id, task_id, report_id, value, raw, origin=None):
    """Internal persistence after a trusted source-specific validator has run.

    Native CLI sources supply an explicit origin. They never manufacture a Core
    assistant report, and an existing report ID cannot change its source type.
    """
    folder = within(root, root / '准备输入/岗位产物')
    source_hash = sha(raw.encode('utf-8'))
    with exclusive_lock(root / '.proposal-sink.lock'):
        existing = []
        for path in folder.glob('*/*/*.v*.json'):
            within(root, path)
            saved = verified_record(path)
            require(saved.get('scopeId') == scope_id, 'stored proposal belongs to another scope')
            previous = saved.get('origin')
            if origin is not None and isinstance(previous, dict) and previous.get('source') == origin.get('source') and previous.get('runKey') == origin.get('runKey'):
                require(saved.get('reportId') == report_id and saved.get('sourceSha256') == source_hash and
                        previous == origin, 'completed native turn changed; manual reconciliation required')
            if saved.get('reportId') == report_id:
                require(saved.get('sourceSha256') == source_hash and saved.get('taskId') == task_id and
                        saved.get('botId') == bot_id and saved.get('origin') == origin,
                        'report ID replay changed content or origin')
                return receipt(root, path, saved)
            existing.append((path, saved))
        target_dir = within(root, folder / bot_id / value['sceneId'])
        target_dir.mkdir(parents=True, exist_ok=True)
        within(root, target_dir)
        versions = [saved['version'] for path, saved in existing
                    if path.parent == target_dir and saved['proposal']['kind'] == value['kind']]
        version = max(versions, default=0) + 1
        require(version <= 9999, 'proposal version limit reached')
        path = target_dir / f'{value["kind"]}.v{version:04d}.json'
        record = {'schemaVersion': 1, 'version': version, 'state': 'VALIDATED_DATA_ONLY',
                  'scopeId': scope_id, 'botId': bot_id, 'taskId': task_id, 'reportId': report_id,
                  'sourceSha256': source_hash, 'proposal': value}
        if origin is not None: record['origin'] = origin
        record['recordSha256'] = sha(canonical(record))
        with path.open('xb') as output:
            output.write(canonical(record) + b'\n'); output.flush(); os.fsync(output.fileno())
        return receipt(root, path, record)
