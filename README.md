# Bili2Note

B 站视频 → AI 结构化讲义。输入 BV 号，自动抓取官方字幕（无字幕则提取音频做语音识别），调用大模型将口语化、碎片化的视频内容提纯为教科书级 Markdown 讲义。

当前版本：**Phase 2**（字幕 + 音频 ASR → 长文本切片 → LLM 提纯 → Markdown）。完整规划见 [requires.md](requires.md)。

## 功能特性

- **B 站字幕抓取**：支持 BV 号 / 视频链接（含 `?p=N` 分P），自动处理 Wbi 签名与风控（buvid 指纹），字幕自动选择（中文人工 CC 优先，其次 AI 字幕）
- **无字幕视频 ASR 转写**：自动提取最低码率 Dash 音频流（不下载画面，省 90% 流量），内置静态 ffmpeg（无需手动安装），按 10 分钟分片规避云端 25MB 限制；支持云端 API 与本地 faster-whisper 双模
- **长视频分块生成**：转写文本超长时自动按时间轴分块（带重叠滑动窗口防止语义截断），逐块生成草稿后合并成稿
- **LLM 讲义提纯**：兼容任意 OpenAI 格式接口，内置 provider 预设（DeepSeek / OpenAI / Kimi / 通义 / 智谱），流式输出，讲义包含 TL;DR、知识大纲、详细讲义、时间戳锚点、口误修正标注、术语表、易错点
- **双形态使用**：CLI 独立运行，同时是标准 Python 包，可直接被 AI Agent 作为 Tool 调用

## 安装

要求 Python >= 3.11，推荐使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync                # 基础安装（含云端 ASR）
uv sync --extra local  # 额外安装本地语音识别引擎 faster-whisper
```

或传统方式：

```bash
pip install -e .            # 基础
pip install -e ".[local]"   # 含本地 ASR
```

> ffmpeg 无需手动安装（自动使用内置静态版本）；系统 PATH 里已有 ffmpeg 时优先用系统的。
> 本地 ASR 首次运行会从 HuggingFace 下载模型，国内建议先设置 `$env:HF_ENDPOINT="https://hf-mirror.com"`。

## 配置

配置文件位于 `~/.bili2note/config.toml`，也可用环境变量 `BILI2NOTE_*` 覆盖（环境变量优先）。

```bash
# 1. 切换 LLM 提供商（自动带出 base_url / 模型 / 输出上限）
#    可选：deepseek / openai / moonshot / qwen / zhipu
bili2note config llm.provider deepseek

# 2. 填入 API Key
bili2note config llm.api_key sk-xxx

# 3. 填入 B 站登录 Cookie（无字幕视频走 ASR 可不配，但要抓字幕必须配）
#    获取方式：浏览器登录 B 站 → F12 → Network → 任意请求 → 复制 Cookie 请求头
bili2note config bili.cookie "SESSDATA=xxx; buvid3=xxx"

# 4.（可选）云端 ASR —— 处理无字幕视频，默认 siliconflow SenseVoiceSmall（免费额度充足）
bili2note config asr.api_key sk-xxx

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
| `asr.local_model` | `BILI2NOTE_ASR_LOCAL_MODEL` | 本地模型档位 | small（中文推荐，追求速度可用 base） |
| `asr.language` | `BILI2NOTE_ASR_LANGUAGE` | 识别语言 | 自动检测 |

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
```

流程：抓取字幕（或 ASR 转写）→ 流式打印讲义到终端 → 保存到 `output/BVxxxx-标题.md`。

ASR 策略说明（`--asr`，默认 `auto`）：

- 有官方字幕 → 直接用字幕（最快最准）
- 无字幕 → 已配 `asr.api_key` 走云端；否则装了 faster-whisper 走本地；都没有则给出配置指引
- 强制 `--asr cloud/local` 时跳过字幕，即使视频有字幕也走音频转写

### 仅抓取字幕

```bash
bili2note fetch BV1xxxx                   # 保存为 output/BVxxxx-P1-subtitle.txt（带时间戳）
```

### 作为 Python 库调用（给 AI Agent 用）

```python
from bili2note.bili import BiliClient, parse_video_id
from bili2note.config import load_config
from bili2note.llm import generate_note

cfg = load_config()
bvid, page = parse_video_id("BV1xxxx")
client = BiliClient(cfg.bili.cookie)
info = client.video_info(bvid)
lines = client.subtitle_content(client.subtitles(bvid, page, info=info)[0])
md = generate_note(info, lines, cfg.llm)
```

ASR 各环节也可独立调用：

```python
from bili2note.audio import download_audio, chunk_wavs
from bili2note.asr import transcribe_cloud, transcribe_local

url, duration = client.audio_stream(bvid)          # Dash 音频流地址 + 分P时长
m4s = download_audio(url, tmp_dir)                  # 下载低码率音频
chunks = chunk_wavs(m4s, duration, tmp_dir)         # 10 分钟分片转 16k wav
lines = transcribe_cloud(chunks[0][0], cfg.asr, offset=chunks[0][1])
```

## 项目结构

```
bili2note/
├── cli.py      # 命令行入口：note / fetch / config
├── config.py   # 配置管理（TOML + 环境变量 + provider 预设）
├── bili.py     # B 站客户端：视频信息 / 字幕 / 音频流 / Wbi 签名（自实现，无第三方 B 站库）
├── audio.py    # 音频下载 + ffmpeg 转码分片（内置静态 ffmpeg）
├── asr.py      # 语音识别：云端 OpenAI 兼容接口 / 本地 faster-whisper
└── llm.py      # Prompt 模板 + LLM 调用（流式 + 长文本 map-reduce 分块）
```

## 已知限制

- **抓字幕需要登录**：不配置 Cookie 时接口不返回字幕列表；无字幕视频走 ASR 则无需 Cookie
- **本地 ASR 精度**：中文视频建议 `asr.local_model` 用 small 及以上；tiny/base 仅适合快速验证
- **BGM 声大的视频**：已内置 VAD 漏检兜底（自动关闭 VAD 重转），但转写质量仍受背景音影响
- **云端 ASR 时间戳**：部分接口不返回分段时间戳，此时按句长在时间窗内近似铺开（分片级精确）

## Roadmap

- [x] **Phase 1**：官方字幕抓取 + LLM 提纯 + Markdown 输出
- [x] **Phase 2**：音频流提取 + ASR 语音转写（云端/本地双模）+ 长文本切片（当前）
- [ ] **Phase 3**：本地知识库 RAG，自动纠正讲师口误、补充术语背景
- [ ] **Phase 4**：多格式导出（GitBook / LaTeX）、凭据管理完善、Agent 技能化封装

## 声明

本项目仅供个人学习研究使用，请遵守 B 站用户协议，勿用于批量抓取或商业用途。字幕与视频内容的版权归原作者所有。
