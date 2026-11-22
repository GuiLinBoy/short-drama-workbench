"""Attach live shared production-rule links to a project, without media or approvals."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generation_runtime import exclusive_lock

TOOLS_ROOT = Path(__file__).resolve().parents[1]
BEGIN = '<!-- production-rules:begin -->'
END = '<!-- production-rules:end -->'
COMMON = (
    'OVERRIDES.md', 'tools/CODEX_WORKFLOW.md', '数据规范-v2.md',
    '镜头语言手册.md', 'skills/minimax-h3-video/SKILL.md',
    'skills/minimax-h3-video/references/audio.md',
    'skills/minimax-h3-video/references/pitfalls.md',
    'skill/references/post-production.md', 'tools/H3_EXECUTION_CHECKS.md', 'tools/VOICE_CLOSEOUT.md', 'tools/SHOT_CONTEXT.md',
    'bots/总导演/SKILL.md', 'bots/编剧/SKILL.md', 'bots/美术/SKILL.md',
    'bots/摄影/SKILL.md', 'bots/剪辑/SKILL.md',
)


def rule_sources(root):
    """Resolve current shared files, never a previous production's local files."""
    root = Path(root).resolve()
    config = json.loads((root / 'project.json').read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict):
        raise ValueError('project.json 必须是对象')
    names = list(COMMON)
    genre = config.get('genre')
    if genre is not None:
        if genre not in ('3d-guoman', 'live-action', 'urban'):
            raise ValueError('未知题材，不能解析共享规范')
        names.append(f'genres/{genre}.md')
    return {name: {'path': str(TOOLS_ROOT / name),
                   'sha256': hashlib.sha256((TOOLS_ROOT / name).read_bytes()).hexdigest()}
            for name in names}


def entry_text():
    base = TOOLS_ROOT.as_posix()
    return (f'{BEGIN}\n'
        '## 本作品的共享制作规范\n\n'
        f'先读 [现行覆盖规范](<{base}/OVERRIDES.md>) 和 '
        f'[制作入口](<{base}/tools/CODEX_WORKFLOW.md>)。这里的链接指向共享原文件，'
        '每次任务按需读取最新内容，不使用建项时的规范副本。\n\n'
        '默认由一个主执行者贯穿制作，按阶段承担五岗职责；五岗规范和适用检查全部保留，'
        '检查结果及依据并入既有报告。未完成不标通过，不适用说明理由；'
        '除非用户另行要求协作，不主动启动独立岗位会话。\n\n'
        f'- 配置及数据：[数据规范](<{base}/数据规范-v2.md>)；'
        f'按 project.json.genre 读取 {base}/genres/ 对应题材。\n'
        f'- 剧本/美术/视频/剪辑：按职责读取 {base}/bots/ 下对应 SKILL.md；'
        f'视频使用 [H3 技能](<{base}/skills/minimax-h3-video/SKILL.md>)，'
        f'镜头参考 [镜头手册](<{base}/镜头语言手册.md>)。\n'
        f'- 声音、说话人、乱语：[声音规范](<{base}/skills/minimax-h3-video/references/audio.md>)；'
        f'[H3 经验](<{base}/skills/minimax-h3-video/references/pitfalls.md>)。\n'
        f'- 首次说话视频通过后同轮提取、登记并送审音色；后续同角色视频必须引用已通过音色。'
        f'执行 [音色收尾](<{base}/tools/VOICE_CLOSEOUT.md>)，未完成不标收尾通过。\n'
        f'- 每段制作前读取当前状态并核对上段实际状态、本段接法、下段交接；'
        f'执行 [片段衔接](<{base}/tools/SHOT_CONTEXT.md>)，缺项先补齐。\n'
        f'- 后期、配乐、上传交付：[共享后期规范](<{base}/skill/references/post-production.md>)。\n\n'
        'H3 提交前检查、合成及发布交付检查使用共享 tools 中的入口，随共享代码更新生效。'
        '内部检查不增加用户确认，复用本作品已有授权与实际审核节点。'
        '其他作品的角色、声音文件、费用、审核和一次性例外不随规则继承。'
        '历史启动材料中的暂停、固定次数或强制五岗会话不覆盖现行规范。\n'
        f'{END}\n')


def ensure_entry(root):
    root = Path(root).resolve()
    rule_sources(root)  # fail locally if the shared installation is incomplete
    path = root / 'AGENTS.md'
    if not path.resolve().is_relative_to(root):
        raise ValueError('作品 AGENTS.md 不能指向作品外')
    with exclusive_lock(root / '协作/.rules.lock'):
        old = path.read_bytes().decode('utf-8-sig') if path.exists() else ''
        if BEGIN in old or END in old:
            if old.count(BEGIN) != 1 or old.count(END) != 1 or old.index(END) < old.index(BEGIN):
                raise ValueError('作品规范入口标记冲突，需内部核对，不能覆盖自定义内容')
            end = old.index(END) + len(END)
            content = old[:old.index(BEGIN)] + entry_text().rstrip('\n') + old[end:]
        else:
            content = old + ('\n\n' if old and not old.endswith('\n\n') else '') + entry_text()
        if content == old:
            return path
        fd, temp_name = tempfile.mkstemp(prefix='.rules-', suffix='.tmp', dir=root)
        try:
            with os.fdopen(fd, 'wb') as target:
                target.write(content.encode('utf-8'))
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--install', action='store_true', help='attach shared links; preserves custom instructions')
    args = parser.parse_args()
    if args.install:
        ensure_entry(args.project)
    print(json.dumps({'project': str(Path(args.project).resolve()),
                      'ruleSources': rule_sources(args.project),
                      'mediaRequests': 0, 'approvalChanges': 0}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit('STOP: ' + str(error))
