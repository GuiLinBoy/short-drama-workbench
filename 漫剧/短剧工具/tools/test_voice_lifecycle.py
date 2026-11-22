"""Synthetic fixtures only: voice closeout never invokes providers or approves media."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import voice_lifecycle as v
import voice_readiness as gate
import pipeline_state as state


class VoiceCloseout(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='voice-closeout-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = [{'id':'C00001-A','name':'张姨'}, {'id':'C00002-A','name':'王叔'}]
        self.put('assets.json',self.assets)
        self.put('clips.json',[{'id':'EP001/Clip00001','status':'generated','video':'source.mp4'}])
        self.video = self.root/'source.mp4'; self.video.write_bytes(b'SYNTHETIC VIDEO PROOF ONLY')
        self.request = {'prompt':'integrated_multimodal_description:\n张姨 (S1) 说：<d>[Chinese] 我闺女买的，一万多！</d>\n王叔在旁边闭口。', 'params':{'mute_audio':False}}
        self.prompt = {'id':'prompt','kind':'video_prompt','subject':'EP001/Clip00001','status':'approved','canApprove':True,'request':self.request,'dependsOn':[]}
        self.result = {'id':'result','kind':'video','subject':'EP001/Clip00001','status':'approved','canApprove':True,
                       'submittedAt':'2026-11-15T01:00:00+00:00','dependsOn':['prompt'],
                       'files':[{'path':'source.mp4','sha256':v.sha(self.video)}]}
        self.items = [self.prompt,self.result]

    def put(self,name,value):
        path = self.root/name; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')

    def register(self,status='generated',seconds=3.8):
        path = self.root/'音色/C00001.wav'; path.parent.mkdir(exist_ok=True)
        with wave.open(str(path),'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
            wav.writeframes(b'\0\0'*int(seconds*16000))
        self.voice = {'character':'C00001','file':'音色/C00001.wav','source_clip':'EP001/Clip00001',
                      'start':0,'end':seconds,'text':'仅用于合成测试','forms':['C00001-A'],'status':status,
                      'fingerprint':{'file':v.sha(path)}}
        self.put('voices.json',[self.voice])

    def submit(self,status='pending'):
        self.sample = {'id':'voice-sample','kind':'audio','subject':'C00001','status':status,'canApprove':True,
                       'submittedAt':'2026-11-15T02:00:00+00:00','dependsOn':['result'],
                       'files':[{'path':self.voice['file'],'sha256':self.voice['fingerprint']['file']}]}
        self.items.append(self.sample)

    def audit(self): return v.audit(self.root,self.items)
    def row(self): return self.audit()['characters'][0]

    def test_canvas_approval_triggers_before_manifest_adoption_and_only_for_actual_speaker(self):
        report = self.audit()
        self.assertEqual([r['character'] for r in report['characters']],['C00001'])
        self.assertEqual(report['pendingProduction'],['C00001'])
        self.assertEqual(self.row()['state'],'extract')
        with self.assertRaisesRegex(ValueError,'收尾未完成'):v.require_prepared(self.root,self.items)

    def test_unapproved_video_and_silent_video_do_not_trigger(self):
        self.result['status']='pending'; self.assertEqual(self.audit()['characters'],[])
        self.result['status']='approved';self.request['prompt']='全程无人说话，所有人双唇闭合。'
        self.assertTrue(self.audit()['complete'])

    def test_registration_does_not_replace_submission(self):
        self.register();self.assertEqual(self.row()['state'],'submit')
        with self.assertRaises(ValueError):v.require_prepared(self.root,self.items)

    def test_pending_sample_completes_local_work_but_not_voice_adoption(self):
        self.register();self.submit()
        report=v.require_prepared(self.root,self.items)
        self.assertTrue(report['preparationComplete']);self.assertFalse(report['complete'])
        self.assertEqual(report['awaitingConfirmation'],['C00001'])
        self.assertEqual(self.row()['sampleReviewId'],'voice-sample')

    def test_approval_requires_manifest_adoption_then_closeout_completes(self):
        self.register();self.submit('approved');self.assertEqual(self.row()['state'],'adopt')
        with self.assertRaises(ValueError):v.require_prepared(self.root,self.items)
        self.voice['status']='approved';self.put('voices.json',[self.voice])
        self.assertTrue(v.require_prepared(self.root,self.items)['complete'])

    def test_changed_audio_rejected_sample_or_missing_source_dependency_blocks(self):
        self.register();self.submit('approved');self.sample['dependsOn']=[]
        self.assertEqual(self.row()['state'],'submit')
        self.sample['dependsOn']=['result'];self.sample['status']='changes_requested'
        self.assertFalse(self.row()['preparationComplete'])
        (self.root/self.voice['file']).write_bytes(b'changed')
        self.assertEqual(self.row()['state'],'register')

    def test_short_sample_is_explicit_and_not_padded_or_silently_approved(self):
        self.register(seconds=1.3);self.submit()
        row=self.row();self.assertEqual(row['seconds'],1.3);self.assertTrue(row['shortSample'])
        self.assertEqual(row['state'],'confirm');self.assertFalse(row['complete'])

    def test_first_voice_exception_is_refused_after_accepted_speech_even_without_registry(self):
        with self.assertRaisesRegex(ValueError,'不得再次使用首次定音'):v.require_first_voice(self.root,'C00001',self.items)
        v.require_first_voice(self.root,'C00002',self.items)

    def test_first_voice_guard_is_also_run_by_submission_gate(self):
        self.register();self.submit()
        request={'prompt':'新片段','references':[],'params':{'mute_audio':False,'duration':4,'resolution':'768P'}}
        self.put('requests/new.json',request)
        task='tasks/new/attempt-001';batch='SYNTHETIC'
        cfg={'version':1,'planReviewId':'plan','requiredVoices':{'EP001/Clip00002':['C00001']},'voiceReviewIds':{},
             'bootstrapRequests':[{'request':'requests/new.json','requestSHA256':v.sha(self.root/'requests/new.json'),
                 'taskDir':task,'batch':batch,'clip':'EP001/Clip00002','character':'C00001','duration':4,'resolution':'768P'}]}
        self.put('协作/声音方案/执行规则-v001.json',cfg)
        self.items += [{'id':'plan','status':'approved','canApprove':True},
                       {'id':'next','subject':'EP001/Clip00002','status':'approved','canApprove':True,
                        'request':{'path':'requests/new.json'},'generation':{'tool':'h3','batch':batch,'taskDir':task}}]
        with patch.object(gate,'_review_items',return_value=self.items),self.assertRaisesRegex(ValueError,'不得再次使用首次定音'):
            gate.require_voice_readiness(self.root,self.root/'requests/new.json',self.root/task,batch)

    def test_ambiguous_approved_dialogue_cannot_disappear_from_checks(self):
        self.request['prompt']='陌生人 (S1) 说：<d>[Chinese] 我来了。</d>'
        self.assertFalse(self.audit()['preparationComplete'])
        with self.assertRaises(ValueError):v.require_first_voice(self.root,'C00001',self.items)

    def test_readonly_and_new_project_isolation(self):
        self.register();self.submit()
        before={p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.audit()
        self.assertEqual(before,{p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        fresh=self.root/'new-project';fresh.mkdir()
        self.assertEqual(v.audit(fresh,[])['characters'],[])
        self.assertFalse((fresh/'voices.json').exists())

    def test_full_reference_speaker_mapping_does_not_count_listener(self):
        self.request['prompt']='subject_definitions:\n<Subject 1> 张姨，唯一说话人(S1)。\n<Subject 2> 王叔，安静听。\ndetailed_description:\n王叔闭口；<Subject 1> (S1) 说：<d>[Chinese] 我来了。</d>'
        self.assertEqual([r['character'] for r in self.audit()['characters']],['C00001'])

    def test_later_subject_actions_do_not_overwrite_identity_definition(self):
        self.request['prompt']='subject_definitions:\n<Subject 1> 张姨，说话人(S1)。\n<Subject 2> 王叔，安静听。\nretention_analysis:\n<Subject 1>（[Shot 1]）：fully_preserved - 身份服装。\ndetailed_description:\n<Subject 1>朝向<Subject 2>，不看镜头。\n0—2秒，<Subject 1> (S1) 说：<d>[Chinese] 我来了。</d>'
        self.assertEqual([r['character'] for r in self.audit()['characters']],['C00001'])

    def test_state_writer_returns_real_pending_closeout_without_changing_approval(self):
        self.put('clips.json',[{'id':'EP001/Clip00001','status':'generated','video':'source.mp4','fingerprint':{'video':v.sha(self.video)}}])
        with patch.object(v,'audit',side_effect=lambda root,items=None,episode=None:v_audit(root,self.items)):
            result=state.mutate(self.root,'总导演','clips','set','EP001/Clip00001',None,[('status','approved')])
        self.assertTrue(result['ok']);self.assertEqual(result['voice_closeout']['characters'][0]['state'],'extract')
        self.assertEqual(json.loads((self.root/'clips.json').read_text()) [0]['status'],'approved')


v_audit=v.audit
if __name__=='__main__':unittest.main()
