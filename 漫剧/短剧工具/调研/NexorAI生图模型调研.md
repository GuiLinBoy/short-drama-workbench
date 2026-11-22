# NexorAI 生图模型调研

- 日期：2026-10-31
- 接口：`https://api.nexorai.com.cn/v1`（不收费，但消耗账号额度）
- 方法：用同一段提示词，每个模型生成 1 张 9:16 图；再挑 4 个模型做"参考图改图"测试，挑 2 个做 16:9 测试。共消耗 15 张图的额度
- 样张：[NexorAI样张/](NexorAI样张/)（已缩小到 900px，方便查看）；测试脚本：[probe.py](probe.py)、[edit_probe.py](edit_probe.py)
- **局限**：每个模型只生成了一两张，画质和风格的评价是主观的，只能作为初步排序参考。等真实项目跑起来，再根据实际效果调整

## 1. 模型清单

`GET /v1/models` 一共返回 43 个模型，其中跟生成相关的有 11 个：

| 类别 | 模型 | 实测状态 |
|---|---|---|
| Grok 生图 | `grok-imagine-image`、`grok-imagine-image-2.0`、`grok-imagine-image-quality` | ✅ 可用 |
| GPT 生图 | `gpt-image-1.5`、`gpt-image-2`、`gpt-image-2.5`、`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst` | ✅ 可用（之前文档记录 10-21 返回 503，现在已恢复） |
| Gemini 生图 | `gemini-3.1-flash-image`（走 chat 接口） | ❌ 返回 403：上游账号因违反服务条款被停用，属于平台方的问题 |
| Grok 视频 | `grok-imagine-video-1.5`、`grok-imagine-video-1.5-lite` | 本次没测；以后可以作为 MiniMax H3 之外的备选视频模型 |

其余 32 个是对话模型（Claude、GPT、Gemini、DeepSeek、GLM 等），这次没测。

## 2. 实测数据

### 2.1 文字生图（请求 9:16，提示词：3D 风格化国漫少年剑客 + 竹林 + 写着"青云"的木牌）

| 模型 | 耗时 | 实际尺寸 | 严格 9:16 | 中文字"青云" | 观察 |
|---|---|---|---|---|---|
| grok-imagine-image-quality | 8.6s | 720×1280 | ✅ | ✅ 正确 | 最接近"3D 风格化国漫"；人物干净；画面偏游戏 CG |
| grok-imagine-image-2.0 | 22.1s | 720×1280 | ✅ | ✅ 正确 | 偏写实电影感，细节和光影最好，但不太像 3D 动漫风 |
| grok-imagine-image | 9.9s | 720×1280 | ✅ | ✅ 正确 | 偏写实；**出了逻辑错误**：手里一把出鞘的剑，腰上又挂一把带鞘的剑 |
| gpt-image-2.5-sunburst | 48.9s | 1024×1536 | ❌ 2:3 | ✅ 正确 | 细节最丰富，偏 2.5D 插画；背景自己加了亭台瀑布 |
| gpt-image-2.5 | 54.7s | 1024×1536 | ❌ 2:3 | ✅ 正确 | 风格和 sunburst 几乎一样 |
| gpt-image-2.5-flare | 39.3s | 1024×1536 | ❌ 2:3 | ✅ 正确 | 风格和 2.5 几乎一样 |
| gpt-image-2 | 66.3s | 1024×1536 | ❌ 2:3 | ✅ 正确 | 自动做成了"角色设定图"版式：右侧拼了脸部、佩剑、衣纹的特写 |
| gpt-image-1.5 | 39.1s | 1024×1536 | ❌ 2:3 | ✅ 正确 | 设定图版式更完整：左侧有三视图小人、色板，右侧有特写 |

补充：
- GPT 系列请求 `864x1536`，实际一律返回 1024×1536。之前文档记录 sunburst 会返回 941×1672，这次没有复现。
- `gpt-image-2`、`gpt-image-2.5`、`gpt-image-2.5-flare` 返回的 token 用量完全相同（输出 601），画风也几乎一样，**怀疑背后是同一个模型**（无法确认，平台内部怎么映射的不清楚）。sunburst 的输出用量是 1372，明显更高。
- Grok 的返回里没有用量字段。

### 2.2 参考图改图（`/v1/images/edits`，以 grok-quality 那张图为图1，要求"同一个人物，换成夜晚屋顶、侧身拔剑"）

| 模型 | 耗时 | 实际尺寸 | 人物一致性 | 观察 |
|---|---|---|---|---|
| grok-imagine-image-quality | 13.4s | 720×1280 ✅ | ★★★★★ | 脸、发型、红发带、剑、玉坠、衣纹几乎完全保留；动作稍显僵硬 |
| grok-imagine-image-2.0 | 27.3s | 720×1280 ✅ | ★★★★☆ | 保留得很好，动作更有张力，夜景更丰富 |
| gpt-image-2.5 | 72.3s | 941×1672（接近 9:16） | ★★★★☆ | 构图最有电影感、动态最强；脸型略有变化 |
| gpt-image-2.5-sunburst | 47.5s | 941×1672（接近 9:16） | ★★★★☆ | 和 2.5 类似，衣摆动态很好 |

四个模型都能比较好地保持人物一致，**能满足"同一个角色跨镜头、跨集复用"的需求**。

### 2.3 横图（16:9 场景）

| 模型 | 实际尺寸 | 结论 |
|---|---|---|
| grok-imagine-image-quality（`aspect_ratio=16:9`） | 1280×720 | ✅ 严格 16:9 |
| gpt-image-2.5（`size=1536x864`） | 1942×809（约 2.4:1） | ❌ 比例明显不对，要裁掉很多 |

### 2.4 接入时要注意的坑

1. **必须带 User-Agent 请求头**。Python 默认的请求头会被 Cloudflare 拦下，返回 `403 error code: 1010`；curl 不受影响。适配层要固定带上一个 User-Agent。
2. Grok 必须传 `aspect_ratio`，`size` 参数无效。
3. GPT 系列的 `size` 不可靠，实际尺寸要以解码后的结果为准，再按核验规则裁切。
4. Grok 的最大输出是 720×1280。用作 H3 视频的参考图够用，因为 H3 本来就会把短边超过 1080 的图缩小。但如果要印刷或做高清海报，就不够了。

## 3. 使用优先级（按用途）

| 用途 | 首选 | 第二 | 第三 | 理由 |
|---|---|---|---|---|
| **角色基础形态**（文字生图） | grok-imagine-image-quality | gpt-image-2.5-sunburst | grok-imagine-image-2.0 | 首选画幅准、快、最贴近 3D 国漫风；sunburst 细节最好，但需要从 2:3 裁成 9:16 |
| **衍生形态 / 跨镜头保持一致**（改图） | grok-imagine-image-quality | grok-imagine-image-2.0 | gpt-image-2.5 | 首选人物保留最好、尺寸准；要更强的动态和构图时用 GPT |
| **场景图（横图 16:9）** | grok-imagine-image-quality | grok-imagine-image-2.0 | — | GPT 横图比例不可控，暂不推荐 |
| **场景图（竖图 9:16）** | grok-imagine-image-quality | grok-imagine-image-2.0 | gpt-image-2.5-sunburst | 同上 |
| **角色设定图（多视图拼版）** | gpt-image-1.5 | gpt-image-2 | — | 会自动排出三视图和特写，适合做给人看的设定稿；但版式不可控，**不能用作视频参考图** |
| **写实风格作品** | grok-imagine-image-2.0 | gpt-image-2.5 | — | 2.0 的写实感最强 |
| **快速草稿** | grok-imagine-image | grok-imagine-image-quality | — | 最快，但容易出逻辑错误，只适合打草稿 |
| 暂不使用 | gpt-image-2.5-flare、gemini-3.1-flash-image | | | flare 和 2.5 几乎一样，没必要单独用；gemini 目前不可用 |

**默认组合**：`grok-imagine-image-quality` 负责几乎所有资产图，`grok-imagine-image-2.0` 作为第一备选，`gpt-image-2.5-sunburst` 用于需要更多细节或更强动态的情况。

**切换规则**：适配层不自动换模型。首选模型失败、或者效果不满意时，由美术 bot 提出、总导演同意（或者你同意）后再换，并记在任务记录里（沿用原文档的规则）。

## 4. 后续可以补充的测试

- 同一个模型多次生成的稳定性（同一提示词跑 3–5 次）
- 多张参考图同时输入（角色 + 场景 + 道具）时，各家对"图1/图2"的理解是否准确
- 透明背景素材（GPT 的 `background: transparent`）
- `grok-imagine-video-1.5` 作为视频备选的能力和价格
