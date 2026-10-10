# Bili2Note

> B 站视频 → AI 结构化讲义。一行命令把任意 B 站视频变成教科书级 Markdown 笔记。

输入一个 BV 号，Bili2Note 自动抓取字幕（无字幕则提取音频做 ASR 转写），可选提取画面板书/PPT，再调用大模型把口语化、碎片化的视频内容提纯为结构化讲义——包含内容概要、知识大纲、详细讲义、时间戳锚点、口误修正标注、术语表与易错点。还能挂载本地 RAG 知识库，用权威教材校对讲师口误。

## Quick Start

最简路径：装成 Agent Skill，装完即用——在 Trae / Claude Code 等支持 Agent Skills 规范的工具里，对 Agent 说"把 BVxxxx 做成讲义"即可触发。

```powershell
# 1) 全局安装命令行 + Python Tool
uv tool install . --force

# 2) 注册 Skill 到通用技能目录（Trae / Claude Code / Codex 都认这里）
mkdir "$env:USERPROFILE\.agents\skills\bili2note" -Force
copy skill\SKILL.md "$env:USERPROFILE\.agents\skills\bili2note\SKILL.md"

# 3) 首次配置（只需一次）：填 LLM Key 和 B 站 Cookie
bili2note config llm.provider deepseek
bili2note config llm.api_key sk-xxx
bili2note config bili.cookie "SESSDATA=xxx"

# 4) 生成讲义
bili2note note BV1xxxx
```

或者直接跑项目自带脚本一次搞定 1+2：

```powershell
Set-ExecutionPolicy -Scope Process Bypass   # 临时放行 PowerShell 脚本
.\install-skill.ps1
```

> **关于两个技能目录**：`~/.agents/skills/` 是跨工具通用标准（Agent Skills 规范），`~/.trae/skills/` 是 Trae 专属。SKILL.md 格式完全一致，按你用的工具选其一即可，`install-skill.ps1` 会同时写入两处做双保险。

## 功能特性

- **B 站字幕抓取**：支持 BV 号 / 视频链接（含 `?p=N` 分P），自动处理 Wbi 签名与 buvid 风控指纹，中文人工 CC 优先、其次 AI 字幕
- **无字幕视频 ASR 转写**：自动提取最低码率 Dash 音频流（不下载画面，省 90% 流量），内置静态 ffmpeg 免安装，按 10 分钟分片规避云端 25MB 限制；云端 API 与本地 faster-whisper 双模
- **视频画面信息提取**：提取关键帧 → VLM（云端视觉模型）或 PaddleOCR（本地）识别板书/PPT/代码，按时间戳对齐注入讲义
- **长视频分块生成**：转写文本超长时自动按时间轴分块（带重叠滑动窗口防语义截断），逐块生成草稿后合并成稿
- **LLM 讲义提纯**：兼容任意 OpenAI 格式接口，内置 provider 预设（DeepSeek / OpenAI / Kimi / 通义 / 智谱），流式输出
- **同课程上下文参考**：同一套课程放同一文件夹，新讲义自动参考已有笔记——术语风格统一、衔接进度，你手工修正过的内容会被后续生成吸收（越用越准）
- **本地知识库 RAG**：摄取权威教材/文档（PDF/txt/md）到本地向量库，生成讲义时自动检索相关片段注入 Prompt——视频讲解与参考资料冲突时以资料为准并标注"知识校对"
- **双形态使用**：CLI 独立运行，同时是标准 Python 包，可直接被 AI Agent 作为 Tool 调用
- **多格式导出**：Markdown → LaTeX 学术排版 / GitBook 目录结构

## 安装

要求 Python >= 3.11，推荐 [uv](https://docs.astral.sh/uv/)。Quick Start 里的 `uv tool install .` 已完成全局安装；下面是本地开发模式：

```bash
uv sync                 # 基础安装（含云端 ASR）
uv sync --extra local   # 额外安装本地语音识别引擎 faster-whisper
uv sync --extra ocr     # 额外安装本地 OCR 引擎 PaddleOCR
uv sync --extra rag     # 额外安装 RAG 知识库（chromadb + pymupdf）
```

或传统 pip 方式：

```bash
pip install -e .             # 基础
pip install -e ".[local]"    # 含本地 ASR
pip install -e ".[ocr]"      # 含本地 OCR
pip install -e ".[rag]"      # 含 RAG
```

> ffmpeg 无需手动安装（自动使用内置静态版本）；系统 PATH 里已有 ffmpeg 时优先用系统的。
> 本地 ASR 首次运行会从 HuggingFace 下载模型，国内建议先设置 `$env:HF_ENDPOINT="https://hf-mirror.com"`。

## 配置

配置文件位于 `~/.bili2note/config.toml`（仓库外，不会误提交），也可用环境变量 `BILI2NOTE_*` 覆盖（环境变量优先）。

```bash
# 1. 切换 LLM 提供商（自动带出 base_url / 模型 / 输出上限）
#    可选：deepseek / openai / moonshot / qwen / zhipu
bili2note config llm.provider deepseek

# 2. 填入 API Key
bili2note config llm.api_key sk-xxx

# 3. 填入 B 站登录 Cookie（抓字幕必须配；无字幕视频走 ASR 可不配）
#    获取方式：浏览器登录 B 站 → F12 → Network → 任意请求 → 复制 Cookie 请求头
bili2note config bili.cookie "SESSDATA=xxx; buvid3=xxx"

# 4.（可选）云端 ASR —— 处理无字幕视频，默认 siliconflow SenseVoiceSmall（免费额度充足）
bili2note config asr.api_key sk-xxx

# 5.（可选）VLM —— 画面信息提取（板书/PPT/代码截图），需支持图片输入的视觉模型
#    可选预设：openai(gpt-4o-mini) / qwen(qwen-vl-plus) / siliconflow(Qwen2-VL) / zhipu(glm-4v-flash)
bili2note config vision.provider zhipu
bili2note config vision.api_key sk-xxx

# 查看当前配置（敏感值自动脱敏）
bili2note config
```

手动微调（优先级高于 provider 预设）：

```bash
bili2note config llm.base_url https://your-proxy.com/v1   # 自定义中转地址
bili2note config llm.model deepseek-reasoner              # 换模型
bili2note config llm.max_tokens 16384                     # 输出上限
bili2note config asr.provider openai                      # 云端 ASR 切 openai whisper-1
bili2note config asr.local_model medium                   # 本地模型档位 tiny/base/small/medium/large-v3
bili2note config asr.language zh                          # 指定识别语言（默认自动检测）
```

<details>
<summary>配置项完整对照表</summary>

| 配置项 | 环境变量 | 说明 | 默认值 |
|---|---|---|---|
| `llm.provider` | `BILI2NOTE_PROVIDER` | 接口预设名 | 无（通用 OpenAI 格式） |
| `llm.api_key` | `BILI2NOTE_API_KEY` | API 密钥 | — |
| `llm.base_url` | `BILI2NOTE_BASE_URL` | 接口地址 | 按预设或 openai |
| `llm.model` | `BILI2NOTE_MODEL` | 模型名 | 按预设或 gpt-4o-mini |
| `llm.max_tokens` | `BILI2NOTE_MAX_TOKENS` | 最大输出 token | 按预设或接口默认 |
| `bili.cookie` | `BILI2NOTE_COOKIE` | B 站登录 Cookie | — |
| `asr.provider` | `BILI2NOTE_ASR_PROVIDER` | 云端 ASR 预设：siliconflow / openai | siliconflow |
| `asr.api_key` | `BILI2NOTE_ASR_API_KEY` | 云端 ASR 密钥 | — |
| `asr.base_url` | `BILI2NOTE_ASR_BASE_URL` | 云端 ASR 地址 | 按预设 |
| `asr.model` | `BILI2NOTE_ASR_MODEL` | 云端 ASR 模型 | 按预设 |
| `asr.local_model` | `BILI2NOTE_ASR_LOCAL_MODEL` | 本地模型档位 | small |
| `asr.language` | `BILI2NOTE_ASR_LANGUAGE` | 识别语言 | 自动检测 |
| `vision.provider` | `BILI2NOTE_VISION_PROVIDER` | VLM 预设：openai / qwen / siliconflow / zhipu | openai |
| `vision.api_key` | `BILI2NOTE_VISION_API_KEY` | VLM 密钥 | — |
| `vision.base_url` | `BILI2NOTE_VISION_BASE_URL` | VLM 地址 | 按预设 |
| `vision.model` | `BILI2NOTE_VISION_MODEL` | VLM 模型 | 按预设 |
| `vision.keyframe_interval` | `BILI2NOTE_VISION_INTERVAL` | 关键帧间隔（秒） | 5.0 |
| `vision.max_frames` | `BILI2NOTE_VISION_MAX_FRAMES` | 最多抽取帧数 | 50 |
| `knowledge.db_path` | `BILI2NOTE_KNOWLEDGE_DB` | 向量库存储路径 | 默认 `~/.bili2note/knowledge` |
| `knowledge.embedding` | `BILI2NOTE_KNOWLEDGE_EMBEDDING` | embedding 模式：default=ONNX 本地 / llmapi=LLM API | default |
| `knowledge.top_k` | `BILI2NOTE_KNOWLEDGE_TOP_K` | 每次检索返回的文档块数 | 5 |
| `knowledge.chunk_size` | `BILI2NOTE_KNOWLEDGE_CHUNK_SIZE` | 文档分块字符数 | 500 |

</details>

## 使用

### 生成讲义（核心功能）

```bash
bili2note note BV1xxxx                    # BV 号：有字幕用字幕，无字幕自动 ASR
bili2note note "https://www.bilibili.com/video/BV1xxxx?p=2"   # URL，自动取第 2P
bili2note note BV1xxxx -o notes           # 指定输出目录
bili2note note BV1xxxx --subtitle 2       # 手动选字幕（编号见运行时列表）
bili2note note BV1xxxx --asr local        # 强制本地语音识别（不用官方字幕）
bili2note note BV1xxxx --asr cloud        # 强制云端语音识别
bili2note note BV1xxxx --asr none         # 仅接受官方字幕，无字幕直接报错
bili2note note BV1xxxx --vision auto      # 自动提取画面信息（有 VLM key 用 VLM，否则用 OCR）
bili2note note BV1xxxx --vision vlm       # 强制云端视觉模型识别画面
bili2note note BV1xxxx --vision ocr       # 强制本地 PaddleOCR（需 uv sync --extra ocr）
bili2note note BV1xxxx --knowledge auto   # 自动检索本地知识库，权威资料校对口误、补充术语
bili2note note BV1xxxx -o 课程文件夹       # 同一课程放同一文件夹：新讲义自动参考文件夹内已有笔记
```

流程：抓取字幕（或 ASR 转写）→ 流式打印讲义到终端 → 保存到 `output/BVxxxx-标题.md`。

**ASR 策略**（`--asr`，默认 `auto`）：

- 有官方字幕 → 直接用字幕（最快最准）
- 无字幕 → 已配 `asr.api_key` 走云端；否则装了 faster-whisper 走本地；都没有则给出配置指引
- 强制 `--asr cloud/local` 时跳过字幕，即使视频有字幕也走音频转写

**Vision 策略**（`--vision`，默认 `none`）：

- `none`（默认）：不提取画面信息，仅基于字幕/ASR 文本生成讲义
- `auto`：已配 `vision.api_key` 走云端 VLM；否则装了 PaddleOCR 走本地 OCR；都没有则跳过
- `vlm`：强制云端视觉模型（GPT-4o-mini / Qwen-VL / GLM-4V），同时识别画面文字 + 生成画面描述
- `ocr`：强制本地 PaddleOCR，仅识别画面文字（不含画面描述），不消耗云端额度

> VLM 和 LLM 是不同的模型，`vision.api_key` 与 `llm.api_key` 可以来自不同平台。例如 LLM 用 DeepSeek，VLM 用智谱 GLM-4V。

### 仅抓取字幕

```bash
bili2note fetch BV1xxxx                   # 保存为 output/BVxxxx-P1-subtitle.txt（带时间戳）
```

### 知识库管理（RAG）

```bash
# 摄取参考资料（支持 .txt / .md / .pdf，目录递归摄取）
bili2note knowledge add ./python-cookbook.pdf ./docs/
bili2note knowledge add C:/books/*
# 查看知识库状态
bili2note knowledge list
# 清空（不可恢复）
bili2note knowledge clear
```

> **Embedding 模式**：默认用本地 ONNX MiniLM 模型（零 API 成本，首次使用需从 S3 下载 ~24MB 模型文件）。如果网络受限或想复用已有 API，可切到 LLM API：
> ```bash
> bili2note config knowledge.embedding llmapi
> ```
> 切到 `llmapi` 后会复用已配置的 `llm.api_key`，自动选对应 provider 的 embedding 模型（DeepSeek→deepseek-embedding-v1、OpenAI→text-embedding-3-small 等）。

## Python API / Agent Tool

Bili2Note 同时是标准 Python 包，可直接被 AI Agent 作为 Tool 调用：

```python
import bili2note as b2n

# 1. 查看视频信息
info = b2n.get_video_info("BV1xx411c7mu")
# → {"title": "...", "pages": [...], "duration": 1234, ...}

# 2. 列出可用字幕
subs = b2n.list_subtitles("BV1xx411c7mu")
# → [{"index": 1, "lan": "zh-CN", "lan_doc": "中文（简体）", "is_ai": False}]

# 3. 获取带时间戳的字幕
lines = b2n.get_subtitles("BV1xx411c7mu")
# → [{"from": 0.5, "to": 3.2, "content": "大家好今天讲一下..."}, ...]

# 4. 一键生成完整讲义（最常用）
markdown = b2n.generate_note(
    "BV1xx411c7mu",
    vision_mode="auto",       # 可选：none/auto/vlm/ocr
    knowledge="auto",         # 可选：none/auto（自动检索本地知识库）
)
# → "# 讲义标题\n\n## ..." 完整 Markdown

# 5. RAG 知识库操作
b2n.knowledge_add(["./textbook.pdf", "./docs/"])  # 摄取
results = b2n.knowledge_query("Python GIL 是什么")  # 检索
b2n.knowledge_list()                               # 状态

# 6. 多格式导出
b2n.export_latex(markdown, "output/讲义.tex")     # → LaTeX .tex
b2n.export_gitbook("./notes/", "./gitbook/")       # → GitBook SUMMARY.md + 章节
```

<details>
<summary>全部 Tool 列表（14 个）</summary>

| Tool | 参数 | 返回 | 说明 |
|---|---|---|---|
| `get_video_info(video)` | BV 号/URL | dict | 视频元信息（标题、UP主、时长、分P列表） |
| `list_subtitles(video, page)` | + page(默认1) | list[dict] | 可用字幕列表 |
| `get_subtitles(video, page, subtitle_index, prefer_chinese)` | 多个可选参数 | list[dict] | 带时间戳的字幕文本 |
| `transcribe(video, page, mode)` | mode=auto/cloud/local | list[dict] | 强制 ASR 转写 |
| `get_transcript(video, page, asr_mode)` | — | dict | 统一获取转写（字幕优先，无字幕自动 ASR） |
| `extract_visual(video, page, mode)` | mode=none/auto/vlm/ocr | str | 画面信息提取，返回可注入 Prompt 的文本 |
| `generate_note(video, page, subtitle_index, asr_mode, vision_mode, knowledge)` | 完整管线参数 | str | 一键从视频生成 Markdown 讲义 |
| `knowledge_add(paths)` | 文件/目录列表 | dict | 摄取文件到 RAG 知识库 |
| `knowledge_list()` | — | dict | 知识库状态（chunk 数、来源列表） |
| `knowledge_clear()` | — | dict | 清空知识库 |
| `knowledge_query(text, top_k)` | + top_k | list[dict] | 检索知识库，返回最相关文档块 |
| `get_config()` | — | dict | 当前配置概览（敏感字段已脱敏） |
| `export_gitbook(md_dir, out_dir)` | 两个路径 | dict | 将目录下所有 .md 组织成 GitBook 结构 |
| `export_latex(markdown, out_path)` | 文本 + 路径 | Path | Markdown → LaTeX 学术排版 |

</details>

> Agent 调用 bili2note 时，bili2note 的 LLM API 是**用户自己配置的**（`~/.bili2note/config.toml`），Agent 自身消耗的是 **Agent 平台的 token**。两层 API 互相独立。

<details>
<summary>底层模块也可独立调用</summary>

```python
from bili2note.audio import download_audio, chunk_wavs
from bili2note.asr import transcribe_cloud, transcribe_local
from bili2note.vision import download_video, extract_keyframes, visual_context_text
from bili2note.rag import ingest, query, format_retrieval

# 例：手动摄取 + 检索知识库
chunks = ingest([Path("textbook.pdf")], cfg.knowledge)
results = query("Python 生成器", cfg.knowledge)
print(format_retrieval(results))
```

</details>

## 项目结构

```
bili2note/
├── cli.py       # 命令行入口：note / fetch / knowledge / config
├── tools.py     # Agent Tool 接口层（14 个标准 Tool）
├── exporters.py # 多格式导出（GitBook / LaTeX）
├── config.py    # 配置管理（TOML + 环境变量 + provider 预设）
├── bili.py      # B 站客户端：视频信息 / 字幕 / 音视频流 / Wbi 签名（自实现）
├── audio.py     # 音频下载 + ffmpeg 转码分片（内置静态 ffmpeg）
├── vision.py    # 视频下载 + ffmpeg 关键帧抽取 + VLM/OCR 画面分析
├── asr.py       # 语音识别：云端 OpenAI 兼容接口 / 本地 faster-whisper
├── llm.py       # Prompt 模板 + LLM 调用（流式 + 长文本 map-reduce 分块）
└── rag.py       # 向量知识库（chromadb）：摄取 / 检索 / 格式化
```

## 已知限制

- **抓字幕需要登录**：不配置 Cookie 时接口不返回字幕列表；无字幕视频走 ASR 则无需 Cookie
- **本地 ASR 精度**：中文视频建议 `asr.local_model` 用 small 及以上；tiny/base 仅适合快速验证
- **BGM 声大的视频**：已内置 VAD 漏检兜底（自动关闭 VAD 重转），但转写质量仍受背景音影响
- **云端 ASR 时间戳**：部分接口不返回分段时间戳，此时按句长在时间窗内近似铺开（分片级精确）
- **画面信息下载量**：关键帧抽取需要下载完整视频流（最低码率约 1.5-3MB/分钟），但比下载原始视频省 90% 以上
- **VLM 成本**：逐帧调用视觉模型会产生额外费用；建议长视频加大 `vision.keyframe_interval`（如 10s）或改用本地 OCR

## Roadmap

- [x] **Phase 1**：官方字幕抓取 + LLM 提纯 + Markdown 输出
- [x] **Phase 2**：音频流提取 + ASR 语音转写（云端/本地双模）+ 长文本切片
- [x] **Phase 3**：视频画面信息提取（VLM / PaddleOCR）+ 同课程上下文参考
- [x] **Phase 4**：本地知识库 RAG（chromadb），自动纠正讲师口误、补充术语背景
- [x] **Phase 5**：Agent Tool 接口层 + 多格式导出（GitBook / LaTeX）+ Skill 化封装

## 声明

本项目仅供个人学习研究使用，请遵守 B 站用户协议，勿用于批量抓取或商业用途。字幕与视频内容的版权归原作者所有。
