"""Read-only deployment checks; no credentials, installation or provider calls."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys


def check(root):
    root = Path(root).resolve()
    failures = []
    manifest = json.loads((root / '文件清单.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        path = (root / item['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            failures.append(item['path'])
        elif hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            failures.append(item['path'])
    print(f"文件完整性：{'通过' if not failures else '失败'}（{len(manifest['files'])} 个文件）")
    for name in failures[:10]:
        print('  缺失或已修改：' + name)
    python_ok = sys.version_info >= (3, 11)
    print(f"Python：{sys.version.split()[0]} / {'满足要求' if python_ok else '需要 3.11+'}")
    for name in ('ffmpeg', 'ffprobe', 'node', 'npm'):
        path = shutil.which(name)
        print(f"{name}：{path or '未安装或不在 PATH'}")
    default_asr = root / ('环境/asr/Scripts/python.exe' if os.name == 'nt' else '环境/asr/bin/python')
    if os.name == 'nt':
        default_font_reg = Path(r'D:\fonts\SourceHanSansSC-Regular.otf')
        default_font_bold = Path(r'D:\fonts\SourceHanSansSC-Bold.otf')
    else:
        default_font_reg = next(
            (p for p in (Path.home() / 'ManjuFonts/SourceHanSansSC-Regular.otf',
                         Path('/System/Library/Fonts/Hiragino Sans GB.ttc'),
                         Path('/System/Library/Fonts/STHeiti Light.ttc')) if p.exists()),
            Path.home() / 'ManjuFonts/SourceHanSansSC-Regular.otf',
        )
        default_font_bold = next(
            (p for p in (Path.home() / 'ManjuFonts/SourceHanSansSC-Bold.otf',
                         Path('/System/Library/Fonts/STHeiti Medium.ttc'),
                         Path('/System/Library/Fonts/Hiragino Sans GB.ttc')) if p.exists()),
            Path.home() / 'ManjuFonts/SourceHanSansSC-Bold.otf',
        )
    paths = {
        'ASR Python': Path(os.environ.get('ASR_PY', default_asr)),
        'ASR 模型缓存': Path(os.environ.get('ASR_MODELS', root / '环境/models')),
        '字幕常规字体': Path(os.environ.get('SUBTITLE_FONT', default_font_reg)),
        '字幕粗体字体': Path(os.environ.get('SUBTITLE_FONT_BOLD', default_font_bold)),
    }
    for name, path in paths.items():
        print(f"{name}：{'存在（尚需实际运行验证）' if path.exists() else '待配置'} / {path}")
    print('不读取密钥，不检查账户余额，不联网，不生成媒体。')
    print('完整制作须另核验模型、滤镜、字体、授权和本机运行时指纹；参见部署说明。')
    return 0 if python_ok and not failures else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        raise SystemExit(check(Path(__file__).resolve().parents[4]))
    except (OSError, ValueError, KeyError) as error:
        print('部署检查失败：' + str(error))
        raise SystemExit(1)
