"""Fault regressions: no real API, credentials or production data."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import os
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))


def module(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


narrate = module('tools/audio/narrate.py', 'reg_narrate')
h3 = module('skills/minimax-h3-video/scripts/h3_task.py', 'reg_h3')
import generation_runtime as runtime
import pipeline_state
import make_sample_project


def worker():
    mode, task = sys.argv[2], Path(sys.argv[3])
    root = task.parents[2]
    global_root = root.parent.parent
    task.mkdir(parents=True, exist_ok=True)
    with runtime.exclusive_lock(task / '.attempt.lock'):
        runtime.recover(task)
        if mode == 'settle':
            runtime.settle(runtime.read_json(task / 'state.json'), task, .3)
        elif mode == 'crash':
            runtime.ledger_once = lambda *a: os._exit(37)
            runtime.settle(runtime.read_json(task / 'state.json'), task, .3)
        else:
            with runtime.exclusive_lock(global_root / '全局设置.lock'):
                providers = {'nexorai'} if mode == 'nexorai' else runtime.SHARED_PROVIDERS
                if runtime.scan(root.parent, providers):
                    raise SystemExit(4)
                state = {'provider': mode, 'status': 'submitting', 'submitted_at': 0}
                kind = 'image' if mode == 'nexorai' else 'speech' if mode == 'compshare-indextts' else 'video'
                fields = {'images': 1, 'date': '2026-11-06'} if kind == 'image' else {'points': .12}
                runtime.reserve(state, task, kind, root / 'approvals' / 'budget.json', 'B',
                                1 if kind == 'image' else .12, fields)


class QueryFaults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / '漫剧' / '作品' / 'A'
        self.task = self.root / 'tasks' / 'T' / 'attempt-001'
        self.task.mkdir(parents=True)
        self.budget = self.root / 'approvals' / 'budget.json'
        self.budget.parent.mkdir()
        self.budget.write_text(json.dumps([{'batch': 'B', 'kind': 'speech', 'unit': 'points',
                                           'used': .12, 'limit': 1}]), encoding='utf-8')
        self.state = {'provider': 'compshare-indextts', 'task_id': 'mock', 'status': 'submitted',
                      'charge': {'file': str(self.budget), 'batch': 'B', 'project': 'A',
                                 'points': .12, 'refunded': False}}
        self.save()
        self.args = SimpleNamespace(task_dir=self.task, output=None)

    def save(self):
        (self.task / 'state.json').write_text(json.dumps(self.state), encoding='utf-8')

    def query(self, code, body):
        with patch.object(narrate, 'http', return_value=(code, body)):
            try:
                narrate.cmd_query(self.args)
            except SystemExit:
                pass

    def used(self):
        return json.loads(self.budget.read_text(encoding='utf-8'))[0]['used']

    def test_query_errors_never_refund(self):
        for code in (401, 404, 429, 500):
            with self.subTest(code=code):
                self.query(code, {'error': 'query unavailable'})
                self.assertEqual(self.used(), .12)

    def test_failed_but_still_reserved_does_not_refund(self):
        self.query(200, {'task': {'status': 'failed', 'point_status': 'reserved'}})
        self.assertEqual(self.used(), .12)

    def test_download_failure_cannot_repeat_settlement(self):
        self.state['output'] = str(self.root / 'audio.wav')
        self.save()
        body = {'task': {'status': 'succeeded', 'point_cost': .3, 'point_status': 'consumed',
                         'output_url': 'http://127.0.0.1:9/missing.wav'}}
        for _ in range(2):
            with patch.object(narrate.urllib.request, 'urlopen', side_effect=OSError('mock download failure')):
                try:
                    self.query(200, body)
                except OSError:
                    pass
        self.assertEqual(self.used(), .3)

    def test_h3_counts_queued_speech(self):
        self.state['status'] = 'queued'
        self.save()
        self.assertEqual(len(h3.running_tasks(self.root.parent)), 1)

    def test_bad_state_cannot_disappear_from_capacity_scan(self):
        (self.task / 'state.json').write_text('{broken', encoding='utf-8')
        with self.assertRaises(SystemExit):
            h3.running_tasks(self.root.parent)

    def test_incomplete_reservation_blocks_admission_until_recovery(self):
        self.state['accounting_version'] = 1
        self.save()
        with self.assertRaises(SystemExit):
            runtime.scan(self.root.parent)

    def test_success_followed_by_query_error_never_refunds(self):
        self.query(200, {'task': {'status': 'succeeded', 'point_cost': .3}})
        self.query(500, {'error': 'mock'})
        self.assertEqual(self.used(), .3)

    def test_released_failure_refunds_exactly_once(self):
        for _ in range(2):
            self.query(200, {'task': {'status': 'failed', 'point_status': 'released'}})
        self.assertEqual(self.used(), 0)
        lines = (self.root.parent.parent / '全局账本.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 1)

    def test_release_clears_prior_refund_pending_marker(self):
        self.query(200, {'task': {'status': 'failed', 'point_status': 'reserved'}})
        self.assertTrue(runtime.read_json(self.task / 'state.json')['refund_pending'])
        self.query(200, {'task': {'status': 'failed', 'point_status': 'released'}})
        final = runtime.read_json(self.task / 'state.json')
        self.assertTrue(final['charge']['refunded'])
        self.assertFalse(final.get('refund_pending'))

    def test_failed_without_release_does_not_refund(self):
        # The actual POST error classification is exercised in CLI tests too.
        self.query(200, {'task': {'status': 'failed'}})
        self.assertEqual(self.used(), .12)

    def test_h3_download_failure_cannot_repeat_settlement(self):
        self.state.update(provider='minimax-h3', output=str(self.root / 'video.mp4'))
        self.save()
        batches = runtime.read_json(self.budget)
        batches[0]['kind'] = 'video'
        runtime.atomic_json(self.budget, batches)
        body = {'task': {'id': 'mock', 'status': 'succeeded', 'usage': {'points': .3},
                         'content': {'url': 'http://127.0.0.1:9/mock.mp4'}}}
        with patch.object(h3, 'http', return_value=(200, body)), \
             patch.object(h3, 'download', side_effect=OSError('injected download failure')):
            for _ in range(2):
                with self.assertRaises(OSError):
                    h3.cmd_query(self.args)
        self.assertEqual(self.used(), .3)

    def test_cancelled_release_can_be_queried_repeatedly(self):
        for _ in range(2):
            self.query(200, {'task': {'status': 'cancelled', 'point_status': 'released'}})
        self.assertEqual(self.used(), 0)

    def test_pending_does_not_create_settings_and_counts_unknown(self):
        settings = self.root.parent.parent / '全局设置.json'
        self.state['status'] = 'unknown'
        self.save()
        with patch.object(h3, 'http', side_effect=AssertionError('network')):
            h3.cmd_pending(SimpleNamespace(project=self.root.parent.parent, all=True))
        self.assertFalse(settings.exists())


class RecoveryTests(unittest.TestCase):
    setUp = QueryFaults.setUp
    save = QueryFaults.save
    used = QueryFaults.used
    def state_for_runtime(self):
        self.state['task_dir'] = str(self.task)
        self.state['charge']['kind'] = 'speech'
        self.save()
        return self.state

    def test_process_crash_after_budget_write_recovers_once(self):
        self.state_for_runtime()
        p = subprocess.run([sys.executable, '-I', __file__, '--worker', 'crash', str(self.task)], capture_output=True)
        self.assertEqual(p.returncode, 37, p.stderr)
        self.assertEqual(self.used(), .3)
        for _ in range(2):
            runtime.recover(self.task)
        self.assertEqual(self.used(), .3)
        self.assertTrue(runtime.read_json(self.task / 'state.json')['charge']['settled'])
        lines = (self.root.parent.parent / '全局账本.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 1)

    def test_crash_after_ledger_append_cannot_duplicate_event(self):
        state = self.state_for_runtime()
        original = runtime.atomic_json
        def fail_state(path, data):
            if Path(path).name == 'state.json':
                raise OSError('injected write failure')
            original(path, data)
        with patch.object(runtime, 'atomic_json', side_effect=fail_state):
            with self.assertRaises(OSError):
                runtime.settle(state, self.task, .3)
        runtime.recover(self.task)
        self.assertEqual(self.used(), .3)
        lines = (self.root.parent.parent / '全局账本.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 1)

    def test_parallel_settlement_is_once(self):
        self.state_for_runtime()
        ps = [subprocess.Popen([sys.executable, '-I', __file__, '--worker', 'settle', str(self.task)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        for p in ps:
            out, err = p.communicate(timeout=30)
            self.assertEqual(p.returncode, 0, err)
        self.assertEqual(self.used(), .3)
        self.assertEqual(len((self.root.parent.parent / '全局账本.jsonl').read_text(encoding='utf-8').splitlines()), 1)

    def test_overrun_is_preserved_and_blocks_new_submits(self):
        state = self.state_for_runtime()
        batches = runtime.read_json(self.budget)
        batches[0]['limit'] = .2
        runtime.atomic_json(self.budget, batches)
        runtime.settle(state, self.task, .3)
        self.assertEqual(self.used(), .12)
        self.assertTrue(runtime.read_json(self.task / 'state.json')['charge']['settlement_pending'])
        with self.assertRaises(SystemExit):
            runtime.validate_budget(self.budget, 'B', 'speech', .01)
        batches = runtime.read_json(self.budget)
        batches[0]['limit'] = 1  # simulate separately approved local budget amendment
        runtime.atomic_json(self.budget, batches)
        runtime.recover(self.task)
        self.assertEqual(self.used(), .3)
        self.assertFalse(runtime.read_json(self.budget)[0]['pending_settlements'])

    def test_identity_separates_projects_but_repeats_stably(self):
        other = self.root.parent / 'B' / 'tasks' / 'T' / 'attempt-001'
        self.assertNotEqual(runtime.identity(self.task, 'same'), runtime.identity(other, 'same'))
        self.assertEqual(runtime.identity(self.task, 'same'), runtime.identity(self.task, 'same'))

    def test_damaged_ledger_is_not_appended_to(self):
        ledger = self.root.parent.parent / '全局账本.jsonl'
        ledger.write_text('{broken', encoding='utf-8')
        with self.assertRaises(SystemExit):
            runtime.settle(self.state_for_runtime(), self.task, .3)
        self.assertEqual(ledger.read_text(encoding='utf-8'), '{broken')


class MultiProjectTests(unittest.TestCase):
    def test_different_attempts_compete_in_shared_pool_both_directions(self):
        for providers in [('minimax-h3', 'compshare-indextts'), ('nexorai', 'nexorai')]:
            with self.subTest(providers=providers), tempfile.TemporaryDirectory() as tmp:
                tasks = []
                for i, prov in enumerate(providers):
                    root = Path(tmp) / '漫剧' / '作品' / str(i)
                    budget = root / 'approvals' / 'budget.json'
                    budget.parent.mkdir(parents=True)
                    kind = 'image' if prov == 'nexorai' else 'speech' if prov == 'compshare-indextts' else 'video'
                    runtime.atomic_json(budget, [{'batch': 'B', 'kind': kind, 'used': 0, 'limit': 10}])
                    tasks.append(root / 'tasks' / 'T' / 'attempt-001')
                ps = [subprocess.Popen([sys.executable, '-I', __file__, '--worker', prov, str(task)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                      for prov, task in zip(providers, tasks)]
                codes = []
                for p in ps:
                    p.communicate(timeout=30)
                    codes.append(p.returncode)
                self.assertEqual(sorted(codes), [0, 4])
                self.assertEqual(len(runtime.scan(tasks[0].parents[3], set(providers))), 1)


class NarrationSourceTests(unittest.TestCase):
    def test_attachment_preserves_other_entry_updates_and_uses_state_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_sample_project.build(Path(tmp) / '漫剧' / '作品' / 'A')
            clips = runtime.read_json(root / 'clips.json')
            clip = clips[-1]
            item = clip['narration'][0]
            expected = pipeline_state.narration_fingerprint(root, clip['id'], item['id'])
            audio = root / 'new.wav'
            audio.write_bytes(b'mock audio')
            errors = []
            def change_other():
                try:
                    pipeline_state.mutate(root, '编剧', 'clips', 'set', clips[0]['id'], None,
                                          [('duration', 9)])
                except BaseException as e:
                    errors.append(e)
            thread = threading.Thread(target=change_other)
            thread.start()
            pipeline_state.attach_narration(root, clip['id'], item['id'], expected, audio, 2)
            thread.join(timeout=5)
            self.assertFalse(errors)
            after = runtime.read_json(root / 'clips.json')
            self.assertEqual(after[0]['duration'], 9)
            self.assertEqual(after[-1]['narration'][0]['audio'], 'new.wav')
            self.assertTrue((root / 'logs/state-changes.jsonl').exists())

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / 'existing.wav'
            audio.write_bytes(b'original')
            with self.assertRaises(SystemExit):
                runtime.check_output(root, audio)
            self.assertEqual(audio.read_bytes(), b'original')

    def test_two_attempts_cannot_win_same_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, out = root / 'one.part', root / 'two.part', root / 'out.wav'
            first.write_bytes(b'one')
            second.write_bytes(b'two')
            runtime.check_output(root, out)
            runtime.check_output(root, out)
            runtime.publish_output(first, out)
            with self.assertRaises(FileExistsError):
                runtime.publish_output(second, out)
            self.assertEqual(out.read_bytes(), b'one')

    def test_prepared_request_text_must_match_fingerprint_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_sample_project.build(Path(tmp) / '漫剧' / '作品' / 'A')
            clip = runtime.read_json(root / 'clips.json')[-1]
            item = clip['narration'][0]
            fingerprint = pipeline_state.narration_fingerprint(root, clip['id'], item['id'])
            with self.assertRaises(pipeline_state.Refused):
                pipeline_state.check_narration(root, clip['id'], item['id'], fingerprint,
                                               expected_text='manually changed request text')

    def test_changed_text_cannot_receive_old_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_sample_project.build(Path(tmp) / '漫剧' / '作品' / 'A')
            clips = runtime.read_json(root / 'clips.json')
            clip = clips[-1]
            item = clip['narration'][0]
            expected = pipeline_state.narration_fingerprint(root, clip['id'], item['id'])
            item['text'] += '新内容'
            runtime.atomic_json(root / 'clips.json', clips)
            before = (root / 'clips.json').read_bytes()
            with self.assertRaises(pipeline_state.Refused):
                pipeline_state.attach_narration(root, clip['id'], item['id'], expected, root / 'new.wav', 2)
            self.assertEqual(before, (root / 'clips.json').read_bytes())

    def test_prepare_never_calls_http_or_credentials_and_resolves_local_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_sample_project.build(Path(tmp) / '漫剧' / '作品' / 'A')
            clips = runtime.read_json(root / 'clips.json')
            for c in clips:
                for n in c.get('narration', []):
                    n.pop('audio', None)
            runtime.atomic_json(root / 'clips.json', clips)
            catalog = root / 'approvals' / 'tts-catalog.json'
            runtime.atomic_json(catalog, {'voices': [{'name': '承泽', 'voice_id': 'mock-id'}]})
            a = SimpleNamespace(project=root, episode='EP001', redo=False)
            with patch.object(narrate, 'http', side_effect=AssertionError('network called')), \
                 patch.object(narrate, 'api_key', side_effect=AssertionError('credential read')):
                narrate.cmd_prepare(a)
            bundle = runtime.read_json(root / '单集制作/EP001/旁白/requests.json')
            self.assertEqual(bundle['requests'][0]['params']['voice']['voice_id'], 'mock-id')
            runtime.atomic_json(catalog, {'voices': [{'name': '承泽', 'voice_id': 'one'},
                                                   {'name': '承泽', 'voice_id': 'two'}]})
            with self.assertRaises(SystemExit):
                narrate.cmd_prepare(a)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        worker()
    else:
        unittest.main()
