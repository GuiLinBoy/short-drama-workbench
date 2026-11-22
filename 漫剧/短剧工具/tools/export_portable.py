"""Export an allowlisted, credential-free Windows workflow deployment ZIP."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TREES = ('bots', 'genres', 'profiles', 'skill', 'skills', 'tools', 'canvas')
DOCS = ('OVERRIDES.md', '数据规范-v2.md', '镜头语言手册.md', '设计文档.md', 'Bot分工与技能说明.md')
SUFFIXES = {'.md', '.py', '.json', '.js', '.jsx', '.mjs', '.css', '.html', '.cmd', '.conf'}
SKIP = {'.git', '__pycache__', 'node_modules', '.qa', 'dist', '.offline-checks', '修复记录'}
SENSITIVE = re.compile(r'(?i)(?:^\.env|credential|password|secret|\.pem$|\.key$|\.log$)')


def selected_files():
    for name in DOCS:
        yield ROOT / name
    for name in TREES:
        for path in sorted((ROOT / name).rglob('*')):
            relative = path.relative_to(ROOT)
            if any(part in SKIP for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError(f'Symlink refused: {relative}')
            if not path.is_file():
                continue
            if SENSITIVE.search(path.name):
                raise ValueError(f'Unexpected sensitive filename: {relative}')
            if path.suffix in SUFFIXES or path.name == '.npmrc':
                yield path
    # Referenced API/research prose, without one-off live probe scripts or captures.
    for name in ('参考文档', '调研'):
        for path in sorted((ROOT / name).rglob('*.md')):
            if path.is_symlink():
                raise ValueError('Symlink refused')
            yield path


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


def build(output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Output already exists; use a new versioned filename')
    with tempfile.TemporaryDirectory(prefix='manju-export-') as temporary:
        stage = Path(temporary) / '短剧制作工作台'
        package_tools = stage / '漫剧/短剧工具'
        for source in selected_files():
            target = package_tools / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        # Build fresh assets into staging: never delete the live canvas dist.
        npm = shutil.which('npm.cmd') or shutil.which('npm')
        if not npm:
            raise ValueError('npm required to build the portable export')
        subprocess.run([npm, 'run', 'build', '--', '--outDir', str(package_tools / 'canvas/dist')],
                       cwd=ROOT / 'canvas', check=True)
        shutil.copyfile(ROOT / 'tools/portable/README.md', stage / '部署说明.md')
        write(stage / 'AGENTS.md', '# 短剧制作工作区\n\n'
              '制作前读取 漫剧/短剧工具/OVERRIDES.md 和 漫剧/短剧工具/tools/CODEX_WORKFLOW.md，'
              '按当前阶段读取五岗、题材、H3及后期规范。默认一个主执行者贯穿五岗，除非用户要求，不建立独立岗位会话。\n\n'
              '本包根目录就是当前工作区，旧文档的 D:\\ai剧集制作 是路径示例。新电脑先读 部署说明.md，'
              '配置实际 ASR 和字体路径，按本机安装重新核验运行时指纹。制作工具始终绑定作品绝对目录。'
              '不继承其他作品的媒体批准、费用、声线和一次性例外，不伪造审核。\n\n'
              '纯本地画布开发不加载制作阶段文档。电脑控制完成后停止控制，不主动结束辅助进程。\n')
        write(stage / '启动画布.cmd', '@echo off\r\nchcp 65001 >nul\r\nsetlocal\r\ncall "%~dp0漫剧\\短剧工具\\canvas\\start.cmd" %*\r\n')
        write(stage / '检查环境.cmd', '@echo off\r\nchcp 65001 >nul\r\nsetlocal\r\npython -I -X utf8 "%~dp0漫剧\\短剧工具\\tools\\portable\\doctor.py"\r\npause\r\n')
        # Fresh defaults: no original account state, approvals, tasks or ledger.
        write(stage / '漫剧/全局设置.json', json.dumps({
            'h3': {'max_running_tasks': 3},
            'nexorai': {'images_cap_per_day': 50, 'images_per_batch': 12, 'max_running': 2},
            'active_projects_max': 3}, ensure_ascii=False, indent=2) + '\n')
        write(stage / '漫剧/作品/说明.md', '每部新作品放在独立子目录；本包不包含真实作品。\n')
        write(stage / '漫剧/曲库/说明.md', '按共享后期规范登记有适用许可的配乐；本包不包含音乐。\n')
        # A source-neutral experience index; actual reusable lessons live in shared rules.
        write(stage / '漫剧/经验库.md', '# 共享经验索引\n\n'
              '视频经验：短剧工具/skills/minimax-h3-video/references/pitfalls.md\n\n'
              '生图经验：短剧工具/skills/nexorai-image/SKILL.md\n\n'
              '后期经验：短剧工具/skill/references/post-production.md\n\n'
              '题材与镜头：短剧工具/genres/、短剧工具/镜头语言手册.md\n')
        records = []
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                raw = path.read_bytes()
                records.append({'path': path.relative_to(stage).as_posix(), 'bytes': len(raw),
                                'sha256': hashlib.sha256(raw).hexdigest()})
        write(stage / '文件清单.json', json.dumps({'version': 1, 'created': datetime.now().isoformat(),
              'purpose': 'workflow-deployment-no-production-data', 'files': records},
              ensure_ascii=False, indent=2) + '\n')
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(stage.parent).as_posix())
        with zipfile.ZipFile(output) as archive:
            if archive.testzip() is not None:
                raise ValueError('ZIP integrity check failed')
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        write(output.with_suffix('.zip.sha256'), digest + '  ' + output.name + '\n')
        return {'zip': str(output), 'bytes': output.stat().st_size, 'files': len(records) + 1,
                'sha256': digest}


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.output), ensure_ascii=False, indent=2))
