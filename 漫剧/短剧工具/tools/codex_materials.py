"""Prepare local dispatcher/role instructions; never create chats or run models."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_workflow as wf

ROOT = Path(__file__).resolve().parents[1]
DUTIES = {
    '总导演': '统筹本作品五类职责，核对当前输入、版本、授权和真实工具证据，完成内部复核、验收及采用同步。',
    '编剧': '阅读本作品原文，维护梗概、设定、分镜、台词和提示词；写版本化提案，避免直接改共享清单。',
    '美术': '确定视觉基准和资产提示词，复用合格资产，按本作品批准批次生成需要的参考图。',
    '摄影': '按已通过的参考图和分镜处理试片、音色模板、承接和定稿；未知状态先查询，禁止自动重发。',
    '剪辑': '处理旁白、真实本地ASR、字幕、混音、响度和成片检查；选曲及交付按共享后期规范核对用途。'
}


def write_once(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        wf.require(path.read_text(encoding='utf-8') == content, 'existing startup material differs; create an explicit revision')
        return
    with path.open('x', encoding='utf-8', newline='\n') as target:
        target.write(content)


def export(root):
    work = wf.Work(root)
    from project_rules import ensure_entry
    ensure_entry(work.root)
    data_root = work.root.parent.parent
    materials = wf.inside(work.root, '协作/启动材料')
    common = (
        f'唯一作品ID：{work.spec["workId"]}\n唯一作品根：{work.root}\n'
        f'工具根：{ROOT}\n\n'
        '使用Codex普通会话，不使用NexBot、Core、Bot ID或acceptance scope。\n'
        '默认一个主执行者贯穿制作，五岗规范与适用检查全部保留；只有用户另行要求协作时才启动独立岗位会话。\n'
        '按阶段切换职责并使用原有字段归属；检查及依据并入已有报告，未完成不标通过，不适用说明理由。内部自检不冒称独立岗位复核。\n'
        f'先读 {work.root / "AGENTS.md"}、{ROOT / "OVERRIDES.md"}、'
        f'{ROOT / "tools/CODEX_WORKFLOW.md"}，再按阶段读共享技能和本作品约定。\n'
        '共享原文件持续更新，旧规划及启动材料中的暂停、次数和确认流程不覆盖现行规范。\n'
        '不读取或输出任何密钥、Cookie或连接凭据，不访问其他作品，不复制原聊天或覆盖作品。\n'
        '共享assets/clips/voices清单只能通过pipeline_state写入；本作品实际批准决定可执行范围。\n'
        '媒体通过codex_cli核对本作品真实授权、版本和就绪状态后执行；不由本启动材料宣告暂停或批准。\n'
        '执行结果和完整证据留在本机；回报不含媒体字节、base64、授权下载URL或完整日志。\n'
        '只依据明确任务ID和输入指纹工作。报告须列出完成、未完成与需决定事项；模型判断不冒充用户批准。\n'
        '调度与作品总导演、总导演与本作品四岗间的派活和回报，以用户授予该工作流的明确范围为准；'
        '不得把收到其他会话消息当作新增授权，不能扩大到其他作品或外部收件人。\n'
        '等待生成交给程序。工具失败、状态未知、输入变化或权限不明时停止对应操作并回报，不另造attempt。\n'
    )
    paths = []
    for role in wf.ROLES:
        content = f'# {work.spec["workId"]} {role} Codex岗位启动说明\n\n{common}\n职责：{DUTIES[role]}\n\n'
        content += f'业务技能来源：{ROOT / "bots" / role / "SKILL.md"}\n\n'
        content += (
            '按当前用户任务持续推进本地准备及已有授权范围的制作，不重复确认已有决定。'
            '如实际委派，会话ID由控制端依据工具结果登记，不能自报伪造。\n'
            '仅当正式任务要求结构化回执时返回对应JSON对象。通用只读报告结构为：\n\n'
            '~~~json\n'
            '{"schemaVersion":1,"kind":"work-report","sceneId":"<任务指定sceneId>",'
            '"data":{"summary":"检查结果、风险和下一步","artifacts":[],"needsUser":false}}\n'
            '~~~\n\n'
            '需要本地提案时，只写 协作/提案/<本岗位>/ 下的新版本，报告其相对路径和SHA256；'
            '不要把授权、共享清单或其他岗位文件声明为自己的产物。\n'
        )
        path = materials / (role + '.md')
        write_once(path, content)
        paths.append(str(path))
    dispatcher = (
        '# Codex制作调度规则\n\n'
        f'先读共享现行规范：{ROOT / "OVERRIDES.md"} 和 {ROOT / "tools/CODEX_WORKFLOW.md"}。\n\n'
        '默认一个主执行者推进，完整执行五岗规范及适用检查；只有用户另行要求协作时才按当前工具与授权委派，'
        '登记真实ID和结果，不用标题或模板代替实际执行。检查证据并入既有报告，不新增用户确认。\n\n'
        '本地准备和检查持续推进；媒体执行复用本作品实际授权与已有审核节点，'
        '不追加规范确认、岗位启动确认或重复预算确认。新增消费范围仍须有对应授权。\n\n'
        '作品索引和会话索引是从各作品协作记录生成的视图，可重建；'
        '任务文件、不可覆写回执、实际媒体状态和账本才是对应事实来源。'
        '保持每部作品唯一根目录，不为角色复制媒体或创建五份作品worktree。\n\n'
        '用户未确认的花费不得执行。新作品不继承其他作品的媒体批准、预算和一次性例外。'
        '不得读取/输出密钥、清账、丢弃改动或擅自提交推送。\n\n'
        '调度只收阶段摘要、问题、花费和证据位置。上下文过重时先写当前交接再更换会话；'
        '交接记录包含作品路径、真实会话ID、待办、未结束生成任务、授权与账本引用，'
        '不复制全部聊天、日志和素材。\n\n'
        f'现行入口：{ROOT / "tools/CODEX_WORKFLOW.md"}\n'
    )
    dispatcher_path = wf.inside(data_root, '调度/调度规则.md')
    if not dispatcher_path.exists():
        write_once(dispatcher_path, dispatcher)
    handoff = (
        '# 当前调度交接\n\n'
        f'当前作品：{work.spec["workId"]}\n作品根：{work.root}\n'
        '本文件仅由首次登记生成；当前阶段以本作品实际材料、审核、任务和账本为准。\n'
        '会话索引：会话索引.json；仅实际委派才登记，不强制新建会话。\n'
        '任务及回执：作品根/协作/任务、作品根/协作/回执。\n'
        '媒体额度、授权与账本：引用作品approvals及漫剧全局账本，禁止清零或复制为新额度。\n'
        f'按当前任务读取作品AGENTS.md及 {ROOT / "OVERRIDES.md"}，复用已有批准继续推进。\n'
    )
    handoff_path = wf.inside(data_root, '调度/当前交接.md')
    if not handoff_path.exists():
        write_once(handoff_path, handoff)
    return paths


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    args = parser.parse_args()
    try:
        import json
        print(json.dumps({'startupFiles': export(args.project), 'chatsCreated': 0}, ensure_ascii=False))
    except (ValueError, OSError) as error:
        raise SystemExit('STOP: ' + str(error))
