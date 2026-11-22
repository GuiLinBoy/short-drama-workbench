"""Regression: a quiet ambience shot keeps its intended level after assembly."""
import array, contextlib, io, json, math, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio import assemble
import make_sample_project


class ClipAudioLevelsTest(unittest.TestCase):
    def test_invalid_override_is_rejected(self):
        for value in ({'target_lufs': -30}, {'target_lufs': -80, 'reason': 'ambience'},
                      {'target_lufs': -30, 'reason': 'ambience', 'dynamic': True}):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    assemble.clip_level_target({'audio': value})

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg unavailable')
    def test_foley_stays_quieter_and_samples_stay_exact(self):
        with tempfile.TemporaryDirectory(prefix='assemble-levels-') as folder:
            root=make_sample_project.build(Path(folder)/'project')
            pj=json.loads((root/'project.json').read_text(encoding='utf-8'))
            pj['ratio']='16:9';pj['post']['burn_subtitles']=False
            (root/'project.json').write_text(json.dumps(pj),encoding='utf-8')
            clips=json.loads((root/'clips.json').read_text(encoding='utf-8'))[:2]
            for c in clips:
                c.update(duration=49/24,status='approved')
                p=root/c['video'];p.parent.mkdir(parents=True,exist_ok=True)
                subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i',f'color=c=gray:size=320x180:rate=24:duration={49/24}',
                                '-f','lavfi','-i',f'sine=frequency=440:duration={49/24}','-c:v','libx264','-preset','ultrafast',
                                '-c:a','aac','-frames:v','49',str(p)],check=True,capture_output=True)
                c['fingerprint']={'video':assemble.digest(p)}
            (root/'clips.json').write_text(json.dumps(clips),encoding='utf-8')
            entries=[{'id':c['id'],'source':c['video'],'sha256':c['fingerprint']['video'],
                      'source_frames':49,'ranges':[[0,49]]} for c in clips]
            entries[1]['audio']={'target_lufs':-30,'reason':'actual no-dialogue ambience shot'}
            (root/'edit.json').write_text(json.dumps({'version':1,'output':{'size':[320,180],'fps':24},'clips':entries}),encoding='utf-8')
            out=root/'final.mp4'
            result=subprocess.run([sys.executable,'-I',str(Path(assemble.__file__)), '--project',str(root),'--episode','EP001',
                                   '--edit-plan','edit.json','--fps','24','--no-visible-ai-label','--purpose','preview','--out',str(out)],capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report=json.loads(out.with_suffix('.validation.json').read_text(encoding='utf-8'))
            levels=report['audio_integrity']['clip_audio_levels']
            self.assertEqual([x['target_lufs'] for x in levels],[-16,-30])
            self.assertAlmostEqual(levels[0]['constant_gain_db']-levels[1]['constant_gain_db'],14,delta=.1)
            self.assertTrue(all(x['audio_samples']==98000==x['expected_samples'] for x in levels))
            self.assertLessEqual(report['actual_duration']-98/24,2048/48000+.001)
            raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(out),'-vn','-ar','48000','-ac','1','-f','s16le','-'])
            samples=array.array('h',raw)
            def rms(start,end):
                values=samples[round(start*48000):round(end*48000)]
                return math.sqrt(sum(x*x for x in values)/len(values))
            self.assertAlmostEqual(20*math.log10(rms(.4,1.5)/rms(2.4,3.5)),14,delta=1)


if __name__=='__main__':unittest.main(verbosity=2)
