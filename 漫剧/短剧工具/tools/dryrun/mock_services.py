"""Loopback-only provider stand-ins. Never log credentials or media data URLs."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from email import policy
from email.parser import BytesParser
from fixtures import png


class MockServices:
    def __init__(self,root):
        self.root=root
        root.mkdir(parents=True)
        names=('PATH','SystemRoot','WINDIR','COMSPEC','PATHEXT','LOCALAPPDATA')
        self.media_env={k:os.environ[k] for k in names if k in os.environ}
        self.media_env.update(TEMP=str(root),TMP=str(root))
        self.jobs={}
        self.keys={}
        self.events=[]
        self.images=[]
        self.lock=threading.RLock()
        self.image_hold=threading.Event()
        self.image_hold.set()
        self.image_started=threading.Event()
        self.httpd=ThreadingHTTPServer(('127.0.0.1',0),self.handler())
        self.base=f'http://127.0.0.1:{self.httpd.server_port}'

    def handler(self):
        service=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass

            def send(self,code,obj=None,raw=None,ctype='application/json'):
                raw=json.dumps(obj,ensure_ascii=False).encode() if raw is None else raw
                self.send_response(code)
                self.send_header('Content-Type',ctype)
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError):pass
                with service.lock:service.events.append({'method':self.command,'path':self.path,'http':code})

            def do_GET(self):
                if self.path=='/minimax/v2/query/point_usage_summary':
                    return self.send(200,{'available_points':100000,'total_points':100000})
                if self.path=='/audio/v1/pricing':
                    return self.send(200,{'enabled':True,'costs':{'text_to_speech':1},'unit_characters':100,
                                          'balance':{'available_points':100000}})
                if self.path.startswith('/media/'):
                    job=service.jobs.get(self.path.rsplit('/',1)[-1])
                    if not job:return self.send(404,{'error':'unknown mock media'})
                    if job.get('download_failures',0):
                        job['download_failures']-=1
                        return self.send(500,{'error':'SIMULATED download failure'})
                    return self.send(200,raw=job['file'].read_bytes(),ctype='audio/wav' if job['provider']=='speech' else 'video/mp4')
                tid=self.path.rsplit('/',1)[-1]
                job=service.jobs.get(tid)
                if not job:return self.send(404,{'error':'unknown mock job'})
                if job.get('query_failures',0):
                    job['query_failures']-=1
                    return self.send(500,{'error':'SIMULATED query failure'})
                job['queries']+=1
                status='queued' if job['queries']==1 else 'succeeded'
                job['status']=status
                task={'task_id':tid,'id':tid,'status':status,'point_status':'consumed' if status=='succeeded' else 'reserved'}
                if job['provider']=='speech':
                    task.update(point_cost=job['actual'],billable_characters=job['characters'],output_url=service.base+'/media/'+tid)
                else:
                    task.update(resolution=job['resolution'],usage={'points':job['actual'],'output_seconds':job['duration']},
                                content={'url':service.base+'/media/'+tid})
                return self.send(200,{'task':task})

            def do_POST(self):
                # Only the known fake key is ever accepted by this stand-in.
                if self.headers.get('Authorization')!='Bearer sk-ml-DRYRUN-FAKE':
                    return self.send(401,{'error':'mock requires fake credentials'})
                raw=self.rfile.read(int(self.headers['Content-Length']))
                if self.path.startswith('/v1/images/'):
                    if 'multipart/form-data' in self.headers.get('Content-Type',''):
                        message=BytesParser(policy=policy.default).parsebytes(('Content-Type: '+self.headers['Content-Type']+'\r\n\r\n').encode()+raw)
                        body={p.get_param('name',header='content-disposition'):p.get_payload(decode=True).decode()
                              for p in message.iter_parts() if not p.get_filename()}
                    else:body=json.loads(raw)
                    service.image_started.set()
                    if not service.image_hold.wait(30):return self.send(500,{'error':'mock image hold timed out'})
                    ratio=body.get('aspect_ratio','16:9')
                    n=int(body.get('n',1))
                    with service.lock:service.images.append({'prompt_hash':hashlib.sha256(body.get('prompt','').encode()).hexdigest(),'n':n})
                    return self.send(200,{'data':[{'b64_json':base64.b64encode(png(ratio,body.get('prompt',''))).decode()} for _ in range(n)]})
                body=json.loads(raw)
                provider='speech' if self.path=='/audio/v1/speech/jobs' else 'h3' if self.path=='/minimax/v2/video_generation' else None
                if not provider:return self.send(404,{'error':'unsupported mock endpoint'})
                key=self.headers.get('Idempotency-Key')
                if not key:return self.send(400,{'error':'idempotency key required'})
                with service.lock:
                    if key in service.keys:return self.send(200,{'task_id':service.keys[key]})
                    tid=f'{provider}-{len(service.jobs)+1:04d}'
                    job={'id':tid,'provider':provider,'queries':0,'status':'queued','key_hash':hashlib.sha256(key.encode()).hexdigest()}
                    if provider=='speech':
                        if body.get('voice',{}).get('voice_id')!='dryrun-voice':return self.send(400,{'error':'unresolved mock voice'})
                        job['characters']=sum(c.isalnum() for c in body['text'])
                        job['actual']=round(job['characters']*.02,2)
                        job['file']=service.root/f'{tid}.wav'
                        command=['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=660:duration=1.2','-ar','16000','-ac','1',str(job['file'])]
                    else:
                        job.update(duration=body['duration'],resolution=body['resolution'],ratio=body['ratio'])
                        job['actual']=round(body['duration']*({'480P':5,'768P':10}[body['resolution']])*.8,2)
                        job['file']=service.root/f'{tid}.mp4'
                        rw,rh=map(int,body['ratio'].split(':'))
                        scale=30 if body['resolution']=='480P' else 48
                        command=['ffmpeg','-v','error','-f','lavfi','-i',f'testsrc2=s={rw*scale}x{rh*scale}:r=12:d={body["duration"]}',
                                 '-f','lavfi','-i',f'sine=frequency=220:duration={body["duration"]}',
                                 '-vf',f"drawtext=fontfile='D\\:/fonts/SourceHanSansSC-Regular.otf':text='SIMULATED {tid}':fontsize=22:fontcolor=white:x=12:y=12",
                                 '-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(job['file'])]
                    p=subprocess.run(command,capture_output=True,env=service.media_env,timeout=90)
                    if p.returncode:return self.send(500,{'error':'mock fixture encoding failed','detail':p.stderr.decode('utf-8','replace')[-300:]})
                    service.jobs[tid]=job
                    service.keys[key]=tid
                return self.send(200,{'task_id':tid})
        return Handler

    def start(self):
        threading.Thread(target=self.httpd.serve_forever,daemon=True).start()
        return self

    def close(self):
        self.image_hold.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def report(self):
        return {'mode':'SIMULATED','jobs':[{k:v for k,v in j.items() if k not in ('file',)} for j in self.jobs.values()],
                'images':self.images,'events':self.events}
