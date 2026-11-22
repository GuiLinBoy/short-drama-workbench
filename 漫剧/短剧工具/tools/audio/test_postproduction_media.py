"""Real FFmpeg measurements and rendered-frame tests, synthetic LOCAL media only.

Fixtures are deliberately retained under the offline runner's project scratch.
No test footage, timing, license evidence or approvals constitute production data.
"""
import array
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import assemble


def ff(*args):
    return subprocess.run(['ffmpeg','-hide_banner','-v','error',*map(str,args)],
                          capture_output=True,check=True).stdout


def samples(path,start,duration):
    raw=ff('-ss',start,'-i',path,'-t',duration,'-vn','-ac','1','-ar','48000','-f','f32le','-')
    values=array.array('f')
    values.frombytes(raw)
    return values


def tone_level(path,start,duration,frequency=440):
    values=samples(path,start,duration)
    n=len(values)
    a=sum(x*math.cos(2*math.pi*frequency*i/48000) for i,x in enumerate(values))*2/n
    b=sum(x*math.sin(2*math.pi*frequency*i/48000) for i,x in enumerate(values))*2/n
    return 20*math.log10(math.hypot(a,b))


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=Path(tempfile.mkdtemp(prefix='batch2-retained-'))
        cls.metrics={}
        cls.root=cls.base/'漫剧'/'作品'/'离线验证'
        cls.root.mkdir(parents=True)
        cls.project={'schema_version':2,'mode':'series','ratio':'16:9','publish':False,
                     'post':{'loudness_lufs':-14,'true_peak_db':-1,'bgm_duck_db':12,
                             'burn_subtitles':True,'aigc_label':{'visible':False,'metadata':False}}}
        cls.clips=[]
        for i in range(3):
            path=cls.root/f'clip{i}.mp4'
            ff('-y','-f','lavfi','-i','color=black:s=320x180:r=12:d=4',
               '-f','lavfi','-i','sine=frequency=220:duration=4',
               '-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',path)
            c={'id':f'EP001/Clip{i+1:05d}','episode':'EP001','duration':4,
               'video':path.name,'status':'approved'}
            if i:
                wav=cls.root/f'narration{i}.wav'
                ff('-y','-f','lavfi','-i',f'sine=frequency={660 if i==1 else 880}:duration=1',wav)
                c['narration']=[{'id':f'n{i}','start':1,'text':f'第{i+1}段字幕',
                                 'audio':wav.name,'narration_duration':1}]
            cls.clips.append(c)
        cls.music=cls.root/'short-music.wav'
        ff('-y','-f','lavfi','-i','sine=frequency=440:duration=1',cls.music)
        (cls.root/'speech.json').write_text(json.dumps({'source':'manual-reviewed','segments':[[1,2]]}),encoding='utf-8')
        cls.save()
        cls.cli('main.mp4','--music',cls.music,'--music-loop','--speech-timeline','speech.json')
        cls.main=cls.root/'main.mp4'
        cls.report=json.loads(cls.main.with_suffix('.validation.json').read_text(encoding='utf-8'))
        print('RETAINED_MEDIA='+str(cls.root),flush=True)

    @classmethod
    def tearDownClass(cls):
        (cls.root/'measurements.json').write_text(json.dumps(cls.metrics,ensure_ascii=False,indent=2),encoding='utf-8')

    @classmethod
    def save(cls):
        (cls.root/'project.json').write_text(json.dumps(cls.project,ensure_ascii=False),encoding='utf-8')
        (cls.root/'clips.json').write_text(json.dumps(cls.clips,ensure_ascii=False),encoding='utf-8')

    @classmethod
    def cli(cls,out,*extra,ok=True):
        p=subprocess.run([sys.executable,'-I',str(HERE/'assemble.py'),'--project',str(cls.root),
                          '--episode','EP001','--fps','12','--out',str(out),*map(str,extra)],
                         capture_output=True,text=True,encoding='utf-8')
        if (p.returncode==0)!=ok:
            raise AssertionError(p.stdout+'\n'+p.stderr)
        return p

    def test_second_and_third_subtitle_pixels_and_srt(self):
        cues=assemble.parse_srt(self.main.with_suffix('.srt').read_text(encoding='utf-8'))
        self.assertEqual([(c['start'],c['end']) for c in cues],[(5,6),(9,10)])
        self.assertEqual([c['text'] for c in cues],['第2段字幕','第3段字幕'])
        counts=[]
        for t in (1.5,5.5,9.5):
            raw=ff('-ss',t,'-i',self.main,'-frames:v','1','-vf','crop=iw:ih/4:0:3*ih/4,format=gray','-f','rawvideo','-')
            counts.append(sum(x>180 for x in raw))
            ff('-y','-ss',t,'-i',self.main,'-frames:v','1',self.root/f'frame-{t}.png')
        self.assertEqual(counts[0],0)
        self.assertGreater(counts[1],100)
        self.assertGreater(counts[2],100)
        self.metrics['subtitle_white_pixels']={'times':[1.5,5.5,9.5],'counts':counts}

    def test_music_is_ducked_during_dialogue_and_recovers_afterward(self):
        baseline=tone_level(self.main,.3,.4)
        voice=tone_level(self.main,1.3,.4)
        recovered=tone_level(self.main,2.5,.4)
        self.assertAlmostEqual(voice-baseline,-12,delta=.8)
        self.assertAlmostEqual(recovered-baseline,0,delta=.8)
        self.metrics['duck_12_db']={'before_db':baseline,'during_db':voice,'after_db':recovered,
                                    'attenuation_db':voice-baseline,'recovery_db':recovered-baseline}

    def test_original_audio_is_ducked_six_db_during_narration(self):
        before=tone_level(self.main,4.3,.4,frequency=220)
        during=tone_level(self.main,5.3,.4,frequency=220)
        self.assertAlmostEqual(during-before,-6,delta=.8)
        self.metrics['narration_original_duck_6_db']={'attenuation_db':during-before}

    def test_narration_overflow_and_stale_duration_refused(self):
        n=self.clips[1]['narration'][0]
        try:
            n['start']=3.5
            self.save()
            p=self.cli('overflow.mp4',ok=False)
            self.assertIn('超出所属片段',p.stderr)
            n['start']=1
            n['narration_duration']=.5
            self.save()
            p=self.cli('stale-duration.mp4',ok=False)
            self.assertIn('时长与音频不符',p.stderr)
            self.assertFalse((self.root/'overflow.mp4').exists())
            self.assertFalse((self.root/'stale-duration.mp4').exists())
        finally:
            n['start']=1
            n['narration_duration']=1
            self.save()

    def test_short_music_loops_through_tail(self):
        self.assertGreater(tone_level(self.main,10.5,.4),-55)
        self.assertTrue(self.report['steps']['narration_music']['music_looped'])

    def test_encoded_loudness_peak_channels_and_fingerprints(self):
        self.assertAlmostEqual(self.report['measured_lufs'],-14,delta=1)
        self.assertLessEqual(self.report['measured_true_peak_db'],-.9)
        self.assertEqual(self.report['delivery']['video_sha256'],assemble.digest(self.main))
        info=assemble.probe(self.main)
        audio=next(s for s in info['streams'] if s['codec_type']=='audio')
        self.assertEqual((audio['channels'],audio['sample_rate']),(2,'48000'))

    def test_six_db_setting_changes_actual_music_attenuation(self):
        self.project['post']['bgm_duck_db']=6
        self.save()
        try:
            self.cli('duck6.mp4','--music',self.music,'--music-loop','--speech-timeline','speech.json')
            rendered=self.root/'duck6.mp4'
            self.assertAlmostEqual(tone_level(rendered,1.3,.4)-tone_level(rendered,.3,.4),-6,delta=.8)
            self.metrics['duck_6_db']={'attenuation_db':tone_level(rendered,1.3,.4)-tone_level(rendered,.3,.4)}
        finally:
            self.project['post']['bgm_duck_db']=12
            self.save()

    def test_source_cannot_be_overwritten_even_with_flag(self):
        source=self.root/'clip0.mp4'
        before=assemble.digest(source)
        result=self.cli(source,'--overwrite',ok=False)
        self.assertIn('源素材',result.stderr)
        self.assertEqual(assemble.digest(source),before)

    def test_sidecar_collision_is_preserved(self):
        srt=self.root/'collision.srt'
        srt.write_text('original',encoding='utf-8')
        self.cli('collision.mp4','--overwrite',ok=False)
        self.assertEqual(srt.read_text(encoding='utf-8'),'original')
        self.assertFalse((self.root/'collision.mp4').exists())

    def test_missing_timeline_and_short_unloopable_music_refused(self):
        p=self.cli('no-timeline.mp4','--music',self.music,ok=False)
        self.assertIn('--speech-timeline',p.stderr)
        p=self.cli('no-loop.mp4','--music',self.music,'--speech-timeline','speech.json',ok=False)
        self.assertIn('过短',p.stderr)

    def test_public_unverified_music_refused_before_render(self):
        self.project['publish']=True
        self.project['post']['aigc_label']={'visible':True,'metadata':True}
        self.save()
        try:
            p=self.cli('public.mp4','--music',self.music,ok=False)
            self.assertIn('授权',p.stderr)
            self.assertFalse((self.root/'public.mp4').exists())
        finally:
            self.project['publish']=False
            self.project['post']['aigc_label']={'visible':False,'metadata':False}
            self.save()

    def test_visible_label_is_independent_of_caption_burn_and_has_metadata(self):
        self.project['post']['burn_subtitles']=False
        self.project['post']['aigc_label']={'visible':True,'metadata':True}
        self.save()
        try:
            self.cli('label-only.mp4')
            rendered=self.root/'label-only.mp4'
            raw=ff('-ss',5.5,'-i',rendered,'-frames:v','1','-vf','crop=iw:ih/4:0:3*ih/4,format=gray','-f','rawvideo','-')
            self.assertEqual(sum(x>180 for x in raw),0)
            counts=[]
            for time in (.5,3.5):
                header=ff('-ss',time,'-i',rendered,'-frames:v','1','-vf','crop=800:100:240:0,format=gray','-f','rawvideo','-')
                counts.append(sum(x>180 for x in header))
            self.assertGreater(counts[0],100)
            self.assertEqual(counts[1],0)
            self.assertIn('AI',assemble.probe(rendered)['format']['tags']['comment'])
            ff('-y','-ss',.5,'-i',rendered,'-frames:v','1',self.root/'label-preview.png')
            self.metrics['label_header_white_pixels']={'times':[.5,3.5],'counts':counts,'subtitles_burned':False}
        finally:
            self.project['post']['burn_subtitles']=True
            self.project['post']['aigc_label']={'visible':False,'metadata':False}
            self.save()


if __name__=='__main__':
    unittest.main(verbosity=2)
