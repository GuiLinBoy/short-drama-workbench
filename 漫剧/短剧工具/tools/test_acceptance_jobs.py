import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import acceptance_jobs as jobs


class Jobs(unittest.TestCase):
    def test_finite_actions_have_owning_roles(self):
        self.assertEqual(jobs.role_for('plan-register'), '编剧')
        self.assertEqual(jobs.role_for('image-character'), '美术')
        self.assertEqual(jobs.role_for('video-final-3'), '摄影')
        self.assertEqual(jobs.role_for('assemble'), '剪辑')
        self.assertEqual(jobs.role_for('approve-final-2'), '总导演')
        with self.assertRaises(ValueError): jobs.role_for('python -c arbitrary')

    def test_job_runner_never_uses_shell_or_inherits_provider_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = jobs.Runner(Path(folder))
            with patch.object(jobs.subprocess, 'run') as run:
                run.return_value.returncode = 0; run.return_value.stdout = '{}'; run.return_value.stderr = ''
                with patch.dict(jobs.os.environ, {'NEXORAI_API_KEY': 'FAKE-ONLY', 'MINIMAX_H3_API_KEY': 'FAKE-ONLY'}):
                    runner.cli('state', '--project', folder, 'show', 'assets')
                kwargs = run.call_args.kwargs
                self.assertFalse(kwargs.get('shell', False))
                self.assertNotIn('NEXORAI_API_KEY', kwargs['env'])
                self.assertNotIn('MINIMAX_H3_API_KEY', kwargs['env'])
                self.assertIn('authorized_cli.py', ' '.join(run.call_args.args[0]))

    def test_existing_unknown_attempt_is_queried_not_resubmitted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); task=root/'tasks/a/attempt-001'; task.mkdir(parents=True)
            (task/'state.json').write_text('{"status":"unknown"}', encoding='utf-8')
            runner=jobs.Runner(root)
            with patch.object(runner,'cli',return_value={'status':'UNKNOWN_SUBMISSION'}) as cli:
                with self.assertRaises(ValueError): runner.generate('h3',task,['submit','--execute'])
            self.assertEqual(cli.call_args.args[0], 'wait')
            self.assertEqual(cli.call_count, 1)

    def test_new_submit_then_wait_requires_delivered_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            runner=jobs.Runner(Path(folder))
            with patch.object(runner,'cli',side_effect=[{}, {'status':'DELIVERED_ACCOUNTING_PENDING'}]) as cli:
                with self.assertRaises(ValueError): runner.generate('tts',Path(folder)/'tasks/a/attempt-001',['submit','--execute'])
            self.assertEqual(cli.call_count, 2)

    def test_fixed_image_recipe_and_final_resolution_do_not_accept_model_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            runner=jobs.Runner(Path(folder))
            (Path(folder)/'assets.json').write_text(json.dumps([{'id':'C00001-A','prompt':'角色/test.txt','image':'角色/test.png'}]),encoding='utf-8')
            with patch.object(runner,'cli',return_value={}), patch.object(runner,'generate') as generate:
                runner.image('character')
                args=generate.call_args.args[2]
                self.assertIn('EP001-参考图', args)
                self.assertNotIn('--after-unknown', args)

    def test_read_only_status_is_root_scoped(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'assets.json').write_text('[]',encoding='utf-8')
            result=jobs.status(root)
            self.assertEqual(result['assets'], [])
            self.assertEqual(result['real_task_states'], [])
            self.assertNotIn('environment', result)

    def test_silent_final_does_not_attach_speech_reference_or_require_voice(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = [dict(id='EP001/'+clip, prompt=clip+'.txt', video=clip+'.mp4',
                         ending_frame=clip+'.png', shots=[{'dialogue': ''}]) for clip in jobs.CLIPS]
            (root/'clips.json').write_text(json.dumps(rows), encoding='utf-8')
            (root/'assets.json').write_text(json.dumps([
                dict(id=aid, image=aid+'.png', status='approved') for aid in ('C00001-A','S00001')]), encoding='utf-8')
            runner = jobs.Runner(root)
            with patch.object(runner, 'cli', return_value={}) as cli, \
                 patch.object(runner, 'generate', return_value={}), \
                 patch.object(runner, 'inspect', return_value={}), patch.object(runner, 'state'):
                runner.video('final', 2)
            args = cli.call_args.args
            self.assertIn('768P', args)
            self.assertFalse(any('reference_audio' in str(arg) for arg in args))

    def test_asr_cache_key_changes_with_current_final_media_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); rows=[]
            for clip in jobs.CLIPS:
                path=root/(clip+'.mp4'); path.write_bytes(clip.encode())
                rows.append({'id':'EP001/'+clip,'video':path.name})
            (root/'clips.json').write_text(json.dumps(rows),encoding='utf-8')
            runner=jobs.Runner(root); before=runner.asr_record_path()
            (root/'Clip00001.mp4').write_bytes(b'actual-revision-fixture')
            self.assertNotEqual(before,runner.asr_record_path())
            with patch.object(runner,'cli') as cli, self.assertRaises(ValueError): runner.run('assemble')
            cli.assert_not_called()

    def test_director_cannot_approve_assets_without_sheet_review(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); path=root/'fixture.png'; path.write_bytes(b'fixture')
            sha=jobs.digest(path)
            data={'target':'assets','decision':'pass','artifacts':[{'id':key,'sha256':sha} for key in ('C00001-A:image','S00001:image')]}
            record={'proposal':{'data':data},'taskId':'actual-fixture-task','reportId':'fixture-report'}
            (root/'approvals').mkdir()
            (root/'approvals/review-evidence.json').write_text(json.dumps({key:{'path':'fixture.png','reviewTaskId':record['taskId']} for key in ('C00001-A:image','S00001:image')}),encoding='utf-8')
            runner=jobs.Runner(root)
            with patch.object(runner,'latest',return_value=record),patch.object(runner,'state') as state,self.assertRaises(ValueError):
                runner.approve('assets')
            state.assert_not_called()


if __name__=='__main__': unittest.main()
