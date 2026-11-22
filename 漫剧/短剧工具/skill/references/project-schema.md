# 项目数据规范（v1）

JSON 是新增项目的机器读取入口，TXT/Markdown 用于创作与人工审阅。每次修改保持二者一致；旧项目先导入并核对，不因缺 JSON 就抛弃已有资产重做。
脚本接受项目根目录与清单路径；清单内产物路径必须相对于项目根目录，不能逃逸项目目录。原著可从用户指定的外部路径读取。

## project.json

```json
{"schema_version":1,"title":"示例漫剧","style":"3D风格化漫剧","ratio":"16:9","character_sheet_ratio":"16:9","target_duration":30,"max_concurrency":1,"max_revisions":2,"image_model":"4.6","video_model":"seedance2.0fast","poll_interval":30,"poll_window":1200}
```

以上为可覆盖的制作偏好；模型、分辨率、时长等以安装的 CLI 帮助与账户实际能力为准。项目需另记录原文路径/哈希、选定范围、对白/旁白/BGM/字幕需求、风格版本；未请求的音频功能不自动增加制作范围。

## assets.json

顶层为数组，每项必含 `id,type,name,detail,prompt,image,status`；可加 `usage,source_asset_id,relation,chapter_ranges,visual_summary,source_evidence,design_additions,style_revision,prompt_sha256`。

```json
[{"id":"C00001-A","type":"character","name":"林舟-基础形态","detail":"角色/C00001-A-林舟.txt","prompt":"角色/C00001-A-林舟.prompt.txt","image":"角色/C00001-A-林舟.png","status":"planned","source_asset_id":null,"relation":"independent","chapter_ranges":[[1,3]]}]
```

角色 ID 为 C + 五位数字 + 大写形态后缀，首形态 A，超过 Z 使用 AA；道具 P00001，场景 S00001，新稳定形态分配新编号并记来源关系。关系值 independent/derived/new-form。临时表情、血迹和能量效果通常放镜头状态，不另建资产。
`status` 为 planned/generated/approved/failed。图片路径在 planned 时可以不存在；只有 approved 且文件可用才能用于提交。按镜头实际形态选图，不以“稀有形态优先级”覆盖剧情中的普通形态。
`资产编号表.txt` 可导出为：编号 | 名称 | 类型 | 视觉摘要 | 图片 | 适用章节 | 关系 | 来源编号。资产详情按 [novel-reader-story](novel-reading.md) 的模板填写。

## clips.json

顶层为按最终播放顺序排列的数组。必填 `id,episode,duration,assets,prompt,video,continue_from,status`。

```json
[{"id":"EP001/Clip00001","episode":"EP001","duration":8,"assets":["C00001-A"],"prompt":"单集制作/EP001/视频_Clip00001.prompt.txt","video":"单集制作/EP001/视频_Clip00001.mp4","continue_from":null,"status":"planned"}]
```

`continue_from` 是此列表中更早的完整 clip ID，null 表示重新起幅。还可记录 `continuity_reason,source_story,shots,task_dir,ending_frame`。
`shots` 可含 start/end/visual/dialogue/audio；时间从 0 连续覆盖 duration。承接来自另一集时仍显式写完整 ID。生成 prompt 中可以保留可读的 [Clip00001-尾帧] 标记，但实际提交统一映射为图1，任务清单记录完整来源路径。
`status` 同资产；approved 表示已经做过人工或 Agent 视觉/音频检查，不是文件存在的同义词。

## 目录

项目根包含 project.json、assets.json、clips.json、大纲.txt、读取进度.json、风格素材.md、角色/、道具/、场景/、单集制作/EP001/ 与 tasks/。
每个任务独占 tasks/<稳定任务名>/attempt-001/，保存 request.json、state.json、response.txt、query-response.txt，不能混用不同请求。任务目录包含原文提示词及输出响应，分享前检查是否包含不应公开的信息。
