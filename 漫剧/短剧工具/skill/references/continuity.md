# 尾帧与连续性

所有作品按 [状态读取与三项衔接核对](../../tools/SHOT_CONTEXT.md) 核对上下段。`continue_from` 表示需要真实尾帧的连续承接；无承接时不强行上传上段尾帧，仍核对相关站位、视线、道具和声音。

```bash
python3 <技能目录>/scripts/extract_ending_frame.py --project-root 项目目录 --manifest clips.json --clip-id EP001/Clip00002
```

脚本定位来源视频，将最后解码帧写到来源集目录，承接报告写到当前集目录。使用临时 PNG 并原子替换，失败不覆盖旧帧。报告记录来源完整 clip ID 和原视频 SHA256。
观察真实尾帧，检查身份、朝向、动作、光影与预期是否相符，再把图片作为下一次提交的参考图。不能仅生成文件就声称衔接自然。
兼容旧项目的文本大纲/分镜调用说明见 [旧项目模式](legacy-continuity.md)。旧模式依赖固定文本字段，新项目优先显式清单。
