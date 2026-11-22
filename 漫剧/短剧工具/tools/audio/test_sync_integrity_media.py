"""Real pulse timing through both render paths, entirely synthetic local media."""
import array
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import assemble


def ff(*args):
    return subprocess.run(['ffmpeg','-v','error',*map(str,args)],capture_output=True,check=True).stdout


def pulse_starts(path):
    raw=ff('-i',path,'-vn','-ac','1','-ar','48000','-f','f32le','-')
    signal=array.array('f');signal.frombytes(raw)
    window=480
    cosine=[math.cos(2*math.pi*880*i/48000) for i in range(window)]
    sine=[math.sin(2*math.pi*880*i/48000) for i in range(window)]
    levels=[]
    for start in range(0,len(signal)-window,window):
        chunk=signal[start:start+window]
        levels.append(math.hypot(sum(x*y for x,y in zip(chunk,cosine)),sum(x*y for x,y in zip(chunk,sine))))
    threshold=max(levels)*.3
    return [i*.01 for i,v in enumerate(levels) if v>threshold and (i==0 or levels[i-1]<=threshold)]


class SyncMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='sync-regression-')
        cls.root=Path(cls.temp.name);cls.clips=[]
        expression='0.01*sin(2*PI*220*t)+if(between(t,0.4,0.6)+between(t,2,2.2),0.2*sin(2*PI*880*t),0)'
        for i in range(4):
            path=cls.root/f'source{i}.mp4';duration=2.75 if i<3 else .75
            args=['-y','-f','lavfi','-i',f'color=black:s=320x180:r=24:d={duration}']
            if i<3:args+=['-f','lavfi','-i',f"aevalsrc='{expression}':s=48000:d={duration}"]
            ff(*args,'-c:v','libx264','-preset','ultrafast',*(['-c:a','aac'] if i<3 else []),path)
            cls.clips.append({'id':f'EP001/Clip{i+1:05d}','episode':'EP001','status':'approved',
                              'video':path.name,'duration':duration,'fingerprint':{'video':assemble.digest(path)}})
        pj={'mode':'series','ratio':'16:9','publish':False,'post':{'loudness_lufs':-16,
            'true_peak_db':-1,'bgm_duck_db':6,'burn_subtitles':False,'aigc_label':{'visible':False,'metadata':False}}}
        (cls.root/'project.json').write_text(json.dumps(pj),encoding='utf-8')
        (cls.root/'clips.json').write_text(json.dumps(cls.clips),encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_both_paths_keep_pulses_and_silent_segment_on_timeline(self):
        for edited in (False,True):
            with self.subTest(edited=edited):
                out=self.root/f'render-{edited}.mp4'
                args=[sys.executable,'-I',str(Path(assemble.__file__)),'--project',str(self.root),
                      '--episode','EP001','--fps','24','--out',str(out)]
                if edited:
                    entries=[]
                    for i,c in enumerate(self.clips):
                        entries.append({'id':c['id'],'source':c['video'],'sha256':c['fingerprint']['video'],
                                        'source_frames':66 if i<3 else 18,'ranges':[[0,24],[30,66]] if i<3 else [[0,18]]})
                    (self.root/'edit.json').write_text(json.dumps({'version':1,'output':{'size':[320,180],'fps':24},'clips':entries}),encoding='utf-8')
                    args+=['--edit-plan','edit.json']
                proc=subprocess.run(args,capture_output=True,text=True,encoding='utf-8')
                self.assertEqual(proc.returncode,0,proc.stdout+'\n'+proc.stderr)
                report=json.loads(out.with_suffix('.validation.json').read_text(encoding='utf-8'))
                step=2.5 if edited else 2.75
                expected=[i*step+t for i in range(3) for t in (.4,1.75 if edited else 2.)]
                actual=pulse_starts(out)
                self.assertEqual(len(actual),len(expected),(actual,expected))
                for got,want in zip(actual,expected):self.assertAlmostEqual(got,want,delta=.011)
                levels=report['audio_integrity']['clip_audio_levels']
                self.assertEqual([x['audio_samples'] for x in levels],[round(step*48000)]*3+[36000])
                self.assertTrue(levels[-1]['silent'])
                self.assertEqual(report['release']['status'],'local_preview_only')

    def test_public_intent_blocks_unlicensed_music_before_render(self):
        path=self.root/'project.json';original=path.read_text(encoding='utf-8')
        project=json.loads(original)
        project.update(delivery_intent={'purpose':'public-release','platform':'douyin','use':'ordinary_showcase'})
        project['post']['aigc_label']={'visible':True,'metadata':True}
        try:
            path.write_text(json.dumps(project),encoding='utf-8')
            out=self.root/'must-not-render.mp4'
            proc=subprocess.run([sys.executable,'-I',str(Path(assemble.__file__)),'--project',str(self.root),
                                 '--episode','EP001','--music',str(self.root/'source0.mp4'),'--out',str(out)],
                                capture_output=True,text=True,encoding='utf-8')
            self.assertNotEqual(proc.returncode,0)
            self.assertIn('授权',proc.stderr)
            self.assertFalse(out.exists());self.assertFalse(out.with_suffix('.validation.json').exists())
        finally:
            path.write_text(original,encoding='utf-8')


if __name__=='__main__':unittest.main()
