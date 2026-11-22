"""Synthetic local review tests. No production work, credentials or provider calls."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import server
from canvas_review import ReviewStore, ReviewError, digest

HERE = Path(__file__).resolve().parent


class ReviewTests(unittest.TestCase):
    def setUp(self):
        (HERE/'.qa').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=HERE/'.qa')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'作品/离线审核样例'
        self.root.mkdir(parents=True)
        self.write('project.json', {'title': '离线样例', 'data_kind': 'simulation'})
        self.write('assets.json', [])
        self.write('clips.json', [])
        self.file('方案.md', '测试方案：暂停生成，预算仅为测试数据。')
        self.file('分镜.md', '八秒分镜：人物走到门前。')
        self.store = ReviewStore(self.root)

    def file(self, name, content):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes): path.write_bytes(content)
        else: path.write_text(content, encoding='utf-8')
        return path

    def write(self, name, value):
        return self.file(name, json.dumps(value, ensure_ascii=False))

    def packet(self, id='plan', kind='plan', path='方案.md', deps=None, **extra):
        return {'id': id, 'kind': kind, 'title': f'审核 {id}', 'subject': 'EP001', 'summary': '仅离线测试',
                'files': [{'path': path, 'label': path}], 'dependsOn': deps or [], **extra}

    def submit_plan(self):
        self.store.submit(self.packet())

    def item(self, id='plan'):
        return next(i for i in self.store.snapshot()['items'] if i['id'] == id)

    def payload(self, ids=('plan',), action='approve', note=''):
        items = [self.item(id) for id in ids]
        return {'requestId': str(uuid.uuid4()), 'action': action, 'note': note,
                'items': [{k: i[k] for k in ('id', 'fingerprint', 'head')} for i in items]}

    def decide(self, ids=('plan',), action='approve', note=''):
        return self.store.decide(self.payload(ids, action, note))

    def request_packet(self, id='prompt', tool='image', deps=None, refs=None):
        self.file('提示词.txt', '雪夜山门，画面细腻。')
        name = f'requests/{id}.json'
        self.write(name, {'provider': 'nexorai' if tool == 'image' else 'minimax-h3', 'prompt': '实际提交的中文提示词',
                         'prompt_source': str(self.root/'提示词.txt'), 'references': refs or [],
                         'params': {'model': 'offline-fixture', 'n': 1} if tool == 'image' else {'model': 'fixture', 'duration': 8}, 'warnings': []})
        packet = self.packet(id, 'image_prompt' if tool == 'image' else 'video_prompt', '提示词.txt', deps or ['plan'],
                             request=name, generation={'tool': tool, 'batch': 'TEST', 'taskDir': f'tasks/{id}/attempt-001'})
        self.store.submit(packet)
        return self.root/name

    def test_view_never_creates_files_or_imports_legacy_approval(self):
        self.write('assets.json', [{'id': 'old', 'status': 'approved'}])
        before = sorted(str(p) for p in self.root.rglob('*'))
        self.assertEqual(self.store.snapshot()['items'], [])
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob('*')))

    def delegated_fixture(self):
        from codex_workflow import sealed
        self.write('协作/工作区.json', sealed({'version': 1, 'workId': 'review-test', 'root': str(self.root.resolve()),
                   'simulation': True, 'mediaMode': 'approval-and-readiness-required'}))
        self.write('approvals/delegation.json', {'status': 'USER_APPROVED_CODEX_MEDIA_EXECUTION',
                   'approved': True, 'delegatedReview': True, 'workId': 'review-test',
                   'projectRoot': str(self.root.resolve()), 'userQuote': '仅离线测试：委托本作品审核'})
        self.submit_plan()
        item = self.item()
        self.write('reports/assessment.json', {**{k: item[k] for k in ('id', 'fingerprint', 'head')},
                   'requestId': 'delegation-test', 'action': 'approve', 'reviewer': '总导演',
                   'note': '实际逐项检查本次制作方案，以下为本地测试依据。',
                   'checks': [{'result': 'pass', 'note': '方案文件与本次送审内容一致。', 'evidence':
                      [{'path': '方案.md', 'sha256': hashlib.sha256((self.root/'方案.md').read_bytes()).hexdigest()}]}]})

    def test_delegated_review_keeps_own_source(self):
        self.delegated_fixture()
        self.store.decide_delegated('approvals/delegation.json', 'reports/assessment.json')
        self.assertEqual(self.item()['status'], 'approved')
        self.assertEqual(self.item()['history'][-1]['source'], 'DELEGATED_DIRECTOR_REVIEW')
        self.assertIn('assessmentSha256', self.item()['history'][-1]['delegation'])

    def test_delegation_requires_project_scope(self):
        self.delegated_fixture()
        path = self.root/'approvals/delegation.json'
        auth = json.loads(path.read_text(encoding='utf-8'))
        for change in ({'delegatedReview': False}, {'workId': 'other'}, {'projectRoot': str(self.root.parent)}):
            self.write('approvals/delegation.json', {**auth, **change})
            with self.assertRaises(ReviewError):
                self.store.decide_delegated('approvals/delegation.json', 'reports/assessment.json')
        self.assertEqual(self.item()['status'], 'pending')

    def test_delegation_rejects_changed_evidence_and_fingerprint(self):
        self.delegated_fixture()
        path = self.root/'reports/assessment.json'
        assessment = json.loads(path.read_text(encoding='utf-8'))
        self.file('方案.md', '新的方案内容')
        with self.assertRaises(ReviewError):
            self.store.decide_delegated('approvals/delegation.json', 'reports/assessment.json')
        assessment['checks'][0]['evidence'][0]['sha256'] = hashlib.sha256((self.root/'方案.md').read_bytes()).hexdigest()
        self.write('reports/assessment.json', assessment)
        with self.assertRaises(ReviewError):
            self.store.decide_delegated('approvals/delegation.json', 'reports/assessment.json')

    def test_delegation_does_not_accept_unfinished_check(self):
        self.delegated_fixture()
        assessment = json.loads((self.root/'reports/assessment.json').read_text(encoding='utf-8'))
        for result in ('fail', 'limitation'):
            assessment['checks'][0]['result'] = result
            self.write('reports/assessment.json', assessment)
            with self.assertRaises(ReviewError):
                self.store.decide_delegated('approvals/delegation.json', 'reports/assessment.json')

    def test_delegation_cannot_skip_dependencies(self):
        self.delegated_fixture()
        self.store.submit(self.packet('child', 'storyboard', '分镜.md', ['plan']))
        assessment = json.loads((self.root/'reports/assessment.json').read_text(encoding='utf-8'))
        assessment.update({k: self.item('child')[k] for k in ('id', 'fingerprint', 'head')})
        self.write('reports/assessment.json', assessment)
        with self.assertRaises(ReviewError):
            self.store.decide_delegated('approvals/delegation.json', 'reports/assessment.json')

    def test_human_review_source_is_unchanged(self):
        self.submit_plan()
        self.decide()
        self.assertEqual(self.item()['history'][-1]['source'], 'CANVAS_USER_ACTION')

    def test_embedded_storyboard_shares_exact_video_prompt_decision(self):
        self.submit_plan(); self.decide()
        self.file('提示词.txt', '单镜说话')
        self.write('requests/combined.json', {'provider':'minimax-h3','prompt':'实际提示词',
            'params':{'model':'fixture','duration':9},'references':[],'warnings':[]})
        packet=self.packet('combined','video_prompt','提示词.txt',['plan'],storyboard='分镜.md',
            request='requests/combined.json',generation={'tool':'h3','batch':'TEST','taskDir':'tasks/combined/attempt-001'})
        self.store.submit(packet)
        self.assertTrue(self.item('combined')['canApprove'])
        self.assertIn('分镜.md',[f['path'] for f in self.item('combined')['files']])
        self.decide(('combined',))
        self.file('分镜.md','改过的分镜')
        self.assertEqual(self.item('combined')['status'],'stale')
        with self.assertRaises(ReviewError):
            self.store.submit({**packet,'id':'no-plan','dependsOn':[]})

    def test_video_draft_is_readable_but_cannot_authorize_generation(self):
        self.submit_plan(); self.decide()
        self.file('草稿.txt', 'subject_definitions:\n完整草稿正文')
        packet = self.packet('draft', 'video_prompt', '分镜.md', ['plan'],
            storyboard='分镜.md', draft={'prompt':'草稿.txt', 'pending':['等待上一段真实尾帧']})
        self.store.submit(packet)
        item = self.item('draft')
        self.assertEqual(item['status'], 'draft')
        self.assertFalse(item['canApprove'])
        self.assertIsNone(item['request'])
        self.assertTrue(any(f.get('text', '').replace('\r\n', '\n') == 'subject_definitions:\n完整草稿正文' for f in item['files']))
        with self.assertRaises(ReviewError): self.decide(('draft',))
        self.decide(('draft',), action='comment', note='先看文字，暂不生成')
        self.assertEqual(self.item('draft')['status'], 'draft')
        with self.assertRaises(ReviewError):
            self.store.submit({**packet, 'id':'mixed', 'request':'requests/fake.json'})
        with self.assertRaises(ReviewError):
            self.store.submit({**packet, 'id':'no-pending', 'draft':{'prompt':'草稿.txt','pending':[]}})

    def test_draft_reference_files_are_bound_but_pending_sources_never_authorize(self):
        self.submit_plan(); self.decide()
        self.file('草稿.txt','引用素材草稿')
        self.file('试听.wav',b'offline-audio-fixture')
        refs=[{'id':'voice','kind':'audio','label':'张姨原音','status':'candidate','source':'第01段采用版',
               'note':'待确认音色','path':'试听.wav','sourceClip':'EP001/Clip00001'},
              {'id':'tail','kind':'tail','label':'递水尾帧','status':'pending','source':'第02段待采用视频',
               'note':'待提取','sourceClip':'EP001/Clip00002'}]
        packet=self.packet('refs','video_prompt','分镜.md',['plan'],storyboard='分镜.md',draft={
            'prompt':'草稿.txt','pending':['整理音色、等待尾帧'],'references':refs})
        self.store.submit(packet)
        item=self.item('refs')
        self.assertFalse(item['canApprove'])
        self.assertEqual(sum(f['path']=='试听.wav' for f in item['files']),1)
        before=item['fingerprint']
        self.file('试听.wav',b'changed-audio')
        self.assertNotEqual(self.item('refs')['fingerprint'],before)
        for bad in ([refs[0],refs[0]], [{**refs[1],'path':'试听.wav'}], [{**refs[0],'path':'不存在.wav'}],
                    [{**refs[0],'sourceClip':12}]):
            with self.assertRaises(ReviewError):
                self.store.submit({**packet,'id':'bad-ref','draft':{**packet['draft'],'references':bad}})

    def test_explicit_reuse_is_bound_to_pending_prompt_without_importing_approval(self):
        self.submit_plan(); self.decide()
        self.file('old.png', b'old-image')
        sha = hashlib.sha256(b'old-image').hexdigest()
        self.write('assets.json', [{'id':'C00002-A','image':'old.png','status':'approved'}])
        receipt = self.write('reuse.txt', {'user_basis':'王叔复用旧图', 'reused_asset':'C00002-A',
                                         'local_image':'old.png','sha256':sha})
        self.write('requests/reuse.json', {'provider':'minimax-h3','prompt':'实际提示词',
            'params':{'model':'fixture','duration':7},'references':[{'path':str(self.root/'old.png'),'sha256':sha}], 'warnings':[]})
        packet = self.packet('reuse','video_prompt','分镜.md',['plan'],storyboard='分镜.md',
            request='requests/reuse.json',generation={'tool':'h3','batch':'TEST','taskDir':'tasks/reuse/attempt-001'},
            reusedReferences=[{'path':'old.png','sha256':sha,'authorization':'reuse.txt',
                               'authorizationSha256':hashlib.sha256(receipt.read_bytes()).hexdigest()}])
        self.store.submit(packet)
        self.assertTrue(self.item('reuse')['canApprove'])
        self.assertEqual(self.item('reuse')['status'], 'pending')
        self.assertEqual(self.item('reuse')['history'], [])
        self.assertEqual(len(self.store.snapshot()['items']), 2)
        self.decide(('reuse',))
        self.file('old.png', b'changed')
        self.assertFalse(self.item('reuse')['canApprove'])
        self.file('old.png', b'old-image')
        self.file('reuse.txt', '{}')
        self.assertFalse(self.item('reuse')['canApprove'])
        with self.assertRaises(ReviewError): self.store.submit(packet)

    def test_image_prompt_reuse_needs_exact_user_evidence_and_its_own_approval(self):
        self.submit_plan(); self.decide()
        self.file('old.png', b'old-image')
        sha = hashlib.sha256(b'old-image').hexdigest()
        self.write('assets.json', [{'id':'C00002-A','image':'old.png','status':'approved'}])
        receipt = self.write('reuse.txt', {'user_basis':'王叔复用旧图', 'reused_asset':'C00002-A',
                                         'local_image':'old.png','sha256':sha})
        request = self.write('requests/image-reuse.json', {'provider':'nexorai','prompt':'双人构图',
            'params':{'model':'fixture','n':1},'references':[{'path':str(self.root/'old.png'),'sha256':sha}], 'warnings':[]})
        packet = self.packet('image-reuse','image_prompt','方案.md',['plan'],
            request='requests/image-reuse.json',generation={'tool':'image','batch':'TEST','taskDir':'tasks/image-reuse/attempt-001'})
        self.store.submit(packet)
        self.assertFalse(self.item('image-reuse')['canApprove'])
        packet['reusedReferences'] = [{'path':'old.png','sha256':sha,'authorization':'reuse.txt',
                                      'authorizationSha256':hashlib.sha256(receipt.read_bytes()).hexdigest()}]
        self.store.submit(packet)
        item = self.item('image-reuse')
        self.assertTrue(item['canApprove'])
        self.assertEqual(item['status'], 'pending')
        self.assertEqual(item['history'], [])
        self.assertEqual(len(self.store.snapshot()['items']), 2)
        with self.assertRaises(ReviewError):
            self.store.require_generation('image', request, 'TEST', self.root/'tasks/image-reuse/attempt-001')
        self.decide(('image-reuse',))
        self.store.require_generation('image', request, 'TEST', self.root/'tasks/image-reuse/attempt-001')
        self.write('assets.json', [{'id':'C00002-A','image':'old.png','status':'planned'}])
        self.assertFalse(self.item('image-reuse')['canApprove'])
        with self.assertRaises(ReviewError): self.store.submit(packet)

    def test_repair_uses_exact_rejected_result_without_approving_it(self):
        self.submit_plan(); self.decide()
        self.request_packet(); self.decide(('prompt',))
        self.file('room.png', b'offline-image')
        source = self.store.submit(self.packet('room', 'image', 'room.png', ['prompt'], subject='S00001'))
        self.decide(('room',), 'request_changes', '去掉多余门洞')
        sha = hashlib.sha256(b'offline-image').hexdigest()
        path = self.request_packet('repair', refs=[{'path': str(self.root/'room.png'), 'sha256': sha}])
        raw = json.loads(path.read_text(encoding='utf-8')); raw['endpoint']='/images/edits'
        self.write('requests/repair.json', raw)
        packet = self.packet('repair', 'image_prompt', '提示词.txt', ['plan'], subject='S00001',
            request='requests/repair.json', generation={'tool':'image','batch':'TEST','taskDir':'tasks/repair/attempt-001'},
            editSource={'reviewId':'room','recordSha256':digest(source),'path':'room.png','sha256':sha})
        self.store.submit(packet)
        self.assertTrue(self.item('repair')['canApprove'])
        self.assertEqual(self.item('repair')['status'], 'pending')
        self.decide(('repair',))
        self.assertEqual(self.item('room')['status'], 'changes_requested')
        self.assertEqual(self.item('prompt')['status'], 'approved')
        self.file('room.png', b'changed')
        self.assertFalse(self.item('repair')['canApprove'])
        self.assertNotEqual(self.item('repair')['status'], 'approved')
        self.file('room.png', b'offline-image')
        for changes in ({'subject':'S00002'}, {'editSource':{**packet['editSource'],'recordSha256':'0'*64}}):
            with self.assertRaises(ReviewError): self.store.submit({**packet,**changes})
        raw['endpoint']='/images/generations'; self.write('requests/repair.json',raw)
        with self.assertRaises(ReviewError): self.store.submit(packet)

    def test_unchanged_text_and_request_reuse_bytes_but_edits_invalidate_review(self):
        self.submit_plan(); self.decide()
        request = self.request_packet()
        self.decide(('prompt',))
        before = self.item('prompt')
        original = Path.read_bytes
        reads = []
        def counted(path):
            reads.append(path)
            return original(path)
        with patch.object(Path, 'read_bytes', counted):
            self.assertEqual(self.item('prompt')['fingerprint'], before['fingerprint'])
            self.assertNotIn(request, reads)
            self.assertNotIn(self.root/'提示词.txt', reads)
            value = json.loads(request.read_text(encoding='utf-8'))
            value['prompt'] = '发生变化的中文提示词'
            self.write('requests/prompt.json', value)
            changed = self.item('prompt')
            self.assertIn(request, reads)
            self.assertEqual(changed['status'], 'stale')
            self.assertNotEqual(changed['fingerprint'], before['fingerprint'])
        self.file('提示词.txt', '修改过的原始提示词')
        self.assertNotEqual(self.item('prompt')['fingerprint'], changed['fingerprint'])
        request.unlink()
        with self.assertRaises(ReviewError):
            self.store.snapshot()

    def test_media_replacement_with_same_size_and_mtime_invalidates_hash(self):
        path = self.file('图.png', b'AAAA')
        entry = {'path': '图.png'}
        first = self.store._file(entry)
        old_stat = path.stat()
        replacement = self.file('replacement.png', b'BBBB')
        os.utime(replacement, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        os.replace(replacement, path)
        self.assertNotEqual(self.store._file(entry)['sha256'], first['sha256'])
        path.unlink()
        self.assertFalse(self.store._file(entry)['exists'])

    def test_ffmpeg_verification_is_reused_until_binary_or_manifest_changes(self):
        path = self.file('ffmpeg.exe', b'AAAA')
        runtime = {'ffmpeg': {'path': str(path), 'sha256': 'a'*64}}
        with patch('authorized_cli.checked_binary', return_value=str(path)) as check:
            self.store._checked_ffmpeg(runtime)
            self.store._checked_ffmpeg(runtime)
            check.assert_called_once()
            old_stat = path.stat()
            replacement = self.file('replacement.exe', b'BBBB')
            os.utime(replacement, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
            os.replace(replacement, path)
            check.side_effect = SystemExit('approved runtime executable changed')
            with self.assertRaises(SystemExit):
                self.store._checked_ffmpeg(runtime)
            self.assertEqual(check.call_count, 2)
            check.side_effect = None
            runtime['ffmpeg']['sha256'] = 'b'*64
            self.store._checked_ffmpeg(runtime)
            self.assertEqual(check.call_count, 3)
            path.unlink()
            with self.assertRaises(OSError):
                self.store._checked_ffmpeg(runtime)

    def test_submission_stays_pending_and_preserves_production_manifests(self):
        before = {n: (self.root/n).read_bytes() for n in ('project.json', 'assets.json', 'clips.json')}
        self.submit_plan()
        self.assertEqual(self.item()['status'], 'pending')
        self.decide()
        self.assertEqual(self.item()['status'], 'approved')
        self.assertEqual(before, {n: (self.root/n).read_bytes() for n in before})
        self.assertFalse(self.store.snapshot()['generationTriggered'])

    def test_changes_require_note_and_are_visible_after_restart(self):
        self.submit_plan()
        with self.assertRaises(ReviewError): self.decide(action='request_changes')
        self.decide(action='request_changes', note='请修改第二句。')
        other = ReviewStore(self.root).snapshot()
        self.assertEqual(other['items'][0]['status'], 'changes_requested')
        self.assertEqual(other['history'][0]['note'], '请修改第二句。')

    def test_comment_preserves_decision_and_defer_is_recorded(self):
        self.submit_plan(); self.decide()
        self.decide(action='comment', note='保留此版本。')
        self.assertEqual(self.item()['status'], 'approved')
        self.decide(action='defer')
        self.assertEqual(self.item()['status'], 'deferred')
        self.assertEqual(len(self.store.snapshot()['history']), 3)

    def test_cancel_defer_returns_pending_and_preserves_all_old_records(self):
        self.submit_plan();self.decide(action='defer')
        self.decide(action='comment',note='等我再决定。')
        old={p.name:p.read_bytes() for p in self.store.journal.glob('*.json')}
        self.assertTrue(self.item()['canResume'])
        self.decide(action='resume')
        current=ReviewStore(self.root).snapshot()
        self.assertEqual(current['items'][0]['status'],'pending')
        self.assertFalse(current['items'][0]['canResume'])
        self.assertEqual([r['action'] for r in current['history']],['resume','comment','defer'])
        self.assertFalse(current['generationTriggered'])
        for name,raw in old.items():self.assertEqual((self.store.journal/name).read_bytes(),raw)

    def test_resume_cannot_reopen_non_deferred_items(self):
        self.submit_plan()
        for action in (None,'approve','request_changes'):
            if action:self.decide(action=action,note='明确修改意见')
            count=len(self.store.records())
            with self.assertRaisesRegex(ReviewError,'暂缓'):self.decide(action='resume')
            self.assertEqual(len(self.store.records()),count)

    def test_cancel_defer_mixed_batch_fails_without_partial_write(self):
        self.submit_plan();self.decide(action='defer');self.store.submit(self.packet('other'))
        count=len(self.store.records())
        with self.assertRaises(ReviewError):self.decide(('plan','other'),action='resume')
        self.assertEqual(len(self.store.records()),count)
        self.assertEqual(self.item()['status'],'deferred')
        self.decide(('other',),action='defer')
        self.decide(('plan','other'),action='resume')
        self.assertTrue(all(i['status']=='pending' for i in self.store.snapshot()['items']))

    def test_changed_material_can_cancel_hold_but_is_never_approved(self):
        self.submit_plan();self.decide(action='defer')
        old_payload=self.payload(action='resume')
        self.file('方案.md','修改后仍待确认的版本')
        self.assertEqual(self.item()['status'],'stale')
        self.assertTrue(self.item()['canResume'])
        with self.assertRaises(ReviewError):self.store.decide(old_payload)
        self.decide(action='resume')
        self.assertEqual(self.item()['status'],'pending')
        self.assertIn('修改后',self.item()['files'][0]['text'])

    def test_cancel_hold_does_not_restore_prior_approval_or_unblock_children(self):
        self.submit_plan();self.decide()
        self.store.submit(self.packet('story','storyboard','分镜.md',['plan']));self.decide(('story',))
        self.decide(action='defer');self.assertEqual(self.item('story')['status'],'blocked')
        self.decide(action='resume')
        self.assertEqual(self.item()['status'],'pending')
        self.assertEqual(self.item('story')['status'],'blocked')
        self.assertFalse(self.item('story')['canApprove'])

    def test_cancel_hold_keeps_generation_guard_locked(self):
        self.submit_plan();self.decide();request=self.request_packet();self.decide(('prompt',))
        self.store.require_generation('image',request,'TEST',self.root/'tasks/prompt/attempt-001')
        self.decide(('prompt',),action='defer');self.decide(('prompt',),action='resume')
        with self.assertRaises(ReviewError):
            self.store.require_generation('image',request,'TEST',self.root/'tasks/prompt/attempt-001')
        self.assertEqual(self.item('prompt')['status'],'pending')

    def test_cancel_hold_response_retry_is_idempotent(self):
        self.submit_plan();self.decide(action='defer');payload=self.payload(action='resume')
        self.store.decide(payload);count=len(self.store.records())
        self.assertTrue(self.store.decide(payload)['duplicate'])
        self.assertEqual(len(self.store.records()),count)

    def test_exact_content_change_invalidates_approval_and_retains_old_text(self):
        self.submit_plan(); self.decide()
        fingerprint = self.item()['fingerprint']
        self.file('方案.md', '改后的方案')
        self.assertNotEqual(fingerprint, self.item()['fingerprint'])
        self.assertEqual(self.item()['status'], 'stale')
        records = self.store.records()
        self.assertIn('暂停生成', records[-1]['entries'][0]['content']['files'][0]['text'])

    def test_modified_packet_and_upstream_invalidate_downstream(self):
        self.submit_plan(); self.decide()
        self.store.submit(self.packet('story', 'storyboard', '分镜.md', ['plan']))
        self.decide(('story',))
        self.file('方案.md', '新方案')
        self.assertEqual(self.item('story')['status'], 'stale')
        self.assertFalse(self.item('story')['canApprove'])

    def test_upstream_withdrawal_blocks_downstream(self):
        self.submit_plan(); self.decide()
        self.store.submit(self.packet('story', 'storyboard', '分镜.md', ['plan']))
        self.decide(('story',))
        self.decide(action='request_changes', note='方案要调整')
        self.assertEqual(self.item('story')['status'], 'blocked')

    def test_stale_batch_is_atomic_and_does_not_partially_approve(self):
        self.submit_plan()
        self.store.submit(self.packet('other'))
        payload = self.payload(('plan', 'other'))
        self.decide(('other',), action='comment', note='先看看')
        count = len(self.store.records())
        with self.assertRaises(ReviewError): self.store.decide(payload)
        self.assertEqual(len(self.store.records()), count)
        self.assertEqual(self.item()['status'], 'pending')

    def test_independent_batch_approval(self):
        self.submit_plan(); self.store.submit(self.packet('other'))
        self.decide(('plan', 'other'))
        self.assertTrue(all(i['status'] == 'approved' for i in self.store.snapshot()['items']))
        self.assertEqual(len(self.store.records()), 3)

    def test_concurrent_decisions_only_one_wins(self):
        self.submit_plan()
        one = self.payload()
        two = {**one, 'requestId': str(uuid.uuid4()), 'action': 'defer'}
        def write(payload):
            try: return ReviewStore(self.root).decide(payload)['saved']
            except ReviewError: return False
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(write, (one, two)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.store.records()), 2)

    def test_lost_response_retry_is_idempotent(self):
        self.submit_plan(); payload = self.payload()
        self.store.decide(payload)
        self.assertTrue(ReviewStore(self.root).decide(payload)['duplicate'])
        self.assertEqual(len(self.store.records()), 2)
        with self.assertRaises(ReviewError): self.store.decide({**payload, 'note': 'changed'})

    def test_repeated_identical_submission_preserves_existing_approval(self):
        self.submit_plan(); self.decide()
        old = self.item()['fingerprint']
        count = len(self.store.records())
        self.submit_plan()
        self.assertEqual(len(self.store.records()), count)
        self.assertEqual(self.item()['fingerprint'], old)
        self.assertEqual(self.item()['status'], 'approved')

    def test_missing_media_cannot_be_approved(self):
        self.submit_plan()
        (self.root/'方案.md').unlink()
        self.assertFalse(self.item()['canApprove'])
        with self.assertRaises(ReviewError): self.decide()

    def test_paths_private_files_and_cycles_are_rejected(self):
        for name in ('../outside.md', 'secret.txt', 'D:/outside.md', 'https://example.invalid/a.md'):
            with self.subTest(name=name), self.assertRaises(ReviewError):
                self.store.submit(self.packet(path=name))
        self.submit_plan()
        self.store.submit(self.packet('story', 'storyboard', '分镜.md', ['plan']))
        with self.assertRaises(ReviewError): self.store.submit(self.packet(deps=['story']))
        with self.assertRaises(ReviewError): self.store.submit(self.packet('bad', 'storyboard', '分镜.md'))

    def test_corrupt_journal_fails_closed_without_overwriting(self):
        self.submit_plan(); path = next(self.store.journal.glob('*.json'))
        path.write_text('{}', encoding='utf-8')
        with self.assertRaises(ReviewError): self.store.snapshot()
        with self.assertRaises(ReviewError): self.store.submit(self.packet('other'))
        self.assertEqual(path.read_text(encoding='utf-8'), '{}')

    def test_request_approval_binds_exact_prompt_params_batch_and_files(self):
        self.submit_plan(); self.decide()
        path = self.request_packet()
        self.assertEqual(self.item('prompt')['request']['prompt'], '实际提交的中文提示词')
        with self.assertRaises(ReviewError): self.store.require_generation('image', path, 'TEST', self.root/'tasks/prompt/attempt-001')
        self.decide(('prompt',))
        self.store.require_generation('image', path, 'TEST', self.root/'tasks/prompt/attempt-001')
        with self.assertRaises(ReviewError): self.store.require_generation('image', path, 'OTHER', self.root/'tasks/prompt/attempt-001')
        request = json.loads(path.read_text(encoding='utf-8')); request['params']['n'] = 2
        self.write('requests/prompt.json', request)
        with self.assertRaises(ReviewError): self.store.require_generation('image', path, 'TEST', self.root/'tasks/prompt/attempt-001')

    def test_reference_hash_and_human_media_review_required(self):
        self.submit_plan(); self.decide()
        path = self.file('参考.png', b'original-image')
        refs = [{'path': str(path), 'role': 'reference_image', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
        self.request_packet(refs=refs)
        self.assertFalse(self.item('prompt')['canApprove'])
        self.file('参考.png', b'changed-image')
        self.assertTrue(any('正式请求不一致' in p for p in self.item('prompt')['problems']))

    def test_complete_h3_review_chain_and_attempt_scope(self):
        self.submit_plan(); self.decide()
        self.store.submit(self.packet('story', 'storyboard', '分镜.md', ['plan'])); self.decide(('story',))
        self.request_packet('image-prompt'); self.decide(('image-prompt',))
        image = self.file('图片.png', b'LOCAL_FIXTURE_IMAGE')
        self.store.submit(self.packet('image', 'image', '图片.png', ['image-prompt'])); self.decide(('image',))
        refs = [{'path':str(image),'role':'reference_image','sha256':hashlib.sha256(image.read_bytes()).hexdigest()}]
        request = self.request_packet('h3', 'h3', ['plan', 'story', 'image'], refs)
        self.assertTrue(self.item('h3')['canApprove'])
        self.decide(('h3',))
        self.store.require_generation('h3', request, 'TEST', self.root/'tasks/h3/attempt-001')
        with self.assertRaises(ReviewError):
            self.store.require_generation('h3', request, 'TEST', self.root/'tasks/h3/attempt-002')
        self.file('视频.mp4', b'LOCAL_FIXTURE_VIDEO')
        self.store.submit(self.packet('video', 'video', '视频.mp4', ['h3'])); self.decide(('video',))
        self.store.submit(self.packet('final', 'final', '视频.mp4', ['video'])); self.decide(('final',))
        self.assertEqual(self.item('final')['status'], 'approved')


    def frame_fixture(self):
        self.submit_plan(); self.decide()
        self.store.submit(self.packet('story', 'storyboard', '分镜.md', ['plan'])); self.decide(('story',))
        self.request_packet('source-prompt', 'h3', ['story']); self.decide(('source-prompt',))
        self.file('源视频.mp4', b'SYNTHETIC_SOURCE_VIDEO')
        self.store.submit(self.packet('source-video', 'video', '源视频.mp4', ['source-prompt']))
        self.decide(('source-video',))
        frame = b'\x89PNG\r\n\x1a\nSYNTHETIC_FRAME'
        image = self.file('原样帧.png', frame)
        refs = [{'path': str(image), 'role': 'reference', 'sha256': hashlib.sha256(frame).hexdigest()}]
        self.request_packet('use-frame', 'h3', ['story', 'source-video'], refs)
        packet = self.store.records()[-1]['packet']
        packet['derivedReferences'] = [dict(path='原样帧.png', source='源视频.mp4', frameIndex=4,
            sourceSha256=hashlib.sha256((self.root/'源视频.mp4').read_bytes()).hexdigest())]
        self.store.submit(packet)
        binary = self.file('ffmpeg.exe', b'SYNTHETIC_EXECUTABLE')
        patches = [patch('authorized_cli.runtime_tools', return_value={'ffmpeg': {'path':str(binary), 'sha256':'a'*64}}),
                   patch('authorized_cli.checked_binary', return_value=str(binary)),
                   patch('canvas_review.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=frame))]
        mocks = [p.start() for p in patches]
        for p in patches: self.addCleanup(p.stop)
        return packet, mocks[-1]

    def test_exact_frame_reuses_approved_source_but_prompt_stays_pending(self):
        packet, run = self.frame_fixture()
        self.assertTrue(self.item('use-frame')['canApprove'])
        self.assertEqual(self.item('use-frame')['status'], 'pending')
        self.assertEqual(self.item('source-video')['status'], 'approved')
        run.assert_called_once()
        self.assertIn('select=eq(n\\,4)', run.call_args.args[0])
        self.assertIn('file,pipe', run.call_args.args[0])
        with self.assertRaises(ReviewError):
            self.store.require_generation('h3', self.root/'requests/use-frame.json', 'TEST', self.root/'tasks/use-frame/attempt-001')

    def test_edited_frame_cannot_inherit_source_approval_even_if_request_hash_matches(self):
        packet, run = self.frame_fixture()
        run.return_value.stdout = b'\x89PNG\r\n\x1a\nDIFFERENT_DECODED_FRAME'
        self.assertFalse(self.item('use-frame')['canApprove'])
        self.assertTrue(any('不是已通过视频' in x for x in self.item('use-frame')['problems']))

    def test_frame_source_withdrawal_blocks_even_cached_decode(self):
        packet, run = self.frame_fixture()
        self.assertTrue(self.item('use-frame')['canApprove'])
        self.decide(('source-video',), action='request_changes', note='源视频需要修改')
        self.assertFalse(self.item('use-frame')['canApprove'])

    def test_frame_source_change_or_wrong_source_hash_is_blocked(self):
        packet, run = self.frame_fixture()
        self.assertTrue(self.item('use-frame')['canApprove'])
        self.file('源视频.mp4', b'CHANGED_SOURCE')
        self.assertFalse(self.item('use-frame')['canApprove'])
        self.assertEqual(self.item('source-video')['status'], 'stale')

    def test_frame_decode_failure_is_not_a_coverage_fallback(self):
        packet, run = self.frame_fixture()
        run.return_value = SimpleNamespace(returncode=1, stdout=b'')
        self.assertFalse(self.item('use-frame')['canApprove'])

    def test_frame_declaration_rejects_filters_bad_index_paths_and_unreferenced_files(self):
        packet, run = self.frame_fixture()
        original = json.loads(json.dumps(packet))
        for change in [dict(frameIndex=True), dict(frameIndex=-1), dict(frameIndex=10000),
                       dict(source='../outside.mp4'), dict(path='not-a-reference.png'), dict(filter='crop=1:1'),
                       dict(sourceSha256='bad')]:
            with self.subTest(change=change), self.assertRaises(ReviewError):
                value = json.loads(json.dumps(original)); value['derivedReferences'][0].update(change)
                self.store.submit(value)

    def test_new_frame_selection_stales_existing_prompt_approval(self):
        packet, run = self.frame_fixture()
        self.decide(('use-frame',))
        packet['derivedReferences'][0]['frameIndex'] = 5
        self.store.submit(packet)
        self.assertEqual(self.item('use-frame')['status'], 'stale')
        self.assertEqual(run.call_count, 2)

    def test_invalid_frame_runtime_fails_closed(self):
        packet, run = self.frame_fixture()
        with patch('authorized_cli.checked_binary', side_effect=SystemExit('runtime changed')):
            self.assertFalse(self.item('use-frame')['canApprove'])
        run.assert_not_called()


class ReviewHTTPTests(unittest.TestCase):
    def setUp(self):
        (HERE/'.qa').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=HERE/'.qa'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'作品/离线HTTP样例'; self.project.mkdir(parents=True)
        for n,v in (('project.json', {'title':'离线HTTP样例','data_kind':'simulation'}), ('assets.json', []), ('clips.json', [])):
            (self.project/n).write_text(json.dumps(v), encoding='utf-8')
        (self.project/'plan.md').write_text('离线审核，不生成。', encoding='utf-8')
        self.store = ReviewStore(self.project)
        self.store.submit({'id':'plan','kind':'plan','title':'HTTP测试方案','files':[{'path':'plan.md'}]})
        self.http = server.Server(('127.0.0.1',0), server.Library(self.root))
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.addCleanup(self.http.server_close); self.addCleanup(self.http.shutdown)
        self.origin = f'http://127.0.0.1:{self.http.server_port}'

    def request(self, path='/api/snapshot', method='GET', body=None, headers=None):
        conn = HTTPConnection('127.0.0.1', self.http.server_port, timeout=3)
        conn.request(method,path,body=body,headers=headers or {})
        result=conn.getresponse(); data=result.read(); headers=dict(result.getheaders()); status=result.status
        conn.close(); return status,headers,json.loads(data)

    def payload(self):
        _, headers, snapshot = self.request()
        item = snapshot['projects'][0]['reviews']['items'][0]
        data = {'project': snapshot['projects'][0]['key'], 'decision': {'requestId':str(uuid.uuid4()), 'action':'approve','note':'',
                 'items':[{k:item[k] for k in ('id','fingerprint','head')}]}}
        return json.dumps(data), {'Origin':self.origin,'Content-Type':'application/json','X-Canvas-Review':headers['X-Canvas-Review']}

    def test_human_action_saves_only_review_journal_and_no_generation(self):
        before = {p.relative_to(self.project).as_posix():p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        body, headers = self.payload()
        status,_,result = self.request('/api/reviews','POST',body,headers)
        self.assertEqual(status,200); self.assertFalse(result['generationTriggered'])
        for name, raw in before.items(): self.assertEqual((self.project/name).read_bytes(),raw)
        self.assertEqual(self.store.snapshot()['items'][0]['status'],'approved')
        self.assertTrue(self.request()[2]['production_execution'] is False)

    def test_cross_origin_missing_nonce_and_readonly_cannot_write(self):
        body, headers = self.payload()
        for change in ({'Origin':'https://attacker.invalid'}, {'Origin':''}, {'X-Canvas-Review':''}, {'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.request('/api/reviews','POST',body,{**headers,**change})[0],403)
        self.http.reviews_enabled=False
        self.assertEqual(self.request('/api/reviews','POST',body,headers)[0],403)
        self.assertEqual(self.store.snapshot()['items'][0]['status'],'pending')

    def test_stale_page_invalid_content_type_and_wrong_project_rejected(self):
        body, headers = self.payload()
        self.assertEqual(self.request('/api/reviews','POST',body,{**headers,'Content-Type':'text/plain'})[0],400)
        wrong=json.loads(body); wrong['project']='../../outside'
        self.assertEqual(self.request('/api/reviews','POST',json.dumps(wrong),headers)[0],409)
        (self.project/'plan.md').write_text('修改后的方案',encoding='utf-8')
        self.assertEqual(self.request('/api/reviews','POST',body,headers)[0],409)
        self.assertEqual(len(self.store.records()),1)

    def test_new_service_session_refreshes_token_when_data_is_unchanged(self):
        _, original_headers, original = self.request()
        self.http.review_token = 'NEW_SYNTHETIC_LOCAL_SESSION'
        status, headers, current = self.request(headers={'If-None-Match':original_headers['ETag']})
        self.assertEqual(status, 200)
        self.assertEqual(original['revision'], current['revision'])
        self.assertNotEqual(original_headers['X-Canvas-Review'], headers['X-Canvas-Review'])
        self.assertNotEqual(original_headers['ETag'], headers['ETag'])

    def test_http_cancel_defer_preserves_history_and_does_not_execute(self):
        body,headers=self.payload();body=json.loads(body);body['decision']['action']='defer'
        self.assertEqual(self.request('/api/reviews','POST',json.dumps(body),headers)[0],200)
        body,headers=self.payload();body=json.loads(body);body['decision']['action']='resume'
        status,_,result=self.request('/api/reviews','POST',json.dumps(body),headers)
        self.assertEqual(status,200);self.assertFalse(result['generationTriggered'])
        state=self.store.snapshot();self.assertEqual(state['items'][0]['status'],'pending')
        self.assertEqual([h['action'] for h in state['history']],['resume','defer'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
