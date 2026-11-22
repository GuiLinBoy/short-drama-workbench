# 接口、参数与任务管理

## 1. 当前执行入口

本项目在Codex会话内通过 `tools/codex_cli.py --project-scope <作品绝对目录> h3 ...` 调用。工作目录为工具根；请求、素材和输出参数使用作品内相对路径。`scripts/h3_task.py`是底层实现，以下能力说明不授权绕过受控入口。

离线 `prepare`、`set-continuity` 不读凭证、不访问平台；其他操作须按入口判定核对本片就绪、操作授权和运行时。真实提交另核对用户费用批准、批次/次数/返工、全局占用、当前画布请求/参考/attempt指纹。`--execute`或导演同意都不能代替这些检查。见 [Codex工作流](../../../tools/CODEX_WORKFLOW.md) 和 [画布审核](../../../tools/CANVAS_REVIEW.md)。

凭证只由受控工具在授权通过后读取；账户余额、平台skill列表等也是外部请求，不为了写提示词自行调用。不要读取/输出认证文件、密钥、Data URL或base64。

## 2. 参数来源与本地默认

依据：[CompShare H3文档](https://www.compshare.cn/docs/minimax-h3/api/minimax-h3-video-api)及本地适配器；表中渠道资料主要在2026-10-31读取，费用在2026-11-07复核，并非本次实时账户探测。当前实际请求与交付证据优先，不用Higgsfield的模型名或参数替换。

| 参数 | 本地适配器的取值/能力记录 | 当前使用规则 |
|---|---|---|
| `model` | `MiniMax-H3`、`minimax-h3-lite` | 默认标准版；不因其他渠道的H3/Max名称换模型 |
| `duration` | 4–30整数秒；超过15秒要求Context-IR及显式`--allow-long` | 本项目当前默认4–15秒；更长实现/质量未核实，不自行启用 |
| `ratio` | `adaptive`、`21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16` | 按本片实际批准；`adaptive`不替代已确定画幅 |
| `resolution` | `480P`、`768P`原生；`1080P`、`2K`、`4K`为渠道超分路径 | 未指定时试生成和定稿均原生`768P`；其他档位依用户明确要求，不能称超分为原生 |
| `use_context_ir` | 布尔，本地默认关闭 | 明确选择开启才传 `--context-ir on`；沿用本片批准，开关变化要重新prepare并匹配原审核节点 |
| `skill_id` | 单个官方或自建ID | 仅Context-IR开启时可传；已保存的目录仅供选择，不能假定所有渠道都有同名ID |
| `mute_audio` | 布尔，由`--audio mute`选择静音 | 静音/带声不能靠提示词模糊猜测；改配音路线须明确 |
| `aigc_watermark` | 底层准备默认false | 只描述实现默认，不授权公开发布或免除发布标识要求 |
| `callback_url` | 平台支持公网回调的资料记录 | 当前本机链路不使用，按已授权查询方式处理 |

适配器使用封闭枚举，分辨率严格写大写`P`；不要依赖平台对错误值的容错或静默降级。实际交付需用probe和媒体核验，不能将请求的768P直接当作交付事实。

### 费用资料（历史核验值，不是本片报价或批准）

2026-11-07记录：输出每秒480P 5、768P 10、1080P 19、2K 20、4K 22积分；Context-IR每任务另加5积分。参考视频先合计时长再向上取整，480P输入每秒5积分，其余档位输入每秒10积分；图片和音频参考当时不额外计费。未来价格/能力变化须另核对，不用Higgsfield积分换算本渠道。

离线准备保留完整费用组件。提交按批次及全局流水预占，成功优先按平台回执或完整用量组件结算；用量/退款依据不足时保留占用，不能猜退款。超分降级按实际交付核算。不得手改`used`清零或让新作品重置旧unknown占用。

## 3. 素材与模式

- 首帧/尾帧槽：最多1张`first_frame`和1张`last_frame`。
- 全参考槽：图片≤9、视频≤3、音频≤3、合计≤12；音频不能单独用，至少有图或视频。
- 首尾帧槽不能与全参考槽混用。普通图用`reference`，底层映射为`reference_image`，不省略角色让平台误当首帧。
- 人物图用于身份/服装，场景图用于空间/光线；镜头构图另写。全参考中的尾帧锚点不能承诺严格首帧，详见 [模式与准备核对](mode-and-preflight.md)。
- 当前工具用本机文件内联上传；不在输出或文件里展开Data URL。图片≤30MiB、视频≤50MiB、音频≤15MiB、整请求≤72MiB为渠道资料记录；图片短边超过1080会缩小、视频会预处理为24FPS/短边720，均须以实际工具证据核对，不能把输入处理当作输出规格。
- 视频自带音轨也会输入模型。只想参考视觉，先在获准范围准备去音轨版本，保留来源。
- 提示词源稿写`[C00001-A]`等标签，工具按附件顺序为图/视频/音频分别生成`<Picture N>`/`<Video N>`/`<Audio N>`；有承接时尾帧按本地约定放首个附件。

## 4. 当前命令示例

在工具根运行。下面路径仅为结构示例，需替换为本片真实文件；不是执行授权。请求输出使用版本化文件名，不能覆盖已提交或已审核版本。

```powershell
# 离线准备；有已确认声线参考时再按实际添加，不凭空填音频
python -I -X utf8 tools/codex_cli.py --project-scope "<作品绝对目录>" h3 prepare --prompt "单集制作/EP001/视频_Clip00002-v001.prompt.txt" --output "requests/Clip00002-v001.json" --ref "C00001-A=角色/C00001-A.png" --ref "S00001=场景/S00001.png" --duration 8 --ratio 9:16 --resolution 768P --skill-id official-dialogue-performance-blocking --purpose "EP001/Clip00002 draft"

# 本地预览，不代表已批准；先解决warnings，再按实际请求送画布
python -I -X utf8 tools/codex_cli.py --project-scope "<作品绝对目录>" h3 submit --request "requests/Clip00002-v001.json" --task-dir "tasks/EP001-Clip00002/attempt-001"

# 仅各项批准与受控就绪匹配后提交一次
python -I -X utf8 tools/codex_cli.py --project-scope "<作品绝对目录>" h3 submit --request "requests/Clip00002-v001.json" --task-dir "tasks/EP001-Clip00002/attempt-001" --output "单集制作/EP001/试生成/视频_Clip00002-v001.mp4" --execute --budget "approvals/budget.json" --batch "EP001-试生成"

# 已有真实任务号且query操作获准时查询，成功后工具下载并校验
python -I -X utf8 tools/codex_cli.py --project-scope "<作品绝对目录>" h3 query --task-dir "tasks/EP001-Clip00002/attempt-001"
```

`--ref`为`标签=路径[@角色]`，可用角色`reference`、`first_frame`、`last_frame`、`reference_video`、`reference_audio`。正式请求中核对映射编号、素材指纹、参数与费用；不手改请求来“修掉”warnings。

当前受控入口拒绝`--all`与`--retry-unknown`等跨范围/重发标志；不绕到底层脚本。旧版`pending --all`/`poll --all`属于历史工具能力，不是当前五岗会话的全局查询权限。

## 5. 实际尾帧承接

编剧在新版本源稿中为依赖真实尾帧的两句加标记，尚未生成时只写预计，不冒称已看图：

```text
[Clip00002-尾帧] 提供[Shot 1]开场状态：{{承接定义：预计人物站位、朝向和姿态}}。
[Shot 1] 开场参考[Clip00002-尾帧]：{{承接开场：预计构图}}。
```

摄影实际查看尾帧后，通过受控入口只改两句：

```powershell
python -I -X utf8 tools/codex_cli.py --project-scope "<作品绝对目录>" h3 set-continuity --prompt "单集制作/EP001/视频_Clip00003-v002.prompt.txt" --frame "单集制作/EP001/尾帧/Clip00002-v001.png" --definition "<Subject 1>站在画面左侧，面朝右，手持灰抹布。" --opening "平视双人中景，<Subject 1>在左，门在右后，左侧窗光。"
```

新内容用中文，可带技术标签；不能混入台词，每句≤600字符。工具将标记改成已核对并追加continuity日志，保存原文、新文、尾帧及SHA。`prepare`剥离标记并核对实际尾帧指纹；改成另一张尾帧必须重新查看，修改请求须重新送审，不能沿用旧批准。

## 6. 状态、并发与重发边界

| 观察 | 处理 |
|---|---|
| scheduled/moderating/queued/running之间往返 | 仍进行中，不算失败；保留任务号，按获准节奏查询 |
| succeeded | 工具下载，核对实际尺寸/时长/音轨和费用，再送结果审核；不因成功就自动发布或跑下一段 |
| failed/blocked/cancelled/rejected | 保存平台状态及计费证据；核对是否明确释放占用，不凭错误文字猜退款；blocked不自动改词重试 |
| unknown/提交超时/5xx且无任务号 | 保持保守占用，上报总导演；不重发、不新开attempt；必要时另走用户明确批准的核对/例外流程 |
| 退出码4且报告明确为并发已满、未提交 | 排队；保留未提交attempt。稍后只有在授权、请求和审核仍有效时才可原attempt首次提交，不冒充失败重做 |

底层幂等键包含请求哈希、任务名和attempt，可帮助识别重复，但不能保证供应商永久保留或永不重复计费，不能据此自动重发。每个attempt只提交一次；新attempt是新的真实生成，需本片批准和预算。不能将“本地被放弃”当作远端取消/并发释放。

查询节奏由当前活动会话执行，可按本片既定计划约5分钟检查一次；等待超过60分钟上报并保留任务号，不自动重新提交。没有已配置的后台任务就不声称会话结束后仍轮询。下载链接有效期资料未明确，成功后在授权范围及时保存本地。
