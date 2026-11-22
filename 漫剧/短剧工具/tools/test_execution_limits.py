"""Project-only paid submission caps. All files and calls are isolated/offline."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))


def policy():
    return {'version':1,'caps':{'total_points':600,'h3_requests':9,'tts_requests':5,'tts_characters':300,'images':6,
                             'video_revisions':2,'revisions_per_clip':1},'allowed_resolutions':['480P','768P'],
            'retry_unknown':False,'revision_batch':'EP001-重做'}


class ExecutionCaps(unittest.TestCase):
    def setUp(self):
        self.module=importlib.import_module('execution_limits')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'作品/A'
        (self.root/'approvals').mkdir(parents=True)
        self.config=self.root/'approvals/execution-limits.json'
        self.config.write_text(json.dumps(policy()),encoding='utf-8')

    def reserve(self,name,kind='video',points=45,**extra):
        task=self.root/'tasks'/name/'attempt-001'
        state={'provider':{'video':'minimax-h3','speech':'compshare-indextts','image':'nexorai'}[kind],
               'requested_resolution':'480P','submitted_characters':extra.pop('characters',0),'request_sha256':name}
        state.update(extra.pop('state',{}))
        return self.module.reserve(task,state,kind,points,{'images':extra.pop('images',1)},extra.pop('batch','B'))

    def counts(self):return json.loads((self.root/'运行记录/执行限额计数.json').read_text(encoding='utf-8'))

    def test_h3_ninth_allowed_tenth_refused(self):
        for i in range(9):self.reserve(str(i))
        with self.assertRaises(SystemExit):self.reserve('10')
        self.assertEqual(self.counts()['totals']['h3_requests'],9)

    def test_tts_character_limit_and_request_limit(self):
        self.reserve('a','speech',1,characters=200)
        self.reserve('b','speech',1,characters=100)
        with self.assertRaises(SystemExit):self.reserve('c','speech',1,characters=1)
        self.assertEqual(self.counts()['totals']['tts_characters'],300)
        for i in range(3):self.reserve('empty'+str(i),'speech',.01)
        with self.assertRaises(SystemExit):self.reserve('sixth','speech',.01)

    def test_failed_attempts_are_not_freed_and_estimates_share_points_cap(self):
        self.reserve('a',points=400)
        self.reserve('b','speech',points=200)
        with self.assertRaises(SystemExit):self.reserve('c',points=1)
        self.assertEqual(self.counts()['totals']['reserved_points'],600)

    def test_images_count_requested_n_including_failed_attempts(self):
        for i in range(3):self.reserve(str(i),'image',points=2,images=2)
        with self.assertRaises(SystemExit):self.reserve('four','image',images=1)
        self.assertEqual(self.counts()['totals']['images'],6)

    def test_same_reservation_is_idempotent_but_modified_one_is_rejected(self):
        self.reserve('a');before=self.counts()
        self.reserve('a');self.assertEqual(before,self.counts())
        with self.assertRaises(SystemExit):self.reserve('a',points=46)

    def test_new_scope_refuses_unknown_repost_and_higher_resolution(self):
        task=self.root/'tasks/a/attempt-001'
        with self.assertRaises(SystemExit):self.module.check_retry(task,True)
        with self.assertRaises(SystemExit):self.reserve('4k',state={'requested_resolution':'4K'})
        self.assertFalse((self.root/'运行记录/执行限额计数.json').exists())

    def test_revision_cap_per_clip_and_overall(self):
        self.reserve('EP001-Clip00001-final',batch='EP001-重做')
        with self.assertRaises(SystemExit):self.reserve('EP001-Clip00001-draft',batch='EP001-重做')
        self.reserve('EP001-Clip00002-final',batch='EP001-重做')
        with self.assertRaises(SystemExit):self.reserve('EP001-Clip00003-final',batch='EP001-重做')

    def test_corrupt_counter_or_policy_is_not_cleared(self):
        self.reserve('a');path=self.root/'运行记录/执行限额计数.json';path.write_text('broken',encoding='utf-8')
        with self.assertRaises(SystemExit):self.reserve('b')
        self.assertEqual(path.read_text(encoding='utf-8'),'broken')
        self.config.write_text('{"version":99}',encoding='utf-8')
        with self.assertRaises(SystemExit):self.reserve('c')

    def test_legacy_projects_without_policy_are_unchanged(self):
        self.config.unlink();self.reserve('a')
        self.assertFalse((self.root/'运行记录/执行限额计数.json').exists())

    def test_valid_json_with_wrong_summary_is_rejected(self):
        self.reserve('a');path=self.root/'运行记录/执行限额计数.json'
        data=self.counts();data['totals']['h3_requests']=0
        path.write_text(json.dumps(data),encoding='utf-8');before=path.read_bytes()
        with self.assertRaises(SystemExit):self.reserve('b')
        self.assertEqual(before,path.read_bytes())

    def test_missing_counter_with_existing_task_is_not_reinitialized(self):
        path=self.root/'tasks/a/attempt-001/state.json';path.parent.mkdir(parents=True)
        path.write_text('{}',encoding='utf-8')
        with self.assertRaises(SystemExit):self.reserve('b')
        self.assertFalse((self.root/'运行记录/执行限额计数.json').exists())

    def test_batch_rules_prevent_unapproved_duration_and_model(self):
        data=policy();data['batch_rules']={'B':{'kind':'video','durations':[8],'resolutions':['480P']},
                                         'I':{'kind':'image','models':['approved']}}
        self.config.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaises(SystemExit):self.reserve('too-long',state={'requested_duration':30})
        with self.assertRaises(SystemExit):self.reserve('other-model','image',batch='I',state={'model':'other'})
        self.reserve('valid',state={'requested_duration':8})

    def test_speech_uses_conservative_per_request_reservation(self):
        data=policy();data.update(tts_unit_price=.01,tts_reservation_per_request=3)
        self.config.write_text(json.dumps(data),encoding='utf-8')
        self.reserve('a','speech',.1,characters=12,state={'billable_characters':10})
        self.assertEqual(self.counts()['totals']['reserved_points'],3)
        with self.assertRaises(SystemExit):self.reserve('b','speech',.01,characters=12,state={'billable_characters':10})

    def test_parallel_contenders_share_last_slot(self):
        cap=policy();cap['caps']['h3_requests']=1
        self.config.write_text(json.dumps(cap),encoding='utf-8')
        processes=[subprocess.Popen([sys.executable,'-I',__file__,'--worker',str(self.root),str(i)],
                                    stdout=subprocess.PIPE,stderr=subprocess.PIPE) for i in range(3)]
        codes=[p.wait(timeout=20) for p in processes]
        self.assertEqual(codes.count(0),1,codes)
        self.assertEqual(self.counts()['totals']['h3_requests'],1)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--worker':
        import execution_limits
        root=Path(sys.argv[2]);name=sys.argv[3]
        execution_limits.reserve(root/'tasks'/name/'attempt-001',{'provider':'minimax-h3','requested_resolution':'480P'},'video',45,{},'B')
    else:unittest.main(verbosity=2)
