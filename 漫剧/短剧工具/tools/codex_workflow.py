"""Local Codex task/receipt bookkeeping. No model, network or credential access.

Thread observations are normalized by the trusted app-tools controller, not by a
worker's reply. Hashes detect accidental changes; they are not authentication or
an OS sandbox. A model's final text alone never proves a tool executed.
"""
import argparse
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proposal_sink as data
from generation_runtime import atomic_json, exclusive_lock

ROLES = ('总导演', '编剧', '美术', '摄影', '剪辑')
KINDS = {'总导演': ('technical-review',), '编剧': ('script',),
         '美术': ('visual-prompt',), '摄影': ('continuity', 'voice-range'), '剪辑': ()}
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def identifier(value):
    require(isinstance(value, str) and ID.fullmatch(value), 'invalid controller identifier')
    require(value not in ('.', '..') and ':' not in value, 'identifier is not a filename')
    return value


def inside(root, relative):
    root = Path(root).resolve()
    require(isinstance(relative, str) and relative and '\0' not in relative, 'relative path required')
    win = PureWindowsPath(relative)
    require(not win.drive and not win.root and not Path(relative).is_absolute() and
            '..' not in relative.replace('\\', '/').split('/'), 'absolute or parent path refused')
    path = (root / relative).resolve()
    require(path != root and path.is_relative_to(root), 'path escaped work directory')
    return path


def digest(path):
    with Path(path).open('rb') as source:
        import hashlib
        return hashlib.file_digest(source, 'sha256').hexdigest()


def read(path):
    try:
        return data.decode(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError('missing or invalid local state; reconcile without resetting it') from error


def sealed(value):
    return {**value, 'recordSha256': data.sha(data.canonical(value))}


def verified(path):
    value = read(path)
    require(isinstance(value, dict) and 'recordSha256' in value, 'unsealed state refused')
    body = {k: v for k, v in value.items() if k != 'recordSha256'}
    require(value['recordSha256'] == data.sha(data.canonical(body)), 'state checksum changed')
    return body


def create(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as target:
        target.write(data.canonical(sealed(value)) + b'\n')
        target.flush()
        os.fsync(target.fileno())


class Work:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.spec = verified(inside(self.root, '协作/工作区.json'))
        require(self.spec.get('version') == 1 and self.spec.get('root') == str(self.root),
                'work root or schema differs')
        identifier(self.spec['workId'])
        require(type(self.spec.get('simulation')) is bool and
                self.spec.get('mediaMode') == 'approval-and-readiness-required', 'invalid initial execution policy')

    @classmethod
    def initialize(cls, root, work_id, scenes, total_ms, narration_limit, simulation=False):
        root = Path(root).resolve()
        identifier(work_id)
        require(root.is_dir() and (root / 'project.json').is_file(), 'existing project required')
        require(type(simulation) is bool and isinstance(scenes, list) and scenes and
                len(set(scenes)) == len(scenes), 'invalid scene list or simulation flag')
        for scene in scenes:
            identifier(scene)
        require(type(total_ms) is int and total_ms > 0 and
                type(narration_limit) is int and narration_limit >= 0, 'invalid work limits')
        for rel in ('approvals/user-authorization.json', 'approvals/execution-limits.json'):
            require(inside(root, rel).is_file(), 'work authorization and media limits required')
        import execution_limits
        policy, _ = execution_limits.config(root)
        require(narration_limit == policy['caps']['tts_characters'], 'narration limit differs from authorization')
        spec = dict(version=1, workId=work_id, root=str(root), scenes=scenes,
                    totalMs=total_ms, narrationLimit=narration_limit,
                    simulation=simulation, mediaMode='approval-and-readiness-required')
        with exclusive_lock(inside(root, '协作/.lock')):
            path = inside(root, '协作/工作区.json')
            if path.exists():
                require(verified(path) == spec, 'registered work changed; no overwrite')
            from project_rules import ensure_entry
            ensure_entry(root)
            if not path.exists():
                create(path, spec)
        return cls(root)

    def lock(self):
        return exclusive_lock(inside(self.root, '协作/.lock'))

    def task_path(self, task_id):
        return inside(self.root, '协作/任务/' + identifier(task_id) + '.json')

    def receipt_path(self, task_id):
        return inside(self.root, '协作/回执/' + identifier(task_id) + '.json')

    def task(self, task_id):
        value = verified(self.task_path(task_id))
        require(value['workId'] == self.spec['workId'] and value['taskId'] == task_id,
                'task belongs to another work')
        require(value['simulation'] == self.spec['simulation'] and
                value['state'] in ('awaiting_thread', 'planned', 'running', 'unknown', 'completed', 'failed'),
                'task execution policy or state changed')
        return value

    def save_task(self, value):
        atomic_json(self.task_path(value['taskId']), sealed(value))

    def bindings(self):
        path = inside(self.root, '协作/会话.json')
        value = verified(path) if path.exists() else {'workId': self.spec['workId'], 'roles': {}, 'history': []}
        require(value['workId'] == self.spec['workId'], 'thread mapping belongs to another work')
        return value

    def bind(self, role, observation):
        require(role in ROLES, 'unknown role')
        data.shape(observation, ('source', 'threadId', 'hostId', 'status', 'simulation'))
        require(observation['source'] == 'CODEX_APP_CREATE_THREAD' and observation['status'] == 'ready' and
                type(observation['simulation']) is bool and
                observation['simulation'] == self.spec['simulation'], 'trusted ready-thread observation required')
        identifier(observation['threadId'])
        identifier(observation['hostId'])
        with self.lock():
            value = self.bindings()
            old = value['roles'].get(role)
            if old == observation:
                return old
            require(not any(item['threadId'] == observation['threadId'] for item in value['roles'].values()),
                    'one thread cannot own two roles')
            if old:
                require(not any(self.task(p.stem)['role'] == role and self.task(p.stem)['state'] in ('running', 'unknown')
                                for p in inside(self.root, '协作/任务').glob('*.json')),
                        'role has unresolved work; reconcile before replacing its thread')
                value['history'].append({'role': role, **old})
            value['roles'][role] = observation
            atomic_json(inside(self.root, '协作/会话.json'), sealed(value))
        return observation

    def check_inputs(self, task):
        for rel, sha in task['inputs'].items():
            path = inside(self.root, rel)
            require(path.is_file() and digest(path) == sha, 'task input missing or changed')
        for dep in task['dependsOn']:
            self.result(dep, require_real=not self.spec['simulation'])

    def new_task(self, role, kind, scene, inputs, depends_on=(), task_id=None, action=None, prepare=False, plans=None):
        require(role in ROLES and (kind == 'work-report' or kind in KINDS[role]), 'proposal kind not owned by role')
        require(scene in self.spec['scenes'] and isinstance(inputs, list) and inputs, 'scene and input files required')
        require(len(set(inputs)) == len(inputs), 'duplicate input')
        if action is not None:
            import acceptance_jobs
            require(acceptance_jobs.role_for(action) == role and kind == 'work-report', 'job role/action differs')
        if plans is not None:
            require(action == 'plan-register' and role == '编剧' and not prepare, 'versioned plans only apply to registration')
            data.shape(plans, ('assets', 'clips'))
            for rel in plans.values():
                require(isinstance(rel, str) and rel in inputs and rel.endswith('.json') and
                        rel.startswith(('准备输入/', '协作/提案/')), 'selected plan must be a fingerprinted proposal input')
                inside(self.root, rel)
        task_id = identifier(task_id or 'task-' + uuid.uuid4().hex)
        require(not prepare or (kind == 'work-report' and action is None and not depends_on),
                'only initial read-only reports may be prepared before thread creation')
        with self.lock():
            binding = self.bindings()['roles'].get(role)
            require(prepare or binding is not None, 'role thread has not been created and observed')
            hashes = {rel: digest(inside(self.root, rel)) for rel in inputs}
            deps = list(depends_on)
            require(len(set(deps)) == len(deps) and task_id not in deps, 'invalid dependencies')
            for dep in deps:
                self.task(identifier(dep))
            value = dict(version=1, workId=self.spec['workId'], taskId=task_id, role=role, kind=kind,
                         sceneId=scene, threadId=None if prepare else binding['threadId'],
                         hostId=None if prepare else binding['hostId'],
                         simulation=self.spec['simulation'], inputs=hashes, dependsOn=deps,
                         action=action, preparedBeforeThread=prepare,
                         state='awaiting_thread' if prepare else 'planned')
            if plans is not None:
                value['plans'] = plans
            if self.task_path(task_id).exists():
                old = self.task(task_id)
                ignored = ('state', 'threadId', 'hostId') if prepare else ('state',)
                require({k: v for k, v in old.items() if k not in ignored} ==
                        {k: v for k, v in value.items() if k not in ignored}, 'task ID reused with a different specification')
                return old
            create(self.task_path(task_id), value)
        return value

    def attach(self, task_id):
        """Attach a prewritten initial prompt to a real observed created thread."""
        with self.lock():
            task = self.task(task_id)
            require(task.get('preparedBeforeThread') is True, 'not an initial creation task')
            binding = self.bindings()['roles'].get(task['role'])
            require(binding is not None, 'created thread has not been observed')
            self.check_inputs(task)
            if task['state'] == 'awaiting_thread':
                task.update(threadId=binding['threadId'], hostId=binding['hostId'], state='planned')
                self.save_task(task)
            else:
                require((task['threadId'], task['hostId']) == (binding['threadId'], binding['hostId']),
                        'initial task already belongs to another thread')
            return task

    def start(self, task_id):
        with self.lock():
            task = self.task(task_id)
            self.check_inputs(task)
            require(task['state'] in ('planned', 'running', 'completed'), 'unknown/failed task cannot restart automatically')
            started = task['state'] == 'planned'
            if started:
                current = self.bindings()['roles'].get(task['role'])
                require(current and current['threadId'] == task['threadId'], 'role thread changed')
                task['state'] = 'running'
                self.save_task(task)
            return task, started

    def complete(self, task_id, raw, observation, assets=None):
        """Controller supplies an observed final reply, never a worker-supplied status."""
        with self.lock():
            task = self.task(task_id)
            self.check_inputs(task)
            require(task['state'] in ('running', 'unknown', 'completed') and task['action'] is None,
                    'not an active proposal task')
            data.shape(observation, ('source', 'threadId', 'hostId', 'turnId', 'taskId', 'inputsSha256',
                                     'status', 'finalSha256', 'simulation'))
            require(observation['source'] == 'CODEX_APP_FINAL_RESPONSE' and observation['status'] == 'completed' and
                    observation['threadId'] == task['threadId'] and observation['hostId'] == task['hostId'] and
                    observation['taskId'] == task_id and
                    observation['inputsSha256'] == data.sha(data.canonical(task['inputs'])) and
                    type(observation['simulation']) is bool and observation['simulation'] == task['simulation'] and
                    observation['finalSha256'] == data.sha(raw.encode('utf-8')), 'completed thread/final response differs')
            identifier(observation['turnId'])
            value = data.decode(raw)
            require(isinstance(value, dict) and value.get('kind') == task['kind'] and
                    value.get('sceneId') == task['sceneId'], 'reply belongs to another task kind/scene')
            if task['kind'] == 'work-report':
                validate_work_report(value, self.root, task)
            else:
                registry = {}
                for key, item in (assets or {}).items():
                    item = dict(item)
                    path = inside(self.root, item['path'])
                    require(item['path'] in task['inputs'] and digest(path) == task['inputs'][item['path']],
                            'review evidence was not in this task input')
                    item['path'] = str(path)
                    registry[key] = item
                data.validate_proposal(value, self.root, registry, scenes=tuple(self.spec['scenes']))
                if task['kind'] == 'technical-review':
                    require(all(registry[a['id']].get('reviewTaskId') == task_id for a in value['data']['artifacts']),
                            'review evidence not linked to this task')
            record = dict(version=1, state='VALIDATED_DATA_ONLY', workId=task['workId'],
                          role=task['role'], taskId=task_id, reportId=observation['turnId'],
                          inputs=task['inputs'], proposal=value, origin=observation)
            for p in inside(self.root, '协作/回执').glob('*.json'):
                old = verified(p)
                require(not (old.get('origin', {}).get('threadId') == observation['threadId'] and
                             old.get('origin', {}).get('turnId') == observation['turnId'] and
                             old.get('taskId') != task_id), 'completed turn already belongs to another task')
            path = self.receipt_path(task_id)
            if path.exists():
                require(verified(path) == record, 'completed receipt changed; reconcile without overwrite')
            else:
                create(path, record)
            task['state'] = 'completed'
            self.save_task(task)
            return record

    def result(self, task_id, require_real=True):
        task = self.task(task_id)
        require(task['state'] == 'completed', 'task has not completed')
        return self._receipt(task, require_real)

    def _receipt(self, task, require_real):
        record = verified(self.receipt_path(task['taskId']))
        require(record.get('version') == 1 and
                record['workId'] == task['workId'] and record['taskId'] == task['taskId'] and
                record['role'] == task['role'] and record['inputs'] == task['inputs'] and
                record['origin']['threadId'] == task['threadId'] and
                record['origin']['hostId'] == task['hostId'] and
                record['origin']['simulation'] is task['simulation'], 'receipt identity differs')
        if require_real:
            require(not task['simulation'] and record['origin'].get('simulation') is False,
                    'simulation cannot enter real production')
        if task['action'] is not None:
            require(record.get('state') == 'LOCAL_TOOL_COMPLETED' and record.get('action') == task['action'] and
                    record['origin'].get('source') == 'LOCAL_PROCESS_EXIT', 'local job evidence differs')
            for artifact in record['artifacts']:
                require(digest(inside(self.root, artifact['path'])) == artifact['sha256'], 'local job artifact changed')
        else:
            require(record.get('state') == 'VALIDATED_DATA_ONLY' and
                    record['proposal']['kind'] == task['kind'] and
                    record['proposal']['sceneId'] == task['sceneId'], 'proposal receipt differs')
            if task['kind'] == 'work-report':
                validate_work_report(record['proposal'], self.root, task)
        self.check_inputs(task)
        return record

    def reconcile(self, task_id):
        """Recover only an already saved, valid completion; no task is executed."""
        with self.lock():
            task = self.task(task_id)
            require(task['state'] in ('running', 'unknown', 'completed'), 'no interrupted completion to reconcile')
            record = self._receipt(task, require_real=not self.spec['simulation'])
            if task['state'] != 'completed':
                task['state'] = 'completed'
                self.save_task(task)
            return record

    def latest(self, role, kind, scene, require_real=True):
        candidates = []
        for p in inside(self.root, '协作/任务').glob('*.json'):
            task = self.task(p.stem)
            if (task['role'], task['kind'], task['sceneId']) == (role, kind, scene) and task['state'] == 'completed':
                candidates.append(self.result(task['taskId'], require_real))
        require(len(candidates) == 1, 'select an exact completed task; ambiguous or missing proposal')
        return candidates[0]

    def recover(self):
        changed = []
        with self.lock():
            for p in inside(self.root, '协作/任务').glob('*.json'):
                task = self.task(p.stem)
                if task['state'] == 'running':
                    task['state'] = 'unknown'
                    self.save_task(task)
                    changed.append(task['taskId'])
        return changed

    def status(self):
        from voice_lifecycle import safe_audit
        tasks = [self.task(p.stem) for p in inside(self.root, '协作/任务').glob('*.json')]
        return dict(workId=self.spec['workId'], simulation=self.spec['simulation'],
                    mediaMode=self.spec['mediaMode'], roles=list(self.bindings()['roles']),
                    tasks=[{k: t[k] for k in ('taskId', 'role', 'kind', 'sceneId', 'state')} for t in tasks],
                    voice_closeout=safe_audit(self.root))


def validate_work_report(value, root, task):
    data.shape(value, ('schemaVersion', 'kind', 'sceneId', 'data'))
    require(type(value['schemaVersion']) is int and value['schemaVersion'] == 1, 'wrong report version')
    body = value['data']
    data.shape(body, ('summary', 'artifacts', 'needsUser'))
    data.text(body['summary'], 1, 4000)
    require(type(body['needsUser']) is bool and isinstance(body['artifacts'], list), 'invalid work report')
    for artifact in body['artifacts']:
        data.shape(artifact, ('path', 'sha256'))
        path = inside(root, artifact['path'])
        require(path.is_file() and digest(path) == artifact['sha256'], 'reported artifact changed')
        require(artifact['path'].startswith(('协作/提案/' + task['role'] + '/',
                                            'reports/' + task['taskId'] + '/')),
                'report cannot claim another role, approvals or shared manifests')


def refresh_indexes(data_root):
    """Derived indexes are recoverable views, never task or budget authority."""
    root = Path(data_root).resolve()
    works = []
    threads = []
    for p in sorted((root / '作品').glob('*/协作/工作区.json')):
        work = Work(p.parents[1])
        with work.lock():
            status = work.status()
            works.append({'workId': status['workId'], 'root': str(work.root),
                          'simulation': status['simulation'], 'tasks': status['tasks']})
            threads.extend({'workId': status['workId'], 'role': role, **value}
                           for role, value in work.bindings()['roles'].items())
    require(len({w['workId'] for w in works}) == len(works), 'duplicate work IDs')
    require(len({(t['hostId'], t['threadId']) for t in threads}) == len(threads),
            'one thread cannot be shared by works')
    with exclusive_lock(inside(root, '调度/.lock')):
        atomic_json(inside(root, '调度/作品索引.json'), sealed({'version': 1, 'derived': True, 'works': works}))
        atomic_json(inside(root, '调度/会话索引.json'), sealed({'version': 1, 'derived': True, 'threads': threads}))
    return {'works': len(works), 'roleThreads': len(threads)}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init')
    p.add_argument('--work-id', required=True)
    p.add_argument('--scene', action='append', required=True)
    p.add_argument('--total-ms', type=int, required=True)
    p.add_argument('--narration-limit', type=int, required=True)
    p.add_argument('--simulation', action='store_true')
    sub.add_parser('status')
    sub.add_parser('recover')
    sub.add_parser('index')
    p = sub.add_parser('bind')
    p.add_argument('--role', choices=ROLES, required=True)
    p.add_argument('--observation', required=True)
    p = sub.add_parser('task')
    p.add_argument('--role', choices=ROLES, required=True)
    p.add_argument('--kind', required=True)
    p.add_argument('--scene', required=True)
    p.add_argument('--input', action='append', required=True)
    p.add_argument('--depends-on', action='append', default=[])
    p.add_argument('--action')
    p.add_argument('--task-id')
    p.add_argument('--prepare-before-thread', action='store_true')
    p.add_argument('--plans-json')
    p = sub.add_parser('start')
    p.add_argument('--task-id', required=True)
    p = sub.add_parser('complete')
    p.add_argument('--task-id', required=True)
    p.add_argument('--reply', required=True)
    p.add_argument('--observation', required=True)
    p.add_argument('--assets')
    p = sub.add_parser('reconcile')
    p.add_argument('--task-id', required=True)
    p = sub.add_parser('attach')
    p.add_argument('--task-id', required=True)
    a = parser.parse_args()
    if a.command == 'init':
        result = Work.initialize(a.project, a.work_id, a.scene, a.total_ms, a.narration_limit, a.simulation).status()
    else:
        work = Work(a.project)
        if a.command == 'status':
            result = work.status()
        elif a.command == 'recover':
            result = {'unknownTasks': work.recover()}
        elif a.command == 'index':
            result = refresh_indexes(work.root.parent.parent)
        elif a.command == 'bind':
            result = work.bind(a.role, read(inside(work.root, a.observation)))
        elif a.command == 'task':
            result = work.new_task(a.role, a.kind, a.scene, a.input, a.depends_on,
                                   task_id=a.task_id, action=a.action, prepare=a.prepare_before_thread,
                                   plans=read(inside(work.root, a.plans_json)) if a.plans_json else None)
        elif a.command == 'attach':
            task = work.attach(a.task_id)
            result = {k: task[k] for k in ('taskId', 'threadId', 'hostId', 'state')}
        elif a.command == 'start':
            task, started = work.start(a.task_id)
            result = {'taskId': task['taskId'], 'newStart': started, 'state': task['state']}
        elif a.command == 'reconcile':
            record = work.reconcile(a.task_id)
            result = {k: record[k] for k in ('workId', 'taskId', 'state')}
        else:
            record = work.complete(a.task_id, inside(work.root, a.reply).read_text(encoding='utf-8'),
                                   read(inside(work.root, a.observation)),
                                   read(inside(work.root, a.assets)) if a.assets else None)
            result = {k: record[k] for k in ('workId', 'taskId', 'state')}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit('STOP: ' + str(error))
