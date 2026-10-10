"""命令行入口：bili2note note / fetch / config。"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import __version__
from .bili import BiliClient, BiliError, fmt_ts, parse_video_id
from .config import ASR_PROVIDERS, CONFIG_PATH, PROVIDERS, VLM_PROVIDERS, Config, load_config, mask, save_config
from .llm import generate_note


def _safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "untitled"


def _choose_subtitle(subs: list, index: int | None):
    if index is not None:
        if index < 1 or index > len(subs):
            raise SystemExit(f"--subtitle 编号 {index} 超出范围（1-{len(subs)}）")
        return subs[index - 1]
    # 自动选择：中文人工 CC > 中文 AI > 第一个
    for sub in subs:
        if sub.lan.startswith("zh") and not sub.ai:
            return sub
    for sub in subs:
        if sub.lan.startswith("zh"):
            return sub
    return subs[0]


def _resolve_asr_mode(cfg: Config, mode: str) -> str:
    """auto = 云端（配了 key）优先，否则本地（装了 faster-whisper）。"""
    if mode == "auto":
        if cfg.asr.api_key:
            return "cloud"
        try:
            import faster_whisper  # noqa: F401
            return "local"
        except ImportError:
            raise SystemExit(
                "该视频没有可用字幕，且 ASR 不可用。两种方案任选：\n"
                "  云端：bili2note config asr.api_key <你的Key>（默认 siliconflow SenseVoice）\n"
                "  本地：uv sync --extra local（faster-whisper，首次运行自动下载模型）"
            )
    if mode == "cloud" and not cfg.asr.api_key:
        raise SystemExit("云端 ASR 未配置 API Key：bili2note config asr.api_key sk-xxx")
    return mode


def _transcript_via_asr(client: BiliClient, cfg: Config, bvid: str, page: int, mode: str) -> list[dict]:
    """下载低码率音频 → ffmpeg 转码分片 → ASR 转写为带时间戳文本。"""
    from .asr import transcribe_cloud, transcribe_local
    from .audio import chunk_wavs, download_audio, full_wav

    url, duration = client.audio_stream(bvid, page)
    print(f"无可用字幕，走 ASR（{mode}）转写，时长 {fmt_ts(duration)}")
    tmp = Path(tempfile.mkdtemp(prefix="bili2note-"))
    try:
        print("下载音频流（最低码率）...")
        m4s = download_audio(url, tmp)
        if mode == "local":
            print(f"转码整段音频（16k mono），本地模型 {cfg.asr.local_model} 转写中（首次运行需下载模型）...")
            return transcribe_local(full_wav(m4s, tmp), cfg.asr)
        chunks = chunk_wavs(m4s, duration, tmp)
        print(f"转码为 {len(chunks)} 个分片，逐片云端转写...")
        lines: list[dict] = []
        for wav, start, length in chunks:
            print(f"  [{fmt_ts(start)} - {fmt_ts(start + length)}] 转写中...")
            lines.extend(transcribe_cloud(wav, cfg.asr, offset=start, duration=length))
        return lines
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _resolve_vision_mode(cfg: Config, mode: str) -> str:
    """auto = VLM（配了 key）优先，否则 OCR（装了 paddleocr），否则 none。"""
    if mode == "auto":
        if cfg.vision.api_key:
            return "vlm"
        try:
            import paddleocr  # noqa: F401
            return "ocr"
        except ImportError:
            return "none"
    if mode == "vlm" and not cfg.vision.api_key:
        raise SystemExit("VLM 未配置 API Key：bili2note config vision.api_key sk-xxx")
    return mode


def _collect_visual_info(client: BiliClient, cfg: Config, bvid: str, page: int,
                          info: dict, mode: str) -> str:
    """下载视频流 → ffmpeg 抽帧 → VLM/OCR 分析，返回画面信息文本。"""
    from .vision import (analyze_frames_ocr, analyze_frames_vlm,
                         download_video, extract_keyframes, visual_context_text)

    url, duration = client.video_stream(bvid, page, info=info)
    print(f"视觉链路：获取视频流（最低码率），时长 {fmt_ts(duration)}")
    tmp = Path(tempfile.mkdtemp(prefix="bili2note-vision-"))
    try:
        print("下载视频流...")
        video = download_video(url, tmp)
        interval = cfg.vision.keyframe_interval
        max_frames = cfg.vision.max_frames
        frames = extract_keyframes(video, tmp, duration, interval=interval, max_frames=max_frames)
        print(f"抽取 {len(frames)} 个关键帧（间隔 {interval:.0f}s）")
        if not frames:
            return ""
        if mode == "vlm":
            print(f"VLM 分析画面（模型 {cfg.vision.model or cfg.vision.provider}）...")
            frame_infos = analyze_frames_vlm(frames, cfg.vision,
                on_progress=lambda i, n: print(f"  画面 {i}/{n}"))
        else:
            print("本地 OCR 识别画面文字...")
            frame_infos = analyze_frames_ocr(frames,
                on_progress=lambda i, n: print(f"  画面 {i}/{n}"))
        return visual_context_text(frame_infos)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _get_transcript(client: BiliClient, bvid: str, page: int, sub_index: int | None):
    info = client.video_info(bvid)
    subs = client.subtitles(bvid, page, info=info)
    if not subs:
        raise SystemExit(
            "该视频没有可用字幕。注意：未登录时 B 站不返回字幕列表（含人工CC），"
            "请先配置登录 Cookie：bili2note config bili.cookie \"SESSDATA=xxx; buvid3=xxx\""
        )
    print("可用字幕：")
    for i, sub in enumerate(subs, 1):
        print(f"  {i}. {sub}")
    sub = _choose_subtitle(subs, sub_index)
    print(f"使用字幕：{sub}")
    return info, client.subtitle_content(sub)


def _course_context(outdir: Path, exclude_prefix: str, per_file: int = 3000, total: int = 15000) -> str:
    """收集同课程文件夹内已有讲义节选（排除当前视频自己的旧文件），作为生成参考上下文。"""
    if not outdir.exists():
        return ""
    parts: list[str] = []
    size = 0
    for p in sorted(outdir.glob("*.md")):
        if p.name.startswith(exclude_prefix):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.strip():
            continue
        excerpt = text[:per_file]
        parts.append(f"《{p.stem}》节选：\n{excerpt}")
        size += len(excerpt)
        if size >= total:
            break
    return "\n\n".join(parts)


def cmd_note(args: argparse.Namespace) -> None:
    cfg: Config = load_config()
    bvid, page = parse_video_id(args.video)
    page = args.page
    client = BiliClient(cfg.bili.cookie)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    context = _course_context(outdir, f"{bvid}-")
    if context:
        print(f"检测到同课程已有讲义 {context.count('《')} 份，将参考其术语与风格")

    print(f"获取视频信息：{bvid} P{page}")
    info = client.video_info(bvid)
    subs = client.subtitles(bvid, page, info=info)

    if subs and args.asr not in ("cloud", "local"):
        print("可用字幕：")
        for i, sub in enumerate(subs, 1):
            print(f"  {i}. {sub}")
        sub = _choose_subtitle(subs, args.subtitle)
        print(f"使用字幕：{sub}")
        lines = client.subtitle_content(sub)
        source = "官方字幕"
    else:
        mode = _resolve_asr_mode(cfg, args.asr)
        if args.subtitle is not None:
            print("[提示] 该视频无可用字幕，忽略 --subtitle 参数")
        lines = _transcript_via_asr(client, cfg, bvid, page, mode)
        source = f"ASR 转写（{mode}）"

    if not lines:
        raise SystemExit("转写结果为空，无法生成讲义")
    print(f"文本共 {len(lines)} 句，时长 {fmt_ts(lines[-1]['to'])}，来源：{source}（模型：{cfg.llm.model}）\n")

    # 视觉链路：按需提取画面信息
    visual_ctx = ""
    if args.vision != "none":
        v_mode = _resolve_vision_mode(cfg, args.vision)
        if v_mode != "none":
            visual_ctx = _collect_visual_info(client, cfg, bvid, page, info, v_mode)
            if visual_ctx:
                print(f"提取到画面信息 {len(visual_ctx)} 字符\n")
            else:
                print("[提示] 未提取到画面信息\n")

    # RAG 知识检索
    kn_ctx = ""
    if args.knowledge != "none":
        from .rag import query, format_retrieval
        k_query = info.get("title", "") + "\n" + "\n".join(l["content"] for l in lines[:10])
        try:
            results = query(k_query, cfg.knowledge)
            kn_ctx = format_retrieval(results)
            if kn_ctx:
                print(f"知识库检索到 {len(results)} 条相关资料片段\n")
            else:
                print("[提示] 知识库为空或未检索到相关内容\n")
        except ImportError:
            print("[提示] chromadb 未安装，跳过知识库检索（uv add chromadb）\n")
        except Exception as e:
            print(f"[提示] 知识库检索失败：{e}\n")

    text = generate_note(info, lines, cfg.llm, page=page, on_delta=sys.stdout.write,
                         progress=lambda i, n: print(f"生成讲义：分块 {i}/{n}" if i else "合并分块草稿..."),
                         context=context, visual_context=visual_ctx or None,
                         knowledge_context=kn_ctx or None)
    print("\n")

    path = outdir / f"{bvid}-{_safe_filename(info.get('title', ''))}.md"
    path.write_text(text, encoding="utf-8")
    print(f"讲义已保存：{path.resolve()}")


def cmd_fetch(args: argparse.Namespace) -> None:
    cfg = load_config()
    bvid, page = parse_video_id(args.video)
    page = args.page
    client = BiliClient(cfg.bili.cookie)

    print(f"获取视频信息：{bvid} P{page}")
    info, lines = _get_transcript(client, bvid, page, args.subtitle)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{bvid}-P{page}-subtitle.txt"
    path.write_text(
        f"# {info.get('title', '')} P{page}\n\n"
        + "\n".join(f"[{fmt_ts(l['from'])}] {l['content']}" for l in lines),
        encoding="utf-8",
    )
    print(f"字幕已保存：{path.resolve()}（共 {len(lines)} 句）")


def cmd_knowledge_add(args: argparse.Namespace) -> None:
    from .rag import ingest

    cfg = load_config()
    SUFFIXES = {".txt", ".md", ".markdown", ".pdf"}
    files: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.suffix.lower() in SUFFIXES and f.is_file():
                    files.append(f)
        elif p.is_file() and p.suffix.lower() in SUFFIXES:
            files.append(p)
        else:
            print(f"[跳过] {raw}（不存在或格式不支持）")

    if not files:
        raise SystemExit("没有可摄取的文件")

    print(f"待摄取 {len(files)} 个文件...")
    try:
        chunks = ingest(files, cfg.knowledge,
                        on_progress=lambda i, n, p: print(f"  [{i}/{n}] {p}"))
        print(f"完成：共摄取 {chunks} 个文档块到知识库")
    except ImportError as e:
        raise SystemExit(f"缺少依赖：{e}。请运行 uv add chromadb pymupdf")


def cmd_knowledge_list(args: argparse.Namespace) -> None:
    from .rag import list_sources, get_or_create_collection

    cfg = load_config()
    try:
        col = get_or_create_collection(cfg.knowledge)
    except ImportError:
        raise SystemExit("缺少依赖：chromadb。请运行 uv add chromadb")

    count = col.count()
    print(f"知识库（{cfg.knowledge.db_path or '默认路径'}）：{count} 个文档块")
    sources = list_sources(cfg.knowledge)
    if not sources:
        print("  （空）")
        return
    for s in sources:
        print(f"  {s}")


def cmd_knowledge_clear(args: argparse.Namespace) -> None:
    from .rag import clear

    cfg = load_config()
    count = clear(cfg.knowledge)
    print(f"已清空知识库：删除 {count} 个文档块")


def cmd_config(args: argparse.Namespace) -> None:
    pairs = args.pairs
    if pairs:
        if len(pairs) % 2 != 0:
            raise SystemExit("配置格式：bili2note config <key> <value> [<key> <value> ...]")
        updates = dict(zip(pairs[::2], pairs[1::2]))
        save_config(updates)
        for k, v in updates.items():
            print(f"已写入 {k} = {mask(v) if ('key' in k or 'cookie' in k) else v}")
        print(f"配置文件：{CONFIG_PATH}")
    else:
        cfg = load_config()
        print(f"配置文件：{CONFIG_PATH}")
        print(f"llm.provider = {cfg.llm.provider or '(未设置，走通用 OpenAI 格式)'}")
        print(f"llm.api_key  = {mask(cfg.llm.api_key)}")
        print(f"llm.base_url = {cfg.llm.base_url}")
        print(f"llm.model    = {cfg.llm.model}")
        print(f"llm.max_tokens = {cfg.llm.max_tokens or '(默认)'}")
        if cfg.llm.provider and cfg.llm.provider not in PROVIDERS:
            known = " / ".join(PROVIDERS)
            print(f"[提示] 未知 provider，可选项：{known}")
        print(f"bili.cookie  = {mask(cfg.bili.cookie)}")
        print(f"asr.provider = {cfg.asr.provider or '(未设置)'}")
        print(f"asr.api_key  = {mask(cfg.asr.api_key)}")
        print(f"asr.base_url = {cfg.asr.base_url or '(provider 预设)'}")
        print(f"asr.model    = {cfg.asr.model or '(provider 预设)'}")
        print(f"asr.local_model = {cfg.asr.local_model}")
        if cfg.asr.provider and cfg.asr.provider not in ASR_PROVIDERS:
            print(f"[提示] 未知 asr provider，可选项：{' / '.join(ASR_PROVIDERS)}")
        print(f"vision.provider = {cfg.vision.provider or '(未设置)'}")
        print(f"vision.api_key  = {mask(cfg.vision.api_key)}")
        print(f"vision.model    = {cfg.vision.model or '(provider 预设)'}")
        if cfg.vision.provider and cfg.vision.provider not in VLM_PROVIDERS:
            print(f"[提示] 未知 vision provider，可选项：{' / '.join(VLM_PROVIDERS)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="bili2note",
        description="B 站视频 → AI 结构化讲义（官方字幕/音频 ASR → LLM 提纯 → Markdown）",
    )
    parser.add_argument("--version", action="version", version=f"bili2note {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_note = sub.add_parser("note", help="抓取字幕/ASR 转写并调用 LLM 生成 Markdown 讲义")
    p_note.add_argument("video", help="BV 号或视频 URL（可含 ?p=N 指定分P）")
    p_note.add_argument("-p", "--page", type=int, default=None, help="分P页码（默认取 URL 中的 p 或 1）")
    p_note.add_argument("-o", "--outdir", default="output", help="输出目录（默认 ./output）")
    p_note.add_argument("--subtitle", type=int, default=None, help="字幕编号（1-based，默认自动选中文）")
    p_note.add_argument("--asr", choices=["none", "auto", "cloud", "local"], default="auto",
                        help="语音识别模式：none=仅字幕 auto=有字幕用字幕否则 ASR（默认） "
                             "cloud=强制云端 local=强制本地 faster-whisper")
    p_note.add_argument("--vision", choices=["none", "auto", "vlm", "ocr"], default="none",
                        help="画面信息提取：none=关闭（默认） auto=有 VLM key 用 VLM 否则 OCR "
                             "vlm=强制云端视觉模型 ocr=强制本地 PaddleOCR（需 uv sync --extra ocr）")
    p_note.add_argument("--knowledge", choices=["none", "auto"], default="none",
                        help="知识库检索：none=关闭（默认） auto=检索本地知识库并注入参考资料")
    p_note.set_defaults(func=cmd_note)

    p_fetch = sub.add_parser("fetch", help="仅抓取字幕，保存为带时间戳的 txt")
    p_fetch.add_argument("video", help="BV 号或视频 URL")
    p_fetch.add_argument("-p", "--page", type=int, default=None, help="分P页码")
    p_fetch.add_argument("-o", "--outdir", default="output", help="输出目录（默认 ./output）")
    p_fetch.add_argument("--subtitle", type=int, default=None, help="字幕编号（1-based）")
    p_fetch.set_defaults(func=cmd_fetch)

    p_cfg = sub.add_parser("config", help="查看或设置配置（无参数为查看）")
    p_cfg.add_argument("pairs", nargs="*", help="键值对，如：llm.api_key sk-xxx bili.cookie 'SESSDATA=...'")
    p_cfg.set_defaults(func=cmd_config)

    p_kn = sub.add_parser("knowledge", help="本地知识库：摄取教材/文档用于 RAG 校对")
    kn_sub = p_kn.add_subparsers(dest="kn_cmd", required=True)
    kn_add = kn_sub.add_parser("add", help="摄取文件到知识库（支持 .txt / .md / .pdf）")
    kn_add.add_argument("paths", nargs="+", help="文件或目录路径（目录会递归摄取所有支持的文件）")
    kn_add.set_defaults(func=cmd_knowledge_add)
    kn_list = kn_sub.add_parser("list", help="列出知识库中的来源文件")
    kn_list.set_defaults(func=cmd_knowledge_list)
    kn_clear = kn_sub.add_parser("clear", help="清空知识库")
    kn_clear.set_defaults(func=cmd_knowledge_clear)

    args = parser.parse_args(argv)

    # 统一处理 -p 与 URL 中的 ?p=：URL 优先级低于显式 -p
    if getattr(args, "page", None) is None and args.cmd in ("note", "fetch"):
        _, page = parse_video_id(args.video)
        args.page = page
    if args.cmd in ("note", "fetch"):
        args.video = re.sub(r"[?&]p=\d+", "", args.video)  # 避免后续重复解析

    try:
        args.func(args)
    except BiliError as e:
        raise SystemExit(f"[B站接口错误] {e}")
    except ValueError as e:
        raise SystemExit(f"[参数错误] {e}")


if __name__ == "__main__":
    main()
