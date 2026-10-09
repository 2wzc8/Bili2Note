"""语音识别：云端走 OpenAI 兼容 /audio/transcriptions，本地走 faster-whisper（optional 依赖）。

统一输出格式与字幕一致：[{from, to, content}]，秒为单位的相对时间戳（已含分片偏移）。
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import ASRConfig


def _strip_tags(text: str) -> str:
    # SenseVoice 输出可能带 <|zh|><|NEUTRAL|> 之类的标签
    return re.sub(r"<\|[^|>]*\|>", "", text).strip()


def _spread_text(text: str, start: float, duration: float) -> list[dict]:
    """云端接口不返回词级时间戳时，按句切分并在时间窗内按长度均匀铺开。"""
    sents = [s.strip() for s in re.split(r"(?<=[。！？!?；;\n])", text) if s.strip()]
    if not sents:
        return []
    total = sum(len(s) for s in sents)
    lines, pos = [], start
    for s in sents:
        d = max(duration * len(s) / total, 0.5)
        lines.append({"from": round(pos, 2), "to": round(pos + d, 2), "content": s})
        pos += d
    return lines


def transcribe_cloud(wav: Path, cfg: ASRConfig, offset: float = 0.0, duration: float = 0.0) -> list[dict]:
    """调用 OpenAI 兼容转写接口。优先取 verbose_json 的分段时间戳，失败退回纯文本铺时间。"""
    if not cfg.api_key:
        raise RuntimeError(
            "云端 ASR 未配置 API Key。请运行：bili2note config asr.api_key sk-xxx"
            "（或设置环境变量 BILI2NOTE_ASR_API_KEY）"
        )
    from openai import OpenAI

    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=300, max_retries=2)
    kwargs: dict = {"language": cfg.language} if cfg.language else {}
    with wav.open("rb") as f:
        try:
            r = client.audio.transcriptions.create(
                model=cfg.model, file=f, response_format="verbose_json", **kwargs)
            segs = getattr(r, "segments", None) or []
            lines = [{"from": offset + s.start, "to": offset + s.end, "content": _strip_tags(s.text or "")}
                     for s in segs if (s.text or "").strip()]
            if lines:
                return lines
            text = _strip_tags(getattr(r, "text", "") or "")
        except Exception:
            f.seek(0)
            r = client.audio.transcriptions.create(
                model=cfg.model, file=f, response_format="json", **kwargs)
            text = _strip_tags(getattr(r, "text", "") or "")
    if not text:
        return []
    return _spread_text(text, offset, duration)


def transcribe_local(wav: Path, cfg: ASRConfig) -> list[dict]:
    """faster-whisper 本地转写（首次运行会自动下载模型，国内建议设 HF_ENDPOINT=https://hf-mirror.com）。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "本地 ASR 需要 faster-whisper。请运行：uv sync --extra local"
            "（国内下载模型建议先设置环境变量 HF_ENDPOINT=https://hf-mirror.com）"
        ) from e

    model = WhisperModel(cfg.local_model, device="auto", compute_type="auto")
    kwargs: dict = {}
    if cfg.language:
        kwargs["language"] = cfg.language

    def _run(vad: bool) -> list[dict]:
        segs, _info = model.transcribe(str(wav), vad_filter=vad, **kwargs)
        out = []
        for s in segs:
            content = s.text.strip()
            if content:
                out.append({"from": round(s.start, 2), "to": round(s.end, 2), "content": content})
        return out

    lines = _run(True)
    # VAD 对 BGM 声大的视频会漏检人声：覆盖不足 50% 时关闭 VAD 重转，取覆盖更大的一版
    audio_len = _wav_duration(wav)
    covered = sum(l["to"] - l["from"] for l in lines)
    if audio_len > 0 and covered < audio_len * 0.5:
        alt = _run(False)
        if sum(l["to"] - l["from"] for l in alt) > covered:
            lines = alt
    return lines


def _wav_duration(wav: Path) -> float:
    import wave
    try:
        with wave.open(str(wav), "rb") as f:
            return f.getnframes() / f.getframerate()
    except Exception:
        return 0.0
