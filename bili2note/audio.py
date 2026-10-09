"""音频链路：Dash 音频下载 → ffmpeg 转码分片（云端 ASR 有 25MB 限制，按时间窗切片）。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx

from .bili import UA


def ffmpeg_exe() -> str:
    """优先系统 PATH 的 ffmpeg，否则用 imageio-ffmpeg 自带的静态二进制。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as e:
        raise RuntimeError(
            "找不到 ffmpeg。请安装 ffmpeg 并加入 PATH，或运行：uv sync（imageio-ffmpeg 会自动提供）"
        ) from e


def download_audio(url: str, dest_dir: Path) -> Path:
    """流式下载 m4s 音频到临时目录。"""
    dest = dest_dir / "audio.m4s"
    with httpx.Client(headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"}, timeout=120,
                      follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"音频下载失败 HTTP {resp.status_code}")
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes(1024 * 256):
                    f.write(chunk)
    return dest


def _run_ffmpeg(args: list[str]) -> None:
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败：{proc.stderr.strip()[:300]}")


def full_wav(m4s: Path, dest_dir: Path) -> Path:
    """整段转 16k 单声道 wav（本地 ASR 用，faster-whisper 自行断句）。"""
    wav = dest_dir / "full.wav"
    _run_ffmpeg(["-i", str(m4s), "-ar", "16000", "-ac", "1", str(wav)])
    return wav


def chunk_wavs(m4s: Path, duration: float, dest_dir: Path, chunk_sec: float = 600.0) -> list[tuple[Path, float, float]]:
    """按时间窗切片转 wav，返回 [(wav路径, 起始秒, 时长秒)]。16k mono wav 约 1.9MB/分钟，10 分钟 < 25MB。"""
    results: list[tuple[Path, float, float]] = []
    start = 0.0
    i = 0
    while start < max(duration, 0.1):
        length = min(chunk_sec, duration - start)
        wav = dest_dir / f"chunk{i:03d}.wav"
        _run_ffmpeg(["-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(m4s),
                     "-ar", "16000", "-ac", "1", str(wav)])
        results.append((wav, start, length))
        start += chunk_sec
        i += 1
    return results
