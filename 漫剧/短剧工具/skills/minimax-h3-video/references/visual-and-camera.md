# 画面与镜头

总原则：**写摄影机能拍到的东西，不写形容词。** 情绪形容词基本不起作用，带时间点的动作动词和首尾姿态才会被模型执行（scenario-labs 分镜 skill 的总结，和 H3 官方"每个细节都要能看见或听见"的要求一致）。

## 1. 风格句（全项目原样复用）

`detailed_description` 的第一句（三段式里是 `[Shot 1]` 开头）写整体风格。官方给出的风格词有 `Cinematic`、`live-action`、`2D-animated`、`3D CG`、`claymation`、`watercolor`、`vintage film`。

每部作品在 `风格素材.md` 或已确认方案中确定中文风格句，相同场景沿用同一份基准，减少不必要变化，但不声称原样复用就一定不漂。以下英文模板仅供理解旧结构，新稿按题材写中文（详见 `~/漫剧/短剧工具/genres/`）：

| 题材 | 风格句示例 |
|---|---|
| 3D 国漫（`3d-guoman`） | `The target video is in a 3D CG stylized Chinese xianxia animation style with soft volumetric light, fine cloth simulation, and a muted jade-green and ivory palette.` |
| 写实真人（`live-action`） | `The target video is live-action and cinematic, with natural skin texture, realistic fabric and materials, soft window light, and a warm neutral grade.` |
| 都市剧（`urban`） | `The target video is live-action and cinematic, set in a contemporary Chinese city, with natural skin texture, realistic fabric, soft window light mixed with warm practical lamps, and a clean neutral grade.` |
| 2D 动漫（备用，暂无题材配置） | `The target video is 2D-animated with clean line art, flat cel shading, and a <palette> palette.` |

参考图的风格要和风格句一致：3D 风格句不能配真人照片当参考图，写实风格句也不能配 3D 渲染图，否则 H3 会在两种风格之间摇摆。写实题材不要写"8K""超清""电影级"这类空话，官方要求避免抽象的风格词。

## 2. 主体与构图

- **已经绑定参考图的主体**：在主体定义中写2–4个辨识特征及素材职责；镜头中首次出现时写位置和当前动作，之后只写变化，不再复述完整外貌。身份参考不默认锁定站姿、背景或景别；文字和图冲突时先解决冲突，不靠堆叠形容词。
- **位置要明确**：写清左/右/前景/背景，以及相互距离，例如 `<Subject 1> stands on the left third of the frame, three paces from <Subject 2>`。双人戏要守住轴线：A 在左、B 在右，没有换位动作就不要颠倒。
- **一个镜头里最多 1–2 个主要人物做动作。** 群像场景让其他人保持静止，或者虚化成背景。
- **防止"双胞胎"**：不要写"X 与 Y 同框"这种把两个主体并列的句子，要分别写各自的位置和动作。两个角色外观相近时，在定义里点出区别特征（发色、服装颜色）。

## 3. 动作

- **写成因果链**：起势 → 接触 → 反馈 → 结果，并写明谁朝哪个方向动。
  - 弱：`he attacks the enemy`
  - 强：`<Subject 1> steps forward with his right foot, drives the sword horizontally from left to right, the blade strikes the opponent's guard with a burst of sparks, and the opponent slides back two steps`
- **写成节拍或计数**：`takes four steps to the window, pauses, and pulls the curtain in the final second`（Sora 2 指南的写法）。
- **每个镜头最多两个动作意图。** 不要在一个镜头里写"伸手进怀 → 摸索 → 掏出 → 展开"这样的多步过程，要拆到多个镜头或多段视频里。
- **情绪写成身体反应**：不写 `she is sad`，写 `her shoulders tremble slightly and her eyes redden as she lowers her gaze`。
- **动态元素**：静态镜头也要有微动，例如 `a light breeze lifts loose strands of hair`、`mist drifts slowly across the courtyard`、`dust motes float in the light shaft`。这样画面才不会像幻灯片。

### 日常动作与表情（所有题材，写实真人和都市剧的重点）

- **手部动作简单明确**：拿起、放下、握住、推开，写清是哪只手、拿的是什么。避免复杂的手指动作（打字、系鞋带、数钱），写实画面里手指最容易崩。
- **眼神写方向**：`looks down at the phone`、`meets his eyes`、`glances toward the door`。对视要写清两人分别朝向哪边，符合轴线。
- **表演克制**：情绪靠呼吸、吞咽、嘴角、眉头、手部的小动作表达；写实题材动作幅度比动画小。
- **道具交接写成步骤**：`she slides the folder across the table; he stops it with his left hand and opens it`，并且和道具资产的参考图保持一致。
- **走位写起点和终点**：`walks from the doorway to the window on the right, three steps, then stops`。

### 打斗、技能（与动作 Skill 配合；主要用于 3D 国漫和动作戏）

摘自 novel-to-manju 的打斗规则，以及官方"动作编排与运镜" Skill：
- 写清谁进攻、谁反应、动势往哪个方向走、镜头在哪。左右方向和视线方向全程一致。
- **物理反馈优先于光效**：写成"腿砸下 → 对手膝弯 → 地面炸裂 → 冲击波"，不要写成"腿发光 → 爆炸 → 人飞了"。
- 武器全程一致：持剑就一直是剑，脱手要有回收的交代。
- 伤势逐级递进，不能"满血复活"。
- 技能释放拆成阶段，不要瞬间闪现：预兆 → 能量汇聚 → 成形 → 激活 → 爆发。大招 1.5–2 秒。
- 节奏是"定位 → 冲击 → 恢复"交替。混乱的场面要保留一个稳定的锚点（轴线、地标或主角的移动方向）。

## 4. 光线与色彩

写光源、方向、质感，再加 3–5 个颜色锚点（Sora 2 指南建议点名三到五种颜色，用来稳定不同镜头之间的色调）：

```text
warm low sunlight from the right rim-lights his hair, soft fill from the misty sky; palette of jade green, ivory, pale gold, and charcoal
```

同一个场景的不同片段，光线描述原样复用，除非剧情要求时间或天气发生变化。

## 5. 运镜（官方词表）

完整写法是**运动类型 + 幅度 + 速度**。中等幅度、正常速度时可以省略后两项。要写成句子里的自然动作，不要把标签堆在句尾。

| 类型 | 英文写法 | 含义 |
|---|---|---|
| 变焦 | `zooms in` / `zooms out` | 机身不动，焦距变化 |
| 推拉 | `pushes in` / `pulls out` | 机身前进或后退 |
| 摇 | `pans left` / `pans right` | 机身不动，水平转向 |
| 横移 | `trucks left` / `trucks right` | 机身水平平移 |
| 俯仰 | `tilts up` / `tilts down` | 机身不动，上下转向 |
| 升降 | `pedestals up` / `pedestals down` | 整机上升或下降 |
| 环绕 | `arc shot` | 绕主体弧线移动 |
| 跟拍 | `tracking shot` | 跟随运动主体 |
| 固定 | `static shot` | 机位和镜头都不动 |
| 晃动 | `shakes slightly` / `shakes strongly` | 轻微或强烈抖动 |
| 主观 | `POV` | 主体的视角 |
| 横滚 | `rolls clockwise` / `rolls counterclockwise` | 绕镜头轴旋转 |
| 幅度 | `with small amplitude` / `with large amplitude` | 构图变化的大小 |
| 速度 | `at slow speed` / `at fast speed` | 变化的快慢 |

```text
The camera pushes in with small amplitude at slow speed toward the folded letter in her hands.
The camera pans right with large amplitude at fast speed, revealing the open doorway.
The camera holds a static shot as the runner exits the frame.
```

- **一个镜头只用一种运镜。** 运镜动词互相矛盾（又推又拉）会让画面乱掉。
- 每个运镜只服务一个目的：揭示威胁、跟随发力、吸收冲击，或者把主体困在画框里（官方动作 Skill）。
- 景别：`extreme close-up`、`close-up`、`medium close-up`、`medium shot`、`medium-wide shot`、`wide shot`、`extreme wide shot`。角度：`eye-level`、`low-angle`、`high-angle`、`overhead`、`canted`。竖屏多用近景和特写，少用远景（远景里的人脸太小，容易崩）。

### 5.1 镜头术语对照（中文 → H3 写法）

镜头的选择见共用层的 [镜头语言手册](../../../镜头语言手册.md)；这里只写**在 H3 上怎么写**。可信度：【官方】官方词表或示例；【实测】社区在本地开源版上实测可用；【部分】只做成一部分；【未测】；【失败】。社区实测不一定等同于我们用的接口，统一验证时会复测关键镜头。

| 镜头 | H3 写法（要拆成画面加运动，只写名字不起作用） | 可信度 |
|---|---|---|
| 过肩 | `In the foreground, <Subject 1>'s left shoulder and the back of his head fill the lower-left corner, softly out of focus; across from him, <Subject 2> faces the camera in sharp focus.`（**先写前景肩膀**） | 【实测】 |
| 过肩推近 | 过肩写法 + `The camera pushes in with small amplitude at slow speed, the focal length locked at one setting throughout.` | 【部分】只能推到中近景 |
| 过腰 | `In the foreground, <Subject 1>'s right hand rests on the sword hilt at his hip, out of focus; beyond it, <Subject 2> ...` | 【未测】 |
| 双人镜头 | `<Subject 1> stands on the left third and <Subject 2> on the right third, about one and a half steps apart, <Subject 2> slightly closer to the camera.`（**必须写距离和前后**） | 【实测】 |
| 七分身 | `a medium-wide shot framing <Subject 1> from mid-thigh up`（不要写 `cowboy shot`） | 只写名字【失败】 |
| 荷兰角 | `a static Dutch angle tilted to the right`（静态倾斜，不要写成运动） | 【实测】 |
| 焦点转移 | `At first <Subject 1> in the foreground is sharp while <Subject 2> behind is blurred; as <Subject 1> turns toward them, the focus shifts to <Subject 2> and <Subject 1> softens.` | 【实测】 |
| 甩镜 | `The camera stays at one fixed spot and whips its direction to the right with strong horizontal motion blur, settling sharply on <Subject 2>'s face.` | 【实测】 |
| 希区柯克变焦 | `The camera pushes in while zooming out, keeping <Subject 1> the same size and position as the background stretches away.` | 【实测】 |
| 摇臂升起 | `The camera pedestals up with large amplitude at slow speed, the lens kept level, revealing ...` | 【实测】 |
| 半圈环绕 | `The camera arcs about 180 degrees around <Subject 1> at slow speed, keeping her centered.` | 【实测】；整圈【部分】 |
| 焦段、相机型号 | `35mm`、`85mm lens` | 基本不起作用，不要依赖 |
| 子弹时间 | — | 【失败】，不要用 |

### 5.2 写法纪律（社区实测总结，用我们自己的话整理）

1. **写成正面的描述**：`the camera stays at one fixed spot throughout` 比 `the camera does not move` 好。单独成句的否定句，在社区实测里会把运镜压掉。替换式的否定（"不再……，改为……"）可以用。
2. **画面里先出现的写在前面**：过肩、遮挡、揭示、甩镜都先写最先入画的东西。
3. **每种运镜只写它自己的变量**：摇镜不写焦距；横移写"镜头朝向全程锁定"，不写"始终对准主体"（会变成环绕）；升降写"镜头保持水平"（否则会变成升加俯仰）。
4. **环境用一句话描述**：`she stands in a quiet hospital corridor at night`，不要写"走廊里必须看到长椅、指示牌、窗户"——列清单会把画面推宽。
5. **防止复制出第二个人**：单人镜头写 `Only <Subject 1> appears in the frame throughout.`；竖屏下做横向运镜（横移、摇）时尤其要写。
6. **时间点会漂移约 ±1 秒**：关键动作用 `first ... then ...` 的次序来保证。
7. **开场景别偏宽**：全参考模式下，想要开场就是近景、特写，不一定拍得出来（社区引用的官方矩阵 0/51）。近景片段多预留一次试生成；统一验证后再决定要不要启用可选首帧。
8. **场景参考图会削弱运镜**：需要大幅运镜的片段，考虑不传场景图，或者把运镜写得更强。
9. **效果不好先诊断实际请求与媒体**：区分引用/构图/声音冲突、动作过密和随机失败；先改有证据的问题。换随机种子或新 attempt 是可能的返工选择，只能用于已批准的具体范围，不能作为默认第一步。

## 6. 文字、字幕与负面约束

- **画面里需要出现的文字**（牌匾、书信、招牌）放在英文双引号里，保留原文：`a wooden plaque reading "青云"`。
- **不想出现字幕时**：台词只放在 `<d>` 里，不要在别处把台词写进引号；风格句之后加一句 `No subtitles, captions, or on-screen text appear.`。这一做法来自其他模型的经验（Veo、Seedance 都把字幕列为常见问题），在 H3 上的效果需要实测。字幕后期统一添加。
- **负面约束只用于不相关的小瑕疵**（字幕、水印、多余的手指）。和画面主体有关的要求，一律改成正面描述：要写 `an empty mountain path with no other travelers`，不要写 `no people`。

## 7. 参考图使用

- 全参考模式下图片最多 9 张，但**不是越多越好**。社区经验是一段视频用 4–5 张最稳（Seedance 官方指南也建议参考素材 4–5 个即可）：上一段尾帧 + 出场角色（只用当前形态那一张）+ 场景，必要时加 1 个关键道具。
- 角色参考图**只用 `assets.json` 里的 `image`**，也就是单人、项目画幅、背景干净的视频参考图。`sheet` 字段里的三视图设定稿会让模型混淆，**不要上传**。
- 衍生形态（换装、长大）只上传当前镜头对应的那一个形态，不要把新旧形态一起上传。
