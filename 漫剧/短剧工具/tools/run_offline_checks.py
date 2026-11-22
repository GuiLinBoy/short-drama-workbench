"""Run tests under a recursive Python network guard, with project-local scratch/logs.

The guard blocks external socket connections and DNS in Python children, including
HTTP redirects. Native ffmpeg commands in these tests use local media only.
"""
import ipaddress
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve()
ROOT = HERE.parent.parent


def guarded(script, args):
    def audit(event, values):
        if event in ('socket.connect', 'socket.getaddrinfo'):
            host = values[1][0] if event == 'socket.connect' else values[0]
            try:
                allowed = ipaddress.ip_address(host).is_loopback
            except ValueError:
                allowed = host == 'localhost'
            if not allowed:
                raise RuntimeError('OFFLINE GUARD: external network forbidden')
    sys.addaudithook(audit)
    original = subprocess.Popen

    class GuardedPopen(original):
        def __init__(self, command, *a, **kw):
            if isinstance(command, (list, tuple)) and Path(str(command[0])).name.lower().startswith('python'):
                cmd = list(map(str, command))
                # All project tests launch Python scripts with optional -I/-X.
                i = 1
                while i < len(cmd) and cmd[i].startswith('-'):
                    i += 2 if cmd[i] in ('-X', '-W') else 1
                if i < len(cmd) and cmd[i].endswith('.py') and Path(cmd[i]).resolve() != HERE:
                    command = [cmd[0], '-I', str(HERE), '--child', cmd[i], *cmd[i+1:]]
            super().__init__(command, *a, **kw)

    subprocess.Popen = GuardedPopen
    tempfile.tempdir = os.environ['TEMP']
    sys.path.insert(0, str(Path(script).resolve().parent))
    sys.argv = [script, *args]
    runpy.run_path(script, run_name='__main__')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        guarded(sys.argv[2], sys.argv[3:])
        return
    run = ROOT / '.offline-checks' / (time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid()))
    run.mkdir(parents=True)
    # Whitelist names BEFORE reading values; never load actual credentials.
    names = ('PATH', 'SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT', 'LOCALAPPDATA')
    env = {k: os.environ[k] for k in names if k in os.environ}
    scratch = run / 'scratch'
    scratch.mkdir()
    env.update(TEMP=str(scratch), TMP=str(scratch),
               MINIMAX_H3_API_KEY='OFFLINE-FAKE-KEY', NEXORAI_API_KEY='OFFLINE-FAKE-KEY',
               H3_BASE_URL='http://127.0.0.1:9', NEXORAI_BASE_URL='http://127.0.0.1:9/v1',
               HF_HUB_OFFLINE='1', MODELSCOPE_OFFLINE='1', PYTHONIOENCODING='utf-8')
    suites = sys.argv[1:] or ['tools/test_generation_regressions.py']
    results = []
    for suite in suites:
        with (run / (Path(suite).stem + '.log')).open('wb') as out:
            p = subprocess.run([sys.executable, '-I', str(HERE), '--child', str(ROOT / suite)],
                               env=env, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, timeout=600)
        lines = (run / (Path(suite).stem + '.log')).read_text(encoding='utf-8', errors='replace').splitlines()
        result = {'suite': suite, 'returncode': p.returncode,
                  'summary': [x for x in lines if x.startswith(('Ran ', 'OK', 'FAILED'))]}
        results.append(result)
        print(json.dumps(result), flush=True)
    (run / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8', newline='\n')
    print(str(run), flush=True)
    raise SystemExit(int(any(x['returncode'] for x in results)))


if __name__ == '__main__':
    main()
