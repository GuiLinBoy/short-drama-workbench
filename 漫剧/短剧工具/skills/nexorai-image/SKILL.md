---
name: nexorai-image
description: 美术用 NexorAI 生成角色视频参考图、三视图、场景图和道具图：按题材选模型、三参考写法、预算批次、尺寸核验、出图后自检和登记。
version: 0.1.0
---

# NexorAI 生图（美术）

- 脚本：`短剧工具/skills/nexorai-image/scripts/nexorai_image.py`（离线测试 `test_nexorai_image.py`，不消耗额度）
- 依据：`参考文档/使用NexorAI生成图片.md`、`调研/NexorAI生图模型调研.md`、`genres/<题材>.md` 第 2–3 节、设计文档 §7.7、`数据规范-v2.md`
- 环境：Python 3.11+、FFmpeg（解码、裁切、转 JPG）。密钥按顺序读环境变量 `NEXORAI_API_KEY`、Windows 凭据管理器或 macOS 钥匙串；**不要打印环境变量，不要把密钥写进任何文件**
- **不要用 `调研/probe.py`、`调研/edit_probe.py`**：那是 Mac 上一次性的实测脚本，没有预算检查

## 必须遵守

1. **先读**：`OVERRIDES.md`、这部作品的 `genres/<题材>.md`、`作品约定.md`（如果有）、`风格素材.md`。
2. **每次提交都要有批准的生图批次**（`approvals/budget.json` 里 `kind: image`、单位张数）。脚本只认这部作品自己的预算文件；还会检查全局设置的每天上限（默认 50 张）和单批上限（默认 12 张）。
3. **模型按题材配置**，脚本自动选第 1 个。要用第 2、3 个写 `--rank`；要用表外的模型写 `--model … --reason "…"`。**换模型要总导演同意**，脚本不会自动换。
4. **每个 attempt 只提交一次**，不自动重试。结果不明（超时、断线）时额度保持扣除；确认要重新生成时新开 attempt 并加 `--after-unknown attempt-NNN`。
5. **额度用完**（NexorAI 返回额度不足）：当天所有生图自动暂停，**马上报告总导演**，等你处理；不要换模型、不要换平台。
6. **出图后必须亲自看图**，自检通过才用 `pipeline_state.py` 标 `generated`；"脚本成功"不等于"图合格"。
7. 不改 `assets.json` 以外的别人负责的文件；`assets.json` 也只能通过 `pipeline_state.py` 改，而且只改归美术的字段。

## 角色图类型（默认先出视频参考图，三视图按需生成）

| 顺序 | 图 | `--kind` | 比例 | 参考与说明 |
|---|---|---|---|---|
| 1 | 视频参考图（必出，传给 H3，身份的唯一来源） | `image` | 作品画幅 | 文字生图（中性自然平光、头顶留白约 15%、完整露脚、浅色干净背景）；衍生形态以来源形态的视频参考图为 `人物` 参考 |
| 2 | 三视图设定稿（**可选**，仅在用户要求或复杂长篇需要时出） | `sheet` | `character_sheet_ratio`（默认 16:9） | **必须**以第 1 步的视频参考图为 `人物` 参考 |

场景用 `--kind scene`（无人，比例和视频一致）；道具用 `--kind prop`（单主体，背景干净）。

## 三参考写法（改图）

用 `--ref 人物=…`、`--ref 姿势=…`、`--ref 服饰=…`，**按写的顺序编号为图1、图2、图3**。提示词里按职责分开写，有几张图就编几个号：

- 图1（人物）：只取五官、脸型、发型发色、肤色、体型比例、画风；**不要**带过来原来的衣服和姿势；
- 图2（姿势）：只取四肢位置、躯干转向、重心、视角；**不要**带过来人偶线条、关节球、模特体型；
- 图3（服饰）：只取版型、颜色、层次、材质、配饰；**不要**带过来服装模特的脸、身体、站姿。

还要写：衣服随动作自然形变；只出单人成图，不拼贴、不加文字；改图写清"只修改……，其他保持不变"，一次只改一处。三张图一起融合时 Grok 是否可靠还没验证，效果不好就请总导演决定是否换 `gpt-image-2`。

## 命令

```bash
S=短剧工具/skills/nexorai-image/scripts/nexorai_image.py

# 1. 离线准备（不联网）
python $S prepare --project <作品> --asset C00001-A --kind image \
  --prompt 角色/C00001-A-林舟.prompt.txt --output tasks/C00001-A-image/request.json

# 三视图：以视频参考图为图1
python $S prepare --project <作品> --asset C00001-A --kind sheet \
  --prompt 角色/C00001-A-林舟.sheet.prompt.txt --ref 人物=<作品>/角色/C00001-A-林舟.png \
  --output tasks/C00001-A-sheet/request.json

# 2. 预览（默认，不联网）→ 在批准的批次内提交一次
python $S submit --request tasks/C00001-A-image/request.json --task-dir tasks/C00001-A-image/attempt-001
python $S submit --request tasks/C00001-A-image/request.json --task-dir tasks/C00001-A-image/attempt-001 \
  --execute --budget approvals/budget.json --batch EP001-参考图

# 3. 自检通过后登记
python 短剧工具/tools/pipeline_state.py --project <作品> --role 美术 set-asset C00001-A --set status=generated
```

其他：`fetch --task-dir …`（图已生成但下载失败时再下载，不重新生成）、`status --task-dir …`、`models`（只读列出可用模型）。`resume` 只由你在额度恢复后运行。

## 结果怎么处理

| 状态 | 额度 | 美术要做的 |
|---|---|---|
| `succeeded` | 扣除 | 看图自检；裁切过的（`action: crop`）确认主体没被切到；通过就标 `generated` |
| `needs_recompose` | 扣除 | 比例差太多（要裁掉 16% 以上），没交付；改构图或请总导演同意换模型后新开 attempt |
| `download_failed` | 扣除 | 用 `fetch` 再下载 |
| `bad_image` | 扣除 | 返回的图打不开，报告总导演 |
| `blocked` | 退回 | 内容被拦截，原因交总导演，由你决定怎么改 |
| `rate_limited` | 退回 | 按 `retry_after` 等一会儿再新开 attempt，不要连续重试 |
| `failed` | 退回 | 报告总导演 |
| `quota_exhausted` | 退回 | 当天生图已暂停，报告总导演 |
| `unknown` | 保持扣除 | 不要直接重试；见"必须遵守"第 4 条 |

## 自检清单（标 generated 之前）

- 脸型、发型、服装、全身是否完整，四肢和手指是否正常；
- 背景是否干净（视频参考图是浅色纯色或简单虚化），有没有多余文字和水印；
- 和这个角色其他形态、三视图是不是同一个人；
- 画风和 `风格素材.md` 的风格句一致；
- 写实题材：不像任何真实存在的人（`genres/live-action.md` §3）；
- 交付文件比例正确（脚本已核验），原图保留在任务目录里。

## 实战经验

（复盘确认后由 `tools/lessons.py` 追加，目前为空）

| 编号 | 日期 | 来源（作品 / 集） | 现象 | 规避方法 | 等级 | 次数 |
|---|---|---|---|---|---|---|
| L010 | 2026-11-16 | 日子是过给自己的/EP001/Clip00002构图v002-v003 | 构图图写了头顶百分比和完整小腿，实际v002仍取景偏紧；v003改为明确拉远和完整全身目标后膝部入画，但鞋底仍未完全出现。 | 以完整发顶、膝部和动作边界等可见目标写构图；生成后实看，不把12%或全身字样当作执行保证。只针对影响本镜动作的问题修复，保留未完全遵循提示词的客观记录。 | 观察 | 1 |

