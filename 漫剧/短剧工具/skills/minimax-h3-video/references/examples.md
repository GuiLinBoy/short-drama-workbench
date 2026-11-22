# 示例

## 当前中文示例：双人短对白（8秒，全参考）

本例仅说明结构，不绑定任何真实作品或素材，不构成生成任务。附件标签依次为`C00001-A`、`C00002-A`、`S00001`，分别提供人物身份/服装及室内空间；不限定参考图的站姿和构图。视觉主体编号与开口顺序分别管理：先说话的`<Subject 2>`是`(S1)`。

```text
subject_definitions:
<Subject 1> 是[C00001-A]中的虚构中年男子，短灰发、蓝灰开衫；只保留身份与服装。
<Subject 2> 是[C00002-A]中的虚构中年女子，短卷发、砖红外套；只保留身份与服装。
<Subject 3> 是[S00001]中的客厅，桌在左前、门在右后，左侧窗光；只保留空间与光线。

summary:
[reference generation] 一个固定双人中景，女子先问，男子再答；身份和空间来自参考，动作与构图按下文执行。

retention_analysis:
<Subject 1>: fully_preserved - 保留面孔、发型与服装，姿态和构图不照搬人物图。
<Subject 2>: fully_preserved - 保留面孔、发型与服装，姿态和构图不照搬人物图。
<Subject 3>: fully_preserved - 保留桌门布局及窗光，具体机位按镜头说明。

detailed_description:
生活写实真人短剧，自然肤质、普通衣料，柔和左侧窗光和暖中性色调。画面不出现字幕、标题或水印。
[Shot 1] 全段8秒，平视固定双人中景。<Subject 1>在画面左侧，<Subject 2>在右侧，相距一步半，二人的面孔和嘴可辨。0—1秒二人闭嘴对视，不说话。
1—3秒，<Subject 2> (S1) 用中年女性清晰中音询问：<d>[Chinese] 你来做什么？</d>
<Subject 1>闭嘴听；女子说完闭嘴。3—4秒无人说话，二人保持原站位。
4—6秒，<Subject 1> (S2) 用中年男性平稳低音回应：<d>[Chinese] 有事请直说。</d>
<Subject 2>闭嘴听；男子说完闭嘴。6—8秒无人说话，二人自然呼吸，视线保持，不追加动作或台词。

overall_soundscape:
轻微室内底噪、指定的两句对白和自然呼吸，无其他人声。

non_diegetic_music:
N/A
```

不为精简而删掉对白/闭嘴时段，也不为了长度目标重复服装与场景。768P是新请求未指定分辨率时的本地默认；实际准备命令及授权关口见 [接口与任务](api-and-tasks.md)。

## 历史英文示例（仅结构参考）

以下原有样例保留供理解技术结构，不要求将新稿翻成英文。旧命令仅作底层接口示意，不能直接作为当前Codex生产执行入口；真实制作通过受控入口。旧稿中的语气词、重抽、定格和镜头数量等建议不代表额外批准。

以下示例展示格式和写法，覆盖 3D 国漫（示例 1–3）、都市剧（示例 4）、写实真人剧（示例 5）。示例里的作品、角色、台词都是虚构的。方括号标签（`[C00001-A]` 等）由 `h3_task.py prepare` 按附件顺序换成 `<Picture N>`。

## 示例 1：动作片段（全参考六段式，承接上一段）

对应 [分镜](storyboard.md) §3 里的 Clip 3：8 秒，2 个镜头，类型为"动作"。

附件顺序：`Clip00002-尾帧`、`C00001-A`（林舟）、`C00003-A`（黑衣人）、`S00002`（竹林小径）。

```text
subject_definitions:
<Subject 1> is the young swordsman in [C00001-A], with a high black ponytail tied with a red ribbon, a white embroidered robe with a black sash, and a jade pendant at the waist.
<Subject 2> is the masked assassin in [C00003-A], in fitted black clothing with a black face cloth and a short curved blade.
<Subject 3> is the bamboo path in [S00002], with dense green bamboo on both sides, a toppled stone lantern on the left, and slanting late-afternoon light.
[Clip00002-尾帧] is the first frame of [Shot 1], showing {{承接定义：<Subject 1> standing on the right third of the frame, facing left, hand resting on his sheathed sword}}.

summary:
[keyframe completion + reference generation] Continuing from [Clip00002-尾帧], <Subject 2> ambushes <Subject 1> on <Subject 3>; <Subject 1> draws and blocks, repels the attacker, and coldly demands who sent him, across two shots.

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - face, red-ribbon ponytail, white embroidered robe, black sash, jade pendant, and sword are retained.
<Subject 2> (appears in [Shot 1], [Shot 2]): fully_preserved - black clothing, face cloth, and curved blade are retained.
<Subject 3> (appears in [Shot 1], [Shot 2]): fully_preserved - bamboo layout, toppled stone lantern on the left, and warm side light are retained.
[Clip00002-尾帧] ([Shot 1] first frame): fully_preserved - position, facing direction, framing, and lighting are retained at 0.00 seconds.

detailed_description:
The target video is in a 3D CG stylized Chinese xianxia animation style with soft volumetric light, fine cloth simulation, and a muted jade-green and ivory palette. No subtitles, captions, or on-screen text appear.
[Shot 1] The shot begins from [Clip00002-尾帧]: {{承接开场：a medium shot of <Subject 3>, warm low sunlight from the upper right rim-lighting the bamboo, with <Subject 1> on the right third of the frame facing left, his hand resting on the sheathed sword}} while a breeze lifts the hem of his robe. In the first second he tilts his head slightly, listening. <Subject 2> bursts in from the left edge of the frame and thrusts the curved blade toward his chest. <Subject 1> steps his right foot back half a pace, draws the sword in one motion, and blocks horizontally; the blades meet with a burst of sparks. He turns his wrist and drives forward, and <Subject 2> slides back two steps, boots scraping the stone path. The camera holds a static shot, then trucks right with small amplitude at slow speed as the attacker is pushed back.
[Shot 2] At 00:05.000, the camera cuts to a low-angle close-up of <Subject 1>, sword tip angled toward the ground, eyes fixed on the left. The camera pushes in with small amplitude at slow speed. <Subject 1> (S1), in a young man's low, steady voice with a slow, restrained pace, lets out a short, contemptuous huff and says coldly: <d>[Chinese] 哼，谁派你来的？</d> He closes his lips. At the left edge of the frame, <Subject 2> kneels on one knee, does not speak, and keeps his lips completely closed behind the face cloth. For the final second, both characters hold their poses and positions and all motion stops.

overall_soundscape:
Bamboo leaves rustle in a steady breeze. The sword leaves its sheath with a sharp metallic ring, the blades clash with a bright impact, cloth snaps as the robes swing, and boots scrape across stone, followed by the assassin's heavy breathing.

non_diegetic_music:
N/A
```

提交命令：

```bash
python3 $S prepare --prompt 单集制作/EP001/视频_Clip00003.prompt.txt --output tasks/EP001-Clip00003-draft/request.json \
  --ref "Clip00002-尾帧=单集制作/EP001/尾帧/Clip00002.png" --ref "C00001-A=角色/C00001-A-林舟.png" \
  --ref "C00003-A=角色/C00003-A-黑衣人.png" --ref "S00002=场景/S00002-竹林小径.png" \
  --duration 8 --ratio 9:16 --resolution 768P --skill-id official-action-choreography-camera-logic
```

要点：
- 尾帧放第一个，在 `subject_definitions` 里定义为 `[Shot 1]` 的首帧，任务类型写 `keyframe completion`。
- 依赖尾帧的两句包在 `{{承接定义：…}}`、`{{承接开场：…}}` 里，由摄影看过真实尾帧后用 `set-continuity` 修改；`prepare` 会去掉标记。
- 角色只写 3–4 个最稳定的特征，之后一律用 `<Subject N>` 称呼。
- 打斗写成"后撤半步 → 拔剑横挡 → 火星 → 翻腕发力 → 对手滑退两步"的因果链，没有血腥画面。
- 台词用"配音版"：「哼，」保证一定发出冷哼，逗号制造停顿，问号让语调上扬。连语气词共 6 个字，放在 3 秒的镜头里；说完后写闭嘴，不说话的人也写了闭嘴。
- 最后一秒定格，供下一段承接。

## 示例 2：对白与内心独白（全参考六段式，不承接）

附件：`C00002-A`（苏晚）、`S00004`（客栈二楼窗边）、`C00002-A音色参考`（音频）。

```text
subject_definitions:
<Subject 1> is the young woman in [C00002-A], with long black hair in a half-up bun, a pale blue cross-collar dress, and a silver hairpin.
<Subject 2> is the inn's second-floor window seat in [S00004], with a wooden lattice window, a low tea table, and warm lantern light against a rainy night outside.
[C00002-A音色参考] is the voice-timbre reference for <Subject 1> (S1).

summary:
[reference generation + audio reference] <Subject 1> sits alone by the window of <Subject 2> on a rainy night, rereads a letter, and her inner voice reveals her decision, in one shot.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - face, half-up bun, pale blue dress, and silver hairpin are retained.
<Subject 2> (appears in [Shot 1]): fully_preserved - lattice window, tea table, lantern light, and rain outside are retained.
[C00002-A音色参考]: reference - <Subject 1> follows its soft, clear timbre and measured delivery without copying the original signal.

detailed_description:
The target video is in a 3D CG stylized Chinese xianxia animation style with soft volumetric light, fine cloth simulation, and a muted jade-green and ivory palette. No subtitles, captions, or on-screen text appear.
[Shot 1] A medium close-up frames <Subject 1> seated at the low tea table on the left half of <Subject 2>, lantern light warming the right side of her face while cool blue rain light falls through the lattice. She unfolds a letter with both hands; a wooden plaque on the wall behind her reads "听雨". The camera pushes in with small amplitude at slow speed. Her eyes move along the lines, her fingers tighten on the paper, and her shoulders rise once with a slow breath. <Subject 1> (S1) says in an off-screen voiceover: <d>[Chinese] 这一次，我不会再等了。</d> while her lips remain completely closed. She folds the letter along its crease and sets it on the table, then turns her gaze to the rain-streaked window and holds still.

overall_soundscape:
Steady rain patters on the roof tiles and the window lattice, with a low wind outside. Paper rustles as the letter unfolds and folds, and a teacup clicks softly against the table.

non_diegetic_music:
N/A
```

要点：
- 内心独白（OS）用官方固定写法 `says in an off-screen voiceover`，并说明嘴唇闭合；这是苏晚自己的声音。如果这句是"旁白"，就不应该写进提示词，而是后期配音（见 [声音](audio.md)"台词的四种类型"）。
- 牌匾文字 "听雨" 是画面里要出现的文字，所以放在英文双引号里；台词不放进引号。
- 音色参考音频在 `subject_definitions` 里绑定到 `(S1)`，在 `retention_analysis` 里标 `reference`，并且不出现 `(Sx)`。

## 示例 3：空镜（I2VA，以场景图作为严格首帧）

用于不需要角色参考的空镜或物件特写，要求开头和场景图完全一致。附件：`S00005=场景/S00005-青云宗山门.png@first_frame`（已登记、已通过的场景图）。

```text
For the target video, at 0.00 seconds into the target video, [S00005] (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 3D CG stylized Chinese xianxia animation, an extreme wide shot of a mountain sect at dawn shown in [S00005], preserving the layered peaks, the white stone stairway in the center, and the golden roofs on the right. Mist drifts slowly from left to right through the valley as the first sunlight spreads across the roofs. The camera pedestals up with large amplitude at slow speed, revealing a giant stone gate at the top of the stairway carved with the characters "青云宗". A flock of white cranes crosses the frame from right to left. No subtitles, captions, or other on-screen text appear.

overall_soundscape: Wind moves through pine trees with distant crane calls, and a temple bell rings once, its echo fading across the valley.

non_diegetic_music: N/A
```

要点：
- 对齐指令必须是第一行，后面空一行。
- 用了首帧模式，就不能再上传任何参考图；需要人物一致时改用全参考模式。
- 这里用的是已登记的场景图 `[S00005]` 作为首帧。本项目**不做关键帧**，所以不要为了首帧专门生成、登记新的图片。

## 示例 4：都市剧对话（全参考六段式，过肩，题材 `urban`）

情境：深夜厨房，妻子（林晚）发现丈夫手机上的消息，质问丈夫（周屿）。这一段是正反打的第一个机位：从周屿的肩后拍林晚。下一段换到林晚肩后拍周屿的反应，拆成相邻的两个片段。7 秒，1 个镜头，类型"对白"。

附件顺序：`C00011-A`（林晚）、`C00012-A`（周屿）、`S00021`（公寓厨房，夜）、`P00031`（黑色手机）。

```text
subject_definitions:
<Subject 1> is the woman in [C00011-A], early thirties, shoulder-length black hair tied loosely, wearing a grey knit cardigan over a white T-shirt.
<Subject 2> is the man in [C00012-A], early thirties, short neat hair, wearing a navy shirt with rolled sleeves.
<Subject 3> is the apartment kitchen at night in [S00021], with a white marble counter, a window on the right, and a warm pendant lamp above the counter.
<Subject 4> is the black smartphone in [P00031].

summary:
[reference generation] In <Subject 3> late at night, <Subject 1> holds up <Subject 4> toward <Subject 2> and quietly confronts him, filmed over his shoulder in a single shot.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - face, loosely tied black hair, grey cardigan, and white T-shirt are retained.
<Subject 2> (appears in [Shot 1]): fully_preserved - short hair and navy shirt are retained; only his shoulder and the back of his head are visible.
<Subject 3> (appears in [Shot 1]): fully_preserved - marble counter, right-side window, and warm pendant lamp are retained.
<Subject 4> (appears in [Shot 1]): fully_preserved - black phone body and lit screen are retained.

detailed_description:
The target video is live-action and cinematic, set in a contemporary Chinese city, with natural skin texture, realistic fabric, soft window light mixed with warm practical lamps, and a clean neutral grade. No subtitles, captions, or on-screen text appear except the phone screen described below.
[Shot 1] In the foreground, <Subject 2>'s right shoulder and the back of his head fill the lower-right corner, softly out of focus. Across the marble counter, <Subject 1> stands on the left third of the frame in sharp focus, lit from above by the warm pendant lamp, with dark blue night outside the window behind her. Only these two people appear in the frame throughout. She silently raises <Subject 4> in her right hand and turns the screen toward him; the screen shows a message reading "明天老地方见". The camera holds a static shot with a shallow depth of field. Her jaw tightens, she draws one slow breath, and then <Subject 1> (S1), in a low, steady young woman's voice, says quietly: <d>[Chinese] 老地方，是哪儿？</d> She closes her lips and keeps her eyes on him, her hand with the phone still raised. <Subject 2> does not speak and keeps his lips completely closed. For the final second, both hold their positions and all motion stops.

overall_soundscape:
A refrigerator hums softly in the quiet kitchen, with faint traffic far below the window. Her cardigan sleeve rustles as she lifts the phone, followed by her slow breath.

non_diegetic_music:
N/A
```

要点：
- 过肩：**先写前景的肩膀和后脑**，再写对面清楚的人；写了"画面中只有这两个人"，防止复制出第三个人。
- 画面文字 "明天老地方见" 写在英文双引号里，并在字幕说明里明确把它排除在外；手机登记成道具 `P00031`，前后片段保持一致。
- 风格句用都市剧模板；没有台词的动作写了 `silently`；不说话的周屿写了闭嘴。
- 正反打的反打（周屿的反应）放在下一个片段，镜头换到林晚的肩后；承接两句由摄影按真实尾帧修改。

## 示例 5：写实真人剧情绪近景（全参考六段式，题材 `live-action`）

情境：医院走廊，女儿得知父亲病情，独自坐在长椅上强忍情绪。6 秒，1 个镜头，类型"叙事"，没有台词。

附件顺序：`C00021-A`（女儿）、`S00041`（医院走廊，夜）。

```text
subject_definitions:
<Subject 1> is the young woman in [C00021-A], mid-twenties, long straight black hair, wearing a beige trench coat over a light blue shirt.
<Subject 2> is the hospital corridor at night in [S00041], with pale green walls, a row of metal benches, and cool overhead fluorescent light.

summary:
[reference generation] <Subject 1> sits alone on a bench in <Subject 2> and silently holds back tears, in one slow push-in shot.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - face, long black hair, beige trench coat, and light blue shirt are retained.
<Subject 2> (appears in [Shot 1]): fully_preserved - pale green walls, metal benches, and cool fluorescent light are retained.

detailed_description:
The target video is live-action and cinematic, with natural skin texture, realistic fabric and materials, cool fluorescent light, and a desaturated blue-green grade. No subtitles, captions, or on-screen text appear.
[Shot 1] A medium close-up frames <Subject 1> seated on a metal bench in a quiet hospital corridor at night, on the right third of the frame, the corridor softly blurred behind her. Only <Subject 1> appears in the frame throughout. She silently holds a folded medical report in both hands on her lap. The camera pushes in with small amplitude at slow speed, the focal length locked at one setting throughout. First she presses her lips together and looks down at the report; then her eyes redden and her breath catches once; finally she lifts her gaze toward the far end of the corridor and blinks slowly, holding back tears. No one speaks during this shot; she keeps her lips closed.

overall_soundscape:
The low hum of fluorescent lights fills the corridor, with distant footsteps and a faint medical monitor beeping behind a closed door. Paper crinkles softly in her hands, and she draws one shaky breath.

non_diegetic_music:
N/A
```

要点：
- 没有台词的片段：写了 `silently` 和 `No one speaks`，环境声写具体，三重防止乱说话。
- 情绪用身体反应表达（抿嘴、眼眶发红、呼吸一顿、慢慢眨眼），并用 first / then / finally 保证先后次序，不依赖秒数。
- 推镜写了"焦距全程锁定"，说明是机位在动；只写一种运镜。
- 开场是中近景：全参考模式可能拍得偏宽，这类片段在试生成时多预留一次。
- 手部只是"拿着一份对折的报告"，没有复杂的手指动作。
