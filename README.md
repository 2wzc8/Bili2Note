# Bili2Note

B 站视频 → AI 结构化讲义。输入 BV 号，自动抓取官方字幕，调用大模型将口语化、碎片化的视频内容提纯为教科书级 Markdown 讲义。

当前版本：**Phase 1 MVP**（官方字幕 → LLM 提纯 → Markdown）。完整规划见 [requires.md](requires.md)。

## 功能特性

- **B 站字幕抓取**：支持 BV 号 / 视频链接（含 `?p=N` 分P），自动处理 Wbi 签名与风控（buvid 指纹），字幕自动选择（中文人工 CC 优先，其次 AI 字幕）
- **LLM 讲义提纯**：兼容任意 OpenAI 格式接口，内置 provider 预设（DeepSeek / OpenAI / Kimi / 通义 / 智谱），流式输出，讲义包含 TL;DR、知识大纲、详细讲义、时间戳锚点、口误修正标注、术语表、易错点
- **双形态使用**：CLI 独立运行，同时是标准 Python 包，可直接被 AI Agent 作为 Tool 调用

## 安装

要求 Python >= 3.11，推荐使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
```

或传统方式：

```bash
pip install -e .
```

## 配置

配置文件位于 `~/.bili2note/config.toml`，也可用环境变量 `BILI2NOTE_*` 覆盖（环境变量优先）。

```bash
# 1. 切换 LLM 提供商（自动带出 base_url / 模型 / 输出上限）
#    可选：deepseek / openai / moonshot / qwen / zhipu
bili2note config llm.provider deepseek

# 2. 填入 API Key
bili2note config llm.api_key sk-xxx

# 3. 填入 B 站登录 Cookie（必需！未登录时 B 站不返回任何字幕，包括人工 CC 字幕）
#    获取方式：浏览器登录 B 站 → F12 → Network → 任意请求 → 复制 Cookie 请求头
bili2note config bili.cookie "SESSDATA=xxx; buvid3=xxx"

# 查看当前配置（敏感值自动脱敏）
bili2note config
```

手动微调（优先级高于 provider 预设）：

```bash
bili2note config llm.base_url https://your-proxy.com/v1   # 自定义中转地址
bili2note config llm.model deepseek-reasoner              # 换模型
bili2note config llm.max_tokens 16384                     # 输出上限
```

| 配置项 | 环境变量 | 说明 | 默认值 |
|---|---|---|---|
| `llm.provider` | `BILI2NOTE_PROVIDER` | 接口预设名 | 无（通用 OpenAI 格式） |
| `llm.api_key` | `BILI2NOTE_API_KEY` | API 密钥 | — |
| `llm.base_url` | `BILI2NOTE_BASE_URL` | 接口地址 | 按预设或 openai |
| `llm.model` | `BILI2NOTE_MODEL` | 模型名 | 按预设或 gpt-4o-mini |
| `llm.max_tokens` | `BILI2NOTE_MAX_TOKENS` | 最大输出 token | 按预设或接口默认 |
| `bili.cookie` | `BILI2NOTE_COOKIE` | B 站登录 Cookie | — |

## 使用

### 生成讲义（核心功能）

```bash
bili2note note BV1xxxx                    # BV 号
bili2note note "https://www.bilibili.com/video/BV1xxxx?p=2"   # URL，自动取第 2P
bili2note note BV1xxxx -o notes           # 指定输出目录
bili2note note BV1xxxx --subtitle 2       # 手动选字幕（编号见运行时列表）
```

流程：抓取字幕 → 流式打印讲义到终端 → 保存到 `output/BVxxxx-标题.md`。

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

## 项目结构

```
bili2note/
├── cli.py      # 命令行入口：note / fetch / config
├── config.py   # 配置管理（TOML + 环境变量 + provider 预设）
├── bili.py     # B 站客户端：视频信息 / 字幕 / Wbi 签名（自实现，无第三方 B 站库）
└── llm.py      # Prompt 模板 + LLM 调用（流式）
```

## 已知限制

- **必须有字幕**：当前仅支持有官方字幕（人工 CC / AI 字幕）的视频；无字幕视频的音频转写（ASR）在 Phase 2 实现
- **AI 字幕依赖登录**：不配置 Cookie 时接口不返回字幕列表
- 长视频字幕目前整段送入模型，依赖模型上下文窗口（Phase 2 实现切片）

## Roadmap

- [x] **Phase 1**：官方字幕抓取 + LLM 提纯 + Markdown 输出（当前）
- [ ] **Phase 2**：音频流提取 + ASR 语音转写（faster-whisper / 云端 API）+ 长文本切片
- [ ] **Phase 3**：本地知识库 RAG，自动纠正讲师口误、补充术语背景
- [ ] **Phase 4**：多格式导出（GitBook / LaTeX）、凭据管理完善、Agent 技能化封装

## 声明

本项目仅供个人学习研究使用，请遵守 B 站用户协议，勿用于批量抓取或商业用途。字幕与视频内容的版权归原作者所有。
