"""Offline synthetic evidence only; never a real viewing or user decision."""
import json
from pathlib import Path
import tempfile
import unittest

import shot_context as c


def handoff_for(context):
    state={key:'SYNTHETIC ONLY：用于隔离测试的具体状态描述。' for key in c.DIMENSIONS}
    prior=[]
    for source in context['upstream']:
        actual=source['actual']
        prior.append({'clip':source['clip'],'basis':'actual' if actual else 'planned',
            'reviewId':actual['reviewId'] if actual else None,'state':state.copy(),
            'evidence':[actual['path']] if actual else ['clips.json']})
    mode=('continuous' if context['current'].get('continue_from') else
          'opening' if not prior else 'cut' if all(s['actual'] for s in context['upstream']) else 'independent')
    prompt=context['current']['prompt']; following=context['next']
    after={'clip':following['clip'] if following else None,'basis':'target',
           'transition':'SYNTHETIC ONLY：说明片尾如何交给下一段或收束。',
           'state':state.copy(),'evidence':[prompt]}
    if following:
        if following['actual']:
            after['evidence'].append(following['actual']['path']);after['reviewId']=following['actual']['reviewId']
        else:after['evidence'].append(following['manifest'].get('prompt') or 'clips.json')
    return {'contextFingerprint':context['fingerprint'],'previous':prior,
        'current':{'mode':mode,'reason':'SYNTHETIC ONLY：具体说明切镜/独立制作与动作省略的依据。',
                   'state':state.copy(),'evidence':[prompt]},'next':after}


class ShotContext(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.put('project.json',{'title':'SYNTHETIC ONLY'})
        self.put('assets.json',[]);self.put('voices.json',[])
        self.clips=[{'id':f'EP001/Clip0000{n}','prompt':f'{n}.txt','status':'planned'} for n in (1,2,3)]
        self.put('clips.json',self.clips)
        for n in (1,2,3):(self.root/f'{n}.txt').write_text(f'SYNTHETIC prompt {n}')
        self.items=[]

    def put(self,name,value):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')

    def context(self,number=2):return c.snapshot(self.root,f'EP001/Clip0000{number}',self.items)

    def adopt(self,number):
        path=f'{number}.mp4';(self.root/path).write_bytes(b'SYNTHETIC VIDEO FILE')
        self.clips[number-1].update(status='approved',video=path)
        self.put('clips.json',self.clips)
        self.items.append({'id':f'result{number}','subject':self.clips[number-1]['id'],'kind':'video',
            'status':'approved','canApprove':True,'files':[{'path':path,'sha256':c.sha(self.root/path)}]})

    def validate(self,context=None,handoff=None,number=2):
        context=context or self.context(number)
        return c.validate_handoff(self.root,context,handoff or handoff_for(context),f'{number}.txt')

    def test_reads_current_neighbors_and_does_not_write_production(self):
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        s=self.context();self.assertEqual(s['previous'],'EP001/Clip00001')
        self.assertEqual(s['next']['clip'],'EP001/Clip00003')
        self.validate(s);self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})

    def test_required_state_file_missing_blocks(self):
        (self.root/'voices.json').unlink()
        with self.assertRaisesRegex(ValueError,'状态文件缺失'):self.context()

    def test_all_three_sections_and_five_dimensions_required(self):
        s=self.context()
        for field in ('previous','current','next'):
            h=handoff_for(s);del h[field]
            with self.subTest(field=field),self.assertRaises(ValueError):self.validate(s,h)
        for field in c.DIMENSIONS:
            h=handoff_for(s);del h['current']['state'][field]
            with self.subTest(field=field),self.assertRaises(ValueError):self.validate(s,h)

    def test_placeholder_or_empty_handoff_cannot_be_signed(self):
        s=self.context();h=handoff_for(s);h['next']['state']['props']='待补道具状态'
        with self.assertRaises(ValueError):self.validate(s,h)

    def test_actual_upstream_requires_exact_adopted_video_and_review(self):
        self.adopt(1);s=self.context();self.validate(s)
        for change in ({'basis':'planned'},{'reviewId':'other'},{'evidence':['1.txt']}):
            h=handoff_for(s);h['previous'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):self.validate(s,h)

    def test_independent_cut_can_be_planned_without_uploading_tail(self):
        s=self.context();self.assertEqual(handoff_for(s)['current']['mode'],'independent')
        self.validate(s)
        h=handoff_for(s);h['current']['mode']='cut'
        with self.assertRaisesRegex(ValueError,'素材尚未就绪'):self.validate(s,h)

    def test_continuous_join_cannot_be_relabeled_independent(self):
        self.clips[1]['continue_from']='EP001/Clip00001';self.put('clips.json',self.clips)
        s=self.context();h=handoff_for(s);h['current']['mode']='independent'
        with self.assertRaisesRegex(ValueError,'continue_from'):self.validate(s,h)
        with self.assertRaisesRegex(ValueError,'素材尚未就绪'):self.validate(s)

    def test_first_and_last_clips_have_explicit_boundary(self):
        self.validate(number=1);self.validate(number=3)
        s=self.context();h=handoff_for(s);h['current']['mode']='opening'
        with self.assertRaises(ValueError):self.validate(s,h)

    def test_context_changes_when_adjacent_prompt_voice_or_review_changes(self):
        self.adopt(1);old=self.context();h=handoff_for(old)
        (self.root/'3.txt').write_text('SYNTHETIC new next opening')
        with self.assertRaisesRegex(ValueError,'状态读取'):self.validate(self.context(),h)
        old=self.context();self.items[0]['status']='pending'
        self.assertNotEqual(old['fingerprint'],self.context()['fingerprint'])
        old=self.context();self.put('voices.json',[{'character':'C00001','file':'missing.wav'}])
        self.assertNotEqual(old['fingerprint'],self.context()['fingerprint'])

    def test_adoption_not_synced_is_not_treated_as_no_actual_source(self):
        self.adopt(1);self.clips[0]['status']='generated';self.put('clips.json',self.clips)
        with self.assertRaisesRegex(ValueError,'尚未同步'):self.validate()

    def test_future_target_must_not_be_claimed_as_observed_result(self):
        s=self.context();h=handoff_for(s);h['next']['basis']='actual'
        with self.assertRaisesRegex(ValueError,'不能预标'):self.validate(s,h)

    def test_already_generated_next_clip_requires_reverse_check(self):
        self.adopt(3);s=self.context();self.validate(s)
        h=handoff_for(s);h['next']['evidence']=['2.txt','3.txt']
        with self.assertRaisesRegex(ValueError,'反向核对'):self.validate(s,h)

    def test_next_planned_opening_must_be_read(self):
        s=self.context();h=handoff_for(s);h['next']['evidence']=['2.txt']
        with self.assertRaisesRegex(ValueError,'读取下段规划'):self.validate(s,h)

    def test_changed_sequence_invalidates_saved_handoff(self):
        old=self.context();self.clips.reverse();self.put('clips.json',self.clips)
        with self.assertRaisesRegex(ValueError,'状态读取'):self.validate(self.context(),handoff_for(old))

    def test_evidence_cannot_escape_project(self):
        s=self.context();h=handoff_for(s);h['current']['evidence']=['../foreign.txt']
        with self.assertRaisesRegex(ValueError,'越出'):self.validate(s,h)


if __name__=='__main__':unittest.main()
