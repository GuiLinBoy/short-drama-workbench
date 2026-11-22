# 第 7 组：两作品隔离离线演练

本工具用**真实 CLI**驱动本地模拟服务，不运行五个真实 bot，不访问 H3 / IndexTTS / NexorAI。审批、作品清单及复盘均标记 `SIMULATED`，不能用作真实消费授权。

在工具根运行：

```powershell
python -I tools/dryrun/run_dryrun.py
```

默认同时做独立的真实本机 ASR 检查，不消耗平台额度。需要现成 FunASR venv、Paraformer / FSMN 模型和 `环境/测试音频/主会话-台词.wav`；缺失就报 `BLOCKED`，失败报 `FAIL` 并非零退出，不安装、不下载、不换桩。该音频是已有 Windows SAPI 中文测试音频，不是 H3 实际生成结果。

重复集成测试可跳过模型：

```powershell
python -I tools/run_offline_checks.py tools/dryrun/test_dryrun.py
```

该测试调用 `--skip-local-asr`；报告如实标记 `NOT_RUN`，`offline_acceptance_passed=false`。不能把这一结果当成真实识别验证。

## 隔离与证据

- 每次新建工具根 `.offline-checks/dryrun-日期时间-随机ID/`，里面包含完整的模拟漫剧根、两个作品、设置、账本、经验及假音色目录。已有目录直接拒绝；不提供清空选项。`--output-root` 仅允许在 `.offline-checks` 内指定全新目录。
- 两作品刻意共用片段 ID、旁白 ID 和批次名：A 为 9:16、3×8 秒；B 为 16:9、2×4 秒。模拟清单由夹具建立，不声称实现了生产级作品登记和三作品调度。
- Python 子进程只继承白名单基础环境和显式假凭据，端点绑定 `127.0.0.1` 随机端口。递归 Python socket/DNS 审计钩子拒绝外网及 HTTP 重定向到外网；这不是操作系统网络沙箱。原生 FFmpeg 只处理本地路径和 lavfi，不给它真实凭据。
- 图像是棋盘格，视频是带 `SIMULATED` 和任务号的测试图，声音是正弦波。所谓 480P/768P 是请求字段契约；模拟媒体几何尺寸不能证明真实平台分辨率语义或质量。最终合成器真实输出 720×1280 / 1280×720、12 fps。
- 主模拟链的 ASR 明确使用 `ASR_STUB` 时间戳。独立真实 ASR 闸门不设置 `ASR_FAKE_RESULT`，按绝对目录加载已有模型，分别执行真实 `align`、`subtitle`、`check`；识别文字必须与预期归一化完全相同，并检查字数、时间戳和 VAD 范围。
- 每次保存 `report.json`、`steps.json`、每步完整 stdout/stderr、mock 请求事件、预算/账本/状态、两部成片/SRT/验证报告/抽帧、复盘、独立 ASR 结果。任何未预期退出码都会终止并保留失败现场。
- 对交接清单中 31 个真实数据文件做前后 SHA256 核对，并比较真实数据目录文件清单；工具目录和独立 `canvas/` 不参与数据扫描。模拟经验写入不提交 Git。

## 真实 CLI 演练链

`pipeline_state` 建资产/分镜 → 图像/三视图/场景 → 模拟审批 → 旁白 prepare / submit / query → 试生成 → 尾帧/承接 → 音色截取并登记 → 定稿 → STUB 对齐/剧本字幕 → 混旁白/循环 BGM/压低音乐/响度/AI 标识 → validate / 完整解码 → 模拟经验事务。

跨作品用例包括图片全局竞争、H3 与排队旁白双向占用共享名额、外作品预算拒绝、查询 500 不退款、成功结算后下载失败重试、重复 query 不重复结算。预算 `used`、账本净额、任务结算与模拟服务实际用量逐作品逐批一致，账本事件 ID 不重复。重复渲染会拒绝且三个已有输出指纹不变。写入新尾帧触发原片段审批失效后，必须再走模拟总导演复审；不绕过状态保护。

首次截音色可用 `align.py cut-voice --project <作品> --character C00001 --source <来源> --start 0.4 --end 3.8 --out 音色/C00001.wav`，先截取后由摄影通过 `pipeline_state add-voice` 登记。三参数须齐全；不修改清单，不覆盖输出，拒绝越界路径及超出来源时长。旧的按 `voices.json` 截取方式保留。尾帧和承接报告也拒绝覆盖。

## 验收范围

`mock_chain_passed=true` 只证明模拟服务下的 CLI 衔接；`offline_acceptance_passed=true` 还要求真实本机 ASR 闸门通过及真实数据未变。两者都不代表生产可用，报告始终保留 `production_ready=false` / `real_bots_tested=false`。

抽帧只能检查选定时点的字幕/标识；声音是测试音，不能证明中文口型、音色一致性、旁白自然度、表演、叙事或真实模型质量。真实 bot 路由/看图、实际服务契约、三作品运行时调度和第 8 组统一验证仍需后续验收；任何平台额度请求继续单独报批。
