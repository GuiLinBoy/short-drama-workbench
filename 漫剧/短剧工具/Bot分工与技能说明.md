# 漫剧制作组：岗位分工与技能入口

先读 [OVERRIDES.md](OVERRIDES.md)。默认一个主执行者按阶段承担五岗职责，全部专业规范和适用检查保留；用户明确要求协作时才另行委派。当前工具、字段归属和参数各由下列文件维护，本页只负责定位，不重复定义。

## 一、制作入口

- 本地制作和续做：[CODEX_WORKFLOW.md](tools/CODEX_WORKFLOW.md)。
- 配置、素材与状态字段：[数据规范 v2](数据规范-v2.md)；素材/片段/音色清单只通过 [pipeline_state.py](tools/pipeline_state.py) 写入。
- 每段开工：[当前状态与上下段衔接](tools/SHOT_CONTEXT.md)；生成提交：[H3执行检查](tools/H3_EXECUTION_CHECKS.md)。
- 结果接受：[采用与音色收尾](tools/VOICE_CLOSEOUT.md)；审核及当前版本：[画布规范](tools/CANVAS_REVIEW.md)。
- 参数、题材及实际授权依据本作品文件；不从历史试片复制暂停、费用、音色或例外。

## 二、五岗职责与完整规范

### 2.1 总导演

负责范围、当前版本、授权核对、内部专业复核、用户审核后的采用同步，以及阶段收尾。详见 [总导演SKILL](bots/总导演/SKILL.md)。核查实际结果，不把技术校验替代用户批准；内部核对不增加用户确认。

### 2.2 编剧

负责原文/剧情、分镜、已批准对白、开场/片尾安排及最终提示词。详见 [编剧SKILL](bots/编剧/SKILL.md)、[H3技能](skills/minimax-h3-video/SKILL.md)。依据实际上下段更新，不把预计画面写成已观察事实。

### 2.3 美术

负责人物、场景、构图及必要道具参考的准备和实际看图自检。详见 [美术SKILL](bots/美术/SKILL.md)、[NexorAI技能](skills/nexorai-image/SKILL.md)。复用本片已允许且适用的素材，三视图和独立道具图按需求决定，不默认增加生成。

### 2.4 摄影

负责素材输入、音色绑定、真实尾帧核对、受控生成及结果首中尾/声音检查。详见 [摄影SKILL](bots/摄影/SKILL.md)、[H3技能](skills/minimax-h3-video/SKILL.md)。来源、版本、任务和费用均以实际记录为准。

### 2.5 剪辑

负责音画同步、对剪、旁白/配乐、字幕、响度和交付。详见 [剪辑SKILL](bots/剪辑/SKILL.md)、[后期规范](skill/references/post-production.md)。修剪改变采用版时核对受影响尾帧、音色与后续衔接。

## 三、按需读取

题材由本片 project.json.genre 指定，见 [题材索引](genres/README.md)。H3技术细节按 [技能路由](skills/minimax-h3-video/SKILL.md) 选择相关参考页。新稿中文、技术标记保留，旧英文例子只作结构参考。

历史模型比较、NexBot配置、试片配方和实施进度保存在 [历史说明](Bot分工与技能说明-历史.md)，仅在需要追溯时读取，不作为当前执行限制。
