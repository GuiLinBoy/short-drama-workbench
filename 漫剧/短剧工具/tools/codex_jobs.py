"""Codex adapter for the existing Snow Gate recipes, not a general film engine.

The old NexBot adapter is retained. Only identity, receipt selection, tool entry
and result transport change here; production recipes and media limits are reused.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acceptance_jobs as recipes
import authorized_cli as launcher
import codex_workflow as wf
import proposal_sink as data

ROOT = Path(__file__).resolve().parents[1]


class Runner(recipes.Runner):
    def __init__(self, root, task_id, thread_id):
        super().__init__(root)
        self.work = wf.Work(root)
        self.task_id = task_id
        self.task_record = self.work.task(task_id)
        wf.require(self.task_record['threadId'] == thread_id, 'job belongs to another role thread')
        wf.require(self.task_record['kind'] == 'work-report' and self.task_record['action'] in recipes.ACTIONS,
                   'task is not a finite local job')
        wf.require(self.task_record['role'] == recipes.role_for(self.task_record['action']), 'job role differs')
        wf.require(tuple(self.work.spec['scenes']) == recipes.CLIPS and self.work.spec['totalMs'] == 24000,
                   'these recipes only cover the three-clip Snow Gate pilot')
        self.outputs = []

    def cli(self, tool, *args):
        command = [sys.executable, '-I', str(ROOT / 'tools/codex_cli.py'), '--project-scope',
                   str(self.root), tool, *map(str, args)]
        env = launcher.clean_environment(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        result = subprocess.run(command, cwd=self.root, env=env, capture_output=True,
                                text=True, encoding='utf-8', timeout=3700)
        self.steps.append({'tool': tool, 'exitCode': result.returncode,
                           'stdoutSha256': data.sha(result.stdout.encode('utf-8')),
                           'stderrSha256': data.sha(result.stderr.encode('utf-8'))})
        # Full text stays local; never print errors/provider responses into a chat.
        folder = wf.inside(self.root, 'reports/' + self.task_id)
        folder.mkdir(parents=True, exist_ok=True)
        index = len(self.steps)
        for label, text in (('stdout', result.stdout), ('stderr', result.stderr)):
            path = folder / f'{index:03d}-{label}.txt'
            with path.open('x', encoding='utf-8', newline='\n') as out:
                out.write(text)
            self.outputs.append({'path': path.relative_to(self.root).as_posix(), 'sha256': wf.digest(path)})
        wf.require(result.returncode == 0, 'local tool stopped; inspect local evidence, never create a replacement attempt')
        try:
            return data.decode(result.stdout)
        except ValueError:
            return {'completed': True, 'stdoutSha256': self.steps[-1]['stdoutSha256']}

    def latest(self, role, kind, scene):
        matches = []
        for dep in self.task_record['dependsOn']:
            task = self.work.task(dep)
            if (task['role'], task['kind'], task['sceneId']) == (role, kind, scene):
                matches.append(self.work.result(dep, require_real=True))
        wf.require(len(matches) == 1, 'one explicit real completed proposal dependency required')
        return matches[0]

    def plan_files(self):
        selected = self.task_record.get('plans', {'assets': '准备输入/资产提案.json', 'clips': '准备输入/分镜提案.json'})
        return {kind: wf.inside(self.root, rel) for kind, rel in selected.items()}

    def plan_items(self, kind):
        rows = recipes.load(self.plan_files()[kind])
        if kind != 'clips':
            return rows
        # Reviewed proposals stay immutable; real-tail continuity edits a runtime copy.
        for row in rows:
            source = wf.inside(self.root, row['prompt'])
            relative = '单集制作/EP001/运行提示词/' + self.task_id + '/' + row['id'].split('/')[-1] + '.prompt.txt'
            target = wf.inside(self.root, relative)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as output:
                    output.write(source.read_bytes())
            else:
                current = recipes.load(self.root / 'clips.json', [])
                registered = next((item for item in current if item['id'] == row['id']), None)
                wf.require((registered and registered['prompt'] == relative) or
                           wf.digest(target) == wf.digest(source), 'unregistered runtime prompt changed')
            row['prompt'] = relative
        return rows

    def script_proposals(self):
        bundles = []
        direct = []
        for dep in self.task_record['dependsOn']:
            task = self.work.task(dep)
            if task['role'] == '编剧' and task['kind'] == 'script':
                direct.append(dep)
            elif task['role'] == '编剧' and task['kind'] == 'work-report' and task['action'] is None:
                bundles.append(dep)
        if not bundles:
            return [self.latest('编剧', 'script', scene)['proposal'] for scene in recipes.CLIPS]
        wf.require(len(bundles) == 1 and not direct, 'choose one exact writer bundle or individual script receipts')
        record = self.work.result(bundles[0], require_real=True)
        artifacts = record['proposal']['data']['artifacts']
        selected_clips = self.plan_files()['clips'].relative_to(self.root).as_posix()
        wf.require(any(a['path'] == selected_clips for a in artifacts),
                   'selected clip plan was not produced by the actual writer task')
        scripts = []
        for artifact in artifacts:
            if artifact['path'].endswith('.script.json'):
                path = wf.inside(self.root, artifact['path'])
                raw = path.read_bytes()
                wf.require(data.sha(raw) == artifact['sha256'], 'writer script artifact changed')
                scripts.append(data.decode(raw))
        return scripts

    def video_prompt(self, phase, cliprow):
        if phase != 'smoke':
            source = super().video_prompt(phase, cliprow)
            if phase not in ('final', 'redo') or not any(shot.get('dialogue') for shot in cliprow['shots']):
                return source
            body = source.read_text('utf-8')
            wf.require(body.count('\nsummary:') == 1, 'voice binding requires exact six-section prompt')
            binding = '<Subject 1> (S1) 的音色遵循[音色参考]；只在剧本规定的时间说出规定的台词。\n'
            body = body.replace('\nsummary:', binding + '\nsummary:', 1)
            relative = '单集制作/EP001/运行提示词/' + self.task_id + '/voice-bound.prompt.txt'
            target = wf.inside(self.root, relative)
            if target.exists():
                wf.require(target.read_text('utf-8') == body, 'voice-bound request prompt changed')
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('x', encoding='utf-8', newline='\n') as output:
                    output.write(body)
            return target
        candidates = []
        for dep in self.task_record['dependsOn']:
            task = self.work.task(dep)
            if task['role'] == '摄影' and task['kind'] == 'work-report' and task['action'] is None:
                result = self.work.result(dep, require_real=True)
                candidates += [item['path'] for item in result['proposal']['data']['artifacts']
                               if item['path'].endswith('/Clip00001-smoke.prompt.txt')]
        wf.require(len(candidates) == 1 and candidates[0] in self.task_record['inputs'],
                   'smoke requires one fingerprinted actual camera four-second prompt')
        return wf.inside(self.root, candidates[0])

    def plan(self):
        scripts = self.script_proposals()
        data.validate_script_rollup(scripts)
        required = {p.relative_to(self.root).as_posix() for p in self.plan_files().values()}
        rows = recipes.load(self.plan_files()['clips'])
        wf.require(isinstance(rows, list) and len(rows) == len(recipes.CLIPS), 'pilot plan requires exact clip rows')
        by_scene = {row['id'].split('/')[-1]: row for row in rows}
        wf.require(set(by_scene) == set(recipes.CLIPS), 'pilot clip rows missing or duplicated')
        for script in scripts:
            row = by_scene[script['sceneId']]
            required.add(row['prompt'])
            expected = dict(durationMs=row['duration'] * 1000,
                            narration=''.join(item['text'] for item in row.get('narration', [])),
                            action='\n'.join(item['visual'] for item in row['shots']))
            wf.require(script['data'] == expected,
                       'writer proposal differs from prepared pilot; explicitly version/review the changed plan')
        wf.require(required.issubset(self.task_record['inputs']),
                   'pilot registration task must fingerprint both plans and all prompt files')
        return super().plan()

    def execute(self):
        task, started = self.work.start(self.task_id)
        if task['state'] == 'completed':
            return self.work.result(self.task_id, require_real=not self.work.spec['simulation'])
        wf.require(started, 'already running job cannot execute twice; reconcile its original evidence')
        try:
            result = self.run(task['action'])
            folder = wf.inside(self.root, 'reports/' + self.task_id)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / 'result.json'
            # Data can be large; controller reads this on demand, not as tool output.
            wf.create(path, {'action': task['action'], 'result': result, 'steps': self.steps})
            record = {'version': 1, 'state': 'LOCAL_TOOL_COMPLETED', 'workId': task['workId'],
                      'taskId': self.task_id, 'role': task['role'], 'inputs': task['inputs'],
                      'action': task['action'],
                      'artifacts': [{'path': path.relative_to(self.root).as_posix(), 'sha256': wf.digest(path)}],
                      'origin': {'source': 'LOCAL_PROCESS_EXIT', 'threadId': task['threadId'],
                                 'hostId': task['hostId'], 'simulation': task['simulation']}}
            with self.work.lock():
                self.work.check_inputs(task)
                wf.create(self.work.receipt_path(self.task_id), record)
                task['state'] = 'completed'
                self.work.save_task(task)
            return record
        except BaseException:
            with self.work.lock():
                current = self.work.task(self.task_id)
                if current['state'] != 'completed':
                    current['state'] = 'unknown'
                    self.work.save_task(current)
            raise


def summary(record):
    return {k: record[k] for k in ('workId', 'taskId', 'role', 'state', 'action', 'artifacts')}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--thread-id', required=True)
    a = parser.parse_args()
    record = Runner(a.project, a.task_id, a.thread_id).execute()
    print(json.dumps(summary(record), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit('STOP: ' + str(error))
