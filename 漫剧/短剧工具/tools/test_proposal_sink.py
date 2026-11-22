import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import proposal_sink as sink


class ProposalSinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bindings = {'scopeId': 'scope-new', 'root': str(self.root),
                         'members': [{'id': f'bot-{i}', 'allowedKinds': ['script'] if i == 1 else []}
                                     for i in range(5)]}
        self.proposal = {'schemaVersion': 1, 'kind': 'script', 'sceneId': 'Clip00001',
                         'data': {'durationMs': 8000, 'narration': '这一夜，他终于回家。', 'action': '推开石门。'}}
        self.status = {'scopeId': 'scope-new', 'members': [{'id': f'bot-{i}'} for i in range(5)],
                       'tasks': [{'id': 'task-1', 'bot_id': 'bot-1', 'status': 'succeeded'}],
                       'reports': [{'id': 'report-1', 'task_id': 'task-1', 'bot_id': 'bot-1',
                                    'role': 'assistant', 'kind': 'message', 'content': ''}]}
        self.refresh()

    def refresh(self):
        raw = json.dumps(self.proposal, ensure_ascii=False)
        self.status['reports'][0]['content'] = raw
        self.status['tasks'][0]['result_summary'] = raw

    def save(self):
        return sink.store_proposal(self.root, self.bindings, self.status, 'task-1', 'report-1', {})

    def test_final_report_receipt_links_actual_task_and_no_execution_claim(self):
        receipt = self.save()
        path = self.root / receipt['relativePath']
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved['proposal'], self.proposal)
        self.assertEqual(saved['taskId'], 'task-1')
        self.assertEqual(saved['reportId'], 'report-1')
        self.assertEqual(saved['sourceSha256'], hashlib.sha256(self.status['reports'][0]['content'].encode()).hexdigest())
        self.assertEqual(saved['state'], 'VALIDATED_DATA_ONLY')
        self.assertNotIn('passed', saved)
        self.assertFalse((self.root / 'assets.json').exists())

    def test_replay_returns_same_receipt_and_edit_is_new_version(self):
        first = self.save()
        self.assertEqual(first, self.save())
        self.proposal['data']['action'] = '雪落在肩头。'
        self.refresh()
        with self.assertRaises(ValueError): self.save()
        self.status['reports'][0]['id'] = 'report-2'
        self.status['tasks'][0]['id'] = 'task-2'
        self.status['reports'][0]['task_id'] = 'task-2'
        second = sink.store_proposal(self.root, self.bindings, self.status, 'task-2', 'report-2', {})
        self.assertNotEqual(first['relativePath'], second['relativePath'])
        self.assertEqual(len(list(self.root.rglob('*.v*.json'))), 2)

    def test_wrong_scope_member_task_and_nonfinal_reports_refused(self):
        original = copy.deepcopy(self.status)
        mutations = [lambda: self.status.update(scopeId='old'),
                     lambda: self.status['members'].pop(),
                     lambda: self.status['reports'][0].update(task_id='another-task'),
                     lambda: self.status['reports'][0].update(bot_id='bot-0'),
                     lambda: self.status['reports'][0].update(role='peer'),
                     lambda: self.status['tasks'][0].update(status='running'),
                     lambda: self.status['tasks'][0].update(result_summary='different final')]
        for change in mutations:
            self.status = copy.deepcopy(original); change()
            with self.subTest(change=change), self.assertRaises(ValueError): self.save()
        self.assertFalse((self.root / '准备输入').exists())

    def test_role_and_extra_code_path_fields_refused(self):
        original = copy.deepcopy(self.proposal)
        mutations = [lambda: self.proposal.update(path='../old.json'),
                     lambda: self.proposal['data'].update(command='python -c arbitrary'),
                     lambda: self.proposal.update(sceneId='../escape'),
                     lambda: self.proposal.update(kind='visual-prompt', data={'positive': '雪夜', 'negative': ''}),
                     lambda: self.proposal['data'].update(durationMs=True),
                     lambda: self.proposal['data'].update(narration='字' * 301)]
        for change in mutations:
            self.proposal = copy.deepcopy(original); change(); self.refresh()
            with self.subTest(change=change), self.assertRaises(ValueError): self.save()
        self.assertFalse((self.root / '准备输入').exists())

    def test_plain_json_only_and_duplicate_keys_refused(self):
        for raw in ('```json\n{}\n```', '{}{}', '{"schemaVersion":1,"schemaVersion":2}', 'NaN'):
            self.status['reports'][0]['content'] = raw
            self.status['tasks'][0]['result_summary'] = raw
            with self.subTest(raw=raw), self.assertRaises(ValueError): self.save()

    def test_continuity_requires_real_own_previous_tail_and_two_lines(self):
        path = self.root / '尾帧/Clip00001.png'; path.parent.mkdir(); path.write_bytes(b'local-fixture-tail')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assets = {'tail-1': {'path': str(path), 'sha256': digest, 'type': 'tail-frame', 'clipId': 'Clip00001'}}
        self.bindings['members'][1]['allowedKinds'] = ['continuity']
        self.proposal = {'schemaVersion': 1, 'kind': 'continuity', 'sceneId': 'Clip00002',
                         'data': {'sourceFrameId': 'tail-1', 'sourceFrameSha256': digest,
                                  'lines': ['归人右手仍扶门。', '镜头从门外接近。']}}
        self.refresh()
        result = sink.store_proposal(self.root, self.bindings, self.status, 'task-1', 'report-1', assets)
        self.assertTrue((self.root / result['relativePath']).exists())
        self.proposal['data']['lines'].pop(); self.refresh()
        with self.assertRaises(ValueError):
            sink.store_proposal(self.root, self.bindings, self.status, 'task-1', 'report-1', assets)
        self.proposal['data']['lines'].append('从门外接近。'); self.refresh()
        assets['tail-1']['clipId'] = 'Clip00003'
        with self.assertRaises(ValueError):
            sink.store_proposal(self.root, self.bindings, self.status, 'task-1', 'report-1', assets)
        assets['tail-1']['clipId'] = 'Clip00001'
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            sink.store_proposal(self.root, self.bindings, self.status, 'task-1', 'report-1', assets)

    def test_voice_range_requires_probed_current_audio_and_valid_integer_bounds(self):
        path = self.root / '声音/reference.wav'; path.parent.mkdir(); path.write_bytes(b'fixture-audio')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assets = {'voice-approved': {'path': str(path), 'sha256': digest, 'probedSha256': digest,
                                    'type': 'audio', 'durationMs': 5010}}
        self.bindings['members'][1]['allowedKinds'] = ['voice-range']
        self.proposal = {'schemaVersion': 1, 'kind': 'voice-range', 'sceneId': 'Clip00001',
                         'data': {'voiceSourceId': 'voice-approved', 'startMs': 300, 'endMs': 4100}}
        self.refresh()
        sink.store_proposal(self.root, self.bindings, self.status, 'task-1', 'report-1', assets)
        for end in (5011, True, -1):
            self.proposal['data']['endMs'] = end; self.refresh()
            with self.assertRaises(ValueError):
                sink.store_proposal(self.root, self.bindings, self.status, 'task-1', 'report-1', assets)

    def test_existing_corrupt_or_modified_version_refuses_without_overwrite(self):
        receipt = self.save(); path = self.root / receipt['relativePath']
        path.write_bytes(b'broken existing receipt')
        before = path.read_bytes()
        with self.assertRaises(ValueError): self.save()
        self.assertEqual(path.read_bytes(), before)

    def test_three_clip_script_rollup_requires_24_seconds_and_cumulative_300_characters(self):
        values = [dict(self.proposal, sceneId=f'Clip{i:05d}') for i in range(1, 4)]
        sink.validate_script_rollup(values)
        values[2] = copy.deepcopy(values[2]); values[2]['data']['durationMs'] = 7999
        with self.assertRaises(ValueError): sink.validate_script_rollup(values)
        values[2]['data']['durationMs'] = 8000
        values[2]['data']['narration'] = '字' * 300
        with self.assertRaises(ValueError): sink.validate_script_rollup(values)

    def test_technical_review_requires_current_artifact_hash_and_actual_review_task(self):
        path=self.root/'reference.png'; path.write_bytes(b'fixture-image')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        assets={'C00001-A:image':{'path':str(path),'sha256':digest,'type':'image','reviewTaskId':'task-1'}}
        self.bindings['members'][1]['allowedKinds']=['technical-review']
        self.proposal={'schemaVersion':1,'kind':'technical-review','sceneId':'Clip00001',
                       'data':{'target':'assets','decision':'pass','artifacts':[{'id':'C00001-A:image','sha256':digest}],
                               'notes':'AUTO_QA：仅合成夹具，不是人工看图。'}}
        self.refresh()
        result=sink.store_proposal(self.root,self.bindings,self.status,'task-1','report-1',assets)
        self.assertEqual(result['state'],'VALIDATED_DATA_ONLY')
        assets['C00001-A:image']['reviewTaskId']='other-task'
        with self.assertRaises(ValueError): sink.store_proposal(self.root,self.bindings,self.status,'task-1','report-1',assets)
        assets['C00001-A:image']['reviewTaskId']='task-1'; path.write_bytes(b'changed')
        with self.assertRaises(ValueError): sink.store_proposal(self.root,self.bindings,self.status,'task-1','report-1',assets)


if __name__ == '__main__': unittest.main()
