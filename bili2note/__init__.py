"""Bili2Note —— B 站视频转 AI 结构化讲义。

可作为 CLI 工具运行，也可作为 Python 库供 AI Agent 调用。

Agent Tool 接口（tools.py）：
    - get_video_info(video)          → 视频元信息
    - list_subtitles(video, page)   → 可用字幕列表
    - get_subtitles(video, page)     → 带时间戳的字幕文本
    - transcribe(video, page, mode)  → ASR 转写
    - get_transcript(video, page)    → 统一获取转写（字幕优先）
    - extract_visual(video, page)    → 画面信息提取
    - generate_note(video, ...)     → 完整管线：视频 → Markdown 讲义
    - knowledge_add(paths)           → 摄取文件到 RAG 知识库
    - knowledge_list()               → 查看知识库状态
    - knowledge_clear()              → 清空知识库
    - knowledge_query(text, top_k)   → 检索知识库
    - get_config()                   → 配置概览

多格式导出（exporters.py）：
    - export_gitbook(md_dir, out_dir)   → GitBook 目录结构
    - export_latex(markdown, out_path)  → LaTeX 学术排版

底层模块：
    - bili.py     BiliClient / 字幕下载 / 视频流
    - asr.py      云端 & 本地语音转写
    - llm.py      LLM 讲义生成
    - rag.py      向量知识库（chromadb）
    - vision.py   画面关键帧提取 + VLM/OCR
    - config.py   TOML 配置管理
"""

__version__ = "0.3.0"

from .tools import (
    get_video_info,
    list_subtitles,
    get_subtitles,
    transcribe,
    get_transcript,
    extract_visual,
    generate_note,
    knowledge_add,
    knowledge_list,
    knowledge_clear,
    knowledge_query,
    get_config,
)
from .exporters import export_gitbook, export_latex, export_latex_file

__all__ = [
    "__version__",
    "get_video_info",
    "list_subtitles",
    "get_subtitles",
    "transcribe",
    "get_transcript",
    "extract_visual",
    "generate_note",
    "knowledge_add",
    "knowledge_list",
    "knowledge_clear",
    "knowledge_query",
    "get_config",
    "export_gitbook",
    "export_latex",
    "export_latex_file",
]
