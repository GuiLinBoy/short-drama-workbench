# 旧项目兼容

```bash
python3 <技能目录>/scripts/extract_ending_frame.py --project-root 项目目录 --episode-dir 单集制作/EP002
python3 <技能目录>/scripts/extract_ending_frame.py --project-root 项目目录 --episode-dir 单集制作/EP001 --clip 2
```

跨集模式读取大纲.txt，标题为“第1个故事 名称”（或旧式“第1章 名称”），字段为“本故事开头画面是否延续上一个故事：是/否”“延续画面简述：…”。此模式要求故事顺序与 EP 序号一一对应。
集内模式读取该集文字分镜.txt，标题“## Clip 2”，字段“是否延续上一个clip画面：是/否”“内容概述：…”。否时不要求前一视频存在。
新项目使用 manifest 模式，避免靠自然语言标题解析依赖关系。旧模式参数中的相对产物路径禁止 ..；项目根可为绝对路径。
