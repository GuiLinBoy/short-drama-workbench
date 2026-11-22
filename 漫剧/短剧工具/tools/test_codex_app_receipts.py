import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_app_receipts as app
import codex_workflow as wf
import test_codex_workflow as fixtures


class AppReceipts(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.Workflow('test_work_registration_idempotent_and_authorization_preserved')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.work = self.fixture.work
        self.task = self.fixture.task(kind='work-report')
        self.work.start(self.task['taskId'])
        self.raw = self.fixture.raw(self.task)
        self.response = dict(schemaVersion=1,
                             thread=dict(id=self.task['threadId'], hostId=self.task['hostId'],
                                         kind='codex', title='只读测试'),
                             turns=[dict(id='actual-turn', status='completed', error=None,
                                         items=[dict(id='actual-message', type='agentMessage',
                                                     phase='final_answer', text=self.raw)])])

    def accept(self, response=None, turn_id='actual-turn'):
        return app.accept(self.work, self.task['taskId'], response or self.response, turn_id)

    def test_actual_response_text_and_native_identity_preserved_idempotently(self):
        first = self.accept()
        self.assertEqual(self.accept(), first)
        self.assertEqual(first['origin']['turnId'], 'actual-turn')
        self.assertEqual(first['origin']['finalSha256'], wf.data.sha(self.raw.encode('utf-8')))
        saved = wf.verified(wf.inside(self.work.root, '协作/观察/' + self.task['taskId'] + '-app-read-final.json'))
        self.assertEqual(saved['finalText'], self.raw)
        self.assertNotIn('preview', saved)

    def test_compact_wait_snapshot_is_not_the_original_final_reply(self):
        value = {k: v for k, v in self.response.items() if k != 'turns'}
        value['latestAssistantMessage'] = {'text': self.raw}
        with self.assertRaises(ValueError):
            self.accept(value)
        self.assertFalse(self.work.receipt_path(self.task['taskId']).exists())

    def test_real_technical_review_uses_controller_registry_and_task_inputs(self):
        path = self.work.root / 'actual-image.png'
        path.write_bytes(b'LOCAL_FIXTURE_NOT_GENERATED_MEDIA')
        task = self.work.new_task('总导演', 'technical-review', 'Clip00001', ['actual-image.png'])
        self.work.start(task['taskId'])
        response = copy.deepcopy(self.response)
        response['thread'].update(id=task['threadId'], hostId=task['hostId'])
        response['turns'][0]['id'] = 'review-turn'
        response['turns'][0]['items'][0]['text'] = json.dumps(dict(
            schemaVersion=1, kind='technical-review', sceneId='Clip00001',
            data=dict(target='assets', decision='pass', notes='SYNTHETIC_TEST_ONLY',
                      artifacts=[dict(id='fixture', sha256=wf.digest(path))])))
        registry = {'fixture': dict(type='image', path='actual-image.png', sha256=wf.digest(path),
                                    reviewTaskId=task['taskId'])}
        with self.assertRaises(ValueError):
            app.accept(self.work, task['taskId'], response, 'review-turn')
        record = app.accept(self.work, task['taskId'], response, 'review-turn', assets=registry)
        self.assertEqual(record['proposal']['kind'], 'technical-review')

    def test_cutoff_json_is_rejected_without_repair_then_full_source_can_be_read(self):
        value = copy.deepcopy(self.response)
        value['turns'][0]['items'][0]['text'] = self.raw[:-4]
        with self.assertRaises(ValueError):
            self.accept(value)
        self.assertFalse(self.work.receipt_path(self.task['taskId']).exists())
        self.accept()

    def test_wrong_thread_host_or_turn_is_rejected(self):
        for key in ('id', 'hostId'):
            value = copy.deepcopy(self.response)
            value['thread'][key] = 'another'
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.accept(value)
        with self.assertRaises(ValueError):
            self.accept(turn_id='another-turn')

    def test_running_error_truncated_and_multiple_final_messages_are_not_success(self):
        for key, value in (('status', 'inProgress'), ('error', {'message': 'failure'})):
            response = copy.deepcopy(self.response)
            response['turns'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.accept(response)
        for mutate in ('truncated', 'duplicate'):
            response = copy.deepcopy(self.response)
            item = response['turns'][0]['items'][0]
            if mutate == 'truncated':
                item['truncated'] = True
            else:
                response['turns'][0]['items'].append(dict(item))
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.accept(response)

    def test_worker_supplied_non_json_or_code_fences_do_not_become_receipts(self):
        for raw in ('Done', '~~~json\n' + self.raw + '\n~~~'):
            value = copy.deepcopy(self.response)
            value['turns'][0]['items'][0]['text'] = raw
            with self.subTest(raw=raw[:8]), self.assertRaises(ValueError):
                self.accept(value)


if __name__ == '__main__':
    unittest.main(verbosity=2)
