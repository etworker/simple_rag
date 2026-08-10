"""
文档解析缓存

以文件 SHA-256 为 key，缓存 doc_parser 的解析结果（段落+表格）。
SHA-256 不变则直接读缓存，跳过耗时的 PDF 解析。

缓存结构：~/.simple_rag/parse_cache/{sha256}.json
"""

import json
import logging
import os

from app.services.utils import compute_sha256
from doc_parser import Document, Paragraph, Table
from doc_parser import parse as _raw_parse

log = logging.getLogger("rag_demo.parse_cache")

# 默认缓存目录（~/.simple_rag/parse_cache/）
_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".simple_rag", "parse_cache")


def cached_parse(filepath: str, config: dict = None, cache_dir: str = None) -> Document:
    """
    带缓存的文档解析

    Args:
        filepath: 文档路径
        config: 解析配置（传给 doc_parser.parse）
        cache_dir: 缓存目录（默认 ~/.simple_rag/parse_cache/）；
                   传 None 使用模块默认 _CACHE_DIR

    Returns:
        Document 对象
    """
    base_dir = cache_dir or _CACHE_DIR
    os.makedirs(base_dir, exist_ok=True)

    file_hash = compute_sha256(filepath)
    cache_path = os.path.join(base_dir, f"{file_hash}.json")

    # 命中缓存
    if os.path.exists(cache_path):
        try:
            doc = _load_cache(cache_path)
            log.info(
                f"📦 命中解析缓存: {doc.filename} ({len(doc.paragraphs)} 段, SHA256={file_hash[-8:].upper()})"
            )
            return doc
        except Exception as e:
            log.warning(f"缓存加载失败，重新解析: {e}")

    # 执行解析
    doc = _raw_parse(filepath, config=config)
    log.info(
        f"✅ 解析完成: {doc.filename} ({len(doc.paragraphs)} 段, {len(doc.tables)} 表)"
    )

    # 写入缓存
    _save_cache(cache_path, doc)
    log.info(f"💾 已缓存解析结果: SHA256={file_hash[-8:].upper()}")

    return doc


def _save_cache(cache_path: str, doc: Document):
    """序列化 Document 到 JSON"""
    data = {
        "filename": doc.filename,
        "paragraphs": [p.to_dict() for p in doc.paragraphs],
        "tables": [t.to_dict() for t in doc.tables],
    }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _load_cache(cache_path: str) -> Document:
    """从 JSON 反序列化 Document"""
    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    paragraphs = [Paragraph.from_dict(p) for p in data["paragraphs"]]
    tables = [Table.from_dict(t) for t in data.get("tables", [])]

    return Document(
        filename=data["filename"],
        paragraphs=paragraphs,
        tables=tables,
    )
