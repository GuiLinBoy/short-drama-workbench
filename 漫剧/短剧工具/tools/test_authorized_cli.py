import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import authorized_cli as cli


class Launcher(unittest.TestCase):
    def test_sensitive_values_are_filtered(self):
        output=io.StringIO();stream=cli.RedactedStream(output,['FAKE-SENSITIVE-ONLY'])
        stream.write('before FAKE-SENSITIVE-ONLY after')
        self.assertEqual(output.getvalue(),'before [REDACTED] after')

    def test_child_environment_is_allowlisted(self):
        values=cli.clean_environment({'PATH':'test','NEXORAI_API_KEY':'FAKE','MINIMAX_H3_API_KEY':'FAKE',
                                     'UNRELATED_SECRET':'FAKE','HF_HUB_OFFLINE':'1'})
        self.assertEqual(values,{'PATH':'test','HF_HUB_OFFLINE':'1'})

    def test_system_environment_names_are_matched_case_insensitively_without_keys(self):
        source={'SYSTEMROOT':'test-windows-directory','LocalAppData':'test-local-directory',
                'PATH':'test-path','nexorai_api_key':'FAKE','Unrelated_Secret':'FAKE'}
        values=cli.clean_environment(source)
        self.assertEqual(values,{k:source[k] for k in ('SYSTEMROOT','LocalAppData','PATH')})

    @unittest.skipUnless(os.name=='nt','Windows resolver environment contract')
    def test_real_sanitized_windows_child_resolves_localhost(self):
        child=subprocess.run([sys.executable,'-I','-c',
                              "import os,socket; assert os.environ.get('SYSTEMROOT'); assert socket.getaddrinfo('localhost',443)"],
                             env=cli.clean_environment(os.environ),capture_output=True,text=True,timeout=15)
        self.assertEqual(child.returncode,0,child.stderr)

    def test_unknown_credential_name_is_refused_before_reading(self):
        with self.assertRaises(SystemExit):cli.user_key('UNRELATED_SECRET')

    def test_process_fake_credential_is_used_without_registry(self):
        with patch.dict(cli.os.environ,{'NEXORAI_API_KEY':'FAKE-ONLY'}):
            self.assertEqual(cli.user_key('NEXORAI_API_KEY'),'FAKE-ONLY')

    def test_scope_escape_and_unknown_repost_are_refused(self):
        root=cli.ROOT.parent/'作品/全链验收-雪夜推门-20261107-fake'
        for args in (['query','--task-dir',str(root.parent/'other/tasks/a/attempt-001')],
                     ['query','--all'],['submit','--retry-unknown']):
            with self.assertRaises(SystemExit):cli.validate_arguments('h3',args,root)

    def test_live_submit_stays_locked_without_core_readiness(self):
        root=cli.ROOT.parent/'作品/全链验收-雪夜推门-20261107-not-created'
        with self.assertRaises(SystemExit):cli.validate_arguments('h3',['submit','--execute'],root)

    def test_reference_and_positional_media_paths_are_scoped(self):
        root=cli.ROOT.parent/'作品/全链验收-雪夜推门-20261107-fake'
        with self.assertRaises(SystemExit):cli.validate_arguments('h3',['prepare','--ref','人物=../../outside.png'],root)
        with self.assertRaises(SystemExit):cli.validate_arguments('align',['align','../../outside.wav'],root)
        cli.validate_arguments('h3',['prepare','--ref','人物=角色/allowed.png'],root)

    def test_actual_child_process_does_not_inherit_credentials(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'NEXORAI_API_KEY':'FAKE-CHILD-ONLY','MINIMAX_H3_API_KEY':'FAKE-CHILD-ONLY'}):
            root=Path(folder);(root/'approvals').mkdir()
            (root/'approvals/runtime-tools.json').write_text(json.dumps({'version':1,'tools':{
                'python':{'path':sys.executable,'sha256':cli.file_digest(sys.executable)}}}),encoding='utf-8')
            with cli.guarded_children(root):
                child=subprocess.run([sys.executable,'-I','-c',
                    "import os; print(any(k in os.environ for k in ('NEXORAI_API_KEY','MINIMAX_H3_API_KEY')))"] ,
                    capture_output=True,text=True,check=True)
        self.assertEqual(child.stdout.strip(),'False')

    def test_runtime_binary_is_fixed_by_path_and_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            binary=Path(folder)/'ffmpeg.exe';binary.write_bytes(b'fixture-not-executed')
            tools={'ffmpeg':{'path':str(binary),'sha256':cli.file_digest(binary)}}
            self.assertEqual(cli.checked_binary('ffmpeg',tools),str(binary))
            with self.assertRaises(SystemExit):cli.checked_binary(str(Path(folder)/'other.exe'),tools)
            binary.write_bytes(b'changed')
            with self.assertRaises(SystemExit):cli.checked_binary('ffmpeg',tools)

    def test_global_quota_resume_is_not_authorized_by_this_scope(self):
        with self.assertRaises(SystemExit):cli.validate_arguments('image',['resume'],cli.ROOT)

    def test_real_recognition_launcher_refuses_stub(self):
        with patch.dict(os.environ,{'ASR_FAKE_RESULT':'test-stub-only'}):
            with self.assertRaises(SystemExit):cli.local_recognition_environment('align',['align','audio.wav'])

    def test_authenticated_redirect_never_reaches_second_server(self):
        received=[]
        class Destination(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                received.append(True);self.send_response(200);self.end_headers()
        second=ThreadingHTTPServer(('127.0.0.1',0),Destination)
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{second.server_port}/');self.end_headers()
        first=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
        for server in (first,second):threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            request=urllib.request.Request(f'http://127.0.0.1:{first.server_port}/',headers={'Authorization':'Bearer FAKE-ONLY'})
            with cli.authenticated_requests(),self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request,timeout=2)
            self.assertEqual(caught.exception.code,302)
            self.assertEqual(received,[])
        finally:
            for server in (first,second):server.shutdown();server.server_close()


if __name__=='__main__':unittest.main(verbosity=2)
