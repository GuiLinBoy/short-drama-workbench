"""The dryrun is an integration test of CLI tools, never of live bots/providers."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))


class DryrunTest(unittest.TestCase):
    def test_two_project_chain(self):
        script=HERE/'run_dryrun.py'
        self.assertTrue(script.is_file(),'offline chain runner not implemented')
        p=subprocess.run([sys.executable,'-I',str(script),'--skip-local-asr'],capture_output=True,
                         text=True,encoding='utf-8',timeout=480)
        self.assertEqual(p.returncode,0,p.stdout+'\n'+p.stderr)
        path=Path(json.loads(p.stdout)['report'])
        report=json.loads(path.read_text(encoding='utf-8'))
        self.assertTrue(report['mock_chain_passed'])
        self.assertEqual(len(report['projects']),2)
        self.assertTrue(report['accounting']['matched'])
        self.assertEqual(report['real_data_changes'],[])
        self.assertEqual(report['approvals'],'SIMULATED')
        self.assertEqual(report['local_asr']['status'],'NOT_RUN')
        self.assertFalse(report['offline_acceptance_passed'])
        self.assertFalse(report['production_ready'])
        self.assertFalse(report['real_bots_tested'])
        provider=json.loads((path.parent/'mock-provider-report.json').read_text(encoding='utf-8'))
        self.assertEqual(len(provider['jobs']),15)
        self.assertEqual(len(provider['images']),6)
        self.assertEqual(report['accounting']['ledger_events'],36)
        for project in report['projects']:
            self.assertTrue(Path(project['movie']).is_file())
            self.assertEqual(project['validation']['full_decode'],'passed')
            self.assertTrue(project['repeated_render_preserved'])
            self.assertEqual(project['recognition'],'ASR_STUB')
            self.assertEqual(project['validation']['steps']['narration_music']['music_duck_db'],12)
            self.assertAlmostEqual(project['validation']['actual_duration'],24 if project['project']=='演练A' else 8,delta=.1)
        # A previous run is preserved rather than reused or erased.
        before=(path.parent/'report.json').read_bytes()
        again=subprocess.run([sys.executable,'-I',str(script),'--skip-local-asr','--output-root',str(path.parent)],
                             capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertNotEqual(again.returncode,0)
        self.assertEqual(before,(path.parent/'report.json').read_bytes())

    def test_real_asr_missing_dependencies_are_not_stubbed(self):
        from asr_smoke import run_checkpoint
        with tempfile.TemporaryDirectory() as folder:
            result=run_checkpoint(SimpleNamespace(env={},root=Path(folder)),Path(folder)/'missing')
        self.assertEqual(result['status'],'BLOCKED')
        self.assertFalse(result['test_stub'])

    def test_network_guard_applies_to_python_descendants(self):
        with tempfile.TemporaryDirectory() as folder:
            child=Path(folder)/'child.py'
            child.write_text("import socket\ntry: socket.getaddrinfo('example.invalid',443)\nexcept RuntimeError as e: print(str(e))\nelse: raise SystemExit(99)\n",encoding='utf-8')
            parent=Path(folder)/'parent.py'
            parent.write_text('import subprocess,sys\nsubprocess.run([sys.executable,"-I",sys.argv[1]],check=True)\n',encoding='utf-8')
            guard=HERE.parents[1]/'tools/run_offline_checks.py'
            p=subprocess.run([sys.executable,'-I',str(guard),'--child',str(parent),str(child)],
                             capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('OFFLINE GUARD: external network forbidden',p.stdout)


if __name__=='__main__':
    unittest.main(verbosity=2)
