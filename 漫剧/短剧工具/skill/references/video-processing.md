# 视频处理与成片

生成成片使用 scripts/assemble_clips.py，从项目 clips.json 读取明确的播放顺序，不按目录文件名猜顺序。

```bash
python3 <技能目录>/scripts/assemble_clips.py --project-root 项目目录 --manifest clips.json --output 成片.mp4 --width 1280 --height 720 --fps 30
```

脚本统一尺寸（保持比例补边）、像素格式、帧率、音频采样率和声道，重置时间戳；缺音轨补静音，保留有音频片段的声音。片段先独立标准化再拼接，避免一次打开过多输入；标准化文件只在临时目录保存。完整解码并核对源时长合计后才替换目标文件。
可用 --episode EP001 仅合成某集。默认不覆盖已有成片，确定重做后传 --overwrite。按项目比例调整尺寸，不强制所有视频变横屏。

此脚本生成直切版；需要淡入淡出、字幕、BGM或响度处理时，按用户要求增加后期并重审时长/音轨。不要自动把多镜头对白与音乐全部替换。
常用操作见 [后期命令](post-production.md)。
