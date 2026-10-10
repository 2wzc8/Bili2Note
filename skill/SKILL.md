---
name: bili2note
description: B站视频→AI结构化讲义。当用户想把B站视频转成讲义/笔记、或者提到B站视频总结、视频字幕提取、视频转Markdown讲义时触发。也适用于用户想操作本地RAG知识库摄取教材/PDF、或导出LaTeX/GitBook格式。
---

# Bili2Note — B站视频转AI讲义

## 描述

一个 Python CLI + Agent Tool，能把任意 B 站视频转化为教科书级 Markdown 讲义。支持自动抓字幕（无字幕自动 ASR）、画面信息提取（VLM/OCR）、本地 RAG 知识库校对口误、LaTeX/GitBook 多格式导出。

## 使用场景

触发关键词（命中其一即可）：
- 视频：BV 号、B站视频、bilibili、B站、课程视频、视频讲义、视频笔记、视频总结
- 字幕：抓字幕、下字幕、视频字幕、B站字幕
- RAG：知识库、摄取教材、PDF 参考资料、本地知识库、rag、vector db
- 导出：LaTeX、.tex、GitBook、SUMMARY.md、学术排版

## 前置条件（首次安装）

如果 `bili2note` 命令不存在，先运行：

```powershell
# 方式1：用 uv（推荐，跨平台，无依赖）
uv tool install . --force

# 方式2：pip 全局安装
pip install -e .
```

可选扩展（按需安装）：
```powershell
# 本地语音识别（更快、离线可用）
uv tool install ".[local]"

# 本地 OCR（板书/PPT 文字识别）
uv tool install ".[ocr]"

# RAG 知识库（向量库 + PDF 读取）
uv tool install ".[rag]"
```

安装完后初始化配置（必需）：
```powershell
bili2note config llm.provider deepseek          # 选提供商
bili2note config llm.api_key sk-xxx             # 填 API Key
bili2note config bili.cookie "SESSDATA=xxx"      # 抓字幕需要
```

## 指令（Agent 调用流程）

### 场景A：用户想生成讲义

按以下优先级走：

1. **先用 Python Tool**（如果可用）：
```python
import bili2note as b2n

# 一键生成（最常用）
md = b2n.generate_note(
    "BV1xx411c7mu",          # BV 号或 URL
    vision_mode="none",      # none / auto / vlm / ocr
    knowledge="none",        # none / auto
)

# 或者分步骤（想让用户确认中间结果时）
info = b2n.get_video_info("BV1xx411c7mu")      # 视频元信息
lines = b2n.get_transcript("BV1xx411c7mu")     # 字幕/ASR
```

2. **如果 Python 不可用，回退到 CLI**：
```powershell
bili2note note BV1xx411c7mu
bili2note note BV1xx411c7mu --vision auto --knowledge auto
```

### 场景B：用户想管理 RAG 知识库

```powershell
# CLI 方式
bili2note knowledge add ./textbook.pdf ./docs/
bili2note knowledge add C:/books/
bili2note knowledge list
bili2note knowledge clear

# Python 方式
import bili2note as b2n
b2n.knowledge_add(["./textbook.pdf", "./docs/"])
b2n.knowledge_list()
b2n.knowledge_query("Python GIL")
```

### 场景C：用户想导出格式

```python
import bili2note as b2n

# Markdown → LaTeX
b2n.export_latex(markdown_text, "讲义.tex")

# 整个目录 → GitBook
b2n.export_gitbook("./notes/", "./gitbook/")
```

## 关键注意事项

- **bili2note 的 LLM API 是用户自己配置的**（`~/.bili2note/config.toml`），不是 Agent 的 API。Agent 只需 import 并调用 Tool。
- **抓字幕必须登录 Cookie**。如果报错"没有可用字幕"，提示用户配置：`bili2note config bili.cookie "SESSDATA=xxx"`
- **RAG 默认用本地 ONNX MiniLM**（零 API 成本，首次自动下载 ~24MB 模型）。如果用户有已有 API，可切到 `bili2note config knowledge.embedding llmapi`
- **输出路径**：CLI 默认写到 `./output/BVxxxx-标题.md`；Python Tool 返回字符串，由 Agent 决定存哪
- **流式输出**：CLI 是流式打印到终端的；Python Tool 默认等全部生成完再返回，流式需用 CLI

## 全部 Python Tool 速查

```python
import bili2note as b2n

# 视频
b2n.get_video_info(video)           # → dict
b2n.list_subtitles(video, page=1)   # → list[dict]
b2n.get_subtitles(video, page=1, prefer_chinese=True)  # → list[dict]
b2n.get_transcript(video, page=1, asr_mode="auto")      # → dict{source, lines, duration}
b2n.transcribe(video, page=1, mode="auto")              # → list[dict] 强制 ASR
b2n.extract_visual(video, page=1, mode="auto")          # → str 画面信息

# 核心
b2n.generate_note(video, page=None, subtitle_index=None,
                  asr_mode="auto", vision_mode="none",
                  knowledge="none")                     # → str Markdown

# RAG
b2n.knowledge_add(["path1", "path2"])     # → dict{ingested, files, skipped}
b2n.knowledge_list()                      # → dict{chunk_count, sources, db_path}
b2n.knowledge_clear()                     # → dict{cleared}
b2n.knowledge_query(text, top_k=5)        # → list[dict{text, source, distance}]

# 系统
b2n.get_config()                          # → dict 配置概览（key 已脱敏）

# 导出
b2n.export_latex(markdown, out_path)      # → Path
b2n.export_gitbook(md_dir, out_dir)       # → dict{chapters, summary}
```

## CLI 速查

```powershell
# 生成讲义
bili2note note BV1xxxx                          # 最基本
bili2note note BV1xxxx -o ./我的课程文件夹/      # 输出目录（同名文件夹=同课程上下文）
bili2note note BV1xxxx --vision auto             # 提取画面信息
bili2note note BV1xxxx --knowledge auto         # RAG 知识库校对

# 抓字幕
bili2note fetch BV1xxxx

# 知识库
bili2note knowledge add ./docs/
bili2note knowledge list
bili2note knowledge clear

# 配置
bili2note config                                # 查看当前
bili2note config llm.provider deepseek          # 设置
```
