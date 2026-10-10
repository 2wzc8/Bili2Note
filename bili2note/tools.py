"""Agent Tool 接口层：封装 Bili2Note 的核心能力，供 AI Agent 作为 Tool 调用。

每个函数都是独立、无副作用（除了写文件/调 B 站 API/调 LLM API）的标准 Tool。
docstring 中的参数说明和返回结构是 Agent 理解 Tool 的唯一依据，请保持准确清晰。
"""

from __future__ import annotations

import tempfile
import shutil
from pathlib import Path
from typing import Any

from .bili import BiliClient, BiliError, parse_video_id, fmt_ts
from .config import Config, load_config
from .rag import (
    ingest as rag_ingest,
    query as rag_query,
    format_retrieval as rag_format,
    list_sources as rag_list,
    clear as rag_clear,
)


# ---- 内部辅助 ----

def _client(cfg: Config | None = None) -> BiliClient:
    if cfg is None:
        cfg = load_config()
    return BiliClient(cookie=cfg.bili.cookie)


def _parse(video: str) -> tuple[str, int]:
    """从 BV 号或 URL 解析 (bvid, page)。"""
    return parse_video_id(video)


# ============================================================================
# 视频信息 & 字幕
# ============================================================================

def get_video_info(video: str) -> dict:
    """获取 B 站视频的元信息。

    Args:
        video: BV 号（如 "BV1xx411c7mu"）或视频 URL。

    Returns:
        dict，包含关键字段：
        - bvid / aid / page: 视频标识
        - title: 视频标题
        - owner: UP 主信息 {"name": str, "mid": int}
        - duration: 时长（秒）
        - desc: 简介
        - pages: 分P列表 [{"page": int, "part": str, "duration": float, "cid": int}, ...]
        - stats: 播放/弹幕/收藏等统计
    """
    bvid, page = _parse(video)
    info = _client().video_info(bvid)
    pages = info.get("pages", [])
    # 简化 pages 列表
    pages_out = [
        {
            "page": p["page"],
            "part": p.get("part", ""),
            "duration": p.get("duration", 0),
            "cid": p["cid"],
        }
        for p in pages
    ]
    return {
        "bvid": bvid,
        "aid": info.get("aid"),
        "title": info.get("title", ""),
        "owner": info.get("owner", {}),
        "duration": info.get("duration", 0),
        "desc": info.get("desc", ""),
        "pages": pages_out,
        "current_page": page,
        "stats": info.get("stat", {}),
    }


def list_subtitles(video: str, page: int | None = None) -> list[dict]:
    """列出指定视频分P的可用字幕（人工 CC 和 AI 生成）。

    Args:
        video: BV 号或 URL。
        page: 分P页码（1-based），默认 1。

    Returns:
        list[dict]，每项 {"index": int, "lan": str, "lan_doc": str, "is_ai": bool}。
        注意：未登录时 B 站不返回字幕列表，此时返回空列表。
    """
    bvid, p = _parse(video)
    if page is not None:
        p = page
    info = _client().video_info(bvid)
    subs = _client().subtitles(bvid, p, info=info)
    return [
        {"index": i + 1, "lan": s.lan, "lan_doc": s.lan_doc, "is_ai": s.ai}
        for i, s in enumerate(subs)
    ]


def get_subtitles(video: str, page: int | None = None,
                  subtitle_index: int | None = None,
                  prefer_chinese: bool = True) -> list[dict]:
    """获取带时间戳的字幕文本。若无可用字幕则抛错。

    Args:
        video: BV 号或 URL。
        page: 分P页码（1-based），默认 1。
        subtitle_index: 字幕编号（1-based），默认自动选第一个中文字幕。
        prefer_chinese: True 时优先选 zh-CN / zh-Hans 字幕。

    Returns:
        list[dict]，每项 {"from": float, "to": float, "content": str}，
        from/to 是秒，content 是字幕文本。
    """
    bvid, p = _parse(video)
    if page is not None:
        p = page
    client = _client()
    info = client.video_info(bvid)
    subs = client.subtitles(bvid, p, info=info)
    if not subs:
        raise RuntimeError(
            "该视频没有可用字幕。注意：未登录时 B 站不返回字幕（含人工CC）。"
            "请先配置 Cookie：bili2note config bili.cookie 'SESSDATA=xxx'"
        )

    if subtitle_index is not None:
        if subtitle_index < 1 or subtitle_index > len(subs):
            raise ValueError(f"字幕编号 {subtitle_index} 超出范围（共 {len(subs)} 个）")
        sub = subs[subtitle_index - 1]
    elif prefer_chinese:
        zh = [s for s in subs if s.lan.startswith("zh")]
        sub = zh[0] if zh else subs[0]
    else:
        sub = subs[0]

    raw = client.subtitle_content(sub)
    return [
        {"from": line["from"], "to": line["to"], "content": line["content"]}
        for line in raw
    ]


# ============================================================================
# ASR 语音转写
# ============================================================================

def transcribe(video: str, page: int | None = None,
               mode: str = "auto") -> list[dict]:
    """下载音频流 → ASR 转写为带时间戳文本。

    Args:
        video: BV 号或 URL。
        page: 分P页码，默认 1。
        mode: "auto"（默认，本地优先，没装本地模型走云端） / "cloud" / "local"。

    Returns:
        list[dict]，每项 {"from": float, "to": float, "content": str}。
    """
    from .asr import transcribe_cloud, transcribe_local
    from .audio import chunk_wavs, download_audio, full_wav

    cfg = load_config()
    bvid, p = _parse(video)
    if page is not None:
        p = page
    client = _client(cfg)

    url, duration = client.audio_stream(bvid, p)
    tmp = Path(tempfile.mkdtemp(prefix="bili2note-"))
    try:
        m4s = download_audio(url, tmp)
        if mode == "local":
            return transcribe_local(full_wav(m4s, tmp), cfg.asr)
        # auto / cloud：云端分片转写
        chunks = chunk_wavs(m4s, duration, tmp)
        lines: list[dict] = []
        for wav, start, length in chunks:
            lines.extend(transcribe_cloud(wav, cfg.asr, offset=start, duration=length))
        return lines
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def get_transcript(video: str, page: int | None = None,
                   subtitle_index: int | None = None,
                   asr_mode: str = "auto") -> dict:
    """统一获取带时间戳的视频转写文本。优先用官方字幕，无字幕时自动走 ASR。

    Args:
        video: BV 号或 URL。
        page: 分P页码，默认 1。
        subtitle_index: 指定字幕编号，默认自动选中文。
        asr_mode: ASR 模式 auto/cloud/local。

    Returns:
        dict: {
            "source": "subtitle" / "asr",
            "lines": [{"from": float, "to": float, "content": str}, ...],
            "duration": float,   # 视频总时长（秒）
        }
    """
    bvid, p = _parse(video)
    if page is not None:
        p = page
    client = _client()
    info = client.video_info(bvid)

    # 先试字幕
    subs = client.subtitles(bvid, p, info=info)
    if subs:
        if subtitle_index is not None:
            sub = subs[subtitle_index - 1]
        else:
            zh = [s for s in subs if s.lan.startswith("zh")]
            sub = zh[0] if zh else subs[0]
        raw = client.subtitle_content(sub)
        lines = [{"from": l["from"], "to": l["to"], "content": l["content"]} for l in raw]
        return {
            "source": "subtitle",
            "lines": lines,
            "duration": float(info.get("duration", 0)),
        }

    # 无字幕，走 ASR
    asr_lines = transcribe(video, page=page, mode=asr_mode)
    return {
        "source": "asr",
        "lines": asr_lines,
        "duration": float(info.get("duration", 0)),
    }


# ============================================================================
# 画面信息提取（VLM / OCR）
# ============================================================================

def extract_visual(video: str, page: int | None = None,
                   mode: str = "auto",
                   interval: float | None = None) -> str:
    """提取视频关键帧并分析画面文字/内容，返回可注入讲义的文本。

    Args:
        video: BV 号或 URL。
        page: 分P页码，默认 1。
        mode: "auto"（有 VLM key 用 VLM，否则本地 OCR，都没有则返回空串）/ "vlm" / "ocr"。
        interval: 关键帧间隔（秒），默认取 config 中的 vision.keyframe_interval。

    Returns:
        str，按时间戳排列的画面信息文本；若无法提取则返回空串。
    """
    from .vision import (
        analyze_frames_ocr, analyze_frames_vlm,
        download_video, extract_keyframes, visual_context_text,
    )

    cfg = load_config()
    bvid, p = _parse(video)
    if page is not None:
        p = page
    client = _client(cfg)
    info = client.video_info(bvid)

    # 确定模式
    if mode == "auto":
        if cfg.vision.api_key:
            mode = "vlm"
        else:
            try:
                import paddleocr  # noqa: F401
                mode = "ocr"
            except ImportError:
                return ""
    if mode == "vlm" and not cfg.vision.api_key:
        raise RuntimeError("VLM 模式需要配置 vision.api_key")

    url, duration = client.video_stream(bvid, p, info=info)
    tmp = Path(tempfile.mkdtemp(prefix="bili2note-vision-"))
    try:
        video_path = download_video(url, tmp)
        interval = interval or cfg.vision.keyframe_interval
        max_frames = cfg.vision.max_frames
        frames = extract_keyframes(video_path, tmp, duration,
                                   interval=interval, max_frames=max_frames)
        if not frames:
            return ""
        if mode == "vlm":
            frame_infos = analyze_frames_vlm(frames, cfg.vision)
        else:
            frame_infos = analyze_frames_ocr(frames)
        return visual_context_text(frame_infos)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================================
# 讲义生成（核心 Tool）
# ============================================================================

def generate_note(
    video: str,
    page: int | None = None,
    subtitle_index: int | None = None,
    asr_mode: str = "auto",
    vision_mode: str = "none",
    knowledge: str = "none",
    stream: bool = False,
) -> str:
    """完整管线：获取字幕/ASR →（可选）画面信息 →（可选）RAG 检索 → LLM 生成 Markdown 讲义。

    这是最常用的 Tool，一次调用完成从视频到讲义的全部工作。

    Args:
        video: BV 号或 URL。
        page: 分P页码，默认 1。
        subtitle_index: 指定字幕编号（1-based），默认自动选中文。
        asr_mode: ASR 模式 auto/cloud/local，默认 auto。
        vision_mode: 画面提取 "none" / "auto" / "vlm" / "ocr"，默认 none。
        knowledge: RAG 检索 "none" / "auto"，默认 none。
            设为 auto 时，会从本地知识库检索与视频主题相关的权威资料注入 Prompt。
            需要先通过 knowledge_add() 摄取参考资料。
        stream: True 时逐分块流式生成；False 时等全部生成完再返回。

    Returns:
        str，完整 Markdown 讲义（含标题、时间戳锚点、口误修正、术语表等）。
    """
    from .llm import generate_note as llm_generate

    cfg = load_config()
    bvid, p = _parse(video)
    if page is not None:
        p = page
    client = _client(cfg)

    # 1. 视频信息
    info = client.video_info(bvid)

    # 2. 字幕 / ASR
    transcript = get_transcript(video, page=p, subtitle_index=subtitle_index,
                                asr_mode=asr_mode)
    lines = transcript["lines"]
    if not lines:
        raise RuntimeError("转写结果为空，无法生成讲义")

    # 3. 画面信息
    visual_ctx = ""
    if vision_mode != "none":
        try:
            visual_ctx = extract_visual(video, page=p, mode=vision_mode)
        except Exception:
            visual_ctx = ""

    # 4. RAG 知识检索
    kn_ctx = ""
    if knowledge != "none":
        k_query = (info.get("title", "") + "\n"
                   + "\n".join(l["content"] for l in lines[:10]))
        try:
            results = rag_query(k_query, cfg.knowledge)
            kn_ctx = rag_format(results)
        except Exception:
            kn_ctx = ""

    # 5. LLM 生成
    return llm_generate(
        info=info,
        lines=lines,
        cfg=cfg.llm,
        page=p,
        visual_context=visual_ctx or None,
        knowledge_context=kn_ctx or None,
    )


# ============================================================================
# RAG 知识库操作
# ============================================================================

def knowledge_add(paths: list[str]) -> dict:
    """摄取本地文件到 RAG 知识库。

    Args:
        paths: 文件或目录路径列表。目录会递归摄取所有 .txt / .md / .pdf 文件。

    Returns:
        dict: {"ingested": int, "files": [str, ...], "skipped": [str, ...]}
    """
    cfg = load_config()
    SUFFIXES = {".txt", ".md", ".markdown", ".pdf"}
    files: list[Path] = []
    skipped: list[str] = []
    for raw in paths:
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.suffix.lower() in SUFFIXES and f.is_file():
                    files.append(f)
        elif p.is_file() and p.suffix.lower() in SUFFIXES:
            files.append(p)
        else:
            skipped.append(str(p))
    if not files:
        raise RuntimeError(f"没有可摄取的文件（支持 .txt / .md / .pdf）。跳过: {skipped}")

    chunks = rag_ingest(files, cfg.knowledge)
    return {
        "ingested": chunks,
        "files": [str(f) for f in files],
        "skipped": skipped,
    }


def knowledge_list() -> dict:
    """列出知识库状态。

    Returns:
        dict: {"chunk_count": int, "sources": [str, ...], "db_path": str}
    """
    cfg = load_config()
    from .rag import get_or_create_collection
    col = get_or_create_collection(cfg.knowledge)
    return {
        "chunk_count": col.count(),
        "sources": rag_list(cfg.knowledge),
        "db_path": str(cfg.knowledge.db_path) or "默认 (~/.bili2note/knowledge)",
    }


def knowledge_clear() -> dict:
    """清空整个知识库。

    Returns:
        dict: {"cleared": int}
    """
    cfg = load_config()
    n = rag_clear(cfg.knowledge)
    return {"cleared": n}


def knowledge_query(text: str, top_k: int | None = None) -> list[dict]:
    """检索知识库中与 query 最相关的文档块。

    Args:
        text: 检索文本（可以是术语、问题、一段视频字幕等）。
        top_k: 返回数量上限，默认取 config 中的 knowledge.top_k（默认 5）。

    Returns:
        list[dict]: [{"text": str, "source": str, "distance": float}, ...]
        distance 越小越相关（cosine distance）。
    """
    cfg = load_config()
    if top_k is not None:
        cfg.knowledge.top_k = top_k
    return rag_query(text, cfg.knowledge)


# ============================================================================
# 配置快捷操作
# ============================================================================

def get_config() -> dict:
    """返回当前配置概览（敏感字段会被掩码）。

    Returns:
        dict，包含 llm / bili / asr / vision / knowledge 的配置摘要。
    """
    from .config import mask
    cfg = load_config()
    return {
        "llm": {
            "provider": cfg.llm.provider,
            "base_url": cfg.llm.base_url or "(默认)",
            "model": cfg.llm.model,
            "api_key": mask(cfg.llm.api_key),
        },
        "bili": {
            "cookie_present": bool(cfg.bili.cookie),
        },
        "asr": {
            "provider": cfg.asr.provider,
            "api_key": mask(cfg.asr.api_key) if cfg.asr.api_key else None,
            "local_model": cfg.asr.local_model,
        },
        "vision": {
            "provider": cfg.vision.provider,
            "api_key": mask(cfg.vision.api_key) if cfg.vision.api_key else None,
        },
        "knowledge": {
            "db_path": cfg.knowledge.db_path or "(默认)",
            "embedding": cfg.knowledge.embedding,
            "top_k": cfg.knowledge.top_k,
        },
    }
