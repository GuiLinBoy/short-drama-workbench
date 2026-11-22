"""Publication routing and sample/timeline regressions, synthetic local fixtures."""
import copy
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import assemble
import delivery_checks as gate


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.global_root=Path(self.tmp.name)/'漫剧'
        self.root=self.global_root/'作品'/'测试';self.root.mkdir(parents=True)
        self.project={'publish':False,'delivery_intent':{'purpose':'public-release',
                     'platform':'douyin','use':'ordinary_showcase'}}
        self.video=self.root/'test.mp4';self.video.write_bytes(b'SYNTHETIC TEST VIDEO')
        self.srt=self.root/'test.srt';self.srt.write_text('synthetic captions',encoding='utf-8')
        self.report={'full_decode':'passed','aigc_metadata':True,
                     'steps':{'burn_in':{'aigc_label':True}},
                     'delivery':{'status':'complete','purpose':'public-release',
                                 'video_sha256':assemble.digest(self.video),'srt_sha256':assemble.digest(self.srt)}}

    def inspect(self):
        (self.root/'project.json').write_text(json.dumps(self.project),encoding='utf-8')
        self.video.with_suffix('.validation.json').write_text(json.dumps(self.report),encoding='utf-8')
        return gate.inspect_delivery(self.root,self.video)

    def test_public_intent_activates_checks_despite_legacy_false(self):
        self.assertEqual(gate.publication_context(self.project)['purpose'],'public-release')

    def test_explicit_preview_does_not_change_project_or_grant_release(self):
        before=copy.deepcopy(self.project)
        context=gate.publication_context(self.project,'preview')
        self.assertTrue(context['public_requested']);self.assertEqual(context['purpose'],'preview')
        self.assertEqual(before,self.project)
        self.report['delivery']['purpose']='preview'
        self.assertFalse(self.inspect()['automaticPass'])

    def test_unknown_purpose_and_nonboolean_publish_refused(self):
        for value in ({'publish':'false'},{'delivery_intent':{'purpose':'final'}}, {'delivery_intent':'public'},
                      {'delivery_intent':[]},{'delivery_intent':False},{'delivery_intent':{'platform':[]}}):
            with self.subTest(value=value),self.assertRaises(ValueError):gate.publication_context(value)

    def test_technical_pass_still_requires_existing_actual_review(self):
        r=self.inspect();self.assertTrue(r['automaticPass'])
        self.assertEqual(r['releaseStatus'],'technical_checks_passed_review_required')
        self.assertEqual(r['manualReview'],'existing_final_review_required')

    def test_changed_video_or_subtitles_cannot_reuse_report(self):
        for path in (self.video,self.srt):
            old=path.read_bytes();path.write_bytes(b'CHANGED')
            self.assertFalse(self.inspect()['automaticPass']);path.write_bytes(old)

    def test_legacy_complete_never_overrides_missing_labels(self):
        self.report['publication']=self.report.pop('delivery')
        self.report['steps']['burn_in']['aigc_label']=False
        self.assertFalse(self.inspect()['automaticPass'])

    def test_local_unlabelled_preview_is_not_public_release(self):
        self.report['local_preview_without_visible_ai_label']=True
        self.assertFalse(self.inspect()['automaticPass'])

    def test_current_music_evidence_rechecked_not_cached_verified(self):
        lib=self.global_root/'曲库';lib.mkdir()
        music=lib/'test.wav';music.write_bytes(b'SYNTHETIC TEST AUDIO')
        self.report['music']={'path':str(music),'verified':True}
        self.report['input_sha256']={str(music):assemble.digest(music)}
        self.assertFalse(self.inspect()['automaticPass'])
        evidence=lib/'proof.txt';evidence.write_text('SYNTHETIC TEST LICENCE',encoding='utf-8')
        track={'file':'test.wav','license_verified':True,'license':'SIMULATED',
               'source_url':'https://example.invalid/test','attribution':'SIMULATED',
               'sha256':assemble.digest(music),'license_evidence':'proof.txt',
               'usage_scope':{'platforms':['douyin'],'uses':['ordinary_showcase']}}
        (lib/'曲库.json').write_text(json.dumps({'tracks':[track]}),encoding='utf-8')
        self.assertTrue(self.inspect()['automaticPass'])
        self.assertTrue(gate.inspect_music(self.root,music)['automaticPass'])
        self.project['delivery_intent']['use']='advertising'
        self.assertFalse(self.inspect()['automaticPass'])
        self.assertFalse(gate.inspect_music(self.root,music)['automaticPass'])
        self.project['delivery_intent']['use']='ordinary_showcase'
        evidence.unlink()
        self.assertFalse(self.inspect()['automaticPass'])

    def test_hidden_label_option_preserves_defaults_and_metadata(self):
        post={'aigc_label':{'visible':True,'metadata':True}}
        before=copy.deepcopy(post)
        result=assemble.effective_ai_label({'publish':False},post,True)
        self.assertEqual(result,{'visible':False,'metadata':True});self.assertEqual(post,before)
        with self.assertRaises(SystemExit):assemble.effective_ai_label({'publish':True},post,True)


class AudioIntegrityTests(unittest.TestCase):
    def test_short_missing_or_excess_samples_are_refused(self):
        for frames in ([],[{'nb_samples':47999}],[{'nb_samples':48001}],[{}]):
            with self.subTest(frames=frames),patch.object(assemble,'run',return_value=SimpleNamespace(stdout=json.dumps({'frames':frames}))):
                with self.assertRaises(SystemExit):assemble.verify_audio_samples('synthetic',48000)
        with patch.object(assemble,'run',return_value=SimpleNamespace(stdout='{"frames":[{"nb_samples":24000},{"nb_samples":24000}]}')):
            self.assertEqual(assemble.verify_audio_samples('synthetic',48000),48000)

    def test_aac_tail_does_not_excuse_lost_video_frames_or_large_drift(self):
        info={'format':{'duration':2.028333},'streams':[{'codec_type':'video','duration':2.,'nb_frames':'48'}]}
        self.assertAlmostEqual(assemble.verify_output_timing(info,2.,48),2.028333)
        for duration,frames,container in [(2.,47,2.028333),(1.96,48,2.028333),(2.,48,2.425),(2.,48,1.9)]:
            broken=copy.deepcopy(info);broken['streams'][0].update(duration=duration,nb_frames=str(frames));broken['format']['duration']=container
            with self.subTest(broken=broken),self.assertRaises(SystemExit):assemble.verify_output_timing(broken,2.,48)


if __name__=='__main__':unittest.main()
