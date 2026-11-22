import copy
import json
from pathlib import Path
import tempfile
import unittest

import cli_proposal_sink as cli
import proposal_sink as sink
import acceptance_jobs as jobs


class NativeProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bindings = {'scopeId': 'scope-cli', 'root': str(self.root),
                         'cliDevice': {'id': 'device-new', 'bindingId': 'core-new:device-new'},
                         'cliJobs': [{'id': 'job-1', 'botId': 'bot-1', 'taskId': 'origin-task', 'root': str(self.root)}],
                         'members': [{'id': f'bot-{i}', 'allowedKinds': ['script'] if i == 1 else []}
                                     for i in range(5)]}
        self.value = {'schemaVersion': 1, 'kind': 'script', 'sceneId': 'Clip00001',
                      'data': {'durationMs': 8000, 'narration': '这一夜，他终于回家。', 'action': '推门。'}}
        self.final = {'source': 'CLI_NATIVE_RESULT', 'reportId': '', 'originTaskId': 'origin-task',
                      'botId': 'bot-1', 'bindingId': 'core-new:device-new', 'sessionId': 'start-call',
                      'nativeSessionId': 'native-thread', 'nativeTurnId': 'native-turn-1',
                      'status': 'completed', 'completionEvidence': 'native-turn-completed',
                      'content': '', 'contentSha256': '', 'contentBytes': 0,
                      'tokenUsage': None, 'usageSemantics': 'CLI_REPORTED_TOKENS_NOT_PHYSICAL_REQUESTS',
                      'simulation': True}
        self.status = {'scopeId': 'scope-cli', 'members': [{'id': f'bot-{i}'} for i in range(5)],
                       'tasks': [{'id': 'origin-task', 'bot_id': 'bot-1', 'status': 'succeeded'},
                                 {'id': 'observer-task', 'bot_id': 'bot-1', 'status': 'succeeded'}],
                       'reports': [],
                       'operations': [{'id': 'start-call', 'task_id': 'origin-task', 'bot_id': 'bot-1',
                                       'tool_name': 'device.agent', 'status': 'succeeded'},
                                      {'id': 'observer-call', 'task_id': 'observer-task', 'bot_id': 'bot-1',
                                       'tool_name': 'device.agent', 'status': 'succeeded'}]}
        self.call = {'id': 'observer-call', 'task_id': 'observer-task', 'bot_id': 'bot-1',
                     'tool_name': 'device.agent', 'status': 'succeeded',
                     'arguments_json': json.dumps({'deviceId': 'device-new', 'operation': 'status',
                                                    'parameters': {'sessionId': 'start-call'}})}
        self.origin_call = {'id': 'start-call', 'task_id': 'origin-task', 'bot_id': 'bot-1',
                            'tool_name': 'device.agent', 'status': 'succeeded',
                            'arguments_json': json.dumps({'deviceId': 'device-new', 'operation': 'start',
                                'parameters': {'agent': 'codex', 'jobId': 'job-1'}}),
                            'result_json': json.dumps({'sessionId': 'start-call', 'cwd': str(self.root),
                                                       'status': 'starting', 'executionMode': 'restricted-inference'})}
        self.refresh()

    def refresh(self, raw=None):
        self.final['content'] = raw if raw is not None else json.dumps(self.value, ensure_ascii=False)
        encoded = self.final['content'].encode('utf-8')
        self.final['contentSha256'] = sink.sha(encoded)
        self.final['contentBytes'] = len(encoded)
        identity = [self.final[k] for k in ('bindingId', 'botId', 'originTaskId', 'sessionId',
                                           'nativeSessionId', 'nativeTurnId', 'contentSha256')]
        self.final['reportId'] = sink.sha(sink.canonical(identity))
        result = {k: self.final[k] for k in ('originTaskId', 'botId', 'bindingId', 'sessionId',
                                             'nativeSessionId', 'nativeTurnId', 'status')}
        result.update(finalResult=copy.deepcopy(self.final), events=[], truncated=False, cwd=str(self.root),
                      executionMode='restricted-inference')
        self.call['result_json'] = json.dumps(result, ensure_ascii=False)

    def save(self, assets=None):
        return cli.store_cli_proposal(self.root, self.bindings, self.status, self.call, assets or {},
                                      origin_call=self.origin_call)

    def test_native_receipt_keeps_provenance_without_fabricating_a_core_report(self):
        receipt = self.save()
        record = sink.verified_record(self.root / receipt['relativePath'])
        self.assertEqual(record['origin']['source'], 'CLI_NATIVE_RESULT')
        self.assertEqual(record['origin']['nativeTurnId'], 'native-turn-1')
        self.assertIs(record['origin']['simulation'], True)
        self.assertEqual(record['taskId'], 'origin-task')
        self.assertEqual(record['proposal'], self.value)
        self.assertEqual(self.status['reports'], [])
        self.assertEqual(record['state'], 'VALIDATED_DATA_ONLY')
        for name in ('assets.json', 'clips.json', 'approvals/live-execution-ready.json'):
            self.assertFalse((self.root / name).exists())

    def test_start_ack_and_core_success_are_not_native_completion(self):
        for status in ('starting', 'running', 'waiting', 'interrupted', 'cancelled', 'failed'):
            self.final['status'] = status
            self.refresh()
            with self.subTest(status=status), self.assertRaises(ValueError): self.save()
        self.assertFalse((self.root / '准备输入').exists())

    def test_scope_origin_observer_and_device_must_all_match(self):
        pristine = copy.deepcopy((self.bindings, self.status, self.call, self.final))
        mutations = [lambda: self.status.update(scopeId='old-scope'),
                     lambda: self.status['members'].pop(),
                     lambda: self.status['tasks'][0].update(bot_id='bot-2'),
                     lambda: self.status['tasks'][1].update(status='running'),
                     lambda: self.status['operations'][0].update(task_id='unrelated'),
                     lambda: self.status['operations'][1].update(status='failed'),
                     lambda: self.call.update(bot_id='bot-2'),
                     lambda: self.call.update(tool_name='device.exec'),
                     lambda: self.bindings['cliDevice'].update(bindingId='other-binding'),
                     lambda: self.bindings['cliDevice'].update(id='other-device'),
                     lambda: self.final.update(originTaskId='unrelated'),
                     lambda: self.final.update(sessionId='another-start')]
        for change in mutations:
            self.bindings, self.status, self.call, self.final = copy.deepcopy(pristine)
            change()
            self.refresh()
            with self.subTest(change=change), self.assertRaises(ValueError): self.save()

    def test_result_must_be_full_utf8_json_with_exact_native_digest_and_identity(self):
        pristine = copy.deepcopy(self.call)
        for field, value in (('contentSha256', '0' * 64), ('contentBytes', len(self.final['content'])),
                             ('completionEvidence', 'process-exit-zero'), ('reportId', 'f' * 64),
                             ('nativeTurnId', 'another-turn')):
            self.call = copy.deepcopy(pristine)
            result = json.loads(self.call['result_json'])
            result['finalResult'][field] = value
            self.call['result_json'] = json.dumps(result, ensure_ascii=False)
            with self.subTest(field=field), self.assertRaises(ValueError): self.save()
        self.call = pristine
        self.refresh(raw='{"schemaVersion":1,"schemaVersion":1}')
        with self.assertRaises(ValueError): self.save()

    def test_existing_role_and_data_only_checks_cannot_be_bypassed(self):
        for alter in (lambda: self.value['data'].update(command='arbitrary'),
                      lambda: self.value.update(sceneId='../old'),
                      lambda: self.bindings['members'][1].update(allowedKinds=[])):
            original = copy.deepcopy((self.value, self.bindings))
            alter(); self.refresh()
            with self.subTest(alter=alter), self.assertRaises(ValueError): self.save()
            self.value, self.bindings = original
        self.assertFalse((self.root / '准备输入').exists())

    def test_missing_or_unrelated_origin_call_is_rejected(self):
        with self.assertRaises(ValueError):
            cli.store_cli_proposal(self.root, self.bindings, self.status, self.call, {})
        self.origin_call['arguments_json'] = json.dumps({'deviceId': 'device-new', 'operation': 'status',
                                                         'parameters': {'sessionId': 'start-call'}})
        with self.assertRaises(ValueError): self.save()

    def test_replay_is_idempotent_and_new_native_turn_appends(self):
        first = self.save()
        self.assertEqual(first, self.save())
        self.final['nativeTurnId'] = 'native-turn-2'
        self.bind_new_job()
        self.value['data']['action'] = '大雪落满肩头。'
        self.refresh()
        second = self.save()
        self.assertNotEqual(first['relativePath'], second['relativePath'])
        self.assertEqual(sink.verified_record(self.root / first['relativePath'])['version'], 1)
        self.assertEqual(sink.verified_record(self.root / second['relativePath'])['version'], 2)

    def bind_new_job(self):
        self.status['tasks'].append({'id': 'new-job-task', 'bot_id': 'bot-1', 'status': 'succeeded'})
        self.status['operations'].append({'id': 'new-job-call', 'task_id': 'new-job-task', 'bot_id': 'bot-1',
                                         'tool_name': 'device.agent', 'status': 'succeeded'})
        self.bindings['cliJobs'].append({'id': 'job-2', 'botId': 'bot-1', 'taskId': 'new-job-task', 'root': str(self.root)})
        self.origin_call.update(id='new-job-call', task_id='new-job-task',
            arguments_json=json.dumps({'deviceId': 'device-new', 'operation': 'start',
                                       'parameters': {'agent': 'codex', 'jobId': 'job-2'}}),
            result_json=json.dumps({'sessionId': 'new-job-call', 'cwd': str(self.root),
                                    'status': 'starting', 'executionMode': 'restricted-inference'}))
        self.final.update(originTaskId='new-job-task', sessionId='new-job-call', nativeSessionId='native-thread-2')
        args=json.loads(self.call['arguments_json']); args['parameters']['sessionId']='new-job-call'
        self.call['arguments_json']=json.dumps(args)

    def test_same_native_turn_cannot_be_reattributed_to_another_successful_core_task(self):
        self.save()
        self.status['tasks'][0]['id']='other-origin-task'
        self.status['operations'][0]['task_id']='other-origin-task'
        self.origin_call['task_id']='other-origin-task'
        self.bindings['cliJobs'][0]['taskId']='other-origin-task'
        self.final['originTaskId']='other-origin-task'
        self.refresh()
        with self.assertRaises(ValueError): self.save()

    def test_restricted_job_cannot_be_resumed_with_free_prompt_or_changed_job(self):
        args = json.loads(self.origin_call['arguments_json'])
        args.update(operation='send',parameters={'sessionId':'start-call','prompt':'未批准追加。'})
        self.origin_call['arguments_json'] = json.dumps(args)
        with self.assertRaises(ValueError): self.save()
        args.update(operation='start',parameters={'agent':'codex','jobId':'unapproved-job'})
        self.origin_call['arguments_json']=json.dumps(args)
        with self.assertRaises(ValueError): self.save()

    def test_legacy_unrestricted_launch_and_ambiguous_simulation_are_rejected(self):
        original = copy.deepcopy(self.call)
        result = json.loads(self.call['result_json']); result['executionMode'] = 'legacy-agent'
        self.call['result_json'] = json.dumps(result)
        with self.assertRaises(ValueError): self.save()
        self.call = original
        self.final['simulation'] = 0; self.refresh()
        with self.assertRaises(ValueError): self.save()

    def test_changed_completion_of_same_native_turn_is_rejected_even_with_new_report_id(self):
        self.save()
        self.value['data']['action'] = '篡改同一次原生完成内容。'
        self.refresh()
        with self.assertRaises(ValueError): self.save()
        self.assertEqual(len(list((self.root / '准备输入').rglob('*.v*.json'))), 1)

    def test_event_window_truncation_does_not_truncate_independent_final_result(self):
        raw = ' ' * 8500 + json.dumps(self.value, ensure_ascii=False)
        self.refresh(raw)
        result = json.loads(self.call['result_json']); result.update(truncated=True, events=[])
        self.call['result_json'] = json.dumps(result, ensure_ascii=False)
        receipt = self.save()
        self.assertEqual(receipt['sourceSha256'], sink.sha(raw.encode('utf-8')))

    def test_simulated_native_receipt_is_rejected_by_actual_fixed_job_reader(self):
        self.save()
        (self.root / 'approvals').mkdir()
        (self.root / 'approvals/scope-binding.json').write_text(json.dumps({
            'scopeId': 'scope-cli', 'roles': {'编剧': 'bot-1'}}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'simulated'):
            jobs.Runner(self.root).latest('编剧', 'script', 'Clip00001')

    def test_usage_stays_cli_reported_tokens_without_money_or_request_conversion(self):
        self.final['tokenUsage'] = {'inputTokens': 300, 'cachedInputTokens': 100,
                                   'outputTokens': 5000, 'totalTokens': 5300}
        self.refresh()
        record = sink.verified_record(self.root / self.save()['relativePath'])
        self.assertEqual(record['origin']['tokenUsage']['outputTokens'], 5000)
        self.assertEqual(record['origin']['usageSemantics'], 'CLI_REPORTED_TOKENS_NOT_PHYSICAL_REQUESTS')
        self.assertNotIn('requests', record['origin'])
        self.assertNotIn('cny', record['origin'])

    def test_technical_review_requires_actual_task_bound_media(self):
        asset = self.root / 'frame.png'; asset.write_bytes(b'isolated-media-fixture')
        digest = sink.sha(asset.read_bytes())
        self.bindings['members'][1]['allowedKinds'] = ['technical-review']
        self.value.update(kind='technical-review', data={'target': 'draft', 'decision': 'pass',
                         'artifacts': [{'id': 'draft-1', 'sha256': digest}], 'notes': '合成技术审核。'})
        self.refresh()
        assets = {'draft-1': {'type': 'image', 'path': str(asset), 'sha256': digest,
                              'reviewTaskId': 'another-task'}}
        with self.assertRaises(ValueError): self.save(assets)
        assets['draft-1']['reviewTaskId'] = 'origin-task'
        self.save(assets)


if __name__ == '__main__': unittest.main()
