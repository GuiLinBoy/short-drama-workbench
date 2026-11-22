"""Offline checks for source-bound frame editing; no generation calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import assemble


class EditPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'source.mp4').write_bytes(b'SYNTHETIC SOURCE')
        self.sha=assemble.digest(self.root/'source.mp4')
        self.clips=[{'id':'EP001/Clip00001','video':'source.mp4','fingerprint':{'video':self.sha}}]
        self.plan={'version':1,'clips':[{'id':'EP001/Clip00001','source':'source.mp4','sha256':self.sha,
                                       'source_frames':96,'ranges':[[12,24],[48,84]],'color':{'brightness':.008}}]}
        self.info={'streams':[{'codec_type':'video','avg_frame_rate':'24/1','nb_frames':'96','duration':'4'}]}

    def load(self):
        (self.root/'edit.json').write_text(json.dumps(self.plan),encoding='utf-8')
        return assemble.load_edit_plan(self.root,'edit.json',self.clips)

    def test_source_bound_plan_preserves_adopted_records(self):
        before=copy.deepcopy(self.clips);_,plan=self.load()
        self.assertEqual(plan,self.plan);self.assertEqual(self.clips,before)

    def test_changed_source_or_fingerprint_is_refused(self):
        (self.root/'source.mp4').write_bytes(b'CHANGED')
        with self.assertRaises(SystemExit):self.load()

    def test_dropped_or_duplicated_clip_is_refused(self):
        self.plan['clips']*=2
        with self.assertRaises(SystemExit):self.load()

    def test_overlapping_or_fractional_frames_are_refused(self):
        for ranges in ([[0,24],[20,48]],[[0,2.5]],[[-1,20]]):
            with self.subTest(ranges=ranges):
                self.plan['clips'][0]['ranges']=ranges
                with self.assertRaises(SystemExit):self.load()

    def test_cut_out_of_source_is_refused(self):
        self.plan['clips'][0]['ranges']=[[0,97]]
        with self.assertRaises(SystemExit):assemble.edit_filters(self.plan['clips'][0],self.info,24,768,1344,True)

    def test_measured_frame_count_and_rate_are_required(self):
        for frames,fps in ((95,24),(96,30)):
            self.plan['clips'][0]['source_frames']=frames
            with self.assertRaises(SystemExit):assemble.edit_filters(self.plan['clips'][0],self.info,fps,768,1344,True)

    def test_disjoint_cuts_trim_both_streams_at_same_times(self):
        graph,duration=assemble.edit_filters(self.plan['clips'][0],self.info,24,768,1344,True)
        self.assertAlmostEqual(duration,2.)
        self.assertIn('trim=start_frame=12:end_frame=24',graph)
        self.assertIn('atrim=start=0.500000000:end=1.000000000',graph)
        self.assertIn('atrim=start=2.000000000:end=3.500000000',graph)
        self.assertIn('concat=n=2:v=1:a=1',graph)

    def test_unmapped_narration_is_refused(self):
        self.clips[0]['narration']=[{'audio':'voice.wav'}]
        with self.assertRaises(SystemExit):self.load()

    def test_unknown_or_excessive_color_change_is_refused(self):
        for color in ({'brightness':1},{'scale':2}):
            self.plan['clips'][0]['color']=color
            with self.assertRaises(SystemExit):self.load()


if __name__=='__main__':unittest.main()
