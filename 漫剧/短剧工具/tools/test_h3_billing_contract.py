"""Offline regressions for the documented H3 output/input/Context-IR tariff."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'tools'))
spec=importlib.util.spec_from_file_location('billing_h3',ROOT/'skills/minimax-h3-video/scripts/h3_task.py')
h3=importlib.util.module_from_spec(spec);spec.loader.exec_module(h3)


class BillingContract(unittest.TestCase):
    def request(self,res='480P',context=True,refs=None):
        return {'provider':'minimax-h3','params':{'duration':8,'resolution':res,'use_context_ir':context},
                'references':refs or []}

    def test_context_ir_and_output_are_both_reserved(self):
        self.assertEqual(h3.billing(self.request())['total_points'],45)
        self.assertEqual(h3.billing(self.request(context=False))['total_points'],40)

    def test_video_durations_sum_before_rounding_and_resolution_input_rate(self):
        refs=[{'role':'reference_video','path':'a.mp4'},{'role':'reference_video','path':'b.mp4'}]
        for res,total in [('480P',60),('768P',115)]:
            with patch.object(h3,'audio_seconds',side_effect=[1.1,1.2]):
                bill=h3.billing(self.request(res,refs=refs))
            self.assertEqual(bill['input_seconds'],3)
            self.assertEqual(bill['total_points'],total)

    def test_images_audio_do_not_add_input_charge(self):
        refs=[{'role':'reference','path':'image.png'},{'role':'reference_audio','path':'voice.wav'}]
        with patch.object(h3,'audio_seconds',side_effect=AssertionError('not video')):
            self.assertEqual(h3.billing(self.request(refs=refs))['total_points'],45)

    def test_unmeasurable_video_is_rejected_instead_of_free(self):
        for duration in (None,float('nan'),float('inf'),0,-1):
            with self.subTest(duration=duration),patch.object(h3,'audio_seconds',return_value=duration):
                with self.assertRaises(SystemExit):h3.billing(self.request(refs=[{'role':'reference_video','path':'bad.mp4'}]))

    def test_prepare_persists_full_estimate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);prompt=root/'prompt.txt';prompt.write_text('No one speaks.\noverall_soundscape: snow wind.\nnon_diegetic_music: N/A',encoding='utf-8')
            args=SimpleNamespace(prompt=prompt,ref=[],output=root/'request.json',duration=8,context_ir='on',allow_long=False,
                                 skill_id=None,audio='generate',purpose='draft',model='MiniMax-H3',ratio='9:16',resolution='480P')
            h3.cmd_prepare(args)
            request=json.loads(args.output.read_text(encoding='utf-8'))
            self.assertEqual(request['estimate_points'],45)
            self.assertEqual(request['billing']['context_ir_points'],5)

    def test_settlement_uses_full_documented_usage(self):
        state={'charge':{'points':45},'delivered_resolution':'480P',
               'usage':{'output_seconds':8,'input_seconds':3,'use_context_ir':True}}
        with patch.object(h3.runtime,'settle') as settle:
            h3.settle(state,Path('no-real-task'))
        self.assertEqual(settle.call_args.args[2],60)

    def test_incomplete_usage_does_not_under_refund_legacy_task(self):
        state={'charge':{'points':45},'delivered_resolution':'480P','usage':{'output_seconds':8}}
        with tempfile.TemporaryDirectory() as d,patch.object(h3.runtime,'settle') as settle:
            h3.settle(state,Path(d))
        settle.assert_not_called()

    def test_saved_request_supplies_missing_usage_components(self):
        with tempfile.TemporaryDirectory() as d:
            task=Path(d)
            request=self.request();request['billing']={'input_seconds':0}
            (task/'request.json').write_text(json.dumps(request),encoding='utf-8')
            state={'charge':{'points':45},'delivered_resolution':'480P','usage':{'output_seconds':8}}
            with patch.object(h3.runtime,'settle') as settle:h3.settle(state,task)
        self.assertEqual(settle.call_args.args[2],45)

    def test_stale_prepared_estimate_is_refused_before_post(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);request=self.request();request.update(estimate_points=40,prompt='No one speaks')
            path=root/'old.json';path.write_text(json.dumps(request),encoding='utf-8')
            args=SimpleNamespace(request=path,execute=True,retry_unknown=False,task_dir=root/'tasks/T/attempt-001',
                                 budget=root/'approvals/budget.json',batch='B',output=None)
            with patch.object(h3,'http') as http:
                with self.assertRaises(SystemExit):h3.cmd_submit(args)
            http.assert_not_called()


if __name__=='__main__':unittest.main(verbosity=2)
