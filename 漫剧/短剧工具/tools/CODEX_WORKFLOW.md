# Codex本地制作入口

先读 [OVERRIDES.md](../OVERRIDES.md)，按阶段读取对应岗位及题材。默认一个主执行者切换五岗职责，专业检查全部保留；用户明确要求协作时再协调任务。

## 每段开始或恢复

1. 读取当前作品配置、片段清单、素材/音色、真实审核和相邻片段，按 [SHOT_CONTEXT.md](SHOT_CONTEXT.md) 核对上段实际状态、本段怎么接、给下段留下什么。缺项先补齐；独立起幅说明依据，不强制每段上传尾帧。
2. 只准备当前授权范围内的内容。H3提示词、模式和素材映射按 [H3技能](../skills/minimax-h3-video/SKILL.md)；受控离线prepare之后按 [执行检查](H3_EXECUTION_CHECKS.md) 完成13组自动核对与6项内部复核。三项衔接并入现有复核，不新建审核节点。
3. [画布](CANVAS_REVIEW.md) 沿用原节点审核当前请求/结果，每项只展示一份当前版本。已有有效批准复用；来源变化使内部复核失效，不伪造批准或覆盖冻结材料。
4. 用户接受结果后同轮同步采用。首次开口视频按 [音色收尾](VOICE_CLOSEOUT.md) 同轮提取、登记、送声音节点；音色通过后同步采用供后续实际引用。生成后查看首中尾、动作、视线及片尾，听辨台词、声音和口型，回填与预计状态的差异。
5. 后期、选曲、发布交付读取 [后期规范](../skill/references/post-production.md)，运行适用自动检查；文件或ASR检查不替代实际听看。

## 本地命令

在共享工具目录执行，作品参数始终使用绝对目录：

```powershell
python -I -X utf8 tools/shot_context.py --project <作品绝对目录> --clip EP001/Clip00002
python -I -X utf8 tools/codex_workflow.py --project <作品绝对目录> status
python -I -X utf8 tools/voice_lifecycle.py --project <作品绝对目录> --require prepared
python -I -X utf8 tools/voice_lifecycle.py --project <作品绝对目录> --require confirmed
python -I -X utf8 tools/h3_execution_checks.py --project <作品绝对目录> check --request <请求路径>
python -I -X utf8 tools/h3_execution_checks.py --project <作品绝对目录> record --request <请求路径> --assessments <内部复核路径>
python -I -X utf8 tools/run_codex_checks.py
```

实际媒体操作统一经 `tools/codex_cli.py --project-scope <作品绝对目录> h3|image ...`。离线准备和检查不读取服务凭据、不提交生成。真实操作另外核对当前用户授权、精确请求/批次/attempt、预算、限额、任务状态和代码指纹；`unknown` 不因等待超时自动重发。

`assets.json`、`clips.json`、`voices.json` 仅经 `pipeline_state.py` 按岗位字段归属写入。使用当前作品证据，不从旧片复制声音、预算、批准或重试例外。必要的纯本地后处理使用已固定的FFmpeg/FFprobe、新版本输出并记录SHA及测量。

## 新作品与规范继承

`codex_workflow.py init` 按本作品实际场次、时长、授权和限额登记，并自动写入共享规范链接；相同登记幂等，不覆盖不同工作区。未登记工作区而直接创作时，在建立 `project.json` 后执行 `tools/project_rules.py --project <作品绝对目录> --install`，不增加确认或消费。

默认规格见OVERRIDES；人物、素材、音色和发布用途来自本片。`make_sample_project.py`、dryrun及固定试片配方只用于隔离演练。共享规范实时读取，不制作规范快照。代码变更后按 `codex_cli.runtime_pin_paths(root)` 内部核对完整指纹，不增加消费范围或自动开启生成。

## 可选协调与历史说明

用户明确要求多执行者协作时，核对实际会话/任务/输入指纹和回执，不伪造执行或复核来源；运行或未知任务不因换岗自动重做。接口关联和旧试片资料见 [历史调度记录](CODEX_WORKFLOW_HISTORY.md)，按需读取。其旧暂停、固定配方、强制五岗会话和具体返工次数不作为当前规则。
