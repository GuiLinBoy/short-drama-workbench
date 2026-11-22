import unittest,tempfile,json,hashlib
from pathlib import Path
from unittest.mock import patch
import voice_readiness as g
import pipeline_state as state

class VoiceGate(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  (self.root/'协作/声音方案').mkdir(parents=True);(self.root/'requests').mkdir();(self.root/'音色').mkdir()
  self.req=self.root/'requests/a.json';self.task=self.root/'tasks/a/attempt-001';self.voice=self.root/'音色/a.wav';self.voice.write_bytes(b'actual-file-fingerprint')
  self.cfg={'version':1,'planReviewId':'plan','requiredVoices':{'EP001/Clip00003':['C00001']},'bootstrapRequests':[],'voiceReviewIds':{'C00001':'sample'}}
  self.request={'params':{'mute_audio':False,'duration':9,'resolution':'480P'},'references':[{'role':'reference_audio','path':str(self.voice),'sha256':g._sha(self.voice),'label':'C00001音色参考','tag':'<Audio 1>'}],'prompt':'<Audio 1>'}
  self.voices=[{'character':'C00001','file':'音色/a.wav','status':'approved','fingerprint':{'file':g._sha(self.voice)}}]
  self.reviews=[{'id':'plan','status':'approved','canApprove':True},{'id':'request','subject':'EP001/Clip00003','status':'approved','canApprove':True,'request':{'path':'requests/a.json'},'generation':{'tool':'h3','batch':'batch','taskDir':'tasks/a/attempt-001'}},{'id':'sample','kind':'audio','subject':'C00001','status':'approved','canApprove':True,'files':[{'path':'音色/a.wav','sha256':g._sha(self.voice)}]}]
  self.patch=patch.object(g,'_review_items',lambda root:self.reviews);self.patch.start();self.addCleanup(self.patch.stop)
 def check(self):
  for p,v in [(self.root/'协作/声音方案/执行规则-v001.json',self.cfg),(self.req,self.request),(self.root/'voices.json',self.voices)]:p.write_text(json.dumps(v),encoding='utf-8')
  return g.require_voice_readiness(self.root,self.req,self.task,'batch')
 def test_complete_bound_voice_passes(self):self.check()
 def test_plan_unapproved_blocks(self):
  self.reviews[0]['status']='pending'
  with self.assertRaises(ValueError):self.check()
 def test_missing_audio_blocks(self):
  self.request['references']=[]
  with self.assertRaises(ValueError):self.check()
 def test_unapproved_voice_blocks(self):
  self.voices[0]['status']='generated'
  with self.assertRaises(ValueError):self.check()
 def test_changed_voice_blocks(self):
  self.voice.write_bytes(b'changed')
  with self.assertRaises(ValueError):self.check()
 def test_strict_frame_cannot_bypass_audio(self):
  self.request['references'].append({'role':'first_frame'})
  with self.assertRaises(ValueError):self.check()
 def test_exact_bootstrap_request_only(self):
  self.voices=[]
  self.request['references']=[];self.request['prompt']='定音'
  self.req.write_text(json.dumps(self.request),encoding='utf-8')
  self.cfg['bootstrapRequests']=[{'request':'requests/a.json','requestSHA256':g._sha(self.req),'taskDir':'tasks/a/attempt-001','batch':'batch','clip':'EP001/Clip00003','character':'C00001','duration':9,'resolution':'480P'}]
  self.check();self.request['params']['duration']=10
  with self.assertRaises(ValueError):self.check()
 def test_unconfigured_projects_cannot_skip_voice_checks(self):
  with self.assertRaisesRegex(ValueError,'缺少本作品声音'):g.require_voice_readiness(self.root,self.req,self.task,'batch')
 def test_plan_with_audio_file_is_not_an_audio_approval(self):
  self.reviews[-1]['kind']='plan'
  with self.assertRaisesRegex(ValueError,'未通过画布'):self.check()
 def test_existing_voice_cannot_be_bootstrapped_again(self):
  self.request['references']=[];self.request['prompt']='定音'
  self.req.write_text(json.dumps(self.request),encoding='utf-8')
  self.cfg['bootstrapRequests']=[{'request':'requests/a.json','requestSHA256':g._sha(self.req),'taskDir':'tasks/a/attempt-001','batch':'batch','clip':'EP001/Clip00003','character':'C00001','duration':9,'resolution':'480P'}]
  with self.assertRaisesRegex(ValueError,'不得再次使用首次定音'):self.check()
 def test_retired_request_cannot_execute(self):
  self.cfg['disabledRequests']=['requests/a.json']
  with self.assertRaisesRegex(ValueError,'停用'):self.check()
 def test_wrong_person_approved_sample_cannot_be_relabelled(self):
  self.reviews[-1]['subject']='C00003'
  with self.assertRaisesRegex(ValueError,'实际通过'):self.check()
 def test_approved_status_without_matching_sample_file_blocks(self):
  self.reviews[-1]['files'][0]['sha256']='different'
  with self.assertRaisesRegex(ValueError,'实际通过'):self.check()
 def test_registered_voice_without_user_sample_review_blocks(self):
  self.cfg['voiceReviewIds']={}
  with self.assertRaisesRegex(ValueError,'未通过画布'):self.check()
 def test_disjoint_provenance_validated(self):
  state.check_values(self.root,{'start':2.15,'end':12.095,'source_segments':[{'start':2.15,'end':4.15},{'start':10.61,'end':12.095}]})
  with self.assertRaises(state.Refused):state.check_values(self.root,{'start':0,'end':10,'source_segments':[{'start':1,'end':5},{'start':4,'end':7}]})

if __name__=='__main__':unittest.main()
