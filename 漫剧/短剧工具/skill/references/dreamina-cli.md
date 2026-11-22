# CLI 使用与适配

参数已在 2026-10-21 依据本机 dreamina 子命令 --help 核对。安装版本可变化，每次迁移环境都重新检查帮助；本包不包含 CLI 或账户凭证。

## 已核对的参数差异

| 模式 | 参考图片参数 | 备注 |
|---|---|---|
| text2image | 无 | --resolution_type、--ratio、--model_version |
| image2image | --images | 最多10张；不是视频用的 --image |
| multimodal2video | 重复 --image | 当前最多9张图；至少图或视频之一；本包准备脚本聚焦图片资产链路 |
| frames2video | --first / --last | 两张真实首尾图；不等于多参考图模式 |
| query_result | --submit_id / --download_dir | 查询、下载，不重复生成 |

已核对 Seedance 2.0 多参考视频支持 4–15 秒整数、720p；实际能力变化需修改准备脚本中的能力配置。图片模型与会员视频模型的可用性取决于账户，不保证所有账户可用。

## 安全提交示例

用实际安装目录替换 <技能目录>，文件名和参考标签按项目填写。命令中的占位符不是 shell 变量。

```bash
python3 <技能目录>/scripts/prepare_submission.py --mode text2image --prompt 角色/C00001-A-林舟.prompt.txt --output tasks/角色A/request.json --model 4.6 --ratio 16:9 --resolution 2k
python3 <技能目录>/scripts/submit_task.py --request tasks/角色A/request.json --task-dir tasks/角色A/attempt-001
```

上例第二条默认预览。已获授权实际生图时加 --execute。为衍生角色使用 --mode image2image，并添加 --reference '身份参考=角色/C00001-A-林舟.png'。

多参考视频示例：

```bash
python3 <技能目录>/scripts/prepare_submission.py --mode multimodal2video --prompt 单集制作/EP001/视频_Clip00002.prompt.txt --output tasks/clip2/request.json --reference '[Clip00001-尾帧]=单集制作/EP001/视频_Clip00001-尾帧.png' --reference '[C00001-A]=角色/C00001-A-林舟.png' --reference '[S00001]=场景/S00001-旧塔.png' --model seedance2.0fast --duration 8 --ratio 16:9 --resolution 720p
python3 <技能目录>/scripts/submit_task.py --request tasks/clip2/request.json --task-dir tasks/clip2/attempt-001 --execute
python3 <技能目录>/scripts/submit_task.py --task-dir tasks/clip2/attempt-001 --query
```

参考附件和 prompt 中的编号必须对应；额外风格图用 --reference '风格参考=风格图.png'，脚本自动给它分配真实图号。图片路径在准备时转成绝对路径，换电脑/搬项目后重新准备请求，不直接复用旧 argv。

生成完成后：

```bash
dreamina query_result --submit_id 实际任务ID --download_dir tasks/clip2/下载
python3 <技能目录>/scripts/submit_task.py --task-dir tasks/clip2/attempt-001 --mark-status succeeded --evidence 'query_result返回完成，媒体已下载并检查'
```

未知提交状态先 dreamina list_task，确认请求与任务相符后用 --recover-id 实际任务ID --evidence '匹配依据' 恢复；该参数不会重新提交。

默认每30秒查询一次，单次查询超时60秒，总等待20分钟。用 Agent 调度间隔，不执行永久 while true。任务状态字段以实际响应为准，query脚本不会自行猜测成功/失败枚举。

默认生成并发1。提高并发时 Agent 先汇总所有正在提交/已提交/未知状态的任务，未知状态占用额度；结合账户限额调度。单任务脚本不提供跨进程并发池，也不承诺服务端幂等。
