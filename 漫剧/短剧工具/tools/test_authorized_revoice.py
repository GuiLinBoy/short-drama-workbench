"""Offline tests: exact user-authorized replacement of an unadopted voice candidate."""
import copy
import unittest
from unittest.mock import patch

import voice_readiness as gate
import voice_lifecycle as lifecycle
import test_voice_lifecycle as fixtures


class AuthorizedRevoice(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.VoiceCloseout();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.register();fixture.submit('changes_requested')
        self.root=fixture.root;self.put=fixture.put;self.items=fixture.items
        self.sample=fixture.sample;self.voice=fixture.voice
        self.sample.update(fingerprint='sample-content',head='actual-rejection')
        self.request={'params':{'duration':15,'resolution':'768P','mute_audio':False},
                      'references':[],'prompt':'新声音，普通话对白'}
        self.request_path=self.root/'requests/new.json';self.put('requests/new.json',self.request)
        self.selected={'id':'new-prompt','kind':'video_prompt','subject':'EP001/Clip00001',
                       'status':'approved','canApprove':True,'request':{'path':'requests/new.json'},
                       'generation':{'tool':'h3','batch':'new','taskDir':'tasks/new/attempt-001'}}
        self.entry={'request':'requests/new.json','requestSHA256':gate._sha(self.request_path),
                    'clip':'EP001/Clip00001','character':'C00001','batch':'new',
                    'taskDir':'tasks/new/attempt-001','duration':15,'resolution':'768P',
                    'authorization':'authorization.txt','rejectedSampleReviewId':self.sample['id'],
                    'rejectedSampleFingerprint':'sample-content','rejectedSampleHead':'actual-rejection',
                    'sourceVideoReviewId':fixture.result['id']}
        self.auth={k:v for k,v in self.entry.items() if k not in ('authorization',)}
        self.auth.update(status='USER_APPROVED_CODEX_MEDIA_EXECUTION',approved=True,workId='synthetic',
                         projectRoot=str(self.root),userQuote='按原描述重新生成，不引用旧声音',
                         voiceDecision='regenerate_without_reference')
        self.cfg={'version':1,'planReviewId':'plan','snapshotFile':'policy.txt',
                  'requiredVoices':{'EP001/Clip00001':['C00001']},'voiceReviewIds':{},
                  'bootstrapRequests':[],'revoiceRequests':[self.entry]}
        self.plan={'id':'plan','kind':'plan','status':'approved','canApprove':True,'files':[]}
        self.items.extend([self.plan,self.selected]);self.bind()
        mocked=patch.object(gate,'_review_items',lambda root:self.items)
        mocked.start();self.addCleanup(mocked.stop)

    def bind(self):
        self.put('authorization.txt',self.auth)
        self.entry['authorizationSHA256']=gate._sha(self.root/'authorization.txt')
        self.put('policy.txt',self.cfg)
        self.put('协作/声音方案/执行规则-v001.json',self.cfg)
        self.plan['files']=[{'path':p,'sha256':gate._sha(self.root/p)} for p in ('policy.txt','authorization.txt')]

    def check(self):
        return gate.require_voice_readiness(self.root,self.request_path,self.root/'tasks/new/attempt-001','new')

    def test_exact_rejected_candidate_revoice_passes(self):
        self.assertEqual(self.check()['action'],'explicit_revoice')
        with self.assertRaises(ValueError):lifecycle.require_first_voice(self.root,'C00001',self.items)

    def test_no_explicit_revoice_authorization_still_blocks(self):
        del self.auth['voiceDecision'];self.bind()
        with self.assertRaisesRegex(ValueError,'用户'):self.check()

    def test_each_bound_scope_field_blocks_mismatch(self):
        for key in ('requestSHA256','clip','batch','taskDir','duration','resolution','character'):
            with self.subTest(key=key):
                old=self.entry[key];self.entry[key]='different';self.bind()
                with self.assertRaises(ValueError):self.check()
                self.entry[key]=old
        self.bind()

    def test_authorization_cannot_come_from_another_project(self):
        self.auth['projectRoot']='another-project';self.bind()
        with self.assertRaises(ValueError):self.check()

    def test_sample_must_have_same_actual_rejection(self):
        for key,value in (('status','pending'),('status','approved'),('head','new-decision'),('fingerprint','changed')):
            with self.subTest(key=key,value=value):
                old=self.sample[key];self.sample[key]=value
                with self.assertRaises(ValueError):self.check()
                self.sample[key]=old

    def test_adopted_voice_cannot_be_silently_replaced(self):
        self.voice['status']='approved';self.put('voices.json',[self.voice])
        with self.assertRaisesRegex(ValueError,'已采用'):self.check()

    def test_reference_audio_video_or_strict_frames_block(self):
        for role in ('reference_audio','reference_video','first_frame','last_frame'):
            with self.subTest(role=role):
                req=copy.deepcopy(self.request);req['references']=[{'role':role}]
                self.put('requests/new.json',req)
                self.entry['requestSHA256']=gate._sha(self.request_path);self.auth['requestSHA256']=self.entry['requestSHA256'];self.bind()
                with self.assertRaisesRegex(ValueError,'普通图片'):self.check()

    def test_other_character_incomplete_closeout_still_blocks(self):
        report=lifecycle.safe_audit(self.root,self.items)
        report['characters'].append({'character':'C00002','preparationComplete':False,'state':'extract'})
        with patch.object(lifecycle,'safe_audit',return_value=report):
            with self.assertRaisesRegex(ValueError,'其他人物'):self.check()

    def test_plan_files_must_bind_authorization_and_policy(self):
        self.plan['files'].pop()
        with self.assertRaisesRegex(ValueError,'已审方案'):self.check()

    def test_authorization_file_change_invalidates_gate(self):
        self.put('authorization.txt',{'changed':True})
        with self.assertRaisesRegex(ValueError,'已变化'):self.check()

    def test_source_approval_cannot_be_discarded(self):
        self.items[1]['status']='changes_requested'
        with self.assertRaisesRegex(ValueError,'原视频'):self.check()

    def test_bootstrap_cannot_be_combined_with_revoice(self):
        self.cfg['bootstrapRequests']=[{'request':'requests/new.json'}];self.bind()
        with self.assertRaisesRegex(ValueError,'冒充'):self.check()


if __name__=='__main__':unittest.main()
