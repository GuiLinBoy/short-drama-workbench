import importlib
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))


class WaitGeneration(unittest.TestCase):
    def setUp(self):
        self.module=importlib.import_module('wait_generation')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'project';self.task=self.root/'tasks/T/attempt-001';self.task.mkdir(parents=True)
        self.state={'status':'submitted','task_id':'fixture-task-id'};self.save()
        self.elapsed=0;self.queries=0

    def save(self):
        if self.state.get('status')=='succeeded':
            output=self.root/'fixture-delivery.mp4';output.write_bytes(b'fixture only')
            self.state['downloaded_to']=str(output)
        (self.task/'state.json').write_text(json.dumps(self.state),encoding='utf-8')

    def clock(self):return self.elapsed
    def sleep(self,seconds):self.elapsed+=seconds
    def run_wait(self,query,seconds=601):
        return self.module.wait_for_generation(self.root,'h3',self.task,seconds,query,self.sleep,self.clock)

    def test_pending_then_delivery_waits_without_resubmitting(self):
        def query():
            self.queries+=1
            if self.queries==3:self.state['status']='succeeded';self.save()
            return 0
        result=self.run_wait(query)
        self.assertEqual(result['status'],'DELIVERED')
        self.assertEqual(self.queries,3);self.assertEqual(self.elapsed,600)
        self.assertEqual(result['create_requests'],0)

    def test_temporary_query_failure_keeps_waiting(self):
        def query():
            self.queries+=1
            if self.queries==1:return 2
            self.state['status']='succeeded';self.save();return 0
        result=self.run_wait(query)
        self.assertEqual(result['status'],'DELIVERED');self.assertEqual(self.elapsed,300)

    def test_download_failure_after_success_is_not_mistaken_for_delivery(self):
        def query():
            self.queries+=1;self.state['status']='succeeded';self.save()
            return 1 if self.queries==1 else 0
        result=self.run_wait(query)
        self.assertEqual(self.queries,2);self.assertEqual(result['status'],'DELIVERED')

    def test_deadline_keeps_task_and_does_not_report_failure(self):
        before=(self.task/'state.json').read_bytes()
        result=self.run_wait(lambda:0,seconds=301)
        self.assertEqual(result['status'],'STILL_PENDING')
        self.assertEqual(result['task_id'],'fixture-task-id')
        self.assertEqual((self.task/'state.json').read_bytes(),before)

    def test_unknown_without_id_never_sends_query_or_post(self):
        self.state.update(status='unknown',task_id=None);self.save()
        result=self.run_wait(lambda:self.fail('must not query without id'))
        self.assertEqual(result['status'],'UNKNOWN_SUBMISSION')
        self.assertEqual(result['create_requests'],0)

    def test_terminal_failure_is_reported_and_scope_escape_refused(self):
        self.state['status']='failed';self.save()
        result=self.run_wait(lambda:0)
        self.assertEqual(result['status'],'TERMINAL_FAILURE')
        with self.assertRaises(SystemExit):self.module.wait_for_generation(self.root,'h3',self.root.parent/'other',10,lambda:0,self.sleep,self.clock)

    def test_delivered_image_is_verified_locally_without_fetch_repost(self):
        image=self.root/'image.png';image.write_bytes(b'fixture only')
        self.state={'status':'succeeded','charge':{'images':1},'deliveries':[{'status':'delivered',
            'delivered':'image.png','sha256':hashlib.sha256(image.read_bytes()).hexdigest()}]};self.save()
        result=self.module.wait_for_generation(self.root,'image',self.task,10,lambda:self.fail('no fetch needed'),self.sleep,self.clock)
        self.assertEqual(result['status'],'DELIVERED');self.assertEqual(result['create_requests'],0)

    def test_image_recompose_is_known_non_delivery_not_unknown_submission(self):
        self.state={'status':'needs_recompose'};self.save()
        result=self.module.wait_for_generation(self.root,'image',self.task,10,lambda:self.fail('must not fetch'),self.sleep,self.clock)
        self.assertEqual(result['status'],'TERMINAL_FAILURE')


if __name__=='__main__':unittest.main(verbosity=2)
