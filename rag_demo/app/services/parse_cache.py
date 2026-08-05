"""
文档解析缓存

以文件 MD5 为 key，缓存 doc_parser 的解析结果（段落+表格）。
MD5 不变则直接读缓存，跳过耗时的 PDF 解析。

缓存结构：data/parse_cache/{md5}.json
"""
import os
import json
import hashlib
import logging
from typing import Optional

from doc_parser import parse as _raw_parse, Document, Paragraph, Table

log = logging.getLogger("rag_demo.parse_cache")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CACHE_DIR = os.path.join(_BASE_DIR, "data", "parse_cache")


def cached_parse(filepath: str, config: dict = None) -> Document:
    """
    带缓存的文档解析

    Args:
        filepath: 文档路径
        config: 解析配置（传给 doc_parser.parse）

    Returns:
        Document 对象
    """
    os.makedirs(_CACHE_DIR, exist_ok=True)

    md5 = _file_md5(filepath)
    cache_path = os.path.join(_CACHE_DIR, f"{md5}.json")

    # 命中缓存
    if os.path.exists(cache_path):
        try:
            doc = _load_cache(cache_path)
            log.info(f"📦 命中解析缓存: {doc.filename} ({len(doc.paragraphs)} 段, MD5={md5[:8]})")
            return doc
        except Exception as e:
            log.warning(f"缓存加载失败，重新解析: {e}")

    # 执行解析
    doc = _raw_parse(filepath, config=config)
    log.info(f"✅ 解析完成: {doc.filename} ({len(doc.paragraphs)} 段, {len(doc.tables)} 表)")

    # 写入缓存
    _save_cache(cache_path, doc)
    log.info(f"💾 已缓存解析结果: MD5={md5[:8]}")

    return doc


def _file_md5(filepath: str) -> str:
    """计算文件 MD5"""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _save_cache(cache_path: str, doc: Document):
    """序列化 Document 到 JSON"""
    data = {
        "filename": doc.filename,
        "paragraphs": [
            {
                "text": p.text,
                "page": p.page,
                "page_end": p.page_end,
                "chapter": p.chapter,
                "chapter_title": p.chapter_title,
                "source_file": p.source_file,
                "index": p.index,
            }
            for p in doc.paragraphs
        ],
        "tables": [
            {
                "rows": t.rows,
                "headers": t.headers,
                "page": t.page,
                "page_end": t.page_end,
                "chapter": t.chapter,
                "chapter_title": t.chapter_title,
                "context_before": t.context_before,
                "source_file": t.source_file,
                "index": t.index,
            }
            for t in doc.tables
        ],
    }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _load_cache(cache_path: str) -> Document:
    """从 JSON 反序列化 Document"""
    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    paragraphs = [
        Paragraph(
            text=p["text"],
            page=p.get("page", 0),
            page_end=p.get("page_end", 0),
            chapter=p.get("chapter", ""),
            chapter_title=p.get("chapter_title", ""),
            source_file=p.get("source_file", ""),
            index=p.get("index", 0),
        )
        for p in data["paragraphs"]
    ]
    tables = [
        Table(
            rows=t.get("rows", []),
            headers=t.get("headers", []),
            page=t.get("page", 0),
            page_end=t.get("page_end", 0),
            chapter=t.get("chapter", ""),
            chapter_title=t.get("chapter_title", ""),
            context_before=t.get("context_before", ""),
            source_file=t.get("source_file", ""),
            index=t.get("index", 0),
        )
        for t in data.get("tables", [])
    ]

    return Document(
        filename=data["filename"],
        paragraphs=paragraphs,
        tables=tables,
    )
