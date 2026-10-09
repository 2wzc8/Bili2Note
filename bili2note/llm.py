"""LLM 讲义生成：调用 OpenAI 兼容接口，将字幕/转写文本提纯为 Markdown 讲义。

长视频转写文本超过单次上下文预算时，先分块逐段生成草稿（map），最后一次调用合并成稿（reduce）。
"""

from __future__ import annotations

from typing import Callable

from openai import OpenAI

from .bili import fmt_ts
from .config import LLMConfig

_STRUCT = """# <视频标题>
> 来源：BV号 · 时长 · 由 Bili2Note 生成

## TL;DR
3-5 句话概括视频核心内容。

## 知识大纲
嵌套列表覆盖全部主要知识点，每项末尾附起始时间戳。

## 详细讲义
按主题分节展开；定义、原理、步骤、示例分层呈现；关键术语加粗；示例代码用代码块。

## 关键代码与命令
提取视频中出现的代码或命令，注明用途与时间戳。

## 术语表
| 术语 | 解释 |（覆盖视频中的关键技术术语）

## 易错点与复习提示
列出讲师强调的易错处、口误修正与常见误解。"""

SYSTEM_PROMPT = """你是一位严谨的技术教科书作者，负责把视频字幕提纯为高质量中文讲义。

输入是带时间戳的 B 站视频字幕，内容口语化，可能包含口误、重复、寒暄和水话。

写作规则：
1. 忠实于视频内容，不虚构、不臆造视频未提及的知识；无把握的内容宁可不写。
2. 剔除寒暄、口头禅、重复与广告，口语转书面语，合并冗余表达。
3. 发现明显口误（如术语说反）按上下文修正，并在该处用引用块标注：`> [口误修正] 原文说"……"，应为"……"`。
4. 保留时间戳锚点：每个小节标题后附起始时间 `[HH:MM:SS]`，用于回溯原视频。
5. 代码、命令、公式必须保持原样，分别用代码块或 LaTeX 包裹，不得改写。
6. 直接输出 Markdown 正文，不要任何额外解释或开场白。

输出结构（无对应内容的章节省略）：

""" + _STRUCT

MERGE_PROMPT = """你是技术教科书主编。下面是对同一视频分段时间轴依次生成的多份讲义草稿，相邻草稿间可能有少量重复。
请把它们合并为一份完整的最终讲义：
1. 严格按以下结构输出（无对应内容的章节省略），直接输出 Markdown，不要解释：

""" + _STRUCT + """

2. 合并去重：重复内容取讲得最清楚的一份，跨段信息可以整合；不要简单罗列拼接。
3. 保留草稿中的时间戳锚点与代码块、公式原样。
4. 草稿中的 [口误修正] 标注汇总到"易错点与复习提示"。"""

# 超过该字符数（约 1.5 万 token）分块；相邻块重叠约 1200 字防止语义截断
CHUNK_BUDGET = 30000
CHUNK_OVERLAP = 1200


def _split_chunks(lines: list[dict], budget: int = CHUNK_BUDGET, overlap: int = CHUNK_OVERLAP) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    size = 0
    for line in lines:
        n = len(line["content"]) + 14
        if cur and size + n > budget:
            chunks.append(cur)
            ov: list[dict] = []
            osz = 0
            for prev in reversed(cur):
                ov.insert(0, prev)
                osz += len(prev["content"]) + 14
                if osz >= overlap:
                    break
            cur, size = list(ov), osz
        cur.append(line)
        size += n
    if cur:
        chunks.append(cur)
    return chunks


def _transcript_text(lines: list[dict]) -> str:
    from .bili import fmt_ts
    return "\n".join(f"[{fmt_ts(l['from'])}] {l['content']}" for l in lines)


def _call_llm(cfg: LLMConfig, system: str, user_msg: str,
              on_delta: Callable[[str], None] | None = None) -> str:
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=600, max_retries=2)
    extra = {"max_tokens": cfg.max_tokens} if cfg.max_tokens else {}
    stream = client.chat.completions.create(
        model=cfg.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
        stream=True,
        **extra,
    )
    chunks: list[str] = []
    for event in stream:
        delta = event.choices[0].delta.content if event.choices else None
        if delta:
            chunks.append(delta)
            if on_delta:
                on_delta(delta)
    text = "".join(chunks).strip()
    if not text:
        raise RuntimeError("LLM 返回了空内容，请检查模型名称与接口配置")
    return text


def _video_header(info: dict, page: int) -> str:
    pages = info.get("pages", [])
    part_title = pages[page - 1].get("part", "") if 1 <= page <= len(pages) else ""
    return (
        f"视频标题：{info.get('title', '')}\n"
        f"UP主：{info.get('owner', {}).get('name', '')}\n"
        f"BV号：{info.get('bvid', '')}（第 {page} P：{part_title}）\n"
        f"视频时长：{info.get('duration', 0)} 秒\n"
    )


def generate_note(
    info: dict,
    lines: list[dict],
    cfg: LLMConfig,
    page: int = 1,
    on_delta: Callable[[str], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> str:
    """生成讲义并返回完整 Markdown。on_delta 用于流式回显，progress(i, n) 汇报分块进度。"""
    if not cfg.api_key:
        raise RuntimeError(
            "未配置 LLM API Key。请运行：bili2note config llm.api_key sk-xxx"
            "（或设置环境变量 BILI2NOTE_API_KEY）"
        )

    chunks = _split_chunks(lines)
    header = _video_header(info, page)

    if len(chunks) == 1:
        user_msg = header + f"\n以下是带时间戳的字幕全文，请生成讲义：\n\n{_transcript_text(chunks[0])}"
        return _call_llm(cfg, SYSTEM_PROMPT, user_msg, on_delta)

    # map：逐块生成草稿
    n = len(chunks)
    notes: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        if progress:
            progress(i, n)
        first, last = chunk[0]["from"], chunk[-1]["to"]
        user_msg = (
            header
            + f"本稿只处理时间范围 [{fmt_ts(first)} - {fmt_ts(last)}] 内的内容"
              f"（第 {i}/{n} 段，与相邻段有少量重叠）。\n"
            + f"\n以下是该段的带时间戳字幕，请生成讲义草稿：\n\n{_transcript_text(chunk)}"
        )
        notes.append(_call_llm(cfg, SYSTEM_PROMPT, user_msg))

    # reduce：合并成稿
    if progress:
        progress(0, n)
    drafts = "\n\n".join(f"<draft>\n{note}\n</draft>" for note in notes)
    user_msg = header + f"\n以下是 {n} 份讲义草稿，请合并为最终讲义：\n\n{drafts}"
    return _call_llm(cfg, MERGE_PROMPT, user_msg, on_delta)
