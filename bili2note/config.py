"""配置管理：环境变量 > 配置文件（~/.bili2note/config.toml）。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
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
class Config:
    llm: LLMConfig
    bili: BiliConfig


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
    return Config(llm=llm, bili=bili)


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
