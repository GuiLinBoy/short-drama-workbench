"""Validate a relocated export using isolated local HTTP; never contacts providers."""
import argparse
import ast
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile


def verify(archive_path):
    with tempfile.TemporaryDirectory(prefix='manju-relocated-') as temporary:
        folder = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None
            for name in archive.namelist():
                assert (folder / name).resolve().is_relative_to(folder)
                assert not any(p in {'.git', 'node_modules', '.qa', '__pycache__', 'approvals', 'tasks'}
                               for p in Path(name).parts), name
            archive.extractall(folder)
        root = folder / '短剧制作工作台'
        tools = root / '漫剧/短剧工具'
        # Integrity doctor must work from the new root with no original paths.
        subprocess.run([sys.executable, '-I', '-X', 'utf8',
                        str(tools / 'tools/portable/doctor.py')], check=True)
        scripts = list(tools.rglob('*.py'))
        secret_matches = []
        for path in root.rglob('*'):
            if not path.is_file():
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
            if re.search(r'\bsk-[A-Za-z0-9_-]{24,}\b|Bearer\s+[A-Za-z0-9_.-]{32,}', text):
                secret_matches.append(str(path.relative_to(root)))
        assert not secret_matches, 'Potential embedded credentials; inspect filenames: ' + repr(secret_matches)
        for path in scripts:
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        data = folder / 'isolated-data'
        (data / '作品').mkdir(parents=True)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-I', '-X', 'utf8', str(tools / 'canvas/server.py'),
                                    '--preview', '--data-root', str(data), '--port', str(port)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            base = f'http://127.0.0.1:{port}'
            for _ in range(80):
                try:
                    with urllib.request.urlopen(base + '/', timeout=1) as response:
                        html = response.read().decode('utf-8')
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError(process.communicate()[1].decode('utf-8', 'replace')[:1000])
                    time.sleep(.1)
            else:
                raise RuntimeError('Relocated server did not start')
            for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"', html):
                with urllib.request.urlopen(base + asset, timeout=5) as response:
                    assert response.status == 200 and len(response.read()) > 0
            request = urllib.request.Request(base + '/api/snapshot', headers={'Origin': base})
            with urllib.request.urlopen(request, timeout=5) as response:
                snapshot = json.load(response)
            assert snapshot['projects'] == [], 'Export must start without production projects'
            print(json.dumps({'zipIntegrity': True, 'relocatedServer': True, 'emptyLibrary': True,
                              'pythonScriptsParsed': len(scripts), 'credentialPatterns': 0,
                              'providerRequests': 0}, ensure_ascii=False))
        finally:
            # This process is our temporary HTTP test server, never a computer-control helper.
            process.terminate()
            process.communicate(timeout=10)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    verify(parser.parse_args().archive)
