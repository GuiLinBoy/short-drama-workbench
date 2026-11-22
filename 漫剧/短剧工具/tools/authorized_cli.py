"""Scoped CLI launcher: in-process user credentials, sanitized output/children.

No credential is accepted as an argument, printed, or stored. This launcher is
not an OS sandbox. The nex_bot scope must restrict commands and file access.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
TARGETS={'h3':ROOT/'skills/minimax-h3-video/scripts/h3_task.py',
         'image':ROOT/'skills/nexorai-image/scripts/nexorai_image.py',
         'tts':ROOT/'tools/audio/narrate.py','align':ROOT/'tools/audio/align.py',
         'assemble':ROOT/'tools/audio/assemble.py','state':ROOT/'tools/pipeline_state.py',
         'validate':ROOT/'tools/validate_project.py','tail':ROOT/'tools/ending_frame.py','wait':ROOT/'tools/wait_generation.py'}
KEY_NAMES={'h3':'MINIMAX_H3_API_KEY','tts':'MINIMAX_H3_API_KEY','image':'NEXORAI_API_KEY'}
ENV_NAMES={'PATH','HOME','USER','TMPDIR','SystemRoot','WINDIR','COMSPEC','PATHEXT','APPDATA','LOCALAPPDATA','TEMP','TMP',
           'USERPROFILE','HOMEDRIVE','HOMEPATH',
           'PYTHONIOENCODING','HF_HUB_OFFLINE','MODELSCOPE_OFFLINE','ASR_PY','ASR_MODELS',
           'ASR_LOCAL_MODEL','ASR_LOCAL_VAD','ASR_REMOTE_URL','ASR_REMOTE_TOKEN_FILE','ASR_REMOTE_DISABLE',
           'SUBTITLE_FONT','SUBTITLE_FONT_BOLD'}


def fail(message):raise SystemExit('ERROR: '+message)


def user_key(name):
    if name not in set(KEY_NAMES.values()):fail('credential name not allowed')
    value=os.environ.get(name)
    if value:return value.strip()
    if sys.platform=='darwin':
        import shutil
        sec='/usr/bin/security' if os.path.isfile('/usr/bin/security') else shutil.which('security')
        if sec:
            r=subprocess.run([sec,'find-generic-password','-s',name,'-w'],capture_output=True,text=True)
            if r.returncode==0 and r.stdout.strip():return r.stdout.strip()
        return None
    if os.name!='nt':return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment') as key:
            value,_=winreg.QueryValueEx(key,name)
            return value.strip() if isinstance(value,str) else None
    except FileNotFoundError:return None


def clean_environment(source):
    # Windows exposes environment names uppercased; SystemRoot is needed by Winsock.
    allowed={name.upper() for name in ENV_NAMES}
    return {k:v for k,v in source.items() if k.upper() in allowed}


def file_digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def runtime_tools(scope):
    try:
        data=json.loads((scope/'approvals/runtime-tools.json').read_text(encoding='utf-8'))
        if data['version']!=1 or not isinstance(data['tools'],dict):raise ValueError('runtime shape')
        if any(name not in ('python','asr_python','ffmpeg','ffprobe') for name in data['tools']):raise ValueError('runtime name')
        return data['tools']
    except (OSError,ValueError,KeyError,TypeError):fail('approved runtime tool manifest missing or invalid')


def checked_binary(program,tools):
    value=Path(program)
    name=value.name.lower().removesuffix('.exe')
    entry=None
    if value.is_absolute():
        entry=next((item for item in tools.values() if Path(item['path']).resolve()==value.resolve()),None)
    else:entry=tools.get(name)
    if not entry:fail('child executable not approved')
    path=Path(entry['path'])
    if not path.is_absolute() or file_digest(path)!=entry['sha256']:fail('approved runtime executable changed')
    return str(path)


class NoAuthenticatedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,message,headers,newurl):
        if request.has_header('Authorization'):
            raise urllib.error.HTTPError(request.full_url,code,'Authenticated redirect refused',headers,
                                         io.BytesIO(b'{"error":"authenticated_redirect_refused"}'))
        return super().redirect_request(request,fp,code,message,headers,newurl)


@contextlib.contextmanager
def authenticated_requests():
    original=urllib.request.urlopen
    opener=urllib.request.build_opener(NoAuthenticatedRedirect())
    def guarded(request,*args,**kwargs):
        if isinstance(request,urllib.request.Request) and request.has_header('Authorization'):
            return opener.open(request,*args,**kwargs)
        return original(request,*args,**kwargs)
    urllib.request.urlopen=guarded
    try:yield
    finally:urllib.request.urlopen=original


class RedactedStream:
    def __init__(self,stream,secrets):self.stream=stream;self.secrets=[v for v in secrets if v]
    def write(self,text):
        for value in self.secrets:text=text.replace(value,'[REDACTED]')
        return self.stream.write(text)
    def flush(self):return self.stream.flush()
    def reconfigure(self,**kwargs):return self.stream.reconfigure(**kwargs)
    def __getattr__(self,name):return getattr(self.stream,name)


def scope_root(path):
    scope=Path(path).resolve()
    if scope.parent!=ROOT.parent/'作品' or not scope.name.startswith('全链验收-雪夜推门-20261107-'):
        fail('scope must be the newly authorized acceptance project')
    if not (scope/'approvals/user-authorization.json').is_file() or not (scope/'approvals/execution-limits.json').is_file():
        fail('scope authorization and execution limits are required')
    return scope


def validate_arguments(tool,args,scope,require_legacy_readiness=True,allow_quota_resumption=False):
    if tool=='image' and args and args[0]=='resume' and not allow_quota_resumption:fail('quota resumption needs a separate user decision')
    if any(v in args for v in ('--all','--retry-unknown','--after-unknown','--full')):
        fail('cross-project scans, unknown reposts and full credential-like URLs are not authorized here')
    for key in ('--project','--project-root'):
        if key in args:
            try:value=Path(args[args.index(key)+1]).resolve()
            except IndexError:fail('missing project argument')
            if value!=scope:fail('tool project does not match the acceptance scope')
    path_keys=('--task-dir','--request','--prompt','--output','--out','--frame','--source','--script','--catalog',
               '--json-file','--srt','--speech-timeline','--music')
    for key in path_keys:
        if key not in args:continue
        try:value=Path(args[args.index(key)+1])
        except IndexError:fail('missing path argument')
        resolved=(scope/value).resolve()
        if not resolved.is_relative_to(scope):fail('tool file path escaped the acceptance scope')
    for i,value in enumerate(args):
        if value!='--ref':continue
        try:reference=args[i+1].split('=',1)[1]
        except (IndexError,ValueError):fail('invalid reference')
        if '@' in reference:reference=reference.rsplit('@',1)[0]
        if not (scope/reference).resolve().is_relative_to(scope):fail('reference escaped acceptance scope')
    if tool=='align' and args and args[0] in ('align','check','subtitle'):
        if len(args)<2 or not (scope/args[1]).resolve().is_relative_to(scope):fail('recognition media escaped acceptance scope')
    if tool in ('state','assemble','tts') and '--project' not in args and tool!='tts':
        fail('explicit acceptance project is required')
    if tool=='tts' and args and args[0]=='prepare' and '--project' not in args:fail('explicit project is required')
    if require_legacy_readiness and tool in KEY_NAMES and args and args[0]=='submit' and '--execute' in args:
        # This is a readiness gate, not evidence that the remote Core is deployed.
        if not (scope/'approvals/live-execution-ready.json').is_file():
            fail('live generation is locked until nex_bot shared budget and scoped bridge are verified')


def local_recognition_environment(tool,args):
    if tool!='align' or not args or args[0] not in ('align','check','subtitle'):return
    if os.environ.get('ASR_FAKE_RESULT'):fail('recognition stub is forbidden in the real acceptance launcher')
    if os.environ.get('ASR_REMOTE_URL') and os.environ.get('ASR_REMOTE_DISABLE')!='1':return
    models=ROOT.parents[1]/'环境/models/models'
    paths={'ASR_LOCAL_MODEL':models/'iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/snapshots/master',
           'ASR_LOCAL_VAD':models/'iic--speech_fsmn_vad_zh-cn-16k-common-pytorch/snapshots/master'}
    for variable,path in paths.items():
        if not path.is_dir():fail('existing local recognition model missing; no downloads allowed')
        os.environ[variable]=str(path)
    os.environ['HF_HUB_OFFLINE']='1';os.environ['MODELSCOPE_OFFLINE']='1'


@contextlib.contextmanager
def guarded_children(scope,launcher_file=None):
    original=subprocess.Popen
    approved=runtime_tools(scope)
    class ScopedPopen(original):
        def __init__(self,command,*args,**kwargs):
            if isinstance(command,(list,tuple)) and command:
                values=list(map(str,command))
                for tool,target in TARGETS.items():
                    if str(target) in values:
                        i=values.index(str(target))
                        if Path(values[0]).name.lower().startswith('python'):
                            command=[values[0],'-I',str(launcher_file or Path(__file__).resolve()),'--project-scope',str(scope),tool,*values[i+1:]]
                        break
                command=list(map(str,command))
                command[0]=checked_binary(command[0],approved)
            else:fail('shell/string child commands are not approved')
            kwargs['env']=clean_environment(kwargs.get('env') or os.environ)
            dirs=[str(Path(item['path']).parent) for item in approved.values()]
            if os.environ.get('SystemRoot'):dirs.append(str(Path(os.environ['SystemRoot'])/'System32'))
            kwargs['env']['PATH']=os.pathsep.join(dict.fromkeys(dirs))
            super().__init__(command,*args,**kwargs)
    subprocess.Popen=ScopedPopen
    try:yield
    finally:subprocess.Popen=original


def main():
    for stream in (sys.stdout,sys.stderr):stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-scope',required=True)
    parser.add_argument('tool',choices=TARGETS)
    parser.add_argument('arguments',nargs=argparse.REMAINDER)
    a=parser.parse_args();scope=scope_root(a.project_scope)
    for name,allowed in (('H3_BASE_URL','https://cp.compshare.cn'),('NEXORAI_BASE_URL','https://api.nexorai.com.cn/v1')):
        if os.environ.get(name,allowed).rstrip('/')!=allowed:fail('live endpoint override refused before loading credentials')
    previous=Path.cwd();os.chdir(scope)
    try:
        validate_arguments(a.tool,a.arguments,scope)
        local_recognition_environment(a.tool,a.arguments)
        approved=runtime_tools(scope)
        original_path=os.environ.get('PATH')
        os.environ['PATH']=os.pathsep.join(dict.fromkeys(str(Path(item['path']).parent) for item in approved.values()))
        variable=KEY_NAMES.get(a.tool)
        secret=user_key(variable) if variable else None
        if variable and secret:os.environ[variable]=secret
        with contextlib.redirect_stdout(RedactedStream(sys.stdout,[secret])),contextlib.redirect_stderr(RedactedStream(sys.stderr,[secret])),guarded_children(scope),authenticated_requests():
            sys.path.insert(0,str(TARGETS[a.tool].parent))
            sys.argv=[str(TARGETS[a.tool]),*a.arguments]
            runpy.run_path(str(TARGETS[a.tool]),run_name='__main__')
    finally:
        if a.tool in KEY_NAMES:os.environ.pop(KEY_NAMES[a.tool],None)
        if 'original_path' in locals():
            if original_path is None:os.environ.pop('PATH',None)
            else:os.environ['PATH']=original_path
        os.chdir(previous)


if __name__=='__main__':main()
