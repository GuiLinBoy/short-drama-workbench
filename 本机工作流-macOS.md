# 短剧制作工作台 · macOS 本机工作流

本指南针对当前 macOS 本机环境，精简了原 Windows 迁移包的历史冗余说明，保留核心防错机制与画布审核能力。

---

## 一、本机环境与一次性准备

本机已就绪：`Python 3.13`、`ffmpeg`、`ffprobe`、`node`、`npm`。

### 1. 日常一键入口（可在 Finder 直接双击或终端运行）
- **检查环境**：[检查环境.command](检查环境.command)（校验文件完整性、依赖、字体、ZeroTier 服务与 8778 端口）
- **启动画布**：[启动画布.command](启动画布.command)（固定监听 `http://127.0.0.1:8778/`，就绪后自动打开浏览器）
- **新建作品**：`./新建作品.sh <作品名> [题材] [画幅] [模式]`
- **查看画布审核待办与续跑提示**：`./当前待办.sh [作品名]`
- **自动生成 H3 6 项内部复核草稿骨架**：
  ```bash
  python3 -I -X utf8 漫剧/短剧工具/tools/h3_review_draft.py --project "<作品绝对路径>" --request "<请求JSON路径>"
  ```

### 2. 配置 API 密钥（二选一，推荐 macOS 钥匙串）
所有生成脚本（`h3_task.py`、`nexorai_image.py`、`narrate.py`、`authorized_cli.py`）均已适配 **环境变量 → macOS 钥匙串** 自动读取：

```bash
# 方式 A：存入 macOS 钥匙串（推荐，不落明文文件）
security add-generic-password -U -s "NEXORAI_API_KEY" -a "$USER" -w "你的NexorAI密钥"
security add-generic-password -U -s "MINIMAX_H3_API_KEY" -a "$USER" -w "你的sk-ml-优云智算密钥"

# 方式 B：或写入 ~/.zshrc 环境变量
export NEXORAI_API_KEY="你的NexorAI密钥"
export MINIMAX_H3_API_KEY="你的sk-ml-优云智算密钥"
```

### 3. 字幕与成片渲染（已对接 ZeroTier 远端渲染，无需安装 `ffmpeg-full`）
- 本机默认 `ffmpeg` 已完整覆盖裁剪、拼接、混音、`-14 LUFS` 响度标准化和元数据写入。
- 当成片需要烧录硬字幕或可见 AI 文字标识、且本机 `ffmpeg` 缺少 `subtitles`/`drawtext` 滤镜时，[assemble.py](漫剧/短剧工具/tools/audio/assemble.py) 会自动调用 ZeroTier `POST /v1/video/render` 完成渲染，再在本机无损回封装原混音频轨与 AI 元数据（复用同一 ZeroTier 地址与令牌）。
- 如需离线纯本机烧录，再另装 `brew install ffmpeg-full` 即可；本机系统内置中文字体（`Hiragino Sans GB.ttc` / `STHeiti Medium.ttc`）已自动兼容。

### 4. 语音识别（已对接 ZeroTier 远端服务，无需本机安装 FunASR / PyTorch）
[align.py](漫剧/短剧工具/tools/audio/align.py) 已内置 ZeroTier 远端视频分析服务（默认 `http://10.197.123.1:8000/v1/video/analyze`，自动绕过代理）：
- **令牌读取顺序**：`ASR_REMOTE_TOKEN` → `ASR_REMOTE_TOKEN_FILE`（默认 `~/Downloads/token.txt`）→ macOS 钥匙串 `ZEROTIER_VIDEO_API_TOKEN`。
- 也可通过 `ASR_REMOTE_URL` 覆盖服务地址；若本机未来安装了 `环境/asr/bin/python` 或显式设置 `ASR_PY`，会自动优先走本机 FunASR。
- 直接使用 `align.py align`、`align.py check`（乱说话检查）、`align.py subtitle`（剧本台词对齐 SRT）即可，无需任何本机模型下载。

---

## 二、推荐的精简本机制作流程（单主执行者）

默认由**一个主执行者贯穿五岗职责**，新稿正文一律使用**中文提示词**，视频默认原生 **768P**（不强制 480P 试片再重跑）。

为降低流程摩擦，日常单人/小团队创作建议聚焦 **3 个高价值人工把关节点**（其余机械步骤批量执行）：

### 阶段 1：立项与分镜（把关点 ①）
1. 创建作品目录并安装共享规范入口：
   ```bash
   ./新建作品.sh 我的第一部短剧 3d-guoman 9:16 short
   ```
   （题材可选：`3d-guoman` 3D 国漫 / `live-action` 写实真人 / `urban` 都市剧；模式：`short` 短片 / `series` 长篇）
2. 将原稿放入 `漫剧/作品/<作品名>/原文/`。
3. 执行者完成：大纲 + 角色/场景清单 + 分镜与视频提示词草稿 + 预算估算。
4. **人工确认**：在画布确认剧情节奏、风格规格与本批预算额度。

### 阶段 2：定妆图与首次音色（把关点 ②）
1. 如有旁白：先用 `narrate.py` 配旁白，拿到真实时长后回填调整片段时长。
2. 美术用 NexorAI 批量生成角色/场景视频参考图（三视图与独立道具图按需生成，不默认追加）。
3. **人工确认**：在画布批量看图通过 → 回填视频提示词引用。
4. **首次开口即音色收尾**：每个角色第一段有对白的视频通过后，同轮从该视频提取干净音色模板（约 4 秒）并登记到 `voices.json`，确认后后续同角色对白自动强制绑定，避免跨段变声。

### 阶段 3：逐段出片与后期成片（把关点 ③）
1. 摄影按依赖顺序用 MiniMax H3（原生 768P）批量生成：
   - 连续镜头先提取上段真实尾帧，再用 `set-continuity` 只改承接句；
   - 非连续镜头可按并发上限（`全局设置.json` 默认最多 3 个同时跑）并行提交。
2. 画布按批次验收视频片段（支持原位返工，保留历史版本不新增杂乱节点）。
3. 剪辑用 `tools/audio/assemble.py` 合成：统一分辨率/帧率 → 混入旁白与已授权配乐 → `-14 LUFS` 响度标准化 → 字幕与 AI 标识 → `delivery_checks.py` 检查。
4. **人工确认**：完整观看并试听成片，确认交付。

---

## 三、常用命令速查（macOS 终端）

在 `短剧制作工作台/漫剧/短剧工具/` 目录下运行（作品路径始终传绝对路径）：

```bash
# 1. 启动画布（也可直接双击根目录 启动画布.command）
./canvas/start.sh

# 2. 查看作品当前状态与音色收尾情况
python3 -I -X utf8 tools/codex_workflow.py --project "<作品绝对路径>" status
python3 -I -X utf8 tools/voice_lifecycle.py --project "<作品绝对路径>"

# 3. 校验项目结构（v1 + v2）
python3 -I -X utf8 tools/validate_project.py "<作品绝对路径>"

# 4. 读取单段上下文与上下段衔接
python3 -I -X utf8 tools/shot_context.py --project "<作品绝对路径>" --clip EP001/Clip00001

# 5. 运行离线回归测试（不联网、不花钱）
python3 -I -X utf8 tools/run_codex_checks.py
```

---

## 四、实用提效提示

- **减少重复点审核**：如某部作品希望全权交给执行者推进，可在作品内明确委托总导演代审（按 `tools/CANVAS_REVIEW.md` 留存委托授权记录，仍保留全部技术检查与预算硬拦截）。
- **只读查看画布**：运行 `./canvas/start.sh --read-only` 可锁定审核写入，仅作看板浏览。
- **规则权威来源**：完整底层规范仍以 [OVERRIDES.md](漫剧/短剧工具/OVERRIDES.md) 与 [CODEX_WORKFLOW.md](漫剧/短剧工具/tools/CODEX_WORKFLOW.md) 为准；旧文档中的 `D:\ai剧集制作` 和 `.cmd` 命令均对应本机当前目录与上述 macOS 命令。
