"""Offline fixtures only. No model process, credentials, or provider requests."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_workflow as wf
import codex_cli as cli
import codex_jobs as jobs
import codex_materials as materials
import proposal_sink as data
from generation_runtime import atomic_json

TOOLS = Path(__file__).resolve().parent


class Workflow(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            'H3_BASE_URL': 'https://cp.compshare.cn',
            'NEXORAI_BASE_URL': 'https://api.nexorai.com.cn/v1',
            'HF_HUB_OFFLINE': '1', 'MODELSCOPE_OFFLINE': '1'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_root = Path(self.temp.name)
        self.work = self.make_work('A')

    def make_work(self, name, simulation=True, scenes=None):
        root = self.data_root / '作品' / name
        (root / 'approvals').mkdir(parents=True)
        (root / 'project.json').write_text('{"title":"fixture"}', encoding='utf-8')
        (root / 'input.txt').write_text('只供离线测试的中文输入', encoding='utf-8')
        (root / 'approvals/user-authorization.json').write_text('{"source":"SYNTHETIC_TEST_ONLY"}', encoding='utf-8')
        caps = dict(total_points=600, h3_requests=9, tts_requests=5, tts_characters=300,
                    images=6, video_revisions=2, revisions_per_clip=1)
        atomic_json(root / 'approvals/execution-limits.json',
                    dict(version=1, caps=caps, retry_unknown=False, allowed_resolutions=['480P', '768P']))
        atomic_json(root / 'approvals/runtime-tools.json',
                    {'version': 1, 'tools': {'python': {'path': sys.executable, 'sha256': wf.digest(sys.executable)}}})
        work = wf.Work.initialize(root, 'work-' + name, scenes or list(data.SCENES), 24000, 300, simulation)
        for role in wf.ROLES:
            work.bind(role, dict(source='CODEX_APP_CREATE_THREAD', threadId='thread-' + name + str(wf.ROLES.index(role)),
                                 hostId='fixture-host', status='ready', simulation=simulation))
        return work

    def task(self, work=None, role='编剧', kind='script', scene='Clip00001', deps=(), action=None, inputs=None):
        work = work or self.work
        return work.new_task(role, kind, scene, inputs or ['input.txt'], deps, action=action)

    def raw(self, task):
        body = {'durationMs': 8000, 'narration': '旁白', 'action': '推门进入'}
        if task['kind'] == 'work-report':
            body = {'summary': '只读材料核对完成', 'artifacts': [], 'needsUser': False}
        return json.dumps(dict(schemaVersion=1, kind=task['kind'], sceneId=task['sceneId'], data=body), ensure_ascii=False)

    def observation(self, task, raw, turn='turn-1'):
        return dict(source='CODEX_APP_FINAL_RESPONSE', threadId=task['threadId'], hostId=task['hostId'],
                    taskId=task['taskId'], inputsSha256=data.sha(data.canonical(task['inputs'])),
                    turnId=turn, status='completed', finalSha256=data.sha(raw.encode()),
                    simulation=task['simulation'])

    def complete(self, task, work=None, turn='turn-1'):
        work = work or self.work
        work.start(task['taskId'])
        raw = self.raw(task)
        return work.complete(task['taskId'], raw, self.observation(task, raw, turn))

    def test_work_registration_idempotent_and_authorization_preserved(self):
        before = (self.work.root / 'approvals/execution-limits.json').read_bytes()
        wf.Work.initialize(self.work.root, 'work-A', list(data.SCENES), 24000, 300, True)
        self.assertEqual(before, (self.work.root / 'approvals/execution-limits.json').read_bytes())
        with self.assertRaises(ValueError):
            wf.Work.initialize(self.work.root, 'different', list(data.SCENES), 24000, 300, True)
        with self.assertRaises(ValueError):
            wf.Work.initialize(self.work.root, 'work-A', list(data.SCENES), 24000, 301, True)

    def test_scope_escape_and_duplicate_role_thread_refused(self):
        for rel in ('../outside', r'C:\outside', r'\\server\share', '/outside', '协作/../../outside'):
            with self.subTest(rel=rel), self.assertRaises(ValueError):
                wf.inside(self.work.root, rel)
        binding = self.work.bindings()['roles']['编剧']
        with self.assertRaises(ValueError):
            self.work.bind('剪辑', binding)
        with self.assertRaises(ValueError):
            self.task(role='剪辑', kind='script')

    def test_input_change_stops_start_and_receipt_reuse(self):
        task = self.task()
        self.complete(task)
        (self.work.root / 'input.txt').write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.work.result(task['taskId'], require_real=False)
        with self.assertRaises(ValueError):
            self.work.start(task['taskId'])

    def test_completed_reply_identity_and_ack_are_not_success(self):
        task = self.task()
        self.work.start(task['taskId'])
        raw = self.raw(task)
        original = self.observation(task, raw)
        for field, value in (('threadId', 'another'), ('hostId', 'another'), ('status', 'accepted'),
                             ('taskId', 'another'), ('inputsSha256', '0' * 64),
                             ('finalSha256', '0' * 64), ('simulation', False)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.work.complete(task['taskId'], raw, {**original, field: value})
        self.assertFalse(self.work.receipt_path(task['taskId']).exists())

    def test_receipt_idempotence_conflict_and_corrupt_state_fail_closed(self):
        task = self.task()
        first = self.complete(task)
        path = self.work.receipt_path(task['taskId'])
        before = path.read_bytes()
        self.assertEqual(self.complete(task), first)
        self.assertEqual(path.read_bytes(), before)
        raw = self.raw(task).replace('推门进入', '另一动作')
        with self.assertRaises(ValueError):
            self.work.complete(task['taskId'], raw, self.observation(task, raw))
        path.write_text('{"broken":', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.work.result(task['taskId'], require_real=False)
        self.assertEqual(path.read_text(encoding='utf-8'), '{"broken":')

    def test_simulation_and_reused_turn_cannot_be_promoted(self):
        first = self.task()
        self.complete(first)
        with self.assertRaises(ValueError):
            self.work.result(first['taskId'])
        second = self.task(scene='Clip00002')
        self.work.start(second['taskId'])
        raw = self.raw(second)
        with self.assertRaises(ValueError):
            self.work.complete(second['taskId'], raw, self.observation(second, raw))

    def test_two_work_tasks_cannot_be_crossed(self):
        other = self.make_work('B')
        task = self.task()
        self.complete(task)
        with self.assertRaises(ValueError):
            other.result(task['taskId'], require_real=False)
        result = wf.refresh_indexes(self.data_root)
        self.assertEqual(result, {'works': 2, 'roleThreads': 10})
        self.assertTrue(wf.verified(self.data_root / '调度/作品索引.json')['derived'])

    def test_recovery_stops_reexecution_but_accepts_observed_original_completion(self):
        task = self.task()
        self.work.start(task['taskId'])
        self.assertEqual(self.work.recover(), [task['taskId']])
        self.assertEqual(self.work.recover(), [])
        with self.assertRaises(ValueError):
            self.work.start(task['taskId'])
        raw = self.raw(task)
        self.work.complete(task['taskId'], raw, self.observation(task, raw))
        self.assertEqual(self.work.task(task['taskId'])['state'], 'completed')

    def test_thread_rotation_preserves_history_and_rejects_unresolved_task(self):
        task = self.task()
        self.work.start(task['taskId'])
        new = dict(source='CODEX_APP_CREATE_THREAD', threadId='replacement', hostId='fixture-host',
                   status='ready', simulation=True)
        with self.assertRaises(ValueError):
            self.work.bind('编剧', new)
        raw = self.raw(task)
        self.work.complete(task['taskId'], raw, self.observation(task, raw))
        self.work.bind('编剧', new)
        self.assertEqual(len(self.work.bindings()['history']), 1)
        self.work.result(task['taskId'], require_real=False)

    def test_dependencies_need_completed_current_inputs(self):
        first = self.task()
        second = self.task(role='美术', kind='work-report', deps=[first['taskId']])
        with self.assertRaises(ValueError):
            self.work.start(second['taskId'])
        self.complete(first)
        self.assertTrue(self.work.start(second['taskId'])[1])

    def test_parallel_process_start_claims_task_once(self):
        task = self.task()
        command = [sys.executable, '-I', str(TOOLS / 'codex_workflow.py'), '--project', str(self.work.root),
                   'start', '--task-id', task['taskId']]
        env = cli.legacy.clean_environment(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        processes = [subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stderr.decode('utf-8', 'replace'))
            results.append(json.loads(stdout)['newStart'])
        self.assertEqual(sorted(results), [False, True])

    def test_stable_task_id_retry_is_idempotent_without_rewriting_state(self):
        task = self.work.new_task('编剧', 'script', 'Clip00001', ['input.txt'], task_id='stable-task')
        self.work.start(task['taskId'])
        before = self.work.task_path(task['taskId']).read_bytes()
        again = self.work.new_task('编剧', 'script', 'Clip00001', ['input.txt'], task_id='stable-task')
        self.assertEqual(again['state'], 'running')
        self.assertEqual(before, self.work.task_path(task['taskId']).read_bytes())
        with self.assertRaises(ValueError):
            self.work.new_task('编剧', 'script', 'Clip00002', ['input.txt'], task_id='stable-task')

    def test_initial_task_prepared_before_model_call_needs_observed_thread_attachment(self):
        work = self.make_work('B')
        mapping = work.bindings()
        binding = mapping['roles'].pop('编剧')
        atomic_json(wf.inside(work.root, '协作/会话.json'), wf.sealed(mapping))
        task = work.new_task('编剧', 'work-report', 'Clip00001', ['input.txt'],
                             task_id='initial-task', prepare=True)
        self.assertIsNone(task['threadId'])
        self.assertEqual(task['state'], 'awaiting_thread')
        with self.assertRaises(ValueError):
            work.start(task['taskId'])
        with self.assertRaises(ValueError):
            work.attach(task['taskId'])
        work.bind('编剧', binding)
        work.attach(task['taskId'])
        self.assertTrue(work.start(task['taskId'])[1])
        same = work.new_task('编剧', 'work-report', 'Clip00001', ['input.txt'],
                             task_id='initial-task', prepare=True)
        self.assertEqual(same['state'], 'running')
        self.assertEqual(same['threadId'], binding['threadId'])

    def test_saved_local_completion_can_reconcile_after_state_write_interruption(self):
        task = self.task(role='总导演', kind='work-report', action='status')
        runner = jobs.Runner(self.work.root, task['taskId'], task['threadId'])
        record = runner.execute()
        current = self.work.task(task['taskId'])
        current['state'] = 'unknown'
        self.work.save_task(current)
        with patch.object(runner, 'run', side_effect=AssertionError('must not rerun')):
            self.assertEqual(self.work.reconcile(task['taskId']), record)
            self.assertEqual(runner.execute(), record)
        path = wf.inside(self.work.root, record['artifacts'][0]['path'])
        path.write_text('damaged', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.work.reconcile(task['taskId'])

    def test_generic_scene_ids_and_rollup_keep_target_limits(self):
        work = self.make_work('B', scenes=['SceneA', 'SceneB'])
        values = []
        for i, scene in enumerate(work.spec['scenes']):
            task = self.task(work=work, scene=scene)
            work.start(task['taskId'])
            value = data.decode(self.raw(task))
            value['data']['durationMs'] = 12000
            raw = json.dumps(value, ensure_ascii=False)
            record = work.complete(task['taskId'], raw, self.observation(task, raw, 'turn-' + str(i)))
            values.append(record['proposal'])
        data.validate_script_rollup(values, scenes=tuple(work.spec['scenes']), duration_ms=24000, narration_limit=300)
        with self.assertRaises(ValueError):
            data.validate_script_rollup(values, scenes=tuple(work.spec['scenes']), duration_ms=24001)

    def test_worker_report_cannot_claim_approval_or_stale_artifact(self):
        task = self.task(kind='work-report')
        self.work.start(task['taskId'])
        value = data.decode(self.raw(task))
        value['data']['artifacts'] = [{'path': 'approvals/user-authorization.json',
                                       'sha256': wf.digest(self.work.root / 'approvals/user-authorization.json')}]
        raw = json.dumps(value)
        with self.assertRaises(ValueError):
            self.work.complete(task['taskId'], raw, self.observation(task, raw))
        rel = '协作/提案/编剧/result.txt'
        path = wf.inside(self.work.root, rel)
        path.parent.mkdir(parents=True)
        path.write_text('fixture', encoding='utf-8')
        value['data']['artifacts'] = [{'path': rel, 'sha256': wf.digest(path)}]
        raw = json.dumps(value)
        self.work.complete(task['taskId'], raw, self.observation(task, raw))
        path.write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.work.result(task['taskId'], require_real=False)

    def test_duplicate_json_keys_are_not_accepted(self):
        task = self.task()
        self.work.start(task['taskId'])
        raw = self.raw(task).replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1')
        with self.assertRaises(ValueError):
            self.work.complete(task['taskId'], raw, self.observation(task, raw))

    def test_media_denied_before_any_credential_or_network_access(self):
        with patch.object(cli.legacy, 'user_key', side_effect=AssertionError('credentials accessed')), \
             patch.object(socket.socket, 'connect', side_effect=AssertionError('network accessed')):
            for tool, args in (('h3', ['submit', '--execute']), ('image', ['submit', '--execute']),
                               ('tts', ['submit', '--execute']), ('h3', ['query'])):
                with self.subTest(tool=tool), self.assertRaises(ValueError):
                    cli.execute(self.work.root, tool, args)

    def test_prepared_warning_blocks_submit_before_credentials(self):
        request = self.work.root / 'request.json'
        atomic_json(request, {'warnings': ['unverified actual tail frame']})
        with patch.object(cli, 'validate_live'), \
             patch.object(cli.legacy, 'user_key', side_effect=AssertionError('credentials accessed')):
            with self.assertRaisesRegex(ValueError, 'unresolved warnings'):
                cli.execute(self.work.root, 'h3', ['submit', '--execute', '--request', str(request)])

    def image_exception_fixture(self):
        work = self.make_work('image-exception', simulation=False)
        request = work.root/'requests/C00001-A-image.json'
        atomic_json(request, dict(provider='nexorai', project=str(work.root), asset='C00001-A', kind='image',
                                  target='角色/C00001-A.png', ratio='9:16', estimate_images=1, warnings=[],
                                  params=dict(model='grok-imagine-image-quality', n=1, aspect_ratio='9:16')))
        old_relative = 'tasks/image-C00001-A-image/attempt-001'
        state = work.root/old_relative/'state.json'
        atomic_json(state, dict(provider='nexorai', status='unknown', request_sha256=wf.digest(request)))
        approval = dict(status='USER_APPROVED_ONE_IMAGE_UNKNOWN_EXCEPTION', approved=True,
                        workId=work.spec['workId'], userQuote='批准，仅本次精确例外，原6张总上限不变',
                        oldAttempt=old_relative, newAttempt='tasks/image-C00001-A-image/attempt-002',
                        requestPath='requests/C00001-A-image.json', requestSha256=wf.digest(request),
                        oldStateSha256=wf.digest(state))
        approval_path = work.root/'approvals/user-authorization-amendment-20261107-image-attempt002.json'
        atomic_json(approval_path, approval)
        atomic_json(work.root/'approvals/codex-live-ready.json',
                    {'oneImageExceptionApprovalSha256': wf.digest(approval_path)})
        args = ['submit','--request',str(request),'--task-dir',str(work.root/approval['newAttempt']),
                '--execute','--budget',str(work.root/'approvals/budget.json'),'--batch','EP001-参考图',
                '--after-unknown','attempt-001']
        return work, args, state, approval_path

    def test_image_unknown_exception_is_exact_and_does_not_mutate_provider_arguments(self):
        work, args, state, approval_path = self.image_exception_fixture()
        original = list(args)
        with patch.object(cli, 'validate_live'), patch('canvas_review.ReviewStore.require_generation') as canvas_guard:
            self.assertTrue(cli.validate('image', args, work))
            canvas_guard.assert_called_once_with('image', work.root/'requests/C00001-A-image.json', 'EP001-参考图',
                                                 work.root/'tasks/image-C00001-A-image/attempt-002')
        self.assertEqual(args, original)
        self.assertEqual(wf.read(state)['status'], 'unknown')
        self.assertNotIn('--after-unknown', cli.image_exception_arguments('image', args, work))
        for flag, value in [('--task-dir', str(state.parent.parent/'attempt-003')),
                            ('--after-unknown','attempt-000'), ('--batch','OTHER')]:
            changed=list(args);changed[changed.index(flag)+1]=value
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                cli.image_exception_arguments('image', changed, work)
        with self.assertRaises(ValueError):
            cli.image_exception_arguments('h3', args, work)

    def test_image_unknown_exception_missing_approval_or_changed_source_stays_locked(self):
        work, args, state, approval_path = self.image_exception_fixture()
        approval=wf.read(approval_path);approval['approved']=False;atomic_json(approval_path,approval)
        with patch.object(cli.legacy,'user_key',side_effect=AssertionError('credential accessed')):
            with self.assertRaises(ValueError):cli.execute(work.root,'image',args)
        approval['approved']=True;atomic_json(approval_path,approval)
        state.write_text('{"provider":"nexorai","status":"unknown","changed":true}',encoding='utf-8')
        with self.assertRaises(ValueError):cli.image_exception_arguments('image',args,work)

    def test_old_spending_and_unknown_exception_do_not_bypass_canvas_review(self):
        work, args, _, _ = self.image_exception_fixture()
        with patch.object(cli, 'validate_live'), \
             patch.object(cli.legacy, 'user_key', side_effect=AssertionError('credentials accessed')), \
             patch.object(socket.socket, 'connect', side_effect=AssertionError('network accessed')):
            with self.assertRaisesRegex(ValueError, '画布'):
                cli.execute(work.root, 'image', args)

    def test_empty_or_wrong_ready_file_does_not_unlock_real_work(self):
        work = self.make_work('B', simulation=False)
        ready = work.root / 'approvals/codex-live-ready.json'
        for value in ({}, {'version': 1, 'workId': 'other', 'root': str(work.root)}):
            atomic_json(ready, value)
            with self.assertRaises(ValueError):
                cli.validate_live(work, 'h3', ['submit', '--execute'])

    def test_ready_record_requires_exact_user_scope_and_current_code_pins(self):
        work = self.make_work('B', simulation=False)
        approval = work.root / 'approvals/paid-fixture.json'
        atomic_json(approval, dict(status='USER_APPROVED_CODEX_MEDIA_EXECUTION', workId=work.spec['workId'],
                                  approved=True, userQuote='SYNTHETIC_TEST_ONLY',
                                  allowedOperations=['h3:submit']))
        files = dict(budget=work.root / 'approvals/budget.json',
                     limits=work.root / 'approvals/execution-limits.json',
                     runtime=work.root / 'approvals/runtime-tools.json',
                     entry=Path(cli.__file__), workflow=Path(wf.__file__),
                     launcher=Path(cli.legacy.__file__),
                     generation_runtime=TOOLS / 'generation_runtime.py',
                     execution_limits=TOOLS / 'execution_limits.py',
                     proposal_sink=TOOLS / 'proposal_sink.py', codex_jobs=TOOLS / 'codex_jobs.py',
                     recipes=TOOLS / 'acceptance_jobs.py', app_receipts=TOOLS / 'codex_app_receipts.py',
                     canvas_review=TOOLS / 'canvas_review.py', voice_readiness=TOOLS / 'voice_readiness.py',
                     voice_lifecycle=TOOLS / 'voice_lifecycle.py',
                     shot_context=TOOLS / 'shot_context.py',
                     h3_execution_checks=TOOLS / 'h3_execution_checks.py',
                     **{'target_' + name: path for name, path in cli.legacy.TARGETS.items()})
        budget_rows = [dict(batch='fixture', kind='video', unit='points', limit=20, used=0,
                            approval='SYNTHETIC_TEST_ONLY')]
        atomic_json(files['budget'], budget_rows)
        ready = dict(version=1, workId=work.spec['workId'], root=str(work.root),
                     approvalFile='approvals/paid-fixture.json', approvalSha256=wf.digest(approval),
                     checks=dict(offlineIsolation=True, mediaLimits=True, receiptAssociation=True,
                                 unknownNoRepost=True, runtimePins=True),
                     pins={name: cli.budget_policy_digest(path) if name == 'budget' else wf.digest(path)
                           for name, path in files.items()})
        atomic_json(work.root / 'approvals/codex-live-ready.json', ready)
        cli.validate_live(work, 'h3', ['submit', '--execute'])  # Pure validation, never executes a provider call.
        # A real reservation changes usage and receipts, without changing spending authority.
        budget_rows[0].update(used=3, _operations={'fixture-charge': 3})
        atomic_json(files['budget'], budget_rows)
        cli.validate_live(work, 'h3', ['submit', '--execute'])
        budget_rows[0]['used'] = 0
        atomic_json(files['budget'], budget_rows)
        with self.assertRaises(ValueError):
            cli.validate_live(work, 'h3', ['submit', '--execute'])
        budget_rows[0]['used'] = 3
        budget_rows[0]['limit'] = 21
        atomic_json(files['budget'], budget_rows)
        with self.assertRaises(ValueError):
            cli.validate_live(work, 'h3', ['submit', '--execute'])
        with self.assertRaises(ValueError):
            cli.validate_live(work, 'image', ['submit', '--execute'])
        atomic_json(files['budget'], {'changed': True})
        with self.assertRaises(ValueError):
            cli.validate_live(work, 'h3', ['submit', '--execute'])

    def test_startup_materials_create_no_chats_and_preserve_edited_materials(self):
        paths = materials.export(self.work.root)
        self.assertEqual(len(paths), 5)
        self.assertEqual(materials.export(self.work.root), paths)
        path = Path(paths[0])
        content = path.read_text(encoding='utf-8')
        self.assertIn('不读取或输出任何密钥', content)
        path.write_text('user edit', encoding='utf-8')
        with self.assertRaises(ValueError):
            materials.export(self.work.root)
        self.assertEqual(path.read_text(encoding='utf-8'), 'user edit')

    def test_new_work_materials_do_not_reset_current_dispatcher_handoff(self):
        materials.export(self.work.root)
        current = self.data_root / '调度/当前交接.md'
        current.write_text('Current real dispatcher state', encoding='utf-8')
        other = self.make_work('B')
        self.assertEqual(len(materials.export(other.root)), 5)
        self.assertEqual(current.read_text(encoding='utf-8'), 'Current real dispatcher state')

    def test_local_entry_runs_real_tool_without_credentials_or_network(self):
        class Output(io.StringIO):
            def reconfigure(self, **kwargs):
                pass
        output = Output()
        with patch.object(cli.legacy, 'user_key', side_effect=AssertionError('credentials accessed')), \
             contextlib.redirect_stdout(output), \
             patch.dict(os.environ, {'NEXORAI_API_KEY': 'FAKE-ONLY', 'MINIMAX_H3_API_KEY': 'FAKE-ONLY'}):
            cli.execute(self.work.root, 'state', ['--project', str(self.work.root), '--role', '总导演', 'show', 'assets'])
        self.assertEqual(data.decode(output.getvalue()), [])

    def test_offline_network_block_restores_transport_after_exception(self):
        original = socket.socket.connect
        with self.assertRaises(ValueError):
            with cli.offline_network():
                with socket.socket() as connection:
                    connection.connect(('127.0.0.1', 9))
        self.assertIs(socket.socket.connect, original)

    def test_existing_unknown_generation_is_queried_without_submit(self):
        task = self.task(role='摄影', kind='work-report', action='video-final-1')
        runner = jobs.Runner(self.work.root, task['taskId'], task['threadId'])
        folder = self.work.root / 'tasks/video/attempt-001'
        folder.mkdir(parents=True)
        (folder / 'state.json').write_text('{"status":"unknown"}', encoding='utf-8')
        with patch.object(runner, 'cli', return_value={'status': 'UNKNOWN_SUBMISSION'}) as call:
            with self.assertRaises(ValueError):
                runner.generate('h3', folder, ['submit', '--execute'])
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[0], 'wait')

    def test_wait_child_stays_on_codex_entry_and_invalid_metadata_has_no_legacy_fallback(self):
        import wait_generation
        self.assertEqual(wait_generation.execution_entry(self.work.root).name, 'codex_cli.py')
        with patch.object(wait_generation.subprocess, 'run') as run:
            run.return_value.returncode = 0
            wait_generation.query_once(self.work.root, 'h3', self.work.root/'tasks/a/attempt-001')
        self.assertIn('codex_cli.py', ' '.join(run.call_args.args[0]))
        self.assertNotIn('authorized_cli.py', ' '.join(run.call_args.args[0]))
        (self.work.root/'协作/工作区.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            wait_generation.execution_entry(self.work.root)

    def test_actual_job_process_logs_stay_local_and_job_does_not_reexecute(self):
        task = self.task(role='总导演', kind='work-report', action='status')
        runner = jobs.Runner(self.work.root, task['taskId'], task['threadId'])
        record = runner.execute()
        self.assertEqual(record['state'], 'LOCAL_TOOL_COMPLETED')
        public = jobs.summary(record)
        self.assertNotIn('result', public)
        self.assertNotIn('stdout', public)
        self.assertEqual(runner.execute(), record)
        path = wf.inside(self.work.root, record['artifacts'][0]['path'])
        self.assertTrue(path.is_file())

    def test_unresolved_job_never_runs_again_after_failure(self):
        task = self.task(role='总导演', kind='work-report', action='status')
        runner = jobs.Runner(self.work.root, task['taskId'], task['threadId'])
        with patch.object(runner, 'run', side_effect=RuntimeError('fixture interruption')):
            with self.assertRaises(RuntimeError):
                runner.execute()
        self.assertEqual(self.work.task(task['taskId'])['state'], 'unknown')
        with patch.object(runner, 'run') as run, self.assertRaises(ValueError):
            runner.execute()
        run.assert_not_called()

    def test_job_receipt_does_not_unlock_fake_proposal_dependencies(self):
        task = self.task()
        self.complete(task)
        job = self.task(role='编剧', kind='work-report', action='plan-register', deps=[task['taskId']])
        runner = jobs.Runner(self.work.root, job['taskId'], job['threadId'])
        with self.assertRaises(ValueError):
            runner.latest('编剧', 'script', 'Clip00001')

    def test_smoke_cannot_use_the_eight_second_formal_prompt(self):
        camera = self.task(role='摄影', kind='work-report')
        job = self.task(role='摄影', kind='work-report', action='video-smoke', deps=[camera['taskId']])
        runner = jobs.Runner(self.work.root, job['taskId'], job['threadId'])
        with patch.object(runner.work, 'result', return_value={'proposal': {'data': {'artifacts': []}}}):
            with self.assertRaisesRegex(ValueError, 'four-second prompt'):
                runner.video_prompt('smoke', {'prompt': 'eight-second.txt'})
        self.assertEqual(runner.video_prompt('draft', {'prompt': 'eight-second.txt'}),
                         self.work.root / 'eight-second.txt')

    def test_final_voice_binding_is_a_copy_and_only_for_scripted_speech(self):
        job = self.task(role='摄影', kind='work-report', action='video-final-1')
        runner = jobs.Runner(self.work.root, job['taskId'], job['threadId'])
        source = self.work.root / 'formal.prompt.txt'
        source.write_text('subject_definitions:\nOriginal actor.\n\nsummary:\nOriginal scene.\n', encoding='utf-8')
        original = source.read_bytes()
        row = {'prompt': source.name, 'shots': [{'dialogue': 'Original scripted words'}]}
        actual = runner.video_prompt('final', row)
        self.assertNotEqual(actual, source)
        self.assertIn('[音色参考]', actual.read_text('utf-8'))
        self.assertIn('的音色遵循', actual.read_text('utf-8'))
        self.assertNotIn('The voice timbre', actual.read_text('utf-8'))
        self.assertEqual(source.read_bytes(), original)
        row['shots'][0]['dialogue'] = ''
        self.assertEqual(runner.video_prompt('final', row), source)

    def test_pilot_registration_cannot_silently_ignore_changed_writer_proposal(self):
        task = self.task(role='编剧', kind='work-report', action='plan-register')
        runner = jobs.Runner(self.work.root, task['taskId'], task['threadId'])
        path = self.work.root / '准备输入/分镜提案.json'
        path.parent.mkdir()
        rows = [{'id': 'EP001/' + scene, 'duration': 8, 'prompt': scene + '.txt',
                 'narration': [], 'shots': [{'visual': 'prepared action'}]} for scene in data.SCENES]
        atomic_json(path, rows)
        proposals = [dict(schemaVersion=1, kind='script', sceneId=scene,
                          data=dict(durationMs=8000, narration='', action='changed action')) for scene in data.SCENES]
        with patch.object(runner, 'latest', side_effect=[{'proposal': p} for p in proposals]), \
             patch.object(runner, 'cli') as call, self.assertRaises(ValueError):
            runner.plan()
        call.assert_not_called()

    def test_explicit_versioned_plans_are_used_without_overwriting_originals(self):
        plans = {'assets': '协作/提案/编剧/assets-v2.json', 'clips': '协作/提案/编剧/clips-v2.json'}
        rows = []
        for scene in data.SCENES:
            prompt = '协作/提案/编剧/' + scene + '.prompt.txt'
            path = wf.inside(self.work.root, prompt)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('new reviewed prompt', encoding='utf-8')
            rows.append(dict(id='EP001/' + scene, duration=8, prompt=prompt,
                             narration=[], shots=[{'visual': 'new action'}]))
        atomic_json(wf.inside(self.work.root, plans['assets']), [])
        atomic_json(wf.inside(self.work.root, plans['clips']), rows)
        original = self.work.root / '准备输入/分镜提案.json'
        original.parent.mkdir()
        original.write_text('original preserved', encoding='utf-8')
        inputs = ['input.txt', *plans.values(), *(row['prompt'] for row in rows)]
        task = self.work.new_task('编剧', 'work-report', 'Clip00001', inputs,
                                  action='plan-register', plans=plans)
        runner = jobs.Runner(self.work.root, task['taskId'], task['threadId'])
        proposals = [dict(schemaVersion=1, kind='script', sceneId=scene,
                          data=dict(durationMs=8000, narration='', action='new action')) for scene in data.SCENES]
        with patch.object(runner, 'latest', side_effect=[{'proposal': p} for p in proposals]), \
             patch.object(runner, 'state') as state, patch.object(runner, 'cli', return_value={'valid': True}):
            runner.plan()
        self.assertEqual(state.call_count, 3)
        for row, call in zip(rows, state.call_args_list):
            registered = call.kwargs['data']
            self.assertNotEqual(registered['prompt'], row['prompt'])
            runtime = wf.inside(self.work.root, registered['prompt'])
            source = wf.inside(self.work.root, row['prompt'])
            self.assertEqual(runtime.read_bytes(), source.read_bytes())
            runtime.write_text('real tail continuity revision', encoding='utf-8')
            self.assertEqual(source.read_text('utf-8'), 'new reviewed prompt')
        self.assertEqual(original.read_text(encoding='utf-8'), 'original preserved')
        with self.assertRaises(ValueError):
            self.work.new_task('编剧', 'work-report', 'Clip00001', ['input.txt'],
                               action='plan-register', plans=plans)

    def test_one_actual_writer_turn_can_supply_three_script_artifacts_without_fake_turns(self):
        writer = self.task(kind='work-report')
        paths = []
        for scene in data.SCENES:
            rel = '协作/提案/编剧/' + scene + '.script.json'
            path = wf.inside(self.work.root, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, dict(schemaVersion=1, kind='script', sceneId=scene,
                                  data=dict(durationMs=8000, narration='', action='action')))
            paths.append({'path': rel, 'sha256': wf.digest(path)})
        clips_rel = '协作/提案/编剧/clips.json'
        atomic_json(wf.inside(self.work.root, clips_rel), [])
        paths.append({'path': clips_rel, 'sha256': wf.digest(wf.inside(self.work.root, clips_rel))})
        job = self.task(role='编剧', kind='work-report', action='plan-register', deps=[writer['taskId']])
        runner = jobs.Runner(self.work.root, job['taskId'], job['threadId'])
        with patch.object(runner, 'plan_files', return_value={'clips': wf.inside(self.work.root, clips_rel)}), \
             patch.object(runner.work, 'result', return_value={'proposal': {'data': {'artifacts': paths}}}) as source:
            scripts = runner.script_proposals()
        self.assertEqual(len(scripts), 3)
        source.assert_called_once_with(writer['taskId'], require_real=True)
        data.validate_script_rollup(scripts)


if __name__ == '__main__':
    unittest.main(verbosity=2)
