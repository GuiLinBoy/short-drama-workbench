"""Read-only API, projection, live updates and local media boundary tests.

All fixtures are generated inside canvas/.qa, never in the production data root.
"""
from http.client import HTTPConnection
from contextlib import redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import server

HERE = Path(__file__).resolve().parent


class ClipTimingTests(unittest.TestCase):
    def test_measurement_requires_current_approved_file_and_full_plan(self):
        clip={'id':'EP001/Clip00001','duration':9,'status':'approved','video':{'name':'cut.mp4','exists':True}}
        adopted={'duration':118/24,'path':'cut.mp4','sha256':'a'*64}
        config={clip['id']:{'planned':5,'adopted':adopted}}
        review={'subject':clip['id'],'kind':'video','status':'approved','files':[
            {'path':'cut.mp4','sha256':'a'*64,'resource':{'exists':True}}]}
        self.assertEqual(server.apply_clip_timings([clip],[review],config),118/24)
        self.assertEqual(clip['timing']['adopted'],adopted)
        self.assertEqual(clip['duration'],9)
        review['files'][0]['sha256']='b'*64
        self.assertEqual(server.apply_clip_timings([clip],[review],config),5)
        self.assertIsNone(clip['timing']['adopted'])
        review['files'][0]['sha256']='a'*64
        review['status']='stale'
        server.apply_clip_timings([clip],[review],config)
        self.assertIsNone(clip['timing']['adopted'])
        self.assertIsNone(server.apply_clip_timings([clip,{'id':'EP001/Clip00002'}],[review],config))


class StartupPortTests(unittest.TestCase):
    def test_all_normal_cli_startups_use_fixed_8778(self):
        self.assertEqual(server.parse_arguments([]).port,8778)
        self.assertEqual(server.parse_arguments(['--read-only']).port,8778)
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            server.parse_arguments(['--port','8765'])

    def test_alternative_port_requires_explicit_isolated_data(self):
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            server.parse_arguments(['--preview','--port','8779'])
        data=HERE/'.qa/explicit-port-fixture'
        args=server.parse_arguments(['--preview','--data-root',str(data),'--port','8779'])
        self.assertEqual(args.port,8779)
        self.assertNotEqual(args.data_root.resolve(),server.HERE.parent.parent.resolve())

    def test_occupied_fixed_port_reports_error_without_retrying_other_ports(self):
        args=server.parse_arguments([])
        with patch.object(server,'parse_arguments',return_value=args), \
             patch.object(server,'Server',side_effect=OSError('occupied')) as factory:
            with self.assertRaisesRegex(SystemExit,'8778.*已被占用') as stopped:
                server.main()
        self.assertIn('未自动更换端口',str(stopped.exception))
        self.assertEqual(factory.call_count,1)
        self.assertEqual(factory.call_args.args[0],('127.0.0.1',8778))


class CanvasTests(unittest.TestCase):
    def setUp(self):
        scratch = HERE/'.qa'
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(scratch.resolve()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'漫剧'
        self.project = self.root/'作品/样例作品'
        self.project.mkdir(parents=True)
        (self.project/'角色').mkdir()
        (self.project/'角色/ref.png').write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*8+(32).to_bytes(4,'big')*2)
        self.write('project.json', {'title':'样例作品','mode':'series','genre':'3d-guoman','ratio':'9:16'})
        self.write('assets.json', [{'id':'C00001-A','type':'character','name':'测试角色','status':'generated','image':'角色/ref.png','prompt':'角色/prompt.txt'}])
        self.write('clips.json', [{'id':'EP001/Clip00001','episode':'EP001','duration':8,'status':'planned','video':'片段.mp4'}])
        (self.project/'角色/prompt.txt').write_text('本地提示词 <script>alert(1)</script>',encoding='utf-8')
        (self.project/'片段.mp4').write_bytes(b'0123456789abcdef')
        (self.project/'approvals').mkdir()
        self.write('approvals/budget.json',[{'batch':'B','kind':'video','limit':100,'used':20}])
        self.lib=server.Library(self.root)
        self.http=server.Server(('127.0.0.1',0),self.lib)
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def write(self,name,value):
        path=self.project/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8',newline='\n')

    def request(self,path,method='GET',headers=None,body=None):
        conn=HTTPConnection('127.0.0.1',self.http.server_port,timeout=3)
        conn.request(method,path,body=body,headers=headers or {})
        response=conn.getresponse()
        body=response.read()
        result=(response.status,dict(response.getheaders()),body)
        conn.close()
        return result

    def snapshot(self):
        return json.loads(self.request('/api/snapshot')[2])

    def status_request(self, status, expected='in_progress', key=None, headers=None):
        project=self.snapshot()['projects'][0]
        defaults={'Origin':f'http://127.0.0.1:{self.http.server_port}',
                  'Content-Type':'application/json','X-Canvas-Review':self.http.review_token}
        return self.request('/api/project-status','POST',{**defaults,**(headers or {})},
                            json.dumps({'project':key or project['key'],'status':status,'expectedStatus':expected}))

    def test_library_status_roundtrip_and_archive_restore_preserve_production_files(self):
        before={p:p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        self.assertEqual(self.snapshot()['projects'][0]['library_status'],'in_progress')
        for status,expected in [('completed','in_progress'),('archived','completed'),('in_progress','archived')]:
            code,_,body=self.status_request(status,expected)
            self.assertEqual(code,200,body)
            self.assertFalse(json.loads(body)['generationTriggered'])
            self.assertEqual(self.snapshot()['projects'][0]['library_status'],status)
        another=server.Library(self.root)
        self.assertEqual(another.snapshot()['projects'][0]['library_status'],'in_progress')
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        created={p.relative_to(self.project).as_posix() for p in self.project.rglob('*') if p.is_file() and p not in before}
        self.assertEqual(created,{'协作/作品状态.json'})

    def test_status_is_manual_and_stale_or_invalid_changes_do_not_overwrite(self):
        self.write('clips.json',[{'id':'clip','status':'approved'}])
        self.assertEqual(self.snapshot()['projects'][0]['library_status'],'in_progress')
        self.assertEqual(self.status_request('completed')[0],200)
        self.assertEqual(self.status_request('archived','in_progress')[0],409)
        self.assertEqual(self.snapshot()['projects'][0]['library_status'],'completed')
        self.assertEqual(self.status_request('deleted','completed')[0],400)
        self.assertEqual(self.status_request(['archived'],'completed')[0],400)
        self.assertEqual(self.status_request('archived','completed',key='../outside')[0],404)

    def test_status_endpoint_requires_current_same_origin_token_and_writable_mode(self):
        self.assertEqual(self.status_request('completed',headers={'X-Canvas-Review':'old'})[0],403)
        self.assertEqual(self.status_request('completed',headers={'Origin':'https://other.invalid'})[0],403)
        self.http.reviews_enabled=False
        self.assertEqual(self.status_request('completed')[0],403)
        self.assertFalse((self.project/'协作/作品状态.json').exists())

    def test_invalid_status_file_cannot_be_silently_overwritten(self):
        self.write('协作/作品状态.json',{'schemaVersion':1,'status':['broken']})
        self.assertEqual(self.snapshot()['projects'][0]['library_status'],'in_progress')
        original=(self.project/'协作/作品状态.json').read_bytes()
        self.assertEqual(self.status_request('completed')[0],409)
        self.assertEqual((self.project/'协作/作品状态.json').read_bytes(),original)

    def test_directory_discovery_does_not_create_registry_or_settings(self):
        before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        data=self.snapshot()
        self.assertEqual(len(data['projects']),1)
        self.assertFalse(data['registry_exists'])
        self.assertEqual(before,{str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_independent_clip_images_require_registered_sources_and_withdrawal_evidence(self):
        self.write('创作画布.json', {
            'clipReferenceImages': {'EP001/Clip00001':'C00001-A', 'EP999/Clip00001':'C00001-A'},
            'withdrawnVideoThrough': {'EP001/Clip00001':'nonexistent-review'}})
        before=(self.project/'创作画布.json').read_bytes()
        project=self.snapshot()['projects'][0]
        self.assertEqual(project['creative_clip_reference_images'], {'EP001/Clip00001':'C00001-A'})
        self.assertEqual(project['creative_withdrawn_video_through'], {})
        self.assertEqual(before,(self.project/'创作画布.json').read_bytes())

    def test_sample_and_tiny_media_are_explicit(self):
        p=self.snapshot()['projects'][0]
        self.assertEqual(p['classification'],'sample')
        self.assertTrue(p['assets'][0]['placeholder'])
        self.assertEqual(p['counts']['points_used'],20)

    def test_legacy_mock_ledger_is_not_current_spend_or_balance(self):
        (self.root/'全局账本.jsonl').write_text(json.dumps({'time':'2026-11-05T01:26:57+08:00',
             'event':'charge','provider_event':'indextts','project':'验收-第4组','points':.12,'available_points':4574.3})+'\n',encoding='utf-8')
        data=self.snapshot()
        self.assertEqual(data['history_mock_count'],1)
        self.assertIsNone(data['balance']['value'])
        self.assertNotIn('4574.3',json.dumps(data))
        self.assertEqual(data['projects'][0]['counts']['points_used'],20)

    def test_writes_are_rejected_and_files_unchanged(self):
        before=(self.project/'clips.json').read_bytes()
        for method in ('POST','PUT','PATCH','DELETE','OPTIONS'):
            self.assertEqual(self.request('/api/snapshot',method)[0],405)
        self.assertEqual(before,(self.project/'clips.json').read_bytes())

    def test_second_canvas_cannot_bind_the_same_listening_port(self):
        with self.assertRaises(OSError):
            server.Server(('127.0.0.1',self.http.server_port),self.lib)

    def test_unknown_host_and_cross_origin_are_rejected(self):
        self.assertEqual(self.request('/api/snapshot',headers={'Host':'attacker.invalid'})[0],403)
        self.assertEqual(self.request('/api/snapshot',headers={'Origin':'https://attacker.invalid'})[0],403)
        self.assertEqual(self.request('/api/snapshot',headers={'Sec-Fetch-Site':'cross-site'})[0],403)

    def test_external_document_navigation_only_allows_static_shell(self):
        headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
        self.assertNotEqual(self.request('/',headers=headers)[0],403)
        self.assertEqual(self.request('/api/snapshot',headers=headers)[0],403)
        self.assertEqual(self.request('/api/events',headers=headers)[0],403)
        self.assertEqual(self.request('/api/media/anything',headers=headers)[0],403)
        self.assertEqual(self.request('/',headers={'Sec-Fetch-Site':'cross-site'})[0],403)

    def test_private_paths_remote_media_and_traversal_are_not_read(self):
        secret=self.project/'secret.txt'
        secret.write_text('DO-NOT-EXPOSE',encoding='utf-8')
        self.write('assets.json',[{'id':'one','image':'https://example.invalid/image.png','prompt':'secret.txt'},
                                  {'id':'two','image':'../../outside.png','prompt':'../secret.txt'}])
        data=self.snapshot()
        for asset in data['projects'][0]['assets']:
            self.assertTrue(asset['image']['blocked'])
            self.assertTrue(asset['prompt']['blocked'])
        self.assertNotIn('DO-NOT-EXPOSE',json.dumps(data))
        self.assertEqual(self.request('/api/media/../../secret.txt')[0],404)
        self.assertEqual(self.request('/%2e%2e/server.py')[0],404)

    def test_media_range_and_head_for_video_seeking(self):
        media=self.snapshot()['projects'][0]['clips'][0]['video']['url']
        status,headers,body=self.request(media,headers={'Range':'bytes=2-5'})
        self.assertEqual((status,body),(206,b'2345'))
        self.assertEqual(headers['Content-Range'],'bytes 2-5/16')
        self.assertEqual(self.request(media,headers={'Range':'bytes=-3'})[2],b'def')
        self.assertEqual(self.request(media,headers={'Range':'bytes=999-1000'})[0],416)
        self.assertEqual(self.request(media,headers={'Range':'bytes=0-1,4-5'})[0],416)
        self.assertEqual(self.request(media,'HEAD')[2],b'')

    def test_prompt_references_and_recipes_keep_local_read_only_boundaries(self):
        note = self.project/'制作记录/运镜.md'
        note.parent.mkdir()
        note.write_text('静态图片本地运镜参数；未调用 H3。',encoding='utf-8')
        self.write('assets.json',[{'id':'one','image':'角色/ref.png',
            'reference_images':['角色/ref.png','../outside.png','https://example.invalid/a.png']}])
        self.write('clips.json',[{'id':'EP001/Clip00001','episode':'EP001','recipe':'制作记录/运镜.md'},
                                 {'id':'EP001/Clip00002','episode':'EP001','recipe':'../outside.md'}])
        self.write('单集制作/EP001/成片.validation.json',{'recipe':'制作记录/运镜.md'})
        project=self.snapshot()['projects'][0]
        refs=project['assets'][0]['reference_images']
        self.assertTrue(refs[0]['exists'])
        self.assertTrue(all(ref['blocked'] for ref in refs[1:]))
        for record in (project['clips'][0],project['finals'][0]):
            recipe=record['recipe']
            self.assertTrue(recipe['exists'])
            status,_,body=self.request('/api/text/'+recipe['id'])
            self.assertEqual(status,200)
            self.assertIn('未调用 H3',json.loads(body)['text'])
        self.assertTrue(project['clips'][1]['recipe']['blocked'])

    def test_etag_and_sse_detect_real_file_change(self):
        status, headers, body = self.request('/api/snapshot')
        self.assertEqual(status, 200)
        first = json.loads(body)
        tag = headers['ETag']  # Also binds the current local review session.
        self.assertEqual(self.request('/api/snapshot',headers={'If-None-Match':tag})[0],304)
        connection=HTTPConnection('127.0.0.1',self.http.server_port,timeout=5)
        connection.request('GET','/api/events')
        response=connection.getresponse()
        self.assertEqual(response.status,200)
        initial=response.readline().decode().strip()
        self.assertIn(first['revision'],initial)
        response.readline()
        self.write('clips.json',[{'id':'EP001/Clip00001','episode':'EP001','duration':8,'status':'approved'}])
        event=response.readline().decode().strip()
        self.assertTrue(event.startswith('data: '),event)
        self.assertNotEqual(initial,event)
        connection.close()
        self.assertEqual(self.snapshot()['projects'][0]['counts']['clip_approved'],1)

    def test_corrupt_states_are_visible_not_silently_successful(self):
        path=self.project/'tasks/T/attempt-001/state.json'
        path.parent.mkdir(parents=True)
        path.write_text('{broken',encoding='utf-8')
        data=self.snapshot()
        self.assertEqual(data['projects'][0]['tasks'][0]['status'],'invalid')
        self.assertTrue(any(i['code']=='invalid_json' for i in data['issues']))

    def test_queued_unknown_and_pending_billing_are_preserved(self):
        for i,status in enumerate(('queued','unknown','succeeded')):
            self.write(f'tasks/T{i}/attempt-001/state.json',{'provider':'compshare-indextts','status':status,'submitted_at':1,
                        'charge':{'settlement_pending':True,'points':.12,'actual_points':.3}})
        tasks=self.snapshot()['projects'][0]['tasks']
        self.assertEqual(sum(t['active'] for t in tasks),2)
        self.assertTrue(all(t['billing_pending'] for t in tasks))
        self.assertTrue(all(t['over_60min'] for t in tasks if t['active']))
        self.assertIsNone(next(t for t in tasks if t['status']=='succeeded')['minutes'])

    def test_registry_annotates_but_cannot_expand_root(self):
        (self.root/'作品清单.json').write_text(json.dumps([{'name':'样例作品','dir':'C:/private',
             'stage':'EP001 参考图','waiting_on':'用户核对'}]),encoding='utf-8')
        p=self.snapshot()['projects'][0]
        self.assertEqual(p['registry']['stage'],'EP001 参考图')
        self.assertEqual(len(self.snapshot()['projects']),1)

    def test_request_headers_keys_and_long_encodings_are_never_exposed(self):
        self.write('tasks/T/attempt-001/state.json',{'provider':'x','status':'unknown',
               'api_key':'DO-NOT-EXPOSE','headers':{'Authorization':'DO-NOT-EXPOSE'},
               'idempotency_key':'DO-NOT-EXPOSE','error':'Bearer DO-NOT-EXPOSE'})
        body=json.dumps(self.snapshot())
        self.assertNotIn('DO-NOT-EXPOSE',body)

    def test_text_references_are_local_and_not_html_execution(self):
        asset=self.snapshot()['projects'][0]['assets'][0]
        status,headers,body=self.request('/api/text/'+asset['prompt']['id'])
        self.assertEqual(status,200)
        self.assertTrue(headers['Content-Type'].startswith('application/json'))
        self.assertIn('<script>',json.loads(body)['text'])  # React renders this as text, not HTML.
        self.assertIn("script-src 'self'",headers['Content-Security-Policy'])


if __name__=='__main__':
    unittest.main(verbosity=2)
