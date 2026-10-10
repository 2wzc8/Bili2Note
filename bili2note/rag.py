"""RAG 知识库：本地向量检索 + 文档摄取 + 术语检索注入 LLM Prompt。

轻量级设计：chromadb 纯本地嵌入式向量库，默认使用 all-MiniLM-L6-v2（sentence-transformers），
中文用户可切换为 bge-small-zh-v1.5。支持 PDF / txt / md 格式摄取。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class RAGConfig:
    """RAG 知识库配置（备用，主要用 config.py 的 KnowledgeConfig）。"""
    db_path: str = ""            # 向量库存储路径，空 = ~/.bili2note/knowledge
    collection: str = "bili2note"
    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 5
    embedding: str = "default"   # default=ONNX MiniLM；llmapi=用 LLM API embedding


def _default_db_path() -> Path:
    from .config import CONFIG_DIR
    return CONFIG_DIR / "knowledge"


def _make_embedding_function(cfg):
    """根据配置创建 embedding function。cfg 可以是 RAGConfig 或 KnowledgeConfig。"""
    from chromadb.api.types import EmbeddingFunction, Documents, Embeddings
    import numpy as np

    mode = getattr(cfg, "embedding", "default")

    if mode == "llmapi":
        # 用 LLM API 的 embedding endpoint
        from .config import load_config
        main_cfg = load_config()
        llm = main_cfg.llm
        if not llm.api_key:
            raise RuntimeError("embedding=llmapi 需要先配置 llm.api_key")

        embedding_model = getattr(cfg, "embedding_model", None)
        if not embedding_model:
            # 自动探测：根据 llm.provider 选默认 embedding 模型
            provider = main_cfg.llm.provider.lower()
            EMBED_MAP = {
                "openai": "text-embedding-3-small",
                "deepseek": "deepseek-embedding-v1",
                "kimi": "moonshot-v1-8k",
                "zhipu": "embedding-2",
                "qwen": "text-embedding-v3",
            }
            embedding_model = EMBED_MAP.get(provider, "text-embedding-3-small")

        class LLMAPIEmbedding(EmbeddingFunction[Documents]):
            def __call__(self, input: Documents) -> Embeddings:
                import httpx
                client = httpx.Client(
                    base_url=llm.base_url.rstrip("/"),
                    headers={"Authorization": f"Bearer {llm.api_key}",
                             "Content-Type": "application/json"},
                    timeout=120,
                )
                resp = client.post("/embeddings",
                                   json={"model": embedding_model, "input": input},
                                   follow_redirects=True)
                client.close()
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Embedding API {resp.status_code}：{resp.text[:200]}。"
                        f"尝试设置 knowledge.embedding=default 用本地 ONNX 模型，"
                        f"或确认 embedding 模型名是否支持。"
                    )
                data = resp.json()
                embeds = sorted(data.get("data", []), key=lambda x: x["index"])
                return [np.array(e["embedding"], dtype=np.float32) for e in embeds]

        return LLMAPIEmbedding()

    # 默认：让 chromadb 用它的 DefaultEmbeddingFunction（ONNX MiniLM）
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    return DefaultEmbeddingFunction()


def get_or_create_collection(cfg):
    """获取或创建向量库 collection。cfg 可以是 RAGConfig 或 KnowledgeConfig。"""
    import chromadb
    from chromadb.config import Settings

    db_path = Path(cfg.db_path) if getattr(cfg, "db_path", "") else _default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(db_path),
        settings=Settings(anonymized_telemetry=False),
    )

    ef = _make_embedding_function(cfg)
    return client.get_or_create_collection(
        name=cfg.collection,
        metadata={"hnsw:space": "cosine"},
        embedding_function=ef,
    )


def _split_text(text: str, cfg) -> list[str]:
    """按字符数简单分块，保留重叠。"""
    chunk_size = getattr(cfg, "chunk_size", 500)
    chunk_overlap = getattr(cfg, "chunk_overlap", 50)
    if chunk_overlap >= chunk_size:
        chunk_overlap = chunk_size // 4
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = end - chunk_overlap
    return chunks


def _read_file(path: Path) -> str:
    """读取 txt / md / pdf 文件内容。"""
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".markdown"):
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        try:
            import fitz  # PyMuPDF
        except ImportError as e:
            raise RuntimeError(
                f"PDF 读取需要 PyMuPDF：pip install pymupdf（或 uv add pymupdf）"
            ) from e
        doc = fitz.open(str(path))
        pages = []
        for page in doc:
            pages.append(page.get_text())
        return "\n".join(pages)
    raise RuntimeError(f"不支持的文件格式：{suffix}（支持 .txt / .md / .pdf）")


def ingest(paths: list[Path], cfg,
           on_progress: Callable[[int, int, str], None] | None = None) -> int:
    """摄取文件到向量库，返回摄取的块数。"""
    col = get_or_create_collection(cfg)
    total_chunks = 0
    n = len(paths)
    for i, p in enumerate(paths, 1):
        if on_progress:
            on_progress(i, n, str(p))
        text = _read_file(p)
        chunks = _split_text(text, cfg)
        if not chunks:
            continue
        c_name = getattr(cfg, "collection", "bili2note")
        _ = c_name  # collection 已在 get_or_create_collection 中使用
        ids = [f"{p.stem}-{j}" for j in range(len(chunks))]
        metas = [{"source": str(p), "chunk": j} for j in range(len(chunks))]
        col.add(documents=chunks, ids=ids, metadatas=metas)
        total_chunks += len(chunks)
    return total_chunks


def query(query_text: str, cfg) -> list[dict]:
    """检索知识库中与 query_text 最相关的 top_k 个文档块。"""
    col = get_or_create_collection(cfg)
    top_k = getattr(cfg, "top_k", 5)
    try:
        results = col.query(query_texts=[query_text], n_results=top_k)
    except Exception:
        # embedding 函数可能未初始化，尝试先 list 再 query
        if col.count() == 0:
            return []
        raise
    docs = results["documents"][0] if results["documents"] else []
    metas = results["metadatas"][0] if results["metadatas"] else []
    distances = results["distances"][0] if results["distances"] else []
    out = []
    for d, m, dist in zip(docs, metas, distances):
        out.append({"text": d, "source": m.get("source", ""), "distance": dist})
    return out


def format_retrieval(results: list[dict], max_chars: int = 4000) -> str:
    """将检索结果格式化为可注入 LLM Prompt 的文本，超过 max_chars 截断。"""
    if not results:
        return ""
    parts: list[str] = []
    total = 0
    for r in results:
        seg = f"[来源：{r['source']}] {r['text']}"
        if total + len(seg) > max_chars:
            break
        parts.append(seg)
        total += len(seg)
    return "\n".join(parts)


def list_sources(cfg: RAGConfig) -> list[str]:
    """列出知识库中的所有来源文件。"""
    col = get_or_create_collection(cfg)
    try:
        data = col.get(include=["metadatas"])
    except Exception:
        return []
    sources = set()
    for m in (data.get("metadatas") or []):
        src = m.get("source", "")
        if src:
            sources.add(src)
    return sorted(sources)


def clear(cfg: RAGConfig) -> int:
    """清空知识库，返回删除的文件数。"""
    col = get_or_create_collection(cfg)
    count = col.count()
    if count > 0:
        col.delete(col.get()["ids"])
    return count
