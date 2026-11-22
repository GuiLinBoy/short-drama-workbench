# H3 提示词结构（官方格式）

依据 MiniMax 官方发布在 Hugging Face 的两份写作指南（2026-10-31 读取）：

- 基础版（T2VA / I2VA / FL2VA / L2VA）：<https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md>
- 全参考版（Ref）：<https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md>

本技能据此组织正式提示词。直接遵循结构便于核对，但不保证 Context-IR 不改写、不保证人物/台词一定保真；开关效果仍以实际媒体为证。模式选择、素材职责与渠道参数分层见 [模式与准备核对](mode-and-preflight.md)。

## 0. 三条总规则

1. **正文按用户最新要求用中文写。** 本项目2026-11-08起采用中文提示词直接提交，不自动翻回英文；技术字段/标签保留，台词仍放`<d>…</d>`。本页旧英文样例保留为结构参考，以OVERRIDES §0为准。
2. **每个细节都要能看见或听见。** 不写剧情梗概，也不写"这一段表现了他的孤独"这类解释。
3. **素材标签由脚本编号。** 正文里写 `[C00001-A]`、`[S00001]`、`[Clip00001-尾帧]`，`h3_task.py prepare` 会按附件顺序换成 `<Picture N>`。不要手写 `<Picture N>`、`图1` 或 `@图片1`。`<Subject N>` 和 `(S1)` 这类不是图片编号，由写提示词的人直接写。

## 1. 先选模式

| 本次素材 | H3 模式 | 结构 |
|---|---|---|
| 没有图（极少用） | T2VA 文生视频 | 三段式 |
| 只给 1 张首帧（`@first_frame`），要求开头严格等于这张图 | I2VA | 对齐指令 + 三段式 |
| 首帧 + 尾帧 | FL2VA | 对齐指令 + 三段式，建议只用一个镜头 |
| 只给尾帧（要求最后落在某个画面上） | L2VA | 对齐指令 + 三段式 |
| **角色/场景/道具参考图（可以再加上一段的尾帧、音色参考）** | **全参考 Ref** | **六段式（漫剧的默认模式）** |

当前适配器的首尾帧槽不能和参考素材槽混用，见 [接口与任务](api-and-tasks.md)。有角色身份参考时通常用全参考六段式；需要严格首帧时用 I2VA，并重新核对可用素材。全参考里声明尾帧锚点不等于严格锁定首帧，不能以此保证景别或构图。

**近景/特写分流策略（防全参考开场偏宽）**：
- 无对白情绪特写、钩子开场或精确构图镜头：优先让美术用 NexorAI（图1人物+图2场景）生成精准首帧图，直接走 **I2VA（严格首帧模式）**，一次锁定构图与人脸，减少抽卡；
- 有对白且需绑音色模板的近景镜头：仍走全参考六段式，可将关系构图图作为 `<Picture 1>` 锚定初始构图。

## 2. 三段式（T2VA / I2VA / FL2VA / L2VA）

```text
[对齐指令，仅 I2VA/FL2VA/L2VA；必须是第一行，后面空一行]

integrated_multimodal_description: [Shot 1] <风格>, <初始构图>... [Shot 2] At 00:03.500, the camera cuts to ...

overall_soundscape: <1–4 句环境声、动作声、非语言人声>

non_diegetic_music: <1–3 句配乐，或 N/A>
```

对齐指令要一字不改（`S.SS` 是总时长，保留两位小数）：

| 模式 | 第一行 |
|---|---|
| I2VA | `For the target video, at 0.00 seconds into the target video, [首帧标签] (from [Shot 1]) is fully referenced.` |
| FL2VA | `How the reference pictures align with the target video — [首帧标签] (from [Shot 1]) aligns with the 0.00-second mark of the target video; [尾帧标签] (from [Shot N]) aligns with the S.SS-second mark of the target video.` |
| L2VA | `How the reference pictures align with the target video — [尾帧标签] (from [Shot N]) aligns with the S.SS-second mark of the target video.` |

（官方原文写的是 `<Picture 1>`。这里写成我们的标签，由脚本替换成 `<Picture N>`。）

各模式正文的写作顺序：
- **I2VA**：首帧锚定（写清图里的风格、主体、构图，并说明保留外观、服装、位置）→ 动作开始 → 连续发展 → 结果或反应。
- **FL2VA**：首帧状态 → 可观察的中间变化 → 差异逐步缩小 → 落到尾帧状态。不要把两张图各描述一遍，要写**连接两张图的运动路径**。
- **L2VA**：合理的前置状态 → 明确的动作和过渡路径 → 最后一个镜头逐步收拢 → 落到尾帧。

## 3. 全参考六段式（漫剧默认）

```text
subject_definitions:
<Subject 1> 是[C00001-A]中的<角色简述>，保留<2–4个辨识特征>；此图只提供身份和服装，不限定姿态与构图。
<Subject 2> 是[S00001]中的<场景>，保留<空间与光线锚点>；具体机位由镜头说明决定。
[Clip00001-尾帧] 提供[Shot 1]开场状态参考，实际可见<站位、朝向、姿态>。
[C00001-A音色参考] 仅提供<Subject 1>的声线参考；本段该角色按首次开口顺序编号(S1)。

summary:
[keyframe completion + reference generation] <用一句中文说明：谁在什么地方做什么，几个镜头，参考素材各起什么作用>。

retention_analysis:
<Subject 1>（出现在[Shot 1]、[Shot 2]）：fully_preserved - <保留身份与服装，姿态/构图由镜头决定>。
<Subject 2>（出现在[Shot 1]、[Shot 2]）：fully_preserved - <保留布局与光线，机位由镜头决定>。
[Clip00001-尾帧]（[Shot 1]开场参考）：fully_preserved - <需承接的实际站位、朝向、光线>。
[C00001-A音色参考]: reference - <Subject 1>参考其音色与表达，不复制原录音内容。

detailed_description:
<项目固定的中文风格与光线基准>。
[Shot 1] 开场参考[Clip00001-尾帧]。<本镜构图、主体位置、动作、运镜、同步声音、台词>。
[Shot 2] At 00:04.000, the camera cuts to <用中文写清的新构图与变化>。

overall_soundscape:
<用中文写清环境声、动作声；不重复对白>

non_diegetic_music:
N/A
```

### 各段要点

**`subject_definitions`**
- 每个需要单独跟踪的对象写一行。
- 角色、场景、道具都定义成 `<Subject N>`，并在定义里注明来自哪张图。
- 只有图片本身作为某个镜头的首帧、关键帧、尾帧或构图锚点时，才单独为它写一行（比如上一段的尾帧）。
- 同一个 Subject 可以来自多张图，例如"外貌来自角色图、服装来自衍生形态图"，要写明每张图提供什么。

**`summary`**
- 用一句中文概括，前面加方括号写任务类型，多个类型用 ` + ` 连接：
  - `keyframe completion`：图片作为具体的某一帧（例如尾帧作为本段第一帧）；
  - `reference generation`：图片只提供角色、场景、风格方面的指导；
  - `audio reference`：只参考音色，不复制原声。
- 这一段不能出现新的标签。

**`retention_analysis`**
- 每个标签一行，可用的标记：
  - `fully_preserved`：完全保留；
  - `partially_preserved`：部分保留；
  - `attribute_transfer`：把特征转移到另一个对象上；
  - `weak_reference`：只保留风格或氛围。
- 音频用 `fully_copy`、`partially_copy`、`reference` 或 `weak_reference`。
- 这一段里不写 `(Sx)`。
- 无需改身份/场景时用 `fully_preserved` 并写清保留范围，例如身份/服装或布局/光线；不顺带锁定定妆图的姿态、背景或机位。该标记是意图，不是人物不漂移的保证。确需改变属性时按所需标记和已批范围说明，不能机械“一律保留”。

**`detailed_description`**
- 正文主体，按播放顺序逐个镜头写，写得越具体越好，正文全用中文（技术标记原样保留）。
- 第一句（在 `[Shot 1]` 之前）是风格句，**全项目原样复用**。
- **单镜分秒节拍锚点**：单镜时长超过 4 秒时，在镜头内用 `[0s-2s]`、`[2s-5s]` 明确动作起手、台词开口和闭口收势，显著降低抢话与动作未做完风险。
- 以完整、无冲突、可执行为准，不设350–500个英文单词的最低长度，也不照搬其他平台的200 tokens建议。优先删除重复外貌/形容词，保留已批台词、时间线、说话人和承接；当前适配器映射后最多7000字符。
- 某个 Subject 第一次清楚出现时，写出它的参考特征、在画面里的位置和当前动作；之后只用标签称呼，不再重复定义。
- 说话时写 `<Subject 1> (S1) <中文声线与表演>：<d>[Chinese] 台词。</d>`。说话人按本段实际开口顺序编号，不说话的视觉主体不占说话编号。

**`overall_soundscape` / `non_diegetic_music`**：见 [声音](audio.md)。

## 4. 镜头与切镜（各模式通用）

- `[Shot 1]` 不写时间。后面的镜头写 `[Shot N] At MM:SS.mmm, the camera cuts to ...`，切点时间必须严格递增，并且在总时长之内。
- 切镜用这几种说法之一：`the camera cuts to` / `the shot cuts to` / `the shot transitions to` / `the shot changes to` / `the shot switches to`。叠化、淡入淡出、划像只在明确要求时才用。
- **切镜必须带来新信息**：新的主体、空间、状态、视角或时间。如果只是景别或角度的小变化，改用运镜。
- 运镜写法和完整词表见 [画面与镜头](visual-and-camera.md)。
- 画面里可见的文字（牌匾、招牌、信纸）写在英文双引号里，保留原文，例如 `a wooden plaque reading "青云"`。
