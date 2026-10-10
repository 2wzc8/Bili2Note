"""配置管理：环境变量 > 配置文件（~/.bili2note/config.toml）。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".bili2note"
CONFIG_PATH = CONFIG_DIR / "config.toml"

_ENV_PREFIX = "BILI2NOTE_"

# 常见 OpenAI 兼容接口预设：bili2note config llm.provider <名称> 即可切换
PROVIDERS: dict[str, dict] = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "max_tokens": 8192},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "moonshot": {"base_url": "https://api.moonshot.cn/v1", "model": "kimi-k2-0905-preview"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-plus"},
}

# 云端 ASR 预设（OpenAI 兼容 /audio/transcriptions）
ASR_PROVIDERS: dict[str, dict] = {
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1", "model": "FunAudioLLM/SenseVoiceSmall"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "whisper-1"},
}

# VLM（视觉语言模型）预设，用于画面信息提取（需支持 image_url 格式）
VLM_PROVIDERS: dict[str, dict] = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-vl-plus"},
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen2-VL-7B-Instruct"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4v-flash"},
}


@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    provider: str = ""
    max_tokens: int | None = None  # 不设置则由接口方决定


@dataclass
class BiliConfig:
    cookie: str = ""


@dataclass
class ASRConfig:
    provider: str = "siliconflow"
    api_key: str = ""
    base_url: str = ""  # 空 = 取 provider 预设
    model: str = ""     # 空 = 取 provider 预设
    local_model: str = "small"  # faster-whisper 模型：tiny/base/small/medium/large-v3
    language: str = ""          # 识别语言，空 = 自动检测（如 zh）


@dataclass
class VisionConfig:
    provider: str = "openai"
    api_key: str = ""
    base_url: str = ""  # 空 = 取 provider 预设
    model: str = ""     # 空 = 取 provider 预设
    keyframe_interval: float = 5.0  # 关键帧间隔（秒）
    max_frames: int = 50            # 最多抽取帧数


@dataclass
class KnowledgeConfig:
    db_path: str = ""           # 向量库路径，空 = ~/.bili2note/knowledge
    collection: str = "bili2note"
    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 5              # 每次检索返回的文档块数
    embedding: str = "default"   # default=chromadb 内置 ONNX MiniLM；llmapi=用 LLM API 的 embedding endpoint


@dataclass
class Config:
    llm: LLMConfig
    bili: BiliConfig
    asr: ASRConfig
    vision: VisionConfig = field(default_factory=VisionConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)


def _read_raw() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def load_config() -> Config:
    raw = _read_raw()
    llm_raw = raw.get("llm", {})
    bili_raw = raw.get("bili", {})

    provider = os.environ.get(_ENV_PREFIX + "PROVIDER", llm_raw.get("provider", "")).lower()
    preset = PROVIDERS.get(provider, {})

    llm = LLMConfig(
        api_key=os.environ.get(_ENV_PREFIX + "API_KEY", llm_raw.get("api_key", "")),
        base_url=os.environ.get(_ENV_PREFIX + "BASE_URL", llm_raw.get("base_url", preset.get("base_url", LLMConfig.base_url))),
        model=os.environ.get(_ENV_PREFIX + "MODEL", llm_raw.get("model", preset.get("model", LLMConfig.model))),
        provider=provider,
        max_tokens=int(os.environ.get(_ENV_PREFIX + "MAX_TOKENS", "") or llm_raw.get("max_tokens") or preset.get("max_tokens") or 0) or None,
    )
    bili = BiliConfig(cookie=os.environ.get(_ENV_PREFIX + "COOKIE", bili_raw.get("cookie", "")))

    asr_raw = raw.get("asr", {})
    a_provider = os.environ.get(_ENV_PREFIX + "ASR_PROVIDER", asr_raw.get("provider", "siliconflow")).lower()
    a_preset = ASR_PROVIDERS.get(a_provider, {})
    asr = ASRConfig(
        provider=a_provider,
        api_key=os.environ.get(_ENV_PREFIX + "ASR_API_KEY", asr_raw.get("api_key", "")),
        base_url=os.environ.get(_ENV_PREFIX + "ASR_BASE_URL", asr_raw.get("base_url", a_preset.get("base_url", ""))),
        model=os.environ.get(_ENV_PREFIX + "ASR_MODEL", asr_raw.get("model", a_preset.get("model", ""))),
        local_model=os.environ.get(_ENV_PREFIX + "ASR_LOCAL_MODEL", asr_raw.get("local_model", "small")),
        language=os.environ.get(_ENV_PREFIX + "ASR_LANGUAGE", asr_raw.get("language", "")),
    )
    return Config(llm=llm, bili=bili, asr=asr, vision=_load_vision(raw), knowledge=_load_knowledge(raw))


def _load_knowledge(raw: dict) -> KnowledgeConfig:
    k_raw = raw.get("knowledge", {})
    return KnowledgeConfig(
        db_path=os.environ.get(_ENV_PREFIX + "KNOWLEDGE_DB", k_raw.get("db_path", "")),
        collection=os.environ.get(_ENV_PREFIX + "KNOWLEDGE_COLLECTION", k_raw.get("collection", "bili2note")),
        chunk_size=int(os.environ.get(_ENV_PREFIX + "KNOWLEDGE_CHUNK_SIZE", "") or k_raw.get("chunk_size", 500)),
        chunk_overlap=int(os.environ.get(_ENV_PREFIX + "KNOWLEDGE_CHUNK_OVERLAP", "") or k_raw.get("chunk_overlap", 50)),
        top_k=int(os.environ.get(_ENV_PREFIX + "KNOWLEDGE_TOP_K", "") or k_raw.get("top_k", 5)),
        embedding=os.environ.get(_ENV_PREFIX + "KNOWLEDGE_EMBEDDING", k_raw.get("embedding", "default")),
    )


def _load_vision(raw: dict) -> VisionConfig:
    """读取 vision 配置段，优先环境变量。"""
    v_raw = raw.get("vision", {})
    v_provider = os.environ.get(_ENV_PREFIX + "VISION_PROVIDER", v_raw.get("provider", "openai")).lower()
    v_preset = VLM_PROVIDERS.get(v_provider, {})
    return VisionConfig(
        provider=v_provider,
        api_key=os.environ.get(_ENV_PREFIX + "VISION_API_KEY", v_raw.get("api_key", "")),
        base_url=os.environ.get(_ENV_PREFIX + "VISION_BASE_URL", v_raw.get("base_url", v_preset.get("base_url", ""))),
        model=os.environ.get(_ENV_PREFIX + "VISION_MODEL", v_raw.get("model", v_preset.get("model", ""))),
        keyframe_interval=float(os.environ.get(_ENV_PREFIX + "VISION_INTERVAL", "") or v_raw.get("keyframe_interval", 5.0)),
        max_frames=int(os.environ.get(_ENV_PREFIX + "VISION_MAX_FRAMES", "") or v_raw.get("max_frames", 50)),
    )


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def save_config(updates: dict[str, str]) -> None:
    """按点分路径写入配置，如 llm.api_key / bili.cookie。"""
    for dotted in updates:
        section, _, key = dotted.partition(".")
        if not section or not key:
            raise ValueError(f"无效的配置项：{dotted}（应为 形如 llm.api_key / bili.cookie）")

    data = _read_raw()
    for dotted, value in updates.items():
        section, key = dotted.split(".", 1)
        data.setdefault(section, {})[key] = str(value)

    lines: list[str] = []
    for section, kv in data.items():
        lines.append(f"[{section}]")
        lines.extend(f'{k} = "{_escape(str(v))}"' for k, v in kv.items())
        lines.append("")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")


def mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value) if value else "(未设置)"
    return value[:4] + "..." + value[-4:]
