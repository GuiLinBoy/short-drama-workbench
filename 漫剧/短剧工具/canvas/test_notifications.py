"""Synthetic outbox/transport evidence; never sends to any real Codex thread."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import unittest
import uuid
from unittest.mock import patch

import server
from canvas_review import ReviewStore, ReviewError, digest
from canvas_notify import NotificationStore

HERE = Path(__file__).resolve().parent


class NotificationTests(unittest.TestCase):
    def test_canvas_shares_one_review_read_and_projection_per_refresh(self):
        lib = server.Library(self.data)
        with patch.object(ReviewStore, 'snapshot', autospec=True, side_effect=ReviewStore.snapshot) as project, \
             patch.object(self.reviews, 'records', wraps=self.reviews.records) as records:
            lib.review_stores[str(self.root)] = self.reviews
            result = lib.snapshot(force=True)
            self.assertEqual(project.call_count, 1)
            self.assertEqual(records.call_count, 1)
        self.assertEqual(len(result['projects'][0]['reviews']['items']), 1)

    def test_shared_notification_projection_matches_fresh_and_writes_recheck(self):
        self.decide()
        records = self.outbox.reviews.records()
        snapshot = self.outbox.reviews.snapshot(records)
        expected = self.outbox.snapshot()
        with patch.object(self.outbox.reviews, 'snapshot', wraps=self.outbox.reviews.snapshot) as build:
            self.assertEqual(self.outbox.snapshot(review_records=records, review_snapshot=snapshot), expected)
            build.assert_not_called()
            self.file('方案.md', 'changed content invalidates previous approval')
            with self.assertRaises(ReviewError):
                self.outbox.enqueue({'requestId': 'changed-content', 'batchFingerprint': expected['batchFingerprint']})
            self.assertGreater(build.call_count, 0)

    def setUp(self):
        (HERE/'.qa').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=HERE/'.qa'); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.root = self.data/'作品/通知隔离样例'; self.root.mkdir(parents=True)
        for name, value in (('project.json',{'title':'通知隔离样例','data_kind':'simulation'}),('assets.json',[]),('clips.json',[])):
            self.write(name,value)
        self.write('协作/工作区.json',{'version':1,'workId':'TEST-work','root':str(self.root),'simulation':True},sealed=True)
        self.binding()
        self.file('方案.md','只作离线测试；不恢复制作，不构成消费授权。')
        self.reviews = ReviewStore(self.root); self.outbox = NotificationStore(self.root)
        self.reviews.submit({'id':'plan','kind':'plan','title':'离线方案','files':[{'path':'方案.md'}]})

    def file(self,name,raw):
        path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True);path.write_text(raw,encoding='utf-8');return path

    def write(self,name,value,sealed=False):
        return self.file(name,json.dumps({**value,'recordSha256':digest(value)} if sealed else value,ensure_ascii=False))

    def binding(self,thread='TEST-director',work='TEST-work'):
        self.write('协作/会话.json',{'workId':work,'roles':{'总导演':{'source':'CODEX_APP_CREATE_THREAD','status':'ready',
            'threadId':thread,'hostId':'TEST-host','simulation':True}},'history':[]},sealed=True)

    def decide(self,action='approve',note=''):
        item=self.reviews.snapshot()['items'][0]
        self.reviews.decide({'requestId':uuid.uuid4().hex,'action':action,'note':note,
                             'items':[{k:item[k] for k in ('id','fingerprint','head')}]})

    def enqueue(self):
        return self.outbox.enqueue({'requestId':uuid.uuid4().hex,'batchFingerprint':self.outbox.snapshot()['batchFingerprint']})

    def claimed(self):
        self.decide(); queued=self.enqueue();claim=self.outbox.claim('TEST-controller')
        self.assertEqual(queued['notificationId'],claim['notificationId']);return claim

    def send_response(self,claim):
        return {'structuredContent':{'schemaVersion':1,'threadId':claim['route']['threadId'],'hostId':'TEST-host',
                 'turnId':'TEST-turn','status':'queued'}}

    def read_response(self,claim,**changes):
        batch=next(r for r in self.outbox.records() if r['type']=='batch')
        ack={'schemaVersion':1,'notificationId':claim['notificationId'],'workId':'TEST-work','status':'received',
             'batchFingerprint':claim['batchFingerprint'],'reviewRecordsDigest':digest(batch['decisionRecords']),
             'summary':'SYNTHETIC TEST ONLY；核对了测试决定。',**changes}
        return {'schemaVersion':1,'thread':{'kind':'codex','id':'TEST-director','hostId':'TEST-host'},
                'turns':[{'id':'TEST-turn','status':'completed','error':None,'items':[
                    {'id':'TEST-message','type':'agentMessage','phase':'final_answer','text':json.dumps(ack,ensure_ascii=False)}]}]}

    def test_view_has_no_writes_and_no_implicit_notification(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        state=self.outbox.snapshot();self.assertFalse(state['canNotify']);self.assertFalse(state['listener']['online'])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_multiple_saved_decisions_become_one_batch_and_new_notes_stay_pending(self):
        self.decide();self.decide('comment','动作放慢。')
        self.assertEqual(self.outbox.snapshot()['unnotifiedDecisions'],2)
        result=self.enqueue();self.assertTrue(result['queued']);self.assertFalse(result['generationTriggered'])
        self.assertEqual(len(self.outbox.snapshot()['queue']),1)
        self.assertEqual(self.outbox.snapshot()['queue'][0]['state'],'queued')
        self.assertEqual(self.outbox.snapshot()['unnotifiedDecisions'],0)
        self.decide('request_changes','请修改第二句。')
        self.assertEqual(self.outbox.snapshot()['unnotifiedDecisions'],1)
        self.enqueue();self.assertEqual(len(self.outbox.snapshot()['queue']),2)

    def test_duplicate_click_and_lost_response_retry_cannot_duplicate_batch(self):
        self.decide();payload={'requestId':'same-request','batchFingerprint':self.outbox.snapshot()['batchFingerprint']}
        self.outbox.enqueue(payload)
        self.assertTrue(NotificationStore(self.root).enqueue(payload)['duplicate'])
        with self.assertRaises(ReviewError):self.outbox.enqueue({**payload,'batchFingerprint':'different'})
        self.assertEqual(len(self.outbox.records()),1)

    def test_stale_page_cannot_include_new_unseen_decisions(self):
        self.decide();payload={'requestId':'old-page','batchFingerprint':self.outbox.snapshot()['batchFingerprint']}
        self.decide('comment','其他标签页刚补充的意见')
        with self.assertRaises(ReviewError):self.outbox.enqueue(payload)
        self.assertEqual(self.outbox.records(),[])

    def test_wrong_or_changed_director_binding_blocks_enqueue_or_delivery(self):
        self.decide();self.binding(work='another-work')
        self.assertFalse(self.outbox.snapshot()['route']['available'])
        with self.assertRaises(ReviewError):self.enqueue()
        self.binding();self.enqueue();self.binding(thread='TEST-other-director')
        self.assertEqual(self.outbox.claim('owner')['state'],'blocked')
        self.assertEqual(self.outbox.snapshot()['queue'][0]['state'],'blocked')
        self.assertEqual(self.outbox.snapshot()['unnotifiedDecisions'],1)
        self.assertTrue(self.outbox.snapshot()['canNotify'])
        self.enqueue()  # Explicit new click; never silently retarget the old batch.
        self.assertEqual(self.outbox.claim('owner')['route']['threadId'],'TEST-other-director')

    def test_upstream_material_change_after_queuing_blocks_send(self):
        self.decide();self.enqueue();self.file('方案.md','内容已经变了')
        self.assertEqual(self.outbox.claim('owner')['state'],'blocked')

    def test_cancel_defer_invalidates_old_queued_batch_and_is_not_an_approval(self):
        self.decide('defer');self.enqueue();self.decide('resume')
        self.assertEqual(self.outbox.claim('owner')['state'],'blocked')
        self.assertEqual(self.reviews.snapshot()['items'][0]['status'],'pending')
        self.assertTrue(self.outbox.snapshot()['canNotify'])
        self.enqueue();claim=self.outbox.claim('owner')
        self.assertEqual(claim['state'],'claimed')
        self.assertIn('不向四岗派活',claim['prompt'])

    def test_only_one_controller_can_claim_and_unknown_delivery_never_resends(self):
        self.decide();self.enqueue()
        with ThreadPoolExecutor(2) as executor:
            results=list(executor.map(lambda owner:NotificationStore(self.root).claim(owner),('one','two')))
        claimed=[r for r in results if r['state']=='claimed'];self.assertEqual(len(claimed),1)
        item=claimed[0]
        outcome=self.outbox.delivery(item['notificationId'],item['claimId'],{'isError':True})
        self.assertEqual(outcome['state'],'delivery_unknown')
        self.assertEqual(self.outbox.claim('three')['state'],'idle')
        self.assertEqual(len(self.outbox.awaiting_receipts()),1)

    def test_confirmed_send_is_delivered_not_received(self):
        claim=self.claimed();result=self.outbox.delivery(claim['notificationId'],claim['claimId'],self.send_response(claim))
        self.assertEqual(result['state'],'delivered')
        self.assertEqual(self.outbox.snapshot()['queue'][0]['label'],'已投递')

    def test_prose_or_wrong_thread_success_is_never_delivery_proof(self):
        for response in ({'content':[{'type':'text','text':'发送成功'}]},
                         {'structuredContent':{'threadId':'wrong','turnId':'t','status':'queued'}}):
            claim=self.claimed();result=self.outbox.delivery(claim['notificationId'],claim['claimId'],response)
            self.assertEqual(result['state'],'delivery_unknown')

    def test_director_ack_requires_exact_native_identity_batch_and_full_final(self):
        claim=self.claimed();self.outbox.delivery(claim['notificationId'],claim['claimId'],self.send_response(claim))
        for changes in ({'notificationId':'wrong'},{'workId':'wrong'},{'batchFingerprint':'wrong'},
                        {'reviewRecordsDigest':'wrong'},{'schemaVersion':True}):
            with self.subTest(changes=changes),self.assertRaises(ReviewError):
                self.outbox.receipt(claim['notificationId'],self.read_response(claim,**changes))
        truncated=self.read_response(claim);truncated['turns'][0]['items'][0]['truncated']=True
        with self.assertRaises(ReviewError):self.outbox.receipt(claim['notificationId'],truncated)
        wrong_thread=self.read_response(claim);wrong_thread['thread']['id']='wrong'
        with self.assertRaises(ReviewError):self.outbox.receipt(claim['notificationId'],wrong_thread)
        self.outbox.receipt(claim['notificationId'],self.read_response(claim))
        self.assertEqual(self.outbox.snapshot()['queue'][0]['state'],'received')
        self.assertTrue(self.outbox.receipt(claim['notificationId'],self.read_response(claim))['duplicate'])

    def test_uncertain_send_can_reconcile_original_reply_without_resend(self):
        claim=self.claimed();self.outbox.delivery(claim['notificationId'],claim['claimId'],{'isError':True})
        self.outbox.receipt(claim['notificationId'],self.read_response(claim))
        self.assertEqual(self.outbox.snapshot()['queue'][0]['state'],'received')
        self.assertEqual(sum(r['type']=='claim' for r in self.outbox.records()),1)

    def test_listener_lifecycle_is_separate_from_queue(self):
        self.outbox.heartbeat('one');self.assertTrue(self.outbox.listener_status()['online'])
        with self.assertRaises(ReviewError):self.outbox.heartbeat('two')
        self.outbox.heartbeat('one',False);self.assertFalse(self.outbox.listener_status()['online'])
        self.decide();self.enqueue();self.assertEqual(self.outbox.snapshot()['queue'][0]['state'],'queued')

    def test_bounded_wait_returns_idle_without_any_model_or_network(self):
        result=self.outbox.wait('local-wait',0);self.assertEqual(result['state'],'idle')
        self.outbox.heartbeat('local-wait',False)

    def test_isolated_cli_wait_and_stop_do_not_depend_on_pythonpath(self):
        entry=HERE.parent/'tools/canvas_notify.py'
        for command,expected in ((['status'],None),(['wait','--owner','TEST-isolated','--timeout','0'],'idle'),
                                 (['stop','--owner','TEST-isolated'],None)):
            result=subprocess.run([sys.executable,'-I','-X','utf8',str(entry),'--project',str(self.root),*command],
                                  capture_output=True,text=True,encoding='utf-8',timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            value=json.loads(result.stdout)
            if expected:self.assertEqual(value['state'],expected)
        self.assertFalse(self.outbox.listener_status()['online'])

    def test_notification_records_preserve_review_and_budget_files(self):
        self.write('approvals/budget.json',[{'limit':600,'used':115.15}])
        self.decide();before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        claim=self.outbox.enqueue({'requestId':'n','batchFingerprint':self.outbox.snapshot()['batchFingerprint']})
        self.assertTrue(claim['queued'])
        for path,raw in before.items():self.assertEqual(Path(path).read_bytes(),raw)

    def test_http_notification_is_same_origin_scoped_and_does_not_send(self):
        self.decide()
        http=server.Server(('127.0.0.1',0),server.Library(self.data))
        threading.Thread(target=http.serve_forever,daemon=True).start()
        self.addCleanup(http.server_close);self.addCleanup(http.shutdown)
        origin=f'http://127.0.0.1:{http.server_port}'
        def request(method,path,body=None,headers=None):
            connection=HTTPConnection('127.0.0.1',http.server_port,timeout=3)
            connection.request(method,path,body,headers or {});response=connection.getresponse()
            status=response.status;head=dict(response.getheaders());result=json.loads(response.read());connection.close();return status,head,result
        _,headers,snapshot=request('GET','/api/snapshot');project=snapshot['projects'][0]
        body=json.dumps({'project':project['key'],'notification':{'requestId':'http-batch','batchFingerprint':project['reviews']['notifications']['batchFingerprint']}})
        headers={'Content-Type':'application/json','Origin':origin,'X-Canvas-Review':headers['X-Canvas-Review']}
        self.assertEqual(request('POST','/api/review-notifications',body,{**headers,'Origin':'https://wrong.invalid'})[0],403)
        self.assertEqual(request('POST','/api/review-notifications',body,headers)[0],200)
        self.assertEqual(self.outbox.snapshot()['queue'][0]['state'],'queued')
        self.assertFalse(self.outbox.snapshot()['listener']['online'])


if __name__=='__main__':unittest.main(verbosity=2)
