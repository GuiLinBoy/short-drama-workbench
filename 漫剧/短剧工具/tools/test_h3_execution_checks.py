"""Synthetic offline fixtures for the mandatory H3 production pre-submit gate."""
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import wave

import h3_execution_checks as g
from test_shot_context import handoff_for


class ExecutionChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        for name in ('requests','音色','reports','协作/声音方案'):
            (self.root/name).mkdir(parents=True,exist_ok=True)
        self.request=self.root/'requests/current.json'
        self.source=self.root/'source.prompt.txt'
        self.style='现代家庭伦理真人AI短剧，真实肤质与衣料。左侧窗光保持统一。'
        self.text='''subject_definitions:
<Subject 1> 是[S00006]中的王叔，蓝灰开衫。说话编号(S1)，固定声线：中年男性偏低声线。
<Subject 2> 是[S00006]中的表婶，砖红外套。说话编号(S2)，固定声线：中年女性中音。
<Subject 3> 是[S00006]中的大强，仅视觉主体，全段安静听话、双唇自然闭合。
<Subject 4> 是[S00006]中的客厅，仅提供布局和光线。
[C00001音色参考] 仅提供<Subject 1> (S1) 王叔的声线参考，不复制或复述原录音。
[C00002音色参考] 仅提供<Subject 2> (S2) 表婶的声线参考，不复制或复述原录音。

summary:
[reference generation + audio reference] 两人在客厅对话。

retention_analysis:
<Subject 1>（[Shot 1]）：fully_preserved - 身份服装，不限定姿态。
<Subject 2>（[Shot 1]）：fully_preserved - 身份服装，不限定姿态。
<Subject 3>（[Shot 1]）：fully_preserved - 身份服装，不限定姿态。
<Subject 4>（[Shot 1]）：fully_preserved - 空间布局。
[C00001音色参考]：reference - <Subject 1>只参考声线，不复制原录音内容。
[C00002音色参考]：reference - <Subject 2>只参考声线，不复制原录音内容。

detailed_description:
STYLE
全段8秒，时间从本段0秒计起。无字幕或水印。
[Shot 1] 固定平视腰部以上双人中景。<Subject 1>左侧朝右，<Subject 2>右侧朝左，两人的嘴清楚可见，头顶和两侧留白，后景有门框。
开头短暂停顿，两人双唇自然闭合。
0.20—3.20秒，表婶安静听话、双唇自然闭合；<Subject 1> (S1) 王叔使用[C00001音色参考]提供的声线，在画内自然说：<d>[Chinese] 你们怎么说的？</d>
王叔说完立即闭口。
3.60—6.60秒，王叔安静听话、双唇自然闭合；<Subject 2> (S2) 表婶使用[C00002音色参考]提供的声线，在画内自然说：<d>[Chinese] 钱存定期了。</d>
表婶说完立即闭口。
最后约1秒（7—8秒），两人双唇自然闭合，保持手部和道具状态。

overall_soundscape:
室内低弱底噪、自然呼吸和轻微衣料摩擦。无额外人声、无旁白、无配乐。

non_diegetic_music:
N/A
'''.replace('STYLE',self.style)
        self.image=self.root/'composition.png';self.image.write_bytes(b'SYNTHETIC_IMAGE_NOT_REAL_MEDIA')
        self.refs=[dict(label='S00006',path=str(self.image),role='reference',tag='<Picture 1>',sha256=g.sha(self.image))]
        for n in (1,2):
            path=self.root/f'音色/C0000{n}.wav'
            self.wav(path,3.5)
            self.refs.append(dict(label=f'C0000{n}音色参考',path=str(path),role='reference_audio',tag=f'<Audio {n}>',sha256=g.sha(path)))
        self.assets=[dict(id='C00001-A',name='王叔'),dict(id='C00002-A',name='表婶'),dict(id='C00003-A',name='大强')]
        self.clips=[dict(id='EP001/Clip00001',status='approved',prompt='baseline.prompt.txt',shots=[]),
            dict(id='EP001/Clip00003',status='planned',prompt='source.prompt.txt',continue_from=None,
                 shots=[dict(dialogue='王叔：你们怎么说的？\n表婶：钱存定期了。')])]
        self.policy=dict(version=1,requiredVoices={'EP001/Clip00003':['C00001','C00002']},bootstrapRequests=[],voiceReviewIds={'C00001':'voice1','C00002':'voice2'})
        self.item=dict(id='current',kind='video_prompt',subject='EP001/Clip00003',status='approved',canApprove=True,
            submittedAt='2026-11-11T01:00:00+00:00',fingerprint='synthetic-fingerprint',dependsOn=[],
            request=dict(path='requests/current.json'),generation=dict(tool='h3',batch='SYNTHETIC',taskDir='tasks/current/attempt-001'))
        self.items=[self.item]+[dict(id=f'voice{n}',kind='audio',subject=f'C0000{n}',status='approved',canApprove=True,
            fingerprint=f'synthetic-voice{n}',files=[dict(path=f'音色/C0000{n}.wav',sha256=self.refs[n]['sha256'])]) for n in (1,2)]
        self.voices=[dict(character=f'C0000{n}',file=f'音色/C0000{n}.wav',status='approved',fingerprint={'file':self.refs[n]['sha256']}) for n in (1,2)]
        self.req=dict(provider='minimax-h3',prompt_source=str(self.source),references=self.refs,
            params=dict(model='MiniMax-H3',duration=8,ratio='9:16',resolution='768P',use_context_ir=False,mute_audio=False),
            warnings=[],billing=dict(total_points=80),estimate_points=80)
        self.refresh()
        self.put('baseline.prompt.txt',self.text)
        self.put('project.json',{'specification_status':'adopted','genre':'live-action','video':{'model':'MiniMax-H3'}})

    def test_pending_project_configuration_blocks_production(self):
        self.put('project.json',{'specification_status':'proposal_pending_user_review',
                               'video':{'model':'待核实，尚未批准'}})
        report=g.analyze(self.root,self.request,items=self.items)
        self.assertFalse(report['technicalPass'])
        self.assertTrue(any('初始化待审' in e['message'] for e in report['errors']))
        self.put('project.json',{'specification_status':'adopted','video':{'model':'MiniMax-H3'}})
        self.assertTrue(g.analyze(self.root,self.request,items=self.items)['technicalPass'])

    def wav(self,path,seconds):
        with wave.open(str(path),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
            f.writeframes(b'\0\0'*int(seconds*16000))

    def put(self,name,value):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False),encoding='utf-8',newline='\n')

    def refresh(self):
        self.put('assets.json',self.assets);self.put('clips.json',self.clips)
        self.put('voices.json',self.voices)
        self.put('协作/声音方案/执行规则-v001.json',self.policy)
        self.put('source.prompt.txt',self.text)
        mapped=g.CONT.sub(lambda m:m[3].strip(),self.text.strip())
        mapping={r['label']:r['tag'] for r in self.refs}
        self.req['prompt']=g.LABEL.sub(lambda m:mapping.get(m[1],m[0]),mapped)
        self.put('requests/current.json',self.req)

    def audit(self):
        return g.analyze(self.root,self.request,items=self.items)

    def blocked(self,rule):
        r=self.audit();self.assertFalse(r['technicalPass'],r)
        self.assertIn(rule,{e['rule'] for e in r['errors']},r['errors'])

    def assessment(self):
        report=self.audit()
        value={'reviewer':'总导演','checks':{k:{'verdict':'pass','note':'SYNTHETIC ONLY：已逐项检查本条具体条件和相应材料。','evidence':['source.prompt.txt']} for k in g.MANUAL},
               'risks':{r['code']:'SYNTHETIC ONLY：明确记录该项取舍及验证限制。' for r in report['risks']}}
        value['checks']['continuity_and_axis']['handoff']=handoff_for(report['shotContext'])
        self.put('reports/assessment.json',value)
        return self.root/'reports/assessment.json'

    def record(self):
        return g.record_review(self.root,self.request,self.assessment(),items=self.items)

    def test_complete_request_passes_automatic_checks(self):
        r=self.audit();self.assertTrue(r['technicalPass'],r['errors']);self.assertEqual(r['risks'],[])

    def test_internal_review_required_even_after_zero_warning(self):
        self.assertTrue(g.check_review(self.root,self.audit()))

    def test_handoff_is_required_inside_existing_professional_review(self):
        path=self.assessment();value=g.read(path)
        del value['checks']['continuity_and_axis']['handoff'];self.put('reports/assessment.json',value)
        with self.assertRaisesRegex(ValueError,'状态读取'):g.record_review(self.root,self.request,path,items=self.items)

    def test_changed_upstream_prompt_invalidates_completed_internal_review(self):
        self.record();self.put('baseline.prompt.txt','SYNTHETIC changed upstream arrangement')
        self.assertTrue(g.check_review(self.root,self.audit()))

    def test_missing_state_reading_blocks_before_execution(self):
        (self.root/'project.json').unlink();self.blocked('continuity')

    def test_post_dialogue_delivery_explanations_block(self):
        original=self.text
        samples=(
            '对白时段是可用窗口，按正常口语完整说一次，不拖长尾音；提前说完的余下时间保持闭口。',
            '\n按正常口语完整说一次，提前说完的余下时间保持闭口。',
            '再说一次。', '把这句台词重复念一遍。',
            'Say the line once.', 'Repeat the line one more time.',
        )
        for suffix in samples:
            with self.subTest(suffix=suffix):
                self.text=original.replace('王叔说完立即闭口。','王叔说完立即闭口。'+suffix)
                self.refresh();self.blocked('dialogue')

    def test_post_dialogue_closure_and_visible_actions_pass(self):
        self.text=self.text.replace('王叔说完立即闭口。','王叔说完立即闭口，继续看着表婶。\n右手留在桌面，门扇保持关闭。')
        self.refresh();r=self.audit();self.assertTrue(r['technicalPass'],r['errors'])

    def test_delivery_before_next_timed_dialogue_is_not_previous_tail(self):
        self.text=self.text.replace('3.60—6.60秒，','3.60—6.60秒，按正常口语完整说一次；')
        self.refresh();r=self.audit();self.assertTrue(r['technicalPass'],r['errors'])

    def test_literal_repetition_request_in_approved_dialogue_is_allowed(self):
        self.text=self.text.replace('[Chinese] 你们怎么说的？','[Chinese] 再说一次。')
        self.clips[1]['shots'][0]['dialogue']='王叔：再说一次。\n表婶：钱存定期了。'
        self.refresh();r=self.audit();self.assertTrue(r['technicalPass'],r['errors'])

    def test_current_internal_review_passes_without_new_user_approval(self):
        self.item['status']='pending'
        before=copy.deepcopy(self.items)
        self.record()
        self.assertEqual(g.check_review(self.root,self.audit()),[])
        self.item['status']='approved'
        self.assertEqual(g.check_review(self.root,self.audit()),[])
        self.item['status']='pending';self.assertEqual(self.items,before)

    def test_old_request_rejected_after_newer_pending_candidate(self):
        newer=copy.deepcopy(self.item);newer.update(id='newer',submittedAt='2026-11-11T02:00:00+00:00',request={'path':'requests/newer.json'},status='pending')
        self.items.append(newer);self.blocked('review_version')

    def test_unregistered_request_is_not_implicitly_executable(self):
        self.items=[];self.blocked('review_version')

    def test_unadopted_new_clip_number_blocks(self):
        self.item['subject']='EP001/Clip00011';self.blocked('review_version')

    def test_source_request_divergence_blocks(self):
        self.req['prompt']=self.req['prompt'].replace('固定平视','缓慢推近');self.put('requests/current.json',self.req);self.blocked('source_mapping')

    def test_reference_change_blocks_even_if_warnings_cleared(self):
        self.image.write_bytes(b'CHANGED');self.blocked('references')

    def test_audio_tag_order_cannot_be_swapped(self):
        self.refs[1]['tag']='<Audio 2>';self.refs[2]['tag']='<Audio 1>';self.refresh();self.blocked('references')

    def test_missing_attachment_tag_blocks(self):
        self.text=self.text.replace('保持统一。','保持统一，参考<Picture 9>。');self.refresh();self.blocked('references')

    def test_strict_frame_and_audio_cannot_mix(self):
        self.refs[0]['role']='first_frame';self.refresh();self.blocked('references')

    def test_duplicate_reference_label_blocks(self):
        self.refs[2]['label']=self.refs[1]['label'];self.refresh();self.blocked('references')

    def test_missing_policy_does_not_silently_bypass_voice_gate(self):
        (self.root/'协作/声音方案/执行规则-v001.json').unlink();self.blocked('audio_binding')

    def test_new_story_without_prior_production_uses_same_dialogue_gates(self):
        # Independent synthetic cast, no adopted previous clip or project convention.
        replacements={'王叔':'沈伯','表婶':'周姨','大强':'赵宁'}
        for old,new in replacements.items():
            self.text=self.text.replace(old,new)
            for asset in self.assets:
                asset['name']=asset['name'].replace(old,new)
            for shot in self.clips[1]['shots']:
                shot['dialogue']=shot['dialogue'].replace(old,new)
        self.clips=self.clips[1:]
        self.refresh()
        report=self.audit()
        self.assertTrue(report['technicalPass'],report['errors'])
        self.assertIn('genres/3d-guoman.md',report['ruleSources'])
        (self.root/'协作/声音方案/执行规则-v001.json').unlink()
        self.blocked('audio_binding')
        self.refresh()
        self.text=self.text.replace('沈伯说完立即闭口。','沈伯说完立即闭口。再说一次。')
        self.refresh();self.blocked('dialogue')

    def test_actual_shared_rule_file_update_invalidates_internal_review(self):
        import shutil
        shared=self.root/'synthetic-shared-tools'
        for name in (*g.SOURCES,'tools/h3_execution_checks.py','tools/voice_readiness.py','tools/voice_lifecycle.py','tools/shot_context.py','tools/validate_project.py'):
            target=shared/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(g.TOOLS_ROOT/name,target)
        with patch.object(g,'TOOLS_ROOT',shared):
            self.record()
            self.assertEqual(g.check_review(self.root,self.audit()),[])
            source=shared/'skills/minimax-h3-video/references/audio.md'
            source.write_text(source.read_text(encoding='utf-8')+'\nSynthetic changed rule.\n',encoding='utf-8')
            self.assertTrue(g.check_review(self.root,self.audit()))

    def test_reference_only_in_preamble_does_not_count_as_inline_binding(self):
        self.text=self.text.replace('王叔使用[C00001音色参考]提供的声线，','王叔');self.refresh();self.blocked('audio_binding')

    def test_wrong_audio_in_dialogue_blocks(self):
        self.text=self.text.replace('王叔使用[C00001音色参考]','王叔使用[C00002音色参考]');self.refresh();self.blocked('audio_binding')

    def test_extra_audio_in_same_spoken_phase_blocks(self):
        self.text=self.text.replace('王叔使用[C00001音色参考]','王叔使用[C00001音色参考][C00002音色参考]');self.refresh();self.blocked('audio_binding')

    def test_audio_definition_without_speaker_id_blocks(self):
        self.text=self.text.replace('仅提供<Subject 1> (S1)','仅提供<Subject 1>');self.refresh();self.blocked('audio_binding')

    def test_audio_definition_wrong_person_blocks(self):
        self.text=self.text.replace('仅提供<Subject 1> (S1)','仅提供<Subject 2> (S2)');self.refresh();self.blocked('audio_binding')

    def test_nonspeaker_must_not_have_speaker_id(self):
        self.text=self.text.replace('大强，仅视觉主体','大强，(S3)仅视觉主体');self.refresh();self.blocked('dialogue')

    def test_speaker_ids_follow_order(self):
        self.text=self.text.replace('(S1)','(S9)');self.refresh();self.blocked('dialogue')

    def test_speaker_ids_forbidden_in_retention(self):
        self.text=self.text.replace('只参考声线','(S1)只参考声线',1);self.refresh();self.blocked('structure')

    def test_changed_dialogue_word_blocks(self):
        self.text=self.text.replace('[Chinese] 你们怎么说的？','[Chinese] 你们到底怎么说的？');self.refresh();self.blocked('dialogue')

    def test_changed_dialogue_speaker_blocks(self):
        self.clips[1]['shots'][0]['dialogue']='表婶：你们怎么说的？\n王叔：钱存定期了。';self.refresh();self.blocked('dialogue')

    def test_stage_directions_inside_dialogue_block(self):
        self.text=self.text.replace('[Chinese] 钱存定期了。','[Chinese] （冷笑）钱存定期了。');self.refresh();self.blocked('dialogue')

    def test_missing_dialogue_language_blocks(self):
        self.text=self.text.replace('[Chinese] 钱存定期了。','钱存定期了。');self.refresh();self.blocked('dialogue')

    def test_malformed_dialogue_tags_block(self):
        self.text=self.text.replace('</d>','',1);self.refresh();self.blocked('dialogue')

    def test_overlap_blocks(self):
        self.text=self.text.replace('3.60—6.60','3.00—6.60');self.refresh();self.blocked('timing')

    def test_global_film_time_in_short_clip_blocks(self):
        self.text=self.text.replace('3.60—6.60','40.36—45.72');self.refresh();self.blocked('timing')

    def test_dialogue_and_tail_hold_cannot_overlap(self):
        self.text=self.text.replace('最后约1秒（7—8秒）','最后约3秒（5—8秒）');self.refresh();self.blocked('timing')

    def test_reversed_action_interval_blocks(self):
        self.text=self.text.replace('最后约1秒（7—8秒）','最后约1秒（8—7秒）');self.refresh();self.blocked('timing')

    def test_missing_dialogue_window_blocks(self):
        self.text=self.text.replace('0.20—3.20秒，','');self.refresh();self.blocked('timing')

    def test_fast_speech_is_review_risk_not_invented_hard_limit(self):
        self.text=self.text.replace('0.20—3.20','0.20—1.00');self.refresh()
        r=self.audit();self.assertTrue(r['technicalPass'],r['errors']);self.assertIn('speech_rate_1',{x['code'] for x in r['risks']})
        self.record();self.assertEqual(g.check_review(self.root,self.audit()),[])

    def test_unaddressed_risk_cannot_be_signed_off(self):
        self.text=self.text.replace('0.20—3.20','0.20—1.00');self.refresh()
        p=self.assessment();a=g.read(p);a['risks']={};self.put('reports/assessment.json',a)
        with self.assertRaisesRegex(ValueError,'风险'):g.record_review(self.root,self.request,p,items=self.items)

    def test_blanket_pass_is_not_professional_review(self):
        p=self.assessment();a=g.read(p);a['checks']['reference_roles']['note']='通过';self.put('reports/assessment.json',a)
        with self.assertRaisesRegex(ValueError,'具体判断'):g.record_review(self.root,self.request,p,items=self.items)

    def test_internal_review_cannot_waive_hard_error(self):
        self.text=self.text.replace('0.20—3.20','0.20—10.00');self.refresh()
        with self.assertRaisesRegex(ValueError,'不能豁免'):self.record()

    def test_missing_listener_closure_blocks(self):
        self.text=self.text.replace('表婶安静听话、双唇自然闭合；','');self.refresh();self.blocked('silence')

    def test_missing_after_speech_closure_blocks(self):
        self.text=self.text.replace('王叔说完立即闭口。\n','');self.refresh();self.blocked('silence')

    def test_no_environment_with_native_audio_blocks(self):
        self.text=self.text.replace('室内低弱底噪、自然呼吸和轻微衣料摩擦。无额外人声、无旁白、无配乐。','N/A');self.refresh();self.blocked('soundscape')

    def test_actual_narration_instruction_blocks(self):
        self.text=self.text.replace('无额外人声、无旁白','增加旁白');self.refresh();self.blocked('soundscape')

    def test_process_notes_cannot_be_sent_to_model(self):
        self.text=self.text.replace('全段8秒','预计80积分，等待送审。全段8秒');self.refresh();self.blocked('residue')

    def test_bare_internal_reference_blocks(self):
        self.text=self.text.replace('开头短暂停顿','在05开口前站定，切C02。开头短暂停顿');self.refresh();self.blocked('residue')

    def test_silent_clip_without_audio_passes(self):
        self.refs[:]=self.refs[:1]
        self.text='\n'.join(l for l in self.text.splitlines() if '音色参考' not in l and '<d>' not in l)
        self.text=self.text.replace('说话编号(S1)，','').replace('说话编号(S2)，','')
        self.text=self.text.replace('开头短暂停顿，两人双唇自然闭合。','全镜无人说话，两人双唇自然闭合。')
        self.clips[1]['shots'][0]['dialogue']='';self.policy['requiredVoices']['EP001/Clip00003']=[];self.refresh()
        self.assertTrue(self.audit()['technicalPass'],self.audit()['errors'])

    def basic_first_frame(self):
        self.refs[:]=self.refs[:1];self.refs[0]['role']='first_frame'
        self.text=('For the target video, at 0.00 seconds into the target video, [S00006] (from [Shot 1]) is fully referenced.\n\n'
            'integrated_multimodal_description:\n'+self.style+'\n[Shot 1] 固定平视客厅全景，王叔在桌边。\n'
            '全镜无人说话，人物双唇自然闭合。\n\noverall_soundscape:\n室内低弱底噪、衣料摩擦。\n\nnon_diegetic_music:\nN/A\n')
        self.clips[1]['shots'][0]['dialogue']='';self.policy['requiredVoices']['EP001/Clip00003']=[];self.refresh()

    def test_first_frame_alignment_passes(self):
        self.basic_first_frame();self.assertTrue(self.audit()['technicalPass'],self.audit()['errors'])

    def test_first_frame_alignment_cannot_be_omitted(self):
        self.basic_first_frame();self.text=self.text.split('\n',1)[1];self.refresh();self.blocked('structure')

    def test_first_frame_alignment_must_use_actual_time(self):
        self.basic_first_frame();self.text=self.text.replace('at 0.00 seconds','at 1.00 seconds');self.refresh();self.blocked('structure')

    def test_first_shot_is_not_a_cut(self):
        self.text=self.text.replace('[Shot 1] 固定平视腰部','[Shot 1] At 00:00.000, the camera cuts to 固定平视腰部');self.refresh();self.blocked('timing')

    def test_reference_audio_over_five_seconds_blocks(self):
        path=Path(self.refs[1]['path']);self.wav(path,5.1);self.refs[1]['sha256']=g.sha(path);self.refresh();self.blocked('audio_binding')

    def test_voice_reference_approved_for_wrong_character_blocks(self):
        self.items[1]['subject']='C00003';self.blocked('audio_binding')

    def test_retention_must_cover_defined_subjects(self):
        self.text=self.text.replace('<Subject 4>（[Shot 1]）：fully_preserved - 空间布局。','');self.refresh();self.blocked('structure')

    def test_total_duration_and_integer_constraints(self):
        for value in (8.5,16,True):
            with self.subTest(value=value):
                self.req['params']['duration']=value;self.refresh();self.blocked('request')

    def test_style_drift_blocks(self):
        self.text=self.text.replace(self.style,'炫酷漫画风格。');self.refresh();self.blocked('style')

    def test_fixed_voice_description_drift_blocks(self):
        self.text=self.text.replace('固定声线：中年男性偏低声线。','固定声线：高亢尖细童声。');self.refresh();self.blocked('dialogue')

    def test_internal_record_cannot_be_edited_into_blanket_pass(self):
        p=self.record();r=g.read(p);r['assessment']['checks']['reference_roles']['note']='通过';g.save(p,r)
        self.assertTrue(g.check_review(self.root,self.audit()))

    def test_duplicate_json_key_fails_closed(self):
        self.request.write_text('{"params":{},"params":{}}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'重复'):self.audit()

    def test_changed_request_invalidates_internal_review(self):
        self.record();self.req['purpose']='new';self.refresh();self.assertTrue(g.check_review(self.root,self.audit()))

    def test_changed_evidence_invalidates_internal_review(self):
        self.put('reports/observation.txt','synthetic observation')
        p=self.assessment();a=g.read(p);a['checks']['reference_roles']['evidence']=['reports/observation.txt'];self.put('reports/assessment.json',a)
        g.record_review(self.root,self.request,p,items=self.items)
        self.put('reports/observation.txt','changed observation');self.assertTrue(g.check_review(self.root,self.audit()))

    def test_changed_rules_invalidate_internal_review(self):
        self.record();r=self.audit();r['ruleSources']['audio.md']='changed';self.assertTrue(g.check_review(self.root,r))

    def test_changed_dependency_invalidates_internal_review(self):
        self.item['dependsOn']=['image'];self.items.append(dict(id='image',fingerprint='original'))
        self.record();self.items[-1]['fingerprint']='changed';self.assertTrue(g.check_review(self.root,self.audit()))

    def test_different_batch_or_attempt_blocks(self):
        with patch.object(g,'review_items',return_value=self.items):
            with self.assertRaisesRegex(ValueError,'批次'):g.require_execution_checks(self.root,self.request,'tasks/current/attempt-001','OTHER')
            with self.assertRaisesRegex(ValueError,'attempt'):g.require_execution_checks(self.root,self.request,'tasks/current/attempt-002','SYNTHETIC')

    def with_tail(self):
        frame=self.root/'tail.png';frame.write_bytes(b'SYNTHETIC_ACTUAL_TAIL')
        video=self.root/'prior.mp4';video.write_bytes(b'SYNTHETIC_ACTUAL_SOURCE_VIDEO')
        self.clips.append(dict(id='EP001/Clip00002',status='approved',video='prior.mp4'))
        self.clips[1]['continue_from']='EP001/Clip00002'
        self.refs.insert(0,dict(label='Clip00002-尾帧',path=str(frame),role='reference',tag='<Picture 1>',sha256=g.sha(frame)))
        self.refs[1]['tag']='<Picture 2>'
        self.text=self.text.replace('summary:', '[Clip00002-尾帧] {{承接定义·已核对：实际门关闭，抹布在桌上。}}\n\nsummary:',1)
        self.text=self.text.replace('[Shot 1] 固定','{{承接开场·已核对：门保持关闭，抹布留在桌上。}}\n[Shot 1] 固定',1)
        log={'frame':str(frame),'frame_sha256':g.sha(frame),'changes':[{'marker':'承接定义','new':'实际门关闭，抹布在桌上。'},{'marker':'承接开场','new':'门保持关闭，抹布留在桌上。'}]}
        self.put('source.prompt.continuity.jsonl',json.dumps(log,ensure_ascii=False)+'\n')
        self.items.append(dict(id='upstream-prompt',kind='video_prompt',subject='EP001/Clip00002',
                               request={'prompt':'SYNTHETIC SILENT SOURCE','params':{'mute_audio':True}}))
        self.items.append(dict(id='upstream',kind='video',subject='EP001/Clip00002',status='approved',canApprove=True,dependsOn=['upstream-prompt'],
                               files=[dict(sha256=g.sha(frame)),dict(sha256=g.sha(video))]))
        self.refresh()

    def test_accepted_spoken_video_requires_closeout_before_next_request(self):
        video=self.root/'accepted.mp4';video.write_bytes(b'SYNTHETIC_ACCEPTED_VIDEO')
        self.items.extend([
            dict(id='accepted-prompt',kind='video_prompt',subject='EP001/Clip00001',request=copy.deepcopy(self.req)),
            dict(id='accepted-video',kind='video',subject='EP001/Clip00001',status='approved',canApprove=True,
                 dependsOn=['accepted-prompt'],files=[dict(path='accepted.mp4',sha256=g.sha(video))])])
        self.voices=[];self.refresh()
        report=self.audit()
        self.assertTrue(any(e['rule']=='audio_binding' and '音色收尾未完成' in e['message'] for e in report['errors']))

    def test_bootstrap_does_not_bypass_existing_voice_guard(self):
        self.policy['bootstrapRequests']=[dict(request='requests/current.json',requestSHA256=g.sha(self.request),
            clip='EP001/Clip00003',batch='SYNTHETIC',taskDir='tasks/current/attempt-001')]
        self.refresh()
        report=self.audit()
        self.assertTrue(any(e['rule']=='audio_binding' and '不得再次使用首次定音' in e['message'] for e in report['errors']))

    def test_real_verified_tail_provenance_passes(self):
        self.with_tail();self.assertTrue(self.audit()['technicalPass'],self.audit()['errors'])

    def test_continuous_clip_without_tail_blocks(self):
        self.clips[1]['continue_from']='EP001/Clip00002';self.refresh();self.blocked('continuity')

    def test_unverified_tail_markers_block(self):
        self.with_tail();self.text=self.text.replace('·已核对','');self.refresh();self.blocked('continuity')

    def test_missing_tail_log_blocks(self):
        self.with_tail();(self.root/'source.prompt.continuity.jsonl').unlink();self.blocked('continuity')

    def test_tail_description_changed_after_viewing_blocks(self):
        self.with_tail();self.text=self.text.replace('门保持关闭，抹布留在桌上。','门已经打开，抹布在手中。');self.refresh();self.blocked('continuity')

    def test_tail_source_video_or_approval_must_match(self):
        self.with_tail();self.items[-1]['status']='pending';self.blocked('continuity')

    def derived_tail_fixture(self):
        self.with_tail()
        self.items[-1]['files'] = [dict(path='prior.mp4', exists=True,
                                       sha256=g.sha(self.root/'prior.mp4'))]
        self.item['derivedReferences'] = [dict(path='tail.png', source='prior.mp4',
            sourceSha256=g.sha(self.root/'prior.mp4'), frameIndex=327)]

    def derived_tail_audit(self, frame_count=328, decode_problems=()):
        from canvas_review import ReviewStore
        covered = set() if decode_problems else {'tail.png'}
        result = types.SimpleNamespace(stdout=json.dumps({'streams':[{'nb_read_frames':str(frame_count)}]}))
        with patch.object(ReviewStore, '_frame_coverage', return_value=(covered,list(decode_problems))), \
             patch('authorized_cli.runtime_tools', return_value={'ffprobe':{'path':'PINNED_TEST_PROBE'}}), \
             patch('authorized_cli.checked_binary', return_value='PINNED_TEST_PROBE'), \
             patch.object(g.subprocess, 'run', return_value=result):
            return self.audit()

    def test_exact_last_frame_inherits_source_approval_without_new_decision(self):
        self.derived_tail_fixture()
        before=copy.deepcopy(self.items)
        report=self.derived_tail_audit()
        self.assertTrue(report['technicalPass'],report['errors'])
        self.assertEqual(self.items,before)

    def test_derived_interior_frame_cannot_masquerade_as_tail(self):
        self.derived_tail_fixture();self.item['derivedReferences'][0]['frameIndex']=200
        self.assertIn('continuity',{x['rule'] for x in self.derived_tail_audit()['errors']})

    def test_derived_tail_decode_or_source_mismatch_blocks(self):
        self.derived_tail_fixture()
        self.assertIn('continuity',{x['rule'] for x in self.derived_tail_audit(decode_problems=['pixels changed'])['errors']})
        self.item['derivedReferences'][0]['sourceSha256']='0'*64
        self.assertIn('continuity',{x['rule'] for x in self.derived_tail_audit()['errors']})

    def test_unapproved_derived_source_stays_blocked(self):
        self.derived_tail_fixture();self.items[-1]['status']='pending'
        self.assertIn('continuity',{x['rule'] for x in self.derived_tail_audit()['errors']})

    def test_current_gate_is_mandatory_before_credentials(self):
        import codex_cli as cli
        work=types.SimpleNamespace(root=self.root)
        args=['submit','--execute','--request',str(self.request),'--batch','SYNTHETIC','--task-dir','tasks/current/attempt-001']
        with patch.object(cli.wf,'Work',return_value=work), \
             patch.object(cli.legacy,'validate_arguments'),patch.object(cli,'validate_live'), \
             patch('canvas_review.ReviewStore.require_generation'),patch('voice_readiness.require_voice_readiness'), \
             patch.object(g,'review_items',return_value=self.items), \
             patch.object(cli.legacy,'user_key',side_effect=AssertionError('credentials accessed')):
            with self.assertRaisesRegex(ValueError,'专业复核'):cli.execute(self.root,'h3',args)


if __name__=='__main__':
    unittest.main()
