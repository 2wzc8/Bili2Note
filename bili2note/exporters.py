"""多格式导出：Markdown → GitBook 目录结构 / LaTeX 学术排版。

这些导出函数把 bili2note 生成的 Markdown 讲义变成可以直接发布或排版的格式。
"""

from __future__ import annotations

import re
from pathlib import Path


# ============================================================================
# GitBook 导出
# ============================================================================

def export_gitbook(markdown_dir: str | Path, output_dir: str | Path) -> dict:
    """将一个目录下的所有 Markdown 讲义组织成 GitBook 的目录结构。

    GitBook 期望：output_dir/
      SUMMARY.md   # 目录
      chapter1.md
      chapter2.md
      ...

    Args:
        markdown_dir: 存放 .md 讲义的目录（会递归收集）。
        output_dir: GitBook 输出目录（自动创建 SUMMARY.md）。

    Returns:
        dict: {"chapters": int, "summary": str}
    """
    md_dir = Path(markdown_dir)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not md_dir.is_dir():
        raise FileNotFoundError(f"Markdown 目录不存在：{md_dir}")

    md_files = sorted(md_dir.rglob("*.md"))
    if not md_files:
        raise RuntimeError(f"{md_dir} 下没有找到 .md 文件")

    # 提取每个文件的 H1 标题作为章节名
    chapters: list[tuple[str, str]] = []
    for md in md_files:
        text = md.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else md.stem
        chapters.append((title, md.relative_to(md_dir).as_posix()))

    # 生成 SUMMARY.md
    lines = ["# Summary", ""]
    for title, rel in chapters:
        lines.append(f"- [{title}]({rel})")
    summary_text = "\n".join(lines) + "\n"
    (out_dir / "SUMMARY.md").write_text(summary_text, encoding="utf-8")

    # 复制所有 markdown 文件（保持相对路径）
    for md in md_files:
        dest = out_dir / md.relative_to(md_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md.read_text(encoding="utf-8"), encoding="utf-8")

    return {"chapters": len(chapters), "summary": summary_text}


# ============================================================================
# LaTeX 导出
# ============================================================================

_LATEX_PREAMBLE = r"""\documentclass[11pt,a4paper]{ctexart}
\usepackage{amsmath,amssymb,amsthm}
\usepackage{graphicx}
\usepackage{listings}
\usepackage{xcolor}
\usepackage{hyperref}
\usepackage{geometry}
\geometry{margin=2.5cm}
\lstset{
  basicstyle=\ttfamily\small,
  keywordstyle=\color{blue},
  commentstyle=\color{green!60},
  stringstyle=\color{red},
  frame=single,
  breaklines=true,
  numbers=left,
  numberstyle=\tiny\color{gray},
  numbersep=8pt,
}
\hypersetup{colorlinks=true,linkcolor=blue,urlcolor=blue}
\title{%(title)s}
\author{Bili2Note 自动生成}
\date{}

\begin{document}
\maketitle
"""

_LATEX_POSTAMBLE = "\n\\end{document}\n"


def _escape_latex(text: str) -> str:
    """转义 LaTeX 特殊字符（保护代码块和行内代码除外）。"""
    replacements = {
        "\\": "\\textbackslash{}",
        "&": "\\&",
        "%": "\\%",
        "#": "\\#",
        "_": "\\_",
        "{": "\\{",
        "}": "\\}",
        "~": "\\textasciitilde{}",
        "^": "\\textasciicircum{}",
    }
    out = text
    for old, new in replacements.items():
        out = out.replace(old, new)
    return out


def _md_to_latex(md: str) -> str:
    """简易 Markdown → LaTeX 转换（足够覆盖 bili2note 输出）。"""
    lines = md.split("\n")
    out: list[str] = []
    in_code = False
    code_lang = ""

    for line in lines:
        stripped = line.strip()

        # 代码块
        if stripped.startswith("```"):
            if not in_code:
                in_code = True
                code_lang = stripped[3:].strip() or "text"
                out.append(f"\\begin{{lstlisting}}[language={code_lang}]")
            else:
                in_code = False
                out.append("\\end{lstlisting}")
                code_lang = ""
            continue

        if in_code:
            # 代码块内容原样输出（listings 自己处理转义）
            out.append(line)
            continue

        # 标题
        h_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if h_match:
            level = len(h_match.group(1))
            title = _escape_latex(h_match.group(2).strip())
            cmd = {1: "\\section", 2: "\\subsection", 3: "\\subsubsection",
                   4: "\\paragraph", 5: "\\subparagraph"}.get(level, "\\paragraph")
            out.append(f"{cmd}{{{title}}}")
            continue

        # 引用块（口误修正 / 知识校对）
        if stripped.startswith("> ["):
            content = _escape_latex(stripped[2:].strip())
            out.append(f"\\begin{{quote}}\n\\textbf{{{content[:20]}}}...\n\\end{{quote}}")
            continue
        if stripped.startswith(">"):
            content = _escape_latex(stripped[1:].strip())
            out.append(f"\\begin{{quote}}\n{content}\n\\end{{quote}}")
            continue

        # 行内代码 `xxx`
        line = re.sub(r"`([^`]+)`", lambda m: f"\\texttt{{{_escape_latex(m.group(1))}}}", line)

        # 加粗 **xxx**
        line = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"\\textbf{{{_escape_latex(m.group(1))}}}", line)

        # 斜体 *xxx*
        line = re.sub(r"\*([^*]+)\*", lambda m: f"\\emph{{{_escape_latex(m.group(1))}}}", line)

        # 链接 [text](url)
        line = re.sub(
            r"\[([^\]]+)\]\(([^)]+)\)",
            lambda m: f"\\href{{{m.group(2)}}}{{{_escape_latex(m.group(1))}}}",
            line,
        )

        # 普通文本：剩余特殊字符转义
        if line.strip():
            line = _escape_latex(line)

        out.append(line)

    return "\n".join(out)


def export_latex(markdown: str, output_path: str | Path) -> Path:
    """将一份 Markdown 讲义导出为 .tex 文件。

    Args:
        markdown: Markdown 内容字符串。
        output_path: 输出 .tex 文件路径。

    Returns:
        Path: 生成的 .tex 文件路径。
    """
    title_match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "讲义"

    body = _md_to_latex(markdown)
    tex = (_LATEX_PREAMBLE % {"title": _escape_latex(title)}) + body + _LATEX_POSTAMBLE

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(tex, encoding="utf-8")
    return out


def export_latex_file(markdown_path: str | Path, output_path: str | Path | None = None) -> Path:
    """从 .md 文件生成 .tex。

    Args:
        markdown_path: 输入 .md 文件。
        output_path: 输出 .tex 路径，默认与输入同目录同文件名。

    Returns:
        Path: 生成的 .tex 文件路径。
    """
    md_path = Path(markdown_path)
    text = md_path.read_text(encoding="utf-8")
    out = Path(output_path) if output_path else md_path.with_suffix(".tex")
    return export_latex(text, out)
