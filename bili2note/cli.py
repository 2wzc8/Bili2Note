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
from .config import ASR_PROVIDERS, CONFIG_PATH, PROVIDERS, Config, load_config, mask, save_config
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


def cmd_note(args: argparse.Namespace) -> None:
    cfg: Config = load_config()
    bvid, page = parse_video_id(args.video)
    page = args.page
    client = BiliClient(cfg.bili.cookie)

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

    text = generate_note(info, lines, cfg.llm, page=page, on_delta=sys.stdout.write,
                         progress=lambda i, n: print(f"生成讲义：分块 {i}/{n}" if i else "合并分块草稿..."))
    print("\n")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
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
