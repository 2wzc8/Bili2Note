"""视觉链路：Dash 视频流下载 → ffmpeg 关键帧抽取 → VLM/OCR 画面信息提取。

画面信息按时间戳组织后注入 LLM Prompt，补齐纯音频/字幕遗漏的视觉内容（板书、PPT、代码演示等）。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from .audio import _run_ffmpeg
from .bili import UA, fmt_ts


@dataclass
class FrameInfo:
    """一帧关键画面的信息。"""
    timestamp: float
    image_path: Path
    text: str = ""
    description: str = ""


def download_video(url: str, dest_dir: Path) -> Path:
    """流式下载 m4s 视频流到临时目录。"""
    dest = dest_dir / "video.m4s"
    with httpx.Client(headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"}, timeout=300,
                      follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"视频流下载失败 HTTP {resp.status_code}")
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes(1024 * 256):
                    f.write(chunk)
    return dest


def extract_keyframes(video_path: Path, dest_dir: Path, duration: float,
                      interval: float = 5.0, max_frames: int = 50) -> list[tuple[Path, float]]:
    """固定间隔抽取关键帧，返回 [(图片路径, 时间戳秒)]。

    自动加大间隔以限制总帧数（长视频不会抽太多帧），JPEG 质量 q=3 够 OCR/VLM 用。
    """
    frames_dir = dest_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    if duration > 0:
        expected = duration / interval
        if expected > max_frames:
            interval = duration / max_frames

    pattern = str(frames_dir / "frame_%04d.jpg")
    _run_ffmpeg([
        "-i", str(video_path),
        "-vf", f"fps=1/{interval:.1f}",
        "-q:v", "3",
        pattern,
    ])

    frames: list[tuple[Path, float]] = []
    for i, p in enumerate(sorted(frames_dir.glob("frame_*.jpg")), 1):
        ts = (i - 1) * interval
        frames.append((p, ts))
    return frames


def _encode_image(path: Path, max_bytes: int = 400_000) -> str:
    """将图片 base64 编码为 data URI；过大则用 ffmpeg 压缩（scale 到 720p + 降质量）。"""
    data = path.read_bytes()
    if len(data) > max_bytes:
        compressed = path.parent / (path.stem + "_sm.jpg")
        _run_ffmpeg(["-i", str(path), "-q:v", "5", "-vf", "scale=-1:720", str(compressed)])
        data = compressed.read_bytes()
    return f"data:image/jpeg;base64,{base64.b64encode(data).decode()}"


_VLM_PROMPT = """请分析这张视频截图，提取以下信息：
1. 画面中的所有文字内容（板书、PPT 标题/列表、代码编辑器内容、公式等），保持原样，不要改写
2. 画面的简要描述（如"PPT 展示进程状态转换图"、"讲师在白板手写推导"、"代码编辑器显示 C++ 代码"等）

严格按以下格式输出，不要任何额外解释：
文字：<提取到的文字，没有则写"无">
描述：<画面简要描述>"""


def analyze_frames_vlm(
    frames: list[tuple[Path, float]],
    cfg,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[FrameInfo]:
    """用 VLM（OpenAI 兼容接口）分析每帧画面，提取文字 + 描述。"""
    from openai import OpenAI

    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=120, max_retries=1)
    results: list[FrameInfo] = []
    n = len(frames)
    for i, (path, ts) in enumerate(frames, 1):
        if on_progress:
            on_progress(i, n)
        fi = FrameInfo(timestamp=ts, image_path=path)
        try:
            img_uri = _encode_image(path)
            resp = client.chat.completions.create(
                model=cfg.model,
                messages=[
                    {"role": "user", "content": [
                        {"type": "text", "text": _VLM_PROMPT},
                        {"type": "image_url", "image_url": {"url": img_uri}},
                    ]},
                ],
                temperature=0.1,
                max_tokens=800,
            )
            answer = resp.choices[0].message.content or ""
            for line in answer.splitlines():
                if line.startswith("文字："):
                    fi.text = line[3:].strip()
                elif line.startswith("描述："):
                    fi.description = line[3:].strip()
        except Exception as e:
            fi.description = f"[VLM 分析失败：{e}]"
        results.append(fi)
    return results


def analyze_frames_ocr(
    frames: list[tuple[Path, float]],
    on_progress: Callable[[int, int], None] | None = None,
) -> list[FrameInfo]:
    """用本地 PaddleOCR 识别每帧文字（不含画面描述）。"""
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        raise RuntimeError(
            "本地 OCR 未安装。请运行：uv sync --extra ocr（安装 PaddleOCR）"
        )

    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    results: list[FrameInfo] = []
    n = len(frames)
    for i, (path, ts) in enumerate(frames, 1):
        if on_progress:
            on_progress(i, n)
        fi = FrameInfo(timestamp=ts, image_path=path)
        try:
            result = ocr.ocr(str(path), cls=True)
            texts = []
            for line in (result[0] or []):
                texts.append(line[1][0])
            fi.text = "\n".join(texts)
        except Exception as e:
            fi.description = f"[OCR 失败：{e}]"
        results.append(fi)
    return results


def visual_context_text(frames: list[FrameInfo]) -> str:
    """将画面信息整理为带时间戳的文本，用于注入 LLM Prompt。跳过空帧。"""
    parts: list[str] = []
    for f in frames:
        if not f.text and not f.description:
            continue
        if f.text == "无" and not f.description:
            continue
        ts = fmt_ts(f.timestamp)
        seg = f"[{ts}]"
        if f.text and f.text != "无":
            seg += f" 画面文字：{f.text}"
        if f.description and not f.description.startswith("["):
            seg += f" 画面：{f.description}"
        if seg != f"[{ts}]":
            parts.append(seg)
    return "\n".join(parts)
